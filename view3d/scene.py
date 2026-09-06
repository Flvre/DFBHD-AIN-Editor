"""Scene assembly and live synchronization for the 3D view.

The view owns state; this module receives it explicitly and builds the
node/entity working scene without importing the application class.
"""

import math
import tkinter as tk

from cmodel.cmodel_access import (get_cmodel_segments_3d,
    get_cmodel_segments_3d_grouped, _cmodel_category_for_type)
from cmodel.cmodel_geom import (_cmodel_rotation_cache_key,
    _rotated_bounds_for_heading, _graphic_name_from_item)
from entity.entity_classify import _graphic_is_context_only, entity_layer_kind
from entity.entity_data import DEFAULT_RADIUS, FORCE_OBSTACLE, OBSTACLE_RADII
from resources.items_def import _load_items_def
import view3d.wf3d_debug as _wf3d_debug_mod

def scope_rank_limit(view, value, *, local_limit=None):
    """Return how many ranked buildings a scope should affect.

    Ranking is by relevance to the selected node/cluster: the first building
    is usually the one containing/touching the selected interior node.  This
    deliberately avoids a raw radius-only view, where an adjacent building
    can steal equal visual priority.
    """
    v = str(value or "Local")
    if v == "Off":
        return 0
    if v == "Selected":
        return 1
    if v == "Tight":
        return 3
    if v == "Full":
        return 10**9
    if local_limit is not None:
        try:
            return max(1, int(local_limit))
        except Exception:
            return 10
    return 10

def entity_scope_allows(view, e, scope_var, *, default='Local'):
    """Return True if an entity should draw for a given visual/collision scope.

    Scope used to be rank-aware only for buildings. Decorations and vehicles
    therefore ignored Selected/Tight/Local and were rendered whenever the
    scope was anything except Off. Keep the same rank contract for every
    context entity type so a focused CModel view cannot silently pull the
    entire decoration set back into the scene.
    """
    scope = str(scope_var.get() if hasattr(scope_var, 'get') else scope_var or default)
    if scope == "Off":
        return False

    kind = e.get("_wf_kind")
    rank = int(e.get("_wf_rank", 999999))
    if kind == "building":
        local_limit = int(view.max_buildings.get() or 16)
    elif kind == "vehicle":
        var = getattr(view, "max_vehicles", None)
        local_limit = int(var.get() if var is not None else 8)
    elif kind == "decoration":
        var = getattr(view, "max_decor", None)
        local_limit = int(var.get() if var is not None else 18)
    else:
        return True

    limit = view._scope_rank_limit(scope, local_limit=local_limit)
    if rank < limit:
        return True

    # Buildings whose footprint contains the selected nodes (edge_d2≈0)
    # should always render — the node is physically inside them. Without
    # this, a large building loses to a small nearby piece that happens
    # to have a closer center, hiding the structure the user is working in.
    if kind == "building" and scope in ("Selected", "Tight"):
        edge_d2 = float(e.get("_wf_edge_d2", float("inf")))
        if edge_d2 < 1.0:
            return True
    return False

def entity_context_footprint_bounds(view, e, tid=None, game_path=None, cmodel_segments_fn=None):
    """Return real-ish world XY footprint bounds for 3D context ranking.

    The node-centric 3D view decides which building is "selected" before it
    draws the CModel overlay.  Using only OBSTACLE_RADII can miss long/odd
    buildings, stairs, balconies, and wall-hugging nodes, which makes the
    correct structure vanish or lose Selected rank.  Prefer the already
    loaded CModel footprint when available, then fall back to old radius
    logic in _collect_context_entities.
    """
    try:
        tid = int(tid if tid is not None else e.get("type_id", 0))
        gp = game_path
        heading = float(e.get("heading", e.get("rot", e.get("yaw", 0.0))) or 0.0)
        h = int(round(heading)) % 360
        key = (gp, tid, h, _wf3d_debug_mod.WF3D_RENDER_CACHE_VERSION)
        cache = getattr(view, "_wf_context_footprint_cache", None)
        if not isinstance(cache, dict):
            cache = view._wf_context_footprint_cache = {}
        local_bounds = cache.get(key)
        if local_bounds is None:
            local_bounds = False
            segs2 = cmodel_segments_fn(tid, gp, allow_load=False, log=None)
            if segs2:
                local_bounds = _rotated_bounds_for_heading(
                    segs2,
                    heading,
                    cache_key=_cmodel_rotation_cache_key(tid, 'wf_context_2d', game_path=game_path),
                )
            else:
                segs3 = get_cmodel_segments_3d(tid, gp, allow_load=False, log=None)
                if segs3:
                    flat = [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in segs3]
                    local_bounds = _rotated_bounds_for_heading(
                        flat,
                        heading,
                        cache_key=_cmodel_rotation_cache_key(tid, 'wf_context_3d', game_path=game_path),
                    )
            if len(cache) > 1024:
                cache.clear()
            cache[key] = local_bounds
        if not local_bounds:
            return None
        mnx, mny, mxx, mxy = local_bounds
        w = mxx - mnx
        h = mxy - mny
        if w <= 0.01 and h <= 0.01:
            return None
        # Degenerate thin-strip CModels (e.g. platform railings parsed as
        # a 245m × 2.6m ribbon) produce a footprint that misses nodes
        # standing on the actual structure.  When the aspect ratio is
        # extreme, inflate the thin axis to a fraction of the long axis
        # so the footprint approximates the real spatial extent.
        long = max(w, h)
        short = min(w, h)
        if long > 8.0 and short > 0.01:
            ratio = long / short
            if ratio > 8.0:
                min_short = min(long * 0.15, 20.0)
                if short < min_short:
                    expand = (min_short - short) * 0.5
                    if w < h:
                        mnx -= expand; mxx += expand
                    else:
                        mny -= expand; mxy += expand
        ex = float(e.get("x", 0.0))
        ey = float(e.get("y", 0.0))
        pad = 0.75
        return (ex + mnx - pad, ey + mny - pad, ex + mxx + pad, ey + mxy + pad)
    except Exception:
        return None

