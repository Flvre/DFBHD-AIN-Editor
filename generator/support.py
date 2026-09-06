"""Generator-local data structures, geometry helpers, and static constants."""

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from format.ain_format import Node, MAX_NEIGHBORS

try:
    from PIL import Image, ImageDraw, ImageFilter
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFilter = None

Point = Tuple[float, float]
Segment = Tuple[float, float, float, float, int]
GENERATOR_CORE_CONTRACT = {
    'placement_owner': 'exact patches only',
    'final_form': 'one-file embedded core, no external runtime dependency',
    'metadata_rule': 'b14/b17 excluded from generator metadata reasoning',
    'default_b12_policy': '0/1 only unless extended b12-b16 annotation is explicitly enabled',
    'node_height_rule': 'terrain/z_at + 1.2489 for generated terrain nodes',
}


@dataclass
class LabNode:
    x: float
    y: float
    z: float
    b12: int
    b15: int
    b16: int = 0
    b17: int = 0
    b18: int = 0
    neighbors: List[int] = None
    tag: str = ""
    quantized_source_keys: object = None
    quantized_map_grid: bool = False

def _cross(o, a, b):
    return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

def _convex_hull(points: Sequence[Point]) -> List[Point]:
    pts = sorted(set((round(x,3), round(y,3)) for x,y in points))
    if len(pts) < 3: return []
    lo=[]
    for p in pts:
        while len(lo)>=2 and _cross(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    up=[]
    for p in reversed(pts):
        while len(up)>=2 and _cross(up[-2], up[-1], p) <= 0: up.pop()
        up.append(p)
    return lo[:-1]+up[:-1]

def _poly_area(poly: Sequence[Point]) -> float:
    if len(poly) < 3: return 0.0
    s=0.0
    for i,p in enumerate(poly):
        q=poly[(i+1)%len(poly)]
        s += p[0]*q[1] - q[0]*p[1]
    return abs(s)*0.5

class SolidMask:
    def __init__(self, segments: Sequence[Segment], pad=5.0, pppm=10.0, line_width_m=0.42):
        self.valid = False
        xs=[]; ys=[]
        for x1,y1,x2,y2,ei in segments:
            xs += [x1,x2]; ys += [y1,y2]
        if not xs or Image is None:
            self.minx=self.miny=0.0; self.maxx=self.maxy=0.0; self.W=self.H=1; self.pix=None; return
        self.pppm=float(pppm)
        self.minx=min(xs)-pad; self.maxx=max(xs)+pad
        self.miny=min(ys)-pad; self.maxy=max(ys)+pad
        self.W=max(64,int((self.maxx-self.minx)*self.pppm)+8)
        self.H=max(64,int((self.maxy-self.miny)*self.pppm)+8)
        line=Image.new('L',(self.W,self.H),0)
        d=ImageDraw.Draw(line)
        lw=max(2,int(round(line_width_m*self.pppm)))
        def wp(x,y): return int(round((x-self.minx)*self.pppm))+4, int(round((self.maxy-y)*self.pppm))+4
        grouped=defaultdict(list)
        for x1,y1,x2,y2,ei in segments:
            d.line((*wp(x1,y1), *wp(x2,y2)), fill=255, width=lw)
            grouped[ei].extend([(x1,y1),(x2,y2)])
        # Entity footprint fill prevents old edge-soup interiors from accepting exterior nodes.
        for ei, pts in grouped.items():
            poly = _convex_hull(pts)
            if len(poly) >= 3 and _poly_area(poly) >= 0.35:
                d.polygon([wp(x,y) for x,y in poly], fill=255)
        try: line = line.filter(ImageFilter.MaxFilter(3))
        except Exception: pass
        outside=Image.new('L',(self.W,self.H),0)
        op=outside.load(); lp=line.load(); q=deque()
        for x in range(self.W):
            for y in (0,self.H-1):
                if lp[x,y] == 0 and op[x,y] == 0:
                    op[x,y] = 255; q.append((x,y))
        for y in range(self.H):
            for x in (0,self.W-1):
                if lp[x,y] == 0 and op[x,y] == 0:
                    op[x,y] = 255; q.append((x,y))
        while q:
            x,y=q.popleft()
            for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if 0 <= nx < self.W and 0 <= ny < self.H and lp[nx,ny] == 0 and op[nx,ny] == 0:
                    op[nx,ny] = 255; q.append((nx,ny))
        blocked=Image.new('L',(self.W,self.H),0); bp=blocked.load()
        for yy in range(self.H):
            for xx in range(self.W):
                if lp[xx,yy] or op[xx,yy] == 0:
                    bp[xx,yy] = 255
        self.pix=blocked.load(); self.valid=True
    def blocked(self, x: float, y: float) -> bool:
        if not self.valid: return False
        px=int(round((x-self.minx)*self.pppm))+4
        py=int(round((self.maxy-y)*self.pppm))+4
        if px<0 or py<0 or px>=self.W or py>=self.H: return False
        return self.pix[px,py] > 0

def radius_m(b15: int) -> float: return float(b15) / 16.0

# ── V52 FAST REPLACEMENT GENERATOR HELPERS ────────────────────────────
def _v52_convex_hull(points):
    pts=sorted(set((round(x,3),round(y,3)) for x,y in points))
    if len(pts)<3: return []
    def cross(o,a,b): return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lo=[]
    for p in pts:
        while len(lo)>=2 and cross(lo[-2],lo[-1],p)<=0: lo.pop()
        lo.append(p)
    up=[]
    for p in reversed(pts):
        while len(up)>=2 and cross(up[-2],up[-1],p)<=0: up.pop()
        up.append(p)
    return lo[:-1]+up[:-1]

def _v52_poly_area(poly):
    return abs(sum(poly[i][0]*poly[(i+1)%len(poly)][1]-poly[(i+1)%len(poly)][0]*poly[i][1] for i in range(len(poly)))*0.5) if len(poly)>=3 else 0

def _v52_point_in_poly(x,y,poly):
    inside=False; n=len(poly); j=n-1
    for i in range(n):
        xi,yi=poly[i]; xj,yj=poly[j]
        if ((yi>y)!=(yj>y)) and (x < (xj-xi)*(y-yi)/(yj-yi+1e-12)+xi):
            inside=not inside
        j=i
    return inside

def _v52_dist_point_seg(px,py, ax,ay,bx,by):
    dx=bx-ax; dy=by-ay; l2=dx*dx+dy*dy
    if l2<=1e-12: return math.hypot(px-ax,py-ay)
    t=max(0,min(1,((px-ax)*dx+(py-ay)*dy)/l2))
    qx=ax+t*dx; qy=ay+t*dy
    return math.hypot(px-qx,py-qy)

class _V52Spatial:
    def __init__(self, cell=4.0):
        self.cell=float(cell); self.grid=defaultdict(list)
    def key(self,x,y): return (int(math.floor(x/self.cell)), int(math.floor(y/self.cell)))
    def insert(self,x,y,obj): self.grid[self.key(x,y)].append((x,y,obj))
    def query(self,x,y,r):
        cx0=int(math.floor((x-r)/self.cell)); cx1=int(math.floor((x+r)/self.cell))
        cy0=int(math.floor((y-r)/self.cell)); cy1=int(math.floor((y+r)/self.cell))
        r2=r*r
        for cx in range(cx0,cx1+1):
            for cy in range(cy0,cy1+1):
                for px,py,obj in self.grid.get((cx,cy),[]):
                    if (px-x)*(px-x)+(py-y)*(py-y)<=r2:
                        yield px,py,obj

# ── END V52 FAST REPLACEMENT GENERATOR HELPERS ────────────────────────


# ── V58 SEED DISC + CAMPAIGN-STYLE REPAIR GENERATOR CONSTANTS ─────────

GENERATOR_REACHABLE_MAX_STEP = 0.35
GENERATOR_REACHABLE_STAIR_STEP = 0.75

GENERATOR_CORRIDOR_FIT = True
GENERATOR_CORRIDOR_FINAL_FACTOR = 0.85
GENERATOR_CORRIDOR_SLIDE_BUDGET = 0.60
GENERATOR_CORRIDOR_CENTER_FLOOR = 0.75
GENERATOR_CORRIDOR_DOMINANCE = True
GENERATOR_QUANTIZED_GRID_M = 0.25
GENERATOR_QUANTIZED_BASE_PITCH_M = 0.50
GENERATOR_QUANTIZED_CENTER_CLEARANCE = 0.42
GENERATOR_STANDING_BODY_LO = 0.20
GENERATOR_STANDING_BODY_HI = 2.00


class _GeneratorStandingVolumeIndex:
    """Seed-generator collision index for cheap local standing-column tests.

    A vertical center column only needs triangles whose XY projection contains
    the candidate point.  Precomputing the projected barycentric coefficients
    avoids a general segment/triangle test for every reachable-surface cell.
    The same index is reused later by link legality and final node validation.
    """
    def __init__(self, triangles, cell=3.0):
        self.cell = float(cell)
        self.triangles = list(triangles or [])
        self.grid = {}
        self.projected = [None] * len(self.triangles)
        self.cache = {}
        self.query_count = 0
        self.cache_hits = 0
        self.triangle_tests = 0
        self.blocked_count = 0

        for triangle_index, triangle in enumerate(self.triangles):
            try:
                ax, ay, az, bx, by, bz, cx, cy, cz, _entity_index = triangle
                ax = float(ax); ay = float(ay); az = float(az)
                bx = float(bx); by = float(by); bz = float(bz)
                cx = float(cx); cy = float(cy); cz = float(cz)
            except Exception:
                continue
            denominator = ((by - cy) * (ax - cx)
                           + (cx - bx) * (ay - cy))
            min_x = min(ax, bx, cx); max_x = max(ax, bx, cx)
            min_y = min(ay, by, cy); max_y = max(ay, by, cy)
            min_z = min(az, bz, cz); max_z = max(az, bz, cz)
            if abs(denominator) > 1e-10:
                inv_denominator = 1.0 / denominator
                self.projected[triangle_index] = (
                    min_x, max_x, min_y, max_y, min_z, max_z,
                    (by - cy) * inv_denominator,
                    (cx - bx) * inv_denominator,
                    (-(by - cy) * cx - (cx - bx) * cy) * inv_denominator,
                    (cy - ay) * inv_denominator,
                    (ax - cx) * inv_denominator,
                    (-(cy - ay) * cx - (ax - cx) * cy) * inv_denominator,
                    az, bz, cz,
                )
            gx0 = int(math.floor(min_x / self.cell))
            gx1 = int(math.floor(max_x / self.cell))
            gy0 = int(math.floor(min_y / self.cell))
            gy1 = int(math.floor(max_y / self.cell))
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    self.grid.setdefault((gx, gy), []).append(triangle_index)

    def query_indices(self, x, y, radius=0.0):
        x = float(x); y = float(y); radius = max(0.0, float(radius))
        result = set()
        gx0 = int(math.floor((x - radius) / self.cell))
        gx1 = int(math.floor((x + radius) / self.cell))
        gy0 = int(math.floor((y - radius) / self.cell))
        gy1 = int(math.floor((y + radius) / self.cell))
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                result.update(self.grid.get((gx, gy), ()))
        return result

    def standing_clear(self, x, y, support_z,
                       body_lo=GENERATOR_STANDING_BODY_LO,
                       body_hi=GENERATOR_STANDING_BODY_HI):
        x = float(x); y = float(y); support_z = float(support_z)
        key = (round(x, 4), round(y, 4), round(support_z, 4),
               round(float(body_lo), 3), round(float(body_hi), 3))
        self.query_count += 1
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached

        z_lo = support_z + float(body_lo)
        z_hi = support_z + float(body_hi)
        gx = int(math.floor(x / self.cell))
        gy = int(math.floor(y / self.cell))
        for triangle_index in self.grid.get((gx, gy), ()):
            projected = self.projected[triangle_index]
            if projected is None:
                continue
            min_x, max_x, min_y, max_y, min_z, max_z = projected[:6]
            if (x < min_x - 1e-7 or x > max_x + 1e-7
                    or y < min_y - 1e-7 or y > max_y + 1e-7
                    or max_z < z_lo - 1e-7 or min_z > z_hi + 1e-7):
                continue
            self.triangle_tests += 1
            wa = projected[6] * x + projected[7] * y + projected[8]
            if wa < -1e-7 or wa > 1.0 + 1e-7:
                continue
            wb = projected[9] * x + projected[10] * y + projected[11]
            if wb < -1e-7 or wa + wb > 1.0 + 1e-7:
                continue
            wc = 1.0 - wa - wb
            hit_z = wa * projected[12] + wb * projected[13] + wc * projected[14]
            if z_lo - 1e-7 <= hit_z <= z_hi + 1e-7:
                self.blocked_count += 1
                self.cache[key] = False
                return False
        self.cache[key] = True
        return True

    def metrics(self):
        return {
            'triangles': len(self.triangles),
            'indexed_triangles': sum(1 for item in self.projected if item is not None),
            'grid_cells': len(self.grid),
            'queries': self.query_count,
            'cache_hits': self.cache_hits,
            'triangle_tests': self.triangle_tests,
            'blocked': self.blocked_count,
        }


# ── Generator worker helpers ────────────────────────────────────────────────

def _generator_node_record(node):
    """Return a class-free record so worker results survive __main__ boundaries."""
    if isinstance(node, dict):
        return dict(node)
    return {
        'x': float(node.x), 'y': float(node.y), 'z': float(node.z),
        'b12': int(getattr(node, 'b12', 0)),
        'b13': int(getattr(node, 'b13', 0)),
        'b14': int(getattr(node, 'b14', 0)),
        'b15': int(node.b15),
        'b16': int(node.b16), 'b17': int(node.b17), 'b18': int(node.b18),
        'neighbors': [int(value) for value in list(node.neighbors or [])],
        'tag': str(getattr(node, 'tag', '') or ''),
        'quantized_source_keys': [
            list(key) for key in getattr(node, 'quantized_source_keys', ()) or ()
        ],
        'quantized_map_grid': bool(getattr(node, 'quantized_map_grid', False)),
    }


def _generator_point_in_polygon(x, y, points):
    """Ray-cast point-in-polygon test for generator crop boundaries."""
    inside = False
    points = list(points or ())
    if len(points) < 3:
        return False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        if ((yi > y) != (yj > y)):
            x_intersection = ((xj - xi) * (y - yi)
                              / ((yj - yi) or 1.0e-9) + xi)
            if x < x_intersection:
                inside = not inside
        j = i
    return inside


def _crop_generator_nodes_for_job(nodes, job):
    """Crop and locally remap a generated graph before boundary stitching."""
    nodes = list(nodes or ())
    polygon = [tuple(point) for point in (job.get('crop_polygon') or ())]
    tiles = [tuple(tile) for tile in (job.get('crop_tiles') or ())]
    if not polygon and not tiles:
        return nodes, len(nodes), len(nodes)

    def keep_node(node):
        x, y = float(node.x), float(node.y)
        if tiles:
            for tx, ty, size in tiles:
                tx, ty, size = float(tx), float(ty), float(size)
                if tx <= x < tx + size and ty <= y < ty + size:
                    return True
            return False
        return _generator_point_in_polygon(x, y, polygon)

    kept_indices = [index for index, node in enumerate(nodes) if keep_node(node)]
    old_to_new = {old: new for new, old in enumerate(kept_indices)}
    cropped = []
    for new_index, old_index in enumerate(kept_indices):
        source = nodes[old_index]
        neighbors = []
        for neighbor in list(source.neighbors or ()):
            try:
                mapped = old_to_new.get(int(neighbor))
            except Exception:
                mapped = None
            if mapped is not None and mapped not in neighbors:
                neighbors.append(mapped)
        cropped.append(Node(
            new_index, float(source.x), float(source.y), float(source.z),
            b12=int(source.b12),
            b13=int(getattr(source, 'b13', 0)),
            b14=int(getattr(source, 'b14', 0)),
            b15=int(source.b15), b16=int(source.b16), b17=int(source.b17),
            b18=int(source.b18), neighbors=neighbors[:MAX_NEIGHBORS],
            quantized_source_keys=getattr(source, 'quantized_source_keys', None),
            quantized_map_grid=bool(getattr(source, 'quantized_map_grid', False))))
    return cropped, len(nodes), len(cropped)
