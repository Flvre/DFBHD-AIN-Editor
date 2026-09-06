"""Tk canvas retained-mode renderers for the 2D map view.

NodeRenderer, EdgeRenderer, GridRenderer manage persistent canvas items
for nodes, edges, and the background grid. Items are diffed (add/remove/
reposition) each frame — no delete('all') rebuild.

EntityRenderer stays in the main editor due to CModel cache dependencies.
"""

import math

from config.ui_theme import (C_NODE, C_NODE_SEL, C_NODE_PREC, ZONE_COLORS,
                      C_EDGE, C_EDGE_SEL, C_GRID)


class NodeRenderer:
    """Manages persistent Tk canvas ovals for visible node dots only.

    Key contract:
    - Items are created only for nodes in the visible set.
    - Items are deleted (not hidden) when nodes leave the viewport.
    - sync() diffs old vs new visible set and creates/moves/recolors minimally.
    - No delete('all') ever. No edge or label management here.
    """

    # Tag used for all node ovals owned by this renderer
    TAG = 'p1_node'

    def __init__(self, canvas):
        self._canvas      = canvas
        self._node_items  = {}   # node list index -> canvas oval item id
        self._visible     = set()  # current visible node indices
        self._style_cache = {}   # node index -> (fill, nr) last drawn style

    # ── Public API ──────────────────────────────────────────────────────────

    def clear(self):
        """Delete all persistent node items. Call after structural edits."""
        for item in self._node_items.values():
            try: self._canvas.delete(item)
            except Exception: pass
        self._node_items.clear()
        self._visible.clear()
        self._style_cache.clear()

    def sync(self, nodes, viewport, cw, ch, hit_tester,
             selected_nodes, show_zones, show_nodes, is_settle=False):
        """Main entry point. Diff visible set, create/update/remove items.

        is_settle=True: called after pan/zoom stops. move()/scale() already
        repositioned existing items correctly — skip to_keep coords() loop.
        Only add new items, remove stale ones, and recolor if style changed.

        is_settle=False: called on data change. Full coords() update needed.
        """
        if not show_nodes or not nodes:
            self.clear()
            return

        zoom    = viewport.zoom
        nr_base = max(2, min(6, zoom * 0.3))
        margin  = max(2.0, 20.0 / zoom)

        x0, y0, x1, y1 = viewport.visible_rect(cw, ch, margin_world=margin)
        new_visible = set(hit_tester.query_rect(x0, y0, x1, y1))

        to_remove = self._visible - new_visible
        to_add    = new_visible - self._visible
        to_keep   = self._visible & new_visible

        # Remove items that left the viewport
        for idx in to_remove:
            item = self._node_items.pop(idx, None)
            if item:
                try: self._canvas.delete(item)
                except Exception: pass
            self._style_cache.pop(idx, None)

        # Add items for newly visible nodes — always needs fresh canvas coords
        for idx in to_add:
            if idx >= len(nodes): continue
            node  = nodes[idx]
            cx, cy = viewport.world_to_canvas(node.x, node.y, cw, ch)
            style  = self._compute_style(idx, nodes[idx], selected_nodes,
                                         show_zones, zoom, nr_base)
            fill, nr = style
            item = self._canvas.create_oval(
                cx - nr, cy - nr, cx + nr, cy + nr,
                fill=fill, outline='', tags=(self.TAG,))
            self._node_items[idx]  = item
            self._style_cache[idx] = style

        if is_settle:
            # Settle path: move()/scale() already repositioned existing items.
            # Only recolor items whose style changed — no coords() calls.
            for idx in to_keep:
                item = self._node_items.get(idx)
                if not item or idx >= len(nodes): continue
                style = self._compute_style(idx, nodes[idx], selected_nodes,
                                            show_zones, zoom, nr_base)
                if self._style_cache.get(idx) != style:
                    fill, _ = style
                    self._canvas.itemconfigure(item, fill=fill)
                    self._style_cache[idx] = style
        else:
            # Full sync path: update position AND style for all kept items
            for idx in to_keep:
                if idx >= len(nodes): continue
                node  = nodes[idx]
                item  = self._node_items.get(idx)
                if not item: continue
                cx, cy = viewport.world_to_canvas(node.x, node.y, cw, ch)
                style  = self._compute_style(idx, nodes[idx], selected_nodes,
                                             show_zones, zoom, nr_base)
                fill, nr = style
                self._canvas.coords(item, cx - nr, cy - nr, cx + nr, cy + nr)
                if self._style_cache.get(idx) != style:
                    self._canvas.itemconfigure(item, fill=fill)
                    self._style_cache[idx] = style

        self._visible = new_visible

    def recolor_selection(self, nodes, old_sel, new_sel, show_zones, zoom):
        """Fast path: recolor only the nodes whose selection state changed.
        Call instead of full sync when only selection changes.
        """
        nr_base = max(2, min(6, zoom * 0.3))
        changed = old_sel.symmetric_difference(new_sel)
        for idx in changed:
            item = self._node_items.get(idx)
            if item is None: continue
            if idx >= len(nodes): continue
            style = self._compute_style(idx, nodes[idx], new_sel,
                                        show_zones, zoom, nr_base)
            fill, nr = style
            self._canvas.itemconfigure(item, fill=fill)
            self._style_cache[idx] = style

    # ── Internal ────────────────────────────────────────────────────────────

    def _compute_style(self, idx, node, selected_nodes, show_zones, zoom, nr_base):
        """Return (fill_color, radius_px) for a node."""
        if idx in selected_nodes:
            fill = C_NODE_SEL
            nr   = max(4, nr_base)
        elif show_zones:
            fill = ZONE_COLORS[node.b14 % len(ZONE_COLORS)]
            nr   = nr_base
        elif node.b12 & 0x01:
            fill = C_NODE_PREC
            nr   = nr_base
        else:
            fill = C_NODE
            nr   = nr_base
        return (fill, nr)


