# Changelog

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
