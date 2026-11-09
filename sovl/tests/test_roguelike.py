"""Roguelike save tests; they run on a temp copy, never the real save."""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sovl_editor import roguelike as rl  # noqa: E402
from sovl_editor import save_model as m  # noqa: E402


@pytest.fixture
def save_file(tmp_path):
    if not rl.SAVE_PATH.exists():
        pytest.skip("no SOVL roguelike save on this machine")
    dest = tmp_path / "campaignProgress.json"
    shutil.copy2(rl.SAVE_PATH, dest)
    return dest


@pytest.fixture
def save(save_file):
    return m.load(save_file)


def commander_in_list(save):
    return next(e["character"] for _, e in rl.units(save) if e.get("character"))


def test_roundtrip_is_byte_identical(save_file):
    assert m.dumps(m.load(save_file)) == save_file.read_bytes().decode("utf-8")


def test_write_names_backup_after_this_save(save_file, tmp_path):
    s = m.load(save_file)
    rl.set_gold(s, 4321)
    bak = rl.write(s, save_file, tmp_path / "b")
    assert bak.name.startswith("campaignProgress_")
    assert m.list_backups(tmp_path / "b", save_file) == [bak]
    assert m.load(save_file)["gold"] == 4321


def test_commander_copies_stay_identical(save):
    rl.add_xp(save, 3)
    rl.heal(save)
    rl.grow(save, 2)
    assert save["Commander"] == commander_in_list(save)


def test_add_xp_adds_once_per_unit_and_commander(save):
    before = [e["campaignUnitProgress"]["xp"] for _, e in rl.units(save)]
    cmd = save["Commander"]["campaignUnitProgress"]["xp"]
    rl.add_xp(save, 2)
    assert [e["campaignUnitProgress"]["xp"] for _, e in rl.units(save)] == [x + 2 for x in before]
    assert save["Commander"]["campaignUnitProgress"]["xp"] == cmd + 2


def test_heal_and_grow(save):
    for _, e in rl.units(save):
        e["count"] = 1
    rl.heal(save)
    assert all(e["count"] == e["campaignUnitProgress"]["maxUnitSize"] for _, e in rl.units(save))
    sizes = [e["campaignUnitProgress"]["maxUnitSize"] for _, e in rl.units(save)]
    rl.grow(save, 4)
    assert [e["count"] for _, e in rl.units(save)] == [x + 4 for x in sizes]


def test_convert_nodes_respects_visited_and_boss(save):
    save["map"]["path"] = save["map"]["path"][:1]  # rewind so the test works at any point in a run
    seen = rl.visited(save)
    boss = [n for n in rl.nodes(save) if n["nodeType"] == "Boss"]
    ahead = [n for n in rl.nodes(save) if rl.can_convert(save, n) and n["nodeType"] == "MinorBattle"]
    behind = [n for n in rl.nodes(save) if not rl.can_convert(save, n)]
    behind_types = [n["nodeType"] for n in behind]
    current_row = save["map"]["path"][-1]["y"]
    assert all(n["point"]["y"] > current_row for n in ahead)
    rl.convert_all(save, "MinorBattle", "Armory")
    assert ahead and all(n["nodeType"] == "Armory" for n in ahead)
    assert [n["nodeType"] for n in behind] == behind_types  # passed/visited nodes untouched
    assert all(n["nodeType"] == "Boss" for n in boss)
    with pytest.raises(ValueError):
        rl.convert_node(save, rl.nodes(save)[0], "Boss")
    mystery = next((n for n in rl.nodes(save) if rl.can_convert(save, n)), None)
    if mystery:
        rl.convert_node(save, mystery, "RandomEvent")
        assert mystery["blueprintName"] == "MysteryEvent"
    for n in rl.nodes(save):
        if rl.node_key(n) in seen:
            assert not rl.convert_node(save, n, "CampSite")
    assert json.loads(m.dumps(save))


def test_ui_roguelike_mode(save_file, tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from sovl_editor import app as ui
    monkeypatch.setattr(m, "game_is_running", lambda: False)
    original = save_file.read_bytes()
    try:
        win = ui.App(tmp_path / "none.json", save_file)
    except tk.TclError:
        pytest.skip("no display")
    win.withdraw()
    win.update()
    win.reload()
    assert win.is_rogue  # the only save present is picked automatically
    win.rl_set_gold(9_999)
    win.rl_heal()
    win.rl_xp()
    win.rl_convert_all()
    win.rl_node_tree.selection_set(win.rl_node_tree.get_children())
    win.rl_convert_selected()
    win.update()
    assert win.save["gold"] == 9_999 and win.dirty
    win.dirty = False
    win.kind.set(ui.GRAND)
    win._on_kind_change()  # missing grand-strategy save must not crash
    assert win.save is None
    win.destroy()
    assert save_file.read_bytes() == original


def test_copy_traits_add_and_replace(save):
    entries = [e for _, e in rl.units(save)]
    src = max(entries, key=lambda e: len(m.unit_abilities(e)) + len(m.unit_banners(e)))
    tgt = next(e for e in entries if e is not src)
    before_a, before_b = m.unit_abilities(tgt), m.unit_banners(tgt)
    m.copy_traits(tgt, m.unit_abilities(src), m.unit_banners(src))
    after_a, after_b = m.unit_abilities(tgt), m.unit_banners(tgt)
    assert after_a[:len(before_a)] == before_a and after_b[:len(before_b)] == before_b  # nothing removed
    assert set(m.unit_abilities(src)) <= set(after_a) and set(m.unit_banners(src)) <= set(after_b)
    assert len(after_a) == len(set(after_a)) and len(after_b) == len(set(after_b))  # no duplicates
    m.copy_traits(tgt, ["x"], [], replace=True)
    assert m.unit_abilities(tgt) == ["x"] and tgt["magicItems"] == []
    assert m.unit_abilities(src)  # source untouched
    assert save["Commander"] == commander_in_list(save)


def test_copy_traits_keeps_null_banners(save):
    e = next(e for _, e in rl.units(save))
    e["magicItems"] = None
    m.copy_traits(e, [], [], replace=True)
    assert e["magicItems"] is None


def test_trait_owners(save):
    owners = m.trait_owners([e for _, e in rl.units(save)])
    for _, e in rl.units(save):
        for t in m.unit_abilities(e) + m.unit_banners(e):
            assert e["unitID"] in owners[t]


def test_copy_dialog(save_file, tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from sovl_editor import app as ui
    monkeypatch.setattr(m, "game_is_running", lambda: False)
    original = save_file.read_bytes()
    try:
        win = ui.App(tmp_path / "none.json", save_file)
    except tk.TclError:
        pytest.skip("no display")
    win.withdraw()
    win.reload()
    dlg = win.rl_copy_dialog()
    dlg.update()
    src = dlg._src()
    dlg.targets.selection_set(0, "end")
    dlg._update_warnings()
    dlg.apply()
    for t in dlg._target_list:
        assert set(m.unit_abilities(src)) <= set(m.unit_abilities(t))
    assert win.dirty and "Copied to" in win.status.get()
    win.dirty = False
    win.reload()  # the dialog must now refuse to edit the stale save
    monkeypatch.setattr(ui.messagebox, "showinfo", lambda *a, **k: None)
    dlg.apply()
    assert not dlg.winfo_exists()
    win.dirty = False
    win.destroy()
    assert save_file.read_bytes() == original
