"""
Dungeons & Dynasties save editor -- a small desktop window.

Double-click this file to run it (the .pyw extension starts it without a
console). It needs nothing but a standard Python install.

    1. In the game: Settings -> "Back up save", save as my-guild.json
    2. Open that file here, make changes, press the tab's Apply button
    3. In the game: Settings -> "Restore...", pick the file

Every write refreshes the save's integrity checksum, which the game verifies on
load. Editing the file by hand instead will make the game treat the save as
tampered, and in the demo that forces the run to the 240-day cap.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dd_mods  # noqa: E402
from dd_mods import ABILITY_MAX, FACILITIES, LEVEL_MAX, ModError  # noqa: E402
from dd_save import DEMO_DAY_CAP, SAVE_VERSION, Save, SaveError  # noqa: E402

SANE_MAX = 10_000_000
GUILD_FIELDS = [
    ("gold", "Gold"),
    ("renown", "Renown"),
    ("patronConfidence", "Patron confidence"),
    ("forage", "Forage"),
]


def _likely_start_dir() -> str:
    here = Path(__file__).resolve().parent / "save_game_backups"
    if here.is_dir():
        return str(here)
    home = Path.home()
    for name in ("Downloads", "Documents", "Desktop"):
        if (home / name).is_dir():
            return str(home / name)
    return str(home)


def _to_int(text: str) -> int | None:
    text = text.strip().replace(",", "").replace("_", "")
    if not text:
        return None
    return int(text)


class Editor(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.save: Save | None = None

        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        self._build_file_row()
        self._build_summary()
        self._build_tabs()
        self._build_status()
        self._set_enabled(False)

    # -- layout ----------------------------------------------------------

    def _build_file_row(self) -> None:
        row = ttk.Frame(self)
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text="Backup file").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.path_var).grid(
            row=0, column=1, sticky="ew", padx=8)
        ttk.Button(row, text="Open...", command=self.on_open).grid(row=0, column=2)
        ttk.Label(
            self, text='Make one in the game with Settings → "Back up save".',
            foreground="#666",
        ).grid(row=1, column=0, sticky="w", pady=(2, 8))

    def _build_summary(self) -> None:
        box = ttk.Frame(self)
        box.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.summary_var = tk.StringVar(value="—")
        ttk.Label(box, textvariable=self.summary_var,
                  font=("TkDefaultFont", 9, "bold")).pack(side="left")
        self.check_var = tk.StringVar(value="")
        self.check_lbl = ttk.Label(box, textvariable=self.check_var)
        self.check_lbl.pack(side="right")

    def _build_tabs(self) -> None:
        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=3, column=0, sticky="nsew")
        self._build_guild_tab()
        self._build_roster_tab()
        self._build_hall_tab()
        self._build_gear_tab()

    # -- guild tab -------------------------------------------------------

    def _build_guild_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(tab, text="Guild")
        self.guild_vars: dict[str, tk.StringVar] = {}

        for i, (field, label) in enumerate(GUILD_FIELDS):
            ttk.Label(tab, text=label).grid(row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            self.guild_vars[field] = var
            ttk.Entry(tab, textvariable=var, width=14, justify="right").grid(
                row=i, column=1, sticky="w", padx=10)

        quick = ttk.Frame(tab)
        quick.grid(row=0, column=2, sticky="w")
        for label, amount in (("+1k", 1_000), ("+10k", 10_000), ("+100k", 100_000)):
            ttk.Button(quick, text=label, width=6,
                       command=lambda a=amount: self._bump("gold", a)
                       ).pack(side="left", padx=2)

        ttk.Button(tab, text="Apply", command=self.apply_guild).grid(
            row=len(GUILD_FIELDS), column=0, columnspan=3, pady=(16, 0), sticky="e")

    def _bump(self, field: str, amount: int) -> None:
        if self.save is None:
            return
        try:
            current = _to_int(self.guild_vars[field].get()) or 0
        except ValueError:
            current = 0
        self.guild_vars[field].set(str(max(0, current + amount)))

    def apply_guild(self) -> None:
        def op(_guild):
            changes = []
            for field, label in GUILD_FIELDS:
                try:
                    value = _to_int(self.guild_vars[field].get())
                except ValueError:
                    raise ModError(f"{label} must be a whole number")
                if value is None or self.save.get_number(field) == value:
                    continue
                if value < 0:
                    raise ModError(f"{label} cannot be negative")
                if field == "gold" and value > SANE_MAX and not messagebox.askyesno(
                    "Very large amount",
                    f"That is more than {SANE_MAX:,} gold, far beyond anything "
                    "the game hands out.\n\nApply anyway?",
                ):
                    continue
                old, new = self.save.set_number(field, value)
                changes.append(f"{field} {old} → {new}")
            return changes

        # Top-level numbers use the raw-text path, so no commit() is needed.
        self._commit(op, structured=False)

    # -- roster tab ------------------------------------------------------

    def _build_roster_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Roster")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        cols = ("lvl", "ability", "potential", "cond", "stress", "fatigue", "morale")
        self.tree = ttk.Treeview(tab, columns=cols, show="tree headings", height=8)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.heading("#0", text="Adventurer")
        self.tree.column("#0", width=150, stretch=False)
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=62, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        bulk = ttk.LabelFrame(tab, text="Everyone", padding=8)
        bulk.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bulk, text="Full restore",
                   command=self.apply_restore).pack(side="left")
        ttk.Label(bulk, foreground="#666",
                  text="  condition & morale to 100, stress & fatigue to 0, "
                       "clears party grind").pack(side="left")

        one = ttk.LabelFrame(tab, text="Selected adventurer", padding=8)
        one.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.hero_vars: dict[str, tk.StringVar] = {}
        specs = [("level", f"Level (1-{LEVEL_MAX})"),
                 ("ability", f"Ability (1-{ABILITY_MAX})"),
                 ("potential", "Potential"),
                 ("attributes", "All attributes (1-20)")]
        for i, (key, label) in enumerate(specs):
            ttk.Label(one, text=label).grid(row=i // 2, column=(i % 2) * 2,
                                            sticky="w", padx=(0, 6), pady=3)
            var = tk.StringVar()
            self.hero_vars[key] = var
            ttk.Entry(one, textvariable=var, width=8, justify="right").grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 18))
        ttk.Button(one, text="Apply to selected",
                   command=self.apply_hero).grid(row=2, column=3, sticky="e",
                                                 pady=(8, 0))

    def apply_restore(self) -> None:
        self._commit(lambda g: dd_mods.restore_roster(g), structured=True)

    def apply_hero(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No selection", "Pick an adventurer first.")
            return
        hero_id = selection[0]

        def op(guild):
            try:
                values = {k: _to_int(v.get()) for k, v in self.hero_vars.items()}
            except ValueError:
                raise ModError("those fields must be whole numbers")
            if all(v is None for v in values.values()):
                return []
            return dd_mods.set_hero(
                guild, hero_id,
                level=values["level"],
                current_ability=values["ability"],
                potential_ability=values["potential"],
                attributes=values["attributes"],
            )

        self._commit(op, structured=True)

    # -- hall tab --------------------------------------------------------

    def _build_hall_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(tab, text="Hall")
        self.hall_vars: dict[str, tk.StringVar] = {}

        ttk.Label(tab, text="Hall level").grid(row=0, column=0, sticky="w", pady=4)
        self.hall_level = tk.StringVar()
        ttk.Entry(tab, textvariable=self.hall_level, width=8,
                  justify="right").grid(row=0, column=1, sticky="w", padx=10)

        ttk.Separator(tab, orient="horizontal").grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=10)

        for i, name in enumerate(FACILITIES):
            r, c = 2 + i // 2, (i % 2) * 2
            ttk.Label(tab, text=name.capitalize()).grid(
                row=r, column=c, sticky="w", pady=3)
            var = tk.StringVar()
            self.hall_vars[name] = var
            ttk.Entry(tab, textvariable=var, width=8, justify="right").grid(
                row=r, column=c + 1, sticky="w", padx=(10, 24))

        bar = ttk.Frame(tab)
        bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        ttk.Label(bar, text="Set all to").pack(side="left")
        self.hall_all = tk.StringVar(value="5")
        ttk.Entry(bar, textvariable=self.hall_all, width=5,
                  justify="right").pack(side="left", padx=6)
        ttk.Button(bar, text="Fill", command=self._fill_hall).pack(side="left")
        ttk.Button(bar, text="Apply", command=self.apply_hall).pack(side="right")

    def _fill_hall(self) -> None:
        value = self.hall_all.get().strip()
        for var in self.hall_vars.values():
            var.set(value)

    def apply_hall(self) -> None:
        def op(guild):
            try:
                level = _to_int(self.hall_level.get())
                facilities = {n: _to_int(v.get()) for n, v in self.hall_vars.items()}
            except ValueError:
                raise ModError("hall levels must be whole numbers")
            facilities = {n: v for n, v in facilities.items() if v is not None}
            return dd_mods.set_hall(guild, level=level, facilities=facilities)

        self._commit(op, structured=True)

    # -- gear tab --------------------------------------------------------

    def _build_gear_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(tab, text="Gear")

        self.gear_summary = tk.StringVar(value="—")
        ttk.Label(tab, textvariable=self.gear_summary).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(tab, text="Upgrade everything to").grid(row=1, column=0, sticky="w")
        self.gear_rarity = tk.StringVar(value="mythic")
        ttk.Combobox(tab, textvariable=self.gear_rarity, values=dd_mods.RARITIES,
                     state="readonly", width=12).grid(row=1, column=1,
                                                      sticky="w", padx=10)

        ttk.Label(tab, text="Which items").grid(row=2, column=0, sticky="w", pady=8)
        self.gear_scope = tk.StringVar(value="all")
        ttk.Combobox(tab, textvariable=self.gear_scope,
                     values=("equipped", "stash", "all"), state="readonly",
                     width=12).grid(row=2, column=1, sticky="w", padx=10)

        ttk.Label(tab, foreground="#666", wraplength=420, justify="left",
                  text="Rarity on its own is mostly cosmetic, so each item's "
                       "mods and value are scaled to the band the game rolls "
                       "that rarity from. Items already at or above the target "
                       "are left alone.").grid(row=3, column=0, columnspan=2,
                                               sticky="w", pady=(10, 0))

        ttk.Button(tab, text="Apply", command=self.apply_gear).grid(
            row=4, column=0, columnspan=2, sticky="e", pady=(16, 0))

    def apply_gear(self) -> None:
        self._commit(
            lambda g: dd_mods.upgrade_gear(g, self.gear_rarity.get(),
                                           scope=self.gear_scope.get()),
            structured=True,
        )

    # -- status ----------------------------------------------------------

    def _build_status(self) -> None:
        self.status = tk.StringVar(value="Open a backup file to begin.")
        self.status_label = ttk.Label(self, textvariable=self.status,
                                      foreground="#444", wraplength=540,
                                      justify="left")
        self.status_label.grid(row=4, column=0, sticky="w", pady=(10, 0))

    def _say(self, message: str, tone: str = "info") -> None:
        colours = {"info": "#444", "good": "#1a7f37", "bad": "#b3261e"}
        self.status.set(message)
        self.status_label.configure(foreground=colours.get(tone, "#444"))

    def _set_enabled(self, on: bool) -> None:
        state = "normal" if on else "disabled"
        for tab_id in self.tabs.tabs():
            self.tabs.tab(tab_id, state=state)

    # -- load / show -----------------------------------------------------

    def on_open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open a Dungeons & Dynasties backup",
            initialdir=_likely_start_dir(),
            filetypes=[("Save backup", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self.reload()

    def reload(self) -> None:
        path = self.path_var.get().strip().strip('"')
        if not path:
            self._say("Choose a file first.", "bad")
            return
        try:
            self.save = Save.load(path)
        except SaveError as exc:
            self.save = None
            self._set_enabled(False)
            self.summary_var.set("—")
            self.check_var.set("")
            self._say(str(exc), "bad")
            return
        self._show()

    def _show(self) -> None:
        save = self.save
        info = save.summary()
        self.summary_var.set(
            f"{info['name']}  ·  day {info['day']} of {DEMO_DAY_CAP}"
            f"  ·  {info['roster']} adventurers"
        )

        healthy = info["signature_ok"]
        version_ok = save.version_ok()
        self.check_var.set("checksum valid" if healthy
                           else "checksum does not match")
        self.check_lbl.configure(foreground="#1a7f37" if healthy else "#b3261e")

        for field, _ in GUILD_FIELDS:
            value = save.get_number(field)
            self.guild_vars[field].set("" if value is None else str(int(value)))

        hall = save.guild.get("hall") or {}
        self.hall_level.set(str(hall.get("level", "")))
        facilities = hall.get("facilities") or {}
        for name, var in self.hall_vars.items():
            var.set(str(facilities.get(name, 0)))

        self.tree.delete(*self.tree.get_children())
        for hero in save.guild.get("roster") or []:
            self.tree.insert(
                "", "end", iid=hero.get("id"), text=str(hero.get("name")),
                values=(hero.get("level"), hero.get("currentAbility"),
                        hero.get("potentialAbility"), hero.get("condition"),
                        hero.get("stress"), hero.get("fatigue"),
                        hero.get("morale")),
            )
        for var in self.hero_vars.values():
            var.set("")

        tally: dict[str, int] = {}
        for _, item in dd_mods._iter_items(save.guild, "all"):
            tally[item["rarity"]] = tally.get(item["rarity"], 0) + 1
        self.gear_summary.set("You hold: " + (", ".join(
            f"{count} {rarity}" for rarity, count in
            sorted(tally.items(), key=lambda kv: dd_mods.RARITIES.index(kv[0]))
        ) or "no gear"))

        self._set_enabled(healthy and version_ok)

        if not version_ok:
            self._say(f"Save is version {info['version']}, but the game loads "
                      f"only version {SAVE_VERSION}. Editing disabled.", "bad")
        elif not healthy:
            self._say(
                "This file's checksum does not match its contents, so it is "
                "not a clean backup. Take a fresh one from the game.", "bad")
        else:
            self._say(f"Loaded {Path(save.path).name}. Ready.", "good")

    # -- write -----------------------------------------------------------

    def _commit(self, operation, structured: bool) -> None:
        """Run an edit, write the file, and report what happened."""
        if self.save is None:
            return
        if structured and not self.save.reserialize_ok():
            messagebox.showerror(
                "Cannot edit safely",
                "This save contains values the editor cannot rewrite exactly, "
                "so the checksum could not be trusted. Simple guild numbers "
                "still work.",
            )
            return

        try:
            changes = operation(self.save.guild)
            if structured:
                self.save.commit()
            if not changes:
                self._say("Nothing to change.", "info")
                self.reload()
                return
            if not (self.save.signature_ok() and self.save.verify_as_game()):
                raise SaveError(
                    "the rewritten save would not pass the game's integrity "
                    "check, so nothing was written"
                )
            self.save.save()
        except (SaveError, ModError) as exc:
            messagebox.showerror("Could not save", str(exc))
            self.reload()
            return

        backup = getattr(self.save, "last_backup", None)
        summary = "; ".join(changes[:3])
        if len(changes) > 3:
            summary += f" (+{len(changes) - 3} more)"
        self._show()
        self._say(
            f"Saved — {summary}."
            + (f" Original kept as {Path(backup).name}." if backup else "")
            + ' Load it with Settings → "Restore...".',
            "good",
        )


def main() -> None:
    root = tk.Tk()
    root.title("Dungeons & Dynasties — Save Editor")
    root.minsize(620, 560)
    try:
        root.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    editor = Editor(root)

    # Accept a save path on the command line, so the file can be dragged onto
    # the launcher or passed by a shortcut.
    if len(sys.argv) > 1:
        editor.path_var.set(sys.argv[1])
        editor.reload()
        if len(sys.argv) > 2 and sys.argv[2].isdigit():
            editor.tabs.select(int(sys.argv[2]))

    root.mainloop()


if __name__ == "__main__":
    main()
