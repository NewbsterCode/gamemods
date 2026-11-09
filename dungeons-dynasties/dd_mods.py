"""
Structured edits to a Dungeons & Dynasties guild: hall, roster, heroes, gear.

Everything here mutates the parsed guild dict in place and returns a list of
human-readable change descriptions. Call `Save.commit()` afterwards to write
the result back with a refreshed signature.

Constants and formulas are taken from the game's own code so edits land on
values the game considers legitimate, and so nothing gets silently rewritten by
the game's load-time sanitiser (`CF`). The sanitiser in particular:

  * recomputes `experience` from `level`, so we set both consistently
  * rounds condition / stress / fatigue / morale to integers
  * de-duplicates the roster by `id`
  * raises `maxDay` to at least `day`

so the helpers below stay inside those rules rather than fighting them.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Game constants
# ---------------------------------------------------------------------------

ABILITY_MAX = 200          # _H; current/potentialAbility ceiling
LEVEL_MAX = 20             # $i(level, 1, 20)
ATTR_MIN, ATTR_MAX = 1, 20  # attributes and hidden traits are a 1-20 scale
PERCENT_MIN, PERCENT_MAX = 0, 100   # condition / stress / fatigue / morale

_XP_QUAD = 0.9             # H5
_XP_LIN = 6                # Ev

RARITIES = ["common", "rare", "mythic", "legendary"]

# q8: the per-rarity magnitudes the game rolls item mods from.
RARITY_SCALE = {
    "common": {"pct": 0.09, "flat": 0.05},
    "rare": {"pct": 0.15, "flat": 0.09},
    "mythic": {"pct": 0.22, "flat": 0.13},
    "legendary": {"pct": 0.30, "flat": 0.17},
}

# Mods the game keeps as whole numbers (everything else is rounded to 4dp).
INTEGER_MODS = {"accuracy"}

# The nine hall facilities, in the order the game stores them.
FACILITIES = [
    "infirmary", "training", "tavern", "armoury",
    "stonehouse", "beacon", "counting", "larder", "academy",
]

# Vitals the roster quality-of-life pass can touch. True == higher is better.
VITALS = {
    "condition": True,
    "morale": True,
    "stress": False,
    "fatigue": False,
}


class ModError(Exception):
    """Raised when a requested change cannot be applied."""


# ---------------------------------------------------------------------------
# Formula ports
# ---------------------------------------------------------------------------

def _clamp(value, low, high):
    """The game's `$i`."""
    return low if value < low else high if value > high else value


def xp_for_level(level: int) -> int:
    """`Ht` -- experience required to sit at `level`."""
    steps = _clamp(int(level), 1, LEVEL_MAX) - 1
    return math.ceil(_XP_QUAD * steps * steps + _XP_LIN * steps)


def level_for_xp(xp: float) -> int:
    """`Op` -- the level the game derives from an experience total."""
    root = math.sqrt(_XP_LIN * _XP_LIN + 4 * _XP_QUAD * max(0, xp))
    return _clamp(int((-_XP_LIN + root) / (2 * _XP_QUAD)) + 1, 1, LEVEL_MAX)


def ability_stars(ability: float) -> float:
    """`_i` -- the half-star rating shown for an ability score."""
    return _clamp(round(ability / ABILITY_MAX * 5 * 2) / 2, 0.5, 5.0)


# ---------------------------------------------------------------------------
# Hero lookup
# ---------------------------------------------------------------------------

def find_heroes(guild: dict, selector: str) -> list[dict]:
    """Resolve `selector` to heroes: "all", an id, or a name (partial, ci)."""
    roster = guild.get("roster") or []
    if selector == "all":
        return list(roster)

    needle = selector.strip().lower()
    exact = [h for h in roster if str(h.get("id", "")).lower() == needle]
    if exact:
        return exact
    named = [h for h in roster if needle == str(h.get("name", "")).lower()]
    if named:
        return named
    partial = [h for h in roster if needle in str(h.get("name", "")).lower()]
    if not partial:
        known = ", ".join(str(h.get("name")) for h in roster)
        raise ModError(f"no hero matches {selector!r}. Roster: {known}")
    return partial


