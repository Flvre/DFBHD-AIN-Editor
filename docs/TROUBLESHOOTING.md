# Troubleshooting

Common issues grouped by their most likely cause.

---

## Game folder not set correctly

These issues are caused by the editor not being able to find the game's .pff archives, which contain models, terrain data, and entity definitions.

Fix: File > Change Game Folder and point it to your Delta Force: Black Hawk Down installation. If your game files are split across multiple locations, use File > Additional PFF Folders to add extra search paths.

**No terrain showing after opening a BMS:** The editor needs .trn, .cpt, and colormap files from the game archives to display terrain.

**Missing entity wireframes:** The editor cannot render an entity if its .3di model file is not found in any loaded .pff archive.

**Nodes appear at the wrong height:** Without the terrain heightmap (.cpt), node Z values fall back to a flat estimate derived from entity positions instead of actual ground elevation.

---

## Load order

**Opening AIN before BMS clears the graph:** Opening a .bms file clears any loaded nodes. Always open the BMS first, then the AIN. This is intentional map-reset behavior.

---

## Performance

**3D view is slow or unresponsive:** Performance depends on the amount of entities visible. Use the 3D view filters (B for buildings, V for vehicles, D for decorations, C for context) to hide categories you don't need.

**Foliage causes lag in the 2D view:** Maps with a large number of trees and bushes can slow down the 2D canvas. Toggle foliage visibility from the View menu if performance is affected.

---

## Generator behavior

**Generator does not fill the entire area:** The generator is geometry-reactive. It places nodes based on walkable ground and collision geometry, not by filling every open space.

**Generator places nodes inside buildings with no available interiors:** On very large or dense maps, some nodes may end up inside solid geometry. Use the cleanup function or the 3D view if 2D view gives problems identifying these nodes and delete or move them manually.

**Quantized generation misses a doorway:** Quantized placement is experimental and may fail to generate doorway or entrance nodes on interior maps. Place or link the missing nodes manually, or switch to a normal circle seed with the Z filter enabled.

**Area-zone generation misses a room or floor:** Tile boundaries can cause the automatic Z filter to miss rooms or floor layers in an area-zone job. Use a normal circle seed with the Z filter enabled for buildings with navigable interiors.

**Stair connection varies by seed:** Some stair-to-upper-floor connections can fail for particular seed positions. The cause is still a hypothesis involving the seed's local Z-layer composition. Delete the result and re-seed the area.

**Reference model is not visible:** The optional standing-human reference requires `Delta01.3di` to be available in the configured DFBHD installation or active PFF stack. It is not included with the editor.
