"""Load, edit and safely write SOVL grand-strategy (solo campaign) saves.

Only the local single-player grand-strategy save is touched. All edit
operations are plain functions on the parsed JSON dict so they can be tested
without the UI.

Field meanings below were derived by diffing a save against the game's own
``.bak`` from the previous round:
  * a building finishes:  Tier 0 / inConstruction 1  ->  Tier 1 / inConstruction 0
  * a settlement upgrade: settlementUpgradeTurns 1 -> 0 and settlementLevel + 1
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SAVE_PATH = (
    Path(os.path.expandvars(r"%USERPROFILE%"))
    / "AppData" / "LocalLow" / "DalenStudios" / "SOVL"
    / "grandStrategyData" / "grandStrategyProgress.json"
)
BACKUP_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SOVLEditor" / "backups"
GAME_EXE = "SOVL.exe"

UNOWNED = -1
# Highest values seen in real saves. The game's true caps are not stored in the
# save, so these are defaults the UI lets the user change.
DEFAULT_MAX_BUILDING_TIER = 3
DEFAULT_MAX_SETTLEMENT_LEVEL = 3

OBJECTIVE_HIDDEN, OBJECTIVE_ACTIVE, OBJECTIVE_DONE = 0, 1, 2
# Most banners seen on one unit in a real save; may be the game's slot limit.
OBSERVED_MAX_BANNERS = 5

Save = dict[str, Any]


# --------------------------------------------------------------------------- io

def game_is_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return GAME_EXE.lower() in out.lower()


def load(path: Path = SAVE_PATH) -> Save:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dumps(save: Save) -> str:
    """Serialise the way the game writes it: 2-space indent, CRLF, raw unicode."""
    return json.dumps(save, indent=2, ensure_ascii=False).replace("\n", "\r\n")


def backup(path: Path = SAVE_PATH, backup_dir: Path = BACKUP_DIR) -> Path:
    """Copy ``path`` to the backup folder, named after the save it came from."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    shutil.copy2(path, dest)
    return dest


def write(save: Save, path: Path = SAVE_PATH, backup_dir: Path = BACKUP_DIR) -> Path:
    """Back up the current file, then atomically replace it. Returns backup path."""
    text = dumps(save)
    json.loads(text)  # never write something we cannot read back
    bak = backup(path, backup_dir)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sovl_edit_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return bak


def list_backups(backup_dir: Path = BACKUP_DIR, path: Path = SAVE_PATH) -> list[Path]:
    """Backups of the save at ``path``, newest first."""
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob(f"{path.stem}_*.json"), reverse=True)


def restore(backup_file: Path, path: Path = SAVE_PATH, backup_dir: Path = BACKUP_DIR) -> Path:
    """Restore a backup (the file being replaced is itself backed up first)."""
    json.loads(backup_file.read_text(encoding="utf-8"))
    pre = backup(path, backup_dir)
    shutil.copy2(backup_file, path)
    return pre


# ---------------------------------------------------------------------- queries

def player_id(save: Save) -> int:
    for e in save["empires"]:
        if e.get("isPlayerFaction"):
            return e["empireID"]
    return save.get("ActivePlayerID", 0)


def empire(save: Save, empire_id: int) -> dict:
    return next(e for e in save["empires"] if e["empireID"] == empire_id)


def empire_name(save: Save, empire_id: int) -> str:
    if empire_id == UNOWNED:
        return "(unowned)"
    try:
        return empire(save, empire_id)["empireName"]
    except StopIteration:
        return f"Empire {empire_id}"


def owned_regions(save: Save, empire_id: int) -> list[dict]:
    return [r for r in save["regions"] if r["ownerID"] == empire_id]


def armies_of(save: Save, empire_id: int) -> list[dict]:
    return [a for a in save["armies"] if a["ownerID"] == empire_id and not a.get("destroyed")]


def army_entries(army: dict) -> Iterator[dict]:
    """Every unit entry in an army list (commanders, battle line, other sections)."""
    lst = army.get("list") or {}
    sections = [lst.get("characters"), lst.get("battleLine"), *(lst.get("armyListSections") or [])]
    for sec in sections:
        for entry in (sec or {}).get("entries") or []:
            yield entry


def _progress_blocks(entry: dict) -> Iterator[dict]:
    if entry.get("campaignUnitProgress"):
        yield entry["campaignUnitProgress"]
    ch = entry.get("character")
    if ch and ch.get("campaignUnitProgress"):
        yield ch["campaignUnitProgress"]


def _same_number_type(old: Any, new: float | int) -> float | int:
    """Keep floats as floats (recruitment counts are stored as 12.0)."""
    return float(new) if isinstance(old, float) else int(new)


# ---------------------------------------------------------------------- empires

def set_gold(save: Save, empire_id: int, gold: int) -> None:
    empire(save, empire_id)["gold"] = int(gold)


