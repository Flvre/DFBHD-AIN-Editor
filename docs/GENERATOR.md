# Generator

This document describes how the navigation graph generator works, from the core placement mechanics to each individual option. It is intended as a technical reference for understanding what the generator does and why.

## 1. What the generator attempts to produce

The generator creates AI navigation nodes automatically based on map geometry. It takes collision data from the map's entities (buildings, walls, vehicles, decorations) and terrain elevation data, then places nodes where AI soldiers can stand and walk.

The process runs in several phases:

1. Geometry from the loaded map is clipped to the generation area (seed disc or zone)
2. Collision segments and triangles are indexed into a spatial grid for fast lookup
3. The walker places candidate nodes on walkable ground, respecting collision boundaries
4. Clearance-fit classifies each node's radius based on how much space surrounds it
5. Connections are built between nearby visible nodes
6. Post-generation cleanup removes bad nodes and fixes edge issues
7. The metadata scanner stamps direction and control values based on nearby geometry

Node positions use the game's fixed-point coordinate system (int32 / 65536). Node height is calculated as terrain surface + 1.2489 metres, the editor's confirmed node-height offset.

The generator is geometry-reactive: it places nodes based on collision geometry and walkable surfaces, not by filling every open space.

## 2. Seed generation and area generation

The generator supports two modes: seed-based and zone-based.

**Seed-based generation:**
After setting options in the Generate AIN dialog, the user clicks a point on the map. That click becomes the seed point. The generator runs outward from the seed within the specified radius, placing nodes using the walker described in section 10.

**Zone-based generation:**
Instead of clicking a seed, the user selects a zone with tiles from the dropdown in the Generate AIN dialog. The radius field is disabled. The generator splits the zone's tile area into local generation jobs, each covering a bounded region. Each job runs the same generator with a local center and radius derived from its tile area. Jobs run sequentially and boundary seams between adjacent jobs are stitched so nodes connect across job boundaries.

Area-zone generation with the automatic Z filter can miss rooms or floor layers because tile boundaries split the local jobs. For buildings with navigable interiors, a normal circle seed with the Z filter enabled is recommended.

Zone-based generation uses the same walker, collision detection, and cleanup as seed-based generation. The difference is only in how the generation area is defined.

**Ignore-collision zone fill:**
The Area Zones panel also has a separate generation mode (Generate menu > Fill Selected Zone) that fills the zone with a simple grid of evenly spaced nodes, ignoring collision geometry entirely. This is a distinct function from the main generator and does not use the walker or any collision checks.

## 3. Radius limits

The generation radius defines how far from the seed point the walker will place nodes. It is set in the Generate AIN dialog.

- Minimum: 8 metres
- Maximum: 80 metres
- Default: 32 metres

The entered value is clamped to this range at the start of generation. If a `radius_cap` is supplied (for example by zone-based tiling), the cap also applies.

When generating into a zone, the radius field is disabled. Each zone tile job derives its own local radius from its tile area instead.

## 4. Automatic generator Z filtering

The Z filter controls which surfaces the generator treats as walkable ground. It is off by default and is useful for buildings with navigable interiors.

When enabled, the generator builds a reachable surface map before the walker runs. Starting from the seed point's support height, it floods outward through neighboring surface cells. Two cells are connected only if their height difference is within a maximum step threshold:

- Normal ground step: 0.35 metres (`GENERATOR_REACHABLE_MAX_STEP`)
- Stair step: 0.75 metres (`GENERATOR_REACHABLE_STAIR_STEP`, used when both cells have model-sourced stair geometry)

The flood uses a 0.25 metre raster grid over the generation disc. Each cell can have multiple support heights (terrain and model surfaces stacked vertically). The flood tracks which height layer at each cell is reachable.

**Standing volume check:**
Before a model surface is accepted as a support candidate, it must pass a standing volume test. The `_GeneratorStandingVolumeIndex` checks that the vertical column from 0.20 metres above the surface to 2.00 metres above it is clear of collision triangles. This prevents surfaces inside solid geometry from becoming walkable.

**Transition blocking:**
The flood also checks whether a wall segment crosses the path between two neighboring cells at body height (0.20 to 1.80 metres above the support). If a collision segment intersects the transition at standing height, the cells are not connected even if their height difference is within the step limit.

