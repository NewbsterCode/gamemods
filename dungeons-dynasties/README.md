# Dungeons & Dynasties save editor

Edits a *Dungeons & Dynasties* save, primarily the guild's **gold on hand**.

There is a GUI (`dd_editor.pyw`) and a command line tool (`dd_gold.py`). Both use
the game's own **Back up save / Restore…** feature, so nothing is injected into the
running game and no game files are modified.

Requires Python 3 (already installed here at `C:\Python314`). No third-party
packages.

## Use it

1. **In the game:** Settings → **Back up save**. Save the file (the game suggests
   `my-guild.json`) somewhere you can find it, e.g. your Downloads folder.
2. **Edit it.** Double-click `Edit Save.bat`, open that file, type the gold you
   want, press **Apply**.
3. **In the game:** Settings → **Restore…**, pick the file you just saved.

Your gold is now whatever you set. The original file is kept alongside it as
`my-guild.json.bak`.

The GUI has four tabs: **Guild** (gold, renown, patron confidence, forage),
**Roster**, **Hall** and **Gear**. Each tab has its own Apply button. You can
also drag a backup `.json` straight onto `Edit Save.bat`.

### Command line

```
python dd_gold.py info   my-guild.json          # headline numbers
python dd_gold.py show   my-guild.json          # roster, hall and gear
python dd_gold.py gold   my-guild.json 50000    # set gold
python dd_gold.py set    my-guild.json renown=250 patronConfidence=100

python dd_gold.py hall   my-guild.json --level 5 --all 5
python dd_gold.py hall   my-guild.json --facility training=8 --facility larder=3

python dd_gold.py roster my-guild.json          # heal, de-stress, rest everyone
python dd_gold.py roster my-guild.json --stress 10 --keep-grind

python dd_gold.py hero   my-guild.json "Nesta" --level 12 --ability 150
python dd_gold.py hero   my-guild.json all --attributes 16 --hidden 16

python dd_gold.py gear   my-guild.json legendary
python dd_gold.py gear   my-guild.json mythic --scope equipped
```

Options: `-o/--output` to write elsewhere, `--no-backup`, `--force`.

## What can be changed

### Guild (top-level numbers)

`gold`, `renown`, `patronConfidence` (0–100), `forage`, `delveGrind`,
`championStreak`, `mandateMisses` — and via `set`, any other top-level number.

### Hall

`--level` plus the nine facilities: `infirmary`, `training`, `tavern`,
`armoury`, `stonehouse`, `beacon`, `counting`, `larder`, `academy`.

### Roster and heroes

`level` (1–20), `currentAbility` / `potentialAbility` (1–200), the 30
`attributes` and 7 `hidden` traits (1–20 each), and the four vitals
`condition` / `morale` / `stress` / `fatigue` (0–100).

The bulk `roster` command restores everyone at once and clears `delveGrind`.

### Gear

Promotes items through `common → rare → mythic → legendary`. Rarity on its own
is mostly cosmetic, so each item's `mods` are scaled by the ratio between the
bands the game rolls that rarity from (`q8`: common `.09/.05`, rare `.15/.09`,
mythic `.22/.13`, legendary `.30/.17`), and `value` by the game's own pricing
curve `1 + rarityIndex * 0.9`. `accuracy` stays a whole number, as the game
keeps it. Items already at or above the target are skipped.

`--scope` picks `equipped`, `stash` or `all`.

## The part that matters: the checksum

A save file looks like this:

```json
{"version":26,"guild":{ ...everything... },"sig":"482gcg.b886e0"}
```

`sig` is an integrity hash over the exact JSON text of `guild`. **The game
recomputes it on load.** If it does not match:

- the save is flagged as tampered, and
- because this is the demo build, a tampered save has its day count forced to
  the demo cap (day 240) — which ends the run, and
- **Restore… refuses the file outright.**

So editing the JSON by hand, or with a text editor or `sed`, will break your
save. This tool recomputes the hash, which is the whole reason it exists.

Verified against the game's own validation code — a hand edit is rejected and
capped, the tool's edit loads clean:

| | hand-edited | this tool |
|---|---|---|
| version accepted | yes | yes |
| flagged tampered | **yes** | no |
| Restore… rejects it | **yes** | no |
| day count forced to 240 | **yes** | no |

### How the edit stays safe

Simple top-level numbers (`gold`, `set`) are patched **in the raw file text**,
leaving every other byte of the `guild` object identical, then the hash is
recomputed. Nothing can drift.

Nested edits (hall, heroes, gear) have to rebuild the JSON, which is only safe
if Python reproduces V8's `JSON.stringify` byte for byte. So before any such
edit the tool re-serialises the **untouched** guild and compares it to the file.
On your save that matches exactly across 1.25 MB, floats and all. If it ever
does not, structured edits are refused and only the raw-text path is offered.