def drain_ai_gold(save: Save) -> int:
    pid = player_id(save)
    n = 0
    for e in save["empires"]:
        if e["empireID"] != pid and e.get("gold", 0) != 0:
            e["gold"] = 0
            n += 1
    return n


def recruit_pool(save: Save, empire_id: int) -> list[dict | None]:
    """Commander recruit slots. The game sets a slot to null once it is recruited."""
    return empire(save, empire_id).get("commanderRecruitPool") or []


def set_commander_level(save: Save, empire_id: int, index: int, level: int) -> bool:
    slot = recruit_pool(save, empire_id)[index]
    if slot is None:
        return False
    slot["level"] = int(level)
    return True


# ---------------------------------------------------- commanders already in armies

# Progression fields copied as a set. The game keeps them consistent with each
# other (e.g. a level 5 commander has 4 levelUpBonuses), so they are never edited
# piecemeal.
PROGRESSION_KEYS = ("level", "levelUpBonuses", "unlockedPropertyIDs")


def commanders(save: Save, empire_id: int | None = None) -> list[tuple[dict, dict]]:
    """(army, character) for every living commander, optionally for one empire."""
    out = []
    for a in save["armies"]:
        if a.get("destroyed") or (empire_id is not None and a["ownerID"] != empire_id):
            continue
        for e in army_entries(a):
            ch = e.get("character")
            if ch and e.get("hasCharacter") and not e.get("deadCharacter"):
                out.append((a, ch))
    return out


def progression_templates(save: Save, target: dict) -> list[tuple[dict, dict]]:
    """Commanders of the same unit type at a higher level than ``target``."""
    tp = target.get("campaignUnitProgress") or {}
    return [(a, ch) for a, ch in commanders(save)
            if ch is not target and ch.get("unitID") == target.get("unitID")
            and (ch.get("campaignUnitProgress") or {}).get("level", 0) > tp.get("level", 0)]


def copy_progression(target: dict, source: dict) -> None:
    """Give ``target`` the level, level-up bonuses and unlocked skills of ``source``."""
    if target.get("unitID") != source.get("unitID"):
        raise ValueError("Can only copy progression between commanders of the same type")
    src = source["campaignUnitProgress"]
    dst = target["campaignUnitProgress"]
    for key in PROGRESSION_KEYS:
        dst[key] = copy.deepcopy(src.get(key))


# ---------------------------------------------------------------------- regions

def claim_region(save: Save, region: dict, empire_id: int) -> None:
    region["ownerID"] = empire_id
    region["explored"] = True
    region["siegeTurns"] = 0
    region["siegeOwnerID"] = UNOWNED
    if region.get("_recruitmentPools") is None:
        region["_recruitmentPools"] = []  # what the game writes on capture


def reveal_map(save: Save) -> int:
    n = 0
    for r in save["regions"]:
        if not r.get("explored"):
            r["explored"] = True
            n += 1
    return n


def end_sieges(save: Save, empire_id: int) -> int:
    n = 0
    for r in owned_regions(save, empire_id):
        if r.get("siegeTurns") or r.get("siegeOwnerID", UNOWNED) != UNOWNED:
            r["siegeTurns"] = 0
            r["siegeOwnerID"] = UNOWNED
            n += 1
    return n


def finish_construction(save: Save, empire_id: int, max_tier: int = DEFAULT_MAX_BUILDING_TIER) -> int:
    n = 0
    for r in owned_regions(save, empire_id):
        for b in r.get("_buildings") or []:
            if b.get("inConstruction"):
                b["inConstruction"] = 0
                b["Tier"] = min(b.get("Tier", 0) + 1, max_tier)
                n += 1
    return n


def max_buildings(save: Save, empire_id: int, max_tier: int = DEFAULT_MAX_BUILDING_TIER) -> int:
    n = 0
    for r in owned_regions(save, empire_id):
        for b in r.get("_buildings") or []:
            if b.get("Tier", 0) < max_tier or b.get("inConstruction"):
                b["Tier"] = max_tier
                b["inConstruction"] = 0
                n += 1
    return n


def finish_settlement_upgrades(save: Save, empire_id: int,
                               max_level: int = DEFAULT_MAX_SETTLEMENT_LEVEL) -> int:
    n = 0
    for r in owned_regions(save, empire_id):
        if r.get("settlementUpgradeTurns", 0) > 0:
            r["settlementUpgradeTurns"] = 0
            r["settlementLevel"] = min(r.get("settlementLevel", 0) + 1, max_level)
            r["settledFromRegionID"] = None
            n += 1
    return n


def max_settlements(save: Save, empire_id: int, max_level: int = DEFAULT_MAX_SETTLEMENT_LEVEL) -> int:
    n = 0
    for r in owned_regions(save, empire_id):
        if r.get("settlementLevel", 0) < max_level:
            r["settlementLevel"] = max_level
            r["settlementUpgradeTurns"] = 0
            r["settledFromRegionID"] = None
            n += 1
    return n


