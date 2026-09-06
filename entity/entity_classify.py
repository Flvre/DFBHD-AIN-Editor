"""Entity collision classification and layer visibility filters."""
from config.render_config import CONTEXT_ONLY_GRAPHICS, CONTEXT_ONLY_GRAPHIC_PREFIXES
from shared.nav_helpers import FORCE_COLLISION_GRAPHICS, FORCE_COLLISION_GRAPHIC_PREFIXES
from cmodel.cmodel_geom import _graphic_name_from_item
from shared.debug_log import _dbg
from entity.entity_data import FOLIAGE_TYPE_IDS, FORCE_OBSTACLE
from resources.items_def import _load_items_def

_get_game_path = lambda: None
_get_pff_cache = lambda: None


# ── Collision classification ────────────────────────────────────────────────

def _graphic_is_context_only(graphic):
    g = str(graphic or '').strip().lower()
    if not g:
        return False
    if g in globals().get('CONTEXT_ONLY_GRAPHICS', set()):
        return True
    return g.startswith(tuple(globals().get('CONTEXT_ONLY_GRAPHIC_PREFIXES', ())))


def _graphic_is_force_collision(graphic):
    g = str(graphic or '').strip().lower()
    if not g:
        return False
    if g in globals().get('FORCE_COLLISION_GRAPHICS', set()):
        return True
    return g.startswith(tuple(globals().get('FORCE_COLLISION_GRAPHIC_PREFIXES', ())))


def classify_item_collision_hint(item):
    """Classify an items.def entry before CModel footprint inspection.

    Returns:
      'context'    -> blue square only, do not queue/load CModel for collision
      'candidate'  -> allow CModel collision rendering
      'unknown'    -> keep visible as marker unless later promoted
    """
    if not item:
        return 'unknown'
    graphic = _graphic_name_from_item(item)
    if _graphic_is_context_only(graphic):
        return 'context'
    if _graphic_is_force_collision(graphic):
        return 'candidate'

    cat = str((item or {}).get('category') or (item or {}).get('type') or '').lower()

    if cat in {'building', 'object', 'vehicle'}:
        return 'candidate'
    if cat == 'decoration':
        return 'candidate'
    if cat == 'foliage':
        return 'candidate'
    if cat:
        _dbg('CMODEL_PARSE', f"[classify] unhandled cat={cat!r} for item={item.get('name','?')}", once_key=('classify_unhandled', cat, item.get('name', '?')))

    if cat in {'marker', 'person', 'effect', 'powerup'}:
        return 'context'

    return 'unknown'


def _item_category_allows_cmodel(item):
    """Return whether items.def semantics allow this type to be collision-loaded."""
    hint = classify_item_collision_hint(item)
    return hint == 'candidate'


# ── Layer visibility ────────────────────────────────────────────────────────

def entity_layer_kind(entity):
    rk = entity.get('render_kind') or entity.get('category')
    if rk:
        rk = str(rk).lower()
        if rk in ('building', 'decoration', 'foliage', 'object', 'vehicle'):
            return rk

    """Classify an entity for left-panel visibility filters.

    Uses items.def category when available, with type-id fallbacks for foliage.
    Returns: building, vehicle, object, decoration, foliage, other
    """
    try:
        tid = int(entity.get('type_id', 0))
    except Exception:
        tid = 0

    if tid in FOLIAGE_TYPE_IDS:
        return 'foliage'

    try:
        game_path = _get_game_path()
        pff_cache = _get_pff_cache()
        item = _load_items_def(game_path, log=None, pff_cache=pff_cache).get(tid, {}) if game_path else {}
        cat = (item.get('category') or '').strip().lower()
        if cat:
            if cat == 'foliage':
                return 'foliage'
            if cat == 'building' or cat == 'buildings':
                return 'building'
            if cat == 'vehicle' or cat == 'vehicles':
                return 'vehicle'
            if cat == 'object' or cat == 'objects':
                return 'object'
            if cat == 'decoration' or cat == 'decorations':
                return 'decoration'
            if cat in ('deco', 'decor'):
                return 'decoration'
    except Exception:
        pass

    if tid in FORCE_OBSTACLE:
        if (1000 <= tid <= 1269) or (1570 <= tid <= 1580) or (2113 <= tid <= 2115):
            return 'building'
        return 'decoration'

    return 'other'


def entity_visible_by_layer(entity, show_buildings=True, show_decorations=True,
                            show_foliage=True, show_vehicles=True,
                            show_objects=True):
    kind = entity_layer_kind(entity)
    if kind == 'building':
        return bool(show_buildings)
    if kind == 'vehicle':
        return bool(show_vehicles)
    if kind == 'object':
        return bool(show_objects)
    if kind == 'decoration':
        return bool(show_decorations)
    if kind == 'foliage':
        return bool(show_foliage)
    return True
