"""Tests run against a temp copy of the real save; the real file is never written."""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sovl_editor import save_model as m  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "grandStrategyProgress.json"


@pytest.fixture
def save_file(tmp_path):
    # Prefer the live save; fall back to the checked-in sample once a campaign ends.
    src = m.SAVE_PATH if m.SAVE_PATH.exists() else FIXTURE
    if not src.exists():
        pytest.skip("no SOVL grand-strategy save on this machine")
    dest = tmp_path / "grandStrategyProgress.json"
    shutil.copy2(src, dest)
    return dest


@pytest.fixture
def save(save_file):
    return m.load(save_file)


def test_roundtrip_is_byte_identical(save_file):
    raw = save_file.read_bytes().decode("utf-8")
    assert m.dumps(m.load(save_file)) == raw


def test_write_backs_up_and_restore_works(save_file, tmp_path):
    bdir = tmp_path / "backups"
    original = save_file.read_bytes()
    s = m.load(save_file)
    m.set_gold(s, m.player_id(s), 12345)
    bak = m.write(s, save_file, bdir)
    assert bak.read_bytes() == original
    assert m.empire(m.load(save_file), m.player_id(s))["gold"] == 12345
    m.restore(bak, save_file, bdir)
    assert save_file.read_bytes() == original
    assert len(m.list_backups(bdir)) == 2  # the pre-restore file was backed up too


def test_only_requested_fields_change(save):
    before = copy.deepcopy(save)
    m.set_gold(save, m.player_id(save), 500)
    before["empires"][0]["gold"] = 500
    assert save == before


def test_drain_ai_gold_spares_player(save):
    pid = m.player_id(save)
    gold = m.empire(save, pid)["gold"]
    m.drain_ai_gold(save)
    assert m.empire(save, pid)["gold"] == gold
    assert all(e["gold"] == 0 for e in save["empires"] if e["empireID"] != pid)


def test_claim_region(save):
    pid = m.player_id(save)
    r = next(r for r in save["regions"] if r["ownerID"] == m.UNOWNED)
    m.claim_region(save, r, pid)
    assert r["ownerID"] == pid and r["explored"] and r["_recruitmentPools"] is not None
    assert r["siegeTurns"] == 0 and r["siegeOwnerID"] == m.UNOWNED


def test_finish_construction_mirrors_game(save):
    pid = m.player_id(save)
    r = m.owned_regions(save, pid)[0]
    r["_buildings"].append({"Tier": 0, "inConstruction": 1, "buildingSourceID": "farms",
                            "BuildingSource": {"BuildingID": "farms", "RegionTraits": None, "NeedsTerrain": False}})
    assert m.finish_construction(save, pid) >= 1
    assert r["_buildings"][-1]["Tier"] == 1 and r["_buildings"][-1]["inConstruction"] == 0


def test_max_buildings_and_settlements_respect_caps(save):
    pid = m.player_id(save)
    m.max_buildings(save, pid, 3)
    m.max_settlements(save, pid, 2)
    for r in m.owned_regions(save, pid):
        assert r["settlementLevel"] >= 2
        assert all(b["Tier"] >= 3 and b["inConstruction"] == 0 for b in r["_buildings"])


def test_finish_settlement_upgrade(save):
    pid = m.player_id(save)
    r = m.owned_regions(save, pid)[0]
    r["settlementLevel"], r["settlementUpgradeTurns"] = 1, 2
    m.finish_settlement_upgrades(save, pid, 3)
    assert r["settlementLevel"] == 2 and r["settlementUpgradeTurns"] == 0


def test_pools_keep_float_counts(save):
    pid = m.player_id(save)
    m.raise_pool_max(save, pid, 10)
    pools = [p for _, p in m.player_pools(save, pid)]
    assert pools and all(p["count"] == p["max"] for p in pools)
    assert all(isinstance(p["count"], float) and isinstance(p["max"], int) for p in pools)
    assert '"count": 0,' not in m.dumps(save)  # never turns 0.0 into 0


def test_armies_refresh_heal_xp(save):
    pid = m.player_id(save)
    m.refresh_armies(save, pid)
    m.heal_armies(save, pid)
    for a in m.armies_of(save, pid):
        assert not a["hasActedThisTurn"] and a["retreatedTurns"] == 0
        for e in m.army_entries(a):
            assert e["count"] >= e["campaignUnitProgress"]["maxUnitSize"]
            assert e["campaignUnitProgress"]["leftOverWounds"] == 0
    a = m.armies_of(save, pid)[0]
    xp = [e["campaignUnitProgress"]["xp"] for e in m.army_entries(a)]
    m.add_xp(a, 25)
    assert [e["campaignUnitProgress"]["xp"] for e in m.army_entries(a)] == [x + 25 for x in xp]