def base_node_ids_for_3d(view):
    """Return the unfiltered 3D working set: locked set or live selection."""
    app = view.app
    locked = None
    try:
        if hasattr(app, '_get_3d_locked_node_ids'):
            locked = app._get_3d_locked_node_ids()
    except Exception:
        locked = None
    if locked is not None:
        cached = getattr(view, '_cached_base_node_ids', None)
        if cached is not None:
            return cached
        result = sorted(int(n) for n in locked if 0 <= int(n) < len(app.nodes))
        view._cached_base_node_ids = result
        return result

    # Live editor selection changes on every 3D pick/deselect and must not
    # be held in the locked-working-set cache.  The stale cache made a
    # deselected node remain visible until an unrelated scene interaction.
    view._cached_base_node_ids = None
    ids = sorted(int(n) for n in getattr(app, "selected_nodes", set()) if 0 <= int(n) < len(app.nodes))
    if not ids and getattr(app, "selected_id", None) is not None:
        try:
            sid = int(app.selected_id)
            if 0 <= sid < len(app.nodes):
                ids = [sid]
        except Exception:
            pass
    view._cached_base_node_ids = ids
    return ids

def zone_source_node_ids_for_3d(view):
    """Return the b14-list source set.

    When a 3D set is locked, use the original anchor set so temporary
    b14-zone expansion does not collapse the zone list to only the expanded
    zone.  When unlocked, fall back to the current live 3D base set.
    """
    app = view.app
    try:
        if bool(getattr(app, '_locked_3d_node_set_active', False)):
            anchor = getattr(app, '_locked_3d_anchor_node_ids', set()) or set()
            anchor = {int(n) for n in anchor if 0 <= int(n) < len(app.nodes)}
            if anchor:
                return sorted(anchor)
    except Exception:
        pass
    return view._base_node_ids_for_3d()

def selected_node_ids(view):
    ids = view._base_node_ids_for_3d()
    allowed = view._enabled_b14_filter_set()
    if allowed is None:
        return ids
    out = []
    try:
        for nid in ids:
            n = view.app.nodes[int(nid)]
            if (int(getattr(n, 'b14', 0)) & 0xFF) in allowed:
                out.append(int(nid))
    except Exception:
        return ids
    return out

