# DFBHD AIN Editor — User Guide

## Getting started

### Introduction
The DFBHD AIN Editor is a tool for creating and editing AI navigation graphs for Delta Force: Black Hawk Down. This guide covers everything from opening your first map to exporting a finished .ain file.

If you are a map creator looking to give AI soldiers working navigation that does not rely on MED waypoints on your custom maps, this is the tool for that. No prior experience with .ain files is needed to follow this guide, but familiarity with the game and its map structure will help.

### Installing and starting the editor

**From executable**

Download the latest release from the GitHub Releases page. Extract the archive and run the executable. On first launch, the editor will ask you to select your Delta Force: Black Hawk Down installation folder.

**From source**

Make sure Python 3.14 or newer is installed. Install the required packages:

```
pip install Pillow shapely psutil
```

Run the editor script directly:

```
python ain_editor_v1_0.py
```

On first launch, the editor will ask you to select your game installation folder. This path is saved and will be remembered for future sessions.

The game folder can be changed at any time from File > Change Game Folder.

### Opening maps and AIN files

To load a map, go to File > Open BMS and select a .bms file. The editor will read the map data, load entity placements, and attempt to load terrain and model geometry from the game's .pff archives.

To load an existing navigation graph, go to File > Open AIN and select an .ain file. The nodes and connections will appear on top of the currently loaded map.

Always open the .bms first, then the .ain. Opening a .bms will clear any loaded nodes, so the map must be loaded before the navigation graph.

If the editor cannot find certain models or terrain files, it will still load what it can. Missing geometry will simply not appear in the views.

## Views

### The 2D top view

The 2D top view is the main workspace. It shows the map from above with nodes, edges, entity outlines, terrain, and grid overlaid on a single canvas.

The view is rendered using PIL (Pillow) and refreshes as you pan, zoom, or make changes. Everything visible can be toggled from the View menu: nodes, edges, radius circles, entities, vehicles, objects, foliage hulls, grid, water, node IDs, zone colours, and more.

Entity outlines respect the BMS heading and pitch values. For a pitched model, the 2D view projects the rotated 3D geometry onto the map plane rather than drawing it as an upright footprint.

**Navigation:**
- Left click and drag to pan
- Mouse wheel to zoom
- F to fit all nodes in view
- G to toggle the grid

**Terrain display modes:**
- F1: Color mode
- F2: Height mode
- F3: Depth mode

The canvas supports both dark and light backgrounds, toggled from View > Light mode: canvas.

### The 3D wireframe view

The 3D wireframe view renders the map's collision geometry, buildings, vehicles, and decorations as wireframe outlines. It shows what the AI navigation system actually works with, not what the game looks like visually.

Entity pitch is applied to the 3D CModel and render-mesh geometry, so tilted containers, pipes, ramps, and similar props retain their map orientation.

**Navigation:**
- Left click and drag to pan
- Right click and drag to orbit
- Mouse wheel to zoom
- Shift+Left click and drag to box select (add)
- Ctrl+Left click and drag to box select (remove)

**Keyboard controls:**
- T: Top view
- S: Side view
- F: Fit view
- G: Toggle grid
- N: Toggle node overlay
- B: Toggle buildings
- V: Toggle vehicles
- D: Toggle decorations
- C: Toggle context (entities)
- L: Toggle floor-coordinate level markers
- Y: Toggle labels
- R: Refresh
- [ and ]: Navigate between nodes

The 3D view can filter what is rendered, making it possible to isolate specific buildings or layers. This is important on dense maps where rendering everything at once would be unreadable.

**Reference Model:** The side panel's **Model next to node** option draws the optional standing-human reference from `Delta01.3di` in the configured DFBHD installation. The model is loaded at runtime and is not bundled with the editor. Use the angle and distance controls to place it beside the active node. If the file is unavailable, the reference overlay simply has nothing to draw.