Surfaces that are not reached by the flood (tables, chair seats, shelving, disconnected ledges) are excluded from the walkable surface map. The walker then only places nodes where the reachable surface map has valid heights.

## 5. Elevated platforms and rooftops

This option adds nodes on elevated surfaces that are not connected to the ground-level flood. It is off by default and requires the Z filter to be enabled.

When the Z filter finishes its flood from the seed, surface cells that were not reached form disconnected components. With rooftop generation enabled, the generator evaluates eligible disconnected components as possible elevated platforms. Without rooftop generation, elevated components are normally discarded, but sufficiently broad non-decoration structural layers at or below the seed can still be recovered as basements or tunnels.

- Components must have at least 48 raster cells to be considered (prevents small clutter surfaces from becoming platforms)
- Below-ground components are rejected (the generator checks each cell against terrain height)
- Components with no terrain data use a conservative fallback based on the seed height
- Decoration-only model surfaces are excluded (crates, furniture stacks do not become platforms)

Qualifying components are added to the reachable surface map as roof layers. The walker then places nodes on these elevated surfaces in addition to the ground level.

The seed height selection changes when rooftops are enabled. Instead of starting from the nearest terrain height, the generator starts from the model support layer under the click point, so clicking on a port deck starts the flood on the deck rather than the ground below.

## 6. Highest broad surface behavior

This option restricts rooftop generation to only the highest exposed structural surface of each building. It is off by default and requires rooftops to be enabled.

Without this option, every qualifying elevated component is admitted (all floors, mezzanines, and rooftops that pass the 48-cell and above-ground checks). With it enabled, the generator:

1. Groups model support surfaces by the building entity they belong to (decoration category surfaces are excluded)
2. For each building, finds coherent structural sheets (connected surfaces within a tight 0.18 metre step tolerance)
3. Requires each sheet to have at least 48 cells and a two-cell-deep interior (measured using 12 neighbor offsets including diagonal and two-cell-away positions)
4. Keeps only sheets whose cells are at the entity's highest support height (exposed surfaces, not covered floors)
5. Exposed sheets must also have an interior region of at least 48 cells

The result is that only broad, flat rooftops are added as walkable surfaces. Narrow parapets, roof trim, and internal floors are filtered out. The ordinary ground-level Z-aware domain is kept unchanged.

## 7. Ladder inclusion

This option adds vertical ladder connections between floors. It is off by default and requires rooftops to be enabled (which in turn requires the Z filter).

The ladder detection works by scanning 3D collision geometry for horizontal rung-like edges:

1. Edges are collected that are nearly horizontal (vertical difference under 0.08 metres) and between 0.30 and 2.25 metres long
2. These edges are grouped by entity, position (rounded to 0.5 metre grid), and angle (rounded to 10 degrees)
3. Groups with regularly spaced vertical levels form rung profiles (a ladder is a stack of evenly spaced horizontal rungs)
4. Each rung profile defines a vertical column with a bottom and top height

When two different generated components (for example, ground level and a rooftop) each have nodes near the bottom and top of a detected ladder, the generator creates a narrow precision chain of nodes linking the two levels. These ladder bridge links connect otherwise disconnected vertical layers of the navigation graph.

## 8. Destroyable object handling

This option skips objects that can be destroyed during gameplay so they do not create permanent holes in the navigation graph. It is on by default.

An entity is considered a clearable prop when all of these conditions are met:

- Its category is `decoration` or `object` (buildings and vehicles are never clearable)
- Its `sounddeath` field starts with an explosion prefix: `EXPLO_FUEL`, `EXPLO_CRATES`, or `EXPLO_DOOR_MTL` (for doors, the entity name must also contain "door")
- It is not a vehicle (checked by `_item_is_vehicle`)
- Its per-instance invincible flag is not set (record byte 0x0e, bit 0x20)

When a clearable prop is identified and the option is enabled, the generator excludes that entity from its collision geometry entirely. This means the walker can place nodes through the space where explosive barrels, crates, and destructible doors sit, since those objects will vanish or open when destroyed in-game.

Objects that only have hit points but leave obstructive remains (rocks, walls, fences) are not clearable. Vehicles are never clearable because their wrecks stay as obstacles. Entities with the invincible flag set are kept as permanent collision regardless of their destruction sound.

## 9. Vehicle handling