def test_complete_objective_only_active(save):
    pid = m.player_id(save)
    m.complete_all_objectives(save, pid)
    for o in m.player_objectives(save, pid):
        if o["status"] == m.OBJECTIVE_ACTIVE and o["target"]:
            assert o["progress"] == o["target"]
        if o["status"] == m.OBJECTIVE_HIDDEN:
            assert o["progress"] == 0


def test_everything_still_serialises(save):
    pid = m.player_id(save)
    for fn in (m.reveal_map, m.drain_ai_gold):
        fn(save)
    for fn in (m.finish_construction, m.max_buildings, m.max_settlements, m.end_sieges,
               m.refill_pools, m.refresh_armies, m.heal_armies, m.complete_all_objectives):
        fn(save, pid)
    assert json.loads(m.dumps(save))["empires"]


def test_ui_smoke(save_file, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from sovl_editor import app as ui
    monkeypatch.setattr(m, "game_is_running", lambda: False)
    original = save_file.read_bytes()
    try:
        win = ui.App(save_file, save_file.parent / "none.json", kind=ui.GRAND)
    except tk.TclError:
        pytest.skip("no display")
    win.withdraw()
    win.reload()
    win.max_gold()
    win.q_max_buildings()
    win.q_refill()
    win.q_heal_all()
    win.q_refresh_armies()
    win.complete_all()
    win.reg_tree.selection_set(win.reg_tree.get_children()[0])
    win.claim_selected()
    win.update()
    assert win.dirty
    assert m.empire(win.save, win.pid)["gold"] == 99_999
    win.dirty = False
    win.destroy()
    assert save_file.read_bytes() == original  # UI never auto-saves


def test_empty_recruit_slots_are_handled(save):
    pid = m.player_id(save)
    pool = m.recruit_pool(save, pid)
    pool[0] = None  # the game nulls a slot once it is recruited
    assert m.set_commander_level(save, pid, 0, 9) is False
    assert json.loads(m.dumps(save))


def test_copy_progression_same_type_only(save):
    heroes = m.commanders(save)
    for _, target in heroes:
        for _, source in m.progression_templates(save, target):
            assert source["unitID"] == target["unitID"]
            assert source["campaignUnitProgress"]["level"] > target["campaignUnitProgress"]["level"]
    _, a = heroes[0]
    b = copy.deepcopy(a)
    b["campaignUnitProgress"].update(level=5, levelUpBonuses=[1, 2, 3, 4], unlockedPropertyIDs=["x"])
    m.copy_progression(a, b)
    p = a["campaignUnitProgress"]
    assert (p["level"], p["levelUpBonuses"], p["unlockedPropertyIDs"]) == (5, [1, 2, 3, 4], ["x"])
    p["levelUpBonuses"].append(9)
    assert b["campaignUnitProgress"]["levelUpBonuses"] == [1, 2, 3, 4]  # independent copy
    b["unitID"] = "somethingElse"
    with pytest.raises(ValueError):
        m.copy_progression(a, b)


def test_ui_loads_save_with_empty_slots_and_copies(save_file, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from sovl_editor import app as ui
    monkeypatch.setattr(m, "game_is_running", lambda: False)
    s = m.load(save_file)
    m.recruit_pool(s, m.player_id(s))[0] = None
    m.write(s, save_file, save_file.parent / "b")
    try:
        win = ui.App(save_file, save_file.parent / "none.json", kind=ui.GRAND)
    except tk.TclError:
        pytest.skip("no display")
    win.withdraw()
    win.reload()
    win.update()
    assert win.cmd_tree.item("0", "values")[0].startswith("(empty")
    copied = False
    for iid in win.hero_tree.get_children():
        win.hero_tree.selection_set(iid)
        win._on_hero_select()
        if win._hero_sources:
            _, target = win._heroes[int(iid)]
            _, source = win._hero_sources[0]
            win.copy_hero_progression()
            assert target["campaignUnitProgress"]["level"] == source["campaignUnitProgress"]["level"]
            copied = True
            break
    assert copied or not any(m.progression_templates(win.save, ch) for _, ch in win._heroes)
    win.dirty = False
    win.destroy()