# ---------------------------------------------------------------------------
# Hall
# ---------------------------------------------------------------------------

def set_hall(guild: dict, level: int | None = None,
             facilities: dict[str, int] | None = None) -> list[str]:
    """Set the hall level and/or facility levels."""
    hall = guild.setdefault("hall", {})
    changes: list[str] = []

    if level is not None:
        if level < 1:
            raise ModError("hall level must be at least 1")
        old = hall.get("level")
        if old != level:
            hall["level"] = level
            changes.append(f"hall level {old} -> {level}")

    if facilities:
        current = hall.setdefault("facilities", {})
        for name, value in facilities.items():
            if name not in FACILITIES:
                raise ModError(
                    f"unknown facility {name!r}; known: {', '.join(FACILITIES)}"
                )
            if value < 0:
                raise ModError(f"{name} level must not be negative")
            old = current.get(name, 0)
            if old != value:
                current[name] = value
                changes.append(f"{name} {old} -> {value}")

    return changes


# ---------------------------------------------------------------------------
# Roster quality of life
# ---------------------------------------------------------------------------

def restore_roster(guild: dict, condition: int | None = 100,
                   morale: int | None = 100, stress: int | None = 0,
                   fatigue: int | None = 0,
                   clear_grind: bool = True) -> list[str]:
    """Bulk-restore the whole roster: heal, de-stress, rest, lift morale.

    Values are written as integers because the game rounds these four fields on
    load anyway. Pass None for any field to leave it untouched.
    """
    wanted = {
        "condition": condition, "morale": morale,
        "stress": stress, "fatigue": fatigue,
    }
    targets = {}
    for field, value in wanted.items():
        if value is None:
            continue
        if not PERCENT_MIN <= value <= PERCENT_MAX:
            raise ModError(
                f"{field} must be between {PERCENT_MIN} and {PERCENT_MAX}"
            )
        targets[field] = int(value)

    roster = guild.get("roster") or []
    touched: dict[str, int] = {field: 0 for field in targets}
    for hero in roster:
        for field, value in targets.items():
            if hero.get(field) != value:
                hero[field] = value
                touched[field] += 1

    changes = [
        f"{field} set to {targets[field]} on {count} hero(es)"
        for field, count in touched.items() if count
    ]

    if clear_grind and guild.get("delveGrind"):
        old = guild["delveGrind"]
        guild["delveGrind"] = 0
        changes.append(f"delveGrind {old} -> 0 (party no longer worn down)")

    return changes


# ---------------------------------------------------------------------------
# Individual heroes
# ---------------------------------------------------------------------------

def set_hero(guild: dict, selector: str, *, level: int | None = None,
             current_ability: int | None = None,
             potential_ability: int | None = None,
             attributes: int | dict[str, int] | None = None,
             hidden: int | dict[str, int] | None = None,
             **vitals: int) -> list[str]:
    """Edit one or more heroes.

    `attributes` / `hidden` accept either a single number (apply to every trait)
    or a mapping of individual trait names.
    """
    heroes = find_heroes(guild, selector)
    changes: list[str] = []

    for hero in heroes:
        name = hero.get("name", hero.get("id", "?"))

        if level is not None:
            if not 1 <= level <= LEVEL_MAX:
                raise ModError(f"level must be between 1 and {LEVEL_MAX}")
            old = hero.get("level")
            hero["level"] = level
            # The sanitiser recomputes experience from level; match it so the
            # two agree immediately rather than after a load.
            hero["experience"] = xp_for_level(level)
            if old != level:
                changes.append(f"{name}: level {old} -> {level}")

        if potential_ability is not None:
            value = _clamp(int(potential_ability), 1, ABILITY_MAX)
            old = hero.get("potentialAbility")
            hero["potentialAbility"] = value
            hero["potentialStars"] = ability_stars(value)
            if old != value:
                changes.append(f"{name}: potential {old} -> {value}")

        if current_ability is not None:
            value = _clamp(int(current_ability), 1, ABILITY_MAX)
            # A hero may not exceed their own ceiling; raise it to suit.
            if value > hero.get("potentialAbility", 0):
                hero["potentialAbility"] = value
                hero["potentialStars"] = ability_stars(value)
            old = hero.get("currentAbility")
            hero["currentAbility"] = value
            hero["abilityStars"] = ability_stars(value)
            if old != value:
                changes.append(f"{name}: ability {old} -> {value}")

        for field, value in vitals.items():
            if value is None:
                continue
            if field not in VITALS:
                raise ModError(f"unknown hero field {field!r}")
            if not PERCENT_MIN <= value <= PERCENT_MAX:
                raise ModError(f"{field} must be 0-100")
            old = hero.get(field)
            hero[field] = int(value)
            if old != int(value):
                changes.append(f"{name}: {field} {old} -> {int(value)}")

        for group, wanted in (("attributes", attributes), ("hidden", hidden)):
            if wanted is None:
                continue
            block = hero.setdefault(group, {})
            if isinstance(wanted, dict):
                items = wanted.items()
                for key in wanted:
                    if key not in block:
                        raise ModError(
                            f"{name} has no {group} entry {key!r}; known: "
                            + ", ".join(sorted(block))
                        )
            else:
                items = ((key, wanted) for key in block)

            bumped = 0
            for key, value in items:
                value = _clamp(int(value), ATTR_MIN, ATTR_MAX)
                if block.get(key) != value:
                    block[key] = value
                    bumped += 1
            if bumped:
                shown = wanted if not isinstance(wanted, dict) else "given values"
                changes.append(f"{name}: {bumped} {group} set to {shown}")

    return changes