def rebuild_scene(view):
    app = view.app

    # A map change invalidates all previous Section coordinates.
    try:
        _scene_map_key = str(getattr(app, 'bms_path', '') or '')
    except Exception:
        _scene_map_key = ''
    _prev_scene_map_key = getattr(view, '_section_scene_map_key', None)
    if _prev_scene_map_key is None:
        view._section_scene_map_key = _scene_map_key
    elif _scene_map_key != _prev_scene_map_key:
        view._section_scene_map_key = _scene_map_key
        view._reset_section_for_new_scene()

    # Section context refreshes must NEVER move the camera. The scene subset
    # can change continuously while a Z/XY handle is dragged, but target,
    # pan, zoom and orbit belong to the user, not to the filtered bounds.
    _preserve_section_camera = view._section_is_enabled()
    if _preserve_section_camera:
        try:
            _section_camera_target = tuple(view.target)
        except Exception:
            _section_camera_target = None
        try:
            _section_camera_pan = (float(view.pan_x), float(view.pan_y))
        except Exception:
            _section_camera_pan = None
        try:
            _section_camera_scale = float(view.scale)
        except Exception:
            _section_camera_scale = None
        try:
            _section_camera_angles = (float(view.yaw), float(view.pitch))
        except Exception:
            _section_camera_angles = None

    # Rebuilding the 3D scene is mostly a node/context operation.
    # Do NOT clear camera-independent entity/CModel world-line caches here.
    # Node edits, 3D picking, and inspector applies may rebuild the scene
    # frequently; static .3DI/CModel geometry should survive those passes.
    view._cached_base_node_ids = None
    try:
        view._refresh_zone_filter_controls()
    except Exception:
        pass
    node_ids = view._selected_node_ids()

    # Using/opening 3D before a map/selection existed must not seed stale
    # Section coordinates into the next real graph.
    if node_ids and getattr(view, '_section_had_no_scene', False):
        view._reset_section_for_new_scene()
        view._section_had_no_scene = False
    elif not node_ids:
        view._section_had_no_scene = True

    view._cached_base_node_ids = None
    view._refresh_node_set_status()
    if not node_ids:
        view.scene = {"node_ids": [], "nodes": [], "entities": [], "neighbors": [], "edges": [], "bounds": None}
        view._wf_blocked_edges = set()
        view._wf_graph_end_pairs = []
        base_ids = view._base_node_ids_for_3d()
        if base_ids:
            view.info_var.set("No visible b14 zones" if view._developer_mode_enabled() else "No visible zones")
        else:
            view.info_var.set("No selected nodes")
        return

    if view.active_node_id not in node_ids:
        sid = getattr(app, "selected_id", None)
        locked_active = bool(getattr(app, '_locked_3d_node_set_active', False))
        # When a 3D node set is locked, the main 2D selection may be
        # intentionally cleared.  Do not silently promote the last locked
        # node into an active/selected-looking node; wait for explicit 3D
        # picking in the next implementation step.
        if locked_active and sid is None:
            view.active_node_id = None
        else:
            view.active_node_id = sid if sid in node_ids else node_ids[-1]

    source_node_ids = list(node_ids)
    source_nodes = [app.nodes[i] for i in source_node_ids]

    # Section is a view operation: never mutate the editor selection/lock.
    # Derive a temporary visible subset for the 3D scene instead.
    if view._section_is_enabled():
        visible_node_ids = [
            int(i) for i in source_node_ids
            if view._section_context_node_visible(app.nodes[int(i)])
        ]
    else:
        visible_node_ids = source_node_ids

    nodes = [app.nodes[i] for i in visible_node_ids]
    neighbors = view._collect_neighbor_nodes(visible_node_ids)
    if view._section_is_enabled():
        neighbors = [n for n in neighbors if view._section_context_node_visible(n)]
    edges = view._collect_node_edges(visible_node_ids, neighbors)

    # Context query uses the visible section focus. If the current slice has
    # no nodes, retain source nodes only as a query anchor so decorations/
    # buildings can re-enter as the Section is moved.
    context_nodes = nodes if nodes else source_nodes
    entities = view._collect_context_entities(context_nodes)

    pts = [(n.x, n.y, n.z) for n in nodes]
    for e in entities:
        pts.append((float(e.get("x", 0.0)), float(e.get("y", 0.0)), float(e.get("z", app.terrain_z))))
    if pts:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)

        if _preserve_section_camera:
            # Section orbit should pivot around the CURRENT filtered
            # context, not the invisible full selected graph.
            #
            # Changing target normally translates the whole orthographic
            # view. Preserve the on-screen location of the new pivot by
            # compensating pan before/after the target change.
            try:
                _new_target = (float(cx), float(cy), float(cz))
                _old_target = tuple(view.target)

                # Prime/fallback projection with current camera state.
                _sx_before, _sy_before, _ = view._project(*_new_target)

                view.target = _new_target

                # _project may still have frame-cached projection target
                # during a rebuild, so compute the post-target position
                # explicitly from current yaw/pitch/scale.
                _cyaw = math.cos(float(view.yaw))
                _syaw = math.sin(float(view.yaw))
                _cp = math.cos(float(view.pitch))
                _sp = math.sin(float(view.pitch))
                _cw = max(1, view.canvas.winfo_width())
                _ch = max(1, view.canvas.winfo_height())

                # New target projects to viewport center + current pan.
                _sx_after = _cw * 0.5 + float(view.pan_x)
                _sy_after = _ch * 0.5 + float(view.pan_y)

                view.pan_x += float(_sx_before) - float(_sx_after)
                view.pan_y += float(_sy_before) - float(_sy_after)
            except Exception:
                # Fallback: keep old camera target if compensation fails.
                try:
                    view.target = _old_target
                except Exception:
                    pass
        else:
            view.target = (cx, cy, cz)

        mnx = min(p[0] for p in pts); mxx = max(p[0] for p in pts)
        mny = min(p[1] for p in pts); mxy = max(p[1] for p in pts)
        mnz = min(p[2] for p in pts); mxz = max(p[2] for p in pts)
        bounds = (mnx, mny, mnz, mxx, mxy, mxz)
    else:
        bounds = None

    view.scene = {
        "node_ids": visible_node_ids,
        "source_node_ids": source_node_ids,
        "nodes": nodes,
        "neighbors": neighbors,
        "edges": edges,
        "entities": entities,
        "bounds": bounds,
    }
    view._refresh_blocked_edge_cache()
    if bool(getattr(view, 'show_graph_ends', tk.BooleanVar(value=False)).get()):
        view._refresh_graph_end_cache()
    else:
        view._wf_graph_end_pairs = []
    nb = len([e for e in entities if e.get("_wf_kind") == "building"])
    nv = len([e for e in entities if e.get("_wf_kind") == "vehicle"])
    nd = len([e for e in entities if e.get("_wf_kind") == "decoration"])
    lock_tag = " | LOCKED" if bool(getattr(app, '_locked_3d_node_set_active', False)) else ""
    _node_info = (
        f"nodes {len(nodes)}/{len(source_node_ids)}"
        if view._section_is_enabled() else f"nodes {len(nodes)}")
    view.info_var.set(f"{_node_info}{lock_tag} | buildings {nb}/{int(view.max_buildings.get() or 0)} | decor {nd} | visual {view.visual_scope.get()} | collision {view.collision_scope.get()} | r {float(view.context_radius.get() or 0):.0f}m")
    view._refresh_cmodel_block_menu(entities)

    if _preserve_section_camera:
        try:
            # Target/pan were intentionally updated above so orbit pivots
            # around the filtered Section while the current frame stays put.
            # Only zoom and orientation are hard-preserved across refresh.
            if _section_camera_scale is not None:
                view.scale = _section_camera_scale
            if _section_camera_angles is not None:
                view.yaw, view.pitch = _section_camera_angles
        except Exception:
            pass