**Node Focus:** The side panel's **Node focus** option is a render-only lens that dims ordinary geometry so nodes and edges remain prominent. Its geometry colour can be changed with **Node Focus Geometry Color**; slider changes preview live, and the accepted colour is remembered. Node Focus does not alter model data, nodes, filters, or exported AIN data. Diagnostic colouring such as tunnel Compare takes precedence where applicable.

## Nodes

### Understanding nodes, radius and connections

A navigation graph is made of nodes and connections (edges). Each node represents a point where an AI soldier can stand. Connections between nodes define the paths AI can walk.

Every node has a radius value (b15) that controls node arrival and graph coverage. Higher values mean the AI switches to the next node from further away (suitable for open areas), while lower values require the AI to get very close before moving on (suitable for tight spaces). Radius circles should barely overlap neighbouring nodes. If the player leaves the radius coverage of the graph, AI following the player may fail to navigate even while the AI itself is still on the graph. Common values: 8 (tight), 24 (normal), 36 (open), 48 (wide open). Radius is displayed as a circle around each node in the 2D view.

The side panel inspector shows and allows editing of each node's properties:

- **Control** — Controls how precisely the AI must reach this node. 0 = Normal (most nodes), 1 = Precise (AI moves to center, useful for doorways and tight corridors).
- **Radius** — Controls node arrival and graph coverage. See above.
- **Takedown (var1)** — Sets the node's role during a takedown. 0 = Standard, 2 = Trigger node, 6 = Assault position, 8 = Grenade target, 9 = Visible marker. Leave at 0 for most nodes.
- **Takedown Zone** — Tags the node as belonging to a tactical zone (room). All nodes in the same room should share the same zone number. 0 = not assigned.
- **Takedown (var2)** — Links a takedown doorway to the zone where the player stands. Used together with Takedown (var1) = 2.

The Details button in the inspector reveals the takedown fields. Most ordinary navigation nodes do not need them.

Edits are not written to the selected node until you press **Apply Changes** or press **Enter** while in an inspector field. Inspector fields only accept numeric input — letters are rejected with a brief tooltip.

**Lock options** keep selected values or height fixed while placing new nodes. Use Update to capture the values from the current node.

**The takedown system** lets AI squads clear rooms automatically or on player command:
1. Set up zones using the Takedown Zones panel in the top bar
2. Tag nodes with the correct Takedown Zone
3. Place assault nodes (Takedown var1 = 6) where the squad should move
4. Place grenade nodes (Takedown var1 = 8) where AI should throw grenades
5. Mark the trigger doorway nodes with Takedown (var2) = the player's zone ID

Connections are bidirectional. If node A connects to node B, node B also connects back to node A. The editor will flag and can fix cases where a connection exists in only one direction.

### Creating, moving and deleting nodes

**Creating nodes:**
In Edit mode, left click on the canvas to place a new node at that position. The node will be assigned a default radius and metadata values.

In Draw mode, click and drag to place nodes continuously along the path of your cursor.

**Moving nodes:**
Select a node and use the arrow keys to nudge it. Arrow keys move the node by 1 metre. Shift+Arrow keys move by 0.1 metre for fine positioning. Ctrl+Arrow keys also move by 0.1 metre.

**Deleting nodes:**
Select a node and press Delete, or right click a node and choose Delete node from the context menu.

### Connecting and disconnecting nodes

**Creating connections:**
Right click a node and choose "Connect from here" to start an edge. Then click the target node to complete the connection.

You can also use the Connect by ID overlay to type in a node ID and connect directly.

Ctrl+Right click and drag acts as an edge brush, drawing connections along the drag path.

**Removing connections:**
Select one or more nodes and use Edit > Remove Selected Connections or the right click context menu. This removes all edges touching the selected nodes.

Shift+Right click and drag acts as a cut tool, severing any edges the drag line crosses.

### Selection and locking tools

**Selecting nodes:**
Left click a node to select it. The selected node is highlighted and its metadata appears in the side panel.

Shift+Left click and drag to box select multiple nodes (adds to the current selection). Ctrl+Left click and drag to box select and remove from the current selection.

Press U to clear the current selection.

**Copy and paste:**
Ctrl+C copies the selected nodes and their relative positions. Ctrl+V pastes them at the cursor location.

