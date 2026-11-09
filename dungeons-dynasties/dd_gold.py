"""
Command line editor for Dungeons & Dynasties backup saves.

    python dd_gold.py info   my-guild.json
    python dd_gold.py show   my-guild.json
    python dd_gold.py gold   my-guild.json 50000
    python dd_gold.py set    my-guild.json renown=200 patronConfidence=100
    python dd_gold.py hall   my-guild.json --level 5 --all 5
    python dd_gold.py roster my-guild.json
    python dd_gold.py hero   my-guild.json "Nesta" --level 12 --ability 150
    python dd_gold.py gear   my-guild.json legendary

Get the file from the game: Settings -> "Back up save".
Put it back with:            Settings -> "Restore...".

The original file is copied to <name>.json.bak before it is overwritten.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import dd_mods
from dd_mods import FACILITIES, ModError
from dd_save import DEMO_DAY_CAP, EDITABLE_HINTS, SAVE_VERSION, Save, SaveError

# A value this large can overflow the game's number formatting and its
# economy balance in ways that are no fun; warn past it but allow it.
SANE_MAX = 10_000_000


def _fmt(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return "?" if value is None else str(value)


def _print_report(save: Save) -> None:
    info = save.summary()
    print(f"  Guild    {info['name'] or '?'}")
    print(f"  Day      {_fmt(info['day'])}   (demo ends at day {DEMO_DAY_CAP})")
    print(f"  Gold     {_fmt(info['gold'])}")
    print(f"  Renown   {_fmt(info['renown'])}")
    print(f"  Roster   {info['roster']} adventurer(s)")
    print(f"  Version  {info['version']}")

    if not save.version_ok():
        print(
            f"  ! Save version is {info['version']}, but this build of the game "
            f"only loads version {SAVE_VERSION}."
        )

    if info["signature_ok"]:
        print("  Checksum valid")
    else:
        print("  ! Checksum does NOT match this file's contents.")
        print(f"      stored   {save.stored_signature}")
        print(f"      computed {save.computed_signature()}")


# ---------------------------------------------------------------------------
# Shared open / write plumbing
# ---------------------------------------------------------------------------

def _open_for_edit(args: argparse.Namespace, structured: bool) -> Save | None:
    """Load and validate a save, or print why it cannot be edited."""
    save = Save.load(args.file)

    # The signature over the untouched file is the proof that this tool reads
    # the save exactly the way the game does. If it does not match, either the
    # file was already edited by something else or the format has changed, and
    # writing would produce a save the game flags as tampered.
    if not save.signature_ok():
        print(
            "Refusing to edit: this file's checksum does not match its contents.\n"
            "  stored   %s\n  computed %s\n"
            "Take a fresh backup from the game (Settings -> Back up save) and retry.\n"
            "Use --force to edit anyway (the game will treat the save as tampered,\n"
            "which in the demo forces the day count to the %d-day cap)."
            % (save.stored_signature, save.computed_signature(), DEMO_DAY_CAP),
            file=sys.stderr,
        )
        if not args.force:
            return None
        print("--force given, continuing.\n", file=sys.stderr)

    if not save.version_ok() and not args.force:
        print(
            f"Refusing to edit: save version is {save.version}, expected "
            f"{SAVE_VERSION}. Use --force to override.",
            file=sys.stderr,
        )
        return None

    if structured and not save.reserialize_ok():
        print(
            "Refusing to edit: this save contains values this tool cannot "
            "rewrite byte-identically, so the checksum could not be trusted.\n"
            "Simple numeric edits (gold, set) still work on it.",
            file=sys.stderr,
        )
        return None

    print(f"\n{Path(args.file).name}")
    _print_report(save)
    print()
    return save


def _finish(save: Save, args: argparse.Namespace, changes: list[str]) -> int:
    if not changes:
        print("  nothing to change.\n")
        return 0

    for line in changes:
        print(f"  {line}")

    # Check the file the way the game will, not just against ourselves.
    if not (save.signature_ok() and save.verify_as_game()):
        print(
            "\nInternal error: the rewritten save would not pass the game's "
            "integrity check. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    target = save.save(args.output, backup=not args.no_backup)
    backup = getattr(save, "last_backup", None)
    print(f"\n  written  {target}")
    if backup:
        print(f"  original {backup}")
    print("  checksum refreshed and verified")
    print('\nLoad it with Settings -> "Restore..." in the game.\n')
    return 0


def _run_mod(args: argparse.Namespace, operation) -> int:
    """Apply a structured change to the guild object and save."""
    save = _open_for_edit(args, structured=True)
    if save is None:
        return 1
    changes = operation(save.guild)
    save.commit()
    return _finish(save, args, changes)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_info(args: argparse.Namespace) -> int:
    save = Save.load(args.file)
    print(f"\n{Path(args.file).name}")
    _print_report(save)
    print()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    save = Save.load(args.file)
    guild = save.guild
    print(f"\n{Path(args.file).name}")

    roster = guild.get("roster") or []
    print(f"\n  Roster ({len(roster)})")
    print(f"    {'name':22}{'lvl':>4}{'ability':>9}{'potential':>11}"
          f"{'cond':>6}{'stress':>8}{'fatigue':>9}{'morale':>8}")
    for hero in roster:
        print(f"    {str(hero.get('name'))[:22]:22}"
              f"{hero.get('level', '?'):>4}"
              f"{hero.get('currentAbility', '?'):>9}"
              f"{hero.get('potentialAbility', '?'):>11}"
              f"{hero.get('condition', '?'):>6}"
              f"{hero.get('stress', '?'):>8}"
              f"{hero.get('fatigue', '?'):>9}"
              f"{hero.get('morale', '?'):>8}")

    hall = guild.get("hall") or {}
    facilities = hall.get("facilities") or {}
    print(f"\n  Hall     level {hall.get('level', '?')}")
    print("    " + "  ".join(
        f"{name}={facilities.get(name, 0)}" for name in FACILITIES
    ))

    tally: dict[str, int] = {}
    for _, item in dd_mods._iter_items(guild, "all"):
        tally[item["rarity"]] = tally.get(item["rarity"], 0) + 1
    print("\n  Gear     " + (", ".join(
        f"{count} {rarity}" for rarity, count in
        sorted(tally.items(), key=lambda kv: dd_mods.RARITIES.index(kv[0]))
    ) or "none"))
    print()
    return 0


def cmd_gold(args: argparse.Namespace) -> int:
    save = _open_for_edit(args, structured=False)
    if save is None:
        return 1
    old, new = save.set_number("gold", args.amount)
    if args.amount > SANE_MAX:
        print(
            f"  note: {args.amount:,} gold is far beyond anything the game "
            "hands out; very large numbers can look odd in the UI.",
            file=sys.stderr,
        )
    return _finish(save, args, [f"gold: {_fmt(old)} -> {_fmt(new)}"])


def cmd_set(args: argparse.Namespace) -> int:
    save = _open_for_edit(args, structured=False)
    if save is None:
        return 1
    changes = []
    for pair in args.assignments:
        if "=" not in pair:
            print(f"Bad assignment {pair!r}; expected field=number.", file=sys.stderr)
            return 2
        field, _, raw = pair.partition("=")
        try:
            value = int(raw.strip().replace(",", "").replace("_", ""))
        except ValueError:
            print(f"Bad number in {pair!r}.", file=sys.stderr)
            return 2
        old, new = save.set_number(field.strip(), value)
        changes.append(f"{field.strip()}: {_fmt(old)} -> {_fmt(new)}")
    return _finish(save, args, changes)


def cmd_hall(args: argparse.Namespace) -> int:
    facilities: dict[str, int] = {}
    if args.all is not None:
        facilities = {name: args.all for name in FACILITIES}
    for pair in args.facility or []:
        if "=" not in pair:
            print(f"Bad facility {pair!r}; expected name=number.", file=sys.stderr)
            return 2
        name, _, raw = pair.partition("=")
        try:
            facilities[name.strip()] = int(raw)
        except ValueError:
            print(f"Bad number in {pair!r}.", file=sys.stderr)
            return 2
    if args.level is None and not facilities:
        print("Nothing requested; pass --level and/or --all/--facility.",
              file=sys.stderr)
        return 2
    return _run_mod(
        args, lambda g: dd_mods.set_hall(g, level=args.level, facilities=facilities)
    )


def cmd_roster(args: argparse.Namespace) -> int:
    return _run_mod(args, lambda g: dd_mods.restore_roster(
        g, condition=args.condition, morale=args.morale,
        stress=args.stress, fatigue=args.fatigue,
        clear_grind=not args.keep_grind,
    ))


def cmd_hero(args: argparse.Namespace) -> int:
    vitals = {
        field: getattr(args, field)
        for field in ("condition", "morale", "stress", "fatigue")
        if getattr(args, field) is not None
    }
    return _run_mod(args, lambda g: dd_mods.set_hero(
        g, args.who, level=args.level,
        current_ability=args.ability, potential_ability=args.potential,
        attributes=args.attributes, hidden=args.hidden, **vitals,
    ))


def cmd_gear(args: argparse.Namespace) -> int:
    return _run_mod(
        args, lambda g: dd_mods.upgrade_gear(g, args.rarity, scope=args.scope)
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    hints = "\n".join(f"  {k:8} {v}" for k, v in EDITABLE_HINTS.items())
    parser = argparse.ArgumentParser(
        prog="dd_gold",
        description=__doc__,
        epilog="Common top-level fields for 'set':\n" + hints,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    def with_file(sub):
        sub.add_argument("file", help="path to the backup .json from the game")
        return sub

    def editable(sub):
        with_file(sub)
        sub.add_argument("-o", "--output", help="write here instead of in place")
        sub.add_argument("--no-backup", action="store_true",
                         help="do not write a .bak copy")
        sub.add_argument("--force", action="store_true",
                         help="edit even if validation fails")
        return sub

    with_file(subs.add_parser("info", help="show a save's headline numbers")
              ).set_defaults(func=cmd_info)
    with_file(subs.add_parser("show", help="list roster, hall and gear")
              ).set_defaults(func=cmd_show)

    p = editable(subs.add_parser("gold", help="set gold on hand"))
    p.add_argument("amount", type=int, help="new gold amount")
    p.set_defaults(func=cmd_gold)

    p = editable(subs.add_parser("set", help="set top-level numeric fields"))
    p.add_argument("assignments", nargs="+", metavar="field=number")
    p.set_defaults(func=cmd_set)

    p = editable(subs.add_parser("hall", help="set hall level and facilities"))
    p.add_argument("--level", type=int, help="hall level")
    p.add_argument("--all", type=int, metavar="N",
                   help="set every facility to N")
    p.add_argument("--facility", action="append", metavar="name=N",
                   help="set one facility; repeatable. Names: "
                        + ", ".join(FACILITIES))
    p.set_defaults(func=cmd_hall)

    p = editable(subs.add_parser(
        "roster", help="bulk-restore everyone (heal, de-stress, rest)"))
    p.add_argument("--condition", type=int, default=100)
    p.add_argument("--morale", type=int, default=100)
    p.add_argument("--stress", type=int, default=0)
    p.add_argument("--fatigue", type=int, default=0)
    p.add_argument("--keep-grind", action="store_true",
                   help="leave delveGrind alone")
    p.set_defaults(func=cmd_roster)

    p = editable(subs.add_parser("hero", help="edit one hero (or all)"))
    p.add_argument("who", help='hero name, id, or "all"')
    p.add_argument("--level", type=int, help=f"1-{dd_mods.LEVEL_MAX}")
    p.add_argument("--ability", type=int,
                   help=f"currentAbility, 1-{dd_mods.ABILITY_MAX}")
    p.add_argument("--potential", type=int,
                   help=f"potentialAbility, 1-{dd_mods.ABILITY_MAX}")
    p.add_argument("--attributes", type=int, metavar="N",
                   help=f"set all 30 attributes to N "
                        f"({dd_mods.ATTR_MIN}-{dd_mods.ATTR_MAX})")
    p.add_argument("--hidden", type=int, metavar="N",
                   help="set all hidden traits to N")
    for field in ("condition", "morale", "stress", "fatigue"):
        p.add_argument(f"--{field}", type=int)
    p.set_defaults(func=cmd_hero)

    p = editable(subs.add_parser("gear", help="upgrade item rarity"))
    p.add_argument("rarity", choices=dd_mods.RARITIES)
    p.add_argument("--scope", choices=("equipped", "stash", "all"), default="all")
    p.set_defaults(func=cmd_gear)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SaveError, ModError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
