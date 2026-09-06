"""CModel geometry helpers: rotation, husk/LOD, foliage hull, segment utilities.

Pure or near-pure functions for CModel rendering.  No Tk dependency.
Cache dicts live here; the monolith calls clear_rotation_caches() to invalidate.
"""

import math

# ── Module-level caches ──────────────────────────────────────────────────────
_rotated_segment_cache = {}
_rotated_bounds_cache = {}
_segment_lod_cache = {}


def clear_rotation_caches():
    """Invalidate all rotation/bounds/LOD caches.  Call after map change."""
    _rotated_segment_cache.clear()
    _rotated_bounds_cache.clear()
    _segment_lod_cache.clear()


# ── Segment point extraction ─────────────────────────────────────────────────

def _xy_from_point(p):
    """Best-effort extraction of numeric XY from segment endpoint formats."""
    try:
        if isinstance(p, (list, tuple)):
            nums = []
            for v in p:
                if isinstance(v, (int, float)):
                    nums.append(float(v))
                elif isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float)):
                    nums.append(float(v[0]))
                if len(nums) >= 2:
                    return nums[0], nums[1]
        if isinstance(p, dict):
            if 'x' in p and 'y' in p:
                return float(p['x']), float(p['y'])
            vals = [float(v) for v in p.values() if isinstance(v, (int, float))]
            if len(vals) >= 2:
                return vals[0], vals[1]
    except Exception:
        return None
    return None


def _segment_xy_pair(seg):
    """Return ((x1,y1),(x2,y2)) or None from strange segment shapes."""
    try:
        if not isinstance(seg, (list, tuple)) or len(seg) < 2:
            return None
        a = _xy_from_point(seg[0])
        b = _xy_from_point(seg[1])
        if a is None or b is None:
            return None
        return a, b
    except Exception:
        return None


def _graphic_name_from_item(item):
    if not item:
        return ""
    try:
        return str((item or {}).get('graphic') or '').strip()
    except Exception:
        return ""


# ── Primitive geometry builders ──────────────────────────────────────────────

def _make_oct_hull(cx, cy, r):
    """Small octagonal hull plus cross marker."""
    pts = []
    for i in range(8):
        ang = math.tau * i / 8.0
        pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r))
    out = []
    for i in range(8):
        out.append((pts[i], pts[(i + 1) % 8]))
    out.append(((cx - r, cy), (cx + r, cy)))
    out.append(((cx, cy - r), (cx, cy + r)))
    return out


def _segment_bbox_segments(segs):
    """Very last-resort local bbox wire."""
    if not segs:
        return []
    mnx = mny = float('inf')
    mxx = mxy = float('-inf')
    for (a, b) in segs:
        for x, y in (a, b):
            if x < mnx: mnx = x
            if x > mxx: mxx = x
            if y < mny: mny = y
            if y > mxy: mxy = y
    if mnx == float('inf'):
        return []
    return [((mnx, mny), (mxx, mny)), ((mxx, mny), (mxx, mxy)),
            ((mxx, mxy), (mnx, mxy)), ((mnx, mxy), (mnx, mny))]


def _convex_hull_segments_from_points(points):
    """Cheap outer husk fallback."""
    if len(points) < 3:
        return []
    pts = sorted(set(points))
    if len(pts) < 3:
        return []

    def cross(o, a, b):
        return (a[0]-o[0]) * (b[1]-o[1]) - (a[1]-o[1]) * (b[0]-o[0])

    lower = []
    for pt in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], pt) <= 0:
            lower.pop()
        lower.append(pt)
    upper = []
    for pt in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], pt) <= 0:
            upper.pop()
        upper.append(pt)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return _segment_bbox_segments([((x, y), (x, y)) for x, y in pts])
    return [(hull[i], hull[(i + 1) % len(hull)]) for i in range(len(hull))]


def _foliage_bounds_from_segments(segs):
    pts = []
    for seg in segs or []:
        pair = _segment_xy_pair(seg)
        if pair is None:
            continue
        pts.extend(pair)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# ── Foliage hull ─────────────────────────────────────────────────────────────

