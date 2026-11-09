"""SOVL Campaign Editor - Tkinter UI for the solo grand-strategy save."""

from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import roguelike as rl
from . import save_model as m

PAD = {"padx": 4, "pady": 3}


def make_tree(parent, columns: list[tuple[str, int]], height: int = 12, select="extended") -> ttk.Treeview:
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c for c, _ in columns], show="headings",
                        height=height, selectmode=select)
    for name, width in columns:
        tree.heading(name, text=name)
        tree.column(name, width=width, anchor="w", stretch=True)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.frame = frame  # type: ignore[attr-defined]
    return tree


def int_entry(parent, width=8) -> tuple[ttk.Spinbox, tk.StringVar]:
    var = tk.StringVar()
    box = ttk.Spinbox(parent, from_=-1, to=10_000_000, textvariable=var, width=width)
    return box, var


def as_int(var: tk.StringVar, what: str) -> int:
    try:
        return int(float(var.get()))
    except ValueError:
        raise ValueError(f"{what} must be a number") from None


GRAND, ROGUE = "Grand strategy", "Roguelike"


def unit_label(e: dict) -> str:
    return f"{e.get('flavourName') or e.get('unitID')}  ({e.get('unitID')}, level " \
           f"{(e.get('campaignUnitProgress') or {}).get('level', '?')})"


class CopyTraitsDialog(tk.Toplevel):
    """Copy chosen abilities and banners from one unit to one or more others."""

    def __init__(self, app: "App", entries: list[dict]):
        super().__init__(app)
        self.app = app
        self._save = app.save
        self.entries = entries
        self.owners = m.trait_owners(entries)
        self.title("Copy abilities & banners")
        self.transient(app)
        self.geometry("900x560")

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Label(top, text="Copy from").pack(side="left", **PAD)
        self.source = ttk.Combobox(top, state="readonly", width=60, values=[unit_label(e) for e in entries])
        self.source.pack(side="left", **PAD)
        self.source.bind("<<ComboboxSelected>>", lambda _e: self._on_source())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8)
        self.ab_frame = ttk.LabelFrame(body, text="Abilities to copy")
        self.ab_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.bn_frame = ttk.LabelFrame(body, text="Banners to copy")
        self.bn_frame.pack(side="left", fill="both", expand=True, padx=4)
        tgt = ttk.LabelFrame(body, text="Copy to (Ctrl/Shift-click for several)")
        tgt.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.targets = tk.Listbox(tgt, selectmode="extended", exportselection=False, width=44)
        self.targets.pack(fill="both", expand=True, padx=4, pady=4)
        self.targets.bind("<<ListboxSelect>>", lambda _e: self._update_warnings())

        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=8, pady=(6, 0))
        self.replace = tk.BooleanVar(value=False)
        ttk.Radiobutton(opts, text="Add to what the target already has", variable=self.replace, value=False,
                        command=self._update_warnings).pack(side="left", **PAD)
        ttk.Radiobutton(opts, text="Replace the target's abilities and banners", variable=self.replace,
                        value=True, command=self._update_warnings).pack(side="left", **PAD)

        self.warning = tk.StringVar()
        ttk.Label(self, textvariable=self.warning, foreground="#b45309", wraplength=860,
                  justify="left").pack(fill="x", padx=8, pady=6)
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Copy", command=self.apply).pack(side="left", **PAD)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", **PAD)

        self.ab_vars: dict[str, tk.BooleanVar] = {}
        self.bn_vars: dict[str, tk.BooleanVar] = {}
        best = max(range(len(entries)), key=lambda i: len(m.unit_abilities(entries[i])) +
                   len(m.unit_banners(entries[i]))) if entries else -1
        if best >= 0:
            self.source.current(best)
            self._on_source()

    def _src(self) -> dict | None:
        i = self.source.current()
        return self.entries[i] if i >= 0 else None

    def _target_entries(self) -> list[dict]:
        return [self._target_list[i] for i in self.targets.curselection()]

    def _checklist(self, frame: ttk.LabelFrame, items: list[str]) -> dict[str, tk.BooleanVar]:
        for w in frame.winfo_children():
            w.destroy()
        out = {}
        if not items:
            ttk.Label(frame, text="(none)", foreground="gray").pack(anchor="w", padx=4)
        for item in items:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(frame, text=item, variable=var, command=self._update_warnings).pack(anchor="w", padx=4)
            out[item] = var
        return out

    def _on_source(self) -> None:
        src = self._src()
        self.ab_vars = self._checklist(self.ab_frame, m.unit_abilities(src))
        self.bn_vars = self._checklist(self.bn_frame, m.unit_banners(src))
        self._target_list = [e for e in self.entries if e is not src]
        self.targets.delete(0, "end")
        for e in self._target_list:
            self.targets.insert("end", unit_label(e))
        self._update_warnings()

    def chosen(self) -> tuple[list[str], list[str]]:
        return ([k for k, v in self.ab_vars.items() if v.get()], [k for k, v in self.bn_vars.items() if v.get()])

    def warnings(self) -> list[str]:
        abilities, banners = self.chosen()
        out = []
        for t in self._target_entries():
            name = t.get("flavourName") or t.get("unitID")
            odd = [a for a in abilities if t.get("unitID") not in self.owners.get(a, set())]
            if odd:
                out.append(f"{name}: no {t.get('unitID')} in your army has {', '.join(odd)} - it may be "
                           "specific to another unit type and do nothing (or misbehave) here.")
            have = [] if self.replace.get() else m.unit_banners(t)
            total = len(dict.fromkeys(have + banners))
            if total > m.OBSERVED_MAX_BANNERS:
                out.append(f"{name}: would carry {total} banners; no unit in your save has more than "
                           f"{m.OBSERVED_MAX_BANNERS}, which may be the game's limit.")
        return out

    def _update_warnings(self) -> None:
        if not self._target_entries():
            self.warning.set("Pick one or more units to copy to.")
            return
        w = self.warnings()
        self.warning.set("\n".join(w) if w else "No problems spotted.")

    def apply(self) -> None:
        if self.app.save is not self._save:
            messagebox.showinfo("Save reloaded", "The save was reloaded or switched; reopen this dialog.",
                                parent=self)
            self.destroy()
            return
        targets = self._target_entries()
        abilities, banners = self.chosen()
        if not targets or not (abilities or banners or self.replace.get()):
            return
        added_a = added_b = 0
        for t in targets:
            a, b = m.copy_traits(t, abilities, banners, self.replace.get())
            added_a, added_b = added_a + a, added_b + b
        self.app.changed(f"Copied to {len(targets)} unit(s): {added_a} new abilit(ies), {added_b} new banner(s)")
        self.owners = m.trait_owners(self.entries)
        sel = self.targets.curselection()
        self._on_source()
        for i in sel:
            self.targets.selection_set(i)
        self._update_warnings()


