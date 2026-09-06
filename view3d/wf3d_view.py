"""3D wireframe view — node-centric 3D context viewer and popup window."""
import math
import os
import re
import sys
import time
import platform
import traceback
import tkinter as tk
from tkinter import font, messagebox
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

from format.ain_format import Node
import view3d.wf3d_debug as _wf3d_debug_mod
from view3d.wf3d_debug import (_wf3d_dbg, _wf3d_dbg_clear, _wf3d_clear_rendermesh_cache,
    WF3D_DEBUG_LOG_PATH)
from cmodel.cmodel_access import (get_cmodel_segments_3d, get_cmodel_collision_triangles_3d,
    get_cmodel_segments_3d_grouped, _cmodel_category_for_type)
from cmodel.cmodel_geom import (_rotated_segments_for_heading, _cmodel_husk_segments,
    _cmodel_rotation_cache_key, _graphic_name_from_item, _rotated_bounds_for_heading)
from cmodel.cmodel_parse import _parse_gpm_cmodel_3d, _parse_render_mesh_3d
from entity.entity_classify import _graphic_is_context_only
from entity.entity_data import DEFAULT_RADIUS, FORCE_OBSTACLE, OBSTACLE_RADII
from entity.entity_classify import entity_layer_kind
from config.render_config import CMODEL_DEDUP_EDGES
from shared.debug_log import _dbg, DEBUG_RENDER_TIMING
from config.editor_config import (_editor_cfg_bool, _load_editor_cfg, _save_editor_cfg,
    _configured_additional_pff_dirs)
from resources.items_def import _load_items_def, _get_3di_bytes_for_type
from shared.nav_helpers import NAV_NODE_Z_LIFT
from resources.pff_archive import _read_from_pff_stack
from terrain.terrain import _terrain_sample_height
from terrain.terrain_water import _terrain_water_height_world
import view3d.helpers as _view3d_helpers
import view3d.camera as _view3d_camera
import view3d.panels as _view3d_panels
import view3d.rendering as _view3d_rendering
import view3d.collision as _view3d_collision
import view3d.section as _view3d_section
import view3d.interaction as _view3d_interaction
import view3d.scene as _view3d_scene
import view3d.geometry as _view3d_geometry
import view3d.navigation as _view3d_navigation
import view3d.controls as _view3d_controls


# ── Module-level getters — wired by monolith after import ──────────────────
_get_game_path = lambda: None
_get_pff_cache = lambda: {}
_get_3di_cache = lambda: {}
_get_cmodel_segments_fn = lambda tid, gp=None, allow_load=False, log=None: None


_REF_MODEL_FILENAME = 'Delta01.3di'
_REF_MODEL_CACHE = {}


def _get_ref_model_geometry(game_path=None):
    """Load the optional Delta01 reference wireframe from the user's game.

    No Delta01 geometry is shipped with the editor.  Returns ``(verts, edges)``
    in the same local coordinate convention used by the 3D CModel parser, with
    the lowest model Z normalized to 0 so the character's feet sit on support.
    """
    gp = game_path or _get_game_path()
    if not gp:
        return (), ()

    try:
        gp_path = Path(gp)
        cache_key = str(gp_path.resolve()).lower()
    except Exception:
        gp_path = Path(gp)
        cache_key = str(gp_path).lower()

    cached = _REF_MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    raw = None
    source = None
    try:
        # Loose files first for mod/debug installs, then the normal PFF stack.
        for direct in (
                gp_path / _REF_MODEL_FILENAME,
                gp_path / 'models' / _REF_MODEL_FILENAME,
                gp_path / 'resource' / _REF_MODEL_FILENAME,
                gp_path / 'resources' / _REF_MODEL_FILENAME):
            if direct.exists():
                try:
                    raw = direct.read_bytes()
                    source = direct
                    break
                except Exception:
                    pass

        if not raw:
            raw, source = _read_from_pff_stack(gp_path, _REF_MODEL_FILENAME, additional_dirs=_configured_additional_pff_dirs(), cache=_get_pff_cache(), dbg=_dbg)

        if not raw:
            _wf3d_dbg(
                f"REF_MODEL: {_REF_MODEL_FILENAME} unavailable in game path {gp_path}")
            _REF_MODEL_CACHE[cache_key] = ((), ())
            return _REF_MODEL_CACHE[cache_key]

        segments = _parse_gpm_cmodel_3d(
            raw, debug_name=_REF_MODEL_FILENAME, return_triangles=False,
            dbg=_dbg, dedup_edges=CMODEL_DEDUP_EDGES) or []
        if not segments:
            _wf3d_dbg(
                f"REF_MODEL: failed to parse {_REF_MODEL_FILENAME} from {source}")
            _REF_MODEL_CACHE[cache_key] = ((), ())
            return _REF_MODEL_CACHE[cache_key]

        # Collapse segment endpoints into an indexed wireframe.  CModel local
        # coordinates are already transformed as (-rawY/256, rawX/256, rawZ/256).
        # Only the vertical origin changes here: feet become local Z=0.
        foot_z = min(float(p[2]) for seg in segments for p in seg)
        verts = []
        vertex_index = {}
        edges = []
        seen_edges = set()

        def vertex_id(point):
            key = (float(point[0]), float(point[1]), float(point[2]) - foot_z)
            found = vertex_index.get(key)
            if found is not None:
                return found
            idx = len(verts)
            vertex_index[key] = idx
            verts.append(key)
            return idx

        for a, b in segments:
            ia = vertex_id(a)
            ib = vertex_id(b)
            if ia == ib:
                continue
            edge_key = (ia, ib) if ia < ib else (ib, ia)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append((ia, ib))

        result = (tuple(verts), tuple(edges))
        _REF_MODEL_CACHE[cache_key] = result
        _wf3d_dbg(
            f"REF_MODEL: loaded {_REF_MODEL_FILENAME} from {source} "
            f"verts={len(verts)} edges={len(edges)} height="
            f"{(max(v[2] for v in verts) if verts else 0.0):.4f}m")
        return result
    except Exception as exc:
        _wf3d_dbg(f"REF_MODEL: runtime load failed: {exc}")
        _REF_MODEL_CACHE[cache_key] = ((), ())
        return _REF_MODEL_CACHE[cache_key]


def _get_cmodel_raw(type_id, game_path):
    """Parse 3D render mesh from 3DI binary.
    Returns list of ((x1,y1,z1),(x2,y2,z2)) in local object space or None.
    """
    gp = game_path or _get_game_path()
    if not gp:
        _wf3d_dbg(f"GET_RAW: type={type_id} fail no game_path")
        return None

    _cache = _get_3di_cache()
    # Keep render-mesh cache separate from the compact/CModel cache and
    # version it. Older debug builds stored negative None entries under only
    # ('rendermesh3d', type_id), which caused fixed parsers to be skipped.
    mesh_ver = _wf3d_debug_mod.WF3D_RENDER_MESH_CACHE_VERSION
    cache_key = ('rendermesh3d', mesh_ver, str(gp).lower(), int(type_id))
    if cache_key in _cache:
        val = _cache[cache_key]
        if val or not _wf3d_debug_mod.WF3D_DEBUG_ENABLED:
            _wf3d_dbg(f"GET_RAW: type={type_id} cache {'HIT' if val else 'MISS-NEG'} keyver={mesh_ver} segs={len(val) if val else 0}")
            return val
        # In debug mode, negative render-mesh cache entries are treated as
        # stale so the log can show the real raw-load/parse failure.
        _wf3d_dbg(f"GET_RAW: type={type_id} bypass NEG cache keyver={mesh_ver}")

    try:
        _wf3d_dbg(f"GET_RAW: type={type_id} loading 3DI bytes")
        raw, item = _get_3di_bytes_for_type(type_id, gp, log=None, pff_cache=_get_pff_cache())
        graphic = None
        try:
            graphic = (item or {}).get('graphic') or (item or {}).get('model') or (item or {}).get('name')
        except Exception:
            pass
        if not raw:
            _wf3d_dbg(f"GET_RAW: type={type_id} no raw 3DI graphic={graphic!r}")
            _cache[cache_key] = None
            return None
        _wf3d_dbg(f"GET_RAW: type={type_id} raw bytes={len(raw)} graphic={graphic!r}")
        segments = _parse_render_mesh_3d(raw, dbg=_wf3d_dbg)
        _cache[cache_key] = segments
        _wf3d_dbg(f"GET_RAW: type={type_id} parsed render segs={len(segments) if segments else 0}")
        return segments
    except Exception as _e:
        _wf3d_dbg(f"GET_RAW: type={type_id} exception={_e}")
        _cache[cache_key] = None
        return None


