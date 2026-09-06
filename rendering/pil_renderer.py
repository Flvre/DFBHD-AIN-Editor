"""PIL back-buffer renderer for the 2D map view.

Single back-buffer renderer that draws all scene layers into one PIL Image
and displays it as a single canvas PhotoImage.  Matches MED editor's
DIB-blit architecture: one blit per frame regardless of scene complexity.
"""

import math

try:
    from PIL import Image, ImageDraw, ImageFont
    from PIL import ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

from rendering.viewport import (_wrap_world_delta, _world_delta,
    _world_to_screen_x, _world_to_screen_y, Viewport)
from cmodel.cmodel_geom import (_foliage_hull_segments, _cmodel_husk_segments,
    _cmodel_rotation_cache_key, _rotated_segments_for_heading,
    _rotated_bounds_for_heading,
    _segment_lod_cache, _rotated_segment_cache, _rotated_bounds_cache)
from cmodel.cmodel_access import (_cmodel_category_for_type,
    _segments_draw_lod_rpx, _is_tree_like_foliage_entity,
    CMODEL_MED_CULL_MARGIN_PX)
from entity.entity_data import (NEVER_EXCLUDE, FOLIAGE_TYPE_IDS,
    ALWAYS_FULL_OUTLINE_TYPES,
    OBSTACLE_RADII, DEFAULT_RADIUS)
from config.ui_theme import (C_CANVAS_BG, C_GRID, C_NODE, C_NODE_SEL, C_NODE_PREC,
    C_NODE_B12_2, C_NODE_B12_4, C_NODE_B12_5, C_NODE_B12_6, C_NODE_B12_7,
    C_NODE_B12_8, C_NODE_B12_9, C_NODE_B12_10, C_NODE_B12_11, C_NODE_B12_12,
    C_BREACH_ACTION, C_BREACH_TACTICAL, C_BREACH_ACTIVATION,
    C_EDGE, C_EDGE_SEL, C_EDGE_ONEWAY,
    C_RADIUS, C_RADIUS_SEL,
    C_ENTITY, C_VEHICLE, C_NEVER_EXCL, ZONE_COLORS)
from shared.debug_log import _dbg
from config.render_config import (
    STATIC_ENTITY_LAYER_CACHE, STATIC_ENTITY_LAYER_PAD_PX,
    STATIC_ENTITY_LAYER_ZOOM_EPS, STATIC_ENTITY_LAYER_MIN_ENTITIES,
    STATIC_ENTITY_LAYER_CLEAR_DERIVED_AFTER_REBUILD,
    CMODEL_FAST_CULL_MARGIN_PX, CMODEL_FAST_LINES_PER_ENTITY,
    CMODEL_FIRST_PASS_MARGIN_PX, CMODEL_ENTITY_HIDE_ZOOM,
    CMODEL_DETAIL_MIN_ZOOM, CMODEL_MAX_REQUESTS_PER_RENDER,
    ENTITY_RENDER_MODE, STABLE_ENTITY_OUTLINES,
    FOLIAGE_HULL_ONLY, SHOW_CONTEXT_ENTITY_MARKERS,
    MAX_VISIBLE_LINES_PER_ENTITY,
    BUILDING_FULL_DETAIL_ZOOM, CMODEL_FULL_DETAIL_BUILDING_ZOOM,
    BUILDINGS_BYPASS_LINE_BUDGET_AT_FULL_ZOOM,
    RADIUS_FILL_MIN_ZOOM,
    CONTEXT_MARKER_SIZE_PX, CONTEXT_MARKER_MIN_ZOOM, CONTEXT_MARKER_MAX_ZOOM)
from terrain.terrain import (
    _terrain_debug_compose_image, _terrain_is_medcook_phase,
    _terrain_medcook_visible_ranges, _terrain_sector_lookup_raw_index,
    _polytrn_sector_index_med, _terrain_tile_family,
    _terrain_quadrant_bounds, _terrain_medcook_source_y_span)
from terrain.terrain_water import _terrain_water_mask_for_size


