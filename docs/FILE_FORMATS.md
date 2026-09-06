# File Formats

This document describes the file formats the editor reads and writes, what data it extracts from each, and how they relate to each other.

| Format | Read | Write | Description |
|--------|------|-------|-------------|
| `.ain` | Yes | Yes | AI navigation graph |
| `.pff` | Yes | No | Game archive |
| `.bms` | Yes | No | Map placement data |
| `.3di` | Yes | No | 3D model geometry |
| `.cpt` | Yes | No | Compiled terrain heightmap |
| `.trn` | Yes | No | Terrain layout definition |
| `.mis` | Yes | No | Mission file |
| `.def` | Yes | No | Entity type definitions (ITEMS.DEF) |

---

## .ain — AI Navigation Graph

The .ain file is the editor's primary format and the only one it writes. It contains the full AI navigation graph used by the game engine to move AI soldiers around a map.

### Structure

The file begins with a 4-byte magic identifier followed by a 4-byte node count:

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0x00 | 4 | char[4] | Magic: `NAI1` or `NAI2` |
| 0x04 | 4 | uint32 | Node count |

After the header, each node is stored as a 52-byte (0x34) record:

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0x00 | 4 | int32 | X coordinate (fixed-point, divide by 65536 for metres) |
| 0x04 | 4 | int32 | Y coordinate (fixed-point) |
| 0x08 | 4 | int32 | Z coordinate (fixed-point) |
| 0x0C | 1 | uint8 | b12 — Control |
| 0x0D | 1 | uint8 | b13 — Takedown (var1) |
| 0x0E | 1 | uint8 | b14 — Takedown Zone |
| 0x0F | 1 | uint8 | b15 — Radius |
| 0x10 | 1 | uint8 | b16 — Direction (editor-generated direction metadata; ignored by the game engine) |
| 0x11 | 1 | uint8 | b17 — Takedown (var2) |
| 0x12 | 1 | uint8 | b18 — RuntimeOccupancy (runtime occupancy/reservation byte; stored as 0 and discarded after loading) |
| 0x13 | 1 | uint8 | Neighbor count |
| 0x14 | 32 | uint16[16] | Neighbor node indices (unused slots = 0x0000) |

Maximum 16 neighbors per node.

After all node records, the file contains an AreaStates tail used for takedown zone flags:

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| +0x00 | 4 | uint32 | Entry count (1–512; the editor writes 256) |
| +0x04 | varies | byte[N][12] | N entries of 12 bytes each |

Each AreaStates entry corresponds to a zone ID. Byte 0 flag 0x02 marks the zone as a valid takedown target.

### Editor behavior

The editor reads both NAI1 and NAI2 files but always writes NAI2. It also handles BMX-wrapped files where the AIN data is embedded after a 0x428-byte BMX header.

On load, the editor sanitizes the node graph: duplicate, self-referencing, and out-of-range neighbor entries are removed.

If the last neighbor slot (index 15) is unused and contains a non-zero value, it is preserved as scan metadata (scan_0x32) for round-trip fidelity.

---

## .pff — Game Archive

PFF is NovaLogic's archive format for packaging game assets. The editor reads .pff archives to find model files (.3di), terrain data (.cpt, .trn), colormaps (.tga), and entity definitions (items.def).

### Structure

The archive has a 0x14-byte header:

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0x00 | 4 | uint32 | Header size (0x14) |
| 0x04 | 4 | uint32 | Archive ID |
| 0x08 | 4 | uint32 | File count |
| 0x0C | 4 | uint32 | Entry size (0x24) |
| 0x10 | 4 | uint32 | Index offset |

The file index is located using the header's index offset. It is usually at the end of the archive but the parser supports archives with trailing footers. Each index entry is 0x24 bytes:

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0x04 | 4 | uint32 | File data offset |
| 0x08 | 4 | uint32 | File data size |
| 0x10 | 16 | char[16] | Filename (null-terminated, Latin-1) |