# ------------------------------------------------------------------ recruitment

def player_pools(save: Save, empire_id: int) -> Iterator[tuple[dict, dict]]:
    for r in owned_regions(save, empire_id):
        for p in r.get("_recruitmentPools") or []:
            yield r, p


def refill_pools(save: Save, empire_id: int) -> int:
    n = 0
    for _, p in player_pools(save, empire_id):
        if p["count"] < p["max"]:
            p["count"] = _same_number_type(p["count"], p["max"])
            n += 1
    return n


def raise_pool_max(save: Save, empire_id: int, amount: int, refill: bool = True) -> int:
    n = 0
    for _, p in player_pools(save, empire_id):
        p["max"] = _same_number_type(p["max"], p["max"] + amount)
        if refill:
            p["count"] = _same_number_type(p["count"], p["max"])
        n += 1
    return n


# ----------------------------------------------------------------------- armies

def refresh_armies(save: Save, empire_id: int) -> int:
    n = 0
    for a in armies_of(save, empire_id):
        if a.get("hasActedThisTurn") or a.get("retreatedTurns"):
            a["hasActedThisTurn"] = False
            a["retreatedTurns"] = 0
            n += 1
    return n


def heal_army(army: dict) -> int:
    """Fill every unit back to its max size and clear lingering wounds."""
    n = 0
    for e in army_entries(army):
        prog = e.get("campaignUnitProgress") or {}
        max_size = prog.get("maxUnitSize")
        if max_size and e.get("count", 0) < max_size:
            e["count"] = max_size
            n += 1
        for p in _progress_blocks(e):
            p["leftOverWounds"] = 0
    return n


def heal_armies(save: Save, empire_id: int) -> int:
    return sum(heal_army(a) for a in armies_of(save, empire_id))


def grow_army(army: dict, extra: int) -> int:
    """Raise every unit's max size by ``extra`` and fill it."""
    n = 0
    for e in army_entries(army):
        prog = e.get("campaignUnitProgress")
        if prog and prog.get("maxUnitSize"):
            prog["maxUnitSize"] += extra
            e["count"] = prog["maxUnitSize"]
            n += 1
    return n


def unit_abilities(entry: dict) -> list[str]:
    return list((entry.get("campaignUnitProgress") or {}).get("unlockedPropertyIDs") or [])


def unit_banners(entry: dict) -> list[str]:
    return list(entry.get("magicItems") or [])


def trait_owners(entries: list[dict]) -> dict[str, set[str]]:
    """Ability/banner ID -> unit types (unitID) that carry it, across ``entries``."""
    owners: dict[str, set[str]] = {}
    for e in entries:
        for trait in unit_abilities(e) + unit_banners(e):
            owners.setdefault(trait, set()).add(e.get("unitID"))
    return owners


def copy_traits(target: dict, abilities: list[str], banners: list[str], replace: bool = False) -> tuple[int, int]:
    """Give a unit entry the listed abilities and banners.

    With ``replace`` the target's lists become exactly these; otherwise missing
    ones are appended and nothing is removed. Abilities live in
    campaignUnitProgress.unlockedPropertyIDs, banners in the entry's magicItems.
    Returns (abilities added, banners added).
    """
    prog = target.setdefault("campaignUnitProgress", {})
    old_abilities, old_banners = unit_abilities(target), unit_banners(target)
    if replace:
        new_abilities, new_banners = list(dict.fromkeys(abilities)), list(dict.fromkeys(banners))
    else:
        new_abilities = old_abilities + [a for a in dict.fromkeys(abilities) if a not in old_abilities]
        new_banners = old_banners + [b for b in dict.fromkeys(banners) if b not in old_banners]
    prog["unlockedPropertyIDs"] = new_abilities
    # The game writes null, not [], for a unit with no banners.
    target["magicItems"] = new_banners or (None if target.get("magicItems") is None else [])
    return (len(set(new_abilities) - set(old_abilities)), len(set(new_banners) - set(old_banners)))


def add_xp(army: dict, amount: int) -> int:
    n = 0
    for e in army_entries(army):
        for p in _progress_blocks(e):
            p["xp"] = p.get("xp", 0) + amount
            n += 1
    return n


# ------------------------------------------------------------------- objectives

def player_objectives(save: Save, empire_id: int) -> list[dict]:
    return [o for o in save.get("objectives") or [] if o.get("empireID") == empire_id]


def complete_objective(obj: dict) -> bool:
    """Meet the objective's target so the game awards it through its own logic."""
    if obj.get("status") != OBJECTIVE_ACTIVE or not obj.get("target"):
        return False
    obj["progress"] = obj["target"]
    return True


def complete_all_objectives(save: Save, empire_id: int) -> int:
    return sum(complete_objective(o) for o in player_objectives(save, empire_id))