class EdgeRenderer:
    """Persistent visible-edge layer. Diffs visible edge set each sync.
    No delete/rebuild in normal path — only coords() and itemconfigure().
    Edges render below nodes (tag_lower called after node sync).
    """

    TAG = 'p2_edge'

    def __init__(self, canvas):
        self._canvas       = canvas
        self._edge_items   = {}   # (min_a, max_b) -> canvas line item id
        self._visible      = set()  # set of normalized edge keys currently drawn
        self._style_cache  = {}   # key -> (fill, width)

    # ── Public API ──────────────────────────────────────────────────────────

    def clear(self):
        """Delete all persistent edge items. Call after structural edits."""
        for item in self._edge_items.values():
            try: self._canvas.delete(item)
            except Exception: pass
        self._edge_items.clear()
        self._visible.clear()
        self._style_cache.clear()

    def sync(self, nodes, visible_nodes, viewport, cw, ch,
             selected_nodes, enabled, is_settle=False):
        """Main entry point. Diff visible edges, create/update/remove items.

        visible_nodes: set of node indices currently visible (from NodeRenderer).
        enabled: bool — if False, clear all edge items and return.
        is_settle=True: skip to_keep coords() loop — move()/scale() already
                        repositioned existing edge lines correctly.
        """
        if not enabled or not nodes:
            self.clear()
            return

        new_visible = self._compute_visible_edges(nodes, visible_nodes)

        to_remove = self._visible - new_visible
        to_add    = new_visible - self._visible
        to_keep   = self._visible & new_visible

        # Remove edges that left the viewport or are zoom-suppressed
        for key in to_remove:
            item = self._edge_items.pop(key, None)
            if item:
                try: self._canvas.delete(item)
                except Exception: pass
            self._style_cache.pop(key, None)

        # Add newly visible edges — always needs fresh canvas coords
        for key in to_add:
            a, b = key
            if a >= len(nodes) or b >= len(nodes): continue
            na, nb = nodes[a], nodes[b]
            ax, ay = viewport.world_to_canvas(na.x, na.y, cw, ch)
            bx, by = viewport.world_to_canvas(nb.x, nb.y, cw, ch)
            style  = self._style_for_edge(key, selected_nodes)
            fill, width = style
            item = self._canvas.create_line(ax, ay, bx, by,
                                             fill=fill, width=width,
                                             tags=(self.TAG,))
            self._edge_items[key]  = item
            self._style_cache[key] = style

        if is_settle:
            # Settle path: move()/scale() already repositioned existing lines.
            # Only recolor items whose style changed — zero coords() calls.
            for key in to_keep:
                item = self._edge_items.get(key)
                if not item: continue
                style = self._style_for_edge(key, selected_nodes)
                if self._style_cache.get(key) != style:
                    fill, width = style
                    self._canvas.itemconfigure(item, fill=fill, width=width)
                    self._style_cache[key] = style
        else:
            # Full sync path: update position AND style for all kept items
            for key in to_keep:
                a, b = key
                if a >= len(nodes) or b >= len(nodes): continue
                item = self._edge_items.get(key)
                if not item: continue
                na, nb = nodes[a], nodes[b]
                ax, ay = viewport.world_to_canvas(na.x, na.y, cw, ch)
                bx, by = viewport.world_to_canvas(nb.x, nb.y, cw, ch)
                self._canvas.coords(item, ax, ay, bx, by)
                style = self._style_for_edge(key, selected_nodes)
                if self._style_cache.get(key) != style:
                    fill, width = style
                    self._canvas.itemconfigure(item, fill=fill, width=width)
                    self._style_cache[key] = style

        self._visible = new_visible

        # Ensure edges render below nodes
        try:
            self._canvas.tag_lower(self.TAG, NodeRenderer.TAG)
        except Exception:
            pass

    def recolor_selection(self, nodes, old_sel, new_sel):
        """Fast path: recolor only edges whose selection state changed.
        Call when selection changes without viewport movement.
        """
        # Edges affected: any visible edge where at least one endpoint
        # is in the symmetric difference of old/new selection
        changed_nodes = old_sel.symmetric_difference(new_sel)
        if not changed_nodes:
            return
        for key in list(self._visible):
            a, b = key
            if a not in changed_nodes and b not in changed_nodes:
                continue
            item = self._edge_items.get(key)
            if not item: continue
            style = self._style_for_edge(key, new_sel)
            if self._style_cache.get(key) != style:
                fill, width = style
                self._canvas.itemconfigure(item, fill=fill, width=width)
                self._style_cache[key] = style

    # ── Internal ────────────────────────────────────────────────────────────

    def _compute_visible_edges(self, nodes, visible_nodes):
        """Return set of normalized edge keys where both endpoints are visible."""
        result = set()
        for a in visible_nodes:
            if a >= len(nodes): continue
            for b in nodes[a].neighbors:
                if b <= a: continue          # normalized: only store a < b
                if b >= len(nodes): continue
                if b in visible_nodes:
                    result.add((a, b))
        return result

    def _style_for_edge(self, key, selected_nodes):
        """Return (fill, width) for an edge."""
        a, b = key
        if a in selected_nodes or b in selected_nodes:
            return (C_EDGE_SEL, 1.5)
        return (C_EDGE, 1.0)


