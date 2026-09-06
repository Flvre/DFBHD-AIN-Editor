"""Viewport, HitTester, and world-coordinate helpers for DFBHD AIN Editor.

Pure coordinate math — no Tk, no caches, no mutable module state.
"""
import math

from shared.spatial_index import SpatialHash

_WORLD_WRAP_HALF = 8388608.0
_WORLD_WRAP_FULL = _WORLD_WRAP_HALF * 2.0

def _wrap_world_delta(delta):
    """Wrap a world-space delta into the signed 23-bit DFBHD range."""
    try:
        d = float(delta)
    except Exception:
        return delta
    if not math.isfinite(d):
        return d
    return ((d + _WORLD_WRAP_HALF) % _WORLD_WRAP_FULL) - _WORLD_WRAP_HALF

def _world_delta(world_value, origin_value):
    return _wrap_world_delta(float(world_value) - float(origin_value))

def _world_to_screen_x(world_x, offset_x, zoom, half_w):
    return half_w + _world_delta(world_x, offset_x) * float(zoom)

def _world_to_screen_y(world_y, offset_y, zoom, half_h):
    return half_h - _world_delta(world_y, offset_y) * float(zoom)

class Viewport:
    """Pure coordinate math + visible world rectangle. No Tk, no node knowledge."""

    def __init__(self, offset_x=0.0, offset_y=0.0, zoom=8.0):
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.zoom     = zoom

    def world_to_canvas(self, wx, wy, cw, ch):
        cx = _world_to_screen_x(wx, self.offset_x, self.zoom, cw / 2.0)
        cy = _world_to_screen_y(wy, self.offset_y, self.zoom, ch / 2.0)
        return cx, cy

    def canvas_to_world(self, cx, cy, cw, ch):
        wx = (cx - cw / 2) / self.zoom + self.offset_x
        wy = -(cy - ch / 2) / self.zoom + self.offset_y
        return wx, wy

    def visible_rect(self, cw, ch, margin_world=0.0):
        """Return (x0, y0, x1, y1) world bounding rect of the current view."""
        x0, y0 = self.canvas_to_world(0,  ch, cw, ch)
        x1, y1 = self.canvas_to_world(cw, 0,  cw, ch)
        return (x0 - margin_world, y0 - margin_world,
                x1 + margin_world, y1 + margin_world)


class HitTester:
    """Spatial index for fast node lookup. Wraps SpatialHash.
    Payload stored per entry: node.id (int).
    All query methods return node IDs, not list indices.
    """

    def __init__(self, cell_size=8.0):
        self._cell = cell_size
        self._hash = SpatialHash(cell_size)

    def rebuild(self, nodes):
        """Full rebuild from node list. Stores node.id as payload."""
        self._hash = SpatialHash(self._cell)
        for node in nodes:
            self._hash.insert(node.x, node.y, node.id)

    def query_rect(self, x0, y0, x1, y1):
        """Return list of node IDs whose positions fall within the world rect."""
        if x0 > x1: x0, x1 = x1, x0
        if y0 > y1: y0, y1 = y1, y0
        c = self._hash.cell
        cx0 = int(math.floor(x0 / c))
        cx1 = int(math.floor(x1 / c))
        cy0 = int(math.floor(y0 / c))
        cy1 = int(math.floor(y1 / c))
        out = []
        grid = self._hash._grid
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                bucket = grid.get((cx, cy))
                if bucket:
                    for px, py, nid in bucket:
                        if x0 <= px <= x1 and y0 <= py <= y1:
                            out.append(nid)
        return out

    def nearest(self, wx, wy, radius_world, nodes):
        """Return node ID of nearest node within radius_world, or None."""
        best_d2 = radius_world * radius_world + 1
        best    = None
        for px, py, nid in self._hash.query_radius(wx, wy, radius_world):
            d2 = (px - wx) ** 2 + (py - wy) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best    = nid
        return best

    def nearest_canvas(self, cx, cy, radius_px, viewport, cw, ch, nodes):
        """Convert canvas coords to world, query nearest node, return node ID."""
        wx, wy = viewport.canvas_to_world(cx, cy, cw, ch)
        radius_world = radius_px / viewport.zoom
        return self.nearest(wx, wy, radius_world, nodes)
