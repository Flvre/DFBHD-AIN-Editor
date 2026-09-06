"""Shared SpatialHash for fast radius queries."""

import math


class SpatialHash:
    """Fixed-cell spatial hash for fast radius queries.

    cell_size should be >= the largest query radius you'll use,
    so a query never needs to check more than 3x3 = 9 cells.
    """
    __slots__ = ('cell', '_grid')

    def __init__(self, cell_size):
        self.cell = float(cell_size)
        self._grid = {}          # (cx, cy) -> list of (x, y, payload)

    def _key(self, x, y):
        c = self.cell
        return (int(math.floor(x / c)), int(math.floor(y / c)))

    def insert(self, x, y, payload=None):
        k = self._key(x, y)
        try:
            self._grid[k].append((x, y, payload))
        except KeyError:
            self._grid[k] = [(x, y, payload)]

    def query_radius(self, x, y, r):
        """Return list of (px, py, payload) within radius r of (x, y)."""
        c = self.cell
        r2 = r * r
        cx0 = int(math.floor((x - r) / c))
        cx1 = int(math.floor((x + r) / c))
        cy0 = int(math.floor((y - r) / c))
        cy1 = int(math.floor((y + r) / c))
        out = []
        grid = self._grid
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                bucket = grid.get((cx, cy))
                if bucket:
                    for px, py, pay in bucket:
                        if (px-x)*(px-x) + (py-y)*(py-y) <= r2:
                            out.append((px, py, pay))
        return out

    def nearest(self, x, y, max_r):
        """Return (px, py, payload) of nearest point within max_r, or None."""
        best_d2 = max_r * max_r + 1
        best = None
        for px, py, pay in self.query_radius(x, y, max_r):
            d2 = (px-x)*(px-x) + (py-y)*(py-y)
            if d2 < best_d2:
                best_d2 = d2
                best = (px, py, pay)
        return best

    def __len__(self):
        return sum(len(v) for v in self._grid.values())