#### The number-formatting trap

JavaScript numbers are all doubles, and `JSON.stringify` prints one with no
fractional part as an integer — `4.0` is written `4`. Python writes `4.0`.

That matters because the game does not hash the file text; it parses the save
and hashes a **re-serialisation** of the object. So a single `4.0` where the
game would write `4` produces a different hash, and the restore fails with
*"That file isn't a valid backup (or it's from a different version)"* — which
looks like a version problem but is really the tamper check firing.

This bit `abilityStars` and `potentialStars`, which are half-steps: `4.5` is
fine, `4.0` was not. Three things now prevent it:

1. integral floats are folded to ints before serialising, as JS prints them
2. the finished text is scanned for spellings JS would never emit
   (`4.0`, `1e-07`) — this is independent of the serialiser, so it catches the
   serialiser itself being wrong
3. `verify_as_game()` reproduces the game's check — parse, re-serialise, hash

If any of them fails the file is not written at all.

It also refuses to touch a file whose checksum does not already match, because
that would mean it is not reading the save the way the game does. That check is
the tool's own proof of correctness on every run.

For gold, only the guild's own top-level `gold` is changed. The save contains
many other nested `gold` fields — quest rewards, delve events, hero inventories
— and those are left alone.

### Staying inside the game's rules

The game runs a sanitiser (`CF`) over every save it loads, which silently
rewrites values it does not like. The tool matches those rules rather than
fighting them:

| Game rule | What the tool does |
|---|---|
| `experience` is recomputed from `level` | sets both, using the game's own XP curve `ceil(0.9(L-1)² + 6(L-1))` |
| vitals are rounded to integers | writes integers |
| `abilityStars` derives from `currentAbility` | recomputes it with the game's `round(a/200*5*2)/2` |
| roster is de-duplicated by `id` | never clones heroes |
| `maxDay` is raised to at least `day` | leaves day alone (see below) |
| ability is capped at 200, level at 20 | clamps to both |

A hero's `potentialAbility` is also raised automatically if you set
`currentAbility` above it, since a hero may not exceed their own ceiling.

## What the game stores, and where

The game is a [Tauri](https://tauri.app) app: a Rust shell around an HTML/JS
frontend running in WebView2. All state lives in the WebView's `localStorage`,
under:

```
C:\Users\<you>\AppData\Local\io.itch.brettlittle.dungeons-and-dynasties\
    EBWebView\Default\Local Storage\leveldb\
```

Keys:

| Key | Holds |
|---|---|
| `guild-manager-save` | save slot 1 |
| `guild-manager-save-slot2` | save slot 2 |
| `guild-manager-save-slot3` | save slot 3 |
| `guild-manager-save-hw-<seed>` | high-water day mark, per slot |
| `dd_save_slot` | which slot is active |
| `dd_founding_draft` | in-progress dynasty creation |
| `dd_*` | assorted UI preferences |

A backup file is a byte-for-byte copy of one of those save values, which is why
the round trip through Back up save / Restore… is lossless.

That storage is a Chrome LevelDB, which is awkward and risky to write to
directly, and the game must be closed to touch it. The Back up save / Restore… route
avoids all of that, so this tool uses it.

### Fields worth knowing

Inside `guild`:

| Field | Meaning |
|---|---|
| `gold` | coin on hand — normal quest rewards are ~100–900 |
| `renown` | guild standing |
| `forage` | stored provisions |
| `day` / `maxDay` | run progress; the demo ends at **240** |
| `roster` | your adventurers |
| `realmSeed` | world seed, used in the high-water key |

The GUI exposes `gold`, `renown` and `forage`. The CLI's `set` accepts any
top-level numeric field, e.g. `python dd_gold.py set my-guild.json forage=99`.

## Notes and limits

- **This is the demo build** (`Dungeons & Dynasties Demo`). Save format version
  is **26**, and the tool only edits version 26 saves — if a full release or an
  update changes the format, the tool will say so rather than corrupt anything.
- Editing `day` is possible via `set` but not advised: the game keeps a separate
  high-water day mark in `localStorage` and takes the maximum, so lowering `day`
  in the file will not roll a run back.
- Absurd amounts are allowed but warned about; the game's number formatting and
  its economy were not built for them.
- Close the game, or at least return to the title screen, before restoring — the
  running session holds its own copy of the state and can write it back over
  yours.

## Files

| File | What it is |
|---|---|
| `Edit Save.bat` | double-click launcher (or drag a save onto it) |
| `dd_editor.pyw` | the GUI |
| `dd_gold.py` | the command line tool |
| `dd_save.py` | the library: parsing, the hash, safe editing |
| `dd_mods.py` | hall / roster / hero / gear edits and game formulas |
| `save_game_backups\` | where your backups live |