class _RogueTabs:
    """Roguelike-campaign tabs, mixed into App."""

    # --------------------------------------------------------------------- army

    def _build_rogue_army_tab(self) -> None:
        tab = ttk.Frame(self.rl_nb)
        self.rl_nb.add(tab, text="Gold & Army")
        top = ttk.LabelFrame(tab, text="Gold")
        top.pack(fill="x", padx=6, pady=6)
        box, self.rl_gold = int_entry(top, 10)
        box.pack(side="left", **PAD)
        ttk.Button(top, text="Set gold", command=self.rl_set_gold).pack(side="left", **PAD)
        ttk.Button(top, text="Gold = 9,999", command=lambda: self.rl_set_gold(9_999)).pack(side="left", **PAD)

        quick = ttk.LabelFrame(tab, text="Army")
        quick.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(quick, text="Heal all units", command=self.rl_heal).pack(side="left", **PAD)
        ttk.Label(quick, text="Grow every unit by").pack(side="left", **PAD)
        box, self.rl_grow_by = int_entry(quick, 5)
        self.rl_grow_by.set("5")
        box.pack(side="left")
        ttk.Button(quick, text="Grow", command=self.rl_grow).pack(side="left", **PAD)
        ttk.Label(quick, text="Add").pack(side="left", **PAD)
        box, self.rl_xp_by = int_entry(quick, 5)
        self.rl_xp_by.set("1")
        box.pack(side="left")
        ttk.Button(quick, text="XP to every unit + commander", command=self.rl_xp).pack(side="left", **PAD)
        ttk.Button(quick, text="Copy abilities & banners...", command=self.rl_copy_dialog).pack(side="left", **PAD)

        self.rl_unit_tree = make_tree(tab, [("Section", 95), ("Unit", 130), ("Name", 200), ("Models", 55),
                                            ("Max size", 60), ("Level", 45), ("XP", 40), ("Abilities", 60),
                                            ("Banners", 60), ("Wounds", 55)])
        self.rl_unit_tree.frame.pack(fill="both", expand=True, padx=6)
        self.rl_unit_tree.bind("<<TreeviewSelect>>", self._rl_on_unit_select)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=6, pady=6)
        self.rl_unit_vars: dict[str, tk.StringVar] = {}
        for key, label in [("count", "Models"), ("maxUnitSize", "Max size"), ("xp", "XP")]:
            ttk.Label(row, text=label).pack(side="left", **PAD)
            box, var = int_entry(row, 6)
            box.pack(side="left")
            self.rl_unit_vars[key] = var
        ttk.Button(row, text="Apply to selected units", command=self.rl_apply_units).pack(side="left", **PAD)
        ttk.Label(tab, foreground="gray", wraplength=1000, justify="left", text=(
            "XP is small in this mode (units start at 0-1), so add a little at a time. Levels aren't edited "
            "directly: in this mode a level carries no saved bonuses, and the game's level rules aren't in "
            "the save.")).pack(anchor="w", padx=6, pady=(0, 6))
        self._rl_units: dict[str, dict] = {}

    def refresh_rogue_army(self) -> None:
        s = self.save
        self.rl_gold.set(str(s.get("gold", 0)))
        sel = self.rl_unit_tree.selection()
        self.rl_unit_tree.delete(*self.rl_unit_tree.get_children())
        self._rl_units.clear()
        for i, (section, e) in enumerate(rl.units(s)):
            prog = e.get("campaignUnitProgress") or {}
            name = e.get("flavourName") or ""
            if e.get("character"):
                name = f"{e['character'].get('flavourName', '')} + {name}"
            self._rl_units[str(i)] = e
            self.rl_unit_tree.insert("", "end", iid=str(i), values=(
                section, e.get("unitID"), name, e.get("count"), prog.get("maxUnitSize"), prog.get("level"),
                prog.get("xp"), len(m.unit_abilities(e)), len(m.unit_banners(e)), prog.get("leftOverWounds", 0)))
        keep = [i for i in sel if self.rl_unit_tree.exists(i)]
        if keep:
            self.rl_unit_tree.selection_set(keep)

    def _rl_on_unit_select(self, _e=None) -> None:
        sel = self.rl_unit_tree.selection()
        if not sel:
            return
        e = self._rl_units[sel[0]]
        prog = e.get("campaignUnitProgress") or {}
        self.rl_unit_vars["count"].set(str(e.get("count", 0)))
        self.rl_unit_vars["maxUnitSize"].set(str(prog.get("maxUnitSize", 0)))
        self.rl_unit_vars["xp"].set(str(prog.get("xp", 0)))

    def rl_apply_units(self) -> None:
        sel = self.rl_unit_tree.selection()
        for iid in sel:
            e = self._rl_units[iid]
            prog = e.setdefault("campaignUnitProgress", {})
            prog["maxUnitSize"] = as_int(self.rl_unit_vars["maxUnitSize"], "Max size")
            prog["xp"] = as_int(self.rl_unit_vars["xp"], "XP")
            e["count"] = as_int(self.rl_unit_vars["count"], "Models")
        self.changed(f"Updated {len(sel)} unit(s)")

    def rl_copy_dialog(self) -> "CopyTraitsDialog":
        return CopyTraitsDialog(self, [e for _, e in rl.units(self.save)])

    def rl_set_gold(self, gold: int | None = None) -> None:
        rl.set_gold(self.save, as_int(self.rl_gold, "Gold") if gold is None else gold)
        self.changed(f"Gold set to {self.save['gold']:,}")

    def rl_heal(self) -> None:
        self.changed(f"Healed {rl.heal(self.save)} unit(s)")

    def rl_grow(self) -> None:
        self.changed(f"Grew {rl.grow(self.save, as_int(self.rl_grow_by, 'Amount'))} unit(s)")

    def rl_xp(self) -> None:
        self.changed(f"Gave XP to {rl.add_xp(self.save, as_int(self.rl_xp_by, 'XP'))} unit(s)/commander")

    # ---------------------------------------------------------------------- map

    def _build_rogue_map_tab(self) -> None:
        tab = ttk.Frame(self.rl_nb)
        self.rl_nb.add(tab, text="Map")
        ttk.Label(tab, wraplength=1000, justify="left", text=(
            "Change what upcoming map nodes are (rows ahead of you). Visited or passed nodes and the boss "
            "can't be changed. Each node keeps its own random seed, so the game builds the new node type "
            "normally.")
        ).pack(anchor="w", padx=6, pady=6)
        quick = ttk.LabelFrame(tab, text="Convert every upcoming node of one type")
        quick.pack(fill="x", padx=6)
        labels = [rl.NODE_LABELS[t] for t in rl.NODE_BLUEPRINTS]
        ttk.Label(quick, text="From").pack(side="left", **PAD)
        self.rl_from = ttk.Combobox(quick, state="readonly", width=16, values=labels)
        self.rl_from.current(0)
        self.rl_from.pack(side="left", **PAD)
        ttk.Label(quick, text="to").pack(side="left", **PAD)
        self.rl_to = ttk.Combobox(quick, state="readonly", width=16, values=labels)
        self.rl_to.current(labels.index(rl.NODE_LABELS["Armory"]))
        self.rl_to.pack(side="left", **PAD)
        ttk.Button(quick, text="Convert all", command=self.rl_convert_all).pack(side="left", **PAD)

        self.rl_node_tree = make_tree(tab, [("Row", 50), ("Column", 60), ("Type", 160), ("Status", 120),
                                            ("Quest", 60)], height=16)
        self.rl_node_tree.frame.pack(fill="both", expand=True, padx=6, pady=6)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(row, text="Change selected to").pack(side="left", **PAD)
        self.rl_node_to = ttk.Combobox(row, state="readonly", width=16, values=labels)
        self.rl_node_to.current(labels.index(rl.NODE_LABELS["CampSite"]))
        self.rl_node_to.pack(side="left", **PAD)
        ttk.Button(row, text="Apply", command=self.rl_convert_selected).pack(side="left", **PAD)
        self._rl_nodes: list[dict] = []

    def _rl_type_from_label(self, box: ttk.Combobox) -> str:
        return list(rl.NODE_BLUEPRINTS)[box.current()]

    def refresh_rogue_map(self) -> None:
        s = self.save
        sel = self.rl_node_tree.selection()
        self.rl_node_tree.delete(*self.rl_node_tree.get_children())
        self._rl_nodes = sorted(rl.nodes(s), key=lambda n: (n["point"]["y"], n["point"]["x"]))
        seen = rl.visited(s)
        for i, n in enumerate(self._rl_nodes):
            key = rl.node_key(n)
            if key in seen:
                status = "visited"
            elif n.get("nodeType") == "Boss":
                status = "boss"
            elif not rl.can_convert(s, n):
                status = "passed"
            else:
                status = "upcoming"
            self.rl_node_tree.insert("", "end", iid=str(i), values=(
                key[1], key[0], rl.NODE_LABELS.get(n.get("nodeType"), n.get("nodeType")), status,
                "yes" if n.get("isQuest") else ""))
        keep = [i for i in sel if self.rl_node_tree.exists(i)]
        if keep:
            self.rl_node_tree.selection_set(keep)

    def rl_convert_selected(self) -> None:
        to = self._rl_type_from_label(self.rl_node_to)
        sel = self.rl_node_tree.selection()
        n = sum(rl.convert_node(self.save, self._rl_nodes[int(i)], to) for i in sel)
        skipped = len(sel) - n
        self.changed(f"Changed {n} node(s) to {rl.NODE_LABELS[to]}"
                     + (f"; skipped {skipped} visited/boss node(s)" if skipped else ""))

    def rl_convert_all(self) -> None:
        frm, to = self._rl_type_from_label(self.rl_from), self._rl_type_from_label(self.rl_to)
        if frm == to:
            return
        n = rl.convert_all(self.save, frm, to)
        self.changed(f"Changed {n} upcoming {rl.NODE_LABELS[frm]} node(s) to {rl.NODE_LABELS[to]}")