def refresh_block_layers_for(view, entities, kind, parent, vis_attr, ids_attr, game_path=None):
    try:
        if parent is None:
            return
        groups = set()
        for e in entities:
            ek = e.get("_wf_kind")
            if kind == "decoration":
                if ek not in ("decoration", "foliage"):
                    continue
            else:
                if ek != kind:
                    continue
            tid = int(e.get("type_id", 0))
            grouped = get_cmodel_segments_3d_grouped(tid, game_path, allow_load=False)
            if grouped:
                for rec in grouped:
                    if len(rec) >= 3:
                        groups.add(int(rec[0]))
        sorted_groups = sorted(groups)
        if sorted_groups == getattr(view, ids_attr, None):
            return
        setattr(view, ids_attr, sorted_groups)
        for w in parent.winfo_children():
            w.destroy()
        vis_dict = getattr(view, vis_attr, {})
        pal = view._side_section_palette()
        bg = pal['body_bg']
        sub_fg = pal.get('sub_label_fg', '#666666')
        if not sorted_groups:
            view._block_layer_toggle_buttons.pop(kind, None)
            view._block_layer_detail_frames.pop(kind, None)
            return
        label_text = "BUILDINGS" if kind == "building" else "DECORATIONS"
        ctrl_fg = pal.get('control_fg', '#eeeeee')
        ctrl_bg = pal.get('control_bg', '#353535')
        ctrl_abg = pal.get('control_active_bg', '#494949')
        ctrl_border = pal.get('control_border', '#686868')
        _is_light = view._side_panel_light_enabled()
        _csel = '#ffffff' if _is_light else '#303030'
        collapsed = bool(view._block_layer_collapsed.get(kind, True))

        head_row = tk.Frame(parent, bg=bg)
        head_row.pack(side="top", fill="x", padx=4, pady=(4, 2))

        details = tk.Frame(parent, bg=bg)
        view._block_layer_detail_frames[kind] = details

        def _sync_disclosure(_kind=kind, _label=label_text, _details=details):
            is_collapsed = bool(view._block_layer_collapsed.get(_kind, True))
            btn = view._block_layer_toggle_buttons.get(_kind)
            if btn is not None:
                try:
                    btn.configure(text=("+ " if is_collapsed else "- ") + _label)
                except Exception:
                    pass
            try:
                if is_collapsed:
                    if _details.winfo_manager():
                        _details.pack_forget()
                elif not _details.winfo_manager():
                    _details.pack(side="top", fill="x", padx=0, pady=(0, 1))
            except Exception:
                pass
            try:
                view.after_idle(lambda: view._side_scroll_canvas.configure(
                    scrollregion=view._side_scroll_canvas.bbox("all")))
            except Exception:
                pass

        def _toggle_disclosure(_kind=kind):
            view._block_layer_collapsed[_kind] = not bool(view._block_layer_collapsed.get(_kind, True))
            _sync_disclosure()

        disclosure = tk.Button(
            head_row, text=("+ " if collapsed else "- ") + label_text,
            command=_toggle_disclosure, bg=bg, fg=sub_fg,
            activebackground=bg, activeforeground=ctrl_fg,
            relief="flat", bd=0, highlightthickness=0,
            font=("Consolas", 9, "bold"), anchor="w", padx=0, pady=0)
        disclosure.pack(side="left", fill="x", expand=True)
        view._block_layer_toggle_buttons[kind] = disclosure

        btn_frame = tk.Frame(head_row, bg=bg)
        btn_frame.pack(side="right")
        def _set_all(on, _va=vis_attr):
            for bv in getattr(view, _va, {}).values():
                bv.set(on)
            view._invalidate_block_caches()
            view.schedule_render()
        tk.Button(btn_frame, text="all", command=lambda: _set_all(True),
                  bg=ctrl_bg, fg=ctrl_fg, activebackground=ctrl_abg,
                  activeforeground=ctrl_fg, relief="flat",
                  font=("Consolas", 8), padx=3, pady=0, bd=0,
                  highlightthickness=1, highlightbackground=ctrl_border,
                  highlightcolor=ctrl_border).pack(side="left", padx=1)
        tk.Button(btn_frame, text="none", command=lambda: _set_all(False),
                  bg=ctrl_bg, fg=ctrl_fg, activebackground=ctrl_abg,
                  activeforeground=ctrl_fg, relief="flat",
                  font=("Consolas", 8), padx=3, pady=0, bd=0,
                  highlightthickness=1, highlightbackground=ctrl_border,
                  highlightcolor=ctrl_border).pack(side="left", padx=1)

        new_vis = {}
        for gi in sorted_groups:
            old_var = vis_dict.get(gi)
            bv = old_var if old_var is not None else tk.BooleanVar(value=True)
            new_vis[gi] = bv
            color_rgb = view._group_palette_color(gi)
            color_hex = "#%02x%02x%02x" % color_rgb
            row = tk.Frame(details, bg=bg)
            row.pack(side="top", fill="x", padx=4, pady=1)
            accent = tk.Frame(row, bg=color_hex, width=2)
            accent.pack(side="left", fill="y", padx=(0, 4))
            accent.pack_propagate(False)
            tk.Checkbutton(row, text=f"Block {gi}", variable=bv,
                           command=lambda: (view._invalidate_block_caches(), view.schedule_render()),
                           bg=bg, fg=ctrl_fg, selectcolor=_csel,
                           activebackground=bg, activeforeground=ctrl_fg,
                           font=("Consolas", 9), bd=0, anchor="w").pack(side="left", fill="x", expand=True)
            def _solo(target_gi=gi, _va=vis_attr):
                for k, bv2 in getattr(view, _va, {}).items():
                    bv2.set(k == target_gi)
                view._invalidate_block_caches()
                view.schedule_render()
            tk.Button(row, text="S", command=_solo,
                      bg=ctrl_bg, fg=ctrl_fg, activebackground=ctrl_abg,
                      activeforeground=ctrl_fg, relief="flat",
                      font=("Consolas", 8), padx=3, pady=0, bd=0,
                      highlightthickness=1, highlightbackground=ctrl_border,
                      highlightcolor=ctrl_border).pack(side="right", padx=(2, 0))
        setattr(view, vis_attr, new_vis)
        _sync_disclosure()
        view._install_side_panel_wheel_bindings(parent)
    except Exception:
        pass