def _foliage_hull_segments(segs, max_clusters=8, dbg=None):
    """Cheap, cluster-aware tree collision view."""
    if not segs:
        return []
    try:
        pts = []
        clean_pairs = []
        for seg in segs:
            pair = _segment_xy_pair(seg)
            if pair is None:
                continue
            a, b = pair
            clean_pairs.append((a, b))
            pts.append(a)
            pts.append(b)

        if not pts:
            return []

        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        w = max(0.01, mxx - mnx)
        h = max(0.01, mxy - mny)

        if max(w, h) < 7.0 or len(pts) < 48:
            cx = (mnx + mxx) * 0.5
            cy = (mny + mxy) * 0.5
            r = max(0.45, min(2.0, min(w, h) * 0.18))
            return _make_oct_hull(cx, cy, r)

        cell = max(5.0, min(9.0, max(w, h) / 5.0))
        buckets = {}
        for x, y in pts:
            ix = int(math.floor((x - mnx) / cell))
            iy = int(math.floor((y - mny) / cell))
            buckets.setdefault((ix, iy), []).append((x, y))

        clusters = []
        for _key, bpts in buckets.items():
            if len(bpts) < 5:
                continue
            bx = [p[0] for p in bpts]
            by = [p[1] for p in bpts]
            cx = sum(bx) / len(bx)
            cy = sum(by) / len(by)
            bw = max(bx) - min(bx)
            bh = max(by) - min(by)
            r = max(0.55, min(2.2, max(0.75, min(cell * 0.22, (max(bw, bh) + 0.5) * 0.16))))
            clusters.append((len(bpts), cx, cy, r))

        if not clusters:
            cx = (mnx + mxx) * 0.5
            cy = (mny + mxy) * 0.5
            return _make_oct_hull(cx, cy, max(0.6, min(2.2, min(w, h) * 0.12)))

        clusters.sort(reverse=True, key=lambda t: t[0])
        clusters = clusters[:max(1, max_clusters)]

        out = []
        for _count, cx, cy, r in clusters:
            out.extend(_make_oct_hull(cx, cy, r))

        if max(w, h) > 18.0:
            inset = min(w, h) * 0.10
            ax0, ax1 = mnx + inset, mxx - inset
            ay0, ay1 = mny + inset, mxy - inset
            if ax1 > ax0 and ay1 > ay0:
                out.extend([
                    ((ax0, ay0), (ax1, ay0)),
                    ((ax1, ay0), (ax1, ay1)),
                    ((ax1, ay1), (ax0, ay1)),
                    ((ax0, ay1), (ax0, ay0)),
                ])

        return out
    except Exception as e:
        if dbg:
            dbg('FOLIAGE_HULL', f'[foliage hull] failed: {e}', once_key=('foliage_hull_failed', type(e).__name__))
        return []


# ── Husk / LOD ───────────────────────────────────────────────────────────────

def _cmodel_husk_segments(segs, lod_cache=None, husk_quant=0.02, max_boundary=2200):
    """Build a far-zoom visual husk from CModel triangle edges."""
    if not segs:
        return segs
    cache = lod_cache if lod_cache is not None else _segment_lod_cache
    key = (id(segs), 'husk')
    cached = cache.get(key)
    if cached is not None:
        return cached

    q = float(husk_quant or 0.02)

    def qp(pt):
        return (int(round(pt[0] / q)), int(round(pt[1] / q)))

    counts = {}
    actual = {}
    points = []
    for a, b in segs:
        qa, qb = qp(a), qp(b)
        if qa == qb:
            continue
        if qa <= qb:
            k = (qa, qb)
            actual.setdefault(k, (a, b))
        else:
            k = (qb, qa)
            actual.setdefault(k, (b, a))
        counts[k] = counts.get(k, 0) + 1
        points.append((round(a[0], 3), round(a[1], 3)))
        points.append((round(b[0], 3), round(b[1], 3)))

    boundary = [actual[k] for k, c in counts.items() if c == 1]

    if boundary and len(boundary) <= max_boundary:
        out = boundary
    else:
        out = _convex_hull_segments_from_points(points) or _segment_bbox_segments(segs)

    if len(cache) > 1024:
        cache.clear()
    cache[key] = out
    return out


# ── Rotation caching ─────────────────────────────────────────────────────────

def _cmodel_rotation_cache_key(type_id, geom_kind='full', game_path=None):
    """Stable rotation-cache key for CModel-derived geometry."""
    try:
        tid = int(type_id)
    except Exception:
        tid = -1
    gp = str(game_path).lower() if game_path else ''
    return ('cmodel', gp, tid, str(geom_kind or 'full'))


def _rotated_segments_for_heading(segs, heading_deg, cache_key=None, cache=None):
    """Cache local-space segment rotation per type+heading."""
    if not segs:
        return segs
    _cache = cache if cache is not None else _rotated_segment_cache
    h = int(round(float(heading_deg or 0))) % 360
    key = (cache_key, h) if cache_key is not None else (id(segs), h)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    a = math.radians(-h)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for (ox1, oz1), (ox2, oz2) in segs:
        rx1 = ca * ox1 - sa * oz1
        ry1 = sa * ox1 + ca * oz1
        rx2 = ca * ox2 - sa * oz2
        ry2 = sa * ox2 + ca * oz2
        out.append(((rx1, ry1), (rx2, ry2)))
    if len(_cache) > 2048:
        _cache.clear()
    _cache[key] = out
    return out


def _rotated_bounds_for_heading(segs, heading_deg, cache_key=None, cache=None):
    """Return rotated local bbox for a segment list and heading."""
    if not segs:
        return (0.0, 0.0, 0.0, 0.0)
    _cache = cache if cache is not None else _rotated_bounds_cache
    h = int(round(float(heading_deg or 0))) % 360
    key = (cache_key, h) if cache_key is not None else (id(segs), h)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    a = math.radians(-h)
    ca, sa = math.cos(a), math.sin(a)
    mnx = mny = float('inf')
    mxx = mxy = float('-inf')
    for (ox1, oy1), (ox2, oy2) in segs:
        for ox, oy in ((ox1, oy1), (ox2, oy2)):
            rx = ca * ox - sa * oy
            ry = sa * ox + ca * oy
            if rx < mnx: mnx = rx
            if rx > mxx: mxx = rx
            if ry < mny: mny = ry
            if ry > mxy: mxy = ry
    if mnx == float('inf'):
        out = (0.0, 0.0, 0.0, 0.0)
    else:
        out = (mnx, mny, mxx, mxy)
    if len(_cache) > 2048:
        _cache.clear()
    _cache[key] = out
    return out
