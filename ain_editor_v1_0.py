"""
DFBHD .AIN Visual Editor
========================
Release: v1.0

Single-file tool for generating, visualizing, and editing Delta Force:
Black Hawk Down .AIN navigation graphs.

Primary layout:
  Left panel  — layer controls and selected-node tools
  Top bar     — editor modes and viewport controls
  Main canvas — 2D top-down or embedded 3D view
  Status bar  — node count, connectivity, and operation status

Normal mode presents creator-facing names and controls. Developer mode exposes
raw metadata fields, diagnostics, and experimental tooling.
"""

import sys, os, traceback
from shared.runtime_paths import writable_app_file

_CRASH_LOG = str(writable_app_file('ain_editor_crash.log', __file__))

def _write_crash(exc_type, exc_val, exc_tb):
    try:
        with open(_CRASH_LOG, 'w') as f:
            f.write('=== AIN EDITOR CRASH LOG ===\n')
            f.write(''.join(traceback.format_exception(exc_type, exc_val, exc_tb)))
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_val, exc_tb)

sys.excepthook = _write_crash

import runtime_namespace
runtime_namespace.bind(globals())

# ── Version ───────────────────────────────────────────────────────────────────
EDITOR_VERSION = "v1.0"


# Adaptive Radius section header + constants — imported from adaptive_radius
# _make_clearance_blocked_fn — imported from clearance_blocked
# adaptive_clearance_b15 through adaptive_resolve_b15 — imported from adaptive_radius
EDITOR_TITLE   = "AIN Editor"

_CONFIG_PATH = str(writable_app_file('ain_editor_config.json', __file__))