def sync_node_state_fast(view, reason='node_change'):
    """Cheap live-update path for 3D node edits/picks.

    The scene stores references to Node objects, so X/Y/Z/byte/radius edits
    are already visible without rebuilding entity context or static model
    caches.  Rebuild only if the visible node id set actually changed.
    """
    try:
        new_ids = view._selected_node_ids()
        cur_ids = list((view.scene or {}).get("node_ids") or [])
        if list(new_ids) != list(cur_ids):
            view.rebuild_scene()
            return False

        sid = getattr(view.app, "selected_id", None)
        if sid is not None:
            try:
                sid = int(sid)
            except Exception:
                sid = None
        view.active_node_id = sid if sid in set(cur_ids) else None
        # Coordinate edits can turn a previously clear edge into a wall
        # crossing (or vice versa) without changing the visible node set.
        # Reclassify only for edit reasons; pure selection remains cheap.
        if reason in {'inspector_apply', 'nudge_node', 'node_drag'}:
            view._refresh_blocked_edge_cache()
            if bool(getattr(view, 'show_graph_ends', tk.BooleanVar(value=False)).get()):
                view._refresh_graph_end_cache()
        view._refresh_node_set_status()
        return True
    except Exception:
        try:
            view.rebuild_scene()
        except Exception:
            pass
        return False