class Wireframe3DView(tk.Frame):
    """Reusable node-centric 3D wireframe view widget.

    This class owns the actual 3D UI/rendering and is intentionally a Frame
    so it can be used both inside a popup window and, later, embedded into the
    main editor viewport for a MED-style 2D/3D toggle.

    Read-only node-centric 3D wireframe view.

    MED's 3D view renders manually selected mission objects only. In the AIN
    editor, nodes are the editable selection, so this view builds a synthetic
    context scene:
      selected nodes + links + nearby buildings/decorations + local terrain grid.
    """

    BG = (0, 0, 0)
    GRID = (62, 52, 33)
    GRID_AXIS_X = (105, 45, 45)
    GRID_AXIS_Y = (45, 95, 55)
    GROUND_SOLID = (8, 8, 7)
    GROUND_SOLID_EDGE = (26, 24, 18)
    BUILDING = (205, 205, 205)
    DECOR = (120, 125, 130)
    VEHICLE = (255, 140, 0)
    NODE_ACTIVE = (255, 215, 0)
    NODE_SELECTED = (255, 150, 0)
    NODE_NEIGHBOR = (80, 170, 90)
    NODE_ID_TEXT = (235, 235, 225)
    EDGE = (0, 220, 255)
    # Diagnostic nav-edge color: a connection whose 3D segment crosses true
    # CModel collision is drawn red. This is display-only; it never edits AIN.
    EDGE_BLOCKED = (255, 60, 60)
    # Selected-node direct links get a static two-pass halo.
    # This is intentionally not animated; it only redraws on normal 3D frames.
    SELECTED_LINK_HALO = (0, 105, 34)
    SELECTED_LINK_CORE = (130, 255, 85)
    SELECTED_NODE_HALO = (40, 115, 35)
    GUIDE = (180, 160, 80)
    CMODEL_OVERLAY = (80, 255, 210)
    # Runtime-loaded Delta01 standing-human reference. Warm amber stays distinct
    # from nav cyan/red, active-node gold, and CModel teal.
    REF_MODEL = (210, 175, 110)
    TEXT = (210, 210, 210)
    DIM = (95, 95, 95)
    # b16-as-pitch test overlay (owner hypothesis: b16 may be tilt, not bearing)
    B16_ARROW = (255, 80, 220)
    B16_ARROW_LEN = 4.0
    FLOOR_COORD = (120, 210, 180)
    SECTION_LOWER = (80, 180, 255)
    SECTION_UPPER = (255, 180, 70)
    SECTION_HANDLE = (245, 245, 245)
    SECTION_DIM = (105, 105, 105)
    SECTION_XY_OUTER = (70, 220, 110)
    SECTION_XY_INNER = (220, 80, 220)
    # Graph-end diagnostic colors are shared by the markers and their tiny
    # viewport legend so the meaning can never drift from the rendered overlay.
    GRAPH_END_CLEAR = (255, 70, 210)
    GRAPH_END_STAIR = (255, 145, 55)

    def __init__(self, parent, app=None, on_close=None):
        super().__init__(parent)
        self.app = app if app is not None else parent
        self._on_close = on_close
        # Window ownership belongs to Wireframe3DWindow or a future embedded
        # viewport container.  The view itself must remain Toplevel-agnostic.
        self.configure(bg="#111111", takefocus=True)

        # Camera
        self.yaw = math.radians(42.0)
        self.pitch = math.radians(24.0)
        # Side [S] is a deterministic orthographic-style yaw cycle. None means
        # the camera is not currently following that cycle (startup, Top, or
        # after a free mouse orbit), so the next press always starts at Front.
        self._side_view_index = None
        self.scale = 7.5
        # Node IDs are only useful once the 3D view is close enough to read them.
        # Below this scale the toggle stays enabled, but no ID text is rasterized.
        self.node_id_min_scale = 8.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.target = (0.0, 0.0, 0.0)
        self._drag_last = None
        self._drag_mode = None
        # Distinguish click-select from pan drag in the 3D viewport.
        self._drag_press_xy = None
        self._drag_moved = False
        self._photo = None
        # Keep one canvas image item and swap its PhotoImage instead
        # of delete/create every frame. This reduces Tk canvas churn while the
        # camera is moving.
        self._canvas_image_item = None
        self._render_job = None
        self._debug_window = None
        self._debug_text = None

        # Interactive 3D connection editing. This is intentionally lightweight:
        # one source ID, one hovered candidate, and one screen-space preview line.
        self._connect3d_source_id = None
        self._connect3d_candidate_id = None
        self._connect3d_mouse_xy = None

        # 3D blocked-edge diagnostics. Collision classification is cached in
        # world space and rebuilt only when the node scene/entity context changes;
        # camera movement only performs a set lookup while drawing.
        self._wf_blocked_edges = set()
        self._wf_edge_collision_tri_cache = {}
        self._wf_edge_collision_index = None

        # Graph-end diagnostics.  This is a render-only aid for spotting places
        # where two disconnected AIN components stop suspiciously close to each
        # other.  Candidate pairs are recomputed only when the scene/topology or
        # node coordinates change, never while the camera moves.
        self._wf_graph_end_pairs = []

        # Scene options
        self.show_grid = tk.BooleanVar(value=True)
        self.show_node_overlay = tk.BooleanVar(value=True)
        self.show_buildings = tk.BooleanVar(value=True)
        self.show_vehicles = tk.BooleanVar(value=False)
        self.show_decorations = tk.BooleanVar(value=False)
        self.show_foliage = tk.BooleanVar(value=False)  # off by default — perf
        self.show_visual_mesh = tk.BooleanVar(value=False)
        self.show_labels = tk.BooleanVar(value=False)
        self.show_radii = tk.BooleanVar(value=False)
        self.show_graph_ends = tk.BooleanVar(value=False)
        # Graph-end categories are independent render filters.  They default on
        # so enabling Graph ends preserves the original v1.0 visual, but either
        # category can be hidden without disabling the diagnostic entirely.
        self.show_graph_end_clear = tk.BooleanVar(value=True)
        self.show_graph_end_stair = tk.BooleanVar(value=True)
        self.show_building_levels = tk.BooleanVar(value=True)
        self.show_floor_coordinates = tk.BooleanVar(value=True)
        # One editor-owned human reference beside the active node.  Only XY
        # offset is user-controlled; feet Z is always sampled from real support.
        self.show_ref_model = tk.BooleanVar(value=False)
        # Orbital placement around the active node. 0° = +X/right in world space,
        # 90° = +Y, 180° = -X, 270° = -Y. This is deliberately independent
        # of the current camera/view direction.
        self.ref_model_angle = tk.DoubleVar(value=0.0)
        self.ref_model_distance = tk.DoubleVar(value=0.8)
        self.show_cmodel_overlay = tk.BooleanVar(value=True)

        # Node Focus is a 3D-view-only render lens. It never changes model,
        # v99_8: while enabled, AIN nodes/edges are composited after context geometry
        # so the navigation graph reads in the foreground without a duplicate pass.
        # CModel, entity, node, filter, or export data. Only the accepted color
        # persists; the mode itself starts disabled every session.
        self.node_focus_mode = tk.BooleanVar(value=False)
        _node_focus_default = "#4A4036"
        try:
            _node_focus_saved = str(_load_editor_cfg().get(
                '3d_node_focus_geometry_color', _node_focus_default) or _node_focus_default)
        except Exception:
            _node_focus_saved = _node_focus_default
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', _node_focus_saved):
            _node_focus_saved = _node_focus_default
        self.node_focus_geometry_color = tk.StringVar(value=_node_focus_saved.upper())
        self._node_focus_rgb = tuple(
            int(_node_focus_saved[i:i+2], 16) for i in (1, 3, 5))
        self._node_focus_color_button = None

        # Keep CModel/Addr-block provenance visible.  All preserves old
        # behavior; Addr Block colors make walls/structural chunks readable.
        self.cmodel_color_mode = tk.StringVar(value="Addr Block")   # Source / Addr Block
        # CModel structural/skeleton display. Raw preserves the old full
        # wireframe; Structure highlights only boundary/crease edges;
        # Structure + Ghost keeps flat/internal triangulation faintly visible.
        self.cmodel_detail_mode = tk.StringVar(value="Raw Wire")
        self.cmodel_group_filter = tk.StringVar(value="All")    # All or numeric group index
        self.cmodel_group_mode = tk.StringVar(value="Only")     # Only / Hide selected group
        self.cmodel_decor_group_filter = tk.StringVar(value="All")
        self.cmodel_decor_group_mode = tk.StringVar(value="Only")
        self.context_mode = tk.StringVar(value="Buildings")
        self.view_preset = tk.StringVar(value="Interior Edit")
        # Context scopes keep nearby objects as orientation while letting
        # collision/visual rendering focus on the node's most relevant building.
        self.building_scope = tk.StringVar(value="Local")       # Selected / Tight / Local / Full
        self.decoration_scope = tk.StringVar(value="Local")    # Off / Tight / Local / Full
        self.visual_scope = tk.StringVar(value="Local")         # Off / Selected / Tight / Local / Full
        self.collision_scope = tk.StringVar(value="Selected")   # Off / Selected / Tight / Local / Full
        self.ground_mode = tk.StringVar(value="Solid + Grid")
        # Underground/tunnel support. Some maps deliberately place
        # usable geometry far below terrain. Keep authored entity Z by default.
        self.show_underground_geometry = tk.BooleanVar(value=True)
        # Tunnel-piece diagnostics. These controls do not change map
        # data; they only help isolate R_Tun* pieces one at a time so parser /
        # transform / context mistakes are visible.
        self.tunnel_piece_focus = tk.StringVar(value="All")
        # Optional tunnel-chain context.  Tunnels are authored as many
        # separate R_Tun* pieces, so normal Local context can cut the chain in
        # half.  This keeps the default clean but lets users include longer
        # tunnel runs when editing underground nodes.
        self.tunnel_context_mode = tk.StringVar(value="Off")  # Off / 80m / 140m / 220m / All
        # Tunnel visual/CModel comparison. Some R_Tun* pieces have a
        # visual render mesh that appears to disagree with the true CModel.
        # These toggles let us isolate whether the visual parser/axis/fit is
        # the offender without touching normal building rendering.
        self.tunnel_draw_mode = tk.StringVar(value="CModel only") # Normal / Visual only / CModel only / Compare
        self.tunnel_visual_transform = tk.StringVar(value="Fit XZY")  # Fit XZY / Raw XZY / Raw XYZ
        self._tunnel_focus_map = {}
        self._tunnel_piece_om = None
        self.label_size = tk.StringVar(value="Normal")
        # 3D readability control for entity geometry only. PIL cannot draw
        # below one physical pixel, so values below 1x use sub-pixel coverage:
        # the line remains one raster pixel wide but is blended toward the 3D
        # background to look genuinely finer. 1.00x preserves the old output.
        self.model_line_width = tk.StringVar(value="1.00x")
        self.node_edge_width = tk.StringVar(value="1")
        self._wf_entity_line_cache = {}
        # Keep camera-independent world-space lines cached.
        # Camera orbit/pan/zoom should only re-project these lines, not rebuild
        # local .3DI/CModel transforms every frame.
        self._wf_visual_world_cache = self._wf_entity_line_cache
        self._wf_cmodel_world_cache = {}
        self._wf_context_footprint_cache = {}
        # Terrain samples are stable while a map is loaded and the 3D
        # grid/entity-base code asks for the same points every frame. Cache them
        # so orbit/zoom and live node edits do not repeatedly hit the heightmap.
        self._wf_terrain_z_cache = {}
        self._wf_cache_hits = 0
        self._wf_cache_misses = 0
        # Performance controls are cheap draw-time guards. These do not
        # change geometry/parsing; they only limit/cull what gets rasterized.
        self.perf_drag_preview = tk.BooleanVar(value=True)
        # Optional quality-over-FPS override for camera drags. Decorations are
        # normally the first geometry to disappear once the temporary motion
        # line budget is exhausted because they are rendered after buildings.
        # Persist this preference independently from view presets.
        self.perf_keep_decorations_motion = tk.BooleanVar(
            value=_editor_cfg_bool('3d_keep_decorations_during_motion', False))
        # Same quality-over-FPS override for collision/CModel geometry. When
        # enabled, camera drags keep the currently visible CModel blocks instead
        # of letting motion LOD / the temporary line budget hide them.
        self.perf_keep_cmodel_blocks_motion = tk.BooleanVar(
            value=_editor_cfg_bool('3d_keep_cmodel_blocks_during_motion', False))
        # Motion LOD makes orbit/pan frames cheaper but restores full
        # quality after mouse release. This is 3D-view only.
        self.perf_motion_lod = tk.BooleanVar(value=True)
        self.perf_offscreen_cull = tk.BooleanVar(value=True)
        self.perf_line_budget = tk.StringVar(value="15000")  # 5000 / 10000 / 15000 / 25000 / Unlimited
        self._draw_line_budget = None
        self._draw_line_count = 0
        self._draw_line_skipped_budget = 0
        self._draw_line_skipped_cull = 0
        self._draw_budget_active = False
        self._render_suppress_labels = False
        self._render_preview = False
        # Transient bounds collector used by Fit [F].  When active, the normal
        # draw helpers feed their *actually visible, section-clipped* world
        # geometry into the fitter instead of rasterizing it.
        self._fit_capture_bounds = None
        self._side_panel_visible = True
        self._side_panel = None
        self._panel_button = None

        self.context_radius = tk.DoubleVar(value=32.0)
        # 3D wireframe context caps. Keep the default focused: one selected
        # node should show its immediate building/local props, not the whole
        # block. AllCtx intentionally expands these for big selected regions.
        self.max_buildings = tk.IntVar(value=10)
        self.max_vehicles = tk.IntVar(value=8)
        self.max_decor = tk.IntVar(value=18)

        self.active_node_id = None
        self.scene = {}

        # 3D box-selection state is transient.  Keep it explicit so a missed
        # modifier-specific release can always be cancelled safely.
        self._box_sel_origin = None
        self._box_sel_mode = None
        self._box_sel_rect_id = None
        # 3D node-set lock status. The actual locked IDs live on
        # the app so embedded and detached 3D views share the same working set.
        self.node_set_status = tk.StringVar(value="Node set: Live selection")
        self._node_set_lock_btn = None
        self._node_set_unlock_btn = None
        # B14 zone filtering lives under the locked/visible 3D node set.
        # It is visibility-only unless the explicit assign-on-lock checkbox is used.
        self._zone_filter_vars = {}          # b14 -> BooleanVar
        self._zone_filter_frame = None
        self._zone_filter_summary = tk.StringVar(value="b14 filter: no node set")
        # Long per-zone controls are useful while editing, but they should not
        # consume most of the Node Set panel by default. Keep only the summary
        # visible until the user explicitly expands the list.
        self._zone_filter_collapsed = tk.BooleanVar(value=True)
        self._zone_filter_toggle_btn = None
        self._zone_filter_details_frame = None
        self._zone_filter_head_frame = None
        self.assign_b14_on_lock = tk.BooleanVar(value=False)
        self.assign_b14_value = tk.StringVar(value="0")

        # Block lists follow the same compact-by-default rule independently for
        # buildings and decorations. Their visibility selections remain active
        # while the UI rows are folded away.
        self._block_layer_collapsed = {"building": True, "decoration": True}
        self._block_layer_toggle_buttons = {}
        self._block_layer_detail_frames = {}

        # 3D Section tool: a camera-independent world-space Z slab.  The two
        # boundaries are manipulated directly in the viewport; numeric values are
        # display-only during normal use.
        self.section_enabled = tk.BooleanVar(value=False)
        self.section_z_enabled = tk.BooleanVar(value=True)
        self.section_xy_enabled = tk.BooleanVar(value=False)
        self.section_z_locked = tk.BooleanVar(value=False)
        self.section_xy_locked = tk.BooleanVar(value=False)

        self.section_lower_z = tk.DoubleVar(value=0.0)
        self.section_upper_z = tk.DoubleVar(value=0.0)

        # XY has its OWN independent volume. These are world-space crop bounds.
        self.section_xy_min_x = tk.DoubleVar(value=0.0)
        self.section_xy_max_x = tk.DoubleVar(value=0.0)
        self.section_xy_min_y = tk.DoubleVar(value=0.0)
        self.section_xy_max_y = tk.DoubleVar(value=0.0)

        self._section_initialized = False
        self._section_xy_initialized = False
        self._section_button = None
        self._section_z_button = None
        self._section_xy_button = None
        self._section_lock_z_button = None
        self._section_lock_xy_button = None
        self._section_value_label = None
        self._section_handle_positions = {}
        self._section_hover_handle = None
        # Section affects the upstream 3D context, not just raster clipping.
        # Rebuilds are debounced while handles move so drag stays responsive.
        self._section_context_refresh_job = None

        # Section control guides must not resize just because the filtered scene
        # gains/loses geometry. Capture their display envelope when Section is
        # entered and keep it fixed until Section is turned off.
        self._section_guide_xy_bounds = None
        self._section_guide_z_bounds = None

        self._build_ui()
        self._bind_events()
        self.rebuild_scene()
        self.fit_view()
        self.schedule_render()


    def _wf3d_force_redraw_debug(self):
        """Force a debug redraw from uncached entity lines."""
        try:
            n = len(getattr(self, '_wf_entity_line_cache', {}) or {})
            self._wf_entity_line_cache.clear()
        except Exception:
            n = -1
        r = _wf3d_clear_rendermesh_cache(cache=_get_3di_cache())
        _wf3d_dbg(f'DEBUG: force redraw; cleared _wf_entity_line_cache entries={n}; cleared rendermesh cache entries={r}', force=True)
        try:
            self.rebuild_scene()
        except Exception as e:
            _wf3d_dbg(f'DEBUG: rebuild_scene exception during force redraw: {e}', force=True)
        try:
            self.schedule_render()
        except Exception as e:
            _wf3d_dbg(f'DEBUG: schedule_render exception during force redraw: {e}', force=True)

    def show_3d_debug(self):
        """Open/update the live 3D debug panel."""
        _wf3d_debug_mod.WF3D_DEBUG_ENABLED = True
        try:
            self._wf_entity_line_cache.clear()
        except Exception:
            pass
        r = _wf3d_clear_rendermesh_cache(cache=_get_3di_cache())
        _wf3d_dbg(f'DEBUG: enabled from Wireframe3DView; cleared _wf_entity_line_cache; cleared rendermesh cache entries={r}', force=True)
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

    def _build_ui(self):
        return _view3d_controls.build_ui(self)

    def _developer_mode_enabled(self):
        return _view3d_controls.developer_mode_enabled(self)


    def _sync_developer_controls(self):
        return _view3d_controls.sync_developer_controls(self)


    def _sync_node_set_zone_labels(self, refresh_controls=False):
        return _view3d_controls.sync_node_set_zone_labels(self, refresh_controls)


    def _toggle_render_details(self):
        return _view3d_controls.toggle_render_details(self)


    def _sync_render_details_visibility(self):
        return _view3d_controls.sync_render_details_visibility(self)


    def _install_side_panel_wheel_bindings(self, widget):
        return _view3d_panels.install_side_panel_wheel_bindings(self, widget)
    def _bind_side_panel_mousewheel(self, _event=None):
        return _view3d_panels.bind_side_panel_mousewheel(self, _event=None)
    def _unbind_side_panel_mousewheel(self, _event=None):
        return _view3d_panels.unbind_side_panel_mousewheel(self, _event=None)
    def _on_side_panel_mousewheel(self, event):
        return _view3d_panels.on_side_panel_mousewheel(self, event)
    def _side_panel_light_enabled(self):
        return _view3d_panels.side_panel_light_enabled(self)

    def _side_section_palette(self):
        return _view3d_panels.side_section_palette(self)

    def _update_side_section_styles(self):
        return _view3d_panels.update_side_section_styles(self)

    def _build_side_panel(self, panel):
        return _view3d_panels.build_side_panel(self, panel)
    def _on_keep_decorations_motion_changed(self):
        return _view3d_panels.on_keep_decorations_motion_changed(self)
    def _keep_decorations_during_motion_active(self):
        return _view3d_panels.keep_decorations_during_motion_active(self)
    def _on_keep_cmodel_blocks_motion_changed(self):
        return _view3d_panels.on_keep_cmodel_blocks_motion_changed(self)
    def _keep_cmodel_blocks_during_motion_active(self):
        return _view3d_panels.keep_cmodel_blocks_during_motion_active(self)
    def _toggle_hints(self):
        return _view3d_panels.toggle_hints(self)
    def _toggle_side_panel(self):
        return _view3d_panels.toggle_side_panel(self)
    def _preset_description(self, mode):
        return _view3d_panels.preset_description(self, mode)

    def _show_preset_hover_desc(self, mode):
        return _view3d_panels.show_preset_hover_desc(self, mode)

    def _clear_preset_hover_desc(self):
        return _view3d_panels.clear_preset_hover_desc(self)

    def _restore_active_preset_desc(self):
        return _view3d_panels.restore_active_preset_desc(self)

    def _preset_button_palette(self):
        return _view3d_panels.preset_button_palette(self)

    def _update_preset_quick_buttons(self):
        return _view3d_panels.update_preset_quick_buttons(self)

    @staticmethod
    def _canonical_view_preset_name(mode):
        return _view3d_helpers.canonical_view_preset_name(mode)

    @staticmethod
    def _view_preset_setting_names():
        return _view3d_helpers.view_preset_setting_names()

    def _capture_view_preset_settings(self):
        return _view3d_panels.capture_view_preset_settings(self)

    def _get_view_preset_override(self, mode=None):
        return _view3d_panels.get_view_preset_override(self, mode=None)

    def _apply_view_preset_override(self, mode=None):
        return _view3d_panels.apply_view_preset_override(self, mode=None)

    def _refresh_preset_override_buttons(self):
        return _view3d_panels.refresh_preset_override_buttons(self)

    def _save_current_view_preset(self):
        return _view3d_panels.save_current_view_preset(self)

    def _reset_current_view_preset(self):
        return _view3d_panels.reset_current_view_preset(self)

    def _set_view_preset(self, mode):
        return _view3d_panels.set_view_preset(self, mode)
    def _apply_view_preset(self, use_override=True):
        return _view3d_panels.apply_view_preset(self, use_override=True)

    def _focus_selected_building(self):
        return _view3d_navigation.focus_selected_building(self)

    def _on_context_scope_changed(self, refit=False):
        """Apply context-scope changes without touching parser caches."""
        try:
            self.rebuild_scene()
            if refit:
                self.fit_view()
            self.schedule_render()
        except Exception:
            try:
                self.schedule_render()
            except Exception:
                pass

    def _scope_rank_limit(self, value, *, local_limit=None):
        return _view3d_scene.scope_rank_limit(self, value, local_limit=local_limit)

    def _entity_scope_allows(self, e, scope_var, *, default='Local'):
        return _view3d_scene.entity_scope_allows(self, e, scope_var, default=default)

    def _entity_context_footprint_bounds(self, e, tid=None):
        return _view3d_scene.entity_context_footprint_bounds(self, e, tid, game_path=_get_game_path(), cmodel_segments_fn=_get_cmodel_segments_fn)

    # ── 3D Section tool ─────────────────────────────────────────────────────
    def _reset_section_for_new_scene(self):
        return _view3d_section.reset_section_for_new_scene(self)

    def _section_is_enabled(self):
        return _view3d_section.section_is_enabled(self)

    def _section_range(self):
        return _view3d_section.section_range(self)


    def _section_xy_range(self):
        return _view3d_section.section_xy_range(self)


    def _section_z_visible(self, z):
        return _view3d_section.section_z_visible(self, z)

    def _section_point_visible(self, x, y, z):
        return _view3d_section.section_point_visible(self, x, y, z)

    def _capture_section_guide_bounds(self):
        return _view3d_section.capture_section_guide_bounds(self)

    def _initialize_section_range(self):
        return _view3d_section.initialize_section_range(self)


    def _initialize_section_xy_range(self):
        return _view3d_section.initialize_section_xy_range(self)


    def _section_context_node_visible(self, n):
        return _view3d_section.section_context_node_visible(self, n)

    def _section_entity_intersects_context(self, e, kind=None):
        return _view3d_section.section_entity_intersects_context(self, e, kind)

    def _schedule_section_context_refresh(self, delay=90):
        return _view3d_section.schedule_section_context_refresh(self, delay)

    def _refresh_section_context_now(self):
        return _view3d_section.refresh_section_context_now(self)

    def _toggle_section_z(self):
        return _view3d_section.toggle_section_z(self)

    def _toggle_section_xy(self):
        return _view3d_section.toggle_section_xy(self)

    def _toggle_section_lock_z(self):
        return _view3d_section.toggle_section_lock_z(self)

    def _toggle_section_lock_xy(self):
        return _view3d_section.toggle_section_lock_xy(self)


    def _toggle_section(self):
        return _view3d_section.toggle_section(self)

    def _update_section_toolbar(self):
        return _view3d_section.update_section_toolbar(self)


    def _section_world_xy_bounds(self):
        return _view3d_section.section_world_xy_bounds(self)


    def _section_scene_z_bounds(self):
        return _view3d_section.section_scene_z_bounds(self)


    def _section_direct_drag_supported(self):
        return _view3d_section.section_direct_drag_supported(self)

    def _section_handle_hit(self, sx, sy):
        return _view3d_section.section_handle_hit(self, sx, sy)

    def _section_motion(self, event):
        return _view3d_section.section_motion(self, event)

    def _section_apply_drag_delta(self, kind, dy):
        return _view3d_section.section_apply_drag_delta(self, kind, dy)

    def _section_apply_xy_drag_delta(self, kind, dx, dy):
        return _view3d_section.section_apply_xy_drag_delta(self, kind, dx, dy)

    def _clip_segment_to_section(self, a, b, rng=None):
        return _view3d_section.clip_segment_to_section(self, a, b, rng)


    def _draw_section_overlay(self, draw):
        return _view3d_section.draw_section_overlay(self, draw)


    def _bind_events(self):
        return _view3d_controls.bind_events(self)

    def _request_close(self):
        return _view3d_controls.request_close(self)

    def _toggle_grid(self):
        return _view3d_controls.toggle_grid(self)

    def _toggle_node_overlay(self):
        return _view3d_controls.toggle_node_overlay(self)

    def _toggle_buildings(self):
        return _view3d_controls.toggle_buildings(self)

    def _toggle_vehicles(self):
        return _view3d_controls.toggle_vehicles(self)

    def _toggle_decor(self):
        return _view3d_controls.toggle_decor(self)

    def _toggle_context(self):
        return _view3d_controls.toggle_context(self)

    def _context_local(self):
        return _view3d_controls.context_local(self)

    def _context_smaller(self):
        return _view3d_controls.context_smaller(self)

    def _context_larger(self):
        return _view3d_controls.context_larger(self)

    def _context_all(self):
        return _view3d_controls.context_all(self)

    def _on_levels_toggle(self):
        return _view3d_controls.on_levels_toggle(self)

    def _toggle_levels(self):
        return _view3d_controls.toggle_levels(self)

    def _toggle_radii(self):
        return _view3d_controls.toggle_radii(self)

    def _toggle_labels(self):
        return _view3d_controls.toggle_labels(self)

    def _sync_context_string(self):
        return _view3d_controls.sync_context_string(self)

    def _refresh_node_set_status(self):
        return _view3d_controls.refresh_node_set_status(self)

    def _lock_selected_nodes_for_3d(self):
        return _view3d_controls.lock_selected_nodes_for_3d(self)

    def _unlock_3d_node_set(self):
        return _view3d_controls.unlock_3d_node_set(self)

    def refresh(self):
        return _view3d_controls.refresh(self)

    def _base_node_ids_for_3d(self):
        return _view3d_scene.base_node_ids_for_3d(self)

    def _zone_source_node_ids_for_3d(self):
        return _view3d_scene.zone_source_node_ids_for_3d(self)


    def _zone_action_palette(self):
        return _view3d_controls.zone_action_palette(self)


    def _zone_button(self, parent, text, cmd):
        return _view3d_controls.zone_button(self, parent, text, cmd)


    def _show_all_zone_nodes(self, zid):
        return _view3d_controls.show_all_zone_nodes(self, zid)


    def _select_zone_nodes(self, zid):
        return _view3d_controls.select_zone_nodes(self, zid)


    def _restore_locked_zone_scope(self):
        return _view3d_controls.restore_locked_zone_scope(self)


    def _detected_b14_counts(self):
        return _view3d_controls.detected_b14_counts(self)

    def _sync_zone_filter_disclosure(self):
        return _view3d_controls.sync_zone_filter_disclosure(self)

    def _toggle_zone_filter_details(self):
        return _view3d_controls.toggle_zone_filter_details(self)

    def _refresh_zone_filter_controls(self):
        return _view3d_controls.refresh_zone_filter_controls(self)

    def _update_zone_filter_summary(self, counts=None):
        return _view3d_controls.update_zone_filter_summary(self, counts)

    def _enabled_b14_filter_set(self):
        return _view3d_controls.enabled_b14_filter_set(self)

    def _set_all_zone_filters(self, visible=True):
        return _view3d_controls.set_all_zone_filters(self, visible)

    def _on_zone_filter_changed(self):
        return _view3d_controls.on_zone_filter_changed(self)

    def _selected_node_ids(self):
        return _view3d_scene.selected_node_ids(self)

    def rebuild_scene(self):
        return _view3d_scene.rebuild_scene(self)

    def _refresh_cmodel_block_menu(self, entities):
        return _view3d_controls.refresh_cmodel_block_menu(self, entities)

    def _refresh_block_layers_for(self, entities, kind, parent, vis_attr, ids_attr):
        return _view3d_scene.refresh_block_layers_for(self, entities, kind, parent, vis_attr, ids_attr, game_path=_get_game_path())

    def _reset_block_visibility(self):
        return _view3d_controls.reset_block_visibility(self)

    def _invalidate_block_caches(self):
        return _view3d_controls.invalidate_block_caches(self)

    def _visible_building_blocks(self):
        return _view3d_controls.visible_building_blocks(self)

    def _visible_decor_blocks(self):
        return _view3d_controls.visible_decor_blocks(self)

    def sync_node_state_fast(self, reason='node_change'):
        return _view3d_scene.sync_node_state_fast(self, reason)

    def _collect_neighbor_nodes(self, selected_ids):
        return _view3d_scene.collect_neighbor_nodes(self, selected_ids)

    def _collect_node_edges(self, selected_ids, neighbors):
        return _view3d_scene.collect_node_edges(self, selected_ids, neighbors)

    def _collect_context_entities(self, nodes):
        return _view3d_scene.collect_context_entities(self, nodes, game_path=_get_game_path(), pff_cache=_get_pff_cache())

    def _fit_capture_point(self, p):
        return _view3d_navigation.fit_capture_point(self, p)

    def _capture_visible_fit_bounds(self):
        return _view3d_navigation.capture_visible_fit_bounds(self)

    def fit_view(self):
        return _view3d_navigation.fit_view(self)

    def fit_view_to_nodes(self):
        return _view3d_navigation.fit_view_to_nodes(self)

    def activate_initial_view(self):
        return _view3d_navigation.activate_initial_view(self)

    def top_view(self):
        return _view3d_navigation.top_view(self)

    def _apply_side_view_index(self, index, announce=True):
        return _view3d_navigation.apply_side_view_index(self, index, announce)

    def side_view(self):
        return _view3d_navigation.side_view(self)

    def front_view(self):
        return _view3d_navigation.front_view(self)

    def prev_node(self):
        return _view3d_interaction.prev_node(self)

    def next_node(self):
        return _view3d_interaction.next_node(self)

    def _start_drag(self, event, mode):
        return _view3d_interaction.start_drag(self, event, mode)

    def _drag(self, event):
        return _view3d_interaction.drag(self, event)

    def _end_drag(self, event):
        return _view3d_interaction.end_drag(self, event)

    def _is_lock_active(self):
        try:
            return bool(getattr(self.app, '_locked_3d_node_set_active', False))
        except Exception:
            return False

    def _cancel_box_select(self):
        return _view3d_interaction.cancel_box_select(self)

    def _on_left_release(self, event):
        """Always finish an active box-selection, even if Shift/Ctrl was released first."""
        if getattr(self, '_box_sel_origin', None) is not None:
            self._box_select_release(event)
            # A box gesture must not also become a pan/click selection.
            self._drag_last = None
            self._drag_mode = None
            self._drag_press_xy = None
            self._drag_moved = False
            self._drag_ctrl = False
            return
        self._end_drag(event)

    def _start_box_select(self, event, mode):
        return _view3d_interaction.start_box_select(self, event, mode)

    def _box_select_drag(self, event):
        return _view3d_interaction.box_select_drag(self, event)

    def _box_select_release(self, event):
        return _view3d_interaction.box_select_release(self, event)

    def _pick_visible_node_id(self, sx, sy, max_dist=14):
        return _view3d_interaction.pick_visible_node_id(self, sx, sy, max_dist)

    def _select_node_from_3d_click(self, sx, sy):
        return _view3d_interaction.select_node_from_3d_click(self, sx, sy)


    def _escape_action(self):
        return _view3d_interaction.escape_action(self)


    def _on_pointer_motion(self, event):
        """Share ordinary pointer motion between Section handles and 3D connect hover."""
        try:
            self._section_motion(event)
        except Exception:
            pass
        if self._connect3d_source_id is not None:
            self._update_3d_connect_candidate(event.x, event.y)
            try:
                self.canvas.configure(cursor='crosshair')
            except Exception:
                pass


    def _show_3d_node_context_menu(self, event):
        return _view3d_interaction.show_3d_node_context_menu(self, event)


    def _start_3d_connection(self, source_id):
        return _view3d_interaction.start_3d_connection(self, source_id)


    def _cancel_3d_connection(self, silent=False):
        return _view3d_interaction.cancel_3d_connection(self, silent)


    def _update_3d_connect_candidate(self, sx, sy, schedule=True):
        return _view3d_interaction.update_3d_connect_candidate(self, sx, sy, schedule)


    def _refresh_3d_connect_candidate(self):
        return _view3d_interaction.refresh_3d_connect_candidate(self)


    def _commit_3d_connection(self, target_id):
        return _view3d_interaction.commit_3d_connection(self, target_id)


    @staticmethod
    def _draw_screen_dashed_line(draw, a, b, color, width=2, dash=9.0, gap=6.0):
        return _view3d_helpers.draw_screen_dashed_line(draw, a, b, color, width=2, dash=9.0, gap=6.0)

    def _draw_3d_connect_overlay(self, draw):
        return _view3d_interaction.draw_3d_connect_overlay(self, draw)


    def _wheel(self, event):
        return _view3d_interaction.wheel(self, event)

    def _zoom_at(self, factor):
        return _view3d_interaction.zoom_at(self, factor)

    def _end_zoom_motion(self):
        return _view3d_interaction.end_zoom_motion(self)

    def schedule_render(self):
        if self._render_job is not None:
            return
        try:
            self._render_job = self.after(16, self.render)
        except Exception as _e:
            print(f"[WF3D] schedule_render error: {_e}")

    def _motion_lod_active(self):
        """True while the 3D camera is being dragged and motion LOD is enabled."""
        try:
            return (bool(getattr(self, "_render_preview", False)) and
                    bool(getattr(self, "perf_motion_lod", tk.BooleanVar(value=True)).get()))
        except Exception:
            return bool(getattr(self, "_render_preview", False))

    def _performance_line_budget(self):
        """Return the current max line budget, or None for unlimited."""
        try:
            v = str(getattr(self, "perf_line_budget", tk.StringVar(value="15000")).get() or "15000").strip()
            if not v or v.lower().startswith("unlim"):
                return None
            return max(500, int(v))
        except Exception:
            return 15000

    def _begin_line_budget(self, preview=False):
        budget = self._performance_line_budget()
        if preview:
            # During camera drag, keep the view responsive by drawing a smaller
            # representative subset. On release we redraw full detail. Motion
            # LOD is a little stricter because it is specifically for orbit/pan
            # responsiveness in dense interiors.
            cap = 3500 if self._motion_lod_active() else 5000
            budget = cap if budget is None else min(int(budget), cap)
        self._draw_line_budget = budget
        self._draw_line_count = 0
        self._draw_line_skipped_budget = 0
        self._draw_line_skipped_cull = 0
        self._draw_budget_active = True

    def _end_line_budget(self):
        self._draw_budget_active = False

    def _line_budget_exhausted(self):
        """Return True once the current frame's configured line cap is reached."""
        try:
            if not bool(getattr(self, '_draw_budget_active', False)):
                return False
            budget = getattr(self, '_draw_line_budget', None)
            if budget is None:
                return False
            return int(getattr(self, '_draw_line_count', 0)) >= int(budget)
        except Exception:
            return False

    # ── 3D math/drawing ─────────────────────────────────────────────────────

    def _project(self, x, y, z):
        # This wrapper supplies the current camera snapshot.  The stateless
        # transform lives in view3d.camera so it can be tested independently.
        try:
            tx, ty, tz = self._proj_target
            cy = self._proj_cy; sy = self._proj_sy
            cp = self._proj_cp; sp = self._proj_sp
            cw = self._proj_cw; ch = self._proj_ch
        except Exception:
            tx, ty, tz = self.target
            cy = math.cos(self.yaw); sy = math.sin(self.yaw)
            cp = math.cos(self.pitch); sp = math.sin(self.pitch)
            cw = max(1, self.canvas.winfo_width())
            ch = max(1, self.canvas.winfo_height())
        return _view3d_camera.project_point(
            x, y, z,
            target=(tx, ty, tz),
            cos_yaw=cy, sin_yaw=sy,
            cos_pitch=cp, sin_pitch=sp,
            canvas_width=cw, canvas_height=ch,
            pan_x=self.pan_x, pan_y=self.pan_y, scale=self.scale)

    def _draw_lines_batch(self, draw, lines_iter):
        return _view3d_rendering.draw_lines_batch(self, draw, lines_iter)
    def _line(self, draw, a, b, color, width=1):
        return _view3d_rendering.line(self, draw, a, b, color, width)

    def _get_3d_label_font(self, size_name=None, px=None):
        return _view3d_rendering.get_3d_label_font(self, size_name, px)

    def _point_marker(self, draw, x, y, z, color, r=5, label=None):
        return _view3d_rendering.point_marker(self, draw, x, y, z, color, r, label)

    def _terrain_z(self, x, y):
        return _view3d_geometry.terrain_z(self, x, y)

    def _clear_3d_geometry_caches(self):
        return _view3d_geometry.clear_3d_geometry_caches(self)

    def _allow_underground_geometry(self):
        return _view3d_geometry.allow_underground_geometry(self)

    def _entity_base_z(self, e, ex=None, ey=None):
        return _view3d_geometry.entity_base_z(self, e, ex, ey)

    def _is_tunnel_entity(self, e):
        return _view3d_geometry.is_tunnel_entity(self, e)

    def _tunnel_context_radius(self):
        return _view3d_geometry.tunnel_context_radius(self)

    def _tunnel_entity_key(self, e):
        return _view3d_geometry.tunnel_entity_key(self, e)

    def _tunnel_focus_key(self):
        return _view3d_geometry.tunnel_focus_key(self)

    def _entity_matches_tunnel_key(self, e, key):
        return _view3d_geometry.entity_matches_tunnel_key(self, e, key)

    def _on_tunnel_focus_changed(self):
        return _view3d_geometry.on_tunnel_focus_changed(self)

    def _set_tunnel_focus_choices(self, choices):
        return _view3d_geometry.set_tunnel_focus_choices(self, choices)

    def _draw_grid(self, draw):
        return _view3d_rendering.draw_grid(self, draw)
    def _entity_height_guess(self, e, segs):
        return _view3d_geometry.entity_height_guess(self, e, segs)

    def _entity_segments3d(self, e):
        return _view3d_geometry.entity_segments3d(self, e, game_path=_get_game_path(), cmodel_segments_fn=_get_cmodel_segments_fn, cmodel_raw_fn=_get_cmodel_raw)

    def _group_palette_color(self, group_index):
        return _view3d_geometry.group_palette_color(self, group_index)

    def _selected_cmodel_group(self):
        return _view3d_geometry.selected_cmodel_group(self)

    def _selected_cmodel_group_mode(self):
        return _view3d_geometry.selected_cmodel_group_mode(self)

    def _selected_decor_cmodel_group(self):
        return _view3d_geometry.selected_decor_cmodel_group(self)

    def _selected_decor_cmodel_group_mode(self):
        return _view3d_geometry.selected_decor_cmodel_group_mode(self)

    def _selected_cmodel_detail_mode(self):
        return _view3d_geometry.selected_cmodel_detail_mode(self)

    def _dim_color(self, color, factor=0.32):
        return _view3d_geometry.dim_color(self, color, factor)

    def _model_line_weight(self):
        return _view3d_geometry.model_line_weight(self)

    def _model_line_width_px(self):
        return _view3d_geometry.model_line_width_px(self)

    def _model_line_color(self, color, factor=None):
        return _view3d_geometry.model_line_color(self, color, factor)

    # ── Node Focus render lens ──────────────────────────────────────────────
    NODE_FOCUS_DEFAULT_HEX = "#4A4036"

    @staticmethod
    def _node_focus_hex_to_rgb(value, fallback=(74, 64, 54)):
        return _view3d_helpers.node_focus_hex_to_rgb(value, fallback=(74, 64, 54))
    @staticmethod
    def _node_focus_rgb_to_hex(rgb):
        return _view3d_helpers.node_focus_rgb_to_hex(rgb)
    def _refresh_node_focus_color_button(self):
        return _view3d_controls.refresh_node_focus_color_button(self)

    def _set_node_focus_color(self, value, *, schedule=True, persist=False):
        return _view3d_controls.set_node_focus_color(self, value, schedule=schedule, persist=persist)

    def _on_node_focus_toggled(self):
        """Toggle the render lens without touching any scene/model caches."""
        self.schedule_render()

    def _open_node_focus_color_dialog(self):
        return _view3d_controls.open_node_focus_color_dialog(self)

    def _draw_cmodel_overlay_for_entity(self, draw, e, col=None,
                                        allow_2d_fallback=True,
                                        group_filter=..., group_mode=...,
                                        render_color_override=None):
        return _view3d_rendering.draw_cmodel_overlay_for_entity(
            self, draw, e, col=col,
            allow_2d_fallback=allow_2d_fallback,
            group_filter=group_filter, group_mode=group_mode,
            render_color_override=render_color_override)
    def _cmodel_solo_active(self):
        try:
            visible = self._visible_building_blocks()
            if visible is None:
                return False
            return len(visible) == 1
        except Exception:
            return False

    def _trim_3d_world_caches(self):
        return _view3d_collision.trim_3d_world_caches(self)

    def _entity_cache_key(self, e, suffix=None):
        return _view3d_collision.entity_cache_key(self, e, suffix)
    def _cmodel_world_lines_for_entity(self, e, col=None,
                                       allow_2d_fallback=True,
                                       group_filter=..., group_mode=...):
        return _view3d_rendering.cmodel_world_lines_for_entity(
            self, e, col=col,
            allow_2d_fallback=allow_2d_fallback,
            game_path=_get_game_path(),
            cmodel_segments_fn=_get_cmodel_segments_fn,
            group_filter=group_filter, group_mode=group_mode)
    def _draw_entities(self, draw):
        return _view3d_rendering.draw_entities(self, draw)
    def _draw_floor_coordinates(self, draw):
        return _view3d_rendering.draw_floor_coordinates(self, draw)
    @staticmethod
    def _wf_segment_hits_triangle(a, b, triangle):
        return _view3d_helpers.wf_segment_hits_triangle(a, b, triangle)

    def _wf_collision_triangles_for_entity(self, e):
        return _view3d_collision.collision_triangles_for_entity(
            self, e, game_path=_get_game_path())
    def _wf_build_edge_collision_index(self, entities):
        return _view3d_collision.build_edge_collision_index(
            self, entities, game_path=_get_game_path())
    def _wf_edge_is_blocked(self, a, b, index):
        return _view3d_collision.wf_edge_is_blocked(self, a, b, index)
    def _refresh_blocked_edge_cache(self):
        return _view3d_collision.refresh_blocked_edge_cache(self)

    def _sync_graph_end_filter_controls(self):
        return _view3d_controls.sync_graph_end_filter_controls(self)

    def _on_graph_ends_changed(self):
        """Toggle the disconnected-component end diagnostic without rebuilding geometry."""
        try:
            enabled = bool(self.show_graph_ends.get())
            if enabled:
                self._refresh_graph_end_cache()
            else:
                self._wf_graph_end_pairs = []
            self._sync_graph_end_filter_controls()
        finally:
            self.schedule_render()

    def _on_graph_end_filter_changed(self):
        """Render-only filter for pink clear-gap vs orange stair-like markers."""
        self.schedule_render()

    def _refresh_graph_end_cache(self):
        return _view3d_rendering.refresh_graph_end_cache(self)
    def _draw_graph_end_overlay(self, draw):
        return _view3d_rendering.draw_graph_end_overlay(self, draw)
    def _draw_graph_end_legend(self, draw):
        return _view3d_rendering.draw_graph_end_legend(self, draw)
    def _ref_model_support_z(self, x, y, node_z):
        return _view3d_rendering.ref_model_support_z(self, x, y, node_z)
    def _draw_ref_model(self, draw):
        return _view3d_rendering.draw_ref_model(self, draw, ref_model_geometry=_get_ref_model_geometry)
    def _draw_nodes(self, draw):
        return _view3d_rendering.draw_nodes(self, draw)


    def render(self):
        self._render_job = None
        try:
            _wf3d_dbg(f"RENDER: begin yaw={math.degrees(self.yaw):.1f} pitch={math.degrees(self.pitch):.1f} scale={self.scale:.3f} scene_nodes={len(self.scene.get('nodes') or [])} scene_entities={len(self.scene.get('entities') or [])}")
            from PIL import Image, ImageDraw, ImageTk
            import time as _wf_perf_time
            _wf_t0 = _wf_perf_time.perf_counter()
            _wf_marks = {}
            def _wf_mark(name, start):
                try:
                    _wf_marks[name] = (_wf_perf_time.perf_counter() - start) * 1000.0
                except Exception:
                    pass
                return _wf_perf_time.perf_counter()

            cw = max(1, self.canvas.winfo_width())
            ch = max(1, self.canvas.winfo_height())
            img = Image.new("RGB", (cw, ch), self.BG)
            draw = ImageDraw.Draw(img)

            # Camera motion can use the normal reduced-detail preview. Section
            # dragging is different: keep full geometry visible so the user sees
            # the cut happen live instead of having the model simplify mid-adjustment.
            preview = (bool(getattr(self, "perf_drag_preview", tk.BooleanVar(value=True)).get())
                       and self._drag_last is not None
                       and self._drag_mode in ("pan", "orbit"))
            self._render_preview = preview
            self._render_suppress_labels = preview
            self._wf_cache_hits = 0
            self._wf_cache_misses = 0
            self._dbg_vis_calls = 0
            self._dbg_vis_time_ms = 0.0

            # Prime projection constants once per frame. The hot
            # loops below can call _project() many thousands of times.
            try:
                self._proj_target = tuple(self.target)
                self._proj_cy = math.cos(self.yaw); self._proj_sy = math.sin(self.yaw)
                self._proj_cp = math.cos(self.pitch); self._proj_sp = math.sin(self.pitch)
                self._proj_cw = cw; self._proj_ch = ch
                self._front_view_width_boost = 2 if abs(self.pitch) < math.radians(25) else 1
            except Exception:
                pass

            # Grid is useful for orientation, but motion LOD may suppress it
            # while the camera is moving. Do not spend geometry budget on it.
            self._draw_budget_active = False
            _wf_pt = _wf_perf_time.perf_counter()
            self._draw_grid(draw)
            _wf_pt = _wf_mark('grid', _wf_pt)
            self._begin_line_budget(preview=preview)
            has_nodes = bool(self.scene.get("nodes") if self.scene else None)
            try:
                _node_focus_front = bool(self.node_focus_mode.get())
            except Exception:
                _node_focus_front = False

            # Normal 3D intentionally draws the graph first and models over it so
            # very large node sets do not bury the scene. Node Focus reverses that
            # priority: dim context geometry is background information and the AIN
            # graph is the thing the user is trying to read. In that mode the graph
            # is drawn ONCE, after entity geometry below -- never as a second pass.
            #
            # When a finite line budget is active, reserve enough of it for the
            # later graph pass. This keeps the existing performance cap meaningful
            # while preventing dense CModels from consuming the entire budget before
            # the focus layer reaches the renderer.
            _node_focus_full_budget = getattr(self, '_draw_line_budget', None)
            if (_node_focus_front and has_nodes and self.show_node_overlay.get()
                    and _node_focus_full_budget is not None):
                try:
                    _nf_budget = int(_node_focus_full_budget)
                    _nf_edges = len((self.scene or {}).get('edges') or ())
                    _nf_radius_lines = 0
                    if bool(getattr(self, 'show_radii', tk.BooleanVar(value=False)).get()):
                        try:
                            _nf_sel_count = len(set(getattr(self.app, 'selected_nodes', set()) or set()))
                        except Exception:
                            _nf_sel_count = 1
                        _nf_radius_lines = min(64, max(1, _nf_sel_count)) * 32
                    # Edges are the dominant node cost. A small cushion covers
                    # selected/blocked link halos without reserving half the frame
                    # for tiny graphs.
                    _nf_reserve = max(300, _nf_edges + _nf_radius_lines + 96)
                    _nf_reserve = min(_nf_reserve, max(0, int(_nf_budget * 0.60)))
                    self._draw_line_budget = max(500, _nf_budget - _nf_reserve)
                except Exception:
                    self._draw_line_budget = _node_focus_full_budget

            if (not _node_focus_front and has_nodes and self.show_node_overlay.get()):
                _wf_pt = _wf_perf_time.perf_counter()
                self._draw_nodes(draw)
                _wf_pt = _wf_mark('nodes', _wf_pt)
            else:
                _wf_marks['nodes'] = 0.0
            _wf_pt = _wf_perf_time.perf_counter()
            model_weight = self._model_line_weight()
            if model_weight < 0.999:
                thin_motion_preview = (
                    preview or bool(getattr(self, '_zoom_motion_active', False)))
                if thin_motion_preview:
                    # 0.50x still frames use a true supersampled half-pixel wire,
                    # but doing that full 2x RGBA + LANCZOS pass on every camera
                    # motion frame is dramatically more expensive than the normal
                    # 1x renderer. During pan/orbit *and wheel zoom*, use the same
                    # cheap 1x model raster path as 1.00x; the exact 0.50x frame is
                    # restored as soon as the camera input settles.
                    self._draw_coord_scale = 1.0
                    self._draw_width_scale = 1.0
                    try:
                        self._draw_entities(draw)
                    finally:
                        self._draw_coord_scale = 1.0
                        self._draw_width_scale = 1.0
                else:
                    # True sub-pixel model wires: render only entities at 2x, using
                    # a one-pixel high-resolution stroke (0.5 output pixel), then
                    # downsample and composite over the normal grid/node frame.
                    ss = 2
                    model_layer = Image.new("RGBA", (cw * ss, ch * ss), (0, 0, 0, 0))
                    model_draw = ImageDraw.Draw(model_layer)
                    self._draw_coord_scale = float(ss)
                    self._draw_width_scale = float(ss) * float(model_weight)
                    try:
                        self._draw_entities(model_draw)
                    finally:
                        self._draw_coord_scale = 1.0
                        self._draw_width_scale = 1.0

                    try:
                        resampling = Image.Resampling.LANCZOS
                    except Exception:
                        resampling = Image.LANCZOS
                    model_layer = model_layer.resize((cw, ch), resampling)

                    # LANCZOS correctly creates partial coverage for a half-pixel
                    # stroke. Boost alpha strongly enough that the original source colors
                    # remain readable instead of looking like a mere color-dimming
                    # slider; spatial coverage remains narrower than the 1x stroke.
                    try:
                        alpha = model_layer.getchannel("A")
                        alpha = alpha.point(lambda a: min(255, int(a * 2.10)) if a else 0)
                        model_layer.putalpha(alpha)
                    except Exception:
                        pass

                    composed = img.convert("RGBA")
                    composed.alpha_composite(model_layer)
                    img = composed.convert("RGB")
                    draw = ImageDraw.Draw(img)
            else:
                self._draw_entities(draw)
            _wf_pt = _wf_mark('entities', _wf_pt)
            if has_nodes:
                try:
                    self._draw_floor_coordinates(draw)
                except Exception:
                    pass

            # Node Focus foreground pass. Restore the frame's original line cap
            # first; entity drawing above used only the non-reserved portion. The
            # graph is therefore guaranteed a fair share of the SAME frame budget
            # without a duplicate node render or an extra full-size image layer.
            if (_node_focus_front and has_nodes and self.show_node_overlay.get()):
                try:
                    self._draw_line_budget = _node_focus_full_budget
                except Exception:
                    pass
                _nf_pt = _wf_perf_time.perf_counter()
                self._draw_nodes(draw)
                _wf_marks['nodes'] = (_wf_perf_time.perf_counter() - _nf_pt) * 1000.0

            if not has_nodes:
                msg = "Select nodes to see them in 3D."
                draw.text((20, 20), msg, fill=self.TEXT)
                try:
                    self._draw_section_overlay(draw)
                except Exception:
                    pass
            else:
                # Redraw only the active node marker on top so orientation/target
                # remains visible even when meshes draw over the node graph.
                try:
                    for n in self.scene.get("nodes") or []:
                        if n.id == self.active_node_id:
                            self._point_marker(draw, n.x, n.y, n.z, self.SELECTED_NODE_HALO, r=9, label=None)
                            self._point_marker(draw, n.x, n.y, n.z, self.NODE_ACTIVE, r=6, label=None)
                            break
                except Exception:
                    pass

                # Suspected disconnected-component endpoints are a diagnostic
                # overlay, so keep them above both normal geometry and the graph.
                try:
                    self._draw_graph_end_overlay(draw)
                except Exception:
                    pass

                # Compact on-canvas key for the two Graph ends meanings.  It is
                # itself gated by show_graph_ends and never appears otherwise.
                try:
                    self._draw_graph_end_legend(draw)
                except Exception:
                    pass

                # Embedded standing-human reference beside the active node.
                # Its feet are sampled from local support; node Z stays untouched.
                try:
                    self._draw_ref_model(draw)
                except Exception:
                    pass

                # Pending 3D connection is a viewport editing overlay: keep the
                # green dashed preview and candidate halo above model geometry.
                try:
                    self._draw_3d_connect_overlay(draw)
                except Exception:
                    pass

                # Section planes/handles are a viewport interaction overlay, so
                # draw them after scene geometry and the active-node marker.
                try:
                    self._draw_section_overlay(draw)
                except Exception:
                    pass

                # Small orientation marker.
                ox, oy = 52, ch - 52
                draw.line((ox, oy, ox + 34, oy), fill=(180, 60, 60), width=2)
                draw.text((ox + 38, oy - 7), "X", fill=(180, 60, 60))
                draw.line((ox, oy, ox, oy - 34), fill=(60, 170, 75), width=2)
                draw.text((ox - 5, oy - 50), "Z", fill=(60, 170, 75))

            self._end_line_budget()
            self._render_suppress_labels = False
            self._render_preview = False

            _wf_pt = _wf_perf_time.perf_counter()
            _reused = False
            try:
                _old = getattr(self, '_photo', None)
                if _old is not None and _old.width() == cw and _old.height() == ch:
                    _old.paste(img)
                    _reused = True
                else:
                    self._photo = ImageTk.PhotoImage(img)
            except Exception:
                self._photo = ImageTk.PhotoImage(img)
            try:
                if getattr(self, "_canvas_image_item", None) is not None:
                    if not _reused:
                        self.canvas.itemconfigure(self._canvas_image_item, image=self._photo)
                else:
                    self._canvas_image_item = self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
            except Exception:
                self.canvas.delete("all")
                self._canvas_image_item = self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
            _wf_mark('photo', _wf_pt)
            _wf_pt_post = _wf_perf_time.perf_counter()
            _wf3d_dbg(f"RENDER: end ok drawn_lines={getattr(self, '_draw_line_count', 0)} skipped_budget={getattr(self, '_draw_line_skipped_budget', 0)} skipped_cull={getattr(self, '_draw_line_skipped_cull', 0)} cache_hits={getattr(self, '_wf_cache_hits', 0)} cache_misses={getattr(self, '_wf_cache_misses', 0)}")
            _wf_pt_dbg = _wf_perf_time.perf_counter()
            self._refresh_3d_debug_panel()
            _wf_mark('dbg_panel', _wf_pt_dbg)
            _wf_pt_blk = _wf_perf_time.perf_counter()
            if not preview:
                try:
                    entities = (self.scene or {}).get('entities') or []
                    self._refresh_cmodel_block_menu(entities)
                except Exception:
                    pass
            _wf_mark('blk_refresh', _wf_pt_blk)
            _wf_mark('post', _wf_pt_post)
            _vis_calls = getattr(self, '_dbg_vis_calls', 0)
            _vis_time = getattr(self, '_dbg_vis_time_ms', 0.0)
            try:
                total_ms = (_wf_perf_time.perf_counter() - _wf_t0) * 1000.0
                # Keep frame-performance diagnostics available for development,
                # but do not spam the console during normal 3D use.
                if DEBUG_RENDER_TIMING:
                    print(
                        f"[PERF] {'motion' if preview else 'still'} {total_ms:.1f}ms | "
                        f"grid={float(_wf_marks.get('grid',0.0)):.1f} "
                        f"nodes={float(_wf_marks.get('nodes',0.0)):.1f} "
                        f"geom={float(_wf_marks.get('entities',0.0)):.1f} "
                        f"tk={float(_wf_marks.get('photo',0.0)):.1f} "
                        f"post={float(_wf_marks.get('post',0.0)):.1f} "
                        f"(dbg={float(_wf_marks.get('dbg_panel',0.0)):.1f} "
                        f"blk={float(_wf_marks.get('blk_refresh',0.0)):.1f}) | "
                        f"vis×{_vis_calls} {_vis_time:.1f}ms | "
                        f"lines={getattr(self, '_draw_line_count', 0)} "
                        f"hits={self._wf_cache_hits} miss={self._wf_cache_misses}"
                    )
                if hasattr(self, 'info_var'):
                    self.info_var.set(
                        f"3D {'motion' if preview else 'still'} {total_ms:.1f}ms | "
                        f"grid {float(_wf_marks.get('grid',0.0)):.1f} "
                        f"nodes {float(_wf_marks.get('nodes',0.0)):.1f} "
                        f"geom {float(_wf_marks.get('entities',0.0)):.1f} "
                        f"tk {float(_wf_marks.get('photo',0.0)):.1f} | "
                        f"lines {getattr(self, '_draw_line_count', 0)} "
                        f"skip {getattr(self, '_draw_line_skipped_budget', 0)}/{getattr(self, '_draw_line_skipped_cull', 0)}"
                    )
            except Exception:
                pass
        except Exception as e:
            self._end_line_budget()
            self._render_suppress_labels = False
            self._render_preview = False
            _wf3d_dbg(f"RENDER: exception={e}", force=True)
            import traceback
            traceback.print_exc()
            try:
                self.canvas.delete("all")
                self._canvas_image_item = None
                self.canvas.create_text(20, 20, anchor="nw", fill="#ff6666",
                                        text=f"3D render error: {e}")
            except Exception:
                pass


