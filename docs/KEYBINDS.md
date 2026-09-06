# Keyboard and mouse controls

Reference for the editor controls in the [illustrated guide](AIN_Editor_Guide.pdf). Standard text editing and dialog conventions are omitted. Distances are world-space distances: 0.1 m is 10 cm; 0.01 m is 1 cm.

## 2D keyboard

| Key | Effect |
|---|---|
| E | Switch to Edit mode |
| D | Switch to Draw mode |
| C | Switch to Color/paint mode |
| Z | Switch to Area Zones |
| R | Toggle radius brush in Draw mode |
| Ctrl+R | Rebuild neighbors for selected nodes; does nothing without a selection |
| U | Clear node selection |
| F | Fit all nodes in view |
| G | Toggle grid |
| H | Toggle help panel |
| F1 | Terrain display: Color |
| F2 | Terrain display: Height |
| F3 | Terrain display: Depth |
| F8 | Open Zone Layer Manager |
| F9 | Toggle debug bar in Developer mode |
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| Ctrl+C | Copy selected nodes |
| Ctrl+V | Paste nodes |
| Delete | Delete selected nodes, or the selected area zone in Area Zone mode |
| Escape | Cancel current editor action |
| Left / Right / Up / Down | Move the active selected node in that direction by 1 m in Edit mode |
| Shift+Left / Right / Up / Down | Move the active selected node by 10 cm in Edit mode |
| Ctrl+Left / Right / Up / Down | Alternative 10 cm node movement in Edit mode |
| Numpad 9 / 3 | Zoom in / out |
| Numpad 8 / 2 / 4 / 6 | Pan up / down / left / right |
| Numpad + / − | Increase / decrease grid size |
| + / − | Alternative grid-size controls; keyboard + is Shift+= |
| A | Apply changes while a Node Inspector numeric field has focus |

## 2D mouse

| Mode / modifier | Input | Effect |
|---|---|---|
| Edit | Left-click node | Select/deselect a node |
| Edit | Left-drag node | Move a node or selected group |
| Edit | Left-drag empty canvas | Pan |
| Any | Middle-drag | Pan |
| Alt | Left-drag canvas | Pan |
| Any | Mouse wheel | Zoom around the pointer |
| Shift | Left-drag | Box-select additional nodes |
| Ctrl | Left-drag | Remove nodes from selection using a box |
| Edit | Right-click node | Open node context menu |
| Edit | Right-click empty canvas | Place a node |
| Ctrl | Right-drag | Connect touched nodes with a brush stroke |
| Shift | Right-drag | Cut connections touched by the stroke |
| Draw | Left-drag | Place nodes |
| Draw | Right-click | Open draw-spacing / radius-brush options |
| Color | Left-click/drag | Paint zone IDs onto nodes |
| Area Zones | Left-click/drag | Select/add tiles |
| Area Zones | Right-click/drag | Erase tiles |
| Generator seed placement | Left-click | Submit the seed position and generate nodes |
| Generator seed placement | Right-click | Cancel seed placement |

## 3D keyboard

| Key | Effect |
|---|---|
| T | Top view |
| S | Side view |
| F | Fit view |
| G | Toggle grid |
| U | Clear node selection |
| N | Toggle node overlay |
| C | Toggle buildings, vehicles, and decorations together |
| B | Toggle buildings |
| V | Toggle vehicles |
| D | Toggle decorations |
| R | Refresh the 3D view |
| L | Toggle floor Z-level coordinates and leader lines |
| Y | Toggle node IDs |
| [ / ] | Previous / next active node in the scene |
| + | Zoom in |
| = | Alternative zoom in |
| − | Zoom out |
| Numpad 9 / 3 | Zoom in / out |
| Numpad 4 / 6 | Move the active selected node left / right by 1 m |
| Numpad 8 / 2 | Move the active selected node forward / backward by 1 m |
| Shift+Numpad 4 / 6 | Move the active selected node left / right by 1 cm |
| Shift+Numpad 8 / 2 | Move the active selected node forward / backward by 1 cm |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Escape | Cancel the active connection; otherwise exit 3D view |

Node movement uses world X/Y axes and leaves Z unchanged; camera rotation does not rotate these movement directions. Node IDs are drawn only when sufficiently zoomed in.

## 3D mouse

| Modifier / context | Input | Effect |
|---|---|---|
| Any | Left-drag | Pan |
| Any | Right-drag | Orbit |
| Ctrl | Right-drag | Alternative orbit |
| Any | Mouse wheel | Zoom |
| Shift, locked Node Set | Left-drag | Select additional nodes using a box |
| Ctrl, locked Node Set | Left-drag | Remove nodes using a box |
| Any | Left-click node | Select a node |
| Any | Left-click empty space | Clear live selection |
| Locked Node Set | Right-click selected node | Open node context menu |
| Connection in progress | Left-click target node | Complete the connection |

The Node Set lock preserves the displayed working set while its live node selection changes.