File data is stored uncompressed at its declared offset and can be read directly by seeking.

### Editor behavior

The editor scans for .pff files in the game folder and any configured additional PFF folders. Archives are searched in priority order:

1. Additional PFF folders (later folders override earlier ones)
2. `localres.pff` (highest base priority — local mod overrides)
3. Expansion archives (`exp1.pff`, `exp2.pff`, etc.)
4. Other archives
5. `resource.pff` (lowest priority — base game assets)

`med.pff` is always skipped as it contains editor-only assets not used by the game.

When looking up a file, the editor checks each archive in priority order and returns the first match.

---

## .bms — Map Placement Data

BMS files contain the entity placement table for a map. Every building, vehicle, soldier, tree, and object placed on the map has an entry here.

### Structure

The file begins with a `BMS\x11` magic identifier. Two uint16 block sizes at offsets 0x242 and 0x246 determine where the entity table starts:

```
entity_start = 0x268 + block_a + block_b
```

Each entity record is 0xB0 (176) bytes:

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0x00 | 2 | uint16 | Type ID (0 = end of table) |
| 0x04 | 2 | uint16 | SSN (entity instance number) |
| 0x0E | 1 | uint8 | Flags (bit 0x20 = invincible) |
| 0x10 | 4 | int32 | X coordinate (fixed-point / 65536) |
| 0x14 | 4 | int32 | Y coordinate (fixed-point / 65536) |
| 0x18 | 4 | int32 | Z coordinate (fixed-point / 65536) |
| 0x34 | 2 | int16 | Heading (degrees) |
| 0x36 | 2 | int16 | Pitch (degrees; `90` is up in MED's convention) |

The entity table ends when a type ID of 0 is encountered. Up to 32 leading empty slots at the start of the table are tolerated and skipped (some third-party map editors leave deleted entity slots).

### Editor behavior

For each entity, the editor looks up its type ID in items.def to determine its category (building, vehicle, decoration, object, foliage) and its 3D model filename. This drives both the visual rendering and the generator's collision detection.

The editor also derives a terrain floor height from entity Z positions. It primarily clusters non-static entity heights and takes the modal floor of the lowest dense cluster. A special sparse-dynamic rescue path can use a much denser lower cluster from all placed entities, which avoids stray underground markers pulling the floor estimate down. Maps with no dynamic entity heights use the 18.5 metre fallback.

BMS files also carry embedded terrain name tokens. If no sibling .mis file exists, the editor scans the BMS binary for printable strings that resolve to a valid .trn asset.

---

## .3di — 3D Model Geometry

3DI is NovaLogic's model format containing both collision geometry (CModel) and render mesh LODs. The editor uses these for entity wireframes in both 2D and 3D views, and for collision detection in the generator.

### Structure

The file starts with a 4-byte magic:

| Magic | Family |
|-------|--------|
| `GPM\x02` | Standard model (identifier proven; descriptive name is an interpretation) |
| `GPS\x02` | Skinned model (identifier proven; descriptive name is an interpretation) |
| `GPP\x02` | Prop/particle model (identifier proven; descriptive name is an interpretation) |

The CModel section begins with a 0x88-byte header containing section counts:

| Header offset | Type | Description |
|---------------|------|-------------|
| 0x04 | uint32 | CModel blob size |
| 0x30 | uint32 | Vertex count (type A — collision vertices) |
| 0x38 | uint32 | Vertex count (type B) |
| 0x40 | uint32 | Face count |
| 0x48 | uint32 | Group count |
| 0x50 | uint32 | Section 0C count |
| 0x58 | uint32 | Section 10 count |
| 0x60 | uint32 | Region count |

The CModel blob follows immediately after the header. It is divided into sections laid out consecutively:

| Section | Entry size | Description |
|---------|-----------|-------------|
| Vertices A | 8 bytes | int16 X, Y, Z + padding (scale: raw / 256.0 metres) |
| Vertices B | 8 bytes | Secondary vertex data |
| Faces | 0x2C (44) bytes | 3 vertex indices (int16) + flags + normals |
| Groups | 0x80 (128) bytes | Group-local vertex/face ranges |
| Section 0C | 0x0C bytes | |
| Section 10 | 0x10 bytes | |
| Regions | 0x60 bytes | Spatial region definitions |

The geometry is organized into groups. Each group owns a contiguous range of vertices and faces. Vertex indices in faces are group-local.

Face flags control collision behavior:
- Bit 0x0001: non-physical (foliage, passthrough)
- Bit 0x0100: light projector (non-collision unless combined with 0x0400)
- Bit 0x0400: physical collision surface

### Editor behavior

The editor parses 3DI files in two ways:

**2D view:** Extracts CModel edges as top-down line segments. Normally this uses the compact X/Y footprint; entities with non-zero pitch use the raw 3D model so the rotated depth/height projects correctly onto the map plane.

**3D view:** Extracts full 3D line segments with height (X/Y/Z), applying entity pitch before heading. Used for the 3D wireframe renderer. Supports multiple LODs.

**Generator:** Extracts collision triangles and walkable surfaces for placing nodes. Entity pitch is applied to local 3D collision/support geometry before heading and world placement. The face collision flag determines which faces block AI movement and which are passthrough.

The optional 3D reference model is also loaded from the user's configured DFBHD installation at runtime. `Delta01.3di` is searched as a loose file and through the active PFF stack; its derived wireframe is kept in memory for the current session and is not bundled with the editor.

Some large buildings place the CModel at non-standard offsets. The parser first tries the direct offset from the MED load sequence, then falls back to a bounded scan for a valid CModel header.

---

## .cpt — Compiled Terrain Heightmap

CPT files contain the terrain elevation data compiled by NovaLogic's terrain tools. Referenced from .trn files via the `polytrn_polydata` field.

### Structure

The file contains a `DPTH` chunk tag followed by a 1024 × 1024 array of uint16 height values (2 MiB of height data).

Height values are stored in little-endian byte order. The conversion to world height is:

```
height_metres = raw_value / 256.0
```

If the `DPTH` marker is not found, the editor runs a compatibility probe that scores candidate offsets for terrain-like height distributions (smooth gradients, reasonable value ranges).

### Editor behavior

The heightmap provides three terrain display modes in the 2D view:

- **C (Color):** The TGA colormap image
- **H (Height):** Green-scale visualization of the DPTH data
- **D (Depth):** Two-tone visualization showing flat vs. elevated areas

The heightmap is also used for terrain-aware node Z positioning in the generator, providing ground elevation at any world coordinate through bilinear interpolation.

---

## .trn — Terrain Layout Definition

TRN files are text-based script files that define terrain geometry and asset references. They describe how the terrain is assembled from image tiles and heightmap data.

### Structure

TRN files use NovaLogic's key-value script format (one `key value` pair per line, values optionally quoted). Comments are supported: `//` and `#` as full-line comments, and `;` for inline comments.

Key fields:

| Field | Description |
|-------|-------------|
| `polytrn_colormap` | TGA colormap filename (visual terrain texture) |
| `polytrn_depthmap` | Secondary terrain image |
| `polytrn_detailmap` | Detail terrain image |
| `polytrn_polydata` | CPT heightmap filename |
| `polytrn_sectorcount` | Number of authored sectors per axis (1-16) |
| `polytrn_origin` | World origin as two floats (sector units) |
| `polytrn_sectors` | Sector tile ID rows (expanded to 16×16 grid) |
| `polytrn_wrapx` | X-axis wrap mode (0 = repeat edge, 1 = wrap) |
| `polytrn_wrapy` | Y-axis wrap mode (0 = repeat edge, 1 = wrap) |
| `polytrn_tilestrip` | Tile strip asset |
| `polytrn_tileinfo` | Tile info asset |

Each terrain sector covers 512 metres of world space. The sector table is expanded to a fixed 16×16 grid following the same expansion rules used by MED (the original map editor).

### Editor behavior

The editor resolves the TRN chain to load terrain assets: colormap for the 2D backdrop, CPT file for heightmap data, and secondary images for terrain debug views.

If the TRN references tilestrip or tileinfo assets, the editor notes that its terrain rendering is approximate, since it does not fully reconstruct the poly terrain build.

---

## .mis — Mission File

MIS files are text-based script files used by NovaLogic's MED editor. They reference the terrain definition and contain mission-level settings.

### Structure

Same key-value format as .trn files.

Key fields used by the editor:

| Field | Description |
|-------|-------------|
| `terrain` | Terrain name (resolves to a .trn file) |
| `water_level` | Water surface height (raw value × 0.5 = world Z) |
| `terrain_tile_tga` | Terrain tile texture |

### Editor behavior

When a .bms file is opened, the editor looks for a sibling .mis file with the same name. If found, it reads the `terrain` field and follows the chain: MIS → TRN → colormap + CPT.

The water level from the MIS sets the height of the water plane rendered in the 2D view.

If no sibling .mis is found, the editor falls back to scanning the BMS binary for terrain tokens that resolve to a valid .trn asset.

The editor can also load .mis files manually through File > Load Terrain MIS (developer mode).

---

## .def — Entity Definitions (ITEMS.DEF)

ITEMS.DEF is NovaLogic's master entity definition file. It maps type IDs to entity properties: name, category, 3D model filename, attributes, and scale.

### Structure

The file may be SCR-encrypted or already plain text. Files starting with `SCR\x01` are decrypted by reversing the payload bytes and applying an XOR key schedule (rotate-left cipher with key 0x2A5A8EAD). Plain text files are used directly.

Once decrypted, the file is plain text with begin/end blocks:

```
begin EntityName
  id 101234
  type Building
  graphic RE_Bld1
  scale 1.0
  attrib: solid static
end
```

Key fields:

| Field | Description |
|-------|-------------|
| `id` | Raw ID (subtract 100000 for the type ID used in BMS records) |
| `type` | Entity category (building, vehicle, decoration, object, foliage) |
| `graphic` | 3D model name (resolved to a .3di filename) |
| `scale` | Model scale factor |
| `attrib:` | Entity attributes (solid, static, etc.) |

### Editor behavior

The editor loads items.def from PFF archives or as a loose file from the game directory. Expansion archives can carry additional or overriding definitions that are merged in priority order (base game first, expansions override).

The entity category determines how the editor treats each placed object:

- **building:** Rendered as wireframes, used as collision obstacles by the generator
- **decoration, object:** Rendered as wireframes; filtered by physical attributes and the destroyable-object policy (clearable props with explosive prefixes are skipped unless invincible)
- **vehicle:** Rendered as wireframes, can be excluded from generation with the "Ignore vehicles" toggle
- **foliage:** Rendered as hull outlines; tree-like foliage with usable CModel geometry can participate in collision, while bush/grass-style visual foliage is skipped by the generator by default

The graphic field links to a .3di file, which the editor loads from the PFF archive stack to get the model's collision geometry and wireframe mesh.

---

## Format chain

When a map is opened, the editor follows this resolution chain:

```
.bms ──→ entities (type ID → items.def → .3di from .pff)
  │
  ├──→ sibling .mis ──→ .trn ──→ colormap (.tga from .pff)
  │                                ├──→ heightmap (.cpt from .pff)
  │                                └──→ secondary images
  │
  └──→ (fallback) BMS terrain tokens ──→ .trn ──→ ...
```

The .ain file is loaded separately on top of the map data and is independent of the BMS/terrain chain.
