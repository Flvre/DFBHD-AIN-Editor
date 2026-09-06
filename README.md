# DFBHD AIN Editor

Read the [illustrated user guide (PDF)](docs/AIN_Editor_Guide.pdf) and the [keyboard and mouse controls](docs/KEYBINDS.md).

## What is the AIN Editor?

This is a complex tool designed to open, edit, and create AIN files for the game Delta Force: Black Hawk Down developed by NovaLogic.

## Why was it created

NovaLogic never released the tool they used to create these files. For more than 2 decades, map creators had to resort to creative and limited ways to make the AI in the game look like they have a sense of direction. Because of NovaLogic's closure as an independent studio and later acquisition by THQ Nordic, chances of the original tool surfacing are close to zero.

This project is the result of months spent reverse engineering game file formats and selected engine behaviours, then creating its own generator algorithms and 3D wireframe rendering system.

## What does it do

The tool is a nav graph editor. Its scope is to create and edit navigation graphs in either 2D top or 3D Wireframe View.

## Features

- Navigation graph generator (seed-based and zone-based)
- 2D top-down view with terrain, entities, collision geometry
- 3D wireframe view with filtering and orbit controls
- Entity orientation support: BMS heading and pitch are reflected in 2D/3D views and generator geometry
- Node Focus render lens for keeping navigation geometry readable
- Optional human reference model loaded from the user's own game installation
- Node editing (control, radius, zone, metadata values)
- Graph tools (rebuild neighbours, link by distance, fix one-way connections)
- Zone system for organizing nodes
- Undo/redo
- Project save/load
- Export to .ain

## Supported files

This editor can:
- parse, open, edit, and create AIN files
- read .pff archive data
- parse .bms map data
- read .3di model data
- parse .cpt and .trn terrain file formats from either .bms or .mis files
- parse .def file formats

| Format | Description |
|--------|-------------|
| `.ain` | Navigation graph file |
| `.pff` | Game archive format |
| `.bms` | Map file |
| `.3di` | 3D model file |
| `.mis` | Mission file for NovaLogic's MED editor |
| `.cpt` | Compiled terrain heightmap |
| `.trn` | Terrain layout definition |
| `.def` | Entity definitions with properties and type IDs |

## Requirements

**OS:** Windows 10/11

**A copy of the game** to read the files.

**Runtime dependencies:**
- Python 3.14 or newer
- Pillow (PIL) — used everywhere from 3D rendering and terrain display to image processing
- Shapely — used in the generator for polygon operations (solid-fill detection)

`psutil` is optional and adds process-memory diagnostics; it is included in `requirements.txt` for convenience.

```
pip install -r requirements.txt
```

**Built into Python** (no install needed):
- tkinter, struct, math, json, threading, collections, pathlib, statistics, pickle, subprocess, etc.

**Build only:**
- PyInstaller — used for creating .exe

## Quick start

### From a release executable

Extract the release archive and run the executable. Set the DFBHD game folder, open a BMS map, then open or create/export an AIN file.

### From source

Install the requirements, then run `python ain_editor_v1_0.py`. Set the DFBHD game folder, open a BMS map, then open or create/export an AIN file.

Detailed usage is covered in the [illustrated PDF guide](docs/AIN_Editor_Guide.pdf) and [Markdown guide](docs/USER_GUIDE.md). See [GENERATOR.md](docs/GENERATOR.md) for generator behavior, [FILE_FORMATS.md](docs/FILE_FORMATS.md) for the supported formats, [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common problems, and [CHANGELOG.md](docs/CHANGELOG.md) for changes.

## Source layout

To compile the Windows EXE, see [Building the executable](docs/BUILD.md).

- `ain_editor_v1_0.py` — launch entry point and shared runtime setup.
- `app.py` — editor state and application controller.
- `ui/`, `rendering/`, `view3d/` — interface, 2D rendering, and 3D view.
- `generator/` — graph generation and worker processes.
- `format/`, `resources/`, `cmodel/`, `terrain/`, `entity/` — game formats, resource loading, geometry, and entities.
- `config/`, `shared/`, `runtime_namespace.py` — configuration and shared helpers.
- `docs/` — user guide and technical documentation.
- `tests/` — regression checks; see [test instructions](tests/README.md).

## Known limitations

The generator does not guarantee a finished graph for every map and it might require manual tweaks such as moving, connecting or deleting nodes.

Quantized placement is experimental and may fail to generate doorway or entrance nodes on maps with interior areas. Area-zone generation with the automatic Z filter may miss some rooms or floor layers because of tile boundaries. Some stair-to-upper-floor connections can also depend on the seed position; re-seeding is the current workaround.

The foliage (trees, bushes) could cause performance issues depending on how many are present in a map.

The 3D View could cause massive performance issues depending on how many entities are rendered.

## Legal notice

This is a fan project. It is not affiliated in any way with NovaLogic or THQ Nordic. The repository does not bundle game assets.

## License

MIT
