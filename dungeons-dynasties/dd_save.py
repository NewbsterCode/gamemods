"""
Core library for reading and editing Dungeons & Dynasties backup saves.

Workflow this tool is built around (all officially supported by the game):

    1. In game: Settings -> "Back up save" -> save as my-guild.json
    2. Edit that file with this tool
    3. In game: Settings -> "Restore..." -> pick the edited file

Save file format (version 26):

    {"version":26,"guild":{...},"sig":"<hash>"}

The `sig` field is an integrity hash over the exact JSON text of the `guild`
value. The game recomputes it on load; if it does not match, the save is
flagged as tampered, and in the demo build a tampered save has its day count
forced to the demo cap (240), which ends the run. So any edit MUST recompute
the signature -- that is the whole point of this module.

To guarantee the hash matches, edits are made surgically on the raw file text
rather than by re-serializing parsed JSON. The bytes of the `guild` value are
left byte-for-byte identical except for the number being changed, so there is
no risk of a Python/JavaScript JSON formatting mismatch.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants mirrored from the game
# ---------------------------------------------------------------------------

SAVE_VERSION = 26          # game constant wF / jie; loader accepts only this
DEMO_DAY_CAP = 240         # game constant G8; demo ends at this day
LOCALSTORAGE_KEY = "guild-manager-save"   # slot 1; slots 2-3 add "-slot2"/"-slot3"

# Fields it is safe and useful to edit. gold is the headline feature.
EDITABLE_HINTS = {
    "gold": "Coin on hand. Normal quest rewards are ~100-900.",
    "renown": "Guild renown / standing.",
    "forage": "Stored provisions.",
}


class SaveError(Exception):
    """Raised when a save file cannot be parsed or edited safely."""


# ---------------------------------------------------------------------------
# Signature hash (port of the game's `yF`)
# ---------------------------------------------------------------------------

_B36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_MASK32 = 0xFFFFFFFF


def _utf16_code_units(text: str) -> list[int]:
    """Yield the UTF-16 code units of `text`, matching JS `charCodeAt`.

    JavaScript strings are UTF-16, so characters outside the BMP (emoji, for
    example, which can legitimately appear in a guild name) are two code units.
    Iterating Python code points directly would diverge on those.
    """
    raw = text.encode("utf-16-le", errors="surrogatepass")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def _to_base36(value: int) -> str:
    """Format an unsigned int like JS `Number.prototype.toString(36)`."""
    if value == 0:
        return "0"
    out = []
    while value:
        out.append(_B36_DIGITS[value % 36])
        value //= 36
    return "".join(reversed(out))


def signature(guild_json_text: str) -> str:
    """Compute the save signature over the raw JSON text of the guild object.

    A direct port of the game's two-accumulator FNV-style hash:

        e = imul(e ^ c, 16777619)
        i = imul(i + c, 2246822507)

    returning `e.toString(36) + "." + i.toString(36)`.
    """
    acc_a = 2166136261
    acc_b = 16777619
    for code in _utf16_code_units(guild_json_text):
        acc_a = ((acc_a ^ code) * 16777619) & _MASK32
        acc_b = ((acc_b + code) * 2246822507) & _MASK32
    return f"{_to_base36(acc_a)}.{_to_base36(acc_b)}"


# ---------------------------------------------------------------------------
# Minimal JSON text scanner
#
# We need the exact source span of a value, not its parsed form, so the raw
# bytes can be preserved around an edit.
# ---------------------------------------------------------------------------

def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _value_end(text: str, start: int) -> int:
    """Return the index just past the JSON value beginning at `start`."""
    if start >= len(text):
        raise SaveError("unexpected end of file while reading a value")

    if text[start] in "{[":
        depth = 0
        in_string = False
        escaped = False
        i = start
        while i < len(text):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise SaveError("unterminated object or array")

    if text[start] == '"':
        i = start + 1
        escaped = False
        while i < len(text):
            ch = text[i]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                return i + 1
            i += 1
        raise SaveError("unterminated string")

    # number, true, false, null
    i = start
    while i < len(text) and text[i] not in ",}]":
        i += 1
    return i


def _find_key_span(text: str, key: str, depth_wanted: int = 1) -> tuple[int, int]:
    """Find `"key": <value>` at the given nesting depth.

    Returns the (start, end) source span of the *value*. Depth 1 means a direct
    member of the outermost object, which is what keeps us from matching a
    same-named key nested deeper -- the game has many nested `gold:` fields on
    quest rewards and delve events, for instance.
    """
    needle = f'"{key}"'
    depth = 0
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            if depth == depth_wanted and text.startswith(needle, i):
                after = _skip_ws(text, i + len(needle))
                if after < len(text) and text[after] == ":":
                    value_start = _skip_ws(text, after + 1)
                    return value_start, _value_end(text, value_start)
            in_string = True
            i += 1
            continue

        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        i += 1

    raise SaveError('could not find a top-level "%s" field' % key)


# ---------------------------------------------------------------------------
# Save file model
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


# JavaScript numbers are all doubles, and `JSON.stringify` prints one with no
# fractional part as an integer: 4.0 becomes "4". Python prints "4.0". A save
# is hashed over its text, and the game re-serialises the parsed object to
# check that hash -- so a single "4.0" we write where the game would write "4"
# makes it declare the save tampered. Integral floats are therefore folded to
# ints before serialising, below the point where doubles stop being exact.
_JS_SAFE_INT = 2 ** 53


def _js_numbers(value: object) -> object:
    """Recursively rewrite integral floats as ints, the way JS prints them."""
    if isinstance(value, float):
        if value.is_integer() and abs(value) < _JS_SAFE_INT:
            return int(value)
        return value
    if isinstance(value, dict):
        return {key: _js_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_js_numbers(item) for item in value]
    return value


# Number spellings Python produces that JavaScript never would. Checking the
# finished text for these catches a serialisation bug directly, rather than
# trusting the serialiser to police itself -- `verify_as_game` shares that
# serialiser, so it cannot detect the serialiser being wrong.
_NOT_JS_NUMBER = re.compile(
    r"""
      -?\d+\.0(?=[,}\]])     # integral float: Python "4.0", JS "4"
    | e[+-]0\d               # padded exponent: Python "1e-07", JS "1e-7"
    """,
    re.VERBOSE,
)


def find_non_js_numbers(text: str) -> list[str]:
    """Return number spellings in `text` that JavaScript would write otherwise."""
    return [match.group() for match in _NOT_JS_NUMBER.finditer(text)]


def js_stringify(value: object) -> str:
    """Serialise like JavaScript's `JSON.stringify` with no spacing.

    The game hashes the exact text of the guild object, so anything written
    back has to reproduce V8's output byte for byte: compact separators, raw
    non-ASCII, shortest round-trip floats, and integers printed without a
    trailing `.0`.
    """
    return json.dumps(
        _js_numbers(value), separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


class Save:
    """A loaded backup save, kept as raw text plus located spans."""

    def __init__(self, raw: str, path: Path | None = None):
        self._init(raw, path)

    def _init(self, raw: str, path: Path | None) -> None:
        self.raw = raw
        self.path = path

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SaveError("not valid JSON: %s" % exc) from exc
        if not isinstance(parsed, dict):
            raise SaveError("save root is not a JSON object")
        if "guild" not in parsed:
            raise SaveError(
                "no 'guild' field -- this does not look like a "
                "Dungeons & Dynasties backup"
            )
        self.data = parsed

        self.guild_start, self.guild_end = _find_key_span(raw, "guild")

    # -- derived views ----------------------------------------------------

    @property
    def guild_text(self) -> str:
        """The exact JSON source of the guild object (what the hash covers)."""
        return self.raw[self.guild_start:self.guild_end]

    @property
    def guild(self) -> dict:
        return self.data["guild"]

    @property
    def version(self) -> int | None:
        value = self.data.get("version")
        return value if isinstance(value, int) else None

    @property
    def stored_signature(self) -> str | None:
        value = self.data.get("sig")
        return value if isinstance(value, str) else None

    def computed_signature(self) -> str:
        return signature(self.guild_text)

    def signature_ok(self) -> bool:
        return self.stored_signature == self.computed_signature()

    def verify_as_game(self) -> bool:
        """Reproduce the game's own tamper check against the current file text.

        `signature_ok` hashes the guild text as it sits in the file, which only
        proves this tool is self-consistent. The game does something stricter:
        it parses the save and hashes a *re-serialisation* of the object. If our
        output differs from the game's for even one number, those two disagree
        and the save is rejected as tampered. This models what the game does, so
        it is the check that actually matters before writing.
        """
        data = json.loads(self.raw)
        guild = data.get("guild")
        if guild is None:
            return False
        return data.get("sig") == signature(js_stringify(guild))

    def version_ok(self) -> bool:
        return self.version == SAVE_VERSION

    # -- reading ----------------------------------------------------------

    def get_number(self, field: str) -> int | float | None:
        value = self.guild.get(field)
        if isinstance(value, bool):
            return None
        return value if isinstance(value, (int, float)) else None

    def summary(self) -> dict:
        guild = self.guild
        return {
            "name": guild.get("name"),
            "day": guild.get("day"),
            "maxDay": guild.get("maxDay"),
            "gold": guild.get("gold"),
            "renown": guild.get("renown"),
            "roster": len(guild.get("roster") or []),
            "version": self.version,
            "signature_ok": self.signature_ok(),
        }

    # -- writing ----------------------------------------------------------

    def set_number(self, field: str, new_value: int) -> tuple[int | float | None, int]:
        """Set a top-level numeric guild field and refresh the signature.

        Returns (old_value, new_value). The edit is applied to the raw text so
        every other byte of the guild object is preserved exactly, which is what
        lets the recomputed signature match what the game will compute.
        """
        if isinstance(new_value, bool) or not isinstance(new_value, int):
            raise SaveError("value must be a whole number")
        if new_value < 0:
            raise SaveError("value must not be negative")

        guild_text = self.guild_text
        old_value = self.guild.get(field)

        if field in self.guild:
            start, end = _find_key_span(guild_text, field)
            current = guild_text[start:end]
            if not _NUMBER_RE.fullmatch(current):
                raise SaveError(
                    '"%s" is not a plain number in this save (found %r)'
                    % (field, current[:40])
                )
            new_guild_text = guild_text[:start] + str(new_value) + guild_text[end:]
        else:
            # Field absent: insert it as the first member of the guild object.
            if not guild_text.startswith("{"):
                raise SaveError("guild value is not an object")
            sep = "" if guild_text[1:2] == "}" else ","
            new_guild_text = '{"%s":%d%s' % (field, new_value, sep) + guild_text[1:]

        self._replace_guild(new_guild_text)
        return old_value, new_value

    def reserialize_ok(self) -> bool:
        """True if re-serialising the parsed guild reproduces the file exactly.

        This is the gate for structured edits. Patching a single number can be
        done on the raw text, but changing nested values means rebuilding the
        JSON, and that is only safe when Python's output is byte-identical to
        what the game wrote. Checking it against the untouched object proves
        that for this specific file before anything is modified.
        """
        return js_stringify(self.guild) == self.guild_text

    def commit(self) -> None:
        """Write the (mutated) guild object back and refresh the signature.

        Use after editing `save.guild` in place. Refuses to run if the guild
        cannot be re-serialised faithfully, so a formatting mismatch can never
        turn into a save the game rejects as tampered.
        """
        rebuilt = js_stringify(self.guild)
        if rebuilt == self.guild_text:
            return  # nothing actually changed

        # Re-parse the file as it currently stands to confirm our serialiser
        # round-trips this data, independent of the edits just made.
        original = json.loads(self.raw)["guild"]
        if js_stringify(original) != self.guild_text:
            raise SaveError(
                "cannot safely rewrite this save: Python's JSON output does "
                "not reproduce the game's byte for byte, so the signature "
                "could not be trusted. Only simple numeric edits are available "
                "for this file."
            )

        # Independent of the serialiser: look for number spellings JavaScript
        # would never emit. This is what actually catches a serialiser bug,
        # since the check below shares the serialiser with the writer.
        offenders = find_non_js_numbers(rebuilt)
        if offenders:
            raise SaveError(
                "refusing to write: %d number(s) were formatted in a way the "
                "game would not (%s). Nothing has been changed."
                % (len(offenders), ", ".join(sorted(set(offenders))[:5]))
            )

        self._replace_guild(rebuilt)

        # Final gate: confirm the game will accept what we just built. This
        # catches any number we wrote whose text differs from the game's own
        # formatting, which would otherwise surface as "that file isn't a valid
        # backup" only after a failed restore.
        if not self.verify_as_game():
            raise SaveError(
                "the rewritten save would not pass the game's own integrity "
                "check, so it was not written. This is a bug in the editor -- "
                "no file has been changed."
            )

    def _replace_guild(self, new_guild_text: str) -> None:
        """Swap in a new guild body, then rewrite the signature to match."""
        new_sig = signature(new_guild_text)

        raw = self.raw[:self.guild_start] + new_guild_text + self.raw[self.guild_end:]

        # Locate the sig span in the rebuilt text; the guild body changed length,
        # so any span found earlier is stale.
        try:
            sig_start, sig_end = _find_key_span(raw, "sig")
        except SaveError:
            # No sig field at all: append one so the game can verify the save.
            closing = raw.rstrip().rfind("}")
            if closing == -1:
                raise SaveError("save root is not a JSON object") from None
            raw = raw[:closing] + ',"sig":' + json.dumps(new_sig) + raw[closing:]
        else:
            raw = raw[:sig_start] + json.dumps(new_sig) + raw[sig_end:]

        self._init(raw, self.path)

    # -- io ---------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Save":
        p = Path(path)
        try:
            raw = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise SaveError("file not found: %s" % p) from None
        except UnicodeDecodeError as exc:
            raise SaveError("file is not UTF-8 text: %s" % exc) from exc
        return cls(raw.lstrip("﻿"), p)

    def save(self, path: str | Path | None = None, backup: bool = True) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise SaveError("no output path given")

        backup_path = None
        if backup and target.exists():
            backup_path = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, backup_path)

        # newline="" keeps the file free of CRLF translation; the game wrote it
        # as a single line of JSON and we keep it that way.
        target.write_text(self.raw, encoding="utf-8", newline="")
        self.path = target
        self.last_backup = backup_path
        return target