This option skips vehicle entities during generation. It is off by default.

When enabled, entities whose category is `vehicle` are excluded from the generator's collision geometry. The walker can then place nodes through the space occupied by vehicles.

This exists because mission vehicles can move away from their authored map positions during gameplay. If the generator treats them as permanent obstacles, the resulting graph may have gaps where the vehicles were parked but will not remain.

Parked vehicles and vehicle husks that are authored as decorations (not true vehicle-category entities) remain as permanent blockers even when this option is on. Only entities classified as `vehicle` by `_generator_entity_category` are affected.

## 10. Normal placement

The walker is the core placement mechanism. It uses depth-first traversal to grow a navigation graph outward from the seed point.

**Starting the walk:**
The walker begins at the clicked seed point. If the seed location is not walkable (inside a wall or on invalid ground), it tries up to 500 random points within the generation radius until it finds a valid starting position.

**Scan pattern:**
From each ordinary placed node, the walker tries 10 candidate directions relative to the parent node's heading: 0°, ±30°, ±60°, ±95°, ±135°, and 180°. Confirmed corridor nodes preserve a forward/backward traversal and do not use the full fan. The first direction that passes all checks becomes the next node, and the walker continues from there.

**Step distance:**
The distance between a parent node and its candidate is based on the parent's radius:
- Corridors: 1.40 × radius
- Open outdoor terrain: 1.50 × radius
- General placement: 1.66 × radius
- Minimum step: 1.05 metres

General steps include a small random variation (±9% distance, ±8° heading) to avoid perfectly regular grids. Corridor steps use the same distance variation but only a small ±1.5° heading jitter. Outdoor steps are deterministic for consistent spacing.

**Walkability check:**
Each candidate position must pass a walkability test with a minimum clearance of 0.45 metres from the nearest collision wall. If a candidate fails, the next scan angle is tried.

**Backtracking:**
If none of the 10 scan angles produce a valid candidate, the current node is popped from the stack and the walker backtracks to the previous node. This continues until the stack is empty.

**Regrow seeds:**
When the stack empties but uncovered walkable areas remain within the generation disc, the walker restarts from those uncovered areas. Each regrow seed continues the walk away from the nearest existing node to avoid duplicating coverage.

**Growth phases:**
For large generation radii, the walker grows in expanding radial phases. When one phase completes, the nodes at the outer boundary are re-armed as active frontier nodes and the walker continues into the next ring outward.

## 11. Quantized placement

Quantized placement is experimental. It admits nodes onto a 0.25 metre coordinate lattice using a 0.50 metre base pitch, producing more uniform spacing than the natural walker.

The method may fail to generate nodes properly at building doorways, and AI pathfinding through entrances may be incomplete on maps with interior areas. The editor displays this warning before the first quantized run; **Never show again** stores the user's choice. Missing doorway or entrance transitions may require manual node placement or linkage.

## 12. Stair and floor handling

The generator recognizes stair geometry during the reachable surface build and applies special rules to allow the flood to traverse vertical steps.

**Stair step threshold:**
Normal surface transitions use the 0.35 metre max step (`GENERATOR_REACHABLE_MAX_STEP`). When both the current cell and its neighbor have model-sourced support with at least one side flagged as a stair sequence candidate, the threshold increases to 0.75 metres (`GENERATOR_REACHABLE_STAIR_STEP`). This allows the flood to climb individual stair treads that would otherwise exceed the normal step limit.

**Stair riser bypass:**
Collision segments that terminate at the height of the next tread act as stair risers. Without special handling, these would block the flood as chest-height walls. The transition blocker allows step risers through when both sides are confirmed as part of a multi-tread flight and the collision edge ends at or below the higher support height.

**Geometry stair recovery:**
Some CModel stair geometry is not pre-flagged as stairs in the collision metadata. After the initial flood, the generator checks whether any building entity has reachable lower floors but unreachable upper floors that contain validated geometric stair sequences. If so, those stair components are added to the reachable set and the flood continues through them.

**Layered stair nodes:**
Nodes placed on verified stair treads are tagged as `layered_stair`. These nodes receive a relaxed clearance floor during validation (0.20 metres instead of the normal 0.62 metres) because stair treads are inherently narrow. A dedicated stair chain linking pass runs after the main graph build to ensure adjacent treads in the same flight are connected, since the angular fabric pass can miss them due to heavy top-view overlap.

