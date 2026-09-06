# Changelog

## Post-v1.0 maintenance — September 2026

- Added the portable Windows build and application icon. Frozen generator workers relaunch the executable directly; settings and logs persist beside the executable.

- Added Shift+Numpad 8/2/4/6 for 0.1-metre movement of the active selected node in Edit mode. Plain numpad directions retain viewport panning.
- Fixed Ctrl+Arrow fine movement when a non-text control has focus. Verified plain arrows use 1 metre, modified arrows use 0.1 metre, and both Shift+= and keypad + increase grid spacing.

- Fixed JSON saving after generation: recursive serialization no longer references the removed FileIOMixin in the active editor.
- Project saves and autosaves now preserve Previous Generator Seeds with per-seed settings; loading restores the retry history. Older projects start with an empty seed history.

- BMS entity pitch is preserved from the entity record and applied to 2D and 3D model rendering.
- Generator collision, support, and walkable geometry now applies entity pitch before heading rotation.
- Pitch is included in geometry cache keys so rotated and upright instances cannot reuse stale geometry.
- Trees-only filtering now also covers foliage with no loaded CModel blockers and fast-render paths.
- Foliage hull-only rendering is off by default; the developer toggle remains available.
- On first launch without a valid game path, a themed prompt explains the requirement and opens the game-folder picker.
- Entity Colors now follows the active light/dark panel theme, stays docked on the left, and supports separate dark/light foliage colors saved in the editor config.
- Autosave (JSON) now defaults to off; it remains available from the File menu.
- Added Ctrl+R as a 2D shortcut for Rebuild Neighbors on selected nodes only; it does nothing with no selection.
- 3D L now toggles the visible Floor coordinates level markers, including on real 3D CModel buildings.
- Added 3D Numpad 8/2/4/6 movement: 1 metre normally and 0.01 metre with Shift.

## v1.0.0 — Initial release

- Navigation graph generator (seed-based and zone-based)
- 2D top-down view with terrain, entities, and collision geometry
- 3D wireframe view with filtering and orbit controls
- Node editing (control, radius, zone, metadata values)
- Graph cleanup tools (rebuild neighbours, link by distance, fix one-way connections)
- Zone system for organizing nodes and zone-based generation
- Metadata scanner
- Node Focus render lens with configurable geometry colour
- Optional runtime-loaded Delta01 human reference model (not bundled)
- Undo/redo
- Project save/load (JSON)
- Autosave (JSON), enabled by default after a project is saved
- Generate with coordinates and session-only Previous Generator Seeds
- Export to .ain (NAI2)
- Reads 8 file formats: .ain, .pff, .bms, .3di, .cpt, .trn, .mis, .def
- Quantized placement (experimental), with a documented doorway/entrance limitation
- Area-zone plus Z-filter warning for possible room/floor omissions at tile boundaries