def collect_neighbor_nodes(view, selected_ids):
    app = view.app
    # Prototype lock means "show only this working set". Do not pull in
    # outside neighbors while locked; edges will be drawn only between
    # locked nodes.
    if bool(getattr(app, '_locked_3d_node_set_active', False)):
        return []
    selected = set(selected_ids)
    out = {}
    for nid in selected_ids:
        if not (0 <= nid < len(app.nodes)):
            continue
        n = app.nodes[nid]
        for nb in getattr(n, "neighbors", []) or []:
            try:
                nb = int(nb)
            except Exception:
                continue
            if nb in selected or nb < 0 or nb >= len(app.nodes):
                continue
            out[nb] = app.nodes[nb]
    return list(out.values())

def collect_node_edges(view, selected_ids, neighbors):
    app = view.app
    selected = set(selected_ids)
    neighbor_ids = set(getattr(n, "id", -1) for n in neighbors)
    edges = []
    seen = set()
    for nid in selected_ids:
        if not (0 <= nid < len(app.nodes)):
            continue
        a = app.nodes[nid]
        for nb in getattr(a, "neighbors", []) or []:
            try:
                nb = int(nb)
            except Exception:
                continue
            if nb < 0 or nb >= len(app.nodes):
                continue
            # Draw selected-selected and selected-neighbor links.
            if nb not in selected and nb not in neighbor_ids:
                continue
            key = tuple(sorted((nid, nb)))
            if key in seen:
                continue
            seen.add(key)
            edges.append((a, app.nodes[nb], nb in selected))
    return edges

