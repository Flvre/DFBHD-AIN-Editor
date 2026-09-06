# Standard library
import copy
import ctypes
import gc
import json
import math
import os
import platform
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

# Tkinter
import tkinter as tk
from tkinter import colorchooser, filedialog, font, messagebox, ttk

# PIL
from PIL import Image, ImageDraw

# Runtime namespace adapter
import runtime_namespace as _ns
from ui import generator_progress as _generator_progress_ui
from ui import events as _ui_events
from ui import generator_dialog as _generator_dialog_ui
from ui import dialogs as _ui_dialogs
from ui import panels as _ui_panels
from ui import breach_panel as _ui_breach_panel
from ui import theme as _ui_theme

# Project modules
from generator.clearance import _adaptive_target_b15, adaptive_resolve_b15
from format.ain_format import Node, MAX_NEIGHBORS, connect_nodes, read_ain, write_ain, sanitize_node_graph
from format.bms_parse import _detect_bms_terrain_name
from format.bms_parse import parse_bms
from generator.clearance import _make_clearance_blocked_fn
from cmodel.cmodel_access import (get_cmodel_collision_segments_3d,
    get_cmodel_collision_triangles_3d, get_cmodel_walkable_triangles,
    get_cmodel_transit_triangles, get_cmodel_walkable_component_profiles,
    _is_tree_like_foliage_entity)
from cmodel.cmodel_geom import _xy_from_point, clear_rotation_caches
from entity.entity_classify import classify_item_collision_hint
from shared.debug_log import DEBUG_RENDER_TIMING, QUIET_DEBUG_LOGS
from config.editor_config import (_configured_additional_pff_dirs,
    _save_additional_pff_dirs, _load_editor_cfg, _save_editor_cfg,
    _editor_cfg_bool)
from config.win32_menu_policy import _WindowsNativeMenuVisualPolicy
from entity.entity_classify import entity_layer_kind, entity_visible_by_layer
from entity.entity_data import DEFAULT_RADIUS, NEVER_EXCLUDE, OBSTACLE_RADII
from generator.core import (_build_generator_reachable_surface,
    _build_generator_terrain_surface_contract, generate_seed_disc_campaign_v58)
from generator.support import (GENERATOR_CORRIDOR_CENTER_FLOOR,
    GENERATOR_CORRIDOR_DOMINANCE, GENERATOR_CORRIDOR_FINAL_FACTOR,
    GENERATOR_CORRIDOR_FIT, GENERATOR_CORRIDOR_SLIDE_BUDGET,
    GENERATOR_QUANTIZED_BASE_PITCH_M, GENERATOR_QUANTIZED_CENTER_CLEARANCE,
    GENERATOR_QUANTIZED_GRID_M, GENERATOR_REACHABLE_MAX_STEP,
    GENERATOR_REACHABLE_STAIR_STEP, GENERATOR_STANDING_BODY_HI,
    GENERATOR_STANDING_BODY_LO, LabNode, Point, Segment, SolidMask,
    _GeneratorStandingVolumeIndex, _V52Spatial,
    _crop_generator_nodes_for_job, _generator_node_record,
    _generator_point_in_polygon, radius_m)
import rendering.grid_spacing as _grid_spacing_mod
from rendering.grid_spacing import (GRID_MODE_MED_FIXED, MED_GRID_SPACING_BY_LABEL,
    MED_GRID_SPACING_CHOICES, _grid_spacing_for_render)
from resources.items_def import _load_items_def
from shared.nav_helpers import (GENERATOR_IGNORE_DESTROYABLE, NAV_NODE_Z_LIFT,
    TERRAIN_NODE_SIDE_TOLERANCE, _gen_node_z, _item_is_clearable_prop,
    build_ground_z_ref, interp_z, nav_node_z)
from resources.pff_archive import _iter_pff_archive_records
from rendering.pil_renderer import PILRenderer, _hex
from shared.process_util import _trim_large_cache_dict
from shared.los import (build_world_collision_segments, build_segment_grid,
    segment_blocked_3d)
from config.render_config import (AUTO_TRIM_AFTER_SETTLE_MS, AUTO_TRIM_COOLDOWN_MS,
    AUTO_TRIM_DERIVED_CACHES, AUTO_TRIM_ROTATED_TOTAL,
    CMODEL_LOAD_IDLE_DELAY, ZOOM_EXACT_REDRAW_AFTER_SETTLE_MS,
    ZOOM_FAST_RENDER_DELAY_MS, ZOOM_SETTLE_DELAY_MS)
from terrain.terrain import (TERRAIN_DEBUG_MODES, _polytrn_sector_index_med,
    _terrain_debug_mode_label, _terrain_debug_report_lines,
    _terrain_phase_coord, _terrain_phase_mode_label,
    _terrain_sample_height, _terrain_sector_lookup_raw_index,
    _terrain_tile_family, _terrain_water_height_world)
from terrain.terrain_load import _guess_mis_for_bms, _load_terrain_backdrop_from_mis
from terrain.terrain_water import _attach_bms_water_level
from config.ui_theme import (C_ACCENT, C_BG, C_BLUE, C_BORDER, C_CANVAS_BG,
    C_DIM, C_ENTITY, C_GREEN, C_GRID, C_LABEL, C_NEVER_EXCL,
    C_PANEL, C_PANEL2, C_TEXT, C_VEHICLE, C_YELLOW,
    UI_DARK_TO_LIGHT_COLOR_MAP, UI_THEME_DARK, UI_THEME_LIGHT, ZONE_COLORS)
from rendering.viewport import HitTester, Viewport, _world_to_screen_x, _world_to_screen_y
import view3d.wf3d_debug as _wf3d_debug_mod
import view3d.wf3d_view as _wf3d_view_mod
from view3d.wf3d_debug import (WF3D_DEBUG_LOG_PATH, _wf3d_clear_rendermesh_cache,
    _wf3d_dbg, _wf3d_dbg_clear)
from view3d.wf3d_view import Wireframe3DView, Wireframe3DWindow
import generator.geometry as _gen_geom

# Module-level helpers
EDITOR_TITLE = "AIN Editor"
_BMS_LOAD_TRACE_ENABLED = False
def _bms_load_trace(message):
    return None
def _bms_load_trace_begin(path):
    return None
def _bms_load_trace_end(message='FULLY LOADED'):
    return None
_PFF_CACHE_NAMES = (
    "_3di_cache", "_pff_index_cache", "_pff_raw_cache",
    "_items_def_cache", "_cmodel_cache", "_cmodel_allowed_cache",
    "_3di_cmodel3d_cache", "_3di_cmodel_collision_segments3d_cache",
    "_3di_cmodel_triangles3d_cache", "_3di_walkable_triangle_cache",
    "_3di_cmodel_transit_triangle_cache",
    "_3di_cmodel3d_grouped_cache",
    "_segment_lod_cache", "_rotated_segment_cache",
    "_rotated_bounds_cache", "_3di_requested",
    "_3di_loading", "_cmodel_queue",
)
def _set_cmodel_pause_until(value):
    _ns.set_val('_cmodel_pause_until', value)


class AINEditor(tk.Tk):

    def __init__(self):
        super().__init__()
        self.report_callback_exception = self._report_callback_exception
        self.title(EDITOR_TITLE)
        icon_path = Path(__file__).resolve().parent / 'assets' / 'dfbhdain.ico'
        if icon_path.is_file():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self.configure(bg=C_BG)
        self.geometry("1280x800")
        self.state('zoomed')
        self.minsize(900,600)

        # State
        self.nodes = []          # list of Node
        self.entities = []       # raw BMS entities
        self.zones = {}
        self.zone_meta = {}
        self._zone_drawing = None
        self._zone_selected = None
        self._zone_drag = None
        self._zone_panel_visible = tk.BooleanVar(value=True)
        self._zone_point_drag = None
        self._tile_select_active = False
        self._tile_selected = set()
        self._tile_select_prev_grid = None
        self._tile_painting = False
        self._tile_erasing = False
        self.terrain_z = 18.5
        self.terrain_info = None
        self.terrain_image = None
        self.terrain_mis_path = ''
        self.terrain_debug_mode = 'current'
        self.terrain_phase_mode = 'medcook_yneg'
        self.terrain_overlay_enabled = False
        self.bms_path = ''
        self.project_path = ''
        self._autosave_enabled = tk.BooleanVar(
            value=_editor_cfg_bool('autosave_json', False))
        self._autosave_interval_ms = 5 * 60 * 1000
        self._autosave_timer = None
        self.selected_id = None
        self.selected_nodes = set()
        self._locked_3d_node_set_active = False
        self._locked_3d_node_ids = set()
        self._locked_3d_anchor_node_ids = set()
        self._locked_3d_node_scope_note = ''
        self._locked_3d_node_reset_reason = ''

        self._ain_pass_overlay = None
        self._ain_pass_mode = None
        self._cleanup_hud = None
        self._cleanup_ratio_var = tk.StringVar(value='30')
        self._cleanup_tiny_var = tk.StringVar(value='10')
        self._scan_hud = None
        self._scan_b12_var = tk.BooleanVar(value=False)
        self._scan_b12_value_var = tk.StringVar(value='4')
        self._scan_b12_exact_var = tk.BooleanVar(value=False)
        self._scan_b12_additional_values_var = tk.StringVar(value='')
        self._scan_b12_additional_exact_var = tk.BooleanVar(value=False)
        self._scan_b12_replace_even_var = tk.BooleanVar(value=False)
        self._scan_b12_replace_odd_var = tk.BooleanVar(value=False)
        self._scan_b16_var = tk.BooleanVar(value=False)
        self._scan_radius_var = tk.StringVar(value='50')
        self._scan_percentage_var = tk.IntVar(value=50)
        self._scan_seed_armed = False
        self._scan_seed_radius = 200.0

        self.mode = tk.StringVar(value='edit')
        self._draw_last_wx   = None
        self._draw_last_wy   = None
        self._draw_min_dist  = 0.5
        self._draw_spacing   = tk.DoubleVar(value=1.5)
        self._draw_overlap   = tk.DoubleVar(value=1.5)
        self._draw_radius_brush = tk.BooleanVar(value=False)
        self._draw_last_b15  = None
        self._cut_stroke     = []
        self._cut_active     = False
        self._edge_brush_stroke = []
        self._edge_brush_hits = []
        self._edge_brush_active = False
        self.next_id = 0

        # Seed-disc generator state
        self._seed_generate_armed = False
        self._seed_generate_radius = 32.0
        self._seed_generate_z_filter = False
        self._seed_generate_rooftops = False
        self._seed_generate_highest_rooftops = False
        self._seed_generate_ladders = False
        self._seed_generate_quantized = False
        self._previous_generator_seeds = []
        self._previous_generator_seeds_window = None
        self._previous_generator_seeds_listbox = None
        self._previous_generator_seeds_retry_button = None
        self._seed_ignore_destroyable = True
        self._seed_ignore_vehicles = False
        self._shared_generator_geometry_cache = None
        self._generator_support_triangles = []
        self._generator_support_triangle_metadata = []
        self._generator_collision_segments_3d = []
        self._generator_collision_triangles_3d = []
        self._generator_worker = None
        self._generator_progress_queue = None
        self._generator_running = False
        self._generator_progress_window = None
        self._generator_progress_bar = None
        self._generator_progress_phase_var = None
        self._generator_progress_detail_var = None
        self._generator_progress_elapsed_var = None
        self._generator_progress_history = []
        self._generator_progress_started_at = None
        self._generator_progress_finished_at = None
        self._generator_progress_phase_started_at = None
        self._generator_progress_phase_name = None
        self._generator_progress_phase_detail = ''
        self._generator_progress_percent = 0.0
        self._generator_progress_seed_text = ''
        self._generator_progress_report_text = ''
        self._generator_progress_report_drawn_text = None
        self._generator_progress_report_last_draw = 0.0
        self._generator_progress_report_box = None
        self._generator_progress_copy_button = None
        self._generator_progress_close_button = None
        self._generator_progress_timer_job = None

        # Viewport state
        self.vp_offset_x = 0.0
        self.vp_offset_y = 0.0
        self.vp_zoom = 8.0
        self._drag_start = None
        self._drag_node_start = None
        self._node_drag_fast = False
        self._node_drag_render_job = None
        self._node_drag_inspector_job = None
        self._connecting_from = None
        self._connect_id_overlay = None
        self._connect_id_source = None
        self._connect_id_var = tk.StringVar(value="")

        # Rubber-band selection state
        self._sel_box_start = None
        self._sel_box_mode  = None
        self._sel_box_item  = None

        # Undo/redo
        self._undo_stack = []
        self._node_clipboard = []
        self._cursor_wx = 0.0
        self._cursor_wy = 0.0

        # Paint-mode stroke state
        self._paint_stroke_active = False
        self._paint_stroke_snap = None
        self._paint_stroke_zone = None
        self._paint_stroke_changed = set()
        self._paint_stroke_live_items = {}

        self._redo_stack = []
        self._UNDO_MAX   = 50
        self._editm      = None
        self._ui_menus   = []
        self._windows_menu_visual_policy = _WindowsNativeMenuVisualPolicy()
        self._nudge_origin = None
        self._nudge_after  = None

        # Rendering objects
        self._viewport     = Viewport(self.vp_offset_x, self.vp_offset_y, self.vp_zoom)
        self._hit_tester   = HitTester(cell_size=8.0)
        self._pil_renderer = None
        self._node_items   = {}

        # Transform state
        self._is_transforming  = False
        self._settle_job       = None
        self._synced_zoom      = None
        self._synced_offset    = (None, None)
        self._drag_node_snap   = None
        self._drag_node_origins = {}
        self._zoom_op_count    = 0
        self._DRIFT_CORRECT_N  = 50
        self._render_pending   = False
        self._last_render_ms   = 0.0
        self._force_next_render_no_cache = False
        self._auto_trim_job = None
        self._last_auto_trim_ms = 0
        self._auto_trim_count = 0
        self._zoom_preview_active = False
        self._zoom_preview_count = 0
        self._zoom_preview_last_ms = 0.0
        self._zoom_preview_job = None
        self._zoom_preview_pending = None
        self._zoom_preview_base = None
        self._zoom_static_layer_preview_count = 0
        self._zoom_static_layer_preview_last = None
        self._debug_events     = []
        self._debug_last_click = None
        self._debug_last_insert = None
        self._watchdog_started = False
        self._watchdog_enabled = True
        self._watchdog_last_heartbeat = time.monotonic()
        self._watchdog_hang_logged = False
        self._watchdog_action = 'startup'
        self._watchdog_snapshot = {}
        self._main_thread_ident = threading.get_ident()
        self._zone_overlay_dirty = True
        self._zone_vp_snapshot  = None

        # Layer toggles
        self.show_nodes    = tk.BooleanVar(value=True)
        self.show_nodes_above_terrain = tk.BooleanVar(value=True)
        self.show_nodes_below_terrain = tk.BooleanVar(value=True)
        self._terrain_node_side_cache = {}
        self._terrain_node_side_cache_key = None
        self.show_edges    = tk.BooleanVar(value=True)
        self.show_radius   = tk.BooleanVar(value=True)
        self.show_radius_fill = tk.BooleanVar(
            value=_editor_cfg_bool('show_radius_fill', False))
        self.show_radius_fill_b15 = tk.BooleanVar(
            value=_editor_cfg_bool('show_radius_fill_b15', False))
        self.keep_radius_fill = tk.BooleanVar(value=False)
        self.light_canvas  = tk.BooleanVar(value=_editor_cfg_bool('light_canvas', False))
        self.light_panels  = tk.BooleanVar(value=_editor_cfg_bool('light_panels', True))
        self.dev_mode      = tk.BooleanVar(value=_editor_cfg_bool('developer_mode', False))
        self.show_scroll_guide = tk.BooleanVar(value=_editor_cfg_bool('show_scroll_guide', True))
        self.show_zones    = tk.BooleanVar(value=False)
        self.show_entities = tk.BooleanVar(value=True)
        self.show_buildings = tk.BooleanVar(value=True)
        self.show_vehicles = tk.BooleanVar(value=True)
        self.show_objects = tk.BooleanVar(value=True)
        self.show_decorations = tk.BooleanVar(value=True)
        self.show_foliage = tk.BooleanVar(value=False)
        self.show_context_markers = tk.BooleanVar(value=False)
        self.show_foliage_hulls = tk.BooleanVar(value=False)
        self.show_trees_only = tk.BooleanVar(value=True)
        self.show_grid     = tk.BooleanVar(value=True)
        self.grid_mode     = tk.StringVar(value=GRID_MODE_MED_FIXED)
        self.grid_fixed_spacing_label = tk.StringVar(value="16 m")
        self.grid_mode.trace_add('write', lambda *_: self._on_grid_display_change())
        self.grid_fixed_spacing_label.trace_add('write', lambda *_: self._on_grid_display_change())
        self.grid_snap_nodes    = tk.BooleanVar(value=False)
        self.adaptive_radius    = tk.BooleanVar(value=False)
        self.place_nodes_on_entities = tk.BooleanVar(value=False)
        self.adaptive_large_r   = tk.BooleanVar(value=False)
        self.adaptive_xlarge_r  = tk.BooleanVar(value=False)
        self.grid_snap_nodes.trace_add('write', lambda *_: self._on_snap_toggle())
        self.show_terrain  = tk.BooleanVar(value=False)
        self.show_water    = tk.BooleanVar(value=True)
        self.terrain_display_mode = tk.StringVar(value='C')
        self.show_ids      = tk.BooleanVar(value=False)
        self.show_b16_arrows   = tk.BooleanVar(value=False)
        self.show_b12_extended = tk.BooleanVar(value=False)
        self.show_b12_bands = tk.BooleanVar(value=False)
        self.b12_filter_rules = []
        self._b12_filter_window = None
        self.show_breach_zones = tk.BooleanVar(value=False)
        self.show_edge_zone_color = tk.BooleanVar(value=False)
        self._debug_bar_visible = False
        self._debug_bar_frame   = None
        self._dbg_b12_hidden    = tk.BooleanVar(value=False)
        self._dbg_b13_full      = tk.BooleanVar(value=False)
        self._dbg_edge_flags    = tk.BooleanVar(value=False)
        self._dbg_0x32_low      = tk.BooleanVar(value=False)
        self._dbg_0x32_high     = tk.BooleanVar(value=False)
        self._dbg_0x32_uint16   = tk.BooleanVar(value=False)
        self._dbg_nc_mismatch   = tk.BooleanVar(value=False)
        self._dbg_construction_strata = tk.BooleanVar(value=False)
        self._dbg_u16_min = 0
        self._dbg_u16_max = 65535
        self._dbg_hi_categories = {}
        self._ain_area_states  = []
        self._breach_zone_ids  = set()
        self._assault_zone_ids = set()
        self._both_zone_ids    = set()
        self._zone_layer_state  = {}
        self._zone_layer_win    = None
        self._locked_zone_ids   = set()
        self._hidden_zone_ids   = set()
        self.z_filter_on   = tk.BooleanVar(value=False)
        _zf_min, _zf_max = self._default_zfilter_bounds()
        self.z_filter_min  = tk.DoubleVar(value=_zf_min)
        self.z_filter_max  = tk.DoubleVar(value=_zf_max)
        self._z_filter_last_valid = [_zf_min, _zf_max]
        self.z_filter_min.trace_add('write', lambda *_: self._on_zfilter_value_change())
        self.z_filter_max.trace_add('write', lambda *_: self._on_zfilter_value_change())
        self._zf_debounce_job = None

        # FPS counter
        self._fps_times = []
        self._fps_var = tk.StringVar(value="FPS: --")

        # View mode
        self._view_mode = "2d"
        self._main_frame = None
        self._canvas_frame = None
        self._embedded_3d_view = None
        self._view3d_toggle_btn = None

        self._sync_grid_render_globals()
        self._build_ui()
        try:
            if self.light_panels.get():
                self._apply_panel_theme(True)
        except Exception:
            pass
        self._bind_events()
        self._post_init()
        # A game path is required for BMS/ITEMS.DEF/CModel features.  Defer
        # the themed prompt until Tk has mapped the main window so it can be
        # centered reliably, then open the existing Windows folder picker.
        self.after_idle(self._ensure_game_folder_selected)

        try:
            self.canvas.focus_set()
        except Exception:
            pass

        try:
            self._apply_canvas_theme(bool(self.light_canvas.get()))
        except Exception:
            pass
        self._start_hang_watchdog()
        self.status("Ready. File — Open BMS to load a map.")

    # === generation.py (GenerationMixin) ===

    """AIN generation UI and graph operations for AINEditor."""

    # ─── GENERATION ───────────────────────────────────────────────────────────

    def _generator_entity_category(self, e, item=None):
        return _gen_geom._generator_entity_category(e, item)

    def _generator_should_skip_non_collision_foliage(self, e, item=None, segs=None):
        return _gen_geom._generator_should_skip_non_collision_foliage(e, item, segs)

    def _generator_collision_entity(self, e):
        return _gen_geom._generator_collision_entity(
            e, game_path=_ns.get('_game_path', None),
            pff_cache=_ns.get('_pff_index_cache', None),
            ignore_destroyable=getattr(self, '_seed_ignore_destroyable', GENERATOR_IGNORE_DESTROYABLE),
            ignore_vehicles=getattr(self, '_seed_ignore_vehicles', False))

    def _generator_geometry_cache_key(self):
        return _gen_geom._generator_geometry_cache_key(
            self.entities, self.bms_path,
            float(getattr(self, 'terrain_z', 18.5) or 18.5),
            getattr(self, 'terrain_info', None),
            getattr(self, 'terrain_mis_path', ''),
            getattr(self, '_seed_generate_z_filter', True),
            getattr(self, '_seed_ignore_destroyable', GENERATOR_IGNORE_DESTROYABLE),
            getattr(self, '_seed_ignore_vehicles', False),
            _ns.get('_game_path', None), _ns.get('_pff_index_cache', None))

    def _collect_generator_geometry(self, on_progress=None):
        def _default_progress(msg):
            self.status(msg)
            self.update_idletasks()
        r = _gen_geom.collect_generator_geometry(
            entities=self.entities,
            terrain_z=float(getattr(self, 'terrain_z', 18.5) or 18.5),
            terrain_info=getattr(self, 'terrain_info', None),
            game_path=_ns.get('_game_path', None),
            pff_cache=_ns.get('_pff_index_cache', None),
            bms_path=str(self.bms_path or ''),
            terrain_mis_path=str(getattr(self, 'terrain_mis_path', '') or ''),
            z_filter_enabled=getattr(self, '_seed_generate_z_filter', True),
            ignore_destroyable=getattr(self, '_seed_ignore_destroyable', GENERATOR_IGNORE_DESTROYABLE),
            ignore_vehicles=getattr(self, '_seed_ignore_vehicles', False),
            cache=getattr(self, '_shared_generator_geometry_cache', None),
            on_progress=on_progress or _default_progress,
        )
        self._shared_generator_geometry_cache = r['cache_dict']
        self._generator_support_triangles = r['support_triangles']
        self._generator_support_triangle_metadata = r['support_triangle_metadata']
        self._generator_collision_segments_3d = r['collision_segments_3d']
        self._generator_collision_triangles_3d = r['collision_triangles_3d']
        return r['world_segments'], r['entities_core'], r['stats'], r['cache_hit']



    def _generator_log(self, msg):
        try:
            self.status(str(msg))
            self.update_idletasks()
        except Exception:
            pass

    def _line_crosses_segments_for_generator(self, ax, ay, bx, by, segments):
        return _gen_geom.line_crosses_segments(ax, ay, bx, by, segments)

    def _add_generator_old_new_seams(self, existing_nodes, batch_nodes, offset, world_segments,
                                     collision_triangles_3d=None,
                                     quantized_topology=False):
        return _gen_geom.add_old_new_seams(
            existing_nodes, batch_nodes, offset, world_segments,
            collision_triangles_3d, quantized_topology)

    def _close_generator_progress_window(self):
        return _generator_progress_ui.close_window(self)

    def _show_generator_progress_window(self, cx, cy, radius):
        return _generator_progress_ui.show_window(self, cx, cy, radius)

    def _compose_generator_progress_report(self):
        return _generator_progress_ui.compose_report(self)

    def _refresh_generator_progress_report(self, force=False):
        return _generator_progress_ui.refresh_report(self, force=force)

    def _select_all_generator_progress_text(self, event=None):
        return _generator_progress_ui.select_all(self, event=event)

    def _copy_generator_progress_details(self):
        return _generator_progress_ui.copy_details(self)

    def _update_generator_progress(self, percent, phase, detail=''):
        return _generator_progress_ui.update(self, percent, phase, detail)

    def _tick_generator_progress_clock(self):
        return _generator_progress_ui.tick(self)

    def _finish_generator_progress(self, detail='Generated graph applied to the editor'):
        return _generator_progress_ui.finish(self, detail)

    def _cancel_generator_process(self):
        if not getattr(self, '_generator_running', False):
            return
        self._generator_cancelled = True
        self._generator_running = False
        self._generator_progress_failed = True
        self._seed_generate_armed = False
        self._restore_pending_zone_generation()
        try:
            mgr = _ns.get('_GENERATOR_PROCESS_MANAGER', None)
            if mgr is not None and mgr._process is not None:
                mgr._process.terminate()
                mgr._process = None
        except Exception:
            pass
        self._generator_worker = None
        self._generator_progress_queue = None
        try:
            self.mode.set('edit')
            self._on_mode_change()
        except Exception:
            try:
                self.mode.set('edit')
            except Exception:
                pass
        try:
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        try:
            self.canvas.delete('seed_radius_preview')
        except Exception:
            pass
        self.status('Generation cancelled.')
        self._close_generator_progress_window()

    def _run_generator_at(self, cx, cy, target_node_z=None):
        """Run the embedded generator without blocking Tk's event loop."""
        import queue as _queue_mod
        import time as _time

        if not self.bms_path:
            messagebox.showinfo('Generate AIN', 'Open a BMS map first.', parent=self)
            return
        if getattr(self, '_generator_running', False):
            self.status('Generate AIN: already running. Wait for the current pass to finish.')
            return

        radius = float(getattr(self, '_seed_generate_radius', 32.0) or 32.0)
        z_filter_enabled = bool(getattr(self, '_seed_generate_z_filter', True))
        rooftops_enabled = bool(
            z_filter_enabled
            and getattr(self, '_seed_generate_rooftops', False))
        highest_rooftops_enabled = bool(
            rooftops_enabled
            and getattr(self, '_seed_generate_highest_rooftops', False))
        ladders_enabled = bool(
            z_filter_enabled and rooftops_enabled
            and getattr(self, '_seed_generate_ladders', False))
        quantized_enabled = bool(getattr(self, '_seed_generate_quantized', False))
        ignore_destroyable = bool(getattr(
            self, '_seed_ignore_destroyable', True))
        ignore_vehicles = bool(getattr(
            self, '_seed_ignore_vehicles', False))
        existing_nodes_snapshot = self._snapshot_nodes()
        existing_count = len(existing_nodes_snapshot)
        bms_path_snapshot = os.path.abspath(self.bms_path)
        if target_node_z is None:
            try:
                if bool(self.z_filter_on.get()):
                    zmin, zmax = self._get_z_filter_bounds()
                    target_node_z = (float(zmin) + float(zmax)) * 0.5
            except Exception:
                target_node_z = None
        target_support_z_snapshot = (
            None if target_node_z is None
            else float(target_node_z) - NAV_NODE_Z_LIFT
        )
        progress_queue = _queue_mod.Queue()
        self._generator_progress_queue = progress_queue
        self._generator_running = True
        self._generator_cancelled = False
        self._seed_generate_armed = False
        self._generator_progress_failed = False
        self._generator_progress_result_applied = False
        _history_settings = {
            'z_filter_enabled': bool(z_filter_enabled),
            'rooftops_enabled': bool(rooftops_enabled),
            'highest_broad_rooftops_only': bool(highest_rooftops_enabled),
            'ladders_enabled': bool(ladders_enabled),
            'quantized_enabled': bool(quantized_enabled),
            'ignore_destroyable_objects': bool(ignore_destroyable),
            'ignore_vehicles': bool(ignore_vehicles),
        }
        _retry_source = getattr(self, '_retrying_seed_index', None)
        _history_kind = (f'Retry #{int(_retry_source):03d}'
                         if _retry_source else 'Seed')
        self._cache_generator_seed(
            cx, cy, radius, kind=_history_kind,
            target_node_z=target_node_z, settings=_history_settings)
        placement_label = ('hierarchical 0.50m base / 0.25m coordinate lattice'
                           if quantized_enabled else 'natural walker')
        layer_label = (
            f', target node Z={float(target_node_z):.3f}'
            if target_node_z is not None else ', automatic seed layer'
        )
        self.status(
            f'Generate AIN: started {placement_label} worker for '
            f'X={cx:.3f}, Y={cy:.3f}, R={radius:.1f}m{layer_label}...')
        self._show_generator_progress_window(cx, cy, radius)
        _run_gen = _ns.get('_run_generator_process', None)
        _configure_worker = _ns.get('_configure_generator_worker_thread', None)

        def _worker():
            import traceback as _traceback
            if _configure_worker is not None:
                _configure_worker()
            t0 = _time.perf_counter()
            try:
                progress_queue.put(('phase', 1.0, 'Preparing isolated worker request',
                                    'Worker owns map, CModel, terrain, and collision caches'))
                process_job = {
                    'bms_path': bms_path_snapshot,
                    'center': (float(cx), float(cy)),
                    'radius': radius,
                    'existing_nodes': [node.to_dict()
                                       for node in existing_nodes_snapshot],
                    'z_filter_enabled': z_filter_enabled,
                    'rooftops_enabled': rooftops_enabled,
                    'highest_broad_rooftops_only': highest_rooftops_enabled,
                    'ladders_enabled': ladders_enabled,
                    'quantized_enabled': quantized_enabled,
                    'ignore_destroyable_objects': ignore_destroyable,
                    'ignore_vehicles': ignore_vehicles,
                    'target_support_z': target_support_z_snapshot,
                }

                progress_queue.put((
                    'status',
                    'Generate AIN: exact core running in isolated low-power worker...'))

                event_gate = {
                    'phase': None,
                    'phase_at': 0.0,
                    'status': None,
                    'status_at': 0.0,
                }

                def _process_event(event):
                    if not event:
                        return
                    now = _time.perf_counter()
                    kind = event[0]
                    if kind == 'phase':
                        phase_name = str(event[2]) if len(event) > 2 else ''
                        phase_changed = phase_name != event_gate['phase']
                        if (phase_changed
                                or now - event_gate['phase_at'] >= 0.125
                                or (len(event) > 1 and float(event[1]) >= 100.0)):
                            event_gate['phase'] = phase_name
                            event_gate['phase_at'] = now
                            progress_queue.put(tuple(event))
                    elif kind == 'status':
                        message = str(event[1]) if len(event) > 1 else ''
                        if (message != event_gate['status']
                                and (event_gate['status'] is None
                                     or now - event_gate['status_at'] >= 0.20)):
                            event_gate['status'] = message
                            event_gate['status_at'] = now
                            progress_queue.put(tuple(event))

                result = _run_gen(
                    process_job, on_event=_process_event)
                if result.get('no_reachable_surface'):
                    progress_queue.put((
                        'warning',
                        'Generate AIN',
                        'The automatic generator Z filter could not find a reachable '
                        'support surface at the clicked seed.'))
                    progress_queue.put(('finished', None))
                    return
                generated = list(result.get('nodes') or [])
                if not generated:
                    progress_queue.put(('warning', 'Generate AIN', 'No nodes generated for this seed.'))
                    progress_queue.put(('finished', None))
                    return

                progress_queue.put(('phase', 93.0, 'Converting generated nodes',
                                    f'Converting {len(generated)} generated records into editor nodes'))
                offset = existing_count
                generated_count = len(generated)
                batch_nodes = []
                for i, n in enumerate(generated):
                    if isinstance(n, dict):
                        nget = n.get
                    else:
                        nget = lambda key, default=None, _node=n: getattr(
                            _node, key, default)
                    local_neighbors = []
                    for nb in sorted(list(nget('neighbors', []) or [])):
                        try:
                            nb = int(nb)
                        except Exception:
                            continue
                        if 0 <= nb < generated_count:
                            local_neighbors.append(offset + nb)
                    batch_nodes.append(Node(offset + i,
                                            float(nget('x', 0.0)),
                                            float(nget('y', 0.0)),
                                            float(nget('z', 0.0)),
                                            b12=int(nget('b12', 0)), b13=0, b14=0,
                                            b15=int(nget('b15', 36)),
                                            b16=int(nget('b16', 0)),
                                            b17=int(nget('b17', 0)),
                                            b18=int(nget('b18', 0)),
                                            neighbors=local_neighbors[:MAX_NEIGHBORS],
                                            quantized_source_keys=nget(
                                                'quantized_source_keys', None),
                                            quantized_map_grid=bool(nget(
                                                'quantized_map_grid', False))))

                progress_queue.put(('phase', 97.0, 'Applying boundary seams',
                                    'Applying worker-validated old/new seam pairs'))
                old_new_links = 0
                seen_seams = set()
                for seam in list(result.get('old_new_seams') or []):
                    try:
                        old_index, local_index = int(seam[0]), int(seam[1])
                    except Exception:
                        continue
                    seam_key = (old_index, local_index)
                    if seam_key in seen_seams:
                        continue
                    seen_seams.add(seam_key)
                    if not (0 <= old_index < existing_count
                            and 0 <= local_index < generated_count):
                        continue
                    new_index = offset + local_index
                    old_node = existing_nodes_snapshot[old_index]
                    new_node = batch_nodes[local_index]
                    if (new_index not in old_node.neighbors
                            and len(old_node.neighbors) < MAX_NEIGHBORS):
                        old_node.neighbors.append(new_index)
                    if (old_index not in new_node.neighbors
                            and len(new_node.neighbors) < MAX_NEIGHBORS):
                        new_node.neighbors.append(old_index)
                    if (new_index in old_node.neighbors
                            and old_index in new_node.neighbors):
                        old_new_links += 1

                metrics = dict(result.get('metrics') or {})
                metrics['geometry_cache_hit'] = bool(
                    metrics.get('worker_geometry_cache_hit', False))
                metrics['geometry_time_sec'] = float(
                    metrics.get('worker_geometry_time_sec', 0.0) or 0.0)
                metrics['total_time_sec'] = round(_time.perf_counter() - t0, 3)
                metrics['old_new_links'] = old_new_links

                progress_queue.put(('phase', 99.0, 'Applying generated graph',
                                    'Returning the completed batch to the editor'))
                progress_queue.put((
                    'result',
                    {
                        'existing_count': existing_count,
                        'stitched_existing_nodes': existing_nodes_snapshot,
                        'batch_nodes': batch_nodes,
                        'metrics': metrics,
                    }))
            except Exception as ex:
                progress_queue.put(('error', str(ex), _traceback.format_exc()))
            finally:
                progress_queue.put(('finished', None))

        self._generator_worker = threading.Thread(
            target=_worker,
            name='AINGeneratorWorker',
            daemon=True)
        self._generator_worker.start()
        self.after(50, self._poll_generator_progress)

    def _poll_generator_progress(self):
        if getattr(self, '_generator_cancelled', False):
            return
        q = getattr(self, '_generator_progress_queue', None)
        if q is None:
            return
        try:
            import queue as _queue
            poll_deadline = time.perf_counter() + 0.004
            processed = 0
            while processed < 32 and time.perf_counter() < poll_deadline:
                kind, *payload = q.get_nowait()
                processed += 1
                if kind == 'status':
                    self.status(payload[0])
                elif kind == 'phase':
                    percent, phase, detail = payload
                    self._update_generator_progress(percent, phase, detail)
                elif kind == 'warning':
                    title, message = payload
                    self._generator_progress_failed = True
                    self._update_generator_progress(100, 'Generation stopped', message)
                    try:
                        self._generator_progress_window.grab_release()
                    except Exception:
                        pass
                    self.status(message)
                    messagebox.showwarning(title, message, parent=self)
                    self._close_generator_progress_window()
                elif kind == 'zone_warning':
                    title, message = payload
                    self._generator_progress_failed = True
                    self._restore_pending_zone_generation()
                    self._update_generator_progress(100, 'Generation stopped', message)
                    try:
                        self._generator_progress_window.grab_release()
                    except Exception:
                        pass
                    self.status(message)
                    messagebox.showwarning(title, message, parent=self)
                    self._close_generator_progress_window()
                elif kind == 'error':
                    message, tb = payload
                    self._generator_progress_failed = True
                    self._update_generator_progress(100, 'Generation failed', message)
                    try:
                        self._generator_progress_window.grab_release()
                    except Exception:
                        pass
                    try:
                        print(tb)
                    except Exception:
                        pass
                    self.status(f'Generate AIN error: {message}')
                    messagebox.showerror('Generate AIN error', message, parent=self)
                    self._close_generator_progress_window()
                elif kind == 'zone_error':
                    message, tb = payload
                    self._generator_progress_failed = True
                    self._restore_pending_zone_generation()
                    self._update_generator_progress(100, 'Generation failed', message)
                    try:
                        self._generator_progress_window.grab_release()
                    except Exception:
                        pass
                    try:
                        print(tb)
                    except Exception:
                        pass
                    self.status(f'Generate Zone error: {message}')
                    messagebox.showerror('Generate Zone error', message, parent=self)
                    self._close_generator_progress_window()
                elif kind == 'result':
                    if not getattr(self, '_generator_cancelled', False):
                        self._apply_generator_result(payload[0])
                        self._generator_progress_result_applied = True
                elif kind == 'zone_result':
                    if not getattr(self, '_generator_cancelled', False):
                        self._generator_progress_result_applied = bool(
                            self._apply_zone_generator_result(payload[0]))
                elif kind == 'finished':
                    self._generator_running = False
                    self._generator_worker = None
                    self._generator_progress_queue = None
                    if not getattr(self, '_generator_progress_failed', False):
                        if getattr(self, '_generator_progress_result_applied', False):
                            detail = getattr(
                                self, '_generator_finish_detail', None)
                            self._finish_generator_progress(
                                detail or 'Generated graph applied to the editor')
                        else:
                            self._finish_generator_progress('Generator worker finished without a node batch')
                    self._generator_finish_detail = None
                    return
        except _queue.Empty:
            pass
        if getattr(self, '_generator_running', False):
            try:
                more_pending = q.qsize() > 0
            except Exception:
                more_pending = False
            self.after(1 if more_pending else 50, self._poll_generator_progress)

    def _restore_pending_zone_generation(self):
        snap = getattr(self, '_generator_zone_snapshot', None)
        self._generator_zone_snapshot = None
        if snap is not None:
            self._restore_zone_snapshot(snap)

    def _apply_zone_generator_result(self, payload):
        batch_nodes = list(payload.get('batch_nodes') or [])
        stitched_existing_nodes = list(
            payload.get('stitched_existing_nodes') or [])
        expected_count = int(payload.get('existing_count', 0))
        zone_id = int(payload.get('zone_id'))
        undo_snapshot = payload.get('undo_snapshot')
        if not batch_nodes:
            self._generator_progress_failed = True
            self._restore_pending_zone_generation()
            self.status('Generate Zone: no nodes generated inside the selected tiles.')
            return False
        if (len(self.nodes or []) != expected_count
                or len(stitched_existing_nodes) != expected_count):
            self._generator_progress_failed = True
            self._restore_pending_zone_generation()
            messagebox.showwarning(
                'Generate Zone',
                'The node list changed while zone generation was running. '
                'The previous zone graph was restored and the generated batch '
                'was discarded.',
                parent=self)
            self.status('Generate Zone discarded: node list changed during generation.')
            return False
        if zone_id not in self.zones:
            self._generator_progress_failed = True
            self._restore_pending_zone_generation()
            self.status('Generate Zone discarded: the target zone no longer exists.')
            return False

        self.nodes = stitched_existing_nodes + batch_nodes
        self.next_id = len(self.nodes)
        meta = self._ensure_zone_meta(zone_id)
        meta['nodes'] = set(payload.get('new_ids') or ())
        job_count = int(payload.get('job_count', 0))
        self._push_undo(
            f'Generate zone {zone_id} ({len(batch_nodes)} nodes, '
            f'{job_count} local transactions)',
            lambda s=undo_snapshot: self._restore_zone_snapshot(s))
        self._generator_zone_snapshot = None
        self._generator_finish_detail = payload.get('finish_detail')
        self._last_generator_metrics = dict(payload.get('metrics') or {})
        self._mark_zones_dirty()
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self.selected_id = None
        self.selected_nodes.clear()
        self._update_inspector()
        self._update_stats()
        self.redraw()
        self.refresh_3d_views(
            rebuild=True, refit=False, reason='zone_local_transactions')
        self.status(
            f'Generate Zone {zone_id} applied {len(batch_nodes)} nodes '
            f'from {job_count} local transaction(s).')
        return True

    def _apply_generator_result(self, payload):
        batch_nodes = list(payload.get('batch_nodes') or [])
        stitched_existing_nodes = list(
            payload.get('stitched_existing_nodes') or [])
        metrics = dict(payload.get('metrics') or {})
        expected_count = int(payload.get('existing_count', 0))
        if not batch_nodes:
            self.status('Generate AIN: no nodes generated for this seed.')
            return
        if len(self.nodes or []) != expected_count:
            messagebox.showwarning(
                'Generate AIN',
                'The node list changed while generation was running. '
                'Generated nodes were discarded to avoid corrupting links.',
                parent=self)
            self.status('Generate AIN discarded: node list changed during generation.')
            return
        if len(stitched_existing_nodes) != expected_count:
            self.status('Generate AIN discarded: stitched old-node snapshot is incomplete.')
            return
        undo_snapshot = self._snapshot_nodes()
        self.nodes = stitched_existing_nodes + batch_nodes
        self._push_undo(
            f'Generate AIN seed ({len(batch_nodes)} nodes)',
            lambda s=undo_snapshot: self._restore_nodes(s))
        self.next_id = len(self.nodes)
        self._rebuild_hit_tester()
        self.selected_id = None
        self.selected_nodes.clear()
        try:
            self.canvas.delete('seed_radius_preview')
        except Exception:
            pass
        try:
            self.mode.set('edit')
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        self._last_generator_metrics = metrics
        self.redraw()
        self._update_stats()
        self.status(
            f'Generate AIN appended {len(batch_nodes)} nodes ({len(self.nodes)} total) in {metrics.get("total_time_sec", 0):.2f}s '
            f'(coverage {metrics.get("disc_coverage_pct", "?")}%, repairs {metrics.get("campaign_repair_added", metrics.get("repair_added", "?"))}, '
            f'old-new links {metrics.get("old_new_links", "?")}, comp {metrics.get("largest_component_pct", "?")}%/{metrics.get("component_count", "?")})')

    def _ask_generator_options(self, preselect_zone=False, fixed_seed_coordinates=None):
        return _generator_dialog_ui.ask_generator_options(
            self, preselect_zone, fixed_seed_coordinates)


    def _ask_generate_coordinates(self):
        return _generator_dialog_ui.ask_generate_coordinates(self)

    def do_generate_with_coordinates(self):
        """Run the normal seed generator at an explicitly entered X/Y coordinate."""
        if not self.bms_path:
            messagebox.showinfo('Generate AIN', 'Open a BMS map first.', parent=self)
            return
        if getattr(self, '_generator_running', False):
            self.status('Generate with coordinates: generator is already running.')
            return
        if getattr(self, '_seed_generate_armed', False):
            self._cancel_seed_generate('switched to coordinate seed')

        coordinates = self._ask_generate_coordinates()
        if coordinates is None:
            return
        cx, cy = coordinates

        options = self._ask_generator_options(
            preselect_zone=False, fixed_seed_coordinates=(cx, cy))
        if options is None:
            return
        radius, z_filter_enabled, rooftops_enabled, quantized_enabled = options[:4]
        ladders_enabled = options[5] if len(options) > 5 else False
        highest_rooftops_enabled = options[6] if len(options) > 6 else False
        ignore_destroyable = options[7] if len(options) > 7 else True
        ignore_vehicles = options[8] if len(options) > 8 else False

        self._seed_generate_radius = float(radius)
        self._seed_generate_z_filter = bool(z_filter_enabled)
        self._seed_generate_rooftops = bool(
            z_filter_enabled and rooftops_enabled)
        self._seed_generate_highest_rooftops = bool(
            z_filter_enabled and rooftops_enabled and highest_rooftops_enabled)
        self._seed_generate_ladders = bool(
            z_filter_enabled and rooftops_enabled and ladders_enabled)
        self._seed_generate_quantized = bool(quantized_enabled)
        self._seed_ignore_destroyable = bool(ignore_destroyable)
        self._seed_ignore_vehicles = bool(ignore_vehicles)
        self._shared_generator_geometry_cache = None

        self._seed_generate_armed = False
        try:
            self.mode.set('edit')
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        self._run_generator_at(float(cx), float(cy), target_node_z=None)


    def do_generate(self):
        """Arm the seed-disc generator. Next left-click picks the seed."""
        if (not getattr(self, '_generator_running', False)
                and self.mode.get() == 'generate'
                and getattr(self, '_seed_generate_armed', False)):
            self._cancel_seed_generate('cancelled by Generate toggle')
            return
        if not self.bms_path:
            messagebox.showinfo('Generate AIN', 'Open a BMS map first.', parent=self)
            return
        options = self._ask_generator_options(preselect_zone=False)
        if options is None:
            return
        radius, z_filter_enabled, rooftops_enabled, quantized_enabled = options[:4]
        selected_zone = options[4] if len(options) > 4 else None
        ladders_enabled = options[5] if len(options) > 5 else False
        highest_rooftops_enabled = options[6] if len(options) > 6 else False
        ignore_destroyable = options[7] if len(options) > 7 else True
        ignore_vehicles = options[8] if len(options) > 8 else False
        self._seed_generate_radius = float(radius)
        self._seed_generate_z_filter = bool(z_filter_enabled)
        self._seed_generate_rooftops = bool(
            z_filter_enabled and rooftops_enabled)
        self._seed_generate_highest_rooftops = bool(
            z_filter_enabled and rooftops_enabled
            and highest_rooftops_enabled)
        self._seed_generate_ladders = bool(
            z_filter_enabled and rooftops_enabled and ladders_enabled)
        self._seed_generate_quantized = bool(quantized_enabled)
        self._seed_ignore_destroyable = bool(ignore_destroyable)
        self._seed_ignore_vehicles = bool(ignore_vehicles)
        self._shared_generator_geometry_cache = None

        if selected_zone is not None and selected_zone in self.zones:
            self._zone_selected = selected_zone
            self._load_zone_tiles()
            opts = {
                'b12': 0,
                'b15': int(self._ensure_zone_meta(selected_zone).get('b15', 36)),
                'spacing': self._default_spacing_for_b15(
                    self._ensure_zone_meta(selected_zone).get('b15', 36)),
                'ignore_collisions': False,
            }
            self._generate_tile_grid_collision_aware(selected_zone, opts)
            return

        self._seed_generate_armed = True
        try:
            self.mode.set('generate')
            self._on_mode_change()
            self.canvas.configure(cursor='target')
        except Exception:
            try:
                self.mode.set('generate')
                self.canvas.configure(cursor='target')
            except Exception:
                pass
        z_state = 'automatic Z filter ON' if self._seed_generate_z_filter else 'automatic Z filter OFF'
        roof_state = 'rooftops ON' if self._seed_generate_rooftops else 'rooftops OFF'
        if self._seed_generate_highest_rooftops:
            highest_roof_state = 'highest broad structural surface only'
        elif self._seed_generate_rooftops:
            highest_roof_state = 'all eligible elevated surfaces'
        else:
            highest_roof_state = 'roof selection inactive'
        ladder_state = (
            'ladders ON' if self._seed_generate_ladders else 'ladders OFF')
        destroyable_state = (
            'destroyable objects ignored'
            if self._seed_ignore_destroyable
            else 'destroyable objects block navigation')
        vehicle_state = (
            'vehicles ignored'
            if self._seed_ignore_vehicles
            else 'vehicles block navigation')
        placement_state = ('hierarchical 0.50m base / 0.25m coordinate lattice'
                           if self._seed_generate_quantized else 'natural walker')
        self.status(
            f'Generate AIN armed: click seed / point of interest for '
            f'{self._seed_generate_radius:.1f}m disc generation '
            f'({z_state}, {roof_state}, {highest_roof_state}, {ladder_state}, '
            f'{destroyable_state}, {vehicle_state}, {placement_state}).')


    def _ask_link_by_distance_options(self, selected_count):
        return _generator_dialog_ui.ask_link_by_distance_options(
            self, selected_count)


    def link_by_distance(self):
        """Rebuild collision-safe, distance-limited links within the selection.

        This is intentionally separate from Rebuild Neighbours. It replaces
        only selected-to-selected edges, preserves every edge touching an
        unselected node, does not use radius-derived reach or Gabriel pruning,
        and never considers an unselected node as a new endpoint.
        """
        if self._generator_edit_blocked():
            return
        if not self.nodes:
            self.status('Link by Distance: no nodes loaded.')
            return

        self._prune_selection_to_editable()
        node_by_id = {int(n.id): n for n in self.nodes}
        selected_ids = sorted(
            int(nid) for nid in self.selected_nodes
            if int(nid) in node_by_id)
        if len(selected_ids) < 2:
            messagebox.showinfo(
                'Link by Distance',
                'Select at least two editable nodes first.', parent=self)
            self.status('Link by Distance: select at least two editable nodes first.')
            return

        options = self._ask_link_by_distance_options(len(selected_ids))
        if options is None:
            return
        max_distance, max_neighbors = options

        self.status(
            f'Link by Distance: checking {len(selected_ids)} selected nodes...')
        self.update_idletasks()

        gp = _ns.get('_game_path', None)
        _get_cmodel_segments = _ns.get('get_cmodel_segments', None)
        all_segs = build_world_collision_segments(
            self.entities, gp, get_cmodel_collision_segments_3d,
            _get_cmodel_segments, NEVER_EXCLUDE, _xy_from_point)

        seg_cell = max(1.0, min(float(max_distance), 16.0))
        seg_grid = build_segment_grid(all_segs, seg_cell)

        def segment_blocked(ax, ay, az, bx, by, bz):
            return segment_blocked_3d(
                ax, ay, az, bx, by, bz, seg_grid, seg_cell)

        node_cell = max(1.0, float(max_distance))
        node_grid = {}
        for nid in selected_ids:
            node = node_by_id[nid]
            cell_key = (int(math.floor(node.x/node_cell)),
                        int(math.floor(node.y/node_cell)))
            node_grid.setdefault(cell_key, []).append(nid)

        existing_pairs = set()
        for nid, node in node_by_id.items():
            for raw_neighbor in (node.neighbors or ()):
                try:
                    neighbor_id = int(raw_neighbor)
                except Exception:
                    continue
                if neighbor_id in node_by_id and neighbor_id != nid:
                    existing_pairs.add(
                        (min(nid, neighbor_id), max(nid, neighbor_id)))
        selected_set = set(selected_ids)
        replaced_pairs = {
            pair for pair in existing_pairs
            if pair[0] in selected_set and pair[1] in selected_set
        }
        preserved_pairs = existing_pairs - replaced_pairs

        degree = {nid: 0 for nid in node_by_id}
        for first_id, second_id in preserved_pairs:
            degree[first_id] += 1
            degree[second_id] += 1

        candidates = []
        seen_pairs = set()
        rejected_layer = 0
        rejected_collision = 0
        cell_radius = int(math.ceil(float(max_distance)/node_cell)) + 1
        for aid in selected_ids:
            first = node_by_id[aid]
            acx = int(math.floor(first.x/node_cell))
            acy = int(math.floor(first.y/node_cell))
            for dcx in range(-cell_radius, cell_radius+1):
                for dcy in range(-cell_radius, cell_radius+1):
                    for bid in node_grid.get((acx+dcx, acy+dcy), ()):
                        if bid <= aid:
                            continue
                        pair = (aid, bid)
                        if pair in seen_pairs:
                            continue
                        seen_pairs.add(pair)
                        second = node_by_id[bid]
                        distance = math.hypot(
                            second.x-first.x, second.y-first.y)
                        if distance < 0.05 or distance > max_distance:
                            continue
                        dz = abs(float(second.z)-float(first.z))
                        allowed_dz = min(1.25, max(0.45, 0.75*distance))
                        if dz > allowed_dz:
                            rejected_layer += 1
                            continue
                        if segment_blocked(
                                first.x, first.y, first.z,
                                second.x, second.y, second.z):
                            rejected_collision += 1
                            continue
                        candidates.append((distance, aid, bid))

        ordered = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))
        accepted = []
        accepted_set = set()
        candidate_by_node = {nid: [] for nid in selected_ids}
        for distance, aid, bid in ordered:
            candidate_by_node[aid].append((distance, bid))
            candidate_by_node[bid].append((distance, aid))

        directions = {nid: [] for nid in selected_ids}

        def remember_direction(first_id, second_id):
            if first_id not in directions or second_id not in node_by_id:
                return
            first = node_by_id[first_id]
            second = node_by_id[second_id]
            directions[first_id].append(
                math.atan2(second.y-first.y, second.x-first.x))

        for first_id, second_id in preserved_pairs:
            remember_direction(first_id, second_id)
            remember_direction(second_id, first_id)

        def accept_pair(distance, aid, bid):
            pair = (min(aid, bid), max(aid, bid))
            if pair in accepted_set:
                return False
            if (degree[aid] >= max_neighbors
                    or degree[bid] >= max_neighbors):
                return False
            accepted.append((distance, aid, bid))
            accepted_set.add(pair)
            degree[aid] += 1
            degree[bid] += 1
            remember_direction(aid, bid)
            remember_direction(bid, aid)
            return True

        parent = {nid: nid for nid in selected_ids}
        rank = {nid: 0 for nid in selected_ids}

        def component_root(nid):
            root = nid
            while parent[root] != root:
                root = parent[root]
            while parent[nid] != nid:
                following = parent[nid]
                parent[nid] = root
                nid = following
            return root

        def join_components(first_id, second_id):
            first_root = component_root(first_id)
            second_root = component_root(second_id)
            if first_root == second_root:
                return
            if rank[first_root] < rank[second_root]:
                first_root, second_root = second_root, first_root
            parent[second_root] = first_root
            if rank[first_root] == rank[second_root]:
                rank[first_root] += 1

        for distance, aid, bid in ordered:
            if component_root(aid) == component_root(bid):
                continue
            if accept_pair(distance, aid, bid):
                join_components(aid, bid)

        max_candidate_count = max(
            (len(candidate_by_node[nid]) for nid in selected_ids), default=1)
        max_radius = max(
            (max(0.0, float(node_by_id[nid].acceptance_radius()))
             for nid in selected_ids), default=0.0)
        hub_score = {}
        soft_target = {}
        for nid in selected_ids:
            node = node_by_id[nid]
            local = candidate_by_node[nid]
            sector_set = set()
            distance_gain = 0.0
            for distance, other_id in local:
                other = node_by_id[other_id]
                angle = math.atan2(other.y-node.y, other.x-node.x)
                sector_set.add(int(((angle+math.pi)/math.tau)*8.0) % 8)
                distance_gain += max(
                    0.0, 1.0-distance/max(float(max_distance), 1e-9))
            count_score = len(local)/max(1, max_candidate_count)
            sector_score = len(sector_set)/8.0
            closeness_score = distance_gain/max(1, len(local))
            radius_score = (
                max(0.0, float(node.acceptance_radius()))/max_radius
                if max_radius > 1e-9 else 0.0)
            score = (0.40*count_score + 0.30*sector_score
                     + 0.15*closeness_score + 0.15*radius_score)
            hub_score[nid] = score
            if max_neighbors <= 2:
                target = max_neighbors
            else:
                target = 2 + int(round(score*(max_neighbors-2)))
                target = max(2, min(max_neighbors, target))
            soft_target[nid] = target

        def angular_novelty(nid, other_id):
            existing = directions[nid]
            if not existing:
                return 1.0
            first = node_by_id[nid]
            second = node_by_id[other_id]
            angle = math.atan2(second.y-first.y, second.x-first.x)
            separation = min(
                abs((angle-old_angle+math.pi) % math.tau-math.pi)
                for old_angle in existing)
            return separation/math.pi

        hub_order = sorted(
            selected_ids,
            key=lambda nid: (
                -hub_score[nid],
                -float(node_by_id[nid].acceptance_radius()), nid))
        for hub_id in hub_order:
            while (degree[hub_id] < soft_target[hub_id]
                   and degree[hub_id] < max_neighbors):
                best = None
                for distance, other_id in candidate_by_node[hub_id]:
                    pair = (min(hub_id, other_id), max(hub_id, other_id))
                    if pair in accepted_set or degree[other_id] >= max_neighbors:
                        continue
                    hub_novelty = angular_novelty(hub_id, other_id)
                    other_novelty = angular_novelty(other_id, hub_id)
                    other_target = max(1, soft_target[other_id])
                    other_need = max(
                        0.0, 1.0-degree[other_id]/other_target)
                    distance_penalty = distance/max(
                        float(max_distance), 1e-9)
                    radius_pair = (
                        (float(node_by_id[hub_id].acceptance_radius())
                         + float(node_by_id[other_id].acceptance_radius()))
                        / (2.0*max_radius)
                        if max_radius > 1e-9 else 0.0)
                    score = (4.0*hub_novelty + 1.5*other_novelty
                             + 0.55*other_need + 0.35*radius_pair
                             + 0.25*hub_score[other_id]
                             - 0.65*distance_penalty)
                    candidate_key = (
                        score, hub_novelty, other_novelty,
                        -distance, -other_id)
                    if best is None or candidate_key > best[0]:
                        best = (candidate_key, distance, other_id)
                if best is None:
                    break
                _key, distance, other_id = best
                if not accept_pair(distance, hub_id, other_id):
                    break

        if not accepted and not replaced_pairs:
            full_nodes = sum(
                1 for nid in selected_ids if degree[nid] >= max_neighbors)
            self.status(
                f'Link by Distance: no selected links to update '
                f'({rejected_collision} blocked, {rejected_layer} cross-layer, '
                f'{full_nodes} at neighbour limit).')
            return

        snap = self._snapshot_nodes()
        for nid in selected_ids:
            node = node_by_id[nid]
            retained = []
            for raw_neighbor in (node.neighbors or ()):
                try:
                    neighbor_id = int(raw_neighbor)
                except Exception:
                    retained.append(raw_neighbor)
                    continue
                if neighbor_id in selected_set and neighbor_id != nid:
                    continue
                retained.append(raw_neighbor)
            node.neighbors = retained

        for _distance, aid, bid in accepted:
            first = node_by_id[aid]
            second = node_by_id[bid]
            first.neighbors.append(bid)
            second.neighbors.append(aid)

        self._push_undo(
            f'Link by distance: {len(replaced_pairs)} to {len(accepted)} connections',
            lambda snapshot=snap: self._restore_nodes(snapshot))
        if self._pil_renderer:
            self._pil_renderer.clear()
        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self.refresh_3d_views(
            rebuild=True, refit=False, reason='link_by_distance')
        self.status(
            f'Link by Distance: rebuilt selected connections '
            f'{len(replaced_pairs)} -> {len(accepted)} among '
            f'{len(selected_ids)} nodes; maximum {max_distance:g}m, '
            f'{max_neighbors} neighbours per node.')
        self.redraw()


    def rebuild_neighbors(self):
        """Rebuild graph links using radius-aware local topology.

        The rebuild is deliberately two-pass:
          1) compute the complete set of desired undirected edges;
          2) apply them symmetrically only after computation is finished.

        This avoids the old order-dependent bug where processing a later node
        could clear the reciprocal half of an edge created earlier.

        Candidate reach uses the legacy distance as a floor, extended by the
        pair's acceptance radii. Long shortcut edges are pruned with a Gabriel-
        style local-neighbour test: if another node lies inside the circle whose
        diameter is the proposed edge, that edge is considered redundant.
        """
        _math = math

        if not self.nodes:
            self.status("No nodes to rebuild.")
            return

        if self._generator_edit_blocked():
            return

        self.status("Rebuilding neighbors...")
        self.update()
        gp = _ns.get('_game_path', None)
        _get_cmodel_segments = _ns.get('get_cmodel_segments', None)
        _CONNECT_DIST = _ns.get('CONNECT_DIST', 5.6)

        # Build and retain the world-space collision/LOS index for this exact
        # map/resource state. Rebuild Neighbors may be run repeatedly on small
        # selections; repeating the complete static-entity pass made each
        # operation pay the full-map cost even when only a handful of nodes
        # were selected.
        entity_rows = tuple(
            (
                int(e.get('type_id', -1)),
                round(float(e.get('x', 0.0)), 6),
                round(float(e.get('y', 0.0)), 6),
                round(float(e.get('heading', 0.0) or 0.0), 6),
                round(float(e.get('pitch', 0.0) or 0.0), 6),
                bool(e.get('is_static')),
            )
            for e in (self.entities or [])
        )
        static_type_ids = sorted({
            row[0] for row in entity_rows
            if row[5] and row[0] not in NEVER_EXCLUDE
        })
        model_signature = tuple(
            (tid, id(get_cmodel_collision_segments_3d(
                tid, gp, allow_load=False)))
            for tid in static_type_ids
        )
        collision_cache_key = (
            str(gp).lower() if gp else '', entity_rows, model_signature,
            float(_CONNECT_DIST),
        )
        collision_cache = getattr(
            self, '_rebuild_neighbor_collision_cache', None)
        if (collision_cache is not None
                and collision_cache[0] == collision_cache_key):
            all_segs, _sc, _sg = collision_cache[1:]
        else:
            all_segs = build_world_collision_segments(
                self.entities, gp, get_cmodel_collision_segments_3d,
                _get_cmodel_segments, NEVER_EXCLUDE, _xy_from_point)

            # A compact LOS grid keeps each segment query local. The old
            # CONNECT_DIST*1.5 cell (about 8.4 m) put too many CModel
            # segments in typical cells for larger selections.
            _sc = max(1.0, min(_CONNECT_DIST * 1.5, 2.0))
            _sg = build_segment_grid(all_segs, _sc)
            self._rebuild_neighbor_collision_cache = (
                collision_cache_key, all_segs, _sc, _sg)

        def seg_blocks(ax, ay, az, bx, by, bz):
            return segment_blocked_3d(
                ax, ay, az, bx, by, bz, _sg, _sc)

        node_by_id = {int(n.id): n for n in self.nodes}
        if self.selected_nodes:
            target_ids = {
                int(nid) for nid in self.selected_nodes
                if int(nid) in node_by_id
            }
        else:
            target_ids = set(node_by_id)

        if not target_ids:
            self.status("Rebuild Neighbours: no editable nodes selected.")
            return

        snap = self._snapshot_nodes()

        legacy_reach = _CONNECT_DIST * 1.5
        max_radius = max(
            (max(0.0, float(n.acceptance_radius())) for n in self.nodes),
            default=0.0)
        max_reach = max(legacy_reach, max_radius * 2.0 + 0.25)
        cell = max(1.0, legacy_reach)
        node_cells = {}
        for n in self.nodes:
            cx, cy = int(_math.floor(n.x/cell)), int(_math.floor(n.y/cell))
            node_cells.setdefault((cx,cy), []).append(n)

        def nearby_nodes(n, radius):
            cx = int(_math.floor(n.x/cell))
            cy = int(_math.floor(n.y/cell))
            cr = int(_math.ceil(radius/cell)) + 1
            seen = set()
            for dcx in range(-cr, cr+1):
                for dcy in range(-cr, cr+1):
                    for nb in node_cells.get((cx+dcx, cy+dcy), ()):
                        if nb.id == n.id or nb.id in seen:
                            continue
                        seen.add(nb.id)
                        yield nb

        MIN_LINK_DIST = 0.05

        def pair_reach(a, b):
            ar = max(0.0, float(a.acceptance_radius()))
            br = max(0.0, float(b.acceptance_radius()))
            return max(legacy_reach, ar + br + 0.25)

        def locally_redundant(a, b, distance):
            """Gabriel-style pruning.

            Reject a long A-B edge if another node lies strictly inside the
            circle whose diameter is A-B. Such a node is a more local route
            between them, so A-B would be a shortcut across the graph.
            """
            mx = (a.x + b.x) * 0.5
            my = (a.y + b.y) * 0.5
            r2 = (distance * 0.5) ** 2
            threshold = r2 * 0.94
            for k in nearby_nodes(a, distance):
                if k.id == b.id:
                    continue
                dx = k.x - mx
                dy = k.y - my
                if dx*dx + dy*dy < threshold:
                    return True
            return False

        existing_edges = set()
        for a in self.nodes:
            aid = int(a.id)
            for bid_raw in list(a.neighbors or ()):
                try:
                    bid = int(bid_raw)
                except Exception:
                    continue
                if bid not in node_by_id or aid == bid:
                    continue
                existing_edges.add((min(aid, bid), max(aid, bid)))

        valid_existing = set()
        for pair in existing_edges:
            a_id, b_id = pair
            if a_id not in target_ids and b_id not in target_ids:
                valid_existing.add(pair)
                continue
            a = node_by_id[a_id]
            b = node_by_id[b_id]
            d = _math.hypot(b.x - a.x, b.y - a.y)
            if d < MIN_LINK_DIST:
                continue
            if d > pair_reach(a, b):
                continue
            if seg_blocks(a.x, a.y, a.z, b.x, b.y, b.z):
                continue
            valid_existing.add(pair)

        removed = len(existing_edges) - len(valid_existing)

        new_proposals = set()
        for aid in sorted(target_ids):
            a = node_by_id[aid]
            for b in nearby_nodes(a, max_reach):
                bid = int(b.id)
                if bid == aid:
                    continue
                pair = (min(aid, bid), max(aid, bid))
                if pair in valid_existing or pair in new_proposals:
                    continue
                d = _math.hypot(b.x - a.x, b.y - a.y)
                if d < MIN_LINK_DIST:
                    continue
                if d > pair_reach(a, b):
                    continue
                if seg_blocks(a.x, a.y, a.z, b.x, b.y, b.z):
                    continue
                if locally_redundant(a, b, d):
                    continue
                new_proposals.add(pair)

        ordered_new = sorted(
            new_proposals,
            key=lambda p: _math.hypot(
                node_by_id[p[1]].x - node_by_id[p[0]].x,
                node_by_id[p[1]].y - node_by_id[p[0]].y))

        degree = {nid: 0 for nid in node_by_id}
        final_edges = set()

        for pair in sorted(valid_existing):
            a, b = pair
            if degree[a] >= MAX_NEIGHBORS or degree[b] >= MAX_NEIGHBORS:
                continue
            final_edges.add(pair)
            degree[a] += 1
            degree[b] += 1

        added = 0
        for pair in ordered_new:
            a, b = pair
            if degree[a] >= MAX_NEIGHBORS or degree[b] >= MAX_NEIGHBORS:
                continue
            final_edges.add(pair)
            degree[a] += 1
            degree[b] += 1
            added += 1

        for n in self.nodes:
            n.neighbors = []
        for a, b in sorted(final_edges):
            node_by_id[a].neighbors.append(b)
            node_by_id[b].neighbors.append(a)

        self._push_undo(
            f"Rebuild neighbours for {len(target_ids)} node"
            + ("" if len(target_ids) == 1 else "s"),
            lambda s=snap: self._restore_nodes(s))

        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self.refresh_3d_views(
            rebuild=True, refit=False, reason='rebuild_neighbors')
        scope = (f"{len(target_ids)} selected"
                 if self.selected_nodes else f"all {len(self.nodes)}")
        self.status(
            f"Rebuilt neighbors for {scope} nodes "
            f"(+{added} added, -{removed} invalid removed)")
        self.redraw()

    # === node_editing.py (NodeEditingMixin) ===

    """Node editing operations for AINEditor."""

    # ── 7. NODE EDITING ──────────────────────────────────────────────────────

    def _snap_to_grid(self, wx, wy):
        """Snap world coordinates to the current MED grid spacing if snap is enabled."""
        if not self.grid_snap_nodes.get():
            return wx, wy
        label = self.grid_fixed_spacing_label.get()
        spacing = next((v for l, v in MED_GRID_SPACING_CHOICES if l == label), None)
        if not spacing:
            return wx, wy
        wx_snapped = round(wx / spacing) * spacing
        wy_snapped = round(wy / spacing) * spacing
        return wx_snapped, wy_snapped


    def _toggle_draw_radius_brush(self):
        self._draw_radius_brush.set(not self._draw_radius_brush.get())
        if self._draw_radius_brush.get():
            self.status("Radius brush ON — node spacing follows last placed radius")
        else:
            self.status("Radius brush OFF")

    def _draw_position_occupied(self, wx, wy, threshold=None, respect_existing_radius=False):
        """Return True if a node already exists within threshold meters.
        Threshold defaults to half the current grid spacing, or 0.5m minimum.
        When respect_existing_radius is True, also rejects placement inside
        the inner half of an existing node's radius (prevents small nodes
        stacking on large node centers without blocking edge placement).
        """
        if threshold is None:
            if self.grid_snap_nodes.get():
                label = self.grid_fixed_spacing_label.get()
                spacing = next((v for l,v in MED_GRID_SPACING_CHOICES if l==label), 1.0)
                threshold = max(0.1, spacing * 0.5)
            else:
                threshold = 0.5
        for n in self.nodes:
            d = math.hypot(n.x - wx, n.y - wy)
            if d <= threshold:
                return True
            if respect_existing_radius:
                existing_r = n.b15 / 16.0
                if d <= existing_r * 0.5:
                    return True
        return False


    def _manual_entity_surface_z_at(self, wx, wy):
        """Return the highest valid CModel support surface at world XY.

        This is used only by the explicit ``Place on entities`` manual-placement
        toggle.  It does not affect the generator, existing nodes, or normal
        terrain placement.  Support comes from the same collision-derived
        walkable triangles used elsewhere by the editor, but the query is done
        directly at the clicked XY so platforms/bridges use their actual top
        surface rather than the entity origin Z.
        """
        _game_path = _ns.get('_game_path', None)
        _pff_index_cache = _ns.get('_pff_index_cache', None)
        if not self.entities or not _game_path:
            return None

        gp = _game_path
        try:
            items = _load_items_def(gp, log=None, pff_cache=_pff_index_cache) if gp else {}
        except Exception:
            items = {}

        best_z = None
        best_tid = None
        best_entity = None
        eps = 1.0e-7

        def _projected_z(px, py, triangle):
            try:
                a, b, c = triangle
                ax, ay, az = map(float, a[:3])
                bx, by, bz = map(float, b[:3])
                cx, cy, cz = map(float, c[:3])
            except Exception:
                return None
            den = ((by - cy) * (ax - cx) +
                   (cx - bx) * (ay - cy))
            if abs(den) <= 1.0e-10:
                return None
            wa = ((by - cy) * (px - cx) +
                  (cx - bx) * (py - cy)) / den
            wb = ((cy - ay) * (px - cx) +
                  (ax - cx) * (py - cy)) / den
            wc = 1.0 - wa - wb
            if wa < -eps or wb < -eps or wc < -eps:
                return None
            return wa * az + wb * bz + wc * cz

        for entity_index, e in enumerate(self.entities or ()):
            try:
                if not bool(e.get('is_static')):
                    continue
                tid = int(e.get('type_id', -1))
                if tid < 0:
                    continue
                item = items.get(tid, {}) if isinstance(items, dict) else {}
                if item and classify_item_collision_hint(item) != 'candidate':
                    continue

                # Manual entity placement runs on AINEditor, not the 3D-view
                # class.  Do not call Wireframe3DView._entity_context_footprint_bounds
                # here: the original v1.0 implementation did exactly that, the
                # AttributeError was swallowed by this loop, and every entity was
                # silently skipped.  Use a generous center-distance prefilter and
                # let the actual walkable-triangle barycentric test be the exact
                # footprint check.  This keeps large platforms/pier pieces eligible
                # even when they are much wider than the legacy 4 m obstacle radius.
                ex = float(e.get('x', 0.0))
                ey = float(e.get('y', 0.0))
                ez = float(e.get('z', 0.0))
                heading = float(e.get('heading', 0.0) or 0.0)
                coarse_radius = max(
                    64.0,
                    float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS)) + 4.0)
                if math.hypot(float(wx) - ex, float(wy) - ey) > coarse_radius:
                    continue

                triangles = get_cmodel_walkable_triangles(
                    tid, gp, allow_load=True, log=None)
                if not triangles:
                    continue

                # Collector transform is world = R(-heading) * local + entity.
                # Convert the click back into local space with the inverse R(+h).
                dx = float(wx) - ex
                dy = float(wy) - ey
                angle = math.radians(heading)
                ca = math.cos(angle)
                sa = math.sin(angle)
                local_x = ca * dx - sa * dy
                local_y = sa * dx + ca * dy

                for triangle in triangles:
                    local_z = _projected_z(local_x, local_y, triangle)
                    if local_z is None:
                        continue
                    world_z = ez + float(local_z)
                    if best_z is None or world_z > best_z:
                        best_z = world_z
                        best_tid = tid
                        best_entity = entity_index
            except Exception:
                continue

        if best_z is not None:
            self._debug_note(
                'manual_entity_surface',
                x=round(float(wx), 4), y=round(float(wy), 4),
                surface_z=round(float(best_z), 6),
                type_id=best_tid, entity_index=best_entity)
        return best_z


    def _insert_node(self, wx, wy):
        """Insert one node, with expanded diagnostics for large-map hangs/crashes."""
        if self._generator_edit_blocked():
            return
        t0 = time.perf_counter()
        timings = {}
        self._watchdog_action = 'insert_node:start'
        self._debug_last_insert = {
            'world_x': round(wx, 6),
            'world_y': round(wy, 6),
            'nodes_before': len(self.nodes),
            'entities': len(self.entities),
            'z_filter_on': self.z_filter_on.get(),
        }
        self._debug_note('insert_start', **self._debug_last_insert)

        entity_surface_z = None
        if self.place_nodes_on_entities.get():
            te0 = time.perf_counter()
            self._watchdog_action = 'insert_node:entity_surface_sample'
            entity_surface_z = self._manual_entity_surface_z_at(wx, wy)
            timings['entity_surface_sample_ms'] = round(
                (time.perf_counter() - te0) * 1000, 3)

        if entity_surface_z is not None:
            nz = float(entity_surface_z) + NAV_NODE_Z_LIFT
            self._debug_note(
                'insert_z_from_entity_surface', z=nz,
                surface_height=round(float(entity_surface_z), 6),
                sample_ms=timings.get('entity_surface_sample_ms'))
        elif self.z_filter_on.get():
            try:
                nz = self._get_z_filter_bounds()[0]
            except Exception:
                nz = self.terrain_z
            timings['z_from_filter_ms'] = round((time.perf_counter() - t0) * 1000, 3)
            self._debug_note('insert_z_from_filter', z=nz)
        else:
            tz_terrain = time.perf_counter()
            self._watchdog_action = 'insert_node:terrain_height_sample'
            nz = self._terrain_aware_node_z(wx, wy)
            timings['terrain_height_sample_ms'] = round((time.perf_counter() - tz_terrain) * 1000, 3)
            if nz is not None:
                self._debug_note('insert_z_from_surface', z=nz,
                                 surface_height=round(nz - NAV_NODE_Z_LIFT, 6),
                                 sample_ms=timings['terrain_height_sample_ms'])
            else:
                tz0 = time.perf_counter()
                self._watchdog_action = 'insert_node:build_ground_z_ref'
                z_ref = build_ground_z_ref(self.entities, self.terrain_z) if self.entities else []
                timings['build_ground_z_ref_ms'] = round((time.perf_counter() - tz0) * 1000, 3)
                timings['z_ref_count'] = len(z_ref)
                tz1 = time.perf_counter()
                self._watchdog_action = 'insert_node:nav_node_z'
                nz = nav_node_z(wx, wy, z_ref, self.terrain_z)
                timings['nav_node_z_ms'] = round((time.perf_counter() - tz1) * 1000, 3)
                self._debug_note('insert_z_resolved_fallback', z=nz, z_ref_count=len(z_ref),
                                 build_ground_z_ref_ms=timings.get('build_ground_z_ref_ms'),
                                 nav_node_z_ms=timings.get('nav_node_z_ms'))

        # Apply Z lock if active — override terrain Z
        if self._lock_z.get():
            nz = self._locked_z.get()

        # Node ids are list indices throughout the editor. Derive the new id
        # from the current list length instead of trusting persisted next_id,
        # which may be stale after repeated map loads.
        nid = len(self.nodes)
        self.next_id = nid + 1

        # Apply value lock if active
        if self._lock_values.get():
            _b12 = self._locked_b12.get()
            _b15 = self._locked_b15.get()
        else:
            _b12 = 0
            _b15 = 36
        _b16 = 0

        # Adaptive radius resolver — overrides _b15 if active.  Time it
        # separately: older diagnostics started the snapshot timer before this
        # block, making a slow clearance query look like a slow deepcopy.
        ta0 = time.perf_counter()
        self._watchdog_action = 'insert_node:adaptive_radius'
        _get_cmodel_segments = _ns.get('get_cmodel_segments', None)
        if self.adaptive_radius.get():
            _use_large = self.adaptive_large_r.get()
            _use_xlarge = self.adaptive_xlarge_r.get()
            _adaptive_ceiling = _adaptive_target_b15(
                _b15,
                use_large=_use_large,
                use_xlarge=_use_xlarge,
                values_locked=self._lock_values.get())
            _blocked_fn = getattr(self, "_clearance_blocked_fn", None)
            if _blocked_fn is None:
                _blocked_fn = _make_clearance_blocked_fn(
                    getattr(self, "entities", None),
                    getattr(self, "_game_path", None),
                    get_cmodel_segments=_get_cmodel_segments)
                self._clearance_blocked_fn = _blocked_fn
            if _blocked_fn is None or not self.entities:
                _blocked_fn = None
            _resolved = adaptive_resolve_b15(
                wx, wy, self.nodes, _adaptive_ceiling,
                use_large=_use_large,
                use_xlarge=_use_xlarge,
                blocked_fn=_blocked_fn)
            if _resolved is None:
                timings['adaptive_radius_ms'] = round((time.perf_counter() - ta0) * 1000, 3)
                self.status("Adaptive R: no fit at this position — skipped")
                return
            _b15 = _resolved
        timings['adaptive_radius_ms'] = round((time.perf_counter() - ta0) * 1000, 3)

        node = Node(nid, wx, wy, nz, b12=_b12, b15=_b15, b16=_b16)
        ts0 = time.perf_counter()
        self._watchdog_action = 'insert_node:snapshot_nodes'
        snap = self._snapshot_nodes()
        timings['snapshot_nodes_ms'] = round((time.perf_counter() - ts0) * 1000, 3)

        def _undo_insert(s=snap):
            self.nodes = s

        tu0 = time.perf_counter()
        self._watchdog_action = 'insert_node:push_undo'
        self._push_undo(f'Insert node {nid}', _undo_insert)
        timings['push_undo_ms'] = round((time.perf_counter() - tu0) * 1000, 3)

        self.nodes.append(node)

        th0 = time.perf_counter()
        self._watchdog_action = 'insert_node:rebuild_hit_tester'
        self._rebuild_hit_tester()
        timings['rebuild_hit_tester_ms'] = round((time.perf_counter() - th0) * 1000, 3)

        tr0 = time.perf_counter()
        self._watchdog_action = 'insert_node:select_node'
        self.select_node(nid)
        timings['select_node_ms'] = round((time.perf_counter() - tr0) * 1000, 3)

        self.status(f"Inserted node {nid} at ({wx:.2f}, {wy:.2f}, Z {nz:.3f})")

        ts1 = time.perf_counter()
        self._watchdog_action = 'insert_node:update_stats'
        self._update_stats()
        timings['update_stats_ms'] = round((time.perf_counter() - ts1) * 1000, 3)

        total_ms = round((time.perf_counter() - t0) * 1000, 3)
        timings['total_insert_ms'] = total_ms
        self._debug_last_insert.update({
            'node_id': nid,
            'z': round(nz, 6),
            'nodes_after': len(self.nodes),
            'timings': timings,
        })
        self._debug_note('insert_done', node_id=nid, total_insert_ms=total_ms)
        self._watchdog_action = 'idle'

        # If node placement succeeds but takes long enough to feel like a freeze,
        # write a diagnostic entry without showing an error dialog. This is the
        # exact case that is hard to understand from a normal traceback-only log.
        if total_ms > 500:
            self._log_crash(
                'slow_insert_node',
                self.bms_path or self.project_path or '<unsaved>',
                f'Node insertion completed but was slow: {total_ms:.1f} ms\n',
                extra={'insert': self._debug_last_insert}
            )


    def _nudge_node(self, dx, dy):
        """Move selected node by dx/dy world units. Arrow keys = 1m, Shift = 0.1m.
        Coalesces rapid nudges into a single undo entry."""
        if self._generator_edit_blocked():
            return
        if self.selected_id is None or self.mode.get() != 'edit':
            return
        node = self.nodes[self.selected_id]
        # Capture pre-nudge position if this is the start of a nudge sequence
        if not hasattr(self, '_nudge_origin') or self._nudge_origin is None:
            self._nudge_origin = (node.x, node.y, self.selected_id)
        node.x += dx
        node.y += dy
        self._rebuild_hit_tester()
        self._update_inspector()
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=False, reason='nudge_node')
        # Cancel previous coalesce timer and restart
        if hasattr(self, '_nudge_after') and self._nudge_after:
            self.after_cancel(self._nudge_after)
        self._nudge_after = self.after(400, self._nudge_commit)


    def _nudge_commit(self):
        """Commit a coalesced nudge sequence to undo stack."""
        self._nudge_after = None
        if not hasattr(self, '_nudge_origin') or self._nudge_origin is None:
            return
        ox, oy, nid = self._nudge_origin
        self._nudge_origin = None
        if nid >= len(self.nodes):
            return
        node = self.nodes[nid]
        if node.x == ox and node.y == oy:
            return
        def _undo(ox=ox, oy=oy, nid=nid):
            if nid < len(self.nodes):
                self.nodes[nid].x = ox
                self.nodes[nid].y = oy
                self._rebuild_hit_tester()
                self._update_inspector()
                self.redraw()
        self._push_undo(f'Nudge node {nid}', _undo)


    def _focus_is_text_input(self):
        widget = self.focus_get()
        if widget is None:
            return False
        try:
            return widget.winfo_class() in {
                'Entry', 'TEntry', 'Text', 'Spinbox', 'TSpinbox',
            }
        except Exception:
            return False


    def _on_copy_shortcut(self, _event=None):
        if self._focus_is_text_input():
            return None
        self._copy_nodes()
        return 'break'


    def _on_paste_shortcut(self, _event=None):
        if self._focus_is_text_input():
            return None
        self._paste_nodes()
        return 'break'


    def _copy_nodes(self):
        """Ctrl+C — copy node values and relative X/Y positions."""
        if not self.selected_nodes:
            self.status("Nothing selected to copy")
            return
        sel = [self.nodes[i] for i in sorted(self.selected_nodes) if i < len(self.nodes)]
        if not sel:
            return
        # Centroid of selection
        cx = sum(n.x for n in sel) / len(sel)
        cy = sum(n.y for n in sel) / len(sel)
        # Map old id -> index in clipboard for preserving internal connections
        id_to_idx = {n.id: i for i, n in enumerate(sel)}
        self._node_clipboard = []
        for n in sel:
            internal_neighbors = [id_to_idx[nb] for nb in n.neighbors if nb in id_to_idx]
            self._node_clipboard.append({
                'b12': n.b12, 'b13': n.b13, 'b14': n.b14,
                'b15': n.b15, 'b16': n.b16, 'b17': n.b17, 'b18': n.b18,
                'dx': n.x - cx, 'dy': n.y - cy, 'z': n.z,
                'internal_neighbors': internal_neighbors,
            })
        self.status(f"Copied {len(sel)} node(s)")


    def _paste_nodes(self):
        """Ctrl+V — paste copied nodes at current cursor position."""
        if self._generator_edit_blocked():
            return
        if not self._node_clipboard:
            self.status("Nothing to paste")
            return
        paste_t0 = time.perf_counter()
        self._watchdog_action = 'paste_nodes'
        wx = getattr(self, '_cursor_wx', 0.0)
        wy = getattr(self, '_cursor_wy', 0.0)
        base_id = len(self.nodes)
        self._debug_note(
            'paste_start',
            clipboard_nodes=len(self._node_clipboard),
            nodes_before=base_id,
            world_x=wx,
            world_y=wy,
            view_mode=getattr(self, '_view_mode', '2d'),
        )
        stage_t0 = time.perf_counter()
        snap = self._snapshot_nodes()
        snapshot_ms = (time.perf_counter() - stage_t0) * 1000.0
        new_nodes = []
        for i, entry in enumerate(self._node_clipboard):
            px = wx + entry['dx']
            py = wy + entry['dy']
            z = entry.get('z')
            if z is None:
                z_ref = build_ground_z_ref(self.entities, self.terrain_z)
                z = nav_node_z(px, py, z_ref, self.terrain_z)
            n = Node(base_id + i, px, py, z,
                     b12=entry['b12'], b13=entry['b13'], b14=entry['b14'],
                     b15=entry['b15'], b16=entry['b16'], b17=entry['b17'], b18=entry['b18'])
            # Restore internal connections using new IDs
            n.neighbors = [base_id + j for j in entry['internal_neighbors']]
            new_nodes.append(n)
        self.nodes.extend(new_nodes)
        self.next_id = len(self.nodes)
        # Select pasted nodes
        self.selected_nodes = {n.id for n in new_nodes}
        self.selected_id = new_nodes[0].id if new_nodes else None
        self._push_undo('Paste nodes', lambda s=snap: self._restore_nodes(s))
        stage_t0 = time.perf_counter()
        self._rebuild_hit_tester()
        hit_tester_ms = (time.perf_counter() - stage_t0) * 1000.0
        self._update_inspector()
        self._update_stats()
        stage_t0 = time.perf_counter()
        self.redraw()
        redraw_ms = (time.perf_counter() - stage_t0) * 1000.0
        stage_t0 = time.perf_counter()
        self.refresh_3d_views(rebuild=True, refit=False, reason='paste_nodes')
        refresh_3d_ms = (time.perf_counter() - stage_t0) * 1000.0
        total_ms = (time.perf_counter() - paste_t0) * 1000.0
        self._debug_note(
            'paste_done',
            pasted_nodes=len(new_nodes),
            nodes_after=len(self.nodes),
            snapshot_ms=snapshot_ms,
            hit_tester_ms=hit_tester_ms,
            redraw_ms=redraw_ms,
            refresh_3d_ms=refresh_3d_ms,
            total_ms=total_ms,
        )
        self._watchdog_action = 'idle'
        self.status(f"Pasted {len(new_nodes)} node(s)")


    def _delete_selected_node(self):
        if self._generator_edit_blocked():
            return
        if not self.selected_nodes:
            return
        try:
            if len(self.selected_nodes) == 1:
                self._delete_node(next(iter(self.selected_nodes)))
            else:
                self._delete_nodes_bulk(set(self.selected_nodes))
        except Exception:
            import traceback
            self._log_crash('delete_selected_node', self.bms_path or self.project_path or '<unsaved>', traceback.format_exc())
            messagebox.showerror("Delete Node Error", "The editor hit an error while deleting a node. Crash details were logged to ain_editor_crash.log.")


    def _reindex_locked_3d_set(self, removed_ids):
        """Update locked 3D node sets after node deletion and reindexing."""
        if not getattr(self, '_locked_3d_node_set_active', False):
            return
        removed = set(removed_ids)
        def _reindex(ids):
            new = set()
            for n in ids:
                if n in removed:
                    continue
                shift = sum(1 for r in removed if r < n)
                new.add(n - shift)
            return new
        self._locked_3d_node_ids = _reindex(self._locked_3d_node_ids)
        self._locked_3d_anchor_node_ids = _reindex(self._locked_3d_anchor_node_ids)
        if not self._locked_3d_node_ids:
            self._locked_3d_node_set_active = False
            self._locked_3d_node_scope_note = ''
            self._locked_3d_node_reset_reason = 'all locked nodes deleted'

    def _snapshot_3d_lock_state(self):
        if not getattr(self, '_locked_3d_node_set_active', False):
            return None
        return {
            'locked_ids': set(self._locked_3d_node_ids),
            'anchor_ids': set(self._locked_3d_anchor_node_ids),
            'scope_note': str(self._locked_3d_node_scope_note or ''),
            'reset_reason': str(self._locked_3d_node_reset_reason or ''),
        }

    def _restore_3d_lock_state(self, lock_snap):
        if lock_snap is None:
            return
        valid = set(range(len(self.nodes)))
        locked = lock_snap['locked_ids'] & valid
        anchor = lock_snap['anchor_ids'] & valid
        if locked:
            self._locked_3d_node_set_active = True
            self._locked_3d_node_ids = locked
            self._locked_3d_anchor_node_ids = anchor or set(locked)
            self._locked_3d_node_scope_note = lock_snap['scope_note']
            self._locked_3d_node_reset_reason = lock_snap['reset_reason']
            self._history_lock_restored = True

    def _delete_node(self, nid):
        if self._generator_edit_blocked():
            return
        if nid < 0 or nid >= len(self.nodes): return
        snap = self._snapshot_nodes()
        lock_snap = self._snapshot_3d_lock_state()
        def _undo_delete(s=snap, ls=lock_snap):
            self._restore_nodes(s)
            self._restore_3d_lock_state(ls)
        self._push_undo(f'Delete node {nid}', _undo_delete)
        self._do_delete_node(nid)
        self._reindex_locked_3d_set({nid})
        self.selected_nodes = set()
        self.selected_id = None
        if self._connecting_from == nid:
            self._connecting_from = None
            self.canvas.configure(cursor='crosshair')
        elif self._connecting_from is not None and self._connecting_from > nid:
            self._connecting_from -= 1
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self._refresh_zone_list() if hasattr(self, '_zone_listbox') else None
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=False, reason='delete_node')


    def _do_delete_node(self, nid):
        """Low-level single node delete — no undo push, no redraw."""
        for node in self.nodes:
            node.neighbors = [
                nb - 1 if nb > nid else nb
                for nb in node.neighbors
                if nb != nid
            ]
        self.nodes.pop(nid)
        for i, node in enumerate(self.nodes):
            node.id = i
        self.next_id = len(self.nodes)
        for meta in getattr(self, 'zone_meta', {}).values():
            old_owned = set(meta.get('nodes') or [])
            if old_owned:
                meta['nodes'] = {
                    (n - 1 if n > nid else n)
                    for n in old_owned if n != nid
                }


    def _delete_nodes_bulk(self, indices):
        """Delete a set of node indices atomically with one undo entry."""
        if self._generator_edit_blocked():
            return
        if not indices: return
        snap = self._snapshot_nodes()
        lock_snap = self._snapshot_3d_lock_state()
        def _undo_bulk(s=snap, ls=lock_snap):
            self._restore_nodes(s)
            self._restore_3d_lock_state(ls)
        self._push_undo(f'Delete {len(indices)} nodes', _undo_bulk)
        removed = set(indices)
        # Delete from highest index down so indices stay valid
        for nid in sorted(indices, reverse=True):
            self._do_delete_node(nid)
        self._reindex_locked_3d_set(removed)
        self.selected_nodes = set()
        self.selected_id = None
        self._connecting_from = None
        self.canvas.configure(cursor='crosshair')
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self._refresh_zone_list() if hasattr(self, '_zone_listbox') else None
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=False, reason='delete_nodes_bulk')


    def _delete_nodes_by_ids(self, node_ids):
        """Delete node IDs and reindex nodes/neighbors/zones. Returns number deleted."""
        if self._generator_edit_blocked():
            return 0
        remove = {int(n) for n in (node_ids or []) if isinstance(n, int) or str(n).isdigit()}
        remove = {n for n in remove if 0 <= n < len(self.nodes)}
        if not remove:
            return 0

        old_to_new = {}
        new_nodes = []
        for old_id, node in enumerate(self.nodes):
            if old_id in remove:
                continue
            new_id = len(new_nodes)
            old_to_new[old_id] = new_id
            node.id = new_id
            new_nodes.append(node)

        for node in new_nodes:
            node.neighbors = [
                old_to_new[nb] for nb in node.neighbors
                if nb in old_to_new and old_to_new[nb] != node.id
            ][:MAX_NEIGHBORS]

        self.nodes = new_nodes
        self.next_id = len(self.nodes)

        for meta in self.zone_meta.values():
            old_owned = set(meta.get('nodes') or [])
            meta['nodes'] = {old_to_new[n] for n in old_owned if n in old_to_new}

        self.selected_nodes = {old_to_new[n] for n in self.selected_nodes if n in old_to_new}
        self.selected_id = old_to_new.get(self.selected_id) if self.selected_id in old_to_new else None
        if getattr(self, '_locked_3d_node_set_active', False):
            self._locked_3d_node_ids = {old_to_new[n] for n in self._locked_3d_node_ids if n in old_to_new}
            self._locked_3d_anchor_node_ids = {old_to_new[n] for n in self._locked_3d_anchor_node_ids if n in old_to_new}
            if not self._locked_3d_node_ids:
                self._locked_3d_node_set_active = False
                self._locked_3d_node_scope_note = ''
                self._locked_3d_node_reset_reason = 'all locked nodes deleted'
        return len(remove)


    def _remove_selected_connections(self):
        """Remove every edge touching the currently selected node(s).

        Single selection cuts all of that node's links. Multi-selection cuts
        every edge touching any selected node, including selected-to-selected
        and selected-to-unselected edges. Node positions/data are unchanged.
        """
        if self._generator_edit_blocked():
            return

        selected = {
            int(n) for n in (getattr(self, 'selected_nodes', set()) or set())
            if 0 <= int(n) < len(self.nodes)
        }
        if not selected:
            try:
                sid = int(self.selected_id) if self.selected_id is not None else None
            except Exception:
                sid = None
            if sid is not None and 0 <= sid < len(self.nodes):
                selected.add(sid)

        if not selected:
            self.status("Remove Selected Connections: select one or more nodes first.")
            return

        # Count unique undirected edges before changing anything.
        touched_edges = set()
        for nid in selected:
            node = self.nodes[nid]
            for nb_id in list(node.neighbors or []):
                try:
                    nb_id = int(nb_id)
                except Exception:
                    continue
                if 0 <= nb_id < len(self.nodes) and nb_id != nid:
                    touched_edges.add((min(nid, nb_id), max(nid, nb_id)))

        # Also catch malformed one-way links pointing INTO a selected node.
        for node in self.nodes:
            if node.id in selected:
                continue
            for nb_id in list(node.neighbors or []):
                try:
                    nb_id = int(nb_id)
                except Exception:
                    continue
                if nb_id in selected:
                    touched_edges.add((min(int(node.id), nb_id),
                                       max(int(node.id), nb_id)))

        if not touched_edges:
            self.status(
                f"Remove Selected Connections: selected "
                f"{len(selected)} node(s) have no connections.")
            return

        snap = self._snapshot_nodes()

        # Strip every selected ID from all other neighbor lists, then clear
        # selected nodes themselves. This guarantees symmetric disconnection
        # even if the source graph already contained one-way links.
        for node in self.nodes:
            if node.id in selected:
                node.neighbors = []
            else:
                node.neighbors = [
                    nb for nb in list(node.neighbors or [])
                    if int(nb) not in selected
                ]

        self._push_undo(
            f"Remove connections from {len(selected)} selected node"
            + ("" if len(selected) == 1 else "s"),
            lambda s=snap: self._restore_nodes(s))

        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self.redraw()
        self.refresh_3d_views(
            rebuild=True, refit=False, reason='remove_selected_connections')

        self.status(
            f"Removed {len(touched_edges)} connection"
            f"{'' if len(touched_edges) == 1 else 's'} touching "
            f"{len(selected)} selected node"
            f"{'' if len(selected) == 1 else 's'} — Ctrl+Z restores.")


    def _fix_oneway_connections(self):
        """Make all connections bidirectional in one pass."""
        if self._generator_edit_blocked():
            return
        snap = self._snapshot_nodes()
        fixed = 0
        id_to_node = {n.id: n for n in self.nodes}
        for n in self.nodes:
            for nb_id in list(n.neighbors):
                if nb_id not in id_to_node: continue
                nb = id_to_node[nb_id]
                if n.id not in nb.neighbors:
                    if len(nb.neighbors) < MAX_NEIGHBORS:
                        nb.neighbors.append(n.id)
                        fixed += 1
        if fixed:
            self._push_undo('Fix one-way connections', lambda s=snap: self._restore_nodes(s))
            self._rebuild_hit_tester()
            self._update_stats()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='fix_oneway')
            self.status(f"Fixed {fixed} one-way connections — all now bidirectional")
        else:
            self.status("No one-way connections found")


    def _add_connection(self, a, b, refresh_reason='add_connection'):
        if self._generator_edit_blocked():
            return
        na = self.nodes[a]; nb2 = self.nodes[b]
        snap = self._snapshot_nodes()
        self._push_undo(f'Connect {a}↔{b}', lambda s=snap: self._restore_nodes(s))
        connect_nodes(na, nb2)
        self._update_inspector()
        self.refresh_3d_views(rebuild=True, refit=False, reason=refresh_reason)


    def _remove_neighbor(self):
        if self._generator_edit_blocked():
            return
        if self.selected_id is None: return
        sel = self._nb_list.curselection()
        if not sel: return
        txt = self._nb_list.get(sel[0])
        try:
            nb_id = int(txt.split()[1])
        except: return
        node = self.nodes[self.selected_id]
        snap = self._snapshot_nodes()
        self._push_undo(f'Disconnect {self.selected_id}↔{nb_id}',
                        lambda s=snap: self._restore_nodes(s))
        if nb_id in node.neighbors: node.neighbors.remove(nb_id)
        other = self.nodes[nb_id]
        if self.selected_id in other.neighbors: other.neighbors.remove(self.selected_id)
        self._update_inspector()
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=False, reason='remove_neighbor')



    def _hide_connect_id_overlay(self):
        """Close the editor-owned Connect-with-ID panel."""
        ov = getattr(self, '_connect_id_overlay', None)
        self._connect_id_overlay = None
        self._connect_id_source = None
        try:
            if ov is not None and ov.winfo_exists():
                ov.destroy()
        except Exception:
            pass


    def _show_connect_id_overlay(self):
        """Open a movable in-editor panel that connects the selected node by ID."""
        if self._generator_edit_blocked():
            return
        source = getattr(self, 'selected_id', None)
        try:
            source = int(source)
        except Exception:
            source = None
        if source is None or not (0 <= source < len(self.nodes)):
            self.status("Select a node first")
            return

        # Re-open against the current selection instead of keeping stale source state.
        self._hide_connect_id_overlay()
        self._connect_id_source = source
        try:
            self._connect_id_var.set("")
        except Exception:
            self._connect_id_var = tk.StringVar(value="")

        try:
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        except Exception:
            light = False

        if light:
            pal = {
                'bg': '#f4f4f4',
                'title': '#e2e2e2',
                'border': '#787878',
                'text': '#1c1c1c',
                'dim': '#555555',
                'entry': '#ffffff',
                'entry_border': '#9a9a9a',
                'connect': '#176a8a',
                'connect_active': '#2385aa',
                'cancel': '#d7d7d7',
                'cancel_active': '#c8c8c8',
            }
        else:
            pal = {
                'bg': '#202020',
                'title': '#2a2a2a',
                'border': '#666666',
                'text': '#eeeeee',
                'dim': '#aaaaaa',
                'entry': '#111111',
                'entry_border': '#666666',
                'connect': '#005b7d',
                'connect_active': '#087da5',
                'cancel': '#3a3a3a',
                'cancel_active': '#4b4b4b',
            }

        ov = tk.Frame(
            self, bg=pal['bg'], bd=0,
            highlightthickness=1, highlightbackground=pal['border'])
        self._connect_id_overlay = ov
        panel_w, panel_h = 318, 148
        ov.place(relx=0.5, rely=0.5, anchor='center',
                 width=panel_w, height=panel_h)
        ov.lift()

        title = tk.Frame(ov, bg=pal['title'], cursor='fleur')
        title.pack(side='top', fill='x')
        tk.Label(
            title, text="Connect with ID",
            bg=pal['title'], fg=pal['text'],
            font=('Consolas', 9, 'bold'), anchor='w',
            padx=8, pady=5).pack(side='left', fill='x', expand=True)
        tk.Label(
            title, text=f"Source: {source}",
            bg=pal['title'], fg=pal['dim'],
            font=('Consolas', 8), padx=8).pack(side='right')

        body = tk.Frame(ov, bg=pal['bg'])
        body.pack(side='top', fill='both', expand=True, padx=10, pady=(9, 8))

        row = tk.Frame(body, bg=pal['bg'])
        row.pack(fill='x')
        tk.Label(
            row, text="Target node ID:",
            bg=pal['bg'], fg=pal['text'],
            font=('Consolas', 9), anchor='w').pack(side='left')

        entry_wrap = tk.Frame(row, bg=pal['entry_border'], bd=0)
        entry_wrap.pack(side='right', fill='x', expand=True, padx=(10, 0))
        entry = tk.Entry(
            entry_wrap, textvariable=self._connect_id_var,
            bg=pal['entry'], fg=pal['text'],
            insertbackground=pal['text'],
            font=('Consolas', 10), relief='flat', bd=0)
        entry.pack(fill='x', padx=1, pady=1, ipady=2)

        hint = tk.Label(
            body, text="Enter the ID of the node you want to connect to.",
            bg=pal['bg'], fg=pal['dim'],
            font=('Consolas', 8), anchor='w')
        hint.pack(fill='x', pady=(5, 7))

        buttons = tk.Frame(body, bg=pal['bg'])
        buttons.pack(fill='x')

        def _commit():
            source_id = getattr(self, '_connect_id_source', None)
            try:
                source_id = int(source_id)
                target_id = int(str(self._connect_id_var.get()).strip())
            except Exception:
                self.status("Connect with ID: enter a valid numeric node ID")
                try:
                    entry.focus_set()
                    entry.selection_range(0, 'end')
                except Exception:
                    pass
                return

            if not (0 <= source_id < len(self.nodes)):
                self.status("Connect with ID: source node no longer exists")
                self._hide_connect_id_overlay()
                return
            if not (0 <= target_id < len(self.nodes)):
                self.status(f"Connect with ID: node {target_id} does not exist")
                try:
                    entry.focus_set()
                    entry.selection_range(0, 'end')
                except Exception:
                    pass
                return
            if source_id == target_id:
                self.status("Connect with ID: a node cannot connect to itself")
                return

            already = target_id in self.nodes[source_id].neighbors
            if not already:
                reason = ('3d_add_connection'
                          if getattr(self, '_view_mode', '2d') == '3d'
                          else 'add_connection')
                self._add_connection(source_id, target_id, refresh_reason=reason)
                try:
                    self.redraw()
                    self._update_stats()
                except Exception:
                    pass
                self.status(f"Connected node {source_id} ↔ {target_id}")
            else:
                self.status(f"Node {source_id} is already connected to node {target_id}")

            self._hide_connect_id_overlay()

        tk.Button(
            buttons, text="Cancel", command=self._hide_connect_id_overlay,
            bg=pal['cancel'], fg=pal['text'],
            activebackground=pal['cancel_active'], activeforeground=pal['text'],
            font=('Consolas', 8), relief='flat', padx=12, pady=2
        ).pack(side='right', padx=(5, 0))
        tk.Button(
            buttons, text="Connect", command=_commit,
            bg=pal['connect'], fg='white',
            activebackground=pal['connect_active'], activeforeground='white',
            font=('Consolas', 8, 'bold'), relief='flat', padx=14, pady=2
        ).pack(side='right')

        # Dragging the title bar repositions the panel inside the editor window.
        drag = {'dx': 0, 'dy': 0}

        def _drag_start(e):
            try:
                drag['dx'] = int(e.x_root - ov.winfo_rootx())
                drag['dy'] = int(e.y_root - ov.winfo_rooty())
            except Exception:
                drag['dx'] = drag['dy'] = 0

        def _drag_move(e):
            try:
                x = int(e.x_root - self.winfo_rootx() - drag['dx'])
                y = int(e.y_root - self.winfo_rooty() - drag['dy'])
                max_x = max(0, int(self.winfo_width()) - panel_w)
                max_y = max(0, int(self.winfo_height()) - panel_h)
                x = max(0, min(max_x, x))
                y = max(0, min(max_y, y))
                ov.place_configure(relx=0, rely=0, x=x, y=y, anchor='nw')
                ov.lift()
            except Exception:
                pass

        for w in (title,) + tuple(title.winfo_children()):
            try:
                w.bind('<ButtonPress-1>', _drag_start)
                w.bind('<B1-Motion>', _drag_move)
            except Exception:
                pass

        entry.bind('<Return>', lambda _e: (_commit(), 'break')[1])
        entry.bind('<Escape>', lambda _e: (self._hide_connect_id_overlay(), 'break')[1])
        ov.bind('<Escape>', lambda _e: (self._hide_connect_id_overlay(), 'break')[1])

        # Focus the freshly-created entry immediately.  This used to be queued
        # with after_idle(), which created a real focus race: if the user opened
        # Connect with ID and clicked the 2D canvas before the idle callback ran,
        # _on_lclick() correctly focused the canvas, then the stale idle callback
        # stole focus back into this Entry.  Canvas-only shortcuts (F1/F2/F3,
        # U/F/G/PageUp/PageDown/Ctrl+Arrow) then appeared dead despite the click.
        # Immediate focus preserves the intended auto-focus without allowing an
        # older deferred action to override a newer explicit canvas click.
        try:
            entry.focus_set()
            entry.selection_range(0, 'end')
        except Exception:
            pass


    def _start_connect(self):
        if self.selected_id is None:
            self.status("Select a node first"); return
        self._start_connect_from(self.selected_id)


    def _start_connect_from(self, nid):
        self._connecting_from = nid
        self.canvas.configure(cursor='plus')
        self.status(f"Click another node to connect to node {nid}. Esc to cancel.")


    def _cancel_connect(self):
        if self._connecting_from is not None:
            self._connecting_from = None
            self.canvas.configure(cursor='crosshair')
            self.status("Connection cancelled.")


    def _update_inspector(self):
        nid = self.selected_id
        if nid is None or nid >= len(self.nodes):
            self._v_id.set("None")
            for v in [self._v_x,self._v_y,self._v_z,self._v_b12,self._v_b13,
                      self._v_b14,self._v_b15,self._v_b16,self._v_b17,self._v_b18]:
                v.set("")
            self._terrain_z_label.configure(text="")
            self._nb_list.delete(0,'end')
            self._neighbors_label.configure(text="Neighbors: 0")
            return
        node = self.nodes[nid]
        self._v_id.set(str(nid))
        self._v_x.set(f"{node.x:.4f}")
        self._v_y.set(f"{node.y:.4f}")
        self._v_z.set(f"{node.z:.4f}")
        _tz = _terrain_sample_height(self.terrain_info, node.x, node.y)
        if _tz is None:
            _tz = self.terrain_z
        self._terrain_z_label.configure(text=f"terrain:{_tz:.3f}")
        self._v_b12.set(str(node.b12))
        self._v_b13.set(str(node.b13))
        self._v_b14.set(str(node.b14))
        self._v_b15.set(str(node.b15))
        self._v_b16.set(str(node.b16))
        self._v_b17.set(str(node.b17))
        self._v_b18.set(str(node.b18))
        # Update zone swatch
        z = node.b14
        color = ZONE_COLORS[z % len(ZONE_COLORS)]
        self._zone_swatch.configure(bg=color)
        # Neighbor list — show ALL nodes that reference this node (bidirectional)
        self._nb_list.delete(0,'end')
        nid = node.id
        # Collect: own neighbors + any node that lists this node as neighbor
        shown = set()
        all_refs = list(node.neighbors)
        for other in self.nodes:
            if other.id != nid and nid in other.neighbors and other.id not in all_refs:
                all_refs.append(other.id)
        self._neighbors_label.configure(text=f"Neighbors: {len(set(all_refs))}")
        for nb_id in all_refs:
            if nb_id in shown: continue
            shown.add(nb_id)
            if 0 <= nb_id < len(self.nodes):
                # Mark one-way connections
                is_mutual = nb_id in node.neighbors and nid in self.nodes[nb_id].neighbors
                tag = "" if is_mutual else " →only"
                self._nb_list.insert('end', f"Node {nb_id}{tag}")
            else:
                self._nb_list.insert('end', f"Invalid {nb_id}")


    def _nudge_coord(self, var, delta):
        try:
            val = float(var.get())
        except ValueError:
            return
        var.set(f"{val + delta:.4f}")
        self._apply_node_changes()

    def _apply_node_changes(self):
        """Apply inspector edits safely.

        Multi-select behavior:
          - detect which inspector fields changed compared to the active node
          - ask whether to apply only those changed fields to all selected nodes
          - never blindly copy every active-node field onto the selection

        This fixes the old problem where editing b12 on a multi-selection also
        copied radius/b13/b14/etc. from the active node to every selected node.
        """
        # Determine which nodes to edit — multi-select or single
        if len(self.selected_nodes) > 1:
            targets = [nid for nid in self.selected_nodes if 0 <= nid < len(self.nodes)]
        elif self.selected_id is not None and 0 <= self.selected_id < len(self.nodes):
            targets = [self.selected_id]
        else:
            return

        if not targets:
            return

        active_id = self.selected_id if self.selected_id in targets else targets[0]
        active = self.nodes[active_id]

        try:
            # Parse values from inspector
            parsed = {
                'x':   float(self._v_x.get()),
                'y':   float(self._v_y.get()),
                'z':   float(self._v_z.get()),
                'b12': int(self._v_b12.get()) & 0xFF,
                'b13': int(self._v_b13.get()) & 0xFF,
                'b14': int(self._v_b14.get()),
                'b15': max(1, min(255, int(self._v_b15.get()))),
                'b16': int(self._v_b16.get()) & 0xFF,
                'b17': int(self._v_b17.get()) & 0xFF,
                'b18': int(self._v_b18.get()) & 0xFF,
            }
            if not 0 <= parsed['b14'] <= 255:
                raise ValueError('Takedown Zone must be between 0 and 255')

            if len(targets) == 1:
                snap = self._snapshot_nodes()
                node = self.nodes[targets[0]]
                for field, value in parsed.items():
                    setattr(node, field, value)
                self._push_undo(f'Edit node {targets[0]}',
                                lambda s=snap: self._restore_nodes(s))
                self.status(f"Applied changes to node {targets[0]}")
                self._rebuild_hit_tester()
                self._update_inspector()
                self.redraw()
                self.refresh_3d_views(rebuild=True, refit=False, reason='inspector_apply')
                return

            # Multi-select: only changed fields should propagate.
            changed = []
            for field, value in parsed.items():
                old = getattr(active, field)
                if isinstance(value, float):
                    try:
                        if abs(float(old) - value) > 0.00005:
                            changed.append(field)
                    except Exception:
                        changed.append(field)
                else:
                    if int(old) != int(value):
                        changed.append(field)

            if not changed:
                self.status("No changed inspector values to apply")
                return

            try:
                _dev = bool(self.dev_mode.get())
            except Exception:
                _dev = False
            if _dev:
                pretty = {
                    'x': 'X', 'y': 'Y', 'z': 'Z',
                    'b12': 'b12', 'b13': 'b13', 'b14': 'b14', 'b15': 'b15/radius',
                    'b16': 'b16', 'b17': 'b17', 'b18': 'b18',
                }
            else:
                pretty = {
                    'x': 'X', 'y': 'Y', 'z': 'Z',
                    'b12': 'Control', 'b13': 'Takedown (var1)', 'b14': 'Takedown Zone',
                    'b15': 'Radius', 'b16': 'Direction', 'b17': 'Takedown (var2)', 'b18': 'b18',
                }
            changed_txt = ", ".join(pretty.get(f, f) for f in changed)

            answer = messagebox.askyesnocancel(
                "Apply to selected nodes?",
                f"{len(targets)} nodes are selected.\n\n"
                f"Changed value(s): {changed_txt}\n\n"
                "Yes: apply ONLY these changed value(s) to all selected nodes.\n"
                "No: apply ONLY these changed value(s) to the active node only.\n"
                "Cancel: do nothing."
            )

            if answer is None:
                self.status("Apply cancelled")
                self._update_inspector()
                return

            snap = self._snapshot_nodes()

            if answer is True:
                apply_targets = targets
                undo_label = f"Edit {len(targets)} nodes ({changed_txt})"
                status_target = f"{len(targets)} selected nodes"
            else:
                apply_targets = [active_id]
                undo_label = f"Edit node {active_id} ({changed_txt})"
                status_target = f"active node {active_id}"

            for nid in apply_targets:
                if 0 <= nid < len(self.nodes):
                    node = self.nodes[nid]
                    for field in changed:
                        setattr(node, field, parsed[field])

            self._push_undo(undo_label, lambda s=snap: self._restore_nodes(s))
            self.status(f"Applied {changed_txt} to {status_target}")

            # X/Y/Z changes affect hit testing and selection proximity.
            if any(f in changed for f in ('x', 'y', 'z')):
                self._rebuild_hit_tester()

            self._update_inspector()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='inspector_apply')

        except ValueError as e:
            self.status(f"Error: {e}")

    def _on_zone_change(self, *args):
        try:
            z = int(self._v_b14.get()) % len(ZONE_COLORS)
            self._zone_swatch.configure(bg=ZONE_COLORS[z])
        except: pass

    def _update_paint_tool_ui(self):
        try:
            radius = float(self._paint_radius.get())
        except Exception:
            radius = 5.0
        try:
            zone = int(self._paint_zone.get())
        except Exception:
            zone = 0
        zone_color = ZONE_COLORS[zone % len(ZONE_COLORS)]
        active = str(self.mode.get()) == 'paint'

        try:
            light = bool(self.light_panels.get())
        except Exception:
            light = False

        if light:
            row_bg = '#fcfcfc'
            text_fg = '#1c1c1c'
            dim_fg = '#6b6b6b'
            inactive_bg = '#ededed'
            idle_border = '#c2c2c2'

            # The zone colours are intentionally bright canvas colours. Darken
            # only the selected row's text so it remains legible on white while
            # leaving the colour swatch and active badge faithful to the zone.
            try:
                r = int(zone_color[1:3], 16)
                g = int(zone_color[3:5], 16)
                b = int(zone_color[5:7], 16)
                selected_fg = f'#{int(r * 0.55):02x}{int(g * 0.55):02x}{int(b * 0.55):02x}'
            except Exception:
                selected_fg = '#006644'
        else:
            row_bg = C_PANEL2
            text_fg = C_TEXT
            dim_fg = C_DIM
            inactive_bg = C_PANEL
            idle_border = C_BORDER
            selected_fg = zone_color

        try:
            if hasattr(self, '_paint_radius_value_label'):
                self._paint_radius_value_label.configure(text=f'{radius:.1f}')
        except Exception:
            pass

        try:
            if hasattr(self, '_paint_active_badge'):
                if active:
                    self._paint_active_badge.configure(text='ACTIVE', bg=zone_color, fg='black')
                else:
                    self._paint_active_badge.configure(
                        text='INACTIVE', bg=inactive_bg, fg=dim_fg)
        except Exception:
            pass

        try:
            if hasattr(self, '_tiler_painter_box'):
                border = zone_color if active else idle_border
                self._tiler_painter_box.configure(highlightbackground=border, highlightcolor=border)
        except Exception:
            pass

        rows = getattr(self, '_paint_zone_rows', {}) or {}
        labels = getattr(self, '_paint_zone_row_labels', {}) or {}
        swatches = getattr(self, '_paint_zone_swatch_labels', {}) or {}
        for zid, row in rows.items():
            selected = (zid == zone)
            border = zone_color if selected else C_PANEL2
            if light and not selected:
                border = row_bg
            try:
                row.configure(bg=row_bg, highlightbackground=border, highlightcolor=border)
            except Exception:
                pass
            rb = labels.get(zid)
            if rb is not None:
                try:
                    rb.configure(
                        bg=row_bg,
                        fg=(selected_fg if selected else text_fg),
                        activebackground=row_bg,
                        activeforeground=(selected_fg if selected else text_fg),
                        selectcolor=ZONE_COLORS[zid % len(ZONE_COLORS)],
                    )
                except Exception:
                    pass
            sw = swatches.get(zid)
            if sw is not None:
                try:
                    sw.configure(bg=ZONE_COLORS[zid % len(ZONE_COLORS)])
                except Exception:
                    pass

        # Keep the custom 8-255 row visually identical to the fixed zone rows.
        try:
            custom_zid = int(self._paint_custom_zone.get())
        except Exception:
            custom_zid = 8
        custom_zid = max(8, min(255, custom_zid))
        custom_color = ZONE_COLORS[custom_zid % len(ZONE_COLORS)]
        custom_selected = (zone == custom_zid and zone >= 8)
        custom_border = zone_color if custom_selected else row_bg
        try:
            self._paint_custom_zone_row.configure(
                bg=row_bg, highlightbackground=custom_border,
                highlightcolor=custom_border)
        except Exception:
            pass
        try:
            self._paint_custom_zone_radio.configure(
                value=custom_zid, bg=row_bg,
                fg=(selected_fg if custom_selected else text_fg),
                activebackground=row_bg,
                activeforeground=(selected_fg if custom_selected else text_fg),
                selectcolor=custom_color)
        except Exception:
            pass
        try:
            entry_bg = '#ffffff' if light else C_PANEL
            self._paint_custom_zone_entry.configure(
                bg=entry_bg, fg=text_fg, insertbackground=text_fg)
        except Exception:
            pass
        try:
            self._paint_custom_zone_swatch.configure(bg=custom_color)
        except Exception:
            pass

        self._update_paint_preview_overlay()

    def _paint_preview_nodes_at(self, wx, wy, radius=None):
        try:
            r = float(radius if radius is not None else self._paint_radius.get())
        except Exception:
            r = 5.0
        r2 = r * r
        editable = self._get_editable_node_ids()
        hits = []
        for nid, node in enumerate(self.nodes):
            if nid not in editable:
                continue
            if (node.x - wx) ** 2 + (node.y - wy) ** 2 <= r2:
                hits.append(nid)
        return hits

    def _clear_paint_preview_overlay(self):
        try:
            self.canvas.delete('paint_preview')
        except Exception:
            pass

    def _update_paint_preview_overlay(self, canvas_x=None, canvas_y=None):
        c = getattr(self, 'canvas', None)
        if c is None:
            return
        self._clear_paint_preview_overlay()
        try:
            active = (str(self.mode.get()) == 'paint')
        except Exception:
            active = False
        if not active:
            self._clear_paint_live_overlay()
            return
        try:
            wx = float(self._cursor_wx)
            wy = float(self._cursor_wy)
        except Exception:
            return
        if canvas_x is None or canvas_y is None:
            try:
                canvas_x, canvas_y = self.world_to_canvas(wx, wy)
            except Exception:
                return

        try:
            zone = int(self._paint_zone.get())
        except Exception:
            zone = 0
        zone_color = ZONE_COLORS[zone % len(ZONE_COLORS)]
        try:
            radius_m = float(self._paint_radius.get())
        except Exception:
            radius_m = 5.0
        radius_px = max(3.0, radius_m * float(self.vp_zoom))
        hit_ids = self._paint_preview_nodes_at(wx, wy, radius_m)

        # Brush circle + crosshair
        try:
            c.create_oval(canvas_x - radius_px, canvas_y - radius_px,
                          canvas_x + radius_px, canvas_y + radius_px,
                          outline=zone_color, width=2, dash=(5, 3), tags='paint_preview')
            c.create_line(canvas_x - 10, canvas_y, canvas_x + 10, canvas_y,
                          fill=zone_color, width=1, tags='paint_preview')
            c.create_line(canvas_x, canvas_y - 10, canvas_x, canvas_y + 10,
                          fill=zone_color, width=1, tags='paint_preview')
        except Exception:
            pass

        # Preview affected nodes
        ring_r = 6 if self.vp_zoom >= 6 else 4
        for nid in hit_ids[:250]:
            try:
                node = self.nodes[nid]
                nx, ny = self.world_to_canvas(node.x, node.y)
                c.create_oval(nx - ring_r, ny - ring_r, nx + ring_r, ny + ring_r,
                              outline=zone_color, width=2, tags='paint_preview')
            except Exception:
                pass

        label = f'Zone {zone} | {radius_m:.1f} m | {len(hit_ids)} node' + ('' if len(hit_ids) == 1 else 's')
        tx = canvas_x + radius_px + 12
        ty = canvas_y - 10
        try:
            text_id = c.create_text(tx, ty, text=label, anchor='w', fill=zone_color,
                                    font=('Consolas', 9, 'bold'), tags='paint_preview')
            bbox = c.bbox(text_id)
            if bbox:
                pad = 4
                rect = c.create_rectangle(bbox[0] - pad, bbox[1] - pad,
                                          bbox[2] + pad, bbox[3] + pad,
                                          fill=C_BG, outline=zone_color, width=1,
                                          tags='paint_preview')
                c.tag_lower(rect, text_id)
        except Exception:
            pass

    # === file_io.py (FileIOMixin) ===

    """File I/O methods for AINEditor: new/open/save/load/export."""

    # ── 15. FILE I/O ─────────────────────────────────────────────────────────

    def new_project(self):
        """Clear everything and start fresh."""
        if self.nodes:
            if not messagebox.askyesno("New Project",
                    "Discard current project and start fresh?"):
                return
        self._stop_autosave_timer()
        self.nodes = []
        self.entities = []
        self.terrain_z = 18.5
        self.bms_path = ''
        self.project_path = ''
        self.next_id = 0
        self.zones = {}
        self.zone_meta = {}
        self._ain_area_states  = []
        self._breach_zone_ids  = set()
        self._assault_zone_ids = set()
        self._both_zone_ids    = set()
        self._zone_layer_state = {}
        self._locked_zone_ids  = set()
        self._hidden_zone_ids  = set()
        self._zone_selected = None
        self._zone_drawing = None
        self._zone_drag = None
        self._mark_zones_dirty()
        self._sync_zone_panel_from_selected()
        self._reset_transient_editor_state(clear_undo=True)
        if self._pil_renderer:
            self._pil_renderer.clear()
        self._rebuild_hit_tester()
        self.vp_offset_x = 0.0
        self.vp_offset_y = 0.0
        self.vp_zoom = 8.0
        self._zoom_var.set("Zoom: 8.0")
        self._update_inspector()
        self._update_stats()
        self.redraw()
        self.status("New project. File — Open BMS to load a map.")


    def open_bms(self):
        if _BMS_LOAD_TRACE_ENABLED:
            try:
                print("[BMSLOAD] Open BMS command invoked; opening file dialog...", flush=True)
            except Exception:
                pass
        path = filedialog.askopenfilename(
            filetypes=[("BMS files","*.bms *.BMS"),("All","*.*")])
        if not path:
            if _BMS_LOAD_TRACE_ENABLED:
                try:
                    print("[BMSLOAD] Open BMS cancelled in file dialog.", flush=True)
                except Exception:
                    pass
            return
        _init_game_path = _ns.get('_init_game_path', None)
        _pff_index_cache = _ns.get('_pff_index_cache', {})
        _preload_fn = _ns.get('preload_cmodels_for_entities', None)

        _bms_load_trace_begin(path)
        _bms_load_trace('1/9 file selected; assigning bms_path and invalidating 2D bitmap cache')
        self.bms_path = path
        if self._pil_renderer:
            self._pil_renderer._last_img = None  # invalidate bitmap cache

        _bms_load_trace('1.5/9 showing Loading overlay immediately')
        self._show_loading_overlay()

        # Parse entities — must succeed for anything to work
        _bms_load_trace('2/9 parse_bms BEGIN')
        _parse_t0 = time.perf_counter()
        try:
            self.entities, self.terrain_z = parse_bms(path, self.status,
                init_game_path=_init_game_path, pff_cache=_pff_index_cache,
                trace=_bms_load_trace)
            _bms_load_trace(
                f'2/9 parse_bms END in {time.perf_counter() - _parse_t0:.3f}s; '
                f'entities={len(self.entities)} terrain_z={self.terrain_z:.4f}')
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            _bms_load_trace(
                f'2/9 parse_bms EXCEPTION after {time.perf_counter() - _parse_t0:.3f}s: {e!r}')
            self._log_crash('open_bms', path, err)
            self.status(f"ERROR loading BMS: {e}")
            self.entities = []
        self.status(f"Loaded BMS: {os.path.basename(path)} — {len(self.entities)} entities")

        _bms_load_trace('3/9 automatic terrain load BEGIN')
        _terrain_t0 = time.perf_counter()
        self._auto_load_terrain_for_bms()
        _bms_load_trace(
            f'3/9 automatic terrain load END in {time.perf_counter() - _terrain_t0:.3f}s; '
            f'terrain_info={bool(getattr(self, "terrain_info", None))} '
            f'terrain_image={bool(getattr(self, "terrain_image", None))}')

        def _on_preload_progress():
            """Called periodically by CModel worker — redraw, keep overlay visible."""
            _bms_load_trace('CModel preload progress callback -> queueing redraw on Tk thread')
            self.after(0, self.redraw)

        def _on_preload_done():
            """Called once at worker completion — queue final UI completion work."""
            _bms_load_trace('CModel preload worker DONE callback received')
            if not QUIET_DEBUG_LOGS:
                print("[OVERLAY] _on_preload_done called from thread")

            def _mark_fully_loaded():
                _bms_load_trace(
                    f'9/9 FULLY LOADED after CModel preload + final redraw; '
                    f'entities={len(self.entities)} nodes={len(self.nodes)} '
                    f'terrain={bool(getattr(self, "terrain_info", None))}')
                _bms_load_trace_end('Open BMS transaction complete')

            def _finalize_preloaded_map():
                renderer = getattr(self, '_pil_renderer', None)
                if renderer is not None:
                    renderer.invalidate_static_entity_layer()
                    renderer._last_img = None
                self.redraw()
                self._hide_loading_overlay()
                _mark_fully_loaded()

            self.after(0, _finalize_preloaded_map)

        # New BMS/entity set: rotated geometry caches are cheap to rebuild and
        # should not carry derived keys across maps.
        _bms_load_trace('5/9 clearing rotated entity geometry caches')
        try:
            clear_rotation_caches()
        except Exception as _cache_exc:
            _bms_load_trace(f'5/9 cache clear warning: {_cache_exc!r}')

        _bms_load_trace('6/9 starting background CModel preload')
        _preload_start_t0 = time.perf_counter()
        t = None
        if _preload_fn is not None:
            t = _preload_fn(self.entities, self.bms_path, log=self.status,
                            on_done=_on_preload_done,
                            on_progress=_on_preload_progress)
        if t is None:
            _bms_load_trace('6/9 no CModel preload worker created')
            self.after_idle(self._hide_loading_overlay)
        else:
            _bms_load_trace(
                f'6/9 CModel preload worker started: name={getattr(t, "name", "?")} '
                f'alive={t.is_alive()} setup={time.perf_counter() - _preload_start_t0:.3f}s')
        if not QUIET_DEBUG_LOGS:
            print(f"[OVERLAY] preload thread started: {t}")

        _bms_load_trace('7/9 resetting AIN/editor transient state for newly opened map')
        self.nodes = []
        self.next_id = 0
        self.zones = {}
        self.zone_meta = {}
        self._zone_selected = None
        self._zone_drawing = None
        self._zone_drag = None
        self._tile_selected = set()
        self._mark_zones_dirty()
        self._reset_transient_editor_state(clear_undo=True)
        self._reset_zfilter()
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()

        _bms_load_trace('8/9 fit_entities + initial redraw + stats BEGIN')
        _ui_t0 = time.perf_counter()
        _fit_t0 = time.perf_counter()
        self.fit_entities()
        _bms_load_trace(f'8/9 fit_entities END dt={time.perf_counter() - _fit_t0:.3f}s')
        _redraw_t0 = time.perf_counter()
        self.redraw()
        _bms_load_trace(f'8/9 initial redraw END dt={time.perf_counter() - _redraw_t0:.3f}s')
        _stats_t0 = time.perf_counter()
        self._update_stats()
        _bms_load_trace(f'8/9 stats update END dt={time.perf_counter() - _stats_t0:.3f}s')
        self.refresh_3d_views(rebuild=True, refit=True, reason='restore_nodes')
        _bms_load_trace(
            f'8/9 editor state READY in {time.perf_counter() - _ui_t0:.3f}s; '
            f'background_preload={t is not None}')

        if t is None:
            def _mark_loaded_without_preload():
                _bms_load_trace(
                    f'9/9 FULLY LOADED (no CModel preload required); '
                    f'entities={len(self.entities)} nodes={len(self.nodes)} '
                    f'terrain={bool(getattr(self, "terrain_info", None))}')
                _bms_load_trace_end('Open BMS transaction complete')
            self.after_idle(_mark_loaded_without_preload)


    def open_ain(self):
        path = filedialog.askopenfilename(
            title="Open AIN",
            filetypes=[("AIN files","*.ain *.AIN"),("All","*.*")])
        if not path: return
        if self._pil_renderer:
            self._pil_renderer._last_img = None  # invalidate bitmap cache
        keep_entities = False
        if self.entities:
            keep_entities = messagebox.askyesno("Keep entities?", "Keep entities as background? Yes=keep No=clear all")
        try:
            nodes_raw, area_states = read_ain(path, self.status)
        except Exception as e:
            import traceback
            self._log_crash('open_ain', path, traceback.format_exc())
            self.status(f"ERROR loading AIN: {e}")
            return
        if not nodes_raw:
            self.status("No nodes loaded — invalid or empty file")
            return
        self._ain_area_states = area_states
        self._breach_zone_ids  = set()   # 0x02 Manual
        self._assault_zone_ids = set()   # 0x01 Auto (no 0x02)
        self._both_zone_ids    = set()   # 0x03 Both
        for i, entry in enumerate(area_states):
            if len(entry) >= 4:
                flags = struct.unpack_from('<I', entry)[0]
                f01 = bool(flags & 0x01)
                f02 = bool(flags & 0x02)
                if f01 and f02:
                    self._both_zone_ids.add(i)
                    self._breach_zone_ids.add(i)
                    self._assault_zone_ids.add(i)
                elif f02:
                    self._breach_zone_ids.add(i)
                elif f01:
                    self._assault_zone_ids.add(i)
        detected = []
        if self._both_zone_ids:    detected.append(f"Both={sorted(self._both_zone_ids)}")
        if self._breach_zone_ids - self._both_zone_ids:
            detected.append(f"Manual={sorted(self._breach_zone_ids - self._both_zone_ids)}")
        if self._assault_zone_ids - self._both_zone_ids:
            detected.append(f"Auto={sorted(self._assault_zone_ids - self._both_zone_ids)}")
        if detected:
            self.status(f"Takedown zones: {', '.join(detected)}")
        if not keep_entities:
            self.entities = []
            self.bms_path = None
        self.nodes = sanitize_node_graph(nodes_raw, self.status)
        self.next_id = len(self.nodes)
        for meta in getattr(self, 'zone_meta', {}).values():
            meta['nodes'] = set()
        self._refresh_zone_list() if hasattr(self, '_zone_listbox') else None
        self._compute_debug_ranges()
        self._reset_transient_editor_state(clear_undo=True)
        self._reset_zfilter()
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self._clearance_blocked_fn = None
        name = os.path.basename(path)
        self.title(EDITOR_TITLE)
        self.status(f"Loaded: {name} — {len(self.nodes)} nodes")
        self.fit_view()
        self._update_stats()
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=True, reason='restore_nodes')


    @staticmethod
    def _project_json_safe(value):
        """Convert editor-owned sets/tuples into deterministic JSON values."""
        if isinstance(value, set):
            items = [AINEditor._project_json_safe(item) for item in value]
            return sorted(items, key=lambda item: repr(item))
        if isinstance(value, tuple):
            return [AINEditor._project_json_safe(item) for item in value]
        if isinstance(value, list):
            return [AINEditor._project_json_safe(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): AINEditor._project_json_safe(item)
                for key, item in value.items()
            }
        return value



    def _restore_generator_seed_history(self, data):
        """Restore saved seed submissions, replacing any previous session history."""
        import math as _math
        history = []
        entries = data.get('previous_generator_seeds') or []
        if isinstance(entries, list):
            for raw in entries[-500:]:
                if not isinstance(raw, dict):
                    continue
                try:
                    entry = dict(raw)
                    for key in ('x', 'y', 'radius'):
                        entry[key] = float(entry[key])
                        if not _math.isfinite(entry[key]):
                            raise ValueError(key)
                    if entry['radius'] <= 0:
                        continue
                    z = entry.get('target_node_z')
                    entry['target_node_z'] = None if z is None else float(z)
                    if z is not None and not _math.isfinite(entry['target_node_z']):
                        continue
                    settings = entry.get('settings') or {}
                    if not isinstance(settings, dict):
                        continue
                    entry['settings'] = dict(settings)
                    entry['bms_path'] = str(entry.get('bms_path') or data.get('bms_path') or '')
                    entry['index'] = len(history) + 1
                    history.append(entry)
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
        self._previous_generator_seeds = history
        self._refresh_previous_generator_seeds_window(select_last=True)

    def save_project(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.json',
            filetypes=[("JSON project","*.json"),("All","*.*")])
        if not path: return
        data = self._build_project_data()
        with open(path,'w') as f: json.dump(data,f,indent=2)
        self.project_path = path
        self.status(f"Project saved: {os.path.basename(path)}")
        self._start_autosave_timer()

    def _build_project_data(self):
        try:
            import hashlib as _hashlib
            with open(os.path.abspath(__file__), 'rb') as _source_file:
                editor_source_sha256 = _hashlib.sha256(
                    _source_file.read()).hexdigest()
        except Exception:
            editor_source_sha256 = None
        return {
            'version': 1,
            'editor_source': os.path.abspath(__file__),
            'editor_source_sha256': editor_source_sha256,
            'bms_path': self.bms_path,
            'terrain_z': self.terrain_z,
            'additional_pff_dirs': [str(p) for p in _configured_additional_pff_dirs()],
            'generator_options': {
                'z_filter': bool(getattr(
                    self, '_seed_generate_z_filter', True)),
                'rooftops': bool(getattr(
                    self, '_seed_generate_rooftops', False)),
                'highest_broad_rooftops_only': bool(getattr(
                    self, '_seed_generate_highest_rooftops', False)),
                'ladders': bool(getattr(
                    self, '_seed_generate_ladders', False)),
                'quantized': bool(getattr(
                    self, '_seed_generate_quantized', False)),
                'ignore_destroyable_objects': bool(getattr(
                    self, '_seed_ignore_destroyable', True)),
                'ignore_vehicles': bool(getattr(
                    self, '_seed_ignore_vehicles', False)),
            },
            'last_generator_metrics': self._project_json_safe(
                getattr(self, '_last_generator_metrics', {}) or {}),
            'previous_generator_seeds': self._project_json_safe(
                getattr(self, '_previous_generator_seeds', []) or []),
            'nodes': [n.to_dict() for n in self.nodes],
            'zones': {str(k): v for k,v in self.zones.items()},
            'zone_meta': {
                str(k): {
                    **{
                        kk: self._project_json_safe(vv)
                        for kk, vv in self._ensure_zone_meta(k).items()
                        if kk != 'nodes'
                    },
                    'nodes': sorted(list(self._ensure_zone_meta(k).get('nodes') or [])),
                }
                for k in self.zones.keys()
            },
        }

    def _on_autosave_toggle(self):
        enabled = bool(self._autosave_enabled.get())
        try:
            cfg = _load_editor_cfg()
            cfg['autosave_json'] = enabled
            _save_editor_cfg(cfg)
        except Exception:
            pass
        if enabled:
            self._start_autosave_timer()
            self.status("Autosave enabled")
        else:
            self._stop_autosave_timer()
            self.status("Autosave disabled")

    def _start_autosave_timer(self):
        self._stop_autosave_timer()
        if not self._autosave_enabled.get():
            return
        if not self.project_path:
            return
        self._autosave_timer = self.after(
            self._autosave_interval_ms, self._autosave_tick)

    def _stop_autosave_timer(self):
        if self._autosave_timer is not None:
            self.after_cancel(self._autosave_timer)
            self._autosave_timer = None

    def _autosave_tick(self):
        self._autosave_timer = None
        if not self._autosave_enabled.get():
            return
        if not self.project_path:
            return
        try:
            data = self._build_project_data()
            with open(self.project_path, 'w') as f:
                json.dump(data, f, indent=2)
            self.status(f"Autosaved: {os.path.basename(self.project_path)}")
        except Exception:
            self.status("Autosave failed")
        self._start_autosave_timer()

    def load_project(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON project","*.json"),("All","*.*")])
        if not path: return
        with open(path) as f: data=json.load(f)
        self.bms_path = data.get('bms_path','')
        self.terrain_z = data.get('terrain_z',18.5)
        project_pff_dirs = data.get('additional_pff_dirs', None)
        if isinstance(project_pff_dirs, list):
            _save_additional_pff_dirs(project_pff_dirs)
            try:
                self._invalidate_pff_resource_context(reparse_bms=False)
            except Exception:
                pass
        generator_options = data.get('generator_options') or {}
        if generator_options:
            self._seed_generate_z_filter = bool(
                generator_options.get('z_filter', True))
            self._seed_generate_rooftops = bool(
                self._seed_generate_z_filter
                and generator_options.get('rooftops', False))
            self._seed_generate_highest_rooftops = bool(
                self._seed_generate_rooftops
                and generator_options.get(
                    'highest_broad_rooftops_only', False))
            self._seed_generate_ladders = bool(
                self._seed_generate_z_filter
                and self._seed_generate_rooftops
                and generator_options.get('ladders', False))
            self._seed_generate_quantized = bool(
                generator_options.get('quantized', False))
            self._seed_ignore_destroyable = bool(
                generator_options.get('ignore_destroyable_objects', True))
            self._seed_ignore_vehicles = bool(
                generator_options.get('ignore_vehicles', False))
        self._last_generator_metrics = dict(
            data.get('last_generator_metrics') or {})
        self._restore_generator_seed_history(data)
        self.nodes = sanitize_node_graph(
            [Node.from_dict(d) for d in data.get('nodes',[])],
            self.status
        )
        self.zones = {int(k): v for k,v in data.get('zones',{}).items()}
        self.zone_meta = {}
        for k, meta in data.get('zone_meta', {}).items():
            try:
                ik = int(k)
            except Exception:
                continue
            self.zone_meta[ik] = dict(meta or {})
            self.zone_meta[ik]['nodes'] = set(self.zone_meta[ik].get('nodes') or [])
            raw_tiles = self.zone_meta[ik].get('tiles')
            if raw_tiles is not None:
                tiles = set()
                for tile in raw_tiles:
                    try:
                        if len(tile) >= 3:
                            tiles.add((
                                float(tile[0]),
                                float(tile[1]),
                                float(tile[2]),
                            ))
                    except Exception:
                        continue
                self.zone_meta[ik]['tiles'] = tiles
        for ik in self.zones.keys():
            self._ensure_zone_meta(ik)
        self._zone_selected = None
        self._zone_drawing = None
        self.next_id = len(self.nodes)
        self._reset_transient_editor_state(clear_undo=True)
        if self.bms_path and os.path.exists(self.bms_path):
            _init_game_path = _ns.get('_init_game_path', None)
            _pff_index_cache = _ns.get('_pff_index_cache', {})
            self.entities, _ = parse_bms(self.bms_path, self.status,
                init_game_path=_init_game_path, pff_cache=_pff_index_cache,
                trace=_bms_load_trace)
        self.project_path = path
        self._start_autosave_timer()
        self._reset_zfilter()
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self.status(f"Project loaded: {os.path.basename(path)} — {len(self.nodes)} nodes")
        self.fit_view()
        self._update_stats()
        self.redraw()


    def export_ain(self):
        if not self.nodes:
            messagebox.showerror("Error","No nodes to export."); return
        path = filedialog.asksaveasfilename(
            defaultextension='.ain',
            filetypes=[("AIN files","*.ain"),("All","*.*")])
        if not path: return
        write_ain(self.nodes, path, self.status,
                  area_states=getattr(self, '_ain_area_states', None))
        messagebox.showinfo("Done",
            f"Exported {len(self.nodes)} nodes\n{os.path.basename(path)}")

    # === phase1_init.py (Phase1InitMixin) ===

    """Post-init renderer setup, FPS, viewport sync, and scroll panel for AINEditor."""

    # ─── PHASE 1 INIT + HELPERS ─────────────────────────────────────────────


    def _post_init(self):
        """Called after canvas is created. Initialise PIL renderer."""
        self._pil_renderer = PILRenderer(self.canvas,
            get_cmodel_segments=_ns.get('get_cmodel_segments', None),
            request_cmodel_segments=_ns.get('request_cmodel_segments', None),
            entity_visible_by_layer=entity_visible_by_layer,
            cmodel_should_lazy_request=_ns.get('_cmodel_should_lazy_request_entity', None),
            enforce_render_cache_caps=_ns.get('_enforce_render_cache_caps', None),
            model_outlines=_ns.get('MODEL_OUTLINES', {}),
            get_cmodel_render_segments=_wf3d_view_mod._get_cmodel_raw,
            get_game_path=lambda: _ns.get('_game_path'),
            grid_spacing_fn=_grid_spacing_for_render)
        self.update_idletasks()
        self._update_paint_tool_ui()
        self._update_zfilter_top_indicator()
        self._tick_fps()
        self.after(100, self._first_render)


    def _first_render(self):
        """Called 100ms after startup — canvas is fully mapped by now."""
        self.redraw()


    def _tick_fps(self):
        now = time.perf_counter()
        self._fps_times.append(now)
        self._fps_times = [t for t in self._fps_times if now - t < 1.0]
        self._fps_var.set(f"FPS: {len(self._fps_times)}")
        self._redraw_fps_counter()
        self.after(16, self._tick_fps)


    def _sync_viewport_obj(self):
        """Keep Viewport object in sync with legacy vp_ attributes."""
        self._viewport.offset_x = self.vp_offset_x
        self._viewport.offset_y = self.vp_offset_y
        self._viewport.zoom     = self.vp_zoom


    def _sync_grid_render_globals(self):
        """Push the editor's grid UI state into the lightweight PIL renderer.

        The renderer is intentionally app-agnostic, so it reads the current grid
        mode from module globals during each draw/zoom-preview pass.
        """
        try:
            _grid_spacing_mod._GRID_RENDER_MODE = GRID_MODE_MED_FIXED
            label = str(self.grid_fixed_spacing_label.get() or "16 m")
            _grid_spacing_mod._GRID_FIXED_SPACING_M = float(MED_GRID_SPACING_BY_LABEL.get(label, 16.0))
        except Exception:
            _grid_spacing_mod._GRID_RENDER_MODE = GRID_MODE_MED_FIXED
            _grid_spacing_mod._GRID_FIXED_SPACING_M = 16.0


    def _refresh_left_panel_scrollregion(self):
        """Recalculate the left panel's real content height after UI sections
        are packed or hidden, then clamp the current view to the new bounds.

        Tk can retain an older canvas window bbox on some Windows builds after
        pack_forget(), which leaves invisible scrollable space.  Use the inner
        frame's requested height as the source of truth instead of bbox('all').
        """
        try:
            sc = self._side_canvas
            panel = self._scroll_panel_frame

            old_top = max(0.0, float(sc.canvasy(0)))

            panel.update_idletasks()
            sc.update_idletasks()

            content_w = max(1, int(sc.winfo_width()), int(panel.winfo_reqwidth()))
            content_h = max(1, int(panel.winfo_reqheight()))
            viewport_h = max(1, int(sc.winfo_height()))

            scroll_h = max(content_h, viewport_h)
            sc.configure(scrollregion=(0, 0, content_w, scroll_h))

            max_top = max(0.0, float(content_h - viewport_h))
            target_top = min(old_top, max_top)
            if max_top <= 0.0:
                sc.yview_moveto(0.0)
            else:
                sc.yview_moveto(target_top / float(scroll_h))

            self._update_scroll_arrow()
        except Exception:
            pass


    def _update_scroll_arrow(self):
        """Show arrow only when Z filter or Details panel are active
        and push content below the visible area."""
        try:
            if not self.show_scroll_guide.get():
                if getattr(self, '_scroll_arrow_shown', False):
                    self._scroll_arrow_shown = False
                    self._scroll_arrow_btn.place_forget()
                    if self._scroll_arrow_anim_id:
                        self.after_cancel(self._scroll_arrow_anim_id)
                        self._scroll_arrow_anim_id = None
                return
            zf_active = (getattr(self, 'z_filter_on', None) and
                         self.z_filter_on.get())
            details_active = (hasattr(self, '_inspector_details_frame') and
                              self._inspector_details_frame.winfo_ismapped())
            if not (zf_active or details_active):
                if getattr(self, '_scroll_arrow_shown', False):
                    self._scroll_arrow_shown = False
                    self._scroll_arrow_btn.place_forget()
                    if self._scroll_arrow_anim_id:
                        self.after_cancel(self._scroll_arrow_anim_id)
                        self._scroll_arrow_anim_id = None
                return

            sc = self._side_canvas
            yview_top, yview_bot = sc.yview()
            has_more_below = yview_bot < 0.999

            if has_more_below and not getattr(self, '_scroll_arrow_dismissed', False):
                if not getattr(self, '_scroll_arrow_shown', False):
                    self._scroll_arrow_shown = True
                    self._scroll_arrow_dismissed = False
                    self._scroll_arrow_btn.place(
                        relx=0.5, rely=1.0, anchor='s', y=-4)
                    self._animate_scroll_arrow()
            else:
                if getattr(self, '_scroll_arrow_shown', False):
                    self._scroll_arrow_shown = False
                    self._scroll_arrow_btn.place_forget()
                    if self._scroll_arrow_anim_id:
                        self.after_cancel(self._scroll_arrow_anim_id)
                        self._scroll_arrow_anim_id = None
        except Exception:
            pass


    def _animate_scroll_arrow(self):
        """Bounce the arrow indicator up and down — 8px smooth."""
        try:
            if not getattr(self, '_scroll_arrow_shown', False):
                return
            offset = getattr(self, '_scroll_arrow_offset', 0)
            offset = (offset + 1) % 40  # full sine cycle
            self._scroll_arrow_offset = offset
            y_off = int(math.sin(offset / 40.0 * 2 * math.pi) * 4)
            self._scroll_arrow_btn.place(
                relx=0.5, rely=1.0, anchor='s', y=y_off - 4)
            self._scroll_arrow_anim_id = self.after(
                40, self._animate_scroll_arrow)  # ~25fps smooth
        except Exception:
            pass


    def _dismiss_scroll_arrow(self, event=None):
        """Permanently dismiss the scroll arrow for this session."""
        self._scroll_arrow_dismissed = True
        self._scroll_arrow_shown = False
        self._scroll_arrow_btn.place_forget()
        if self._scroll_arrow_anim_id:
            self.after_cancel(self._scroll_arrow_anim_id)
            self._scroll_arrow_anim_id = None
        self._side_canvas.yview_scroll(3, 'units')


    def _bind_all_panel_scroll(self):
        """Bind scroll to all current panel children. Call after building."""
        if hasattr(self, '_bind_scroll_to_widget') and hasattr(self, '_scroll_panel_frame'):
            self._bind_scroll_to_widget(self._scroll_panel_frame)

    # === grid_diag.py (GridDiagMixin) ===

    """Terrain-aware node Z, auto-trim, and cache management for AINEditor."""

    # ─── GRID / RADIUS DIAGNOSTICS ───────────────────────────────────────────


    def _terrain_aware_node_z(self, wx, wy):
        """Return terrain height + MED node lift.

        Collision/entity height placement is temporarily disabled.  The
        previous collision-aware pass was useful for bridges in theory, but it
        sometimes picked inconsistent surfaces.  Keep terrain-aware placement
        stable until true CModel surface sampling is implemented.
        """
        try:
            terrain_h = _terrain_sample_height(self.terrain_info, wx, wy)
        except Exception as e:
            self._debug_note('terrain_height_sample_failed', error=str(e), x=wx, y=wy)
            terrain_h = None

        if terrain_h is None:
            return None

        node_z = float(terrain_h) + NAV_NODE_Z_LIFT
        self._debug_note(
            'terrain_only_z_sample',
            x=round(wx, 3), y=round(wy, 3),
            terrain=round(float(terrain_h), 6),
            collision='disabled',
            node_z=round(node_z, 6),
        )
        return node_z




    def _schedule_auto_trim_after_settle(self, delay_ms=None):
        """Schedule a quiet trim of rebuildable render caches after pan/zoom settles."""
        if not bool(AUTO_TRIM_DERIVED_CACHES):
            return
        try:
            if getattr(self, '_auto_trim_job', None):
                self.after_cancel(self._auto_trim_job)
            if delay_ms is None:
                delay_ms = int(AUTO_TRIM_AFTER_SETTLE_MS)
            self._auto_trim_job = self.after(int(delay_ms), self._auto_trim_derived_caches_if_needed)
        except Exception:
            pass


    def _auto_trim_derived_caches_if_needed(self):
        """Quietly clear derived caches if they grew during navigation.

        This intentionally keeps _3di_cache/CModels loaded. It only removes
        rotated segments/bounds and segment LOD results, which are rebuildable.
        """
        self._auto_trim_job = None
        if not bool(AUTO_TRIM_DERIVED_CACHES):
            return
        try:
            if getattr(self, '_is_transforming', False) or getattr(self, '_render_pending', False):
                self._schedule_auto_trim_after_settle(int(AUTO_TRIM_AFTER_SETTLE_MS))
                return

            now_ms = int(time.time() * 1000)
            cooldown = int(AUTO_TRIM_COOLDOWN_MS)
            if now_ms - int(getattr(self, '_last_auto_trim_ms', 0) or 0) < cooldown:
                return
            seg_cache = _ns.get('_rotated_segment_cache', {})
            bnd_cache = _ns.get('_rotated_bounds_cache', {})
            lod_cache = _ns.get('_segment_lod_cache', {})
            total_rot = (len(seg_cache) if hasattr(seg_cache, '__len__') else 0) + (len(bnd_cache) if hasattr(bnd_cache, '__len__') else 0)
            threshold = int(AUTO_TRIM_ROTATED_TOTAL)
            if total_rot < threshold and (not hasattr(lod_cache, '__len__') or len(lod_cache) == 0):
                return

            removed = self.trim_memory_caches(quiet=True)
            self._last_auto_trim_ms = now_ms
            self._auto_trim_count = int(getattr(self, '_auto_trim_count', 0) or 0) + 1
            try:
                parts = []
                for k in ('_segment_lod_cache', '_rotated_segment_cache', '_rotated_bounds_cache'):
                    if k in removed:
                        parts.append(f"{k}={removed[k]}")
                self.status("Auto-trimmed derived render caches" + ((": " + ", ".join(parts)) if parts else "."))
            except Exception:
                pass
        except Exception:
            pass


    def trim_memory_caches(self, quiet=False):
        """Trim/clear derived render caches that can be rebuilt."""
        removed = {}
        try:
            clear_names = ('_segment_lod_cache', '_rotated_segment_cache', '_rotated_bounds_cache')
            for name in clear_names:
                obj = _ns.get(name, None)
                if hasattr(obj, 'clear'):
                    removed[name] = len(obj)
                    obj.clear()

            obj = _ns.get('_3di_cache', None)
            if isinstance(obj, dict):
                rm_keys = [k for k in obj if isinstance(k, tuple) and k and k[0] == 'rendermesh3d']
                for k in rm_keys:
                    del obj[k]
                removed['_3di_cache_rendermesh'] = len(rm_keys)

            w3d = getattr(self, '_wire3d_window', None)
            if w3d is not None and hasattr(w3d, '_wf_entity_line_cache'):
                removed['_wf_entity_line_cache'] = len(w3d._wf_entity_line_cache)
                w3d._wf_entity_line_cache = {}

            for name in ('_3di_requested', '_cmodel_queue'):
                obj = _ns.get(name, None)
                if hasattr(obj, 'clear'):
                    removed[name] = len(obj)
                    obj.clear()

            obj = _ns.get('_cmodel_allowed_cache', None)
            if isinstance(obj, dict):
                removed['_cmodel_allowed_cache_trimmed'] = _trim_large_cache_dict(obj, 128)
            _enforce = _ns.get('_enforce_render_cache_caps', None)
            if _enforce is not None:
                _enforce()

            try:
                if (not quiet) and getattr(self, '_pil_renderer', None) is not None and hasattr(self._pil_renderer, 'invalidate_static_entity_layer'):
                    self._pil_renderer.invalidate_static_entity_layer()
                    removed['_static_entity_layer'] = 1
            except Exception:
                pass

            try:
                gc.collect()
            except Exception:
                pass

            if not quiet:
                msg = "Cleared derived render caches:\n" + "\n".join(f"{k}: {v}" for k, v in removed.items())
                print(msg)
                try:
                    messagebox.showinfo("Memory Caches", msg)
                except Exception:
                    pass
                self.status("Memory caches trimmed.")
        except Exception as e:
            if not quiet:
                try:
                    messagebox.showerror("Memory Caches", str(e))
                except Exception:
                    pass
        return removed

    # === utilities.py (UtilitiesMixin) ===

    """Utility methods for AINEditor: PFF management, status, stats."""

    # ── 17. UTILITIES ────────────────────────────────────────────────────────

    def _invalidate_pff_resource_context(self, reparse_bms=False):
        """Clear resource/model caches after game/PFF search-path changes."""
        for cache_name in _PFF_CACHE_NAMES:
            obj = _ns.get(cache_name, None)
            if hasattr(obj, "clear"):
                obj.clear()
            elif obj is not None:
                _ns.set_val(cache_name, {})

        self._clearance_blocked_fn = None
        try:
            if self._pil_renderer:
                self._pil_renderer.clear()
        except Exception:
            pass
        try:
            view = getattr(self, '_embedded_3d_view', None)
            if view is not None and hasattr(view, '_invalidate_world_geometry_cache'):
                view._invalidate_world_geometry_cache()
        except Exception:
            pass

        if reparse_bms and self.bms_path and os.path.exists(self.bms_path):
            try:
                _init_gp = _ns.get('_init_game_path', None)
                _pff_cache = _ns.get('_pff_index_cache', {})
                self.entities, _ = parse_bms(self.bms_path, self.status,
                    init_game_path=_init_gp, pff_cache=_pff_cache,
                    trace=_bms_load_trace)
            except Exception as e:
                self.status(f"PFF paths updated, but BMS reload failed: {e}")

        try:
            self.redraw()
        except Exception:
            pass


    def _open_additional_pff_folders_dialog(self):
        return _ui_dialogs.open_additional_pff_folders_dialog(self)


    def _show_loaded_pffs(self):
        """Show the currently active PFF archive filenames."""
        _game_path = _ns.get('_game_path', None)
        _init_gp = _ns.get('_init_game_path', None)

        gp = _game_path or (_init_gp(getattr(self, 'bms_path', None)) if _init_gp else None)
        records = _iter_pff_archive_records(gp, additional_dirs=_configured_additional_pff_dirs()) if gp else []
        names = [Path(rec['path']).name for rec in records]

        win = tk.Toplevel(self)
        try:
            win.withdraw()
        except Exception:
            pass
        win.title("Loaded PFFs")
        win.resizable(False, False)
        try:
            self._set_owned_popup_window(win)
        except Exception:
            pass

        body = tk.Frame(win)
        body.pack(fill='both', expand=True, padx=16, pady=14)

        title_label = tk.Label(
            body, text="Loaded PFF files",
            font=('Consolas', 11, 'bold'), anchor='w')
        title_label.pack(fill='x', pady=(0, 8))

        lb = None
        empty_label = None
        if names:
            visible_rows = min(max(len(names), 3), 14)
            lb = tk.Listbox(
                body, height=visible_rows, width=42,
                font=('Consolas', 10), activestyle='none',
                relief='solid', bd=1)
            lb.pack(fill='both', expand=True)
            for name in names:
                lb.insert('end', name)
        else:
            empty_label = tk.Label(
                body, text="No PFF files detected.",
                font=('Consolas', 10), anchor='w')
            empty_label.pack(fill='x', pady=(2, 4))

        close_btn = tk.Button(
            body, text="Close",
            relief='flat', font=('Consolas', 10),
            padx=14, pady=4)
        close_btn.pack(pady=(12, 0))

        def apply_theme(*_args):
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return

            light = bool(self.light_panels.get())
            bg = '#f7f7f7' if light else '#303030'
            fg = '#111111' if light else '#f2f2f2'
            dim = '#555555' if light else '#c8c8c8'
            list_bg = '#ffffff' if light else '#101010'
            list_fg = '#111111' if light else '#f2f2f2'
            btn_bg = '#e9e9e9' if light else '#444444'
            btn_fg = '#111111' if light else '#f2f2f2'
            active_bg = '#d9e7f7' if light else '#315f88'
            active_fg = '#111111' if light else '#ffffff'

            try:
                win.configure(bg=bg)
                body.configure(bg=bg)
                title_label.configure(bg=bg, fg=fg)
            except Exception:
                pass

            if lb is not None:
                try:
                    lb.configure(
                        bg=list_bg, fg=list_fg,
                        selectbackground=active_bg,
                        selectforeground=active_fg)
                except Exception:
                    pass

            if empty_label is not None:
                try:
                    empty_label.configure(bg=bg, fg=dim)
                except Exception:
                    pass

            try:
                close_btn.configure(
                    bg=btn_bg, fg=btn_fg,
                    activebackground=active_bg,
                    activeforeground=active_fg)
            except Exception:
                pass

        apply_theme()

        try:
            _theme_trace_id = self.light_panels.trace_add('write', apply_theme)
        except Exception:
            _theme_trace_id = None

        def _close():
            if _theme_trace_id is not None:
                try:
                    self.light_panels.trace_remove('write', _theme_trace_id)
                except Exception:
                    pass
            try:
                win.destroy()
            except Exception:
                pass

        close_btn.configure(command=_close)
        try:
            win.protocol("WM_DELETE_WINDOW", _close)
        except Exception:
            pass

        try:
            win.update_idletasks()
            w = max(360, win.winfo_reqwidth())
            h = win.winfo_reqheight()
            self._center_popup_over_editor(win, w, h)
        except Exception:
            pass

        try:
            win.deiconify()
            win.lift(self.winfo_toplevel())
        except Exception:
            pass


    def _reselect_game_folder(self):
        """Let user pick a new game folder and clear all geometry caches."""
        folder = filedialog.askdirectory(
            title="Select DFBHD Game Folder (containing .pff files)",
            parent=self)
        if not folder:
            return
        p = Path(folder)
        pff_sub = (p / "pff")
        has_pff = (any((p / n).exists() for n in
                       ("resource.pff", "localres.pff", "sounds.pff")) or
                   bool(list(p.glob("*.pff"))) or
                   (pff_sub.is_dir() and bool(list(pff_sub.glob("*.pff")))))
        if not has_pff:
            if not messagebox.askyesno(
                    "No PFF files found",
                    f"No .pff files found in:\n{folder}\n\n"
                    "This may not be the correct game folder.\n"
                    "Use this folder anyway?",
                    parent=self):
                return
        _save_gp = _ns.get('_save_game_path', None)
        if _save_gp is not None:
            _save_gp(p)
        self._invalidate_pff_resource_context(reparse_bms=False)
        self.entities = []
        self.redraw()
        self.status(f"Game folder updated: {p} — reload your map to apply.")

    def _ensure_game_folder_selected(self):
        """Prompt once at startup when no valid DFBHD game folder is known."""
        try:
            init_game_path = _ns.get('_init_game_path', None)
            detected = init_game_path(None) if init_game_path else None
            if detected and Path(detected).is_dir():
                return
        except Exception:
            pass
        if getattr(self, '_game_folder_prompt', None) is not None:
            try:
                if self._game_folder_prompt.winfo_exists():
                    return
            except Exception:
                pass

        pal = self._ui_theme()
        win = tk.Toplevel(self)
        self._game_folder_prompt = win
        # Keep the native Toplevel from flashing at Windows' default origin
        # before the centered geometry has been assigned.
        win.withdraw()
        win.title('DFBHD Game Folder Required')
        win.configure(bg=pal['popup_bg'])
        win.transient(self)
        win.resizable(False, False)

        body = tk.Frame(win, bg=pal['popup_bg'], padx=24, pady=20)
        body.pack(fill='both', expand=True)
        tk.Label(
            body, text='No DFBHD game path detected',
            bg=pal['popup_bg'], fg=pal['text'],
            font=('Segoe UI', 12, 'bold'), anchor='w').pack(fill='x')
        tk.Label(
            body,
            text=('The editor needs your DFBHD game folder to read BMS, '
                  'ITEMS.DEF, and model data.\n\n'
                  'Select the folder that contains the game PFF files.'),
            bg=pal['popup_bg'], fg=pal['text_dim'],
            justify='left', anchor='w', wraplength=420).pack(
                fill='x', pady=(10, 18))

        actions = tk.Frame(body, bg=pal['popup_bg'])
        actions.pack(fill='x')

        def choose():
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            self._game_folder_prompt = None
            self._reselect_game_folder()

        tk.Button(
            actions, text='Select game folder...', command=choose,
            bg=pal['button_bg'], fg=pal['button_fg'],
            activebackground=pal['button_active_bg'],
            activeforeground=pal['button_active_fg'],
            relief='flat', padx=12, pady=5).pack(side='right')

        def close_prompt():
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            self._game_folder_prompt = None

        win.protocol('WM_DELETE_WINDOW', close_prompt)
        try:
            win.update_idletasks()
            self._center_popup_over_editor(
                win, max(460, win.winfo_reqwidth()), win.winfo_reqheight())
            win.deiconify()
            win.grab_set()
            win.lift(self.winfo_toplevel())
            win.focus_force()
        except Exception:
            pass

    def status(self, msg):
        if threading.current_thread() is not threading.main_thread():
            q = getattr(self, '_generator_progress_queue', None)
            if q is not None:
                try:
                    q.put(('status', str(msg)))
                except Exception:
                    pass
            return
        self._status_var.set(str(msg))

    def update_idletasks(self):
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            return super().update_idletasks()
        except Exception:
            return


    def _update_stats(self):
        n = len(self.nodes)
        if n == 0:
            self._stats_var.set("No nodes")
            self._connectivity_cache = None
            return
        conn_key = (n, sum(len(nd.neighbors) for nd in self.nodes))
        if getattr(self, '_connectivity_cache', None) == conn_key:
            sel_n = len(self.selected_nodes)
            base = getattr(self, '_stats_base', '')
            suffix = f"  |  Sel: {sel_n}" if sel_n > 0 else ""
            if self.z_filter_on.get():
                try:
                    zmin, zmax = self._get_z_filter_bounds()
                    vis = len(self._get_visible_nodes())
                    suffix += f"  |  Z: {zmin:.1f}-{zmax:.1f}m ({vis} visible)"
                except Exception:
                    pass
            self._stats_var.set(base + suffix)
            return
        graph_components = self._graph_components_for_ids(range(n))
        components = len(graph_components)
        largest = len(graph_components[0]) if graph_components else 0
        pct = largest / n * 100
        base = f"Nodes: {n}  |  Largest CC: {largest}/{n} ({pct:.0f}%)  |  Components: {components}"
        self._stats_base = base
        self._connectivity_cache = conn_key
        sel_n = len(self.selected_nodes)
        suffix = f"  |  Sel: {sel_n}" if sel_n > 0 else ""
        if self.z_filter_on.get():
            try:
                zmin, zmax = self._get_z_filter_bounds()
                vis = len(self._get_visible_nodes())
                suffix += f"  |  Z: {zmin:.1f}-{zmax:.1f}m ({vis} visible)"
            except Exception:
                pass
        self._stats_var.set(base + suffix)

    # === view_3d.py (View3DMixin) ===

    """3D view toggle, debug panel, node locking, and zone scope for AINEditor."""

    # ── 14. 3D VIEW ──────────────────────────────────────────────────────────

    def toggle_3d_view(self):
        """Toggle the center viewport between normal 2D canvas and embedded 3D."""
        if getattr(self, '_view_mode', '2d') == '3d':
            self.show_2d_view()
        else:
            self.show_3d_view()


    def show_3d_view(self):
        """Embed Wireframe3DView into the main center viewport."""
        try:
            _existing_3d = getattr(self, '_embedded_3d_view', None)
            if _existing_3d is not None:
                try:
                    _existing_3d._cancel_box_select()
                except Exception:
                    pass
            main = getattr(self, '_main_frame', None)
            cf = getattr(self, '_canvas_frame', None)
            if main is None or cf is None:
                self.open_3d_wireframe()
                return

            _sv_creating = getattr(self, '_embedded_3d_view', None) is None
            if _sv_creating:
                self._embedded_3d_view = Wireframe3DView(main, self, on_close=self.show_2d_view)
                try:
                    if self.light_panels.get():
                        self._apply_panel_theme(True)
                except Exception:
                    pass

            try:
                cf.grid_remove()
            except Exception:
                pass

            self._embedded_3d_view._initial_view_done = False
            self._embedded_3d_view.grid(row=0, column=1, sticky='nsew')
            self._view_mode = '3d'
            try:
                self._view3d_toggle_btn.configure(text='2D View')
                self._sync_topbar_theme()
            except Exception:
                pass
            try:
                self.after_idle(self._embedded_3d_view.activate_initial_view)
                self.after(80, self._embedded_3d_view.activate_initial_view)
            except Exception:
                traceback.print_exc()
            self.status('3D view active. Press 2D View or Escape in the 3D view to return.')
        except Exception as e:
            try:
                messagebox.showerror('3D View Error', str(e))
            except Exception:
                pass
            traceback.print_exc()


    def show_2d_view(self):
        """Return from embedded 3D view to the normal 2D map canvas."""
        try:
            v = getattr(self, '_embedded_3d_view', None)
            if v is not None:
                try:
                    v._cancel_box_select()
                except Exception:
                    pass
                try:
                    v.grid_remove()
                except Exception:
                    pass
            cf = getattr(self, '_canvas_frame', None)
            if cf is not None:
                cf.grid(row=0, column=1, sticky='nsew')
            self._view_mode = '2d'
            try:
                self._view3d_toggle_btn.configure(text='3D View')
                self._sync_topbar_theme()
            except Exception:
                pass
            try:
                self.canvas.focus_set()
            except Exception:
                pass
            self.status('2D view active.')
            try:
                self.redraw()
            except Exception:
                pass
        except Exception as e:
            try:
                messagebox.showerror('2D View Error', str(e))
            except Exception:
                pass
            traceback.print_exc()



    def open_3d_wireframe(self):
        """Open detached node-centric 3D wireframe popup fallback/debug view."""
        try:
            node_ids = sorted(int(n) for n in getattr(self, 'selected_nodes', set()) if 0 <= int(n) < len(self.nodes))
            if not node_ids and self.selected_id is not None and 0 <= int(self.selected_id) < len(self.nodes):
                node_ids = [int(self.selected_id)]

            win = getattr(self, '_wire3d_window', None)
            if win is not None:
                try:
                    if win.winfo_exists():
                        win.lift()
                        win.refresh()
                        return
                except Exception:
                    pass

            self._wire3d_window = Wireframe3DWindow(self)
        except Exception as e:
            try:
                messagebox.showerror("3D Wireframe Error", str(e))
            except Exception:
                pass
            traceback.print_exc()



    def show_3d_debug(self):
        """Open/update the live 3D debug panel."""
        _wf3d_debug_mod.WF3D_DEBUG_ENABLED = True
        try:
            self._wf_entity_line_cache.clear()
        except Exception:
            pass
        _cache = _ns.get('_3di_cache', {})
        r = _wf3d_clear_rendermesh_cache(cache=_cache)
        _wf3d_dbg(f'DEBUG: enabled from Wireframe3DWindow; cleared _wf_entity_line_cache; cleared rendermesh cache entries={r}', force=True)
        if self._debug_window is not None and self._debug_window.winfo_exists():
            self._debug_window.lift()
            self._refresh_3d_debug_panel()
            return
        win = tk.Toplevel(self)
        self._debug_window = win
        win.title('3D Wireframe Debug — calls and fallbacks')
        win.geometry('980x520')
        win.configure(bg='#151515')
        top = tk.Frame(win, bg='#202020')
        top.pack(side='top', fill='x')
        def _btn(text, cmd):
            b = tk.Button(top, text=text, command=cmd, bg='#303030', fg='#eeeeee',
                          relief='flat', font=('Consolas', 9), padx=6, pady=2)
            b.pack(side='left', padx=3, pady=4)
            return b
        _btn('Refresh', self._refresh_3d_debug_panel)
        _btn('Clear', self._clear_3d_debug_panel)
        _btn('Force redraw', self._wf3d_force_redraw_debug)
        _btn('Copy log path', lambda: self.clipboard_clear() or self.clipboard_append(WF3D_DEBUG_LOG_PATH))
        self._debug_enabled_var = tk.BooleanVar(value=bool(_wf3d_debug_mod.WF3D_DEBUG_ENABLED))
        def _toggle_dbg():
            _wf3d_debug_mod.WF3D_DEBUG_ENABLED = bool(self._debug_enabled_var.get())
            _wf3d_dbg(f"DEBUG: enabled={_wf3d_debug_mod.WF3D_DEBUG_ENABLED}", force=True)
            self._refresh_3d_debug_panel()
        tk.Checkbutton(top, text='Enabled', variable=self._debug_enabled_var, command=_toggle_dbg,
                       bg='#202020', fg='#eeeeee', selectcolor='#303030',
                       activebackground='#202020', activeforeground='#ffffff',
                       font=('Consolas', 9), bd=0).pack(side='left', padx=8)
        tk.Label(top, text=f'Log: {WF3D_DEBUG_LOG_PATH}', bg='#202020', fg='#999999',
                 font=('Consolas', 8)).pack(side='left', padx=8)
        txt = tk.Text(win, bg='#050505', fg='#dddddd', insertbackground='#ffffff',
                      font=('Consolas', 8), wrap='none')
        self._debug_text = txt
        ys = tk.Scrollbar(win, orient='vertical', command=txt.yview)
        xs = tk.Scrollbar(win, orient='horizontal', command=txt.xview)
        txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side='right', fill='y')
        xs.pack(side='bottom', fill='x')
        txt.pack(side='left', fill='both', expand=True)
        win.protocol('WM_DELETE_WINDOW', lambda: (setattr(self, '_debug_window', None), setattr(self, '_debug_text', None), win.destroy()))
        self._refresh_3d_debug_panel()


    def _refresh_3d_debug_panel(self):
        txt = getattr(self, '_debug_text', None)
        if txt is None:
            return
        try:
            txt.configure(state='normal')
            txt.delete('1.0', 'end')
            lines = _wf3d_debug_mod.WF3D_DEBUG_LINES
            txt.insert('end', '\n'.join(lines[-_wf3d_debug_mod.WF3D_DEBUG_MAX_LINES:]))
            txt.insert('end', '\n')
            txt.see('end')
            txt.configure(state='normal')
        except Exception:
            pass


    def _clear_3d_debug_panel(self):
        _wf3d_dbg_clear()
        _wf3d_dbg('DEBUG: cleared', force=True)
        self._refresh_3d_debug_panel()


    def refresh_3d_views(self, rebuild=True, refit=False, reason='node_change'):
        """Refresh all open 3D wireframe views after editor-side node changes."""
        topology_reasons = {
            'paste_nodes', 'restore_nodes', 'undo',
            'generate', 'generate_nodes',
            'delete_zone_nodes', 'clear_zone_nodes', 'delete_node_from_zone',
        }
        selection_reasons = {'selection', 'box_selection', 'zone_selection'}
        if reason in topology_reasons:
            self._reset_3d_node_lock(reason)
        elif reason in selection_reasons:
            self._sync_3d_node_lock_after_selection(reason)

        views = []
        v = getattr(self, '_embedded_3d_view', None)
        if v is not None and getattr(self, '_view_mode', '2d') == '3d':
            try:
                if v.winfo_ismapped():
                    views.append(v)
            except Exception:
                views.append(v)
        elif v is not None and reason == 'paste_nodes':
            self._debug_note('paste_3d_refresh_skipped_hidden')

        win = getattr(self, '_wire3d_window', None)
        if win is not None:
            try:
                if win.winfo_exists():
                    wv = getattr(win, 'view', win)
                    if wv is not None and wv not in views:
                        views.append(wv)
                else:
                    self._wire3d_window = None
            except Exception:
                self._wire3d_window = None

        if not views:
            return

        light_reasons = {'3d_selection', 'inspector_apply', 'nudge_node', 'node_drag'}

        for view in list(views):
            try:
                did_fast = False
                if rebuild and reason in light_reasons and hasattr(view, 'sync_node_state_fast'):
                    did_fast = bool(view.sync_node_state_fast(reason=reason))
                elif rebuild and hasattr(view, 'rebuild_scene'):
                    view.rebuild_scene()
                if refit and hasattr(view, 'fit_view'):
                    view.fit_view()
                if hasattr(view, '_refresh_node_set_status'):
                    view._refresh_node_set_status()
                if hasattr(view, 'schedule_render'):
                    view.schedule_render()
                try:
                    if hasattr(view, '_refresh_3d_debug_panel'):
                        _wf3d_dbg(f'LIVE_SYNC: refreshed reason={reason} rebuild={rebuild} refit={refit} fast={did_fast}', force=False)
                        view._refresh_3d_debug_panel()
                except Exception:
                    pass
            except Exception:
                traceback.print_exc()


    def clear_selection_from_3d(self):
        """Clear the live editor selection from 3D without unlocking its working set."""
        old_sel = set(getattr(self, 'selected_nodes', set()) or set())
        old_id = getattr(self, 'selected_id', None)
        if old_id is None and not old_sel:
            return False
        self.selected_id = None
        self.selected_nodes = set()
        self._update_inspector()
        self._update_stats()
        try:
            self._recolor_selection(old_sel, self.selected_nodes)
        except Exception:
            pass
        try:
            self.status("3D selection cleared")
        except Exception:
            pass
        self.refresh_3d_views(rebuild=True, refit=False, reason='3d_selection')
        return True


    def select_node_from_3d(self, nid):
        """Select a node from the 3D viewport."""
        try:
            nid = int(nid)
        except Exception:
            return False
        if not (0 <= nid < len(self.nodes)):
            return False
        if not self._node_is_interactive(self.nodes[nid]):
            return False
        old_sel = set(getattr(self, 'selected_nodes', set()) or set())

        if getattr(self, 'selected_id', None) == nid and old_sel == {nid}:
            return self.clear_selection_from_3d()

        self.selected_id = nid
        self.selected_nodes = {nid}
        self._show_info_for_node_selection()
        self._update_inspector()
        self._update_stats()
        try:
            self._recolor_selection(old_sel, self.selected_nodes)
        except Exception:
            pass
        try:
            self.status(f"3D selected node {nid}")
        except Exception:
            pass
        self.refresh_3d_views(rebuild=True, refit=False, reason='3d_selection')
        return True


    def _current_selection_ids_for_3d_lock(self):
        ids = set()
        try:
            for n in getattr(self, 'selected_nodes', set()) or set():
                n = int(n)
                if 0 <= n < len(self.nodes):
                    ids.add(n)
        except Exception:
            pass
        if not ids:
            try:
                sid = getattr(self, 'selected_id', None)
                if sid is not None:
                    sid = int(sid)
                    if 0 <= sid < len(self.nodes):
                        ids.add(sid)
            except Exception:
                pass
        return ids


    def _get_3d_locked_node_ids(self):
        if not bool(getattr(self, '_locked_3d_node_set_active', False)):
            return None
        ids = {int(n) for n in getattr(self, '_locked_3d_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        if not ids:
            self._locked_3d_node_set_active = False
            self._locked_3d_node_ids = set()
            self._locked_3d_anchor_node_ids = set()
            self._locked_3d_node_scope_note = ''
            return None
        return ids


    def _3d_zone_ui_name(self):
        """Creator-facing Zone label; raw b14 name is Developer-mode only."""
        try:
            return "b14" if bool(self.dev_mode.get()) else "Zone"
        except Exception:
            return "Zone"


    def _3d_node_lock_status_text(self):
        ids = self._get_3d_locked_node_ids()
        if ids is None:
            reason = getattr(self, '_locked_3d_node_reset_reason', '')
            return "Node set: Live selection" + (f"\nLast reset: {reason}" if reason else "")
        note = str(getattr(self, '_locked_3d_node_scope_note', '') or '')
        if note and self._3d_zone_ui_name() != "b14" and note.startswith("b14 "):
            note = "Zone " + note[4:]
        anchor = {int(n) for n in getattr(self, '_locked_3d_anchor_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        if note:
            if anchor and len(anchor) != len(ids):
                return f"Node set: {note}\nRestore returns to locked {len(anchor)} nodes."
            return f"Node set: {note}"
        return f"Node set: Locked {len(ids)} nodes\nLive 2D selection cleared; only this set is shown in 3D."


    def lock_3d_selected_nodes(self, assign_b14=None):
        ids = self._current_selection_ids_for_3d_lock()
        if not ids:
            self.status("Select nodes first, then lock the 3D node set")
            return False

        assigned_msg = ""
        if assign_b14 is not None:
            try:
                zone = int(assign_b14) & 0xFF
                snap = self._snapshot_nodes() if hasattr(self, '_snapshot_nodes') else None
                changed = 0
                for nid in sorted(ids):
                    if 0 <= int(nid) < len(self.nodes):
                        n = self.nodes[int(nid)]
                        if int(getattr(n, 'b14', 0)) != zone:
                            n.b14 = zone
                            changed += 1
                zone_name = self._3d_zone_ui_name()
                if changed and snap is not None and hasattr(self, '_push_undo'):
                    self._push_undo(f'Set {zone_name}={zone} for 3D locked set ({changed} nodes)',
                                    lambda s=snap: self._restore_nodes(s))
                assigned_msg = f"; {zone_name} set to {zone} on {len(ids)} node(s)"
            except Exception:
                assigned_msg = f"; {self._3d_zone_ui_name()} assignment failed"

        old_sel = set(getattr(self, 'selected_nodes', set()) or set())
        self._locked_3d_node_ids = set(ids)
        self._locked_3d_anchor_node_ids = set(ids)
        self._locked_3d_node_set_active = True
        self._locked_3d_node_scope_note = ''
        self._locked_3d_node_reset_reason = ''
        self.selected_id = None
        self.selected_nodes = set()
        try:
            self._update_inspector()
            self._update_stats()
            self._recolor_selection(old_sel, self.selected_nodes)
        except Exception:
            pass
        try:
            self.redraw()
        except Exception:
            pass

        self.status(f"3D node set locked: {len(ids)} node(s); live selection cleared{assigned_msg}")
        self.refresh_3d_views(rebuild=True, refit=False, reason='3d_node_lock')
        return True


    def _all_node_ids_with_b14(self, zone):
        try:
            zone = int(zone) & 0xFF
        except Exception:
            return set()
        out = set()
        for i, n in enumerate(self.nodes):
            try:
                if (int(getattr(n, 'b14', 0)) & 0xFF) == zone:
                    out.add(int(i))
            except Exception:
                pass
        return out


    def set_3d_b14_zone_scope(self, zone, select=False):
        """Temporarily expand the locked 3D set to all map nodes in b14 zone."""
        try:
            zone = int(zone) & 0xFF
        except Exception:
            self.status(f"Invalid {self._3d_zone_ui_name()} zone")
            return False

        locked_active = bool(getattr(self, '_locked_3d_node_set_active', False))
        if locked_active:
            anchor = {int(n) for n in getattr(self, '_locked_3d_anchor_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
            if not anchor:
                anchor = {int(n) for n in getattr(self, '_locked_3d_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        else:
            anchor = self._current_selection_ids_for_3d_lock()

        if not anchor:
            self.status(f"Select nodes first, then use {self._3d_zone_ui_name()} zone all/select")
            return False

        self._locked_3d_anchor_node_ids = set(anchor)
        ids = self._all_node_ids_with_b14(zone)
        if not ids:
            self.status(f"No nodes found with {self._3d_zone_ui_name()}={zone}")
            return False

        old_sel = set(getattr(self, 'selected_nodes', set()) or set())
        self._locked_3d_node_ids = set(ids)
        self._locked_3d_node_set_active = True
        self._locked_3d_node_scope_note = f"b14 {zone} scope: {len(ids)} node(s)"
        self._locked_3d_node_reset_reason = ''

        if select:
            self.selected_nodes = set(ids)
            self.selected_id = min(ids) if ids else None
            action = "selected"
        else:
            self.selected_nodes = set()
            self.selected_id = None
            action = "shown"

        try:
            self._update_inspector()
            self._update_stats()
            self._recolor_selection(old_sel, self.selected_nodes)
            self.redraw()
        except Exception:
            pass
        self.status(f"3D {self._3d_zone_ui_name()} {zone}: {len(ids)} node(s) {action}; Restore returns to original {len(anchor)} node set")
        self.refresh_3d_views(rebuild=True, refit=True, reason='3d_b14_zone_scope')
        return True


    def restore_3d_locked_anchor(self):
        """Return the 3D working set to the original locked selection."""
        if not bool(getattr(self, '_locked_3d_node_set_active', False)):
            return False
        anchor = {int(n) for n in getattr(self, '_locked_3d_anchor_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        if not anchor:
            anchor = {int(n) for n in getattr(self, '_locked_3d_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        if not anchor:
            return self._reset_3d_node_lock('empty locked anchor')

        old_sel = set(getattr(self, 'selected_nodes', set()) or set())
        self._locked_3d_node_ids = set(anchor)
        self._locked_3d_node_set_active = True
        self._locked_3d_node_scope_note = ''
        self.selected_nodes = set()
        self.selected_id = None
        try:
            self._update_inspector()
            self._update_stats()
            self._recolor_selection(old_sel, self.selected_nodes)
            self.redraw()
        except Exception:
            pass
        self.status(f"3D node set restored: {len(anchor)} locked node(s)")
        self.refresh_3d_views(rebuild=True, refit=True, reason='3d_b14_restore_anchor')
        return True


    def unlock_3d_node_set(self, reason='manual'):
        if bool(getattr(self, '_locked_3d_node_set_active', False)):
            self._locked_3d_node_set_active = False
            self._locked_3d_node_ids = set()
            self._locked_3d_anchor_node_ids = set()
            self._locked_3d_node_scope_note = ''
            self._locked_3d_node_reset_reason = reason or 'manual'
            self.status("3D node set unlocked")
            self.refresh_3d_views(rebuild=True, refit=False, reason='3d_node_unlock')
            return True
        return False


    def _reset_3d_node_lock(self, reason):
        if bool(getattr(self, '_locked_3d_node_set_active', False)):
            self._locked_3d_node_set_active = False
            self._locked_3d_node_ids = set()
            self._locked_3d_anchor_node_ids = set()
            self._locked_3d_node_scope_note = ''
            self._locked_3d_node_reset_reason = reason or 'reset'
            try:
                self.status(f"3D node set reset: {reason}")
            except Exception:
                pass
            return True
        return False


    def _sync_3d_node_lock_after_selection(self, reason='selection'):
        if not bool(getattr(self, '_locked_3d_node_set_active', False)):
            return False
        locked = {int(n) for n in getattr(self, '_locked_3d_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        anchor = {int(n) for n in getattr(self, '_locked_3d_anchor_node_ids', set()) if 0 <= int(n) < len(self.nodes)}
        allowed = locked | anchor
        if not locked:
            return self._reset_3d_node_lock('empty locked set')
        cur = self._current_selection_ids_for_3d_lock()
        if cur and allowed and not cur.issubset(allowed):
            return self._reset_3d_node_lock('outside node selected')
        return False

    # === view_helpers.py (ViewHelpersMixin) ===

    """Terrain loading, overlay, and view helper methods for AINEditor."""

    # ─── VIEW HELPERS ────────────────────────────────────────────────────────

    def load_terrain_mis(self):
        """Manual terrain backdrop loader: MIS -> TRN -> polytrn_colormap."""
        initial = Path(self.bms_path).parent if self.bms_path else Path.cwd()
        path = filedialog.askopenfilename(
            initialdir=str(initial),
            filetypes=[("MIS files","*.mis *.MIS"),("All","*.*")]
        )
        if not path:
            return
        self._load_terrain_from_mis(path, manual=True)


    def _apply_terrain_info(self, info, source_path='', manual=False):
        """Install a loaded terrain backdrop/heightmap into the editor."""
        self.terrain_info = info
        if isinstance(info, dict):
            info['_display_mode'] = str(self.terrain_display_mode.get() or 'C').upper()
        self.terrain_mis_path = str(source_path or '')
        if info.get('ok') and info.get('image') is not None:
            self.terrain_image = info['image']
            self.show_terrain.set(True)
            if self._pil_renderer:
                self._pil_renderer.clear()
                self._pil_renderer._terrain_tile_cache_key = None
                self._pil_renderer._terrain_tile_cache_img = None
            sp = info.get('spatial') or {}
            htxt = "height ON" if info.get('heightmap') is not None else "height unavailable"
            _water_z = _terrain_water_height_world(info)
            _water_txt = f", water {_water_z:.2f}m" if _water_z is not None else ""
            self.status(
                f"Terrain loaded{' (approximate)' if info.get('approximate_mode') else ''}: "
                f"{Path(info.get('colormap_file','')).name} "
                f"from {Path(info.get('terrain_trn','')).name} "
                f"({sp.get('sectorcount', '?')} sectors, {htxt}"
                f"{_water_txt}"
                f"{', compat' if info.get('compat_note') else ''})"
            )
            self.redraw()
            return True

        self.terrain_image = None
        if manual:
            msg = "\n".join(info.get('debug_lines')[-8:]) or "No terrain backdrop could be loaded."
            messagebox.showwarning("Terrain not loaded", msg, parent=self)
        self.status("No terrain backdrop loaded")
        return False


    def _load_terrain_from_mis(self, mis_path, manual=False):
        """Load terrain image layer using MED's MIS/TRN chain."""
        try:
            _pff_cache = _ns.get('_pff_index_cache', {})
            _init_gp = _ns.get('_init_game_path', None) or (lambda p=None: None)
            game_dir = (_init_gp(self.bms_path) or Path(self.bms_path).parent) if self.bms_path else Path(mis_path).parent
            self._watchdog_action = 'terrain:load_mis'
            info = _load_terrain_backdrop_from_mis(mis_path, game_dir, pff_cache=_pff_cache)
            info = _attach_bms_water_level(info, self.bms_path)
            self._watchdog_action = 'terrain:loaded_mis'
            applied = self._apply_terrain_info(info, str(mis_path), manual=manual)
            return applied
        except Exception as e:
            import traceback
            self._log_crash('load_terrain_mis', str(mis_path), traceback.format_exc())
            self.status(f"ERROR loading terrain: {e}")
            if manual:
                messagebox.showerror("Terrain error", str(e), parent=self)
            return False


    def _auto_load_terrain_for_bms(self):
        """Try sibling MIS first, then BMS-declared terrain if the MIS is absent."""
        mis = _guess_mis_for_bms(self.bms_path)
        if mis:
            if self._load_terrain_from_mis(mis, manual=False):
                return

        try:
            _pff_cache = _ns.get('_pff_index_cache', {})
            _init_gp = _ns.get('_init_game_path', lambda p=None: None)
            game_dir = _init_gp(self.bms_path) or Path(self.bms_path).parent
            terrain_name, _terrain_source = _detect_bms_terrain_name(
                self.bms_path, game_dir, log=self.status,
                init_game_path=_init_gp, pff_cache=_pff_cache)
            if terrain_name:
                self._watchdog_action = 'terrain:load_bms'
                info = _load_terrain_backdrop_from_mis(
                    None,
                    game_dir,
                    terrain_name_override=terrain_name,
                    source_label=f"BMS {Path(self.bms_path).name}",
                    pff_cache=_pff_cache,
                )
                info = _attach_bms_water_level(info, self.bms_path)
                self._watchdog_action = 'terrain:loaded_bms'
                _applied = self._apply_terrain_info(info, f"BMS:{terrain_name}", manual=False)
                if _applied:
                    return

            self.terrain_image = None
            self.terrain_info = None
            self.terrain_mis_path = ''
            self.show_terrain.set(False)
            self.status("No sibling MIS found and BMS terrain could not be loaded")
        except Exception as e:
            import traceback
            self._log_crash('auto_load_terrain_bms', self.bms_path or '<no bms>', traceback.format_exc())
            self.terrain_image = None
            self.terrain_info = None
            self.terrain_mis_path = ''
            self.show_terrain.set(False)
            self.status(f"ERROR loading BMS terrain: {e}")


    def _show_loading_overlay(self):
        """Show a non-closeable 'Loading models...' overlay centered on the canvas."""
        if not QUIET_DEBUG_LOGS:
            print("[OVERLAY] _show_loading_overlay called")
        if getattr(self, '_loading_overlay', None):
            try: self._loading_overlay.destroy()
            except Exception: pass
        ov = tk.Toplevel(self)
        ov.overrideredirect(True)
        ov.resizable(False, False)
        ov.attributes('-topmost', True)
        bg = '#1a1a1a'
        ov.configure(bg=bg)
        tk.Label(ov, text='Loading map...', font=('Consolas', 13, 'bold'),
                 fg='#f0c040', bg=bg, padx=24, pady=16).pack()
        tk.Label(ov, text='Please wait while terrain and models are loaded.',
                 font=('Consolas', 9), fg='#888888', bg=bg, padx=24, pady=4).pack()
        self.update_idletasks()
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        cx = self.canvas.winfo_rootx() + cw // 2
        cy = self.canvas.winfo_rooty() + ch // 2
        ov.update_idletasks()
        ow = ov.winfo_width()
        oh = ov.winfo_height()
        if not QUIET_DEBUG_LOGS:
            print(f"[OVERLAY] canvas={cw}x{ch} center=({cx},{cy}) overlay={ow}x{oh}")
        ov.geometry(f'+{cx - ow//2}+{cy - oh//2}')
        ov.update()
        self._loading_overlay = ov
        if not QUIET_DEBUG_LOGS:
            print(f"[OVERLAY] overlay created and placed")


    def _hide_loading_overlay(self):
        """Dismiss the loading overlay."""
        if not QUIET_DEBUG_LOGS:
            print("[OVERLAY] _hide_loading_overlay called")
        ov = getattr(self, '_loading_overlay', None)
        if ov:
            try: ov.destroy()
            except Exception: pass
            self._loading_overlay = None
            if not QUIET_DEBUG_LOGS:
                print("[OVERLAY] overlay destroyed")

    # === transform_settle.py (TransformSettleMixin) ===

    """Pan/zoom transform lifecycle, settle, and idle-render for AINEditor."""

    # ─── FILE OPERATIONS ────────────────────────────────────────────────────

    def _begin_transform(self):
        """Mark that a pan/zoom transform is in progress.
        Cancels any pending settle job and sets the transforming flag.
        """
        self._is_transforming = True
        _set_cmodel_pause_until(time.time() + float(CMODEL_LOAD_IDLE_DELAY))
        if self._settle_job:
            self.after_cancel(self._settle_job)
            self._settle_job = None


    def _schedule_settle(self):
        """Schedule a full sync 100ms after the last transform event."""
        if self._settle_job:
            self.after_cancel(self._settle_job)
        self._settle_job = self.after(int(ZOOM_SETTLE_DELAY_MS), self._do_settle)


    def _schedule_render_idle(self):
        """GetQueueStatus equivalent: defer render until event queue is empty.
        Uses after(0,...) which fires only after all pending events are processed.
        This mirrors MED's GetQueueStatus(2) check — only render on the LAST
        mouse event in a burst, skipping intermediate frames entirely.
        """
        if not self._render_pending:
            self._render_pending = True
            self.after(int(ZOOM_FAST_RENDER_DELAY_MS), self._do_idle_render)


    def _do_idle_render(self):
        """Called when event queue is drained. Render one frame."""
        self._render_pending = False
        t0 = time.perf_counter()
        force_no_cache = bool(getattr(self, '_force_next_render_no_cache', False))
        self._force_next_render_no_cache = False
        self._pil_render(fast_mode=self._is_transforming, force_no_cache=force_no_cache)
        self._last_render_ms = (time.perf_counter() - t0) * 1000


    def _do_settle(self):
        """Called 100ms after last pan/zoom event.
        PIL renderer redraws everything from scratch each frame.
        """
        self._settle_job      = None
        self._is_transforming = False
        self._synced_zoom     = self.vp_zoom
        self._synced_offset   = (self.vp_offset_x, self.vp_offset_y)

        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        self._sync_viewport_obj()

        # Check if drift correction is due
        force_full = (self._zoom_op_count >= self._DRIFT_CORRECT_N)
        if force_full:
            self._zoom_op_count = 0

        # If wheel zoom used bitmap preview, do not immediately perform
        # another expensive exact/static-layer rebuild here. Schedule one exact
        # redraw after the wheel burst settles.
        _set_cmodel_pause_until(time.time() + float(CMODEL_LOAD_IDLE_DELAY))
        try:
            if bool(getattr(self, '_zoom_preview_active', False)):
                self._zoom_preview_active = False
                self._zoom_preview_pending = None
                self._zoom_preview_base = None
                try:
                    if getattr(self, '_zoom_preview_job', None):
                        self.after_cancel(self._zoom_preview_job)
                        self._zoom_preview_job = None
                except Exception:
                    pass
                delay_ms = int(ZOOM_EXACT_REDRAW_AFTER_SETTLE_MS)
                self.after(delay_ms, self.redraw)
                self._schedule_auto_trim_after_settle(delay_ms + int(AUTO_TRIM_AFTER_SETTLE_MS))
            else:
                self._pil_render(fast_mode=True)
                delay_ms = int(float(CMODEL_LOAD_IDLE_DELAY) * 1000) + 80
                self.after(delay_ms, self.redraw)
                self._schedule_auto_trim_after_settle(delay_ms + int(AUTO_TRIM_AFTER_SETTLE_MS))
        except Exception:
            pass


    def _recolor_selection(self, old_sel, new_sel):
        """With PIL renderer, selection change requires a full render pass."""
        self._pil_render()

    # === view_fit.py (ViewFitMixin) ===

    """Fit-to-view helpers for AINEditor (nodes, entities, bounding box)."""

    def fit_view(self):
        if not self.nodes: return
        xs = [n.x for n in self.nodes]; ys = [n.y for n in self.nodes]
        self._fit_to_bounds(min(xs),max(xs),min(ys),max(ys))


    def fit_entities(self):
        if not self.entities: return
        points = []
        for entity in self.entities:
            try:
                x = float(entity['x'])
                y = float(entity['y'])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(x) and math.isfinite(y):
                points.append((x, y))
        if not points:
            return

        xs = sorted(p[0] for p in points)
        ys = sorted(p[1] for p in points)
        xmin, xmax = xs[0], xs[-1]
        ymin, ymax = ys[0], ys[-1]

        # BMS files can contain a few remote markers, actors, or preserved
        # editor records far outside the authored map.  Fitting those absolute
        # extrema makes the real entity cluster appear as a tiny speck.  Use a
        # central 98% frame only when the extrema enlarge either axis by a
        # clearly pathological amount; ordinary maps still fit every entity.
        if len(points) >= 100:
            trim = max(1, int(len(points) * 0.01))
            if trim * 2 < len(points) - 4:
                txmin, txmax = xs[trim], xs[-trim - 1]
                tymin, tymax = ys[trim], ys[-trim - 1]
                full_w = max(xmax - xmin, 1e-6)
                full_h = max(ymax - ymin, 1e-6)
                trim_w = max(txmax - txmin, 1e-6)
                trim_h = max(tymax - tymin, 1e-6)
                if full_w / trim_w >= 1.8 or full_h / trim_h >= 1.8:
                    xmin, xmax = txmin, txmax
                    ymin, ymax = tymin, tymax

        self._fit_to_bounds(xmin, xmax, ymin, ymax)


    def _fit_to_bounds(self, xmin, xmax, ymin, ymax):
        self.update_idletasks()
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        wx = xmax - xmin or 1; wy = ymax - ymin or 1
        self.vp_zoom = min(min(cw/wx, ch/wy) * 0.85, 25.0)
        self.vp_offset_x = (xmin+xmax)/2
        self.vp_offset_y = (ymin+ymax)/2
        self._zoom_var.set(f"Zoom: {self.vp_zoom:.1f}")
        self.redraw()

    # === panel_toggles.py (PanelTogglesMixin) ===

    """Inspector panel toggle, sync, and lock-capture methods for AINEditor."""

    # ─── UI CONSTRUCTION ────────────────────────────────────────────────────


    def _on_place_nodes_on_entities_toggle(self):
        """Report the manual entity-surface placement mode."""
        if self.place_nodes_on_entities.get():
            self.status(
                "Place on entities ON — new RMB/Pen nodes use the highest valid CModel support under the cursor")
        else:
            self.status(
                "Place on entities OFF — new RMB/Pen nodes use normal terrain/Z placement")


    def _on_adaptive_r_toggle(self):
        """Show/hide the Inspector's Large/XLarge Radius extensions for Adaptive radius."""
        btn_l = getattr(self, '_adaptive_large_r_btn', None)
        btn_xl = getattr(self, '_adaptive_xlarge_r_btn', None)
        btn_place = getattr(self, '_place_nodes_on_entities_btn', None)
        if self.adaptive_radius.get():
            for btn in (btn_l, btn_xl):
                if btn is not None and not btn.winfo_ismapped():
                    if btn_place is not None:
                        btn.pack(fill='x', padx=(20, 4), pady=(0, 2), before=btn_place)
                    else:
                        btn.pack(fill='x', padx=(20, 4), pady=(0, 2))
            self.status("Adaptive radius ON — placed nodes will adapt to neighbours")
        else:
            self.adaptive_large_r.set(False)
            self.adaptive_xlarge_r.set(False)
            for btn in (btn_l, btn_xl):
                if btn is not None:
                    btn.pack_forget()
            self.status("Adaptive radius OFF")

    def _sync_lock_options_panel(self):
        """Show/hide the Inspector lock options body."""
        body = getattr(self, '_lock_options_frame', None)
        btn = getattr(self, '_lock_options_toggle_btn', None)
        visible = bool(getattr(self, '_lock_options_visible', tk.BooleanVar(value=False)).get())
        try:
            if body is not None:
                if visible:
                    if not body.winfo_ismapped():
                        anchor = getattr(self, '_lock_options_after_widget', None)
                        if anchor is not None:
                            body.pack(fill='x', padx=0, pady=(0, 2), before=anchor)
                        else:
                            body.pack(fill='x', padx=0, pady=(0, 2))
                else:
                    body.pack_forget()
        except Exception:
            pass
        try:
            if btn is not None:
                btn.configure(text='Hide' if visible else 'Show')
        except Exception:
            pass
        try:
            self._update_scroll_arrow()
        except Exception:
            pass


    def _toggle_lock_options_panel(self):
        try:
            self._lock_options_visible.set(not bool(self._lock_options_visible.get()))
        except Exception:
            return
        self._sync_lock_options_panel()


    def _sync_neighbors_panel(self):
        """Show/hide the Neighbors list and connection controls."""
        body = getattr(self, '_neighbors_body', None)
        btn = getattr(self, '_neighbors_toggle_btn', None)
        try:
            visible = bool(self._neighbors_visible.get())
        except Exception:
            visible = True
        try:
            if body is not None:
                if visible:
                    if not body.winfo_ismapped():
                        anchor = getattr(self, '_neighbors_after_separator', None)
                        if anchor is not None:
                            body.pack(fill='x', before=anchor)
                        else:
                            body.pack(fill='x')
                else:
                    body.pack_forget()
        except Exception:
            pass
        try:
            if btn is not None:
                btn.configure(text='Hide' if visible else 'Show')
        except Exception:
            pass
        try:
            self._update_scroll_arrow()
        except Exception:
            pass


    def _toggle_neighbors_panel(self):
        try:
            self._neighbors_visible.set(not bool(self._neighbors_visible.get()))
        except Exception:
            return
        self._sync_neighbors_panel()


    def _on_lock_values_toggle(self):
        """Auto-capture current node values when lock is first enabled."""
        if self._lock_values.get():
            self._capture_locked_values(silent=True)


    def _on_lock_z_toggle(self):
        """Auto-capture current node Z when lock is first enabled."""
        if self._lock_z.get():
            self._capture_locked_z(silent=True)


    def _capture_locked_values(self, silent=False):
        """Capture current inspector values into the lock."""
        try:
            b12 = int(self._v_b12.get()) & 0xFF
            b15 = int(self._v_b15.get()) & 0xFF
        except (ValueError, tk.TclError):
            if not silent:
                self.status("Lock values: select a node first")
            return
        self._locked_b12.set(b12)
        self._locked_b15.set(b15)
        self._lock_values.set(True)
        try:
            dev = bool(self.dev_mode.get())
        except Exception:
            dev = False
        if dev:
            lbl = f"b12={b12} b15={b15}"
        else:
            lbl = f"Control={b12} Radius={b15}"
        self._locked_values_label.configure(text=lbl, fg=self._ui_theme()['text'])
        if not silent:
            self.status(f"Locked: {lbl}")


    def _capture_locked_z(self, silent=False):
        """Capture current inspector Z into the Z lock."""
        try:
            z = float(self._v_z.get())
        except (ValueError, tk.TclError):
            if not silent:
                self.status("Lock Z: select a node first")
            return
        self._locked_z.set(round(z, 4))
        self._lock_z.set(True)
        if not silent:
            self.status(f"Locked Z: {z:.4f}m")

    # === help_grid.py (HelpGridMixin) ===

    """Help panel content dictionaries and grid snap/display helpers for AINEditor."""

    # ── Help panel ───────────────────────────────────────────────────────────

    _HELP_CONTENT = {
        'generate': [
            ('Generate mode', None),
            ('LMB',           'Select / deselect'),
            ('Green Generate','Run node generator'),
        ],
        'edit': [
            ('Edit mode', None),
            ('LMB',              'Select node'),
            ('RMB',              'Place node'),
            ('LMB drag',         'Move node'),
            ('Shift+LMB drag',   'Box select'),
            ('Shift+RMB drag',   'Cut connections'),
            ('Ctrl+RMB drag',    'Connect touched nodes'),
            ('Delete',           'Delete selected'),
            ('Ctrl+C / Ctrl+V',  'Copy / Paste'),
            ('Ctrl+Z',           'Undo'),
            ('Ctrl+R',           'Rebuild selected neighbors'),
            ('Arrow keys',       'Nudge node 1 m'),
            ('Shift/Ctrl+Arrows','Nudge node 0.1 m'),
            ('Shift+Numpad',     '8/2/4/6: nudge 0.1 m'),
        ],
        'draw': [
            ('Draw Nodes mode', None),
            ('LMB / drag',     'Place nodes'),
            ('Grid snap',      'Enable in left panel'),
            ('Lock values',    'Enable in inspector'),
            ('Lock Z',         'Enable in inspector'),
        ],
        'paint': [
            ('Paint Zone mode', None),
            ('LMB drag',        'Paint zone'),
        ],
        'zone': [
            ('Area Zones mode', None),
            ('LMB',             'Place zone point'),
            ('LMB drag (body)', 'Move zone'),
            ('RMB',             'Zone options'),
            ('Grid Select', None),
            ('LMB / drag',      'Select tiles'),
            ('RMB / drag',      'Erase tiles'),
        ],
    }

    _HELP_GLOBAL = [
        ('All modes', None),
        ('Scroll wheel',    'Zoom in / out'),
        ('LMB / Middle drag', 'Pan'),
        ('U',               'Clear node selection'),
        ('H',               'Toggle this panel'),
    ]


    def _on_snap_toggle(self, *_):
        """Update status bar when snap toggle changes."""
        if self.grid_snap_nodes.get():
            label = self.grid_fixed_spacing_label.get()
            self.status(f"Grid snap ON — nodes will snap to {label} grid")
        else:
            self.status("Grid snap OFF")


    def _grid_step_down(self):
        labels = [l for l, _ in MED_GRID_SPACING_CHOICES]
        cur = self.grid_fixed_spacing_label.get()
        idx = labels.index(cur) if cur in labels else 0
        if idx < len(labels) - 1:
            self.grid_fixed_spacing_label.set(labels[idx + 1])
            self._on_grid_display_change()

    def _grid_step_up(self):
        labels = [l for l, _ in MED_GRID_SPACING_CHOICES]
        cur = self.grid_fixed_spacing_label.get()
        idx = labels.index(cur) if cur in labels else 0
        if idx > 0:
            self.grid_fixed_spacing_label.set(labels[idx - 1])
            self._on_grid_display_change()

    def _on_grid_display_change(self, *_):
        self._sync_grid_render_globals()
        try:
            mode = str(self.grid_mode.get())
            label = str(self.grid_fixed_spacing_label.get())
            if mode == GRID_MODE_MED_FIXED:
                self.status(f"Grid: MED fixed {label}")
            else:
                self.status("Grid: Adaptive zoom spacing")
        except Exception:
            pass
        try:
            self._force_next_render_no_cache = True
            self._schedule_render_idle()
        except Exception:
            try:
                self.redraw()
            except Exception:
                pass

    # === terrain_ui.py (TerrainUIMixin) ===

    """Terrain display mode, debug overlays, sector inspection for AINEditor."""

    # ── 16. TERRAIN ──────────────────────────────────────────────────────────

    def _invalidate_terrain_render_cache(self):
        """Invalidate terrain-dependent frame state without throwing away CModel work.

        C/H/D changes only alter the terrain bitmap.  The old implementation
        called PILRenderer.clear(), which also destroyed the oversized retained
        static-entity/CModel layer.  On campaign maps that turned a cheap terrain
        mode switch into a full building/entity re-rasterization.

        Keep the retained entity layer intact and invalidate only the completed
        scene bitmap used by fast pan/zoom.  Any legacy terrain-only cache fields
        are cleared defensively if present.
        """
        try:
            renderer = self._pil_renderer
            if renderer:
                # A future fast pan must never reuse the previous terrain mode.
                renderer._last_img = None
                renderer._last_vp_off_x = None
                renderer._last_vp_off_y = None
                renderer._last_vp_zoom = None

                # These existed in earlier terrain experiments; keep the
                # invalidation harmless if a downstream build restores them.
                if hasattr(renderer, '_terrain_tile_cache_key'):
                    renderer._terrain_tile_cache_key = None
                if hasattr(renderer, '_terrain_tile_cache_img'):
                    renderer._terrain_tile_cache_img = None

                # Deliberately preserve _static_entity_layer_*: terrain mode does
                # not change entity geometry, category visibility, or CModels.
        except Exception:
            pass


    def _sync_terrain_mode_button_colors(self):
        btns = getattr(self, '_terrain_mode_buttons', None)
        if not btns:
            return
        light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        selected = self.terrain_display_mode.get()
        for mode, btn in btns.items():
            if mode == selected:
                btn.configure(fg='#ffffff')
            else:
                btn.configure(fg='#1c1c1c' if light else C_TEXT)


    def _set_terrain_display_mode(self, mode):
        """Select MED-style C/H/D terrain visualization without changing visibility."""
        selected = str(mode or 'C').strip().upper()
        if selected not in ('C', 'H', 'D'):
            selected = 'C'
        if self.terrain_display_mode.get() != selected:
            self.terrain_display_mode.set(selected)
        if isinstance(self.terrain_info, dict):
            key = None
            if selected == 'H':
                key = 'height_display_image'
            elif selected == 'D':
                key = 'depth_display_image'
            if key and self.terrain_info.get(key) is None:
                self.status(f"Terrain {selected} view unavailable: no DPTH height payload")
                self.terrain_display_mode.set('C')
                selected = 'C'
            self.terrain_info['_display_mode'] = selected
        self._sync_terrain_mode_button_colors()
        self._invalidate_terrain_render_cache()
        self.status(f"Terrain view: {selected}")
        self.redraw()


    def _update_terrain_debug_button_labels(self):
        try:
            if hasattr(self, '_terrain_debug_btn') and self._terrain_debug_btn:
                self._terrain_debug_btn.config(text=_terrain_debug_mode_label(self.terrain_debug_mode))
            if hasattr(self, '_terrain_overlay_btn') and self._terrain_overlay_btn:
                self._terrain_overlay_btn.config(
                    text=f"Terrain Overlay: {'On' if self.terrain_overlay_enabled else 'Off'}"
                )
            if hasattr(self, '_terrain_phase_btn') and self._terrain_phase_btn:
                self._terrain_phase_btn.config(text=_terrain_phase_mode_label(self.terrain_phase_mode))
        except Exception:
            pass


    def _cycle_terrain_debug_mode(self):
        modes = [name for name, _label in TERRAIN_DEBUG_MODES]
        try:
            idx = modes.index(self.terrain_debug_mode)
        except Exception:
            idx = 0
        self.terrain_debug_mode = modes[(idx + 1) % len(modes)]
        self._update_terrain_debug_button_labels()
        self._invalidate_terrain_render_cache()
        print(f"[TERRAIN] debug mode -> {self.terrain_debug_mode}")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        self.status(f"Terrain debug mode: {_terrain_debug_mode_label(self.terrain_debug_mode)}")
        self.redraw()


    def _toggle_terrain_overlay(self):
        self.terrain_overlay_enabled = not bool(self.terrain_overlay_enabled)
        self._update_terrain_debug_button_labels()
        self._invalidate_terrain_render_cache()
        print(f"[TERRAIN] overlay -> {'on' if self.terrain_overlay_enabled else 'off'}")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        self.status(f"Terrain overlay: {'ON' if self.terrain_overlay_enabled else 'OFF'}")
        self.redraw()


    def _show_terrain_debug_report(self):
        info = self.terrain_info
        if not isinstance(info, dict) or not info.get('terrain_trn'):
            messagebox.showinfo("Terrain Report", "No terrain is currently loaded.", parent=self)
            return
        lines = _terrain_debug_report_lines(info)
        try:
            dlg = tk.Toplevel(self)
            dlg.title("Terrain Debug Report")
            dlg.configure(bg=C_PANEL)
            dlg.geometry("860x620")
            dlg.transient(self)
            txtf = tk.Frame(dlg, bg=C_PANEL)
            txtf.pack(fill='both', expand=True, padx=10, pady=10)
            sb = tk.Scrollbar(txtf)
            sb.pack(side='right', fill='y')
            txt = tk.Text(
                txtf, bg='#151515', fg=C_TEXT, relief='flat', wrap='none',
                yscrollcommand=sb.set, font=('Consolas', 9)
            )
            txt.pack(side='left', fill='both', expand=True)
            sb.config(command=txt.yview)
            txt.insert('1.0', '\n'.join(lines))
            txt.config(state='disabled')
            tk.Button(
                dlg, text='Close', command=dlg.destroy,
                bg=C_PANEL2, fg=C_TEXT, relief='flat', padx=10, pady=3
            ).pack(pady=(0, 10))
            dlg.lift()
        except Exception:
            try:
                messagebox.showinfo("Terrain Report", '\n'.join(lines[:40]), parent=self)
            except Exception:
                pass


    def _terrain_visible_sector_debug_lines(self):
        info = self.terrain_info
        if not isinstance(info, dict):
            return ["No terrain info loaded."]
        sp = info.get('spatial') or {}
        sectors = sp.get('sectors') or []
        if not sectors:
            return ["No expanded sector table available."]
        try:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 600
        except Exception:
            cw, ch = 800, 600
        try:
            self._sync_viewport_obj()
            vp = self._viewport
        except Exception:
            vp = None
        if vp is None:
            return ["Viewport unavailable."]
        sector_world = float(sp.get('sector_world', 512.0))
        x0 = float(sp.get('world_x0', -4.0 * sector_world))
        y0 = float(sp.get('world_y0', -4.0 * sector_world))
        wrapx = int(sp.get('wrapx') or 0)
        wrapy = int(sp.get('wrapy') or 0)
        zoom = float(getattr(vp, 'zoom', 1.0) or 1.0)
        wx_min = vp.offset_x - (cw / 2.0) / zoom
        wx_max = vp.offset_x + (cw / 2.0) / zoom
        wy_min = vp.offset_y - (ch / 2.0) / zoom
        wy_max = vp.offset_y + (ch / 2.0) / zoom
        raw_col_min = int(math.floor((wx_min - x0) / sector_world))
        raw_col_max = int(math.floor((wx_max - x0) / sector_world))
        raw_row_min = int(math.floor((wy_min - y0) / sector_world))
        raw_row_max = int(math.floor((wy_max - y0) / sector_world))
        phase_mode = getattr(self, 'terrain_phase_mode', 'current')
        lines = [
            '',
            'Visible sector debug',
            '-' * 40,
            f"Viewport center: ({vp.offset_x:.3f}, {vp.offset_y:.3f}) zoom={zoom:.4f}",
            f"Terrain phase mode: {_terrain_phase_mode_label(phase_mode)}",
            f"MED32 viewport phase: ({_terrain_phase_coord(vp.offset_x, 'med32'):.3f}, {_terrain_phase_coord(vp.offset_y, 'med32'):.3f})",
            'Sector phase modes change sector-table lookup only; 512m draw scale is unchanged.',
            f"Visible world: x=({wx_min:.3f},{wx_max:.3f}) y=({wy_min:.3f},{wy_max:.3f})",
            f"Raw sector span: cols {raw_col_min}..{raw_col_max} rows {raw_row_min}..{raw_row_max}",
        ]
        family_counts = {}
        rendered = 0
        for raw_row in range(raw_row_min, raw_row_max + 1):
            row_raw_idx = _terrain_sector_lookup_raw_index(
                raw_row, y0 + (raw_row + 0.5) * sector_world, y0, sector_world, phase_mode
            )
            world_row = _polytrn_sector_index_med(row_raw_idx, wrapy)
            table_row = 15 - int(world_row)
            row_parts = []
            for raw_col in range(raw_col_min, raw_col_max + 1):
                col_raw_idx = _terrain_sector_lookup_raw_index(
                    raw_col, x0 + (raw_col + 0.5) * sector_world, x0, sector_world, phase_mode
                )
                col = _polytrn_sector_index_med(col_raw_idx, wrapx)
                try:
                    tile_id = int(sectors[table_row][col])
                except Exception:
                    tile_id = -999
                family_name, quad_id = _terrain_tile_family(tile_id)
                family_counts[family_name] = family_counts.get(family_name, 0) + 1
                if phase_mode != 'current':
                    row_parts.append(f"{raw_col}/{col_raw_idx}->{col}:{tile_id}/{family_name or 'unsupported'}:{quad_id}")
                else:
                    row_parts.append(f"{raw_col}->{col}:{tile_id}/{family_name or 'unsupported'}:{quad_id}")
                rendered += 1
            lines.append(f"raw_row {raw_row} -> world_row {world_row} -> table_row {table_row}: " + ' | '.join(row_parts))
        lines.append(f"Visible sector sample count: {rendered}")
        lines.append("Family counts: " + ', '.join(f"{k or 'unsupported'}={v}" for k, v in sorted(family_counts.items(), key=lambda kv: str(kv[0]))))
        return lines


    def _terrain_entity_alignment_debug_lines(self):
        info = self.terrain_info
        if not isinstance(info, dict):
            return ["No terrain info loaded."]
        sp = info.get('spatial') or {}
        tx0 = float(sp.get('world_x0', 0.0))
        ty0 = float(sp.get('world_y0', 0.0))
        tx1 = float(sp.get('world_x1', 0.0))
        ty1 = float(sp.get('world_y1', 0.0))
        tcx = (tx0 + tx1) * 0.5
        tcy = (ty0 + ty1) * 0.5
        ents = [e for e in (self.entities or []) if isinstance(e, dict)]
        lines = [
            '',
            'Entity / terrain alignment debug',
            '-' * 40,
            f"Terrain bounds: x=({tx0:.3f},{tx1:.3f}) y=({ty0:.3f},{ty1:.3f})",
            f"Terrain center: ({tcx:.3f}, {tcy:.3f})",
        ]
        if not ents:
            lines.append('No entities loaded.')
            return lines

        xs = [float(e.get('x', 0.0)) for e in ents]
        ys = [float(e.get('y', 0.0)) for e in ents]
        ex0, ex1 = min(xs), max(xs)
        ey0, ey1 = min(ys), max(ys)
        ecx = (ex0 + ex1) * 0.5
        ecy = (ey0 + ey1) * 0.5
        dx = ecx - tcx
        dy = ecy - tcy
        lines.extend([
            f"Entity bounds: x=({ex0:.3f},{ex1:.3f}) y=({ey0:.3f},{ey1:.3f})",
            f"Entity center: ({ecx:.3f}, {ecy:.3f})",
            f"Entity center delta from terrain center: dx={dx:.3f} dy={dy:.3f}",
            f"Entity center inside terrain box: {'yes' if (tx0 <= ecx <= tx1 and ty0 <= ecy <= ty1) else 'no'}",
            f"Entity count: {len(ents)}",
        ])

        try:
            self._sync_viewport_obj()
            vp = self._viewport
            lines.extend([
                f"Viewport center delta from terrain center: dx={vp.offset_x - tcx:.3f} dy={vp.offset_y - tcy:.3f}",
                f"Viewport center delta from entity center: dx={vp.offset_x - ecx:.3f} dy={vp.offset_y - ecy:.3f}",
            ])
        except Exception:
            pass

        return lines

    # === mouse_node_ops.py (MouseNodeOpsMixin) ===

    """Node context menu, full node data viewer, selection box, node operations."""

    def _node_context_menu(self, event, nid):
        m = self._make_ui_menu(self, persistent=False)
        m.add_command(label=f"Select node {nid}", command=lambda: self.select_node(nid))
        m.add_command(label="Connect from here", command=lambda: self._start_connect_from(nid))
        m.add_separator()
        if bool(self.dev_mode.get()):
            m.add_command(label="See all node data",
                          command=lambda: self._show_full_node_data(nid))
            m.add_separator()
        # Rebuild neighbours — works on selection or just this node
        n_sel = len(self.selected_nodes)
        if n_sel > 1:
            m.add_command(label=f"Rebuild Neighbours ({n_sel} selected)",
                         command=self.rebuild_neighbors)
            m.add_command(label="Link by Distance...",
                          command=self.link_by_distance)
            m.add_command(label=f"Remove Selected Connections ({n_sel} selected)",
                         command=self._remove_selected_connections)
        else:
            m.add_command(label="Rebuild Neighbours",
                         command=self.rebuild_neighbors)
            m.add_command(label="Link by Distance...",
                          command=self.link_by_distance, state='disabled')
            m.add_command(label="Remove Selected Connections",
                         command=self._remove_selected_connections)
        m.add_separator()
        m.add_command(label="Delete node", command=lambda: self._delete_node(nid))
        m.post(event.x_root, event.y_root)


    def _show_full_node_data(self, nid):
        """Show every byte of a node record in a popup window."""
        import struct as _struct

        node = next((n for n in self.nodes if n.id == nid), None)
        if not node:
            return

        win = tk.Toplevel(self)
        win.title(f"Node {nid} — Full Data")
        win.configure(bg=C_PANEL)
        win.resizable(True, True)
        win.geometry("600x680")

        # ── Header ────────────────────────────────────────────────────────────
        tk.Label(win, text=f"Node {nid}  —  Full Raw Record",
                 bg=C_PANEL, fg='#ffdd00', font=('Consolas',10,'bold'),
                 anchor='w').pack(fill='x', padx=12, pady=(12,3))

        # ── Scrollable text area ───────────────────────────────────────────────
        frame = tk.Frame(win, bg=C_PANEL)
        frame.pack(fill='both', expand=True, padx=10, pady=(0,4))
        sb = tk.Scrollbar(frame)
        sb.pack(side='right', fill='y')
        txt = tk.Text(frame, bg='#0a0a0a', fg='#cccccc',
                      font=('Consolas',9), wrap='none',
                      yscrollcommand=sb.set, relief='flat', bd=0)
        txt.pack(fill='both', expand=True)
        sb.config(command=txt.yview)

        # Tag colors
        txt.tag_config('hdr',     foreground='#ffdd00', font=('Consolas',9,'bold'))
        txt.tag_config('known',   foreground='#00cc66')
        txt.tag_config('unknown', foreground='#ff8800')
        txt.tag_config('raw',     foreground='#666666')
        txt.tag_config('val',     foreground='#ffffff')
        txt.tag_config('dim',     foreground='#444444')
        txt.tag_config('hi',      foreground='#ff4444')
        txt.tag_config('sep',     foreground='#333333')

        def line(label, value, note='', tag='known', vtag='val'):
            txt.insert('end', f"  {label:<28}", tag)
            txt.insert('end', f"{str(value):<20}", vtag)
            if note:
                txt.insert('end', f"  # {note}", 'dim')
            txt.insert('end', '\n')

        def sep(title=''):
            if title:
                txt.insert('end', f"\n  ── {title} {chr(8212)*(40-len(title))}\n", 'hdr')
            else:
                txt.insert('end', '\n', 'sep')

        raw = getattr(node, 'raw_bytes', None)

        # ── Known fields from Node object ──────────────────────────────────────
        sep("Position")
        line("X:", f"{node.x:.6f}", "meters (int32 / 65536)")
        line("Y:", f"{node.y:.6f}", "meters (int32 / 65536)")
        line("Z:", f"{node.z:.6f}", "meters (int32 / 65536)")

        sep("Flags  +0x0C..+0x13")
        line("+0x0C  b12  NodeFlags:", f"{node.b12}  (0b{node.b12:08b})",
             f"bit0=precision  bit2=zone_member  bit3=entry_lock  bit7=door_lock(runtime)")
        line("+0x0D  b13  NodeClass:", f"{node.b13}",
             {0:'standard', 1:'dead_class (NovaLogic-era, no engine runstate)',
              2:'zone_portal/doorway', 4:'secondary_assault (role2, needs team_size=4)',
              6:'assault_pos', 7:'rally_point (role3, needs team_size=4)', 8:'grenade_throw', 9:'marker'}.get(node.b13, '?'))
        line("+0x0E  b14  AreaId:", f"{node.b14}", "tactical zone ID (squad system only)")
        line("+0x0F  b15  Radius:", f"{node.b15}",
             f"accept radius = {node.b15 * 0x1000 / 65536:.3f}m")
        line("+0x10  b16  DirSectors:", f"{node.b16}  hi={node.b16>>4}  lo={node.b16&0xf}",
             "stealth nibbles  (0=skip detection)")
        line("+0x11  b17  SecAreaId:", f"{node.b17}", "should = b14 (NAI1 confirmed)")
        line("+0x12  b18  Occupancy:", f"{node.b18}", "runtime only — always 0 in file")
        line("+0x13  LinkCount:", f"{len(node.neighbors)}",
             f"active neighbor slots (max 16)")

        sep("Neighbor indices  +0x14..+0x23  (10 × uint16)")
        if raw and len(raw) >= 0x34:
            for slot in range(10):
                off = 0x14 + slot * 2
                v = _struct.unpack_from('<H', raw, off)[0]
                active = slot < len(node.neighbors)
                note = f"→ node {v}" if active and v < 200000 else ("inactive" if not active else "?")
                tag = 'known' if active else 'dim'
                line(f"+0x{off:02X}  neighbor[{slot}]:", f"{v}  (0x{v:04X})", note, tag=tag)
        else:
            for slot, nb in enumerate(node.neighbors):
                line(f"  neighbor[{slot}]:", nb, "from editor data")

        sep("EdgeFlags  +0x28..+0x31  (10 × uint8)")
        if raw and len(raw) >= 0x34:
            nc = len(node.neighbors)
            for slot in range(10):
                ef = raw[0x28 + slot]
                active = slot < nc
                bits = []
                if ef & 0x01: bits.append("SpecialTraversal")
                if ef & 0x08: bits.append("NoShortcut")
                if ef & 0x10: bits.append("Unk0x10")
                if ef & 0x20: bits.append("Unk0x20")
                other = ef & ~0x39
                if other: bits.append(f"Unk0x{other:02X}")
                note = ', '.join(bits) if bits else ("zero" if ef == 0 else "?")
                if not active and ef != 0:
                    note = f"⚠ INACTIVE SLOT nonzero: {note}"
                tag = 'hi' if (not active and ef != 0) else ('known' if active else 'dim')
                vtag = 'hi' if (not active and ef != 0) else 'val'
                line(f"+0x{0x28+slot:02X}  EdgeFlags[{slot}]:", f"{ef}  (0x{ef:02X})",
                     note, tag=tag, vtag=vtag)
        else:
            txt.insert('end', "  No raw bytes available\n", 'dim')

        sep("Unknown tail  +0x32..+0x33  (uint16)")
        if raw and len(raw) >= 0x34:
            u16 = _struct.unpack_from('<H', raw, 0x32)[0]
            lo  = raw[0x32]
            hi  = raw[0x33]
            line("+0x32  lo byte:", f"{lo}  (0x{lo:02X})",
                 "flood-fill hop distance from seed (generator residue)", tag='unknown')
            line("+0x33  hi byte:", f"{hi}  (0x{hi:02X})",
                 {0:'zero — no tag', 1:'compound member flag', 255:'boundary/cap'}.get(hi,
                 f"rare value — investigate"), tag='unknown')
            line("+0x32  full uint16:", f"{u16}  (0x{u16:04X})",
                 "generator working data — engine ignores at runtime", tag='unknown')
        else:
            txt.insert('end', "  No raw bytes available\n", 'dim')

        sep("Raw hex dump  (full 52 bytes)")
        if raw and len(raw) >= 0x34:
            txt.insert('end', "  ", 'dim')
            for i in range(0x34):
                txt.insert('end', f"{raw[i]:02X} ", 'raw')
                if (i+1) % 16 == 0:
                    txt.insert('end', '\n  ', 'dim')
            txt.insert('end', '\n')
        else:
            txt.insert('end', f"  raw_bytes length: {len(raw) if raw else 0}\n", 'dim')

        sep("Neighbors (editor)")
        line("Neighbor list:", str(list(node.neighbors)), "")

        txt.config(state='disabled')

        # ── Copy button ────────────────────────────────────────────────────────
        def _copy():
            self.clipboard_clear()
            self.clipboard_append(txt.get('1.0', 'end'))
        tk.Button(win, text="Copy all to clipboard", command=_copy,
                  bg=C_PANEL2, fg=C_TEXT, font=('Consolas',8),
                  relief='flat', padx=8, pady=3).pack(pady=(0,8))

        def _close_breach_win():
            try:
                win.destroy()
            finally:
                self._breach_win = None

        win.protocol("WM_DELETE_WINDOW", _close_breach_win)

    # ─── NODE OPERATIONS ────────────────────────────────────────────────────


    def _update_sel_box(self, cx, cy):
        """Draw/update the rubber-band rectangle during drag."""
        if self._sel_box_start is None:
            return
        x0, y0 = self._sel_box_start
        color = '#4488ff' if self._sel_box_mode == 'add' else '#ff4444'
        if self._sel_box_item is not None:
            self.canvas.coords(self._sel_box_item, x0, y0, cx, cy)
        else:
            self._sel_box_item = self.canvas.create_rectangle(
                x0, y0, cx, cy,
                outline=color, width=1, dash=(4, 3),
                tags='sel_box')


    def _selection_node_visual_radius_px(self, node_id=None):
        """Return the on-screen dot radius used for node interaction/selection.

        This keeps rubber-band selection aligned with the visible node size,
        so partially covering a node dot still counts as selecting it.
        """
        try:
            zoom = float(self.vp_zoom)
        except Exception:
            zoom = 1.0
        nr_base = max(2, min(6, zoom * 0.3))
        if node_id is not None and node_id in getattr(self, 'selected_nodes', set()):
            return max(4, nr_base)
        return nr_base


    def _finish_sel_box(self, cx, cy):
        """Apply rubber-band selection on release."""
        if self._sel_box_start is None:
            return
        x0, y0 = self._sel_box_start
        # Canvas-space bounding box (handle any drag direction).  Use the visible
        # node dot radius for hit-testing so selection matches what the user sees.
        x_min, x_max = min(x0, cx), max(x0, cx)
        y_min, y_max = min(y0, cy), max(y0, cy)
        editable_ids = self._get_editable_node_ids()
        count_before = len(self.selected_nodes)
        for node in self.nodes:
            if node.id not in editable_ids:
                continue

            try:
                sx, sy = self.world_to_canvas(node.x, node.y)
            except Exception:
                continue
            rpx = self._selection_node_visual_radius_px(node.id)

            # Select if the selection box intersects the visible node disc bounds.
            hit = (
                (sx + rpx) >= x_min and
                (sx - rpx) <= x_max and
                (sy + rpx) >= y_min and
                (sy - rpx) <= y_max
            )
            if hit:
                if self._sel_box_mode == 'add':
                    self.selected_nodes.add(node.id)
                elif self._sel_box_mode == 'remove':
                    self.selected_nodes.discard(node.id)
        # Update selected_id to last item in set (or None)
        if self.selected_nodes:
            self.selected_id = max(self.selected_nodes)
            self._show_info_for_node_selection()
        else:
            self.selected_id = None
        delta = len(self.selected_nodes) - count_before
        verb = 'added' if self._sel_box_mode == 'add' else 'removed'
        self.status(f"Selection: {abs(delta)} nodes {verb}. Total: {len(self.selected_nodes)}")
        self._update_inspector()
        self._update_stats()
        self.refresh_3d_views(rebuild=True, refit=False, reason='box_selection')

    # === zfilter_zone.py (ZFilterZoneMixin) ===

    """Z-axis filtering, hit testing, zone management."""

    def _default_zfilter_bounds(self):
        """Return the terrain-based default Z-filter range.

        The filter's baseline is the map terrain/floor Z, not the generated
        node height. Generated terrain nodes commonly sit at terrain_z +
        NAV_NODE_Z_LIFT, but the filter window should start at the real floor.
        """
        try:
            base = float(getattr(self, 'terrain_z', 18.5))
        except Exception:
            base = 18.5
        if not math.isfinite(base):
            base = 18.5
        return (round(base, 4), round(base + 1.5, 4))


    def _set_zfilter_to_default_bounds(self):
        zmin, zmax = self._default_zfilter_bounds()
        self._z_filter_last_valid = [zmin, zmax]
        try:
            self.z_filter_min.set(zmin)
            self.z_filter_max.set(zmax)
        except Exception:
            pass
        return zmin, zmax


    def _maybe_apply_zfilter_default_bounds(self):
        """Upgrade stale/invalid old 0..10 defaults when enabling the filter.

        If the user has manually typed a valid custom range, leave it alone.
        """
        try:
            cur_min = self._read_z_filter_value(self.z_filter_min, 0)
            cur_max = self._read_z_filter_value(self.z_filter_max, 1)
        except Exception:
            self._set_zfilter_to_default_bounds()
            return

        old_default = abs(cur_min - 0.0) < 1e-6 and abs(cur_max - 10.0) < 1e-6
        invalid_range = cur_max <= cur_min
        if old_default or invalid_range:
            self._set_zfilter_to_default_bounds()


    def _reset_zfilter(self):
        """Reset Z filter to off state with terrain-based min/max defaults."""
        self.z_filter_on.set(False)
        self._set_zfilter_to_default_bounds()
        self._zf_frame.pack_forget()
        self._update_zfilter_top_indicator()


    def _update_zfilter_top_indicator(self):
        """Show a compact top-right warning while the visibility Z filter is active."""
        ind = getattr(self, '_zfilter_top_indicator', None)
        if ind is None:
            return
        try:
            active = bool(self.z_filter_on.get())
        except Exception:
            active = False
        try:
            if active:
                # Pack to the right side. This keeps it near Help/Grid without
                # adding another permanent top-bar status block while inactive.
                if not ind.winfo_ismapped():
                    ind.pack(side='right', padx=(2, 4))
            else:
                if ind.winfo_ismapped():
                    ind.pack_forget()
            self._sync_topbar_theme()
        except Exception:
            pass


    def _on_zfilter_toggle(self):
        """Show/hide the Z filter min/max fields and redraw."""
        if self.z_filter_on.get():
            self._maybe_apply_zfilter_default_bounds()
            separator = getattr(self, '_post_z_filter_separator', None)
            if separator is not None:
                self._zf_frame.pack(fill='x', before=separator)
            else:
                self._zf_frame.pack(fill='x')
        else:
            self._zf_frame.pack_forget()
        self._update_zfilter_top_indicator()
        self.update_idletasks()
        self._rebuild_hit_tester()
        self._update_inspector()
        self.redraw()
        self.after(80, self._update_scroll_arrow)


    def _on_zfilter_value_change(self):
        """Called on every keystroke in Z filter fields. Debounced rebuild."""
        if not self.z_filter_on.get():
            return
        if hasattr(self, '_zf_debounce_job') and self._zf_debounce_job:
            self.after_cancel(self._zf_debounce_job)
        self._zf_debounce_job = self.after(300, self._zfilter_apply)


    def _read_z_filter_value(self, variable, index):
        """Read a Tk numeric variable without letting malformed Entry text crash."""
        fallback = self._z_filter_last_valid[index]
        try:
            raw = self.tk.globalgetvar(variable._name)
            value = float(raw)
        except (tk.TclError, TypeError, ValueError):
            value = fallback
            try:
                variable.set(value)
            except tk.TclError:
                pass
        else:
            self._z_filter_last_valid[index] = value
        return value


    def _get_z_filter_bounds(self):
        return (
            self._read_z_filter_value(self.z_filter_min, 0),
            self._read_z_filter_value(self.z_filter_max, 1),
        )


    def _zfilter_apply(self):
        self._zf_debounce_job = None
        self._get_z_filter_bounds()
        self._rebuild_hit_tester()
        self.redraw()


    def _z_visible(self, node):
        """Return True if node passes the current Z filter."""
        if not self.z_filter_on.get():
            return True
        zmin, zmax = self._get_z_filter_bounds()
        return zmin <= node.z <= zmax


    def _get_visible_nodes(self):
        """Return nodes passing layer and Z filters.

        For soft Z mode, also include connected neighbors that pass the
        non-Z layer filters.
        """
        b12_visible_ids = self._get_layer_visible_ids()
        if not self.z_filter_on.get():
            if b12_visible_ids is None:
                return self.nodes
            return [n for n in self.nodes if n.id in b12_visible_ids]
        zmin, zmax = self._get_z_filter_bounds()
        # Hard pass: nodes within range
        in_range = set()
        for n in self.nodes:
            if zmin <= n.z <= zmax:
                in_range.add(n.id)
        if b12_visible_ids is not None:
            in_range &= b12_visible_ids
        return [n for n in self.nodes if n.id in in_range]


    # ── 10. HIT TESTER ───────────────────────────────────────────────────────

    def _rebuild_hit_tester(self):
        """Rebuild HitTester respecting Z filter. Stores node IDs."""
        visible = self._get_visible_nodes()
        self._hit_tester.rebuild(visible)

    # ─────────────────────────────────────────────────────────────────────────────
    # AREA ZONE SYSTEM
    # ─────────────────────────────────────────────────────────────────────────────

    ZONE_PALETTE = [
        '#ff8c00','#00c8ff','#ff50c8','#50ff50',
        '#ffff00','#c864ff','#ff6464','#64ffc8',
    ]


    # ── 11. ZONE SYSTEM ──────────────────────────────────────────────────────

    def _mark_zones_dirty(self):
        """Call whenever zones are added, moved, or deleted."""
        self._zone_overlay_dirty = True
        try:
            self._refresh_zone_list()
            self._sync_zone_panel_from_selected()
        except Exception:
            pass


    def _default_zone_meta(self, anum):
        return {
            'name': f'Zone {anum}',
            'color': '#00ffaa',
            'nodes': set(),
            'points': [],          # polygon/control points in world coords
            'b12': 0,
            'b15': 36,
            'ignore_collisions': False,
        }


    def _ensure_zone_meta(self, anum):
        meta = self.zone_meta.get(anum)
        if meta is None:
            meta = self._default_zone_meta(anum)
            self.zone_meta[anum] = meta
        else:
            # Compatibility with JSON lists and old project saves.
            if 'nodes' not in meta:
                meta['nodes'] = set()
            elif not isinstance(meta['nodes'], set):
                meta['nodes'] = set(meta.get('nodes') or [])
            meta.setdefault('name', f'Zone {anum}')
            meta.setdefault('color', '#00ffaa')
            meta.setdefault('points', [])
            meta.setdefault('b12', 0)
            meta.setdefault('b15', 36)
            meta.setdefault('ignore_collisions', False)
        return meta


    def _zone_snapshot(self):
        import copy
        return (
            self._snapshot_nodes(),
            copy.deepcopy(self.zones),
            copy.deepcopy(self.zone_meta),
            self._zone_selected,
        )


    def _restore_zone_snapshot(self, snap):
        nodes, zones, zone_meta, selected = snap
        self._restore_nodes(nodes)
        self.zones = zones
        self.zone_meta = zone_meta
        for anum in list(self.zones.keys()):
            self._ensure_zone_meta(anum)
        self._zone_selected = selected if selected in self.zones else None
        self._mark_zones_dirty()
        self._sync_zone_panel_from_selected()
        self._rebuild_hit_tester()
        self._update_stats()
        self.redraw()


    def _zone_color(self, area_num):
        try:
            return self._ensure_zone_meta(area_num).get('color') or '#00ffaa'
        except Exception:
            return '#00ffaa'


    def _zone_points(self, anum):
        """Return editable area-control points for a zone. Old rect zones get 2 implicit points."""
        if anum not in self.zones:
            return []
        meta = self._ensure_zone_meta(anum)
        pts = meta.get('points') or []
        if not pts:
            x1, y1, x2, y2 = self.zones[anum]
            pts = [(x1, y1), (x2, y2)]
            meta['points'] = list(pts)
        return list(pts)


    def _update_zone_rect_from_points(self, anum):
        pts = self._zone_points(anum)
        if not pts:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.zones[anum] = [min(xs), min(ys), max(xs), max(ys)]


    def _valid_hex_color(self, value, fallback='#00ffaa'):
        v = str(value or '').strip()
        if len(v) == 7 and v[0] == '#':
            try:
                int(v[1:], 16)
                return v.lower()
            except Exception:
                pass
        return fallback

    # === undo_select.py (UndoSelectMixin) ===

    """Drawing mode selection, rubber-band selection, undo/redo system."""

    def select_node(self, nid):
        if nid is not None and 0 <= nid < len(self.nodes):
            if not self._node_is_interactive(self.nodes[nid]):
                return
        old_sel = set(self.selected_nodes)
        self.selected_id = nid
        if nid is None:
            self.selected_nodes = set()
        else:
            self.selected_nodes = {nid}
            self._show_info_for_node_selection()
        self._update_inspector()
        self._update_stats()
        # Recolor nodes and edges in place — no full redraw needed
        self._recolor_selection(old_sel, self.selected_nodes)
        self.refresh_3d_views(rebuild=True, refit=False, reason='selection')

    # ─── RUBBER-BAND SELECTION ───────────────────────────────────────────────


    def _clear_paint_live_overlay(self):
        try:
            self.canvas.delete('paint_live')
        except Exception:
            pass
        self._paint_stroke_live_items = {}

    def _draw_paint_live_nodes(self, node_ids, zone=None):
        """Draw cheap live recolor dots for nodes changed during the current paint stroke.

        This gives the old immediate visual feedback without forcing a full PIL
        redraw/3D sync on every drag event. The real render is still committed
        once on mouse release.
        """
        if not node_ids:
            return
        c = getattr(self, 'canvas', None)
        if c is None:
            return
        try:
            if zone is None:
                zone = int(self._paint_zone.get()) & 0xFF
        except Exception:
            zone = 0
        col = ZONE_COLORS[int(zone) % len(ZONE_COLORS)]
        live_items = getattr(self, '_paint_stroke_live_items', None)
        if live_items is None:
            live_items = {}
            self._paint_stroke_live_items = live_items

        # Match the existing node-dot feel closely enough for live feedback,
        # but keep it light: one filled dot + one tiny ring per changed node.
        try:
            dot_r = 3 if float(self.vp_zoom) >= 5.0 else 2
            ring_r = dot_r + 2
        except Exception:
            dot_r = 3
            ring_r = 5

        for nid in node_ids:
            if nid in live_items:
                continue
            if not (0 <= nid < len(self.nodes)):
                continue
            try:
                n = self.nodes[nid]
                cx, cy = self.world_to_canvas(n.x, n.y)
                ring = c.create_oval(cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                                     outline=col, width=1, tags='paint_live')
                dot = c.create_oval(cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r,
                                    fill=col, outline=col, width=1, tags='paint_live')
                live_items[nid] = (ring, dot)
            except Exception:
                pass

    def _begin_paint_stroke(self):
        """Start one b14 paint stroke.

        The old paint path created a full node snapshot and a full redraw for
        every mouse-move event.  On a 3k+ node map, dragging the brush could
        produce dozens of deep copies per second.  A stroke now gets one undo
        snapshot, one undo entry, one final redraw, and one 3D sync.
        """
        if getattr(self, '_paint_stroke_active', False):
            return
        try:
            zone = int(self._paint_zone.get()) & 0xFF
        except Exception:
            zone = 0
        self._paint_stroke_active = True
        self._paint_stroke_snap = None      # lazy: only snapshot on first real change
        self._paint_stroke_zone = zone
        self._paint_stroke_changed = set()

    def _finish_paint_stroke(self):
        changed = set(getattr(self, '_paint_stroke_changed', set()) or set())
        snap = getattr(self, '_paint_stroke_snap', None)
        zone = getattr(self, '_paint_stroke_zone', None)

        self._paint_stroke_active = False
        self._paint_stroke_snap = None
        self._paint_stroke_zone = None
        self._paint_stroke_changed = set()
        self._clear_paint_live_overlay()

        if changed and snap is not None:
            self._push_undo(f'Paint zone {zone} ({len(changed)} nodes)',
                            lambda s=snap: self._restore_nodes(s))
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='paint_zone')
            self.status(f"Painted {len(changed)} nodes with zone {zone}")
        else:
            self._update_paint_preview_overlay()

    def _paint_at(self, wx, wy):
        try:
            zone = int(self._paint_zone.get()) & 0xFF
        except Exception:
            zone = 0
        try:
            radius = float(self._paint_radius.get())
        except Exception:
            radius = 5.0
        r2 = radius * radius

        if not getattr(self, '_paint_stroke_active', False):
            self._begin_paint_stroke()

        editable = self._get_editable_node_ids()
        changed_now = 0
        changed_ids = []
        for nid, node in enumerate(self.nodes):
            if nid not in editable:
                continue
            if (node.x - wx) ** 2 + (node.y - wy) ** 2 <= r2:
                if int(getattr(node, 'b14', 0)) != zone:
                    if self._paint_stroke_snap is None:
                        self._paint_stroke_snap = self._snapshot_nodes()
                    node.b14 = zone
                    self._paint_stroke_changed.add(nid)
                    changed_ids.append(nid)
                    changed_now += 1

        if changed_now:
            self._draw_paint_live_nodes(changed_ids, zone)
            total = len(getattr(self, '_paint_stroke_changed', set()) or set())
            self.status(f"Painting zone {zone}: {total} node" + ('' if total == 1 else 's'))


    # ── 8. UNDO / REDO ───────────────────────────────────────────────────────

    def _push_undo(self, description, restore_fn):
        """Push a restore function onto the undo stack."""
        self._undo_stack.append((description, restore_fn))
        if len(self._undo_stack) > self._UNDO_MAX:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_menu()


    def _snapshot_history_interaction_state(self):
        """Capture selection + locked 3D working-set state around Undo/Redo.

        Selection and the locked 3D node set are editor interaction state, not
        navigation data.  A topology-only edit such as adding/removing an edge
        must not make the visible locked set disappear when history is applied.
        """
        try:
            selected_nodes = {int(n) for n in (self.selected_nodes or set())}
        except Exception:
            selected_nodes = set()
        try:
            selected_id = (None if self.selected_id is None else int(self.selected_id))
        except Exception:
            selected_id = None
        return {
            'selected_id': selected_id,
            'selected_nodes': selected_nodes,
            'lock_active': bool(getattr(self, '_locked_3d_node_set_active', False)),
            'locked_ids': set(getattr(self, '_locked_3d_node_ids', set()) or set()),
            'anchor_ids': set(getattr(self, '_locked_3d_anchor_node_ids', set()) or set()),
            'scope_note': str(getattr(self, '_locked_3d_node_scope_note', '') or ''),
            'reset_reason': str(getattr(self, '_locked_3d_node_reset_reason', '') or ''),
        }


    def _restore_history_interaction_state(self, state, identity_stable):
        """Restore interaction state when node IDs survived the history step.

        Node-count changes are treated conservatively because insertion/deletion
        can reindex IDs.  In that case the old safety behaviour remains: clear
        live selection and drop the locked 3D set instead of showing wrong nodes.

        Exception: when the restore function already restored the lock state
        (via _history_lock_restored flag), skip lock processing — the restore
        had the correct pre-operation IDs.
        """
        if not identity_stable:
            self.selected_id = None
            self.selected_nodes = set()
            lock_already_handled = bool(getattr(self, '_history_lock_restored', False))
            self._history_lock_restored = False
            if not lock_already_handled:
                if bool(getattr(self, '_locked_3d_node_set_active', False)):
                    self._reset_3d_node_lock('history changed node identity')
            return

        valid = set(range(len(self.nodes)))
        selected = {int(n) for n in state.get('selected_nodes', set()) if int(n) in valid}
        sid = state.get('selected_id')
        try:
            sid = None if sid is None else int(sid)
        except Exception:
            sid = None
        if sid not in valid:
            sid = None
        self.selected_nodes = selected
        self.selected_id = sid

        if bool(state.get('lock_active')):
            locked = {int(n) for n in state.get('locked_ids', set()) if int(n) in valid}
            anchor = {int(n) for n in state.get('anchor_ids', set()) if int(n) in valid}
            if locked:
                self._locked_3d_node_set_active = True
                self._locked_3d_node_ids = locked
                self._locked_3d_anchor_node_ids = anchor or set(locked)
                self._locked_3d_node_scope_note = str(state.get('scope_note', '') or '')
                self._locked_3d_node_reset_reason = str(state.get('reset_reason', '') or '')
            else:
                self._locked_3d_node_set_active = False
                self._locked_3d_node_ids = set()
                self._locked_3d_anchor_node_ids = set()
                self._locked_3d_node_scope_note = ''
                self._locked_3d_node_reset_reason = 'history left no valid locked nodes'


    def undo(self):
        if not self._undo_stack:
            self.status('Nothing to undo.')
            return
        desc, restore_fn = self._undo_stack.pop()
        snap = self._zone_snapshot()
        lock_snap = self._snapshot_3d_lock_state()
        interaction = self._snapshot_history_interaction_state()
        node_count_before = len(self.nodes)
        def _redo_restore(s=snap, ls=lock_snap):
            self._restore_zone_snapshot(s)
            self._restore_3d_lock_state(ls)
        self._redo_stack.append((desc, _redo_restore))
        self._history_restore_active = True
        self._history_lock_restored = False
        try:
            restore_fn()
            identity_stable = (len(self.nodes) == node_count_before)
            self._restore_history_interaction_state(interaction, identity_stable)
            self.next_id = len(self.nodes)
            self._update_inspector()
            self._update_stats()
            self._update_undo_menu()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='history_undo')
            self.status(f"Undo: {desc}")
        finally:
            self._history_restore_active = False


    def redo(self):
        if not self._redo_stack:
            self.status('Nothing to redo.')
            return
        desc, restore_fn = self._redo_stack.pop()
        snap = self._zone_snapshot()
        lock_snap = self._snapshot_3d_lock_state()
        interaction = self._snapshot_history_interaction_state()
        node_count_before = len(self.nodes)
        def _undo_restore(s=snap, ls=lock_snap):
            self._restore_zone_snapshot(s)
            self._restore_3d_lock_state(ls)
        self._undo_stack.append((desc, _undo_restore))
        self._history_restore_active = True
        self._history_lock_restored = False
        try:
            restore_fn()
            identity_stable = (len(self.nodes) == node_count_before)
            self._restore_history_interaction_state(interaction, identity_stable)
            self.next_id = len(self.nodes)
            self._update_inspector()
            self._update_stats()
            self._update_undo_menu()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='history_redo')
            self.status(f"Redo: {desc}")
        finally:
            self._history_restore_active = False


    def _snapshot_nodes(self):
        """Return a snapshot of current nodes list for undo.

        Manual slot-copy instead of copy.deepcopy — only the mutable
        'neighbors' list needs cloning; every other slot is immutable
        (int, float, bytes, frozenset, bool, None).
        """
        result = []
        _new = object.__new__
        _Node = Node
        _append = result.append
        for n in self.nodes:
            c = _new(_Node)
            c.id = n.id
            c.x = n.x; c.y = n.y; c.z = n.z
            c.b12 = n.b12; c.b13 = n.b13; c.b14 = n.b14; c.b15 = n.b15
            c.b16 = n.b16; c.b17 = n.b17; c.b18 = n.b18
            c.neighbors = list(n.neighbors)
            c.raw_bytes = n.raw_bytes
            c.quantized_source_keys = n.quantized_source_keys
            c.quantized_map_grid = n.quantized_map_grid
            c.scan_0x32 = n.scan_0x32
            _append(c)
        return result


    def _restore_nodes(self, snapshot):
        """Restore nodes list from a snapshot (used by undo)."""
        old_count = len(self.nodes)
        self.nodes = snapshot
        self.next_id = len(self.nodes)
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        # During Undo/Redo, never reset the locked 3D working set — the
        # history wrapper restores interaction state and performs the final
        # scene rebuild.  Even count-changing restores (delete/insert undo)
        # should preserve the lock so the 3D view stays stable.
        history_safe = bool(getattr(self, '_history_restore_active', False))
        reason = 'history_restore_nodes' if history_safe else 'restore_nodes'
        self.refresh_3d_views(rebuild=True, refit=False, reason=reason)


    def _reset_transient_editor_state(self, clear_undo=False):
        """Reset transient edit/session state when loading or replacing data."""
        self.selected_id = None
        self.selected_nodes = set()
        self._connecting_from = None
        self._drag_start = None
        self._drag_node_start = None
        self._edge_brush_active = False
        self._edge_brush_stroke = []
        self._edge_brush_hits = []
        self._drag_node_snap = None
        self._sel_box_start = None
        self._sel_box_mode = None
        self._nudge_origin = None
        if self._nudge_after:
            try:
                self.after_cancel(self._nudge_after)
            except Exception:
                pass
            self._nudge_after = None
        if getattr(self, '_zf_debounce_job', None):
            try:
                self.after_cancel(self._zf_debounce_job)
            except Exception:
                pass
            self._zf_debounce_job = None
        if getattr(self, '_settle_job', None):
            try:
                self.after_cancel(self._settle_job)
            except Exception:
                pass
            self._settle_job = None
        if getattr(self, '_redraw_job', None):
            try:
                self.after_cancel(self._redraw_job)
            except Exception:
                pass
            self._redraw_job = None
        self._is_transforming = False
        self._render_pending = False
        self._synced_zoom = None
        self._synced_offset = (None, None)
        self._zoom_op_count = 0
        self._zone_vp_snapshot = None
        if self._sel_box_item is not None:
            try:
                self.canvas.delete(self._sel_box_item)
            except Exception:
                pass
            self._sel_box_item = None
        try:
            self.canvas.delete('connect_preview')
            self.canvas.delete('edge_brush_stroke')
            self.canvas.delete('sel_box')
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        self._locked_3d_node_set_active = False
        self._locked_3d_node_ids = set()
        self._locked_3d_anchor_node_ids = set()
        self._locked_3d_node_scope_note = ''
        self._locked_3d_node_reset_reason = 'map reload'
        if clear_undo:
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._update_undo_menu()


    def _update_undo_menu(self):
        """Refresh Undo/Redo menu item states."""
        try:
            m = self._editm
            if self._undo_stack:
                m.entryconfig(0, state='normal',
                              label=f"Undo: {self._undo_stack[-1][0]}  [Ctrl+Z]")
            else:
                m.entryconfig(0, state='disabled', label="Undo  [Ctrl+Z]")
            if self._redo_stack:
                m.entryconfig(1, state='normal',
                              label=f"Redo: {self._redo_stack[-1][0]}  [Ctrl+Y]")
            else:
                m.entryconfig(1, state='disabled', label="Redo  [Ctrl+Y]")
        except Exception:
            pass

    # === zoom_render.py (ZoomRenderMixin) ===

    """Coordinate-transform zoom preview and node-drag render helpers."""

    def _start_zoom_preview_burst_if_needed(self):
        """Capture a stable bitmap at the start of a wheel burst."""
        try:
            if self._zoom_preview_base is None:
                r = self._pil_renderer
                if r is not None and getattr(r, '_last_img', None) is not None:
                    self._zoom_preview_base = {
                        'img': r._last_img.copy(),
                        'cw': int(r._last_img.size[0]),
                        'ch': int(r._last_img.size[1]),
                        'zoom': float(getattr(r, '_last_vp_zoom', self.vp_zoom) or self.vp_zoom),
                        'off_x': float(getattr(r, '_last_vp_off_x', self.vp_offset_x) or self.vp_offset_x),
                        'off_y': float(getattr(r, '_last_vp_off_y', self.vp_offset_y) or self.vp_offset_y),
                    }
        except Exception:
            self._zoom_preview_base = None


    def _schedule_zoom_preview(self):
        """Coalesce many wheel events into a throttled bitmap preview."""
        if not bool(globals().get('ZOOM_BITMAP_PREVIEW', True)):
            return False
        try:
            self._start_zoom_preview_burst_if_needed()
            if self._zoom_preview_base is None:
                return False
            self._zoom_preview_pending = {
                'zoom': float(self.vp_zoom),
                'off_x': float(self.vp_offset_x),
                'off_y': float(self.vp_offset_y),
            }
            if self._zoom_preview_job is None:
                delay = int(globals().get('ZOOM_PREVIEW_THROTTLE_MS', 33))
                self._zoom_preview_job = self.after(max(1, delay), self._run_zoom_preview_job)
            return True
        except Exception:
            return False


    def _run_zoom_preview_job(self):
        self._zoom_preview_job = None
        try:
            if not self._zoom_preview_pending or not self._zoom_preview_base:
                return
            base_state = self._zoom_preview_base
            pending = self._zoom_preview_pending
            self._zoom_preview_pending = None

            ok = self._zoom_preview_from_bitmap_state(base_state, pending)
            if ok:
                self._zoom_preview_active = True

            if self._zoom_preview_pending is not None:
                self._schedule_zoom_preview()
        except Exception:
            self._zoom_preview_job = None


    def _zoom_preview_from_bitmap_state(self, base_state, pending_state):
        """Temporary zoom preview.

        Primary mode:
        - redraw cheap background/grid at the current target viewport
        - transform the oversized retained static-entity layer into that view
        - fall back to old full-screen bitmap preview only if needed

        This tests the theory that MED approximates only expensive retained
        layers while cheap viewport/grid state updates immediately.
        """
        # When the radius fill is on, use the FULL-bitmap preview so nodes + the alpha fill
        # zoom WITH the scene instead of vanishing during the gesture. The cheap static-layer
        # preview only carries entities/grid (no nodes/edges/fill), which is why the fill was
        # disappearing while zooming. The fill is baked into _last_img, so the full preview
        # scales it correctly.
        prefer_full = False
        try:
            _fill_on = bool(self.show_radius_fill.get() or self.show_radius_fill_b15.get())
            _radius_on = bool(self.show_radius.get())
            _keep_fill = bool(getattr(self, 'keep_radius_fill', tk.BooleanVar(value=False)).get())
            prefer_full = bool(_fill_on and (_radius_on or _keep_fill))
        except Exception:
            prefer_full = False

        if bool(globals().get('ZOOM_STATIC_LAYER_PREVIEW', True)) and not prefer_full:
            ok = self._zoom_static_layer_preview_from_state(base_state, pending_state)
            if ok:
                return True

        if prefer_full or bool(globals().get('ZOOM_STATIC_LAYER_PREVIEW_FALLBACK_BITMAP', True)):
            return self._zoom_full_bitmap_preview_from_state(base_state, pending_state)
        return False


    def _zoom_static_layer_preview_from_state(self, base_state, pending_state):
        """Preview from retained oversized static entity layer.

        This avoids the black zoom-out border because the background/grid
        is freshly redrawn, and missing entity pixels outside the retained layer
        are transparent rather than black.
        """
        if not bool(globals().get('ZOOM_BITMAP_PREVIEW', True)):
            return False
        try:
            r = self._pil_renderer
            if r is None:
                return False

            layer = getattr(r, '_static_entity_layer_img', None)
            layer_vp = getattr(r, '_static_entity_layer_vp', None)
            if layer is None or layer_vp is None:
                return False

            cw = self.canvas.winfo_width() or int(base_state.get('cw', 800) or 800)
            ch = self.canvas.winfo_height() or int(base_state.get('ch', 600) or 600)
            if cw <= 0 or ch <= 0:
                return False

            old_zoom = float(getattr(layer_vp, 'zoom', base_state.get('zoom', 1.0)) or 1.0)
            old_off_x = float(getattr(layer_vp, 'offset_x', base_state.get('off_x', 0.0)) or 0.0)
            old_off_y = float(getattr(layer_vp, 'offset_y', base_state.get('off_y', 0.0)) or 0.0)

            new_zoom = float(pending_state.get('zoom', self.vp_zoom))
            new_off_x = float(pending_state.get('off_x', self.vp_offset_x))
            new_off_y = float(pending_state.get('off_y', self.vp_offset_y))

            lw, lh = layer.size
            scale = old_zoom / max(0.0001, new_zoom)

            # Destination screen -> source retained static-layer coordinates.
            # layer coordinates use its own center because it was rendered as
            # an oversized viewport, not as a simple screen crop.
            a = scale
            b = 0.0
            c = (new_off_x - old_off_x) * old_zoom + (lw / 2.0) - scale * (cw / 2.0)
            d = 0.0
            e = scale
            f = (old_off_y - new_off_y) * old_zoom + (lh / 2.0) - scale * (ch / 2.0)

            resample_name = str(globals().get('ZOOM_PREVIEW_RESAMPLE', 'bilinear')).lower()
            try:
                resample = Image.Resampling.BILINEAR if resample_name == 'bilinear' else Image.Resampling.NEAREST
            except Exception:
                resample = Image.BILINEAR if resample_name == 'bilinear' else Image.NEAREST

            # Fresh truthful cheap frame for the target viewport.
            frame = Image.new('RGB', (cw, ch), r.pil_bg)
            try:
                vp = Viewport(new_off_x, new_off_y, new_zoom)
                if bool(self.show_terrain.get()) and self.terrain_image is not None:
                    r._draw_terrain_backdrop(
                        frame, self.terrain_image, self.terrain_info, vp, cw, ch, new_zoom,
                        show_water=self.show_water.get())
                draw = ImageDraw.Draw(frame)
                if bool(self.show_grid.get()):
                    r._draw_grid(draw, vp, cw, ch, new_zoom)
            except Exception:
                pass

            try:
                transformed = layer.transform(
                    (cw, ch),
                    Image.AFFINE,
                    (a, b, c, d, e, f),
                    resample=resample,
                    fillcolor=(0, 0, 0, 0)
                )
            except TypeError:
                transformed = layer.transform(
                    (cw, ch),
                    Image.AFFINE,
                    (a, b, c, d, e, f),
                    resample=resample
                )

            frame.paste(transformed, (0, 0), transformed)
            self._draw_live_nodes_over_preview(frame, new_off_x, new_off_y, new_zoom, cw, ch)
            r._blit(frame, cw, ch)

            self._zoom_preview_count = int(getattr(self, '_zoom_preview_count', 0) or 0) + 1
            self._zoom_static_layer_preview_count = int(getattr(self, '_zoom_static_layer_preview_count', 0) or 0) + 1
            self._zoom_static_layer_preview_last = {
                'mode': 'static_layer',
                'source_size': (lw, lh),
                'scale': round(float(scale), 4),
            }
            return True
        except Exception:
            return False


    def _draw_live_nodes_over_preview(self, frame, new_off_x, new_off_y, new_zoom, cw, ch):
        """Draw exact current nodes/radius over temporary zoom preview."""
        try:
            if not getattr(self, 'show_nodes', None) or not self.show_nodes.get():
                return False
            if not getattr(self, 'nodes', None):
                return False
            r = self._pil_renderer
            if r is None:
                return False

            z_visible = None
            try:
                b12_visible_ids = self._get_layer_visible_ids()
                if self.z_filter_on.get():
                    zmin, zmax = self._get_z_filter_bounds()
                    z_visible = set(n.id for n in self.nodes if zmin <= n.z <= zmax)
                    if b12_visible_ids is not None:
                        z_visible &= b12_visible_ids
                elif b12_visible_ids is not None:
                    z_visible = b12_visible_ids
            except Exception:
                z_visible = None

            draw = ImageDraw.Draw(frame)
            vp = Viewport(float(new_off_x), float(new_off_y), float(new_zoom))
            r._draw_nodes(
                draw,
                self.nodes,
                vp,
                int(cw),
                int(ch),
                self.selected_nodes,
                bool(self.show_zones.get()),
                bool(self.show_radius.get()),
                False,
                float(new_zoom),
                z_visible,
                show_b16_arrows=False,
                show_b12_extended=bool(self.show_b12_extended.get()),
                show_b12_bands=bool(self.show_b12_bands.get())
            )
            return True
        except Exception:
            return False


    def _zoom_full_bitmap_preview_from_state(self, base_state, pending_state):
        """Full visible-frame bitmap preview retained as a fallback."""
        if not bool(globals().get('ZOOM_BITMAP_PREVIEW', True)):
            return False
        try:
            r = self._pil_renderer
            if r is None:
                return False
            base = base_state.get('img')
            if base is None:
                return False
            cw = self.canvas.winfo_width() or base.size[0]
            ch = self.canvas.winfo_height() or base.size[1]
            if base.size[0] != cw or base.size[1] != ch:
                return False

            old_zoom = float(base_state.get('zoom', 1.0))
            old_off_x = float(base_state.get('off_x', 0.0))
            old_off_y = float(base_state.get('off_y', 0.0))
            new_zoom = float(pending_state.get('zoom', self.vp_zoom))
            new_off_x = float(pending_state.get('off_x', self.vp_offset_x))
            new_off_y = float(pending_state.get('off_y', self.vp_offset_y))

            scale = old_zoom / max(0.0001, new_zoom)
            old_center_x = cw / 2.0 + (new_off_x - old_off_x) * old_zoom
            old_center_y = ch / 2.0 - (new_off_y - old_off_y) * old_zoom

            a = scale
            b = 0.0
            c = old_center_x - scale * (cw / 2.0)
            d = 0.0
            e = scale
            f = old_center_y - scale * (ch / 2.0)

            resample_name = str(globals().get('ZOOM_PREVIEW_RESAMPLE', 'nearest')).lower()
            try:
                resample = Image.Resampling.BILINEAR if resample_name == 'bilinear' else Image.Resampling.NEAREST
            except Exception:
                resample = Image.NEAREST

            preview = base.transform((cw, ch), Image.AFFINE, (a, b, c, d, e, f), resample=resample)
            r._blit(preview, cw, ch)
            self._zoom_preview_count = int(getattr(self, '_zoom_preview_count', 0) or 0) + 1
            self._zoom_static_layer_preview_last = {'mode': 'fallback_full_bitmap'}
            return True
        except Exception:
            return False

    # ── Phase 4: transform-aware rendering ─────────────────────────────────


    # ── 5b. NODE DRAG RENDER HELPERS ──────────────────────────────────────

    def _schedule_node_drag_redraw(self):
        """Fast redraw path used while dragging selected nodes.

        Avoids entity/CModel rendering and coalesces mouse motion events.
        """
        if self._node_drag_render_job is None:
            self._node_drag_render_job = self.after(16, self._do_node_drag_redraw)
        if self._node_drag_inspector_job is None:
            self._node_drag_inspector_job = self.after(60, self._do_node_drag_inspector)


    def _do_node_drag_redraw(self):
        self._node_drag_render_job = None
        self._node_drag_fast = True
        try:
            self._pil_render(fast_mode=False, force_no_cache=True)
            self.refresh_3d_views(rebuild=True, refit=False, reason='node_drag')
        finally:
            # Keep flag true until release; next drag frames should stay cheap.
            self._node_drag_fast = True


    def _do_node_drag_inspector(self):
        self._node_drag_inspector_job = None
        try:
            self._update_inspector()
            self._update_stats()
        except Exception:
            pass

    # === ui_helpers_events.py (UIHelpersEventsMixin) ===

    """UI helpers/callbacks and keyboard/mouse event binding."""

    def _switch_tab(self, force=False):
        """Open, switch, or collapse the lower Info/Tiler tool drawer."""
        t = self._tab.get()
        active = getattr(self, '_active_tab', None)

        # Clicking the already-open tab collapses the whole lower drawer.
        # Programmatic callers can pass force=True when they specifically need
        # a tab visible (for example the existing mode-change workflow).
        if not force and t and t == active:
            self._info_frame.pack_forget()
            self._tiler_frame.pack_forget()
            self._bytes_frame.pack_forget()
            self._tab_frame.pack_forget()
            self._active_tab = None
            self._tab.set('')
            try:
                self.after_idle(self._refresh_left_panel_scrollregion)
            except Exception:
                pass
            return

        self._info_frame.pack_forget()
        self._tiler_frame.pack_forget()
        self._bytes_frame.pack_forget()

        if t not in ('info', 'tiler', 'bytes'):
            self._tab_frame.pack_forget()
            self._active_tab = None
            self._tab.set('')
            try:
                self.after_idle(self._refresh_left_panel_scrollregion)
            except Exception:
                pass
            return

        # The content container itself is also collapsible so it reserves no
        # inspector/tiler height while both tabs are closed.
        if not self._tab_frame.winfo_manager():
            self._tab_frame.pack(fill='both', expand=True, padx=4, pady=4)

        if t == 'info':
            self._info_frame.pack(fill='both', expand=True)
        elif t == 'tiler':
            self._tiler_frame.pack(fill='both', expand=True)
        else:
            self._bytes_frame.pack(fill='both', expand=True)
            self._refresh_bytes_tab()

        self._active_tab = t
        try:
            self.after_idle(self._refresh_left_panel_scrollregion)
        except Exception:
            pass


    def _show_info_for_node_selection(self):
        """Expand the Info drawer when a node selection becomes active.

        Selection may open/switch to Info, but deselection never closes it; only
        an explicit tab click collapses the drawer.
        """
        try:
            if getattr(self, '_active_tab', None) == 'info':
                return
            self._tab.set('info')
            self._switch_tab(force=True)
        except Exception:
            pass


    def _on_mode_change(self):
        m = self.mode.get()
        if m != 'paint' and getattr(self, '_paint_stroke_active', False):
            self._finish_paint_stroke()
        # Reset draw state when leaving draw mode
        self._draw_last_wx = None
        self._draw_last_wy = None
        self._draw_last_b15 = None
        self._draw_no_grid_warned = False
        if m == 'paint':
            self._tab.set('tiler')
            self._switch_tab(force=True)
        else:
            self._tab.set('info')
            self._switch_tab(force=True)
        self._connecting_from = None
        self._update_zone_panel_visibility()
        if m == 'zone':
            self._enter_zone_mode()
        else:
            if self._tile_select_active:
                self._exit_tile_select()
        self.status(f"Mode: {m.capitalize()}")
        self._update_paint_tool_ui()
        # Update mode button active states
        if hasattr(self, "_update_mode_btns"):
            self._update_mode_btns()
        # Refresh help panel content if visible
        if getattr(self, "_help_visible", False):
            self._refresh_help_content()

    # ─── EVENT BINDING ──────────────────────────────────────────────────────


    def _clear_node_selection_shortcut(self):
        """Clear the live node selection without touching graph data or 3D locks."""
        old_sel = set(getattr(self, 'selected_nodes', set()) or set())
        old_id = getattr(self, 'selected_id', None)
        if old_id is None and not old_sel:
            try:
                self.status("Selection already clear")
            except Exception:
                pass
            return False
        self.selected_id = None
        self.selected_nodes = set()
        self._update_inspector()
        self._update_stats()
        try:
            self._recolor_selection(old_sel, self.selected_nodes)
        except Exception:
            try:
                self.redraw()
            except Exception:
                pass
        try:
            self.status("Node selection cleared")
        except Exception:
            pass
        # Keep an already-open 3D viewport synchronized, but do not unlock its
        # explicit working set or refit/move its camera.
        try:
            self.refresh_3d_views(rebuild=True, refit=False, reason='3d_selection')
        except Exception:
            pass
        return True


    def _toggle_2d_grid_shortcut(self):
        """Toggle the 2D grid only; the 3D viewport keeps its own G binding."""
        self.show_grid.set(not bool(self.show_grid.get()))
        self.status(f"Grid {'ON' if self.show_grid.get() else 'OFF'}")
        self.redraw()


    def _fit_all_nodes_shortcut(self):
        """Run the existing 2D Fit all nodes command and refresh open 3D chrome."""
        self.fit_view()
        # Fitting the 2D camera does not change the graph, but a detached/active
        # 3D view may be open at the same time. Refresh it without rebuilding or
        # refitting so the operation cannot disturb its camera/working set.
        try:
            self.refresh_3d_views(rebuild=False, refit=False, reason='2d_fit')
        except Exception:
            pass


    def _bind_events(self):
        return _ui_events.bind_events(self)

    # === ain_pass.py (AINPassMixin) ===

    """AIN Pass / post-generation tools: cleanup, scan applier chooser overlay."""

    def _on_escape_key(self, _event=None):
        """Esc closes/cancels the current modal editor state first."""
        try:
            if self._cancel_seed_generate('cancelled'):
                return 'break'
            if self._cancel_scan_seed('cancelled'):
                return 'break'
            if getattr(self, '_ain_pass_overlay', None) is not None:
                self._hide_ain_pass_overlay()
                return 'break'
            if getattr(self, '_ain_pass_mode', None):
                self._exit_ain_pass_mode()
                return 'break'
            if getattr(self, '_connect_id_overlay', None) is not None:
                self._hide_connect_id_overlay()
                return 'break'
        except Exception:
            pass
        self._cancel_connect()
        return 'break'


    def _toggle_ain_pass_overlay(self):
        if getattr(self, '_ain_pass_overlay', None) is not None:
            self._hide_ain_pass_overlay()
            return
        if getattr(self, '_ain_pass_mode', None):
            self._exit_ain_pass_mode()
            return
        if bool(self.dev_mode.get()):
            self._show_ain_pass_overlay()
        else:
            self._enter_cleanup_mode()


    def _hide_ain_pass_overlay(self):
        ov = getattr(self, '_ain_pass_overlay', None)
        self._ain_pass_overlay = None
        try:
            if ov is not None and ov.winfo_exists():
                ov.destroy()
        except Exception:
            pass


    def _show_ain_pass_overlay(self):
        """Show the in-editor AIN Pass chooser overlay centered over the map."""
        try:
            if getattr(self, '_ain_pass_overlay', None) is not None:
                self._ain_pass_overlay.lift()
                return
            parent = getattr(self, '_canvas_frame', None) or self
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
            if light:
                pal = {
                    'bg': '#f8f8f8',
                    'outer': '#d0d0d0',
                    'card': '#ffffff',
                    'card_disabled': '#f0f0f0',
                    'card_outline': '#777777',
                    'card_outline_disabled': '#aaaaaa',
                    'text': '#111111',
                    'disabled_text': '#555555',
                    'hover': '#1f6fb2',
                    'close_bg': '#ffe4e4',
                    'close_outline': '#d9534f',
                    'close_x': '#d40000',
                }
            else:
                pal = {
                    'bg': '#161616',
                    'outer': '#444444',
                    'card': '#202020',
                    'card_disabled': '#1b1b1b',
                    'card_outline': '#707070',
                    'card_outline_disabled': '#4c4c4c',
                    'text': '#eeeeee',
                    'disabled_text': '#8a8a8a',
                    'hover': '#3388cc',
                    'close_bg': '#3a1515',
                    'close_outline': '#9a3333',
                    'close_x': '#ff5555',
                }

            ov = tk.Frame(parent, bg=pal['bg'], bd=0,
                          highlightthickness=1, highlightbackground=pal['outer'])
            self._ain_pass_overlay = ov
            ov.place(relx=0.5, rely=0.43, anchor='center', width=382, height=222)
            ov.lift()

            c = tk.Canvas(ov, width=382, height=222, bg=pal['bg'],
                          highlightthickness=0, bd=0, cursor='arrow')
            c.pack(fill='both', expand=True)

            hover = {'card': None}
            cleanup_box = (42, 42, 174, 186)
            scan_box = (206, 42, 338, 186)
            close_box = (352, 10, 374, 32)

            def _draw_node(x, y, r=7, fill=None, outline='#22c766'):
                if fill is None:
                    fill = pal['bg']
                c.create_oval(x-r, y-r, x+r, y+r, fill=fill, outline=outline, width=1)
                c.create_oval(x-2, y-2, x+2, y+2, fill=outline, outline='')

            def _draw():
                c.delete('all')
                c.create_rectangle(0, 0, 381, 221, fill=pal['bg'], outline='')

                # Decorative AIN motif nodes/edges.  They are intentionally inert.
                c.create_line(24, 30, 54, 18, 78, 30, fill='#22c766', width=1)
                _draw_node(24, 30, 8); _draw_node(54, 18, 7); _draw_node(78, 30, 8)
                c.create_line(24, 30, 24, 62, fill='#22c766', width=1)
                _draw_node(24, 62, 13)

                c.create_line(322, 18, 356, 20, 360, 50, 342, 70, fill='#22c766', width=1)
                for x, y, col in [(322, 18, '#ffb347'), (356, 20, '#22c766'), (360, 50, '#22c766'), (342, 70, '#22c766')]:
                    _draw_node(x, y, 5, outline=col)
                c.create_line(352, 30, 374, 34, 374, 86, fill='#22c766', width=1)
                _draw_node(374, 34, 4); _draw_node(374, 86, 8)

                c.create_line(170, 196, 210, 196, 230, 208, 260, 196, 300, 206, 344, 194, 374, 198,
                              fill='#22c766', width=1)
                for x, y, col, r in [(170,196,'#22c766',5), (210,196,'#22c766',6), (230,208,'#ff66cc',6),
                                     (260,196,'#22c766',5), (300,206,'#22c766',6), (344,194,'#22c766',6),
                                     (374,198,'#22c766',5)]:
                    _draw_node(x, y, r, outline=col)

                # Close button.
                c.create_rectangle(*close_box, fill=pal['close_bg'], outline=pal['close_outline'], width=1, tags=('close',))
                c.create_line(357, 15, 369, 27, fill=pal['close_x'], width=2, tags=('close',))
                c.create_line(369, 15, 357, 27, fill=pal['close_x'], width=2, tags=('close',))

                def _card_text(box, title, lines, color, tag):
                    # Draw each line explicitly instead of using Canvas text wrapping.
                    # This keeps spacing predictable while preserving readable font size.
                    x0, y0, x1, y1 = box
                    c.create_text(x0+18, y0+18, text=title, anchor='nw',
                                  fill=color, font=('Consolas', 10, 'bold'), tags=(tag,))
                    y = y0 + 54
                    for line in lines:
                        c.create_text(x0+10, y, text=line, anchor='nw',
                                      fill=color, font=('Consolas', 8), tags=(tag,))
                        y += 13

                # Cleanup card: active.
                clean_outline = pal['hover'] if hover['card'] == 'cleanup' else pal['card_outline']
                clean_width = 4 if hover['card'] == 'cleanup' else 3
                c.create_rectangle(*cleanup_box, fill=pal['card'], outline=clean_outline, width=clean_width,
                                   tags=('cleanup_card',))
                _card_text(cleanup_box, 'Cleanup',
                           ['Clean leftover', 'node residues', 'from generator.'],
                           pal['text'], 'cleanup_card')

                # Scan applier card: active.
                scan_outline = pal['hover'] if hover['card'] == 'scan' else pal['card_outline']
                scan_width = 4 if hover['card'] == 'scan' else 3
                c.create_rectangle(*scan_box, fill=pal['card'], outline=scan_outline, width=scan_width,
                                   tags=('scan_card',))
                _card_text(scan_box, 'Scan applier',
                           ['Stamp distance', 'field and meta-', 'data on nodes.'],
                           pal['text'], 'scan_card')

            def _inside(box, x, y):
                x0, y0, x1, y1 = box
                return x0 <= x <= x1 and y0 <= y <= y1

            def _motion(e):
                if _inside(cleanup_box, e.x, e.y):
                    card = 'cleanup'
                elif _inside(scan_box, e.x, e.y):
                    card = 'scan'
                else:
                    card = None
                if card != hover['card']:
                    hover['card'] = card
                    c.configure(cursor='hand2' if card else 'arrow')
                    _draw()

            def _click(e):
                if _inside(close_box, e.x, e.y):
                    self._hide_ain_pass_overlay()
                    return
                if _inside(cleanup_box, e.x, e.y):
                    self._enter_cleanup_mode()
                    return
                if _inside(scan_box, e.x, e.y):
                    self._enter_scan_mode()
                    return

            c.bind('<Motion>', _motion)
            c.bind('<Leave>', lambda _e: (hover.__setitem__('card', None), c.configure(cursor='arrow'), _draw()))
            c.bind('<Button-1>', _click)
            _draw()
            self.status('Graph Tools: choose Cleanup or Scan applier.')
        except Exception as ex:
            self.status(f'Graph Tools overlay error: {ex}')


    def _enter_cleanup_mode(self):
        """Close chooser and enter the direct graph-cleaning mode."""
        self._hide_ain_pass_overlay()
        self._ain_pass_mode = 'cleanup'
        self._show_cleanup_hud()
        self.status('Cleanup mode: select a region, then Clean graph. Ctrl+Z restores mistakes.')


    def _exit_ain_pass_mode(self):
        self._ain_pass_mode = None
        self._scan_seed_armed = False
        try:
            self.canvas.delete('scan_radius_preview')
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        for attr in ('_cleanup_hud', '_scan_hud'):
            hud = getattr(self, attr, None)
            setattr(self, attr, None)
            try:
                if hud is not None and hud.winfo_exists():
                    hud.destroy()
            except Exception:
                pass
        self.status('Graph Tools mode closed.')


    def _show_cleanup_hud(self):
        return _ui_dialogs.show_cleanup_hud(self)


    def _cleanup_parse_thresholds(self):
        try:
            ratio = float(str(self._cleanup_ratio_var.get()).strip().replace('%', ''))
        except Exception:
            ratio = 30.0
            try: self._cleanup_ratio_var.set('30')
            except Exception: pass
        try:
            tiny = int(float(str(self._cleanup_tiny_var.get()).strip()))
        except Exception:
            tiny = 10
            try: self._cleanup_tiny_var.set('10')
            except Exception: pass
        ratio = max(0.0, min(100.0, ratio))
        tiny = max(0, tiny)
        return ratio, tiny


    def _graph_components_for_ids(self, node_ids):
        """Connected components inside node_ids, using links as undirected edges."""
        try:
            ids = {int(n) for n in (node_ids or []) if 0 <= int(n) < len(self.nodes)}
        except Exception:
            ids = set()
        if not ids:
            return []

        adj = {nid: set() for nid in ids}
        for nid in ids:
            try:
                for nb in self.nodes[nid].neighbors:
                    try:
                        nb = int(nb)
                    except Exception:
                        continue
                    if nb in ids:
                        adj[nid].add(nb)
                        adj[nb].add(nid)
            except Exception:
                pass

        seen = set()
        comps = []
        for start in sorted(ids):
            if start in seen:
                continue
            stack = [start]
            seen.add(start)
            comp = set()
            while stack:
                cur = stack.pop()
                comp.add(cur)
                for nb in adj.get(cur, ()):
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            comps.append(comp)
        comps.sort(key=lambda c: (-len(c), min(c) if c else 0))
        return comps


    def _cleanup_clean_selected_region(self):
        """Delete nominated residue components without cutting a valid graph.

        Connectivity is always measured across every graph node, so view and Z
        filters cannot turn a fragment of a valid graph into a fake island. The
        largest complete component touched by the selected region is the local
        protected reference; the global largest component remains protected too.
        """
        if self._generator_edit_blocked():
            return
        editable_ids = self._get_editable_node_ids()
        selected = {
            int(n) for n in getattr(self, 'selected_nodes', set())
            if 0 <= int(n) < len(self.nodes) and int(n) in editable_ids
        }
        if not selected:
            self.status('Cleanup: select a region first. Use Shift + LMB box selection.')
            return

        all_ids = set(range(len(self.nodes)))
        comps = self._graph_components_for_ids(all_ids)
        if len(comps) <= 1:
            self.status(f'Cleanup: complete graph has one component ({len(all_ids)} nodes). Nothing to delete.')
            return

        ratio, tiny = self._cleanup_parse_thresholds()
        largest_size = max(1, len(comps[0]))
        total_size = max(1, len(all_ids))

        node_to_comp = {}
        for index, comp in enumerate(comps):
            for nid in comp:
                node_to_comp[nid] = index
        touched_indices = {
            node_to_comp[nid] for nid in selected if nid in node_to_comp
        }
        if not touched_indices:
            self.status('Cleanup: selection did not touch a complete graph component.')
            return

        # A selected region nominates residue around a local valid graph; it
        # does not nominate that graph itself for deletion.  Using the largest
        # touched complete component as the reference preserves independently
        # generated seeds while complete-graph connectivity still prevents a
        # box/Z-filter boundary from splitting the real main graph.
        protected_index = min(
            touched_indices,
            key=lambda index: (-len(comps[index]), index))
        protected_size = max(1, len(comps[protected_index]))
        threshold = protected_size * (ratio / 100.0)

        if len(touched_indices) == 1:
            self.status(
                f'Cleanup: selection touches one complete component '
                f'({protected_size} nodes); protected. Nothing to delete.')
            return

        # Explicitly hidden/locked tactical zones remain protected.  Ordinary
        # node visibility and Z filtering do not affect graph connectivity.
        protected_zone_ids = (
            set(getattr(self, '_hidden_zone_ids', set())) |
            set(getattr(self, '_locked_zone_ids', set()))
        )
        removable = []
        protected_matches = 0
        for index in sorted(touched_indices):
            if index == 0 or index == protected_index:
                continue  # global main and the local selected reference are safe
            comp = comps[index]
            csz = len(comp)
            if not (csz <= tiny or csz <= threshold):
                continue
            if protected_zone_ids and any(
                    getattr(self.nodes[nid], 'b14', 0) in protected_zone_ids
                    for nid in comp):
                protected_matches += 1
                continue
            removable.append(comp)

        remove_ids = set().union(*removable) if removable else set()
        if not remove_ids:
            touched_sizes = ', '.join(
                str(len(comps[index]))
                for index in sorted(
                    touched_indices,
                    key=lambda index: (-len(comps[index]), index))[:8])
            self.status(
                f'Cleanup: no selected islands matched. Touched sizes=[{touched_sizes}] '
                f'ratio<={ratio:g}% of protected graph ({protected_size} nodes) tiny<={tiny}'
                + (f'; protected={protected_matches}.' if protected_matches else '.')
            )
            return

        removed_count = len(remove_ids)
        island_count = len(removable)
        self._delete_nodes_bulk(remove_ids)
        self.status(
            f'Cleanup: deleted {removed_count} node(s) from {island_count} island(s). '
            f'Protected local reference {protected_size} node(s) and global main '
            f'{largest_size}/{total_size}. Ctrl+Z restores.'
        )


    def _cleanup_delete_smaller_picked_component(self):
        """Delete the smallest full component touched by the current selection."""
        if self._generator_edit_blocked():
            return
        editable_ids = self._get_editable_node_ids()
        selected = {
            int(n) for n in getattr(self, 'selected_nodes', set())
            if 0 <= int(n) < len(self.nodes) and int(n) in editable_ids
        }
        if len(selected) < 2:
            self.status('Cleanup: select nodes from at least two separate components first.')
            return

        # Picked nodes nominate components, but connectivity is always measured
        # across the complete graph.  View and Z filters cannot split a CC.
        comps = self._graph_components_for_ids(range(len(self.nodes)))
        node_to_comp = {}
        for idx, comp in enumerate(comps):
            for nid in comp:
                node_to_comp[nid] = idx

        touched = []
        seen = set()
        for nid in sorted(selected):
            ci = node_to_comp.get(nid)
            if ci is None or ci in seen:
                continue
            seen.add(ci)
            touched.append(comps[ci])

        if len(touched) < 2:
            self.status('Cleanup: picked nodes are in the same component. Pick one node from the real graph and one from the island.')
            return

        touched.sort(key=lambda c: (len(c), min(c) if c else 0))
        if len(touched) >= 2 and len(touched[0]) == len(touched[1]):
            self.status('Cleanup: two smallest picked components have the same size. Refusing to guess.')
            return

        target = touched[0]
        removed_count = len(target)
        sizes = ', '.join(str(len(c)) for c in sorted(touched, key=lambda c: len(c), reverse=True))
        self._delete_nodes_bulk(set(target))
        self.status(f'Cleanup: deleted smaller picked component ({removed_count} node(s)). Picked component sizes: {sizes}. Ctrl+Z restores.')

    # === viewport_render.py (ViewportRenderMixin) ===

    """Methods for viewport rendering and draw pipeline in AINEditor."""

    # ── 5. VIEWPORT / RENDER ─────────────────────────────────────────────────

    def world_to_canvas(self, wx, wy):
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        cx = _world_to_screen_x(wx, self.vp_offset_x, self.vp_zoom, cw / 2.0)
        cy = _world_to_screen_y(wy, self.vp_offset_y, self.vp_zoom, ch / 2.0)
        return cx, cy


    def canvas_to_world(self, cx, cy):
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        wx = (cx - cw/2) / self.vp_zoom + self.vp_offset_x
        wy = -(cy - ch/2) / self.vp_zoom + self.vp_offset_y
        return wx, wy


    def redraw(self, full=True):
        """PIL-backed redraw. Draws all scene layers into one bitmap, blits once.
        All persistent renderer complexity replaced by a single PIL render call.
        """
        c = self.canvas
        cw = c.winfo_width()
        ch = c.winfo_height()
        # If canvas not yet mapped, force layout and retry
        if cw <= 1 or ch <= 1:
            self.update_idletasks()
            cw = c.winfo_width() or 800
            ch = c.winfo_height() or 600

        # ── PIL renders all scene layers (grid, entities, edges, nodes, radius, labels)
        self._pil_render()

        # ── Overlays — lightweight Tk items drawn on top of PIL bitmap ────────
        c.delete('connect_preview')
        if self._connecting_from is not None and self._connecting_from < len(self.nodes):
            fn = self.nodes[self._connecting_from]
            fcx, fcy = self.world_to_canvas(fn.x, fn.y)
            try:
                mx = self.winfo_pointerx() - self.canvas.winfo_rootx()
                my = self.winfo_pointery() - self.canvas.winfo_rooty()
                c.create_line(fcx, fcy, mx, my,
                              fill=C_BLUE, width=1, dash=(4, 4),
                              tags='connect_preview')
            except Exception:
                pass

        self._update_paint_preview_overlay()


    def _pil_render(self, fast_mode=False, force_no_cache=False):
        try:
            self._pil_renderer.show_context_markers = bool(self.show_context_markers.get())
            globals()['SHOW_FOLIAGE'] = bool(self.show_foliage.get())
            self._pil_renderer.foliage_hull_only = bool(self.show_foliage_hulls.get())
            self._pil_renderer.foliage_trees_only = bool(self.show_trees_only.get())
        except Exception:
            pass
        """Central PIL render call. Draws all scene layers into one bitmap."""
        if self._pil_renderer is None:
            return
        import time
        _t0 = time.perf_counter()
        self._sync_viewport_obj()
        if force_no_cache and self._pil_renderer is not None:
            try:
                self._pil_renderer._last_img = None
                self._pil_renderer._last_vp_zoom = None
                self._pil_renderer._last_vp_off_x = None
                self._pil_renderer._last_vp_off_y = None
                # Do NOT invalidate the static entity retained layer here.
                # force_no_cache is also used by pan/grid freshness logic, and
                # clearing the static layer on every pan made the renderer rebuild it
                # constantly instead of reusing it.
            except Exception:
                pass
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        n = len(self.nodes)
        if n > 700:   edge_zoom_thresh = 2.5
        elif n > 300: edge_zoom_thresh = 1.5
        else:         edge_zoom_thresh = 0.8

        z = float(self.vp_zoom)
        heavy = n >= int(globals().get('LOD_NODE_HEAVY_COUNT', 5000))

        lod_show_radius = self.show_radius.get()
        lod_show_radius_fill = self.show_radius_fill.get()
        lod_show_radius_fill_b15 = self.show_radius_fill_b15.get()
        if not lod_show_radius and not bool(getattr(self, 'keep_radius_fill', tk.BooleanVar(value=False)).get()):
            lod_show_radius_fill = False
            lod_show_radius_fill_b15 = False
        lod_show_edges = self.show_edges.get()
        lod_show_entities = self.show_entities.get()
        lod_show_ids = self.show_ids.get()
        lod_show_b16 = self.show_b16_arrows.get()
        lod_show_b12_ext = self.show_b12_extended.get()
        lod_show_breach = self.show_breach_zones.get()
        if lod_show_breach:
            lod_breach_ids = (
                getattr(self, '_breach_zone_ids',  set()),
                getattr(self, '_assault_zone_ids', set()),
                getattr(self, '_both_zone_ids',    set()),
            )
        else:
            lod_breach_ids = None
        # Debug overlay — pass a bound method if any overlay is active
        lod_debug_fn = self._debug_overlay_color if self._any_debug_overlay_active() else None
        # Zone layer state
        lod_hidden_zones = getattr(self, '_hidden_zone_ids', set()) or None
        lod_locked_zones = getattr(self, '_locked_zone_ids', set()) or None

        # Node dragging must keep entities/buildings visible for placement
        # context. Only skip node extras that clutter and cost time; a full
        # render still happens on release.
        if getattr(self, '_node_drag_fast', False):
            lod_show_entities = True
            lod_show_radius = bool(self.show_radius.get())
            lod_show_ids = False
            lod_show_b16 = False
            # Keep node colors/radius stable during drag/zoom; do not override Extended b12.

        if heavy:
            if z < float(globals().get('LOD_HIDE_EDGES_ZOOM', 0.45)):
                lod_show_edges = False
            if z < float(globals().get('LOD_HIDE_ENTITIES_ZOOM', 0.22)):
                lod_show_entities = False
            if z < float(globals().get('LOD_SIMPLE_NODES_ZOOM', 0.65)):
                lod_show_ids = False
                lod_show_b16 = False
                lod_show_b12_ext = False

        if isinstance(self.terrain_info, dict):
            self.terrain_info['_debug_mode'] = self.terrain_debug_mode
            self.terrain_info['_phase_mode'] = self.terrain_phase_mode
            self.terrain_info['_debug_overlay'] = bool(self.terrain_overlay_enabled)

        z_filter_enabled = self.z_filter_on.get()
        if z_filter_enabled:
            z_filter_min, z_filter_max = self._get_z_filter_bounds()
        else:
            z_filter_min = z_filter_max = None
        b12_visible_ids = self._get_layer_visible_ids()

        self._pil_renderer._show_edge_zone_color = self.show_edge_zone_color.get()
        self._pil_renderer.render(
            self.nodes, self.entities, self._viewport, cw, ch,
            self.selected_nodes,
            self.show_nodes.get(), lod_show_edges,
            self.show_grid.get(), lod_show_entities,
            lod_show_radius, self.show_zones.get(),
            lod_show_ids, edge_zoom_thresh,
            z_filter_enabled,
            z_filter_min,
            z_filter_max,
            lod_show_b16,
            lod_show_b12_ext,
            show_b12_bands=self.show_b12_bands.get(),
            show_breach=lod_show_breach,
            breach_zone_ids=lod_breach_ids,
            debug_overlay_fn=lod_debug_fn,
            hidden_zone_ids=lod_hidden_zones,
            locked_zone_ids=lod_locked_zones,
            fast_mode=fast_mode,
            terrain_image=self.terrain_image,
            show_terrain=self.show_terrain.get(),
            terrain_info=self.terrain_info,
            show_water=self.show_water.get(),
            show_buildings=self.show_buildings.get(),
            show_decorations=self.show_decorations.get(),
            show_radius_fill=lod_show_radius_fill,
            show_radius_fill_b15=lod_show_radius_fill_b15,
            b12_visible_ids=b12_visible_ids,
            show_foliage=self.show_foliage.get(),
            show_vehicles=self.show_vehicles.get(),
            show_objects=self.show_objects.get(),
            simple_nodes=bool(heavy and z < float(globals().get('LOD_SIMPLE_NODES_ZOOM', 0.65))))
        import time
        _ms = (time.perf_counter() - _t0) * 1000
        if DEBUG_RENDER_TIMING and _ms > 5:
            print(f"RENDER {_ms:.1f}ms nodes={len(self.nodes)}")
        # Draw zone overlays only when viewport changed or zones changed
        vp_snap = (round(self._viewport.zoom, 4),
                   round(self._viewport.offset_x, 2),
                   round(self._viewport.offset_y, 2))
        if self._zone_overlay_dirty or vp_snap != self._zone_vp_snapshot:
            self._redraw_zone_overlays()
            self._zone_vp_snapshot = vp_snap
            self._zone_overlay_dirty = False


    def _draw_grid(self, cw, ch):
        c = self.canvas
        # World coords at canvas corners
        wx0, wy0 = self.canvas_to_world(0, ch)
        wx1, wy1 = self.canvas_to_world(cw, 0)

        spacing, grid_mode, safety_note = _grid_spacing_for_render(self.vp_zoom, wx0, wy0, wx1, wy1)

        # Vertical lines
        x = math.floor(wx0/spacing)*spacing
        while x <= wx1:
            cx, _ = self.world_to_canvas(x, 0)
            c.create_line(cx, 0, cx, ch, fill=C_GRID, width=1, tags='grid')
            x += spacing

        # Horizontal lines
        y = math.floor(wy0/spacing)*spacing
        while y <= wy1:
            _, cy = self.world_to_canvas(0, y)
            c.create_line(0, cy, cw, cy, fill=C_GRID, width=1, tags='grid')
            y += spacing

        # Origin cross
        ox, oy = self.world_to_canvas(0, 0)
        c.create_line(ox-10, oy, ox+10, oy, fill='#666666', width=1, tags='grid')
        c.create_line(ox, oy-10, ox, oy+10, fill='#666666', width=1, tags='grid')


    def _draw_entities(self):
        c = self.canvas
        for e in self.entities:
            if not entity_visible_by_layer(
                e,
                self.show_buildings.get(),
                self.show_decorations.get(),
                self.show_foliage.get(),
                self.show_vehicles.get(),
                self.show_objects.get(),
            ):
                continue
            ex, ey = e['x'], e['y']
            ecx, ecy = self.world_to_canvas(ex, ey)
            r = OBSTACLE_RADII.get(e['type_id'], DEFAULT_RADIUS)
            rpx = max(1, r * self.vp_zoom)
            if e['type_id'] in NEVER_EXCLUDE:
                color = C_NEVER_EXCL
                c.create_rectangle(ecx-4, ecy-4, ecx+4, ecy+4,
                                   outline=color, width=1, tags='entity')
            elif e['is_static']:
                color = C_VEHICLE if entity_layer_kind(e) == 'vehicle' else C_ENTITY
                if rpx < 300:
                    c.create_oval(ecx-rpx, ecy-rpx, ecx+rpx, ecy+rpx,
                                  outline=color, width=1, dash=(3,3), tags='entity')
            if self.vp_zoom > 6:
                c.create_text(ecx, ecy, text=str(e['type_id']),
                              fill=C_DIM, font=('Consolas',7), tags='entity_label')


    def _on_light_canvas_toggle(self):
        try:
            self._apply_canvas_theme(bool(self.light_canvas.get()))
        except Exception:
            pass
        self._save_ui_preferences()

    def _apply_canvas_theme(self, light):
        return _ui_theme.apply_canvas_theme(self, light)

    def _open_entity_color_dialog(self):
        return _ui_dialogs.open_entity_color_dialog(self)


    def _apply_entity_custom_colors(self):
        """Apply user-chosen entity colors to the renderer globals and redraw."""
        cfg = _load_editor_cfg()
        light = bool(self.light_canvas.get())
        if light:
            col = cfg.get('entity_color_light', '#464646')
            foliage_col = cfg.get('foliage_color_light', '#2d782d')
        else:
            col = cfg.get('entity_color_dark', C_ENTITY)
            foliage_col = cfg.get('foliage_color_dark', '#3cb43c')
        if self._pil_renderer is not None:
            self._pil_renderer.pil_entity = _hex(col)
            self._pil_renderer.pil_foliage = _hex(foliage_col)
        try:
            r = self._pil_renderer
            if r is not None:
                r.invalidate_static_entity_layer()
                r._last_img = None
        except Exception:
            pass
        self._zoom_preview_base = None
        self.redraw()


    def _sync_3d_preset_button_theme(self):
        return _ui_theme.sync_3d_preset_button_theme(self)


    def _save_ui_preferences(self):
        try:
            cfg = _load_editor_cfg()
            cfg['light_canvas'] = bool(self.light_canvas.get())
            cfg['light_panels'] = bool(self.light_panels.get())
            cfg['developer_mode'] = bool(self.dev_mode.get())
            cfg['show_radius_fill'] = bool(self.show_radius_fill.get())
            cfg['show_radius_fill_b15'] = bool(
                self.show_radius_fill_b15.get())
            cfg['show_scroll_guide'] = bool(self.show_scroll_guide.get())
            _save_editor_cfg(cfg)
        except Exception:
            pass

    def _on_scroll_guide_toggle(self):
        self._save_ui_preferences()
        self._update_scroll_arrow()

    def _on_radius_fill_toggle(self):
        self._save_ui_preferences()
        self.redraw()

    def _on_light_panels_toggle(self):
        return _ui_theme.on_light_panels_toggle(self)

    def _apply_panel_theme(self, light, root=None):
        return _ui_panels.apply_panel_theme(self, light, root)

    # === ui_build.py (UIBuildMixin) ===

    """Methods for menu/UI construction and theme switching in AINEditor."""

    # ── 2. UI BUILD ──────────────────────────────────────────────────────────

    def _ui_theme(self, light=None):
        return _ui_theme.ui_theme(self, light)

    def _menu_theme(self, light=None):
        return _ui_theme.menu_theme(self, light)

    @staticmethod
    def _windows_system_menu_rgb_theme():
        return _ui_theme.windows_system_menu_rgb_theme()

    def _apply_menu_theme(self, menu, light=None):
        return _ui_theme.apply_menu_theme(self, menu, light)

    def _make_ui_menu(self, parent, *, tearoff=0, persistent=True):
        """Create a Tk Menu through the editor's single menu styling path."""
        menu = tk.Menu(parent, tearoff=tearoff, relief='flat', bd=0,
                       activeborderwidth=0)
        self._apply_menu_theme(menu)
        if persistent:
            self._ui_menus.append(menu)
        return menu

    def _sync_all_menu_themes(self):
        return _ui_theme.sync_all_menu_themes(self)

    def _build_ui(self):
        self._build_menu()
        self._build_top_bar()
        self._build_debug_bar()

        # Main layout: left panel + center viewport + optional right Zone Panel
        main = tk.Frame(self, bg=C_BG)
        self._main_frame = main
        main.pack(fill='both', expand=True)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=0)
        main.rowconfigure(0, weight=1)

        self._build_left_panel(main)
        self.after(100, self._bind_all_panel_scroll)
        self._build_canvas(main)
        self._build_zone_panel(main)
        self._update_zone_panel_visibility()
        self._build_status_bar()


    def _build_menu(self):
        mb = self._make_ui_menu(self, tearoff=0)
        self.configure(menu=mb)

        fm = self._make_ui_menu(mb)
        mb.add_cascade(label="File", menu=fm)
        self._file_menu = fm
        self._rebuild_file_menu()

        editm = self._make_ui_menu(mb)
        mb.add_cascade(label="Edit", menu=editm)
        self._editm = editm
        self._rebuild_edit_menu()

        gm = self._make_ui_menu(mb)
        mb.add_cascade(label="Generate", menu=gm)
        gm.add_command(label="Generate from BMS", command=self.do_generate)
        gm.add_command(label="Generate with coordinates...",
                       command=self.do_generate_with_coordinates)
        gm.add_command(label="Previous Generator Seeds...",
                       command=self._show_previous_generator_seeds)
        gm.add_separator()
        gm.add_command(label="Rebuild Neighbors",  command=self.rebuild_neighbors)
        gm.add_command(label="Link by Distance...", command=self.link_by_distance)
        gm.add_command(label="Fix One-Way Connections", command=self._fix_oneway_connections)
        gm.add_separator()
        gm.add_command(label="Fill Selected Zone (Ignore Collisions)...",
                       command=self.fill_selected_zone_ignore_collisions)
        self._apply_menu_theme(gm)

        zm = self._make_ui_menu(mb)
        mb.add_cascade(label="Zones", menu=zm)
        zm.add_command(label="Zone Layers  [F8]",
                       command=self._open_zone_layer_manager)
        zm.add_separator()
        zm.add_command(label="Clear all zones",
                       command=self._clear_all_zones)
        zm.add_separator()
        zm.add_command(label="Delete selected zone  [Del]",
                       command=self._delete_selected_zone)
        self._apply_menu_theme(zm)

        vm = self._make_ui_menu(mb)
        mb.add_cascade(label="View", menu=vm)
        self._view_menu = vm
        self._view_grid_submenu = None
        self._rebuild_view_menu()
        infom = self._make_ui_menu(mb)
        mb.add_cascade(label="Info", menu=infom)
        infom.add_command(label="About AIN Editor", command=self._show_about_ain_editor)
        self._apply_menu_theme(infom)
        self._apply_menu_theme(mb)


    def _rebuild_edit_menu(self):
        """Rebuild Edit so debugging utilities only exist in Developer mode."""
        editm = getattr(self, '_editm', None)
        if editm is None:
            return
        try:
            editm.delete(0, 'end')
        except Exception:
            pass

        editm.add_command(label="Undo  [Ctrl+Z]", command=self.undo,
                          state='normal' if len(getattr(self, 'history', [])) > 1 else 'disabled')
        editm.add_command(label="Redo  [Ctrl+Y]", command=self.redo,
                          state='normal' if bool(getattr(self, 'future', [])) else 'disabled')
        editm.add_separator()
        editm.add_command(label="Remove Selected Connections",
                          command=self._remove_selected_connections)
        editm.add_separator()
        if bool(self.dev_mode.get()):
            editm.add_command(label="Debug Values  [F9]", command=self._toggle_debug_bar)
            editm.add_separator()
        editm.add_command(label="Entity Colors...", command=self._open_entity_color_dialog)
        self._apply_menu_theme(editm)


    def _rebuild_file_menu(self):
        """Rebuild File so PFF diagnostics exist only in Developer mode."""
        fm = getattr(self, '_file_menu', None)
        if fm is None:
            return
        try:
            fm.delete(0, 'end')
        except Exception:
            pass

        fm.add_command(label="New", command=self.new_project)
        fm.add_separator()
        fm.add_command(label="Change Game Folder...", command=self._reselect_game_folder)
        fm.add_command(label="Additional PFF Folders...",
                       command=self._open_additional_pff_folders_dialog)
        fm.add_command(label="Show Loaded PFFs...", command=self._show_loaded_pffs)
        fm.add_separator()
        fm.add_command(label="Open BMS", command=self.open_bms)
        if bool(self.dev_mode.get()):
            fm.add_command(label="Load Terrain MIS", command=self.load_terrain_mis)
        fm.add_command(label="Open AIN", command=self.open_ain)
        fm.add_separator()
        fm.add_command(label="Save Project (JSON)", command=self.save_project)
        fm.add_command(label="Load Project (JSON)", command=self.load_project)
        fm.add_checkbutton(label="Autosave (JSON)", variable=self._autosave_enabled,
                           command=self._on_autosave_toggle)
        fm.add_separator()
        fm.add_command(label="Export AIN", command=self.export_ain)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.quit)
        self._apply_menu_theme(fm)


    def _rebuild_view_menu(self):
        """Rebuild View so reverse-engineering controls exist only in Developer mode."""
        vm = getattr(self, '_view_menu', None)
        if vm is None:
            return

        try:
            vm.delete(0, 'end')
        except Exception:
            pass

        old_grid_sub = getattr(self, '_view_grid_submenu', None)
        if old_grid_sub is not None:
            try:
                old_grid_sub.destroy()
            except Exception:
                pass
        self._view_grid_submenu = None

        dev = bool(self.dev_mode.get())

        vm.add_checkbutton(label="Nodes",    variable=self.show_nodes,    command=self.redraw)
        vm.add_checkbutton(label="Show nodes above terrain",
                           variable=self.show_nodes_above_terrain,
                           command=self._on_terrain_node_visibility_toggle)
        vm.add_checkbutton(label="Show nodes below terrain",
                           variable=self.show_nodes_below_terrain,
                           command=self._on_terrain_node_visibility_toggle)
        vm.add_checkbutton(label="Edges",    variable=self.show_edges,    command=self.redraw)
        vm.add_checkbutton(label="Radius", variable=self.show_radius, command=self.redraw)
        vm.add_checkbutton(label="Zone colours",   variable=self.show_zones,  command=self.redraw)
        vm.add_checkbutton(label="Entities",       variable=self.show_entities, command=self.redraw)
        vm.add_checkbutton(label="Vehicles",       variable=self.show_vehicles, command=self.redraw)
        vm.add_checkbutton(label="Objects",        variable=self.show_objects, command=self.redraw)
        if dev:
            vm.add_checkbutton(label="Other Entities", variable=self.show_context_markers, command=self.redraw)
            vm.add_checkbutton(label="Foliage hulls", variable=self.show_foliage_hulls, command=self.redraw)
        vm.add_checkbutton(label="Trees only", variable=self.show_trees_only, command=self.redraw)
        vm.add_checkbutton(label="Grid",       variable=self.show_grid,       command=self.redraw)
        vm.add_checkbutton(label="Show water", variable=self.show_water,
                           command=self._on_show_water_toggle)
        vm.add_separator()

        # Grid is always MED fixed — no mode toggle needed.  The submenu
        # deliberately uses the exact same menu factory as its parent so Windows
        # system colours, borders, active rows, and dark-mode values cannot drift.
        grid_sub = self._make_ui_menu(vm)
        self._view_grid_submenu = grid_sub
        # Let Tk/Windows draw the native cascade indicator.  Do not embed a
        # second text arrow in the label; Windows already supplies one here.
        vm.add_cascade(label="MED fixed spacing", menu=grid_sub)
        for _label, _value in MED_GRID_SPACING_CHOICES:
            grid_sub.add_radiobutton(label=_label, variable=self.grid_fixed_spacing_label,
                                     value=_label, command=self._on_grid_display_change)

        if dev:
            vm.add_separator()
            vm.add_command(label="Grid / Radius / Offset Diagnostic",
                           command=self._show_grid_radius_diagnostic)
            vm.add_command(label="Terrain Debug Report", command=self._show_terrain_debug_report)

        vm.add_separator()
        vm.add_checkbutton(label="Radius fill (alpha overlap)",
                           variable=self.show_radius_fill,
                           command=self._on_radius_fill_toggle)
        vm.add_checkbutton(label=("Radius fill: b15 colors" if dev else "Radius fill: color by size"),
                           variable=self.show_radius_fill_b15,
                           command=self._on_radius_fill_toggle)
        vm.add_checkbutton(label="Keep radius fill when circles off",
                           variable=self.keep_radius_fill, command=self.redraw)
        if dev:
            vm.add_command(label="b12 value filter...", command=self._open_b12_filter_popup)
        vm.add_checkbutton(label="Node IDs", variable=self.show_ids, command=self.redraw)
        vm.add_checkbutton(label="Connection zone color",
                           variable=self.show_edge_zone_color, command=self.redraw)
        if dev:
            vm.add_checkbutton(label="b16 arrows",
                               variable=self.show_b16_arrows, command=self.redraw)
            vm.add_checkbutton(label="Extended b12 colors",
                               variable=self.show_b12_extended,
                               command=self._on_extended_b12_toggle)
            vm.add_checkbutton(label="b12 Bands",
                               variable=self.show_b12_bands, command=self.redraw)

        vm.add_separator()
        vm.add_checkbutton(label="Light mode: panels/UI",
                           variable=self.light_panels, command=self._on_light_panels_toggle)
        vm.add_checkbutton(label="Light mode: canvas",
                           variable=self.light_canvas, command=self._on_light_canvas_toggle)
        vm.add_checkbutton(label="Scroll arrow guide",
                           variable=self.show_scroll_guide, command=self._on_scroll_guide_toggle)
        vm.add_separator()
        vm.add_checkbutton(label="Developer mode",
                           variable=self.dev_mode, command=self._on_dev_mode_toggle)
        vm.add_separator()
        vm.add_command(label="Fit all nodes", command=self.fit_view)

        self._sync_all_menu_themes()

        # Tk's default dark-menu indicator can render nearly black on Windows,
        # which makes selected View entries hard to distinguish.  Keep only the
        # View menu's check/radio indicators high-contrast for the active panel
        # theme; the rest of each menu entry retains the existing theme colours.
        self._sync_view_grid_submenu_theme()
        self._sync_view_menu_checkmark_colors()


    def _sync_view_grid_submenu_theme(self):
        return _ui_theme.sync_view_grid_submenu_theme(self)


    def _sync_view_menu_checkmark_colors(self):
        """Install a stable, compact native-style View-menu checkmark.

        Modern Windows/Tk can draw its built-in menu indicator much larger than
        older editor builds did.  Keep Tk's unstable native margin hidden, but on
        Windows ask USER32 to draw the real menu-check glyph into an off-screen
        bitmap, normalize that glyph to the old compact footprint, then use it in
        the same fixed image cell for every ordinary View-menu row.  This keeps
        the native Windows shape while the editor controls its final size and
        spacing.  If Win32 rendering is unavailable, fall back to the previously
        tested antialiased custom tick with identical layout dimensions.
        """
        indicator = '#303030' if bool(self.light_panels.get()) else '#d8d8d8'

        # Preserve the spacing that tested well in v97_3.  Only the artwork inside
        # this cell changes: the label column remains exactly where it was.
        _cell_w = 17
        _cell_h = 13
        _target_w = 10
        _target_h = 9

        def _native_windows_check_mask():
            """Return a PIL L-mode mask made from USER32's real menu check glyph."""
            if os.name != 'nt':
                return None
            try:
                import ctypes
                from ctypes import wintypes
                from PIL import Image

                gdi32 = ctypes.windll.gdi32
                user32 = ctypes.windll.user32

                class _BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ('biSize', wintypes.DWORD),
                        ('biWidth', wintypes.LONG),
                        ('biHeight', wintypes.LONG),
                        ('biPlanes', wintypes.WORD),
                        ('biBitCount', wintypes.WORD),
                        ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD),
                        ('biXPelsPerMeter', wintypes.LONG),
                        ('biYPelsPerMeter', wintypes.LONG),
                        ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD),
                    ]

                class _RGBQUAD(ctypes.Structure):
                    _fields_ = [
                        ('rgbBlue', ctypes.c_ubyte),
                        ('rgbGreen', ctypes.c_ubyte),
                        ('rgbRed', ctypes.c_ubyte),
                        ('rgbReserved', ctypes.c_ubyte),
                    ]

                class _BITMAPINFO(ctypes.Structure):
                    _fields_ = [
                        ('bmiHeader', _BITMAPINFOHEADER),
                        ('bmiColors', _RGBQUAD * 1),
                    ]

                class _RECT(ctypes.Structure):
                    _fields_ = [
                        ('left', wintypes.LONG), ('top', wintypes.LONG),
                        ('right', wintypes.LONG), ('bottom', wintypes.LONG),
                    ]

                # Render generously first.  We then crop the actual Windows glyph
                # and scale that *shape* to our compact target footprint, so a Tk/
                # Windows update cannot make the menu check huge again.
                src_w = 24
                src_h = 24
                bmi = _BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = src_w
                bmi.bmiHeader.biHeight = -src_h  # top-down DIB
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0  # BI_RGB

                bits = ctypes.c_void_p()
                hdc = gdi32.CreateCompatibleDC(0)
                if not hdc:
                    return None
                hbmp = None
                old_obj = None
                try:
                    hbmp = gdi32.CreateDIBSection(
                        hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), 0, 0)
                    if not hbmp or not bits.value:
                        return None
                    old_obj = gdi32.SelectObject(hdc, hbmp)

                    # White background lets us recover the Windows-drawn glyph as
                    # a coverage mask regardless of the current system text color.
                    raw_size = src_w * src_h * 4
                    white = (ctypes.c_ubyte * raw_size)(*([255] * raw_size))
                    ctypes.memmove(bits.value, white, raw_size)

                    rect = _RECT(0, 0, src_w, src_h)
                    DFC_MENU = 2
                    DFCS_MENUCHECK = 0x0001
                    if not user32.DrawFrameControl(
                            hdc, ctypes.byref(rect), DFC_MENU, DFCS_MENUCHECK):
                        return None

                    raw = ctypes.string_at(bits.value, raw_size)
                    rgba = Image.frombuffer(
                        'RGBA', (src_w, src_h), raw, 'raw', 'BGRA', 0, 1).copy()
                    rgb = rgba.convert('RGB')

                    # Convert white-background output to an alpha/coverage mask.
                    # DrawFrameControl is usually hard-edged here, but this also
                    # retains any grey antialiasing supplied by Windows.
                    pix = rgb.load()
                    mask = Image.new('L', rgb.size, 0)
                    mp = mask.load()
                    for yy in range(src_h):
                        for xx in range(src_w):
                            rr, gg, bb = pix[xx, yy]
                            mp[xx, yy] = max(0, min(255, 255 - min(rr, gg, bb)))

                    bbox = mask.getbbox()
                    if not bbox:
                        return None
                    mask = mask.crop(bbox)
                    mw, mh = mask.size
                    scale = min(float(_target_w) / max(1, mw),
                                float(_target_h) / max(1, mh))
                    nw = max(1, int(round(mw * scale)))
                    nh = max(1, int(round(mh * scale)))
                    return mask.resize((nw, nh), Image.Resampling.LANCZOS)
                finally:
                    try:
                        if old_obj:
                            gdi32.SelectObject(hdc, old_obj)
                    except Exception:
                        pass
                    try:
                        if hbmp:
                            gdi32.DeleteObject(hbmp)
                    except Exception:
                        pass
                    try:
                        gdi32.DeleteDC(hdc)
                    except Exception:
                        pass
            except Exception:
                return None

        try:
            from PIL import Image, ImageDraw, ImageTk
            _rgba = tuple(int(indicator[i:i+2], 16)
                          for i in (1, 3, 5)) + (255,)
            _empty_img = Image.new('RGBA', (_cell_w, _cell_h), (0, 0, 0, 0))
            _checked_img = _empty_img.copy()

            _mask = _native_windows_check_mask()
            if _mask is None:
                # Non-Windows / Win32 failure fallback.  Keep the v97_3 shape and
                # weight, but normalize it to the same compact target footprint.
                _scale = 4
                _fallback_hi = Image.new(
                    'L', (_target_w * _scale, _target_h * _scale), 0)
                _draw = ImageDraw.Draw(_fallback_hi)
                _points = [
                    (0.7 * _scale, 4.2 * _scale),
                    (3.2 * _scale, 6.8 * _scale),
                    (9.0 * _scale, 0.8 * _scale),
                ]
                _stroke = 7
                _draw.line(_points, fill=255, width=_stroke, joint='curve')
                _radius = _stroke / 2.0
                for _x, _y in (_points[0], _points[-1]):
                    _draw.ellipse((_x - _radius, _y - _radius,
                                   _x + _radius, _y + _radius), fill=255)
                _mask = _fallback_hi.resize(
                    (_target_w, _target_h), Image.Resampling.LANCZOS)

            _glyph = Image.new('RGBA', _mask.size, _rgba)
            _glyph.putalpha(_mask)
            _gx = 1
            _gy = max(0, (_cell_h - _mask.size[1]) // 2)
            _checked_img.alpha_composite(_glyph, (_gx, _gy))

            empty = ImageTk.PhotoImage(_empty_img, master=self)
            checked = ImageTk.PhotoImage(_checked_img, master=self)
        except Exception:
            # Last-resort pure Tk fallback with the same 17x13 layout cell.
            empty = tk.PhotoImage(master=self, width=_cell_w, height=_cell_h)
            checked = tk.PhotoImage(master=self, width=_cell_w, height=_cell_h)
            for _x, _y in ((1, 6), (2, 7), (3, 8),
                           (4, 7), (5, 6), (6, 5),
                           (7, 4), (8, 3), (9, 2)):
                checked.put(indicator, (_x, _y))
                if _y + 1 < _cell_h:
                    checked.put(indicator, (_x, _y + 1))

        # Tk images are reference-counted by Python.  Retain them for as long
        # as the rebuilt menus may display them.
        self._view_menu_indicator_images = (empty, checked)

        for menu in (getattr(self, '_view_menu', None),
                     getattr(self, '_view_grid_submenu', None)):
            if menu is None:
                continue
            try:
                end = menu.index('end')
            except Exception:
                continue
            if end is None:
                continue

            for index in range(int(end) + 1):
                try:
                    entry_type = menu.type(index)
                    if entry_type == 'separator':
                        continue

                    # Hide the platform margin on EVERY ordinary row, then reserve
                    # exactly one fixed cell so all text remains perfectly aligned.
                    if entry_type in ('checkbutton', 'radiobutton'):
                        menu.entryconfigure(
                            index,
                            indicatoron=False,
                            hidemargin=True,
                            image=empty,
                            selectimage=checked,
                            compound='left')
                    else:
                        menu.entryconfigure(
                            index,
                            hidemargin=True,
                            image=empty,
                            compound='left')
                except Exception:
                    pass


    def _show_about_ain_editor(self):
        """Show the integrated About dialog for the community editor."""
        existing = getattr(self, '_about_ain_window', None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass

        light = bool(self.light_panels.get())
        bg = '#f2f2f2' if light else C_PANEL
        panel_bg = '#ffffff' if light else C_PANEL2
        fg = '#151515' if light else C_TEXT
        dim = '#4f4f4f' if light else C_DIM
        border = '#b8b8b8' if light else C_BORDER
        button_bg = '#f7f7f7' if light else C_PANEL2

        win = tk.Toplevel(self)
        self._about_ain_window = win
        win.title('About AIN Editor')
        win.configure(bg=bg)
        win.resizable(False, False)
        win.transient(self)

        def close():
            try:
                win.grab_release()
            except Exception:
                pass
            self._about_ain_window = None
            win.destroy()

        win.protocol('WM_DELETE_WINDOW', close)
        win.bind('<Escape>', lambda _event: close())
        win.bind('<Return>', lambda _event: close())

        outer = tk.Frame(win, bg=bg, padx=18, pady=16)
        outer.pack(fill='both', expand=True)
        card = tk.Frame(outer, bg=panel_bg, highlightthickness=1,
                        highlightbackground=border, padx=24, pady=18)
        card.pack(fill='both', expand=True)

        tk.Label(card, text='Delta Force: Black Hawk Down', bg=panel_bg, fg=fg,
                 font=('Segoe UI', 11)).pack(pady=(0, 2))
        tk.Label(card, text='AIN Editor', bg=panel_bg, fg=fg,
                 font=('Segoe UI', 18, 'bold')).pack(pady=(0, 14))
        tk.Label(card, text='Version: v1.0', bg=panel_bg, fg=fg,
                 font=('Segoe UI', 10)).pack(pady=(0, 14))
        tk.Label(card, text='Community made tool.  Credits: Flvre',
                 bg=panel_bg, fg=fg, font=('Segoe UI', 10)).pack(pady=(0, 16))

        tk.Frame(card, bg=border, height=1).pack(fill='x', pady=(0, 14))
        notice = (
            'For details on how to use AIN Editor, please read '
            'AIN_Editor_Guide.pdf'
        )
        tk.Label(card, text=notice, bg=panel_bg, fg=fg,
                 font=('Segoe UI', 9), justify='left', anchor='w',
                 wraplength=430).pack(fill='x')

        ok = tk.Button(outer, text='OK', command=close, width=10,
                       bg=button_bg, fg=fg, activebackground=C_ACCENT,
                       activeforeground='white', relief='raised', bd=1,
                       font=('Segoe UI', 9))
        ok.pack(pady=(14, 0))

        win.update_idletasks()
        width = max(500, win.winfo_reqwidth())
        height = max(330, win.winfo_reqheight())
        try:
            self.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        except Exception:
            x = max(0, (win.winfo_screenwidth() - width) // 2)
            y = max(0, (win.winfo_screenheight() - height) // 2)
        win.geometry(f'{width}x{height}+{x}+{y}')
        win.grab_set()
        ok.focus_set()
        win.lift()


    def _b12_filter_rule_label(self, rule):
        mode = rule.get('mode')
        value = int(rule.get('value', 0))
        include = bool(rule.get('include', False))
        if mode == 'hide':
            return f"hide b12 = {value}"
        if mode == 'only':
            return f"show only b12 = {value}"
        if mode == 'above':
            return f"show b12 {'>=' if include else '>'} {value}"
        if mode == 'below':
            return f"show b12 {'<=' if include else '<'} {value}"
        return f"{mode} {value}"


    def _b12_filter_rule_matches(self, b12_value, rule):
        try:
            value = int(rule.get('value', 0))
            b12_value = int(b12_value)
        except Exception:
            return False
        mode = rule.get('mode')
        include = bool(rule.get('include', False))
        if mode in ('hide', 'only'):
            return b12_value == value
        if mode == 'above':
            return b12_value >= value if include else b12_value > value
        if mode == 'below':
            return b12_value <= value if include else b12_value < value
        return False


    def _node_passes_b12_filter(self, node):
        rules = getattr(self, 'b12_filter_rules', None) or []
        if not rules:
            return True
        try:
            b12_value = int(getattr(node, 'b12', 0))
        except Exception:
            b12_value = 0
        show_rules = [r for r in rules if r.get('mode') in ('only', 'above', 'below')]
        hide_rules = [r for r in rules if r.get('mode') == 'hide']
        visible = True
        if show_rules:
            visible = any(self._b12_filter_rule_matches(b12_value, r) for r in show_rules)
        if visible and hide_rules:
            visible = not any(self._b12_filter_rule_matches(b12_value, r) for r in hide_rules)
        return visible


    def _get_b12_visible_ids(self):
        rules = getattr(self, 'b12_filter_rules', None) or []
        if not rules:
            return None
        return {n.id for n in self.nodes if self._node_passes_b12_filter(n)}


    def _terrain_node_side(self, node):
        """Classify a node as above/surface terrain or below terrain.

        AIN node Z includes NAV_NODE_Z_LIFT, so compare the reconstructed
        support height rather than the raw node coordinate.  Missing terrain
        data is kept in the above/surface class so maps without a usable MED
        heightmap do not unexpectedly lose their graph.
        """
        info = getattr(self, 'terrain_info', None)
        if not isinstance(info, dict):
            return 'above'
        heightmap = info.get('heightmap')
        spatial = info.get('spatial')
        cache_key = (id(heightmap), id(spatial))
        if cache_key != getattr(self, '_terrain_node_side_cache_key', None):
            self._terrain_node_side_cache = {}
            self._terrain_node_side_cache_key = cache_key

        signature = (float(node.x), float(node.y), float(node.z))
        cached = self._terrain_node_side_cache.get(node.id)
        if cached is not None and cached[:3] == signature:
            return cached[3]

        terrain_z = _terrain_sample_height(info, node.x, node.y)
        if terrain_z is None:
            side = 'above'
        else:
            support_z = float(node.z) - NAV_NODE_Z_LIFT
            side = (
                'below'
                if support_z < float(terrain_z) - TERRAIN_NODE_SIDE_TOLERANCE
                else 'above'
            )
        self._terrain_node_side_cache[node.id] = signature + (side,)
        return side


    def _node_passes_terrain_visibility(self, node):
        show_above = bool(self.show_nodes_above_terrain.get())
        show_below = bool(self.show_nodes_below_terrain.get())
        if show_above and show_below:
            return True
        if not show_above and not show_below:
            return False
        side = self._terrain_node_side(node)
        return show_below if side == 'below' else show_above


    def _get_terrain_visible_ids(self):
        if (self.show_nodes_above_terrain.get()
                and self.show_nodes_below_terrain.get()):
            return None
        return {
            n.id for n in self.nodes
            if self._node_passes_terrain_visibility(n)
        }


    def _get_layer_visible_ids(self):
        """Return the combined non-Z node visibility set, or None for all."""
        b12_visible_ids = self._get_b12_visible_ids()
        terrain_visible_ids = self._get_terrain_visible_ids()
        if b12_visible_ids is None:
            return terrain_visible_ids
        if terrain_visible_ids is None:
            return b12_visible_ids
        return set(b12_visible_ids) & set(terrain_visible_ids)


    def _on_show_water_toggle(self):
        """Refresh both 2D and any visible 3D view after water visibility changes."""
        try:
            self.redraw()
        except Exception:
            pass
        try:
            self.refresh_3d_views(rebuild=False, refit=False, reason='water_visibility')
        except Exception:
            pass


    def _on_terrain_node_visibility_toggle(self):
        # Hit-testing uses the same visible-node set as rendering, so a hidden
        # surface or tunnel graph cannot be selected accidentally while cleaning.
        self._prune_selection_to_editable()
        self._rebuild_hit_tester()
        self.redraw()


    def _apply_b12_filter_change(self):
        try:
            self._prune_selection_to_editable()
            self._rebuild_hit_tester()
        except Exception:
            pass
        try:
            self.redraw()
        except Exception:
            pass


    def _refresh_b12_filter_listbox(self):
        lb = getattr(self, '_b12_filter_listbox', None)
        if lb is None:
            return
        lb.delete(0, 'end')
        for rule in getattr(self, 'b12_filter_rules', []) or []:
            lb.insert('end', self._b12_filter_rule_label(rule))


    def _open_b12_filter_popup(self):
        if self._b12_filter_window is not None and self._b12_filter_window.winfo_exists():
            self._b12_filter_window.deiconify()
            self._b12_filter_window.lift()
            return

        win = tk.Toplevel(self)
        self._b12_filter_window = win
        win.title("b12 value filter")
        win.configure(bg=C_PANEL)
        win.transient(self)
        win.resizable(False, False)

        def _on_close():
            self._b12_filter_window = None
            self._b12_filter_listbox = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

        outer = tk.Frame(win, bg=C_PANEL, padx=12, pady=10)
        outer.pack(fill='both', expand=True)

        tk.Label(outer, text="b12 Filter Rules", bg=C_PANEL, fg=C_ACCENT,
                 font=('Consolas', 10, 'bold'), anchor='w').pack(fill='x')
        tk.Label(outer, text="Show-only / above / below rules form the visible set. Hide rules subtract from it.",
                 bg=C_PANEL, fg=C_DIM, font=('Consolas', 8), anchor='w',
                 wraplength=420, justify='left').pack(fill='x', pady=(2, 8))

        form = tk.Frame(outer, bg=C_PANEL)
        form.pack(fill='x')
        tk.Label(form, text="Value:", bg=C_PANEL, fg=C_TEXT,
                 font=('Consolas', 9)).grid(row=0, column=0, sticky='w')
        value_var = tk.StringVar(value="0")
        value_entry = tk.Entry(form, textvariable=value_var, width=8,
                               bg=C_PANEL2, fg=C_TEXT, insertbackground=C_TEXT,
                               font=('Consolas', 9), relief='flat')
        value_entry.grid(row=0, column=1, sticky='w', padx=(6, 12))

        mode_var = tk.StringVar(value='only')
        include_var = tk.BooleanVar(value=False)

        modes = [
            ('Hide value', 'hide'),
            ('Show only', 'only'),
            ('Below', 'below'),
            ('Above', 'above'),
        ]
        mode_frame = tk.Frame(outer, bg=C_PANEL)
        mode_frame.pack(fill='x', pady=(8, 2))
        for label, value in modes:
            tk.Radiobutton(mode_frame, text=label, variable=mode_var, value=value,
                           bg=C_PANEL, fg=C_TEXT, selectcolor=C_PANEL2,
                           activebackground=C_PANEL, activeforeground=C_TEXT,
                           font=('Consolas', 9), command=lambda: _sync_include_state()).pack(side='left', padx=(0, 10))

        include_cb = tk.Checkbutton(outer, text="include =",
                                    variable=include_var, bg=C_PANEL, fg=C_TEXT,
                                    activebackground=C_PANEL, activeforeground=C_TEXT,
                                    selectcolor=C_PANEL2, font=('Consolas', 9))
        include_cb.pack(anchor='w', pady=(2, 8))

        def _sync_include_state():
            if mode_var.get() in ('above', 'below'):
                include_cb.configure(state='normal')
            else:
                include_var.set(False)
                include_cb.configure(state='disabled')

        def _add_rule():
            try:
                value = int(value_var.get().strip())
            except Exception:
                messagebox.showerror("b12 filter", "Enter a valid b12 byte value from 0 to 255.", parent=win)
                return
            if not 0 <= value <= 255:
                messagebox.showerror("b12 filter", "b12 must be from 0 to 255.", parent=win)
                return
            self.b12_filter_rules.append({
                'value': value,
                'mode': mode_var.get(),
                'include': bool(include_var.get()) if mode_var.get() in ('above', 'below') else False,
            })
            self._refresh_b12_filter_listbox()
            self._apply_b12_filter_change()

        def _remove_selected():
            sel = list(self._b12_filter_listbox.curselection())
            if not sel:
                return
            for idx in reversed(sel):
                if 0 <= idx < len(self.b12_filter_rules):
                    del self.b12_filter_rules[idx]
            self._refresh_b12_filter_listbox()
            self._apply_b12_filter_change()

        def _clear_rules():
            self.b12_filter_rules.clear()
            self._refresh_b12_filter_listbox()
            self._apply_b12_filter_change()

        btns = tk.Frame(outer, bg=C_PANEL)
        btns.pack(fill='x', pady=(2, 8))
        tk.Button(btns, text="Add rule", command=_add_rule,
                  bg=C_ACCENT, fg='white', font=('Consolas', 9),
                  relief='flat', padx=8).pack(side='left')
        tk.Button(btns, text="Remove selected", command=_remove_selected,
                  bg='#555555', fg=C_TEXT, font=('Consolas', 9),
                  relief='flat', padx=8).pack(side='left', padx=(6, 0))
        tk.Button(btns, text="Clear", command=_clear_rules,
                  bg='#552222', fg=C_TEXT, font=('Consolas', 9),
                  relief='flat', padx=8).pack(side='left', padx=(6, 0))

        self._b12_filter_listbox = tk.Listbox(outer, height=7, width=54,
                                              bg=C_PANEL2, fg=C_TEXT,
                                              selectbackground=C_ACCENT,
                                              font=('Consolas', 9),
                                              relief='flat')
        self._b12_filter_listbox.pack(fill='x')
        self._refresh_b12_filter_listbox()
        _sync_include_state()
        win.update_idletasks()
        try:
            width = win.winfo_width()
            height = win.winfo_height()
            parent_x = self.winfo_rootx()
            parent_y = self.winfo_rooty()
            parent_w = self.winfo_width()
            parent_h = self.winfo_height()
            x = parent_x + max(0, (parent_w - width) // 2)
            y = parent_y + max(0, (parent_h - height) // 2)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        value_entry.focus_set()

    # === breach_diag.py (BreachDiagMixin) ===

    """Methods for breach system, diagnostics, and zone layer management in AINEditor."""

    # ── 12. BREACH SYSTEM ────────────────────────────────────────────────────

    def _sync_breach_panel_mode(self):
        return _ui_breach_panel.sync_breach_panel_mode(self)


    def _open_breach_panel(self):
        return _ui_breach_panel.open_breach_panel(self)


    # ── 13. DEBUG / DIAGNOSTICS ──────────────────────────────────────────────

    def _toggle_debug_bar(self):
        """Show or hide the debug overlay bar."""
        self._debug_bar_visible = not self._debug_bar_visible
        if self._debug_bar_frame:
            if self._debug_bar_visible:
                # Pack after the top bar (before main content)
                self._debug_bar_frame.pack(fill='x', side='top', after=None)
                # Re-pack: insert between top bar and main frame
                self._debug_bar_frame.pack(fill='x', side='top')
            else:
                self._debug_bar_frame.pack_forget()
        self.redraw()


    def _debug_overlay_color(self, node):
        """Return an override color for a node based on active debug overlays.
        Returns None if no debug overlay applies.
        Uses a heat-map: 0=black, 255=white, with the filter's accent color as midpoint.
        Only the first active overlay wins to avoid confusion.
        """
        raw = getattr(node, 'raw_bytes', None)
        if not raw or len(raw) < 0x34:
            return None

        def heat(val, max_val, r, g, b):
            """Map val 0..max_val to black -> (r,g,b) -> white."""
            if val == 0:
                return None  # zero = don't override
            t = min(val / max_val, 1.0)
            if t < 0.5:
                t2 = t * 2
                return f'#{int(r*t2):02x}{int(g*t2):02x}{int(b*t2):02x}'
            else:
                t2 = (t - 0.5) * 2
                return f'#{int(r + (255-r)*t2):02x}{int(g + (255-g)*t2):02x}{int(b + (255-b)*t2):02x}'

        import math
        import struct

        # b12 high bits (bits 4-7) — unknown
        if self._dbg_b12_hidden.get():
            hi = (raw[0x0c] >> 4) & 0x0f
            return heat(hi, 15, 0xff, 0xaa, 0x00)

        # b13 full value
        if self._dbg_b13_full.get():
            v = raw[0x0d]
            if v == 0:
                return None
            # Color by specific known/unknown values
            palette = {1:'#aaaaaa', 2:'#00ff88', 6:'#ff66ff',
                       7:'#ff00ff', 8:'#ffaa00', 9:'#ff4400'}
            return palette.get(v, heat(v, 15, 0xff, 0x66, 0xff))

        # +0x28..+0x31 EdgeFlags — any nonzero slot
        if self._dbg_edge_flags.get():
            nc = raw[0x13]
            ef_bytes = raw[0x28:0x32]
            active_nz   = any(ef_bytes[i] != 0 for i in range(min(nc, 10)))
            inactive_nz = any(ef_bytes[i] != 0 for i in range(min(nc, 10), 10))
            if active_nz and inactive_nz:
                return '#ffffff'  # both — white
            if active_nz:
                return '#00ffcc'  # real EdgeFlags — cyan
            if inactive_nz:
                return '#ff4444'  # wrong slot — red
            return None

        # +0x28 inactive slots only (generator residue alarm)
        if self._dbg_nc_mismatch.get():
            nc = raw[0x13]
            ef_bytes = raw[0x28:0x32]
            inactive_nz = any(ef_bytes[i] != 0 for i in range(min(nc, 10), 10))
            return '#ff4444' if inactive_nz else None

        # Preserved construction signature. The +0x32 high byte is stable
        # across the known NAI1/NAI2 SPBHD_13 files while +0x32 low is
        # repeatedly recomputed. Unused EdgeFlags slots often carry the same
        # fill byte as +0x33, exposing separately constructed graph strata.
        if self._dbg_construction_strata.get():
            nc = min(raw[0x13], 10)
            hi = raw[0x33]
            inactive = tuple(raw[0x28 + nc:0x32])

            if not inactive:
                return '#b8b8b8'               # all ten edge slots are active

            fills = set(inactive)
            if len(fills) != 1:
                return '#d66cff'               # mixed/stale inactive-slot fill

            fill = inactive[0]
            if fill != hi:
                return '#ffe066'               # uniform, but not coupled to +0x33

            if hi == 0:
                return '#00d878'               # canonical 00/00 stratum
            if hi == 1:
                return '#ff4f5e'               # canonical 01/01 stratum
            if hi == 0xff:
                return '#ff9f1c'               # canonical FF/FF stratum

            # Stable categorical colors for rarer exact fill/high-byte pairs.
            palette = ('#00c8ff', '#b56cff', '#fff05a',
                       '#3478f6', '#00d6c9', '#ff66c4')
            return palette[hi % len(palette)]

        # +0x32 low byte — flood-fill gradient with special seed node markers
        # Value=1 = exact seed (gold ring, blue/white duo)
        # Value=2-5 = near-seed (grey ring, blue/cyan duo)
        # Value>5 = normal heat gradient
        if self._dbg_0x32_low.get():
            v = raw[0x32]
            if v == 0:
                return None
            elif v == 1:
                return {'type': 'seed_exact'}    # gold ring, blue+white duo
            elif v <= 5:
                return {'type': 'seed_near'}     # grey ring, blue+cyan duo
            else:
                return heat(v, 255, 0x44, 0xaa, 0xff)

        # +0x33 high byte — CATEGORICAL not linear
        # Values observed: 0 (most), 1 (large clusters), 255 (boundaries),
        # 63/95/127/191 (rare), anything else (very rare)
        if self._dbg_0x32_high.get():
            v = raw[0x33]
            if v == 0:
                return None                    # zero — invisible
            elif v == 1:
                return '#00ff88'               # 1 — bright green (large clusters)
            elif v == 255:
                return '#ff4400'               # 255 — orange-red (boundary/cap)
            elif v in (63, 127, 191):
                return '#ffff00'               # power-of-2-minus-1 — yellow
            elif v in (95, 159, 223):
                return '#ff88ff'               # other common — magenta
            else:
                return heat(v, 254, 0x88, 0xdd, 0xff)  # rare values — blue

        # +0x32 full uint16 — NORMALISED to actual min/max of loaded file
        # Prevents all-black when values cluster in a narrow range like 256-464
        if self._dbg_0x32_uint16.get():
            v = struct.unpack_from('<H', raw, 0x32)[0]
            if v == 0:
                return None
            lo = self._dbg_u16_min
            hi = max(self._dbg_u16_max, lo + 1)
            # Normalise v to 0-255 within the actual data range
            norm = int(255 * (v - lo) / (hi - lo))
            norm = max(1, min(255, norm))
            return heat(norm, 255, 0xaa, 0xee, 0xff)

        return None


    def _compute_debug_ranges(self):
        """Scan loaded nodes and compute normalised ranges for debug overlays.
        Called after every AIN load so overlays always show meaningful colours.
        """
        import struct as _struct
        u16_vals = []
        hi_cats  = {}

        for node in self.nodes:
            raw = getattr(node, 'raw_bytes', None)
            if not raw or len(raw) < 0x34:
                continue
            u16 = _struct.unpack_from('<H', raw, 0x32)[0]
            hi  = raw[0x33]
            if u16 != 0:
                u16_vals.append(u16)
            hi_cats[hi] = hi_cats.get(hi, 0) + 1

        if u16_vals:
            self._dbg_u16_min = min(u16_vals)
            self._dbg_u16_max = max(u16_vals)
        else:
            self._dbg_u16_min = 0
            self._dbg_u16_max = 1

        self._dbg_hi_categories = hi_cats

        # Log useful summary
        nonzero_u16 = len(u16_vals)
        nonzero_hi  = sum(c for v, c in hi_cats.items() if v != 0)
        if nonzero_u16 or nonzero_hi:
            self.status(
                f"+0x32 u16: {nonzero_u16} nonzero  "
                f"range=[{self._dbg_u16_min}..{self._dbg_u16_max}]  "
                f"|  +0x33 hi: {nonzero_hi} nonzero  "
                f"unique={len([v for v in hi_cats if v!=0])}"
            )


    def _any_debug_overlay_active(self):
        return any(v.get() for v in [
            self._dbg_b12_hidden, self._dbg_b13_full, self._dbg_edge_flags,
            self._dbg_nc_mismatch, self._dbg_construction_strata,
            self._dbg_0x32_low, self._dbg_0x32_high, self._dbg_0x32_uint16])

    # ─── ZONE LAYER MANAGER ─────────────────────────────────────────────────


    def _show_grid_radius_diagnostic(self):
        try:
            report = self._build_grid_radius_diagnostic_text()
        except Exception as e:
            messagebox.showerror("Grid / Radius / Overlap Diagnostic", str(e), parent=self)
            return

        win = tk.Toplevel(self)
        win.title("Grid / Radius / Overlap Diagnostic")
        win.configure(bg=C_PANEL)
        win.geometry("980x760")

        top = tk.Frame(win, bg=C_PANEL)
        top.pack(fill='x', padx=8, pady=6)
        tk.Label(top, text="Grid / Radius / Overlap Diagnostic", bg=C_PANEL, fg=C_TEXT,
                 font=('Consolas', 11, 'bold')).pack(side='left')

        txt = tk.Text(win, bg="#101010", fg="#dddddd", insertbackground="#ffffff",
                      font=('Consolas', 9), wrap='none')
        txt.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        txt.insert('1.0', report)
        txt.configure(state='disabled')

        btns = tk.Frame(win, bg=C_PANEL)
        btns.pack(fill='x', padx=8, pady=(0, 8))

        def copy_report():
            self.clipboard_clear()
            self.clipboard_append(report)
            self.status("Grid / Radius / Overlap Diagnostic copied to clipboard")

        def save_report():
            try:
                path = filedialog.asksaveasfilename(
                    parent=win,
                    title="Save Grid / Radius / Overlap Diagnostic",
                    defaultextension=".txt",
                    filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
                )
                if not path:
                    return
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(report)
                self.status(f"Saved Grid / Radius / Overlap Diagnostic: {path}")
            except Exception as e:
                messagebox.showerror("Save Diagnostic", str(e), parent=win)

        tk.Button(btns, text="Copy", command=copy_report,
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_ACCENT,
                  activeforeground='white', relief='flat',
                  font=('Consolas', 9), padx=10, pady=3).pack(side='left', padx=3)
        tk.Button(btns, text="Save...", command=save_report,
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_ACCENT,
                  activeforeground='white', relief='flat',
                  font=('Consolas', 9), padx=10, pady=3).pack(side='left', padx=3)
        tk.Button(btns, text="Close", command=win.destroy,
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_ACCENT,
                  activeforeground='white', relief='flat',
                  font=('Consolas', 9), padx=10, pady=3).pack(side='right', padx=3)
        self.status("Grid / Radius / Overlap Diagnostic generated")

    # === status_stats.py (StatusStatsMixin) ===

    """Methods for status bar and statistics display in AINEditor."""

    # ─── STATUS / STATS ─────────────────────────────────────────────────────


    def _build_grid_radius_diagnostic_text(self):
        """Build an exact fixed-point grid/radius/residue diagnostic report.

        This report expands the older bucketed Grid/Radius/Offset view with raw
        16.16 fixed-point residue checks.  This is meant to answer whether
        off-grid nodes are exact 6.25cm phase nodes, smaller hidden sub-grid
        nodes, deterministic byte-scale jitter, or optimizer/merge residue.
        """
        import struct
        from collections import Counter, defaultdict

        scope_label, nodes = self._diagnostic_node_scope()
        total = len(nodes)
        lines = []
        lines.append("DFBHD AIN Exact Fixed-Point Residue / Grid / Radius Overlap Diagnostic")
        lines.append("=" * 78)
        lines.append(f"Scope: {scope_label}")
        lines.append(f"Total nodes tested: {total}")
        lines.append("")
        lines.append("Important unit note:")
        lines.append("  AIN coordinates are 16.16 fixed-point meters.")
        lines.append("  Node.acceptance_radius() = b15 * 0x1000 / 65536 = b15 / 16 meters.")
        lines.append("  1 b15 unit = 0.0625 m = 6.25 cm.")
        lines.append("  0x4000 = 25cm, 0x2000 = 12.5cm, 0x1000 = 6.25cm.")
        lines.append("")

        if not nodes:
            return "\n".join(lines + ["No nodes loaded."])

        def pct(n, d=total):
            return self._fmt_pct(n, d)

        def cm_from_raw(raw_units):
            return (float(raw_units) / 65536.0) * 100.0

        def fmt_raw(raw_units):
            sign = "-" if int(raw_units) < 0 else ""
            return f"{sign}0x{abs(int(raw_units)):04X} {cm_from_raw(raw_units):8.4f}cm"

        def fmt_res(res):
            return f"0x{int(res) & 0xFFFF:04X} {cm_from_raw(res):8.4f}cm"

        def raw_xyz(n):
            raw = getattr(n, 'raw_bytes', None)
            if raw and len(raw) >= 12:
                try:
                    return struct.unpack_from('<iii', raw, 0)
                except Exception:
                    pass
            return (int(round(float(n.x) * 65536.0)),
                    int(round(float(n.y) * 65536.0)),
                    int(round(float(n.z) * 65536.0)))

        def res25(v):
            return int(v) % 0x4000

        def signed25_from_res(r):
            r = int(r) % 0x4000
            if r <= 0x2000:
                return r
            return r - 0x4000

        clean_residues = {0x0000, 0x1000, 0x2000, 0x3000}
        clean_pairs = {(a, b) for a in clean_residues for b in clean_residues}

        records = []
        for local_i, n in enumerate(nodes):
            rx, ry, rz = raw_xyz(n)
            xr = res25(rx)
            yr = res25(ry)
            sx = signed25_from_res(xr)
            sy = signed25_from_res(yr)
            b12 = int(getattr(n, 'b12', 0) or 0)
            b13 = int(getattr(n, 'b13', 0) or 0)
            b14 = int(getattr(n, 'b14', 0) or 0)
            b15 = int(getattr(n, 'b15', 0) or 0)
            b16 = int(getattr(n, 'b16', 0) or 0)
            b17 = int(getattr(n, 'b17', 0) or 0)
            b18 = int(getattr(n, 'b18', 0) or 0)
            nid = int(getattr(n, 'id', local_i) or local_i)
            records.append({
                'node': n, 'id': nid, 'rx': rx, 'ry': ry, 'rz': rz,
                'xr': xr, 'yr': yr, 'sx': sx, 'sy': sy,
                'b12': b12, 'b13': b13, 'b14': b14, 'b15': b15,
                'b16': b16, 'b17': b17, 'b18': b18,
                'clean': (xr, yr) in clean_pairs,
            })

        grid_steps = [
            ("2 m", 2.0), ("1 m", 1.0), ("50 cm", 0.5),
            ("25 cm", 0.25), ("12.5 cm", 0.125), ("6.25 cm", 0.0625),
        ]
        tolerances = [("<=1mm", 0.001), ("<=1cm", 0.01), ("<=2.5cm", 0.025), ("<=5cm", 0.05)]
        lines.append("CENTER GRID FIT  (meter-space sanity check)")
        lines.append("-" * 78)
        lines.append("Uses max(|x error|, |y error|) to nearest grid intersection, origin = world 0.")
        hdr = f"{'Grid':>8}  " + "  ".join(f"{name:>10}" for name, _ in tolerances) + f"  {'avg err':>9}  {'max err':>9}"
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for label, step in grid_steps:
            errs = [self._grid_error_2d(r['node'].x, r['node'].y, step)[0] for r in records]
            avg = sum(errs) / len(errs) if errs else 0.0
            mx = max(errs) if errs else 0.0
            counts = [sum(1 for e in errs if e <= tol) for _, tol in tolerances]
            lines.append(
                f"{label:>8}  "
                + "  ".join(f"{c:4d}/{total:<4d} {pct(c):>6}" for c in counts)
                + f"  {avg:8.4f}m  {mx:8.4f}m"
            )
        lines.append("")

        align_steps = [
            ("25.000cm", 0x4000), ("12.500cm", 0x2000), ("6.250cm", 0x1000),
            ("3.125cm", 0x0800), ("1.5625cm", 0x0400), ("0.78125cm", 0x0200),
            ("0.390625cm", 0x0100), ("0.1953125cm", 0x0080),
        ]
        lines.append("EXACT FIXED-POINT ALIGNMENT")
        lines.append("-" * 78)
        lines.append("Tests raw 16.16 integer divisibility. This avoids float/bucket ambiguity.")
        lines.append(f"{'Step':>13}  {'Raw':>7}  {'X aligned':>18}  {'Y aligned':>18}  {'Both axes':>18}")
        for label, step in align_steps:
            cx = sum(1 for r in records if r['rx'] % step == 0)
            cy = sum(1 for r in records if r['ry'] % step == 0)
            cb = sum(1 for r in records if r['rx'] % step == 0 and r['ry'] % step == 0)
            lines.append(f"{label:>13}  0x{step:04X}  {cx:5d}/{total:<5d} {pct(cx):>6}  {cy:5d}/{total:<5d} {pct(cy):>6}  {cb:5d}/{total:<5d} {pct(cb):>6}")
        lines.append("")

        cxr = Counter(r['xr'] for r in records)
        cyr = Counter(r['yr'] for r in records)
        cpair = Counter((r['xr'], r['yr']) for r in records)
        csigned_pair = Counter((r['sx'], r['sy']) for r in records)
        clean_count = sum(1 for r in records if r['clean'])
        weird_count = total - clean_count
        lines.append("RAW 25CM CELL RESIDUES")
        lines.append("-" * 78)
        lines.append("Residue = raw_coord modulo 0x4000. Clean 6.25cm phases are 0x0000/1000/2000/3000.")
        lines.append(f"Clean 6.25cm phase pairs: {clean_count:5d}/{total:<5d} {pct(clean_count)}")
        lines.append(f"Non-clean / weird pairs:   {weird_count:5d}/{total:<5d} {pct(weird_count)}")
        lines.append("")
        lines.append("Top X residues inside 25cm cell:")
        for res, c in cxr.most_common(16):
            mark = "*" if res in clean_residues else " "
            lines.append(f" {mark} {fmt_res(res)}  count={c:5d}/{total:<5d} {pct(c)}")
        lines.append("Top Y residues inside 25cm cell:")
        for res, c in cyr.most_common(16):
            mark = "*" if res in clean_residues else " "
            lines.append(f" {mark} {fmt_res(res)}  count={c:5d}/{total:<5d} {pct(c)}")
        lines.append("")
        lines.append("Top X/Y residue pairs inside 25cm cell:")
        for (xr, yr), c in cpair.most_common(24):
            mark = "*" if (xr, yr) in clean_pairs else " "
            lines.append(f" {mark} x={fmt_res(xr)}  y={fmt_res(yr)}  count={c:5d}/{total:<5d} {pct(c)}")
        lines.append("")

        lines.append("SIGNED OFFSET FROM NEAREST 25CM GRID  (exact raw version)")
        lines.append("-" * 78)
        lines.append("Residue 0x3000 is shown as signed -0x1000 = -6.25cm.")
        csx = Counter(r['sx'] for r in records)
        csy = Counter(r['sy'] for r in records)
        lines.append("Top signed X offsets:")
        for off, c in csx.most_common(16):
            lines.append(f"  x={fmt_raw(off)}  count={c:5d}/{total:<5d} {pct(c)}")
        lines.append("Top signed Y offsets:")
        for off, c in csy.most_common(16):
            lines.append(f"  y={fmt_raw(off)}  count={c:5d}/{total:<5d} {pct(c)}")
        lines.append("Top signed X/Y offset pairs:")
        for (sx, sy), c in csigned_pair.most_common(24):
            lines.append(f"  x={fmt_raw(sx)}  y={fmt_raw(sy)}  count={c:5d}/{total:<5d} {pct(c)}")
        lines.append("")

        weird_pairs = Counter((r['xr'], r['yr']) for r in records if not r['clean'])
        lines.append("WEIRD / NON-CLEAN RESIDUES")
        lines.append("-" * 78)
        if weird_pairs:
            lines.append("Top non-clean residue pairs:")
            for (xr, yr), c in weird_pairs.most_common(30):
                lines.append(f"  x={fmt_res(xr)}  y={fmt_res(yr)}  count={c:5d}/{weird_count:<5d} {self._fmt_pct(c,weird_count)}")
        else:
            lines.append("No non-clean residue pairs in this scope.")
        lines.append("")

        def dist_to_clean_phase(res):
            res = int(res) % 0x4000
            best = None
            for ph in clean_residues:
                d = abs(res - ph)
                d = min(d, 0x4000 - d)
                if best is None or d < best:
                    best = d
            return int(best or 0)

        delta_buckets = Counter()
        for r in records:
            dx = dist_to_clean_phase(r['xr'])
            dy = dist_to_clean_phase(r['yr'])
            md = max(dx, dy)
            if md == 0:
                label = "exact"
            elif md <= int(round(0.0005 * 65536.0)):
                label = "<=0.5mm"
            elif md <= int(round(0.001 * 65536.0)):
                label = "<=1mm"
            elif md <= int(round(0.005 * 65536.0)):
                label = "<=5mm"
            elif md <= int(round(0.010 * 65536.0)):
                label = "<=1cm"
            else:
                label = ">1cm"
            delta_buckets[label] += 1
        lines.append("DISTANCE FROM NEAREST CLEAN 6.25CM PHASE")
        lines.append("-" * 78)
        for label in ["exact", "<=0.5mm", "<=1mm", "<=5mm", "<=1cm", ">1cm"]:
            c = delta_buckets.get(label, 0)
            lines.append(f"  {label:>8}: {c:5d}/{total:<5d} {pct(c)}")
        lines.append("")

        def phase_class(xr, yr):
            if xr == 0 and yr == 0:
                return "PHASE_25_MAIN"
            if xr == 0x2000 and yr == 0x2000:
                return "PHASE_HALF_DIAGONAL"
            if (xr, yr) in ((0x2000, 0), (0, 0x2000)):
                return "PHASE_AXIS_HALF"
            if xr in clean_residues and yr in clean_residues:
                if xr in (0x1000, 0x3000) or yr in (0x1000, 0x3000):
                    return "PHASE_QUARTER"
                return "PHASE_OTHER_CLEAN"
            return "PHASE_WEIRD"

        for r in records:
            r['phase_class'] = phase_class(r['xr'], r['yr'])
        cclass = Counter(r['phase_class'] for r in records)
        lines.append("PHASE CLASSIFICATION")
        lines.append("-" * 78)
        for label in ["PHASE_25_MAIN", "PHASE_HALF_DIAGONAL", "PHASE_AXIS_HALF", "PHASE_QUARTER", "PHASE_OTHER_CLEAN", "PHASE_WEIRD"]:
            c = cclass.get(label, 0)
            lines.append(f"  {label:>22}: {c:5d}/{total:<5d} {pct(c)}")
        lines.append("")

        vals = [r['b15'] for r in records]
        vc = Counter(vals)
        lines.append("RADIUS / b15 FIT")
        lines.append("-" * 78)
        lines.append("b15 is integer radius units. Rendered radius = b15 / 16 meters.")
        for mod, label in [(2, "12.5cm radius grid"), (4, "25cm radius grid"), (8, "50cm radius grid"), (16, "1m radius grid")]:
            c = sum(1 for v in vals if v % mod == 0)
            lines.append(f"b15 % {mod:<2d} == 0  ({label:>18}): {c:5d}/{total:<5d} {pct(c)}")
        lines.append("")
        lines.append("Most common b15 values in scope:")
        for v, c in vc.most_common(24):
            lines.append(f"  b15={v:3d}  radius={v/16.0:7.3f}m  count={c:5d}  {pct(c)}")
        lines.append("")

        lines.append("BY b15 RADIUS VALUE")
        lines.append("-" * 78)
        b15_groups = defaultdict(list)
        for r in records:
            b15_groups[r['b15']].append(r)
        for b15, rs in sorted(b15_groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:24]:
            cnt = len(rs)
            clean = sum(1 for q in rs if q['clean'])
            pc = Counter(q['phase_class'] for q in rs).most_common(3)
            pair = Counter((q['xr'], q['yr']) for q in rs).most_common(3)
            pc_txt = ", ".join(f"{name}:{c}" for name, c in pc)
            pair_txt = ", ".join(f"({a:04X},{b:04X}):{c}" for (a, b), c in pair)
            lines.append(f"  b15={b15:3d} radius={b15/16.0:6.3f}m count={cnt:5d} {self._fmt_pct(cnt,total)} clean={clean:5d}/{cnt:<5d} {self._fmt_pct(clean,cnt)}")
            lines.append(f"      classes: {pc_txt}")
            lines.append(f"      pairs:   {pair_txt}")
        lines.append("")

        def group_report(field, label, max_groups=24, min_count=3):
            groups = defaultdict(list)
            for r in records:
                groups[r[field]].append(r)
            lines.append(label)
            lines.append("-" * 78)
            rows = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            any_row = False
            for val, rs in rows[:max_groups]:
                if len(rs) < min_count:
                    continue
                any_row = True
                cnt = len(rs)
                clean = sum(1 for q in rs if q['clean'])
                avg_b15 = sum(q['b15'] for q in rs) / cnt
                pc = Counter(q['phase_class'] for q in rs).most_common(1)[0]
                pair = Counter((q['xr'], q['yr']) for q in rs).most_common(1)[0]
                lines.append(
                    f"  {field}={val:3d}  count={cnt:5d} {self._fmt_pct(cnt,total)}  "
                    f"clean={clean:5d}/{cnt:<5d} {self._fmt_pct(clean,cnt)}  "
                    f"avg_b15={avg_b15:6.2f}  top_class={pc[0]}({pc[1]})  "
                    f"top_pair=({pair[0][0]:04X},{pair[0][1]:04X})"
                )
            if not any_row:
                lines.append("  no groups with enough samples")
            if len(rows) > max_groups:
                lines.append(f"  ... {len(rows)-max_groups} more groups")
            lines.append("")

        group_report('b12', "BY b12")
        group_report('b13', "BY b13")
        group_report('b14', "BY b14 / area id")
        group_report('b16', "BY b16")
        group_report('b17', "BY b17")
        group_report('b18', "BY b18")

        lines.append("NEAREST-NEIGHBOR SPACING / RESIDUE TYPE")
        lines.append("-" * 78)
        nns = []
        sample = records
        if len(sample) > 3000:
            sample = sample[:3000]
            lines.append("NOTE: nearest-neighbor sample capped to first 3000 scoped nodes for UI safety.")
        if len(sample) >= 2:
            for i, r in enumerate(sample):
                best = None
                best_id = None
                best_dx = best_dy = 0.0
                n = r['node']
                for j, q in enumerate(sample):
                    if i == j:
                        continue
                    m = q['node']
                    dx = float(m.x) - float(n.x)
                    dy = float(m.y) - float(n.y)
                    d2 = dx*dx + dy*dy
                    if best is None or d2 < best:
                        best = d2
                        best_id = q['id']
                        best_dx = dx
                        best_dy = dy
                if best is not None:
                    d = best ** 0.5
                    r['nn'] = d
                    r['nn_id'] = best_id
                    nns.append((d, abs(best_dx), abs(best_dy), r['phase_class']))
        if nns:
            dvals = [d for d, dx, dy, cls in nns]
            lines.append(f"NN distance avg={sum(dvals)/len(dvals):.3f}m  min={min(dvals):.3f}m  max={max(dvals):.3f}m")
            for cls in ["PHASE_25_MAIN", "PHASE_HALF_DIAGONAL", "PHASE_AXIS_HALF", "PHASE_QUARTER", "PHASE_OTHER_CLEAN", "PHASE_WEIRD"]:
                dv = [d for d, dx, dy, c in nns if c == cls]
                if dv:
                    lines.append(f"  {cls:>22}: count={len(dv):5d} avgNN={sum(dv)/len(dv):7.3f}m min={min(dv):7.3f}m max={max(dv):7.3f}m")
            bucket = Counter((round(dx / 0.25) * 0.25, round(dy / 0.25) * 0.25) for d, dx, dy, cls in nns)
            lines.append("")
            lines.append("Most common nearest-neighbor |dx|/|dy| buckets rounded to 25cm:")
            for (dx, dy), c in bucket.most_common(16):
                lines.append(f"  dx={dx:6.2f}m  dy={dy:6.2f}m  count={c:5d}")
        else:
            lines.append("Not enough nodes for nearest-neighbor report.")
        lines.append("")

        lines.append("FINAL RADIUS OVERLAP / CONTAINMENT")
        lines.append("-" * 78)
        lines.append("Uses final rendered radii (b15 / 16m) and 2D circle intersection.")
        lines.append("Primary totals use |dZ| <= 0.35m; cross-layer overlaps are reported separately.")
        lines.append("Ordinary edge overlap is expected. High smaller-disc coverage and containment are the alarms.")

        overlap_records = []
        zero_radius_count = 0
        for rec_i, r in enumerate(records):
            radius_m = max(0.0, float(r['b15']) / 16.0)
            if radius_m <= 0.0:
                zero_radius_count += 1
                continue
            overlap_records.append({
                'scope_i': rec_i,
                'record': r,
                'x': float(r['node'].x),
                'y': float(r['node'].y),
                'z': float(r['node'].z),
                'radius': radius_m,
            })

        def circle_overlap_fraction_of_smaller(radius_a, radius_b, center_distance):
            """Return intersected area as a fraction of the smaller disc."""
            small = min(float(radius_a), float(radius_b))
            large = max(float(radius_a), float(radius_b))
            d = max(0.0, float(center_distance))
            if small <= 0.0 or d >= small + large:
                return 0.0
            if d <= large - small:
                return 1.0
            # Standard two-circle lens area. Clamp all acos inputs because the
            # AIN coordinates and b15 values are exact but converted to float.
            a_arg = (d*d + small*small - large*large) / (2.0*d*small)
            b_arg = (d*d + large*large - small*small) / (2.0*d*large)
            a_arg = max(-1.0, min(1.0, a_arg))
            b_arg = max(-1.0, min(1.0, b_arg))
            radicand = (
                (-d + small + large) * (d + small - large)
                * (d - small + large) * (d + small + large)
            )
            lens = (
                small*small * math.acos(a_arg)
                + large*large * math.acos(b_arg)
                - 0.5 * math.sqrt(max(0.0, radicand))
            )
            return max(0.0, min(1.0, lens / (math.pi * small * small)))

        overlap_pairs = []
        if len(overlap_records) >= 2:
            ordered_overlap = sorted(overlap_records, key=lambda q: (q['x'], q['y'], q['z']))
            max_radius = max(q['radius'] for q in ordered_overlap)
            for i, a in enumerate(ordered_overlap):
                for b in ordered_overlap[i + 1:]:
                    dx = b['x'] - a['x']
                    if dx > a['radius'] + max_radius:
                        break
                    radius_sum = a['radius'] + b['radius']
                    dy = b['y'] - a['y']
                    if abs(dy) >= radius_sum:
                        continue
                    distance = math.hypot(dx, dy)
                    if distance >= radius_sum:
                        continue
                    dz = abs(b['z'] - a['z'])
                    smaller_fraction = circle_overlap_fraction_of_smaller(
                        a['radius'], b['radius'], distance
                    )
                    smaller = a if a['radius'] <= b['radius'] else b
                    larger = b if smaller is a else a
                    fully_contained = (
                        distance + smaller['radius'] <= larger['radius'] + 1.0e-9
                    )
                    center_inside_larger = distance <= larger['radius'] + 1.0e-9
                    a_neighbors = set(int(v) for v in getattr(a['record']['node'], 'neighbors', []) or [])
                    b_neighbors = set(int(v) for v in getattr(b['record']['node'], 'neighbors', []) or [])
                    linked = (
                        int(b['record']['id']) in a_neighbors
                        or int(a['record']['id']) in b_neighbors
                    )
                    overlap_pairs.append({
                        'a': a, 'b': b, 'distance': distance, 'dz': dz,
                        'fraction': smaller_fraction,
                        'depth': max(0.0, radius_sum - distance),
                        'fully_contained': fully_contained,
                        'center_inside_larger': center_inside_larger,
                        'linked': linked,
                    })

        same_z_pairs = [p for p in overlap_pairs if p['dz'] <= 0.10]
        same_band_pairs = [p for p in overlap_pairs if p['dz'] <= 0.35]
        sloped_band_pairs = [p for p in overlap_pairs if 0.10 < p['dz'] <= 0.35]
        cross_layer_pairs = [p for p in overlap_pairs if p['dz'] > 0.35]

        lines.append(f"Positive-radius nodes: {len(overlap_records):5d}/{total:<5d}  zero-radius excluded: {zero_radius_count}")
        lines.append(f"All 2D-overlapping pairs: {len(overlap_pairs):7d}")
        lines.append(f"  strict same-Z pairs (|dZ| <= 0.10m): {len(same_z_pairs):7d}")
        lines.append(f"  same movement band (|dZ| <= 0.35m): {len(same_band_pairs):7d}")
        lines.append(f"    sloped/stair band only (0.10m < |dZ| <= 0.35m): {len(sloped_band_pairs):7d}")
        lines.append(f"  cross-layer pairs (|dZ| > 0.35m): {len(cross_layer_pairs):7d}")

        severity = Counter()
        for p in same_band_pairs:
            percent = p['fraction'] * 100.0
            if p['fully_contained']:
                bucket = "100% / full containment"
            elif percent >= 90.0:
                bucket = "90-99%"
            elif percent >= 80.0:
                bucket = "80-89%"
            elif percent >= 70.0:
                bucket = "70-79%"
            elif percent >= 50.0:
                bucket = "50-69%"
            elif percent >= 25.0:
                bucket = "25-49%"
            else:
                bucket = "<25%"
            severity[bucket] += 1

        lines.append("")
        lines.append("Same-band pair count by percentage of the smaller disc covered:")
        for label in [
            "<25%", "25-49%", "50-69%", "70-79%",
            "80-89%", "90-99%", "100% / full containment",
        ]:
            count = severity.get(label, 0)
            lines.append(
                f"  {label:>24}: {count:7d}/{len(same_band_pairs):<7d} "
                f"{self._fmt_pct(count, len(same_band_pairs))}"
            )

        high_pairs = [p for p in same_band_pairs if p['fraction'] >= 0.70]
        center_inside_pairs = [p for p in same_band_pairs if p['center_inside_larger']]
        contained_pairs = [p for p in same_band_pairs if p['fully_contained']]
        high_nodes = set()
        center_inside_nodes = set()
        contained_nodes = set()
        for p in high_pairs:
            a = p['a']; b = p['b']
            if abs(a['radius'] - b['radius']) <= 1.0e-9:
                high_nodes.update((a['scope_i'], b['scope_i']))
            else:
                high_nodes.add(a['scope_i'] if a['radius'] < b['radius'] else b['scope_i'])
        for p in center_inside_pairs:
            a = p['a']; b = p['b']
            if abs(a['radius'] - b['radius']) <= 1.0e-9:
                center_inside_nodes.update((a['scope_i'], b['scope_i']))
            else:
                center_inside_nodes.add(a['scope_i'] if a['radius'] < b['radius'] else b['scope_i'])
        for p in contained_pairs:
            a = p['a']; b = p['b']
            if abs(a['radius'] - b['radius']) <= 1.0e-9:
                contained_nodes.update((a['scope_i'], b['scope_i']))
            else:
                contained_nodes.add(a['scope_i'] if a['radius'] < b['radius'] else b['scope_i'])

        lines.append("")
        lines.append(f"Same-band >=70% smaller-disc overlap: {len(high_pairs)} pairs; {len(high_nodes)} affected smaller/equal nodes")
        lines.append(
            f"  >=70% linked/unlinked: "
            f"{sum(1 for p in high_pairs if p['linked'])}/"
            f"{sum(1 for p in high_pairs if not p['linked'])}"
        )
        lines.append(f"Same-band center inside larger/equal radius: {len(center_inside_pairs)} pairs; {len(center_inside_nodes)} affected nodes")
        lines.append(
            f"  center-inside linked/unlinked: "
            f"{sum(1 for p in center_inside_pairs if p['linked'])}/"
            f"{sum(1 for p in center_inside_pairs if not p['linked'])}"
        )
        lines.append(f"Same-band complete smaller-disc containment: {len(contained_pairs)} pairs; {len(contained_nodes)} affected nodes")
        lines.append(
            f"  full-containment linked/unlinked: "
            f"{sum(1 for p in contained_pairs if p['linked'])}/"
            f"{sum(1 for p in contained_pairs if not p['linked'])}"
        )
        lines.append(f"Same-band overlapping pairs already linked: {sum(1 for p in same_band_pairs if p['linked'])}/{len(same_band_pairs)}")

        if same_band_pairs:
            lines.append("")
            lines.append("Worst same-band overlap pairs (sorted by smaller-disc coverage):")
            ranked_pairs = sorted(
                same_band_pairs,
                key=lambda p: (
                    -p['fraction'], -int(p['fully_contained']),
                    -int(p['center_inside_larger']), -p['depth'],
                    p['a']['record']['id'], p['b']['record']['id'],
                ),
            )
            for p in ranked_pairs[:50]:
                a = p['a']; b = p['b']
                flags = []
                if p['fully_contained']:
                    flags.append("FULL")
                elif p['center_inside_larger']:
                    flags.append("CENTER-IN")
                if p['linked']:
                    flags.append("LINKED")
                flag_text = ",".join(flags) if flags else "-"
                a_degree = len(getattr(a['record']['node'], 'neighbors', []) or [])
                b_degree = len(getattr(b['record']['node'], 'neighbors', []) or [])
                lines.append(
                    f"  id={a['record']['id']:5d} b15={a['record']['b15']:3d} r={a['radius']:5.2f}m deg={a_degree:2d}"
                    f"  <-> id={b['record']['id']:5d} b15={b['record']['b15']:3d} r={b['radius']:5.2f}m deg={b_degree:2d}"
                    f"  dXY={p['distance']:6.3f}m dZ={p['dz']:5.3f}m"
                    f"  smaller-covered={p['fraction']*100.0:6.2f}%  {flag_text}"
                )
            if len(ranked_pairs) > 50:
                lines.append(f"  ... {len(ranked_pairs)-50} more same-band overlap pairs")
        lines.append("")

        lines.append("EXACT DUPLICATE CENTER CHECK")
        lines.append("-" * 78)
        exact = defaultdict(list)
        for r in records:
            exact[(r['rx'], r['ry'])].append(r)
        dup_groups = [(k, v) for k, v in exact.items() if len(v) > 1]
        lines.append(f"Exact same raw X/Y duplicate groups: {len(dup_groups)}")
        if dup_groups:
            for (rx, ry), rs in sorted(dup_groups, key=lambda kv: -len(kv[1]))[:20]:
                ids = ",".join(str(q['id']) for q in rs[:12])
                bvals = ",".join(str(q['b15']) for q in rs[:12])
                lines.append(f"  raw=({rx},{ry}) pos=({rx/65536.0:.4f},{ry/65536.0:.4f}) count={len(rs)} ids={ids} b15={bvals}")
            if len(dup_groups) > 20:
                lines.append(f"  ... {len(dup_groups)-20} more duplicate groups")
        lines.append("")

        lines.append("WEIRD NODE SAMPLES")
        lines.append("-" * 78)
        weird_records = [r for r in records if not r['clean']]
        if not weird_records:
            lines.append("No weird/non-clean residue nodes in this scope.")
        else:
            shown = 0
            by_pair = defaultdict(list)
            for r in weird_records:
                by_pair[(r['xr'], r['yr'])].append(r)
            for (xr, yr), rs in sorted(by_pair.items(), key=lambda kv: -len(kv[1]))[:10]:
                lines.append(f"  Pair x={fmt_res(xr)} y={fmt_res(yr)} count={len(rs)}")
                for r in rs[:5]:
                    n = r['node']
                    nn = r.get('nn', None)
                    nn_txt = f" nn={nn:.3f}m->id{r.get('nn_id')}" if nn is not None else ""
                    lines.append(
                        f"    id={r['id']:5d} x={float(n.x):10.4f} y={float(n.y):10.4f} "
                        f"b12={r['b12']:3d} b14={r['b14']:3d} b15={r['b15']:3d} b16={r['b16']:3d} "
                        f"class={r['phase_class']}{nn_txt}"
                    )
                    shown += 1
                    if shown >= 50:
                        break
                if shown >= 50:
                    lines.append("  ... sample limit reached")
                    break
        lines.append("")

        try:
            if len(getattr(self, 'selected_nodes', set())) == 1:
                idx = next(iter(self.selected_nodes))
                n = self.nodes[idx]
                rx, ry, rz = raw_xyz(n)
                xr = res25(rx); yr = res25(ry)
                lines.append("SELECTED NODE DETAIL")
                lines.append("-" * 78)
                lines.append(f"node id={getattr(n,'id',idx)}")
                lines.append(f"x={n.x:.6f}  y={n.y:.6f}  z={n.z:.6f}")
                lines.append(f"raw_x={rx} raw_y={ry} raw_z={rz}")
                lines.append(f"x_res25={fmt_res(xr)}  y_res25={fmt_res(yr)}")
                lines.append(f"x_signed25={fmt_raw(signed25_from_res(xr))}  y_signed25={fmt_raw(signed25_from_res(yr))}")
                lines.append(f"b12={int(n.b12)} b13={int(n.b13)} b14={int(n.b14)} b15={int(n.b15)} b16={int(n.b16)} b17={int(n.b17)} b18={int(n.b18)}")
                lines.append(f"radius={n.acceptance_radius():.6f}m  b15 mod2={int(n.b15)%2} mod4={int(n.b15)%4} mod8={int(n.b15)%8} mod16={int(n.b15)%16}")
                lines.append("")
        except Exception:
            pass

        lines.append("INTERPRETATION REMINDERS")
        lines.append("-" * 78)
        lines.append("  • If nearly all residues are 0x0000/1000/2000/3000, this is clean 6.25cm phase logic.")
        lines.append("  • If many nodes align to 0x0800 or 0x0400 but not 0x1000, there may be a smaller hidden sub-grid.")
        lines.append("  • Repeated non-clean residues imply a deterministic rule/table/optimizer artifact, not random float dust.")
        lines.append("  • If weird residues correlate with b15/b12/b16/area, that may reveal hidden generator modes.")
        lines.append("  • If weird residues correlate with NN spacing, optimizer/collapse/prune behavior becomes more likely.")
        lines.append("  • Center placement and b15 radius quantization are separate systems.")
        return "\n".join(lines)


    def _diagnostic_node_scope(self):
        """Return (label, nodes) for grid/radius diagnostics.

        Priority:
          1) selected nodes, if any
          2) nodes currently inside the 2D viewport, if possible
          3) all loaded nodes
        """
        try:
            if getattr(self, 'selected_nodes', None):
                ids = sorted(int(i) for i in self.selected_nodes if 0 <= int(i) < len(self.nodes))
                if ids:
                    return f"selected nodes ({len(ids)})", [self.nodes[i] for i in ids]
        except Exception:
            pass

        try:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 600
            x0, y0 = self.canvas_to_world(0, ch)
            x1, y1 = self.canvas_to_world(cw, 0)
            xmin, xmax = sorted((x0, x1))
            ymin, ymax = sorted((y0, y1))
            pad = max(2.0, 20.0 / max(float(self.vp_zoom or 1.0), 0.001))
            visible = [
                n for n in self.nodes
                if (xmin - pad) <= float(n.x) <= (xmax + pad)
                and (ymin - pad) <= float(n.y) <= (ymax + pad)
            ]
            if visible:
                return f"visible viewport nodes ({len(visible)})", visible
        except Exception:
            pass

        return f"all nodes ({len(self.nodes)})", list(self.nodes)

    @staticmethod

    def _grid_error_1d(value, step):
        if step <= 0:
            return 0.0
        return abs(float(value) - round(float(value) / step) * step)

    @classmethod

    def _grid_error_2d(cls, x, y, step):
        ex = cls._grid_error_1d(x, step)
        ey = cls._grid_error_1d(y, step)
        return max(ex, ey), (ex * ex + ey * ey) ** 0.5, ex, ey

    @staticmethod

    def _fmt_pct(n, d):
        return "0.0%" if not d else f"{(100.0 * n / d):5.1f}%"


    def _debug_note(self, label, **data):
        """Append a small breadcrumb for expanded crash logs."""
        try:
            import time
            ev = {
                't': round(time.perf_counter(), 6),
                'label': str(label),
            }
            for k, v in data.items():
                try:
                    if isinstance(v, float):
                        ev[k] = round(v, 6)
                    elif isinstance(v, (int, str, bool)) or v is None:
                        ev[k] = v
                    else:
                        ev[k] = repr(v)[:200]
                except Exception:
                    ev[k] = '<unrepr>'
            if not hasattr(self, '_debug_events') or self._debug_events is None:
                self._debug_events = []
            self._debug_events.append(ev)
            if len(self._debug_events) > 80:
                self._debug_events = self._debug_events[-80:]
        except Exception:
            pass


    def _collect_debug_state(self, extra=None):
        """Collect editor state for crash/slow-operation diagnostics.

        Must never raise. This is intentionally compact: enough to understand
        what the editor was doing without dumping the whole map.
        """
        try:
            import platform, time
            cw = self.canvas.winfo_width() if hasattr(self, 'canvas') else None
            ch = self.canvas.winfo_height() if hasattr(self, 'canvas') else None
            nodes = getattr(self, 'nodes', []) or []
            entities = getattr(self, 'entities', []) or []

            # Node health summary.
            invalid_neighbors = 0
            max_neighbors = 0
            total_neighbors = 0
            for n in nodes:
                nbs = getattr(n, 'neighbors', []) or []
                max_neighbors = max(max_neighbors, len(nbs))
                total_neighbors += len(nbs)
                for nb in nbs:
                    if not isinstance(nb, int) or nb < 0 or nb >= len(nodes):
                        invalid_neighbors += 1

            # Entity type summary, but keep it small.
            type_counts = {}
            static_count = 0
            outlined_count = 0
            for e in entities:
                tid = e.get('type_id') if isinstance(e, dict) else None
                type_counts[tid] = type_counts.get(tid, 0) + 1
                if isinstance(e, dict) and e.get('is_static'):
                    static_count += 1
                if tid in _ns.get('MODEL_OUTLINES', {}):
                    outlined_count += 1
            top_types = sorted(type_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]

            state = {
                'python': sys.version.replace('\n', ' '),
                'platform': platform.platform(),
                'cwd': os.getcwd(),
                'editor_file': os.path.abspath(__file__),
                'bms_path': getattr(self, 'bms_path', ''),
                'project_path': getattr(self, 'project_path', ''),
                'mode': self.mode.get() if hasattr(self, 'mode') else None,
                'canvas': {'w': cw, 'h': ch},
                'viewport': {
                    'zoom': getattr(self, 'vp_zoom', None),
                    'offset_x': getattr(self, 'vp_offset_x', None),
                    'offset_y': getattr(self, 'vp_offset_y', None),
                    'last_render_ms': getattr(self, '_last_render_ms', None),
                    'render_pending': getattr(self, '_render_pending', None),
                    'is_transforming': getattr(self, '_is_transforming', None),
                },
                'toggles': {
                    'nodes': self.show_nodes.get() if hasattr(self, 'show_nodes') else None,
                    'edges': self.show_edges.get() if hasattr(self, 'show_edges') else None,
                    'radius': self.show_radius.get() if hasattr(self, 'show_radius') else None,
                    'entities': self.show_entities.get() if hasattr(self, 'show_entities') else None,
                    'grid': self.show_grid.get() if hasattr(self, 'show_grid') else None,
                    'ids': self.show_ids.get() if hasattr(self, 'show_ids') else None,
                    'zones': self.show_zones.get() if hasattr(self, 'show_zones') else None,
                },
                'z_filter': {
                    'on': self.z_filter_on.get() if hasattr(self, 'z_filter_on') else None,
                    'min': self._get_z_filter_bounds()[0] if hasattr(self, 'z_filter_min') else None,
                    'max': self._get_z_filter_bounds()[1] if hasattr(self, 'z_filter_max') else None,
                },
                'selection': {
                    'selected_id': getattr(self, 'selected_id', None),
                    'selected_nodes_count': len(getattr(self, 'selected_nodes', []) or []),
                    'connecting_from': getattr(self, '_connecting_from', None),
                },
                'counts': {
                    'nodes': len(nodes),
                    'entities': len(entities),
                    'static_entities': static_count,
                    'outlined_entities': outlined_count,
                    'zones': len(getattr(self, 'zones', {}) or {}),
                    'undo': len(getattr(self, '_undo_stack', []) or []),
                    'redo': len(getattr(self, '_redo_stack', []) or []),
                },
                'node_health': {
                    'total_neighbor_refs': total_neighbors,
                    'max_neighbors': max_neighbors,
                    'invalid_neighbor_refs': invalid_neighbors,
                    'next_id': getattr(self, 'next_id', None),
                },
                'entity_top_types': top_types,
                'last_click': getattr(self, '_debug_last_click', None),
                'last_insert': getattr(self, '_debug_last_insert', None),
                'debug_events': list(getattr(self, '_debug_events', []) or [])[-40:],
            }

            if extra:
                state['extra'] = extra
            return state
        except Exception as e:
            return {'debug_state_error': repr(e)}


    def _format_debug_state(self, state):
        """Pretty-print debug state for the crash log."""
        try:
            import pprint
            return pprint.pformat(state, width=120, sort_dicts=False)
        except Exception:
            try:
                return repr(state)
            except Exception:
                return '<state-unprintable>'


    def _log_crash(self, context, path, traceback_str, extra=None):
        """Write expanded crash info to ain_editor_crash.log next to the editor."""
        import datetime
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ain_editor_crash.log')
            now = datetime.datetime.now()
            state = self._collect_debug_state(extra=extra)
            state_txt = self._format_debug_state(state)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Time: {now.isoformat(sep=' ', timespec='milliseconds')}\n")
                f.write(f"Context: {context}\n")
                f.write(f"File: {path}\n")
                f.write("\n--- TRACEBACK / MESSAGE ---\n")
                f.write(traceback_str or '<no traceback supplied>')
                f.write("\n\n--- EDITOR STATE ---\n")
                f.write(state_txt)
                f.write("\n")
            try:
                print("\n" + "=" * 80, file=sys.stderr)
                print(f"Time: {now.isoformat(sep=' ', timespec='milliseconds')}", file=sys.stderr)
                print(f"Context: {context}", file=sys.stderr)
                print(f"File: {path}", file=sys.stderr)
                print("--- TRACEBACK / MESSAGE ---", file=sys.stderr)
                print(traceback_str or '<no traceback supplied>', file=sys.stderr)
                print("--- EDITOR STATE ---", file=sys.stderr)
                print(state_txt, file=sys.stderr)
                sys.stderr.flush()
            except Exception:
                pass
            self.status(f"Crash logged to ain_editor_crash.log")
        except Exception:
            pass  # never crash the crash logger


    def _report_callback_exception(self, exc, val, tb):
        tb_str = ''.join(traceback.format_exception(exc, val, tb))
        self._log_crash('tk_callback', self.bms_path or self.project_path or '<unsaved>', tb_str)
        try:
            messagebox.showerror(
                "Editor Crash",
                "The editor hit an exception.\n\n"
                "A traceback was printed to the console and logged to "
                "ain_editor_crash.log."
            )
        except Exception:
            pass


    def _start_hang_watchdog(self):
        """Start a background watchdog that logs UI hangs.

        Normal exception logging only runs if Tk gets control back. This thread
        catches the harder case: node placement or redraw locks the main thread
        so the editor stops responding and no traceback appears.
        """
        try:
            if self._watchdog_started:
                return
            self._watchdog_started = True
            self._watchdog_last_heartbeat = time.monotonic()
            self.after(500, self._watchdog_heartbeat)
            t = threading.Thread(target=self._hang_watchdog_loop,
                                 name='AINEditorHangWatchdog',
                                 daemon=True)
            t.start()
        except Exception:
            pass


    def _watchdog_heartbeat(self):
        """Main-thread heartbeat. If this stops, the watchdog logs a hang."""
        try:
            self._watchdog_last_heartbeat = time.monotonic()
            self._watchdog_hang_logged = False

            # Snapshot only values safe to read later from the watchdog thread.
            # Do all Tk/StringVar access here on the main thread.
            self._watchdog_snapshot = {
                'mode': self.mode.get() if hasattr(self, 'mode') else None,
                'nodes': len(getattr(self, 'nodes', []) or []),
                'entities': len(getattr(self, 'entities', []) or []),
                'zones': len(getattr(self, 'zones', {}) or {}),
                'selected_id': getattr(self, 'selected_id', None),
                'selected_nodes_count': len(getattr(self, 'selected_nodes', []) or []),
                'viewport': {
                    'zoom': getattr(self, 'vp_zoom', None),
                    'offset_x': getattr(self, 'vp_offset_x', None),
                    'offset_y': getattr(self, 'vp_offset_y', None),
                    'last_render_ms': getattr(self, '_last_render_ms', None),
                    'render_pending': getattr(self, '_render_pending', None),
                    'is_transforming': getattr(self, '_is_transforming', None),
                },
                'toggles': {
                    'nodes': self.show_nodes.get() if hasattr(self, 'show_nodes') else None,
                    'edges': self.show_edges.get() if hasattr(self, 'show_edges') else None,
                    'radius': self.show_radius.get() if hasattr(self, 'show_radius') else None,
                    'entities': self.show_entities.get() if hasattr(self, 'show_entities') else None,
                    'grid': self.show_grid.get() if hasattr(self, 'show_grid') else None,
                    'ids': self.show_ids.get() if hasattr(self, 'show_ids') else None,
                    'zones': self.show_zones.get() if hasattr(self, 'show_zones') else None,
                },
                'z_filter': {
                    'on': self.z_filter_on.get() if hasattr(self, 'z_filter_on') else None,
                    'min': self._get_z_filter_bounds()[0] if hasattr(self, 'z_filter_min') else None,
                    'max': self._get_z_filter_bounds()[1] if hasattr(self, 'z_filter_max') else None,
                },
                'bms_path': getattr(self, 'bms_path', ''),
                'project_path': getattr(self, 'project_path', ''),
                'last_click': getattr(self, '_debug_last_click', None),
                'last_insert': getattr(self, '_debug_last_insert', None),
                'last_action': getattr(self, '_watchdog_action', None),
                'debug_events': list(getattr(self, '_debug_events', []) or [])[-40:],
            }
        except Exception:
            pass
        try:
            if getattr(self, '_watchdog_enabled', False):
                self.after(500, self._watchdog_heartbeat)
        except Exception:
            pass


    def _hang_watchdog_loop(self):
        """Daemon thread: write a crash log if the UI thread freezes >10s."""
        while True:
            try:
                time.sleep(1.0)
                if not getattr(self, '_watchdog_enabled', False):
                    continue
                age = time.monotonic() - getattr(self, '_watchdog_last_heartbeat', 0)
                if age >= 10.0 and not getattr(self, '_watchdog_hang_logged', False):
                    self._watchdog_hang_logged = True
                    self._write_hang_log_from_thread(age)
            except Exception:
                # Watchdog must never bring down the editor.
                pass


    def _write_hang_log_from_thread(self, age_seconds):
        """Thread-safe hang logger. Avoids Tk calls completely."""
        try:
            import datetime, pprint
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'ain_editor_crash.log')
            now = datetime.datetime.now()

            # Capture the main thread Python stack. This is the most valuable
            # part for freezes: it shows where the editor was stuck.
            main_stack = '<main thread stack unavailable>'
            try:
                frames = sys._current_frames()
                frame = frames.get(getattr(self, '_main_thread_ident', None))
                if frame is not None:
                    main_stack = ''.join(traceback.format_stack(frame))
            except Exception as e:
                main_stack = f'<stack capture failed: {e!r}>'

            snap = dict(getattr(self, '_watchdog_snapshot', {}) or {})
            snap['hang_age_seconds'] = round(float(age_seconds), 3)
            snap['watchdog_action_live'] = getattr(self, '_watchdog_action', None)
            snap['last_click_live'] = getattr(self, '_debug_last_click', None)
            snap['last_insert_live'] = getattr(self, '_debug_last_insert', None)
            snap['debug_events_live'] = list(getattr(self, '_debug_events', []) or [])[-40:]

            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Time: {now.isoformat(sep=' ', timespec='milliseconds')}\n")
                f.write("Context: WATCHDOG_HANG_10S\n")
                f.write(f"File: {getattr(self, 'bms_path', '') or getattr(self, 'project_path', '') or '<unsaved>'}\n")
                f.write(f"Message: Tk main thread did not heartbeat for {age_seconds:.1f} seconds.\n")
                f.write("\n--- MAIN THREAD STACK AT HANG ---\n")
                f.write(main_stack)
                f.write("\n--- LAST SAFE WATCHDOG SNAPSHOT ---\n")
                f.write(pprint.pformat(snap, width=120, sort_dicts=False))
                f.write("\n")

            try:
                print("\n" + "=" * 80, file=sys.stderr)
                print(f"Time: {now.isoformat(sep=' ', timespec='milliseconds')}", file=sys.stderr)
                print("Context: WATCHDOG_HANG_10S", file=sys.stderr)
                print(f"Main thread did not heartbeat for {age_seconds:.1f}s", file=sys.stderr)
                print("--- MAIN THREAD STACK AT HANG ---", file=sys.stderr)
                print(main_stack, file=sys.stderr)
                print("--- LAST SAFE WATCHDOG SNAPSHOT ---", file=sys.stderr)
                print(pprint.pformat(snap, width=120, sort_dicts=False), file=sys.stderr)
                sys.stderr.flush()
            except Exception:
                pass
        except Exception:
            pass

    # === canvas_events.py (CanvasEventsMixin) ===

    """Methods for canvas mouse/keyboard event handling in AINEditor."""

    # ── 6. CANVAS EVENTS ─────────────────────────────────────────────────────

    def _cancel_seed_generate(self, reason='cancelled'):
        """Cancel the armed seed-disc generator preview without touching nodes.

        This only cancels the pre-click seed selection state. It deliberately
        does not try to interrupt a generator worker once generation has started.
        """
        if getattr(self, '_generator_running', False):
            self.status('Generate AIN: generation is already running; waiting for it to finish.')
            return False
        try:
            armed = bool(getattr(self, '_seed_generate_armed', False))
        except Exception:
            armed = False
        if not armed and str(self.mode.get()) != 'generate':
            return False

        self._seed_generate_armed = False
        self._shared_generator_geometry_cache = None
        try:
            self.canvas.delete('seed_radius_preview')
        except Exception:
            pass
        try:
            self.mode.set('edit')
            self._on_mode_change()
        except Exception:
            try:
                self.mode.set('edit')
            except Exception:
                pass
        try:
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        self.status(f'Generate AIN {reason}.')
        return True

    def _on_mouse_move(self, event):
        # Only update coordinate display — no hit-testing on every move
        wx, wy = self.canvas_to_world(event.x, event.y)
        self._coord_var.set(f"X: {wx:8.3f}  Y: {wy:8.3f}")
        self._cursor_wx = wx
        self._cursor_wy = wy
        if getattr(self, '_generator_running', False):
            return
        # Update rubber-band box if dragging a selection
        if self._sel_box_start is not None:
            self._update_sel_box(event.x, event.y)
        # Live preview of the generation disc around the cursor while picking a seed
        self._update_seed_radius_preview(event.x, event.y)
        # Scan Applier has its own visible one-shot disc.
        self._update_scan_radius_preview(event.x, event.y)
        self._update_paint_preview_overlay(event.x, event.y)

    def _generator_edit_blocked(self):
        if getattr(self, '_generator_running', False):
            self.status('Generate AIN: generation running; editing is locked until it finishes.')
            return True
        return False

    def _update_seed_radius_preview(self, cx, cy):
        """While the seed-disc generator is armed, draw the chosen radius as a dashed disc
        around the cursor, so the radius (e.g. 32 m) is visible before clicking the seed."""
        tag = 'seed_radius_preview'
        try:
            armed = (self.mode.get() == 'generate' and getattr(self, '_seed_generate_armed', False))
        except Exception:
            armed = False
        if not armed:
            try:
                self.canvas.delete(tag)
            except Exception:
                pass
            return
        try:
            r_m  = float(getattr(self, '_seed_generate_radius', 32.0) or 32.0)
            r_px = r_m * float(self.vp_zoom)
        except Exception:
            return
        self.canvas.delete(tag)
        col = '#3fd964'
        self.canvas.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px,
                                outline=col, width=2, dash=(6, 4), tags=tag)
        self.canvas.create_line(cx - 8, cy, cx + 8, cy, fill=col, tags=tag)
        self.canvas.create_line(cx, cy - 8, cx, cy + 8, fill=col, tags=tag)
        self.canvas.create_text(cx + 12, cy, anchor='w', fill=col,
                                text=f'{r_m:.0f} m radius', tags=tag)

    def _on_lclick(self, event):
        # Canvas actions own subsequent keyboard shortcuts. Without this, an
        # Entry edited earlier can receive Ctrl+V before node paste runs.
        self.canvas.focus_set()
        # A plain click/select must never leave the renderer in node-drag-fast mode.
        self._node_drag_fast = False
        mode = self.mode.get()
        shift = (event.state & 0x0001) != 0
        ctrl  = (event.state & 0x0004) != 0
        if getattr(self, '_generator_running', False):
            self.status('Generate AIN: generation running; editing is locked until it finishes.')
            return

        if bool(getattr(self, '_scan_seed_armed', False)):
            wx, wy = self.canvas_to_world(event.x, event.y)
            self._scan_apply_at(wx, wy)
            return

        # Zone mode clicks
        if mode == 'zone':
            if self._on_tile_press(event, erase=False):
                return
            wx, wy = self.canvas_to_world(event.x, event.y)

            # Clicking inside a zone selects it. Dragging after this moves the whole zone.
            hit = self._zone_at_canvas(event.x, event.y)
            if hit is not None:
                self._zone_selected = hit
                self._sync_zone_panel_from_selected()
                zx1,zy1,zx2,zy2 = self.zones[hit]
                pts0 = list(self._zone_points(hit))
                self._zone_drag = ('body', hit, zx1,zy1,zx2,zy2, wx,wy, pts0, self._zone_snapshot())
                self._mark_zones_dirty()
                self._redraw_zone_overlays()
                self.status(f"Selected Zone {hit}")
                return
            return

        if mode in ('draw', 'paint', 'zone') and (shift or ctrl):
            self.mode.set('edit')
            self._on_mode_change()
            mode = 'edit'

        # Rubber-band selection start (Shift or Ctrl held in edit mode)
        if mode == 'edit' and (shift or ctrl):
            self._sel_box_start = (event.x, event.y)
            self._sel_box_mode  = 'add' if shift else 'remove'
            return

        nid = self._node_at_canvas(event.x, event.y)

        if self._connecting_from is not None:
            if nid is not None and nid != self._connecting_from:
                self._add_connection(self._connecting_from, nid)
                self.status(f"Connected node {self._connecting_from} ↔ {nid}")
            self._connecting_from = None
            self.canvas.configure(cursor='crosshair')
            self.redraw()
            return

        if mode == 'edit':
            if nid is not None:
                # Click on already-selected node -> deselect it (single select only)
                if nid == self.selected_id and len(self.selected_nodes) <= 1:
                    self.select_node(None)
                elif nid in self.selected_nodes and len(self.selected_nodes) > 1:
                    # Clicking a node that's part of multi-selection — start group drag
                    # without changing the selection
                    self.selected_id = nid
                    self._show_info_for_node_selection()
                    self._drag_node_start = (event.x, event.y,
                                             self.nodes[nid].x, self.nodes[nid].y)
                    self._drag_node_snap = self._snapshot_nodes()
                    self._drag_node_origins = {
                        n: (self.nodes[n].x, self.nodes[n].y)
                        for n in self.selected_nodes
                        if n < len(self.nodes)
                    }
                else:
                    self.select_node(nid)
                    self._drag_node_start = (event.x, event.y,
                                             self.nodes[nid].x, self.nodes[nid].y)
                    # Snapshot position BEFORE drag starts — used for undo on release
                    self._drag_node_snap = self._snapshot_nodes()
                    # Store origins of all selected nodes for multi-drag
                    self._drag_node_origins = {
                        n: (self.nodes[n].x, self.nodes[n].y)
                        for n in self.selected_nodes
                        if n < len(self.nodes)
                    }
            else:
                self.select_node(None)
                self._drag_node_snap = None
                self._drag_start = (event.x, event.y,
                                    self.vp_offset_x, self.vp_offset_y)

        elif mode == 'paint':
            wx, wy = self.canvas_to_world(event.x, event.y)
            self._cursor_wx = wx
            self._cursor_wy = wy
            self._coord_var.set(f"X: {wx:8.3f}  Y: {wy:8.3f}")
            self._begin_paint_stroke()
            self._paint_at(wx, wy)
            self._update_paint_preview_overlay(event.x, event.y)

        elif mode == 'draw':
            wx, wy = self.canvas_to_world(event.x, event.y)
            wx, wy = self._snap_to_grid(wx, wy)
            lx = self._draw_last_wx
            ly = self._draw_last_wy
            _rb_active = self._draw_radius_brush.get() and self._draw_last_b15 is not None
            if _rb_active:
                _rb_r = self._draw_last_b15 / 16.0
                min_d = _rb_r * self._draw_overlap.get()
            elif self.grid_snap_nodes.get():
                label = self.grid_fixed_spacing_label.get()
                min_d = next((v for l,v in MED_GRID_SPACING_CHOICES if l==label), self._draw_spacing.get())
            else:
                min_d = self._draw_spacing.get()
            import math as _dm
            if lx is None or _dm.hypot(wx-lx, wy-ly) >= min_d:
                _occ_th = _rb_r if _rb_active else None
                if not self._draw_position_occupied(wx, wy, threshold=_occ_th, respect_existing_radius=_rb_active):
                    self._draw_last_wx = wx
                    self._draw_last_wy = wy
                    try:
                        _n_before = len(self.nodes)
                        self._insert_node(wx, wy)
                        if len(self.nodes) > _n_before:
                            self._draw_last_b15 = self.nodes[-1].b15
                    except Exception:
                        pass

        elif mode == 'generate':
            wx, wy = self.canvas_to_world(event.x, event.y)
            target_node_z = None
            # A selected reference node is the most explicit layer request the
            # 2D editor can provide.  Preserve its Z before clearing selection.
            selected_seed_nodes = [
                self.nodes[int(node_id)]
                for node_id in getattr(self, 'selected_nodes', set())
                if 0 <= int(node_id) < len(self.nodes)
            ]
            if selected_seed_nodes:
                nearest = min(
                    selected_seed_nodes,
                    key=lambda node: (float(node.x) - float(wx)) ** 2
                                     + (float(node.y) - float(wy)) ** 2,
                )
                target_node_z = float(nearest.z)
            self.select_node(None)
            if getattr(self, '_seed_generate_armed', False):
                self._run_generator_at(wx, wy, target_node_z=target_node_z)
            else:
                self.status('Generate mode: press Generate first, choose radius, then click seed point.')
            return

        elif mode == 'draw':
            wx, wy = self.canvas_to_world(event.x, event.y)
            wx, wy = self._snap_to_grid(wx, wy)
            # Check grid warning
            if not self.show_grid.get() and not getattr(self, '_draw_no_grid_warned', False):
                self._draw_no_grid_warned = True
                messagebox.showwarning(
                    'No Grid Active',
                    'You are drawing nodes without grid snap active.\n'
                    'Nodes will be placed at cursor position with no alignment.\n\n'
                    'Enable grid snap in the left panel for aligned placement.',
                    parent=self)
            _rb_active = self._draw_radius_brush.get() and self._draw_last_b15 is not None
            if _rb_active:
                import math as _dm
                _rb_r = self._draw_last_b15 / 16.0
                lx = self._draw_last_wx
                ly = self._draw_last_wy
                if lx is not None and _dm.hypot(wx - lx, wy - ly) < _rb_r * self._draw_overlap.get():
                    return
            _occ_th = _rb_r if _rb_active else None
            if not self._draw_position_occupied(wx, wy, threshold=_occ_th, respect_existing_radius=_rb_active):
                self._draw_last_wx = wx
                self._draw_last_wy = wy
                try:
                    _n_before = len(self.nodes)
                    self._insert_node(wx, wy)
                    if len(self.nodes) > _n_before:
                        self._draw_last_b15 = self.nodes[-1].b15
                except Exception:
                    pass


    def _on_ldrag(self, event):
        mode = self.mode.get()
        # Zone mode — handle area-point / area-body drag
        if mode == 'zone':
            if self._on_tile_drag(event, erase=False):
                return
            wx, wy = self.canvas_to_world(event.x, event.y)

            if self._zone_drag is not None:
                dtype = self._zone_drag[0]
                anum  = self._zone_drag[1]
                if anum in self.zones:
                    zx1,zy1,zx2,zy2 = self.zones[anum]
                    if dtype == 'corner':
                        cidx = self._zone_drag[2]
                        if cidx == 0:
                            self.zones[anum] = [wx, wy, zx2, zy2]
                            meta = self._ensure_zone_meta(anum)
                            pts = list(meta.get('points') or self._zone_points(anum))
                            if pts:
                                pts[0] = (wx, wy)
                                meta['points'] = pts
                        else:
                            self.zones[anum] = [zx1, zy1, wx, wy]
                            meta = self._ensure_zone_meta(anum)
                            pts = list(meta.get('points') or self._zone_points(anum))
                            if len(pts) >= 2:
                                pts[1] = (wx, wy)
                                meta['points'] = pts
                    elif dtype == 'body':
                        ox1,oy1,ox2,oy2,omx,omy = self._zone_drag[2:8]
                        pts0 = self._zone_drag[8] if len(self._zone_drag) > 8 else []
                        dx = wx - omx
                        dy = wy - omy
                        self.zones[anum] = [ox1+dx, oy1+dy, ox2+dx, oy2+dy]
                        if pts0:
                            meta = self._ensure_zone_meta(anum)
                            meta['points'] = [(px + dx, py + dy) for px, py in pts0]
                    self._mark_zones_dirty()
                    self._redraw_zone_overlays()
                return
        # Rubber-band box in progress — update visual directly here
        if self._sel_box_start is not None:
            self._update_sel_box(event.x, event.y)
            return
        if mode == 'edit':
            if self._drag_node_start and self.selected_id is not None:
                sx, sy, ox, oy = self._drag_node_start
                dx = (event.x - sx) / self.vp_zoom
                dy = -(event.y - sy) / self.vp_zoom
                if len(self.selected_nodes) > 1:
                    for nid in self.selected_nodes:
                        node = self.nodes[nid]
                        ox_n, oy_n = self._drag_node_origins.get(nid, (node.x, node.y))
                        nx, ny = self._snap_to_grid(ox_n + dx, oy_n + dy)
                        node.x = nx
                        node.y = ny
                else:
                    node = self.nodes[self.selected_id]
                    nx, ny = self._snap_to_grid(ox + dx, oy + dy)
                    node.x = nx
                    node.y = ny
                self._node_drag_fast = True
                self._schedule_node_drag_redraw()
            elif self._drag_start:
                sx, sy, ox, oy = self._drag_start
                dx = (event.x - sx) / self.vp_zoom
                dy = -(event.y - sy) / self.vp_zoom
                self.vp_offset_x = ox - dx
                self.vp_offset_y = oy - dy
                self._begin_transform()
                self._schedule_render_idle()  # render only when queue drains
                self._schedule_settle()
        elif mode == 'paint':
            wx, wy = self.canvas_to_world(event.x, event.y)
            self._cursor_wx = wx
            self._cursor_wy = wy
            self._coord_var.set(f"X: {wx:8.3f}  Y: {wy:8.3f}")
            self._begin_paint_stroke()
            self._paint_at(wx, wy)
            self._update_paint_preview_overlay(event.x, event.y)

        elif mode == 'draw':
            wx, wy = self.canvas_to_world(event.x, event.y)
            wx, wy = self._snap_to_grid(wx, wy)
            lx = self._draw_last_wx
            ly = self._draw_last_wy
            _rb_active = self._draw_radius_brush.get() and self._draw_last_b15 is not None
            if _rb_active:
                _rb_r = self._draw_last_b15 / 16.0
                min_d = _rb_r * self._draw_overlap.get()
            elif self.grid_snap_nodes.get():
                label = self.grid_fixed_spacing_label.get()
                min_d = next((v for l,v in MED_GRID_SPACING_CHOICES if l==label), self._draw_spacing.get())
            else:
                min_d = self._draw_spacing.get()
            import math as _dm
            if lx is None or _dm.hypot(wx-lx, wy-ly) >= min_d:
                _occ_th = _rb_r if _rb_active else None
                if not self._draw_position_occupied(wx, wy, threshold=_occ_th, respect_existing_radius=_rb_active):
                    self._draw_last_wx = wx
                    self._draw_last_wy = wy
                    try:
                        _n_before = len(self.nodes)
                        self._insert_node(wx, wy)
                        if len(self.nodes) > _n_before:
                            self._draw_last_b15 = self.nodes[-1].b15
                    except Exception:
                        pass


    def _on_lrelease(self, event):
        if self._tile_painting:
            self._on_tile_release(event)
            return
        if getattr(self, '_generator_running', False):
            self.status('Generate AIN: generation running; editing is locked until it finishes.')
            return
        if getattr(self, '_paint_stroke_active', False):
            self._finish_paint_stroke()
            return
        # Mouse release can end node-drag fast mode.
        # It is harmless to clear this even for zone/pan releases.
        # Pending jobs are cancelled in the node-release block below.
        # Finish zone drag
        if self._zone_drag is not None:
            dtype = self._zone_drag[0]
            anum  = self._zone_drag[1]
            snap = self._zone_drag[-1] if isinstance(self._zone_drag[-1], tuple) else None
            if snap is not None:
                self._push_undo(f'Move zone {anum}', lambda s=snap: self._restore_zone_snapshot(s))
            self._zone_drag = None
            self._mark_zones_dirty()
            self._redraw_zone_overlays()
            return

        # Finish rubber-band selection
        if self._sel_box_start is not None:
            self._finish_sel_box(event.x, event.y)
            self._sel_box_start = None
            self._sel_box_mode  = None
            if self._sel_box_item is not None:
                self.canvas.delete(self._sel_box_item)
                self._sel_box_item = None
            self.redraw()
            return
        node_actually_moved = False
        if self._drag_node_start is not None:
            nid = self.selected_id
            if nid is not None and nid < len(self.nodes) and self._drag_node_snap is not None:
                if len(self.selected_nodes) > 1:
                    # Multi-node drag — check if any node moved
                    any_moved = any(
                        abs(self.nodes[n].x - self._drag_node_snap[n].x) > 0.01 or
                        abs(self.nodes[n].y - self._drag_node_snap[n].y) > 0.01
                        for n in self.selected_nodes if n < len(self.nodes)
                    )
                    if any_moved:
                        node_actually_moved = True
                        snap = self._drag_node_snap
                        self._push_undo(f'Move {len(self.selected_nodes)} nodes',
                                        lambda s=snap: self._restore_nodes(s))
                else:
                    snap_node = self._drag_node_snap[nid]
                    cur_node  = self.nodes[nid]
                    moved = (abs(cur_node.x - snap_node.x) > 0.01 or
                             abs(cur_node.y - snap_node.y) > 0.01)
                    if moved:
                        node_actually_moved = True
                        snap = self._drag_node_snap
                        self._push_undo(f'Move node {nid}',
                                        lambda s=snap: self._restore_nodes(s))
            self._drag_node_snap = None
            self._drag_node_origins = {}
            # Stop fast drag path and cancel pending cheap frames.
            self._node_drag_fast = False
            try:
                if self._node_drag_render_job is not None:
                    self.after_cancel(self._node_drag_render_job)
                    self._node_drag_render_job = None
                if self._node_drag_inspector_job is not None:
                    self.after_cancel(self._node_drag_inspector_job)
                    self._node_drag_inspector_job = None
            except Exception:
                pass
            self._update_inspector()
            self._update_stats()
            if node_actually_moved:
                # Node moved — rebuild hit tester and do a full sync
                self._rebuild_hit_tester()
                self.redraw()
                self.refresh_3d_views(rebuild=True, refit=False, reason='node_move_release')
        # Pan drag: settle already handles the final sync — no redraw here
        self._node_drag_fast = False
        self._drag_start = None
        self._drag_node_start = None


    def _on_mclick(self, event):
        # Pan start. Keep the original viewport so drag distance maps exactly
        # to world offset; do not rely on Tk canvas scan/canvas items.
        self._drag_start = (event.x, event.y, self.vp_offset_x, self.vp_offset_y)
        self._begin_transform()
        try:
            self.canvas.focus_set()
        except Exception:
            pass


    def _on_mdrag(self, event):
        if self._drag_start:
            sx, sy, ox, oy = self._drag_start
            z = max(0.0001, float(self.vp_zoom))
            dx = (event.x - sx) / z
            dy = -(event.y - sy) / z
            self.vp_offset_x = ox - dx
            self.vp_offset_y = oy - dy
            self._begin_transform()
            self._schedule_render_idle()
            self._schedule_settle()


    def _on_mrelease(self, event):
        if self._drag_start:
            self._drag_start = None
            self._force_next_render_no_cache = True
            self._schedule_settle()
            self._schedule_render_idle()


    def _on_rclick(self, event):
        if getattr(self, '_generator_running', False):
            self.status('Generate AIN: generation running; editing is locked until it finishes.')
            return
        mode = self.mode.get()
        if bool(getattr(self, '_scan_seed_armed', False)):
            self._cancel_scan_seed('cancelled by right-click')
            return
        if mode == 'zone' and self._on_tile_press(event, erase=True):
            return
        if mode == 'generate':
            self._cancel_seed_generate('cancelled by right-click')
            return
        if mode == 'draw':
            m = self._make_ui_menu(self, persistent=False)
            m.add_checkbutton(
                label="Radius brush",
                variable=self._draw_radius_brush,
                command=lambda: self.status(
                    "Radius brush ON — node spacing follows last placed radius"
                    if self._draw_radius_brush.get()
                    else "Radius brush OFF"))
            _spacing_choices = [
                ("0.5 m (fine)", 0.5),
                ("1.0 m", 1.0),
                ("1.5 m (default)", 1.5),
                ("2.0 m", 2.0),
                ("3.0 m", 3.0),
                ("4.0 m", 4.0),
                ("6.0 m", 6.0),
                ("8.0 m", 8.0),
            ]
            sm = self._make_ui_menu(m, persistent=False)
            for _lbl, _val in _spacing_choices:
                sm.add_radiobutton(
                    label=_lbl,
                    variable=self._draw_spacing,
                    value=_val,
                    command=lambda v=_val: self.status(f"Node spacing: {v} m"))
            m.add_cascade(label="Node spacing", menu=sm)
            _overlap_choices = [
                ("Max (0.25x)", 0.25),
                ("Very Heavy (0.5x)", 0.5),
                ("Heavy (0.75x)", 0.75),
                ("Moderate (1.0x)", 1.0),
                ("Medium (1.25x)", 1.25),
                ("Light (1.5x)", 1.5),
                ("Minimal (1.75x)", 1.75),
                ("None (2.0x)", 2.0),
            ]
            om = self._make_ui_menu(m, persistent=False)
            for _lbl, _val in _overlap_choices:
                om.add_radiobutton(
                    label=_lbl,
                    variable=self._draw_overlap,
                    value=_val,
                    command=lambda v=_val: self.status(f"Radius overlap: {v}x"))
            m.add_cascade(label="Radius overlap", menu=om)
            m.post(event.x_root, event.y_root)
            return
        if mode == 'edit':
            # Check if right-clicking on a node (context menu) or empty (insert)
            nid = self._node_at_canvas(event.x, event.y)
            if nid is not None:
                self._node_context_menu(event, nid)
            else:
                wx, wy = self.canvas_to_world(event.x, event.y)
                self._debug_last_click = {
                    'button': 'right',
                    'canvas_x': event.x,
                    'canvas_y': event.y,
                    'world_x': round(wx, 6),
                    'world_y': round(wy, 6),
                    'mode': mode,
                    'zoom': round(self.vp_zoom, 6),
                }
                self._debug_note('right_click_insert_attempt', **self._debug_last_click)
                try:
                    wx, wy = self._snap_to_grid(wx, wy)
                    self._insert_node(wx, wy)
                except Exception:
                    import traceback
                    self._log_crash(
                        'insert_node',
                        self.bms_path or self.project_path or '<unsaved>',
                        traceback.format_exc(),
                        extra={'click': self._debug_last_click}
                    )
                    messagebox.showerror(
                        "Insert Node Error",
                        "The editor hit an error while inserting a node. Crash details were logged to ain_editor_crash.log."
                    )

    def _on_rdrag(self, event):
        if self.mode.get() == 'zone':
            self._on_tile_drag(event, erase=True)

    def _on_rrelease(self, event):
        self._on_tile_release(event)

    def _on_scroll(self, event):
        if event.num == 4 or event.delta > 0:
            factor = 1.2
        else:
            factor = 1/1.2

        old_zoom = float(self.vp_zoom)
        old_off_x = float(self.vp_offset_x)
        old_off_y = float(self.vp_offset_y)

        wx, wy = self.canvas_to_world(event.x, event.y)
        self.vp_zoom = max(0.05, min(200.0, self.vp_zoom * factor))
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        self.vp_offset_x = wx - (event.x - cw/2) / self.vp_zoom
        self.vp_offset_y = wy + (event.y - ch/2) / self.vp_zoom
        self._zoom_var.set(f"Zoom: {self.vp_zoom:.1f}")

        self._zoom_op_count += 1
        self._begin_transform()

        # Wheel zoom preview is throttled/coalesced. We update the
        # logical viewport on every wheel event, but do at most one bitmap
        # transform per throttle interval.
        preview_ok = self._schedule_zoom_preview()
        if not preview_ok:
            self._schedule_render_idle()

        self._schedule_settle()

    def _zoom_center(self, factor):
        self.vp_zoom = max(0.05, min(200.0, self.vp_zoom * factor))
        self._zoom_var.set(f"Zoom: {self.vp_zoom:.1f}")
        self._zoom_op_count += 1
        self._begin_transform()
        preview_ok = self._schedule_zoom_preview()
        if not preview_ok:
            self._schedule_render_idle()
        self._schedule_settle()

    def _pan_grid(self, dx, dy):
        """MED-style numpad pan: move one tenth of the selected grid spacing.

        Reverse-engineering dfbhdmed v2.05c shows VK_NUMPAD2/4/6/8 do not
        advance by a full grid cell.  MED takes its current fixed grid value,
        divides it by 10 (signed integer arithmetic internally), and adds/subtracts
        that amount from the viewport offsets before invalidating/redrawing.

        Keeping the normal render path here is intentional: this reproduces
        MED's movement cadence without changing free mouse panning or introducing
        deferred/bitmap-only navigation artifacts.
        """
        spacing = float(_grid_spacing_mod._GRID_FIXED_SPACING_M or 16.0)
        step = spacing * 0.1
        self.vp_offset_x += dx * step
        self.vp_offset_y += dy * step
        self._schedule_render_idle()

    def _segments_intersect(self, ax, ay, bx, by, cx, cy, dx, dy):
        """Return True if segment AB intersects segment CD.
        Uses cross product sign test — no libraries needed.
        Cross product of AB with point P = (bx-ax)*(py-ay) - (by-ay)*(px-ax)
        If C and D are on opposite sides of AB, AND
           A and B are on opposite sides of CD — segments intersect.
        """
        def _cross(px, py, qx, qy, rx, ry):
            return (qx-px)*(ry-py) - (qy-py)*(rx-px)
        d1 = _cross(cx, cy, dx, dy, ax, ay)
        d2 = _cross(cx, cy, dx, dy, bx, by)
        d3 = _cross(ax, ay, bx, by, cx, cy)
        d4 = _cross(ax, ay, bx, by, dx, dy)
        if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
           ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
            return True
        return False


    def _on_cut_start(self, event):
        """Shift+RMB pressed — begin cut stroke."""
        if self._generator_edit_blocked():
            return
        self._cut_active = True
        self._cut_stroke = [(event.x, event.y)]
        # Draw initial point
        self.canvas.delete("cut_stroke")


    def _on_cut_drag(self, event):
        """Shift+RMB dragging — extend cut stroke and draw it.
        Throttled: only redraws every 3rd point to reduce canvas load.
        """
        if self._generator_edit_blocked():
            self._cut_active = False
            self._cut_stroke = []
            try:
                self.canvas.delete("cut_stroke")
            except Exception:
                pass
            return
        if not self._cut_active:
            return
        self._cut_stroke.append((event.x, event.y))
        # Throttle: redraw every 3 points only
        if len(self._cut_stroke) % 3 != 0 and len(self._cut_stroke) > 3:
            return
        if len(self._cut_stroke) >= 2:
            pts = [coord for p in self._cut_stroke for coord in p]
            existing = self.canvas.find_withtag("cut_stroke")
            if existing:
                # Update existing line coords — much faster than delete+create
                self.canvas.coords(existing[0], *pts)
            else:
                self.canvas.create_line(
                    *pts,
                    fill="#ff4444", width=2, dash=(4, 3),
                    tags="cut_stroke", capstyle="round")


    def _on_cut_release(self, event):
        """Shift+RMB released — find and cut all intersected edges."""
        if self._generator_edit_blocked():
            self._cut_active = False
            self._cut_stroke = []
            try:
                self.canvas.delete("cut_stroke")
            except Exception:
                pass
            return
        if not self._cut_active:
            return
        self._cut_active = False
        self.canvas.delete("cut_stroke")
        # Flush any pending tkinter events before heavy work
        self.update_idletasks()

        stroke = self._cut_stroke
        self._cut_stroke = []

        if len(stroke) < 2 or not self.nodes:
            return

        # Convert stroke points to world space segments
        stroke_segs = []
        for i in range(len(stroke) - 1):
            ax, ay = self.canvas_to_world(stroke[i][0],   stroke[i][1])
            bx, by = self.canvas_to_world(stroke[i+1][0], stroke[i+1][1])
            stroke_segs.append((ax, ay, bx, by))

        # Build node lookup
        node_by_id = {n.id: n for n in self.nodes}

        # Find all edges that intersect any stroke segment
        cuts = set()  # set of (min_id, max_id) pairs to disconnect
        seen = set()
        for n in self.nodes:
            for nb_id in n.neighbors:
                pair = (min(n.id, nb_id), max(n.id, nb_id))
                if pair in seen:
                    continue
                seen.add(pair)
                nb = node_by_id.get(nb_id)
                if nb is None:
                    continue
                # Check edge n->nb against all stroke segments
                for ax, ay, bx, by in stroke_segs:
                    if self._segments_intersect(
                            n.x, n.y, nb.x, nb.y,
                            ax, ay, bx, by):
                        cuts.add(pair)
                        break

        if not cuts:
            self.status("Cut: no edges intersected")
            return

        # Lightweight undo — only store the cut pairs, not a full snapshot.
        # Reconnecting is just adding back to neighbor lists — no deep copy needed.
        cuts_frozen = frozenset(cuts)
        def _undo_cut(pairs=cuts_frozen, lookup=node_by_id):
            for a_id, b_id in pairs:
                na = lookup.get(a_id) or next((n for n in self.nodes if n.id==a_id), None)
                nb = lookup.get(b_id) or next((n for n in self.nodes if n.id==b_id), None)
                if na and nb:
                    if b_id not in na.neighbors: na.neighbors.append(b_id)
                    if a_id not in nb.neighbors: nb.neighbors.append(a_id)
            self.redraw()
        self._push_undo(f"Cut {len(cuts)} edge(s)", _undo_cut)

        # Disconnect all cut edges
        for a_id, b_id in cuts:
            na = node_by_id.get(a_id)
            nb = node_by_id.get(b_id)
            if na and nb:
                if b_id in na.neighbors: na.neighbors.remove(b_id)
                if a_id in nb.neighbors: nb.neighbors.remove(a_id)

        self.redraw()
        self.status(f"Cut: {len(cuts)} connection(s) removed")


    def _on_edge_brush_start(self, event):
        """Ctrl+RMB pressed — begin manual edge brush stroke."""
        if self._generator_edit_blocked():
            return 'break'
        if self.mode.get() != 'edit':
            return 'break'
        self._edge_brush_active = True
        self._edge_brush_stroke = [(event.x, event.y)]
        self._edge_brush_hits = []
        self.canvas.delete("edge_brush_stroke")
        self._edge_brush_add_hit_at(event.x, event.y)
        self.status("Edge brush: drag through nodes to connect them")
        return 'break'


    def _on_edge_brush_drag(self, event):
        """Ctrl+RMB drag — detect nodes touched by the stroke, in stroke order."""
        if not self._edge_brush_active:
            return 'break'

        last = self._edge_brush_stroke[-1] if self._edge_brush_stroke else (event.x, event.y)
        dx = event.x - last[0]
        dy = event.y - last[1]
        dist = math.hypot(dx, dy)

        # Sample along the stroke so fast mouse motion does not skip small nodes.
        steps = max(1, int(dist / 6.0))
        for step in range(1, steps + 1):
            t = step / steps
            sx = last[0] + dx * t
            sy = last[1] + dy * t
            self._edge_brush_add_hit_at(sx, sy)

        self._edge_brush_stroke.append((event.x, event.y))

        if len(self._edge_brush_stroke) >= 2:
            pts = [coord for p in self._edge_brush_stroke for coord in p]
            existing = self.canvas.find_withtag("edge_brush_stroke")
            if existing:
                self.canvas.coords(existing[0], *pts)
            else:
                self.canvas.create_line(
                    *pts,
                    fill="#4499ff", width=2, dash=(2, 2),
                    tags="edge_brush_stroke", capstyle="round")

        return 'break'


    def _on_edge_brush_release(self, event):
        """Ctrl+RMB released — connect consecutive nodes touched by the stroke."""
        if self._generator_edit_blocked():
            self._edge_brush_active = False
            self._edge_brush_stroke = []
            self._edge_brush_hits = []
            try:
                self.canvas.delete("edge_brush_stroke")
            except Exception:
                pass
            return 'break'
        if not self._edge_brush_active:
            return 'break'

        self._edge_brush_active = False
        self._edge_brush_add_hit_at(event.x, event.y)
        self.canvas.delete("edge_brush_stroke")

        ordered_hits = list(self._edge_brush_hits)
        self._edge_brush_stroke = []
        self._edge_brush_hits = []

        if len(ordered_hits) < 2:
            self.status("Edge brush: not enough nodes touched")
            return 'break'

        node_by_id = {n.id: n for n in self.nodes}
        pairs = []
        seen_pairs = set()
        skipped_full = 0

        for a_id, b_id in zip(ordered_hits, ordered_hits[1:]):
            if a_id == b_id:
                continue
            pair = (min(a_id, b_id), max(a_id, b_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            a = node_by_id.get(a_id)
            b = node_by_id.get(b_id)
            if a is None or b is None:
                continue
            if not self._node_is_interactive(a) or not self._node_is_interactive(b):
                continue
            if b_id in a.neighbors or a_id in b.neighbors:
                continue
            if len(a.neighbors) >= MAX_NEIGHBORS or len(b.neighbors) >= MAX_NEIGHBORS:
                skipped_full += 1
                continue
            pairs.append((a_id, b_id))

        if not pairs:
            suffix = f" ({skipped_full} skipped: full neighbor list)" if skipped_full else ""
            self.status(f"Edge brush: no new connections{suffix}")
            return 'break'

        pairs_frozen = tuple(pairs)

        def _undo_edge_brush(pairs=pairs_frozen):
            lookup = {n.id: n for n in self.nodes}
            for a_id, b_id in pairs:
                a = lookup.get(a_id)
                b = lookup.get(b_id)
                if a and b:
                    if b_id in a.neighbors:
                        a.neighbors.remove(b_id)
                    if a_id in b.neighbors:
                        b.neighbors.remove(a_id)
            self._update_inspector()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='undo_edge_brush')

        self._push_undo(f"Edge brush {len(pairs)} edge(s)", _undo_edge_brush)

        added = 0
        for a_id, b_id in pairs:
            a = node_by_id.get(a_id)
            b = node_by_id.get(b_id)
            if a and b and connect_nodes(a, b):
                added += 1

        self._update_inspector()
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=False, reason='edge_brush')
        suffix = f", {skipped_full} skipped full" if skipped_full else ""
        self.status(f"Edge brush: {added} connection(s) added{suffix}")
        return 'break'


    def _edge_brush_add_hit_at(self, cx, cy, radius=12):
        """Add nearest visible node around a stroke point to ordered edge-brush hits."""
        nid = self._edge_brush_node_at_canvas(cx, cy, radius=radius)
        if nid is None:
            return

        hits = self._edge_brush_hits
        if hits and hits[-1] == nid:
            return

        # If the stroke briefly leaves and re-enters the last edge, do not create
        # immediate A-B-A noise. Deliberate longer revisits are still allowed.
        if len(hits) >= 2 and hits[-2] == nid:
            return

        hits.append(nid)


    def _edge_brush_node_at_canvas(self, cx, cy, radius=12):
        """Nearest node for edge brush, using screen-space pickup and visible filters."""
        if not self.nodes:
            return None
        if getattr(self, 'show_nodes', None) is not None and not self.show_nodes.get():
            return None

        best_id = None
        best_d2 = float(radius * radius)

        zmin = zmax = None
        if getattr(self, 'z_filter_on', None) is not None and self.z_filter_on.get():
            try:
                zmin, zmax = self._get_z_filter_bounds()
            except Exception:
                zmin = zmax = None

        for node in self.nodes:
            if not self._node_is_interactive(node):
                continue
            if zmin is not None and zmax is not None and not (zmin <= node.z <= zmax):
                continue
            sx, sy = self.world_to_canvas(node.x, node.y)
            dx = sx - cx
            dy = sy - cy
            d2 = dx * dx + dy * dy
            if d2 <= best_d2:
                best_d2 = d2
                best_id = node.id
        return best_id


    def _node_at_canvas(self, cx, cy, radius=8):
        """Return node ID of nearest node via HitTester (spatial hash).
        Locked and hidden nodes are excluded from interaction per zone layer state.
        """
        if not self.nodes:
            return None
        self._sync_viewport_obj()
        effective_r_px = max(radius, min(20, radius * (3.0 / max(self.vp_zoom, 0.5))))
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        result = self._hit_tester.nearest_canvas(
            cx, cy, effective_r_px, self._viewport, cw, ch, self.nodes)
        if result is None:
            return None
        # Check zone layer — locked and hidden nodes are non-interactive
        if result < len(self.nodes):
            nid = self.nodes[result].id if hasattr(self.nodes[result], 'id') else result
            b14 = self.nodes[result].b14
            hidden  = getattr(self, '_hidden_zone_ids', set())
            locked  = getattr(self, '_locked_zone_ids', set())
            if b14 in hidden or b14 in locked:
                return None
        return result


    def _node_is_interactive(self, node):
        """Return True if this node can be interacted with (not locked/hidden)."""
        if not self._node_passes_terrain_visibility(node):
            return False
        b14 = getattr(node, 'b14', 0)
        if b14 in getattr(self, '_hidden_zone_ids', set()):
            return False
        if b14 in getattr(self, '_locked_zone_ids', set()):
            return False
        return True


    def _get_editable_node_ids(self):
        """Return the set of node IDs that are currently visible AND interactive."""
        visible_ids = {n.id for n in self._get_visible_nodes()}
        hidden_zones = getattr(self, '_hidden_zone_ids', set())
        locked_zones = getattr(self, '_locked_zone_ids', set())
        return {
            n.id for n in self.nodes
            if n.id in visible_ids
            and getattr(n, 'b14', 0) not in hidden_zones
            and getattr(n, 'b14', 0) not in locked_zones
        }


    def _prune_selection_to_editable(self):
        """Remove non-editable nodes from the current selection."""
        editable = self._get_editable_node_ids()
        pruned = self.selected_nodes & editable
        if pruned == self.selected_nodes:
            return False
        self.selected_nodes = pruned
        if self.selected_id is not None and self.selected_id not in pruned:
            self.selected_id = max(pruned) if pruned else None
        self._update_inspector()
        self._update_stats()
        return True

    # === debug_bar_zone.py (DebugBarZoneMixin) ===

    """Methods for the debug bar, zone system, and tile-based zone generation in AINEditor."""

    # ─── DEBUG BAR ──────────────────────────────────────────────────────────


    def _toggle_zone_panel(self):
        self._zone_panel_visible.set(not bool(self._zone_panel_visible.get()))
        self._update_zone_panel_visibility()


    def _hide_zone_panel(self):
        self._zone_panel_visible.set(False)
        self._update_zone_panel_visibility()


    def _update_zone_panel_visibility(self):
        if not hasattr(self, '_zone_panel'):
            return
        show = (self.mode.get() == 'zone') and bool(self._zone_panel_visible.get())
        if show:
            self._zone_panel.grid(row=0, column=2, sticky='ns')
        else:
            self._zone_panel.grid_remove()
        if hasattr(self, '_zone_panel_toggle_btn'):
            self._zone_panel_toggle_btn.configure(
                text=("Hide Zone Panel" if show else "Show Zone Panel")
            )
        self._sync_topbar_theme()


    def _sync_zone_generation_controls(self):
        """b12/b15 zone values are only used by the collision-ignoring fill path."""
        active = bool(getattr(self, '_zone_ignore_var', tk.BooleanVar(value=False)).get())
        state = 'normal' if active else 'disabled'
        try:
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        except Exception:
            light = False

        if light:
            label_active = '#1c1c1c'
            label_dim = '#6b6b6b'
            entry_bg = '#fcfcfc'
            entry_disabled_bg = '#e9e9e9'
            entry_fg = '#1c1c1c'
            entry_disabled_fg = '#777777'
        else:
            label_active = C_TEXT
            label_dim = C_DIM
            entry_bg = C_PANEL2
            entry_disabled_bg = C_PANEL2
            entry_fg = C_TEXT
            entry_disabled_fg = C_DIM

        for lbl in (getattr(self, '_zone_b12_label', None),
                    getattr(self, '_zone_b15_label', None)):
            try:
                lbl.configure(fg=(label_active if active else label_dim))
            except Exception:
                pass

        for ent in (getattr(self, '_zone_b12_entry', None),
                    getattr(self, '_zone_b15_entry', None)):
            try:
                ent.configure(
                    state=state,
                    bg=(entry_bg if active else entry_disabled_bg),
                    fg=entry_fg,
                    insertbackground=entry_fg,
                    disabledbackground=entry_disabled_bg,
                    disabledforeground=entry_disabled_fg)
            except Exception:
                try:
                    ent.configure(state=state)
                except Exception:
                    pass


    def _on_zone_ignore_toggle(self):
        self._sync_zone_generation_controls()
        self._apply_zone_panel_fields()


    def _choose_zone_color(self):
        cur = self._valid_hex_color(getattr(self, '_zone_color_var', tk.StringVar(value='#00ffaa')).get())
        chosen = colorchooser.askcolor(color=cur, parent=self, title="Choose zone color")
        if chosen and chosen[1]:
            self._zone_color_var.set(chosen[1].lower())
            self._apply_zone_panel_fields()


    def _apply_zone_panel_fields(self):
        anum = self._zone_selected
        if anum is None or anum not in self.zones:
            return
        meta = self._ensure_zone_meta(anum)
        meta['name'] = (self._zone_name_var.get().strip() if hasattr(self, '_zone_name_var') else '') or f'Zone {anum}'
        meta['color'] = self._valid_hex_color(self._zone_color_var.get() if hasattr(self, '_zone_color_var') else '#00ffaa')
        try:
            meta['b12'] = max(0, min(255, int(float(self._zone_b12_var.get()))))
        except Exception:
            meta['b12'] = 0
            self._zone_b12_var.set('0')
        try:
            meta['b15'] = max(1, min(255, int(float(self._zone_b15_var.get()))))
        except Exception:
            meta['b15'] = 36
            self._zone_b15_var.set('36')
        meta['ignore_collisions'] = bool(self._zone_ignore_var.get()) if hasattr(self, '_zone_ignore_var') else False
        try:
            self._zone_color_preview.configure(bg=meta['color'])
        except Exception:
            pass
        self._refresh_zone_list()
        self._redraw_zone_overlays()


    def _sync_zone_panel_from_selected(self):
        if not hasattr(self, '_zone_name_var'):
            return
        anum = self._zone_selected
        if anum is None or anum not in self.zones:
            self._zone_name_var.set('')
            self._zone_color_var.set('#00ffaa')
            self._zone_b12_var.set('0')
            self._zone_b15_var.set('36')
            self._zone_ignore_var.set(False)
            try:
                self._zone_color_preview.configure(bg='#00ffaa')
            except Exception:
                pass
            self._sync_zone_generation_controls()
            return
        meta = self._ensure_zone_meta(anum)
        self._zone_name_var.set(str(meta.get('name') or f'Zone {anum}'))
        self._zone_color_var.set(self._valid_hex_color(meta.get('color'), '#00ffaa'))
        self._zone_b12_var.set(str(meta.get('b12', 0)))
        self._zone_b15_var.set(str(meta.get('b15', 36)))
        self._zone_ignore_var.set(bool(meta.get('ignore_collisions', False)))
        try:
            self._zone_color_preview.configure(bg=self._zone_color_var.get())
        except Exception:
            pass
        self._sync_zone_generation_controls()
        try:
            self._zone_listbox.selection_clear(0, 'end')
            keys = sorted(self.zones.keys())
            if anum in keys:
                self._zone_listbox.selection_set(keys.index(anum))
                self._zone_listbox.see(keys.index(anum))
        except Exception:
            pass


    def _refresh_zone_list(self):
        if not hasattr(self, '_zone_listbox'):
            return
        if not hasattr(self, '_zone_list_expanded'):
            self._zone_list_expanded = set()
        lb = self._zone_listbox
        cur = self._zone_selected
        lb.delete(0, 'end')
        self._zone_list_items = []
        for anum in sorted(self.zones.keys()):
            meta = self._ensure_zone_meta(anum)
            n_tiles = len(meta.get('tiles') or [])
            n_owned = len(meta.get('nodes') or [])
            name = meta.get('name') or f'Zone {anum}'
            expanded = anum in self._zone_list_expanded
            arrow = '▼' if expanded else '▶'
            lb.insert('end', f"{arrow} {name}  [{n_tiles} tiles, {n_owned} nodes]")
            self._zone_list_items.append(('zone', anum, None))
            if expanded:
                for nid in sorted(meta.get('nodes') or []):
                    if 0 <= int(nid) < len(self.nodes):
                        n = self.nodes[int(nid)]
                        lb.insert('end', f"    Node {nid}: ({n.x:.1f}, {n.y:.1f}, {n.z:.1f})")
                    else:
                        lb.insert('end', f"    Node {nid}: missing")
                    self._zone_list_items.append(('node', anum, int(nid)))
        if cur in self.zones:
            try:
                idx = next(i for i, it in enumerate(self._zone_list_items) if it[0] == 'zone' and it[1] == cur)
                lb.selection_clear(0, 'end')
                lb.selection_set(idx)
            except Exception:
                pass


    def _on_zone_list_select(self, event=None):
        if not hasattr(self, '_zone_listbox'):
            return
        sel = self._zone_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if not (0 <= idx < len(getattr(self, '_zone_list_items', []))):
            return
        kind, anum, payload = self._zone_list_items[idx]
        if kind == 'zone':
            if not hasattr(self, '_zone_list_expanded'):
                self._zone_list_expanded = set()
            if anum in self._zone_list_expanded:
                self._zone_list_expanded.discard(anum)
            else:
                self._zone_list_expanded.add(anum)
        self._zone_selected = anum
        self._load_zone_tiles()
        if kind == 'node' and payload is not None and 0 <= int(payload) < len(self.nodes):
            nid = int(payload)
            if self._node_is_interactive(self.nodes[nid]):
                self.selected_id = nid
                self.selected_nodes = {nid}
                self._update_inspector()
        self._sync_zone_panel_from_selected()
        self._refresh_zone_list()
        self._redraw_zone_overlays()
        self.redraw()
        if kind == 'node':
            self.refresh_3d_views(rebuild=True, refit=False, reason='zone_selection')
        self.status(f"Selected Zone {anum}" + (f" {kind} {payload}" if kind != 'zone' else ""))


    def _on_zone_list_click(self, event=None):
        if event is None or not hasattr(self, '_zone_listbox'):
            return
        lb = self._zone_listbox
        idx = lb.nearest(event.y)
        if not (0 <= idx < len(getattr(self, '_zone_list_items', []))):
            return
        bbox = lb.bbox(idx)
        if bbox is None:
            return
        _x, y, _w, h = bbox
        if not (y <= event.y <= y + h):
            return
        kind, _anum, _payload = self._zone_list_items[idx]
        if kind not in ('point', 'node'):
            return
        text = lb.get(idx).rstrip()
        if not text.endswith('(x)'):
            return
        if event.x < max(0, lb.winfo_width() - 36):
            return
        lb.selection_clear(0, 'end')
        lb.selection_set(idx)
        lb.activate(idx)
        self._delete_selected_zone_list_item()
        return 'break'


    def _on_zone_list_delete_key(self, event=None):
        self._delete_selected_zone_list_item()
        return 'break'


    def _delete_selected_zone_list_item(self):
        if not hasattr(self, '_zone_listbox'):
            return
        sel = self._zone_listbox.curselection()
        if not sel:
            self.status("No zone list item selected")
            return
        idx = sel[0]
        if not (0 <= idx < len(getattr(self, '_zone_list_items', []))):
            return
        kind, anum, payload = self._zone_list_items[idx]
        if anum not in self.zones:
            return
        if kind == 'zone':
            self._delete_selected_zone()
            return
        snap = self._zone_snapshot()
        meta = self._ensure_zone_meta(anum)
        if kind == 'point':
            pts = list(meta.get('points') or self._zone_points(anum))
            if len(pts) <= 2:
                messagebox.showinfo("Area Point", "A zone needs at least two points.", parent=self)
                return
            try:
                pts.pop(int(payload))
            except Exception:
                return
            meta['points'] = pts
            self._update_zone_rect_from_points(anum)
            self._push_undo(f'Delete point from zone {anum}', lambda s=snap: self._restore_zone_snapshot(s))
        elif kind == 'node':
            nid = int(payload)
            if nid not in set(meta.get('nodes') or []):
                return
            self._delete_nodes_by_ids({nid})
            self._push_undo(f'Delete node {nid} from zone {anum}', lambda s=snap: self._restore_zone_snapshot(s))
        self._mark_zones_dirty()
        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self.redraw()
        self.refresh_3d_views(rebuild=True, refit=False, reason='delete_node_from_zone')
        self.status(f"Deleted {kind} from Zone {anum}")


    def _redraw_zone_overlays(self):
        """Draw all zones as Tk canvas polygons/rectangles on top of PIL image."""
        self.canvas.delete('zone_overlay')
        if str(self.mode.get()) != 'zone':
            return
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        vp = self._viewport

        self._draw_tile_overlays(cw, ch, vp)

        for anum, coords in self.zones.items():
            x1, y1, x2, y2 = coords
            if x1 == x2 and y1 == y2:
                continue
            col = self._zone_color(anum)
            cx1, cy1 = vp.world_to_canvas(x1, y1, cw, ch)
            cx2, cy2 = vp.world_to_canvas(x2, y2, cw, ch)
            meta = self._ensure_zone_meta(anum)
            label = meta.get('name') or f"Zone {anum}"
            self.canvas.create_text(
                (min(cx1,cx2) + max(cx1,cx2)) / 2,
                (min(cy1,cy2) + max(cy1,cy2)) / 2,
                text=label,
                fill=col, font=('Arial',9,'bold'),
                tags='zone_overlay')


    def _point_in_poly(self, x, y, pts):
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if ((yi > y) != (yj > y)):
                xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
                if x < xint:
                    inside = not inside
            j = i
        return inside


    def _zone_at_canvas(self, cx, cy):
        """Return area_num of zone containing canvas point, or None."""
        wx, wy = self.canvas_to_world(cx, cy)
        for anum in reversed(sorted(self.zones.keys())):
            pts = self._zone_points(anum)
            if len(pts) >= 3:
                if self._point_in_poly(wx, wy, pts):
                    return anum
            else:
                zx1,zy1,zx2,zy2 = self.zones[anum]
                if min(zx1,zx2)<=wx<=max(zx1,zx2) and min(zy1,zy2)<=wy<=max(zy1,zy2):
                    return anum
        return None


    def _clear_zone_nodes_internal(self, anum):
        if anum not in self.zones:
            return 0
        meta = self._ensure_zone_meta(anum)
        owned = set(meta.get('nodes') or [])
        deleted = self._delete_nodes_by_ids(owned)
        meta = self._ensure_zone_meta(anum)
        meta['nodes'] = set()
        return deleted


    def clear_selected_zone_nodes(self):
        anum = self._zone_selected
        if anum is None or anum not in self.zones:
            messagebox.showinfo("No Zone Selected", "Select a zone first.", parent=self)
            return
        meta = self._ensure_zone_meta(anum)
        count = len(meta.get('nodes') or [])
        if count <= 0:
            self.status(f"Zone {anum} has no owned nodes to clear")
            return
        if not messagebox.askyesno("Clear Zone Nodes",
                                   f"Delete {count} node(s) owned by Zone {anum}?",
                                   parent=self):
            return
        snap = self._zone_snapshot()
        deleted = self._clear_zone_nodes_internal(anum)
        self._push_undo(f'Clear nodes in zone {anum}', lambda s=snap: self._restore_zone_snapshot(s))
        self._mark_zones_dirty()
        self._rebuild_hit_tester()
        self._update_stats()
        self.redraw()
        self.status(f"Cleared {deleted} node(s) from Zone {anum}")


    def _get_selected_zone_key(self):
        """Return currently selected zone key or None."""
        if hasattr(self, '_zone_selected') and self._zone_selected is not None:
            if self._zone_selected in (self.zones or {}):
                return self._zone_selected
        sel = self._zone_listbox.curselection() if hasattr(self, '_zone_listbox') else ()
        if sel:
            keys = list((self.zones or {}).keys())
            if sel[0] < len(keys):
                return keys[sel[0]]
        return None


    def _delete_selected_zone(self):
        anum = self._zone_selected
        if anum is None or anum not in self.zones:
            self.status("No selected zone")
            return

        meta = self._ensure_zone_meta(anum)
        owned_count = len(meta.get('nodes') or [])
        delete_nodes = False
        if owned_count:
            delete_nodes = messagebox.askyesno(
                "Delete Zone",
                f"Zone {anum} owns {owned_count} node(s). Delete those nodes too?\n\n"
                f"Yes = delete zone and its generated nodes\n"
                f"No = delete zone but keep nodes as normal/global nodes",
                parent=self)
        else:
            if not messagebox.askyesno("Delete Zone", f"Delete Zone {anum}?", parent=self):
                return

        snap = self._zone_snapshot()
        if delete_nodes:
            self._clear_zone_nodes_internal(anum)
        self.zones.pop(anum, None)
        self.zone_meta.pop(anum, None)
        self._zone_selected = None
        self._tile_selected = set()
        self._push_undo(f'Delete zone {anum}', lambda s=snap: self._restore_zone_snapshot(s))
        self._mark_zones_dirty()
        self._redraw_zone_overlays()
        self._rebuild_hit_tester()
        self._update_stats()
        self.redraw()
        self.status(f"Zone {anum} deleted" + (" with nodes" if delete_nodes else ""))


    # ── Grid Tile Selection ────────────────────────────────────────────────

    TILE_GRID_SIZE = 32.0

    def _current_grid_spacing(self):
        label = str(self.grid_fixed_spacing_label.get() or "32 m")
        return float(MED_GRID_SPACING_BY_LABEL.get(label, 32.0))

    def _next_zone_number(self):
        return (max(self.zones.keys(), default=0) + 1) if self.zones else 1

    def _enter_zone_mode(self):
        if not self.zones:
            self._add_new_zone()
        if not self._tile_select_active:
            self._enter_tile_select()
        self._load_zone_tiles()
        self._refresh_zone_list()

    def _add_new_zone(self):
        anum = self._next_zone_number()
        self.zones[anum] = [0.0, 0.0, 0.0, 0.0]
        meta = self._ensure_zone_meta(anum)
        meta['name'] = f"Zone {anum}"
        meta['color'] = self._zone_color_var.get()
        meta['tiles'] = set()
        self._zone_selected = anum
        self._tile_selected = meta['tiles']
        self._mark_zones_dirty()
        self._refresh_zone_list()
        self._sync_zone_panel_from_selected()
        self._redraw_zone_overlays()
        self.status(f"Created Zone {anum}")

    def _load_zone_tiles(self):
        anum = self._zone_selected
        if anum is not None and anum in self.zones:
            meta = self._ensure_zone_meta(anum)
            if 'tiles' not in meta:
                meta['tiles'] = set()
            self._tile_selected = meta['tiles']
        else:
            self._tile_selected = set()
        self._redraw_zone_overlays()

    def _enter_tile_select(self):
        self._tile_select_active = True
        self.mode.set('zone')
        gs = self._current_grid_spacing()
        self.status(f"Grid Select: LMB paint, RMB erase (current grid {gs:.4g}m)")
        self._redraw_zone_overlays()

    def _exit_tile_select(self):
        self._tile_select_active = False
        self._tile_painting = False
        self._tile_erasing = False
        self._redraw_zone_overlays()

    def _clear_tile_selection(self):
        self._tile_selected.clear()
        self._update_zone_rect_from_tiles()
        self._redraw_zone_overlays()
        self.status("Tile selection cleared")

    def _all_zone_tiles(self):
        all_tiles = set()
        for anum in self.zones:
            meta = self._ensure_zone_meta(anum)
            tiles = meta.get('tiles')
            if tiles:
                all_tiles.update(tiles)
        return all_tiles

    def _tile_overlaps_any(self, tx, ty, ts):
        for (ex, ey, es) in self._all_zone_tiles():
            if tx < ex + es and tx + ts > ex and ty < ey + es and ty + ts > ey:
                return True
        return False

    def _find_fitting_tile(self, wx, wy, start_size):
        import math
        size = start_size
        min_size = 1.0
        while size >= min_size:
            sx = math.floor(wx / size) * size
            sy = math.floor(wy / size) * size
            if not self._tile_overlaps_any(sx, sy, size):
                return (sx, sy, size)
            size /= 2.0
        return None

    def _tile_at_point(self, wx, wy):
        for tile in self._tile_selected:
            tx, ty, ts = tile
            if tx <= wx < tx + ts and ty <= wy < ty + ts:
                return tile
        return None

    def _update_zone_rect_from_tiles(self):
        anum = self._zone_selected
        if anum is None or anum not in self.zones:
            return
        tiles = self._tile_selected
        if not tiles:
            self.zones[anum] = [0.0, 0.0, 0.0, 0.0]
            return
        xs = [tx for tx, ty, ts in tiles] + [tx + ts for tx, ty, ts in tiles]
        ys = [ty for tx, ty, ts in tiles] + [ty + ts for tx, ty, ts in tiles]
        self.zones[anum] = [min(xs), min(ys), max(xs), max(ys)]

    def _on_tile_press(self, event, erase=False):
        if not self._tile_select_active:
            return False
        if self._zone_selected is None or self._zone_selected not in self.zones:
            self.status("Select or add a zone first")
            return True
        wx, wy = self.canvas_to_world(event.x, event.y)
        if erase:
            self._tile_erasing = True
            hit = self._tile_at_point(wx, wy)
            if hit:
                self._tile_selected.discard(hit)
        else:
            self._tile_painting = True
            gs = self._current_grid_spacing()
            fit = self._find_fitting_tile(wx, wy, gs)
            if fit:
                self._tile_selected.add(fit)
        self._redraw_zone_overlays()
        return True

    def _on_tile_drag(self, event, erase=False):
        if not self._tile_select_active:
            return False
        if not (self._tile_painting or self._tile_erasing):
            return False
        wx, wy = self.canvas_to_world(event.x, event.y)
        if self._tile_erasing:
            hit = self._tile_at_point(wx, wy)
            if hit:
                self._tile_selected.discard(hit)
        else:
            gs = self._current_grid_spacing()
            fit = self._find_fitting_tile(wx, wy, gs)
            if fit:
                self._tile_selected.add(fit)
        self._redraw_zone_overlays()
        return True

    def _on_tile_release(self, event):
        self._tile_painting = False
        self._tile_erasing = False
        self._update_zone_rect_from_tiles()
        self._mark_zones_dirty()

    def _draw_tile_overlays(self, cw, ch, vp):
        for anum in sorted(self.zones.keys()):
            meta = self._ensure_zone_meta(anum)
            tiles = meta.get('tiles')
            if not tiles:
                continue
            col = self._valid_hex_color(meta.get('color'), '#00ffaa')
            is_sel = (anum == self._zone_selected)
            stipple = 'gray25' if is_sel else 'gray12'
            try:
                cr = int(col[1:3], 16)
                cg = int(col[3:5], 16)
                cb = int(col[5:7], 16)
                fill_col = f'#{cr:02x}{cg:02x}{cb:02x}'
            except Exception:
                fill_col = '#00ffaa'
            for (tx, ty, ts) in tiles:
                cx1, cy1 = vp.world_to_canvas(tx, ty, cw, ch)
                cx2, cy2 = vp.world_to_canvas(tx + ts, ty + ts, cw, ch)
                rx1, rx2 = min(cx1, cx2), max(cx1, cx2)
                ry1, ry2 = min(cy1, cy2), max(cy1, cy2)
                self.canvas.create_rectangle(
                    rx1, ry1, rx2, ry2,
                    fill=fill_col, outline=fill_col,
                    stipple=stipple, width=1,
                    tags='zone_overlay')

    def _clear_all_zones(self):
        """Remove all zones, optionally deleting nodes owned by zones."""
        if not self.zones:
            self.status("No zones to clear")
            return
        owned = set()
        for meta in self.zone_meta.values():
            owned.update(meta.get('nodes') or [])
        delete_nodes = False
        if owned:
            delete_nodes = messagebox.askyesno(
                "Delete All Zones",
                f"Delete all {len(self.zones)} zone(s) and {len(owned)} owned node(s)?\n\n"
                f"Yes = delete zones and generated zone nodes\n"
                f"No = delete zones only, keep nodes",
                parent=self)
        else:
            if not messagebox.askyesno("Delete All Zones", f"Delete all {len(self.zones)} zone(s)?", parent=self):
                return

        snap = self._zone_snapshot()
        if delete_nodes:
            self._delete_nodes_by_ids(owned)
        self.zones = {}
        self.zone_meta = {}
        self._zone_selected = None
        self._tile_selected = set()
        self._zone_drawing = None
        self._zone_drag = None
        self._push_undo('Delete all zones', lambda s=snap: self._restore_zone_snapshot(s))
        self._mark_zones_dirty()
        self._redraw_zone_overlays()
        self._rebuild_hit_tester()
        self._update_stats()
        self.redraw()
        self.status("All zones deleted" + (" with owned nodes" if delete_nodes else ""))


    def _zone_generation_options(self):
        """Return options from the Zone Panel for selected zone."""
        anum = self._zone_selected
        if anum is None or anum not in self.zones:
            return None
        self._apply_zone_panel_fields()
        meta = self._ensure_zone_meta(anum)
        return {
            'b12': int(meta.get('b12', 0)),
            'b15': int(meta.get('b15', 36)),
            'spacing': self._default_spacing_for_b15(meta.get('b15', 36)),
            'ignore_collisions': bool(meta.get('ignore_collisions', False)),
        }


    def generate_selected_zone_nodes(self):
        """Populate selected zone with owned nodes."""
        if self._zone_selected is not None and self._zone_selected in self.zones:
            self._update_zone_rect_from_tiles()
        if self._zone_selected is None or self._zone_selected not in self.zones:
            messagebox.showinfo("No Zone Selected", "Select/create an area zone first.", parent=self)
            return
        opts = self._zone_generation_options()
        if not opts:
            return

        anum = self._zone_selected
        if not opts.get('ignore_collisions', False):
            meta = self._ensure_zone_meta(anum)
            if meta.get('tiles'):
                gen_opts = self._ask_generator_options(preselect_zone=True)
                if gen_opts is None:
                    return
                _radius, z_filter, rooftops, quantized = gen_opts[:4]
                picked_zone = gen_opts[4] if len(gen_opts) > 4 else None
                ladders = gen_opts[5] if len(gen_opts) > 5 else False
                highest_rooftops = gen_opts[6] if len(gen_opts) > 6 else False
                ignore_destroyable = gen_opts[7] if len(gen_opts) > 7 else True
                ignore_vehicles = gen_opts[8] if len(gen_opts) > 8 else False
                self._seed_generate_z_filter = bool(z_filter)
                self._seed_generate_rooftops = bool(z_filter and rooftops)
                self._seed_generate_highest_rooftops = bool(
                    z_filter and rooftops and highest_rooftops)
                self._seed_generate_ladders = bool(
                    z_filter and rooftops and ladders)
                self._seed_generate_quantized = bool(quantized)
                self._seed_ignore_destroyable = bool(ignore_destroyable)
                self._seed_ignore_vehicles = bool(ignore_vehicles)
                if picked_zone is not None and picked_zone in self.zones:
                    anum = picked_zone
                    self._zone_selected = anum
                    self._sync_zone_panel_from_selected()
                    self._load_zone_tiles()
                    self._update_zone_rect_from_tiles()
                    meta_picked = self._ensure_zone_meta(anum)
                    opts = {
                        'b12': int(meta_picked.get('b12', 0)),
                        'b15': int(meta_picked.get('b15', 36)),
                        'spacing': self._default_spacing_for_b15(meta_picked.get('b15', 36)),
                        'ignore_collisions': bool(meta_picked.get('ignore_collisions', False)),
                    }
                self._generate_tile_grid_collision_aware(anum, opts)
                return
            self._generate_selected_zone_nodes_spatial(anum, opts)
            return

        meta = self._ensure_zone_meta(anum)
        existing = len(meta.get('nodes') or [])
        if existing:
            if not messagebox.askyesno(
                "Regenerate Zone",
                f"Zone {anum} already owns {existing} node(s).\n\n"
                f"Clear and regenerate this zone only?",
                parent=self):
                return

        snap = self._zone_snapshot()
        self._clear_zone_nodes_internal(anum)

        x1, y1, x2, y2 = self.zones[anum]
        xmin, xmax = sorted((x1, x2))
        ymin, ymax = sorted((y1, y2))
        spacing = float(opts['spacing'])
        b12 = int(opts['b12'])
        b15 = int(opts['b15'])

        z_ref = build_ground_z_ref(self.entities, self.terrain_z) if self.entities else []
        _t_info = getattr(self, 'terrain_info', None)
        created = {}
        new_ids = []
        row = 0
        y = ymin
        eps = 1e-6

        meta_tiles = self._ensure_zone_meta(anum).get('tiles')
        while y <= ymax + eps:
            col = 0
            x = xmin
            while x <= xmax + eps:
                if meta_tiles:
                    _in_tile = False
                    for (tx, ty, ts) in meta_tiles:
                        if tx <= x < tx + ts and ty <= y < ty + ts:
                            _in_tile = True
                            break
                    if not _in_tile:
                        x += spacing
                        col += 1
                        continue
                else:
                    pts = self._zone_points(anum)
                    if len(pts) >= 3 and not self._point_in_poly(x, y, pts):
                        x += spacing
                        col += 1
                        continue
                z = _gen_node_z(x, y, _t_info, z_ref, self.terrain_z)

                nid = len(self.nodes)
                node = Node(nid, x, y, z, b12=b12, b15=b15)
                self.nodes.append(node)
                created[(row, col)] = nid
                new_ids.append(nid)
                x += spacing
                col += 1
            y += spacing
            row += 1

        neighbor_steps = ((1, 0), (0, 1), (1, 1), (1, -1))
        for (row, col), a in created.items():
            for dr, dc in neighbor_steps:
                b = created.get((row + dr, col + dc))
                if b is None:
                    continue
                na = self.nodes[a]
                nb = self.nodes[b]
                if b not in na.neighbors and len(na.neighbors) < MAX_NEIGHBORS:
                    na.neighbors.append(b)
                if a not in nb.neighbors and len(nb.neighbors) < MAX_NEIGHBORS:
                    nb.neighbors.append(a)

        self.next_id = len(self.nodes)
        meta = self._ensure_zone_meta(anum)
        meta['nodes'] = set(new_ids)
        self._push_undo(
            f'Generate zone {anum} ({len(new_ids)} nodes)',
            lambda s=snap: self._restore_zone_snapshot(s)
        )
        self._mark_zones_dirty()
        self._pil_renderer.clear() if self._pil_renderer else None
        self._rebuild_hit_tester()
        self._update_inspector()
        self._update_stats()
        self.redraw()
        self.status(
            f"Generated {len(new_ids)} owned node(s) in Zone {anum} "
            f"(b12={b12}, b15={b15}, spacing={spacing:.2f}m, ignores collision)"
        )


    # Match the ordinary clicked-seed contract.  Arbitrary zone size is handled
    # by the job lattice, not by silently widening the generator's radius cap.
    TILE_DISC_RADIUS_CAP = 80.0
    TILE_LOCAL_GENERATION_SPAN = 64.0
    TILE_LOCAL_GENERATION_MARGIN = 6.0

    @staticmethod
    def _cluster_tiles(tile_set):
        """Group tiles into clusters of edge-touching tiles (multi-size aware)."""
        remaining = list(tile_set)
        clusters = []
        while remaining:
            seed = remaining.pop()
            cluster = [seed]
            visited = {seed}
            queue = [seed]
            while queue:
                a = queue.pop()
                ax, ay, a_s = a
                still = []
                for b in remaining:
                    if b in visited:
                        continue
                    bx, by, bs = b
                    touch_x = (abs(ax - (bx + bs)) < 0.01 or abs((ax + a_s) - bx) < 0.01) and ay < by + bs - 0.01 and ay + a_s > by + 0.01
                    touch_y = (abs(ay - (by + bs)) < 0.01 or abs((ay + a_s) - by) < 0.01) and ax < bx + bs - 0.01 and ax + a_s > bx + 0.01
                    if touch_x or touch_y:
                        visited.add(b)
                        cluster.append(b)
                        queue.append(b)
                    else:
                        still.append(b)
                remaining = still
            clusters.append(set(cluster))
        return clusters

    @classmethod
    def _tile_local_generation_jobs(cls, tile_set):
        """Split tile zones into map-anchored ordinary-seed transactions.

        The job lattice is independent of the selected zone's outer bounds.
        Overlap belongs to generation context only: every final point is owned
        by exactly one non-overlapping core rectangle.
        """
        import math as _math

        span = float(cls.TILE_LOCAL_GENERATION_SPAN)
        margin = float(cls.TILE_LOCAL_GENERATION_MARGIN)
        jobs = []
        clusters = cls._cluster_tiles(tile_set)
        clusters.sort(key=lambda c: (
            min(float(t[1]) for t in c),
            min(float(t[0]) for t in c)))

        for cluster_id, cluster in enumerate(clusters):
            tiles = tuple(sorted(cluster, key=lambda t: (t[1], t[0], t[2])))
            xmin = min(float(tx) for tx, _ty, _ts in tiles)
            ymin = min(float(ty) for _tx, ty, _ts in tiles)
            xmax = max(float(tx + ts) for tx, _ty, ts in tiles)
            ymax = max(float(ty + ts) for _tx, ty, ts in tiles)

            core_x1 = _math.floor(xmin / span) * span
            while core_x1 < xmax - 1e-6:
                core_x2 = core_x1 + span
                core_y1 = _math.floor(ymin / span) * span
                while core_y1 < ymax - 1e-6:
                    core_y2 = core_y1 + span
                    touched = []
                    for tx, ty, ts in tiles:
                        tx2 = tx + ts
                        ty2 = ty + ts
                        if (tx < core_x2 and tx2 > core_x1
                                and ty < core_y2 and ty2 > core_y1):
                            touched.append((tx, ty, ts))
                    if touched:
                        covered_x1 = min(max(float(tx), core_x1)
                                         for tx, _ty, _ts in touched)
                        covered_y1 = min(max(float(ty), core_y1)
                                         for _tx, ty, _ts in touched)
                        covered_x2 = max(min(float(tx + ts), core_x2)
                                         for tx, _ty, ts in touched)
                        covered_y2 = max(min(float(ty + ts), core_y2)
                                         for _tx, ty, ts in touched)
                        cx = (covered_x1 + covered_x2) * 0.5
                        cy = (covered_y1 + covered_y2) * 0.5
                        radius = _math.hypot(
                            covered_x2 - covered_x1,
                            covered_y2 - covered_y1) * 0.5 + margin
                        jobs.append({
                            'cluster_id': int(cluster_id),
                            'tiles': tiles,
                            'core': (core_x1, core_y1, core_x2, core_y2),
                            'center': (cx, cy),
                            'radius': min(
                                float(cls.TILE_DISC_RADIUS_CAP),
                                max(8.0, float(radius))),
                        })
                    core_y1 = core_y2
                core_x1 = core_x2
        jobs.sort(key=lambda job: (
            int(job['cluster_id']),
            float(job['core'][1]),
            float(job['core'][0])))
        return jobs

    @staticmethod
    def _point_in_zone_tiles(x, y, tiles):
        fx = float(x)
        fy = float(y)
        for tx, ty, ts in tiles:
            if tx <= fx < tx + ts and ty <= fy < ty + ts:
                return True
        return False

    def _generate_tile_grid_collision_aware(self, anum, opts):
        """Generate an arbitrary tile zone without blocking Tk's UI thread."""
        import math as _math
        import queue
        import time as _time

        if not self.bms_path:
            messagebox.showinfo('Generate Zone', 'Open a BMS map first.', parent=self)
            return
        if getattr(self, '_generator_running', False):
            self.status('Generate Zone: another generator pass is already running.')
            return

        meta = self._ensure_zone_meta(anum)
        tile_set = set(meta.get('tiles') or ())
        if not tile_set:
            self._generate_selected_zone_nodes_spatial(anum, opts)
            return

        existing = len(meta.get('nodes') or [])
        if existing and not messagebox.askyesno(
                'Regenerate Zone',
                f'Zone {anum} already owns {existing} node(s).\n\n'
                f'Clear and regenerate this zone only?',
                parent=self):
            return

        jobs = self._tile_local_generation_jobs(tile_set)
        if not jobs:
            messagebox.showwarning(
                'Generate Zone', 'The selected tiles contain no generation area.',
                parent=self)
            return

        all_xs = ([float(tx) for tx, _ty, _ts in tile_set]
                  + [float(tx + ts) for tx, _ty, ts in tile_set])
        all_ys = ([float(ty) for _tx, ty, _ts in tile_set]
                  + [float(ty + ts) for _tx, ty, ts in tile_set])
        global_cx = (min(all_xs) + max(all_xs)) * 0.5
        global_cy = (min(all_ys) + max(all_ys)) * 0.5
        global_radius = max(
            _math.hypot(x - global_cx, y - global_cy)
            for x in (min(all_xs), max(all_xs))
            for y in (min(all_ys), max(all_ys))) + 4.0

        # Regeneration removes the old zone batch before placement.  Preserve
        # the complete editor state for failure/undo, then give the worker a
        # deep graph snapshot: seam stitching mutates reciprocal old links.
        zone_snapshot = self._zone_snapshot()
        self._clear_zone_nodes_internal(anum)
        existing_nodes_snapshot = self._snapshot_nodes()
        existing_count = len(existing_nodes_snapshot)
        immutable_existing_nodes = tuple(existing_nodes_snapshot)
        entities_snapshot = list(self.entities or [])
        terrain_info_snapshot = getattr(self, 'terrain_info', None)
        terrain_z_snapshot = float(getattr(self, 'terrain_z', 18.5) or 18.5)
        z_filter_enabled = bool(getattr(self, '_seed_generate_z_filter', True))
        rooftops_enabled = bool(
            z_filter_enabled and getattr(self, '_seed_generate_rooftops', False))
        highest_rooftops_enabled = bool(
            rooftops_enabled
            and getattr(self, '_seed_generate_highest_rooftops', False))
        ladders_enabled = bool(
            z_filter_enabled and rooftops_enabled
            and getattr(self, '_seed_generate_ladders', False))
        quantized_enabled = bool(getattr(self, '_seed_generate_quantized', False))
        ignore_destroyable = bool(getattr(
            self, '_seed_ignore_destroyable', True))
        ignore_vehicles = bool(getattr(
            self, '_seed_ignore_vehicles', False))
        cluster_count = len(self._cluster_tiles(tile_set))

        progress_queue = queue.Queue()
        self._generator_progress_queue = progress_queue
        self._generator_running = True
        self._generator_cancelled = False
        self._generator_progress_failed = False
        self._generator_progress_result_applied = False
        self._generator_finish_detail = None
        self._generator_zone_snapshot = zone_snapshot
        self.status(
            f'Generate Zone {anum}: started {len(jobs)} bounded local '
            f'transaction(s) for {len(tile_set)} tiles...')
        self._show_generator_progress_window(global_cx, global_cy, global_radius)
        bms_path_snapshot = os.path.abspath(self.bms_path)

        def _worker():
            import traceback as _traceback
            _configure_wt = _ns.get('_configure_generator_worker_thread', None)
            if _configure_wt:
                _configure_wt()
            t0 = _time.perf_counter()
            try:
                progress_queue.put((
                    'phase', 1.0, 'Preparing isolated zone request',
                    'Worker owns map, CModel, terrain, and collision caches'))
                process_job = {
                    'job_kind': 'tile_zone',
                    'bms_path': bms_path_snapshot,
                    'tile_jobs': jobs,
                    'existing_nodes': [node.to_dict()
                                       for node in existing_nodes_snapshot],
                    'z_filter_enabled': z_filter_enabled,
                    'rooftops_enabled': rooftops_enabled,
                    'highest_broad_rooftops_only': highest_rooftops_enabled,
                    'ladders_enabled': ladders_enabled,
                    'quantized_enabled': quantized_enabled,
                    'ignore_destroyable_objects': ignore_destroyable,
                    'ignore_vehicles': ignore_vehicles,
                    'radius_cap': float(self.TILE_DISC_RADIUS_CAP),
                }

                progress_queue.put((
                    'status',
                    f'Generate Zone {anum}: exact transactions running in '
                    'isolated low-power worker...'))

                event_gate = {
                    'phase': None,
                    'phase_at': 0.0,
                    'status': None,
                    'status_at': 0.0,
                }

                def _process_event(event):
                    if not event:
                        return
                    kind = event[0]
                    now = _time.perf_counter()
                    if kind == 'phase' and len(event) >= 4:
                        phase_key = (str(event[2]), str(event[3]))
                        if (phase_key != event_gate['phase']
                                or now - event_gate['phase_at'] >= 0.125):
                            event_gate['phase'] = phase_key
                            event_gate['phase_at'] = now
                            progress_queue.put((
                                'phase', float(event[1]),
                                str(event[2]), str(event[3])))
                    elif kind == 'status' and len(event) >= 2:
                        status_text = str(event[1])
                        if (status_text != event_gate['status']
                                or now - event_gate['status_at'] >= 0.25):
                            event_gate['status'] = status_text
                            event_gate['status_at'] = now
                            progress_queue.put(('status', status_text))

                _run_gen = _ns.get('_run_generator_process', None)
                if _run_gen is None:
                    raise RuntimeError('_run_generator_process not found')
                result = _run_gen(
                    process_job, on_event=_process_event)
                if not isinstance(result, dict):
                    raise RuntimeError('Isolated zone worker returned no result.')

                stitched_existing_nodes = [
                    Node.from_dict(record)
                    for record in list(
                        result.get('stitched_existing_nodes') or [])]
                all_batch_nodes = [
                    Node.from_dict(record)
                    for record in list(result.get('batch_nodes') or [])]
                if not all_batch_nodes:
                    raise RuntimeError(
                        'No valid navigation nodes were generated inside the '
                        'selected tiles.')

                metrics = dict(result.get('metrics') or {})
                metrics['geometry_cache_hit'] = bool(
                    metrics.get('worker_geometry_cache_hit', False))
                metrics['geometry_time_sec'] = float(
                    metrics.get('worker_geometry_time_sec', 0.0) or 0.0)
                metrics['total_time_sec'] = round(
                    _time.perf_counter() - t0, 3)
                metrics['zone_id'] = int(anum)
                metrics['tiles'] = len(tile_set)
                metrics['connected_tile_clusters'] = int(cluster_count)
                metrics['local_transactions'] = len(jobs)

                all_new_ids = [int(node.id) for node in all_batch_nodes]
                total_before_crop = int(
                    metrics.get('total_before_crop', len(all_batch_nodes)) or 0)
                total_seam_links = int(
                    metrics.get('old_new_links', 0) or 0)
                elapsed = float(metrics['total_time_sec'])
                finish_detail = (
                    f'{len(tile_set)} tiles -> {len(jobs)} local transaction(s), '
                    f'{len(all_batch_nodes)}/{total_before_crop} nodes, '
                    f'{total_seam_links} seam links, {elapsed:.2f}s')
                progress_queue.put((
                    'phase', 94.0, 'Applying generated graph',
                    f'{len(all_batch_nodes)} nodes from {len(jobs)} '
                    'local transactions'))
                progress_queue.put((
                    'zone_result', {
                        'zone_id': anum,
                        'existing_count': existing_count,
                        'stitched_existing_nodes': stitched_existing_nodes,
                        'batch_nodes': all_batch_nodes,
                        'new_ids': all_new_ids,
                        'job_count': len(jobs),
                        'metrics': metrics,
                        'finish_detail': finish_detail,
                        'undo_snapshot': zone_snapshot,
                    }))
                return

                progress_queue.put((
                    'phase', 2.0, 'Collecting movement geometry',
                    f'{len(tile_set)} tiles; {len(jobs)} bounded local transaction(s)'))
                tg0 = _time.perf_counter()
                try:
                    world_segments, entities_core, geom_stats, cache_hit = (
                        self._collect_generator_geometry(
                            on_progress=lambda message: progress_queue.put(
                                ('status', str(message)))))
                except Exception as geom_ex:
                    world_segments, entities_core, geom_stats, cache_hit = (
                        [], [], {'collector_error': str(geom_ex)}, False)
                tg1 = _time.perf_counter()

                # Geometry collection installs one coherent immutable-for-this-
                # run cache.  Generation owns the modal edit transaction, so
                # copying hundreds of thousands of tuples here only adds memory
                # bandwidth, fan load, and latency without improving isolation.
                support_triangles = getattr(
                    self, '_generator_support_triangles', []) or []
                support_metadata = getattr(
                    self, '_generator_support_triangle_metadata', []) or []
                collision_segments_3d = getattr(
                    self, '_generator_collision_segments_3d', []) or []
                collision_triangles_3d = getattr(
                    self, '_generator_collision_triangles_3d', []) or []

                z_ref = (build_ground_z_ref(
                    entities_snapshot, terrain_z_snapshot)
                    if entities_snapshot else [])

                def _surface_z(x, y):
                    if terrain_info_snapshot is not None:
                        try:
                            h = _terrain_sample_height(terrain_info_snapshot, x, y)
                            if h is not None:
                                return float(h)
                        except Exception:
                            pass
                    return interp_z(x, y, z_ref, terrain_z_snapshot)

                raw_batches = []
                all_batch_nodes = []
                all_new_ids = []
                total_before_crop = 0
                total_seam_links = 0
                job_reports = []

                for job_index, job in enumerate(jobs):
                    cx, cy = job['center']
                    radius = float(job['radius'])
                    base_pct = 9.0 + 82.0 * job_index / max(1, len(jobs))
                    span_pct = 82.0 / max(1, len(jobs))
                    progress_queue.put((
                        'phase', base_pct,
                        f'Local transaction {job_index + 1}/{len(jobs)}',
                        f'R={radius:.1f}m; building reachable surface'))

                    generator_surface_z = _surface_z
                    support_metrics = {'enabled': False}
                    if z_filter_enabled:
                        generator_surface_z, support_metrics = (
                            _build_generator_reachable_surface(
                                (float(cx), float(cy)),
                                radius,
                                _surface_z,
                                support_triangles,
                                support_triangle_metadata=support_metadata,
                                collision_segments_3d=collision_segments_3d,
                                collision_triangles_3d=collision_triangles_3d,
                                max_step=GENERATOR_REACHABLE_MAX_STEP,
                                cell=0.25,
                                include_rooftops=rooftops_enabled,
                                highest_broad_rooftops_only=
                                    highest_rooftops_enabled))
                        if generator_surface_z is None:
                            job_reports.append({
                                'job': job_index + 1,
                                'center': (round(cx, 3), round(cy, 3)),
                                'radius': round(radius, 3),
                                'status': 'no_reachable_surface',
                            })
                            continue
                    else:
                        generator_surface_z, support_metrics = (
                            _build_generator_terrain_surface_contract(
                                (float(cx), float(cy)), radius, _surface_z,
                                collision_triangles_3d=collision_triangles_3d,
                                cell=0.50))

                    progress_queue.put((
                        'phase', base_pct + span_pct * 0.35,
                        f'Local transaction {job_index + 1}/{len(jobs)}',
                        f'Placing and connecting nodes (R={radius:.1f}m)'))

                    def _job_progress(percent, phase, detail,
                                      _base=base_pct, _span=span_pct,
                                      _job=job_index + 1,
                                      _count=len(jobs)):
                        core_pct = max(0.0, min(100.0, float(percent)))
                        overall = _base + _span * (0.35 + core_pct * 0.0060)
                        progress_queue.put((
                            'phase', overall,
                            f'Local transaction {_job}/{_count}: {phase}',
                            detail))

                    result = generate_seed_disc_campaign_v58(
                        world_segments or [],
                        entities_core or [],
                        terrain_z_snapshot,
                        center=(float(cx), float(cy)),
                        focus_radius=radius,
                        existing_nodes=immutable_existing_nodes,
                        log=lambda _m: None,
                        z_at=generator_surface_z,
                        collision_segments_3d=collision_segments_3d,
                        collision_triangles_3d=collision_triangles_3d,
                        radius_cap=self.TILE_DISC_RADIUS_CAP,
                        on_progress=_job_progress,
                        quantize_to_map_grid=quantized_enabled,
                        geometry_pre_normalized=True,
                        include_ladders=ladders_enabled)

                    generated = list(result.get('nodes') or [])
                    total_before_crop += len(generated)
                    core_x1, core_y1, core_x2, core_y2 = job['core']
                    kept_indices = []
                    for local_index, node in enumerate(generated):
                        nx = float(node.x)
                        ny = float(node.y)
                        if not (core_x1 <= nx < core_x2
                                and core_y1 <= ny < core_y2):
                            continue
                        if self._point_in_zone_tiles(nx, ny, job['tiles']):
                            kept_indices.append(local_index)

                    result_metrics = result.get('metrics') or {}
                    report = {
                        'job': job_index + 1,
                        'cluster': int(job['cluster_id']) + 1,
                        'center': (round(cx, 3), round(cy, 3)),
                        'radius': round(radius, 3),
                        'generated': len(generated),
                        'kept': len(kept_indices),
                        'seam_links': 0,
                        'walker_regrow_seeds': result_metrics.get(
                            'walker_regrow_seeds'),
                        'automatic_z_filter': support_metrics,
                    }
                    job_reports.append(report)
                    raw_batches.append({
                        'generated': generated,
                        'kept_indices': kept_indices,
                        'report': report,
                    })
                    progress_queue.put((
                        'phase', base_pct + span_pct * 0.95,
                        f'Local transaction {job_index + 1}/{len(jobs)}',
                        f'Owned {len(kept_indices)}/{len(generated)} nodes; '
                        f'placement remains isolated from other zone jobs'))

                # Conversion happens only after all isolated placement passes.
                batch_ranges = []
                for raw in raw_batches:
                    generated = raw['generated']
                    kept_indices = raw['kept_indices']
                    offset = existing_count + len(all_batch_nodes)
                    index_map = {
                        old_i: offset + new_i
                        for new_i, old_i in enumerate(kept_indices)}
                    local_batch = []
                    for old_i in kept_indices:
                        n = generated[old_i]
                        local_neighbors = []
                        for nb in sorted(list(n.neighbors or [])):
                            try:
                                mapped = index_map.get(int(nb))
                            except Exception:
                                mapped = None
                            if mapped is not None:
                                local_neighbors.append(mapped)
                        local_batch.append(Node(
                            index_map[old_i],
                            float(n.x), float(n.y), float(n.z),
                            b12=int(n.b12), b13=0, b14=0, b15=int(n.b15),
                            b16=int(n.b16), b17=int(n.b17), b18=int(n.b18),
                            neighbors=local_neighbors[:MAX_NEIGHBORS],
                            quantized_source_keys=getattr(
                                n, 'quantized_source_keys', None),
                            quantized_map_grid=bool(getattr(
                                n, 'quantized_map_grid', False))))
                    start = len(all_batch_nodes)
                    all_batch_nodes.extend(local_batch)
                    all_new_ids.extend(n.id for n in local_batch)
                    batch_ranges.append((
                        start, len(all_batch_nodes), raw['report']))

                # Placement remains unchanged: seam reconciliation starts only
                # after every local transaction has frozen its owned batch.
                for start, end, report in batch_ranges:
                    local_batch = all_batch_nodes[start:end]
                    if not local_batch:
                        continue
                    prior_nodes = existing_nodes_snapshot + all_batch_nodes[:start]
                    offset = existing_count + start
                    seam_links = 0
                    if prior_nodes:
                        seam_links = self._add_generator_old_new_seams(
                            prior_nodes,
                            local_batch,
                            offset,
                            world_segments or [],
                            collision_triangles_3d)
                    report['seam_links'] = int(seam_links or 0)
                    total_seam_links += int(seam_links or 0)

                if not all_batch_nodes:
                    progress_queue.put((
                        'zone_warning', 'Generate Zone',
                        'The generator ran but no nodes landed inside the selected tiles.'))
                    return

                elapsed = _time.perf_counter() - t0
                metrics = {
                    'geometry_cache_hit': bool(cache_hit),
                    'geometry_time_sec': round(tg1 - tg0, 3),
                    'total_time_sec': round(elapsed, 3),
                    'geometry_stats': geom_stats,
                    'old_new_links': int(total_seam_links),
                    'zone_id': anum,
                    'tiles': len(tile_set),
                    'connected_tile_clusters': cluster_count,
                    'local_transactions': len(jobs),
                    'local_transaction_reports': job_reports,
                    'total_before_crop': total_before_crop,
                    'zone_generated_after_crop': len(all_batch_nodes),
                }
                finish_detail = (
                    f'{len(tile_set)} tiles -> {len(jobs)} local transaction(s), '
                    f'{len(all_batch_nodes)}/{total_before_crop} nodes, '
                    f'{int(total_seam_links)} seam links, {elapsed:.2f}s')
                progress_queue.put((
                    'phase', 94.0, 'Applying generated graph',
                    f'{len(all_batch_nodes)} nodes from {len(jobs)} local transactions'))
                progress_queue.put((
                    'zone_result', {
                        'zone_id': anum,
                        'existing_count': existing_count,
                        'stitched_existing_nodes': existing_nodes_snapshot,
                        'batch_nodes': all_batch_nodes,
                        'new_ids': all_new_ids,
                        'job_count': len(jobs),
                        'metrics': metrics,
                        'finish_detail': finish_detail,
                        'undo_snapshot': zone_snapshot,
                    }))
            except Exception as ex:
                progress_queue.put((
                    'zone_error', str(ex), _traceback.format_exc()))
            finally:
                progress_queue.put(('finished', None))

        self._generator_worker = threading.Thread(
            target=_worker,
            name='AINZoneGeneratorWorker',
            daemon=True)
        self._generator_worker.start()
        self.after(50, self._poll_generator_progress)

    def _removed_legacy_generate_selected_zone_nodes_spatial(self, anum, opts):
        """Retained only as unreachable reference code; the active route is below."""
        import math as _math
        import time as _time

        if not self.bms_path:
            messagebox.showinfo('Generate Zone', 'Open a BMS map first.', parent=self)
            return

        meta = self._ensure_zone_meta(anum)
        existing = len(meta.get('nodes') or [])
        if existing:
            if not messagebox.askyesno(
                "Regenerate Zone",
                f"Zone {anum} already owns {existing} node(s).\n\n"
                f"Clear and regenerate this zone only?",
                parent=self):
                return

        pts = list(meta.get('points') or self._zone_points(anum))
        if len(pts) < 3:
            x1, y1, x2, y2 = self.zones[anum]
            xmin, xmax = sorted((x1, x2))
            ymin, ymax = sorted((y1, y2))
            pts = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        if len(pts) < 3:
            messagebox.showinfo("Generate Zone", "The selected zone has no valid area.", parent=self)
            return

        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        spacing = float(opts.get('spacing') or 2.0)
        radius = max(_math.hypot(float(px) - cx, float(py) - cy) for px, py in pts)
        radius = max(4.0, radius + max(2.0, spacing * 2.0))

        tile_set = meta.get('tiles')
        def _inside_zone(x, y):
            if tile_set:
                _fx, _fy = float(x), float(y)
                for (tx, ty, ts) in tile_set:
                    if tx <= _fx < tx + ts and ty <= _fy < ty + ts:
                        return True
                return False
            if len(pts) >= 3:
                return self._point_in_poly(float(x), float(y), pts)
            return False

        snap = self._zone_snapshot()
        self._clear_zone_nodes_internal(anum)
        t0 = _time.perf_counter()

        try:
            self.status(
                f'Zone {anum}: collecting geometry for spatial generation '
                f'(center X={cx:.3f}, Y={cy:.3f}, R={radius:.1f}m)...')
            self.update_idletasks()

            tg0 = _time.perf_counter()
            try:
                world_segments, entities_core, geom_stats, cache_hit = self._collect_generator_geometry()
            except Exception as geom_ex:
                world_segments, entities_core, geom_stats, cache_hit = [], [], {'collector_error': str(geom_ex)}, False
            tg1 = _time.perf_counter()

            _t_info = getattr(self, 'terrain_info', None)
            _tz = float(getattr(self, 'terrain_z', 18.5) or 18.5)
            _z_ref = build_ground_z_ref(self.entities, _tz) if self.entities else []

            def _surface_z(x, y):
                if _t_info is not None:
                    try:
                        h = _terrain_sample_height(_t_info, x, y)
                        if h is not None:
                            return float(h)
                    except Exception:
                        pass
                return interp_z(x, y, _z_ref, _tz)

            generator_surface_z = _surface_z
            support_metrics = {'enabled': False}
            if bool(getattr(self, '_seed_generate_z_filter', True)):
                self.status(
                    f'Zone {anum}: building automatic reachable-surface Z filter '
                    f'(maximum step {GENERATOR_REACHABLE_MAX_STEP:.2f}m)...')
                self.update_idletasks()
                generator_surface_z, support_metrics = _build_generator_reachable_surface(
                    (float(cx), float(cy)),
                    radius,
                    _surface_z,
                    getattr(self, '_generator_support_triangles', []),
                    support_triangle_metadata=getattr(
                        self, '_generator_support_triangle_metadata', []),
                    collision_segments_3d=getattr(
                        self, '_generator_collision_segments_3d', []),
                    collision_triangles_3d=getattr(
                        self, '_generator_collision_triangles_3d', []),
                    max_step=GENERATOR_REACHABLE_MAX_STEP,
                    cell=0.25,
                    include_rooftops=bool(
                        getattr(self, '_seed_generate_rooftops', False)),
                    highest_broad_rooftops_only=bool(getattr(
                        self, '_seed_generate_highest_rooftops', False)),
                )
                if generator_surface_z is None:
                    self._restore_zone_snapshot(snap)
                    messagebox.showwarning(
                        'Generate Zone',
                        'The automatic generator Z filter could not find a reachable '
                        'support surface for this zone.',
                        parent=self)
                    self.status(f'Zone {anum}: automatic Z filter found no seed support.')
                    return
            else:
                generator_surface_z, support_metrics = (
                    _build_generator_terrain_surface_contract(
                        (float(cx), float(cy)), radius, _surface_z,
                        collision_triangles_3d=getattr(
                            self, '_generator_collision_triangles_3d', []),
                        cell=0.50))

            result = generate_seed_disc_campaign_v58(
                world_segments or [],
                entities_core or [],
                _tz,
                center=(float(cx), float(cy)),
                focus_radius=radius,
                existing_nodes=list(self.nodes or []),
                log=self._generator_log,
                z_at=generator_surface_z,
                # Keep height-aware collision active even when the automatic
                # reachable-surface Z filter is off.  This is the same contract
                # used by normal clicked-seed generation.
                collision_segments_3d=getattr(
                    self, '_generator_collision_segments_3d', []),
                collision_triangles_3d=getattr(
                    self, '_generator_collision_triangles_3d', []),
                geometry_pre_normalized=True,
                include_ladders=bool(
                    getattr(self, '_seed_generate_z_filter', False)
                    and getattr(self, '_seed_generate_rooftops', False)
                    and getattr(self, '_seed_generate_ladders', False)),
                )
            result.setdefault('metrics', {})['automatic_z_filter'] = support_metrics
            tc1 = _time.perf_counter()

            generated = list(result.get('nodes') or [])
            kept_indices = [
                i for i, node in enumerate(generated)
                if _inside_zone(float(node.x), float(node.y))
            ]
            if not kept_indices:
                self._restore_zone_snapshot(snap)
                self.status(f'Zone {anum}: generator produced no nodes inside this area.')
                messagebox.showwarning(
                    'Generate Zone',
                    'The generator ran, but no generated nodes landed inside the selected zone.',
                    parent=self)
                return

            existing_nodes = list(self.nodes or [])
            offset = len(existing_nodes)
            index_map = {old_i: offset + new_i for new_i, old_i in enumerate(kept_indices)}
            batch_nodes = []
            new_ids = []

            for old_i in kept_indices:
                n = generated[old_i]
                new_id = index_map[old_i]
                local_neighbors = []
                for nb in sorted(list(n.neighbors or [])):
                    try:
                        nb = int(nb)
                    except Exception:
                        continue
                    mapped = index_map.get(nb)
                    if mapped is not None:
                        local_neighbors.append(mapped)
                batch_nodes.append(Node(new_id, float(n.x), float(n.y), float(n.z),
                                        b12=int(n.b12), b13=0, b14=0, b15=int(n.b15),
                                        b16=int(n.b16), b17=int(n.b17), b18=int(n.b18),
                                        neighbors=local_neighbors[:MAX_NEIGHBORS],
                                        quantized_source_keys=getattr(
                                            n, 'quantized_source_keys', None),
                                        quantized_map_grid=bool(getattr(
                                            n, 'quantized_map_grid', False))))
                new_ids.append(new_id)

            old_new_links = self._add_generator_old_new_seams(
                existing_nodes,
                batch_nodes,
                offset,
                world_segments or [],
                getattr(self, '_generator_collision_triangles_3d', []),
            )

            self.nodes = existing_nodes + batch_nodes
            self.next_id = len(self.nodes)
            meta = self._ensure_zone_meta(anum)
            meta['nodes'] = set(new_ids)
            self._push_undo(
                f'Generate spatial zone {anum} ({len(new_ids)} nodes)',
                lambda s=snap: self._restore_zone_snapshot(s)
            )
            self._mark_zones_dirty()
            self._pil_renderer.clear() if self._pil_renderer else None
            self._rebuild_hit_tester()
            self.selected_id = None
            self.selected_nodes.clear()
            self._update_inspector()
            self._update_stats()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='zone_spatial_generate')

            metrics = dict(result.get('metrics') or {})
            metrics['geometry_cache_hit'] = bool(cache_hit)
            metrics['geometry_time_sec'] = round(tg1 - tg0, 3)
            metrics['core_time_sec'] = round(tc1 - tg1, 3)
            metrics['total_time_sec'] = round(_time.perf_counter() - t0, 3)
            metrics['geometry_stats'] = geom_stats
            metrics['old_new_links'] = old_new_links
            metrics['zone_id'] = anum
            metrics['zone_generated_before_crop'] = len(generated)
            metrics['zone_generated_after_crop'] = len(batch_nodes)
            self._last_generator_metrics = metrics

            self.status(
                f'Zone {anum}: spatial generator kept {len(batch_nodes)}/{len(generated)} nodes '
                f'in area, old-new links {old_new_links}, {metrics["total_time_sec"]:.2f}s')
        except Exception as ex:
            self._restore_zone_snapshot(snap)
            import traceback
            traceback.print_exc()
            messagebox.showerror('Generate Zone error', str(ex), parent=self)



    def _generate_selected_zone_nodes_spatial(self, anum, opts):
        """Generate and crop a polygon zone in the persistent subprocess."""
        import math as _math
        import queue
        import time as _time

        if not self.bms_path:
            messagebox.showinfo('Generate Zone', 'Open a BMS map first.', parent=self)
            return
        if getattr(self, '_generator_running', False):
            self.status('Generate Zone: another generator pass is already running.')
            return

        meta = self._ensure_zone_meta(anum)
        existing = len(meta.get('nodes') or [])
        if existing and not messagebox.askyesno(
                'Regenerate Zone',
                f'Zone {anum} already owns {existing} node(s).\n\n'
                f'Clear and regenerate this zone only?', parent=self):
            return

        pts = list(meta.get('points') or self._zone_points(anum))
        if len(pts) < 3:
            x1, y1, x2, y2 = self.zones[anum]
            xmin, xmax = sorted((x1, x2))
            ymin, ymax = sorted((y1, y2))
            pts = [(xmin, ymin), (xmax, ymin),
                   (xmax, ymax), (xmin, ymax)]
        if len(pts) < 3:
            messagebox.showinfo(
                'Generate Zone', 'The selected zone has no valid area.', parent=self)
            return

        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        spacing = float(opts.get('spacing') or 2.0)
        radius = max(
            _math.hypot(float(px) - cx, float(py) - cy) for px, py in pts)
        radius = max(4.0, radius + max(2.0, spacing * 2.0))

        zone_snapshot = self._zone_snapshot()
        self._clear_zone_nodes_internal(anum)
        existing_nodes_snapshot = self._snapshot_nodes()
        existing_count = len(existing_nodes_snapshot)
        z_filter_enabled = bool(getattr(self, '_seed_generate_z_filter', True))
        rooftops_enabled = bool(
            z_filter_enabled and getattr(self, '_seed_generate_rooftops', False))
        ladders_enabled = bool(
            z_filter_enabled and rooftops_enabled
            and getattr(self, '_seed_generate_ladders', False))
        quantized_enabled = bool(getattr(self, '_seed_generate_quantized', False))
        bms_path_snapshot = os.path.abspath(self.bms_path)
        crop_polygon = [[float(x), float(y)] for x, y in pts]

        progress_queue = queue.Queue()
        self._generator_progress_queue = progress_queue
        self._generator_running = True
        self._generator_cancelled = False
        self._seed_generate_armed = False
        self._generator_progress_failed = False
        self._generator_progress_result_applied = False
        self._generator_finish_detail = None
        self._generator_zone_snapshot = zone_snapshot
        self.status(
            f'Generate Zone {anum}: started isolated exact worker '
            f'(X={cx:.3f}, Y={cy:.3f}, R={radius:.1f}m)...')
        self._show_generator_progress_window(cx, cy, radius)

        def _worker():
            import traceback as _traceback
            _configure_wt = _ns.get('_configure_generator_worker_thread', None)
            if _configure_wt:
                _configure_wt()
            t0 = _time.perf_counter()
            try:
                process_job = {
                    'bms_path': bms_path_snapshot,
                    'center': (float(cx), float(cy)),
                    'radius': float(radius),
                    'existing_nodes': [n.to_dict()
                                       for n in existing_nodes_snapshot],
                    'z_filter_enabled': z_filter_enabled,
                    'rooftops_enabled': rooftops_enabled,
                    'ladders_enabled': ladders_enabled,
                    'quantized_enabled': quantized_enabled,
                    'target_support_z': None,
                    'crop_polygon': crop_polygon,
                }
                progress_queue.put((
                    'status', f'Generate Zone {anum}: exact core and crop '
                    'running in isolated low-power worker...'))

                event_gate = {
                    'phase': None, 'phase_at': 0.0,
                    'status': None, 'status_at': 0.0,
                }

                def _process_event(event):
                    if not event:
                        return
                    now = _time.perf_counter()
                    kind = event[0]
                    if kind == 'phase':
                        name = str(event[2]) if len(event) > 2 else ''
                        if (name != event_gate['phase']
                                or now - event_gate['phase_at'] >= 0.125):
                            event_gate['phase'] = name
                            event_gate['phase_at'] = now
                            progress_queue.put(tuple(event))
                    elif kind == 'status':
                        message = str(event[1]) if len(event) > 1 else ''
                        if (message != event_gate['status']
                                and (event_gate['status'] is None
                                     or now - event_gate['status_at'] >= 0.20)):
                            event_gate['status'] = message
                            event_gate['status_at'] = now
                            progress_queue.put(tuple(event))

                _run_gen = _ns.get('_run_generator_process', None)
                if _run_gen is None:
                    raise RuntimeError('_run_generator_process not found')
                result = _run_gen(
                    process_job, on_event=_process_event)
                if result.get('no_reachable_surface'):
                    progress_queue.put((
                        'zone_warning', 'Generate Zone',
                        'The automatic generator Z filter could not find a '
                        'reachable support surface at this zone center.'))
                    return
                generated = list(result.get('nodes') or [])
                if not generated:
                    progress_queue.put((
                        'zone_warning', 'Generate Zone',
                        'The spatial generator produced no nodes inside this area.'))
                    return

                progress_queue.put((
                    'phase', 95.0, 'Converting cropped zone nodes',
                    f'Converting {len(generated)} generated records'))
                offset = existing_count
                generated_count = len(generated)
                batch_nodes = []
                for i, record in enumerate(generated):
                    nget = record.get if isinstance(record, dict) else (
                        lambda key, default=None, _node=record: getattr(
                            _node, key, default))
                    neighbors = []
                    for neighbor in sorted(list(nget('neighbors', []) or [])):
                        try:
                            neighbor = int(neighbor)
                        except Exception:
                            continue
                        if 0 <= neighbor < generated_count:
                            neighbors.append(offset + neighbor)
                    batch_nodes.append(Node(
                        offset + i,
                        float(nget('x', 0.0)), float(nget('y', 0.0)),
                        float(nget('z', 0.0)),
                        b12=int(nget('b12', 0)), b13=int(nget('b13', 0)),
                        b14=int(nget('b14', 0)), b15=int(nget('b15', 36)),
                        b16=int(nget('b16', 0)), b17=int(nget('b17', 0)),
                        b18=int(nget('b18', 0)),
                        neighbors=neighbors[:MAX_NEIGHBORS],
                        quantized_source_keys=nget(
                            'quantized_source_keys', None),
                        quantized_map_grid=bool(nget(
                            'quantized_map_grid', False))))

                old_new_links = 0
                seen_seams = set()
                for pair in list(result.get('old_new_seams') or []):
                    try:
                        old_index, local_index = int(pair[0]), int(pair[1])
                    except Exception:
                        continue
                    if (old_index, local_index) in seen_seams:
                        continue
                    seen_seams.add((old_index, local_index))
                    if not (0 <= old_index < existing_count
                            and 0 <= local_index < generated_count):
                        continue
                    new_index = offset + local_index
                    old_node = existing_nodes_snapshot[old_index]
                    new_node = batch_nodes[local_index]
                    if (new_index not in old_node.neighbors
                            and len(old_node.neighbors) < MAX_NEIGHBORS):
                        old_node.neighbors.append(new_index)
                    if (old_index not in new_node.neighbors
                            and len(new_node.neighbors) < MAX_NEIGHBORS):
                        new_node.neighbors.append(old_index)
                    if (new_index in old_node.neighbors
                            and old_index in new_node.neighbors):
                        old_new_links += 1

                metrics = dict(result.get('metrics') or {})
                metrics['geometry_cache_hit'] = bool(
                    metrics.get('worker_geometry_cache_hit', False))
                metrics['geometry_time_sec'] = float(
                    metrics.get('worker_geometry_time_sec', 0.0) or 0.0)
                metrics['total_time_sec'] = round(
                    _time.perf_counter() - t0, 3)
                metrics['old_new_links'] = old_new_links
                metrics['zone_id'] = anum
                before_crop = int(metrics.get(
                    'zone_generated_before_crop', generated_count))
                finish_detail = (
                    f'Zone {anum}: kept {generated_count}/{before_crop} nodes, '
                    f'{old_new_links} boundary links, '
                    f'{metrics["total_time_sec"]:.2f}s')
                progress_queue.put((
                    'phase', 99.0, 'Applying generated zone graph',
                    'Returning the completed cropped graph to the editor'))
                progress_queue.put((
                    'zone_result', {
                        'zone_id': anum,
                        'existing_count': existing_count,
                        'stitched_existing_nodes': existing_nodes_snapshot,
                        'batch_nodes': batch_nodes,
                        'new_ids': list(range(
                            offset, offset + generated_count)),
                        'job_count': 1,
                        'metrics': metrics,
                        'finish_detail': finish_detail,
                        'undo_snapshot': zone_snapshot,
                    }))
            except Exception as ex:
                progress_queue.put((
                    'zone_error', str(ex), _traceback.format_exc()))
            finally:
                progress_queue.put(('finished', None))

        self._generator_worker = threading.Thread(
            target=_worker, name='AINPolygonZoneGeneratorWorker', daemon=True)
        self._generator_worker.start()
        self.after(50, self._poll_generator_progress)

    def _default_spacing_for_b15(self, b15):
        """Map b15 to grid spacing so radius circles barely touch.
        radius = b15 * 0x1000 / 65536 meters.
        spacing = diameter * 0.9 so nodes are spaced correctly for the radius.
        """
        try:
            b15 = float(b15)
        except Exception:
            b15 = 36.0
        radius_m = b15 * 0x1000 / 65536.0
        return max(0.5, round(radius_m * 2.0 * 0.9, 2))


    def fill_selected_zone_ignore_collisions(self):
        """Compatibility menu command: use the selected zone's panel options."""
        if self._zone_selected is None or self._zone_selected not in self.zones:
            messagebox.showinfo(
                "No Zone Selected",
                "Select an area zone first, then run the fill tool.",
                parent=self
            )
            return
        self._zone_ignore_var.set(True) if hasattr(self, '_zone_ignore_var') else None
        self.generate_selected_zone_nodes()


    def _open_zone_layer_manager(self):
        return _ui_dialogs.open_zone_layer_manager(self)

    # === help_panel.py (HelpPanelMixin) ===

    """Methods for the help panel, byte flags help, node help, dev inspector in AINEditor."""

    # ── 4. HELP PANEL ────────────────────────────────────────────────────────

    def _help_panel_palette(self):
        return _ui_panels.help_panel_palette(self)
    def _sync_help_panel_theme(self):
        return _ui_panels.sync_help_panel_theme(self)
    def _build_help_panel(self):
        return _ui_panels.build_help_panel(self)

    def _refresh_help_content(self):
        return _ui_panels.refresh_help_content(self)

    def _toggle_help_panel(self):
        return _ui_panels.toggle_help_panel(self)


    def _byte_flag_highlight_palette(self):
        """Return b12/b15 highlight colours for the current panel theme."""
        try:
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        except Exception:
            light = False

        if light:
            return {
                'b12': {
                    'row': '#dceeff',
                    'entry': '#c9e2ff',
                    'label': '#005a9e',
                    'bar': '#3399ff',
                    'fg': '#1c1c1c',
                },
                'b15': {
                    'row': '#dcf3dc',
                    'entry': '#c8e9c8',
                    'label': '#1f7a1f',
                    'bar': '#33aa33',
                    'fg': '#1c1c1c',
                },
            }

        return {
            'b12': {
                'row': '#183754',
                'entry': '#102f4a',
                'label': '#a8d5ff',
                'bar': '#3399ff',
                'fg': '#f0f0f0',
            },
            'b15': {
                'row': '#193d22',
                'entry': '#12351a',
                'label': '#b6f5b6',
                'bar': '#33aa33',
                'fg': '#f0f0f0',
            },
        }


    def _sync_byte_flag_highlights(self):
        """Apply b12/b15 important-row styling after light/dark theme changes."""
        widgets = getattr(self, '_byte_flag_highlight_widgets', {}) or {}
        pal = self._byte_flag_highlight_palette()
        for key, parts in widgets.items():
            c = pal.get(key, {})
            try:
                parts.get('frame').configure(bg=c.get('row', C_PANEL2))
            except Exception:
                pass
            try:
                parts.get('bar').configure(bg=c.get('bar', '#00ffaa'))
            except Exception:
                pass
            try:
                parts.get('label').configure(bg=c.get('row', C_PANEL2), fg=c.get('label', C_TEXT))
            except Exception:
                pass
            try:
                parts.get('entry').configure(
                    bg=c.get('entry', C_PANEL),
                    fg=c.get('fg', C_TEXT),
                    insertbackground=c.get('fg', C_TEXT))
            except Exception:
                pass


    def _show_byte_flags_help(self):
        """Toggle the Byte Flags help as an editor-owned popup window."""
        win = getattr(self, '_byte_flags_help_win', None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
            self._byte_flags_help_win = None
            return

        try:
            dev = bool(self.dev_mode.get())
        except Exception:
            dev = False

        try:
            light = bool(self.light_panels.get())
        except Exception:
            light = False

        bg = '#ffffff' if light else '#111111'
        fg = '#1c1c1c' if light else '#ffffff'
        dim = '#555555' if light else '#ffffff'
        border = '#c2c2c2' if light else C_BORDER
        title_fg = '#006644' if light else C_GREEN
        pal = self._byte_flag_highlight_palette()

        win = tk.Toplevel(self)
        try:
            win.withdraw()
        except Exception:
            pass
        win.title('Node Properties — Help' if not dev else 'Byte Flags — Help')
        win.resizable(False, False)
        win.configure(bg=bg)
        self._byte_flags_help_win = win
        self._set_owned_popup_window(win)

        def _close():
            try:
                win.destroy()
            except Exception:
                pass
            self._byte_flags_help_win = None

        win.protocol('WM_DELETE_WINDOW', _close)
        win.bind('<Escape>', lambda e: (_close(), 'break'))

        outer = tk.Frame(win, bg=border, bd=0)
        outer.pack(fill='both', expand=True, padx=1, pady=1)
        panel = tk.Frame(outer, bg=bg)
        panel.pack(fill='both', expand=True, padx=1, pady=1)

        header = tk.Frame(panel, bg=bg)
        header.pack(fill='x', padx=12, pady=(10, 6))
        tk.Label(header, text=('Byte flags' if dev else 'Node properties'), bg=bg, fg=title_fg,
                 font=('Consolas', 10, 'bold'), anchor='w').pack(side='left')

        tk.Frame(panel, bg=border, height=1).pack(fill='x', padx=12)

        body = tk.Frame(panel, bg=bg)
        body.pack(fill='both', expand=True, padx=12, pady=(8, 12))

        def legend_row(key, title, desc):
            row = tk.Frame(body, bg=bg)
            row.pack(fill='x', pady=(2, 3))
            sw = tk.Frame(row, width=9, height=9, bg=pal[key]['bar'])
            sw.pack(side='left', padx=(0, 7), pady=2)
            sw.pack_propagate(False)
            tk.Label(row, text=title, bg=bg, fg=pal[key]['label'],
                     font=('Consolas', 9, 'bold'), anchor='w').pack(side='left')
            tk.Label(row, text=desc, bg=bg, fg=fg,
                     font=('Consolas', 9), anchor='w').pack(side='left')

        if dev:
            legend_row('b12', 'b12', ' — Movement mode')
            legend_row('b15', 'b15', ' — Acceptance range')
            msg = (
                'b12 decides how AI treats the node during movement/path arrival.\n'
                'b15 controls the node radius / acceptance range.\n\n'
                'Together, they affect how AI moves through the graph and how '
                'close it needs to be before a node is considered reached.'
            )
        else:
            legend_row('b12', 'Control', ' — Movement mode')
            legend_row('b15', 'Radius', ' — Acceptance range')
            msg = (
                'Control decides how AI treats the node during movement/path arrival.\n'
                'Radius controls the node radius / acceptance range.\n\n'
                'Together, they affect how AI moves through the graph and how '
                'close it needs to be before a node is considered reached.'
            )

        tk.Label(body, text=msg, bg=bg, fg=dim, font=('Consolas', 9),
                 justify='left', anchor='w', wraplength=410).pack(
                     fill='x', pady=(7, 0))

        try:
            self._center_popup_over_editor(win, 460, 220)
        except Exception:
            pass
        try:
            win.deiconify()
            win.lift(self.winfo_toplevel())
            win.focus_set()
        except Exception:
            pass


    def _refresh_byte_flags_help_popup(self):
        """Rebuild the open Byte Flags popup after theme or mode changes."""
        win = getattr(self, '_byte_flags_help_win', None)
        if win is None:
            return
        try:
            if not win.winfo_exists():
                self._byte_flags_help_win = None
                return
        except Exception:
            self._byte_flags_help_win = None
            return
        try:
            win.destroy()
        except Exception:
            pass
        self._byte_flags_help_win = None
        self._show_byte_flags_help()


    def _toggle_node_help(self):
        """Toggle the Node Inspector help popup."""
        if getattr(self, '_node_help_win', None) and self._node_help_win.winfo_exists():
            self._node_help_win.destroy()
            self._node_help_win = None
            return
        self._show_node_help()


    def _node_help_popup_palette(self):
        """Theme colours for the Node Inspector field-guide popup."""
        try:
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        except Exception:
            light = False

        if light:
            return {
                'bg': '#ffffff',
                'fg': '#1c1c1c',
                'dim': '#666666',
                'green': '#006644',
                'yellow': '#8a6500',
                'ph': '#a05000',
                'border': '#c2c2c2',
                'button_bg': '#eeeeee',
                'button_fg': '#555555',
                'button_active_bg': '#e0e0e0',
                'button_active_fg': '#111111',
                'text_bg': '#ffffff',
            }

        return {
            'bg': '#111111',
            'fg': '#cccccc',
            'dim': '#888888',
            'green': '#44ff88',
            'yellow': '#f0c040',
            'ph': '#ff9944',
            'border': C_BORDER,
            'button_bg': '#222222',
            'button_fg': '#ff4444',
            'button_active_bg': C_BORDER,
            'button_active_fg': C_TEXT,
            'text_bg': '#111111',
        }


    def _set_owned_popup_window(self, win):
        """Make a popup owned by the editor so Windows does not treat it as a separate taskbar app."""
        try:
            parent = self.winfo_toplevel()
        except Exception:
            parent = self
        try:
            win.transient(parent)
        except Exception:
            pass
        try:
            win.wm_transient(parent)
        except Exception:
            pass
        # On Windows, Tk Toplevels can still show as separate taskbar/Alt-Tab
        # windows.  The toolwindow style keeps this as an owned utility popup.
        try:
            if sys.platform.startswith('win'):
                win.attributes('-toolwindow', True)
        except Exception:
            pass
        try:
            win.lift(parent)
        except Exception:
            pass



    def _center_popup_over_editor(self, win, width=None, height=None):
        """Center a popup over the main editor window, with screen clamping."""
        try:
            win.update_idletasks()
        except Exception:
            pass

        try:
            width = int(width or max(win.winfo_reqwidth(), win.winfo_width(), 1))
            height = int(height or max(win.winfo_reqheight(), win.winfo_height(), 1))
        except Exception:
            width = int(width or 500)
            height = int(height or 520)

        try:
            parent = self.winfo_toplevel()
            parent.update_idletasks()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            if pw <= 1 or ph <= 1:
                raise RuntimeError('parent window not mapped yet')
            x = px + (pw - width) // 2
            y = py + (ph - height) // 2
        except Exception:
            try:
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            except Exception:
                sw, sh = 1920, 1080
            x = (sw - width) // 2
            y = (sh - height) // 2

        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = max(0, min(int(x), max(0, sw - width)))
            y = max(0, min(int(y), max(0, sh - height)))
        except Exception:
            x, y = int(x), int(y)

        try:
            win.geometry(f'{width}x{height}+{x}+{y}')
        except Exception:
            try:
                win.geometry(f'+{x}+{y}')
            except Exception:
                pass

    def _sync_node_help_window_theme(self):
        return _ui_theme.sync_node_help_window_theme(self)


    def _populate_node_help_text(self):
        """Fill the Node Inspector help with creator or Developer-mode content."""
        txt = getattr(self, '_node_help_text', None)
        if txt is None:
            return

        try:
            dev = bool(self.dev_mode.get())
        except Exception:
            dev = False

        try:
            txt.config(state='normal')
            txt.delete('1.0', 'end')
        except Exception:
            return

        def w(text, tag='normal'):
            txt.insert('end', text, tag)

        if not dev:
            # Creator-facing guide: semantic names, practical usage, and no raw
            # byte archaeology. The underlying fields and values remain the same.
            w("Node Inspector — Quick Guide\n", 'green')
            w("Select a node to view or change its navigation settings.\n\n", 'dim')

            w("ID", 'yellow')
            w(" — The node's unique number. It is assigned automatically.\n\n", 'normal')

            w("X, Y, Z", 'yellow')
            w(" — The node's position in the world, measured in meters.\n"
              "  Drag the node on the canvas or edit these values directly.\n\n", 'normal')

            w("──── Control ────\n", 'green')
            w("Controls how precisely the AI must reach this node.\n", 'normal')
            w("  0 = Normal — suitable for most navigation nodes\n"
              "  1 = Precise — AI moves to the center of the node\n"
              "      Useful for doorways, narrow corridors, and tight turns\n"
              "  Leave this at 0 unless the location needs precise movement.\n\n", 'dim')

            w("──── Radius ────\n", 'green')
            w("Controls node arrival and graph coverage for AI following the player.\n", 'normal')
            w("  Smaller values suit tight spaces.\n"
              "  Larger values suit open areas.\n"
              "  Use Radius circles as a visual guide; nearby circles should\n"
              "  overlap slightly without covering large unrelated areas.\n"
              "  Keep the player's expected route inside the radius coverage.\n"
              "  If the player leaves the graph, AI following the player may fail\n"
              "  to navigate even while the AI itself is still on the graph.\n"
              "  This specifically applies to AI following the player.\n"
              "  Common values: 8 (tight) / 24 (normal) / 36 (open) / 48 (wide)\n\n", 'dim')

            w("──── Details ────\n", 'green')
            w("The Details button reveals settings used mainly by takedown zones.\n", 'normal')
            w("  Most ordinary navigation nodes do not need these fields.\n\n", 'dim')

            w("Takedown (var1)", 'yellow')
            w(" — Sets the node's role during a takedown.\n", 'normal')
            w("  0 = Standard navigation\n"
              "  2 = Takedown / trigger node\n"
              "  6 = Assault position\n"
              "  8 = Grenade target\n"
              "      AI finds line of sight to this node and throws a grenade\n"
              "      toward it. Place where you want the grenade to land.\n"
              "      Place it relative to the entrance of a building, otherwise\n"
              "      the AI might trigger the flashbang animation in air or\n"
              "      other awkward places.\n"
              "  9 = Visible marker\n\n", 'dim')

            w("Takedown Zone", 'yellow')
            w(" — The room or tactical zone this node belongs to.\n", 'normal')
            w("  Nodes in the same room should use the same zone number.\n"
              "  0 means the node is not assigned to a takedown zone.\n"
              "  The Takedown Zones tool is the recommended way to set this up.\n\n", 'dim')

            w("Takedown (var2)", 'yellow')
            w(" — Links a takedown doorway to the zone where the player stands.\n", 'normal')
            w("  Use Takedown (var1) = 2 for the trigger to apply.\n"
              "  Example: Zone 5 is the takedown area. Use Takedown (var1) = 2 /\n"
              "    Zone ID = 0 / Takedown (var2) = 5.\n"
              "    Zone ID must not match Takedown (var2) on the trigger node.\n"
              "  Leave it at 0 for normal navigation nodes.\n"
              "  Use the Takedown Zones tool when creating room-to-room triggers.\n\n", 'dim')

            w("──── Apply Changes ────\n", 'green')
            w("Edits are not written to the selected node until you press ", 'normal')
            w("Apply Changes", 'yellow')
            w(".\n\n", 'normal')

            w("──── Lock options ────\n", 'green')
            w("Keeps selected values or height fixed while placing new nodes.\n", 'normal')
            w("  Use Update to capture the values from the current node.\n\n", 'dim')

            w("──── Connections ────\n", 'green')
            w("Lists every node connected to the selected node.\n"
              "  Select a connection and press ", 'normal')
            w("Remove", 'yellow')
            w(" to delete it.\n"
              "  Use ", 'normal')
            w("Connect with ID", 'yellow')
            w(" to create a new connection.\n"
              "  Shift+RMB drag on the canvas can cut connections.\n", 'normal')
        else:
            # Developer guide: preserve the existing raw field terminology and
            # reverse-engineering detail exactly as before this mode split.
            w("Node Inspector — Field Guide\n", 'green')
            w("What each value means and when to change it.\n\n", 'dim')

            w("ID", 'yellow')
            w(" — The node's unique number. Assigned automatically. Don't edit.\n\n", 'normal')

            w("X, Y, Z", 'yellow')
            w(" — Position in the world (meters). Z is height.\n"
              "  Move nodes by dragging them on the canvas or by editing these directly.\n\n", 'normal')

            w("──── Node Flags (b12) ────\n", 'green')
            w("Controls how the AI navigates this node.\n", 'normal')
            w("  0 = normal — AI navigates through this node freely\n"
              "  1 = precise — AI walks exactly to the center of this node\n"
              "      Use for doorways, tight corridors, important waypoints\n"
              "  For most nodes: leave at 0\n\n", 'dim')

            w("──── Node Role (b13) ────\n", 'green')
            w("Sets the tactical purpose of this node in the takedown system.\n", 'normal')
            w("  0 = standard navigation node (default)\n"
              "  1 = dead class — present in NovaLogic-authored graphs but has\n"
              "      no engine runstate. The engine does not act on this value.\n"
              "  2 = takedown activation node — always used together with b17\n"
              "  4 = unknown takedown value. Read/checked by the engine at 0x004890F0.\n"
              "      Verified specifically in Delta Force: Black Hawk Down: Team Sabre\n"
              "      patch 1.5.0 when dfv.cfg uses mp_numteams = 4 instead of the\n"
              "      normal mp_numteams = 2. Exact behavior/functionality is unconfirmed.\n"
              "  6 = assault position — where the squad moves while clearing a room\n"
              "  7 = unknown takedown value. Read/checked by the engine at 0x0041AD24\n"
              "      and 0x004890F0. Verified specifically in Team Sabre patch 1.5.0\n"
              "      when dfv.cfg uses mp_numteams = 4 instead of mp_numteams = 2.\n"
              "      Exact behavior/functionality is unconfirmed.\n"
              "  8 = grenade target — AI finds line of sight to this node and\n"
              "      throws a grenade toward it. Place where you want the\n"
              "      grenade to land.\n"
              "  9 = marker — shows a red floating pyramid for that respective node\n"
              "  For most nodes: leave at 0\n\n"
              "  ENGINE NOTE (Team Sabre 1.5.0 executable only):\n"
              "    b13=4 is checked at 0x004890F0.\n"
              "    b13=7 is checked at 0x0041AD24 and 0x004890F0.\n"
              "    These paths are read when dfv.cfg has mp_numteams = 4; the normal\n"
              "    value is mp_numteams = 2. Being read does not prove the values\n"
              "    function as intended; their exact takedown purpose remains unknown.\n"
              "    Do not assume these addresses/conditions apply to patch 1.5.0.5.\n\n", 'dim')

            w("──── Zone ID (b14) ────\n", 'green')
            w("Tags this node as belonging to a tactical zone (room).\n", 'normal')
            w("  All nodes in the same room should share the same zone ID.\n"
              "  The takedown system uses this to know which room to clear.\n"
              "  0 = not part of any zone\n"
              "  Use the Takedown Zones panel to set this up properly.\n\n", 'dim')

            w("──── Radius (b15) ────\n", 'green')
            w("Controls node arrival and graph coverage for AI following the player.\n", 'normal')
            w("  Higher = AI switches nodes from further away (open areas)\n"
              "  Lower  = AI must get very close before moving on (tight spaces)\n"
              "  Radius circles should barely overlap neighboring nodes\n"
              "  Keep the player's expected route inside the radius coverage.\n"
              "  If the player leaves the graph, AI following the player may fail\n"
              "  to navigate even while the AI itself is still on the graph.\n"
              "  This specifically applies to AI following the player.\n"
              "  Common values: 8 (tight) / 24 (normal) / 36 (open) / 48 (wide open)\n\n", 'dim')

            w("──── Direction Metadata (b16) ────\n", 'green')
            w("Metadata direction for this node.\n", 'normal')
            w("  0 = used for most nodes\n"
              "  Non-zero values draw an arrow for that node in the editor.\n"
              "  Does not affect AI movement or pathfinding.\n"
              "  Earlier .ain versions do not appear to contain this field yet.\n"
              "  It may be metadata from a later generator/conversion pass,\n"
              "  possibly used to point toward geometry or other nodes.\n\n", 'dim')

            w("──── Trigger Zone (b17) ────\n", 'green')
            w("Used by the takedown system to link two rooms together.\n", 'normal')
            w("  b14 = the zone being cleared (takedown area)\n"
              "  b17 = the zone the trigger node points to\n"
              "  b13 = 2 marks the node as a trigger\n"
              "  The trigger node's b14 must not match its b17.\n"
              "  Example: b13=2 / b14=0 / b17=5 triggers a takedown in zone 5.\n"
              "  Leave at 0 for standard navigation nodes.\n\n", 'dim')

            w("──── Runtime Value (b18) ────\n", 'green')
            w("Temporary runtime-only value.\n", 'normal')
            w("  The game resets it to 0 when the map is loaded.\n"
              "  Any value stored in the .AIN is discarded on load.\n"
              "  It may be used while the map is running, but it is not preserved\n"
              "  and has no lasting gameplay effect. Leave it at 0.\n\n", 'dim')

            w("──── Connections ────\n", 'green')
            w("Lists all nodes this node is connected to.\n"
              "  Select a connection + ", 'normal')
            w("Remove", 'yellow')
            w(" to delete it.\n"
              "  Use ", 'normal')
            w("Connect with ID", 'yellow')
            w(" in the panel to add new connections.\n"
              "  Or use Shift+RMB drag on the canvas to cut connections.\n\n", 'normal')

            w("──── Takedown System ────\n", 'green')
            w("The takedown system lets AI squads clear rooms automatically\n"
              "or on player command.\n", 'normal')
            w("  1. Set up zones using the Takedown Zones panel in the top bar\n"
              "  2. Tag nodes with the correct Zone ID (b14)\n"
              "  3. Place assault nodes (b13=6) where the squad should move\n"
              "  4. Place grenade nodes (b13=8) where AI should throw grenades\n"
              "  5. Mark the trigger doorway nodes with b17 = player's zone ID\n\n"
              "  Team Sabre 1.5.0 note: b13=4 and b13=7 are read by engine paths\n"
              "  when dfv.cfg uses mp_numteams = 4 instead of the normal value 2.\n"
              "  Their exact behavior remains unconfirmed; these findings and addresses\n"
              "  are specific to patch 1.5.0 and are not asserted for patch 1.5.0.5.\n\n", 'dim')

        try:
            txt.config(state='disabled')
            txt.yview_moveto(0.0)
        except Exception:
            pass


    def _show_node_help(self):
        return _ui_dialogs.show_node_help(self)


    def _sync_dev_mode_inspector(self):
        """Switch creator-facing labels and raw byte names with Developer mode."""
        try:
            dev = bool(self.dev_mode.get())
        except Exception:
            dev = False


        # The public button opens Cleanup directly. Developer mode exposes
        # the wider post-processing chooser, so name it Graph Tools there.
        try:
            self._ain_pass_btn.configure(text=('Graph Tools' if dev else 'Graph Cleanup'))
        except Exception:
            pass

        # Area Zones uses the same semantic names as the Node Inspector in the
        # normal creator-facing UI. Developer mode exposes the raw byte names.
        try:
            self._zone_b12_label.configure(text=('b12' if dev else 'Control'))
        except Exception:
            pass
        try:
            self._zone_b15_label.configure(text=('b15' if dev else 'Radius'))
        except Exception:
            pass

        labels = ({
            'b12': 'b12:',
            'b13': 'b13:',
            'b14': 'b14:',
            'b15': 'b15:',
            'b16': 'b16:',
            'b17': 'b17:',
            'b18': 'b18:',
        } if dev else {
            'b12': 'Control:',
            'b13': 'Takedown (var1):',
            'b14': 'Takedown Zone:',
            'b15': 'Radius:',
            'b16': 'Direction:',
            'b17': 'Takedown (var2):',
            'b18': 'b18:',
        })

        for key, text in labels.items():
            try:
                self._inspector_field_labels[key].configure(text=text)
            except Exception:
                pass

        # The top-right Grid/Radius/Offset diagnostic is also Developer-mode-only.
        # Keep the command and window implemented; only its public top-bar entry
        # is hidden in the simplified creator-facing layout.
        try:
            grid_diag_btn = getattr(self, '_grid_diag_btn', None)
            if grid_diag_btn is not None:
                if dev:
                    if not grid_diag_btn.winfo_manager():
                        grid_diag_btn.pack(side='right', padx=2)
                else:
                    grid_diag_btn.pack_forget()
        except Exception:
            pass

        # Section header and lock labels switch between dev/normal names.
        try:
            self._byte_flags_title.configure(text="Byte flags:" if dev else "Node properties:")
        except Exception:
            pass
        try:
            self._lock_values_cb.configure(text="Lock b values" if dev else "Lock values")
        except Exception:
            pass
        try:
            _tfg = self._ui_theme()['text']
            if not self._lock_values.get():
                self._locked_values_label.configure(
                    text="b12=? b15=?" if dev else "Control=? Radius=?", fg=_tfg)
            else:
                b12 = self._locked_b12.get()
                b15 = self._locked_b15.get()
                if dev:
                    self._locked_values_label.configure(text=f"b12={b12} b15={b15}", fg=_tfg)
                else:
                    self._locked_values_label.configure(text=f"Control={b12} Radius={b15}", fg=_tfg)
        except Exception:
            pass

        # The Node metadata visibility controls are Developer-mode-only.
        # Insert the complete section back into its original place when enabled.
        try:
            metadata_section = getattr(self, '_node_metadata_section', None)
            z_filter_separator = getattr(self, '_z_filter_separator', None)
            if metadata_section is not None:
                if dev:
                    if not metadata_section.winfo_manager():
                        if z_filter_separator is not None:
                            metadata_section.pack(fill='x', before=z_filter_separator)
                        else:
                            metadata_section.pack(fill='x')
                else:
                    metadata_section.pack_forget()
        except Exception:
            pass

        # b16 and b18 are Developer-mode-only rows. They stay inside Details,
        # so the normal Details collapse/expand behavior remains unchanged.
        try:
            b16_row = self._inspector_field_rows.get('b16')
            b17_row = self._inspector_field_rows.get('b17')
            spacer = getattr(self, '_inspector_direction_spacer', None)
            if dev:
                if spacer is not None and not spacer.winfo_manager():
                    spacer.pack(before=b17_row)
                if b16_row is not None and not b16_row.winfo_manager():
                    b16_row.pack(fill='x', padx=4, pady=1, before=b17_row)
            else:
                if b16_row is not None:
                    b16_row.pack_forget()
                if spacer is not None:
                    spacer.pack_forget()

            b18_row = self._inspector_field_rows.get('b18')
            if b18_row is not None:
                if dev:
                    if not b18_row.winfo_manager():
                        b18_row.pack(fill='x', padx=4, pady=1)
                else:
                    b18_row.pack_forget()
        except Exception:
            pass

        try:
            # pack()/pack_forget() can leave the canvas with a stale content
            # bbox on Windows. Recompute after geometry has settled so hidden
            # Developer-mode sections do not leave invisible scroll space.
            self.after_idle(self._refresh_left_panel_scrollregion)
            self.after(80, self._refresh_left_panel_scrollregion)
        except Exception:
            pass


    def _on_extended_b12_toggle(self):
        """Extended B12 is the master switch for the full B12 visualization.

        Enabling it starts B12 Bands automatically. Bands remains independently
        switchable from View afterwards, so the user can turn Bands back off
        without disabling Extended B12. Disabling Extended B12 also clears Bands
        so normal parity view is genuinely clean.
        """
        try:
            if bool(self.show_b12_extended.get()):
                self.show_b12_bands.set(True)
            else:
                self.show_b12_bands.set(False)
        except Exception:
            pass
        self.redraw()

    def _cache_generator_seed(self, x, y, radius, *, kind='Seed',
                              target_node_z=None, settings=None):
        """Remember one normal generator seed, including it in project saves."""
        try:
            history = getattr(self, '_previous_generator_seeds', None)
            if not isinstance(history, list):
                history = []
                self._previous_generator_seeds = history
            entry = {
                'index': len(history) + 1,
                'time': time.strftime('%H:%M:%S'),
                'kind': str(kind or 'Seed'),
                'x': float(x),
                'y': float(y),
                'radius': float(radius),
                'target_node_z': (None if target_node_z is None
                                  else float(target_node_z)),
                'settings': dict(settings or {}),
                'bms_path': os.path.abspath(str(getattr(self, 'bms_path', '') or '')),
            }
            history.append(entry)
            # Keep enough history for long debugging sessions without allowing
            # an accidental all-day session to grow forever.
            if len(history) > 500:
                del history[:-500]
                for i, item in enumerate(history, 1):
                    item['index'] = i
            self._refresh_previous_generator_seeds_window(select_last=True)
            return entry
        except Exception:
            return None

    @staticmethod
    def _format_previous_generator_seed_entry(entry):
        kind = str(entry.get('kind') or 'Seed')
        layer = entry.get('target_node_z')
        layer_text = (f" | nodeZ={float(layer):.3f}" if layer is not None else '')
        settings = dict(entry.get('settings') or {})
        flags = []
        if settings:
            flags.append('Z' if settings.get('z_filter_enabled') else 'no-Z')
            if settings.get('rooftops_enabled'):
                flags.append('rooftops')
            if settings.get('highest_broad_rooftops_only'):
                flags.append('highest-only')
            if settings.get('ladders_enabled'):
                flags.append('ladders')
            if settings.get('quantized_enabled'):
                flags.append('quantized')
            if settings.get('ignore_destroyable_objects'):
                flags.append('ignore-destroyable')
            if settings.get('ignore_vehicles'):
                flags.append('ignore-vehicles')
        flag_text = (" | " + ', '.join(flags)) if flags else ''
        return (
            f"#{int(entry.get('index', 0)):03d}  "
            f"[{entry.get('time', '--:--:--')}]  {kind:<8}  "
            f"X={float(entry.get('x', 0.0)):.3f}  "
            f"Y={float(entry.get('y', 0.0)):.3f}  "
            f"R={float(entry.get('radius', 0.0)):.1f}m"
            f"{layer_text}{flag_text}")

    def _format_previous_generator_seeds(self):
        entries = list(getattr(self, '_previous_generator_seeds', []) or [])
        if not entries:
            return 'No previous generator seeds are available.'
        return '\n'.join(self._format_previous_generator_seed_entry(e)
                         for e in entries)

    def _selected_previous_generator_seed(self):
        box = getattr(self, '_previous_generator_seeds_listbox', None)
        entries = list(getattr(self, '_previous_generator_seeds', []) or [])
        if box is None:
            return None
        try:
            selected = box.curselection()
            if not selected:
                return None
            index = int(selected[0])
            if 0 <= index < len(entries):
                return entries[index]
        except Exception:
            pass
        return None

    def _update_previous_generator_seed_retry_state(self, _event=None):
        entry = self._selected_previous_generator_seed()
        state = 'normal' if entry is not None else 'disabled'
        for attr in ('_previous_generator_seeds_retry_button',
                     '_previous_generator_seeds_copy_button'):
            btn = getattr(self, attr, None)
            if btn is None:
                continue
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _copy_selected_previous_generator_seed(self):
        """Copy only the currently selected seed-history line."""
        entry = self._selected_previous_generator_seed()
        if entry is None:
            return
        payload = self._format_previous_generator_seed_entry(entry)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self.update_idletasks()
            self.status(
                f'Copied generator seed #{int(entry.get("index", 0)):03d}.')
        except Exception:
            pass

    def _refresh_previous_generator_seeds_window(self, select_last=False):
        win = getattr(self, '_previous_generator_seeds_window', None)
        box = getattr(self, '_previous_generator_seeds_listbox', None)
        try:
            if win is None or not win.winfo_exists() or box is None:
                return
            old_selection = box.curselection()
            box.delete(0, 'end')
            entries = list(getattr(self, '_previous_generator_seeds', []) or [])
            for entry in entries:
                box.insert('end', self._format_previous_generator_seed_entry(entry))
            target = None
            if select_last and entries:
                target = len(entries) - 1
            elif old_selection and int(old_selection[0]) < len(entries):
                target = int(old_selection[0])
            if target is not None:
                box.selection_set(target)
                box.see(target)
            self._update_previous_generator_seed_retry_state()
        except Exception:
            pass

    def _retry_previous_generator_seed(self):
        """Run the selected seed again with the options recorded at submission."""
        entry = self._selected_previous_generator_seed()
        if entry is None:
            return
        if getattr(self, '_generator_running', False):
            self.status('Retry seed: generator is already running.')
            return

        saved_map = os.path.abspath(str(entry.get('bms_path') or ''))
        current_map = os.path.abspath(str(getattr(self, 'bms_path', '') or ''))
        if saved_map and current_map and os.path.normcase(saved_map) != os.path.normcase(current_map):
            messagebox.showwarning(
                'Retry Seed',
                'This seed belongs to a different BMS map. Open that map before retrying it.',
                parent=getattr(self, '_previous_generator_seeds_window', self))
            return

        settings = dict(entry.get('settings') or {})
        attrs = {
            '_seed_generate_radius': float(entry.get('radius', 32.0)),
            '_seed_generate_z_filter': bool(settings.get(
                'z_filter_enabled', getattr(self, '_seed_generate_z_filter', True))),
            '_seed_generate_rooftops': bool(settings.get(
                'rooftops_enabled', getattr(self, '_seed_generate_rooftops', False))),
            '_seed_generate_highest_rooftops': bool(settings.get(
                'highest_broad_rooftops_only',
                getattr(self, '_seed_generate_highest_rooftops', False))),
            '_seed_generate_ladders': bool(settings.get(
                'ladders_enabled', getattr(self, '_seed_generate_ladders', False))),
            '_seed_generate_quantized': bool(settings.get(
                'quantized_enabled', getattr(self, '_seed_generate_quantized', False))),
            '_seed_ignore_destroyable': bool(settings.get(
                'ignore_destroyable_objects', getattr(self, '_seed_ignore_destroyable', True))),
            '_seed_ignore_vehicles': bool(settings.get(
                'ignore_vehicles', getattr(self, '_seed_ignore_vehicles', False))),
        }
        previous = {name: getattr(self, name, None) for name in attrs}
        previous_retry_source = getattr(self, '_retrying_seed_index', None)
        try:
            for name, value in attrs.items():
                setattr(self, name, value)
            self._retrying_seed_index = int(entry.get('index', 0) or 0)
            self._run_generator_at(
                float(entry.get('x', 0.0)),
                float(entry.get('y', 0.0)),
                target_node_z=entry.get('target_node_z'))
        finally:
            for name, value in previous.items():
                setattr(self, name, value)
            self._retrying_seed_index = previous_retry_source

    def _show_previous_generator_seeds(self):
        return _generator_dialog_ui.show_previous_generator_seeds(self)

    def _on_dev_mode_toggle(self):
        """Apply and persist the Developer mode presentation."""
        dev = bool(self.dev_mode.get())

        # These overlays expose raw metadata and must not remain active behind
        # a hidden normal-mode section. Remember their choices for this session,
        # disable them outside Developer mode, and restore them when it returns.
        if dev:
            saved = getattr(self, '_dev_metadata_overlay_state', None)
            if saved is not None:
                try:
                    self.show_b12_extended.set(bool(saved[0]))
                    self.show_b12_bands.set(bool(saved[0]))
                    self.show_b16_arrows.set(bool(saved[1]))
                except Exception:
                    pass
        else:
            try:
                self._dev_metadata_overlay_state = (
                    bool(self.show_b12_extended.get()),
                    bool(self.show_b16_arrows.get()),
                )
                self.show_b12_extended.set(False)
                self.show_b12_bands.set(False)
                self.show_b16_arrows.set(False)
            except Exception:
                pass

        self._sync_dev_mode_inspector()
        self._rebuild_view_menu()
        self._rebuild_file_menu()
        self._rebuild_edit_menu()
        if not dev and getattr(self, '_debug_bar_visible', False):
            self._toggle_debug_bar()
        try:
            views = [getattr(self, '_embedded_3d_view', None)]
            win = getattr(self, '_wire3d_window', None)
            views.append(getattr(win, 'view', None) if win is not None else None)
            for view in views:
                if view is not None and hasattr(view, '_sync_developer_controls'):
                    view._sync_developer_controls()
        except Exception:
            pass
        # If either help popup is already open, switch its wording immediately.
        try:
            help_win = getattr(self, '_node_help_win', None)
            if help_win is not None and help_win.winfo_exists():
                self._populate_node_help_text()
        except Exception:
            pass
        try:
            self._refresh_byte_flags_help_popup()
        except Exception:
            pass
        try:
            breach_win = getattr(self, '_breach_win', None)
            if breach_win is not None and breach_win.winfo_exists():
                self._sync_breach_panel_mode()
        except Exception:
            pass
        self._save_ui_preferences()
        self.redraw()
        try:
            self.status('Developer mode enabled: raw byte names, metadata controls, b16, and b18 are visible.'
                        if dev else
                        'Developer mode disabled: simplified inspector fields restored.')
        except Exception:
            pass


    def _toggle_node_inspector_details(self):
        """Show or hide the extra Node Inspector byte fields."""
        show = not bool(self._inspector_details_on.get())
        self._inspector_details_on.set(show)
        light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        if show:
            self._inspector_details_frame.pack(fill='x', pady=(1,0), before=self._info_apply_btn)
            self._details_btn.configure(fg=C_GREEN, relief='solid', bd=1)
        else:
            self._inspector_details_frame.pack_forget()
            self._details_btn.configure(fg=('#1c1c1c' if light else '#ffffff'), relief='solid', bd=1)
        self.after(80, self._update_scroll_arrow)

    # === ui_builder.py (UIBuilderMixin) ===

    """Methods for building the main UI layout in AINEditor."""

    def _build_top_bar(self):
        bar = tk.Frame(self, bg=C_PANEL, height=34, bd=0)
        self._topbar_frame = bar
        self._topbar_separators = []
        bar.pack(fill='x', side='top')
        bar.pack_propagate(False)

        # ── Left: action buttons ───────────────────────────────────────────
        self._generate_btn = tk.Button(bar, text="▶ Generate", command=self.do_generate,
                  bg='#006000', fg='white', font=('Consolas',9,'bold'),
                  relief='flat', padx=8, pady=2)
        self._generate_btn.pack(side='left', padx=(8,2))

        self._ain_pass_btn = tk.Button(bar, text="Graph Cleanup", command=self._toggle_ain_pass_overlay,
                  bg='#00437a', fg='white', font=('Consolas',9,'bold'),
                  relief='flat', padx=8, pady=2)
        self._ain_pass_btn.pack(side='left', padx=2)

        self._export_btn = tk.Button(bar, text="Export AIN", command=self.export_ain,
                  bg='#2f7fc0', fg='white', font=('Consolas',9),
                  relief='flat', padx=8, pady=2)
        self._export_btn.pack(side='left', padx=2)

        self._view3d_toggle_btn = tk.Button(bar, text="3D View",
                  command=self.toggle_3d_view,
                  bg='#303030', fg='white', font=('Consolas',9),
                  relief='flat', padx=8, pady=2)
        self._view3d_toggle_btn.pack(side='left', padx=2)

        sep = tk.Frame(bar, bg=C_BORDER, width=1)
        sep.pack(side='left', fill='y', padx=6)
        self._topbar_separators.append(sep)

        # ── Mode buttons — icon style ──────────────────────────────────────
        # Hidden generate mode — variable still active, no button shown
        self._mode_btns = {}
        _mode_defs = [
            ('edit',  'E',  'Edit — place and edit nodes'),
            ('draw',  '🖊', 'Draw — draw nodes continuously'),
            ('paint', None, 'Color — paint b14 zone color onto nodes'),
            ('zone',  'AZ', 'Area Zones — define generation zones'),
        ]

        def _make_mode_btn(parent, mode_key, icon, tooltip_text):
            if icon is None:
                # Paint bucket — canvas-drawn gradient node icon
                btn_frame = tk.Frame(parent, bg=C_PANEL, cursor='hand2')
                btn_frame.pack(side='left', padx=2)
                c = tk.Canvas(btn_frame, width=26, height=26,
                              bg=C_PANEL, highlightthickness=0, cursor='hand2')
                c.pack()
                # Simple four-quadrant b14 colour icon.
                x0, y0, x1, y1 = 5, 5, 21, 21
                mx, my = 13, 13
                c.create_rectangle(x0, y0, mx, my, fill='#0088ff', outline='')  # blue
                c.create_rectangle(mx, y0, x1, my, fill='#ff3333', outline='')  # red
                c.create_rectangle(x0, my, mx, y1, fill='#00cc55', outline='')  # green
                c.create_rectangle(mx, my, x1, y1, fill='#ffdd00', outline='')  # yellow
                c.create_rectangle(x0, y0, x1, y1, outline='#001111', width=1)
                c.create_line(mx, y0, mx, y1, fill='#001111')
                c.create_line(x0, my, x1, my, fill='#001111')
                c.bind('<Button-1>', lambda e: _set_mode('paint'))
                btn_frame.bind('<Button-1>', lambda e: _set_mode('paint'))
                # Store canvas as the button reference
                self._mode_btns['paint'] = (btn_frame, c)
                _attach_tooltip(btn_frame, tooltip_text)
                _attach_tooltip(c, tooltip_text)
                return btn_frame
            else:
                # Use larger font for emoji icons so they render properly
                _fsize = 14 if icon in ('🖊',) else 9
                btn = tk.Button(parent, text=icon,
                                command=lambda m=mode_key: _set_mode(m),
                                bg=C_PANEL2, fg=C_TEXT,
                                font=('Consolas', _fsize, 'bold'),
                                relief='flat', padx=6, pady=2,
                                activebackground=C_BORDER,
                                activeforeground=C_TEXT,
                                cursor='hand2')
                btn.pack(side='left', padx=2)
                self._mode_btns[mode_key] = btn
                _attach_tooltip(btn, tooltip_text)
                return btn

        def _set_mode(m):
            self.mode.set(m)
            self._on_mode_change()
            _update_mode_btns()

        def _update_mode_btns():
            cur = self.mode.get()
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
            neutral_bg = '#fcfcfc' if light else C_PANEL2
            paint_bg = neutral_bg if light else C_PANEL
            inactive_fg = '#1c1c1c' if light else C_TEXT
            active_col = '#008a2e' if light else '#00ff44'
            for mk, btn_ref in self._mode_btns.items():
                active = (mk == cur)
                if isinstance(btn_ref, tuple):
                    # Canvas paint button
                    frame, canvas = btn_ref
                    hl = active_col if active else paint_bg
                    frame.configure(bg=paint_bg,
                                    highlightbackground=hl,
                                    highlightcolor=hl,
                                    highlightthickness=2 if active else 0,
                                    relief='solid' if active else 'flat')
                    try:
                        canvas.configure(bg=paint_bg)
                    except Exception:
                        pass
                else:
                    btn_ref.configure(
                        bg=neutral_bg,
                        activebackground=('#d7e8ff' if light else C_BORDER),
                        activeforeground=('#003a66' if light else C_TEXT),
                        highlightbackground=active_col if active else neutral_bg,
                        highlightcolor=active_col if active else neutral_bg,
                        highlightthickness=2 if active else 0,
                        relief='solid' if active else 'flat',
                        fg=active_col if active else inactive_fg)

        def _attach_tooltip(widget, text):
            _tip = [None]
            def _show(e):
                if _tip[0] and _tip[0].winfo_exists():
                    return
                tip = tk.Toplevel(self)
                tip.overrideredirect(True)
                tip.configure(bg='#1e1e1e')
                tk.Label(tip, text=text, bg='#1e1e1e', fg='#cccccc',
                         font=('Consolas', 8), padx=6, pady=3,
                         relief='solid', bd=1).pack()
                # Position below widget
                wx = widget.winfo_rootx()
                wy = widget.winfo_rooty() + widget.winfo_height() + 2
                tip.geometry(f"+{wx}+{wy}")
                _tip[0] = tip
            def _hide(e):
                if _tip[0] and _tip[0].winfo_exists():
                    _tip[0].destroy()
                _tip[0] = None
            widget.bind('<Enter>', _show)
            widget.bind('<Leave>', _hide)

        self._update_mode_btns = _update_mode_btns

        for mode_key, icon, tip in _mode_defs:
            _make_mode_btn(bar, mode_key, icon, tip)

        # Initialize active state
        _update_mode_btns()

        # ── Zone panel + Takedown zones ─────────────────────────────────────
        self._zone_panel_toggle_btn = tk.Button(
            bar, text="Zone Panel", command=self._toggle_zone_panel,
            bg=C_PANEL2, fg=C_TEXT, font=('Consolas',8),
            relief='flat', padx=6, pady=1)
        self._zone_panel_toggle_btn.pack(side='left', padx=(0, 2))

        self._breach_panel_btn = tk.Button(
            bar, text="Takedown Zones", command=self._open_breach_panel,
            bg='#550000', fg='#ff6666', font=('Consolas',8),
            relief='flat', padx=6, pady=1)
        self._breach_panel_btn.pack(side='left', padx=(0, 2))

        # Adaptive radius belongs to node placement/editing rather than the
        # global viewport chrome, so its controls live in Node Inspector.
        sep = tk.Frame(bar, bg=C_BORDER, width=1)
        sep.pack(side='left', fill='y', padx=6)
        self._topbar_separators.append(sep)

        # ── Coordinates + zoom (left side, after separators) ──────────────
        self._coord_var = tk.StringVar(value="X: 0.000  Y: 0.000")
        self._coord_label = tk.Label(bar, textvariable=self._coord_var,
                 bg=C_PANEL, fg=C_GREEN, font=('Consolas',9),
                 width=28, anchor='w')
        self._coord_label.pack(side='left', padx=4)

        self._zoom_var = tk.StringVar(value="Zoom: 8.0")
        self._zoom_label = tk.Label(bar, textvariable=self._zoom_var,
                 bg=C_PANEL, fg=C_DIM, font=('Consolas',9))
        self._zoom_label.pack(side='left', padx=4)

        # ── Right side ─────────────────────────────────────────────────────
        # Help panel toggle
        self._help_visible = False
        self._topbar_help_btn = tk.Button(
            bar, text='Help [H]',
            command=self._toggle_help_panel,
            bg='#3a3a3a', fg='#e8e8e8',
            font=('Consolas', 8, 'bold'), relief='solid', bd=1,
            padx=8, pady=2,
            highlightbackground=C_BORDER, highlightthickness=1,
            activebackground=C_BORDER, activeforeground='white')
        self._topbar_help_btn.pack(side='right', padx=(0, 8))

        # Top-right warning when the visibility Z filter is active. Keep it out
        # of the main coordinate/status text so it does not clutter normal use.
        self._zfilter_top_indicator = tk.Label(
            bar,
            text="! Z filter is on",
            bg='#5a3b00', fg='#ffd84a',
            font=('Consolas', 8, 'bold'),
            padx=7, pady=2,
            relief='solid', bd=1,
            highlightbackground='#9a6a00', highlightthickness=1,
        )
        self._zfilter_top_indicator.pack_forget()

        # Clear Render Caches remains callable from diagnostics, hidden from the top bar.

        self._grid_diag_btn = tk.Button(bar, text="Grid/Radius/Offset",
                  command=self._show_grid_radius_diagnostic,
                  bg=C_PANEL2, fg=C_DIM, activebackground=C_ACCENT,
                  activeforeground='white', relief='flat',
                  font=('Consolas',8), padx=8, pady=2)
        # Raw placement diagnostics are kept available, but the top-bar entry
        # is Developer-mode-only. _sync_dev_mode_inspector() controls packing.
        self._grid_diag_btn.pack_forget()

        self._terrain_overlay_btn = tk.Button(
            bar, text="Terrain Overlay: Off",
            command=self._toggle_terrain_overlay,
            bg=C_PANEL2, fg=C_TEXT, activebackground=C_ACCENT,
            activeforeground='white', relief='flat',
            font=('Consolas',8), padx=8, pady=2)
        self._terrain_debug_btn = tk.Button(
            bar, text=_terrain_debug_mode_label(self.terrain_debug_mode),
            command=self._cycle_terrain_debug_mode,
            bg=C_PANEL2, fg=C_TEXT, activebackground=C_ACCENT,
            activeforeground='white', relief='flat',
            font=('Consolas',8), padx=8, pady=2)
        self._terrain_phase_btn = None
        self._sync_topbar_theme()
        self._sync_adaptive_radius_theme()
        self._sync_takedown_label_theme()



    def _sync_topbar_theme(self):
        return _ui_theme.sync_topbar_theme(self)


    def _build_left_panel(self, parent):
        return _ui_panels.build_left_panel(self, parent)


    def _build_canvas(self, parent):
        cf = tk.Frame(parent, bg=C_BG)
        self._canvas_frame = cf
        cf.grid(row=0, column=1, sticky='nsew')
        cf.rowconfigure(0, weight=1)
        cf.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(cf, bg=C_CANVAS_BG,
                                highlightthickness=0, cursor='crosshair')
        self.canvas.grid(row=0, column=0, sticky='nsew')


    def _build_status_bar(self):
        bar = tk.Frame(self, bg=C_PANEL2, height=22, bd=0)
        bar.pack(fill='x', side='bottom')
        bar.pack_propagate(False)
        self._status_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self._status_var,
                 bg=C_PANEL2, fg=C_TEXT, font=('Consolas',8),
                 anchor='w').pack(side='left', padx=8)
        self._stats_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self._stats_var,
                 bg=C_PANEL2, fg=C_DIM, font=('Consolas',8),
                 anchor='e').pack(side='right', padx=8)
        self._fps_canvas = tk.Canvas(bar, width=64, height=18,
                                     bg=C_PANEL2, highlightthickness=0, bd=0)
        self._fps_canvas.pack(side='right', padx=8)
        self._redraw_fps_counter()


    def _redraw_fps_counter(self):
        """Draw FPS text; light-panel mode uses darker amber instead of an outline."""
        c = getattr(self, '_fps_canvas', None)
        if c is None:
            return
        try:
            light = bool(getattr(self, 'light_panels', tk.BooleanVar(value=False)).get())
        except Exception:
            light = False
        bg = '#fcfcfc' if light else C_PANEL2
        fg = '#8a6500' if light else C_YELLOW
        text = self._fps_var.get() if hasattr(self, '_fps_var') else 'FPS: --'

        try:
            c.configure(bg=bg)
            c.delete('all')
            w = max(64, int(c.winfo_width() or 64))
            h = max(18, int(c.winfo_height() or 18))
            x = w - 2
            y = h // 2
            c.create_text(x, y, text=text, fill=fg, font=('Consolas', 8), anchor='e')
        except Exception:
            pass


    def _build_bytes_tab(self):
        self._bytes_frame = tk.Frame(self._tab_frame, bg=C_PANEL2)
        # Header
        hdr = tk.Frame(self._bytes_frame, bg=C_PANEL2)
        hdr.pack(fill='x', padx=4, pady=(4,0))
        tk.Label(hdr, text="Byte Inspector (b19-b105)",
                 bg=C_PANEL2, fg=C_GREEN, font=('Consolas',8,'bold')).pack(side='left')
        tk.Button(hdr, text='↺ Refresh', font=('Consolas',7),
                  bg=C_PANEL, fg=C_TEXT, relief='flat',
                  command=self._refresh_bytes_tab).pack(side='right', padx=2)
        # Scrollable text
        f = tk.Frame(self._bytes_frame, bg=C_PANEL2)
        f.pack(fill='both', expand=True, padx=4, pady=4)
        sb = tk.Scrollbar(f)
        sb.pack(side='right', fill='y')
        self._bytes_txt = tk.Text(f, yscrollcommand=sb.set, wrap='none',
                                   bg='#0a0a0a', fg='#cccccc',
                                   font=('Consolas',8), relief='flat',
                                   state='disabled')
        self._bytes_txt.pack(side='left', fill='both', expand=True)
        sb.config(command=self._bytes_txt.yview)
        self._bytes_txt.tag_config('nonzero', foreground='#f0c040')
        self._bytes_txt.tag_config('header', foreground='#44ff88', font=('Consolas',8,'bold'))
        self._bytes_txt.tag_config('dim', foreground='#555555')


    def _refresh_bytes_tab(self):
        txt = self._bytes_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')

        nodes = self.nodes
        if not nodes:
            txt.insert('end', 'No nodes loaded.', 'dim')
            txt.config(state='disabled')
            return

        # Check which nodes have raw_bytes
        nodes_with_raw = [n for n in nodes if getattr(n, 'raw_bytes', None)]
        if not nodes_with_raw:
            txt.insert('end', 'No raw byte data available.\nLoad a campaign AIN file first.', 'dim')
            txt.config(state='disabled')
            return

        txt.insert('end', f'Loaded: {len(nodes_with_raw)} nodes with raw data\n', 'header')
        txt.insert('end', f'Scanning bytes 19-105 for non-zero values...\n\n', 'dim')

        # For each byte position 19-105, find nodes with non-zero values
        found_any = False
        for byte_pos in range(19, 106):
            node_vals = []
            for n in nodes_with_raw:
                raw = n.raw_bytes
                if byte_pos < len(raw):
                    val = raw[byte_pos]
                    if val != 0:
                        node_vals.append((n.id, val))

            if node_vals:
                found_any = True
                txt.insert('end', f'byte[{byte_pos:3d}] (0x{byte_pos:02X}): ', 'header')
                txt.insert('end', f'{len(node_vals)} nodes with non-zero values\n')
                # Show first 10 examples
                for nid, val in node_vals[:10]:
                    txt.insert('end', f'  node {nid:5d}: ', 'dim')
                    txt.insert('end', f'{val:3d} (0x{val:02X})\n', 'nonzero')
                if len(node_vals) > 10:
                    txt.insert('end', f'  ... and {len(node_vals)-10} more\n', 'dim')
                txt.insert('end', '\n')

        if not found_any:
            txt.insert('end', 'All bytes 19-105 are zero across all nodes.\n', 'dim')
            txt.insert('end', 'Record stride appears to be exactly 52 bytes (0x34).\n', 'dim')

        txt.config(state='disabled')


    def _build_info_tab(self):
        self._info_frame = tk.Frame(self._tab_frame, bg=C_PANEL2)
        self._inspector_field_labels = {}
        self._inspector_field_rows = {}

        def lrow(parent, label, var, entry=True, width=10, key=None):
            f = tk.Frame(parent, bg=C_PANEL2)
            f.pack(fill='x', padx=4, pady=1)
            label_widget = tk.Label(
                f, text=label, bg=C_PANEL2, fg=C_TEXT,
                font=('Consolas',8), anchor='w')
            if key:
                self._inspector_field_labels[key] = label_widget
                self._inspector_field_rows[key] = f
            if entry:
                e = tk.Entry(f, textvariable=var, width=width,
                             bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                             font=('Consolas',8), relief='flat', bd=2)
                # Keep the editable value compact and pinned to the panel's
                # right edge.  The label owns the remaining width so longer,
                # descriptive field names can replace the old b# labels.
                e.pack(side='right')
                label_widget.pack(side='left', fill='x', expand=True)
                return e
            else:
                value_widget = tk.Label(
                    f, textvariable=var, bg=C_PANEL2, fg=C_TEXT,
                    font=('Consolas',8), width=width, anchor='e')
                value_widget.pack(side='right')
                label_widget.pack(side='left', fill='x', expand=True)
                return None

        def highlighted_brow(parent, key, label, var, width=10):
            """Important byte row: subtle row highlight + left accent strip."""
            f = tk.Frame(parent, bg=C_PANEL2)
            f.pack(fill='x', padx=4, pady=1)
            bar = tk.Frame(f, width=4, bg='#3399ff')
            bar.pack(side='left', fill='y', padx=(0,4))
            bar.pack_propagate(False)
            lbl = tk.Label(f, text=label, bg=C_PANEL2, fg=C_TEXT,
                           font=('Consolas',8,'bold'), anchor='w')
            e = tk.Entry(f, textvariable=var, width=width,
                         bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                         font=('Consolas',8,'bold'), relief='flat', bd=2)
            e.pack(side='right')
            lbl.pack(side='left', fill='x', expand=True)
            try:
                self._byte_flag_highlight_widgets[key] = {
                    'frame': f,
                    'bar': bar,
                    'label': lbl,
                    'entry': e,
                }
                self._inspector_field_labels[key] = lbl
                self._inspector_field_rows[key] = f
            except Exception:
                pass
            return e

        # Node ID (read-only)
        self._v_id  = tk.StringVar(value="None")
        self._v_x   = tk.StringVar(value="")
        self._v_y   = tk.StringVar(value="")
        self._v_z   = tk.StringVar(value="")
        self._v_b12 = tk.StringVar(value="")
        self._v_b13 = tk.StringVar(value="")
        self._v_b14 = tk.StringVar(value="")
        self._v_b15 = tk.StringVar(value="")
        self._v_b16 = tk.StringVar(value="")

        # Lock options
        self._lock_values      = tk.BooleanVar(value=False)
        self._lock_z           = tk.BooleanVar(value=False)
        self._lock_options_visible = tk.BooleanVar(value=False)
        self._neighbors_visible = tk.BooleanVar(value=True)
        self._locked_b12       = tk.IntVar(value=0)
        self._locked_b15       = tk.IntVar(value=36)
        self._locked_z         = tk.DoubleVar(value=0.0)
        self._v_b17 = tk.StringVar(value="")
        self._v_b18 = tk.StringVar(value="")
        self._inspector_details_on = tk.BooleanVar(value=False)
        self._byte_flag_highlight_widgets = {}

        # Node Inspector header row with Details and — buttons
        _hdr = tk.Frame(self._info_frame, bg=C_PANEL2)
        _hdr.pack(fill='x', padx=4, pady=(4,2))
        tk.Label(_hdr, text="Node Inspector",
                 bg=C_PANEL2, fg=C_GREEN, font=('Consolas',8,'bold'),
                 anchor='w').pack(side='left')
        self._help_btn = tk.Button(_hdr, text='?', font=('Consolas',9,'bold'),
                                   bg=C_PANEL2, fg='#f0c040',
                                   activebackground=C_BORDER, activeforeground='#f0c040',
                                   relief='solid', bd=1, cursor='hand2', width=2,
                                   command=self._toggle_node_help)
        self._help_btn.pack(side='right', padx=2)
        self._details_btn = tk.Button(_hdr, text='Details', font=('Consolas',9),
                                      bg=C_PANEL2, fg='#ffffff',
                                      activebackground=C_BORDER, activeforeground='#ffffff',
                                      relief='solid', bd=1, cursor='hand2',
                                      command=self._toggle_node_inspector_details)
        self._details_btn.pack(side='right', padx=(0,2))

        lrow(self._info_frame, "ID:",  self._v_id, entry=False)
        _x_row = tk.Frame(self._info_frame, bg=C_PANEL2)
        _x_row.pack(fill='x', padx=4, pady=1)
        tk.Label(_x_row, text="X:", bg=C_PANEL2, fg=C_LABEL,
                 font=('Consolas',8), anchor='w').pack(side='left', fill='x', expand=True)
        self._e_x = tk.Entry(_x_row, textvariable=self._v_x, width=10,
                             bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                             font=('Consolas',8), relief='flat', bd=2)
        self._e_x.pack(side='right')
        tk.Button(_x_row, text='+', font=('Consolas',7,'bold'),
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                  relief='solid', bd=1, width=2, cursor='hand2',
                  command=lambda: self._nudge_coord(self._v_x, 0.1)).pack(side='right', padx=1)
        tk.Button(_x_row, text='−', font=('Consolas',7,'bold'),
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                  relief='solid', bd=1, width=2, cursor='hand2',
                  command=lambda: self._nudge_coord(self._v_x, -0.1)).pack(side='right', padx=1)

        _y_row = tk.Frame(self._info_frame, bg=C_PANEL2)
        _y_row.pack(fill='x', padx=4, pady=1)
        tk.Label(_y_row, text="Y:", bg=C_PANEL2, fg=C_LABEL,
                 font=('Consolas',8), anchor='w').pack(side='left', fill='x', expand=True)
        self._e_y = tk.Entry(_y_row, textvariable=self._v_y, width=10,
                             bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                             font=('Consolas',8), relief='flat', bd=2)
        self._e_y.pack(side='right')
        tk.Button(_y_row, text='+', font=('Consolas',7,'bold'),
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                  relief='solid', bd=1, width=2, cursor='hand2',
                  command=lambda: self._nudge_coord(self._v_y, 0.1)).pack(side='right', padx=1)
        tk.Button(_y_row, text='−', font=('Consolas',7,'bold'),
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                  relief='solid', bd=1, width=2, cursor='hand2',
                  command=lambda: self._nudge_coord(self._v_y, -0.1)).pack(side='right', padx=1)

        _z_row = tk.Frame(self._info_frame, bg=C_PANEL2)
        _z_row.pack(fill='x', padx=4, pady=1)
        tk.Label(_z_row, text="Z:", bg=C_PANEL2, fg=C_LABEL,
                 font=('Consolas',8), anchor='w').pack(side='left')
        self._e_z = tk.Entry(_z_row, textvariable=self._v_z, width=10,
                             bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                             font=('Consolas',8), relief='flat', bd=2)
        self._e_z.pack(side='right')
        tk.Button(_z_row, text='+', font=('Consolas',7,'bold'),
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                  relief='solid', bd=1, width=2, cursor='hand2',
                  command=lambda: self._nudge_coord(self._v_z, 0.1)).pack(side='right', padx=1)
        tk.Button(_z_row, text='−', font=('Consolas',7,'bold'),
                  bg=C_PANEL2, fg=C_TEXT, activebackground=C_BORDER,
                  relief='solid', bd=1, width=2, cursor='hand2',
                  command=lambda: self._nudge_coord(self._v_z, -0.1)).pack(side='right', padx=1)
        self._terrain_z_label = tk.Label(
            _z_row, text="", bg=C_PANEL2, fg=C_DIM,
            font=('Consolas',8), anchor='e')
        self._terrain_z_label.pack(side='right', padx=(0,4))

        tk.Frame(self._info_frame, bg=C_BORDER, height=1).pack(fill='x', padx=4, pady=2)
        _byte_hdr = tk.Frame(self._info_frame, bg=C_PANEL2)
        _byte_hdr.pack(fill='x', padx=4)
        self._byte_flags_title = tk.Label(_byte_hdr, text="Node properties:",
                 bg=C_PANEL2, fg=C_LABEL, font=('Consolas',8,'bold'),
                 anchor='w')
        self._byte_flags_title.pack(side='left')

        self._e_b12 = highlighted_brow(
            self._info_frame, 'b12', "Control:", self._v_b12)
        self._e_b15 = highlighted_brow(
            self._info_frame, 'b15', "Radius:", self._v_b15)
        self._sync_byte_flag_highlights()

        self._inspector_details_frame = tk.Frame(self._info_frame, bg=C_PANEL2)

        self._e_b13 = lrow(
            self._inspector_details_frame, "Takedown (var1):", self._v_b13, key='b13')

        # b14 zone with colour swatch
        f14 = tk.Frame(self._inspector_details_frame, bg=C_PANEL2)
        f14.pack(fill='x', padx=4, pady=1)
        f14_label = tk.Label(
            f14, text="Takedown Zone:", bg=C_PANEL2, fg=C_TEXT,
            font=('Consolas',8), anchor='w')
        self._e_b14_label = f14_label
        self._zone_id_validate = self.register(
            lambda proposed: (proposed == '' or
                              (proposed.isdigit() and int(proposed) <= 255)))
        self._e_b14 = tk.Entry(f14, textvariable=self._v_b14, width=5,
                               bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                               font=('Consolas',8), relief='flat', bd=2,
                               validate='key',
                               validatecommand=(self._zone_id_validate, '%P'))
        self._e_b14.pack(side='right')
        self._zone_swatch = tk.Label(f14, text="  ", bg='white',
                                     relief='flat', width=2)
        self._zone_swatch.pack(side='right', padx=2)
        f14_label.pack(side='left', fill='x', expand=True)
        self._inspector_field_labels['b14'] = f14_label
        self._inspector_field_rows['b14'] = f14

        self._inspector_direction_spacer = tk.Label(
            self._inspector_details_frame, textvariable=tk.StringVar(value=""),
            bg=C_PANEL2, fg=C_DIM, font=('Consolas',7))
        self._inspector_direction_spacer.pack()
        self._e_b16 = lrow(
            self._inspector_details_frame, "Direction:", self._v_b16, key='b16')
        self._e_b17 = lrow(
            self._inspector_details_frame, "Takedown (var2):", self._v_b17, key='b17')
        # b18 remains hidden in the simplified UI, but Developer mode reveals
        # the existing editable field without changing its load/apply/export path.
        self._e_b18 = lrow(
            self._inspector_details_frame, "b18:", self._v_b18, key='b18')
        self._sync_dev_mode_inspector()

        # Apply button
        self._info_apply_btn = tk.Button(self._info_frame, text="Apply Changes",
                                         command=self._apply_node_changes,
                                         bg=C_ACCENT, fg='white', font=('Consolas',8),
                                         relief='flat', pady=2)
        self._info_apply_btn.pack(fill='x', padx=4, pady=4)

        def _enter_applies(_event=None):
            self._apply_node_changes()
            return 'break'

        self._validation_tooltip = None
        def _show_validation_tooltip(widget):
            if self._validation_tooltip is not None:
                try:
                    self._validation_tooltip.destroy()
                except Exception:
                    pass
                self._validation_tooltip = None
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            tip.attributes('-topmost', True)
            tip.configure(bg='#1a1a2e')
            frm = tk.Frame(tip, bg='#1a1a2e', highlightbackground='#ff4444',
                           highlightthickness=1)
            frm.pack()
            tk.Label(frm, text="Numbers only", bg='#1a1a2e', fg='#ff6666',
                     font=('Consolas', 8, 'bold'), padx=6, pady=2).pack()
            wx = widget.winfo_rootx()
            wy = widget.winfo_rooty()
            tip.geometry(f'+{wx}+{wy - 24}')
            self._validation_tooltip = tip
            tip.after(2000, lambda: _dismiss_tooltip(tip))

        def _dismiss_tooltip(tip):
            try:
                tip.destroy()
            except Exception:
                pass
            if self._validation_tooltip is tip:
                self._validation_tooltip = None

        _coord_entries = {self._e_x, self._e_y, self._e_z}
        def _on_inspector_key(event):
            ch = event.char
            if not ch or ord(ch) < 32:
                return None
            if ch in ('a', 'A'):
                self._apply_node_changes()
                return 'break'
            widget = event.widget
            if widget in _coord_entries:
                if ch in '0123456789.-':
                    return None
            else:
                if ch in '0123456789':
                    return None
            _show_validation_tooltip(widget)
            return 'break'

        for _entry_widget in (
            self._e_x, self._e_y, self._e_z,
            self._e_b12, self._e_b15,
            self._e_b13, self._e_b14, self._e_b16, self._e_b17, self._e_b18,
        ):
            if _entry_widget is not None:
                _entry_widget.bind('<Return>', _enter_applies)
                _entry_widget.bind('<KeyPress>', _on_inspector_key)

        # ── Adaptive radius ───────────────────────────
        # This affects newly placed nodes, so keep it with the node-editing
        # controls instead of the global top bar.  Large Radius remains a rare
        # extension and only appears while Adaptive radius is enabled.
        adaptive_row = tk.Frame(self._info_frame, bg=C_PANEL2)
        adaptive_row.pack(fill='x', padx=4, pady=(0, 2))
        self._adaptive_r_btn = tk.Checkbutton(
            adaptive_row, text='Adaptive radius',
            variable=self.adaptive_radius,
            command=self._on_adaptive_r_toggle,
            bg=C_PANEL2, fg=C_LABEL, selectcolor=C_PANEL,
            activebackground=C_PANEL2, activeforeground=C_TEXT,
            font=('Consolas', 9), bd=0, highlightthickness=0, anchor='w')
        self._adaptive_r_btn.pack(side='left', fill='x', expand=True)

        self._adaptive_large_r_btn = tk.Checkbutton(
            self._info_frame, text='Large Radius (48+)',
            variable=self.adaptive_large_r,
            command=lambda: self.adaptive_xlarge_r.set(False) if self.adaptive_large_r.get() else None,
            bg=C_PANEL2, fg=C_LABEL, selectcolor=C_PANEL,
            activebackground=C_PANEL2, activeforeground=C_TEXT,
            font=('Consolas', 9), bd=0, highlightthickness=0, anchor='w')
        self._adaptive_xlarge_r_btn = tk.Checkbutton(
            self._info_frame, text='XL Radius (128+)',
            variable=self.adaptive_xlarge_r,
            command=lambda: self.adaptive_large_r.set(False) if self.adaptive_xlarge_r.get() else None,
            bg=C_PANEL2, fg=C_LABEL, selectcolor=C_PANEL,
            activebackground=C_PANEL2, activeforeground=C_TEXT,
            font=('Consolas', 9), bd=0, highlightthickness=0, anchor='w')

        self._place_nodes_on_entities_btn = tk.Checkbutton(
            self._info_frame, text='Place on entities',
            variable=self.place_nodes_on_entities,
            command=self._on_place_nodes_on_entities_toggle,
            bg=C_PANEL2, fg=C_LABEL, selectcolor=C_PANEL,
            activebackground=C_PANEL2, activeforeground=C_TEXT,
            font=('Consolas', 9), bd=0, highlightthickness=0, anchor='w')
        self._place_nodes_on_entities_btn.pack(
            fill='x', padx=4, pady=(0, 2))

        # ── Lock options ──────────────────────────────
        self._adaptive_lock_separator = tk.Frame(
            self._info_frame, bg=C_BORDER, height=1)
        self._adaptive_lock_separator.pack(fill='x', padx=4, pady=(2,2))
        if self.adaptive_radius.get():
            self._adaptive_large_r_btn.pack(
                fill='x', padx=(20, 4), pady=(0, 2),
                before=self._place_nodes_on_entities_btn)
            self._adaptive_xlarge_r_btn.pack(
                fill='x', padx=(20, 4), pady=(0, 2),
                before=self._place_nodes_on_entities_btn)
        lock_hdr = tk.Frame(self._info_frame, bg=C_PANEL2)
        lock_hdr.pack(fill='x', padx=4, pady=(0,2))
        tk.Label(lock_hdr, text="Lock options",
                 bg=C_PANEL2, fg=C_LABEL, font=('Consolas',9,'bold'),
                 anchor='w').pack(side='left')
        self._lock_options_toggle_btn = tk.Button(
            lock_hdr, text="Show", command=self._toggle_lock_options_panel,
            bg=C_PANEL2, fg='#ffffff', font=('Consolas',8,'bold'),
            relief='solid', bd=1, padx=8, pady=1, width=6,
            activebackground=C_BORDER, activeforeground='#ffffff')
        self._lock_options_toggle_btn.pack(side='right')

        self._lock_options_frame = tk.Frame(self._info_frame, bg=C_PANEL2)

        # Lock values row
        lv_row = tk.Frame(self._lock_options_frame, bg=C_PANEL2)
        lv_row.pack(fill='x', padx=4, pady=1)
        self._lock_values_cb = tk.Checkbutton(lv_row, text="Lock values",
                       variable=self._lock_values,
                       bg=C_PANEL2, fg=C_LABEL, selectcolor=C_PANEL,
                       activebackground=C_PANEL2, activeforeground=C_TEXT,
                       font=('Consolas',9), anchor='w',
                       command=self._on_lock_values_toggle
                       ).pack(side='left')
        tk.Button(lv_row, text="Update",
                  command=self._capture_locked_values,
                  bg='#505050', fg='#cccccc', font=('Consolas',8),
                  relief='flat', padx=7, pady=1,
                  activebackground='#606060',
                  activeforeground='#ffffff').pack(side='right')

        # Locked value display
        self._locked_values_label = tk.Label(
            self._lock_options_frame,
            text="Control=? Radius=?",
            bg=C_PANEL2, fg=C_LABEL, font=('Consolas',8), anchor='w')
        self._locked_values_label.pack(fill='x', padx=12, pady=(0,2))

        # Lock Z row
        lz_row = tk.Frame(self._lock_options_frame, bg=C_PANEL2)
        lz_row.pack(fill='x', padx=4, pady=1)
        tk.Checkbutton(lz_row, text="Lock Z",
                       variable=self._lock_z,
                       bg=C_PANEL2, fg=C_LABEL, selectcolor=C_PANEL,
                       activebackground=C_PANEL2, activeforeground=C_TEXT,
                       font=('Consolas',9), anchor='w',
                       command=self._on_lock_z_toggle
                       ).pack(side='left')
        tk.Button(lz_row, text="Update",
                  command=self._capture_locked_z,
                  bg='#505050', fg='#cccccc', font=('Consolas',8),
                  relief='flat', padx=7, pady=1,
                  activebackground='#606060',
                  activeforeground='#ffffff').pack(side='right')

        # Locked Z display/entry
        lz_val_row = tk.Frame(self._lock_options_frame, bg=C_PANEL2)
        lz_val_row.pack(fill='x', padx=12, pady=(0,4))
        tk.Label(lz_val_row, text="Z:", bg=C_PANEL2, fg=C_LABEL,
                 font=('Consolas',8), width=3, anchor='w').pack(side='left')
        tk.Entry(lz_val_row, textvariable=self._locked_z, width=8,
                 bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
                 font=('Consolas',8), relief='flat').pack(side='left')
        tk.Label(lz_val_row, text="m", bg=C_PANEL2, fg=C_LABEL,
                 font=('Consolas',8)).pack(side='left', padx=2)

        self._sync_lock_options_panel()

        self._lock_options_after_widget = tk.Frame(self._info_frame, bg=C_BORDER, height=1)
        self._lock_options_after_widget.pack(fill='x', padx=4, pady=2)

        # Neighbors section — collapsible, matching Lock options.
        neighbors_hdr = tk.Frame(self._info_frame, bg=C_PANEL2)
        neighbors_hdr.pack(fill='x', padx=4, pady=(0, 2))
        self._neighbors_label = tk.Label(
            neighbors_hdr, text="Neighbors: 0",
            bg=C_PANEL2, fg=C_TEXT, font=('Consolas',8,'bold'),
            anchor='w')
        self._neighbors_label.pack(side='left', fill='x', expand=True)
        self._neighbors_toggle_btn = tk.Button(
            neighbors_hdr, text="Hide", command=self._toggle_neighbors_panel,
            bg=C_PANEL2, fg='#ffffff', font=('Consolas',8,'bold'),
            relief='solid', bd=1, padx=8, pady=1, width=6,
            activebackground=C_BORDER, activeforeground='#ffffff')
        self._neighbors_toggle_btn.pack(side='right')

        self._neighbors_body = tk.Frame(self._info_frame, bg=C_PANEL2)
        self._neighbors_body.pack(fill='x')

        nb_frame = tk.Frame(self._neighbors_body, bg=C_PANEL2)
        nb_frame.pack(fill='x', padx=4)
        self._nb_scrollbar = tk.Scrollbar(nb_frame, orient='vertical')
        self._nb_list = tk.Listbox(nb_frame, height=5, width=18,
                                   bg=C_PANEL, fg=C_TEXT,
                                   selectbackground=C_ACCENT,
                                   font=('Consolas',8),
                                   yscrollcommand=self._nb_scrollbar.set,
                                   relief='flat', bd=0)
        self._nb_scrollbar.config(command=self._nb_list.yview)
        self._nb_list.pack(side='left', fill='x', expand=True)
        self._nb_scrollbar.pack(side='right', fill='y')

        # Stop mouse wheel events from bubbling up to the panel scroller.
        # 'break' tells tkinter: event handled here, don't propagate further.
        # Without this, scrolling the neighbours list scrolls the whole panel.
        def _nb_wheel(e):
            self._nb_list.yview_scroll(int(-1*(e.delta/120)) if e.delta else
                                       (-1 if e.num==4 else 1), 'units')
            return 'break'  # stop propagation to panel canvas
        self._nb_list.bind('<MouseWheel>', _nb_wheel)
        self._nb_list.bind('<Button-4>',   _nb_wheel)
        self._nb_list.bind('<Button-5>',   _nb_wheel)

        btn_row = tk.Frame(self._neighbors_body, bg=C_PANEL2)
        btn_row.pack(fill='x', padx=4, pady=2)
        tk.Button(btn_row, text="Remove",
                  command=self._remove_neighbor,
                  bg='#660000', fg='white', font=('Consolas',8),
                  relief='flat', padx=5, pady=1).pack(side='left', padx=2)
        tk.Button(btn_row, text="Connect with ID",
                  command=self._show_connect_id_overlay,
                  bg='#004466', fg='white', font=('Consolas',8),
                  relief='flat', padx=5, pady=1).pack(side='left', padx=2)

        self._neighbors_after_separator = tk.Frame(self._info_frame, bg=C_BORDER, height=1)
        self._neighbors_after_separator.pack(fill='x', padx=4, pady=2)
        self._sync_neighbors_panel()

        # Delete node button
        tk.Button(self._info_frame, text="Delete Node",
                  command=self._delete_selected_node,
                  bg='#880000', fg='white', font=('Consolas',9),
                  relief='flat', pady=3).pack(fill='x', padx=4, pady=2)

        # Bind entry changes
        for var in [self._v_b14]:
            var.trace_add('write', self._on_zone_change)


    def _build_tiler_tab(self):
        self._tiler_frame = tk.Frame(self._tab_frame, bg=C_PANEL2)
        self._paint_zone_rows = {}
        self._paint_zone_row_labels = {}
        self._paint_zone_swatch_labels = {}

        self._tiler_painter_box = tk.Frame(
            self._tiler_frame,
            bg=C_PANEL2,
            highlightthickness=1,
            highlightbackground=C_BORDER,
            highlightcolor=C_BORDER,
            bd=0,
        )
        self._tiler_painter_box.pack(fill='x', padx=4, pady=(4, 2))

        header = tk.Frame(self._tiler_painter_box, bg=C_PANEL2)
        header.pack(fill='x', padx=4, pady=(4, 2))
        tk.Label(header, text="Zone Painter",
                 bg=C_PANEL2, fg=C_GREEN, font=('Consolas',8,'bold'),
                 anchor='w').pack(side='left')
        self._paint_active_badge = tk.Label(
            header,
            text="INACTIVE",
            bg=C_PANEL,
            fg=C_DIM,
            font=('Consolas',7,'bold'),
            padx=5,
            pady=1,
        )
        self._paint_active_badge.pack(side='right')

        tk.Label(self._tiler_painter_box,
                 text="Paint zone ID\non nodes in\nPaint mode.\n\nSelect zone:",
                 bg=C_PANEL2, fg=C_TEXT, font=('Consolas',8),
                 justify='left').pack(fill='x', padx=4, pady=2)

        self._paint_zone = tk.IntVar(value=0)
        for z in range(8):
            row_bg = C_PANEL2
            f = tk.Frame(self._tiler_painter_box, bg=row_bg,
                         highlightthickness=1, highlightbackground=C_PANEL2,
                         highlightcolor=C_PANEL2, bd=0)
            f.pack(fill='x', padx=4, pady=1)
            rb = tk.Radiobutton(
                f, text=f"Zone {z}",
                variable=self._paint_zone, value=z,
                bg=row_bg, fg=C_TEXT,
                selectcolor=ZONE_COLORS[z % len(ZONE_COLORS)],
                activebackground=row_bg,
                activeforeground=C_TEXT,
                font=('Consolas',8),
                anchor='w',
            )
            rb.pack(side='left')
            swatch = tk.Label(f, text="  ",
                              bg=ZONE_COLORS[z % len(ZONE_COLORS)],
                              width=3)
            swatch.pack(side='left', padx=2)
            self._paint_zone_rows[z] = f
            self._paint_zone_row_labels[z] = rb
            self._paint_zone_swatch_labels[z] = swatch

        # Custom zone selector.  The engine field is one byte, so IDs 8-255
        # are available here while the common 0-7 choices stay one-click rows.
        custom_row = tk.Frame(self._tiler_painter_box, bg=C_PANEL2,
                              highlightthickness=1, highlightbackground=C_PANEL2,
                              highlightcolor=C_PANEL2, bd=0)
        custom_row.pack(fill='x', padx=4, pady=1)
        self._paint_custom_zone_row = custom_row
        self._paint_custom_zone = tk.StringVar(value='8')
        self._paint_custom_zone_radio = tk.Radiobutton(
            custom_row, text='Zone',
            variable=self._paint_zone, value=8,
            bg=C_PANEL2, fg=C_TEXT,
            selectcolor=ZONE_COLORS[8 % len(ZONE_COLORS)],
            activebackground=C_PANEL2, activeforeground=C_TEXT,
            font=('Consolas',8), anchor='w')
        self._paint_custom_zone_radio.pack(side='left')

        def _valid_custom_zone(proposed):
            if proposed == '':
                return True
            return proposed.isdigit() and int(proposed) <= 255

        self._paint_custom_zone_entry = tk.Entry(
            custom_row, textvariable=self._paint_custom_zone, width=4,
            justify='center', font=('Consolas',8),
            bg=C_PANEL, fg=C_TEXT, insertbackground=C_TEXT,
            relief='sunken', bd=1,
            validate='key',
            validatecommand=(self.register(_valid_custom_zone), '%P'))
        self._paint_custom_zone_entry.pack(side='left', padx=(2, 3))
        self._paint_custom_zone_swatch = tk.Label(
            custom_row, text='  ', bg=ZONE_COLORS[8 % len(ZONE_COLORS)], width=3)
        self._paint_custom_zone_swatch.pack(side='left', padx=2)

        def _custom_zone_changed(*_):
            try:
                zid = int(self._paint_custom_zone.get())
            except Exception:
                return
            # 0-7 already have dedicated rows.  Intermediate one-digit values
            # are allowed while typing a larger ID but do not replace them.
            if not (8 <= zid <= 255):
                return
            try:
                self._paint_custom_zone_radio.configure(value=zid)
                self._paint_custom_zone_swatch.configure(
                    bg=ZONE_COLORS[zid % len(ZONE_COLORS)])
            except Exception:
                pass
            if int(self._paint_zone.get()) != zid:
                self._paint_zone.set(zid)
            else:
                self._update_paint_tool_ui()

        self._paint_custom_zone.trace_add('write', _custom_zone_changed)
        self._paint_custom_zone_entry.bind(
            '<Return>', lambda _e: (_custom_zone_changed(), 'break')[1])

        tk.Frame(self._tiler_painter_box, bg=C_BORDER, height=1).pack(fill='x', padx=4, pady=4)

        radius_row = tk.Frame(self._tiler_painter_box, bg=C_PANEL2)
        radius_row.pack(fill='x', padx=4)
        tk.Label(radius_row,
                 text="Paint radius (m):",
                 bg=C_PANEL2, fg=C_DIM, font=('Consolas',9)).pack(side='left')
        self._paint_radius_value_label = tk.Label(
            radius_row,
            text="5.0",
            bg=C_PANEL2,
            fg=C_TEXT,
            font=('Consolas',8,'bold'),
        )
        self._paint_radius_value_label.pack(side='right')
        self._paint_radius = tk.DoubleVar(value=5.0)
        self._paint_radius_scale = tk.Scale(self._tiler_painter_box, variable=self._paint_radius,
                 from_=1.0, to=30.0, resolution=0.5,
                 orient='horizontal', bg=C_PANEL2, fg=C_TEXT,
                 troughcolor=C_PANEL, highlightthickness=0,
                 font=('Consolas',7))
        self._paint_radius_scale.pack(fill='x', padx=4)

        self._paint_zone.trace_add('write', lambda *_: self._update_paint_tool_ui())
        self._paint_radius.trace_add('write', lambda *_: self._update_paint_tool_ui())



    def _build_zone_panel(self, parent):
        return _ui_panels.build_zone_panel(self, parent)



    def _build_debug_bar(self):
        """Build the hidden debug overlay bar (shown via F9 / Edit > Debug Values)."""
        bar = tk.Frame(self, bg='#1a0a00', bd=0, height=30)
        bar.pack_propagate(False)
        # Don't pack yet — hidden by default
        self._debug_bar_frame = bar
        self._debug_bar_color_widgets = []

        self._debug_bar_title = tk.Label(
            bar, text="DBG:", bg='#1a0a00', fg='#ff8800',
            font=('Consolas',8,'bold'))
        self._debug_bar_title.pack(side='left', padx=(8,4))

        def _dbg_toggle(var, label, color, light_color):
            """Make a toggle button that colorizes nodes by a raw byte field."""
            btn_var = tk.StringVar()
            def _update():
                if var.get():
                    btn_var.set(f"[{label}]")
                else:
                    btn_var.set(label)
                self.redraw()
            var.trace_add('write', lambda *_: _update())
            btn = tk.Checkbutton(
                bar, textvariable=btn_var, variable=var,
                bg='#1a0a00', fg=color, selectcolor='#330000',
                activebackground='#1a0a00', activeforeground=color,
                font=('Consolas',8), relief='flat',
                indicatoron=False, padx=6, pady=1,
                command=self.redraw)
            btn._debug_dark_fg = color
            btn._debug_light_fg = light_color
            self._debug_bar_color_widgets.append(btn)
            btn_var.set(label)
            btn.pack(side='left', padx=2)
            return btn

        # Dark-mode colours remain exactly as before.  The second colour is a
        # light-mode-only, darker companion chosen for contrast on #fff1e8.
        _dbg_toggle(self._dbg_b12_hidden,  "b12 hi-bits",  '#ffaa00', '#a85a00')
        tk.Frame(bar, bg='#333', width=1).pack(side='left', fill='y', padx=4)
        _dbg_toggle(self._dbg_b13_full,    "b13 full",     '#ff66ff', '#a500a5')
        tk.Frame(bar, bg='#333', width=1).pack(side='left', fill='y', padx=4)
        _dbg_toggle(self._dbg_edge_flags,  "+0x28 EdgeFlags", '#00ffcc', '#007b67')
        _dbg_toggle(self._dbg_nc_mismatch, "+0x28 inactive slots", '#ff4444', '#b52a2a')
        _dbg_toggle(self._dbg_construction_strata, "construction strata", '#7cffb2', '#1b7f48')
        tk.Frame(bar, bg='#333', width=1).pack(side='left', fill='y', padx=4)
        _dbg_toggle(self._dbg_0x32_low,    "+0x32 lo",       '#44aaff', '#0069a8')
        _dbg_toggle(self._dbg_0x32_high,   "+0x32 hi (cat)", '#88ddff', '#237a9a')
        _dbg_toggle(self._dbg_0x32_uint16, "+0x32 u16 norm", '#aaeeff', '#3a7488')
        tk.Frame(bar, bg='#333', width=1).pack(side='left', fill='y', padx=4)

        self._debug_bar_legend = tk.Label(
            bar,
            text="Strata: 00/00=green  01/01=red  FF/FF=orange  mismatch=mixed  |  u16=synthetic composite",
            bg='#1a0a00', fg='#555', font=('Consolas',7))
        self._debug_bar_legend.pack(side='left', padx=8)

        self._debug_bar_close = tk.Button(
            bar, text="✕", command=self._toggle_debug_bar,
            bg='#1a0a00', fg='#ff4444', font=('Consolas',9,'bold'),
            relief='flat', padx=4)
        self._debug_bar_close.pack(side='right', padx=6)
        self._sync_debug_bar_theme()


    def _sync_adaptive_radius_theme(self):
        return _ui_theme.sync_adaptive_radius_theme(self)


    def _sync_takedown_label_theme(self):
        return _ui_theme.sync_takedown_label_theme(self)


    def _sync_debug_bar_theme(self):
        return _ui_theme.sync_debug_bar_theme(self)

    # === scan_applier.py (ScanApplierMixin) ===

    """Methods for the b12/b16 scan applier overlay in AINEditor."""

    # ─── SCAN APPLIER ─────────────────────────────────────────────────────────

    def _enter_scan_mode(self):
        self._hide_ain_pass_overlay()
        self._ain_pass_mode = 'scan'
        self._show_scan_hud()
        self.status('Scan Applier: configure channels and click Apply.')

    def _show_scan_hud(self):
        return _generator_progress_ui.show_scan_hud(self)

    def _scan_parse_radius(self):
        try:
            r = float(str(self._scan_radius_var.get()).strip())
            return max(0.0, r)
        except Exception:
            return 200.0

    def _scan_apply(self):
        """Arm one local scanner disc; the next canvas click chooses its center."""
        if not self.nodes:
            self.status('Scan Applier: no nodes loaded.')
            return

        scan_radius = self._scan_parse_radius()
        if scan_radius <= 0.0:
            self.status('Scan Applier: radius must be greater than 0 m.')
            return

        # Freeze this invocation's settings now.  The popup is destroyed before
        # the map click, so _scan_apply_at must not depend on live widget state.
        self._scan_seed_radius = float(scan_radius)
        self._scan_seed_do_b12 = bool(self._scan_b12_var.get())
        self._scan_seed_do_b16 = bool(self._scan_b16_var.get())
        try:
            scan_pct = int(self._scan_percentage_var.get())
        except Exception:
            scan_pct = 50
        self._scan_seed_percentage = max(0, min(100, int(round(scan_pct / 5.0) * 5)))
        try:
            requested_b12 = int(str(self._scan_b12_value_var.get()).strip(), 0)
        except Exception:
            requested_b12 = -1
        if self._scan_seed_do_b12 and not (0 <= requested_b12 <= 255):
            try:
                messagebox.showerror(
                    'Invalid B12 value',
                    'B12 must be between 0 and 255.')
            except Exception:
                self.status('Scan Applier: B12 must be between 0 and 255.')
            return
        self._scan_seed_b12_value = int(requested_b12 if requested_b12 >= 0 else 0)
        self._scan_seed_b12_exact = bool(self._scan_b12_exact_var.get())

        self._scan_seed_b12_additional_exact = bool(
            self._scan_b12_additional_exact_var.get())
        additional_values = []
        if self._scan_seed_do_b12:
            raw_additional = str(
                self._scan_b12_additional_values_var.get())
            if raw_additional.strip():
                tokens = raw_additional.split(',')
                for token in tokens:
                    stripped = token.strip()
                    if (not stripped or
                            re.fullmatch(r'(?:0[xX][0-9A-Fa-f]+|[0-9]+)', stripped) is None):
                        try:
                            messagebox.showerror(
                                'Invalid B12 value',
                                f'Additional B12 value "{stripped or token}" is not recognized.\\n'
                                'Use comma-separated values such as 12,24 or 12 , 24.')
                        except Exception:
                            self.status('Scan Applier: unrecognized additional B12 value.')
                        return
                    try:
                        value = int(stripped, 0)
                    except Exception:
                        value = -1
                    if not (0 <= value <= 255):
                        try:
                            messagebox.showerror(
                                'Invalid B12 value',
                                f'Additional B12 value {stripped} is outside 0–255.')
                        except Exception:
                            self.status('Scan Applier: Additional B12 maximum is 255.')
                        return
                    additional_values.append(value)
        self._scan_seed_b12_additional_values = tuple(additional_values)

        self._scan_seed_b12_replace_even = bool(self._scan_b12_replace_even_var.get())
        self._scan_seed_b12_replace_odd = bool(self._scan_b12_replace_odd_var.get())
        self._scan_seed_armed = True

        # Keep Claude's popup/options; Apply closes it and hands control to the
        # same kind of map-space placement interaction used by Generate.
        hud = getattr(self, '_scan_hud', None)
        self._scan_hud = None
        try:
            if hud is not None and hud.winfo_exists():
                hud.destroy()
        except Exception:
            pass
        try:
            self.canvas.configure(cursor='target')
        except Exception:
            pass

        # Draw the disc immediately at the current pointer position.  Without
        # this, destroying the popup can leave no <Motion> event until the mouse
        # moves again, making the armed scanner look like it has no visual.
        try:
            px = self.winfo_pointerx() - self.canvas.winfo_rootx()
            py = self.winfo_pointery() - self.canvas.winfo_rooty()
            if 0 <= px <= self.canvas.winfo_width() and 0 <= py <= self.canvas.winfo_height():
                self._update_scan_radius_preview(px, py)
        except Exception:
            pass

        self.status(
            f'Scan Applier armed: move the {scan_radius:.1f}m disc and click its center.'
        )

    def _cancel_scan_seed(self, reason='cancelled'):
        if not bool(getattr(self, '_scan_seed_armed', False)):
            return False
        self._scan_seed_armed = False
        self._ain_pass_mode = None
        try:
            self.canvas.delete('scan_radius_preview')
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass
        self.status(f'Scan Applier {reason}.')
        return True

    def _update_scan_radius_preview(self, cx, cy):
        """Visible scanner radius disc, separate from the green generator disc."""
        tag = 'scan_radius_preview'
        if not bool(getattr(self, '_scan_seed_armed', False)):
            try:
                self.canvas.delete(tag)
            except Exception:
                pass
            return
        try:
            r_m = float(getattr(self, '_scan_seed_radius', 200.0) or 200.0)
            r_px = r_m * float(self.vp_zoom)
        except Exception:
            return

        self.canvas.delete(tag)
        col = '#36a9ff'
        # A clear cyan/blue ring + center marker.  It behaves like Generate's
        # radius preview, but cannot be mistaken for the generator's green one.
        self.canvas.create_oval(
            cx-r_px, cy-r_px, cx+r_px, cy+r_px,
            outline=col, width=3, dash=(7, 4), tags=tag)
        self.canvas.create_oval(
            cx-4, cy-4, cx+4, cy+4,
            outline=col, fill=col, tags=tag)
        self.canvas.create_text(
            cx+10, cy+10, anchor='nw', fill=col,
            text=f'{r_m:.0f} m', tags=tag)
        try:
            self.canvas.tag_raise(tag)
        except Exception:
            pass

    def _scan_apply_at(self, origin_x, origin_y):
        """Apply one atomic local scan.

        0x32 always runs.  Optional B12/B16 channels are evaluated from a
        frozen pre-scan node snapshot, accumulated separately, and committed
        only after every node in the disc has been examined.  Therefore a
        second identical scan cannot reveal more nodes merely because the
        first scan changed metadata.
        """
        import math as _math

        _scan_progress_win = None
        _scan_progress_label = None
        _scan_progress_bar = None

        def _scan_progress_open():
            nonlocal _scan_progress_win, _scan_progress_label, _scan_progress_bar
            try:
                win = tk.Toplevel(self)
                win.title('Navigation Scan')
                win.transient(self)
                win.resizable(False, False)
                win.configure(bg=C_PANEL)

                frame = tk.Frame(win, bg=C_PANEL, padx=18, pady=14)
                frame.pack(fill='both', expand=True)

                lbl = tk.Label(
                    frame, text='Detecting nodes…',
                    bg=C_PANEL, fg=C_TEXT,
                    font=('Consolas', 9),
                    anchor='w', justify='left', width=36
                )
                lbl.pack(fill='x', pady=(0, 9))

                bar = ttk.Progressbar(
                    frame, orient='horizontal',
                    mode='determinate', maximum=100, length=300
                )
                bar.pack(fill='x')
                bar['value'] = 5

                win.update_idletasks()
                try:
                    ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
                    rx, ry = self.winfo_rootx(), self.winfo_rooty()
                    rw, rh = self.winfo_width(), self.winfo_height()
                    win.geometry(
                        f'+{rx + max(0, (rw-ww)//2)}'
                        f'+{ry + max(0, (rh-wh)//2)}'
                    )
                except Exception:
                    pass

                win.lift()
                try:
                    win.grab_set()
                except Exception:
                    pass
                win.update_idletasks()

                _scan_progress_win = win
                _scan_progress_label = lbl
                _scan_progress_bar = bar
            except Exception:
                pass

        def _scan_progress(msg=None, value=None):
            try:
                if msg is not None and _scan_progress_label is not None:
                    _scan_progress_label.configure(text=msg)
                if value is not None and _scan_progress_bar is not None:
                    _scan_progress_bar['value'] = max(0, min(100, float(value)))
                if _scan_progress_win is not None:
                    _scan_progress_win.update_idletasks()
            except Exception:
                pass

        def _scan_progress_close():
            try:
                if _scan_progress_win is not None:
                    try:
                        _scan_progress_win.grab_release()
                    except Exception:
                        pass
                    _scan_progress_win.destroy()
            except Exception:
                pass

        _scan_progress_open()

        nodes = self.nodes
        if not nodes:
            _scan_progress_close()
            self._cancel_scan_seed('cancelled: no nodes loaded')
            return

        scan_radius = float(getattr(self, '_scan_seed_radius', 200.0) or 200.0)
        do_b12 = bool(getattr(self, '_scan_seed_do_b12', False))
        do_b16 = bool(getattr(self, '_scan_seed_do_b16', False))
        requested_b12 = int(getattr(self, '_scan_seed_b12_value', 0)) & 0xFF
        exact_b12 = bool(getattr(self, '_scan_seed_b12_exact', False))
        additional_b12_values = tuple(
            int(v) & 0xFF for v in
            getattr(self, '_scan_seed_b12_additional_values', ()))
        additional_b12_enabled = bool(additional_b12_values)
        additional_b12_exact = bool(getattr(self, '_scan_seed_b12_additional_exact', False))
        replace_even_baseline = bool(getattr(self, '_scan_seed_b12_replace_even', False))
        replace_odd_baseline = bool(getattr(self, '_scan_seed_b12_replace_odd', False))
        scan_percentage = max(0, min(100, int(getattr(self, '_scan_seed_percentage', 50))))

        # Consume placement immediately.
        self._scan_seed_armed = False
        self._ain_pass_mode = None
        try:
            self.canvas.delete('scan_radius_preview')
            self.canvas.configure(cursor='crosshair')
        except Exception:
            pass

        # Freeze node state BEFORE any channel writes.
        frozen = []
        for nd in nodes:
            frozen.append({
                'x': float(nd.x), 'y': float(nd.y), 'z': float(nd.z),
                'b12': int(nd.b12), 'b16': int(nd.b16),
                'neighbors': tuple(int(j) for j in nd.neighbors),
                'scan_0x32': getattr(nd, 'scan_0x32', None),
            })

        flooded = set()
        dists = {}
        max_dist = 0.0
        radius_sq = scan_radius * scan_radius
        for i, nd in enumerate(frozen):
            dx = nd['x'] - origin_x
            dy = nd['y'] - origin_y
            d2 = dx*dx + dy*dy
            if d2 > radius_sq:
                continue
            d = _math.sqrt(d2)
            flooded.add(i)
            dists[i] = d
            if d > max_dist:
                max_dist = d

        if not flooded:
            self.status(
                f'Scan Applier: no nodes inside {scan_radius:.0f}m disc at '
                f'({origin_x:.1f}, {origin_y:.1f}).'
            )
            return

        # B12 needs collision context.  Reuse the editor's production geometry
        # cache; if it is not built yet this call builds it once, then later
        # scans reuse the shared cache.
        world_segments = []
        if do_b12 or do_b16:
            try:
                world_segments, _cores, _stats, _cached = self._collect_generator_geometry()
            except Exception:
                world_segments = []

        # Spatial bucket for 2D wall segments.
        cell = 12.0
        buckets = {}
        if world_segments:
            for s in world_segments:
                try:
                    x1, y1, x2, y2 = map(float, s[:4])
                except Exception:
                    continue
                gx0 = int(_math.floor(min(x1, x2) / cell))
                gx1 = int(_math.floor(max(x1, x2) / cell))
                gy0 = int(_math.floor(min(y1, y2) / cell))
                gy1 = int(_math.floor(max(y1, y2) / cell))
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        buckets.setdefault((gx, gy), []).append((x1, y1, x2, y2))

        _scan_progress(
            f'Detected {len(flooded)} nodes.\nApplying values…',
            45
        )

        def _largest_gap_mid(i):
            n = frozen[i]
            angs = []
            for j in n['neighbors']:
                if 0 <= j < len(frozen):
                    q = frozen[j]
                    dx = q['x'] - n['x']
                    dy = q['y'] - n['y']
                    if abs(dx) + abs(dy) > 1e-9:
                        angs.append(_math.atan2(dy, dx) % _math.tau)
            if len(angs) < 2:
                return None
            angs.sort()
            best_gap = -1.0
            best_mid = None
            for k, a in enumerate(angs):
                b = angs[(k + 1) % len(angs)]
                gap = (b - a) % _math.tau
                if gap > best_gap:
                    best_gap = gap
                    best_mid = (a + gap * 0.5) % _math.tau
            return best_mid

        def _ray_seg_t(px, py, dx, dy, x1, y1, x2, y2):
            sx = x2 - x1
            sy = y2 - y1
            den = dx * sy - dy * sx
            if abs(den) < 1e-10:
                return None
            ox = x1 - px
            oy = y1 - py
            t = (ox * sy - oy * sx) / den
            u = (ox * dy - oy * dx) / den
            if t > 0.03 and 0.0 <= u <= 1.0:
                return t
            return None

        def _b12_hit_limit(i):
            """Radius-aware collision distance used by B12 context scoring."""
            try:
                n = frozen[i]
                # Derive a modest local scale from link lengths.
                lengths = []
                for j in n['neighbors']:
                    if 0 <= j < len(frozen):
                        q = frozen[j]
                        d = _math.hypot(
                            float(q['x']) - float(n['x']),
                            float(q['y']) - float(n['y']))
                        if d > 1e-6:
                            lengths.append(d)
                local = (sum(lengths) / len(lengths)) if lengths else 2.0
            except Exception:
                local = 2.0
            return max(0.75, min(3.5, local * 1.15))

        def _ray_hits_scan_collision(px, py, dx, dy, max_hit):
            """Use the scanner's EXISTING collision grid for a directional hit test.

            Important: the scanner grid is named `buckets` and uses `cell`.
            Bucket entries are raw segments, not (id, segment) pairs.
            """
            ex = px + dx * max_hit
            ey = py + dy * max_hit
            gx0 = int(_math.floor(min(px, ex) / cell)) - 1
            gx1 = int(_math.floor(max(px, ex) / cell)) + 1
            gy0 = int(_math.floor(min(py, ey) / cell)) - 1
            gy1 = int(_math.floor(max(py, ey) / cell)) + 1

            seen = set()
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    for seg in buckets.get((gx, gy), ()):
                        sid = id(seg)
                        if sid in seen:
                            continue
                        seen.add(sid)
                        t = _ray_seg_t(px, py, dx, dy, *seg)
                        if t is not None and t <= max_hit:
                            return True
            return False

        def _b12_eligible(i):
            """Current reconstructed behaviour only.

            Exact requested byte is written when the node's largest graph-gap
            direction encounters nearby collision.  Fallback/promotion rules
            are deliberately NOT implemented yet.
            """
            if not buckets:
                return False
            mid = _largest_gap_mid(i)
            if mid is None:
                return False
            n = frozen[i]
            px, py = n['x'], n['y']
            dx, dy = _math.cos(mid), _math.sin(mid)

            # SPBHD_03 reconstruction puts the useful hit band around ~1–3 m.
            # 3.25 m is intentionally a conservative eligibility ceiling, not
            # a B12 fallback/classification rule.
            max_hit = 3.25
            ex = px + dx * max_hit
            ey = py + dy * max_hit
            gx0 = int(_math.floor(min(px, ex) / cell)) - 1
            gx1 = int(_math.floor(max(px, ex) / cell)) + 1
            gy0 = int(_math.floor(min(py, ey) / cell)) - 1
            gy1 = int(_math.floor(max(py, ey) / cell)) + 1
            best = None
            seen = set()
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    for seg in buckets.get((gx, gy), ()):
                        sid = id(seg)
                        if sid in seen:
                            continue
                        seen.add(sid)
                        t = _ray_seg_t(px, py, dx, dy, *seg)
                        if t is not None and t <= max_hit and (best is None or t < best):
                            best = t
            return best is not None

        def _b12_dynamic_bit1(i):
            """Recover B12 bit 1 as a per-node narrow/two-sided condition.

            This is deliberately B12-local: it uses only the final graph fan and
            collision around the same largest-gap axis.  The ordinary campaign
            4/5 -> 6/7 transition is strongly associated with an axial graph and
            nearby obstruction on the opposite side.
            """
            n = frozen[i]
            angs = []
            for j in n['neighbors']:
                if 0 <= j < len(frozen):
                    q = frozen[j]
                    dx = q['x'] - n['x']
                    dy = q['y'] - n['y']
                    if abs(dx) + abs(dy) > 1e-9:
                        angs.append(_math.atan2(dy, dx) % _math.tau)
            if len(angs) < 2:
                return False

            # Undirected doubled-angle axiality: opposite links reinforce.
            cc = sum(_math.cos(2.0 * a) for a in angs) / len(angs)
            ss = sum(_math.sin(2.0 * a) for a in angs) / len(angs)
            axiality = _math.hypot(cc, ss)
            if axiality < 0.60:
                return False

            mid = _largest_gap_mid(i)
            if mid is None or not buckets:
                return False
            px, py = n['x'], n['y']
            # Opposite the primary obstruction.  A nearby hit here is the
            # recovered two-sided/narrow signature behind B12 bit 1.
            ang = (mid + _math.pi) % _math.tau
            dx, dy = _math.cos(ang), _math.sin(ang)
            max_hit = 3.25
            ex = px + dx * max_hit
            ey = py + dy * max_hit
            gx0 = int(_math.floor(min(px, ex) / cell)) - 1
            gx1 = int(_math.floor(max(px, ex) / cell)) + 1
            gy0 = int(_math.floor(min(py, ey) / cell)) - 1
            gy1 = int(_math.floor(max(py, ey) / cell)) + 1
            seen = set()
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    for seg in buckets.get((gx, gy), ()):
                        sid = id(seg)
                        if sid in seen:
                            continue
                        seen.add(sid)
                        t = _ray_seg_t(px, py, dx, dy, *seg)
                        if t is not None and t <= max_hit:
                            return True
            return False

        def _b12_scan_value_for(i, request_value, exact_mode=False):
            """Build one B12 result from one enabled request family."""
            request_value = int(request_value) & 0xFF
            if exact_mode:
                return request_value

            # Normal mode is a FAMILY operation, including 0/1:
            #   requested 0 or 1 -> 0/1 per-node parity
            #   requested 4      -> 4/5/6/7
            #   requested 12     -> 12/13/14/15
            #   requested 14     -> 14/15
            #
            # Bit0 is always resolved per node in normal mode; Exact Byte is
            # the only way to force literal 0 or literal 1 everywhere.
            fixed = request_value & 0xFE
            precision = int(frozen[i]['b12']) & 0x01
            narrow = 0
            if (request_value & 0x02) == 0 and (request_value & 0x0C):
                if _b12_dynamic_bit1(i):
                    narrow = 0x02
            return (fixed | precision | narrow) & 0xFF

        def _b12_scan_value(i):
            return _b12_scan_value_for(i, requested_b12, exact_b12)

        def _b12_request_family_base(value):
            return int(value) & 0xFC

        def _b12_context_score(i):
            """Score directional strength from frozen graph/collision context."""
            mid = _largest_gap_mid(i)
            if mid is None:
                return 0.0

            n = frozen[i]
            px = float(n['x'])
            py = float(n['y'])
            dx = _math.cos(mid)
            dy = _math.sin(mid)

            max_hit = _b12_hit_limit(i)
            hit_forward = _ray_hits_scan_collision(px, py, dx, dy, max_hit)
            hit_back = _ray_hits_scan_collision(px, py, -dx, -dy, max_hit)

            score = 0.0
            if hit_forward:
                score += 1.0
            if hit_back:
                score += 1.0
            if _b12_dynamic_bit1(i):
                score += 1.0

            bearings = []
            for j in frozen[i]['neighbors']:
                if 0 <= j < len(frozen):
                    q = frozen[j]
                    vx = float(q['x']) - px
                    vy = float(q['y']) - py
                    if abs(vx) + abs(vy) > 1e-9:
                        bearings.append(_math.atan2(vy, vx) % (_math.pi * 2.0))
            bearings.sort()
            if len(bearings) >= 2:
                gaps = []
                for k, a in enumerate(bearings):
                    b = bearings[(k + 1) % len(bearings)]
                    gaps.append((b - a) % (_math.pi * 2.0))
                gaps.sort(reverse=True)
                if len(gaps) > 1:
                    gap2_deg = _math.degrees(gaps[1])
                    if gap2_deg >= 90.0:
                        score += 0.75
                    elif gap2_deg >= 60.0:
                        score += 0.35
            return score

        def _b12_high_context(i):
            """Return True when the node qualifies for the 0x80 high-B12 overlay.

            There is NO extra 3% lottery here. The geometry/topology condition
            itself is the rarity gate.
            """
            return _b12_context_score(i) >= 2.75

        def _b12_low_result_for_request(i, request_value, exact_mode=False):
            """Compute the low (<128) result used to match a high overlay."""
            value = int(request_value) & 0x7F
            # For matching purposes, even an exact high request is compared
            # against the node's naturally computed low result.
            fixed = value & 0xFE
            precision = int(frozen[i]['b12']) & 0x01
            narrow = 0
            if (value & 0x02) == 0 and (value & 0x0C):
                if _b12_dynamic_bit1(i):
                    narrow = 0x02
            return (fixed | precision | narrow) & 0x7F

        def _b12_choose_high_overlay(i, high_choices, normal_choice):
            """Choose a 128+ value by low-bit meaning, not random priority.

            High values are 0x80 overlays of low B12 patterns:
              128 -> low 0
              129 -> low 1
              131 -> low 3
              133 -> low 5
              etc.

            When a node qualifies for the high overlay, compare the node's
            natural low result to the low 7 bits of every enabled high value.
            Exact low-bit matches win. If no exact match exists, choose the
            closest enabled low-bit pattern while strongly preferring parity.
            """
            if not high_choices:
                return None
            if not _b12_high_context(i):
                return None

            # The normal/default family supplies the low result that the 0x80
            # overlay modifies. This keeps high values spatially tied to the
            # same B12 logic instead of becoming a separate random population.
            normal_value, normal_exact = normal_choice
            low_result = _b12_scan_value_for(i, normal_value, normal_exact) & 0x7F

            exact_matches = [
                c for c in high_choices
                if (int(c[0]) & 0x7F) == low_result
            ]
            if exact_matches:
                return exact_matches[0]

            # If the exact overlay value was not enabled, select the closest
            # enabled low-bit pattern. Parity mismatch is penalized heavily.
            def _distance(choice):
                target = int(choice[0]) & 0x7F
                parity_penalty = 8 if ((target ^ low_result) & 0x01) else 0
                bit_distance = (target ^ low_result).bit_count()
                numeric_distance = abs(target - low_result) / 128.0
                return (parity_penalty + bit_distance + numeric_distance,
                        bit_distance, abs(target - low_result), target)

            return min(high_choices, key=_distance)

        def _b12_choose_request(i):
            """Choose enabled B12 families from actual node context.

            Ordinary families use contextual promotion. 128+ values are a rare
            0x80 overlay whose low bits are matched to that ordinary result.
            """
            choices = [(requested_b12, exact_b12)]
            choices.extend(
                (value, additional_b12_exact)
                for value in additional_b12_values)

            if len(choices) == 1:
                return choices[0]

            normal_choices = [c for c in choices if c[0] < 128]
            high_choices = [c for c in choices if c[0] >= 128]

            # If the author supplied ONLY high values, derive the matching low
            # family from their low 7-bit forms rather than hash-splitting them.
            if not normal_choices:
                synthetic_low = sorted(
                    {(int(c[0]) & 0x7F, False) for c in high_choices},
                    key=lambda c: (c[0] & 0xFC, c[0]))
                if not synthetic_low:
                    return high_choices[0]
                score = _b12_context_score(i)
                if score < 1.75:
                    level = 0
                elif score < 2.75:
                    level = 1
                else:
                    level = 2 + int(score - 3.5)
                level = max(0, min(level, len(synthetic_low) - 1))
                low_choice = synthetic_low[level]

                high = _b12_choose_high_overlay(i, high_choices, low_choice)
                if high is not None:
                    return high

                # Outside high context, preserve a deterministic literal output
                # rather than inventing an unrequested low value.
                low_result = _b12_scan_value_for(
                    i, low_choice[0], low_choice[1]) & 0x7F
                return min(
                    high_choices,
                    key=lambda c: (
                        8 if (((int(c[0]) & 0x7F) ^ low_result) & 1) else 0,
                        ((int(c[0]) & 0x7F) ^ low_result).bit_count(),
                        abs((int(c[0]) & 0x7F) - low_result)
                    )
                )

            # Lowest ordinary family is the default. Stronger contexts promote
            # to higher enabled ordinary families.
            normal_choices = sorted(
                normal_choices,
                key=lambda c: (_b12_request_family_base(c[0]), c[0]))

            if len(normal_choices) == 1:
                normal_choice = normal_choices[0]
            else:
                score = _b12_context_score(i)
                if score < 1.75:
                    level = 0
                elif score < 2.75:
                    level = 1
                else:
                    level = 2 + int(score - 3.5)
                level = max(0, min(level, len(normal_choices) - 1))
                normal_choice = normal_choices[level]

            # High overlay can replace the ordinary choice only when the node's
            # strong context warrants it.
            high_choice = _b12_choose_high_overlay(
                i, high_choices, normal_choice)
            if high_choice is not None:
                return high_choice

            return normal_choice

        # Deterministic thinning after eligibility. Same scan + same graph +
        # same percentage always selects the same nodes, so repeated scans
        # cannot progressively fill the eligible population.
        def _scan_percentage_accept(i):
            if scan_percentage >= 100:
                return True
            if scan_percentage <= 0:
                return False
            n = frozen[i]
            vals = (
                i,
                int(round(n['x'] * 16.0)),
                int(round(n['y'] * 16.0)),
                int(round(n['z'] * 16.0)),
                int(round(origin_x * 16.0)),
                int(round(origin_y * 16.0)),
            )
            v = 0x811C9DC5
            for q in vals:
                q &= 0xffffffff
                v ^= q
                v = (v * 0x01000193) & 0xffffffff
                v ^= (v >> 16)
            return (v % 100) < scan_percentage

        # Compute every result without mutating self.nodes.
        pending_32 = {}
        pending_b12 = {}
        pending_b16 = {}

        _scan_total = max(1, len(flooded))
        _scan_step = max(1, _scan_total // 20)
        for _scan_pos, i in enumerate(flooded, 1):
            if (_scan_pos == 1 or _scan_pos == _scan_total or
                    (_scan_pos % _scan_step) == 0):
                _scan_progress(
                    f'Detected {len(flooded)} nodes.\nApplying values…',
                    45.0 + (45.0 * _scan_pos / _scan_total)
                )

            nc = len(frozen[i]['neighbors'])
            if nc <= 15:
                if max_dist > 0:
                    lo = int(255.0 * (1.0 - dists[i] / max_dist))
                else:
                    lo = 255
                lo = max(0, min(255, lo))
                pending_32[i] = lo

            if do_b12:
                old_b12 = int(frozen[i]['b12']) & 0xFF

                # Optional exact parity override. OFF by default. When enabled,
                # matching existing even/odd values receive the chosen requested
                # byte literally and bypass normal B12 eligibility/thinning.
                force_parity = (
                    ((old_b12 & 0x01) == 0 and replace_even_baseline) or
                    ((old_b12 & 0x01) == 1 and replace_odd_baseline)
                )
                if force_parity:
                    _req, _req_exact = _b12_choose_request(i)
                    pending_b12[i] = _req
                elif _b12_eligible(i) and _scan_percentage_accept(i):
                    # Normal 0/1 is NOT a hard reset. It uses the same family
                    # path as every other non-exact request, so 0 or 1 resolves
                    # to per-node 0/1 parity. Exact Byte remains literal.
                    _req, _req_exact = _b12_choose_request(i)
                    pending_b12[i] = _b12_scan_value_for(
                        i, _req, _req_exact)
                else:
                    # A complete re-scan returns nodes not selected this time
                    # to their parity-only baseline.
                    pending_b12[i] = old_b12 & 0x01

            # B12=0/1 can never retain a direction byte.  This invariant applies
            # even when the B16 channel checkbox is OFF.
            final_b12 = pending_b12.get(i, int(frozen[i]['b12']) & 0xFF)
            if do_b12 and final_b12 in (0, 1):
                pending_b16[i] = 0
            elif do_b16:
                # B16 is a COMPLETE scan result, not additive metadata.
                if _b12_eligible(i):
                    mid = _largest_gap_mid(i)
                    if mid is not None:
                        deg = _math.degrees(mid)
                        val = int(round(((deg - 215.0) % 360.0) / 360.0 * 256.0)) % 256
                        if val == 0:
                            val = 1
                        pending_b16[i] = val
                    else:
                        pending_b16[i] = 0
                else:
                    pending_b16[i] = 0

        _scan_progress(
            f'Detected {len(flooded)} nodes.\nSaving updated values…',
            92
        )

        # One undo record for the whole atomic scan.
        snapshot = self._snapshot_nodes()

        changed_32 = changed_b12 = changed_b16 = 0
        for i, lo in pending_32.items():
            newv = lo  # high byte stays zero in the current reconstructed 0x32 pass
            if getattr(nodes[i], 'scan_0x32', None) != newv:
                nodes[i].scan_0x32 = newv
                changed_32 += 1

        for i, val in pending_b12.items():
            if int(nodes[i].b12) != val:
                nodes[i].b12 = val
                changed_b12 += 1

        for i, val in pending_b16.items():
            if int(nodes[i].b16) != val:
                nodes[i].b16 = val
                changed_b16 += 1

        total_changed = changed_32 + changed_b12 + changed_b16
        if total_changed:
            self._push_undo(
                'Apply navigation scan',
                lambda s=snapshot: self._restore_nodes(s)
            )

        if do_b12 and not world_segments:
            b12_note = ' B12: 0 changed (collision geometry unavailable).'
        elif do_b12:
            b12_note = (
                f' B12: {changed_b12} changed across {len(pending_b12)} scanned nodes'
                f' ({str(scan_percentage) + "% target"}; '
                f'requested {requested_b12}'
                f'{(" + " + ",".join(str(v) for v in additional_b12_values)) if additional_b12_enabled else ""}; '
                f'{"128+ overlay; " if any(v >= 128 for v in ((requested_b12,) + additional_b12_values)) and any(v < 128 for v in ((requested_b12,) + additional_b12_values)) else ""}'
                f'{"exact" if exact_b12 else "per-node bitfield"}; '
                f'force even={"on" if replace_even_baseline else "off"}, '
                f'odd={"on" if replace_odd_baseline else "off"}).'
            )
        else:
            b12_note = ''

        if do_b16 and not world_segments:
            b16_note = ' B16: 0 changed (collision geometry unavailable).'
        elif do_b16:
            b16_note = (
                f' B16: {changed_b16} changed across {len(pending_b16)} scanned nodes'
                f' (eligible solved; remainder -> 0).'
            )
        else:
            b16_note = ''

        _scan_progress(
            f'Detected {len(flooded)} nodes.\nApplying values… done.',
            100
        )
        _scan_progress_close()

        self.status(
            f'Scan applied once to {len(flooded)} nodes inside {scan_radius:.0f}m disc. '
            f'0x32: {changed_32} changed.{b12_note}{b16_note}'
        )
        try:
            self._update_inspector()
            self._update_stats()
            self.redraw()
            self.refresh_3d_views(rebuild=True, refit=False, reason='scan_apply')
        except Exception:
            try:
                self.redraw()
            except Exception:
                pass