**Floor detection:**
When the Z filter is on, different floor levels within the same building are handled as separate support layers at the same raster cell. The flood attempts to connect floors that are reachable via stairs or ramps. In normal mode this generally succeeds; in quantized mode some doorway and floor transitions may not reliably connect (see section 11). Floors that can only be reached by ladders remain disconnected unless ladder inclusion is enabled.

## 13. Boundary seams between generated and existing graphs

When new nodes are generated next to an existing graph (from a previous generation pass or a manually placed graph), the generator stitches the two together with boundary seam links.

The seam stitching function (`_add_generator_old_new_seams`) runs after each generation batch:

1. Existing nodes are indexed into a spatial grid (8 metre cell size)
2. For each new node near the boundary, the function finds existing nodes within linking distance
3. Each candidate link is tested against collision geometry: 2D segment intersection (wall crossing) and 3D collision face intersection (floors and ceilings between the nodes)
4. Links that pass collision checks and are not directionally redundant with existing connections are added

The stitching preserves the existing graph structure. It only adds new links between old and new nodes; it never modifies or removes existing connections. This means generating multiple overlapping seeds or zones builds up a connected graph incrementally.

For zone-based generation, seam stitching also runs between adjacent tile jobs within the same zone. Each tile job generates independently, and the boundary between tiles is stitched so the resulting graph connects across tile boundaries.

Ladder seam links are tracked separately and carry the old-to-new node mapping so that ladder bridge connections created in the current batch can be extended to existing nodes on adjacent floors.

## 14. Cleanup after generation

The generator runs several validation and cleanup passes after node placement and before returning the final graph.

**Solid geometry rejection:**
Nodes whose center falls inside enclosed solid geometry are rejected. The generator builds 2D enclosed-mesh grids for closed building footprints and checks each node against them. Nodes inside solid models are dropped unless the Z filter has independently proven the node's support layer is reachable and the 3D standing column is clear (which handles navigable rooms inside buildings like warehouses).

**Clearance gate:**
Each node must have sufficient clearance from hard collision walls. The required clearance depends on the node's radius and classification. Corridor-fit nodes use a 0.85 factor, outdoor nodes use a dedicated outdoor acceptance factor, and standard nodes use the default radius clearance factor. The minimum center clearance is 0.62 metres for normal nodes and 0.20 metres for stair nodes.

**Standing volume validation:**
The standing volume index checks that the vertical column from 0.20 to 2.00 metres above each node's support surface is clear of collision triangles. Nodes that fail this check (inside solid floors, ceilings, or thick model geometry) are rejected.

**Graph backbone build:**
After validation, surviving nodes are connected by nearest-visible links. Candidate edges are sorted by distance. A two-phase pass first attempts to bring every node to at least degree two (backbone), then fills remaining connections up to a degree limit (6 for normal walker, 5 for quantized). An emergency fill attempts to link any node still below degree two, though some quantized doorway nodes may still receive zero or one valid candidate due to geometry constraints.

**Stair chain repair:**
A dedicated pass connects consecutive stair tread nodes that the angular fabric missed. Landing nodes at the top and bottom of flights are connected to the nearest floor-level nodes.

The editor also provides manual cleanup tools (Rebuild Neighbors, Link by Distance, Fix One-Way Connections) that can be run after generation to further refine the graph.

## 15. Progress reporting and cancellation

The generator reports progress through a modal window that shows the current phase, detail text, elapsed time, and a progress bar.

**Progress phases:**
The generator calls an `on_progress` callback at key points during execution with a percentage (0-100), a phase name, and detail text. The major phases are:

- 2%: Preparing generator (normalizing inputs)
- 7%: Indexing collision geometry
- 12%: Resolving solid geometry (enclosed mesh detection)
- 18%: Sampling hard geometry (clearance queries)
- 27%+: Walker placement / quantized lattice admission
- 38%: Promoting quantized radius families (quantized mode)
- 48%: Repairing coverage gaps
- 58%: Resolving floors, stairs, and rooftop support
- 67%: Validating generated nodes (final collision and radius gates)
- 73%: Building navigation links (testing local visibility and connection legality)
- 87%: Connecting stair chains
- 93%: Auditing the final graph
- 96%: Writing node metadata
- 98%: Core generation complete
- 100%: Complete