class GridRenderer:
    """Persistent grid layer. Rebuilds only when zoom bucket or viewport
    tile changes enough to require new lines. During pan, items move via
    canvas.move() — no delete/recreate in the normal path.
    """

    TAG       = 'p3_grid'
    TAG_CROSS = 'p3_cross'

    # Zoom buckets matching _draw_grid spacing logic
    @staticmethod
    def _spacing(zoom):
        if zoom >= 20: return 5.0
        if zoom >= 5:  return 10.0
        if zoom >= 1:  return 50.0
        return 200.0

    def __init__(self, canvas):
        self._canvas    = canvas
        self._built     = False
        # State when items were last built
        self._last_spacing = None
        self._last_cw      = None
        self._last_ch      = None
        # World rect that was covered when items were last built (with margin)
        self._last_wx0 = None
        self._last_wx1 = None
        self._last_wy0 = None
        self._last_wy1 = None

    def clear(self):
        self._canvas.delete(self.TAG)
        self._canvas.delete(self.TAG_CROSS)
        self._built = False
        self._last_spacing = None

    def sync(self, viewport, cw, ch, enabled):
        """Sync grid layer. Rebuild only when necessary, move otherwise."""
        if not enabled:
            self.clear()
            return

        spacing = self._spacing(viewport.zoom)
        wx0, wy0, wx1, wy1 = viewport.visible_rect(cw, ch)

        # Need full rebuild if:
        # 1. Not built yet
        # 2. Canvas size changed
        # 3. Zoom bucket changed (spacing changed)
        # 4. Viewport moved far enough that lines at edges need adding/removing
        #    (when world rect extends past the cached rect by more than one spacing)
        need_rebuild = (
            not self._built
            or spacing != self._last_spacing
            or cw != self._last_cw
            or ch != self._last_ch
            or wx0 < self._last_wx0 + spacing
            or wx1 > self._last_wx1 - spacing
            or wy0 < self._last_wy0 + spacing
            or wy1 > self._last_wy1 - spacing
        )

        if need_rebuild:
            self._rebuild(viewport, cw, ch, spacing, wx0, wy0, wx1, wy1)
        else:
            # Just reposition existing items via coords update
            # Faster than move('all') since we only touch grid items
            self._reposition(viewport, cw, ch, spacing)

    def _rebuild(self, viewport, cw, ch, spacing, wx0, wy0, wx1, wy1):
        """Full rebuild of grid items. Build with extra margin so items
        don't need immediate rebuild on small pans."""
        c = self._canvas
        c.delete(self.TAG)
        c.delete(self.TAG_CROSS)

        # Add 2 extra spacings of margin on each side
        margin = spacing * 2
        bx0 = math.floor((wx0 - margin) / spacing) * spacing
        bx1 = math.ceil( (wx1 + margin) / spacing) * spacing
        by0 = math.floor((wy0 - margin) / spacing) * spacing
        by1 = math.ceil( (wy1 + margin) / spacing) * spacing

        # Vertical lines
        x = bx0
        while x <= bx1 + 0.001:
            cx, _ = viewport.world_to_canvas(x, 0, cw, ch)
            c.create_line(cx, 0, cx, ch, fill=C_GRID, width=1,
                          tags=(self.TAG,))
            x += spacing

        # Horizontal lines
        y = by0
        while y <= by1 + 0.001:
            _, cy = viewport.world_to_canvas(0, y, cw, ch)
            c.create_line(0, cy, cw, cy, fill=C_GRID, width=1,
                          tags=(self.TAG,))
            y += spacing

        # Origin cross (always)
        ox, oy = viewport.world_to_canvas(0, 0, cw, ch)
        c.create_line(ox-10, oy, ox+10, oy, fill='#666666', width=1,
                      tags=(self.TAG_CROSS,))
        c.create_line(ox, oy-10, ox, oy+10, fill='#666666', width=1,
                      tags=(self.TAG_CROSS,))

        # Ensure grid renders below everything else
        try:
            c.tag_lower(self.TAG)
            c.tag_lower(self.TAG_CROSS)
        except Exception:
            pass

        self._built       = True
        self._last_spacing = spacing
        self._last_cw      = cw
        self._last_ch      = ch
        self._last_wx0     = bx0
        self._last_wx1     = bx1
        self._last_wy0     = by0
        self._last_wy1     = by1

    def _reposition(self, viewport, cw, ch, spacing):
        """Reposition all grid lines using coords() for visible lines.
        Called when viewport moved within cached bounds — no item creation.
        Only redraws lines that are visible; clips off-screen lines.
        """
        c = self._canvas
        items = c.find_withtag(self.TAG)
        if not items:
            # Items got wiped somehow — fall back to rebuild
            wx0, wy0, wx1, wy1 = viewport.visible_rect(cw, ch)
            self._rebuild(viewport, cw, ch, spacing, wx0, wy0, wx1, wy1)
            return

        margin = spacing * 2
        bx0 = self._last_wx0; bx1 = self._last_wx1
        by0 = self._last_wy0; by1 = self._last_wy1

        new_coords = []
        x = bx0
        while x <= bx1 + 0.001:
            cx, _ = viewport.world_to_canvas(x, 0, cw, ch)
            new_coords.append((cx, 0, cx, ch))
            x += spacing
        y = by0
        while y <= by1 + 0.001:
            _, cy = viewport.world_to_canvas(0, y, cw, ch)
            new_coords.append((0, cy, cw, cy))
            y += spacing

        for i, item in enumerate(items):
            if i < len(new_coords):
                c.coords(item, *new_coords[i])

        # Origin cross
        ox, oy = viewport.world_to_canvas(0, 0, cw, ch)
        cross = c.find_withtag(self.TAG_CROSS)
        if len(cross) >= 2:
            c.coords(cross[0], ox-10, oy, ox+10, oy)
            c.coords(cross[1], ox, oy-10, ox, oy+10)