### Node metadata and the metadata scanner

Direction (b16) is metadata stamped onto nodes by the generator or by the metadata scanner. It records a world-anchored direction for each node, but the game ignores it: it has no effect on AI movement or pathfinding. Non-zero values draw an arrow in the editor.

The metadata scanner is a post-placement pass that stamps Direction and Control values onto nodes based on their position relative to nearby geometry. This tries to reproduce the same pass that NovaLogic's original tool performed after placing nodes.

In Developer mode (View > Developer mode), raw byte names (b12, b13, b14, b15, b16, b17, b18) are shown instead of the semantic labels.

## Graph and zones

### Graph cleanup tools

The editor provides several tools for fixing and maintaining the navigation graph:

- **Rebuild Neighbors** (Generate menu or right click): Rebuilds the neighbour connections for all or selected nodes based on proximity and collision checks. **Ctrl+R** in the 2D view rebuilds only when nodes are selected; with no selection it does nothing.
- **Link by Distance** (Generate menu or right click): Opens a dialog to rebuild edges within the selection using a maximum distance and maximum neighbours per node limit.
- **Fix One-Way Connections** (Generate menu): Finds cases where node A lists node B as a neighbor but node B does not list node A back, and adds the missing neighbor entry.
- **Remove Selected Connections** (Edit menu or right click): Deletes all edges that touch the selected nodes.

### Zones and zone colours

Zones are named, colored regions that can be drawn over the map. They serve two purposes: organizing nodes into logical groups and defining areas for zone-based generation.

**Managing zones:**
- Press F8 or go to Zones > Zone Layers to open the zone layer manager
- Each zone has a name, a color, and a set of tiles defining its area
- Zones can be drawn in Area Zone mode (AZ button in the toolbar)
- Delete to remove the selected zone
- Zones > Clear all zones removes all zone definitions

**Zone-based generation:**
When generating from the Generate menu, you can select a zone with tiles instead of placing a seed. The generator will fill the zone's area with nodes.

Zone colours can be displayed on nodes and connections through the View menu toggles: Zone colours and Connection zone color.

## Generator

### Using the generator

The generator creates navigation nodes automatically based on map geometry. Open it from Generate > Generate from BMS.

**Radius:** The generation area around the clicked seed point, between 8 and 80 metres. Disabled when generating into a zone.

**Generate zone:** Instead of clicking a seed, select a zone with tiles to fill that area with nodes.

**Automatic generator Z filter:** Keeps nodes on walkable ground and ignores raised surfaces like tables and chairs. Useful for buildings with navigable interiors.

**Generate on elevated platforms / rooftops:** Adds nodes on eligible elevated surfaces. Requires the Z filter to be on.

**Limit added platforms to highest broad surfaces:** Only generates on the highest broad structural surface instead of all eligible platforms. Requires rooftops to be on.

**Include ladders:** Adds ladder connections between floors. Requires rooftops to be on.

**Ignore destroyable objects:** Skips objects that can be destroyed during gameplay so they don't block the graph. On by default.

**Ignore vehicles:** Skips vehicle entities during placement. Off by default.

**Quantized placement:** Snaps nodes to a grid instead of natural walker placement. Produces more uniform, evenly spaced graphs.

Quantized placement is experimental. Before it runs, the editor warns that doorway and entrance nodes may not be generated correctly on maps with interior areas. The warning can be dismissed with **Never show again**. If an interior transition is missing, place or link the affected nodes manually.

After setting the options, click Generate. For seed-based generation, click on the map to place the seed point. The generator will place nodes, create connections, and run cleanup passes automatically.

See the full generator guide (GENERATOR.md) for detailed behavior and limitations.

## Map data

### Map entities, view filters and terrain

The editor reads entity data from .bms map files. Entities include buildings, vehicles, trees, decorations, and other objects placed on the map.