def _load_config():
    try:
        import json
        with open(_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(cfg):
    try:
        import json
        with open(_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

import tkinter as tk
import re
from tkinter import filedialog, messagebox, ttk, simpledialog, colorchooser
import struct, math, os, json, sys, traceback, statistics, threading, time, io
import pickle, tempfile, subprocess, atexit
from pathlib import Path
from format.ain_format import SENTINEL, MAX_NEIGHBORS, EXTRA_COUNT, Node, write_ain, read_ain, sanitize_node_graph, connect_nodes
from shared.spatial_index import SpatialHash
from resources.items_def import BHD_SCRIPT_KEY, _rol32, _descr_crypt, _decrypt_scr, _parse_items_def_text, _graphic_to_filename
from generator.support import (Point, Segment, GENERATOR_CORE_CONTRACT, LabNode,
    _cross, _convex_hull, _poly_area, SolidMask, radius_m,
    _v52_convex_hull, _v52_poly_area, _v52_point_in_poly, _v52_dist_point_seg, _V52Spatial,
    GENERATOR_REACHABLE_MAX_STEP, GENERATOR_REACHABLE_STAIR_STEP,
    GENERATOR_CORRIDOR_FIT, GENERATOR_CORRIDOR_FINAL_FACTOR, GENERATOR_CORRIDOR_SLIDE_BUDGET,
    GENERATOR_CORRIDOR_CENTER_FLOOR, GENERATOR_CORRIDOR_DOMINANCE,
    GENERATOR_QUANTIZED_GRID_M, GENERATOR_QUANTIZED_BASE_PITCH_M,
    GENERATOR_QUANTIZED_CENTER_CLEARANCE, GENERATOR_STANDING_BODY_LO, GENERATOR_STANDING_BODY_HI,
    _GeneratorStandingVolumeIndex,
    _generator_node_record, _generator_point_in_polygon, _crop_generator_nodes_for_job)
from cmodel.cmodel_parse import (_u32, _i16, _cmodel_face_is_collision,
    _parse_gpm_cmodel, _parse_gpm_cmodel_3d, _parse_gpm_cmodel_3d_grouped,
    _parse_render_mesh_3d)
from rendering.viewport import (_WORLD_WRAP_HALF, _WORLD_WRAP_FULL,
    _wrap_world_delta, _world_delta, _world_to_screen_x, _world_to_screen_y,
    Viewport, HitTester)
from resources.pff_archive import (_pff_priority_key, _build_pff_index, _read_from_pff,
    _read_from_pff_fuzzy, _pff_find_candidates,
    _iter_pff_archive_records, _iter_pff_archives, _read_from_pff_stack)
from terrain.terrain import (DEBUG_TERRAIN, TERRAIN_TILE_WORLD_SIZE,
    _ensure_ext, _parse_simple_script_kv, _expand_polytrn_sectors_med,
    _terrain_quadrant_bounds, _terrain_tile_family,
    TERRAIN_DEBUG_MODES, TERRAIN_PHASE_MODES,
    _terrain_phase_mode_label, _med_fixed32_meter_phase, _terrain_phase_coord,
    _terrain_sector_lookup_raw_index, _terrain_is_medcook_phase,
    _terrain_medcook_visible_ranges, _terrain_medcook_source_y_span,
    _terrain_debug_mode_label, _terrain_debug_compose_image,
    _terrain_debug_report_lines, _parse_trn_spatial_metadata,
    _extract_cpt_heightmap, _build_cpt_chd_images,
    _polytrn_sector_index_med, _terrain_sample_height, _terrain_water_height_world)
from shared.nav_helpers import (NAV_NODE_Z_LIFT, TERRAIN_NODE_SIDE_TOLERANCE,
    GENERATOR_IGNORE_DESTROYABLE, GENERATOR_CLEARABLE_DEATH_PREFIXES,
    ENTITY_INVINCIBLE_FLAG_OFFSET, ENTITY_INVINCIBLE_FLAG_MASK,
    FORCE_COLLISION_GRAPHIC_PREFIXES, FORCE_COLLISION_GRAPHICS,
    _VEHICLE_NAME_WORDS,
    interp_z, nav_node_z, _gen_node_z, build_ground_z_ref,
    _gen_measure_clearance, _item_is_vehicle, _item_is_clearable_prop)
from cmodel.cmodel_geom import (clear_rotation_caches,
    _xy_from_point, _segment_xy_pair, _graphic_name_from_item,
    _make_oct_hull, _segment_bbox_segments, _convex_hull_segments_from_points,
    _foliage_bounds_from_segments, _foliage_hull_segments,
    _cmodel_husk_segments, _cmodel_rotation_cache_key,
    _rotated_segments_for_heading, _rotated_bounds_for_heading)
from config.ui_theme import (C_BG, C_PANEL, C_PANEL2, C_BORDER, C_TEXT, C_DIM, C_LABEL,
    C_GREEN, C_YELLOW, C_ORANGE, C_BLUE, C_RED, C_ACCENT,
    UI_THEME_DARK, UI_THEME_LIGHT, UI_DARK_TO_LIGHT_COLOR_MAP,
    C_CANVAS_BG, C_GRID, C_NODE, C_NODE_SEL, C_NODE_PREC,
    C_NODE_B12_12, C_NODE_B12_5, C_NODE_ZONE,
    C_NODE_B12_2, C_NODE_B12_4, C_NODE_B12_6, C_NODE_B12_7,
    C_NODE_B12_8, C_NODE_B12_9, C_NODE_B12_10, C_NODE_B12_11,
    C_BREACH_ACTION, C_BREACH_TACTICAL, C_B13_COLORS, C_BREACH_ACTIVATION,
    C_EDGE, C_EDGE_SEL, C_EDGE_ONEWAY, C_RADIUS, C_RADIUS_SEL,
    C_ENTITY, C_VEHICLE, C_NEVER_EXCL, ZONE_COLORS)
from rendering.tk_renderers import NodeRenderer, EdgeRenderer, GridRenderer
from generator.clearance import (ADAPTIVE_PALETTE_BASE, ADAPTIVE_PALETTE_LARGE,
    ADAPTIVE_PALETTE_XLARGE, ADAPTIVE_OVERLAP, ADAPTIVE_DIST_STEP,
    ADAPTIVE_DIST_MAX, ADAPTIVE_SEARCH_MULT,
    adaptive_clearance_b15, _build_adaptive_matrix,
    _ADAPTIVE_MATRIX_BASE, _ADAPTIVE_MATRIX_LARGE, _ADAPTIVE_MATRIX_XLARGE,
    _adaptive_target_b15, adaptive_resolve_b15)
from config.win32_menu_policy import _WindowsNativeMenuVisualPolicy
from config.editor_config import (_editor_cfg_path, _load_editor_cfg, _save_editor_cfg,
    _editor_cfg_bool, _configured_additional_pff_dirs, _save_additional_pff_dirs)
from shared.debug_log import (_dbg, QUIET_DEBUG_LOGS,
    DEBUG_RENDER_TIMING, DEBUG_PFF_INDEX, DEBUG_ITEMS_DEF,
    DEBUG_CMODEL_QUEUE, DEBUG_CMODEL_PARSE, DEBUG_CMODEL_FAIL,
    DEBUG_CMODEL_MISSING, DEBUG_CMODEL_DRAW)
from config.render_config import (ENABLE_PFF_CMODEL, CMODEL_DEDUP_EDGES,
    MAX_SEGMENT_LOD_CACHE, MAX_ROTATED_SEGMENT_CACHE, MAX_ROTATED_BOUNDS_CACHE,
    ZOOM_SETTLE_DELAY_MS, ZOOM_FAST_RENDER_DELAY_MS,
    LOD_NODE_HEAVY_COUNT, LOD_HIDE_RADIUS_ZOOM, LOD_HIDE_EDGES_ZOOM,
    LOD_HIDE_ENTITIES_ZOOM, LOD_SIMPLE_NODES_ZOOM,
    CMODEL_PRELOAD_ON_BMS_LOAD, CMODEL_REQUEST_ON_DRAW,
    CMODEL_PRELOAD_CATEGORIES, CMODEL_PRELOAD_MAX_TYPES,
    CMODEL_LAZY_DECORATION_MIN_ZOOM, CMODEL_LAZY_DECORATION_MIN_RPX,
    CMODEL_LAZY_FOLIAGE_MIN_ZOOM, CMODEL_LAZY_FOLIAGE_MIN_RPX,
    AUTO_TRIM_DERIVED_CACHES, AUTO_TRIM_AFTER_SETTLE_MS,
    AUTO_TRIM_ROTATED_TOTAL, AUTO_TRIM_COOLDOWN_MS,
    STATIC_ENTITY_LAYER_CACHE, STATIC_ENTITY_LAYER_PAD_PX,
    STATIC_ENTITY_LAYER_ZOOM_EPS, STATIC_ENTITY_LAYER_MIN_ENTITIES,
    STATIC_ENTITY_LAYER_CLEAR_DERIVED_AFTER_REBUILD,
    ZOOM_BITMAP_PREVIEW, ZOOM_PREVIEW_RESAMPLE, ZOOM_PREVIEW_THROTTLE_MS,
    ZOOM_EXACT_REDRAW_AFTER_SETTLE_MS,
    ZOOM_STATIC_LAYER_PREVIEW, ZOOM_STATIC_LAYER_PREVIEW_FALLBACK_BITMAP,
    CMODEL_DETAIL_MIN_ZOOM, CMODEL_BOX_ZOOM, CMODEL_MAX_DRAW_SEGS,
    CMODEL_WORKER_SLEEP, CMODEL_FULL_DETAIL_ZOOM,
    CMODEL_FULL_DETAIL_BUILDING_ZOOM, CMODEL_FULL_DETAIL_OBJECT_ZOOM,
    CMODEL_FULL_DETAIL_DECORATION_ZOOM, CMODEL_FULL_DETAIL_HEAVY_ZOOM,
    CMODEL_HEAVY_SEG_THRESHOLD, CMODEL_HUSK_SMALL_SEG_LIMIT,
    CMODEL_HUSK_MAX_BOUNDARY_SEGS, CMODEL_HUSK_QUANT,
    CMODEL_SKIP_CATEGORIES, CMODEL_STATIC_CATEGORIES,
    CMODEL_DECORATION_REQUIRE_PHYSICAL, CMODEL_DECORATION_PHYSICAL_KEYS,
    CMODEL_DECORATION_ALLOW_GRAPHIC_PREFIXES, CMODEL_DECORATION_SKIP_ATTRS,
    CMODEL_MAX_REQUESTS_PER_RENDER, CMODEL_MAX_QUEUE_SIZE,
    CMODEL_LOAD_IDLE_DELAY, CMODEL_ENTITY_HIDE_ZOOM, CMODEL_FIRST_PASS_MARGIN_PX,
    ENTITY_RENDER_MODE, STABLE_ENTITY_OUTLINES, FOLIAGE_HULL_ONLY,
    BUILDING_FULL_DETAIL_ZOOM, OBJECT_FULL_DETAIL_ZOOM,
    DECORATION_FULL_DETAIL_ZOOM, VEHICLE_FULL_DETAIL_ZOOM,
    HEAVY_FULL_DETAIL_ZOOM, HEAVY_SEG_THRESHOLD,
    MAX_VISIBLE_LINES_PER_ENTITY,
    SHOW_CONTEXT_ENTITY_MARKERS, CONTEXT_MARKER_SIZE_PX,
    CONTEXT_MARKER_MIN_ZOOM, CONTEXT_MARKER_MAX_ZOOM,
    CMODEL_EXCLUDE_GRAPHIC_PREFIXES,
    CONTEXT_ONLY_GRAPHIC_PREFIXES, CONTEXT_ONLY_GRAPHICS)
from entity.entity_classify import (_graphic_is_context_only, _graphic_is_force_collision,
    classify_item_collision_hint, _item_category_allows_cmodel)
from shared.process_util import (_status_log, _process_memory_mb,
    _trim_large_cache_dict, _process_memory_details)
from entity.entity_data import (NEVER_EXCLUDE, FOLIAGE_TYPE_IDS, FORCE_OBSTACLE,
    ALWAYS_FULL_OUTLINE_TYPES, DEFAULT_INNER, DEFAULT_OUTER,
    RING_RADII, PERIM_OFFSET_OVERRIDES, DEFAULT_RADIUS,
    OBSTACLE_RADII, OWNED_PADDING_BY_TYPE)
from terrain.terrain_water import (_attach_bms_water_level,
    _build_terrain_water_mask, _terrain_water_mask_for_size,
    _terrain_navigable_height)
from resources.asset_resolve import (_terrain_asset_search_roots,
    _resolve_game_asset_bytes)
from terrain.terrain_load import (_load_pil_image_from_bytes,
    _guess_mis_for_bms, _load_terrain_backdrop_from_mis)
from resources.items_def import (_items_def_cache,
    _load_items_def, _get_3di_bytes_for_type)
import cmodel.cmodel_access as cmodel_access
from cmodel.cmodel_access import (get_cmodel_segments_3d,
    get_cmodel_collision_segments_3d, get_cmodel_collision_triangles_3d,
    get_cmodel_walkable_triangles, get_cmodel_transit_triangles,
    get_cmodel_walkable_component_profiles, get_cmodel_segments_3d_grouped,
    _cmodel_type_allowed, _cmodel_category_for_type,
    _3di_cmodel3d_cache, _3di_cmodel_collision_segments3d_cache,
    _3di_cmodel_triangles3d_cache, _3di_walkable_triangle_cache,
    _3di_cmodel_transit_triangle_cache, _3di_cmodel3d_grouped_cache,
    _cmodel_allowed_cache,
    CMODEL_RPX_FULL_BUILDING, CMODEL_RPX_FULL_OBJECT, CMODEL_RPX_FULL_HEAVY,
    CMODEL_RPX_MEDIUM, CMODEL_RPX_MARKER_MIN, CMODEL_MED_CULL_MARGIN_PX,
    _segments_draw_lod_rpx, _is_tree_like_foliage_entity)
from rendering.entity_renderer import EntityRenderer
from rendering.pil_renderer import PILRenderer
from format.bms_parse import parse_bms
from entity.entity_classify import entity_layer_kind, entity_visible_by_layer
import entity.entity_classify as _entity_classify_mod
from rendering.grid_spacing import (GRID_MODE_ADAPTIVE, GRID_MODE_MED_FIXED,
    MED_GRID_SPACING_CHOICES, MED_GRID_SPACING_BY_LABEL,
    _adaptive_grid_spacing_for_zoom, _grid_spacing_for_render)
import rendering.grid_spacing as _grid_spacing_mod
from generator.clearance import _make_clearance_blocked_fn
import resources.pff_archive as _pff_inspect_mod
from resources.pff_archive import _inspect_pff_records, _inspect_all_pffs
from format.bms_parse import _detect_bms_terrain_name
import view3d.wf3d_debug as _wf3d_debug_mod
from view3d.wf3d_debug import (_wf3d_dbg, _wf3d_dbg_clear, _wf3d_clear_rendermesh_cache,
    WF3D_DEBUG_ENABLED, WF3D_DEBUG_TO_CONSOLE, WF3D_DEBUG_MAX_LINES,
    WF3D_DEBUG_LINES, WF3D_DEBUG_LOG_PATH,
    WF3D_RENDER_CACHE_VERSION, WF3D_RENDER_MESH_CACHE_VERSION)


# ── Generator worker/process infrastructure — extracted to generator/process.py ──
from generator.process import (
    _configure_generator_worker_thread,
    _run_generator_process,
    _generator_worker_server_cli,
    _generator_worker_process_cli,
    _PersistentGeneratorProcess,
    _generator_cooperative_yield,
    _GENERATOR_PROCESS_EVENT_PREFIX,
)
import generator.process as _gen_process_mod
_GENERATOR_PROCESS_MANAGER = _gen_process_mod._GENERATOR_PROCESS_MANAGER

# SpatialHash — imported from spatial_index


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# SENTINEL, MAX_NEIGHBORS, EXTRA_COUNT — imported from ain_format

# ── Format / manual-rebuild constants ───────────────────────────────────────
# MAX_NEIGHBORS is the NAI2 format/manual-editing capacity.
# The seed-disc generator internally caps generated graph links at the
# observed campaign generator cap of 10.
GRID_SPACING    = 3.5   # legacy/manual Rebuild Neighbors spacing default
# Nav helpers — imported from nav_helpers

# Nav helpers — imported from nav_helpers

CONNECT_DIST    = GRID_SPACING * 1.6  # manual Rebuild Neighbors link distance

# MED-inspired colours — imported from ui_theme

# UI theme dicts + color remap — imported from ui_theme

# _WindowsNativeMenuVisualPolicy — imported from win32_menu_policy
# Canvas colours + ZONE_COLORS — imported from ui_theme

# ─────────────────────────────────────────────────────────────────────────────
# GENERATOR DATA
# ─────────────────────────────────────────────────────────────────────────────


# Cached outline metadata for faster entity rendering/culling.
_MODEL_OUTLINE_BOUNDS_CACHE = {}
_MODEL_OUTLINE_SIMPLE_CACHE = {}

def _model_outline_bounds(type_id):
    """Return (min_x, min_z, max_x, max_z, radius) for a model outline."""
    cached = _MODEL_OUTLINE_BOUNDS_CACHE.get(type_id)
    if cached is not None:
        return cached
    segs = MODEL_OUTLINES.get(type_id) or []
    if not segs:
        r = OBSTACLE_RADII.get(type_id, DEFAULT_RADIUS)
        cached = (-r, -r, r, r, r)
        _MODEL_OUTLINE_BOUNDS_CACHE[type_id] = cached
        return cached

    xs = []
    zs = []
    for (x1, z1), (x2, z2) in segs:
        xs.extend((x1, x2))
        zs.extend((z1, z2))
    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)
    radius = max(abs(min_x), abs(max_x), abs(min_z), abs(max_z),
                 OBSTACLE_RADII.get(type_id, DEFAULT_RADIUS))
    cached = (min_x, min_z, max_x, max_z, radius)
    _MODEL_OUTLINE_BOUNDS_CACHE[type_id] = cached
    return cached




EXTERNAL_MODEL_OUTLINE_META = {}  # populated by _load_external_model_outline

def _apply_external_model_outline_meta():
    """Merge external outline metadata into the editor registries."""
    for tid, meta in EXTERNAL_MODEL_OUTLINE_META.items():
        if meta.get("force_obstacle", True):
            FORCE_OBSTACLE.add(tid)
        if meta.get("obstacle_radius") is not None:
            OBSTACLE_RADII[tid] = float(meta["obstacle_radius"])
        rr = meta.get("ring_radii")
        if isinstance(rr, (list, tuple)) and len(rr) == 2:
            RING_RADII[tid] = (float(rr[0]), float(rr[1]))
        if meta.get("perim_offset") is not None:
            PERIM_OFFSET_OVERRIDES[tid] = float(meta["perim_offset"])
        if meta.get("owned_padding") is not None:
            OWNED_PADDING_BY_TYPE[tid] = float(meta["owned_padding"])

_apply_external_model_outline_meta()





# Node, SENTINEL, MAX_NEIGHBORS, EXTRA_COUNT — imported from ain_format


# Nav helpers — imported from nav_helpers



# ─────────────────────────────────────────────────────────────────────────────
# PFF + ITEMS.DEF + 3DI CMODEL PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
# This is the new source of truth for static object outlines.  The old hand-made
# TYPE_xxxx_RECTS / MODEL_OUTLINES system is intentionally disabled below.
#
# Pipeline:
#   PFF stack     -> encrypted items.def -> type_id -> graphic name
#   PFF stack     -> graphic.3di        -> GPM/GPS/GPP CModel
#   CModel groups -> group-local int16 vertices + 0x2C faces -> line segments
#
# ── Open BMS load trace ──────────────────────────────────────────────────────
# Removed from v97_50. Keep no-op shims so the load path remains unchanged.
def _bms_load_trace(message):
    return None

def _bms_load_trace_begin(path):
    return None

def _bms_load_trace_end(message='FULLY LOADED'):
    return None

# ── 3D WIREFRAME DEBUGGER — imported from wf3d_debug ───────────────────────

# Nav helpers — imported from nav_helpers

# BHD_SCRIPT_KEY — imported from items_def

_game_path = None
_pff_index_cache = {}       # {pff_path: {filename_lower: (offset, size)}}
_entity_classify_mod._get_game_path = lambda: _game_path
_entity_classify_mod._get_pff_cache = lambda: _pff_index_cache
_3di_cache = {}             # {(game_path, type_id): segments-or-None}
_3di_loading = set()        # keys currently being loaded by background thread
_3di_requested = set()      # keys already queued for lazy background load
_cmodel_queue = []          # [(type_id, game_path)] pending lazy loads
_cmodel_worker_running = False
_segment_bounds_cache = {}  # id(segments) -> (minx,miny,maxx,maxy,radius)
# _segment_lod_cache — now in cmodel_geom
# _rotated_segment_cache — now in cmodel_geom
# _rotated_bounds_cache — now in cmodel_geom

# _cmodel_rotation_cache_key — imported from cmodel_geom

_cmodel_pause_until = 0.0     # pan/zoom debounce; don't queue/load while view is moving

cmodel_access._get_default_game_path = lambda: _game_path
cmodel_access._get_pff_cache = lambda: _pff_index_cache
cmodel_access._get_foliage_trees_only = lambda: globals().get('FOLIAGE_TREES_ONLY', True)
_pff_inspect_mod._get_game_path = lambda: _game_path
_pff_inspect_mod._get_pff_cache = lambda: _pff_index_cache






def _safe_len(obj):
    try:
        return len(obj)
    except Exception:
        return 0


def _image_raw_mb(img):
    try:
        w, h = img.size
        mode = getattr(img, 'mode', '') or ''
        bands = len(img.getbands()) if hasattr(img, 'getbands') else (4 if mode in ('RGBA', 'RGBX') else 3 if mode == 'RGB' else 1)
        return (int(w) * int(h) * int(bands)) / (1024.0 * 1024.0), f"{w}x{h} {mode}"
    except Exception:
        return None, "present"


def _segments_raw_coord_mb(segs):
    """Approximate raw coordinate payload only, not full Python object overhead."""
    try:
        return (len(segs) * 4 * 8) / (1024.0 * 1024.0)
    except Exception:
        return 0.0


def _audit_dict_cache(name, obj):
    info = {'name': name, 'count': _safe_len(obj)}
    try:
        info['shallow_mb'] = sys.getsizeof(obj) / (1024.0 * 1024.0)
    except Exception:
        pass
    cap_name = None
    if name == '_segment_lod_cache':
        cap_name = 'MAX_SEGMENT_LOD_CACHE'
    elif name == '_rotated_segment_cache':
        cap_name = 'MAX_ROTATED_SEGMENT_CACHE'
    elif name == '_rotated_bounds_cache':
        cap_name = 'MAX_ROTATED_BOUNDS_CACHE'
    if cap_name:
        info['cap'] = globals().get(cap_name, '?')
    return info


def _entity_audit_snapshot(entities, zoom=1.0, game_path=None):
    """Best-effort, bounded scan of entities/cmodel caches for memory/perf diagnosis.

    This does not request or load any new models. It only reads existing entity and
    cache state so the report itself should not change memory behavior.
    """
    import collections
    entities = list(entities or [])
    cat_counts = collections.Counter()
    static_counts = collections.Counter()
    type_counts = collections.Counter()
    loaded_type_counts = collections.Counter()
    missing_loaded_types = 0
    total_loaded_lines_seen = 0
    med_buckets = {
        '>150px': {'entities': 0, 'loaded_entities': 0, 'lines': 0},
        '80-150px': {'entities': 0, 'loaded_entities': 0, 'lines': 0},
        '<=80px': {'entities': 0, 'loaded_entities': 0, 'lines': 0},
        'unknown': {'entities': 0, 'loaded_entities': 0, 'lines': 0},
    }
    category_loaded_lines = collections.Counter()
    loaded_type_line_instances = collections.Counter()
    gp = str(game_path or globals().get('_game_path') or '').lower()

    cache = globals().get('_3di_cache', {}) or {}
    per_type_seg_count = {}
    per_type_raw_mb = {}
    for key, segs in list(cache.items()):
        try:
            if isinstance(key, tuple):
                kgp = str(key[0]).lower() if len(key) > 1 else ''
                tid = int(key[-1])
                if gp and kgp and kgp != gp:
                    continue
            else:
                tid = int(key)
        except Exception:
            continue
        if segs:
            try:
                per_type_seg_count[tid] = len(segs)
                per_type_raw_mb[tid] = _segments_raw_coord_mb(segs)
            except Exception:
                pass

    for e in entities:
        try:
            tid = int(e.get('type_id', -1))
        except Exception:
            tid = -1
        cat = str(e.get('render_kind') or e.get('category') or '').lower()
        if not cat:
            try:
                cat = str(_cmodel_category_for_type(tid, game_path=game_path) or '').lower()
            except Exception:
                cat = ''
        if not cat:
            cat = 'unknown'
        cat_counts[cat] += 1
        type_counts[tid] += 1
        if e.get('is_static'):
            static_counts[cat] += 1

        seg_count = per_type_seg_count.get(tid, 0)
        if seg_count:
            loaded_type_counts[tid] += 1
            total_loaded_lines_seen += seg_count
            category_loaded_lines[cat] += seg_count
            loaded_type_line_instances[tid] += seg_count
        else:
            missing_loaded_types += 1

        # MED-style projected radius estimate. We mirror the official draw-stack
        # idea: choose LOD bucket from projected model radius. This estimate uses
        # cached model bounds when present, otherwise known obstacle/default radius.
        try:
            if seg_count:
                b = _segments_bounds(cache.get((gp, tid)) or next((v for k, v in cache.items() if isinstance(k, tuple) and int(k[-1]) == tid and v), None) or [])
                radius = float(b[4]) if b and len(b) > 4 else float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
            else:
                radius = float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
            rpx = radius * float(zoom or 1.0)
            if rpx > 150.0:
                bucket = '>150px'
            elif rpx > 80.0:
                bucket = '80-150px'
            else:
                bucket = '<=80px'
        except Exception:
            bucket = 'unknown'
        med_buckets[bucket]['entities'] += 1
        if seg_count:
            med_buckets[bucket]['loaded_entities'] += 1
            med_buckets[bucket]['lines'] += seg_count

    top_loaded_types = []
    for tid, count in loaded_type_line_instances.most_common(20):
        top_loaded_types.append((tid, type_counts.get(tid, 0), per_type_seg_count.get(tid, 0), count, per_type_raw_mb.get(tid, 0.0)))

    return {
        'entity_count': len(entities),
        'category_counts': cat_counts,
        'static_counts': static_counts,
        'type_counts': type_counts,
        'unique_type_count': len(type_counts),
        'loaded_unique_type_count': len(per_type_seg_count),
        'loaded_type_instance_count': sum(loaded_type_counts.values()),
        'missing_loaded_type_instances': missing_loaded_types,
        'total_loaded_line_instances': total_loaded_lines_seen,
        'category_loaded_lines': category_loaded_lines,
        'top_loaded_types': top_loaded_types,
        'med_buckets': med_buckets,
        'cached_type_segments': per_type_seg_count,
        'cached_type_raw_mb': per_type_raw_mb,
    }


def _gc_type_snapshot(limit=12, max_objects=250000):
    """Small GC summary for leak hunting. Bounded so Memory Report remains usable."""
    try:
        import gc, collections
        objs = gc.get_objects()
        if len(objs) > max_objects:
            sample = objs[:max_objects]
            truncated = True
        else:
            sample = objs
            truncated = False
        c = collections.Counter(type(o).__name__ for o in sample)
        return {'total_tracked': len(objs), 'sampled': len(sample), 'truncated': truncated, 'top': c.most_common(limit), 'counts': gc.get_count()}
    except Exception:
        return None

def _enforce_render_cache_caps():
    """Keep derived render caches bounded during normal use."""
    try:
        _trim_large_cache_dict(globals().get('_segment_lod_cache', {}), globals().get('MAX_SEGMENT_LOD_CACHE', 64))
        _trim_large_cache_dict(globals().get('_rotated_segment_cache', {}), globals().get('MAX_ROTATED_SEGMENT_CACHE', 160))
        _trim_large_cache_dict(globals().get('_rotated_bounds_cache', {}), globals().get('MAX_ROTATED_BOUNDS_CACHE', 160))
    except Exception:
        pass



# _editor_cfg_path — imported from editor_config

# _load_editor_cfg — imported from editor_config

# _save_editor_cfg — imported from editor_config

# _editor_cfg_bool — imported from editor_config

# _configured_additional_pff_dirs — imported from editor_config

# _save_additional_pff_dirs — imported from editor_config

def _init_game_path(bms_path=None):
    """Find game folder from config or by walking upward from the BMS path."""
    global _game_path
    if _game_path and Path(_game_path).is_dir():
        return Path(_game_path)

    try:
        p = Path(_load_editor_cfg().get('game_path', ''))
        has_pff_dir = bool(list((p / 'pff').glob('*.pff'))) if (p / 'pff').is_dir() else False
        if p.is_dir() and (any((p / n).exists() for n in ('resource.pff', 'localres.pff')) or bool(list(p.glob('*.pff'))) or has_pff_dir):
            _game_path = p
            return p
    except Exception:
        pass

    if bms_path:
        p = Path(bms_path).resolve().parent
        for candidate in (p, p.parent, p.parent.parent):
            has_pff_dir = bool(list((candidate / 'pff').glob('*.pff'))) if candidate and (candidate / 'pff').is_dir() else False
            if candidate and candidate.is_dir() and (any((candidate / n).exists() for n in ('resource.pff', 'localres.pff')) or bool(list(candidate.glob('*.pff'))) or has_pff_dir):
                _game_path = candidate
                _save_game_path(candidate)
                return candidate
    return None


def _save_game_path(path):
    global _game_path
    _game_path = Path(path)
    try:
        cfg = _load_editor_cfg()
        cfg['game_path'] = str(_game_path)
        _save_editor_cfg(cfg)
    except Exception:
        pass


# _inspect_pff_records, _inspect_all_pffs — imported from pff_inspect

# _detect_bms_terrain_name — imported from bms_detect




# _rol32, _descr_crypt, _decrypt_scr, _parse_items_def_text — imported from items_def


# _load_items_def, _get_3di_bytes_for_type — imported from items_load


# ── CModel pure parsers extracted to cmodel_parse.py ─────────────────────


# CModel 3D getters extracted to cmodel_access.py


# CModel RPX constants + draw LOD + foliage heuristic — imported from cmodel_access

# _make_oct_hull — imported from cmodel_geom


# _rotated_segments_for_heading — imported from cmodel_geom


# _rotated_bounds_for_heading — imported from cmodel_geom

def get_cmodel_segments(type_id, game_path=None, allow_load=False, log=None):
    """Return cached CModel line segments for a type.  No sync load unless allowed."""
    if not ENABLE_PFF_CMODEL:
        return None
    gp = Path(game_path) if game_path else (_game_path if _game_path else None)
    if not gp:
        return None
    if not _cmodel_type_allowed(type_id, gp, log=log):
        return None
    cache_key = (str(gp).lower(), int(type_id))
    if cache_key in _3di_cache:
        return _3di_cache[cache_key]
    if not allow_load:
        return None
    raw, item = _get_3di_bytes_for_type(type_id, gp, log=log, pff_cache=_pff_index_cache)
    if not raw:
        _3di_cache[cache_key] = None
        return None
    graphic = item.get('graphic') if item else str(type_id)
    segs = _parse_gpm_cmodel(raw, debug_name=f'{type_id}/{graphic}',
                             dbg=_dbg, dedup_edges=CMODEL_DEDUP_EDGES)
    _3di_cache[cache_key] = segs
    return segs


def _start_cmodel_worker(log=None):
    """Start the single lazy CModel worker if needed."""
    global _cmodel_worker_running
    if _cmodel_worker_running:
        return
    _cmodel_worker_running = True

    def worker():
        global _cmodel_worker_running
        try:
            while True:
                # Keep loading independent of zoom/pan.  The worker sleeps after each
                # model so it yields to the UI, but it must not wait for zoom-in;
                # otherwise models appear only after the user changes viewport.
                try:
                    tid, gp = _cmodel_queue.pop(0)
                    _dbg('CMODEL_QUEUE', f'[3DI] worker loading type {tid}; remaining={len(_cmodel_queue)}', once_key=('worker_load', str(gp).lower(), int(tid)))
                except IndexError:
                    break
                key = (str(gp).lower(), int(tid))
                if key in _3di_cache:
                    time.sleep(CMODEL_WORKER_SLEEP)
                    continue
                _3di_loading.add(key)
                try:
                    get_cmodel_segments(tid, gp, allow_load=True, log=log)
                except Exception as e:
                    _dbg('CMODEL_QUEUE', f'[3DI] lazy load error {tid}: {e}', once_key=('lazy_error', tid))
                    _3di_cache[key] = None
                finally:
                    _3di_loading.discard(key)
                time.sleep(CMODEL_WORKER_SLEEP)
        finally:
            _cmodel_worker_running = False

    threading.Thread(target=worker, name='LazyCModelWorker', daemon=True).start()


def _cmodel_should_lazy_request_entity(entity, zoom, rpx):
    """Return True if an unloaded entity should queue CModel parsing now.

    This is a memory policy, not a geometry policy. Loaded models still draw
    exactly as before. Unloaded decorations/foliage wait until close enough so
    BMS open does not slowly accumulate every static prop into _3di_cache.
    """
    try:
        if not entity or not entity.get('is_static'):
            return False
        tid = int(entity.get('type_id', -1))
        cat = str(entity.get('category') or _cmodel_category_for_type(tid)).lower()
        if cat in {'building', 'decoration', 'object', 'vehicle'}:
            return True
        if cat == 'foliage':
            if not bool(globals().get('FOLIAGE_HULL_ONLY', True)) and not bool(globals().get('SHOW_FOLIAGE', False)):
                return False
            return (float(zoom) >= float(globals().get('CMODEL_LAZY_FOLIAGE_MIN_ZOOM', 12.0)) or
                    float(rpx) >= float(globals().get('CMODEL_LAZY_FOLIAGE_MIN_RPX', 120.0)))
        return False
    except Exception:
        return False


def request_cmodel_segments(type_id, game_path=None, bms_path=None, log=None):
    """Queue a type for background CModel parsing. Never blocks the renderer."""
    if not ENABLE_PFF_CMODEL or not CMODEL_REQUEST_ON_DRAW:
        return
    gp = Path(game_path) if game_path else _init_game_path(bms_path)
    if not gp:
        return
    if not _cmodel_type_allowed(type_id, gp, log=log):
        return
    key = (str(gp).lower(), int(type_id))
    if key in _3di_cache or key in _3di_requested or key in _3di_loading:
        return
    max_q = int(globals().get('CMODEL_MAX_QUEUE_SIZE', 12))
    if len(_cmodel_queue) >= max_q:
        return
    _3di_requested.add(key)
    _cmodel_queue.append((int(type_id), gp))
    _dbg('CMODEL_QUEUE', f'[3DI] queued type {int(type_id)}; queue={len(_cmodel_queue)}', once_key=('queued', str(gp).lower(), int(type_id)))
    _start_cmodel_worker(log=log)


# _segment_bbox_segments — imported from cmodel_geom


# _convex_hull_segments_from_points — imported from cmodel_geom


# _cmodel_husk_segments — imported from cmodel_geom


def preload_cmodels_for_entities(entities, bms_path=None, log=None, on_done=None,
                                 on_progress=None, categories=None, max_types=None,
                                 worker_sleep=None, thread_name='CModelPreload'):
    """Background warm preload of CModels for static map entities.
    This is intentionally independent of zoom so models do not appear only after zooming in.
    """
    _bms_load_trace(
        f'preload_cmodels: enter entities={len(entities or [])} '
        f'enabled={CMODEL_PRELOAD_ON_BMS_LOAD} pff_cmodel={ENABLE_PFF_CMODEL}')
    if not CMODEL_PRELOAD_ON_BMS_LOAD:
        _bms_load_trace('preload_cmodels: skipped because CMODEL_PRELOAD_ON_BMS_LOAD is false')
        return None
    if not ENABLE_PFF_CMODEL or not entities:
        _bms_load_trace(
            f'preload_cmodels: skipped because ENABLE_PFF_CMODEL={ENABLE_PFF_CMODEL} '
            f'or entity list is empty')
        return None
    _gp_pre_t0 = time.perf_counter()
    gp = _init_game_path(bms_path)
    _bms_load_trace(
        f'preload_cmodels: game path={gp!s} resolved in {time.perf_counter() - _gp_pre_t0:.3f}s')
    if not gp:
        _status_log(log, '[3DI] game folder not found; use config or open BMS from game tree')
        _bms_load_trace('preload_cmodels: skipped because game folder was not resolved')
        return None

    def _preload_priority(tid):
        try:
            item = _load_items_def(gp, log=None, pff_cache=_pff_index_cache).get(int(tid), {})
            cat = (item.get('category') or '').lower()
            return ({'building': 0, 'vehicle': 1, 'decoration': 2, 'object': 3}.get(cat, 4), int(tid))
        except Exception:
            return (9, int(tid))

    # Preload only structural collision categories by default.
    # Decorations were the top loaded line contributor in the audit. They remain
    # exact when loaded, but now load lazily at close zoom/large projected size.
    preload_categories = set(
        categories if categories is not None
        else globals().get('CMODEL_PRELOAD_CATEGORIES', {'building', 'object'}))
    type_ids = sorted({
        int(e.get('type_id', -1))
        for e in entities
        if e.get('is_static') and str(e.get('category') or _cmodel_category_for_type(int(e.get('type_id', -1)))).lower() in preload_categories
    }, key=_preload_priority)
    max_preload = int(
        globals().get('CMODEL_PRELOAD_MAX_TYPES', 48)
        if max_types is None else max_types)
    _bms_load_trace(
        f'preload_cmodels: candidate structural type IDs={len(type_ids)} '
        f'categories={sorted(preload_categories)} max_types={max_preload}')
    if max_preload > 0 and len(type_ids) > max_preload:
        _bms_load_trace(
            f'preload_cmodels: truncating candidates {len(type_ids)} -> {max_preload}')
        type_ids = type_ids[:max_preload]
    if not type_ids:
        _bms_load_trace('preload_cmodels: no preloadable structural type IDs; no worker created')
        return None

    def worker():
        loaded = 0
        tried = 0
        cached = 0
        worker_t0 = time.perf_counter()
        _bms_load_trace(f'preload worker: BEGIN; {len(type_ids)} type IDs queued')
        try:
            _items_worker_t0 = time.perf_counter()
            _load_items_def(gp, log=log, pff_cache=_pff_index_cache)
            _bms_load_trace(
                f'preload worker: items.def/cache warm-up took '
                f'{time.perf_counter() - _items_worker_t0:.3f}s')
            for index, tid in enumerate(type_ids, 1):
                key = (str(gp).lower(), tid)
                if key in _3di_cache:
                    cached += 1
                    _bms_load_trace(
                        f'preload worker: [{index}/{len(type_ids)}] tid={tid} cache HIT')
                    continue
                tried += 1
                _type_t0 = time.perf_counter()
                _bms_load_trace(
                    f'preload worker: [{index}/{len(type_ids)}] tid={tid} parse BEGIN')
                _3di_loading.add(key)
                _ok = False
                try:
                    _ok = bool(get_cmodel_segments(tid, gp, allow_load=True, log=log))
                    if _ok:
                        loaded += 1
                        if on_progress and loaded % 25 == 0:
                            try:
                                on_progress()
                            except Exception:
                                pass
                finally:
                    _3di_loading.discard(key)
                _bms_load_trace(
                    f'preload worker: [{index}/{len(type_ids)}] tid={tid} parse END '
                    f'ok={_ok} dt={time.perf_counter() - _type_t0:.3f}s')
                # Normal map-open warming yields between types. An explicit
                # layer activation can request zero delay because it already
                # runs off the Tk thread and should become visible as one set.
                delay = (CMODEL_WORKER_SLEEP if worker_sleep is None
                         else max(0.0, float(worker_sleep)))
                if delay:
                    time.sleep(delay)
            _status_log(log, f'[3DI] CModel preload complete: {loaded}/{tried} parsed')
            _bms_load_trace(
                f'preload worker: COMPLETE parsed={loaded}/{tried} cached={cached} '
                f'total={time.perf_counter() - worker_t0:.3f}s')
        finally:
            if on_done:
                try:
                    _bms_load_trace('preload worker: invoking on_done callback')
                    on_done()
                except Exception as _done_exc:
                    _bms_load_trace(f'preload worker: on_done callback raised {_done_exc!r}')

    t = threading.Thread(target=worker, name=str(thread_name), daemon=True)
    t.start()
    _bms_load_trace(f'preload_cmodels: worker thread started name={t.name}')
    return t


def _segments_bounds(segs):
    if not segs:
        return (0, 0, 0, 0, 1.0)
    key = id(segs)
    b = _segment_bounds_cache.get(key)
    if b:
        return b
    xs, ys = [], []
    for (a, b) in segs:
        xs.extend((a[0], b[0]))
        ys.extend((a[1], b[1]))
    mnx, mxx = min(xs), max(xs)
    mny, mxy = min(ys), max(ys)
    r = max(abs(mnx), abs(mxx), abs(mny), abs(mxy), 1.0)
    out = (mnx, mny, mxx, mxy, r)
    _segment_bounds_cache[key] = out
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CLEAN SLATE: OLD HAND-TRACED COLLISION/TEMPLATE GEOMETRY REMOVED
# ─────────────────────────────────────────────────────────────────────────────
# The old editor carried hundreds of manually traced TYPE_xxxx_RECTS/SEGS/CIRCLES
# and MODEL_OUTLINES/TRUSTED_RECT_TYPES fallbacks. They are intentionally disabled
# here so no previous-session geometry can mask the new PFF/3DI pipeline work.
# Static BMS entities may still be visible as simple radius markers, but no
# hand-made building/decor collision templates are used for drawing or generation.
OLD_MANUAL_GEOMETRY_REMOVED = True

# Old hardcoded TYPE_ID_TO_GRAPHIC map was intentionally removed.
# Keep an empty alias so any remaining fallback references do not crash.
TYPE_ID_TO_GRAPHIC = globals().get('TYPE_ID_TO_GRAPHIC', {})

for _name in list(globals()):
    if _name.startswith('TYPE_') and (
        _name.endswith('_RECTS') or
        _name.endswith('_SEGS') or
        _name.endswith('_CIRCLES') or
        _name.endswith('_DECOR_CIRCLES') or
        _name.endswith('_DECOR_SEGS') or
        _name.endswith('_RUBBLE_RECTS') or
        _name.endswith('_BARRELS')
    ):
        globals()[_name] = []

MODEL_OUTLINES = {}
TRUSTED_RECT_TYPES = {}
TRUSTED_CIRCLE_TYPES = {}
DECOR_CIRCLE_TYPES = {}
DECOR_SEGMENT_TYPES = {}
RUBBLE_RECT_TYPES = {}
EXTERNAL_MODEL_OUTLINE_FILES = {}
EXTERNAL_MODEL_OUTLINE_META = {}

# Nav helpers — imported from nav_helpers




# ─────────────────────────────────────────────────────────────────────────────
# 3DI / CModel / AIN I/O helpers


# ── 3DI model helpers extracted to wf3d_view.py ─────────────────────────────


# write_ain, read_ain, sanitize_node_graph — imported from ain_format


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — PERSISTENT NODE RENDERING
# Viewport, HitTester, world coord helpers — imported from viewport
# NodeRenderer — imported from tk_renderers
# EdgeRenderer — imported from tk_renderers

# GridRenderer — imported from tk_renderers

# EntityRenderer extracted to entity_renderer.py

# PIL RENDERER — replaces NodeRenderer, EdgeRenderer, GridRenderer, EntityRenderer
# Draws everything into a single PIL bitmap, blits as one canvas image.
# Architecture matches MED editor: DIB back-buffer, full redraw each frame.
# ─────────────────────────────────────────────────────────────────────────────

try:
    from PIL import Image, ImageDraw, ImageFont
    from PIL import ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


# Colour constants pre-parsed as RGB tuples for PIL
def _hex(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

# Grid modes, spacing choices, _grid_spacing_for_render — imported from grid_spacing


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────


# ── 3D wireframe view extracted to wf3d_view.py ──────────────────────────────
import view3d.wf3d_view as _wf3d_view_mod
from view3d.wf3d_view import Wireframe3DView, Wireframe3DWindow, _get_ref_model_geometry, _get_cmodel_raw
_wf3d_view_mod._get_game_path = lambda: _game_path
_wf3d_view_mod._get_pff_cache = lambda: _pff_index_cache
_wf3d_view_mod._get_3di_cache = lambda: _3di_cache
_wf3d_view_mod._get_cmodel_segments_fn = get_cmodel_segments


# GENERATOR_CORE_START
# Generator core extracted to generator_core.py
from generator.core import (generate_seed_disc_campaign_v58,
    _build_generator_reachable_surface, _build_generator_terrain_surface_contract)
# GENERATOR_CORE_END

# ── AINEditor class — collapsed into app.py ──────────────────────────────────
from app import AINEditor


if __name__ == '__main__':
    if (len(sys.argv) >= 2
            and sys.argv[1] == '--generator-worker-server'):
        raise SystemExit(_generator_worker_server_cli())
    if (len(sys.argv) >= 4
            and sys.argv[1] == '--generator-worker-process'):
        raise SystemExit(_generator_worker_process_cli(sys.argv[2], sys.argv[3]))
    app = AINEditor()
    app.mainloop()
