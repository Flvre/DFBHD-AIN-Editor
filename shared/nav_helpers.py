"""Navigation helpers: Z interpolation, ground reference, entity classification.

Pure or near-pure functions used by the generator and editor for nav node
placement.  No Tk dependency; no globals() access.
"""

from shared.spatial_index import SpatialHash
from terrain.terrain import _terrain_sample_height

# ── Constants ────────────────────────────────────────────────────────────────

NAV_NODE_Z_LIFT = 1.2489
TERRAIN_NODE_SIDE_TOLERANCE = 0.35

GENERATOR_IGNORE_DESTROYABLE = True
GENERATOR_CLEARABLE_DEATH_PREFIXES = ('EXPLO_FUEL', 'EXPLO_CRATES')

ENTITY_INVINCIBLE_FLAG_OFFSET = 0x0e
ENTITY_INVINCIBLE_FLAG_MASK   = 0x20

FORCE_COLLISION_GRAPHIC_PREFIXES = (
    "barl", "mbarrel", "wcrate", "cargo",
    "bfence", "mfence", "stnwal", "mwal", "sndbag", "sack", "baricd",
    "tire", "vndcrt", "hndcrt", "wlad", "trunk",
    "dockslb", "dock", "pblok", "pwalk",
    "ncar", "ntrk", "nbus", "f5ton", "fhum", "etek", "fjeep", "nctrk",
    "ship", "bhdf", "fblkhwkx", "gtruck",
    "rmcabnts",
)

FORCE_COLLISION_GRAPHICS = {
    "fan02", "mine_me", "heatr01", "radio", "file01",
    "chair02", "chair03", "chair04",
    "table02", "table03", "table04", "table05", "table06", "table07", "table08",
    "sofa01", "dresser", "dressr02", "bed04",
    "rmlivina", "rmlivinb", "rmbeda", "rmoffica",
    "armry03",
}

_VEHICLE_NAME_WORDS = (
    'truck','trck','jeep','fjeep','humv','hummer','bus','tank','ship','boat','rhib','apc',
    'bradley','btr','technical','heli','copter','plane','craft','buggy','bird','hawk',
    'pavelow','cobra','mh6','mh53','fah','c130','pickup','exocet','tanker','patrol','dune','vbl',
)


# ── Z interpolation ─────────────────────────────────────────────────────────

def interp_z(nx, ny, ref, dz, _cache={}):
    """Inverse-distance-weighted Z interpolation."""
    if not ref:
        return dz

    ref_id = id(ref)
    if ref_id not in _cache:
        h = SpatialHash(30.0)
        for rx, ry, rz in ref:
            h.insert(rx, ry, rz)
        _cache[ref_id] = (h, ref)
        if len(_cache) > 8:
            oldest = next(iter(_cache))
            del _cache[oldest]

    h, ref_list = _cache[ref_id]

    nearby = []
    for radius in (60.0, 200.0, 500.0):
        nearby = h.query_radius(nx, ny, radius)
        if nearby:
            break

    if not nearby:
        nearest = []
        for rx, ry, rz in ref_list:
            d2 = (nx-rx)*(nx-rx) + (ny-ry)*(ny-ry)
            nearest.append((d2, rx, ry, rz))
        nearest.sort(key=lambda t: t[0])
        nearby = [(rx, ry, rz) for _, rx, ry, rz in nearest[:12]]

    tw = 0.0
    wz = 0.0
    for rx, ry, rz in nearby:
        d2 = (nx-rx)**2 + (ny-ry)**2
        if d2 < 0.0001:
            return rz
        w = 1.0 / (d2 ** 0.75)
        tw += w
        wz += w * rz
    return wz / tw if tw > 0 else dz


def nav_node_z(nx, ny, ref, dz, lift=NAV_NODE_Z_LIFT):
    """Ground-interpolated nav height with the standard campaign-like lift."""
    return interp_z(nx, ny, ref, dz) + lift


def _gen_node_z(px, py, terrain_info, z_ref, terrain_z):
    """Get the best Z for a generated node at (px, py).
    Priority: terrain heightmap sample > entity interpolation > flat terrain_z.
    """
    if terrain_info is not None:
        try:
            th = _terrain_sample_height(terrain_info, px, py)
            if th is not None:
                return float(th) + NAV_NODE_Z_LIFT
        except Exception:
            pass
    return nav_node_z(px, py, z_ref, terrain_z)


def build_ground_z_ref(entities, terrain_z):
    """Build height references that represent ground, not arbitrary object tops."""
    if not entities:
        return []

    all_pts = [(e['x'], e['y'], e['z']) for e in entities]

    close = [p for p in all_pts if abs(p[2] - terrain_z) <= 2.5]
    if len(close) >= 4:
        return close

    wider = [p for p in all_pts if abs(p[2] - terrain_z) <= 6.0]
    if len(wider) >= 4:
        return wider

    groundish = [p for p in all_pts if 1.0 < p[2] < 500.0]
    return groundish if groundish else all_pts


# ── Clearance measurement ────────────────────────────────────────────────────

def _gen_measure_clearance(blocked_fn, x, y, dx, dy, step=0.5, max_d=14.0):
    """March in direction (dx,dy) until blocked. Return traversable distance."""
    dist = 0.0
    while dist + step <= max_d:
        nd = dist + step
        if blocked_fn(x + dx * nd, y + dy * nd):
            break
        dist = nd
    return dist


# ── Entity classification ────────────────────────────────────────────────────

def _item_is_vehicle(item):
    """Vehicle / wreck-leaving object: stays a nav obstacle even when destroyed."""
    if not item:
        return False
    if (item.get('category') or '').lower() == 'vehicle':
        return True
    if 'husk_sub_part_types' in item or 'unit_type' in item:
        return True
    nm = ((item.get('name') or '') + ' ' + (item.get('graphic') or '')).lower()
    if 'parked' in nm or 'version of' in nm:
        return True
    return any(w in nm for w in _VEHICLE_NAME_WORDS)


def _item_is_clearable_prop(item, clearable_death_prefixes=None):
    """True when destruction clears this object's navigation footprint."""
    if not item:
        return False
    if (item.get('category') or '').lower() not in ('decoration', 'object'):
        return False
    death = str(item.get('sounddeath') or '').upper()
    name = str(item.get('name') or '').lower()
    if death.startswith('EXPLO_DOOR_MTL') and 'door' in name:
        return True
    prefixes = clearable_death_prefixes if clearable_death_prefixes is not None else GENERATOR_CLEARABLE_DEATH_PREFIXES
    if not any(death.startswith(p) for p in prefixes):
        return False
    return not _item_is_vehicle(item)