**View filters (View menu):**
- Entities: Toggle building and static object wireframes
- Vehicles: Toggle vehicle wireframes
- Objects: Toggle object wireframes
- Trees only: Filter foliage to tree-like entities and hide bush/weed/fern clutter
- Foliage hulls: Show foliage collision hulls (Developer mode)
- Other Entities: Show remaining entity markers (Developer mode)
- Show water: Render the water plane

Entity and foliage wireframe colors can be customized through Edit > Entity Colors, with separate settings for dark and light canvas modes. The dialog stays on the left side of the editor so it does not interrupt the main workspace.

**Terrain:**
Terrain data is loaded automatically when a .bms is opened, reading from .cpt and .trn files referenced by the map. The terrain provides elevation data used for node Z positioning and is displayed as a background layer in the 2D view.

The terrain display mode can be switched between Color (F1), Height (F2), and Depth (F3) views.

**Additional PFF folders:**
If your game files are somehow split across multiple locations, use File > Additional PFF Folders to add extra search paths for .pff archives. The editor will search all configured folders when loading models and terrain.

**Area-zone warning:** Area-zone generation with the automatic Z filter can miss rooms or floor layers because of tile boundaries. For buildings with navigable interiors, use a normal circle seed with the Z filter enabled instead.

**Coordinates and previous seeds:** **Generate with coordinates...** accepts an explicit X/Y seed and resolves its Z support from the map. **Previous Generator Seeds...** shows saved and current seed submissions and can retry one without re-entering its options. Project JSON saves this history (up to 500 entries), including X/Y, radius, target node height, map reference, and each seed's generator settings. Load the project and select **Retry Seed** to resubmit a recorded seed. The referenced BMS map and game resources must be available; they are not embedded in JSON. Older projects without saved seeds load with an empty history. Clearing history takes effect in the saved file when you save the project again.

## Controls

See [Keyboard and mouse controls](KEYBINDS.md) for the finalized keybind appendix. The [illustrated PDF guide](AIN_Editor_Guide.pdf) includes the chart alongside the editor screenshots.

## Output

### Exporting AIN files

To export the current graph as an .ain file, go to File > Export AIN. Choose a filename and location. The editor will write the complete navigation graph in the binary .ain format that the game reads.

Before exporting, make sure the graph is in a usable state: nodes should be connected and disconnected components or possible problematic nodes should be addressed.

The editor also supports saving and loading projects as JSON files (File > Save Project / Load Project). Project files preserve nodes, zones, metadata, zone definitions, generator options, and previous generator seeds with their individual settings for retrying runs. **Autosave (JSON)** is off by default; enable it from the File menu if you want the current project written approximately every five minutes. Use project files for work in progress and export to .ain when the graph is ready for the game. Or open/edit/export .ain if you don't care about preserving the area zones.

## Reference

### Known limitations

The generator does not guarantee a finished graph for every map and it might require manual tweaks such as moving, connecting or deleting nodes.

The foliage (trees, bushes) could cause performance issues depending on how many are present in a map.

The 3D View could cause massive performance issues depending on how many entities are rendered.

Some model geometry may not be available if the corresponding .3di file cannot be found in the game's .pff archives or additional PFF folders.

Quantized placement may fail to generate doorway or entrance nodes on interior maps. Area-zone generation with the automatic Z filter may miss rooms or floor layers at tile boundaries. Stair-to-upper-floor connectivity can also vary with the seed position; re-seeding is the current workaround while the cause remains under investigation.

### Recommended workflows

**Starting a new graph from scratch:**
1. Open the .bms map file
2. Use Generate > Generate from BMS to open the generator options
3. Set your radius and options, then click on the map to place the seed
4. After generation, inspect the result and make manual adjustments where needed
5. Use the graph cleanup tools to fix isolated nodes that are usually found leaking in entities
6. Export to .ain

**Editing an existing graph:**
1. Open the .bms map file
2. Open the existing .ain file
3. Make changes using the editing tools
4. Export to .ain when done

**Working with zones:**
1. Switch to Area Zone mode
2. Draw zone boundaries on the map
3. Use Generate > Generate from BMS and select the zone to generate within the defined area
4. Repeat for additional zones as needed
