"""Edits for the SOVL roguelike campaign save (campaignData/campaignProgress.json).

Loading, writing, backups and restore are shared with save_model, which also
provides the unit helpers (the army list uses the same structure as grand
strategy).

From a save diffed against the game's own .bak:
  * the commander is stored twice - top-level ``Commander`` and
    ``list.characters.entries[0].character`` - and the game updates both together.
  * ``map.path`` lists the nodes already visited, as (x, y) grid points.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Iterator

from . import save_model as m

SAVE_PATH = m.SAVE_PATH.parent.parent / "campaignData" / "campaignProgress.json"

# nodeType -> blueprintName the game uses for it. Boss nodes are never offered.
NODE_BLUEPRINTS = {
    "MinorBattle": "MinorBattle",
    "EliteBattle": "EliteBattle",
    "RandomEvent": "MysteryEvent",
    "Armory": "Armory",
    "CampSite": "CampSite",
}
NODE_LABELS = {
    "MinorBattle": "Battle",
    "EliteBattle": "Elite battle",
    "RandomEvent": "Mystery event",
    "Armory": "Armory (shop)",
    "CampSite": "Campsite (rest)",
    "Boss": "Boss",
}

Save = m.Save


def army(save: Save) -> dict:
    """The run's army in the shape save_model's unit helpers expect."""
    return {"list": save["list"]}


def units(save: Save) -> Iterator[tuple[str, dict]]:
    lst = save["list"]
    for sec in [lst.get("characters"), lst.get("battleLine"), *(lst.get("armyListSections") or [])]:
        for e in (sec or {}).get("entries") or []:
            yield sec.get("sectionName", ""), e


def _commander_copies(save: Save) -> list[dict]:
    """Every stored copy of the commander (top-level plus the one in the army list)."""
    out = []
    top = save.get("Commander")
    if top:
        out.append(top)
    for _, e in units(save):
        ch = e.get("character")
        if ch and top and ch.get("UnitSeed") == top.get("UnitSeed") and ch is not top:
            out.append(ch)
    return out


def sync_commander(save: Save) -> None:
    """Make the top-level Commander match the copy in the army list."""
    copies = _commander_copies(save)
    if len(copies) > 1:
        save["Commander"] = copy.deepcopy(copies[1])


def set_gold(save: Save, gold: int) -> None:
    save["gold"] = int(gold)


def heal(save: Save) -> int:
    n = m.heal_army(army(save))
    sync_commander(save)
    return n


def grow(save: Save, extra: int) -> int:
    return m.grow_army(army(save), extra)


def add_xp(save: Save, amount: int) -> int:
    """Add XP to every unit, and to the commander once (both copies stay equal)."""
    n = 0
    for _, e in units(save):
        prog = e.get("campaignUnitProgress")
        if prog is not None:
            prog["xp"] = prog.get("xp", 0) + amount
            n += 1
    copies = _commander_copies(save)
    if copies:
        prog = copies[-1]["campaignUnitProgress"]
        prog["xp"] = prog.get("xp", 0) + amount
        sync_commander(save)
        n += 1
    return n


# ------------------------------------------------------------------------ map

def visited(save: Save) -> set[tuple[int, int]]:
    return {(p["x"], p["y"]) for p in save["map"].get("path") or []}


def nodes(save: Save) -> list[dict]:
    return save["map"]["nodes"]


def node_key(node: dict) -> tuple[int, int]:
    return node["point"]["x"], node["point"]["y"]


def can_convert(save: Save, node: dict) -> bool:
    """Only nodes in rows ahead of the current position, and never the boss."""
    current = _current_point(save)
    ahead = current is None or node["point"]["y"] > current[1]
    return node.get("nodeType") in NODE_BLUEPRINTS and ahead and node_key(node) not in visited(save)


def _current_point(save: Save) -> tuple[int, int] | None:
    path = save["map"].get("path") or []
    return (path[-1]["x"], path[-1]["y"]) if path else None


def convert_node(save: Save, node: dict, node_type: str) -> bool:
    """Change a future map node's type. Visited nodes and the boss are left alone."""
    if node_type not in NODE_BLUEPRINTS:
        raise ValueError(f"Unsupported node type {node_type!r}")
    if not can_convert(save, node):
        return False
    node["nodeType"] = node_type
    node["blueprintName"] = NODE_BLUEPRINTS[node_type]
    return True


def convert_all(save: Save, from_type: str, to_type: str) -> int:
    return sum(convert_node(save, n, to_type) for n in nodes(save) if n.get("nodeType") == from_type)


def write(save: Save, path: Path = SAVE_PATH, backup_dir: Path = m.BACKUP_DIR) -> Path:
    sync_commander(save)
    return m.write(save, path, backup_dir)
