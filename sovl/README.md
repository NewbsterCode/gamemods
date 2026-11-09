# SOVL Campaign Editor

A save editor for the **solo grand-strategy campaign** in SOVL: Fantasy Warfare.
It edits `%USERPROFILE%\AppData\LocalLow\DalenStudios\SOVL\grandStrategyData\grandStrategyProgress.json`
and nothing else. Multiplayer is not touched.

## Run

Double-click `SOVL Campaign Editor.pyw`, or run `python -m sovl_editor` from this `sovl` folder. Needs Python 3.10+ (Tkinter is included).

## Use

1. **Quit SOVL to the desktop.** If the game is running when you save, it overwrites your edits.
2. Make your edits in the tabs, then click **Save to game**.
3. Launch SOVL and continue the campaign.

Every save first copies the current file to `%LOCALAPPDATA%\SOVLEditor\backups`.
**Restore backup...** puts any of those copies back.

| Tab | What you can do |
|---|---|
| Empire | Set gold, battles won and regions taken for any empire; set AI gold to 0; change commander recruit levels |
| Regions & Buildings | Claim regions, reveal the map, end sieges, finish or max buildings and settlement upgrades, edit any region or building |
| Recruitment | Refill recruitment pools, raise their max sizes, edit single pools |
| Armies | Let armies act again, heal units, add XP, grow unit sizes, edit units |
| Objectives | Set progress to the target so the game awards the reward itself |

### Roguelike campaign

Pick **Roguelike** in the toolbar's **Campaign** box (the editor opens whichever save changed most
recently). It edits `...\SOVL\campaignData\campaignProgress.json`.

| Tab | What you can do |
|---|---|
| Gold & Army | Set gold, heal units, grow unit sizes, add XP, edit single units (the commander's two stored copies stay in sync) |
| Map | Change upcoming nodes into armories, campsites, mystery events or battles, one at a time or every node of a type. Visited/passed nodes and the boss can't be changed |

This folder is synced by Steam Cloud. If Steam asks which copy to keep on the next launch, pick the
local one.

### Grand strategy caps

**Max building tier** and **Max settlement level** (toolbar) default to 3. That is the highest
value seen in real saves; the game's true caps aren't stored in the save. If the game misbehaves
after a big jump, restore a backup.

## Tests

From this `sovl` folder, `python -m pytest tests` runs against a temp copy of your save; the real file is never written.