class App(_RogueTabs, tk.Tk):
    def __init__(self, path: Path = m.SAVE_PATH, rogue_path: Path = rl.SAVE_PATH, kind: str | None = None):
        super().__init__()
        self.title("SOVL Campaign Editor")
        self.geometry("1100x760")
        self.minsize(900, 560)
        self.paths = {GRAND: path, ROGUE: rogue_path}
        self.save: m.Save | None = None
        self.dirty = False
        self.status = tk.StringVar()
        self.max_tier = tk.StringVar(value=str(m.DEFAULT_MAX_BUILDING_TIER))
        self.max_level = tk.StringVar(value=str(m.DEFAULT_MAX_SETTLEMENT_LEVEL))
        self.kind = tk.StringVar(value=kind or self._default_kind())

        self._build_toolbar()
        ttk.Label(self, textvariable=self.status, anchor="w", relief="sunken").pack(fill="x", side="bottom")
        self.nb = ttk.Notebook(self)
        self._build_empire_tab()
        self._build_regions_tab()
        self._build_recruit_tab()
        self._build_armies_tab()
        self._build_objectives_tab()
        self.rl_nb = ttk.Notebook(self)
        self._build_rogue_army_tab()
        self._build_rogue_map_tab()
        self._show_kind()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.report_callback_exception = self._on_error
        self.after(50, self.reload)

    @property
    def path(self) -> Path:
        return self.paths[self.kind.get()]

    @property
    def is_rogue(self) -> bool:
        return self.kind.get() == ROGUE

    def _default_kind(self) -> str:
        """Open whichever campaign save was written most recently."""
        found = [(p.stat().st_mtime, k) for k, p in self.paths.items() if p.exists()]
        return max(found)[1] if found else GRAND

    def _show_kind(self) -> None:
        self.nb.pack_forget()
        self.rl_nb.pack_forget()
        (self.rl_nb if self.is_rogue else self.nb).pack(fill="both", expand=True, padx=6, pady=4)
        if self.is_rogue:
            self._grand_bar.pack_forget()
        else:
            self._grand_bar.pack(side="left")

    def _on_kind_change(self, _e=None) -> None:
        if self.dirty and not messagebox.askyesno("Discard changes?", "Discard unsaved edits and switch campaign?"):
            self.kind.set(ROGUE if self._loaded_kind == GRAND else GRAND)
            return
        self.dirty = False
        self.save = None
        self._show_kind()
        self.reload()

    # ----------------------------------------------------------------- plumbing

    def _on_error(self, exc, val, tb):
        messagebox.showerror("Error", str(val), parent=self)

    @property
    def pid(self) -> int:
        return m.player_id(self.save)

    def set_status(self, text: str) -> None:
        mark = "  [unsaved changes]" if self.dirty else ""
        self.status.set(f"{text}{mark}")

    def changed(self, text: str) -> None:
        self.dirty = True
        self.refresh_all()
        self.set_status(text)

    def cap_tier(self) -> int:
        return as_int(self.max_tier, "Max building tier")

    def cap_level(self) -> int:
        return as_int(self.max_level, "Max settlement level")

    # ------------------------------------------------------------------ toolbar

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Label(bar, text="Campaign").pack(side="left")
        box = ttk.Combobox(bar, state="readonly", width=15, values=[GRAND, ROGUE], textvariable=self.kind)
        box.pack(side="left", **PAD)
        box.bind("<<ComboboxSelected>>", self._on_kind_change)
        self._loaded_kind = self.kind.get()
        ttk.Button(bar, text="Reload", command=self.reload).pack(side="left", **PAD)
        ttk.Button(bar, text="Save to game", command=self.write).pack(side="left", **PAD)
        ttk.Button(bar, text="Restore backup...", command=self.restore_dialog).pack(side="left", **PAD)
        ttk.Button(bar, text="Open backups folder", command=self.open_backups).pack(side="left", **PAD)
        self._grand_bar = ttk.Frame(bar)  # controls that only apply to grand strategy
        ttk.Separator(self._grand_bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(self._grand_bar, text="Max building tier").pack(side="left")
        ttk.Spinbox(self._grand_bar, from_=1, to=10, width=4, textvariable=self.max_tier).pack(side="left", **PAD)
        ttk.Label(self._grand_bar, text="Max settlement level").pack(side="left")
        ttk.Spinbox(self._grand_bar, from_=1, to=10, width=4, textvariable=self.max_level).pack(side="left", **PAD)
        self.info = tk.StringVar()
        ttk.Label(bar, textvariable=self.info).pack(side="right", **PAD)

    def reload(self) -> None:
        if self.dirty and not messagebox.askyesno("Discard changes?", "Discard unsaved edits and reload?"):
            return
        self._loaded_kind = self.kind.get()
        if not self.path.exists():
            self.save = None
            self.dirty = False
            self.info.set("")
            self.set_status(f"No {self.kind.get().lower()} save found ({self.path}). Start a "
                            f"{self.kind.get().lower()} campaign in SOVL, or switch campaign above.")
            return
        self.save = m.load(self.path)
        self.dirty = False
        self.refresh_all()
        msg = f"Loaded {self.path.name}"
        if m.game_is_running():
            msg += "  -  SOVL is running: close it before saving, or the game will overwrite your edits"
        self.set_status(msg)

    def write(self) -> None:
        if self.save is None:
            return
        if m.game_is_running():
            messagebox.showwarning("Close SOVL first",
                                   "SOVL is running. Quit to the desktop before saving, otherwise the game "
                                   "will overwrite the edited save.")
            return
        if not messagebox.askyesno("Save", "Write changes to the campaign save?\n"
                                   "(A backup of the current file is made first.)"):
            return
        bak = rl.write(self.save, self.path) if self.is_rogue else m.write(self.save, self.path)
        self.dirty = False
        self.set_status(f"Saved. Backup: {bak.name}")

    def open_backups(self) -> None:
        m.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(m.BACKUP_DIR)  # noqa: S606 - Windows only, opens Explorer

    def restore_dialog(self) -> None:
        backups = m.list_backups(m.BACKUP_DIR, self.path)
        if not backups:
            messagebox.showinfo("Restore", f"No {self.kind.get().lower()} backups yet. "
                                "One is created every time you save.")
            return
        if m.game_is_running():
            messagebox.showwarning("Close SOVL first", "Quit SOVL before restoring a backup.")
            return
        win = tk.Toplevel(self)
        win.title("Restore backup")
        win.transient(self)
        lb = tk.Listbox(win, width=60, height=15)
        for b in backups:
            lb.insert("end", b.name)
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        lb.selection_set(0)

        def do_restore():
            sel = lb.curselection()
            if not sel:
                return
            m.restore(backups[sel[0]], self.path)
            win.destroy()
            self.dirty = False
            self.reload()
            self.set_status(f"Restored {backups[sel[0]].name}")

        ttk.Button(win, text="Restore selected", command=do_restore).pack(pady=(0, 8))

    def on_close(self) -> None:
        if self.dirty and not messagebox.askyesno("Quit", "You have unsaved changes. Quit anyway?"):
            return
        self.destroy()

    def refresh_all(self) -> None:
        if self.save is None:
            return
        s = self.save
        if self.is_rogue:
            act = 3 if s.get("isAct3") else 2 if s.get("isAct2") else 1
            self.info.set(f"{s.get('Commander', {}).get('flavourName', '')}  |  act {act}  |  "
                          f"step {s.get('step')}  |  gold {s.get('gold')}")
            self.refresh_rogue_army()
            self.refresh_rogue_map()
            return
        self.info.set(f"{m.empire_name(s, self.pid)}  |  round {s.get('CurrentRound')}  |  "
                      f"map {s.get('mapId')}  |  gold {m.empire(s, self.pid).get('gold')}")
        self.refresh_empire()
        self.refresh_regions()
        self.refresh_recruit()
        self.refresh_armies()
        self.refresh_objectives()

    # ------------------------------------------------------------------- empire

    def _build_empire_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Empire")
        top = ttk.LabelFrame(tab, text="Treasury & stats")
        top.pack(fill="x", padx=6, pady=6)
        ttk.Label(top, text="Empire").grid(row=0, column=0, sticky="w", **PAD)
        self.emp_choice = ttk.Combobox(top, state="readonly", width=28)
        self.emp_choice.grid(row=0, column=1, sticky="w", **PAD)
        self.emp_choice.bind("<<ComboboxSelected>>", lambda _e: self.refresh_empire(keep_choice=True))

        self.emp_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate([("gold", "Gold"), ("battlesWon", "Battles won"),
                                          ("regionsTaken", "Regions taken")]):
            ttk.Label(top, text=label).grid(row=1 + i, column=0, sticky="w", **PAD)
            box, var = int_entry(top, 12)
            box.grid(row=1 + i, column=1, sticky="w", **PAD)
            self.emp_vars[key] = var
        btns = ttk.Frame(top)
        btns.grid(row=1, column=2, rowspan=3, sticky="nw", padx=20)
        ttk.Button(btns, text="Apply", command=self.apply_empire).pack(fill="x", **PAD)
        ttk.Button(btns, text="My gold = 99,999", command=self.max_gold).pack(fill="x", **PAD)
        ttk.Button(btns, text="Drain all AI gold to 0", command=self.drain_ai).pack(fill="x", **PAD)

        mine = ttk.LabelFrame(tab, text="Commanders in your armies")
        mine.pack(fill="both", expand=True, padx=6, pady=6)
        self.hero_tree = make_tree(mine, [("Name", 220), ("Unit", 140), ("Army", 180), ("Level", 50),
                                          ("Level-up bonuses", 70), ("Skills", 260)], height=5, select="browse")
        self.hero_tree.frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.hero_tree.bind("<<TreeviewSelect>>", self._on_hero_select)
        row = ttk.Frame(mine)
        row.pack(fill="x")
        ttk.Label(row, text="Copy level, bonuses & skills from").pack(side="left", **PAD)
        self.hero_source = ttk.Combobox(row, state="readonly", width=60)
        self.hero_source.pack(side="left", **PAD)
        ttk.Button(row, text="Copy", command=self.copy_hero_progression).pack(side="left", **PAD)
        ttk.Label(mine, foreground="gray", wraplength=1000, justify="left", text=(
            "Only commanders of the same type at a higher level can be used (from any empire). Copying the "
            "full set keeps the save consistent; to get a higher-level commander of a new type, raise a "
            "recruit's level below before recruiting them - the game then rolls their bonuses itself.")
        ).pack(anchor="w", padx=4, pady=(0, 4))
        self._heroes: list[tuple[dict, dict]] = []
        self._hero_sources: list[tuple[dict, dict]] = []

        cmd = ttk.LabelFrame(tab, text="Commander recruit pool (not yet recruited)")
        cmd.pack(fill="both", expand=True, padx=6, pady=6)
        self.cmd_tree = make_tree(cmd, [("Name", 220), ("Unit", 160), ("Level", 60)], height=4, select="browse")
        self.cmd_tree.frame.pack(fill="both", expand=True, padx=4, pady=4)
        row = ttk.Frame(cmd)
        row.pack(fill="x")
        ttk.Label(row, text="Level").pack(side="left", **PAD)
        box, self.cmd_level = int_entry(row, 6)
        box.pack(side="left", **PAD)
        ttk.Button(row, text="Set level on selected", command=self.set_cmd_level).pack(side="left", **PAD)
        ttk.Button(row, text="+1 level to all", command=self.bump_cmd_levels).pack(side="left", **PAD)
        self.cmd_tree.bind("<<TreeviewSelect>>", self._on_cmd_select)

    def _selected_empire_id(self) -> int:
        idx = self.emp_choice.current()
        return self.save["empires"][idx]["empireID"] if idx >= 0 else self.pid

    def refresh_empire(self, keep_choice: bool = False) -> None:
        s = self.save
        names = [f"{e['empireName']}{'  (you)' if e.get('isPlayerFaction') else ''}" for e in s["empires"]]
        current = self.emp_choice.current()
        self.emp_choice["values"] = names
        if not keep_choice or current < 0:
            current = current if current >= 0 else next(
                i for i, e in enumerate(s["empires"]) if e["empireID"] == self.pid)
        self.emp_choice.current(current)
        e = m.empire(s, self._selected_empire_id())
        for key, var in self.emp_vars.items():
            var.set(str(e.get(key, 0)))
        self.cmd_tree.delete(*self.cmd_tree.get_children())
        for i, c in enumerate(m.recruit_pool(s, e["empireID"])):
            if c is None:
                values = ("(empty - already recruited)", "", "")
            else:
                values = (c.get("name", "").strip(), c.get("unitID"), c.get("level"))
            self.cmd_tree.insert("", "end", iid=str(i), values=values)

        sel = self.hero_tree.selection()
        self.hero_tree.delete(*self.hero_tree.get_children())
        self._heroes = m.commanders(s, e["empireID"])
        for i, (army, ch) in enumerate(self._heroes):
            prog = ch.get("campaignUnitProgress") or {}
            self.hero_tree.insert("", "end", iid=str(i), values=(
                ch.get("flavourName"), ch.get("unitID"), army.get("armyName"), prog.get("level"),
                len(prog.get("levelUpBonuses") or []), ", ".join(prog.get("unlockedPropertyIDs") or [])))
        if sel and self.hero_tree.exists(sel[0]):
            self.hero_tree.selection_set(sel)
        self._on_hero_select()

    def _on_hero_select(self, _e=None) -> None:
        sel = self.hero_tree.selection()
        self._hero_sources = []
        if sel:
            _, target = self._heroes[int(sel[0])]
            self._hero_sources = m.progression_templates(self.save, target)
        labels = []
        for army, ch in self._hero_sources:
            prog = ch["campaignUnitProgress"]
            skills = ", ".join(prog.get("unlockedPropertyIDs") or []) or "no skills"
            labels.append(f"{ch.get('flavourName')} ({m.empire_name(self.save, army['ownerID'])}) "
                          f"- level {prog.get('level')}, {skills}")
        self.hero_source["values"] = labels
        if labels:
            self.hero_source.current(0)
        else:
            self.hero_source.set("(select a commander)" if not sel else "(no higher-level commander of this type)")

    def copy_hero_progression(self) -> None:
        sel = self.hero_tree.selection()
        idx = self.hero_source.current()
        if not sel or idx < 0 or not self._hero_sources:
            return
        _, target = self._heroes[int(sel[0])]
        _, source = self._hero_sources[idx]
        m.copy_progression(target, source)
        self.changed(f"{target.get('flavourName')} is now level {target['campaignUnitProgress']['level']}")

    def apply_empire(self) -> None:
        e = m.empire(self.save, self._selected_empire_id())
        for key, var in self.emp_vars.items():
            e[key] = as_int(var, key)
        self.changed(f"Updated {e['empireName']}")

    def max_gold(self) -> None:
        m.set_gold(self.save, self.pid, 99_999)
        self.changed("Gold set to 99,999")

    def drain_ai(self) -> None:
        n = m.drain_ai_gold(self.save)
        self.changed(f"Drained gold from {n} AI empire(s)")

    def _on_cmd_select(self, _e=None) -> None:
        sel = self.cmd_tree.selection()
        if sel:
            self.cmd_level.set(self.cmd_tree.item(sel[0], "values")[2])

    def set_cmd_level(self) -> None:
        sel = self.cmd_tree.selection()
        if not sel:
            return
        if m.set_commander_level(self.save, self._selected_empire_id(), int(sel[0]),
                                 as_int(self.cmd_level, "Level")):
            self.changed("Recruit level updated")
        else:
            self.set_status("That slot is empty - the commander was already recruited")

    def bump_cmd_levels(self) -> None:
        pool = [c for c in m.recruit_pool(self.save, self._selected_empire_id()) if c is not None]
        for c in pool:
            c["level"] = c.get("level", 1) + 1
        self.changed(f"Raised {len(pool)} commander(s) by one level")

    # ------------------------------------------------------------------ regions

    def _build_regions_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Regions & Buildings")
        quick = ttk.LabelFrame(tab, text="Quick actions (your regions)")
        quick.pack(fill="x", padx=6, pady=6)
        for text, fn in [("Finish all construction", self.q_finish_construction),
                         ("Max all buildings", self.q_max_buildings),
                         ("Finish settlement upgrades", self.q_finish_settlements),
                         ("Max all settlements", self.q_max_settlements),
                         ("End sieges", self.q_end_sieges),
                         ("Reveal whole map", self.q_reveal)]:
            ttk.Button(quick, text=text, command=fn).pack(side="left", **PAD)

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=6)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        self.reg_mine_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Only show my regions", variable=self.reg_mine_only,
                        command=self.refresh_regions).pack(anchor="w")
        self.reg_tree = make_tree(left, [("ID", 40), ("Owner", 150), ("Settlement", 75), ("Upgrade", 60),
                                         ("Growth", 55), ("Explored", 60), ("Siege", 70), ("Buildings", 70)])
        self.reg_tree.frame.pack(fill="both", expand=True)
        self.reg_tree.bind("<<TreeviewSelect>>", self._on_region_select)
        row = ttk.Frame(left)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Claim selected for me", command=self.claim_selected).pack(side="left", **PAD)

        edit = ttk.LabelFrame(right, text="Edit selected region")
        edit.pack(fill="x")
        ttk.Label(edit, text="Owner").grid(row=0, column=0, sticky="w", **PAD)
        self.reg_owner = ttk.Combobox(edit, state="readonly", width=24)
        self.reg_owner.grid(row=0, column=1, sticky="w", **PAD)
        self.reg_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate([("settlementLevel", "Settlement level"), ("growth", "Growth"),
                                          ("settlementUpgradeTurns", "Upgrade turns left")]):
            ttk.Label(edit, text=label).grid(row=1 + i, column=0, sticky="w", **PAD)
            box, var = int_entry(edit)
            box.grid(row=1 + i, column=1, sticky="w", **PAD)
            self.reg_vars[key] = var
        self.reg_explored = tk.BooleanVar()
        ttk.Checkbutton(edit, text="Explored", variable=self.reg_explored).grid(row=4, column=1, sticky="w", **PAD)
        ttk.Button(edit, text="Apply to region", command=self.apply_region).grid(row=5, column=1, sticky="w", **PAD)

        bld = ttk.LabelFrame(right, text="Buildings in selected region")
        bld.pack(fill="both", expand=True, pady=(6, 0))
        self.bld_tree = make_tree(bld, [("Building", 140), ("Tier", 50), ("Building now", 90)], height=6,
                                  select="browse")
        self.bld_tree.frame.pack(fill="both", expand=True, padx=4, pady=4)
        row = ttk.Frame(bld)
        row.pack(fill="x")
        ttk.Label(row, text="Tier").pack(side="left", **PAD)
        box, self.bld_tier = int_entry(row, 5)
        box.pack(side="left", **PAD)
        ttk.Button(row, text="Set tier (finishes build)", command=self.set_building_tier).pack(side="left", **PAD)

    def _owner_options(self) -> list[tuple[int, str]]:
        return [(m.UNOWNED, "(unowned)")] + [(e["empireID"], e["empireName"]) for e in self.save["empires"]]

    def refresh_regions(self) -> None:
        s = self.save
        if s is None:
            return
        sel = self.reg_tree.selection()
        self.reg_tree.delete(*self.reg_tree.get_children())
        for i, r in enumerate(s["regions"]):
            if self.reg_mine_only.get() and r["ownerID"] != self.pid:
                continue
            siege = "-" if not r.get("siegeTurns") else f"{m.empire_name(s, r['siegeOwnerID'])[:8]} {r['siegeTurns']}t"
            owner = m.empire_name(s, r["ownerID"]) + ("  (you)" if r["ownerID"] == self.pid else "")
            self.reg_tree.insert("", "end", iid=str(i), values=(
                r["regionID"], owner, r.get("settlementLevel"), r.get("settlementUpgradeTurns"),
                r.get("growth"), "yes" if r.get("explored") else "no", siege, len(r.get("_buildings") or [])))
        keep = [i for i in sel if self.reg_tree.exists(i)]
        if keep:
            self.reg_tree.selection_set(keep)
        self.reg_owner["values"] = [name for _, name in self._owner_options()]
        self._on_region_select()

    def _selected_regions(self) -> list[dict]:
        return [self.save["regions"][int(i)] for i in self.reg_tree.selection()]

    def _on_region_select(self, _e=None) -> None:
        regs = self._selected_regions()
        self.bld_tree.delete(*self.bld_tree.get_children())
        if not regs:
            return
        r = regs[0]
        ids = [oid for oid, _ in self._owner_options()]
        self.reg_owner.current(ids.index(r["ownerID"]) if r["ownerID"] in ids else 0)
        for key, var in self.reg_vars.items():
            var.set(str(r.get(key, 0)))
        self.reg_explored.set(bool(r.get("explored")))
        for i, b in enumerate(r.get("_buildings") or []):
            self.bld_tree.insert("", "end", iid=str(i), values=(
                b.get("buildingSourceID"), b.get("Tier"), "yes" if b.get("inConstruction") else "no"))

    def apply_region(self) -> None:
        regs = self._selected_regions()
        if not regs:
            return
        r = regs[0]
        owner = self._owner_options()[self.reg_owner.current()][0]
        if owner != r["ownerID"]:
            if owner == m.UNOWNED:
                r["ownerID"] = m.UNOWNED
            else:
                m.claim_region(self.save, r, owner)
        for key, var in self.reg_vars.items():
            r[key] = as_int(var, key)
        r["explored"] = self.reg_explored.get()
        self.changed(f"Region {r['regionID']} updated")

    def claim_selected(self) -> None:
        regs = self._selected_regions()
        for r in regs:
            m.claim_region(self.save, r, self.pid)
        self.changed(f"Claimed {len(regs)} region(s)")

    def set_building_tier(self) -> None:
        regs = self._selected_regions()
        sel = self.bld_tree.selection()
        if not regs or not sel:
            return
        b = regs[0]["_buildings"][int(sel[0])]
        b["Tier"] = as_int(self.bld_tier, "Tier")
        b["inConstruction"] = 0
        self.changed(f"{b['buildingSourceID']} set to tier {b['Tier']}")

    def q_finish_construction(self):
        self.changed(f"Finished {m.finish_construction(self.save, self.pid, self.cap_tier())} building(s)")

    def q_max_buildings(self):
        self.changed(f"Maxed {m.max_buildings(self.save, self.pid, self.cap_tier())} building(s)")

    def q_finish_settlements(self):
        n = m.finish_settlement_upgrades(self.save, self.pid, self.cap_level())
        self.changed(f"Finished {n} settlement upgrade(s)")

    def q_max_settlements(self):
        self.changed(f"Maxed {m.max_settlements(self.save, self.pid, self.cap_level())} settlement(s)")

    def q_end_sieges(self):
        self.changed(f"Ended {m.end_sieges(self.save, self.pid)} siege(s)")

    def q_reveal(self):
        self.changed(f"Revealed {m.reveal_map(self.save)} region(s)")

    # -------------------------------------------------------------- recruitment

    def _build_recruit_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Recruitment")
        quick = ttk.LabelFrame(tab, text="Quick actions (your regions)")
        quick.pack(fill="x", padx=6, pady=6)
        ttk.Button(quick, text="Refill all pools", command=self.q_refill).pack(side="left", **PAD)
        ttk.Label(quick, text="Raise every pool's max by").pack(side="left", **PAD)
        box, self.pool_raise = int_entry(quick, 6)
        self.pool_raise.set("10")
        box.pack(side="left", **PAD)
        ttk.Button(quick, text="Raise + refill", command=self.q_raise_pools).pack(side="left", **PAD)

        self.pool_tree = make_tree(tab, [("Region", 60), ("Pool", 200), ("Available", 80), ("Max", 60)])
        self.pool_tree.frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.pool_tree.bind("<<TreeviewSelect>>", self._on_pool_select)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(row, text="Available").pack(side="left", **PAD)
        box, self.pool_count = int_entry(row, 6)
        box.pack(side="left", **PAD)
        ttk.Label(row, text="Max").pack(side="left", **PAD)
        box, self.pool_max = int_entry(row, 6)
        box.pack(side="left", **PAD)
        ttk.Button(row, text="Apply to selected", command=self.apply_pool).pack(side="left", **PAD)
        self._pool_index: dict[str, dict] = {}

    def refresh_recruit(self) -> None:
        self.pool_tree.delete(*self.pool_tree.get_children())
        self._pool_index.clear()
        for i, (r, p) in enumerate(m.player_pools(self.save, self.pid)):
            self._pool_index[str(i)] = p
            self.pool_tree.insert("", "end", iid=str(i), values=(r["regionID"], p["poolID"], int(p["count"]), p["max"]))

    def _on_pool_select(self, _e=None) -> None:
        sel = self.pool_tree.selection()
        if sel:
            p = self._pool_index[sel[0]]
            self.pool_count.set(str(int(p["count"])))
            self.pool_max.set(str(p["max"]))

    def apply_pool(self) -> None:
        sel = self.pool_tree.selection()
        for iid in sel:
            p = self._pool_index[iid]
            p["max"] = m._same_number_type(p["max"], as_int(self.pool_max, "Max"))
            p["count"] = m._same_number_type(p["count"], as_int(self.pool_count, "Available"))
        self.changed(f"Updated {len(sel)} pool(s)")

    def q_refill(self):
        self.changed(f"Refilled {m.refill_pools(self.save, self.pid)} pool(s)")

    def q_raise_pools(self):
        n = m.raise_pool_max(self.save, self.pid, as_int(self.pool_raise, "Amount"))
        self.changed(f"Raised and refilled {n} pool(s)")

    # ------------------------------------------------------------------- armies

    def _build_armies_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Armies")
        quick = ttk.LabelFrame(tab, text="Quick actions (all your armies)")
        quick.pack(fill="x", padx=6, pady=6)
        ttk.Button(quick, text="Let armies act again", command=self.q_refresh_armies).pack(side="left", **PAD)
        ttk.Button(quick, text="Heal all armies", command=self.q_heal_all).pack(side="left", **PAD)

        body = ttk.PanedWindow(tab, orient="vertical")
        body.pack(fill="both", expand=True, padx=6, pady=6)
        top = ttk.Frame(body)
        bottom = ttk.Frame(body)
        body.add(top, weight=1)
        body.add(bottom, weight=2)

        self.army_tree = make_tree(top, [("Army", 220), ("Region", 60), ("Acted", 60), ("Retreat turns", 90),
                                         ("Units", 50)], height=5, select="browse")
        self.army_tree.frame.pack(fill="both", expand=True)
        self.army_tree.bind("<<TreeviewSelect>>", lambda _e: self.refresh_units())
        row = ttk.Frame(top)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Heal selected army", command=self.heal_selected_army).pack(side="left", **PAD)
        ttk.Label(row, text="Add").pack(side="left", **PAD)
        box, self.army_xp = int_entry(row, 6)
        self.army_xp.set("50")
        box.pack(side="left")
        ttk.Button(row, text="XP to every unit", command=self.xp_selected_army).pack(side="left", **PAD)
        ttk.Label(row, text="Grow unit size by").pack(side="left", **PAD)
        box, self.army_grow = int_entry(row, 5)
        self.army_grow.set("5")
        box.pack(side="left")
        ttk.Button(row, text="Grow all units", command=self.grow_selected_army).pack(side="left", **PAD)

        self.unit_tree = make_tree(bottom, [("Section", 90), ("Unit", 150), ("Name", 200), ("Models", 60),
                                            ("Max size", 65), ("Level", 50), ("XP", 50), ("Wounds", 60)])
        self.unit_tree.frame.pack(fill="both", expand=True)
        self.unit_tree.bind("<<TreeviewSelect>>", self._on_unit_select)
        row = ttk.Frame(bottom)
        row.pack(fill="x", pady=4)
        self.unit_vars: dict[str, tk.StringVar] = {}
        for key, label in [("count", "Models"), ("maxUnitSize", "Max size"), ("xp", "XP")]:
            ttk.Label(row, text=label).pack(side="left", **PAD)
            box, var = int_entry(row, 6)
            box.pack(side="left")
            self.unit_vars[key] = var
        ttk.Button(row, text="Apply to selected units", command=self.apply_units).pack(side="left", **PAD)
        self._armies: list[dict] = []
        self._units: dict[str, dict] = {}

    def refresh_armies(self) -> None:
        sel = self.army_tree.selection()
        self.army_tree.delete(*self.army_tree.get_children())
        self._armies = m.armies_of(self.save, self.pid)
        for i, a in enumerate(self._armies):
            self.army_tree.insert("", "end", iid=str(i), values=(
                a["armyName"], a.get("currentRegion"), "yes" if a.get("hasActedThisTurn") else "no",
                a.get("retreatedTurns", 0), sum(1 for _ in m.army_entries(a))))
        if sel and self.army_tree.exists(sel[0]):
            self.army_tree.selection_set(sel)
        elif self._armies:
            self.army_tree.selection_set("0")
        self.refresh_units()

    def _selected_army(self) -> dict | None:
        sel = self.army_tree.selection()
        return self._armies[int(sel[0])] if sel else None

    def refresh_units(self) -> None:
        sel = self.unit_tree.selection()
        self.unit_tree.delete(*self.unit_tree.get_children())
        self._units.clear()
        army = self._selected_army()
        if not army:
            return
        lst = army["list"]
        sections = [lst.get("characters"), lst.get("battleLine"), *(lst.get("armyListSections") or [])]
        n = 0
        for sec in sections:
            for e in (sec or {}).get("entries") or []:
                prog = e.get("campaignUnitProgress") or {}
                name = e.get("flavourName") or ""
                if e.get("character"):
                    name = f"{e['character'].get('flavourName', '')} + {name}"
                iid = str(n)
                self._units[iid] = e
                self.unit_tree.insert("", "end", iid=iid, values=(
                    sec.get("sectionName"), e.get("unitID"), name, e.get("count"), prog.get("maxUnitSize"),
                    prog.get("level"), prog.get("xp"), prog.get("leftOverWounds", 0)))
                n += 1
        keep = [i for i in sel if self.unit_tree.exists(i)]
        if keep:
            self.unit_tree.selection_set(keep)

    def _on_unit_select(self, _e=None) -> None:
        sel = self.unit_tree.selection()
        if not sel:
            return
        e = self._units[sel[0]]
        prog = e.get("campaignUnitProgress") or {}
        self.unit_vars["count"].set(str(e.get("count", 0)))
        self.unit_vars["maxUnitSize"].set(str(prog.get("maxUnitSize", 0)))
        self.unit_vars["xp"].set(str(prog.get("xp", 0)))

    def apply_units(self) -> None:
        sel = self.unit_tree.selection()
        for iid in sel:
            e = self._units[iid]
            prog = e.setdefault("campaignUnitProgress", {})
            prog["maxUnitSize"] = as_int(self.unit_vars["maxUnitSize"], "Max size")
            prog["xp"] = as_int(self.unit_vars["xp"], "XP")
            e["count"] = as_int(self.unit_vars["count"], "Models")
        self.changed(f"Updated {len(sel)} unit(s)")

    def heal_selected_army(self) -> None:
        a = self._selected_army()
        if a:
            self.changed(f"Healed {m.heal_army(a)} unit(s) in {a['armyName']}")

    def xp_selected_army(self) -> None:
        a = self._selected_army()
        if a:
            n = m.add_xp(a, as_int(self.army_xp, "XP"))
            self.changed(f"Gave XP to {n} unit(s)/character(s) in {a['armyName']}")

    def grow_selected_army(self) -> None:
        a = self._selected_army()
        if a:
            self.changed(f"Grew {m.grow_army(a, as_int(self.army_grow, 'Amount'))} unit(s) in {a['armyName']}")

    def q_refresh_armies(self):
        self.changed(f"Refreshed {m.refresh_armies(self.save, self.pid)} army(ies)")

    def q_heal_all(self):
        self.changed(f"Healed {m.heal_armies(self.save, self.pid)} unit(s)")

    # --------------------------------------------------------------- objectives

    def _build_objectives_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Objectives")
        ttk.Label(tab, wraplength=900, justify="left", text=(
            "Completing an objective sets its progress to its target. The game then marks it done and "
            "awards the reward itself (usually at the end of your turn), so you get the real reward.")
        ).pack(anchor="w", padx=6, pady=6)
        self.obj_tree = make_tree(tab, [("Slot", 40), ("Objective", 180), ("Status", 80), ("Progress", 80),
                                        ("Target", 140), ("Reward", 380)])
        self.obj_tree.frame.pack(fill="both", expand=True, padx=6)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Button(row, text="Complete selected", command=self.complete_selected).pack(side="left", **PAD)
        ttk.Button(row, text="Complete all active", command=self.complete_all).pack(side="left", **PAD)
        self._objs: list[dict] = []

    def refresh_objectives(self) -> None:
        self.obj_tree.delete(*self.obj_tree.get_children())
        self._objs = m.player_objectives(self.save, self.pid)
        status = {m.OBJECTIVE_HIDDEN: "hidden", m.OBJECTIVE_ACTIVE: "active", m.OBJECTIVE_DONE: "done"}
        for i, o in enumerate(self._objs):
            target = o.get("targetBuildingID") or (
                m.empire_name(self.save, o["targetEmpireID"]) if o.get("targetEmpireID", -1) >= 0 else "")
            self.obj_tree.insert("", "end", iid=str(i), values=(
                o.get("slot"), o.get("objectiveID") or "(not revealed)", status.get(o.get("status"), o.get("status")),
                f"{o.get('progress')}/{o.get('target')}", target, o.get("rewardText") or ""))

    def complete_selected(self) -> None:
        n = sum(m.complete_objective(self._objs[int(i)]) for i in self.obj_tree.selection())
        self.changed(f"Completed {n} objective(s)" if n else "Only active, revealed objectives can be completed")

    def complete_all(self) -> None:
        self.changed(f"Completed {m.complete_all_objectives(self.save, self.pid)} objective(s)")


def main() -> None:
    App().mainloop()