**Cancellation:**
Escape or right-click cancels the armed seed-placement preview before generation starts. Once generation is running, use the progress window's **Cancel** button or close button; this terminates the persistent generator subprocess and discards the partial result. The progress window cannot be closed to accept a partial graph.

## 16. Known geometry limitations

The generator operates on the collision geometry available from parsed .3di CModel data, not the visual game meshes. This creates several inherent limitations:

**Model availability:**
If a .3di model file cannot be found in the game's .pff archives or additional PFF folders, that entity has no collision geometry. The generator will place nodes through its position as if it were empty space.

**Enclosed mesh detection:**
The solid-fill detection uses 2D footprint closure to identify enclosed buildings. Very large or complex building footprints that exceed the adaptive cell budget may not be fully resolved, potentially allowing nodes inside solid structures. The generator uses adaptive cell sizing to handle this, but edge cases remain.

**Foliage collision:**
Tree-like foliage with usable CModel geometry is included in collision preparation. Bush, shrub, grass, and other visual-clutter foliage is skipped, as is foliage with no usable CModel geometry. The classification uses items.def names plus bounded footprint heuristics.

**Pitched entities:**
The generator applies each entity's BMS pitch to its local 3D collision, support, and walkable geometry before heading rotation. This preserves the obstruction and surface shape of tilted props without changing the generator's placement algorithm.

**CModel normals:**
The generator uses the broad walkable-face classification from the CModel parser. Triangles that face upward within a normal tolerance are treated as walkable support. Steep walls and ceilings are excluded. The classification threshold works for most standard building geometry but may misclassify unusual architectural surfaces.

## 17. Cases requiring seed deletion and regeneration

Some situations require deleting the generated graph and running the generator again with different settings:

**Changed Z filter state:**
Switching the Z filter on or off fundamentally changes which surfaces are walkable. A graph generated without the Z filter will have nodes on tables, shelves, and other raised surfaces that become invalid when the Z filter is turned on, and vice versa. Regeneration is needed to rebuild the reachable surface map.

**Changed rooftop or ladder options:**
Enabling or disabling rooftop generation changes which elevated platforms receive nodes. Enabling ladders adds vertical connections that did not exist before. Since these options affect the reachable surface map and node placement, the existing graph should be cleared and regenerated.

**Changed destroyable or vehicle options:**
Toggling whether destroyable objects or vehicles are ignored changes the collision geometry the generator works with. Nodes placed through a barrel's position with the option on will conflict with the barrel's collision when the option is off.

**Relocated or modified entities:**
If map entities have been moved, added, or removed in the map editor since the last generation, the collision geometry has changed. The existing graph may have nodes inside newly placed buildings or gaps where removed buildings used to block placement.

**Seed-dependent stair connectivity:** On some interior stair layouts, the connection from a stair landing to the upper floor can fail for particular seed positions even when nearby seeds succeed. The cause is still a hypothesis involving the local Z-layer composition at the seed, not a proven engine rule. The current workaround is to delete the generated result and re-seed the area.

## 18. Cases requiring manual node placement or linkage

The generator handles most open terrain and standard building layouts automatically, but some situations need manual intervention after generation:

**Nodes inside solid buildings:**
On very large or dense maps, some nodes may end up inside solid geometry that the enclosed-mesh detection did not fully resolve. Use the cleanup function or the 3D view to identify these nodes and delete or move them manually.

**Disconnected components:**
The generator may produce multiple disconnected groups of nodes, especially around complex geometry with narrow passages. Use the graph cleanup tools (Rebuild Neighbors, Link by Distance) to connect nearby components, or manually add edges between them.

**Tight corridors and doorways:**
Very narrow passages may not receive nodes if the walker's minimum clearance (0.45 metres) prevents placement. Place nodes manually in these locations and connect them to the surrounding graph.

**Custom tactical layouts:**
The generator places nodes for general navigation coverage. Specific tactical setups like takedown zones, precise assault positions, or grenade nodes need to be placed and configured manually using the node editing tools.

**Radius tuning:**
The generator assigns radius values based on available clearance (clearance-fit), but specific gameplay requirements may need manual radius adjustments. Open areas meant for wide AI movement may need larger radii, while tight indoor spaces may need smaller values than the generator assigned.