# ---------------------------------------------------------------------------
# Gear
# ---------------------------------------------------------------------------

def _iter_items(guild: dict, scope: str):
    """Yield (location, item) for gear in the requested scope."""
    if scope in ("equipped", "all"):
        for hero in guild.get("roster") or []:
            equipment = hero.get("equipment") or {}
            for slot, item in equipment.items():
                if isinstance(item, dict) and "rarity" in item:
                    yield f"{hero.get('name', '?')}'s {slot}", item
    if scope in ("stash", "all"):
        for item in guild.get("stash") or []:
            if isinstance(item, dict) and "rarity" in item:
                yield "stash", item


def upgrade_gear(guild: dict, target_rarity: str,
                 scope: str = "all") -> list[str]:
    """Raise gear to `target_rarity`, scaling its mods and value to match.

    Rarity alone is mostly cosmetic -- the numbers that matter live in `mods`.
    So each mod is scaled by the ratio between the rarity bands the game rolls
    from (`q8`), and `value` by the same curve the game prices items on
    (`1 + rarityIndex * 0.9`). Items already at or above the target are left be.
    """
    if target_rarity not in RARITIES:
        raise ModError(
            f"unknown rarity {target_rarity!r}; known: {', '.join(RARITIES)}"
        )
    if scope not in ("equipped", "stash", "all"):
        raise ModError("scope must be 'equipped', 'stash' or 'all'")

    target_index = RARITIES.index(target_rarity)
    target_scale = RARITY_SCALE[target_rarity]
    changes: list[str] = []

    for where, item in _iter_items(guild, scope):
        current = item.get("rarity")
        if current not in RARITIES:
            continue
        current_index = RARITIES.index(current)
        if current_index >= target_index:
            continue

        source_scale = RARITY_SCALE[current]
        pct_ratio = target_scale["pct"] / source_scale["pct"]
        flat_ratio = target_scale["flat"] / source_scale["flat"]

        mods = item.get("mods")
        if isinstance(mods, dict):
            for key, value in list(mods.items()):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                if key in INTEGER_MODS:
                    scaled = value * flat_ratio
                    mods[key] = int(math.copysign(math.ceil(abs(scaled)), scaled))
                else:
                    mods[key] = round(value * pct_ratio, 4)

        old_value = item.get("value")
        if isinstance(old_value, (int, float)):
            price_ratio = (1 + target_index * 0.9) / (1 + current_index * 0.9)
            item["value"] = round(old_value * price_ratio)

        item["rarity"] = target_rarity
        changes.append(
            f"{where}: {item.get('name', '?')} {current} -> {target_rarity}"
        )

    return changes