def collect_context_entities(view, nodes, game_path=None, pff_cache=None):
    app = view.app
    if not getattr(app, "entities", None):
        return []
    radius = float(view.context_radius.get() or 32.0)
    if len(nodes) > 1:
        mnx = min(n.x for n in nodes) - radius
        mxx = max(n.x for n in nodes) + radius
        mny = min(n.y for n in nodes) - radius
        mxy = max(n.y for n in nodes) + radius
        cx = (mnx + mxx) * 0.5
        cy = (mny + mxy) * 0.5
    else:
        cx, cy = nodes[0].x, nodes[0].y
        mnx, mxx = cx - radius, cx + radius
        mny, mxy = cy - radius, cy + radius

    # XY Section becomes the actual context query area rather than a final
    # pixel clip. This is what makes decoration caps repopulate per-room and
    # prevents unrelated building/model context from reaching render caches.
    _section_xy = view._section_xy_range()
    if _section_xy is not None:
        sx0, sx1, sy0, sy1 = _section_xy
        mnx, mxx = sx0, sx1
        mny, mxy = sy0, sy1
        cx = (sx0 + sx1) * 0.5
        cy = (sy0 + sy1) * 0.5
        radius = max(1.0, 0.5 * math.hypot(sx1-sx0, sy1-sy0))

    buildings = []
    vehicles = []
    decor = []

    def _wf_context_kind(ent, tid):
        """Normalize MIS/items.def categories for 3D context rendering.

        Underground/basement pieces are often authored as generic
        Object/static collision entities, not category=building.  The old
        3D context collector only accepted building/vehicle/decoration, so
        these structural objects were silently skipped even in Full context.
        Treat static objects/collision-ish entities as building-like context
        so they render under the Buildings/CModel controls instead of being
        hidden behind the Decorations checkbox.
        """
        raw = str(ent.get("category") or _cmodel_category_for_type(tid) or "").strip().lower()
        layer = ""
        try:
            layer = str(entity_layer_kind(ent) or "").strip().lower()
        except Exception:
            layer = raw
        is_static = bool(ent.get("is_static"))
        graphic = str(ent.get("graphic") or "").strip()

        if raw in ("building", "buildings") or layer == "building":
            return "building"
        if raw in ("vehicle", "vehicles") or layer == "vehicle":
            return "vehicle"

        # Generic static objects can be tunnels, basements, bunkers, helper
        # collision pieces, bridges, etc.  They are structural for AIN work.
        structural_raw = {
            "object", "objects", "static", "statics",
            "collision", "collision model", "collision_model",
            "cmodel", "cmodel3d", "tile", "tiles", "char tile", "char_tile",
            "building object", "structure", "structures",
        }
        if (raw in structural_raw or layer == "object") and (is_static or graphic):
            return "building"

        # FORCE_OBSTACLE entries are static navigation/collision context even
        # when items.def naming is missing or odd.  Keep them with buildings
        # if they are not foliage/vehicles.
        try:
            if is_static and int(tid) in FORCE_OBSTACLE and layer not in ("foliage", "vehicle", "decoration"):
                return "building"
        except Exception:
            pass

        if layer == "foliage" or raw in ("foliage",):
            return "foliage"
        if raw in ("decoration", "decorations", "decor", "deco") or layer == "decoration":
            return "decoration"

        # Last resort: a static entity with a graphic can still be renderable
        # 3DI context.  Put it behind Buildings so tunnel pieces are visible
        # in Interior/X-Ray without forcing Decorations on.
        if is_static and graphic:
            return "building"
        return None

    for e in app.entities:
        tid = int(e.get("type_id", 0))
        kind = _wf_context_kind(e, tid)
        if kind not in ("building", "vehicle", "decoration", "foliage"):
            continue

        # Skip visual-only clutter (trash, effects, markers) that has
        # no navigation relevance — same graphic check the 2D view uses.
        if kind in ("decoration", "vehicle", "foliage"):
            _gr = str(e.get('graphic') or '').strip()
            if not _gr and game_path:
                try:
                    _gr = _graphic_name_from_item(_load_items_def(game_path, pff_cache=pff_cache).get(tid, {}))
                except Exception:
                    _gr = ''
            if _graphic_is_context_only(_gr):
                continue

        if not view._section_entity_intersects_context(e, kind):
            continue

        ex = float(e.get("x", 0.0)); ey = float(e.get("y", 0.0))
        base_r = float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
        footprint = view._entity_context_footprint_bounds(e, tid) if kind == "building" else None

        # Center-based distance: always computed as the baseline.
        ctr_edge_dx = max(abs(ex - cx) - base_r, 0.0)
        ctr_edge_dy = max(abs(ey - cy) - base_r, 0.0)
        ctr_edge_d2 = ctr_edge_dx * ctr_edge_dx + ctr_edge_dy * ctr_edge_dy
        ctr_center_d2 = (ex - cx) * (ex - cx) + (ey - cy) * (ey - cy)

        if footprint is not None:
            fmnx, fmny, fmxx, fmxy = footprint
            sep_dx = max(mnx - fmxx, fmnx - mxx, 0.0)
            sep_dy = max(mny - fmxy, fmny - mxy, 0.0)
            if sep_dx * sep_dx + sep_dy * sep_dy > 0.0:
                # Footprint doesn't overlap query box — fall through to
                # center-based instead of hard-skipping. A degenerate or
                # narrow CModel parse should not exclude entities that
                # center-based logic would keep.
                if ctr_edge_d2 > radius * radius:
                    continue
                edge_d2 = ctr_edge_d2
                center_d2 = ctr_center_d2
            else:
                fp_edge_dx = max(fmnx - cx, cx - fmxx, 0.0)
                fp_edge_dy = max(fmny - cy, cy - fmxy, 0.0)
                fp_edge_d2 = fp_edge_dx * fp_edge_dx + fp_edge_dy * fp_edge_dy
                fcx = (fmnx + fmxx) * 0.5
                fcy = (fmny + fmxy) * 0.5
                fp_center_d2 = (fcx - cx) * (fcx - cx) + (fcy - cy) * (fcy - cy)
                # Footprint should only IMPROVE ranking vs center-based,
                # never degrade it.  A correctly parsed large building gets
                # edge_d2≈0 (node inside); a degenerate thin-strip parse
                # falls back to the center-based value.
                edge_d2 = min(fp_edge_d2, ctr_edge_d2)
                center_d2 = min(fp_center_d2, ctr_center_d2)
        else:
            if ex + base_r < mnx or ex - base_r > mxx or ey + base_r < mny or ey - base_r > mxy:
                if ctr_edge_d2 > radius * radius:
                    continue
            edge_d2 = ctr_edge_d2
            center_d2 = ctr_center_d2

        e2 = dict(e)
        e2["_wf_kind"] = kind
        e2["_wf_raw_category"] = str(e.get("category") or _cmodel_category_for_type(tid) or "").lower()
        e2["_wf_edge_d2"] = edge_d2
        e2["_wf_dist2"] = center_d2
        if kind == "building":
            buildings.append(e2)
        elif kind == "vehicle":
            vehicles.append(e2)
        else:
            decor.append(e2)  # foliage also goes here for render order

    # If a specific tunnel piece is focused, make sure it is in
    # the scene even if the normal local context/rank cap would skip it.
    focus_key = view._tunnel_focus_key()
    if focus_key is not None:
        found_focus = any(view._entity_matches_tunnel_key(e, focus_key) for e in buildings + vehicles + decor)
        if not found_focus:
            for e in app.entities:
                try:
                    if view._entity_matches_tunnel_key(e, focus_key):
                        if not view._section_entity_intersects_context(e, "building"):
                            continue
                        e2 = dict(e)
                        e2["_wf_kind"] = "building"
                        e2["_wf_raw_category"] = str(e.get("category") or _cmodel_category_for_type(int(e.get("type_id", 0))) or "").lower()
                        e2["_wf_edge_d2"] = 0.0
                        e2["_wf_dist2"] = 0.0
                        e2["_wf_tunnel_forced"] = True
                        buildings.insert(0, e2)
                        break
                except Exception:
                    continue

    # Optional tunnel context.  R_Tun* underground areas are chains
    # of separate pieces.  A small selected node cluster can otherwise make
    # the 3D view show only one chunk and hide the rest of the passage.
    tunnel_radius = view._tunnel_context_radius()
    if tunnel_radius is not None:
        existing_tunnel_keys = {view._tunnel_entity_key(e) for e in (buildings + vehicles + decor) if view._is_tunnel_entity(e)}
        for e in app.entities:
            try:
                if not view._is_tunnel_entity(e):
                    continue
                key = view._tunnel_entity_key(e)
                if key in existing_tunnel_keys:
                    continue
                tid = int(e.get("type_id", 0))
                if not view._section_entity_intersects_context(e, "building"):
                    continue
                ex = float(e.get("x", 0.0)); ey = float(e.get("y", 0.0))
                base_r = float(OBSTACLE_RADII.get(tid, DEFAULT_RADIUS))
                edge_dx = max(abs(ex - cx) - base_r, 0.0)
                edge_dy = max(abs(ey - cy) - base_r, 0.0)
                edge_d2 = edge_dx * edge_dx + edge_dy * edge_dy
                if tunnel_radius != float("inf") and edge_d2 > tunnel_radius * tunnel_radius:
                    continue
                e2 = dict(e)
                e2["_wf_kind"] = "building"
                e2["_wf_raw_category"] = str(e.get("category") or _cmodel_category_for_type(tid) or "").lower()
                e2["_wf_edge_d2"] = edge_d2
                e2["_wf_dist2"] = (ex - cx) * (ex - cx) + (ey - cy) * (ey - cy)
                e2["_wf_tunnel_context"] = True
                buildings.append(e2)
                existing_tunnel_keys.add(key)
            except Exception:
                continue

    # Sort by edge distance first, then center distance. This makes
    # containing/touching structures win over random nearby structures.
    buildings.sort(key=lambda e: (e.get("_wf_edge_d2", 0.0), e.get("_wf_dist2", 0.0)))
    vehicles.sort(key=lambda e: (e.get("_wf_edge_d2", 0.0), e.get("_wf_dist2", 0.0)))
    decor.sort(key=lambda e: (e.get("_wf_edge_d2", 0.0), e.get("_wf_dist2", 0.0)))

    for i, e in enumerate(buildings):
        e["_wf_rank"] = i
        if i == 0:
            e["_wf_primary"] = True
    for i, e in enumerate(vehicles):
        e["_wf_rank"] = i
    for i, e in enumerate(decor):
        e["_wf_rank"] = i

    # Building scope is rank-based, not pure radius-based. This is the
    # first anti-clutter pass for interiors: the closest/containing building
    # remains first-class, while adjacent buildings stop getting equal visual
    # priority unless the user asks for wider context.
    raw_building_max = int(view.max_buildings.get() or 10)
    building_scope = str(getattr(view, "building_scope", tk.StringVar(value="Local")).get() or "Local")
    building_max = min(raw_building_max, view._scope_rank_limit(building_scope, local_limit=raw_building_max))
    vehicle_max = int(getattr(view, "max_vehicles", tk.IntVar(value=8)).get() or 8)
    decor_scope = str(getattr(view, "decoration_scope", tk.StringVar(value="Local")).get() or "Local")
    raw_decor_max = int(view.max_decor.get() or 18)
    decor_max = min(raw_decor_max, view._scope_rank_limit(decor_scope, local_limit=raw_decor_max))
    # Under Section, `decor` already contains ONLY current-volume candidates,
    # so the normal cap is repopulated from the room/slice instead of keeping
    # the stale whole-building decoration sample.
    chosen_buildings = list(buildings[:building_max])
    # Do not let the normal anti-clutter rank cap throw away tunnel pieces
    # the user explicitly asked to include.  This is still bounded by the
    # Tunnel context dropdown unless set to All.
    if view._tunnel_context_radius() is not None:
        chosen_keys = {view._tunnel_entity_key(e) for e in chosen_buildings if view._is_tunnel_entity(e)}
        for e in buildings:
            if e.get("_wf_tunnel_context"):
                key = view._tunnel_entity_key(e)
                if key not in chosen_keys:
                    chosen_buildings.append(e)
                    chosen_keys.add(key)
    return chosen_buildings + vehicles[:vehicle_max] + decor[:decor_max]