def _hex(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# Static PIL color constants (not changed at runtime by theme switch)
_PIL_NODE       = _hex(C_NODE)
_PIL_NODE_SEL   = _hex(C_NODE_SEL)
_PIL_NODE_PREC  = _hex(C_NODE_PREC)
_PIL_NODE_B12_12 = _hex(C_NODE_B12_12)
_PIL_NODE_B12_5  = _hex(C_NODE_B12_5)
_PIL_NODE_B12_2  = _hex(C_NODE_B12_2)
_PIL_NODE_B12_4  = _hex(C_NODE_B12_4)
_PIL_NODE_B12_6  = _hex(C_NODE_B12_6)
_PIL_NODE_B12_7  = _hex(C_NODE_B12_7)
_PIL_NODE_B12_8  = _hex(C_NODE_B12_8)
_PIL_NODE_B12_9  = _hex(C_NODE_B12_9)
_PIL_NODE_B12_10 = _hex(C_NODE_B12_10)
_PIL_NODE_B12_11 = _hex(C_NODE_B12_11)
_PIL_BREACH_ACTION     = _hex(C_BREACH_ACTION)
_PIL_BREACH_TACTICAL   = _hex(C_BREACH_TACTICAL)
_PIL_BREACH_ACTIVATION = _hex(C_BREACH_ACTIVATION)
_PIL_EDGE       = _hex(C_EDGE)
_PIL_EDGE_SEL   = _hex(C_EDGE_SEL)
_PIL_EDGE_ONEWAY = _hex(C_EDGE_ONEWAY)
_PIL_NEVER_EXCL = _hex(C_NEVER_EXCL)
_PIL_RADIUS     = _hex(C_RADIUS)
_PIL_RADIUS_SEL = _hex(C_RADIUS_SEL)

class PILRenderer:
    """Single back-buffer renderer. Draws all scene layers into one PIL Image
    and displays it as a single canvas PhotoImage. Matches MED editor's
    DIB-blit architecture: one blit per frame regardless of scene complexity.

    Replaces NodeRenderer, EdgeRenderer, GridRenderer, EntityRenderer.
    Tk canvas retains only lightweight overlays (rubber-band, connect preview).
    """

    def __init__(self, canvas, *,
                 get_cmodel_segments=None,
                 request_cmodel_segments=None,
                 entity_visible_by_layer=None,
                 cmodel_should_lazy_request=None,
                 enforce_render_cache_caps=None,
                 model_outlines=None,
                 get_cmodel_render_segments=None,
                 get_game_path=None,
                 grid_spacing_fn=None):
        self._canvas   = canvas
        self._photo    = None
        self._img_item = None
        self._w        = 0
        self._h        = 0
        self._last_img      = None
        self._last_vp_off_x = None
        self._last_vp_off_y = None
        self._last_vp_zoom  = None
        self._last_profile  = {}
        self._static_entity_layer_img = None
        self._static_entity_layer_key = None
        self._static_entity_layer_vp = None
        self._static_entity_layer_size = None
        self._static_entity_layer_stats = {}
        # Constructor-injected monolith dependencies
        self._get_cmodel_segments = get_cmodel_segments or (lambda tid, **kw: None)
        self._request_cmodel_segments = request_cmodel_segments or (lambda tid, **kw: None)
        self._entity_visible_by_layer = entity_visible_by_layer or (lambda *a, **kw: True)
        self._cmodel_should_lazy_request = cmodel_should_lazy_request or (lambda *a, **kw: False)
        self._enforce_render_cache_caps = enforce_render_cache_caps or (lambda: None)
        self._model_outlines = model_outlines if model_outlines is not None else {}
        self._get_cmodel_render_segments = (get_cmodel_render_segments or
                                             (lambda tid, *a, **kw: None))
        self._get_game_path = get_game_path or (lambda: None)
        self._grid_spacing_fn = grid_spacing_fn or (lambda z, *a: (200.0, "Adaptive", None))
        # Runtime-toggled settings (set by host before render)
        self.foliage_hull_only = FOLIAGE_HULL_ONLY
        self.foliage_trees_only = True
        self.show_context_markers = SHOW_CONTEXT_ENTITY_MARKERS
        # Theme-changeable colors (updated by host on theme switch)
        self.pil_bg      = _hex(C_CANVAS_BG)
        self.pil_grid    = _hex(C_GRID)
        self.pil_entity  = _hex(C_ENTITY)
        self.pil_vehicle = _hex(C_VEHICLE)
        self.pil_foliage = (60, 180, 60)
        self.pil_origin  = (0x66, 0x66, 0x66)
        self.pil_node_id = (0xe0, 0xe0, 0xe0)
        self._show_edge_zone_color = False

    def clear(self):
        if self._img_item is not None:
            try: self._canvas.delete(self._img_item)
            except Exception: pass
            self._img_item = None
        self._photo = None
        self._static_entity_layer_img = None
        self._static_entity_layer_key = None
        self._static_entity_layer_vp = None
        self._static_entity_layer_size = None
        self._static_entity_layer_stats = {}
        self._pitched_outline_cache = {}

    def _pitched_outline_segments(self, type_id, entity, compact_segs):
        """Project a raw 3D model's pitched footprint into 2D map space.

        The normal 2D CModel is an already-flattened footprint and therefore
        has no height to rotate.  For a non-zero entity pitch, use the raw
        render mesh, fit its X/Z footprint to the compact CModel footprint,
        rotate its local depth/height around X, and leave heading rotation to
        the existing 2D path.
        """
        try:
            pitch = float(entity.get('pitch', 0.0) or 0.0)
        except Exception:
            pitch = 0.0
        if abs(pitch) < 1e-6:
            return compact_segs
        try:
            cache = getattr(self, '_pitched_outline_cache', None)
            if cache is None:
                cache = self._pitched_outline_cache = {}
            key = (int(type_id), round(pitch, 4), id(compact_segs))
            if key in cache:
                return cache[key]
            raw = self._get_cmodel_render_segments(
                int(type_id), self._get_game_path())
            if not raw:
                return compact_segs

            raw_pts = [p for pair in raw for p in pair]
            if not raw_pts:
                return compact_segs
            rxs = [float(p[0]) for p in raw_pts]
            rys = [float(p[1]) for p in raw_pts]
            rzs = [float(p[2]) for p in raw_pts]
            rcx = (min(rxs) + max(rxs)) * 0.5
            rcz = (min(rzs) + max(rzs)) * 0.5
            sx = sz = 1.0
            ccx = ccy = 0.0
            if compact_segs:
                compact_pts = [p for pair in compact_segs for p in pair]
                cxs = [float(p[0]) for p in compact_pts]
                cys = [float(p[1]) for p in compact_pts]
                rw = max(1e-6, max(rxs) - min(rxs))
                rd = max(1e-6, max(rzs) - min(rzs))
                sx = (max(cxs) - min(cxs)) / rw
                sz = (max(cys) - min(cys)) / rd
                ccx = (min(cxs) + max(cxs)) * 0.5
                ccy = (min(cys) + max(cys)) * 0.5
                if not (0.005 <= abs(sx) <= 200.0):
                    sx = 1.0
                if not (0.005 <= abs(sz) <= 200.0):
                    sz = sx
            sy = (abs(sx) + abs(sz)) * 0.5
            if not (0.25 <= abs(sy) <= 4.0):
                sy = 1.0
            cp = math.cos(math.radians(pitch))
            sp = math.sin(math.radians(pitch))
            out = []
            for a, b in raw:
                pair = []
                for p in (a, b):
                    x = (float(p[0]) - rcx) * sx + ccx
                    depth = (float(p[2]) - rcz) * sz + ccy
                    height = float(p[1]) * sy
                    # Positive MED pitch rotates vertical height into map
                    # depth, turning a standing model onto the ground.
                    depth_pitched = depth * cp - height * sp
                    pair.append((x, depth_pitched))
                out.append((pair[0], pair[1]))
            if len(cache) > 512:
                cache.clear()
            cache[key] = out
            return out
        except Exception:
            return compact_segs

    def render(self, nodes, entities, viewport, cw, ch,
               selected_nodes, show_nodes, show_edges, show_grid,
               show_entities, show_radius, show_zones, show_labels,
               edge_zoom_thresh,
               z_filter=False, z_min=None, z_max=None,
               show_b16_arrows=False, show_b12_extended=False, show_b12_bands=False,
               show_breach=False, breach_zone_ids=None,
               debug_overlay_fn=None,
               hidden_zone_ids=None, locked_zone_ids=None,
               fast_mode=False,
               terrain_image=None, show_terrain=False, terrain_info=None, show_water=True,
               show_buildings=True, show_decorations=True, show_foliage=True,
               show_vehicles=True, show_objects=True,
               simple_nodes=False, show_radius_fill=False,
               show_radius_fill_b15=False,
               b12_visible_ids=None):
        """Full scene render into PIL bitmap, then blit to canvas.
        In fast_mode (during pan/zoom), shifts cached bitmap for instant pan feel.
        Called every redraw — PIL handles 1000+ nodes/8000+ edges fast.
        """
        if not PIL_AVAILABLE:
            return

        zoom = viewport.zoom
        vp   = viewport

        # ── Bitmap pan in fast_mode ───────────────────────────────────────────
        # If we have a cached full image and zoom hasn't changed, just shift the
        # bitmap by the pan delta. Fills exposed edges with background color.
        # This is what MED does — slide the existing bitmap, don't redraw.
        if (fast_mode and self._last_img is not None
                and self._last_vp_zoom is not None
                and abs(zoom - self._last_vp_zoom) < 0.001
                and self._last_img.size == (cw, ch)):
            dx = int(_wrap_world_delta(self._last_vp_off_x - vp.offset_x) * zoom)
            dy = int(_wrap_world_delta(vp.offset_y - self._last_vp_off_y) * zoom)
            if dx != 0 or dy != 0:
                shifted = Image.new('RGB', (cw, ch), self.pil_bg)
                # Paste cached image shifted by (dx, dy)
                paste_x = dx
                paste_y = dy
                import time as _profile_time
                _pt0 = _profile_time.perf_counter()
                shifted.paste(self._last_img, (paste_x, paste_y))
                self._blit(shifted, cw, ch)
                try:
                    self._last_profile = {
                        'total': round((_profile_time.perf_counter() - _pt0) * 1000.0, 2),
                        'cached_shift': True,
                        'fast': True,
                        'zoom': round(float(zoom), 3),
                    }
                except Exception:
                    pass
                return
            else:
                # No movement — blit cached image directly
                import time as _profile_time
                _pt0 = _profile_time.perf_counter()
                self._blit(self._last_img, cw, ch)
                try:
                    self._last_profile = {
                        'total': round((_profile_time.perf_counter() - _pt0) * 1000.0, 2),
                        'cached_blit': True,
                        'fast': True,
                        'zoom': round(float(zoom), 3),
                    }
                except Exception:
                    pass
                return

        img  = Image.new('RGB', (cw, ch), self.pil_bg)
        draw = ImageDraw.Draw(img)

        import time as _profile_time
        _prof = {}
        _prof_t0 = _profile_time.perf_counter()
        def _mark_profile(name, start):
            try:
                _prof[name] = round((_profile_time.perf_counter() - start) * 1000.0, 2)
            except Exception:
                pass
            return _profile_time.perf_counter()

        # Build Z-filtered visible id set
        if z_filter and z_min is not None and z_max is not None:
            z_visible = set(n.id for n in nodes if z_min <= n.z <= z_max)
        else:
            z_visible = None  # None = show all
        if b12_visible_ids is not None:
            b12_visible_ids = set(b12_visible_ids)
            z_visible = b12_visible_ids if z_visible is None else (set(z_visible) & b12_visible_ids)

        # During active zoom, avoid expensive entity/CModel/edge work.
        # This makes the wheel feel immediate; full detail returns on settle.
        zoom_fast_mode = bool(fast_mode and self._last_vp_zoom is not None and abs(zoom - self._last_vp_zoom) >= 0.001)
        if zoom_fast_mode:
            # Keep nodes/radius visible while zooming. Only skip the expensive
            # extras that cause the worst hitching on large graphs.
            show_edges = False
            show_labels = False
            show_b16_arrows = False
            # Keep node color semantics stable while zooming/panning.
            # show_b12_extended intentionally remains unchanged.

        # ── Terrain backdrop ─────────────────────────────────────────────────
        _pt = _profile_time.perf_counter()
        if show_terrain and terrain_image is not None:
            self._draw_terrain_backdrop(
                img, terrain_image, terrain_info, vp, cw, ch, zoom,
                show_water=show_water)
        _pt = _mark_profile('terrain', _pt)

        # ── Grid ─────────────────────────────────────────────────────────────
        if show_grid:
            self._draw_grid(draw, vp, cw, ch, zoom)

        # ── Entities ─────────────────────────────────────────────────────────
        _pt = _profile_time.perf_counter()
        entity_layer_stats = None
        if show_entities and entities:
            if fast_mode:
                self._draw_entities_fast(draw, entities, vp, cw, ch, zoom,
                                         show_buildings=show_buildings,
                                         show_decorations=show_decorations,
                                         show_foliage=show_foliage,
                                         show_vehicles=show_vehicles,
                                         show_objects=show_objects)
                entity_layer_stats = {'mode': 'fast_direct'}
            else:
                entity_layer_stats = self._draw_static_entities_layer_cached(
                    img, entities, vp, cw, ch, zoom,
                    show_buildings=show_buildings,
                    show_decorations=show_decorations,
                    show_foliage=show_foliage,
                    show_vehicles=show_vehicles,
                    show_objects=show_objects)
        _pt = _mark_profile('entities', _pt)
        try:
            if entity_layer_stats:
                self._static_entity_layer_stats = dict(entity_layer_stats)
                _prof['static_entity_layer'] = entity_layer_stats.get('mode')
        except Exception:
            pass

        self._enforce_render_cache_caps()

        # ── Edges ─────────────────────────────────────────────────────────────
        draw_edges = show_edges and zoom > edge_zoom_thresh
        if draw_edges and nodes:
            self._draw_edges(draw, nodes, vp, cw, ch, selected_nodes, zoom, z_visible,
                              hidden_zone_ids=hidden_zone_ids, locked_zone_ids=locked_zone_ids,
                              show_breach=show_breach, breach_zone_ids=breach_zone_ids,
                              show_zones=show_zones)

        # ── Nodes ─────────────────────────────────────────────────────────────
        if show_nodes and nodes:
            if simple_nodes:
                self._draw_nodes_simple(draw, nodes, vp, cw, ch, selected_nodes, zoom, z_visible,
                                         hidden_zone_ids=hidden_zone_ids, locked_zone_ids=locked_zone_ids)
            else:
                self._draw_nodes(draw, nodes, vp, cw, ch, selected_nodes,
                                 show_zones, show_radius, show_labels, zoom, z_visible,
                                 show_b16_arrows=show_b16_arrows,
                                 show_b12_extended=show_b12_extended,
                                 show_b12_bands=show_b12_bands,
                                 show_breach=show_breach,
                                 breach_zone_ids=breach_zone_ids,
                                 debug_overlay_fn=debug_overlay_fn,
                                 hidden_zone_ids=hidden_zone_ids,
                                 locked_zone_ids=locked_zone_ids)

        # ── Radius alpha-fill (overlap-accumulating, baked into the bitmap so it pans
        #    and caches with everything else instead of being a stale separate overlay)
        if (show_radius_fill or show_radius_fill_b15) and show_nodes and nodes:
            self._draw_radius_fill(img, nodes, vp, cw, ch, zoom, z_visible,
                                    hidden_zone_ids=hidden_zone_ids,
                                    locked_zone_ids=locked_zone_ids,
                                    color_by_b15=show_radius_fill_b15,
                                    selected_nodes=selected_nodes)

        # Blit to canvas — ONE Tcl crossing
        try:
            _prof['total_no_blit'] = round((_profile_time.perf_counter() - _prof_t0) * 1000.0, 2)
            _prof['zoom'] = round(float(zoom), 3)
            if entity_layer_stats:
                _prof['static_entity_layer'] = entity_layer_stats.get('mode')
            self._last_profile = _prof
        except Exception:
            pass

        self._last_img      = img.copy()
        self._last_vp_off_x = vp.offset_x
        self._last_vp_off_y = vp.offset_y
        self._last_vp_zoom  = zoom
        self._blit(img, cw, ch)

    def invalidate_static_entity_layer(self):
        """Drop the retained static entity layer."""
        self._static_entity_layer_img = None
        self._static_entity_layer_key = None
        self._static_entity_layer_vp = None
        self._static_entity_layer_size = None
        self._static_entity_layer_stats = {}

    def _static_entity_cache_key(self, entities, zoom, show_buildings, show_decorations,
                                 show_foliage, show_vehicles, show_objects):
        try:
            return (
                round(float(zoom), 4),
                bool(show_buildings), bool(show_decorations), bool(show_foliage),
                bool(show_vehicles), bool(show_objects),
                len(entities or ()),
                tuple((int(e.get('type_id', 0)),
                       round(float(e.get('heading', 0.0) or 0.0), 3),
                       round(float(e.get('pitch', 0.0) or 0.0), 3))
                      for e in (entities or ())),
                # Do NOT key the retained 2D layer to len(_3di_cache). The lazy
                # CModel worker can populate that cache several times per second;
                # tying the bitmap key to it turned each newly decoded model into
                # a synchronous full-layer rebuild while the user was panning.
                # Newly available geometry is picked up naturally in newly exposed
                # strips and on the next real layer invalidation (zoom/toggle/map).
                self.foliage_hull_only,
                self.foliage_trees_only,
                bool(self.show_context_markers),
            )
        except Exception:
            return None

    def _paste_static_entity_layer(self, img, layer, cached_vp, cur_vp, cw, ch, pad):
        try:
            zoom = float(cur_vp.zoom)
            dx = int(round(_wrap_world_delta(cached_vp.offset_x - cur_vp.offset_x) * zoom))
            dy = int(round(_wrap_world_delta(cur_vp.offset_y - cached_vp.offset_y) * zoom))
            left = int(pad - dx)
            top = int(pad - dy)
            if left < 0 or top < 0 or left + cw > layer.size[0] or top + ch > layer.size[1]:
                return False
            crop = layer.crop((left, top, left + cw, top + ch))
            img.paste(crop, (0, 0), crop)
            return True
        except Exception:
            return False

    def _draw_static_entity_strip(self, layer, rect, entities, cache_vp, zoom,
                                  show_buildings=True, show_decorations=True,
                                  show_foliage=True, show_vehicles=True,
                                  show_objects=True):
        """Render only one newly exposed rectangle of the retained entity layer.

        The normal retained layer is larger than the viewport. Previously, once
        panning crossed that padding, the entire large layer was rebuilt in one
        blocking operation. This helper lets the cache behave like a classic
        scrolled backbuffer: move the pixels we already have, then rasterize only
        the strip of world space that has just entered the cache.
        """
        try:
            x0, y0, x1, y1 = [int(v) for v in rect]
            lw, lh = layer.size
            x0 = max(0, min(lw, x0)); x1 = max(0, min(lw, x1))
            y0 = max(0, min(lh, y0)); y1 = max(0, min(lh, y1))
            if x1 <= x0 or y1 <= y0:
                return 0

            sw, sh = x1 - x0, y1 - y0
            strip = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
            sdraw = ImageDraw.Draw(strip)

            # Convert the strip centre inside the full retained layer back into
            # world coordinates. _draw_entities then gets an ordinary viewport,
            # but its viewport is only as large as the missing strip.
            cx = (x0 + x1) * 0.5
            cy = (y0 + y1) * 0.5
            z = max(0.000001, float(zoom))
            strip_off_x = float(cache_vp.offset_x) + (cx - lw * 0.5) / z
            strip_off_y = float(cache_vp.offset_y) - (cy - lh * 0.5) / z
            strip_vp = Viewport(strip_off_x, strip_off_y, z)

            self._draw_entities(
                sdraw, entities, strip_vp, sw, sh, z,
                show_buildings=show_buildings,
                show_decorations=show_decorations,
                show_foliage=show_foliage,
                show_vehicles=show_vehicles,
                show_objects=show_objects)
            layer.paste(strip, (x0, y0), strip)
            return sw * sh
        except Exception:
            return 0

    def _scroll_static_entity_layer(self, entities, cur_vp, zoom,
                                    show_buildings=True, show_decorations=True,
                                    show_foliage=True, show_vehicles=True,
                                    show_objects=True):
        """Scroll/rebase the retained entity bitmap and fill only exposed strips.

        Returns True when the cached layer was successfully moved to a new
        centre. Large teleports/zoom changes deliberately fall back to the normal
        full rebuild path.
        """
        layer = self._static_entity_layer_img
        old_vp = self._static_entity_layer_vp
        if layer is None or old_vp is None:
            return False
        try:
            z = float(zoom)
            if abs(float(old_vp.zoom) - z) >= STATIC_ENTITY_LAYER_ZOOM_EPS:
                return False
            lw, lh = layer.size

            # Integer pixel shift needed to keep old world pixels registered in
            # the new retained layer coordinate system.
            dx = int(round(_wrap_world_delta(float(old_vp.offset_x) - float(cur_vp.offset_x)) * z))
            dy = int(round(_wrap_world_delta(float(cur_vp.offset_y) - float(old_vp.offset_y)) * z))
            if dx == 0 and dy == 0:
                return True
            if abs(dx) >= lw or abs(dy) >= lh:
                return False

            moved = Image.new('RGBA', (lw, lh), (0, 0, 0, 0))
            moved.paste(layer, (dx, dy))

            # Because the shift is rounded to integral pixels, keep the cache's
            # world centre aligned to that exact pixel displacement rather than
            # accumulating sub-pixel drift over a long key hold.
            new_off_x = float(old_vp.offset_x) - (dx / z)
            new_off_y = float(old_vp.offset_y) + (dy / z)
            new_vp = Viewport(new_off_x, new_off_y, z)

            filled_px = 0
            if dx > 0:       # old pixels moved right -> left strip exposed
                filled_px += self._draw_static_entity_strip(
                    moved, (0, 0, dx, lh), entities, new_vp, z,
                    show_buildings, show_decorations, show_foliage, show_vehicles,
                    show_objects)
            elif dx < 0:     # old pixels moved left -> right strip exposed
                filled_px += self._draw_static_entity_strip(
                    moved, (lw + dx, 0, lw, lh), entities, new_vp, z,
                    show_buildings, show_decorations, show_foliage, show_vehicles,
                    show_objects)

            if dy > 0:       # old pixels moved down -> top strip exposed
                filled_px += self._draw_static_entity_strip(
                    moved, (0, 0, lw, dy), entities, new_vp, z,
                    show_buildings, show_decorations, show_foliage, show_vehicles,
                    show_objects)
            elif dy < 0:     # old pixels moved up -> bottom strip exposed
                filled_px += self._draw_static_entity_strip(
                    moved, (0, lh + dy, lw, lh), entities, new_vp, z,
                    show_buildings, show_decorations, show_foliage, show_vehicles,
                    show_objects)

            self._static_entity_layer_img = moved
            self._static_entity_layer_vp = new_vp
            self._static_entity_layer_size = (lw, lh)
            self._static_entity_layer_stats = {
                'mode': 'scroll_fill',
                'shift_px': (dx, dy),
                'filled_px': int(filled_px),
            }
            return True
        except Exception:
            return False


    def _clear_static_layer_build_caches(self):
        """Clear rebuildable caches after a static entity layer has been baked.

        The retained layer now contains the visible static geometry, so holding
        large rotated/LOD caches after the rebuild only increases RSS. Exact
        source CModels in _3di_cache are not cleared.
        """
        if not STATIC_ENTITY_LAYER_CLEAR_DERIVED_AFTER_REBUILD:
            return {}
        removed = {}
        try:
            for name, obj in (('_segment_lod_cache', _segment_lod_cache),
                              ('_rotated_segment_cache', _rotated_segment_cache),
                              ('_rotated_bounds_cache', _rotated_bounds_cache)):
                removed[name] = len(obj)
                obj.clear()
        except Exception:
            pass
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        return removed


    def _draw_static_entities_layer_cached(self, img, entities, vp, cw, ch, zoom,
                                           show_buildings=True, show_decorations=True,
                                           show_foliage=True, show_vehicles=True,
                                           show_objects=True):
        """Draw or reuse an oversized transparent static entity layer."""
        if (not STATIC_ENTITY_LAYER_CACHE or
                not entities or
                len(entities) < STATIC_ENTITY_LAYER_MIN_ENTITIES):
            draw = ImageDraw.Draw(img)
            self._draw_entities(draw, entities, vp, cw, ch, zoom,
                                show_buildings=show_buildings,
                                show_decorations=show_decorations,
                                show_foliage=show_foliage,
                                show_vehicles=show_vehicles,
                                show_objects=show_objects)
            return {'mode': 'direct'}

        pad = STATIC_ENTITY_LAYER_PAD_PX
        key = self._static_entity_cache_key(entities, zoom, show_buildings, show_decorations,
                                            show_foliage, show_vehicles, show_objects)

        cached = (
            self._static_entity_layer_img is not None and
            self._static_entity_layer_key == key and
            self._static_entity_layer_vp is not None and
            self._static_entity_layer_size == (cw + pad * 2, ch + pad * 2)
        )

        if cached:
            if self._paste_static_entity_layer(img, self._static_entity_layer_img,
                                               self._static_entity_layer_vp, vp, cw, ch, pad):
                try:
                    raw_mb = (self._static_entity_layer_img.size[0] * self._static_entity_layer_img.size[1] * 4) / (1024.0 * 1024.0)
                except Exception:
                    raw_mb = 0.0
                return {'mode': 'reuse', 'pad': pad, 'layer_raw_mb': round(raw_mb, 1)}

            # Instead of rebuilding the entire oversized layer when the pan
            # reaches its padding boundary, scroll the existing bitmap and
            # rasterize only the newly exposed edge strip(s). This is the key
            # difference from v98_40's periodic 1-2 second cache cliff.
            if self._scroll_static_entity_layer(
                    entities, vp, zoom,
                    show_buildings=show_buildings,
                    show_decorations=show_decorations,
                    show_foliage=show_foliage,
                    show_vehicles=show_vehicles,
                    show_objects=show_objects):
                if self._paste_static_entity_layer(img, self._static_entity_layer_img,
                                                   self._static_entity_layer_vp, vp, cw, ch, pad):
                    try:
                        raw_mb = (self._static_entity_layer_img.size[0] * self._static_entity_layer_img.size[1] * 4) / (1024.0 * 1024.0)
                    except Exception:
                        raw_mb = 0.0
                    stats = dict(getattr(self, '_static_entity_layer_stats', {}) or {})
                    stats.update({'pad': pad, 'layer_raw_mb': round(raw_mb, 1)})
                    return stats

            # Very large teleport / incompatible cache: use the established full
            # rebuild fallback rather than risking a partially populated layer.
            cache_miss_reason = 'outside_pad_scroll_failed'
        else:
            cache_miss_reason = 'key_or_size'

        layer_w, layer_h = cw + pad * 2, ch + pad * 2
        layer = Image.new('RGBA', (layer_w, layer_h), (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        layer_vp = Viewport(vp.offset_x, vp.offset_y, zoom)
        self._draw_entities(ldraw, entities, layer_vp, layer_w, layer_h, zoom,
                            show_buildings=show_buildings,
                            show_decorations=show_decorations,
                            show_foliage=show_foliage,
                            show_vehicles=show_vehicles,
                            show_objects=show_objects)
        self._static_entity_layer_img = layer
        self._static_entity_layer_key = key
        self._static_entity_layer_vp = layer_vp
        self._static_entity_layer_size = (layer_w, layer_h)
        ok = self._paste_static_entity_layer(img, layer, layer_vp, vp, cw, ch, pad)
        cleared = self._clear_static_layer_build_caches()
        raw_mb = (layer_w * layer_h * 4) / (1024.0 * 1024.0)
        return {'mode': 'rebuild_paste' if ok else 'rebuild_direct', 'pad': pad,
                'miss': cache_miss_reason, 'layer_size': (layer_w, layer_h),
                'layer_raw_mb': round(raw_mb, 1), 'cleared_after_rebuild': cleared}

    def _blit(self, img, cw, ch):
        """Convert PIL image to PhotoImage and display on canvas."""
        photo = ImageTk.PhotoImage(img)
        self._photo = photo   # must keep reference or GC kills it

        if self._img_item is None:
            # First render — create the image item
            self._img_item = self._canvas.create_image(
                0, 0, image=photo, anchor='nw', tags=('pil_layer',))
        elif cw != self._w or ch != self._h:
            # Canvas was resized — delete and recreate so size is correct
            try: self._canvas.delete(self._img_item)
            except Exception: pass
            self._img_item = self._canvas.create_image(
                0, 0, image=photo, anchor='nw', tags=('pil_layer',))
        else:
            # Normal frame — just update the image
            self._canvas.itemconfigure(self._img_item, image=photo)

        # Ensure PIL layer is below Tk overlays (rubber-band, connect preview)
        # Use tag_raise on overlays instead of tag_lower on PIL layer
        # to avoid accidentally hiding the image when it's the only item
        try:
            self._canvas.tag_raise('sel_box')
            self._canvas.tag_raise('connect_preview')
        except Exception:
            pass

        self._w = cw
        self._h = ch


    def _draw_terrain_backdrop(self, img, terrain_image, terrain_info, vp, cw, ch, zoom, show_water=True):
        """Draw terrain colormap using MED's sector clamp/wrap behavior.

        Do not clip drawing to the 16x16 terrain rectangle. MED keeps sampling
        outside the authored region:
          - wrap off: clamp to edge sector
          - wrap on : wrap sector index with & 0xf

        This fixes dvd1-style terrains that previously had a hard cutoff.
        """
        if terrain_image is None:
            return
        try:
            sp = {}
            primary_image = terrain_image
            secondary_image = None
            debug_mode = 'current'
            debug_overlay = False
            debug_cache = None
            if isinstance(terrain_info, dict):
                sp = terrain_info.get('spatial') or {}
                primary_image = terrain_info.get('primary_image') or terrain_image
                secondary_image = terrain_info.get('secondary_image')
                debug_mode = str(terrain_info.get('_debug_mode') or 'current')
                phase_mode = str(terrain_info.get('_phase_mode') or 'current')
                debug_overlay = bool(terrain_info.get('_debug_overlay'))
                debug_cache = terrain_info.setdefault('_debug_compose_cache', {})
                display_mode = str(terrain_info.get('_display_mode') or 'C').upper()
                display_image = None
                if display_mode == 'H':
                    display_image = terrain_info.get('height_display_image')
                elif display_mode == 'D':
                    display_image = terrain_info.get('depth_display_image')
                if display_image is not None:
                    # H/D visualize the canonical DPTH payload for both terrain
                    # tile families.  Debug image composition applies only to C.
                    primary_image = display_image
                    secondary_image = display_image
                    debug_mode = 'current'
            else:
                phase_mode = 'current'

            sector_world = float(sp.get('sector_world', 512.0))
            sectors = sp.get('sectors') or [[1 for _ in range(16)] for __ in range(16)]
            x0 = float(sp.get('world_x0', -4.0 * sector_world))
            y0 = float(sp.get('world_y0', -4.0 * sector_world))
            wrapx = int(sp.get('wrapx') or 0)
            wrapy = int(sp.get('wrapy') or 0)

            if sector_world <= 0 or zoom <= 0:
                return

            src_w, src_h = primary_image.size
            forced_debug_image = _terrain_debug_compose_image(
                primary_image, secondary_image, debug_mode, debug_cache
            )
            overlay_draw = None
            if debug_overlay:
                try:
                    from PIL import ImageDraw
                    overlay_draw = ImageDraw.Draw(img)
                except Exception:
                    overlay_draw = None

            wx_min = vp.offset_x - (cw / 2.0) / zoom
            wx_max = vp.offset_x + (cw / 2.0) / zoom
            wy_min = vp.offset_y - (ch / 2.0) / zoom
            wy_max = vp.offset_y + (ch / 2.0) / zoom

            import math as _math
            medcook_mode = _terrain_is_medcook_phase(phase_mode)
            if medcook_mode:
                raw_col0, raw_col1, raw_row0, raw_row1, _mc_x_bounds, _mc_y_bounds, _mc_local_y = _terrain_medcook_visible_ranges(
                    wx_min, wx_max, wy_min, wy_max, sp, phase_mode
                )
            else:
                raw_col0 = int(_math.floor((wx_min - x0) / sector_world))
                raw_col1 = int(_math.floor((wx_max - x0) / sector_world))
                raw_row0 = int(_math.floor((wy_min - y0) / sector_world))
                raw_row1 = int(_math.floor((wy_max - y0) / sector_world))

            # Safety cap for extreme zoom-out. Normal use is far below this.
            if (raw_col1 - raw_col0 + 1) * (raw_row1 - raw_row0 + 1) > 900:
                return

            resample = Image.BILINEAR if zoom >= 0.2 else Image.NEAREST

            for raw_world_row in range(raw_row0, raw_row1 + 1):
                if medcook_mode:
                    sy_world0, sy_world1 = _mc_y_bounds(raw_world_row)
                    row_raw_idx = raw_world_row
                    world_row_idx = _polytrn_sector_index_med(row_raw_idx, wrapy)
                    table_row = int(world_row_idx)
                else:
                    sy_world0 = y0 + raw_world_row * sector_world
                    sy_world1 = sy_world0 + sector_world
                    row_raw_idx = _terrain_sector_lookup_raw_index(
                        raw_world_row, (sy_world0 + sy_world1) * 0.5, y0, sector_world, phase_mode
                    )
                    world_row_idx = _polytrn_sector_index_med(row_raw_idx, wrapy)
                    table_row = 15 - int(world_row_idx)

                vis_y0 = max(wy_min, sy_world0)
                vis_y1 = min(wy_max, sy_world1)
                if vis_y1 <= vis_y0:
                    continue

                if table_row < 0 or table_row >= len(sectors):
                    continue

                for raw_col in range(raw_col0, raw_col1 + 1):
                    if medcook_mode:
                        sx_world0, sx_world1 = _mc_x_bounds(raw_col)
                        col_raw_idx = raw_col
                        col = _polytrn_sector_index_med(col_raw_idx, wrapx)
                    else:
                        sx_world0 = x0 + raw_col * sector_world
                        sx_world1 = sx_world0 + sector_world
                        col_raw_idx = _terrain_sector_lookup_raw_index(
                            raw_col, (sx_world0 + sx_world1) * 0.5, x0, sector_world, phase_mode
                        )
                        col = _polytrn_sector_index_med(col_raw_idx, wrapx)

                    vis_x0 = max(wx_min, sx_world0)
                    vis_x1 = min(wx_max, sx_world1)
                    if vis_x1 <= vis_x0:
                        continue

                    try:
                        tile_id = int(sectors[table_row][col])
                    except Exception:
                        tile_id = 0
                    family_name, quad_id = _terrain_tile_family(tile_id)
                    if family_name == 'empty':
                        continue
                    if family_name is None:
                        _dbg(
                            'TERRAIN',
                            f'[TERRAIN] unsupported terrain tile id {tile_id} at row={table_row} col={col}; '
                            'terrain backdrop skipped for this sector',
                            once_key=('unsupported_tile_draw', tile_id, table_row, col)
                        )
                        continue
                    active_image = primary_image
                    if forced_debug_image is not None:
                        active_image = forced_debug_image
                    elif family_name == 'secondary' and secondary_image is not None:
                        active_image = secondary_image
                    elif family_name == 'secondary' and secondary_image is None:
                        _dbg(
                            'TERRAIN',
                            f'[TERRAIN] secondary terrain family tile id {tile_id} fell back to primary image',
                            once_key=('secondary_fallback_draw', tile_id)
                        )
                    src_w, src_h = active_image.size
                    box = _terrain_quadrant_bounds(quad_id, src_w, src_h)
                    if box is None:
                        continue
                    bx0, by0, bx1, by1 = box
                    bw = bx1 - bx0
                    bh = by1 - by0

                    u0 = (vis_x0 - sx_world0) / sector_world
                    u1 = (vis_x1 - sx_world0) / sector_world
                    v0 = (vis_y0 - sy_world0) / sector_world
                    v1 = (vis_y1 - sy_world0) / sector_world

                    src_x0 = bx0 + max(0, min(bw - 1, int(_math.floor(u0 * bw))))
                    src_x1 = bx0 + max(src_x0 - bx0 + 1, min(bw, int(_math.ceil(u1 * bw))))
                    if medcook_mode:
                        yneg_mode = str(phase_mode or '').strip().lower() == 'medcook_yneg'
                        sy0_rel, sy1_rel = _terrain_medcook_source_y_span(
                            vis_y0, vis_y1, raw_world_row, _mc_local_y, bh, yneg_mode
                        )
                        src_y0 = by0 + sy0_rel
                        src_y1 = by0 + max(sy0_rel + 1, sy1_rel)
                    else:
                        # World up/north maps to image top inside each quadrant.
                        src_y0 = by0 + max(0, min(bh - 1, int(_math.floor((1.0 - v1) * bh))))
                        src_y1 = by0 + max(src_y0 - by0 + 1, min(bh, int(_math.ceil((1.0 - v0) * bh))))

                    dx0 = int(round(_world_to_screen_x(vis_x0, vp.offset_x, zoom, cw / 2.0)))
                    dx1 = int(round(_world_to_screen_x(vis_x1, vp.offset_x, zoom, cw / 2.0)))
                    dy0 = int(round(_world_to_screen_y(vis_y1, vp.offset_y, zoom, ch / 2.0)))
                    dy1 = int(round(_world_to_screen_y(vis_y0, vp.offset_y, zoom, ch / 2.0)))
                    if dx1 <= dx0 or dy1 <= dy0:
                        continue

                    crop = active_image.crop((src_x0, src_y0, src_x1, src_y1))
                    crop = crop.resize((dx1 - dx0, dy1 - dy0), resample)

                    cx0, cy0 = max(0, dx0), max(0, dy0)
                    cx1, cy1 = min(cw, dx1), min(ch, dy1)
                    if cx1 <= cx0 or cy1 <= cy0:
                        continue
                    if (cx0, cy0, cx1, cy1) != (dx0, dy0, dx1, dy1):
                        crop = crop.crop((cx0 - dx0, cy0 - dy0, cx1 - dx0, cy1 - dy0))
                    img.paste(crop, (cx0, cy0))

                    # Water is part of the terrain layer. The mask uses the
                    # same CPT/quadrant coordinates as the visible terrain.
                    if show_water and isinstance(terrain_info, dict):
                        _water_mask = _terrain_water_mask_for_size(
                            terrain_info, active_image.size)
                        if _water_mask is not None:
                            try:
                                _wcrop = _water_mask.crop(
                                    (src_x0, src_y0, src_x1, src_y1))
                                _wcrop = _wcrop.resize(
                                    (dx1 - dx0, dy1 - dy0), Image.NEAREST)
                                if (cx0, cy0, cx1, cy1) != (dx0, dy0, dx1, dy1):
                                    _wcrop = _wcrop.crop(
                                        (cx0 - dx0, cy0 - dy0,
                                         cx1 - dx0, cy1 - dy0))
                                img.paste(_wcrop, (cx0, cy0), _wcrop)
                            except Exception:
                                pass

                    if overlay_draw is not None:
                        try:
                            fam_color = (80, 255, 80) if family_name == 'primary' else (80, 220, 255)
                            overlay_draw.rectangle([cx0, cy0, cx1 - 1, cy1 - 1], outline=fam_color, width=1)
                            if (cx1 - cx0) >= 22 and (cy1 - cy0) >= 16:
                                overlay_draw.text(
                                    (cx0 + 2, cy0 + 1),
                                    f'{tile_id}',
                                    fill=(255, 255, 0)
                                )
                            if (cx1 - cx0) >= 44 and (cy1 - cy0) >= 28:
                                overlay_draw.text(
                                    (cx0 + 2, cy0 + 11),
                                    f'{table_row},{col}',
                                    fill=(220, 220, 220)
                                )
                            if phase_mode != 'current' and (cx1 - cx0) >= 70 and (cy1 - cy0) >= 40:
                                overlay_draw.text(
                                    (cx0 + 2, cy0 + 21),
                                    f'{phase_mode}',
                                    fill=(120, 220, 255)
                                )
                        except Exception:
                            pass
        except Exception as e:
            _dbg('TERRAIN', f'[TERRAIN] draw error: {e}', once_key=('draw_error_clamp_wrap_fix', str(e)))


    def _draw_grid(self, draw, vp, cw, ch, zoom):
        wx0, wy0, wx1, wy1 = vp.visible_rect(cw, ch)
        spacing, grid_mode, safety_note = self._grid_spacing_fn(zoom, wx0, wy0, wx1, wy1)

        x = math.floor(wx0 / spacing) * spacing
        while x <= wx1 + spacing:
            cx, _ = vp.world_to_canvas(x, 0, cw, ch)
            if 0 <= cx <= cw:
                draw.line([(cx, 0), (cx, ch)], fill=self.pil_grid, width=1)
            x += spacing

        y = math.floor(wy0 / spacing) * spacing
        while y <= wy1 + spacing:
            _, cy = vp.world_to_canvas(0, y, cw, ch)
            if 0 <= cy <= ch:
                draw.line([(0, cy), (cw, cy)], fill=self.pil_grid, width=1)
            y += spacing

        # Origin cross
        ox, oy = vp.world_to_canvas(0, 0, cw, ch)
        draw.line([(ox-10, oy), (ox+10, oy)], fill=self.pil_origin, width=1)
        draw.line([(ox, oy-10), (ox, oy+10)], fill=self.pil_origin, width=1)


    def _draw_entity_context_marker(self, draw, ecx, ecy, color=None, size=None):
        """Small cheap context marker for non-detailed/unloaded entities."""
        if not self.show_context_markers:
            return
        s = int(size or CONTEXT_MARKER_SIZE_PX)
        col = color or _PIL_NEVER_EXCL
        draw.rectangle([ecx - s, ecy - s, ecx + s, ecy + s], outline=col)

    def _draw_entities_fast(self, draw, entities, vp, cw, ch, zoom,
                            show_buildings=True, show_decorations=True,
                            show_foliage=True, show_vehicles=True,
                            show_objects=True):
        """Fast entity draw for pan/zoom interaction.

        Older fast mode drew only center circles. That made buildings/props
        appear to disappear while zooming or when their origin was offscreen.
        This version draws cached CModel outlines for visible buildings/decor,
        using exact rotated bounds. It never queues new model loads here.
        """
        half_w, half_h = cw * 0.5, ch * 0.5
        off_x, off_y = vp.offset_x, vp.offset_y
        z = zoom
        margin = CMODEL_FAST_CULL_MARGIN_PX
        line_budget = CMODEL_FAST_LINES_PER_ENTITY

        for e in entities:
            if not self._entity_visible_by_layer(
                    e, show_buildings, show_decorations, show_foliage,
                    show_vehicles, show_objects):
                continue

            type_id = int(e.get('type_id', 0))
            ex, ey = float(e.get('x', 0.0)), float(e.get('y', 0.0))
            ecx = _world_to_screen_x(ex, off_x, z, half_w)
            ecy = _world_to_screen_y(ey, off_y, z, half_h)

            if type_id in NEVER_EXCLUDE:
                if not self.show_context_markers:
                    continue
                if ecx < -20 or ecx > cw + 20 or ecy < -20 or ecy > ch + 20:
                    continue
                r = max(1, min(4, int(z * 0.8)))
                draw.ellipse([ecx-r, ecy-r, ecx+r, ecy+r], outline=(60, 160, 60))
                continue

            eclass = e.get('editor_class')
            if eclass == 'context':
                continue
            if not e.get('is_static') and type_id not in ALWAYS_FULL_OUTLINE_TYPES:
                continue

            outline_segs = self._get_cmodel_segments(type_id, allow_load=False) or self._model_outlines.get(type_id)
            cat = str(e.get('category') or e.get('render_kind') or
                      _cmodel_category_for_type(type_id)).lower()
            if type_id in FOLIAGE_TYPE_IDS:
                cat = 'foliage'
            if cat == 'foliage' and not _is_tree_like_foliage_entity(
                    type_id, outline_segs,
                    trees_only=self.foliage_trees_only):
                continue
            outline_segs = self._pitched_outline_segments(
                type_id, e, outline_segs)
            if outline_segs:
                # Buildings must stay readable. Foliage uses trunk/collision hull
                # by default so leaves do not dominate render time.
                if cat == 'foliage' and self.foliage_hull_only:
                    draw_segs = _foliage_hull_segments(outline_segs)
                    geom_kind = f'foliage_hull:{len(draw_segs)}'
                elif cat == 'building':
                    draw_segs = outline_segs
                    geom_kind = 'full'
                elif len(outline_segs) > line_budget:
                    draw_segs = _cmodel_husk_segments(outline_segs) or outline_segs
                    geom_kind = f'husk:{len(draw_segs)}' if draw_segs is not outline_segs else 'full'
                else:
                    draw_segs = outline_segs
                    geom_kind = 'full'

                rot_cache_key = _cmodel_rotation_cache_key(type_id, geom_kind, game_path=self._get_game_path())
                bmnx, bmny, bmxx, bmxy = _rotated_bounds_for_heading(draw_segs, e.get('heading', 0), rot_cache_key)
                sx_min = half_w + (ex - off_x + bmnx) * z
                sx_max = half_w + (ex - off_x + bmxx) * z
                sy_min = half_h - (ey - off_y + bmxy) * z
                sy_max = half_h - (ey - off_y + bmny) * z
                if sx_max < -margin or sx_min > cw + margin or sy_max < -margin or sy_min > ch + margin:
                    continue

                rot_segs = _rotated_segments_for_heading(draw_segs, e.get('heading', 0), rot_cache_key)
                base_x = _world_delta(ex, off_x)
                base_y = _world_delta(ey, off_y)
                ent_color = self.pil_foliage if cat == 'foliage' else (self.pil_vehicle if cat == 'vehicle' else self.pil_entity)

                # If stable outlines are enabled, do not stride/morph vehicles
                # and props while zooming. Otherwise the editor shows different
                # collision shapes at different zoom levels.
                step = 1
                if (not STABLE_ENTITY_OUTLINES and
                    cat != 'building' and len(rot_segs) > line_budget):
                    step = max(1, int(len(rot_segs) / line_budget))

                for idx, (r1, r2) in enumerate(rot_segs):
                    if idx % step:
                        continue
                    sx1 = half_w + (base_x + r1[0]) * z
                    sy1 = half_h - (base_y + r1[1]) * z
                    sx2 = half_w + (base_x + r2[0]) * z
                    sy2 = half_h - (base_y + r2[1]) * z
                    if (sx1 < -margin and sx2 < -margin) or (sx1 > cw + margin and sx2 > cw + margin):
                        continue
                    if (sy1 < -margin and sy2 < -margin) or (sy1 > ch + margin and sy2 > ch + margin):
                        continue
                    draw.line([(sx1, sy1), (sx2, sy2)], fill=ent_color, width=1)
                continue

            # No cached model yet: use a more generous projected radius marker,
            # not the old tiny origin-only dot.
            base_r = float(OBSTACLE_RADII.get(type_id, DEFAULT_RADIUS))
            r = max(3, min(28, base_r * z))
            if ecx + r < -margin or ecx - r > cw + margin or ecy + r < -margin or ecy - r > ch + margin:
                continue
            draw.ellipse([ecx - r, ecy - r, ecx + r, ecy + r], outline=self.pil_entity)


    def _draw_entities(self, draw, entities, vp, cw, ch, zoom,
                       show_buildings=True, show_decorations=True,
                       show_foliage=True, show_vehicles=True,
                       show_objects=True):
        """Draw static collision/entity outlines with strict viewport culling."""
        seg_color = self.pil_entity
        # Per-entity color: foliage gets green
        def _entity_color(e):
            cat = _cmodel_category_for_type(int(e.get('type_id', 0)))
            if cat == 'foliage':
                return self.pil_foliage
            if cat == 'vehicle':
                return self.pil_vehicle
            return seg_color
        request_count = 0
        dbg_seen = dbg_static = dbg_no_geom = dbg_drawn_entities = dbg_lines = dbg_culled_first = dbg_culled_bbox = dbg_husk_entities = dbg_lod_full = dbg_lod_medium = dbg_lod_low = 0
        half_w, half_h = cw * 0.5, ch * 0.5
        off_x, off_y = vp.offset_x, vp.offset_y
        z = zoom
        margin = CMODEL_FIRST_PASS_MARGIN_PX

        for e in entities:
            if not self._entity_visible_by_layer(
                    e, show_buildings, show_decorations, show_foliage,
                    show_vehicles, show_objects):
                continue
            dbg_seen += 1
            type_id = e['type_id']
            ex, ey = e['x'], e['y']
            # Screen center. Do not cull large static models by center/radius
            # when cached geometry exists: long buildings/walls can have their
            # origin offscreen while visible edges are on-screen.
            ecx = _world_to_screen_x(ex, off_x, z, half_w)
            ecy = _world_to_screen_y(ey, off_y, z, half_h)
            base_r = OBSTACLE_RADII.get(type_id, DEFAULT_RADIUS)
            rpx = max(1.0, float(base_r) * z)
            med_margin = CMODEL_MED_CULL_MARGIN_PX

            # Grab cached geometry early. allow_load=False is important here:
            # visibility tests must not trigger synchronous loading.
            outline_segs = self._get_cmodel_segments(type_id, allow_load=False) or self._model_outlines.get(type_id)
            cat = str(e.get('category') or e.get('render_kind') or
                      _cmodel_category_for_type(type_id)).lower()
            if type_id in FOLIAGE_TYPE_IDS:
                cat = 'foliage'
            if cat == 'foliage' and not _is_tree_like_foliage_entity(
                    type_id, outline_segs,
                    trees_only=self.foliage_trees_only):
                continue
            outline_segs = self._pitched_outline_segments(
                type_id, e, outline_segs)

            if not outline_segs:
                if ecx + rpx < -med_margin or ecx - rpx > cw + med_margin:
                    dbg_culled_first += 1
                    continue
                if ecy + rpx < -med_margin or ecy - rpx > ch + med_margin:
                    dbg_culled_first += 1
                    continue

            if type_id in NEVER_EXCLUDE:
                if self.show_context_markers:
                    draw.rectangle([ecx-4, ecy-4, ecx+4, ecy+4], outline=_PIL_NEVER_EXCL)
                continue

            eclass = e.get('editor_class')
            if eclass == 'context':
                if (self.show_context_markers and
                    CONTEXT_MARKER_MIN_ZOOM <= z <=
                    CONTEXT_MARKER_MAX_ZOOM):
                    self._draw_entity_context_marker(draw, ecx, ecy)
                continue

            if not e.get('is_static') and type_id not in ALWAYS_FULL_OUTLINE_TYPES:
                if (self.show_context_markers and
                    CONTEXT_MARKER_MIN_ZOOM <= z <=
                    CONTEXT_MARKER_MAX_ZOOM):
                    self._draw_entity_context_marker(draw, ecx, ecy)
                continue
            dbg_static += 1

            hide_zoom = CMODEL_ENTITY_HIDE_ZOOM
            if z >= hide_zoom and type_id not in ALWAYS_FULL_OUTLINE_TYPES:
                continue

            if not outline_segs:
                dbg_no_geom += 1
                if (e.get('is_static') and z >= CMODEL_DETAIL_MIN_ZOOM and
                    request_count < CMODEL_MAX_REQUESTS_PER_RENDER and
                    self._cmodel_should_lazy_request(e, z, rpx)):
                    self._request_cmodel_segments(type_id)
                    request_count += 1
                if self.show_context_markers:
                    self._draw_entity_context_marker(draw, ecx, ecy)
                else:
                    rrpx = min(rpx, 8)
                    draw.ellipse([ecx-rrpx, ecy-rrpx, ecx+rrpx, ecy+rrpx], outline=self.pil_entity)
                if z > 10:
                    draw.text((ecx, ecy), str(type_id), fill=(0x88, 0x88, 0x88))
                continue

            draw_segs = _segments_draw_lod_rpx(outline_segs, rpx, type_id)
            if draw_segs is outline_segs:
                geom_kind = 'full'
            else:
                cat_tmp = str(e.get('category') or _cmodel_category_for_type(type_id)).lower()
                if cat_tmp == 'foliage':
                    geom_kind = f'foliage_hull:{len(draw_segs)}'
                else:
                    geom_kind = f'husk:{len(draw_segs)}'
            rot_cache_key = _cmodel_rotation_cache_key(type_id, geom_kind, game_path=self._get_game_path())
            if draw_segs is outline_segs:
                dbg_lod_full += 1
            elif draw_segs:
                dbg_husk_entities += 1
                dbg_lod_medium += 1
            if not draw_segs:
                dbg_lod_low += 1
                self._draw_entity_context_marker(draw, ecx, ecy)
                continue

            # Precise per-instance bbox after heading rotation. Much tighter than
            # a radius for walls/long models.
            bmnx, bmny, bmxx, bmxy = _rotated_bounds_for_heading(draw_segs, e.get('heading', 0), rot_cache_key)
            sx_min = half_w + (ex - off_x + bmnx) * z
            sx_max = half_w + (ex - off_x + bmxx) * z
            sy_min = half_h - (ey - off_y + bmxy) * z
            sy_max = half_h - (ey - off_y + bmny) * z
            if sx_max < -margin or sx_min > cw + margin or sy_max < -margin or sy_min > ch + margin:
                dbg_culled_bbox += 1
                continue

            mode = str(ENTITY_RENDER_MODE).lower()
            budget = MAX_VISIBLE_LINES_PER_ENTITY
            cat_for_budget = _cmodel_category_for_type(type_id)
            building_full_zoom = BUILDING_FULL_DETAIL_ZOOM
            # Buildings are structural navigation context. Do not collapse them
            # because they have many lines; that is exactly when the outline is
            # needed to remain readable while zooming.
            building_should_bypass_budget = (
                cat_for_budget == 'building'
                or (z >= building_full_zoom and BUILDINGS_BYPASS_LINE_BUDGET_AT_FULL_ZOOM)
            )
            if (mode == 'hybrid' and not STABLE_ENTITY_OUTLINES and draw_segs is outline_segs and
                len(draw_segs) > budget and not building_should_bypass_budget):
                draw_segs = _cmodel_husk_segments(outline_segs)
                dbg_husk_entities += 1
                if not draw_segs:
                    self._draw_entity_context_marker(draw, ecx, ecy)
                    continue
                rot_cache_key = _cmodel_rotation_cache_key(type_id, f'husk:{len(draw_segs)}', game_path=self._get_game_path())
                bmnx, bmny, bmxx, bmxy = _rotated_bounds_for_heading(draw_segs, e.get('heading', 0), rot_cache_key)
                sx_min = half_w + (ex - off_x + bmnx) * z
                sx_max = half_w + (ex - off_x + bmxx) * z
                sy_min = half_h - (ey - off_y + bmxy) * z
                sy_max = half_h - (ey - off_y + bmny) * z
                if sx_max < -margin or sx_min > cw + margin or sy_max < -margin or sy_min > ch + margin:
                    dbg_culled_bbox += 1
                    continue

            dbg_drawn_entities += 1

            rot_segs = _rotated_segments_for_heading(draw_segs, e.get('heading', 0), rot_cache_key)
            base_x = _world_delta(ex, off_x)
            base_y = _world_delta(ey, off_y)
            ent_color = _entity_color(e)
            for (r1, r2) in rot_segs:
                sx1 = half_w + (base_x + r1[0]) * z
                sy1 = half_h - (base_y + r1[1]) * z
                sx2 = half_w + (base_x + r2[0]) * z
                sy2 = half_h - (base_y + r2[1]) * z

                # Per-line screen cull for big partially visible objects.
                if (sx1 < -margin and sx2 < -margin) or (sx1 > cw + margin and sx2 > cw + margin):
                    continue
                if (sy1 < -margin and sy2 < -margin) or (sy1 > ch + margin and sy2 > ch + margin):
                    continue

                draw.line([(sx1, sy1), (sx2, sy2)], fill=ent_color, width=1)
                dbg_lines += 1

            if z > 6:
                draw.text((ecx, ecy), str(type_id), fill=(0x88, 0x88, 0x88))

        _dbg('CMODEL_DRAW',
             f'[draw] entities={dbg_seen} static={dbg_static} first_cull={dbg_culled_first} '
             f'bbox_cull={dbg_culled_bbox} no_geom={dbg_no_geom} drawn={dbg_drawn_entities} '
             f'lines={dbg_lines} full={dbg_lod_full} med={dbg_lod_medium} low={dbg_lod_low} husk={dbg_husk_entities} queued={request_count} zoom={z:.2f}',
             throttle=1.0)

    def _draw_edges(self, draw, nodes, vp, cw, ch, selected_nodes, zoom, z_visible=None,
                    hidden_zone_ids=None, locked_zone_ids=None,
                    show_breach=False, breach_zone_ids=None, show_zones=False):
        margin = max(2.0, 20.0 / zoom)
        x0, y0, x1, y1 = vp.visible_rect(cw, ch, margin_world=margin)
        _hidden = hidden_zone_ids or set()
        _locked = locked_zone_ids or set()
        id_to_node = {node.id: node for node in nodes}
        vis_ids = set()
        for node in nodes:
            if node.b14 in _hidden:
                continue
            if z_visible is not None and node.id not in z_visible:
                continue
            if x0 <= node.x <= x1 and y0 <= node.y <= y1:
                vis_ids.add(node.id)
        _zone_active = False
        _zone_color_cache = {}
        if getattr(self, '_show_edge_zone_color', False):
            if show_breach and breach_zone_ids:
                manual_ids, auto_ids, both_ids = breach_zone_ids
                _breach_all_ids = manual_ids | auto_ids | both_ids
                if _breach_all_ids:
                    _zone_active = True
                    for zid in _breach_all_ids:
                        _zone_color_cache[zid] = _hex(ZONE_COLORS[int(zid) % len(ZONE_COLORS)])
            elif show_zones:
                _zone_active = True
        # Draw edges — each edge once (lower id first)
        # One-way connections drawn in red, mutual in green
        # Locked nodes: edges hidden entirely (no visual clutter)
        for nid in vis_ids:
            node = id_to_node[nid]
            if node.b14 in _locked:
                continue  # locked node — no edges drawn
            ax, ay = vp.world_to_canvas(node.x, node.y, cw, ch)
            is_sel = nid in selected_nodes
            for nb_id in node.neighbors:
                if nb_id not in id_to_node: continue
                nb = id_to_node[nb_id]
                if z_visible is not None and nb_id not in z_visible:
                    continue
                if nb.b14 in _hidden or nb.b14 in _locked:
                    continue  # neighbour is hidden or locked — skip edge
                # Only draw each edge once — use lower id, but for one-way
                # we need to check both directions first
                is_mutual = nid in nb.neighbors
                if not is_mutual:
                    bx, by = vp.world_to_canvas(nb.x, nb.y, cw, ch)
                    col = _PIL_EDGE_SEL if (is_sel or nb_id in selected_nodes) else _PIL_EDGE_ONEWAY
                    draw.line([(ax, ay), (bx, by)], fill=col, width=2)
                elif nb_id > nid:
                    if nb_id not in vis_ids: continue
                    bx, by = vp.world_to_canvas(nb.x, nb.y, cw, ch)
                    if is_sel or nb_id in selected_nodes:
                        col = _PIL_EDGE_SEL
                    elif _zone_active and node.b14 == nb.b14 and node.b14 != 0:
                        zid = int(node.b14)
                        col = _zone_color_cache.get(zid) or _hex(ZONE_COLORS[zid % len(ZONE_COLORS)])
                    else:
                        col = _PIL_EDGE
                    draw.line([(ax, ay), (bx, by)], fill=col, width=1)

    def _draw_nodes_simple(self, draw, nodes, vp, cw, ch, selected_nodes, zoom, z_visible=None,
                            hidden_zone_ids=None, locked_zone_ids=None):
        """Very cheap overview nodes for low zoom / huge graphs."""
        half_w, half_h = cw * 0.5, ch * 0.5
        off_x, off_y = vp.offset_x, vp.offset_y
        z = zoom
        r = 1 if z < 0.35 else 2
        sel = selected_nodes or set()
        margin = 4
        _hidden = hidden_zone_ids or set()
        _locked = locked_zone_ids or set()
        for n in nodes:
            if n.b14 in _hidden:
                continue
            if z_visible is not None and n.id not in z_visible:
                continue
            x = _world_to_screen_x(n.x, off_x, z, half_w)
            y = _world_to_screen_y(n.y, off_y, z, half_h)
            if x < -margin or x > cw + margin or y < -margin or y > ch + margin:
                continue
            col = _PIL_NODE_SEL if n.id in sel else _PIL_NODE
            if r <= 1:
                draw.point((int(x), int(y)), fill=col)
            else:
                draw.rectangle([x-r, y-r, x+r, y+r], outline=col)

    def _radius_fill_b15_color(self, b15, alpha=72):
        """Return a radius-fill color for the full byte-sized b15 range."""
        try:
            value = int(b15)
        except Exception:
            value = 0
        value = max(0, min(255, value))
        stops = [
            (0, (64, 96, 255)),       # blue
            (12, (0, 210, 255)),      # cyan
            (24, (0, 220, 90)),       # green
            (36, (255, 220, 0)),      # yellow
            (48, (255, 80, 40)),      # orange/red — existing endpoint
            (64, (255, 0, 0)),        # red
            (96, (255, 0, 150)),      # magenta
            (128, (190, 0, 220)),     # violet
            (160, (110, 0, 255)),     # blue-violet
            (192, (30, 100, 230)),    # blue/cyan
            (224, (0, 210, 150)),     # green-cyan
            (255, (255, 220, 0)),     # yellow
        ]
        for idx in range(len(stops) - 1):
            left_t, left_rgb = stops[idx]
            right_t, right_rgb = stops[idx + 1]
            if value <= right_t:
                span = max(0.0001, right_t - left_t)
                local = max(0.0, min(1.0, (value - left_t) / span))
                rgb = tuple(int(round(left_rgb[ch] + (right_rgb[ch] - left_rgb[ch]) * local))
                            for ch in range(3))
                return (*rgb, alpha)
        return (*stops[-1][1], alpha)

    def _draw_radius_fill(self, img, nodes, vp, cw, ch, zoom, z_visible=None,
                          hidden_zone_ids=None, locked_zone_ids=None,
                          color_by_b15=False, selected_nodes=None):
        """Filled, alpha-blended green acceptance-radius discs composited into the scene
        bitmap. Overlapping discs ACCUMULATE (denser green where more nodes overlap), so
        radius coverage/overlap is readable. Visible-rect culled; partial discs are cropped
        so alpha_composite stays in bounds. Only drawn when zoomed in (mirrors the outline)."""
        if zoom <= RADIUS_FILL_MIN_ZOOM:
            return   # zoomed out: hide the fill for performance, like the radius outline
        try:
            margin = max(2.0, 20.0 / zoom)
            x0, y0, x1, y1 = vp.visible_rect(cw, ch, margin_world=margin)
        except Exception:
            x0 = y0 = -1e18; x1 = y1 = 1e18
        hidden = hidden_zone_ids or set()
        locked = locked_zone_ids or set()
        selected = selected_nodes or set()
        overlay = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
        base_fill = (30, 175, 72, 72)   # slightly darker green; per-disc alpha so overlaps build up
        selected_fill = (255, 220, 0, 105)  # selected node radius fill, matching yellow selection
        drew = False
        for node in nodes:
            if z_visible is not None and node.id not in z_visible:
                continue
            if not (x0 <= node.x <= x1 and y0 <= node.y <= y1):
                continue
            if node.b14 in hidden or node.b14 in locked:
                continue
            r_px = node.acceptance_radius() * zoom
            if r_px < 1.0:
                continue
            ncx, ncy = vp.world_to_canvas(node.x, node.y, cw, ch)
            dx = int(round(ncx - r_px)); dy = int(round(ncy - r_px))
            d = int(round(r_px * 2)) + 1
            sx0 = max(0, -dx); sy0 = max(0, -dy)
            sx1 = min(d, cw - dx); sy1 = min(d, ch - dy)
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            is_sel = node.id in selected
            fill = selected_fill if is_sel else (self._radius_fill_b15_color(node.b15) if color_by_b15 else base_fill)
            if fill is None:
                fill = selected_fill if is_sel else base_fill
            tile = Image.new('RGBA', (d, d), (0, 0, 0, 0))
            ImageDraw.Draw(tile).ellipse((0, 0, d - 1, d - 1), fill=fill)
            if (sx0, sy0, sx1, sy1) != (0, 0, d, d):
                tile = tile.crop((sx0, sy0, sx1, sy1))
            overlay.alpha_composite(tile, (dx + sx0, dy + sy0))
            drew = True
        if drew:
            img.paste(overlay, (0, 0), overlay)

    def _draw_nodes(self, draw, nodes, vp, cw, ch, selected_nodes,
                    show_zones, show_radius, show_labels, zoom, z_visible=None,
                    show_b16_arrows=False, show_b12_extended=False, show_b12_bands=False,
                    show_breach=False, breach_zone_ids=None,
                    debug_overlay_fn=None,
                    hidden_zone_ids=None, locked_zone_ids=None):
        margin  = max(2.0, 20.0 / zoom)
        x0, y0, x1, y1 = vp.visible_rect(cw, ch, margin_world=margin)
        nr_base = max(2, min(6, zoom * 0.3))
        # b12 Bands (View>b12 Bands): cycled palette for values 2..12; >12 repeat
        # the cycle. 13-50 = duo shade, 51-127 = blue ring, 128+ = duo + ring.
        _b12_cycle = (_PIL_NODE_B12_2, _hex('#ee55aa'), _PIL_NODE_B12_4, _PIL_NODE_B12_5,
                      _PIL_NODE_B12_6, _PIL_NODE_B12_7, _PIL_NODE_B12_8, _PIL_NODE_B12_9,
                      _PIL_NODE_B12_10, _PIL_NODE_B12_11, _PIL_NODE_B12_12)
        _b12_ring = _hex('#33aaff')

        for idx, node in enumerate(nodes):
            if z_visible is not None and node.id not in z_visible:
                continue
            if not (x0 <= node.x <= x1 and y0 <= node.y <= y1):
                continue
            ncx, ncy = vp.world_to_canvas(node.x, node.y, cw, ch)
            is_sel = node.id in selected_nodes

            # Radius circle — skip for locked/hidden zones
            _b14_early = node.b14
            _hidden_early = hidden_zone_ids or set()
            _locked_early = locked_zone_ids or set()
            if show_radius and (zoom > 8.0 or is_sel) and \
                    _b14_early not in _hidden_early and _b14_early not in _locked_early:
                r_px = node.acceptance_radius() * zoom
                if r_px > 2:
                    col = _PIL_RADIUS_SEL if is_sel else _PIL_RADIUS
                    draw.ellipse([ncx-r_px, ncy-r_px, ncx+r_px, ncy+r_px],
                                 outline=col)

            # Zone layer — skip hidden nodes entirely, ghost locked ones
            _b14 = node.b14
            _hidden_ids = hidden_zone_ids or set()
            _locked_ids = locked_zone_ids or set()
            if _b14 in _hidden_ids:
                continue
            _is_locked = _b14 in _locked_ids

            # Node dot — debug overlay takes priority after selection
            _dbg_col = debug_overlay_fn(node) if debug_overlay_fn else None
            drew_custom = False
            if _is_locked:
                # Ghost: draw at reduced opacity using dimmed color
                col = _hex('#333333')
                nr  = max(2, nr_base - 1)
            elif is_sel:
                col = _PIL_NODE_SEL
                nr  = max(4, nr_base)
            elif show_b12_bands and node.b12 >= 13:
                # Standout ONLY for values >12 (duo/rings). 2-12 stays with Extended
                # b12; this composes with it, never replaces or filters it.
                b12v = node.b12
                base = _b12_cycle[(b12v - 2) % 11]
                nr = nr_base
                _duo = (13 <= b12v <= 50) or (b12v >= 128)
                _ring = (51 <= b12v <= 127) or (b12v >= 128)
                if _duo:
                    _dk = (base[0] // 2, base[1] // 2, base[2] // 2)
                    draw.pieslice([ncx-nr, ncy-nr, ncx+nr, ncy+nr], start=90, end=270, fill=base)
                    draw.pieslice([ncx-nr, ncy-nr, ncx+nr, ncy+nr], start=270, end=90, fill=_dk)
                else:
                    draw.ellipse([ncx-nr, ncy-nr, ncx+nr, ncy+nr], fill=base)
                if _ring:
                    _rr = nr + 3
                    draw.ellipse([ncx-_rr, ncy-_rr, ncx+_rr, ncy+_rr], outline=_b12_ring, width=2)
                col = base
                drew_custom = True
            elif _dbg_col:
                if isinstance(_dbg_col, dict):
                    # Special seed node rendering — duo split + ring
                    seed_type = _dbg_col.get('type', '')
                    if seed_type == 'seed_exact':
                        # Gold ring, left=deep blue, right=white
                        ring_r = nr_base + 4
                        draw.ellipse([ncx-ring_r, ncy-ring_r, ncx+ring_r, ncy+ring_r],
                                     outline=_hex('#FFD700'), width=2)
                        # Left half — deep blue
                        draw.pieslice([ncx-nr_base, ncy-nr_base, ncx+nr_base, ncy+nr_base],
                                      start=90, end=270, fill=_hex('#0033cc'))
                        # Right half — bright white
                        draw.pieslice([ncx-nr_base, ncy-nr_base, ncx+nr_base, ncy+nr_base],
                                      start=270, end=90, fill=_hex('#ffffff'))
                        continue
                    elif seed_type == 'seed_near':
                        # Silver ring, left=medium blue, right=light cyan
                        ring_r = nr_base + 3
                        draw.ellipse([ncx-ring_r, ncy-ring_r, ncx+ring_r, ncy+ring_r],
                                     outline=_hex('#AAAAAA'), width=1)
                        # Left half — medium blue
                        draw.pieslice([ncx-nr_base, ncy-nr_base, ncx+nr_base, ncy+nr_base],
                                      start=90, end=270, fill=_hex('#2266dd'))
                        # Right half — light cyan
                        draw.pieslice([ncx-nr_base, ncy-nr_base, ncx+nr_base, ncy+nr_base],
                                      start=270, end=90, fill=_hex('#88eeff'))
                        continue
                    # Unknown dict type — fall through to normal
                    col = _PIL_NODE
                    nr  = nr_base
                else:
                    col = _dbg_col
                    nr  = nr_base
            elif show_breach and breach_zone_ids:
                manual_ids, auto_ids, both_ids = breach_zone_ids
                all_ids = manual_ids | auto_ids | both_ids
                in_action     = node.b14 in all_ids
                in_activation = node.b17 in all_ids
                if in_action:
                    _zc = ZONE_COLORS[int(node.b14) % len(ZONE_COLORS)]
                    col = _hex(_zc)
                    nr  = nr_base
                    b13v = node.b13
                    _B13_HALF_COLORS = {
                        1: "#ffffff",
                        4: "#ff8800",
                        6: "#ffee00",
                        7: "#ff2222",
                        8: "#00dd44",
                    }
                    if b13v in _B13_HALF_COLORS:
                        _b13c = _hex(_B13_HALF_COLORS[b13v])
                        bbox = [ncx-nr_base, ncy-nr_base, ncx+nr_base, ncy+nr_base]
                        draw.pieslice(bbox, start=270, end=90, fill=col)
                        draw.pieslice(bbox, start=90, end=270, fill=_b13c)
                        draw.ellipse(bbox, outline=(0, 0, 0), width=1)
                        ring_r = nr_base + 3
                        if b13v == 6:
                            ring_r = nr_base + 4
                        draw.ellipse([ncx-ring_r, ncy-ring_r,
                                      ncx+ring_r, ncy+ring_r],
                                     outline=_b13c, width=2)
                        drew_custom = True
                    elif b13v == 9:
                        tr = nr_base + 7
                        pts = [
                            ncx,       ncy - tr,
                            ncx + tr,  ncy + tr,
                            ncx - tr,  ncy + tr,
                        ]
                        draw.polygon(pts, outline=_hex("#ff0000"),
                                     fill=None)
                elif in_activation:
                    ring_r = nr_base + 3
                    draw.ellipse([ncx-ring_r, ncy-ring_r, ncx+ring_r, ncy+ring_r],
                                 outline=_PIL_BREACH_ACTIVATION)
                    col = _PIL_NODE
                    nr  = nr_base
                else:
                    col = _PIL_NODE
                    nr  = nr_base
            elif show_zones:
                if show_b12_extended and node.b12 >= 2:
                    _EXT = {
                        2:  _PIL_NODE_B12_2,
                        4:  _PIL_NODE_B12_4,
                        5:  _PIL_NODE_B12_5,
                        6:  _PIL_NODE_B12_6,
                        7:  _PIL_NODE_B12_7,
                        8:  _PIL_NODE_B12_8,
                        9:  _PIL_NODE_B12_9,
                        10: _PIL_NODE_B12_10,
                        11: _PIL_NODE_B12_11,
                        12: _PIL_NODE_B12_12,
                    }
                    col = _EXT.get(node.b12, _hex(ZONE_COLORS[node.b14 % len(ZONE_COLORS)]))
                else:
                    col = _hex(ZONE_COLORS[node.b14 % len(ZONE_COLORS)])
                nr  = nr_base
            elif show_b12_extended and node.b12 >= 2:
                _EXT = {
                    2:  _PIL_NODE_B12_2,
                    4:  _PIL_NODE_B12_4,
                    5:  _PIL_NODE_B12_5,
                    6:  _PIL_NODE_B12_6,
                    7:  _PIL_NODE_B12_7,
                    8:  _PIL_NODE_B12_8,
                    9:  _PIL_NODE_B12_9,
                    10: _PIL_NODE_B12_10,
                    11: _PIL_NODE_B12_11,
                    12: _PIL_NODE_B12_12,
                }
                col = _EXT.get(node.b12, _PIL_NODE)
                nr  = nr_base
            elif node.b12 & 0x01:
                col = _PIL_NODE_PREC
                nr  = nr_base
            else:
                col = _PIL_NODE
                nr  = nr_base
            if not drew_custom:
                draw.ellipse([ncx-nr, ncy-nr, ncx+nr, ncy+nr], fill=col)

            # b16 arrow
            if show_b16_arrows and node.b16 > 0:
                import math
                angle_rad = (node.b16 * 2 * math.pi / 255) - math.pi / 2
                arrow_len = max(8, nr_base * 3)
                tip_x = ncx + math.cos(angle_rad) * arrow_len
                tip_y = ncy + math.sin(angle_rad) * arrow_len
                draw.line([ncx, ncy, tip_x, tip_y], fill=(255, 255, 80), width=1)
                head = 3
                left_x = tip_x + math.cos(angle_rad + 2.5) * head
                left_y = tip_y + math.sin(angle_rad + 2.5) * head
                right_x = tip_x + math.cos(angle_rad - 2.5) * head
                right_y = tip_y + math.sin(angle_rad - 2.5) * head
                draw.line([tip_x, tip_y, left_x, left_y], fill=(255, 255, 80), width=1)
                draw.line([tip_x, tip_y, right_x, right_y], fill=(255, 255, 80), width=1)

            # Label
            if show_labels and zoom > 20.0:
                draw.text((ncx + nr + 2, ncy - 4), str(node.id),
                          fill=self.pil_node_id)