# connect_nodes — imported from ain_format

class Wireframe3DWindow(tk.Toplevel):
    """Detached popup wrapper for the reusable Wireframe3DView.

    Keep the old popup behavior working while moving
    all actual 3D UI/rendering logic into Wireframe3DView(tk.Frame).  Future
    embedded 3D mode can instantiate Wireframe3DView directly without copying
    renderer code.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("3D Wireframe - Node Context")
        self.geometry("1180x760")
        self.minsize(640, 420)
        self.configure(bg="#111111")
        self.view = Wireframe3DView(self, app, on_close=self._close_from_view)
        self.view.pack(side="top", fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._close_from_view)
        try:
            self.after_idle(self.view.activate_initial_view)
            self.after(80, self.view.activate_initial_view)
        except Exception:
            pass
        try:
            self.view.focus_set()
        except Exception:
            pass

    def _close_from_view(self):
        try:
            if getattr(self.app, "_wire3d_window", None) is self:
                self.app._wire3d_window = None
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # Compatibility delegates used by the main editor/cache trimming code.
    @property
    def _wf_entity_line_cache(self):
        return getattr(self.view, "_wf_entity_line_cache", {})

    @_wf_entity_line_cache.setter
    def _wf_entity_line_cache(self, value):
        try:
            self.view._wf_entity_line_cache = value
        except Exception:
            pass

    def refresh(self):
        return self.view.refresh()

    def rebuild_scene(self):
        return self.view.rebuild_scene()

    def schedule_render(self):
        return self.view.schedule_render()

    def fit_view(self):
        return self.view.fit_view()

    def show_3d_debug(self):
        return self.view.show_3d_debug()

