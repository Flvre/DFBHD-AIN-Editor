"""
DFBHD generator core — extracted from ain_editor_v1_0.py.

GENERATOR_CORE_START
─────────────────────────────────────────────────────────────────────────────
G0 — GENERATOR CORE

Boundary contract:
  Inputs:  world_segments, entities_core, terrain_z or z_at, center/focus_radius,
           existing_nodes, generator options.
  Outputs: nodes, links, metrics/diagnostics.
  Forbidden: Tk widgets, messageboxes, editor selection state, hidden globals
             that change placement outside the explicit options.

Protected layer:
  Placement / graded radius bands / walker stepping / repair placement must
  not be altered unless applying an exact placement patch.
─────────────────────────────────────────────────────────────────────────────
"""
import generator.audit as _gen_audit
import generator.coverage as _gen_coverage
import generator.linking as _gen_linking
from generator.support import (Point, Segment, LabNode, SolidMask, radius_m,
    _cross, _convex_hull, _poly_area,
    _GeneratorStandingVolumeIndex, _V52Spatial,
    _v52_convex_hull, _v52_dist_point_seg, _v52_point_in_poly, _v52_poly_area,
    GENERATOR_REACHABLE_MAX_STEP, GENERATOR_REACHABLE_STAIR_STEP,
    GENERATOR_CORRIDOR_FIT, GENERATOR_CORRIDOR_FINAL_FACTOR,
    GENERATOR_CORRIDOR_SLIDE_BUDGET, GENERATOR_CORRIDOR_CENTER_FLOOR,
    GENERATOR_CORRIDOR_DOMINANCE,
    GENERATOR_QUANTIZED_GRID_M, GENERATOR_QUANTIZED_BASE_PITCH_M,
    GENERATOR_QUANTIZED_CENTER_CLEARANCE)
from format.ain_format import Node
from shared.nav_helpers import NAV_NODE_Z_LIFT

"""
DFBHD generator core.

The editor collects real BMS/PFF/CModel movement-band geometry and calls the
generator; the core returns generated nodes, links, and metrics.
"""
import math, random, statistics
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFilter = None

# Generator support classes/functions/constants — imported from generator_support
# (LabNode, SolidMask, _GeneratorStandingVolumeIndex, geometry helpers, constants)


def _build_generator_reachable_surface(center, radius, terrain_surface, support_triangles,
                                       support_triangle_metadata=None,
                                       collision_segments_3d=None,
                                       collision_triangles_3d=None,
                                       standing_volume_index=None,
                                       max_step=GENERATOR_REACHABLE_MAX_STEP, cell=0.25,
                                       include_rooftops=False,
                                       highest_broad_rooftops_only=False,
                                       seed_support_z=None,
                                       debug_transitions=False):
    """Build the automatic generator-only reachable-height filter.

    Terrain and CModel support triangles are rasterized into layered support
    candidates. Starting at the clicked seed surface, the flood may move only
    between neighboring supports whose height difference is <= max_step.
    Raised clutter therefore remains unreachable unless a real slope/stair
    chain leads onto it.
    """
    from collections import defaultdict as _defaultdict, deque as _deque
    cx, cy = float(center[0]), float(center[1])
    radius = float(radius)
    cell = float(cell)
    max_step = float(max_step)
    stable_surface_anchor = bool(
        seed_support_z is None
        and (not include_rooftops or highest_broad_rooftops_only))
    grid_cx, grid_cy = cx, cy
    if stable_surface_anchor:
        grid_cx = math.floor(cx / cell + 0.5) * cell
        grid_cy = math.floor(cy / cell + 0.5) * cell
    x0 = grid_cx - radius
    y0 = grid_cy - radius
    size = int(math.ceil((radius * 2.0) / cell)) + 1
    candidates = _defaultdict(list)
    model_support_heights = _defaultdict(list)
    model_support_sources = _defaultdict(list)
    model_support_categories = _defaultdict(list)
    model_support_provenance = _defaultdict(list)
    tunnel_stair_support_heights = _defaultdict(list)
    geometry_stair_support_sources = _defaultdict(list)
    collision_segments_3d = list(collision_segments_3d or [])
    standing_volume_index = standing_volume_index or (
        _GeneratorStandingVolumeIndex(collision_triangles_3d or [], cell=3.0)
        if collision_triangles_3d else None)
    standing_rejected_terrain = 0
    standing_rejected_model = 0
    terrain_overhead_model_support_rejected = 0
    terrain_overhead_geometry_stair_support_preserved = 0
    tunnel_roof_triangles_filtered = 0
    transition_blocked_count = 0
    transition_blocked_samples = []
    collision_cell = 0.5
    collision_grid = _defaultdict(list)
    for segment_index, segment in enumerate(collision_segments_3d):
        x1, y1, _z1, x2, y2, _z2, _entity_index = segment
        gx0 = int(math.floor(min(x1, x2) / collision_cell))
        gx1 = int(math.floor(max(x1, x2) / collision_cell))
        gy0 = int(math.floor(min(y1, y2) / collision_cell))
        gy1 = int(math.floor(max(y1, y2) / collision_cell))
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                collision_grid[(gx, gy)].append(segment_index)

    def _transition_blocked(ax, ay, az, bx, by, bz,
                            allow_step_riser=False):
        nonlocal transition_blocked_count
        if not collision_segments_3d:
            return False
        epsilon = 1e-6
        gx0 = int(math.floor((min(ax, bx) - epsilon) / collision_cell))
        gx1 = int(math.floor((max(ax, bx) + epsilon) / collision_cell))
        gy0 = int(math.floor((min(ay, by) - epsilon) / collision_cell))
        gy1 = int(math.floor((max(ay, by) + epsilon) / collision_cell))
        # Most surface transitions are nowhere near a hard segment.  Avoid
        # allocating and updating a set for those millions of empty queries;
        # materialize the deduplicating set only after the first occupied
        # collision-index cell is found.
        nearby = None
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                indexed = collision_grid.get((gx, gy))
                if not indexed:
                    continue
                if nearby is None:
                    nearby = set(indexed)
                else:
                    nearby.update(indexed)
        if nearby is None:
            return False
        dx = bx - ax
        dy = by - ay
        for segment_index in nearby:
            x1, y1, z1, x2, y2, z2, _entity_index = collision_segments_3d[segment_index]
            ex = x2 - x1
            ey = y2 - y1
            denominator = dx * ey - dy * ex
            if abs(denominator) < 1e-10:
                continue
            t = ((x1 - ax) * ey - (y1 - ay) * ex) / denominator
            if t < -1e-6 or t > 1.0 + 1e-6:
                continue
            u = ((x1 - ax) * dy - (y1 - ay) * dx) / denominator
            if u < 0.0 or u > 1.0:
                continue
            support_z = az + (bz - az) * t
            body_lo = support_z + 0.20
            body_hi = support_z + 1.80
            segment_z = z1 + (z2 - z1) * u
            if body_lo <= segment_z <= body_hi:
                # A discrete stair tread has a collision riser terminating at
                # the next tread's support height.  Treating that top edge as
                # chest-height wall collision prevents the flood from ever
                # reaching the next step.  Only bypass it inside a previously
                # verified multi-tread flight, and only when the intersected
                # edge ends at the higher support.  Rails and walls continue
                # above that height and remain blockers.
                if (allow_step_riser
                        and segment_z <= max(float(az), float(bz)) + 0.08):
                    continue
                transition_blocked_count += 1
                if (debug_transitions and max(az, bz) > 18.55
                        and len(transition_blocked_samples) < 512):
                    transition_blocked_samples.append({
                        'from': (round(ax, 4), round(ay, 4), round(az, 4)),
                        'to': (round(bx, 4), round(by, 4), round(bz, 4)),
                        'segment': tuple(round(float(value), 4)
                                         for value in (x1, y1, z1,
                                                       x2, y2, z2)),
                        'intersection_z': round(float(segment_z), 4),
                    })
                return True
        return False

    def _inside_disc(ix, iy):
        x = x0 + ix * cell
        y = y0 + iy * cell
        return math.hypot(x - cx, y - cy) <= radius

    terrain_support_heights = {}
    for ix in range(size):
        for iy in range(size):
            if not _inside_disc(ix, iy):
                continue
            x = x0 + ix * cell
            y = y0 + iy * cell
            try:
                z = terrain_surface(x, y)
            except Exception:
                z = None
            if z is not None:
                z = float(z)
                terrain_support_heights[(ix, iy)] = z
                if (standing_volume_index is not None
                        and not standing_volume_index.standing_clear(x, y, z)):
                    standing_rejected_terrain += 1
                else:
                    candidates[(ix, iy)].append(z)

    def _triangle_z_at(x, y, triangle):
        a, b, c = triangle
        denominator = (
            (b[1] - c[1]) * (a[0] - c[0])
            + (c[0] - b[0]) * (a[1] - c[1])
        )
        if abs(denominator) <= 1e-10:
            return None
        wa = (
            (b[1] - c[1]) * (x - c[0])
            + (c[0] - b[0]) * (y - c[1])
        ) / denominator
        wb = (
            (c[1] - a[1]) * (x - c[0])
            + (a[0] - c[0]) * (y - c[1])
        ) / denominator
        wc = 1.0 - wa - wb
        if wa < -0.001 or wb < -0.001 or wc < -0.001:
            return None
        return wa * a[2] + wb * b[2] + wc * c[2]

    support_triangle_metadata = list(support_triangle_metadata or ())
    for triangle_index, triangle in enumerate(support_triangles or []):
        xs = [point[0] for point in triangle]
        ys = [point[1] for point in triangle]
        triangle_metadata = (
            support_triangle_metadata[triangle_index]
            if triangle_index < len(support_triangle_metadata)
            else {}
        )
        triangle_is_tunnel_stair = bool(
            triangle_metadata.get('tunnel_stair_candidate')
            or triangle_metadata.get('stair_sequence_candidate'))
        triangle_is_geometry_stair = bool(
            triangle_metadata.get('geometry_stair_sequence_candidate'))
        triangle_entity_index = int(
            triangle_metadata.get('entity_index', -1))
        triangle_category = str(
            triangle_metadata.get('category') or '').strip().lower()
        component_size = int(
            triangle_metadata.get('component_size', 0) or 0)
        component_z_span = float(
            triangle_metadata.get('component_z_span', 0.0) or 0.0)
        component_footprint_area = float(
            triangle_metadata.get('component_footprint_area', 0.0) or 0.0)
        # v99_12 diagnostic: do not admit small sloped building-mesh patches as
        # ordinary walkable support unless geometry analysis identified them as
        # an actual stair sequence.  SPBHD_10 re_bsmt exposes several bevel/trim
        # components (8/9/16/24 tris, ~0.32-0.41 m Z span) that the old generic
        # normal-Z test treated as floors; the walker then climbs them and embeds
        # false elevated nodes into the main graph.
        triangle_is_small_sloped_building_patch = bool(
            triangle_category == 'building'
            and not triangle_is_tunnel_stair
            and not triangle_is_geometry_stair
            and 2 <= component_size <= 24
            and 0.08 <= component_z_span <= 0.60
            and 0.0 < component_footprint_area <= 12.0)
        if triangle_is_small_sloped_building_patch:
            continue
        if (
            not include_rooftops
            and triangle_metadata.get('tunnel_roof_candidate')
        ):
            tunnel_roof_triangles_filtered += 1
            continue
        ix0 = max(0, int(math.floor((min(xs) - x0) / cell)) - 1)
        ix1 = min(size - 1, int(math.ceil((max(xs) - x0) / cell)) + 1)
        iy0 = max(0, int(math.floor((min(ys) - y0) / cell)) - 1)
        iy1 = min(size - 1, int(math.ceil((max(ys) - y0) / cell)) + 1)
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                if not _inside_disc(ix, iy):
                    continue
                x = x0 + ix * cell
                y = y0 + iy * cell
                z = _triangle_z_at(x, y, triangle)
                if z is not None:
                    z = float(z)
                    terrain_at_cell = terrain_support_heights.get((ix, iy))
                    terrain_crosses_standing_band = (
                        terrain_at_cell is not None
                        and z + 0.20 <= float(terrain_at_cell) <= z + 1.80
                    )
                    if (terrain_crosses_standing_band
                            and not triangle_is_geometry_stair):
                        # Terrain crossing the standing-height band makes this
                        # an embedded model face, not a usable underground
                        # floor.  Deep basements/tunnels remain eligible because
                        # their terrain separation is greater than body height.
                        standing_rejected_model += 1
                        terrain_overhead_model_support_rejected += 1
                    elif (standing_volume_index is not None
                            and not standing_volume_index.standing_clear(x, y, z)):
                        standing_rejected_model += 1
                    else:
                        if (terrain_crosses_standing_band
                                and triangle_is_geometry_stair):
                            terrain_overhead_geometry_stair_support_preserved += 1
                        candidates[(ix, iy)].append(z)
                        model_support_heights[(ix, iy)].append(z)
                        model_support_sources[(ix, iy)].append(
                            (z, triangle_entity_index))
                        model_support_categories[(ix, iy)].append(
                            (z, triangle_category))
                        model_support_provenance[(ix, iy)].append(
                            (z, triangle_entity_index, triangle_category))
                        if triangle_is_tunnel_stair:
                            tunnel_stair_support_heights[(ix, iy)].append(z)
                        if triangle_is_geometry_stair:
                            geometry_stair_support_sources[(ix, iy)].append(
                                (z, triangle_entity_index))

    for key, values in list(candidates.items()):
        unique = []
        for value in sorted(values):
            if not unique:
                unique.append(value)
            elif abs(value - unique[-1]) <= max_step:
                unique[-1] = max(unique[-1], value)
            else:
                unique.append(value)
        candidates[key] = unique

    seed_cell = (
        max(0, min(size - 1, int(round((cx - x0) / cell)))),
        max(0, min(size - 1, int(round((cy - y0) / cell)))),
    )
    seed_values = candidates.get(seed_cell, [])
    if not seed_values:
        return None, {'enabled': True, 'reachable_cells': 0, 'reason': 'seed_has_no_support'}
    try:
        seed_terrain = terrain_surface(cx, cy)
    except Exception:
        seed_terrain = None
    seed_selection = 'lowest_available_support'
    if seed_support_z is not None:
        seed_index = min(
            range(len(seed_values)),
            key=lambda index: abs(seed_values[index] - float(seed_support_z)),
        )
        seed_selection = 'requested_support_z'
    elif include_rooftops and not highest_broad_rooftops_only:
        # Elevated-platform mode must start from the model layer under the
        # click.  Starting from terrain and then admitting every disconnected
        # topmost component is what made ports miss their deck while filling
        # unrelated roofs.  Exact model provenance was recorded during
        # rasterization, so this selection is local and inexpensive.
        model_values = model_support_heights.get(seed_cell, ())
        model_indices = [
            index for index, value in enumerate(seed_values)
            if any(abs(float(value) - float(model_z)) <= 0.08
                   for model_z in model_values)
        ]
        if model_indices:
            if seed_terrain is None:
                seed_index = min(model_indices, key=lambda index: seed_values[index])
            else:
                seed_index = min(
                    model_indices,
                    key=lambda index: abs(seed_values[index] - float(seed_terrain)),
                )
            seed_selection = 'clicked_model_support'
        elif seed_terrain is None:
            seed_index = 0
        else:
            seed_index = min(
                range(len(seed_values)),
                key=lambda index: abs(seed_values[index] - float(seed_terrain)),
            )
            seed_selection = 'terrain_fallback'
    elif seed_terrain is None:
        seed_index = 0
    else:
        seed_index = min(
            range(len(seed_values)),
            key=lambda index: abs(seed_values[index] - float(seed_terrain)),
        )
        seed_selection = 'terrain_nearest'

    seed_z = seed_values[seed_index]

    # Resolve support provenance once for every rasterized state.  The old
    # neighbor walker repeated these small ``any(...)`` scans for every edge
    # (and again in the reverse direction), which becomes millions of Python
    # calls on a large seed.  The aligned flag tuples preserve the exact 0.08 m
    # classification while moving the work out of the graph traversal.
    model_state_flags = {}
    tunnel_stair_state_flags = {}
    for state_key, state_values in candidates.items():
        model_sources = model_support_heights.get(state_key, ())
        stair_sources = tunnel_stair_support_heights.get(state_key, ())
        model_state_flags[state_key] = tuple(
            any(abs(float(source_z) - float(value)) <= 0.08
                for source_z in model_sources)
            for value in state_values
        )
        tunnel_stair_state_flags[state_key] = tuple(
            any(abs(float(source_z) - float(value)) <= 0.08
                for source_z in stair_sources)
            for value in state_values
        )

    def _is_tunnel_stair_layer(key, value):
        return any(
            abs(float(source_z) - float(value)) <= 0.08
            for source_z in tunnel_stair_support_heights.get(key, ())
        )

    def _state_neighbors(state, visited_states=None, available_states=None):
        ix, iy, support_index = state
        state_key = (ix, iy)
        current_z = candidates[state_key][support_index]
        current_is_model = model_state_flags[state_key][support_index]
        current_is_stair = tunnel_stair_state_flags[state_key][support_index]
        current_x = x0 + ix * cell
        current_y = y0 + iy * cell
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            neighbor = (ix + dx, iy + dy)
            neighbor_values = candidates.get(neighbor, ())
            if not neighbor_values:
                continue
            neighbor_model_flags = model_state_flags[neighbor]
            neighbor_stair_flags = tunnel_stair_state_flags[neighbor]
            next_x = x0 + neighbor[0] * cell
            next_y = y0 + neighbor[1] * cell
            for next_index, next_z in enumerate(neighbor_values):
                next_state = (neighbor[0], neighbor[1], next_index)
                # Flood traversal never needs to re-test an edge leading to a
                # state it has already reached.  Likewise, disconnected-
                # component discovery only needs edges whose far state is
                # still in ``remaining``.  The previous caller-side checks
                # happened after the full height/collision test, effectively
                # paying for most undirected edges twice.
                if (visited_states is not None
                        and next_state in visited_states):
                    continue
                if (available_states is not None
                        and next_state not in available_states):
                    continue
                step_limit = max_step
                stair_transition = (
                    current_is_model
                    and neighbor_model_flags[next_index]
                    and (
                        current_is_stair
                        or neighbor_stair_flags[next_index]
                    )
                )
                if stair_transition:
                    step_limit = max(
                        step_limit, GENERATOR_REACHABLE_STAIR_STEP)
                if abs(next_z - current_z) > step_limit + 1e-6:
                    continue
                if _transition_blocked(
                    current_x, current_y, current_z,
                    next_x, next_y, next_z,
                    allow_step_riser=stair_transition,
                ):
                    continue
                yield next_state

    reachable_states = {(seed_cell[0], seed_cell[1], seed_index)}
    queue = _deque(reachable_states)
    _flood_yield_counter = 0
    while queue:
        _flood_yield_counter += 1
        state = queue.popleft()
        for next_state in _state_neighbors(
                state, visited_states=reachable_states):
            reachable_states.add(next_state)
            queue.append(next_state)

    # Normally parsed third-party CModels can contain a complete geometric
    # stair sequence without carrying the recovered collision-policy marker.
    # Do not trust those sequences globally: first prove that this exact model
    # has reachable lower support while leaving a substantial upper-floor gap.
    # Only then enable its already geometry-validated stair components and
    # continue the same flood.  Models whose normal path already reaches their
    # top (for example MCOLYMPS) remain byte-for-byte on the original path.
    geometry_stair_recovery_entities = set()
    geometry_stair_recovery_states_added = 0
    entity_support_max = {}
    entity_reachable_max = {}
    for source_rows in model_support_sources.values():
        for source_z, entity_index in source_rows:
            if int(entity_index) < 0:
                continue
            entity_support_max[entity_index] = max(
                float(source_z),
                entity_support_max.get(entity_index, float('-inf')))
    for ix, iy, support_index in reachable_states:
        support_z = float(candidates[(ix, iy)][support_index])
        for source_z, entity_index in model_support_sources.get((ix, iy), ()):
            if int(entity_index) < 0 or abs(float(source_z) - support_z) > 0.08:
                continue
            entity_reachable_max[entity_index] = max(
                support_z,
                entity_reachable_max.get(entity_index, float('-inf')))
    for entity_index, maximum_z in entity_support_max.items():
        reachable_z = entity_reachable_max.get(entity_index)
        if reachable_z is None or maximum_z <= reachable_z + 2.0:
            continue
        has_upper_geometry_stair = any(
            int(source_entity) == int(entity_index)
            and float(source_z) > reachable_z + 0.20
            for source_rows in geometry_stair_support_sources.values()
            for source_z, source_entity in source_rows)
        if has_upper_geometry_stair:
            geometry_stair_recovery_entities.add(int(entity_index))

    if geometry_stair_recovery_entities:
        for state_key, source_rows in geometry_stair_support_sources.items():
            for source_z, entity_index in source_rows:
                if int(entity_index) in geometry_stair_recovery_entities:
                    tunnel_stair_support_heights[state_key].append(
                        float(source_z))
        for state_key, state_values in candidates.items():
            stair_sources = tunnel_stair_support_heights.get(state_key, ())
            tunnel_stair_state_flags[state_key] = tuple(
                any(abs(float(source_z) - float(value)) <= 0.08
                    for source_z in stair_sources)
                for value in state_values)
        recovery_before = len(reachable_states)
        queue = _deque(reachable_states)
        while queue:
            state = queue.popleft()
            for next_state in _state_neighbors(
                    state, visited_states=reachable_states):
                reachable_states.add(next_state)
                queue.append(next_state)
        geometry_stair_recovery_states_added = (
            len(reachable_states) - recovery_before)

    roof_states = set()
    # Rooftop generation is a building-layer request, not a one-way climb from
    # the selected Z.  Using ``seed_z`` as the lower bound made a roof-selected
    # seed reject every valid floor beneath it.  Classify supports against the
    # local terrain instead: above-ground floors are admitted in either
    # direction, while real basement/tunnel support remains below the cutoff.
    rooftop_ground_tolerance = 0.75
    terrain_height_cache = {}
    rooftop_aboveground_states_added = 0
    rooftop_belowground_states_rejected = 0
    rooftop_initial_belowground_removed = 0
    highest_rooftop_entities_considered = 0
    highest_rooftop_sheets_considered = 0
    highest_rooftop_narrow_sheets_rejected = 0
    highest_rooftop_covered_sheets_rejected = 0
    highest_rooftop_selected_entities = 0
    highest_rooftop_selected_sheets = 0
    highest_rooftop_selected_states = 0

    def _terrain_height_for_cell(ix, iy):
        key = (int(ix), int(iy))
        if key in terrain_height_cache:
            return terrain_height_cache[key]
        x = x0 + int(ix) * cell
        y = y0 + int(iy) * cell
        try:
            value = terrain_surface(x, y)
            value = None if value is None else float(value)
        except Exception:
            value = None
        terrain_height_cache[key] = value
        return value

    def _state_above_ground(state):
        ix, iy, support_index = state
        terrain_z = _terrain_height_for_cell(ix, iy)
        if terrain_z is None:
            return None
        support_z = float(candidates[(ix, iy)][support_index])
        return support_z >= terrain_z - rooftop_ground_tolerance

    seed_ground_z = _terrain_height_for_cell(seed_cell[0], seed_cell[1])
    seed_is_aboveground = (
        seed_ground_z is not None
        and float(seed_z) >= float(seed_ground_z) - rooftop_ground_tolerance
    )
    if (include_rooftops and not highest_broad_rooftops_only
            and seed_is_aboveground):
        kept_reachable_states = set()
        for state in reachable_states:
            above_ground = _state_above_ground(state)
            if above_ground is False:
                rooftop_initial_belowground_removed += 1
                continue
            kept_reachable_states.add(state)
        reachable_states = kept_reachable_states

    scan_disconnected_components = (
        include_rooftops
        or seed_selection not in (
            'clicked_model_support',
            'requested_support_z',
        )
    )

    def _state_has_non_decoration_model_support(state):
        ix, iy, support_index = state
        support_z = float(candidates[(ix, iy)][support_index])
        return any(
            abs(float(source_z) - support_z) <= 0.08
            and str(source_category) != 'decoration'
            for source_z, source_category
            in model_support_categories.get((ix, iy), ())
        )

    if scan_disconnected_components:
        all_states = {
            (ix, iy, support_index)
            for (ix, iy), values in candidates.items()
            for support_index in range(len(values))
        }
        remaining = all_states - reachable_states
        while remaining:
            root = remaining.pop()
            component = {root}
            component_queue = _deque([root])
            while component_queue:
                state = component_queue.popleft()
                for next_state in _state_neighbors(
                        state, available_states=remaining):
                    remaining.remove(next_state)
                    component.add(next_state)
                    component_queue.append(next_state)
            if include_rooftops and not highest_broad_rooftops_only:
                admitted = set()
                terrain_known = False
                for state in component:
                    above_ground = _state_above_ground(state)
                    if above_ground is not None:
                        terrain_known = True
                    if above_ground is False:
                        rooftop_belowground_states_rejected += 1
                    else:
                        admitted.add(state)
                if not terrain_known:
                    # No terrain height means above/below ground cannot be
                    # classified. Preserve the previous conservative fallback
                    # rather than silently admitting lower stacked floors.
                    comp_heights = [candidates[(ix, iy)][si]
                                    for ix, iy, si in component]
                    if (
                        seed_selection in (
                            'clicked_model_support',
                            'requested_support_z',
                        )
                        and min(comp_heights) <= seed_z + 1.0
                    ):
                        admitted.clear()
                admitted_cells = {(state[0], state[1]) for state in admitted}
                if len(admitted_cells) >= 48:
                    reachable_states.update(admitted)
                    roof_states.update(admitted)
                    rooftop_aboveground_states_added += len(admitted)
            else:
                # A normal 2D seed still needs disconnected structural layers
                # to recover basements and tunnels.  Decoration-only support is
                # not such a layer: crate stacks and furniture must remain
                # collision without becoming implicit underground platforms.
                recoverable_states = {
                    state for state in component
                    if _state_has_non_decoration_model_support(state)
                }
                component_cells = {
                    (state[0], state[1]) for state in recoverable_states
                }
                if len(component_cells) >= 48:
                    comp_heights = [candidates[(ix, iy)][si]
                                    for ix, iy, si in recoverable_states]
                    if max(comp_heights) <= seed_z + 1.0:
                        reachable_states.update(recoverable_states)
                        roof_states.update(recoverable_states)

    if include_rooftops and highest_broad_rooftops_only:
        # The literal highest raster sample is commonly a parapet, roof trim,
        # stair tread, or other narrow part of the same building CModel.  Build
        # coherent structural sheets per placed building and require a sheet to
        # have a two-cell-deep interior before it may define a selected roof.
        # The highest support is resolved per local vertical column, so every
        # exposed part of a stepped roof survives while covered floor support
        # does not.  This keeps the option geometric without map/model-name
        # special cases.
        structural_states_by_entity = _defaultdict(set)
        for state_key, state_values in candidates.items():
            provenance_rows = model_support_provenance.get(state_key, ())
            if not provenance_rows:
                continue
            for support_index, support_z in enumerate(state_values):
                state = (state_key[0], state_key[1], support_index)
                if _state_above_ground(state) is False:
                    continue
                for source_z, entity_index, source_category in provenance_rows:
                    if (source_category == 'building'
                            and int(entity_index) >= 0
                            and abs(float(source_z) - float(support_z)) <= 0.08):
                        structural_states_by_entity[int(entity_index)].add(state)

        selected_roof_states = set()
        sheet_step = min(max_step, 0.18)
        interior_offsets = (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
            (2, 0), (-2, 0), (0, 2), (0, -2),
        )
        for entity_index, entity_states in structural_states_by_entity.items():
            if not entity_states:
                continue
            highest_rooftop_entities_considered += 1
            entity_top_z_by_cell = {}
            for state in entity_states:
                state_cell = (state[0], state[1])
                state_z = float(candidates[state_cell][state[2]])
                entity_top_z_by_cell[state_cell] = max(
                    state_z,
                    entity_top_z_by_cell.get(state_cell, float('-inf')),
                )
            remaining_entity_states = set(entity_states)
            broad_sheets = []
            while remaining_entity_states:
                root = remaining_entity_states.pop()
                sheet = {root}
                sheet_queue = _deque([root])
                while sheet_queue:
                    state = sheet_queue.popleft()
                    state_z = float(candidates[(state[0], state[1])][state[2]])
                    for next_state in _state_neighbors(
                            state, available_states=remaining_entity_states):
                        next_z = float(
                            candidates[(next_state[0], next_state[1])]
                                      [next_state[2]])
                        if abs(next_z - state_z) > sheet_step + 1e-6:
                            continue
                        remaining_entity_states.remove(next_state)
                        sheet.add(next_state)
                        sheet_queue.append(next_state)

                highest_rooftop_sheets_considered += 1
                sheet_cells = {(state[0], state[1]) for state in sheet}
                interior_cells = {
                    key for key in sheet_cells
                    if all((key[0] + dx, key[1] + dy) in sheet_cells
                           for dx, dy in interior_offsets)
                }
                if len(sheet_cells) < 48 or not interior_cells:
                    highest_rooftop_narrow_sheets_rejected += 1
                    continue
                sheet_heights = sorted(
                    float(candidates[(state[0], state[1])][state[2]])
                    for state in sheet)
                representative_z = sheet_heights[len(sheet_heights) // 2]
                broad_sheets.append((
                    representative_z, len(interior_cells), len(sheet_cells), sheet))

            if not broad_sheets:
                continue
            entity_selected = []
            for row in broad_sheets:
                sheet = row[3]
                sheet_top_z_by_cell = {}
                for state in sheet:
                    state_cell = (state[0], state[1])
                    state_z = float(candidates[state_cell][state[2]])
                    sheet_top_z_by_cell[state_cell] = max(
                        state_z,
                        sheet_top_z_by_cell.get(state_cell, float('-inf')),
                    )
                exposed_cells = {
                    state_cell
                    for state_cell, sheet_z in sheet_top_z_by_cell.items()
                    if sheet_z >= (
                        entity_top_z_by_cell.get(state_cell, float('inf')) - 0.08)
                }
                exposed_interior_cells = {
                    key for key in exposed_cells
                    if all((key[0] + dx, key[1] + dy) in exposed_cells
                           for dx, dy in interior_offsets)
                }
                if len(exposed_cells) < 48 or not exposed_interior_cells:
                    highest_rooftop_covered_sheets_rejected += 1
                    continue
                exposed_states = {
                    state for state in sheet
                    if (state[0], state[1]) in exposed_cells
                }
                entity_selected.append((row, exposed_states))
            if entity_selected:
                highest_rooftop_selected_entities += 1
            highest_rooftop_selected_sheets += len(entity_selected)
            for _row, exposed_states in entity_selected:
                selected_roof_states.update(exposed_states)

        # Highest-rooftop mode retains the complete ordinary Z-aware domain and
        # changes only which additional disconnected elevated sheets are added.
        # The selected structural roofs are tagged separately for the layered
        # rooftop pass; terrain, interiors, basements, tunnels, and stairs keep
        # their ordinary placement behavior.
        reachable_states.update(selected_roof_states)
        roof_states = set(selected_roof_states)
        highest_rooftop_selected_states = len(selected_roof_states)

    reachable_heights = _defaultdict(list)
    for ix, iy, support_index in reachable_states:
        reachable_heights[(ix, iy)].append(candidates[(ix, iy)][support_index])
    reachable_layers = {
        key: tuple(sorted(set(round(value, 5) for value in values)))
        for key, values in reachable_heights.items()
        if values
    }
    model_layers = {}
    geometry_stair_layers = {}
    tunnel_stair_layers = {}
    for key, layer_values in reachable_layers.items():
        source_values = model_support_heights.get(key, ())
        matched = tuple(
            value for value in layer_values
            if any(abs(float(source_z) - float(value)) <= 0.08
                   for source_z in source_values)
        )
        if matched:
            model_layers[key] = matched
        geometry_stair_matched = tuple(
            value for value in layer_values
            if any(abs(float(source_z) - float(value)) <= 0.08
                   for source_z, _entity_index in
                   geometry_stair_support_sources.get(key, ()))
        )
        if geometry_stair_matched:
            geometry_stair_layers[key] = geometry_stair_matched
        stair_matched = tuple(
            value for value in layer_values
            if _is_tunnel_stair_layer(key, value)
        )
        if stair_matched:
            tunnel_stair_layers[key] = stair_matched
    roof_layers_raw = _defaultdict(list)
    for ix, iy, support_index in roof_states:
        roof_layers_raw[(ix, iy)].append(candidates[(ix, iy)][support_index])
    roof_layers = {
        key: tuple(sorted(set(round(value, 5) for value in values)))
        for key, values in roof_layers_raw.items()
        if values
    }
    resolved = {key: max(values) for key, values in reachable_layers.items()}

    def _surface_at(x, y):
        ix = int(round((float(x) - x0) / cell))
        iy = int(round((float(y) - y0) / cell))
        return resolved.get((ix, iy))

    def _heights_at(x, y):
        ix = int(round((float(x) - x0) / cell))
        iy = int(round((float(y) - y0) / cell))
        return reachable_layers.get((ix, iy), ())

    def _height_near(x, y, preferred_z):
        values = _heights_at(x, y)
        if not values:
            return None
        if preferred_z is None:
            return max(values)
        return min(values, key=lambda value: abs(value - float(preferred_z)))

    def _model_support_near(x, y, preferred_z=None):
        ix = int(round((float(x) - x0) / cell))
        iy = int(round((float(y) - y0) / cell))
        values = model_support_heights.get((ix, iy), ())
        if not values:
            return False
        if preferred_z is None:
            return True
        pz = float(preferred_z)
        return any(abs(float(value) - pz) <= max_step + 1e-6 for value in values)

    def _path_height(ax, ay, start_z, bx, by, step_limit=None):
        distance = math.hypot(float(bx) - float(ax), float(by) - float(ay))
        steps = max(1, int(math.ceil(distance / max(0.10, cell * 0.75))))
        current_z = float(start_z)
        path_step_limit = (
            float(max_step) if step_limit is None else float(step_limit))
        for step_index in range(1, steps + 1):
            amount = step_index / steps
            x = float(ax) + (float(bx) - float(ax)) * amount
            y = float(ay) + (float(by) - float(ay)) * amount
            values = _heights_at(x, y)
            possible = [
                value for value in values
                if abs(value - current_z) <= path_step_limit + 1e-6
            ]
            if not possible:
                return None
            current_z = min(possible, key=lambda value: abs(value - current_z))
        return current_z

    roof_cells = {(ix, iy) for ix, iy, _support_index in roof_states}
    _surface_at.heights_at = _heights_at
    _surface_at.height_near = _height_near
    _surface_at.model_support_near = _model_support_near
    _surface_at.path_height = _path_height
    _surface_at.reachable_layers = reachable_layers
    _surface_at.model_layers = model_layers
    _surface_at.geometry_stair_layers = geometry_stair_layers
    _surface_at.tunnel_stair_layers = tunnel_stair_layers
    _surface_at.roof_layers = roof_layers
    _surface_at.roof_cells = roof_cells
    _surface_at.grid_origin = (x0, y0)
    _surface_at.grid_cell = cell
    _surface_at.seed_z = seed_z
    _surface_at.seed_ground_z = seed_ground_z
    _surface_at.seed_selection = seed_selection
    _surface_at.include_rooftops = bool(include_rooftops)
    _surface_at.highest_broad_rooftops_only = bool(
        include_rooftops and highest_broad_rooftops_only)
    _surface_at.standing_volume_index = standing_volume_index

    metrics = {
        'enabled': True,
        'max_step': max_step,
        'stair_step': GENERATOR_REACHABLE_STAIR_STEP,
        'cell': cell,
        'support_triangles': len(support_triangles or []),
        'collision_segments_3d': len(collision_segments_3d),
        'candidate_cells': len(candidates),
        'reachable_cells': len(resolved),
        'reachable_layers': sum(len(values) for values in reachable_layers.values()),
        'roof_cells': len(roof_cells),
        'include_rooftops': bool(include_rooftops),
        'highest_broad_rooftops_only': bool(
            include_rooftops and highest_broad_rooftops_only),
        'highest_rooftop_entities_considered': int(
            highest_rooftop_entities_considered),
        'highest_rooftop_sheets_considered': int(
            highest_rooftop_sheets_considered),
        'highest_rooftop_narrow_sheets_rejected': int(
            highest_rooftop_narrow_sheets_rejected),
        'highest_rooftop_covered_sheets_rejected': int(
            highest_rooftop_covered_sheets_rejected),
        'highest_rooftop_selected_entities': int(
            highest_rooftop_selected_entities),
        'highest_rooftop_selected_sheets': int(
            highest_rooftop_selected_sheets),
        'highest_rooftop_selected_states': int(
            highest_rooftop_selected_states),
        'rooftop_ground_tolerance': float(rooftop_ground_tolerance),
        'seed_ground_z': seed_ground_z,
        'seed_is_aboveground': bool(seed_is_aboveground),
        'rooftop_aboveground_states_added':
            int(rooftop_aboveground_states_added),
        'rooftop_belowground_states_rejected':
            int(rooftop_belowground_states_rejected),
        'rooftop_initial_belowground_removed':
            int(rooftop_initial_belowground_removed),
        'requested_seed_support_z': (None if seed_support_z is None
                                     else float(seed_support_z)),
        'seed_selection': seed_selection,
        'seed_z': seed_z,
        'reachable_min_z': min(resolved.values()) if resolved else None,
        'reachable_max_z': max(resolved.values()) if resolved else None,
        'standing_rejected_terrain': standing_rejected_terrain,
        'standing_rejected_model': standing_rejected_model,
        'terrain_overhead_model_support_rejected':
            terrain_overhead_model_support_rejected,
        'terrain_overhead_geometry_stair_support_preserved':
            terrain_overhead_geometry_stair_support_preserved,
        'geometry_stair_recovery_entities': sorted(
            int(value) for value in geometry_stair_recovery_entities),
        'geometry_stair_recovery_states_added': int(
            geometry_stair_recovery_states_added),
        'transition_blocked_count': transition_blocked_count,
        'transition_blocked_samples': (transition_blocked_samples
                                       if debug_transitions else None),
        'tunnel_roof_triangles_filtered': tunnel_roof_triangles_filtered,
        'standing_volume_index': (standing_volume_index.metrics()
                                  if standing_volume_index is not None else None),
    }
    return _surface_at, metrics


def _build_generator_terrain_surface_contract(center, radius, terrain_surface,
                                              collision_triangles_3d=None,
                                              standing_volume_index=None,
                                              cell=0.50):
    """Give Z-off generation the same map-space support contract as Z-on.

    This deliberately contains terrain only: no model floor, stair, rooftop or
    basement is admitted.  The compact 0.50 m raster replaces the later Z-off
    coverage raster, lets all ordinary stages share one domain, and preserves
    the inexpensive exterior path while eliminating seed-relative behavior.
    """
    from collections import defaultdict as _defaultdict

    cx, cy = float(center[0]), float(center[1])
    radius = float(radius)
    cell = max(0.25, float(cell))
    standing_volume_index = standing_volume_index or (
        _GeneratorStandingVolumeIndex(collision_triangles_3d or [], cell=3.0)
        if collision_triangles_3d else None)

    # Global integer keys make this contract independent of seed centre/radius.
    ix0 = int(math.floor((cx - radius) / cell))
    ix1 = int(math.ceil((cx + radius) / cell))
    iy0 = int(math.floor((cy - radius) / cell))
    iy1 = int(math.ceil((cy + radius) / cell))
    reachable_layers = {}
    rejected_standing = 0
    sampled = 0
    for ix in range(ix0, ix1 + 1):
        x = ix * cell
        for iy in range(iy0, iy1 + 1):
            y = iy * cell
            if math.hypot(x - cx, y - cy) > radius:
                continue
            sampled += 1
            try:
                value = terrain_surface(x, y)
            except Exception:
                value = None
            if value is None:
                continue
            value = float(value)
            if (standing_volume_index is not None
                    and not standing_volume_index.standing_clear(x, y, value)):
                rejected_standing += 1
                continue
            reachable_layers[(ix, iy)] = (value,)

    def _heights_at(x, y):
        return reachable_layers.get((
            int(round(float(x) / cell)),
            int(round(float(y) / cell))), ())

    def _height_near(x, y, preferred_z=None):
        values = _heights_at(x, y)
        if not values:
            return None
        if preferred_z is None:
            return values[0]
        return min(values, key=lambda value: abs(value - float(preferred_z)))

    def _model_support_near(_x, _y, preferred_z=None):
        return False

    def _path_height(ax, ay, start_z, bx, by, step_limit=None):
        distance = math.hypot(float(bx) - float(ax), float(by) - float(ay))
        steps = max(1, int(math.ceil(distance / max(0.10, cell * 0.75))))
        current_z = float(start_z)
        limit = (GENERATOR_REACHABLE_MAX_STEP if step_limit is None
                 else float(step_limit))
        for step_index in range(1, steps + 1):
            amount = step_index / steps
            x = float(ax) + (float(bx) - float(ax)) * amount
            y = float(ay) + (float(by) - float(ay)) * amount
            values = _heights_at(x, y)
            possible = [value for value in values
                        if abs(float(value) - current_z) <= limit + 1e-6]
            if not possible:
                return None
            current_z = min(possible, key=lambda value: abs(value - current_z))
        return current_z

    try:
        seed_z = terrain_surface(cx, cy)
        seed_z = None if seed_z is None else float(seed_z)
    except Exception:
        seed_z = None

    # Preserve the continuous terrain callback used before this contract; only
    # attach the common map-space metadata consumed by generator stages.
    terrain_surface.heights_at = _heights_at
    terrain_surface.height_near = _height_near
    terrain_surface.model_support_near = _model_support_near
    terrain_surface.path_height = _path_height
    terrain_surface.reachable_layers = reachable_layers
    terrain_surface.model_layers = {}
    terrain_surface.geometry_stair_layers = {}
    terrain_surface.tunnel_stair_layers = {}
    terrain_surface.roof_layers = {}
    terrain_surface.roof_cells = set()
    terrain_surface.grid_origin = (0.0, 0.0)
    terrain_surface.grid_cell = cell
    terrain_surface.seed_z = seed_z
    terrain_surface.seed_ground_z = seed_z
    terrain_surface.seed_selection = 'terrain_nearest'
    terrain_surface.include_rooftops = False
    terrain_surface.standing_volume_index = standing_volume_index

    metrics = {
        'enabled': False,
        'terrain_contract': True,
        'cell': cell,
        'sampled_cells': sampled,
        'reachable_cells': len(reachable_layers),
        'reachable_layers': len(reachable_layers),
        'seed_z': seed_z,
        'seed_selection': 'terrain_nearest',
        'standing_rejected_terrain': rejected_standing,
        'standing_volume_index': (standing_volume_index.metrics()
                                  if standing_volume_index is not None else None),
    }
    return terrain_surface, metrics


def generate_seed_disc_campaign_v58(world_segments, entities, terrain_z=18.5, *, center=(0.0,0.0), focus_radius=32.0, brush_b15=16, z_at=None, collision_segments_3d=None, collision_triangles_3d=None, existing_nodes=None, log=None, debug_probe=None, on_progress=None, quantize_to_map_grid=False, radius_cap=80.0, geometry_pre_normalized=False, include_ladders=False):
    """Seed-disc generator.

    Correct model:
    - the clicked seed disc is the required movement coverage domain;
    - empty walkable space inside the disc receives nodes even when no geometry is nearby;
    - geometry blocks/shapes nodes and links, but does not decide whether nodes exist;
    - a campaign-style conditional repair pass softens spacing only inside confirmed holes.

    When quantize_to_map_grid is enabled, the seed remains only the center of
    the requested coverage disc.  The campaign evidence uses a 0.25 m file
    coordinate quantum but a 0.50 m base-node pitch.  Fine cells are admitted
    first, then legal 2x2 groups are recursively promoted through b15
    4->8->16->32.  Unpromoted cells remain as the characteristic rails and
    crosses visible in SPBHD_13; radius is therefore an output of the packing
    hierarchy, never the gate which decides whether a base candidate exists.
    """
    import time as _time
    t0 = _time.perf_counter()
    log = log or (lambda m: None)
    def _progress(percent, phase, detail=''):
        if on_progress is not None:
            try:
                on_progress(float(percent), str(phase), str(detail))
            except Exception:
                pass
            # The generator is CPU-bound Python.  Yield after the deliberately
            # sparse progress callbacks so Tk's main thread can repaint the
            # monitor even while a large grow/repair phase is running.
            _time.sleep(0)

    _progress(2, 'Preparing generator', 'Normalizing seed, terrain, and collision inputs')
    requested_cx, requested_cy = float(center[0]), float(center[1])
    cx, cy = requested_cx, requested_cy
    R = max(8.0, min(float(focus_radius), float(radius_cap)))
    quantize_to_map_grid = bool(quantize_to_map_grid)
    quantized_grid_m = float(GENERATOR_QUANTIZED_GRID_M)
    seed_selection_hint = str(
        getattr(z_at, 'seed_selection', '') or '') if callable(z_at) else ''
    stable_outdoor_seed_anchor = bool(
        not quantize_to_map_grid
        and seed_selection_hint in ('terrain_nearest', 'terrain_fallback'))
    if stable_outdoor_seed_anchor:
        # Stabilize only the coverage anchor. Nodes remain continuous,
        # geometry-shaped walker output; this is not quantized topology.
        cx = math.floor(requested_cx / quantized_grid_m + 0.5) * quantized_grid_m
        cy = math.floor(requested_cy / quantized_grid_m + 0.5) * quantized_grid_m
    quantized_base_candidate_count = 0
    lattice_bridge_count = 0
    lattice_doorway_rejected = {}
    _lattice_solid_not_walkable = 0
    quantized_promotion_metrics = {}
    quantized_partial_promotion_metrics = {}
    quantized_optimizer_metrics = {}
    quantized_axis_link_count = 0
    quantized_inherited_link_count = 0
    quantized_fallback_link_count = 0
    quantized_layer_candidate_count = 0
    quantized_layer_support_reject_count = 0
    quantized_layer_skeleton_link_count = 0
    quantized_layer_local_link_count = 0
    quantized_cardinal_gap_candidate_count = 0
    quantized_cardinal_gap_support_reject_count = 0
    quantized_cardinal_gap_collision_reject_count = 0
    quantized_cardinal_gap_link_count = 0
    quantized_cardinal_gap_details = []
    ordinary_radius_refit_metrics = {}

    def _map_lattice_index(value):
        # The lattice belongs to the map (world origin 0), never to the clicked
        # seed.  Integer indices also avoid accumulating floating-point drift.
        return int(math.floor(float(value) / quantized_grid_m + 0.5))

    def _map_lattice_xy(x, y):
        return (_map_lattice_index(x) * quantized_grid_m,
                _map_lattice_index(y) * quantized_grid_m)
    debug_probe = debug_probe if isinstance(debug_probe, dict) else None
    debug_probe_polygon = debug_probe.get('polygon') if debug_probe else None
    debug_probe_bbox = debug_probe.get('bbox') if debug_probe else None
    debug_probe_records = []
    corridor_fit_debug_records = []
    provenance_debug_records = []
    _trace_provenance = bool(
        debug_probe and debug_probe.get('trace_provenance', False))
    _max_provenance_records = int(
        (debug_probe or {}).get('max_provenance_records', 100000))

    def _debug_probe_contains(x, y):
        if not debug_probe:
            return False
        if debug_probe_bbox is not None:
            try:
                b0, b1, b2, b3 = debug_probe_bbox
                if x < b0 or x > b2 or y < b1 or y > b3:
                    return False
            except Exception:
                return False
        if debug_probe_polygon:
            return _v52_point_in_poly(x, y, debug_probe_polygon)
        return True

    def _trace_provenance_record(kind, x, y, **fields):
        if (not _trace_provenance
                or len(provenance_debug_records) >= _max_provenance_records
                or not _debug_probe_contains(float(x), float(y))):
            return
        record = {
            'kind': str(kind),
            'x': round(float(x), 4),
            'y': round(float(y), 4),
        }
        record.update(fields)
        provenance_debug_records.append(record)

    node_z = float(terrain_z) + 1.2489
    def _surface_z_at(x, y, preferred_z=None):
        if callable(z_at):
            try:
                if preferred_z is not None and hasattr(z_at, 'height_near'):
                    return z_at.height_near(float(x), float(y), float(preferred_z))
                return z_at(float(x), float(y))
            except Exception:
                return None
        return float(terrain_z)

    def _node_z_at(x, y, preferred_surface_z=None):
        surface_z = _surface_z_at(x, y, preferred_surface_z)
        if surface_z is None:
            return None
        return float(surface_z) + 1.2489

    if geometry_pre_normalized:
        # Geometry collected by AINEditor is already stored as float/int tuples.
        # Reuse those immutable input lists across area-zone transactions; each
        # job rebinds its own disc-filtered lists below and never mutates them.
        segs = world_segments or []
        segs3d = collision_segments_3d or []
        triangles3d = collision_triangles_3d or []
    else:
        segs = [(float(x1), float(y1), float(x2), float(y2), int(ei)) for x1,y1,x2,y2,ei in (world_segments or [])]
        segs3d = [
            (float(x1), float(y1), float(z1), float(x2), float(y2), float(z2), int(ei))
            for x1,y1,z1,x2,y2,z2,ei in (collision_segments_3d or [])
        ]
        triangles3d = [
            (float(ax), float(ay), float(az),
             float(bx), float(by), float(bz),
             float(cx3), float(cy3), float(cz3), int(ei))
            for ax,ay,az,bx,by,bz,cx3,cy3,cz3,ei in (collision_triangles_3d or [])
        ]
    height_aware_collision = bool(segs3d) and callable(z_at)

    # ── Disc-scope filter: only keep geometry near the seed disc ──
    _disc_margin = R + 12.0
    _disc_x0 = cx - _disc_margin
    _disc_x1 = cx + _disc_margin
    _disc_y0 = cy - _disc_margin
    _disc_y1 = cy + _disc_margin
    _n_segs_pre = len(segs)
    _n_s3d_pre = len(segs3d)
    _n_tri_pre = len(triangles3d)
    segs = [s for s in segs if not (
        max(s[0], s[2]) < _disc_x0 or min(s[0], s[2]) > _disc_x1 or
        max(s[1], s[3]) < _disc_y0 or min(s[1], s[3]) > _disc_y1)]
    segs3d = [s for s in segs3d if not (
        max(s[0], s[3]) < _disc_x0 or min(s[0], s[3]) > _disc_x1 or
        max(s[1], s[4]) < _disc_y0 or min(s[1], s[4]) > _disc_y1)]
    triangles3d = [t for t in triangles3d if not (
        max(t[0], t[3], t[6]) < _disc_x0 or min(t[0], t[3], t[6]) > _disc_x1 or
        max(t[1], t[4], t[7]) < _disc_y0 or min(t[1], t[4], t[7]) > _disc_y1)]
    log(f'Disc-scope filter: segs {_n_segs_pre}->{len(segs)}, segs3d {_n_s3d_pre}->{len(segs3d)}, tri {_n_tri_pre}->{len(triangles3d)}')

    _progress(7, 'Indexing collision geometry', 'Building the local hard-segment spatial index')
    SEG_CELL = 3.0
    seg_grid = {}
    for _si,(_x1,_y1,_x2,_y2,_ei) in enumerate(segs):
        for _gx in range(int(min(_x1,_x2)//SEG_CELL), int(max(_x1,_x2)//SEG_CELL)+1):
            for _gy in range(int(min(_y1,_y2)//SEG_CELL), int(max(_y1,_y2)//SEG_CELL)+1):
                seg_grid.setdefault((_gx,_gy), []).append(_si)
    def _segs_near(x, y, r):
        out = set()
        for _gx in range(int((x-r)//SEG_CELL), int((x+r)//SEG_CELL)+1):
            for _gy in range(int((y-r)//SEG_CELL), int((y+r)//SEG_CELL)+1):
                _b = seg_grid.get((_gx,_gy))
                if _b: out.update(_b)
        return out

    seg3d_grid = {}
    for _si,(_x1,_y1,_z1,_x2,_y2,_z2,_ei) in enumerate(segs3d):
        for _gx in range(int(min(_x1,_x2)//SEG_CELL), int(max(_x1,_x2)//SEG_CELL)+1):
            for _gy in range(int(min(_y1,_y2)//SEG_CELL), int(max(_y1,_y2)//SEG_CELL)+1):
                seg3d_grid.setdefault((_gx,_gy), []).append(_si)

    # Parallel arrays for fast nearest_hard access (avoids tuple unpack per hit)
    _s3d_x1 = [s[0] for s in segs3d]
    _s3d_y1 = [s[1] for s in segs3d]
    _s3d_z1 = [s[2] for s in segs3d]
    _s3d_x2 = [s[3] for s in segs3d]
    _s3d_y2 = [s[4] for s in segs3d]
    _s3d_z2 = [s[5] for s in segs3d]
    _nh_visit = [0] * len(segs3d)
    _nh_visit_id = [0]

    def _segs3d_near(x, y, r):
        out = set()
        for _gx in range(int((x-r)//SEG_CELL), int((x+r)//SEG_CELL)+1):
            for _gy in range(int((y-r)//SEG_CELL), int((y+r)//SEG_CELL)+1):
                bucket = seg3d_grid.get((_gx,_gy))
                if bucket:
                    out.update(bucket)
        return out

    standing_volume_index = getattr(z_at, 'standing_volume_index', None)
    if (not isinstance(standing_volume_index, _GeneratorStandingVolumeIndex)
            or len(standing_volume_index.triangles) != len(triangles3d)):
        standing_volume_index = (
            _GeneratorStandingVolumeIndex(triangles3d, cell=SEG_CELL)
            if triangles3d else None)

    def _triangles3d_near(x, y, r):
        if standing_volume_index is None:
            return set()
        return standing_volume_index.query_indices(x, y, r)

    def _segment_hits_triangle(px, py, pz, qx, qy, qz, triangle):
        ax,ay,az,bx,by,bz,cx3,cy3,cz3,_ei = triangle
        dx,dy,dz = qx-px, qy-py, qz-pz
        e1x,e1y,e1z = bx-ax, by-ay, bz-az
        e2x,e2y,e2z = cx3-ax, cy3-ay, cz3-az
        hx = dy*e2z - dz*e2y
        hy = dz*e2x - dx*e2z
        hz = dx*e2y - dy*e2x
        det = e1x*hx + e1y*hy + e1z*hz
        if -1e-10 < det < 1e-10:
            return False
        inv_det = 1.0 / det
        sx,sy,sz = px-ax, py-ay, pz-az
        u = (sx*hx + sy*hy + sz*hz) * inv_det
        if u < -1e-7 or u > 1.0 + 1e-7:
            return False
        qvx = sy*e1z - sz*e1y
        qvy = sz*e1x - sx*e1z
        qvz = sx*e1y - sy*e1x
        v = (dx*qvx + dy*qvy + dz*qvz) * inv_det
        if v < -1e-7 or u + v > 1.0 + 1e-7:
            return False
        t = (e2x*qvx + e2y*qvy + e2z*qvz) * inv_det
        return 0.02 < t < 0.98

    def radius_m_local(b15):
        return float(b15) / 16.0

    # Geometry policy:
    # Buildings / large CModels are collision SOURCES, not filled solid blobs.
    # Earlier builds convex-filled every entity hull, which incorrectly blocked
    # open-sky courtyards/corridors inside building models.  Keep hull-fill only
    # for compact genuinely-solid props (barrels/crates/vehicles/etc.); use the
    # actual movement-band segments for buildings, walls and open structures.
    ent_pts = defaultdict(list)
    for x1,y1,x2,y2,ei in segs:
        ent_pts[ei].append((x1,y1)); ent_pts[ei].append((x2,y2))

    def _ent_for_idx(_ei):
        try:
            if 0 <= int(_ei) < len(entities or []):
                return entities[int(_ei)] or {}
        except Exception:
            pass
        return {}

    def _allow_solid_hull_fill(_ei, _h):
        e = _ent_for_idx(_ei)
        name = str(e.get('name') or e.get('graphic') or e.get('model') or e.get('label') or '').lower()
        cat = str(e.get('category') or e.get('render_kind') or '').lower()
        try:
            tid = int(e.get('type_id', -1))
        except Exception:
            tid = -1
        xs = [p[0] for p in _h]; ys = [p[1] for p in _h]
        w = max(xs) - min(xs); hgt = max(ys) - min(ys)
        area = abs(_v52_poly_area(_h))
        seg_count = max(0, len(ent_pts.get(_ei, [])) // 2)

        # Do not choose one extreme for every building.
        # The earlier policy allowed all building interiors so open-sky/corridor models could
        # receive nodes, but that also let nodes appear inside plain closed
        # houses/buildings that have no playable interior.  Keep complex/open
        # structural models segment-only, but fill simple compact closed shells.
        open_structure_words = (
            'corridor','hall','compound','mwall','mwal','wall','fence','gate','door',
            'arch','ruin','rubble','bridge','walk','pier','roof','canopy','open'
        )
        closed_building_words = (
            'house','hut','shack','shed','bld','bldg','building','room','shop','store'
        )
        is_buildingish = (cat == 'building') or any(wd in name for wd in closed_building_words)
        is_open_structure = any(wd in name for wd in open_structure_words)
        if is_buildingish:
            if is_open_structure:
                return False
            # Simple closed buildings/rooms get a conservative filled hull so
            # nodes do not appear inside non-interior models.  Complex/large
            # models remain segment-only so open courtyards/corridors are not
            # wiped out as one solid blob.
            # Segment count is not a reliable interior signal.
            # Small closed buildings can have hundreds/thousands of triangle
            # edges, so the old seg_count<=90 guard let nodes leak inside
            # MogSlm*/MogBld* closed footprints.  Use footprint size instead.
            max_closed_area = float(globals().get('GENERATOR_CLOSED_BUILDING_HULL_MAX_AREA', 160.0))
            max_closed_span = float(globals().get('GENERATOR_CLOSED_BUILDING_HULL_MAX_SPAN', 18.0))
            return (area <= max_closed_area and max(w, hgt) <= max_closed_span)

        # Compact props are genuinely solid enough that segment-distance alone
        # would allow a node in the middle unless we fill the hull.
        solid_words = ('barl','barrel','crate','wcrate','cargo','box','tire','sandbag','sndbag',
                       'vndcrt','hndcrt','chair','table','sofa','bed','dresser',
                       'ncar','ntrk','nbus','car','truck','bus','humv','jeep','vehicle')
        if e.get('_barrel') or any(wd in name for wd in solid_words):
            return area <= 95.0 and max(w, hgt) <= 16.0

        # Generic small closed props may be filled, but large/complex models are
        # not.  This is intentionally conservative to avoid reintroducing filled
        # building-footprint blockers.
        return area <= 8.0 and max(w, hgt) <= 4.5

    hulls = []
    solid_hull_skipped = 0
    for ei,pts in ent_pts.items():
        h = _v52_convex_hull(pts)
        if len(h) >= 3 and _v52_poly_area(h) > 0.85:
            if _allow_solid_hull_fill(ei, h):
                hulls.append(h)
            else:
                solid_hull_skipped += 1

    hull_boxes = []
    for h in hulls:
        _xs=[p[0] for p in h]; _ys=[p[1] for p in h]
        hull_boxes.append((min(_xs),min(_ys),max(_xs),max(_ys)))

    _progress(12, 'Resolving solid geometry', 'Finding enclosed model cells and collision blockers')
    # Real-geometry blocker pass:
    # Hull filling alone is not enough. Large/open city-block models can contain
    # both walkable open corridors AND closed solid cells.  Segment-only mode lets
    # nodes appear inside those closed cells because the center can be far enough
    # from the nearest wall segment.  Build a tiny per-entity 2D flood grid from
    # actual hard segments: cells connected to the outside are open; unvisited
    # cells enclosed by segment walls are treated as solid.  This preserves cracks
    # / open corridors while blocking closed geometry pockets.
    enclosed_mesh_grids = []
    def _build_enclosed_mesh_grid(_ei, _segs_e):
        if not _segs_e:
            return None
        xs=[]; ys=[]
        for _x1,_y1,_x2,_y2 in _segs_e:
            xs.extend((_x1,_x2)); ys.extend((_y1,_y2))
        if not xs or not ys:
            return None
        minx,maxx=min(xs),max(xs); miny,maxy=min(ys),max(ys)
        w=maxx-minx; h=maxy-miny
        if w < 1.0 or h < 1.0:
            return None
        cell=float(globals().get('GENERATOR_ENCLOSED_MESH_CELL', 0.50))
        pad=float(globals().get('GENERATOR_ENCLOSED_MESH_PAD', 1.25))
        ox=minx-pad; oy=miny-pad
        nx=max(4, int(math.ceil((w+2*pad)/cell))+1)
        ny=max(4, int(math.ceil((h+2*pad)/cell))+1)
        # Bound by actual raster cost, not axis-aligned footprint area. Rotated
        # city blocks inflate bbox area even when the cell count is still safe.
        if nx*ny > int(globals().get('GENERATOR_ENCLOSED_MESH_MAX_CELLS', 45000)):
            return None
        wall=set()
        def _mark(ix,iy):
            if 0 <= ix < nx and 0 <= iy < ny:
                wall.add((ix,iy))
        # Rasterize segments with a small thickness so triangle-wire cracks do
        # not leak the flood through actual walls.  Door/corridor openings wider
        # than roughly a cell remain open.
        for _x1,_y1,_x2,_y2 in _segs_e:
            L=math.hypot(_x2-_x1,_y2-_y1)
            steps=max(1, int(math.ceil(L/(cell*0.35))))
            for k in range(steps+1):
                t=k/steps
                x=_x1+(_x2-_x1)*t; y=_y1+(_y2-_y1)*t
                ix=int(math.floor((x-ox)/cell)); iy=int(math.floor((y-oy)/cell))
                for dx in (-1,0,1):
                    for dy in (-1,0,1):
                        _mark(ix+dx,iy+dy)
        # Flood from outside/border cells through non-wall space.
        seen=set(); dq=deque()
        for ix in range(nx):
            for iy in (0, ny-1):
                if (ix,iy) not in wall and (ix,iy) not in seen:
                    seen.add((ix,iy)); dq.append((ix,iy))
        for iy in range(ny):
            for ix in (0, nx-1):
                if (ix,iy) not in wall and (ix,iy) not in seen:
                    seen.add((ix,iy)); dq.append((ix,iy))
        while dq:
            ix,iy=dq.popleft()
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                jx,jy=ix+dx,iy+dy
                if 0 <= jx < nx and 0 <= jy < ny and (jx,jy) not in wall and (jx,jy) not in seen:
                    seen.add((jx,jy)); dq.append((jx,jy))
        enclosed=set()
        for ix in range(1,nx-1):
            for iy in range(1,ny-1):
                if (ix,iy) not in wall and (ix,iy) not in seen:
                    enclosed.add((ix,iy))
        if len(enclosed) < int(globals().get('GENERATOR_ENCLOSED_MESH_MIN_CELLS', 4)):
            return None
        return {'ei':_ei,'ox':ox,'oy':oy,'cell':cell,'nx':nx,'ny':ny,'enclosed':enclosed,
                'bbox':(ox,oy,ox+nx*cell,oy+ny*cell)}

    _ent_segs = defaultdict(list)
    for _x1,_y1,_x2,_y2,_ei in segs:
        _ent_segs[_ei].append((_x1,_y1,_x2,_y2))
    for _ei,_se in _ent_segs.items():
        _g=_build_enclosed_mesh_grid(_ei,_se)
        if _g is not None:
            enclosed_mesh_grids.append(_g)

    # Optional polygon-face blockers from actual closed segment loops.
    # This catches roof/solid rectangles inside large/open city-block models.
    # Open corridors remain open unless there is an actual closed face loop.
    polygon_face_blockers = []
    if bool(globals().get('GENERATOR_USE_POLYGON_FACE_BLOCKERS', True)):
        try:
            from shapely.geometry import LineString as _ShpLineString
            from shapely.ops import unary_union as _shp_unary_union, polygonize as _shp_polygonize
            _lines_by_ent = defaultdict(list)
            for _x1,_y1,_x2,_y2,_ei in segs:
                _e = _ent_for_idx(_ei)
                _cat = str(_e.get('category') or _e.get('render_kind') or '').lower()
                if _cat != 'building':
                    continue
                if abs(_x1-_x2) + abs(_y1-_y2) < 0.05:
                    continue
                _lines_by_ent[_ei].append(_ShpLineString([(_x1,_y1),(_x2,_y2)]))
            _min_area = float(globals().get('GENERATOR_POLYGON_FACE_MIN_AREA', 0.35))
            _max_area = float(globals().get('GENERATOR_POLYGON_FACE_MAX_AREA', 180.0))
            for _ei,_lines in _lines_by_ent.items():
                if len(_lines) < 4:
                    continue
                try:
                    for _p in _shp_polygonize(_shp_unary_union(_lines)):
                        _a = float(_p.area)
                        if _min_area <= _a <= _max_area:
                            polygon_face_blockers.append((_p, _p.bounds))
                except Exception:
                    pass
        except Exception:
            polygon_face_blockers = []

    def inside_polygon_face_blocker(x,y):
        if not polygon_face_blockers:
            return False
        try:
            from shapely.geometry import Point as _ShpPoint
            _pt = _ShpPoint(float(x), float(y))
            for _p,_b in polygon_face_blockers:
                if _b[0] <= x <= _b[2] and _b[1] <= y <= _b[3] and _p.contains(_pt):
                    return True
        except Exception:
            return False
        return False

    def inside_enclosed_mesh(x,y):
        for _g in enclosed_mesh_grids:
            b0,b1,b2,b3=_g['bbox']
            if x<b0 or x>b2 or y<b1 or y>b3:
                continue
            ix=int(math.floor((x-_g['ox'])/_g['cell']))
            iy=int(math.floor((y-_g['oy'])/_g['cell']))
            if (ix,iy) in _g['enclosed']:
                return True
        return False

    def solid_detail(x,y):
        hits = []
        for _hi,h in enumerate(hulls):
            _b0,_b1,_b2,_b3 = hull_boxes[_hi]
            if x<_b0 or x>_b2 or y<_b1 or y>_b3:
                continue
            if _v52_point_in_poly(x,y,h):
                hits.append('filled_hull')
                break
        if inside_enclosed_mesh(x,y):
            hits.append('enclosed_mesh')
        if inside_polygon_face_blocker(x,y):
            hits.append('polygon_face_blocker')
        return hits

    def inside_solid(x,y):
        return bool(solid_detail(x,y))

    _progress(18, 'Sampling hard geometry', 'Preparing clearance queries used by node placement')
    # Height-aware collision uses the 3D spatial grid directly; skip
    # the expensive rasterization into sample points.
    sample_hash = _V52Spatial(2.5)
    samples = []
    if not height_aware_collision:
        for si,(x1,y1,x2,y2,ei) in enumerate(segs):
            L = math.hypot(x2-x1, y2-y1)
            n = max(1, int(math.ceil(L / 0.65)))
            for k in range(n+1):
                t = k / n
                sx = x1 + (x2-x1)*t
                sy = y1 + (y2-y1)*t
                idx = len(samples)
                samples.append((sx,sy,si))
                sample_hash.insert(sx,sy,idx)

    nh_cache = {}
    clearance_factor = float(globals().get(
        'GENERATOR_NODE_RADIUS_CLEARANCE_FACTOR', 1.06))
    configured_bands = list(globals().get(
        'V64_RADIUS_BANDS', [(4.6,48),(3.2,36),(2.0,24)]))
    clearance_search_radius = max(
        [2.7]
        + [float(threshold) for threshold, _b15 in configured_bands]
        + [float(_b15) / 16.0 * clearance_factor
           for _threshold, _b15 in configured_bands]
        + [float(_b15) / 16.0 * clearance_factor
           for _b15 in (16, 12, 8, 4)]
    )

    def nearest_hard(x,y,surface_z=None):
        if height_aware_collision:
            if surface_z is None:
                surface_z = _surface_z_at(x, y)
            if surface_z is None:
                return 0.0
            key = (round(float(x), 4), round(float(y), 4), round(float(surface_z), 4))
            if key in nh_cache:
                return nh_cache[key]
            body_lo = float(surface_z) + 0.20
            body_hi = float(surface_z) + 1.80
            best_sq = 999.0 * 999.0
            _nh_visit_id[0] += 1
            _vid = _nh_visit_id[0]
            _sc = SEG_CELL
            _g = seg3d_grid.get
            # Two-tier search: inner 1.5m radius (~4 cells) catches the
            # common case of nearby walls (corridors, rooms); outer search
            # only runs when needed for open-area classification.
            _ir = 1.5
            _igx0 = int((x - _ir) // _sc); _igx1 = int((x + _ir) // _sc)
            _igy0 = int((y - _ir) // _sc); _igy1 = int((y + _ir) // _sc)
            for _gx in range(_igx0, _igx1 + 1):
                for _gy in range(_igy0, _igy1 + 1):
                    _bk = _g((_gx, _gy))
                    if _bk is None:
                        continue
                    for _si in _bk:
                        _nh_visit[_si] = _vid
                        _sz1 = _s3d_z1[_si]; _sz2 = _s3d_z2[_si]
                        if (_sz1 < body_lo and _sz2 < body_lo) or (_sz1 > body_hi and _sz2 > body_hi):
                            continue
                        _ax = _s3d_x1[_si]; _ay = _s3d_y1[_si]
                        _bx = _s3d_x2[_si]; _by = _s3d_y2[_si]
                        _dx = _bx - _ax; _dy = _by - _ay
                        _l2 = _dx * _dx + _dy * _dy
                        if _l2 <= 1e-12:
                            _d_sq = (x - _ax) ** 2 + (y - _ay) ** 2
                        else:
                            _t = ((x - _ax) * _dx + (y - _ay) * _dy) / _l2
                            if _t < 0.0: _t = 0.0
                            elif _t > 1.0: _t = 1.0
                            _qx = _ax + _t * _dx; _qy = _ay + _t * _dy
                            _d_sq = (x - _qx) ** 2 + (y - _qy) ** 2
                        if _d_sq < best_sq:
                            best_sq = _d_sq
            if best_sq > 1.0:
                _csr = clearance_search_radius
                for _gx in range(int((x - _csr) // _sc), int((x + _csr) // _sc) + 1):
                    for _gy in range(int((y - _csr) // _sc), int((y + _csr) // _sc) + 1):
                        if _igx0 <= _gx <= _igx1 and _igy0 <= _gy <= _igy1:
                            continue
                        _bk = _g((_gx, _gy))
                        if _bk is None:
                            continue
                        for _si in _bk:
                            if _nh_visit[_si] == _vid:
                                continue
                            _nh_visit[_si] = _vid
                            _sz1 = _s3d_z1[_si]; _sz2 = _s3d_z2[_si]
                            if (_sz1 < body_lo and _sz2 < body_lo) or (_sz1 > body_hi and _sz2 > body_hi):
                                continue
                            _ax = _s3d_x1[_si]; _ay = _s3d_y1[_si]
                            _bx = _s3d_x2[_si]; _by = _s3d_y2[_si]
                            _dx = _bx - _ax; _dy = _by - _ay
                            _l2 = _dx * _dx + _dy * _dy
                            if _l2 <= 1e-12:
                                _d_sq = (x - _ax) ** 2 + (y - _ay) ** 2
                            else:
                                _t = ((x - _ax) * _dx + (y - _ay) * _dy) / _l2
                                if _t < 0.0: _t = 0.0
                                elif _t > 1.0: _t = 1.0
                                _qx = _ax + _t * _dx; _qy = _ay + _t * _dy
                                _d_sq = (x - _qx) ** 2 + (y - _qy) ** 2
                            if _d_sq < best_sq:
                                best_sq = _d_sq
            best = best_sq ** 0.5
            nh_cache[key] = best
            return best
        if not samples:
            return 999.0
        key = (round(float(x),2), round(float(y),2))
        if key in nh_cache:
            return nh_cache[key]
        best = 999.0
        for rad in (2.0,4.0,8.0,16.0):
            hit = False
            for sx,sy,idx in sample_hash.query(x,y,rad):
                hit = True
                d = math.hypot(x-sx, y-sy)
                if d < best:
                    best = d
            if hit:
                break
        if best < 2.7:
            for _si in _segs_near(x, y, 3.0):
                x1,y1,x2,y2,ei = segs[_si]
                d = _v52_dist_point_seg(x,y,x1,y1,x2,y2)
                if d < best:
                    best = d
        nh_cache[key] = best
        return best

    # Complete a local core before admitting farther terrain.  A single
    # unbounded depth-first walk makes the topology inside an unchanged 32 m
    # region depend on whether the user requested 32 m or 80 m: the larger job
    # can leave the core, consume the fixed restart budget elsewhere, then
    # reconstruct the core with late hole-repair nodes.  Keep one geometry
    # transaction and one graph, but grow it through bounded concentric
    # frontiers.  Automatic Z still only changes the available support layers;
    # it does not select another generator.
    # Use one stable local core and one outer expansion.  Subdividing the
    # outer area into many narrow rings repeatedly exhausts the walker at an
    # artificial boundary and leaves concentric work for the late coverage
    # repair.  That is precisely the large-seed "patch" pattern this bounded
    # growth is meant to prevent.
    _core_growth_radius = min(float(R), 32.0)
    _growth_phase_radii = [float(_core_growth_radius)]
    if float(R) > _core_growth_radius + 1.0e-9:
        _growth_phase_radii.append(float(R))
    _growth_phase_index = 0
    _active_growth_radius = _growth_phase_radii[0]
    _bounded_growth_active = not quantize_to_map_grid

    def in_disc(x,y):
        _limit = (_active_growth_radius
                  if _bounded_growth_active else float(R))
        return math.hypot(x-cx, y-cy) <= _limit

    def walkable(x,y,clear=0.50,surface_z=None):
        if not in_disc(x,y):
            return False
        if height_aware_collision:
            if surface_z is None:
                surface_z = _surface_z_at(x, y)
            if surface_z is None:
                return False
            return nearest_hard(x,y,surface_z) >= clear
        return (not inside_solid(x,y)) and nearest_hard(x,y) >= clear

    # Hard visual/collision gate:
    # a generated node is valid only if its acceptance radius fits the current
    # hard geometry clearance.  Older lab tests only kept the center outside
    # blockers; that still allowed the visible radius circle to sit inside walls.
    # This factor is intentionally stricter than the old 0.92 campaign wall-hug.
    NODE_RADIUS_CLEARANCE_FACTOR = clearance_factor
    # Ordinary terrain nodes use b15 as the engine's acceptance radius, not as
    # a body-clearance disc.  The final refitter below already encodes the
    # measured campaign contract as distance ~= (b15_a + b15_b) / 24, while a
    # displayed radius is b15 / 16.  Apply that same 2/3 conversion during
    # outdoor admission so positions are not fragmented into small families
    # and then enlarged only after exploration has finished.  Model-supported
    # floors and the dedicated corridor/stair families retain their existing
    # stricter fit gates.
    OUTDOOR_ACCEPTANCE_CLEARANCE_FACTOR = (
        NODE_RADIUS_CLEARANCE_FACTOR * (16.0 / 24.0))
    OUTDOOR_BANDS = list(globals().get(
        'GENERATOR_OUTDOOR_RADIUS_BANDS',
        [(2.20, 48), (1.65, 36), (1.10, 24)]))

    # Graded radius bands calibrated to campaign family mix.
    # Override per map via global V64_RADIUS_BANDS = [(thr48,48),(thr36,36),(thr24,24)].
    BANDS = configured_bands
    SQUEEZE = (16, 12, 8, 4)
    # Extended b12/b16 annotation must be opt-in. The default generator output
    # should be boring/valid: radius-nav b12=0 in open space and center-nav b12=1
    # only in genuinely tight/squeezed placement. Do not globally OR b12 with 4.
    EXTENDED_B12 = bool(globals().get('GENERATOR_EXTENDED_B12', False))
    def classify(x,y,surface_z=None,known_clearance=None,
                 radius_factor=None,bands=None):
        d = (nearest_hard(x,y,surface_z) if known_clearance is None
             else float(known_clearance))
        fit_factor = (NODE_RADIUS_CLEARANCE_FACTOR
                      if radius_factor is None else float(radius_factor))
        radius_bands = BANDS if bands is None else bands
        # Broad bands still use their calibrated open-space thresholds, but also
        # must pass the hard radius-vs-geometry fit gate.
        for thr,b in radius_bands:
            if d >= thr and d >= radius_m_local(b) * fit_factor:
                return b, 0, 'open_field'
        if d >= radius_m_local(16) * fit_factor:
            # 16-radius nodes are not automatically center-nav. Only very tight
            # clearance gets b12=1; otherwise keep the safe radius-nav default.
            return 16, (1 if d < 1.35 else 0), 'standard'
        for b in SQUEEZE[1:]:
            if d >= radius_m_local(b) * fit_factor:
                return b, 1, 'wall_squeezed'
        return 0, 0, 'no_fit'

    def classify_lattice(x, y, surface_z=None, known_clearance=None):
        """Largest fitting rung used by the world-anchored tangent packer."""
        d = (nearest_hard(x, y, surface_z) if known_clearance is None
             else float(known_clearance))
        for b in (32, 16, 8, 4):
            required = max(float(GENERATOR_QUANTIZED_CENTER_CLEARANCE),
                           radius_m_local(b) * NODE_RADIUS_CLEARANCE_FACTOR)
            if d >= required:
                return b, (1 if b <= 8 else 0), 'map_lattice'
        return 0, 0, 'no_fit'

    def _corridor_wall_hit(x, y, dx, dy, axis_x, axis_y, surface_z,
                           max_distance=3.0):
        """Return the first body-height wall hit along a 2-D ray.

        Clearance alone cannot distinguish a corridor from several unrelated
        props which merely happen to form a local ridge.  This query requires
        an actual collision segment crossing the ray and requires that segment
        to run approximately along the proposed corridor axis.
        """
        ray_len = float(max_distance)
        end_x = x + dx * ray_len
        end_y = y + dy * ray_len
        mid_x = (x + end_x) * 0.5
        mid_y = (y + end_y) * 0.5
        body_lo = float(surface_z) + 0.20
        body_hi = float(surface_z) + 1.80
        best = None
        for _si in _segs3d_near(mid_x, mid_y, ray_len * 0.55 + 0.25):
            x1,y1,z1,x2,y2,z2,ei = segs3d[_si]
            if max(z1,z2) < body_lo or min(z1,z2) > body_hi:
                continue
            sx = x2 - x1
            sy = y2 - y1
            seg_len = math.hypot(sx, sy)
            if seg_len < 1e-6:
                continue
            # A corridor side must run with the lane.  Reject cross pieces and
            # triangle fragments which do not describe a persistent side wall.
            alignment = abs((sx * axis_x + sy * axis_y) / seg_len)
            if alignment < 0.58:
                continue
            denom = dx * sy - dy * sx
            if abs(denom) < 1e-9:
                continue
            ox = x1 - x
            oy = y1 - y
            t = (ox * sy - oy * sx) / denom
            u = (ox * dy - oy * dx) / denom
            if t <= 0.03 or t > ray_len or u < -1e-6 or u > 1.000001:
                continue
            # Collision wall triangles are often represented by diagonal edges.
            # Their projected crossing may lie at the top/bottom endpoint even
            # though the edge spans the complete body band, so the span test
            # above is authoritative (the same rule used by nearest_hard()).
            if best is None or t < best[0]:
                best = (t, alignment, int(ei))
        return best

    def _corridor_has_parallel_sides(x, y, axis, surface_z,
                                     station_offsets=(0.0, -1.50, 1.50),
                                     required_stations=2,
                                     width_tolerance=1.10,
                                     min_width=1.20,
                                     max_width=5.25,
                                     wall_distance=3.0):
        """Confirm a lane from real parallel collision on both sides.

        Two of three longitudinal stations must see an opposing wall pair.
        This allows a doorway or corridor end at one station while rejecting
        the isolated clearance peaks created by ordinary outdoor clutter.
        """
        axis_x = math.cos(axis)
        axis_y = math.sin(axis)
        side_x = -axis_y
        side_y = axis_x
        stations = []
        station_offsets = tuple(station_offsets)
        for station_i, along in enumerate(station_offsets):
            px = x + axis_x * along
            py = y + axis_y * along
            left = _corridor_wall_hit(
                px, py, side_x, side_y, axis_x, axis_y, surface_z,
                max_distance=wall_distance)
            right = _corridor_wall_hit(
                px, py, -side_x, -side_y, axis_x, axis_y, surface_z,
                max_distance=wall_distance)
            if left is not None and right is not None:
                width = left[0] + right[0]
                if float(min_width) <= width <= float(max_width):
                    stations.append(width)
            remaining = len(station_offsets) - station_i - 1
            if len(stations) + remaining < int(required_stations):
                return False
        if len(stations) < int(required_stations):
            return False
        return max(stations) - min(stations) <= float(width_tolerance)

    def _corridor_axis_at(x, y, surface_z):
        """Return (axis_radians, clearance) for a persistent clearance ridge.

        A corridor center is locally flat along one axis and falls toward hard
        geometry on both perpendicular sides.  A single wall fails because
        clearance rises on its open side; a room/open field fails because it
        has no strongly preferred ridge axis.
        """
        d0 = nearest_hard(x, y, surface_z)
        if d0 < 0.75 or d0 > 2.35:
            return None
        probe = 0.75
        ring = []
        for direction_i in range(16):
            direction = math.radians(22.5 * direction_i)
            ring.append(nearest_hard(
                x + math.cos(direction)*probe,
                y + math.sin(direction)*probe,
                surface_z))
        best = None
        for axis_i in range(8):
            axis = math.radians(22.5 * axis_i)
            long_a = ring[axis_i]
            long_b = ring[(axis_i + 8) % 16]
            side_a = ring[(axis_i + 4) % 16]
            side_b = ring[(axis_i + 12) % 16]
            if min(long_a, long_b) < d0 - 0.14:
                continue
            if side_a > d0 - 0.22 or side_b > d0 - 0.22:
                continue
            ridge_score = min(long_a, long_b) - max(side_a, side_b)
            if ridge_score < 0.24:
                continue
            if best is None or ridge_score > best[0]:
                best = (ridge_score, axis)
        if best is None:
            return None

        axis = best[1]
        ca = math.cos(axis); sa = math.sin(axis)
        # A displacement-authoritative corridor must be a persistent lane, not
        # a short clearance ridge beside a crate, vehicle, or building corner.
        for far_probe in (1.50, 3.00):
            far_a = nearest_hard(
                x + ca*far_probe, y + sa*far_probe, surface_z)
            far_b = nearest_hard(
                x - ca*far_probe, y - sa*far_probe, surface_z)
            if min(far_a, far_b) < d0 - 0.22:
                return None
        if height_aware_collision and not _corridor_has_parallel_sides(
                x, y, axis, surface_z):
            return None
        return axis, d0

    corridor_last_result = None

    def _corridor_fit_candidate_uncached(x, y, surface_z,
                                         start_clearance=None,
                                         continuation_axis=None):
        """Trial the proven slide, but commit it only on a real corridor ridge."""
        nonlocal corridor_last_result
        corridor_last_result = None
        _debug_corridor = _debug_probe_contains(x, y)
        def _corridor_fail(reason, **extra):
            nonlocal corridor_last_result
            corridor_last_result = {
                'accepted': False,
                'reason': str(reason),
                **extra,
            }
            if (_debug_corridor and len(corridor_fit_debug_records) <
                    int((debug_probe or {}).get('max_corridor_records', 4000))):
                corridor_fit_debug_records.append({
                    'x': round(float(x), 4), 'y': round(float(y), 4),
                    'reason': str(reason), **extra})
            return None
        if not globals().get('GENERATOR_CORRIDOR_FIT', True):
            return _corridor_fail('disabled')
        if surface_z is None:
            return _corridor_fail('no_surface_z')
        centre_floor = float(globals().get('GENERATOR_CORRIDOR_CENTER_FLOOR', 0.42))
        if start_clearance is None:
            start_clearance = nearest_hard(x, y, surface_z)
        else:
            start_clearance = float(start_clearance)
        if start_clearance < centre_floor or start_clearance > 2.35:
            return _corridor_fail(
                'start_clearance', clearance=round(float(start_clearance), 4))

        # Prove the long opposing-wall structure *before* the expensive rescue
        # slide and 16-direction ridge sampling.  Ordinary props can imitate a
        # local clearance ridge, but they cannot supply the same wall pair ten
        # metres apart.  This is a direct geometry test, not a cached guess.
        if continuation_axis is None:
            structural_axis = None
            for candidate_axis in (0.0, math.pi*0.25,
                                   math.pi*0.5, math.pi*0.75):
                if _corridor_has_parallel_sides(
                        x, y, candidate_axis, surface_z,
                        station_offsets=(0.0, -5.0, 5.0),
                        required_stations=3, width_tolerance=1.35):
                    structural_axis = candidate_axis
                    break
            if structural_axis is None:
                return _corridor_fail(
                    'long_wall_prefilter',
                    clearance=round(float(start_clearance), 4))

        origin_x = float(x); origin_y = float(y)
        px = origin_x; py = origin_y
        clearance = start_clearance
        slide_budget = float(globals().get('GENERATOR_CORRIDOR_SLIDE_BUDGET', 0.60))
        slid = 0.0
        guard = 0
        while slid < slide_budget and guard < 12:
            guard += 1
            step = min(0.125, slide_budget - slid)
            probe = None
            # Once the cheap opposing-wall gate has established corridor context,
            # retain the proven all-direction rescue search.  Restricting the
            # slide to the approximate prefilter axis leaves parallel side rows
            # in slightly skewed corridors.
            for angle in (0.0, 0.785, 1.571, 2.356,
                          3.142, 3.927, 4.712, 5.498):
                nx = px + math.cos(angle) * step
                ny = py + math.sin(angle) * step
                nd = nearest_hard(nx, ny, surface_z)
                if probe is None or nd > probe[0]:
                    probe = (nd, nx, ny)
            if probe is None or probe[0] <= clearance + 1e-6:
                break
            clearance, px, py = probe
            slid += step

        ridge = _corridor_axis_at(px, py, surface_z)
        if ridge is None:
            return _corridor_fail(
                'first_ridge_test', clearance=round(float(clearance), 4),
                slid=round(float(slid), 4), px=round(float(px), 4), py=round(float(py), 4))
        axis, _trial_clearance = ridge
        # The unconstrained rescue probe can follow tiny clearance noise along
        # the corridor.  Keep only its perpendicular component: centering may
        # change the lane offset, never the candidate's longitudinal station.
        side_axis = axis + math.pi * 0.5
        side_x = math.cos(side_axis); side_y = math.sin(side_axis)
        side_shift = (px - origin_x) * side_x + (py - origin_y) * side_y
        px = origin_x + side_shift * side_x
        py = origin_y + side_shift * side_y
        ridge = _corridor_axis_at(px, py, surface_z)
        if ridge is None:
            return _corridor_fail(
                'projected_ridge_test', slid=round(float(slid), 4),
                px=round(float(px), 4), py=round(float(py), 4))
        axis, fitted_clearance = ridge
        if continuation_axis is None:
            # Enter corridor mode only after proving a genuinely long lane.
            # Once entered, children may continue on the locally confirmed
            # lane all the way to a doorway/end instead of reverting early.
            if height_aware_collision and not _corridor_has_parallel_sides(
                    px, py, axis, surface_z,
                    station_offsets=(0.0, -5.0, 5.0),
                    required_stations=3, width_tolerance=1.25):
                return _corridor_fail('long_lane_entry_test')
        else:
            hinted_axis = math.radians(float(continuation_axis))
            axis_delta = abs(((axis - hinted_axis + math.pi * 0.5) %
                              math.pi) - math.pi * 0.5)
            if axis_delta > math.radians(28.0):
                return _corridor_fail(
                    'continuation_axis_mismatch',
                    delta=round(math.degrees(axis_delta), 3))
        final_factor = float(globals().get('GENERATOR_CORRIDOR_FINAL_FACTOR', 0.85))
        b15 = int(fitted_clearance / (final_factor * 0.0625))
        b15 = min(48, b15)
        if b15 < 20:
            return _corridor_fail(
                'center_radius_below_20', b15=int(b15),
                clearance=round(float(fitted_clearance), 4))
        if (_debug_corridor and len(corridor_fit_debug_records) <
                int((debug_probe or {}).get('max_corridor_records', 4000))):
            corridor_fit_debug_records.append({
                'x': round(float(x), 4), 'y': round(float(y), 4),
                'reason': 'success', 'px': round(float(px), 4),
                'py': round(float(py), 4), 'b15': int(b15),
                'clearance': round(float(fitted_clearance), 4)})
        corridor_last_result = {
            'accepted': True,
            'reason': 'success',
            'px': round(float(px), 4),
            'py': round(float(py), 4),
            'b15': int(b15),
            'axis_degrees': round(
                math.degrees(float(axis)) % 180.0, 4),
            'clearance': round(float(fitted_clearance), 4),
        }
        return px, py, b15, axis, fitted_clearance

    # Candidate positions are effectively unique during a grow, so caching the
    # whole classifier only adds dictionary overhead.  Reuse the exact repeated
    # geometry queries inside it instead.
    corridor_fit_candidate = _corridor_fit_candidate_uncached

    # Generated small-radius fallbacks are provisional near a confirmed corridor
    # lane.  A later centered corridor node may supersede them without changing
    # indices during the depth-first walk; they are physically removed before
    # graph construction.
    corridor_dominated = set()
    # Tight bounds for coverage repair queries.  The previous implementation
    # searched the theoretical unsigned-byte maximum radius (15.9375 m) for
    # every sample even though generated families normally top out at 3 m.
    # Tracking the radii actually present preserves the exact coverage test
    # while avoiding candidates which cannot possibly cover the sample.
    coverage_radius_state = {'prior': 0.0, 'generated': 0.0}

    def coverage(nodes_raw, x, y, margin=0.50, node_z=None):
        """Return coverage by the best same-layer radius residual.

        Centre distance alone is not a coverage ordering when radii differ: a
        nearby small node can miss a sample that a slightly farther large node
        covers.  Compare ``distance - radius`` for every plausible same-layer
        disc instead.  The Z gate prevents a floor above or below from hiding a
        real hole on the sampled support surface.
        """
        best_d = 999.0; best_r = 0.0; best_i = -1
        # Existing nodes count as already-covered area for additive batches.
        # They are tagged with negative indices so callers can distinguish them
        # if needed, but the coverage boolean is what matters here.
        # A node farther away than its radius plus the requested margin cannot
        # cover this sample.  Use the largest radius actually present in each
        # indexed set instead of the theoretical byte maximum.
        prior_query_radius = coverage_radius_state['prior'] + float(margin)
        generated_query_radius = (
            coverage_radius_state['generated'] + float(margin))
        if node_z is None:
            node_z = _node_z_at(x, y)
        best_key = None
        for px, py, i in prior_grid.query(x, y, prior_query_radius):
            n = prior_nodes[i]
            if (node_z is not None
                    and abs(float(n['z']) - float(node_z)) > 0.75):
                continue
            d = math.hypot(px-x, py-y)
            r = radius_m_local(n['b15'])
            # Lowest residual owns coverage.  Preserve deterministic prior-node
            # and index ordering for exact ties.
            candidate_key = (d - r, 0, int(i))
            if best_key is None or candidate_key < best_key:
                best_key = candidate_key
                best_d = d
                best_r = r
                best_i = -1 - i
        if nodes_raw is nodes:
            generated_candidates = node_grid.query(
                x, y, generated_query_radius)
        else:
            # Kept for callers supplying an alternate list; the generator's
            # repair path always passes the indexed `nodes` list.
            generated_candidates = (
                (n['x'], n['y'], i) for i, n in enumerate(nodes_raw)
            )
        for px, py, i in generated_candidates:
            if i in corridor_dominated:
                continue
            n = nodes_raw[i]
            if (node_z is not None
                    and abs(float(n['z']) - float(node_z)) > 0.75):
                continue
            d = math.hypot(px-x, py-y)
            r = radius_m_local(n['b15'])
            candidate_key = (d - r, 1, int(i))
            if best_key is None or candidate_key < best_key:
                best_key = candidate_key
                best_d = d
                best_r = r
                best_i = i
        return best_d <= best_r + margin, best_d, best_r, best_i

    nodes = []
    rm_brush_g=[brush_b15/16.0]
    node_grid = _V52Spatial(5.5)

    # Additive-batch guard:
    # Existing editor nodes are treated as already-occupied coverage/spacing.
    # The generator still returns only the new batch, but it must not place
    # duplicate nodes on top of previous batches when the user clicks nearby.
    prior_nodes = []
    prior_grid = _V52Spatial(6.5)
    for _pn in (existing_nodes or []):
        try:
            _x = float(_pn.get('x') if isinstance(_pn, dict) else getattr(_pn, 'x'))
            _y = float(_pn.get('y') if isinstance(_pn, dict) else getattr(_pn, 'y'))
            _z = float(_pn.get('z') if isinstance(_pn, dict) else getattr(_pn, 'z'))
            _b15 = int(_pn.get('b15', 36) if isinstance(_pn, dict) else getattr(_pn, 'b15', 36))
            _source_id = int(
                _pn.get('id', len(prior_nodes)) if isinstance(_pn, dict)
                else getattr(_pn, 'id', len(prior_nodes)))
            _source_neighbors = tuple(int(v) for v in (
                _pn.get('neighbors', ()) if isinstance(_pn, dict)
                else getattr(_pn, 'neighbors', ())) or ())
            _qmode = bool(
                _pn.get('quantized_map_grid', False) if isinstance(_pn, dict)
                else getattr(_pn, 'quantized_map_grid', False))
        except Exception:
            continue
        try:
            _qkeys_raw = (_pn.get('quantized_source_keys', ()) if isinstance(_pn, dict)
                          else getattr(_pn, 'quantized_source_keys', ()))
            if isinstance(_qkeys_raw, str):
                _qkeys_raw = ()
            _qkeys = frozenset(
                (int(key[0]), int(key[1])) for key in (_qkeys_raw or ()))
        except Exception:
            _qkeys = frozenset()
        # Only nearby existing nodes can affect this seed-disc batch. Keep a
        # generous margin so edge coverage and spacing are respected.
        if math.hypot(_x-cx, _y-cy) <= R + max(8.0, _b15/16.0 + 4.0):
            _idx = len(prior_nodes)
            prior_nodes.append({
                'x': _x, 'y': _y, 'z': _z, 'b15': _b15,
                'source_id': _source_id,
                'source_neighbors': _source_neighbors,
                'quantized_source_keys': _qkeys,
                'quantized_map_grid': _qmode,
            })
            coverage_radius_state['prior'] = max(
                coverage_radius_state['prior'], radius_m_local(_b15))
            prior_grid.insert(_x, _y, _idx)

    prior_edges = []
    prior_edge_grid = _V52Spatial(8.0)
    _prior_by_source_id = {
        int(node['source_id']): index
        for index, node in enumerate(prior_nodes)
    }
    for _prior_i, _prior_node in enumerate(prior_nodes):
        for _neighbor_source_id in _prior_node.get(
                'source_neighbors', ()):
            _prior_j = _prior_by_source_id.get(int(_neighbor_source_id))
            if _prior_j is None or _prior_j <= _prior_i:
                continue
            _other = prior_nodes[_prior_j]
            if abs(float(_prior_node['z']) - float(_other['z'])) > 1.80:
                continue
            _edge_i = len(prior_edges)
            prior_edges.append((_prior_i, _prior_j))
            prior_edge_grid.insert(
                (float(_prior_node['x']) + float(_other['x'])) * 0.5,
                (float(_prior_node['y']) + float(_other['y'])) * 0.5,
                _edge_i)

    reject = Counter()
    rng_seed = ((int(round(cx*100))*73856093) ^ (int(round(cy*100))*19349663) ^ 0xD15C0DE) & 0xffffffff
    rng = random.Random(rng_seed)

    def _prior_edge_covering_point(x, y, node_world_z):
        """Return an already-linked prior span whose two discs cover the point."""
        if not prior_edges or node_world_z is None:
            return None
        seen = set()
        for _mx, _my, _edge_i in prior_edge_grid.query(x, y, 12.0):
            if _edge_i in seen:
                continue
            seen.add(_edge_i)
            _ai, _bi = prior_edges[_edge_i]
            _a = prior_nodes[_ai]
            _b = prior_nodes[_bi]
            if (abs(float(_a['z']) - float(node_world_z)) > 1.80
                    or abs(float(_b['z']) - float(node_world_z)) > 1.80):
                continue
            _ax = float(_a['x']); _ay = float(_a['y'])
            _bx = float(_b['x']); _by = float(_b['y'])
            _vx = _bx - _ax; _vy = _by - _ay
            _length_sq = _vx * _vx + _vy * _vy
            if _length_sq < 1e-9:
                continue
            _length = math.sqrt(_length_sq)
            _ra = radius_m_local(_a['b15'])
            _rb = radius_m_local(_b['b15'])
            if _length > _ra + _rb + 0.25:
                continue
            _t = ((float(x) - _ax) * _vx
                  + (float(y) - _ay) * _vy) / _length_sq
            if _t <= 0.02 or _t >= 0.98:
                continue
            _da = math.hypot(float(x) - _ax, float(y) - _ay)
            _db = math.hypot(float(x) - _bx, float(y) - _by)
            if _da > _ra + 0.05 or _db > _rb + 0.05:
                continue
            _px = _ax + _vx * _t
            _py = _ay + _vy * _t
            return {
                'edge_index': int(_edge_i),
                'a_source_id': int(_a['source_id']),
                'b_source_id': int(_b['source_id']),
                'a_b15': int(_a['b15']),
                'b_b15': int(_b['b15']),
                'distance_a': round(float(_da), 4),
                'distance_b': round(float(_db), 4),
                'perpendicular': round(
                    math.hypot(float(x) - _px, float(y) - _py), 4),
                't': round(float(_t), 4),
            }
        return None

    def _generated_parent_edge_covering_point(x, y, node_world_z):
        """Return an existing grower parent span whose two discs cover the point."""
        if node_world_z is None:
            return None
        seen = set()
        for _nx, _ny, _child_i in node_grid.query(x, y, 12.0):
            if _child_i in seen or _child_i >= len(nodes):
                continue
            seen.add(_child_i)
            _child = nodes[_child_i]
            _parent_i = _child.get('parent_index')
            if (_parent_i is None or int(_parent_i) < 0
                    or int(_parent_i) >= len(nodes)):
                continue
            _parent_i = int(_parent_i)
            _parent = nodes[_parent_i]
            if (_child_i in corridor_dominated
                    or _parent_i in corridor_dominated):
                continue
            if (abs(float(_child['z']) - float(node_world_z)) > 1.80
                    or abs(float(_parent['z']) - float(node_world_z)) > 1.80):
                continue
            _ax = float(_parent['x']); _ay = float(_parent['y'])
            _bx = float(_child['x']); _by = float(_child['y'])
            _vx = _bx - _ax; _vy = _by - _ay
            _length_sq = _vx * _vx + _vy * _vy
            if _length_sq < 1e-9:
                continue
            _length = math.sqrt(_length_sq)
            _ra = radius_m_local(_parent['b15'])
            _rb = radius_m_local(_child['b15'])
            if _length > _ra + _rb + 0.25:
                continue
            _t = ((float(x) - _ax) * _vx
                  + (float(y) - _ay) * _vy) / _length_sq
            if _t <= 0.02 or _t >= 0.98:
                continue
            _da = math.hypot(float(x) - _ax, float(y) - _ay)
            _db = math.hypot(float(x) - _bx, float(y) - _by)
            if _da > _ra + 0.05 or _db > _rb + 0.05:
                continue
            return {
                'parent_index': int(_parent_i),
                'child_index': int(_child_i),
                'distance_parent': round(float(_da), 4),
                'distance_child': round(float(_db), 4),
                't': round(float(_t), 4),
            }
        return None

    _transition_floor_route_cache = {}

    def _transition_floor_wall_blocked(ax, ay, az, bx, by, bz):
        """Fast body-height wall test for one flat transition-floor route."""
        if not segs3d:
            return False
        dx = float(bx) - float(ax)
        dy = float(by) - float(ay)
        if dx * dx + dy * dy <= 1e-10:
            return False
        epsilon = 1e-6
        nearby = set()
        gx0 = int(math.floor((min(float(ax), float(bx)) - epsilon) / SEG_CELL))
        gx1 = int(math.floor((max(float(ax), float(bx)) + epsilon) / SEG_CELL))
        gy0 = int(math.floor((min(float(ay), float(by)) - epsilon) / SEG_CELL))
        gy1 = int(math.floor((max(float(ay), float(by)) + epsilon) / SEG_CELL))
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                bucket = seg3d_grid.get((gx, gy))
                if bucket:
                    nearby.update(bucket)
        for segment_index in nearby:
            x1 = _s3d_x1[segment_index]
            y1 = _s3d_y1[segment_index]
            z1 = _s3d_z1[segment_index]
            x2 = _s3d_x2[segment_index]
            y2 = _s3d_y2[segment_index]
            z2 = _s3d_z2[segment_index]
            ex = x2 - x1
            ey = y2 - y1
            denominator = dx * ey - dy * ex
            if abs(denominator) < 1e-10:
                continue
            t = ((x1 - float(ax)) * ey - (y1 - float(ay)) * ex) / denominator
            if t <= 0.02 or t >= 0.98:
                continue
            u = ((x1 - float(ax)) * dy - (y1 - float(ay)) * dx) / denominator
            if u < 0.0 or u > 1.0:
                continue
            support_z = float(az) + (float(bz) - float(az)) * t
            segment_z = z1 + (z2 - z1) * u
            if support_z + 0.20 <= segment_z <= support_z + 1.80:
                return True
        return False

    def _transition_floor_direct_route_ok(x, y, surface_z,
                                          other_x, other_y, other_world_z):
        """Return False when a nearby overlapping node is across a hard wall.

        This deliberately stays cheap: transition-network membership already
        proves the candidate belongs to reachable model support. The missing
        landing bug is the false *across-wall* coverage/spacing decision, so
        only the body-height blocker test belongs in this hot path.
        """
        if surface_z is None or other_world_z is None:
            return False
        other_surface_z = float(other_world_z) - 1.2489
        if abs(float(surface_z) - other_surface_z) > 1.0:
            return False

        first_key = (
            int(round(float(x) * 4.0)),
            int(round(float(y) * 4.0)),
            int(round(float(surface_z) * 20.0)))
        second_key = (
            int(round(float(other_x) * 4.0)),
            int(round(float(other_y) * 4.0)),
            int(round(other_surface_z * 20.0)))
        cache_key = (
            (first_key, second_key)
            if first_key <= second_key else
            (second_key, first_key))
        cached = _transition_floor_route_cache.get(cache_key)
        if cached is not None:
            return cached
        result = not _transition_floor_wall_blocked(
            x, y, surface_z, other_x, other_y, other_surface_z)
        _transition_floor_route_cache[cache_key] = bool(result)
        return bool(result)

    def spacing_ok(x,y,b15,mode='normal',surface_z=None):
        rm = radius_m_local(b15)
        node_world_z = None if surface_z is None else float(surface_z) + 1.2489
        _covered_prior_edge = (
            None if mode == 'transition_floor'
            else _prior_edge_covering_point(x, y, node_world_z))
        if _covered_prior_edge is not None:
            return False, {
                'close_count': 0,
                'deep_overlap_count': 0,
                'closest': None,
                'prior_edge_covered': _covered_prior_edge,
            }
        if mode == 'lattice':
            # Tangent-disc packing: mixed or equal families may touch, never
            # overlap.  Every radius rung is an exact multiple of the 0.25 m
            # world lattice, so equal-family distances land at exactly 2r.
            factor = 1.0; floor = quantized_grid_m
        elif mode == 'platform':
            # Elevated model surfaces are sampled from a dense 0.25 m support
            # raster.  A slightly firmer shared-spacing contract prevents the
            # raster scan from producing the visibly overlapping roof/platform
            # carpets that the ordinary terrain walker tolerates.
            factor = 0.84; floor = 1.00
        elif mode == 'corridor':
            factor = 0.68; floor = 0.85
        elif mode == 'stair':
            # Transition-corridor nodes may need to follow abrupt treads at
            # sub-metre pitch.  Authored interior
            # chains deliberately overlap their acceptance discs so a climb
            # remains covered between centres; do not apply floor-node packing
            # to this support-verified family.
            factor = 0.35; floor = 0.50
        elif mode == 'transition_floor':
            # Critical flat landing/corridor fabric belonging to a verified
            # stair network. Use ordinary floor spacing, but a nearby node only
            # consumes the spacing slot when it is actually reachable on the
            # same support side. This prevents across-wall nodes from deleting
            # doorway/landing candidates.
            factor = 0.78; floor = 0.95
        elif mode == 'coverage':
            # Closing a proven radius hole.  Ordinary spacing is a texture
            # rule and is stricter than the authored data: over 88,262
            # campaign nodes the linked-pair disc gap is median 0.000 m with
            # p25 -0.836 m and p5 -1.760 m, so authored graphs overlap far
            # more deeply than 'normal' allows.  A node that demonstrably
            # covers uncovered navigable surface is admitted at campaign-
            # legal separation; it still has to pass clearance and the final
            # geometry gate.
            # A late fill is not allowed to create a pair that the finished
            # graph's campaign-envelope contraction would reject.  The former
            # 0.34 admission floor admitted large repair discs around existing
            # small walkers at d/(r1+r2)=0.409-0.442, while contraction refused
            # to link the same pairs below 0.45.  A 0.50 placement contract
            # leaves room for the final radius refit without creating unlinked
            # swallowed-node pairs.
            factor = float(globals().get(
                'GENERATOR_COVERAGE_SPACING_FACTOR', 0.50))
            floor = float(globals().get(
                'GENERATOR_COVERAGE_SPACING_FLOOR', 0.45))
        elif mode == 'outdoor':
            # Ordinary terrain in the authored SPBHD_08/16/17 graphs forms a
            # coherent overlapping mesh.  Keep this contract separate from
            # interiors, corridors and stairs: the stable outdoor walker uses
            # a shorter step and must be allowed to accept that formation.
            factor = float(globals().get(
                'GENERATOR_OUTDOOR_SPACING_FACTOR', 0.70))
            floor = float(globals().get(
                'GENERATOR_OUTDOOR_SPACING_FLOOR', 0.95))
        elif mode == 'normal':
            factor = 0.78; floor = 0.95
        else:
            factor = 0.56; floor = 0.80
        close_count = 0
        deep_overlap_count = 0
        closest = None
        for px,py,j in prior_grid.query(x,y,6.5):
            q = prior_nodes[j]
            if node_world_z is not None and abs(float(q['z']) - node_world_z) > 1.80:
                continue
            qrm = radius_m_local(q['b15'])
            req = factor * (rm + qrm)
            _small_radius_relaxation = (
                False
                if (mode == 'coverage' and globals().get(
                    'GENERATOR_COVERAGE_MIXED_RADIUS_CONTRACT', True))
                else (b15 < 36 or q['b15'] < 36))
            if mode != 'lattice' and _small_radius_relaxation:
                req *= 0.88
            req = max(floor, req)
            d = math.hypot(x-px, y-py)
            if closest is None or d < closest[0]:
                closest = (d, req, -1 - j, q['b15'])
            _route_blocks_transition_floor = True
            if mode == 'transition_floor' and d < req:
                _route_blocks_transition_floor = _transition_floor_direct_route_ok(
                    x, y, surface_z, px, py, q.get('z'))
            if d < req and _route_blocks_transition_floor:
                close_count += 1
            if (d < 0.48 * (rm + qrm)
                    and _route_blocks_transition_floor):
                deep_overlap_count += 1
        for px,py,j in node_grid.query(x,y,5.5):
            if j in corridor_dominated:
                continue
            q = nodes[j]
            if node_world_z is not None and abs(float(q['z']) - node_world_z) > 1.80:
                continue
            if (globals().get('GENERATOR_CORRIDOR_DOMINANCE', True)
                    and mode == 'corridor' and int(q.get('b15', 0)) <= 16
                    and q.get('family') != 'corridor_fit'):
                # A confirmed centered lane is allowed to replace provisional
                # small generated nodes. Existing/prior AIN nodes above are
                # deliberately never ignored.
                continue
            qrm = radius_m_local(q['b15'])
            req = factor * (rm + qrm)
            _small_radius_relaxation = (
                False
                if (mode == 'coverage' and globals().get(
                    'GENERATOR_COVERAGE_MIXED_RADIUS_CONTRACT', True))
                else (b15 < 36 or q['b15'] < 36))
            if mode != 'lattice' and _small_radius_relaxation:
                req *= 0.88
            req = max(floor, req)
            d = math.hypot(x-px, y-py)
            if closest is None or d < closest[0]:
                closest = (d, req, j, q['b15'])
            _route_blocks_transition_floor = True
            if mode == 'transition_floor' and d < req:
                _route_blocks_transition_floor = _transition_floor_direct_route_ok(
                    x, y, surface_z, px, py, q.get('z'))
            if d < req and _route_blocks_transition_floor:
                close_count += 1
            if (d < 0.48 * (rm + qrm)
                    and _route_blocks_transition_floor):
                deep_overlap_count += 1
        return close_count == 0, {'close_count':close_count, 'deep_overlap_count':deep_overlap_count, 'closest':closest}

    def can_add_normal(x,y,b15,surface_z=None,mode='normal',min_clearance=0.62,
                       radius_factor=None,spacing_mode=None):
        rm = radius_m_local(b15)
        fit_factor = (NODE_RADIUS_CLEARANCE_FACTOR if radius_factor is None
                      else float(radius_factor))
        if not walkable(
                x,y, max(float(min_clearance), rm * fit_factor),
                surface_z=surface_z):
            reject['clearance_or_domain'] += 1
            return False
        ok,info = spacing_ok(
            x,y,b15,(spacing_mode if spacing_mode is not None else mode),
            surface_z=surface_z)
        if not ok:
            reject['spacing'] += 1
            return False
        return True

    def add_node_raw(x,y,b15,b12,tag,family,surface_z=None,
                     quantize_xy=True):
        quantized_node = bool(quantize_to_map_grid and quantize_xy)
        if quantized_node:
            x, y = _map_lattice_xy(x, y)
            surface_z = _surface_z_at(x, y, surface_z)
            if surface_z is None:
                reject['unreachable_quantized_support'] += 1
                return None
        resolved_z = _node_z_at(x,y,surface_z)
        if resolved_z is None:
            reject['unreachable_support_z'] += 1
            return None
        idx = len(nodes)
        nodes.append({'x':float(x),'y':float(y),'z':resolved_z,'b12':int(b12),'b15':int(b15),'b16':0,'b17':0,'b18':0,'tag':tag,'family':family,
                      'quantized_map_grid':quantized_node})
        coverage_radius_state['generated'] = max(
            coverage_radius_state['generated'], radius_m_local(b15))
        node_grid.insert(x,y,idx)
        return idx

    def _corridor_node_dominates_point(corridor_node, px, py, pz):
        axis_degrees = corridor_node.get('corridor_axis')
        if axis_degrees is None:
            return False
        if abs(float(corridor_node['z']) - float(pz)) > 0.75:
            return False
        dx = float(px) - float(corridor_node['x'])
        dy = float(py) - float(corridor_node['y'])
        distance = math.hypot(dx, dy)
        if distance > 3.10:
            return False
        axis = math.radians(float(axis_degrees))
        along = abs(dx * math.cos(axis) + dy * math.sin(axis))
        lateral = abs(-dx * math.sin(axis) + dy * math.cos(axis))
        clearance = float(corridor_node.get(
            'corridor_clearance', radius_m_local(corridor_node['b15'])))
        if along > 1.75 or lateral > max(2.40, clearance + 0.90):
            return False

        # Do not dominate through a wall. Closely sample the straight route on
        # the same support plane; a hard segment drives clearance below 0.12 m.
        sample_count = max(2, int(math.ceil(distance / 0.20)))
        corridor_surface_z = float(corridor_node['z']) - 1.2489
        point_surface_z = float(pz) - 1.2489
        for sample_i in range(1, sample_count):
            t = sample_i / sample_count
            sx = float(corridor_node['x']) + dx * t
            sy = float(corridor_node['y']) + dy * t
            sz = corridor_surface_z + (point_surface_z - corridor_surface_z) * t
            if not walkable(sx, sy, 0.12, surface_z=sz):
                return False
        return True

    _hard_lane_cache = {}
    _hard_lane_segment_cache = {}
    _hard_lane_axis_cache = {}

    def _hard_corridor_lane_at(x, y, surface_z, preferred_axis=None):
        """Return a collision-defined lane and its centered candidate.

        Reachable support remains continuous beneath outdoor compounds, so
        support-strip width cannot distinguish a MogBlk passage from an open
        field.  This test uses actual opposing collision walls at multiple
        longitudinal stations and is therefore shared by outdoor and indoor
        constrained spaces.
        """
        if surface_z is None:
            return None
        preferred = (
            None if preferred_axis is None
            else float(preferred_axis) % math.pi)
        key = (
            round(float(x) * 2.0) / 2.0,
            round(float(y) * 2.0) / 2.0,
            round(float(surface_z) * 2.0) / 2.0,
            None if preferred is None else round(preferred, 3),
        )
        if key in _hard_lane_cache:
            return _hard_lane_cache[key]

        segment_key = (float(x), float(y), float(surface_z))
        _pre = _hard_lane_segment_cache.get(segment_key)
        if _pre is None:
            body_lo = float(surface_z) + 0.20
            body_hi = float(surface_z) + 1.80
            _pre = []
            # Lane rays never accept a wall farther than 7.50 m.  Restrict the
            # spatial query and reject segment bounding boxes outside that circle
            # before the segment is tested against up to thirteen axes.
            for _si in _segs3d_near(x, y, 7.50):
                _s = segs3d[_si]
                _gap_x = max(_s[0] - x, x - _s[3], 0.0) if _s[0] <= _s[3] else max(_s[3] - x, x - _s[0], 0.0)
                _gap_y = max(_s[1] - y, y - _s[4], 0.0) if _s[1] <= _s[4] else max(_s[4] - y, y - _s[1], 0.0)
                if _gap_x * _gap_x + _gap_y * _gap_y > 56.25:
                    continue
                if (max(_s[2], _s[5]) < body_lo
                        or min(_s[2], _s[5]) > body_hi):
                    continue
                _dx = _s[3] - _s[0]
                _dy = _s[4] - _s[1]
                _slen = math.hypot(_dx, _dy)
                if _slen < 1e-6:
                    continue
                _pre.append((
                    _s[0], _s[1], _s[3], _s[4], _dx, _dy, _slen))
            _pre = tuple(_pre)
            _hard_lane_segment_cache[segment_key] = _pre
        if not _pre:
            _hard_lane_cache[key] = None
            return None

        axes = []
        if preferred is not None:
            axes.append(preferred)
        _pi = math.pi
        for axis_i in range(12):
            axis = _pi * axis_i / 12.0
            if all(abs(((axis - old + _pi * 0.5) % _pi)
                       - _pi * 0.5) > 0.0698132
                   for old in axes):
                axes.append(axis)

        def _axis_candidate_uncached(axis):
            axis_x = math.cos(axis)
            axis_y = math.sin(axis)
            side_x = -axis_y
            side_y = axis_x
            # Segment orientation and ray denominator do not change between
            # the three longitudinal stations.  The former implementation
            # recomputed the denominator (and its reciprocal) three times for
            # every eligible segment.  Prepare it once; station order and all
            # exact intersection tests below remain unchanged.
            prepared_segments = []
            for p in _pre:
                axis_dot = p[4] * axis_x + p[5] * axis_y
                if abs(axis_dot / p[6]) < 0.58:
                    continue
                # side=(-axis_y, axis_x), therefore the original cross-product
                # denominator is exactly the negative segment/axis dot above.
                denom = -axis_dot
                if abs(denom) < 1e-9:
                    continue
                prepared_segments.append((
                    p[0], p[1], p[4], p[5], 1.0 / denom,
                ))
            if len(prepared_segments) < 2:
                return None
            station_widths = []
            s0_left = None
            s0_right = None
            fail_count = 0
            for si, along in enumerate((0.0, -1.50, 1.50)):
                if fail_count > 1:
                    break
                px = x + axis_x * along
                py = y + axis_y * along
                left_t = None
                right_t = None
                for sx1, sy1, sdx, sdy, inv_d in prepared_segments:
                    ox = sx1 - px
                    oy = sy1 - py
                    t = (ox * sdy - oy * sdx) * inv_d
                    u = (ox * side_y - oy * side_x) * inv_d
                    if u < -1e-6 or u > 1.000001:
                        continue
                    if t > 0.03 and t <= 7.50:
                        if left_t is None or t < left_t:
                            left_t = t
                    if t < -0.03 and t >= -7.50:
                        rt = -t
                        if right_t is None or rt < right_t:
                            right_t = rt
                if left_t is not None and right_t is not None:
                    w = left_t + right_t
                    if 1.20 <= w <= 7.50:
                        station_widths.append(w)
                        if si == 0:
                            s0_left = left_t
                            s0_right = right_t
                    else:
                        fail_count += 1
                else:
                    fail_count += 1
            if len(station_widths) < 2:
                return None
            if max(station_widths) - min(station_widths) > 1.35:
                return None
            if s0_left is None or s0_right is None:
                return None
            width = s0_left + s0_right
            shift = (s0_left - s0_right) * 0.5
            centered_x = float(x) + side_x * shift
            centered_y = float(y) + side_y * shift
            candidate = {
                'axis': float(axis),
                'axis_degrees': math.degrees(float(axis)) % 180.0,
                'x': centered_x,
                'y': centered_y,
                'width': width,
                'half_width': width * 0.5,
                'shift': shift,
                '_imbalance': abs(s0_left - s0_right),
            }
            return candidate

        def _axis_candidate(axis):
            axis_key = (segment_key, float(axis))
            if axis_key not in _hard_lane_axis_cache:
                _hard_lane_axis_cache[axis_key] = _axis_candidate_uncached(axis)
            return _hard_lane_axis_cache[axis_key]

        best = None
        for axis in axes:
            candidate = _axis_candidate(axis)
            if candidate is None:
                continue
            preference_penalty = 0.0
            if preferred is not None:
                preference_penalty = abs(
                    ((axis - preferred + _pi * 0.5) % _pi)
                    - _pi * 0.5)
            score = (
                preference_penalty * 2.0,
                float(candidate['_imbalance']),
                float(candidate['width']),
            )
            if best is None or score < best[0]:
                best = (score, candidate)
        result = None if best is None else best[1]
        _hard_lane_cache[key] = result
        return result

    placement_profile = defaultdict(float)
    _model_support_cache = {}

    def _model_supported_at(x, y, surface_z):
        if (not height_aware_collision
                or not hasattr(z_at, 'model_support_near')
                or surface_z is None):
            return False
        key = (
            round(float(x), 4),
            round(float(y), 4),
            round(float(surface_z), 4),
        )
        if key not in _model_support_cache:
            try:
                _model_support_cache[key] = bool(
                    z_at.model_support_near(x, y, surface_z))
            except Exception:
                _model_support_cache[key] = False
        return _model_support_cache[key]

    _lane_snap_cache = {}
    lane_snapped_nodes = [0]

    def _support_lane_snap(x, y, surface_z,
                           max_width=float(globals().get(
                               'GENERATOR_LANE_SNAP_MAX_WIDTH', 3.40))):
        """Centre a node across a narrow strip of walkable support.

        Wall-to-wall corridor detection cannot see a balcony, catwalk or stair
        run: one side is a wall, the other is a drop, and a drop is not
        collision geometry, so these chains fall through to the open-field
        path and wander across the strip.  The strip is unambiguous in the
        reachable support raster though -- it simply stops.  Measure the
        walkable extent across several axes, take the narrowest, and move to
        its midpoint.  Only strips narrower than max_width are touched, so
        rooms and open ground keep their natural texture.
        """
        layers = getattr(z_at, 'reachable_layers', None) if callable(z_at) else None
        origin = getattr(z_at, 'grid_origin', None) if callable(z_at) else None
        if not layers or origin is None or surface_z is None:
            return None
        gox, goy = origin
        gcell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25)
        key = (int(round(x / gcell)), int(round(y / gcell)),
               int(round(float(surface_z) / 0.25)))
        if key in _lane_snap_cache:
            return _lane_snap_cache[key]

        def supported(px, py):
            gi = int(round((px - gox) / gcell))
            gj = int(round((py - goy) / gcell))
            for value in layers.get((gi, gj), ()):
                if abs(float(value) - float(surface_z)) <= 0.35:
                    return True
            return False

        best = None
        for axis_i in range(6):
            angle = math.pi * axis_i / 6.0
            nx = -math.sin(angle)
            ny = math.cos(angle)
            forward = 0.0
            while forward < max_width:
                if not supported(x + nx * (forward + gcell),
                                 y + ny * (forward + gcell)):
                    break
                forward += gcell
            backward = 0.0
            while backward < max_width:
                if not supported(x - nx * (backward + gcell),
                                 y - ny * (backward + gcell)):
                    break
                backward += gcell
            width = forward + backward
            if width < max_width and (best is None or width < best[0]):
                best = (width, nx, ny, forward, backward)
        result = None
        if best is not None:
            _width, nx, ny, forward, backward = best
            # The width was measured across the normal, so the lane runs along
            # the sampled angle.
            lane_angle = math.atan2(-nx, ny)
            ax = math.cos(lane_angle)
            ay = math.sin(lane_angle)

            def _width_at(px, py):
                f = 0.0
                while f < max_width:
                    if not supported(px + nx * (f + gcell), py + ny * (f + gcell)):
                        break
                    f += gcell
                b = 0.0
                while b < max_width:
                    if not supported(px - nx * (b + gcell), py - ny * (b + gcell)):
                        break
                    b += gcell
                return f + b

            # Only centre on a strip that actually persists.  At a doorway,
            # stair head or room junction the narrow reading is incidental, and
            # shifting the node off the threshold destroys the link that has to
            # pass through it -- which cost 8 points of largest-component on two
            # reference buildings when this was applied unconditionally.
            persistent = True
            for probe in (1.25, -1.25):
                px = x + ax * probe
                py = y + ay * probe
                if not supported(px, py) or _width_at(px, py) >= max_width:
                    persistent = False
                    break
            if persistent:
                shift = (forward - backward) * 0.5
                result = (x + nx * shift, y + ny * shift,
                          lane_angle, _width * 0.5)
        _lane_snap_cache[key] = result
        return result

    def add_normal(x,y,tag,heading=None,surface_z=None,mode='normal',
                   b15_cap=None,min_clearance=0.62,fallback_b15=None,
                   corridor_hint=None,spacing_mode=None,parent_idx=None):
        nonlocal corridor_last_result
        placement_profile['calls'] += 1
        requested_x = float(x)
        requested_y = float(y)
        lattice_admission = quantize_to_map_grid and mode in ('normal', 'lattice')
        if quantize_to_map_grid:
            x, y = _map_lattice_xy(x, y)
            surface_z = _surface_z_at(x, y, surface_z)
            if surface_z is None:
                reject['unreachable_quantized_support'] += 1
                return False
        elif surface_z is None:
            surface_z = _surface_z_at(x, y)
        if surface_z is None:
            _trace_provenance_record(
                'placement_rejected', requested_x, requested_y,
                tag=str(tag), mode=str(mode), parent_idx=parent_idx,
                reason='no_surface_z')
            return False
        _continuation_axis = (
            None if corridor_hint is None
            else math.radians(float(corridor_hint) % 180.0))
        _model_supported_surface = _model_supported_at(x, y, surface_z)
        _hard_lane = None
        if (mode == 'normal' and not quantize_to_map_grid
                and not _model_supported_surface):
            _nh_pre = nearest_hard(x, y, surface_z)
            if _nh_pre <= 7.50:
                _hl_t0 = _time.perf_counter()
                _hard_lane = _hard_corridor_lane_at(
                    x, y, surface_z, preferred_axis=_continuation_axis)
                placement_profile['hard_lane_sec'] += _time.perf_counter() - _hl_t0
        if _hard_lane is not None:
            _hx = float(_hard_lane['x'])
            _hy = float(_hard_lane['y'])
            if walkable(_hx, _hy, 0.20, surface_z=surface_z):
                x, y = _hx, _hy
            else:
                _hard_lane = None
        _lane_axis = None
        _lane_half_width = None
        if (not quantize_to_map_grid
                and _hard_lane is None
                and globals().get('GENERATOR_SUPPORT_LANE_SNAP', True)):
            _sl_t0 = _time.perf_counter()
            _snapped = _support_lane_snap(x, y, surface_z)
            placement_profile['snap_lane_sec'] += _time.perf_counter() - _sl_t0
            if _snapped is not None:
                _sx, _sy, _sax, _shw = _snapped
                if walkable(_sx, _sy, 0.20, surface_z=surface_z):
                    x, y = _sx, _sy
                    _lane_axis = _sax
                    _lane_half_width = _shw
                    lane_snapped_nodes[0] += 1
        corridor_axis = None
        corridor_factor = None
        # Quantized mode has one placement contract for every source of nodes:
        # surface growth, layered floors, and stair transitions all share the
        # same tangent-disc spacing gate.  Letting the legacy repair rule back
        # in here creates snapped-but-overlapping nodes after the main packer.
        corridor_spacing_mode = ('lattice' if quantize_to_map_grid else None)
        _corridor_t0 = _time.perf_counter()
        corridor_last_result = None
        corridor = None
        if mode == 'normal' and not quantize_to_map_grid:
            if _hard_lane is not None:
                _hard_clearance = min(
                    float(_hard_lane['half_width']),
                    float(nearest_hard(x, y, surface_z)))
                _hard_factor = float(globals().get(
                    'GENERATOR_CORRIDOR_FINAL_FACTOR', 0.85))
                _hard_b15 = min(
                    48, int(_hard_clearance / (_hard_factor * 0.0625)))
                if _hard_b15 >= 8:
                    corridor = (
                        float(x), float(y), int(_hard_b15),
                        float(_hard_lane['axis']), float(_hard_clearance))
                    corridor_last_result = {
                        'accepted': True,
                        'reason': 'hard_parallel_sides',
                        'px': round(float(x), 4),
                        'py': round(float(y), 4),
                        'b15': int(_hard_b15),
                        'axis_degrees': round(float(
                            _hard_lane['axis_degrees']), 4),
                        'clearance': round(float(_hard_clearance), 4),
                        'width': round(float(_hard_lane['width']), 4),
                    }
            else:
                corridor = corridor_fit_candidate(
                    x, y, surface_z,
                    continuation_axis=_continuation_axis)
        corridor_trial = (
            dict(corridor_last_result)
            if corridor_last_result is not None else None)
        placement_profile['corridor_sec'] += _time.perf_counter() - _corridor_t0
        if mode == 'normal':
            placement_profile['corridor_calls'] += 1
        if corridor is not None:
            placement_profile['corridor_hits'] += 1
            x, y, b15, corridor_axis, _corridor_clearance = corridor
            _classify_t0 = _time.perf_counter()
            _legacy_b15, b12, _legacy_family = classify(x, y, surface_z)
            placement_profile['classify_sec'] += _time.perf_counter() - _classify_t0
            fam = 'corridor_fit'
            corridor_factor = float(globals().get('GENERATOR_CORRIDOR_FINAL_FACTOR', 0.85))
            corridor_spacing_mode = 'corridor'
        elif lattice_admission:
            _classify_t0 = _time.perf_counter()
            b15,b12,fam = classify_lattice(x,y,surface_z)
            placement_profile['classify_sec'] += _time.perf_counter() - _classify_t0
            corridor_spacing_mode = 'lattice'
        else:
            _classify_t0 = _time.perf_counter()
            _ordinary_outdoor = (
                ordinary_outdoor_contract_enabled
                and mode == 'normal' and not _model_supported_surface)
            b15,b12,fam = classify(
                x, y, surface_z,
                radius_factor=(
                    OUTDOOR_ACCEPTANCE_CLEARANCE_FACTOR
                    if _ordinary_outdoor else None),
                bands=(OUTDOOR_BANDS if _ordinary_outdoor else None))
            placement_profile['classify_sec'] += _time.perf_counter() - _classify_t0
        if b15 == 0 and fallback_b15 is not None:
            _fallback_t0 = _time.perf_counter()
            if nearest_hard(x,y,surface_z) >= max(
                    float(min_clearance),
                    radius_m_local(fallback_b15) * NODE_RADIUS_CLEARANCE_FACTOR):
                b15,b12,fam = int(fallback_b15),1,'stair_transition'
            placement_profile['fallback_sec'] += _time.perf_counter() - _fallback_t0
        if b15_cap is not None and b15:
            b15 = min(int(b15), int(b15_cap))
        if b15 == 0:
            reject['no_fit'] += 1
            _trace_provenance_record(
                'placement_rejected', x, y,
                requested_x=round(requested_x, 4),
                requested_y=round(requested_y, 4),
                tag=str(tag), mode=str(mode), parent_idx=parent_idx,
                reason='no_fit', family=str(fam),
                corridor_trial=corridor_trial,
                corridor_hint_degrees=(
                    None if corridor_hint is None else round(
                        float(corridor_hint) % 180.0, 4)))
            return False
        _gate_t0 = _time.perf_counter()
        _gate_radius_factor = corridor_factor
        if (ordinary_outdoor_contract_enabled
                and _gate_radius_factor is None and mode == 'normal'
                and not _model_supported_surface):
            _gate_radius_factor = OUTDOOR_ACCEPTANCE_CLEARANCE_FACTOR
        _can_add = can_add_normal(
                x,y,b15,surface_z=surface_z,mode=mode,
                min_clearance=min_clearance,
                radius_factor=_gate_radius_factor,
                spacing_mode=(spacing_mode if spacing_mode is not None
                              else corridor_spacing_mode))
        placement_profile['gate_sec'] += _time.perf_counter() - _gate_t0
        if not _can_add:
            _spacing_mode_used = (
                spacing_mode if spacing_mode is not None
                else corridor_spacing_mode)
            _spacing_ok_trace, _spacing_info_trace = spacing_ok(
                x, y, b15, _spacing_mode_used or mode,
                surface_z=surface_z)
            _trace_provenance_record(
                'placement_rejected', x, y,
                requested_x=round(requested_x, 4),
                requested_y=round(requested_y, 4),
                tag=str(tag), mode=str(mode), parent_idx=parent_idx,
                reason=(
                    'spacing' if not _spacing_ok_trace
                    else 'clearance_or_domain'),
                family=str(fam), b15=int(b15),
                nearest_hard=round(
                    float(nearest_hard(x, y, surface_z)), 4),
                spacing_mode=str(_spacing_mode_used or mode),
                spacing=_spacing_info_trace,
                corridor_trial=corridor_trial,
                corridor_hint_degrees=(
                    None if corridor_hint is None else round(
                        float(corridor_hint) % 180.0, 4)))
            if fam == 'corridor_fit' and _debug_probe_contains(x, y):
                _spacing_ok_debug, _spacing_info_debug = spacing_ok(
                    x, y, b15, 'corridor', surface_z=surface_z)
                corridor_fit_debug_records.append({
                    'x': round(float(x), 4), 'y': round(float(y), 4),
                    'reason': 'admission_rejected', 'b15': int(b15),
                    'nearest_hard': round(float(nearest_hard(x, y, surface_z)), 4),
                    'required_clearance': round(float(max(
                        float(min_clearance), radius_m_local(b15) *
                        float(globals().get('GENERATOR_CORRIDOR_FINAL_FACTOR', 0.85)))), 4),
                    'spacing_ok': bool(_spacing_ok_debug),
                    'spacing': _spacing_info_debug})
            return False
        _insert_t0 = _time.perf_counter()
        idx=add_node_raw(x,y,b15,b12,tag,fam,surface_z=surface_z)
        placement_profile['insert_sec'] += _time.perf_counter() - _insert_t0
        if idx is None:
            _trace_provenance_record(
                'placement_rejected', x, y,
                tag=str(tag), mode=str(mode), parent_idx=parent_idx,
                reason='raw_insert_failed', family=str(fam), b15=int(b15),
                corridor_trial=corridor_trial)
            return False
        nodes[idx]['creation_index'] = int(idx)
        nodes[idx]['parent_index'] = (
            None if parent_idx is None else int(parent_idx))
        nodes[idx]['requested_mode'] = str(mode)
        nodes[idx]['spacing_mode'] = str(
            spacing_mode if spacing_mode is not None
            else (corridor_spacing_mode or mode))
        nodes[idx]['corridor_trial'] = corridor_trial
        nodes[idx]['corridor_hint'] = (
            None if corridor_hint is None else float(corridor_hint))
        _trace_provenance_record(
            'placement_accepted', x, y,
            requested_x=round(requested_x, 4),
            requested_y=round(requested_y, 4),
            node_index=int(idx), tag=str(tag), mode=str(mode),
            parent_idx=parent_idx, family=str(fam), b15=int(b15),
            spacing_mode=nodes[idx]['spacing_mode'],
            corridor_trial=corridor_trial,
            corridor_hint_degrees=(
                None if corridor_hint is None else round(
                    float(corridor_hint) % 180.0, 4)))
        if fam == 'corridor_fit' and _debug_probe_contains(x, y):
            corridor_fit_debug_records.append({
                'x': round(float(x), 4), 'y': round(float(y), 4),
                'reason': 'admission_accepted', 'b15': int(b15), 'index': int(idx)})
        if fam == 'corridor_fit' and corridor_axis is not None:
            axis_heading = math.degrees(float(corridor_axis)) % 180.0
            if heading is not None:
                option_a = axis_heading
                option_b = axis_heading + 180.0
                delta_a = abs(((option_a - float(heading) + 180.0) % 360.0) - 180.0)
                delta_b = abs(((option_b - float(heading) + 180.0) % 360.0) - 180.0)
                corridor_heading = option_a if delta_a <= delta_b else option_b
            else:
                corridor_heading = axis_heading
            nodes[idx]['corridor_axis'] = axis_heading
            nodes[idx]['corridor_clearance'] = float(_corridor_clearance)
            nodes[idx]['heading'] = corridor_heading
        elif _lane_axis is not None:
            # Kept separate from corridor_axis on purpose: a support-strip lane
            # steers the walker but must not freeze the node out of the radius
            # refit the way a proven wall-to-wall corridor does.
            lane_heading = math.degrees(float(_lane_axis)) % 180.0
            if heading is not None:
                _opt_a = lane_heading
                _opt_b = lane_heading + 180.0
                _delta_a = abs(((_opt_a - float(heading) + 180.0) % 360.0) - 180.0)
                _delta_b = abs(((_opt_b - float(heading) + 180.0) % 360.0) - 180.0)
                nodes[idx]['heading'] = _opt_a if _delta_a <= _delta_b else _opt_b
            else:
                nodes[idx]['heading'] = lane_heading
            nodes[idx]['lane_axis'] = lane_heading
            if _lane_half_width is not None:
                nodes[idx]['lane_half_width'] = float(_lane_half_width)
        else:
            nodes[idx]['heading']=heading
        return True

    _progress(
        27, 'Growing navigation nodes',
        ('Packing a 0.50 m base lattice on 0.25 m coordinates'
         if quantize_to_map_grid else 'Walking outward from the selected seed'))
    # Depth-first walker: campaign traversal discipline (ID coherence).
    seed_surface_z = getattr(z_at, 'seed_z', None) if callable(z_at) else None
    if stable_outdoor_seed_anchor:
        seed_surface_z = _surface_z_at(cx, cy, seed_surface_z)
    if seed_surface_z is None:
        seed_surface_z = _surface_z_at(cx, cy)
    seed_ground_z = getattr(z_at, 'seed_ground_z', None)
    seed_selection = str(getattr(z_at, 'seed_selection', '') or '')
    seed_is_special_surface = (
        seed_selection in ('requested_support_z', 'clicked_model_support')
        or (seed_ground_z is not None and seed_surface_z is not None
            and abs(float(seed_surface_z) - float(seed_ground_z)) > 0.75))
    ordinary_outdoor_contract_enabled = bool(
        not quantize_to_map_grid
        and not seed_is_special_surface
        and seed_surface_z is not None
        and not _model_supported_at(cx, cy, seed_surface_z))
    if (not quantize_to_map_grid
            and walkable(cx,cy,0.62,surface_z=seed_surface_z)):
        add_normal(cx,cy,'seed',surface_z=seed_surface_z)
    tries = 0
    while not quantize_to_map_grid and not nodes and tries < 500:
        tries += 1
        a = rng.random() * math.tau
        r = R * math.sqrt(rng.random())
        x = cx + math.cos(a)*r
        y = cy + math.sin(a)*r
        fallback_surface_z = _surface_z_at(x, y, seed_surface_z)
        if walkable(x,y,0.62,surface_z=fallback_surface_z):
            add_normal(x,y,'seed_fallback',surface_z=fallback_surface_z)

    stack = list(range(len(nodes))) if not quantize_to_map_grid else []
    rm_brush = brush_b15/16.0
    if quantize_to_map_grid:
        _base_pitch = float(GENERATOR_QUANTIZED_BASE_PITCH_M)
        max_nodes_soft = min(20000, max(
            256, int((math.pi * R * R) / max(0.125, _base_pitch ** 2))))
    else:
        max_nodes_soft = max(
            96, int((math.pi*R*R) / max(1.2, 2.0*rm_brush*rm_brush)))
    SCAN = [0.0, -30.0, 30.0, -60.0, 60.0, -95.0, 95.0, -135.0, 135.0, 180.0]

    grow_started = _time.perf_counter()
    grow_candidate_checks = 0
    grow_walkable_sec = 0.0
    grow_placement_sec = 0.0
    quantized_stair_base_cells_reserved = 0

    def _report_grower_progress(force=False):
        if not force and grow_candidate_checks % 50:
            return
        elapsed = _time.perf_counter() - grow_started
        other = max(0.0, elapsed - grow_walkable_sec - grow_placement_sec)
        pct = 27.0 + min(19.0, 19.0 * len(nodes) / max(1, max_nodes_soft))
        _progress(
            pct,
            'Growing navigation nodes',
            f'Nodes {len(nodes)}/{max_nodes_soft}; frontier {len(stack)}; '
            f'candidate checks {grow_candidate_checks}; support tests {grow_walkable_sec:.2f}s; '
            f'placement/classification {grow_placement_sec:.2f}s; other {other:.2f}s')

    if quantize_to_map_grid:
        # SPBHD_13 proves that 0.25 m is the coordinate quantum, while the
        # smallest regular family (b15=4, radius .25 m) has a .50 m pitch.
        # Admit that complete fine lattice before assigning any larger radius.
        def _quantized_final_candidate_ok(qx, qy, qsurface_z, qb15):
            """Apply the later validator before a hierarchy transaction.

            A parent may consume four children only when that same parent will
            survive the final solid/radius gate.  Otherwise rejecting it later
            creates a four-cell coverage hole with no way to restore the kids.
            """
            qsolid_layers = solid_detail(qx, qy)
            qmodel_supported = _model_supported_at(qx, qy, qsurface_z)
            qsolid_ok = ((not qsolid_layers)
                         or (height_aware_collision
                             and qmodel_supported))
            qrequired = max(
                float(GENERATOR_QUANTIZED_CENTER_CLEARANCE),
                radius_m_local(qb15) * NODE_RADIUS_CLEARANCE_FACTOR)
            return (qsolid_ok
                    and nearest_hard(qx, qy, qsurface_z) >= qrequired)

        base_pitch = float(GENERATOR_QUANTIZED_BASE_PITCH_M)
        quantized_stair_layers = getattr(
            z_at, 'geometry_stair_layers', {}) if callable(z_at) else {}
        quantized_grid_origin = getattr(
            z_at, 'grid_origin', None) if callable(z_at) else None
        quantized_support_cell = float(getattr(
            z_at, 'grid_cell', 0.25) or 0.25) if callable(z_at) else 0.25

        def _quantized_stair_support_at(x, y, surface_z):
            """Reserve verified stair treads for the sparse flight sampler."""
            if (surface_z is None or quantized_grid_origin is None
                    or not quantized_stair_layers):
                return False
            ix = int(round(
                (float(x) - float(quantized_grid_origin[0]))
                / quantized_support_cell))
            iy = int(round(
                (float(y) - float(quantized_grid_origin[1]))
                / quantized_support_cell))
            return any(
                abs(float(value) - float(surface_z)) <= 0.08
                for value in quantized_stair_layers.get((ix, iy), ()))

        # NovaLogic-style additive ownership: quantized survivors retain the
        # absolute base cells from which they were promoted.  A later seed may
        # traverse those cells, but it may not admit them again.  Older/non-
        # quantized nodes do not have exact provenance, so conservatively
        # rasterize their acceptance discs onto the same absolute lattice.
        # The value is a list because stacked floors can own the same XY cell.
        prior_cell_owners = defaultdict(list)
        for prior_index, prior in enumerate(prior_nodes):
            exact_keys = prior.get('quantized_source_keys') or ()
            if (not exact_keys and prior.get('quantized_map_grid')
                    and int(prior.get('b15', 0)) in (4, 8, 16, 32)):
                side_cells = int(prior['b15']) // 4
                center_qx = int(round(prior['x'] / quantized_grid_m))
                center_qy = int(round(prior['y'] / quantized_grid_m))
                source_offsets = range(-(side_cells - 1), side_cells, 2)
                exact_keys = frozenset(
                    (center_qx + off_x, center_qy + off_y)
                    for off_x in source_offsets for off_y in source_offsets)
            if exact_keys:
                for source_key in exact_keys:
                    prior_cell_owners[(int(source_key[0]),
                                       int(source_key[1]))].append(
                        (float(prior['z']), prior_index))
                continue
            owner_radius = max(
                base_pitch * 0.5,
                radius_m_local(prior['b15']) + base_pitch * 0.4)
            owner_ix0 = int(math.floor((prior['x'] - owner_radius) / base_pitch))
            owner_ix1 = int(math.ceil((prior['x'] + owner_radius) / base_pitch))
            owner_iy0 = int(math.floor((prior['y'] - owner_radius) / base_pitch))
            owner_iy1 = int(math.ceil((prior['y'] + owner_radius) / base_pitch))
            inserted_owner_cell = False
            for owner_ix in range(owner_ix0, owner_ix1 + 1):
                owner_x = owner_ix * base_pitch
                for owner_iy in range(owner_iy0, owner_iy1 + 1):
                    owner_y = owner_iy * base_pitch
                    if math.hypot(owner_x - prior['x'],
                                  owner_y - prior['y']) > owner_radius + 1e-9:
                        continue
                    prior_cell_owners[(owner_ix * 2, owner_iy * 2)].append(
                        (float(prior['z']), prior_index))
                    inserted_owner_cell = True
            if not inserted_owner_cell:
                nearest_ix = int(math.floor(prior['x'] / base_pitch + 0.5))
                nearest_iy = int(math.floor(prior['y'] / base_pitch + 0.5))
                prior_cell_owners[(nearest_ix * 2, nearest_iy * 2)].append(
                    (float(prior['z']), prior_index))

        def _prior_owns_quantized_cell(source_key, surface_z):
            for owner_z, _owner_index in prior_cell_owners.get(source_key, ()):
                if abs(float(owner_z) - float(surface_z)) <= 1.80:
                    return True
            return False

        seed_ix = int(math.floor(cx / base_pitch + 0.5))
        seed_iy = int(math.floor(cy / base_pitch + 0.5))
        ix0 = int(math.floor((cx - R) / base_pitch))
        ix1 = int(math.ceil((cx + R) / base_pitch))
        iy0 = int(math.floor((cy - R) / base_pitch))
        iy1 = int(math.ceil((cy + R) / base_pitch))
        lattice_queue = deque([(seed_ix, seed_iy)])
        lattice_seen = set()
        lattice_candidates = {}
        lattice_doorway_rejected = {}
        _lattice_solid_not_walkable = 0
        lattice_cell_estimate = max(1, int(math.pi * (R / base_pitch) ** 2))
        _progress(27, 'Admitting quantized base lattice',
                  'Testing absolute 0.50 m base cells before radius assignment')

        while lattice_queue:
            ix, iy = lattice_queue.popleft()
            if (ix, iy) in lattice_seen:
                continue
            lattice_seen.add((ix, iy))
            if ix < ix0 or ix > ix1 or iy < iy0 or iy > iy1:
                continue
            x = ix * base_pitch
            y = iy * base_pitch
            if math.hypot(x - cx, y - cy) > R + 1e-9:
                continue
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                nxt = (ix + dx, iy + dy)
                if nxt not in lattice_seen:
                    lattice_queue.append(nxt)

            grow_candidate_checks += 1
            candidate_surface_z = _surface_z_at(x, y, seed_surface_z)
            qx = int(round(x / quantized_grid_m))
            qy = int(round(y / quantized_grid_m))
            source_key = (qx, qy)
            if _quantized_stair_support_at(x, y, candidate_surface_z):
                # Quantized room/floor cells stay hierarchical, but a stair
                # flight uses the normal generator's sparse, width-aware rows.
                # Admitting these tread cells here would pack nodes onto nearly
                # every step before the stair sampler gets a chance to run.
                quantized_stair_base_cells_reserved += 1
                continue
            if (candidate_surface_z is not None
                    and _prior_owns_quantized_cell(
                        source_key, float(candidate_surface_z))):
                reject['prior_quantized_cell_owned'] += 1
                continue
            _walk_t0 = _time.perf_counter()
            candidate_walkable = walkable(
                x, y, GENERATOR_QUANTIZED_CENTER_CLEARANCE,
                surface_z=candidate_surface_z)
            grow_walkable_sec += _time.perf_counter() - _walk_t0
            if candidate_walkable and candidate_surface_z is not None:
                if _quantized_final_candidate_ok(
                        x, y, float(candidate_surface_z), 4):
                    lattice_candidates[(qx, qy)] = {
                        'x': x, 'y': y, 'surface_z': float(candidate_surface_z),
                        'b15': 4, 'source_cells': 1,
                        'source_keys': frozenset(((qx, qy),)),
                        'max_b15': int(classify_lattice(
                            x, y, float(candidate_surface_z))[0]),
                    }
                else:
                    lattice_doorway_rejected[(qx, qy)] = {
                        'x': x, 'y': y,
                        'surface_z': float(candidate_surface_z),
                    }
            elif (candidate_surface_z is not None
                    and not candidate_walkable and solid_detail(x, y)):
                _lattice_solid_not_walkable += 1

            if grow_candidate_checks % 256 == 0:
                pct = min(38.0, 27.0 + 11.0 *
                          (len(lattice_seen) / lattice_cell_estimate))
                _progress(
                    pct, 'Admitting quantized base lattice',
                    f'Cells {len(lattice_seen)}/{lattice_cell_estimate}; '
                    f'walkable candidates {len(lattice_candidates)}; '
                    f'support tests {grow_walkable_sec:.2f}s')

        # Doorway transition bridge: cells that passed walkable() but were
        # rejected by qsolid_ok at building doorway thresholds where model
        # floor triangles stop short of the opening.  The normal generator
        # handles these via walkable() alone (no solid_detail gate).  Admit
        # rejected cells that have an already-admitted lattice neighbor
        # within one Z-step, up to 2 cells deep into the gap.
        lattice_bridge_count = 0
        _bridge_max_z = float(GENERATOR_REACHABLE_MAX_STEP)
        for _bridge_iter in range(2):
            _bridge_new = {}
            for (bqx, bqy), binfo in lattice_doorway_rejected.items():
                if (bqx, bqy) in lattice_candidates:
                    continue
                _anchor_z = None
                for bdx, bdy in ((2, 0), (0, 2), (-2, 0), (0, -2)):
                    bnk = (bqx + bdx, bqy + bdy)
                    if bnk in lattice_candidates:
                        _anchor_z = lattice_candidates[bnk]['surface_z']
                        break
                if _anchor_z is None:
                    continue
                bsz = binfo['surface_z']
                if abs(bsz - _anchor_z) > _bridge_max_z:
                    continue
                _bridge_new[(bqx, bqy)] = {
                    'x': binfo['x'], 'y': binfo['y'],
                    'surface_z': bsz,
                    'b15': 4, 'source_cells': 1,
                    'source_keys': frozenset(((bqx, bqy),)),
                    'max_b15': 4,
                }
            lattice_candidates.update(_bridge_new)
            lattice_bridge_count += len(_bridge_new)
            if not _bridge_new:
                break

        # Recursive adaptive 2x2 promotion.  This primary hierarchy is exact:
        # four child cells make one parent.  Keeping it exact preserves the
        # local phase that permits later b16/b32 promotions.
        packed_nodes = []
        current_level = dict(lattice_candidates)
        promotion_counts = Counter()
        partial_promotion_counts = Counter()
        _progress(38, 'Promoting quantized radius families',
                  f'Base candidates {len(current_level)}; testing 2x2 groups')
        for target_b15, phase_pct in ((8, 40.5), (16, 43.0), (32, 45.5)):
            child_b15 = target_b15 // 2
            child_pitch_q = max(1, child_b15 // 2)
            consumed = set()
            promoted = {}
            # Placement remains the campaign-calibrated greedy hierarchy.
            # Phase forcing was tested here and rejected: it over-promoted
            # coarse families and erased valid coverage.  Clean graph structure
            # is instead recovered from the base-cell topology retained below.
            ordered_origins = sorted(current_level, key=lambda p: (p[1], p[0]))
            for qx, qy in ordered_origins:
                quad = ((qx, qy),
                        (qx + child_pitch_q, qy),
                        (qx, qy + child_pitch_q),
                        (qx + child_pitch_q, qy + child_pitch_q))
                if any(key in consumed or key not in current_level for key in quad):
                    continue
                present_keys = list(quad)
                children = [current_level[key] for key in present_keys]
                child_z = [item['surface_z'] for item in children]
                target_radius = radius_m_local(target_b15)
                max_support_delta = max(0.40, target_radius * 0.75)
                if max(child_z) - min(child_z) > max_support_delta * 2.0:
                    continue
                center_qx = qx + child_pitch_q // 2
                center_qy = qy + child_pitch_q // 2
                center_x = center_qx * quantized_grid_m
                center_y = center_qy * quantized_grid_m
                if math.hypot(center_x - cx, center_y - cy) > R + 1e-9:
                    continue
                center_hint = sum(child_z) / 4.0
                center_z = _surface_z_at(center_x, center_y, center_hint)
                if center_z is None:
                    continue
                if max(abs(float(center_z) - value) for value in child_z) > max_support_delta:
                    continue
                required_clearance = max(
                    float(GENERATOR_QUANTIZED_CENTER_CLEARANCE),
                    target_radius * NODE_RADIUS_CLEARANCE_FACTOR)
                _walk_t0 = _time.perf_counter()
                promotion_legal = walkable(
                    center_x, center_y, required_clearance,
                    surface_z=center_z)
                grow_walkable_sec += _time.perf_counter() - _walk_t0
                if (not promotion_legal
                        or not _quantized_final_candidate_ok(
                            center_x, center_y, float(center_z), target_b15)):
                    continue
                consumed.update(present_keys)
                promoted[(center_qx, center_qy)] = {
                    'x': center_x, 'y': center_y,
                    'surface_z': float(center_z), 'b15': target_b15,
                    'source_cells': sum(item['source_cells'] for item in children),
                    'source_keys': frozenset().union(
                        *(item['source_keys'] for item in children)),
                }

            for key, item in current_level.items():
                if key not in consumed:
                    packed_nodes.append(item)
            promotion_counts[target_b15] = len(promoted)
            current_level = promoted
            _progress(
                phase_pct, 'Promoting quantized radius families',
                f'b15 {child_b15}->{target_b15}: {len(promoted)} promotions; '
                f'{partial_promotion_counts[target_b15]} partial; '
                f'{len(packed_nodes)} unpromoted survivors retained')
        packed_nodes.extend(current_level.values())


        # Conservative source-cell optimizer.  The previous cleanup searched
        # for any three nearby b8 centres and replaced them with b16.  Those
        # centres did not necessarily own one parent work cell, so the pass
        # could create the broken rails visible in quantized output.
        #
        # Every transaction below is instead expressed in the admitted 0.50 m
        # work lattice.  A parent may replace leaves only when their source-key
        # union is exactly the admitted part of one legal parent footprint.
        # Missing source cells must have been tested inside this seed (rather
        # than merely lying outside it), may occur only on the footprint rim,
        # and the admitted cells must remain cardinally connected.  Thus a
        # clipped wall/edge cell can be consolidated, but a parent can never
        # bridge an interior obstacle, consume a foreign phase, or lose source
        # coverage.  Existing same-family phase is only a tie-breaker.
        optimizer_counts = Counter()
        optimizer_rejects = Counter()
        admitted_source_keys = frozenset(lattice_candidates)

        # The recursive pass is excellent at the coarse decision (its b32
        # population already matches SPBHD_13), but its row-major b8 choices
        # can strand four-by-four source blocks that should become b16.  Freeze
        # those proven b32 leaves, return every lower leaf to its admitted base
        # cells, and repack b16 then b8.  Implied child centres from the frozen
        # parent are phase anchors; away from an anchor the stable row-major
        # order establishes a local phase rather than switching per node.
        frozen_b32 = [item for item in packed_nodes if int(item['b15']) == 32]
        frozen_source_keys = (frozenset().union(
            *(item['source_keys'] for item in frozen_b32))
            if frozen_b32 else frozenset())
        remaining_source_keys = set(admitted_source_keys.difference(
            frozen_source_keys))
        repacked_nodes = list(frozen_b32)
        phase_anchors = {
            16: set(),
            8: set(),
        }
        for item in frozen_b32:
            parent_qx = int(round(item['x'] / quantized_grid_m))
            parent_qy = int(round(item['y'] / quantized_grid_m))
            for off_x in (-4, 4):
                for off_y in (-4, 4):
                    phase_anchors[16].add((parent_qx + off_x,
                                           parent_qy + off_y))

        def _exact_repack_family(target_b15, source_keys, anchors):
            if not source_keys:
                return [], set()
            side_cells = target_b15 // 4
            source_offsets = tuple(range(
                -(side_cells - 1), side_cells, 2))
            source_x = [key[0] for key in source_keys]
            source_y = [key[1] for key in source_keys]
            first_center_x = min(source_x) + 1
            first_center_y = min(source_y) + 1
            if not (first_center_x & 1):
                first_center_x += 1
            if not (first_center_y & 1):
                first_center_y += 1
            pitch_q = target_b15 // 2
            candidates = []
            for center_qx in range(first_center_x, max(source_x) + 2, 2):
                for center_qy in range(first_center_y, max(source_y) + 2, 2):
                    full_keys = frozenset(
                        (center_qx + off_x, center_qy + off_y)
                        for off_x in source_offsets for off_y in source_offsets)
                    if not full_keys.issubset(source_keys):
                        continue
                    if any(int(lattice_candidates[key].get('max_b15', 4))
                           < target_b15 for key in full_keys):
                        continue
                    source_z = [lattice_candidates[key]['surface_z']
                                for key in full_keys]
                    target_radius = radius_m_local(target_b15)
                    max_support_delta = max(0.40, target_radius * 0.75)
                    if max(source_z) - min(source_z) > max_support_delta * 2.0:
                        continue
                    center_x = center_qx * quantized_grid_m
                    center_y = center_qy * quantized_grid_m
                    center_z = _surface_z_at(
                        center_x, center_y, sum(source_z) / len(source_z))
                    if center_z is None or max(
                            abs(float(center_z) - value) for value in source_z
                            ) > max_support_delta:
                        continue
                    required_clearance = max(
                        float(GENERATOR_QUANTIZED_CENTER_CLEARANCE),
                        target_radius * NODE_RADIUS_CLEARANCE_FACTOR)
                    if (not walkable(center_x, center_y, required_clearance,
                                     surface_z=center_z)
                            or not _quantized_final_candidate_ok(
                                center_x, center_y, float(center_z), target_b15)):
                        continue
                    phase_support = sum(
                        (center_qx + off_x * pitch_q,
                         center_qy + off_y * pitch_q) in anchors
                        for off_x in range(-3, 4) for off_y in range(-3, 4)
                        if off_x or off_y)
                    candidates.append((
                        phase_support, center_qy, center_qx, float(center_z),
                        full_keys))
            # Eligibility has already separated the radius bands.  Inside one
            # band, preserve an inherited local phase and use stable map order
            # only where no parent supplied one.
            candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
            accepted = []
            accepted_centres = set(anchors)
            for (_phase_support, center_qy, center_qx,
                 center_z, full_keys) in candidates:
                if not full_keys.issubset(source_keys):
                    continue
                center_x = center_qx * quantized_grid_m
                center_y = center_qy * quantized_grid_m
                accepted.append({
                    'x': center_x, 'y': center_y,
                    'surface_z': center_z, 'b15': target_b15,
                    'source_cells': len(full_keys),
                    'source_keys': frozenset(full_keys),
                })
                source_keys.difference_update(full_keys)
                accepted_centres.add((center_qx, center_qy))
            return accepted, accepted_centres

        repacked_b16, accepted_b16_centres = _exact_repack_family(
            16, remaining_source_keys, phase_anchors[16])
        repacked_nodes.extend(repacked_b16)
        for center_qx, center_qy in accepted_b16_centres:
            for off_x in (-2, 2):
                for off_y in (-2, 2):
                    phase_anchors[8].add((center_qx + off_x,
                                          center_qy + off_y))
        repacked_b8, _accepted_b8_centres = _exact_repack_family(
            8, remaining_source_keys, phase_anchors[8])
        repacked_nodes.extend(repacked_b8)
        for source_key in sorted(remaining_source_keys,
                                 key=lambda key: (key[1], key[0])):
            repacked_nodes.append(dict(lattice_candidates[source_key]))
        packed_nodes = repacked_nodes
        repacked_family_counts = {
            16: len(repacked_b16),
            8: len(repacked_b8),
        }

        active_leaves = {index: item for index, item in enumerate(packed_nodes)}
        source_owner = {}
        for leaf_id, item in active_leaves.items():
            for source_key in item['source_keys']:
                if source_key in source_owner:
                    raise RuntimeError('quantized source cell has two owners')
                source_owner[source_key] = leaf_id
        next_leaf_id = len(active_leaves)

        def _source_keys_connected(source_keys):
            if not source_keys:
                return False
            pending = [next(iter(source_keys))]
            visited = {pending[0]}
            while pending:
                key_x, key_y = pending.pop()
                for delta_x, delta_y in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                    neighbor = (key_x + delta_x, key_y + delta_y)
                    if neighbor in source_keys and neighbor not in visited:
                        visited.add(neighbor)
                        pending.append(neighbor)
            return len(visited) == len(source_keys)

        def _parent_footprint(center_qx, center_qy, target_b15):
            side_cells = max(2, int(target_b15) // 4)
            offsets = range(-(side_cells - 1), side_cells, 2)
            return frozenset(
                (center_qx + off_x, center_qy + off_y)
                for off_x in offsets for off_y in offsets)

        def _has_interior_source_gap(full_keys, present_keys):
            if len(full_keys) == len(present_keys):
                return False
            xs = sorted({key[0] for key in full_keys})
            ys = sorted({key[1] for key in full_keys})
            min_x, max_x = xs[0], xs[-1]
            min_y, max_y = ys[0], ys[-1]
            for key_x, key_y in full_keys.difference(present_keys):
                if min_x < key_x < max_x and min_y < key_y < max_y:
                    return True
            return False

        # Coarse-to-fine is intentional here.  The exact hierarchy has already
        # established legal coarse phases.  Let those phases claim their work
        # cells before clipped b8 boundary cells can cross a future b16/b32
        # footprint and make that transaction impossible.
        for target_b15 in (16, 8):
            side_cells = target_b15 // 4
            full_cell_count = side_cells * side_cells
            minimum_present = full_cell_count if target_b15 == 32 else int(
                math.ceil(full_cell_count * 0.75))
            target_pitch_q = max(2, target_b15 // 2)
            existing_target_points = {
                (int(round(item['x'] / quantized_grid_m)),
                 int(round(item['y'] / quantized_grid_m)))
                for item in active_leaves.values()
                if int(item['b15']) == target_b15
            }
            candidates = []
            source_x = [key[0] for key in admitted_source_keys]
            source_y = [key[1] for key in admitted_source_keys]
            if not source_x or not source_y:
                continue
            first_center_x = min(source_x) + 1
            first_center_y = min(source_y) + 1
            if not (first_center_x & 1):
                first_center_x += 1
            if not (first_center_y & 1):
                first_center_y += 1
            for center_qx in range(first_center_x, max(source_x) + 2, 2):
                for center_qy in range(first_center_y, max(source_y) + 2, 2):
                    full_keys = _parent_footprint(
                        center_qx, center_qy, target_b15)
                    present_keys = full_keys.intersection(admitted_source_keys)
                    if len(present_keys) < minimum_present:
                        continue
                    if any(int(lattice_candidates[key].get('max_b15', 4))
                           < target_b15 for key in present_keys):
                        optimizer_rejects['source_family'] += 1
                        continue
                    complete = len(present_keys) == full_cell_count
                    if not complete:
                        # A clipped promotion may compact around collision or
                        # the seed rim, but it must never borrow the geometric
                        # footprint of cells owned by a previous batch.  Doing
                        # so leaves a large-radius survivor immediately beside
                        # the old node whose cell caused the clip.
                        missing_keys = full_keys.difference(present_keys)
                        if any(key in prior_cell_owners for key in missing_keys):
                            optimizer_rejects['prior_owned_footprint'] += 1
                            continue
                        # Every nominal source cell must be inside the sampled
                        # disc.  Otherwise this is an unknowable seed-rim clip.
                        if any(math.hypot(
                                key[0] * quantized_grid_m - cx,
                                key[1] * quantized_grid_m - cy) > R + 1.0e-9
                               for key in full_keys):
                            optimizer_rejects['seed_rim'] += 1
                            continue
                        if _has_interior_source_gap(full_keys, present_keys):
                            optimizer_rejects['interior_gap'] += 1
                            continue
                    if not _source_keys_connected(present_keys):
                        optimizer_rejects['disconnected_source'] += 1
                        continue
                    owner_ids = {source_owner[key] for key in present_keys}
                    owner_items = [active_leaves[leaf_id] for leaf_id in owner_ids]
                    if any(not item['source_keys'].issubset(full_keys)
                           for item in owner_items):
                        continue
                    if any(int(item['b15']) >= target_b15 for item in owner_items):
                        continue
                    reduction = len(owner_ids) - 1
                    if reduction <= 0:
                        continue
                    phase_support = sum(
                        (center_qx + off_x * target_pitch_q,
                         center_qy + off_y * target_pitch_q)
                        in existing_target_points
                        for off_x in range(-2, 3) for off_y in range(-2, 3)
                        if off_x or off_y)
                    # Cross-phase b32 creation was the source of the rejected
                    # top-down over-collapse.  Extend an established coarse
                    # phase, but never invent an isolated b32 work cell.
                    if target_b15 == 32 and phase_support < 1:
                        optimizer_rejects['isolated_b32'] += 1
                        continue
                    candidates.append((
                        reduction, int(complete), phase_support,
                        len(present_keys), center_qx, center_qy,
                        full_keys, present_keys))

            candidates.sort(
                key=lambda item: (-item[0], -item[1], -item[2],
                                  -item[3], item[5], item[4]))
            accepted_target_points = set(existing_target_points)
            for (_reduction, _complete, _phase_support, _present_count,
                 center_qx, center_qy, full_keys, present_keys) in candidates:
                owner_ids = {source_owner[key] for key in present_keys}
                if any(leaf_id not in active_leaves for leaf_id in owner_ids):
                    continue
                owner_items = [active_leaves[leaf_id] for leaf_id in owner_ids]
                if any(not item['source_keys'].issubset(full_keys)
                       for item in owner_items):
                    continue
                if any(int(item['b15']) >= target_b15 for item in owner_items):
                    continue
                if frozenset().union(
                        *(item['source_keys'] for item in owner_items)) != present_keys:
                    optimizer_rejects['ownership_mismatch'] += 1
                    continue
                phase_support = sum(
                    (center_qx + off_x * target_pitch_q,
                     center_qy + off_y * target_pitch_q)
                    in accepted_target_points
                    for off_x in range(-2, 3) for off_y in range(-2, 3)
                    if off_x or off_y)
                if target_b15 == 32 and phase_support < 1:
                    continue
                source_z = [lattice_candidates[key]['surface_z']
                            for key in present_keys]
                target_radius = radius_m_local(target_b15)
                max_support_delta = max(0.40, target_radius * 0.75)
                if max(source_z) - min(source_z) > max_support_delta * 2.0:
                    optimizer_rejects['support_span'] += 1
                    continue
                center_x = center_qx * quantized_grid_m
                center_y = center_qy * quantized_grid_m
                center_z = _surface_z_at(
                    center_x, center_y, sum(source_z) / len(source_z))
                if center_z is None or max(
                        abs(float(center_z) - value) for value in source_z
                        ) > max_support_delta:
                    optimizer_rejects['center_support'] += 1
                    continue
                required_clearance = max(
                    float(GENERATOR_QUANTIZED_CENTER_CLEARANCE),
                    target_radius * NODE_RADIUS_CLEARANCE_FACTOR)
                _walk_t0 = _time.perf_counter()
                promotion_legal = walkable(
                    center_x, center_y, required_clearance,
                    surface_z=center_z)
                grow_walkable_sec += _time.perf_counter() - _walk_t0
                if (not promotion_legal
                        or not _quantized_final_candidate_ok(
                            center_x, center_y, float(center_z), target_b15)):
                    optimizer_rejects['collision_or_clearance'] += 1
                    continue
                for leaf_id in owner_ids:
                    del active_leaves[leaf_id]
                new_item = {
                    'x': center_x, 'y': center_y,
                    'surface_z': float(center_z), 'b15': target_b15,
                    'source_cells': len(present_keys),
                    'source_keys': frozenset(present_keys),
                }
                new_leaf_id = next_leaf_id
                next_leaf_id += 1
                active_leaves[new_leaf_id] = new_item
                for source_key in present_keys:
                    source_owner[source_key] = new_leaf_id
                accepted_target_points.add((center_qx, center_qy))
                optimizer_counts[target_b15] += 1

        packed_nodes = list(active_leaves.values())
        owned_after = frozenset().union(
            *(item['source_keys'] for item in packed_nodes)) if packed_nodes else frozenset()
        if owned_after != admitted_source_keys or len(source_owner) != len(admitted_source_keys):
            raise RuntimeError('quantized optimizer changed source-cell coverage')
        partial_promotion_counts.update(optimizer_counts)
        promotion_counts.update(optimizer_counts)
        quantized_optimizer_metrics = {
            'frozen_b32': len(frozen_b32),
            'exact_repack': {
                'b16': repacked_family_counts[16],
                'b8': repacked_family_counts[8],
            },
            'proof_replacements': {
                'b16': optimizer_counts[16],
                'b8': optimizer_counts[8],
                'b32': optimizer_counts[32],
            },
            'source_cells_before': len(admitted_source_keys),
            'source_cells_after': len(owned_after),
            'rejects': dict(sorted(optimizer_rejects.items())),
        }
        _progress(
            45.8, 'Optimizing quantized radius families',
            f'Exact repack b16={repacked_family_counts[16]}, '
            f'b8={repacked_family_counts[8]}; proof replacements '
            f'b8={optimizer_counts[8]}, '
            f'b16={optimizer_counts[16]}, b32={optimizer_counts[32]}; '
            f'{len(admitted_source_keys)} source cells preserved exactly')
        quantized_base_candidate_count = len(lattice_candidates)
        quantized_promotion_metrics = dict(sorted(promotion_counts.items()))
        quantized_partial_promotion_metrics = dict(
            sorted(partial_promotion_counts.items()))

        _place_t0 = _time.perf_counter()
        for item in sorted(packed_nodes,
                           key=lambda n: (n['surface_z'], n['y'], n['x'], n['b15'])):
            b15 = int(item['b15'])
            node_index = add_node_raw(
                item['x'], item['y'], b15, (1 if b15 <= 8 else 0),
                'quantized_hierarchy', f'quantized_b{b15}',
                surface_z=item['surface_z'])
            if node_index is not None:
                nodes[node_index]['quantized_source_keys'] = item['source_keys']
        grow_placement_sec += _time.perf_counter() - _place_t0
        _progress(
            46, 'Promoting quantized radius families',
            f'Hierarchy complete: {len(nodes)} survivors from '
            f'{len(lattice_candidates)} base candidates; promotions '
            f'b8={promotion_counts[8]}, b16={promotion_counts[16]}, '
            f'b32={promotion_counts[32]}')

    # A single depth-first walk ends when its stack drains, which happens long
    # before max_nodes_soft whenever the seed starts in an enclosed space: the
    # walk fills the interior, never randomly finds the doorway, and the rest
    # of the disc is left bare for a 34-node repair budget to cover.  Every
    # cell in reachable_layers already passed the <=0.35 m step flood from the
    # seed, so it is connected navigation space -- re-seed the same walker
    # there rather than treating a drained stack as "finished".
    walker_regrow_seeds = 0
    _regrow_limit = int(globals().get('GENERATOR_WALKER_REGROW_SEEDS', 64))
    _regrow_targets = None
    _regrow_cursor = 0

    def _regrow_build_targets():
        def _target_order(item):
            tx, ty, tz = item
            dx = float(tx) - float(cx)
            dy = float(ty) - float(cy)
            # The old (Z,Y,X) scan exhausted all 64 restarts along the bottom
            # edge of large seeds.  A radius-stable order makes every larger
            # seed retain the same inner recovery priority instead of creating
            # a new directional patch.  Z is deliberately after XY so layered
            # jobs do not spend the complete restart budget on one floor.
            return (
                round(dx * dx + dy * dy, 6),
                round((math.atan2(dy, dx) + math.tau) % math.tau, 9),
                round(float(tz), 3),
                round(float(ty), 3),
                round(float(tx), 3))

        layers = getattr(z_at, 'reachable_layers', None) if callable(z_at) else None
        origin = getattr(z_at, 'grid_origin', None) if callable(z_at) else None
        if not layers or origin is None:
            # The automatic Z/interior filter is optional in the editor.  The
            # old recovery path silently disappeared when it was off, leaving
            # one depth-first branch to represent the entire seed disc.  Build
            # the same deterministic 1 m recovery lattice from the ordinary
            # surface callback so constrained-only seeds can restart too.
            ordered = []
            stride_m = 1.0
            ix0 = int(math.floor((cx - R) / stride_m))
            ix1 = int(math.ceil((cx + R) / stride_m))
            iy0 = int(math.floor((cy - R) / stride_m))
            iy1 = int(math.ceil((cy + R) / stride_m))
            for ix in range(ix0, ix1 + 1):
                tx = ix * stride_m
                for iy in range(iy0, iy1 + 1):
                    ty = iy * stride_m
                    if (math.hypot(tx - cx, ty - cy)
                            > _active_growth_radius - 0.75):
                        continue
                    tz = _surface_z_at(tx, ty)
                    if tz is None:
                        continue
                    ordered.append((
                        round(float(tx), 3),
                        round(float(ty), 3),
                        round(float(tz), 3)))
            ordered.sort(key=_target_order)
            return ordered
        gox, goy = origin
        gcell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25)
        stride = max(1, int(round(1.0 / gcell)))
        ordered = []
        for (gix, giy), values in layers.items():
            if gix % stride or giy % stride:
                continue
            tx = gox + gix * gcell
            ty = goy + giy * gcell
            if (math.hypot(tx - cx, ty - cy)
                    > _active_growth_radius - 0.75):
                continue
            for value in values:
                ordered.append((
                    round(float(tx), 3),
                    round(float(ty), 3),
                    round(float(value), 3)))
        ordered.sort(key=_target_order)
        return ordered

    def _regrow_strip_width(tx, ty, surface_z, cap=8.0):
        layers = getattr(z_at, 'reachable_layers', None) if callable(z_at) else None
        origin = getattr(z_at, 'grid_origin', None) if callable(z_at) else None
        if not layers or origin is None:
            return cap
        gox, goy = origin
        gcell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25)

        def _sup(px, py):
            for v in layers.get((int(round((px - gox) / gcell)),
                                 int(round((py - goy) / gcell))), ()):
                if abs(float(v) - float(surface_z)) <= 0.35:
                    return True
            return False

        narrowest = cap
        for axis_i in range(6):
            angle = math.pi * axis_i / 6.0
            nx = -math.sin(angle)
            ny = math.cos(angle)
            forward = 0.0
            while forward < cap and _sup(tx + nx * (forward + gcell),
                                         ty + ny * (forward + gcell)):
                forward += gcell
            backward = 0.0
            while backward < cap and _sup(tx - nx * (backward + gcell),
                                          ty - ny * (backward + gcell)):
                backward += gcell
            narrowest = min(narrowest, forward + backward + gcell)
        return narrowest

    def _regrow_uncovered(tx, ty, surface_z):
        # A corridor gets one lane.  Cells beside an existing lane in a narrow
        # strip are uncovered by radius but are NOT a place to start a second
        # chain -- re-seeding there is what produced parallel rows in corridors.
        # Re-seeding is for support that nothing is working yet.
        _narrow = float(globals().get('GENERATOR_REGROW_MIN_WIDTH', 3.5))
        _lane_exists = False
        for px, py, ni in node_grid.query(tx, ty, 6.0):
            other = nodes[ni]
            if abs(float(other['z']) - 1.2489 - float(surface_z)) > 0.50:
                continue
            distance = math.hypot(float(px) - tx, float(py) - ty)
            if distance <= radius_m_local(other['b15']):
                _trace_provenance_record(
                    'regrow_rejected', tx, ty,
                    reason='covered_by_node', existing_index=int(ni),
                    existing_b15=int(other['b15']),
                    distance=round(float(distance), 4))
                return False
            if distance <= 4.0:
                _lane_exists = True
        for px, py, pi in prior_grid.query(tx, ty, 6.0):
            other = prior_nodes[pi]
            if abs(float(other['z']) - 1.2489 - float(surface_z)) > 0.50:
                continue
            distance = math.hypot(float(px) - tx, float(py) - ty)
            if distance <= radius_m_local(other['b15']):
                _trace_provenance_record(
                    'regrow_rejected', tx, ty,
                    reason='covered_by_prior_node', existing_index=int(pi),
                    existing_b15=int(other['b15']),
                    distance=round(float(distance), 4))
                return False
            if distance <= 4.0:
                _lane_exists = True
        # Collision corridor classification is the expensive part of a restart
        # probe.  Most lattice targets are already covered, and returned above;
        # only classify an uncovered target when a nearby lane makes the
        # narrow-strip decision relevant.
        _regrow_model_supported = _model_supported_at(tx, ty, surface_z)
        _hard_lane = (
            _hard_corridor_lane_at(tx, ty, surface_z)
            if _lane_exists else None)
        if (_lane_exists and _hard_lane is not None
                and (_regrow_model_supported or not globals().get(
                    'GENERATOR_EXTERIOR_CORRIDOR_GAP_FILL', True))):
            # Keep the established interior/platform policy.  The new centered
            # gap completion is for collision corridors on exterior terrain;
            # interior stairs and landing topology already have their own
            # support-aware generation.
            _trace_provenance_record(
                'regrow_rejected', tx, ty,
                reason='collision_corridor_existing_lane',
                axis_degrees=round(float(
                    _hard_lane['axis_degrees']), 4),
                width=round(float(_hard_lane['width']), 4))
            return False
        _strip_width = (
            _regrow_strip_width(tx, ty, surface_z)
            if _lane_exists and _hard_lane is None else None)
        if (_lane_exists and _hard_lane is None
                and _strip_width < _narrow):
            _trace_provenance_record(
                'regrow_rejected', tx, ty,
                reason='narrow_strip_existing_lane',
                strip_width=round(float(_strip_width), 4),
                minimum_width=round(float(_narrow), 4))
            return False
        _trace_provenance_record(
            'regrow_offered', tx, ty,
            lane_exists=bool(_lane_exists),
            strip_width=(
                None if _strip_width is None
                else round(float(_strip_width), 4)))
        return True

    def _regrow_seed_walker():
        nonlocal _regrow_targets, _regrow_cursor, walker_regrow_seeds
        if quantize_to_map_grid or walker_regrow_seeds >= _regrow_limit:
            return False
        if _regrow_targets is None:
            _regrow_targets = _regrow_build_targets()
        while _regrow_cursor < len(_regrow_targets):
            tx, ty, tz = _regrow_targets[_regrow_cursor]
            _regrow_cursor += 1
            if not _regrow_uncovered(tx, ty, tz):
                continue
            _target_hard_lane = (
                None if _model_supported_at(tx, ty, tz)
                else _hard_corridor_lane_at(tx, ty, tz))
            if _target_hard_lane is not None:
                tx = float(_target_hard_lane['x'])
                ty = float(_target_hard_lane['y'])
                tz = _surface_z_at(tx, ty, tz)
                if tz is None:
                    continue
                if not _regrow_uncovered(tx, ty, tz):
                    continue
            if not walkable(tx, ty, 0.62, surface_z=tz):
                continue
            # Continue away from the nearest same-floor part of the graph.
            # Previously every fallback seed had no heading and therefore all
            # restarted on the map X axis, stamping the same triangular patch
            # repeatedly across large jobs.
            _regrow_heading = None
            _regrow_nearest_d2 = float('inf')
            for px, py, pi in node_grid.query(tx, ty, 8.0):
                other = nodes[pi]
                if abs(float(other['z']) - 1.2489 - float(tz)) > 0.50:
                    continue
                dx = float(tx) - float(px)
                dy = float(ty) - float(py)
                d2 = dx * dx + dy * dy
                if d2 < _regrow_nearest_d2 and d2 > 1.0e-8:
                    _regrow_nearest_d2 = d2
                    _regrow_heading = math.degrees(math.atan2(dy, dx))
            if _regrow_heading is None:
                _regrow_heading = math.degrees(math.atan2(ty - cy, tx - cx))
            # _regrow_uncovered() has already proved that this same-floor
            # centre lies outside every existing acceptance disc.  Reapplying
            # ordinary outdoor texture spacing here creates an impossible
            # annulus: the point is uncovered, yet the restart is rejected as
            # too close.  Coverage admission resolves that contradiction while
            # retaining collision, support, final pair-distance and corridor
            # centring gates.  This is shared by terrain-only and layered
            # providers; Automatic Z does not select a different generator.
            if add_normal(
                    tx, ty, 'walker_regrow', surface_z=tz,
                    heading=_regrow_heading,
                    spacing_mode='coverage'):
                stack.append(len(nodes) - 1)
                walker_regrow_seeds += 1
                return True
        return False

    def _advance_growth_frontier():
        """Open the next ring and resume from the completed inner boundary."""
        nonlocal _growth_phase_index, _active_growth_radius
        nonlocal _regrow_targets, _regrow_cursor, walker_regrow_seeds
        if quantize_to_map_grid:
            return False
        next_index = _growth_phase_index + 1
        if next_index >= len(_growth_phase_radii):
            return False
        previous_radius = float(_active_growth_radius)
        _growth_phase_index = next_index
        _active_growth_radius = _growth_phase_radii[next_index]
        _regrow_targets = None
        _regrow_cursor = 0
        walker_regrow_seeds = 0

        # The completed inner phase has drained ``stack``.  Merely increasing
        # in_disc() here used to open the outer area with no live growth
        # frontier, so almost the entire outer ring was manufactured later by
        # the expensive coverage-repair lattice.  Besides the cost, that made
        # large seeds look like a collection of local patches rather than one
        # continuous graph.
        #
        # Re-arm the nodes that actually reached the previous radial boundary.
        # Their retained headings continue the same branches outward; no new
        # seed, geometry transaction, grid origin, or alternate Z generator is
        # introduced.  A band slightly wider than the largest ordinary step
        # keeps obstructed/corridor boundary branches available as well.
        boundary_band = max(5.0, min(8.0, previous_radius * 0.20))
        boundary_floor = max(0.0, previous_radius - boundary_band)
        boundary_nodes = []
        for node_index, node in enumerate(nodes):
            radial_distance = math.hypot(
                float(node['x']) - cx, float(node['y']) - cy)
            if boundary_floor <= radial_distance <= previous_radius + 1.0e-6:
                boundary_nodes.append((radial_distance, node_index))
        if not boundary_nodes and nodes:
            # Degenerate enclosed seeds may never reach the requested boundary.
            # Continue from their real outer envelope instead of falling back
            # immediately to the global repair lattice.
            outermost = max(
                math.hypot(float(node['x']) - cx, float(node['y']) - cy)
                for node in nodes)
            fallback_floor = max(0.0, outermost - boundary_band)
            for node_index, node in enumerate(nodes):
                radial_distance = math.hypot(
                    float(node['x']) - cx, float(node['y']) - cy)
                if radial_distance >= fallback_floor:
                    boundary_nodes.append((radial_distance, node_index))
        boundary_nodes.sort(key=lambda item: (item[0], item[1]))
        stack.extend(node_index for _distance, node_index in boundary_nodes)
        return True

    while len(nodes) < max_nodes_soft:
        if not stack:
            if not _regrow_seed_walker():
                if _advance_growth_frontier():
                    continue
                break
            continue
        ai = stack[-1]
        ax = nodes[ai]['x']; ay = nodes[ai]['y']
        parent_surface_z = float(nodes[ai]['z']) - 1.2489
        parent_model_supported = _model_supported_at(
            ax, ay, parent_surface_z)
        stable_outdoor_walk = (
            ordinary_outdoor_contract_enabled
            and not parent_model_supported)
        base_h = nodes[ai].get('heading')
        if base_h is None:
            # Ordinary outdoor growth must not change formation merely because
            # the clicked seed moved across a rounded RNG boundary.  Corridor
            # nodes receive their confirmed lane heading during admission; an
            # unheaded ordinary seed starts from the map X axis and the
            # existing irregular SCAN fan supplies the non-quantized branches.
            base_h = 0.0 if stable_outdoor_walk else rng.random()*360.0
        parent_corridor_axis = nodes[ai].get('corridor_axis')
        # Preserve the established forward/back corridor traversal exactly.
        # Perpendicular probes changed the deterministic walker order far beyond
        # the local exit and broke already-proven stair/landing topology.  The
        # recovery lattice below handles uncovered corridor-to-field regions
        # after a branch drains without perturbing the original walk.
        scan_offsets = (
            (0.0, 180.0) if parent_corridor_axis is not None else SCAN)
        heading_jitter = (
            3.0 if parent_corridor_axis is not None
            else (0.0 if stable_outdoor_walk else 16.0))
        accepted = False
        for off in scan_offsets:
            grow_candidate_checks += 1
            h = base_h + off
            if heading_jitter:
                h += (rng.random()-0.5)*heading_jitter
            rm_here = radius_m_local(nodes[ai]['b15'])
            if parent_corridor_axis is not None:
                step_factor = 1.40
            elif stable_outdoor_walk:
                step_factor = float(globals().get(
                    'GENERATOR_OUTDOOR_STEP_FACTOR', 1.50))
            else:
                step_factor = 1.66
            step = step_factor * rm_here
            if (parent_corridor_axis is not None
                    or not stable_outdoor_walk):
                step *= 0.94 + rng.random()*0.18
            step = max(step, 1.05)
            x = ax + math.cos(math.radians(h))*step
            y = ay + math.sin(math.radians(h))*step
            if hasattr(z_at, 'path_height'):
                candidate_surface_z = z_at.path_height(
                    ax, ay, parent_surface_z, x, y)
            else:
                candidate_surface_z = _surface_z_at(x, y, parent_surface_z)
            _walk_t0 = _time.perf_counter()
            _walkable_candidate = walkable(x,y,0.45,surface_z=candidate_surface_z)
            grow_walkable_sec += _time.perf_counter() - _walk_t0
            if not _walkable_candidate:
                _report_grower_progress()
                continue
            _place_t0 = _time.perf_counter()
            _added_candidate = add_normal(
                x,y,'walker',heading=h, surface_z=candidate_surface_z,
                corridor_hint=parent_corridor_axis,
                spacing_mode=(
                    'outdoor'
                    if stable_outdoor_walk and parent_corridor_axis is None
                    else None),
                parent_idx=ai)
            grow_placement_sec += _time.perf_counter() - _place_t0
            _report_grower_progress()
            if _added_candidate:
                stack.append(len(nodes)-1)
                accepted = True
                break
        if not accepted:
            stack.pop()
    _bounded_growth_active = False
    if not quantize_to_map_grid:
        _report_grower_progress(force=True)

    _progress(48, 'Repairing coverage gaps', 'Filling reachable holes left by the initial grower')
    repair_added, repair_log = _gen_coverage.repair_coverage_gaps(
        cx, cy, R, nodes, quantize_to_map_grid, rng_seed,
        walkable, coverage, nearest_hard,
        _surface_z_at, classify,
        _generated_parent_edge_covering_point,
        spacing_ok, add_node_raw, radius_m_local,
        NODE_RADIUS_CLEARANCE_FACTOR)

    _progress(58, 'Resolving floors and stairs', 'Adding valid layered, stair, and rooftop support')
    # Multi-layer support pass:
    # - preserves lower rooms beneath overlapping upper floors;
    # - deliberately samples stair/ramp transition cells that the broad 2D
    #   walker can skip;
    # - optionally fills disconnected topmost roof components selected by the
    #   generator rooftop option.
    layered_added = 0
    layered_stair_added = 0
    layered_stair_sample_count = 0
    layered_stair_candidate_count = 0
    layered_stair_false_floor_nodes_dominated = 0
    transition_flight_count = 0
    transition_lane_count = 0
    transition_flight_ranges = []
    transition_slope_sec = 0.0
    transition_merge_metrics = {
        'raw_flights': 0,
        'coalesced_flights': 0,
        'fragments_merged': 0,
    }
    transition_network_state_count = 0
    transition_network_floor_candidate_count = 0
    layered_floor_added = 0
    rooftop_added = 0
    reachable_layers = getattr(z_at, 'reachable_layers', {}) if callable(z_at) else {}
    model_layers = getattr(z_at, 'model_layers', {}) if callable(z_at) else {}
    geometry_stair_layers = getattr(
        z_at, 'geometry_stair_layers', {}) if callable(z_at) else {}
    tunnel_stair_layers = getattr(
        z_at, 'tunnel_stair_layers', {}) if callable(z_at) else {}
    roof_layers = getattr(z_at, 'roof_layers', {}) if callable(z_at) else {}
    grid_origin = getattr(z_at, 'grid_origin', None) if callable(z_at) else None
    grid_cell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25) if callable(z_at) else 0.25

    def _layer_in(mapping, key, surface_z, tolerance=0.08):
        return any(
            abs(float(value) - float(surface_z)) <= float(tolerance)
            for value in mapping.get(key, ()))

    def _geometry_stair_layer_key(x, y, surface_z):
        if grid_origin is None:
            return None
        ix = int(round((float(x) - float(grid_origin[0])) / grid_cell))
        iy = int(round((float(y) - float(grid_origin[1])) / grid_cell))
        key = (ix, iy)
        return key if _layer_in(
            geometry_stair_layers, key, surface_z) else None

    def _dominate_false_floor_over_stair(x, y, surface_z, stair_b15):
        """Let verified stair treads replace a generated crossing floor sheet.

        Terrain can cut horizontally through an authored basement staircase.
        The ordinary walker then occupies that false floor first and spacing
        rejects the one transition row needed to join the basement to the
        building.  Prior AIN nodes and same/adjacent-tread nodes remain absolute;
        only provisional generated walkers more than one ordinary step away in Z
        are superseded.
        """
        if _geometry_stair_layer_key(x, y, surface_z) is None:
            return 0
        candidate_z = float(surface_z) + NAV_NODE_Z_LIFT
        candidate_radius = radius_m_local(stair_b15)
        dominated = 0
        for px, py, node_index in node_grid.query(x, y, 1.25):
            if node_index in corridor_dominated:
                continue
            blocker = nodes[node_index]
            if blocker.get('tag') not in ('walker', 'walker_regrow'):
                continue
            if blocker.get('family') not in (
                    'standard', 'wall_squeezed', 'open_field', 'corridor_fit'):
                continue
            blocker_surface_z = float(blocker['z']) - NAV_NODE_Z_LIFT
            blocker_radius = radius_m_local(blocker['b15'])
            required = max(
                0.50, 0.35 * (candidate_radius + blocker_radius) * 0.88)
            blocker_distance = math.hypot(
                float(px) - float(x), float(py) - float(y))
            if blocker_distance >= required:
                continue

            support_delta = abs(blocker_surface_z - float(surface_z))
            # Verified stair topology must win over a provisional ordinary
            # walker that happened to occupy the same authored tread first.
            # Which ordinary walker reaches a tread is seed/order dependent;
            # allowing it to consume the spacing slot can delete one stair row
            # and break an otherwise valid floor -> stair -> floor chain.
            # Restrict this replacement to generated walker nodes whose OWN
            # support is independently present in geometry_stair_layers.
            blocker_is_verified_stair_support = (
                _geometry_stair_layer_key(
                    float(px), float(py), blocker_surface_z) is not None)
            if (support_delta <= GENERATOR_REACHABLE_MAX_STEP + 1e-6
                    and blocker_is_verified_stair_support):
                corridor_dominated.add(node_index)
                dominated += 1
                continue

            # Preserve the older false-crossing-floor repair: a provisional
            # walker on a nearby but genuinely different support sheet can also
            # suppress a verified stair row.
            if support_delta <= GENERATOR_REACHABLE_MAX_STEP + 1e-6:
                continue
            if abs(float(blocker['z']) - candidate_z) > 1.80:
                continue
            corridor_dominated.add(node_index)
            dominated += 1
        return dominated

    def _transition_step_limit(first_key, first_z, second_key, second_z):
        if (
            _layer_in(tunnel_stair_layers, first_key, first_z)
            or _layer_in(tunnel_stair_layers, second_key, second_z)
        ):
            return GENERATOR_REACHABLE_STAIR_STEP
        return GENERATOR_REACHABLE_MAX_STEP

    def _transition_slope_candidates(samples):
        """Cover coherent stepped ascents as temporary width-aware slopes.

        The support raster still follows the real treads and remains the sole
        source of final node heights. This pass uses the direction of repeated
        support-height changes to separate individual flights, fits a temporary
        slope through each flight, measures its usable cross-section, and packs
        one or more longitudinal lanes across it. Every packed point is then
        snapped back onto reachable model support; no temporary surface escapes
        this function or becomes collision.
        """
        if not samples:
            return []

        from collections import defaultdict as _defaultdict, deque as _deque
        import statistics as _statistics

        neighbor_offsets = (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        )

        def _model_values_near(ix, iy, surface_z):
            key = (int(ix), int(iy))
            values = []
            for value in reachable_layers.get(key, ()):
                value = float(value)
                if not _layer_in(model_layers, key, value):
                    continue
                delta = abs(value - float(surface_z))
                if 0.05 < delta <= (
                    _transition_step_limit(
                        key, surface_z, key, value) + 1e-6
                ):
                    values.append(value)
            return values

        # A lower neighbour behind the cell and a higher neighbour ahead both
        # vote in the same upward XY direction because dz remains signed.
        directed_samples = []
        directions = []
        for ix, iy, surface_z in samples:
            gx = 0.0
            gy = 0.0
            for dx, dy in neighbor_offsets:
                values = _model_values_near(ix + dx, iy + dy, surface_z)
                if not values:
                    continue
                other_z = min(
                    values,
                    key=lambda value: abs(float(value) - float(surface_z)))
                dz = float(other_z) - float(surface_z)
                distance = math.hypot(dx, dy)
                gx += float(dx) * dz / distance
                gy += float(dy) * dz / distance
            magnitude = math.hypot(gx, gy)
            if magnitude <= 1e-8:
                continue
            directed_samples.append((int(ix), int(iy), float(surface_z)))
            directions.append((gx / magnitude, gy / magnitude))

        if not directed_samples:
            return []

        by_cell = _defaultdict(list)
        for sample_index, sample in enumerate(directed_samples):
            by_cell[(sample[0], sample[1])].append(sample_index)

        # Direction-aware components prevent opposing switchback flights from
        # becoming one centre corridor. The tolerance absorbs raster stepping.
        direction_cosine = math.cos(math.radians(55.0))
        unseen = set(range(len(directed_samples)))
        oriented_components = []
        while unseen:
            root = min(unseen)
            unseen.remove(root)
            component = [root]
            queue = _deque([root])
            root_direction = directions[root]
            while queue:
                current = queue.popleft()
                ix, iy, surface_z = directed_samples[current]
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        if dx == 0 and dy == 0:
                            continue
                        for other in by_cell.get((ix + dx, iy + dy), ()):
                            if other not in unseen:
                                continue
                            other_z = float(directed_samples[other][2])
                            if abs(other_z - float(surface_z)) > (
                                _transition_step_limit(
                                    (ix, iy), surface_z,
                                    (ix + dx, iy + dy), other_z)
                                + 1e-6
                            ):
                                continue
                            other_direction = directions[other]
                            if (
                                    root_direction[0] * other_direction[0]
                                    + root_direction[1] * other_direction[1]
                                    < direction_cosine):
                                continue
                            unseen.remove(other)
                            component.append(other)
                            queue.append(other)
            oriented_components.append(component)

        def _mean_direction(indices):
            ux = sum(directions[index][0] for index in indices)
            uy = sum(directions[index][1] for index in indices)
            magnitude = math.hypot(ux, uy)
            if magnitude <= 1e-8:
                return None
            return (ux / magnitude, uy / magnitude)

        def _fit(indices, direction):
            ux, uy = direction
            points = [
                (origin_x + directed_samples[index][0] * grid_cell,
                 origin_y + directed_samples[index][1] * grid_cell,
                 float(directed_samples[index][2]))
                for index in indices
            ]
            mean_x = sum(point[0] for point in points) / len(points)
            mean_y = sum(point[1] for point in points) / len(points)
            s_values = [
                (point[0] - mean_x) * ux + (point[1] - mean_y) * uy
                for point in points
            ]
            z_values = [point[2] for point in points]
            mean_s = sum(s_values) / len(s_values)
            mean_z = sum(z_values) / len(z_values)
            denominator = sum((value - mean_s) ** 2 for value in s_values)
            if denominator <= 1e-10:
                return None
            slope = sum(
                (s_value - mean_s) * (z_value - mean_z)
                for s_value, z_value in zip(s_values, z_values)
            ) / denominator
            predictions = [
                mean_z + slope * (s_value - mean_s)
                for s_value in s_values
            ]
            squared_error = sum(
                (z_value - prediction) ** 2
                for z_value, prediction in zip(z_values, predictions))
            total_variance = sum((z_value - mean_z) ** 2 for z_value in z_values)
            r_squared = (
                1.0 - squared_error / total_variance
                if total_variance > 1e-10 else 0.0)
            rmse = math.sqrt(squared_error / len(points))
            return {
                'mean_x': mean_x,
                'mean_y': mean_y,
                'mean_s': mean_s,
                'mean_z': mean_z,
                'slope': slope,
                'r_squared': r_squared,
                'rmse': rmse,
            }

        def _local_slope(indices, direction):
            ux, uy = direction
            members = set(indices)
            estimates = []
            for index in indices:
                ix, iy, surface_z = directed_samples[index]
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        if dx == 0 and dy == 0:
                            continue
                        for other in by_cell.get((ix + dx, iy + dy), ()):
                            if other not in members or other <= index:
                                continue
                            other_z = float(directed_samples[other][2])
                            dz = other_z - float(surface_z)
                            ds = grid_cell * (
                                float(dx) * ux + float(dy) * uy)
                            if abs(dz) <= 0.05 or abs(ds) <= 0.10:
                                continue
                            estimate = dz / ds
                            if 0.08 <= estimate <= 2.0:
                                estimates.append(estimate)
            return _statistics.median(estimates) if estimates else None

        def _spatial_subcomponents(indices):
            members = set(indices)
            remaining = set(indices)
            result = []
            while remaining:
                root = min(remaining)
                remaining.remove(root)
                component = [root]
                queue = _deque([root])
                while queue:
                    current = queue.popleft()
                    ix, iy, surface_z = directed_samples[current]
                    for dx in range(-2, 3):
                        for dy in range(-2, 3):
                            if dx == 0 and dy == 0:
                                continue
                            for other in by_cell.get((ix + dx, iy + dy), ()):
                                if other not in remaining or other not in members:
                                    continue
                                other_z = float(directed_samples[other][2])
                                if abs(other_z - float(surface_z)) > (
                                    _transition_step_limit(
                                        (ix, iy), surface_z,
                                        (ix + dx, iy + dy), other_z)
                                    + 1e-6
                                ):
                                    continue
                                remaining.remove(other)
                                component.append(other)
                                queue.append(other)
                result.append(component)
            return result

        # Parallel flights can share an upward direction and touch through a
        # landing. A poor single-plane fit is split by local-slope intercept.
        flights = []
        flight_source_ids = []
        for source_id, component in enumerate(oriented_components):
            if len(component) < 8:
                continue
            direction = _mean_direction(component)
            if direction is None:
                continue
            fit = _fit(component, direction)
            if fit is None:
                continue
            z_values = [
                float(directed_samples[index][2]) for index in component]
            z_span = max(z_values) - min(z_values)
            if (
                    z_span >= 0.95
                    and 0.08 <= fit['slope'] <= 2.0
                    and fit['r_squared'] >= 0.70
                    and fit['rmse'] <= 0.28):
                flights.append(component)
                flight_source_ids.append(source_id)
                continue

            slope = _local_slope(component, direction)
            if slope is None:
                continue
            ux, uy = direction
            intercept_by_index = {}
            for index in component:
                ix, iy, surface_z = directed_samples[index]
                x = origin_x + ix * grid_cell
                y = origin_y + iy * grid_cell
                intercept = float(surface_z) - slope * (x * ux + y * uy)
                intercept_by_index[index] = intercept

            # Stair treads smear each fitted intercept by roughly one riser, so
            # a simple "split at a large gap" fails when several stacked flights
            # touch through landings. Repeatedly take the densest 0.44 m
            # intercept window, validate its spatial pieces, and remove it.
            remaining = set(component)
            while len(remaining) >= 8:
                intercept_bins = _defaultdict(set)
                for index in remaining:
                    bin_key = int(round(intercept_by_index[index] / 0.10))
                    intercept_bins[bin_key].add(index)
                best_group = set()
                for bin_key in intercept_bins:
                    group = set()
                    for neighbor_bin in range(bin_key - 2, bin_key + 3):
                        group.update(intercept_bins.get(neighbor_bin, ()))
                    if len(group) > len(best_group):
                        best_group = group
                if len(best_group) < 8:
                    break
                remaining.difference_update(best_group)
                for subgroup in _spatial_subcomponents(best_group):
                    if len(subgroup) < 8:
                        continue
                    subgroup_direction = _mean_direction(subgroup)
                    if subgroup_direction is None:
                        continue
                    subgroup_fit = _fit(subgroup, subgroup_direction)
                    if subgroup_fit is None:
                        continue
                    subgroup_z = [
                        float(directed_samples[index][2])
                        for index in subgroup
                    ]
                    if (
                            max(subgroup_z) - min(subgroup_z) >= 0.95
                            and 0.08 <= subgroup_fit['slope'] <= 2.0
                            and subgroup_fit['r_squared'] >= 0.62
                            and subgroup_fit['rmse'] <= 0.32):
                        flights.append(subgroup)
                        flight_source_ids.append(source_id)

        # One physical wide flight can be fragmented into several parallel
        # intercept subgroups because transition samples live mainly along
        # tread boundaries. Packing each subgroup independently creates a
        # large-radius chain plus extra small-radius chains in its gaps. Merge
        # only side-by-side fragments originating from the same oriented
        # component, with strongly overlapping climb extents and height spans.
        transition_merge_metrics['raw_flights'] = len(flights)
        if len(flights) > 1:
            descriptors = []
            for flight in flights:
                direction = _mean_direction(flight)
                fit = _fit(flight, direction) if direction is not None else None
                if direction is None or fit is None:
                    descriptors.append(None)
                    continue
                ux, uy = direction
                points = [
                    (origin_x + directed_samples[index][0] * grid_cell,
                     origin_y + directed_samples[index][1] * grid_cell,
                     float(directed_samples[index][2]))
                    for index in flight
                ]
                along = [point[0]*ux + point[1]*uy for point in points]
                across = [-point[0]*uy + point[1]*ux for point in points]
                descriptors.append({
                    'direction': direction,
                    'slope': float(fit['slope']),
                    'along': (min(along), max(along)),
                    'across_center': sum(across) / len(across),
                    'center': (
                        sum(point[0] for point in points) / len(points),
                        sum(point[1] for point in points) / len(points)),
                    'bbox': (
                        min(point[0] for point in points),
                        max(point[0] for point in points),
                        min(point[1] for point in points),
                        max(point[1] for point in points)),
                    'z': (
                        min(point[2] for point in points),
                        max(point[2] for point in points)),
                    'points': points,
                    'cells': {
                        (directed_samples[index][0],
                         directed_samples[index][1])
                        for index in flight
                    },
                })
            transition_merge_metrics['raw_details'] = [
                (None if descriptor is None else {
                    'flight': int(index),
                    'direction': (
                        round(float(descriptor['direction'][0]), 4),
                        round(float(descriptor['direction'][1]), 4)),
                    'slope': round(float(descriptor['slope']), 4),
                    'center': tuple(
                        round(float(value), 3)
                        for value in descriptor['center']),
                    'bbox': tuple(
                        round(float(value), 3)
                        for value in descriptor['bbox']),
                    'z': tuple(
                        round(float(value), 3)
                        for value in descriptor['z']),
                })
                for index, descriptor in enumerate(descriptors)
            ]

            parents = list(range(len(flights)))

            def _find_parent(index):
                while parents[index] != index:
                    parents[index] = parents[parents[index]]
                    index = parents[index]
                return index

            def _union(first, second):
                first_root = _find_parent(first)
                second_root = _find_parent(second)
                if first_root != second_root:
                    parents[second_root] = first_root

            def _fragment_pair(first, second):
                a = descriptors[first]
                b = descriptors[second]
                if a is None or b is None:
                    return False
                direction_dot = (
                    a['direction'][0]*b['direction'][0]
                    + a['direction'][1]*b['direction'][1])
                # A one-cell-wide tread-edge fragment has a noisy gradient
                # direction (the MCOLYMPS side strips deviate by about 40
                # degrees from the actual climb axis). Height/extent overlap
                # and direct spatial adjacency below provide the stronger
                # safeguards against merging a different staircase.
                if direction_dot < math.cos(math.radians(45.0)):
                    return False
                if abs(a['slope'] - b['slope']) > 0.35:
                    return False
                z_overlap = min(a['z'][1], b['z'][1]) - max(
                    a['z'][0], b['z'][0])
                min_z_span = min(
                    a['z'][1]-a['z'][0], b['z'][1]-b['z'][0])
                if z_overlap < 0.65 * max(0.01, min_z_span):
                    return False
                common_x = (
                    a['direction'][0] + b['direction'][0])
                common_y = (
                    a['direction'][1] + b['direction'][1])
                common_magnitude = math.hypot(common_x, common_y)
                if common_magnitude <= 1e-8:
                    return False
                common_x /= common_magnitude
                common_y /= common_magnitude
                a_along_values = [
                    point[0]*common_x + point[1]*common_y
                    for point in a['points']]
                b_along_values = [
                    point[0]*common_x + point[1]*common_y
                    for point in b['points']]
                a_along = (min(a_along_values), max(a_along_values))
                b_along = (min(b_along_values), max(b_along_values))
                along_overlap = min(
                    a_along[1], b_along[1]) - max(
                    a_along[0], b_along[0])
                min_along_span = min(
                    a_along[1]-a_along[0],
                    b_along[1]-b_along[0])
                if along_overlap < 0.60 * max(0.01, min_along_span):
                    return False
                a_across = sum(
                    -point[0]*common_y + point[1]*common_x
                    for point in a['points']) / len(a['points'])
                b_across = sum(
                    -point[0]*common_y + point[1]*common_x
                    for point in b['points']) / len(b['points'])
                if abs(a_across - b_across) > 3.0:
                    return False
                max_cell_distance_sq = int(
                    round(1.25 / grid_cell)) ** 2
                for ax, ay in a['cells']:
                    for bx, by in b['cells']:
                        dx = int(ax) - int(bx)
                        dy = int(ay) - int(by)
                        if dx*dx + dy*dy <= max_cell_distance_sq:
                            return True
                return False

            for first in range(len(flights)):
                for second in range(first+1, len(flights)):
                    if _fragment_pair(first, second):
                        _union(first, second)
            merged_by_root = _defaultdict(list)
            for index in range(len(flights)):
                merged_by_root[_find_parent(index)].append(index)
            coalesced = []
            for indices in merged_by_root.values():
                merged = set()
                for index in indices:
                    merged.update(flights[index])
                coalesced.append(sorted(merged))
            transition_merge_metrics['fragments_merged'] = (
                len(flights) - len(coalesced))
            flights = coalesced
        transition_merge_metrics['coalesced_flights'] = len(flights)

        selected = []
        used_support = set()
        flight_id = 0

        def _trimmed_extent(values):
            ordered = sorted(float(value) for value in values)
            if len(ordered) >= 12:
                trim = max(1, int(len(ordered) * 0.05))
                ordered = ordered[trim:-trim] or ordered
            return ordered[0], ordered[-1]

        def _snap_model_support(target_x, target_y, predicted_z):
            target_ix = int(round((target_x - origin_x) / grid_cell))
            target_iy = int(round((target_y - origin_y) / grid_cell))
            best = None
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    ix = target_ix + dx
                    iy = target_iy + dy
                    key = (ix, iy)
                    x = origin_x + ix * grid_cell
                    y = origin_y + iy * grid_cell
                    planar_distance = math.hypot(x - target_x, y - target_y)
                    for surface_z in reachable_layers.get(key, ()):
                        surface_z = float(surface_z)
                        if not _layer_in(model_layers, key, surface_z):
                            continue
                        z_error = abs(surface_z - float(predicted_z))
                        if z_error > 0.48:
                            continue
                        score = planar_distance + 0.80 * z_error
                        if best is None or score < best[0]:
                            best = (score, ix, iy, surface_z, x, y)
            return best

        for flight in sorted(
                flights,
                key=lambda indices: (
                    min(float(directed_samples[index][2]) for index in indices),
                    min(directed_samples[index][0] for index in indices),
                    min(directed_samples[index][1] for index in indices))):
            direction = _mean_direction(flight)
            fit = _fit(flight, direction) if direction is not None else None
            if direction is None or fit is None:
                continue
            ux, uy = direction
            vx, vy = -uy, ux
            mean_x = fit['mean_x']
            mean_y = fit['mean_y']
            point_records = []
            for index in flight:
                ix, iy, surface_z = directed_samples[index]
                x = origin_x + ix * grid_cell
                y = origin_y + iy * grid_cell
                s_value = (x - mean_x) * ux + (y - mean_y) * uy
                t_value = (x - mean_x) * vx + (y - mean_y) * vy
                point_records.append((s_value, t_value, float(surface_z)))
            s_min = min(record[0] for record in point_records)
            s_max = max(record[0] for record in point_records)
            flight_length = s_max - s_min
            if flight_length < 0.70:
                continue

            width_bins = _defaultdict(list)
            for s_value, t_value, _surface_z in point_records:
                width_bins[int(round((s_value - s_min) / 0.50))].append(
                    t_value)
            widths = []
            for values in width_bins.values():
                if len(values) < 3:
                    continue
                low, high = _trimmed_extent(values)
                widths.append(max(grid_cell, high - low + grid_cell))
            if not widths:
                low, high = _trimmed_extent(
                    [record[1] for record in point_records])
                widths = [max(grid_cell, high - low + grid_cell)]
            widths.sort()
            width_index = int(round(0.25 * (len(widths) - 1)))
            usable_width = max(grid_cell, widths[width_index])

            # b15/16 is the displayed acceptance radius. One lane covers at
            # most a two-metre strip at b16; wider support gains more lanes.
            lane_count = max(
                1, int(math.ceil(max(0.0, usable_width - 0.10) / 2.0)))
            nominal_lane_width = usable_width / lane_count
            nominal_b15 = max(
                6, min(16, int(round(nominal_lane_width * 8.0))))
            nominal_radius = nominal_b15 / 16.0
            # The minimum longitudinal pitch is 0.75 m. A radius below b15=6
            # has a diameter smaller than that pitch and necessarily leaves a
            # visible coverage gap even when placement is correct. b15=6 is
            # also the smallest authored stair family observed on the tight
            # MCTarget switchbacks; keep narrow flights at that covered family
            # instead of adding more nodes at b15=4/5.
            longitudinal_pitch = max(
                0.75, min(2.0, 2.0 * nominal_radius))
            row_count = max(
                2, int(math.ceil(flight_length / longitudinal_pitch)) + 1)

            for row_index in range(row_count):
                amount = row_index / (row_count - 1)
                target_s = s_min + (s_max - s_min) * amount
                cross_window = max(0.55, longitudinal_pitch * 0.55)
                cross_values = [
                    record[1] for record in point_records
                    if abs(record[0] - target_s) <= cross_window
                ]
                if len(cross_values) < 3:
                    cross_values = [
                        record[1] for record in sorted(
                            point_records,
                            key=lambda record: abs(record[0] - target_s))[:12]
                    ]
                cross_low, cross_high = _trimmed_extent(cross_values)
                local_width = max(
                    grid_cell, cross_high - cross_low + grid_cell)
                local_lane_width = local_width / lane_count
                # Radius is a property of the usable flight/lane, not of one
                # raster row. Sparse tread samples at an edge or landing can
                # make a single cross-section look artificially narrow and
                # used to create isolated b15=4/6 nodes between much larger
                # neighbours. The flight-wide width is already the lower
                # quartile of valid cross-sections, so it is conservative
                # enough to use unchanged along every row.
                local_b15 = nominal_b15

                for lane_index in range(lane_count):
                    target_t = (
                        cross_low - grid_cell * 0.5
                        + (lane_index + 0.5) * local_lane_width)
                    target_x = mean_x + target_s * ux + target_t * vx
                    target_y = mean_y + target_s * uy + target_t * vy
                    predicted_z = (
                        fit['mean_z']
                        + fit['slope'] * (target_s - fit['mean_s']))
                    snapped = _snap_model_support(
                        target_x, target_y, predicted_z)
                    if snapped is None:
                        continue
                    _score, ix, iy, surface_z, x, y = snapped
                    support_key = (
                        int(ix), int(iy),
                        int(round(float(surface_z) / 0.05)))
                    if support_key in used_support:
                        continue
                    used_support.add(support_key)
                    selected.append((
                        'layered_stair',
                        float(x), float(y), float(surface_z),
                        int(local_b15), int(flight_id),
                        int(lane_index), int(row_index),
                    ))
            flight_id += 1

        return selected

    def _transition_network_floor_candidates(samples):
        """Tile flat model support belonging to a real vertical network.

        A stair chain is incomplete without its landings, between-flight
        corridors, and terminal balcony.  Those surfaces can be single-layer
        model support, so the legacy ``is_multilayer`` gate silently skipped
        them even though the reachability flood had proved them accessible.

        Starting only from detected model transitions, find their complete
        step-connected model component.  Components must span at least 0.75 m
        vertically; this excludes isolated lips and ordinary flat platforms.
        Cells close to the transition raster remain owned by the sparse stair
        corridor, while the remaining flat support is tiled at roughly 1 m.
        """
        if not samples or not model_layers:
            return [], 0

        from collections import defaultdict as _defaultdict, deque as _deque

        state_values = {}
        states_by_cell = _defaultdict(list)
        for (ix, iy), values in model_layers.items():
            for value_index, value in enumerate(values):
                state = (int(ix), int(iy), int(value_index))
                state_values[state] = float(value)
                states_by_cell[(int(ix), int(iy))].append(state)

        seed_states = set()
        transition_z_by_cell = _defaultdict(list)
        for ix, iy, surface_z in samples:
            transition_z_by_cell[(int(ix), int(iy))].append(float(surface_z))
            for state in states_by_cell.get((int(ix), int(iy)), ()):
                if abs(state_values[state] - float(surface_z)) <= 0.08:
                    seed_states.add(state)

        def neighbors(state):
            ix, iy, _value_index = state
            surface_z = state_values[state]
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),
                           (1,1),(1,-1),(-1,1),(-1,-1)):
                for other in states_by_cell.get((ix+dx, iy+dy), ()):
                    if abs(state_values[other] - surface_z) <= (
                        _transition_step_limit(
                            (ix, iy), surface_z,
                            (ix + dx, iy + dy), state_values[other])
                        + 1e-6
                    ):
                        yield other

        accepted_states = set()
        visited = set()
        for seed_state in sorted(seed_states):
            if seed_state in visited:
                continue
            component = {seed_state}
            visited.add(seed_state)
            queue = _deque([seed_state])
            while queue:
                current = queue.popleft()
                for other in neighbors(current):
                    if other in visited:
                        continue
                    visited.add(other)
                    component.add(other)
                    queue.append(other)
            component_z = [state_values[state] for state in component]
            if (len(component) >= 16
                    and max(component_z) - min(component_z) >= 0.75):
                accepted_states.update(component)

        def near_transition(ix, iy, surface_z, radius_cells=2):
            radius_cells = max(0, int(radius_cells))
            for dx in range(-radius_cells, radius_cells + 1):
                for dy in range(-radius_cells, radius_cells + 1):
                    if any(
                            abs(float(other_z) - float(surface_z)) <= 0.50
                            for other_z in transition_z_by_cell.get(
                                (ix+dx, iy+dy), ())):
                        return True
            return False

        tile_span = max(1, int(round(1.0 / grid_cell)))
        tiles = {}
        for state in accepted_states:
            ix, iy, _value_index = state
            surface_z = state_values[state]
            if near_transition(ix, iy, surface_z):
                continue
            tile_key = (
                int(math.floor(ix / tile_span)),
                int(math.floor(iy / tile_span)),
                int(round(surface_z / 0.25)),
            )
            # Critical landing/corridor ownership is a property of map
            # geometry, not of where the user happened to click the generator.
            # Rank cells with a stable world-space hash only; never rng_seed.
            _mask64 = 0xffffffffffffffff
            _iz = int(round(float(surface_z) / 0.05))
            score = (
                ((int(ix) & _mask64) * 0x9e3779b185ebca87)
                ^ ((int(iy) & _mask64) * 0xc2b2ae3d27d4eb4f)
                ^ ((_iz & _mask64) * 0x165667b19e3779f9)
                ^ 0x53544149
            ) & _mask64
            score ^= score >> 30
            score = (score * 0xbf58476d1ce4e5b9) & _mask64
            score ^= score >> 27
            score = (score * 0x94d049bb133111eb) & _mask64
            score ^= score >> 31
            # Only the landing/corridor cells close to a real vertical
            # transition need the wall-aware anti-suppression rule. Farther
            # transition-network floor fabric keeps ordinary spacing cost.
            critical_transition_floor = near_transition(
                ix, iy, surface_z,
                radius_cells=max(3, int(round(2.0 / grid_cell))))
            candidate = (
                'layered_floor',
                origin_x + ix * grid_cell,
                origin_y + iy * grid_cell,
                float(surface_z),
                ('transition_network_floor_critical'
                 if critical_transition_floor
                 else 'transition_network_floor'),
            )
            prior = tiles.get(tile_key)
            if prior is None or score < prior[0]:
                tiles[tile_key] = (score, candidate)

        def already_covered(candidate):
            _tag, x, y, surface_z = candidate[:4]
            critical = (
                len(candidate) >= 5
                and candidate[4] == 'transition_network_floor_critical')
            node_z = float(surface_z) + 1.2489
            for px, py, node_index in node_grid.query(x, y, 5.0):
                node = nodes[node_index]
                if abs(float(node['z']) - node_z) > 1.0:
                    continue
                if math.hypot(float(px)-x, float(py)-y) <= (
                        radius_m_local(node['b15']) + 0.85):
                    if (not critical
                            or _transition_floor_direct_route_ok(
                                x, y, surface_z, px, py, node.get('z'))):
                        return True
            for px, py, prior_index in prior_grid.query(x, y, 5.0):
                prior = prior_nodes[prior_index]
                if abs(float(prior['z']) - node_z) > 1.0:
                    continue
                if math.hypot(float(px)-x, float(py)-y) <= (
                        radius_m_local(prior['b15']) + 0.85):
                    if (not critical
                            or _transition_floor_direct_route_ok(
                                x, y, surface_z, px, py, prior.get('z'))):
                        return True
            return False

        # Explicitly sort the tile winners. ``accepted_states`` is a set, so
        # relying on dict insertion order here would still make critical
        # landing order depend on process/hash traversal. The score and final
        # tie-break are entirely map anchored.
        ranked_tiles = sorted(
            tiles.values(),
            key=lambda entry: (
                int(entry[0]),
                round(float(entry[1][3]), 4),
                round(float(entry[1][2]), 4),
                round(float(entry[1][1]), 4)))
        candidates = [
            entry[1] for entry in ranked_tiles
            if not already_covered(entry[1])
        ]
        return candidates, len(accepted_states)

    if reachable_layers and grid_origin is not None:
        origin_x, origin_y = grid_origin
        layered_candidates = []
        transition_samples = []
        platform_candidate_tiles = {}
        # This pass can only emit candidates backed by a model or roof layer.
        # Iterating the entire reachable terrain raster made large open-field
        # seeds pay for tens of thousands of cells which immediately failed
        # the ownership test below.  Restricting the iteration to owned keys is
        # output-equivalent: keys outside this set cannot satisfy any of the
        # three admission conditions.
        layered_active_keys = (
            set(model_layers.keys()) | set(roof_layers.keys())
        ) & set(reachable_layers.keys())
        for ix, iy in sorted(layered_active_keys):
            layer_values = reachable_layers[(ix, iy)]
            is_multilayer = len(layer_values) > 1
            for surface_z in layer_values:
                layer_key = (ix, iy)
                is_model_layer = _layer_in(model_layers, layer_key, surface_z)
                is_roof_layer = _layer_in(roof_layers, layer_key, surface_z)
                transition = False
                # The natural walker already handles terrain gradients.  The
                # old test called every 5-35 cm terrain change a stair and laid
                # a rigid 0.25 m candidate carpet across hills and platforms.
                # Dense transition connectors belong only to model support.
                if is_model_layer:
                    for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),
                                   (1,1),(1,-1),(-1,1),(-1,-1)):
                        neighbor_key = (ix+dx, iy+dy)
                        neighbor_values = reachable_layers.get(neighbor_key, ())
                        if any(
                                _layer_in(model_layers, neighbor_key, other_z)
                                and 0.05 < abs(
                                    float(other_z)-float(surface_z)) <= (
                                        _transition_step_limit(
                                            layer_key, surface_z,
                                            neighbor_key, other_z)
                                        + 1e-6)
                                for other_z in neighbor_values):
                            transition = True
                            break
                # A second projected layer does not make the terrain beneath a
                # deck another floor-placement target.  The terrain walker
                # already owns terrain; this pass owns actual model layers.
                if not (is_roof_layer or transition
                        or (is_multilayer and is_model_layer)):
                    continue
                stride = 1 if (transition or quantize_to_map_grid) else 4
                if ix % stride or iy % stride:
                    if not is_roof_layer:
                        continue
                x = origin_x + ix * grid_cell
                y = origin_y + iy * grid_cell
                tag = (
                    'layered_stair' if transition
                    else ('rooftop' if is_roof_layer else 'layered_floor')
                )
                if transition:
                    transition_samples.append((ix, iy, float(surface_z)))
                    continue
                candidate = (tag, x, y, float(surface_z))
                if is_roof_layer and not transition:
                    # One support-backed candidate per roughly 1 m tile keeps
                    # the old bounded workload, but choose its 0.25 m cell by a
                    # deterministic spatial hash instead of always taking the
                    # same map-grid corner.
                    tile_span = max(1, int(round(1.0 / grid_cell)))
                    tile_key = (
                        int(math.floor(ix / tile_span)),
                        int(math.floor(iy / tile_span)),
                        int(round(float(surface_z) / 0.10)),
                    )
                    candidate_score = (
                        (int(ix) * 73856093)
                        ^ (int(iy) * 19349663)
                        ^ int(rng_seed)
                    ) & 0xffffffff
                    prior = platform_candidate_tiles.get(tile_key)
                    if prior is None or candidate_score < prior[0]:
                        platform_candidate_tiles[tile_key] = (candidate_score, candidate)
                else:
                    layered_candidates.append(candidate)

        # A sorted support raster makes platform nodes follow scan lines.  Use
        # a dedicated deterministic shuffle for broad elevated surfaces while
        # leaving stairs and interior-floor ordering unchanged.
        platform_candidates = [item[1] for item in platform_candidate_tiles.values()]
        transition_slope_t0 = _time.perf_counter()
        transition_slope_candidates = _transition_slope_candidates(
            transition_samples)
        transition_slope_sec = _time.perf_counter() - transition_slope_t0
        (transition_network_floor_candidates,
         transition_network_state_count) = _transition_network_floor_candidates(
            transition_samples)
        layered_stair_sample_count = len(transition_samples)
        layered_stair_candidate_count = len(transition_slope_candidates)
        if transition_slope_candidates:
            transition_flight_count = 1 + max(
                int(candidate[5]) for candidate in transition_slope_candidates)
            transition_lane_count = len({
                (int(candidate[5]), int(candidate[6]))
                for candidate in transition_slope_candidates
            })
            for flight_index in range(transition_flight_count):
                flight_candidates = [
                    candidate for candidate in transition_slope_candidates
                    if int(candidate[5]) == flight_index
                ]
                if not flight_candidates:
                    continue
                transition_flight_ranges.append({
                    'flight': int(flight_index),
                    'nodes': len(flight_candidates),
                    'lanes': 1 + max(
                        int(candidate[6]) for candidate in flight_candidates),
                    'min_z': min(
                        float(candidate[3]) for candidate in flight_candidates),
                    'max_z': max(
                        float(candidate[3]) for candidate in flight_candidates),
                })
        transition_network_floor_candidate_count = len(
            transition_network_floor_candidates)
        # Establish the real flat landings/corridors before packing the
        # temporary-slope rows.  Putting slope candidates first lets an
        # endpoint stair node occupy a landing, reject the proper floor node
        # on spacing, and leave the climb attached through a long forced spur
        # instead of through the balcony/corridor fabric.
        other_layered_candidates = (
            transition_network_floor_candidates
            + transition_slope_candidates
            + layered_candidates
        )
        random.Random(rng_seed ^ 0x504C4154).shuffle(platform_candidates)
        for candidate in platform_candidates + other_layered_candidates:
            tag, x, y, surface_z = candidate[:4]
            transition = tag == 'layered_stair'
            is_roof_layer = tag == 'rooftop'
            is_transition_floor = (
                len(candidate) >= 5
                and candidate[4] == 'transition_network_floor_critical')
            if transition:
                # Width-aware slope packing selected this radius and snapped the
                # centre back to real reachable model support. Re-running radial
                # wall clearance would still mistake tread risers for walls.
                stair_b15 = int(candidate[4]) if len(candidate) >= 5 else 12
                layered_stair_false_floor_nodes_dominated += (
                    _dominate_false_floor_over_stair(
                        x, y, surface_z, stair_b15))
                spacing_allowed, _spacing_info = spacing_ok(
                    x, y, stair_b15, 'stair', surface_z=surface_z)
                if not spacing_allowed:
                    reject['spacing'] += 1
                    continue
                stair_index = add_node_raw(
                    x, y, stair_b15, 1, tag, 'stair_transition',
                    surface_z=surface_z, quantize_xy=False)
                if stair_index is not None:
                    if len(candidate) >= 8:
                        nodes[stair_index]['stair_flight'] = int(candidate[5])
                        nodes[stair_index]['stair_lane'] = int(candidate[6])
                        nodes[stair_index]['stair_row'] = int(candidate[7])
                    layered_added += 1
                    layered_stair_added += 1
                continue
            placement_mode = (
                'stair' if transition else
                ('transition_floor' if is_transition_floor else
                 ('lattice' if quantize_to_map_grid else
                  ('platform' if is_roof_layer else 'normal')))
            )
            # A platform is an ordinary open navigation surface.  Do not force
            # it into the small-radius multilayer family merely because terrain
            # happens to exist beneath the same XY cell.
            b15_cap = 8 if transition else (None if is_roof_layer else 16)
            if add_normal(
                    x, y, tag,
                    surface_z=surface_z,
                    mode=placement_mode,
                    b15_cap=b15_cap,
                    min_clearance=(
                        0.20 if transition else
                        (GENERATOR_QUANTIZED_CENTER_CLEARANCE
                         if quantize_to_map_grid else 0.62)),
                    fallback_b15=(4 if transition else None)):
                layered_added += 1
                if transition:
                    layered_stair_added += 1
                elif is_roof_layer:
                    rooftop_added += 1
                else:
                    layered_floor_added += 1

    # Coverage completion.  Measured over 88,262 authored campaign nodes, the
    # gap between linked acceptance discs has median exactly 0.000 m and 62.9%
    # of linked pairs overlap: continuous radius coverage of the navigable
    # surface is a designed property of the authored graphs, not texture.  The
    # earlier repair pass cannot express that -- it stops at 34 nodes however
    # much surface is bare.  Walk every reachable support cell that no
    # same-layer disc covers and offer it to the ordinary admission path, so
    # anything still uncovered afterwards carries a concrete clearance,
    # standing-volume or spacing reason instead of an exhausted quota.
    coverage_fill_added = 0
    coverage_fill_offered = 0
    coverage_fill_rejects = Counter()
    if (not quantize_to_map_grid
            and globals().get('GENERATOR_COVERAGE_FILL', True)):
        # One generator, one completion policy.  Automatic Z changes only the
        # support surfaces offered to this stage.  With Z enabled, consume the
        # existing flooded terrain/model layers.  With Z disabled, expose the
        # ordinary exterior surface through the same map-anchored layer
        # contract without building the expensive layered raster.
        _cov_layers = (
            getattr(z_at, 'reachable_layers', None)
            if callable(z_at) else None)
        _cov_origin = (
            getattr(z_at, 'grid_origin', None)
            if callable(z_at) else None)
        if not _cov_layers or _cov_origin is None:
            _terrain_cov_cell = float(globals().get(
                'GENERATOR_TERRAIN_COVERAGE_CELL', 0.50))
            _terrain_cov_cell = max(0.25, _terrain_cov_cell)
            _cov_origin = (0.0, 0.0)
            _cov_layers = {}
            _terrain_ix0 = int(math.floor((cx - R) / _terrain_cov_cell))
            _terrain_ix1 = int(math.ceil((cx + R) / _terrain_cov_cell))
            _terrain_iy0 = int(math.floor((cy - R) / _terrain_cov_cell))
            _terrain_iy1 = int(math.ceil((cy + R) / _terrain_cov_cell))
            for _terrain_ix in range(_terrain_ix0, _terrain_ix1 + 1):
                _terrain_x = _terrain_ix * _terrain_cov_cell
                for _terrain_iy in range(_terrain_iy0, _terrain_iy1 + 1):
                    _terrain_y = _terrain_iy * _terrain_cov_cell
                    if math.hypot(_terrain_x - cx, _terrain_y - cy) > R - 0.5:
                        continue
                    _terrain_z = _surface_z_at(_terrain_x, _terrain_y)
                    if (_terrain_z is None
                            or not walkable(
                                _terrain_x, _terrain_y, 0.20,
                                surface_z=_terrain_z)):
                        continue
                    _cov_layers[(_terrain_ix, _terrain_iy)] = (
                        float(_terrain_z),)
            # The shared code below reads the provider's native cell size.
            class _TerrainCoverageProvider:
                pass
            _terrain_cov_provider = _TerrainCoverageProvider()
            _terrain_cov_provider.grid_cell = _terrain_cov_cell
            _terrain_cov_provider.grid_origin = _cov_origin
        else:
            _terrain_cov_provider = None
        if _cov_layers and _cov_origin is not None:
            _cov_ox, _cov_oy = _cov_origin
            _cov_cell = float(
                (getattr(_terrain_cov_provider, 'grid_cell', None)
                 if _terrain_cov_provider is not None
                 else getattr(z_at, 'grid_cell', 0.25)) or 0.25)
            _cov_stride = max(1, int(round(0.5 / _cov_cell)))
            _cov_targets = []
            for (_gix, _giy), _values in _cov_layers.items():
                if _gix % _cov_stride or _giy % _cov_stride:
                    continue
                _tx = _cov_ox + _gix * _cov_cell
                _ty = _cov_oy + _giy * _cov_cell
                if math.hypot(_tx - cx, _ty - cy) > R - 0.5:
                    continue
                for _value in _values:
                    _cov_targets.append(
                        (round(float(_value), 3), round(_ty, 3), round(_tx, 3)))
            # Never walk this raster in (Z, Y, X) order.  Coverage admission is
            # stateful: every accepted node changes the answer for the targets
            # that follow it.  A lexical walk therefore imprints scan bands and
            # increasingly obvious geometric patches as the seed grows.
            #
            # Give every map-space support cell a stable pseudo-random priority
            # instead.  The priority is anchored to fixed world coordinates,
            # not to the seed centre or radius, so an overlapping 32 m and 80 m
            # generation considers their shared cells in the same relative
            # order.  The final coordinate tuple is only a collision tie-break.
            def _coverage_map_priority(target):
                _pz, _py, _px = target
                _ix = int(round(_px * 4.0))
                _iy = int(round(_py * 4.0))
                _iz = int(round(_pz * 16.0))
                _mask64 = 0xffffffffffffffff
                _key = (
                    ((_ix & _mask64) * 0x9e3779b185ebca87)
                    ^ ((_iy & _mask64) * 0xc2b2ae3d27d4eb4f)
                    ^ ((_iz & _mask64) * 0x165667b19e3779f9)
                ) & _mask64
                _key ^= _key >> 30
                _key = (_key * 0xbf58476d1ce4e5b9) & _mask64
                _key ^= _key >> 27
                _key = (_key * 0x94d049bb133111eb) & _mask64
                _key ^= _key >> 31
                return (_key, _pz, _py, _px)

            _cov_targets.sort(key=_coverage_map_priority)

            # Exhaustive coverage is NOT the authored contract.  Measured with
            # this same Z-aware test, NovaLogic's own graphs cover only
            # 30.6-67.1% of the walkable support raster (RdioBld2 30.6, UNBld01
            # 45.6, RE_Bld1 53.8, SP_Bld1 58.8, MCTarget 67.1).  They cover
            # routes, not floor, and deliberately leave the fringe beside a
            # corridor wall bare.  Filling that fringe is what turns a clean
            # single lane into several parallel chains, because a narrow
            # corridor classifies to a small radius and then needs many nodes
            # to reach the walls.  The margin keeps the fill for real holes.
            _cov_margin = float(globals().get(
                'GENERATOR_COVERAGE_FILL_MARGIN', 0.20))

            def _coverage_hole(tx, ty, tz):
                node_world_z = float(tz) + 1.2489
                _point_cov_margin = _cov_margin
                if _model_supported_at(tx, ty, tz):
                    # Preserve the established interior/platform coverage
                    # texture. The stricter margin is for exterior terrain
                    # gaps, not model-supported floors.
                    _point_cov_margin = max(_point_cov_margin, 0.90)
                for _px, _py, _ni in node_grid.query(tx, ty, 6.5):
                    if _ni in corridor_dominated:
                        continue
                    _q = nodes[_ni]
                    if abs(float(_q['z']) - node_world_z) > 0.50:
                        continue
                    if math.hypot(float(_px) - tx, float(_py) - ty) <= (
                            radius_m_local(_q['b15']) + _point_cov_margin):
                        _trace_provenance_record(
                            'coverage_rejected', tx, ty,
                            reason='covered_by_generated_node',
                            existing_index=int(_ni),
                            existing_b15=int(_q['b15']),
                            distance=round(math.hypot(
                                float(_px) - tx, float(_py) - ty), 4))
                        return False
                for _px, _py, _pj in prior_grid.query(tx, ty, 6.5):
                    _q = prior_nodes[_pj]
                    if abs(float(_q['z']) - node_world_z) > 0.50:
                        continue
                    if math.hypot(float(_px) - tx, float(_py) - ty) <= (
                            radius_m_local(_q['b15']) + _point_cov_margin):
                        _trace_provenance_record(
                            'coverage_rejected', tx, ty,
                            reason='covered_by_prior_node',
                            existing_index=int(_pj),
                            existing_b15=int(_q['b15']),
                            distance=round(math.hypot(
                                float(_px) - tx, float(_py) - ty), 4))
                        return False
                return True

            # A corridor gets ONE lane.  The walker already lays a single clean
            # chain down a narrow strip; trying to also cover the floor out to
            # the walls forces extra parallel chains, because a narrow corridor
            # classifies to a small radius and one lane cannot reach both sides.
            # So the fill does not operate in narrow support at all -- it exists
            # for open rooms and fields, where a hole is a real hole.
            _cov_min_width = float(globals().get(
                'GENERATOR_COVERAGE_FILL_MIN_WIDTH', 3.5))

            def _cov_strip_width(tx, ty, tz, cap=8.0):
                def _sup(px, py):
                    for v in _cov_layers.get(
                            (int(round((px - _cov_ox) / _cov_cell)),
                             int(round((py - _cov_oy) / _cov_cell))), ()):
                        if abs(float(v) - float(tz)) <= 0.35:
                            return True
                    return False
                narrowest = cap
                for axis_i in range(6):
                    angle = math.pi * axis_i / 6.0
                    nx = -math.sin(angle)
                    ny = math.cos(angle)
                    forward = 0.0
                    while forward < cap and _sup(tx + nx * (forward + _cov_cell),
                                                 ty + ny * (forward + _cov_cell)):
                        forward += _cov_cell
                    backward = 0.0
                    while backward < cap and _sup(tx - nx * (backward + _cov_cell),
                                                  ty - ny * (backward + _cov_cell)):
                        backward += _cov_cell
                    narrowest = min(narrowest, forward + backward + _cov_cell)
                return narrowest

            _cov_budget = int(max_nodes_soft)
            for _cov_z, _cov_y, _cov_x in _cov_targets:
                if coverage_fill_added >= _cov_budget:
                    break
                if not _coverage_hole(_cov_x, _cov_y, _cov_z):
                    continue
                coverage_fill_offered += 1
                _cov_model_supported = _model_supported_at(
                    _cov_x, _cov_y, _cov_z)
                _cov_hard_lane = _hard_corridor_lane_at(
                    _cov_x, _cov_y, _cov_z)
                if (_cov_hard_lane is not None
                        and (_cov_model_supported or not globals().get(
                            'GENERATOR_EXTERIOR_CORRIDOR_GAP_FILL', True))):
                    coverage_fill_rejects[
                        'collision_corridor_reserved'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='collision_corridor_reserved',
                        axis_degrees=round(float(
                            _cov_hard_lane['axis_degrees']), 4),
                        width=round(float(
                            _cov_hard_lane['width']), 4))
                    continue
                if _cov_hard_lane is not None:
                    _cov_x = float(_cov_hard_lane['x'])
                    _cov_y = float(_cov_hard_lane['y'])
                    _cov_z = _surface_z_at(_cov_x, _cov_y, _cov_z)
                    if (_cov_z is None
                            or not _coverage_hole(
                                _cov_x, _cov_y, _cov_z)):
                        coverage_fill_rejects[
                            'corridor_center_covered'] += 1
                        _trace_provenance_record(
                            'coverage_rejected', _cov_x, _cov_y,
                            reason='corridor_center_covered',
                            axis_degrees=round(float(
                                _cov_hard_lane['axis_degrees']), 4),
                            width=round(float(
                                _cov_hard_lane['width']), 4))
                        continue
                    _cov_width = float(_cov_hard_lane['width'])
                else:
                    _cov_width = _cov_strip_width(
                        _cov_x, _cov_y, _cov_z)
                if (_cov_hard_lane is None
                        and _cov_width < _cov_min_width):
                    coverage_fill_rejects['corridor_keeps_one_lane'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='corridor_keeps_one_lane',
                        strip_width=round(float(_cov_width), 4),
                        minimum_width=round(float(_cov_min_width), 4))
                    continue
                _cb15, _cb12, _cfam = classify(_cov_x, _cov_y, _cov_z)
                if _cb15 <= 0:
                    coverage_fill_rejects['no_radius_fit'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='no_radius_fit',
                        strip_width=round(float(_cov_width), 4))
                    continue
                _creq = max(0.62,
                            radius_m_local(_cb15) * NODE_RADIUS_CLEARANCE_FACTOR)
                if not walkable(
                        _cov_x, _cov_y, _creq, surface_z=_cov_z):
                    coverage_fill_rejects['clearance'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='clearance',
                        b15=int(_cb15),
                        strip_width=round(float(_cov_width), 4),
                        required_clearance=round(float(_creq), 4),
                        nearest_hard=round(float(nearest_hard(
                            _cov_x, _cov_y, _cov_z)), 4))
                    continue
                _cok, _cinfo = spacing_ok(
                    _cov_x, _cov_y, _cb15, 'coverage', surface_z=_cov_z)
                if not _cok:
                    coverage_fill_rejects['spacing'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='spacing', b15=int(_cb15),
                        strip_width=round(float(_cov_width), 4),
                        spacing=_cinfo)
                    continue
                # Apply the final geometry gate here rather than letting a
                # doomed node occupy the hole: an accepted-then-culled fill
                # node suppresses its own neighbours through _coverage_hole
                # and leaves the surface emptier than before it was tried.
                if standing_volume_index is not None and not (
                        standing_volume_index.standing_clear(
                            _cov_x, _cov_y, _cov_z)):
                    coverage_fill_rejects['standing_volume'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='standing_volume', b15=int(_cb15),
                        strip_width=round(float(_cov_width), 4))
                    continue
                _cov_solid = solid_detail(_cov_x, _cov_y)
                if _cov_solid:
                    _cov_model_support = _model_supported_at(
                        _cov_x, _cov_y, _cov_z)
                    if not (height_aware_collision and _cov_model_support):
                        coverage_fill_rejects['inside_solid'] += 1
                        _trace_provenance_record(
                            'coverage_rejected', _cov_x, _cov_y,
                            reason='inside_solid', b15=int(_cb15),
                            strip_width=round(float(_cov_width), 4))
                        continue
                if nearest_hard(_cov_x, _cov_y, _cov_z) < max(
                        0.62, radius_m_local(_cb15)
                        * NODE_RADIUS_CLEARANCE_FACTOR):
                    coverage_fill_rejects['final_clearance'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='final_clearance', b15=int(_cb15),
                        strip_width=round(float(_cov_width), 4))
                    continue
                if add_normal(_cov_x, _cov_y, 'coverage_fill',
                              surface_z=_cov_z, spacing_mode='coverage'):
                    coverage_fill_added += 1
                else:
                    coverage_fill_rejects['admission'] += 1
                    _trace_provenance_record(
                        'coverage_rejected', _cov_x, _cov_y,
                        reason='admission', b15=int(_cb15),
                        strip_width=round(float(_cov_width), 4))

    if globals().get('GENERATOR_CORRIDOR_DOMINANCE', True):
        for corridor_i, corridor_node in enumerate(nodes):
            if corridor_node.get('family') != 'corridor_fit':
                continue
            for _px, _py, small_i in node_grid.query(
                    corridor_node['x'], corridor_node['y'], 3.20):
                if small_i == corridor_i or small_i in corridor_dominated:
                    continue
                small_node = nodes[small_i]
                if (small_node.get('tag') not in ('walker', 'walker_regrow')
                        or small_node.get('family') not in (
                            'standard', 'wall_squeezed', 'open_field')
                        or int(small_node.get('b15', 0)) > 16):
                    continue
                if _model_supported_at(
                        small_node['x'], small_node['y'],
                        float(small_node['z']) - 1.2489):
                    continue
                if _corridor_node_dominates_point(
                        corridor_node, small_node['x'], small_node['y'], small_node['z']):
                    corridor_dominated.add(small_i)

    _progress(67, 'Validating generated nodes', 'Rejecting nodes that fail final collision and radius gates')
    # Final hard rejection before graph build: no node center inside solid and
    # no acceptance-radius circle intersecting hard geometry.  This is the visual
    # lab gate, not a soft metric.
    _kept = []
    _dropped_radius_geometry = 0
    for _node_i, _n in enumerate(nodes):
        if _node_i in corridor_dominated:
            _dropped_radius_geometry += 1
            continue
        _rm = radius_m_local(_n['b15'])
        _surface_z = float(_n['z']) - 1.2489
        _solid_layers = solid_detail(_n['x'], _n['y'])
        _model_supported_node = _model_supported_at(
            _n['x'], _n['y'], _surface_z)
        # ``enclosed_mesh`` is a conservative 2D footprint fill.  It is useful
        # for closed models whose wall segments leak in plan view, but it is
        # not authoritative for a reachable terrain-floor room inside a
        # height-aware CModel (WareHse1 is the minimal counterexample).  Keep
        # the footprint gate unless the automatic-Z flood independently
        # reached this exact support layer and the real 3D standing column is
        # clear.  The lightweight Z-off terrain contract uses the same proof;
        # actual wall proximity remains rejected by the clearance gate below.
        _standing_clear = (
            standing_volume_index is None
            or standing_volume_index.standing_clear(
                _n['x'], _n['y'], _surface_z))
        _reachable_surface_node = False
        if (height_aware_collision
                and getattr(z_at, 'reachable_layers', None)
                and hasattr(z_at, 'height_near')):
            try:
                _reachable_surface_z = z_at.height_near(
                    float(_n['x']), float(_n['y']), float(_surface_z))
                _reachable_surface_node = (
                    _reachable_surface_z is not None
                    and abs(float(_reachable_surface_z) - _surface_z) <= 0.35)
            except Exception:
                _reachable_surface_node = False
        # ``filled_hull`` is produced by the same conservative plan-view
        # closure as ``enclosed_mesh``.  Some open ruins report both layers
        # over a real, reachable room floor.  Once automatic-Z has proved the
        # exact support layer and the height-aware standing column is clear,
        # neither conservative 2D label may veto that node.  Real 3D blockers
        # are still rejected by ``standing_clear``; Z-off never enters here.
        _conservative_2d_solid_layers = {
            'enclosed_mesh', 'filled_hull'
        }
        _reachable_3d_solid_bypass = (
            _standing_clear
            and _reachable_surface_node
            and bool(_solid_layers)
            and all(layer in _conservative_2d_solid_layers
                    for layer in _solid_layers))
        solid_ok = ((not _solid_layers)
                    or (height_aware_collision
                        and (_model_supported_node
                             or _reachable_3d_solid_bypass)))
        if quantize_to_map_grid:
            # The lattice admission pass already tests the final snapped
            # coordinate.  Keep the same center-clearance floor here so the
            # final validator cannot silently erase valid b15=4/8 nodes from
            # tight corridors after the packer has accepted them.
            _min_clearance = (0.20 if _n.get('tag') == 'layered_stair'
                              else GENERATOR_QUANTIZED_CENTER_CLEARANCE)
        else:
            _min_clearance = 0.20 if _n.get('tag') == 'layered_stair' else 0.62
        _final_radius_factor = NODE_RADIUS_CLEARANCE_FACTOR
        if _n.get('family') == 'corridor_fit':
            _final_radius_factor = float(globals().get(
                'GENERATOR_CORRIDOR_FINAL_FACTOR', 0.85))
        elif (ordinary_outdoor_contract_enabled
              and not quantize_to_map_grid and not _model_supported_node
              and _n.get('family') != 'stair_transition'):
            _final_radius_factor = OUTDOOR_ACCEPTANCE_CLEARANCE_FACTOR
        _required_clearance = max(_min_clearance, _rm * _final_radius_factor)
        _nh = nearest_hard(_n['x'], _n['y'], _surface_z)
        if not _standing_clear:
            reject['standing_volume'] += 1
        _verified_stair_support = (
            _n.get('tag') == 'layered_stair' and _model_supported_node)
        _kept_by_final_gate = (
            _standing_clear and solid_ok
            and (_verified_stair_support or _nh >= _required_clearance))
        if _debug_probe_contains(_n['x'], _n['y']):
            if _kept_by_final_gate:
                if _solid_layers and _model_supported_node:
                    _reason = 'kept_model_supported_inside_2d_solid'
                elif _solid_layers and _reachable_3d_solid_bypass:
                    _reason = 'kept_reachable_3d_clear_inside_enclosed_mesh'
                else:
                    _reason = 'kept_clearance_ok'
            elif not _standing_clear:
                _reason = 'dropped_standing_volume'
            elif not solid_ok:
                _reason = 'dropped_inside_solid'
            else:
                _reason = 'dropped_nearest_hard'
            if len(debug_probe_records) < int((debug_probe or {}).get('max_records', 160)):
                debug_probe_records.append({
                    'x': round(float(_n['x']), 3),
                    'y': round(float(_n['y']), 3),
                    'z': round(float(_n['z']), 3),
                    'b15': int(_n.get('b15', 0)),
                    'tag': str(_n.get('tag', '')),
                    'solid_layers_2d': list(_solid_layers),
                    'solid_gate_bypassed': bool(
                        _solid_layers and (
                            _model_supported_node
                            or _reachable_3d_solid_bypass)),
                    'reachable_surface_node': bool(_reachable_surface_node),
                    'standing_volume_clear': bool(_standing_clear),
                    'nearest_hard': round(float(_nh), 3),
                    'required_clearance': round(float(_required_clearance), 3),
                    'final_gate': 'kept' if _kept_by_final_gate else 'dropped',
                    'reason': _reason,
                })
        if _kept_by_final_gate:
            _kept.append(_n)
        else:
            _dropped_radius_geometry += 1
    nodes = _kept
    if corridor_dominated:
        reject['corridor_dominated_removed'] += len(corridor_dominated)
        corridor_dominated.clear()
    if _dropped_radius_geometry:
        reject['post_radius_geometry_cull'] += _dropped_radius_geometry

    # Final coverage relaxation.
    #
    # The explorer, restart walker and hole repair are deliberately local and
    # add-only.  They can therefore leave a valid pocket bare when an otherwise
    # useful candidate was rejected by spacing, and the final collision cull can
    # expose another pocket after all earlier repair has finished.  Repair the
    # *finished placement field* here, before radius refit and graph links exist.
    #
    # This first conservative pass only owns ordinary terrain nodes.  Confirmed
    # corridor lanes, stairs, transitions, layered/model-supported floors and
    # existing editor nodes are fixed obstacles/coverage.  A pocket is repaired
    # by the least invasive legal action: grow one radius family, move one
    # ordinary node by at most 0.50 m without losing unique coverage, then add a
    # node through the normal admission path.  Geometry/support sampling is done
    # once and all later trials use the cached sample field.
    coverage_relaxation_metrics = {
        'enabled': False,
        'sample_count': 0,
        'initial_missing_samples': 0,
        'final_missing_samples': 0,
        'initial_components': 0,
        'final_components': 0,
        'radius_growth_actions': 0,
        'move_actions': 0,
        'add_actions': 0,
        'actions': 0,
        'action_log': [],
        'seconds': 0.0,
    }
    _coverage_relaxation_reachable_layers = (
        getattr(z_at, 'reachable_layers', None) if callable(z_at) else None)
    if (not quantize_to_map_grid and nodes
            and not _coverage_relaxation_reachable_layers
            # Rejected experiment retained only for diagnostic comparison.
            # Unified support completion above is now the active path.
            and globals().get('GENERATOR_FINAL_COVERAGE_RELAXATION', False)):
        _progress(
            69, 'Relaxing final coverage',
            'Closing ordinary same-floor gaps without moving corridors, stairs or interiors')
        _relax_t0 = _time.perf_counter()
        _relax_step = float(globals().get(
            'GENERATOR_FINAL_COVERAGE_SAMPLE_STEP', 0.85))
        _relax_margin = float(globals().get(
            'GENERATOR_FINAL_COVERAGE_MARGIN', 0.20))
        _relax_max_actions = int(globals().get(
            'GENERATOR_FINAL_COVERAGE_MAX_ACTIONS',
            max(10, min(48, int(round(R * 0.75))))))
        _relax_max_rounds = int(globals().get(
            'GENERATOR_FINAL_COVERAGE_MAX_ROUNDS', 3))
        _relax_min_component = int(globals().get(
            'GENERATOR_FINAL_COVERAGE_MIN_COMPONENT_SAMPLES', 2))
        _relax_move_steps = tuple(float(v) for v in globals().get(
            'GENERATOR_FINAL_COVERAGE_MOVE_STEPS', (0.25, 0.50)))
        _relax_samples = []
        _relax_seen_samples = set()

        def _relax_offer_sample(sx, sy, surface_z):
            if surface_z is None:
                return
            sx = float(sx); sy = float(sy); surface_z = float(surface_z)
            if math.hypot(sx - cx, sy - cy) > R - 1.20:
                return
            key = (round(sx, 3), round(sy, 3), round(surface_z, 3))
            if key in _relax_seen_samples:
                return
            if _model_supported_at(sx, sy, surface_z):
                return
            if not walkable(sx, sy, 0.50, surface_z=surface_z):
                return
            if (standing_volume_index is not None
                    and not standing_volume_index.standing_clear(
                        sx, sy, surface_z)):
                return
            _relax_seen_samples.add(key)
            _relax_samples.append((sx, sy, surface_z, surface_z + 1.2489))

        # This first deployment is intentionally Z-off only. Z-on is the
        # interior/tunnel/layered path and already owns a separate exhaustive
        # support repair. Ordinary Z-off terrain has one support height per XY
        # position, so use a map-anchored lattice and cache it exactly once. The
        # reachable-layer branch is retained for a later separately validated
        # layered relaxation, but is excluded by the gate above today.
        _relax_layers = getattr(z_at, 'reachable_layers', None) if callable(z_at) else None
        _relax_origin = getattr(z_at, 'grid_origin', None) if callable(z_at) else None
        if _relax_layers and _relax_origin is not None:
            _rox, _roy = _relax_origin
            _rcell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25)
            _rstride = max(1, int(round(_relax_step / _rcell)))
            for (_rgx, _rgy), _rvalues in _relax_layers.items():
                if _rgx % _rstride or _rgy % _rstride:
                    continue
                _rsx = _rox + _rgx * _rcell
                _rsy = _roy + _rgy * _rcell
                for _rsz in _rvalues:
                    _relax_offer_sample(_rsx, _rsy, _rsz)
        else:
            _rix0 = int(math.floor((cx - R) / _relax_step))
            _rix1 = int(math.ceil((cx + R) / _relax_step))
            _riy0 = int(math.floor((cy - R) / _relax_step))
            _riy1 = int(math.ceil((cy + R) / _relax_step))
            for _rix in range(_rix0, _rix1 + 1):
                _rsx = _rix * _relax_step
                for _riy in range(_riy0, _riy1 + 1):
                    _rsy = _riy * _relax_step
                    _relax_offer_sample(
                        _rsx, _rsy, _surface_z_at(_rsx, _rsy))

        _relax_all_sample_grid = _V52Spatial(max(0.50, _relax_step))
        for _sample_i, _sample in enumerate(_relax_samples):
            _relax_all_sample_grid.insert(_sample[0], _sample[1], _sample_i)

        _relax_index = None
        _relax_max_radius = 0.0

        def _relax_rebuild_node_index():
            nonlocal _relax_index, _relax_max_radius, node_grid
            _relax_index = _V52Spatial(4.0)
            _relax_max_radius = 0.0
            for _ri, _rn in enumerate(prior_nodes):
                _rr = radius_m_local(_rn['b15'])
                _relax_index.insert(_rn['x'], _rn['y'], (-1 - _ri, _rr, _rn['z']))
                _relax_max_radius = max(_relax_max_radius, _rr)
            node_grid = _V52Spatial(5.5)
            for _ri, _rn in enumerate(nodes):
                _rr = radius_m_local(_rn['b15'])
                _relax_index.insert(_rn['x'], _rn['y'], (_ri, _rr, _rn['z']))
                node_grid.insert(_rn['x'], _rn['y'], _ri)
                _relax_max_radius = max(_relax_max_radius, _rr)

        def _relax_sample_coverage(sample, skip_index=None,
                                   override_index=None, override=None):
            sx, sy, _surface_z, node_height = sample
            covered = False
            nearest_residual = 999.0
            nearest_index = None
            query_radius = max(4.0, _relax_max_radius + 1.25)
            for _px, _py, _record in _relax_index.query(sx, sy, query_radius):
                _ni, _nr, _nz = _record
                if _ni == skip_index or _ni == override_index:
                    continue
                if abs(float(_nz) - node_height) > 0.75:
                    continue
                _distance = math.hypot(sx - _px, sy - _py)
                _residual = _distance - float(_nr)
                if _residual < nearest_residual:
                    nearest_residual = _residual
                    nearest_index = _ni
                if _residual <= _relax_margin:
                    covered = True
            if override is not None:
                _ox, _oy, _oradius, _oz = override
                if abs(float(_oz) - node_height) <= 0.75:
                    _distance = math.hypot(sx - _ox, sy - _oy)
                    _residual = _distance - float(_oradius)
                    if _residual < nearest_residual:
                        nearest_residual = _residual
                        nearest_index = override_index
                    if _residual <= _relax_margin:
                        covered = True
            return covered, nearest_residual, nearest_index

        def _relax_missing_components():
            missing = []
            for _si, _sample in enumerate(_relax_samples):
                _covered, _residual, _nearest = _relax_sample_coverage(_sample)
                if not _covered:
                    missing.append((_si, _residual, _nearest))
            if not missing:
                return [], []
            missing_set = {item[0] for item in missing}
            sample_grid = _V52Spatial(max(0.50, _relax_step))
            for _si in missing_set:
                _sample = _relax_samples[_si]
                sample_grid.insert(_sample[0], _sample[1], _si)
            components = []
            while missing_set:
                seed_i = min(missing_set)
                missing_set.remove(seed_i)
                queue = [seed_i]
                component = []
                while queue:
                    current_i = queue.pop()
                    component.append(current_i)
                    current = _relax_samples[current_i]
                    for _px, _py, neighbor_i in sample_grid.query(
                            current[0], current[1], _relax_step * 1.48):
                        if neighbor_i not in missing_set:
                            continue
                        neighbor = _relax_samples[neighbor_i]
                        if abs(neighbor[2] - current[2]) > 0.40:
                            continue
                        missing_set.remove(neighbor_i)
                        queue.append(neighbor_i)
                components.append(component)
            components.sort(key=lambda c: (-len(c), min(c)))
            return missing, components

        def _relax_node_is_fixed(index, allow_wall=False):
            n = nodes[index]
            if (n.get('family') in ('corridor_fit', 'stair_transition')
                    or n.get('corridor_axis') is not None
                    or n.get('tag') in ('layered_stair', 'layered_floor', 'rooftop')
                    or _model_supported_at(
                        n['x'], n['y'], float(n['z']) - 1.2489)):
                return True
            if not allow_wall and n.get('family') == 'wall_squeezed':
                return True
            return n.get('family') not in ('standard', 'open_field', 'wall_squeezed')

        def _relax_growth_legal(index, new_b15):
            n = nodes[index]
            surface_z = float(n['z']) - 1.2489
            new_radius = radius_m_local(new_b15)
            clearance = nearest_hard(n['x'], n['y'], surface_z)
            cap = int(math.floor(
                max(0.0, clearance) * 24.0 /
                max(1e-6, NODE_RADIUS_CLEARANCE_FACTOR) + 1e-6))
            if new_b15 > min(48, cap):
                return False
            for _px, _py, _record in _relax_index.query(
                    n['x'], n['y'], new_radius + _relax_max_radius + 0.25):
                _other_i, _other_r, _other_z = _record
                if _other_i == index or abs(float(_other_z) - float(n['z'])) > 0.75:
                    continue
                distance = math.hypot(n['x'] - _px, n['y'] - _py)
                old_radius = radius_m_local(n['b15'])
                old_depth = max(0.0, 0.56 * (old_radius + _other_r) - distance)
                new_depth = max(0.0, 0.56 * (new_radius + _other_r) - distance)
                if new_depth > old_depth + 0.05:
                    return False
            return True

        def _relax_try_growth(component):
            candidates = set()
            for _si in component:
                sx, sy, _sz, node_height = _relax_samples[_si]
                for _px, _py, _record in _relax_index.query(
                        sx, sy, _relax_max_radius + 2.0):
                    _ni, _nr, _nz = _record
                    if _ni >= 0 and abs(float(_nz) - node_height) <= 0.75:
                        candidates.add(_ni)
            best = None
            rungs = (8, 12, 16, 24, 36, 48)
            for _ni in sorted(candidates):
                if _relax_node_is_fixed(_ni, allow_wall=True):
                    continue
                n = nodes[_ni]
                larger = [r for r in rungs if r > int(n['b15'])]
                if not larger:
                    continue
                new_b15 = larger[0]
                if not _relax_growth_legal(_ni, new_b15):
                    continue
                old_radius = radius_m_local(n['b15'])
                new_radius = radius_m_local(new_b15)
                gain = 0
                for _si in component:
                    sample = _relax_samples[_si]
                    if abs(sample[3] - float(n['z'])) > 0.75:
                        continue
                    distance = math.hypot(sample[0] - n['x'], sample[1] - n['y'])
                    if distance > old_radius + _relax_margin and distance <= new_radius + _relax_margin:
                        gain += 1
                if gain <= 0:
                    continue
                key = (gain, -new_b15, -_ni)
                if best is None or key > best[0]:
                    best = (key, _ni, new_b15)
            if best is None:
                return False
            _key, _ni, new_b15 = best
            old_b15 = int(nodes[_ni]['b15'])
            nodes[_ni]['coverage_relaxation_original_b15'] = old_b15
            nodes[_ni]['b15'] = int(new_b15)
            nodes[_ni]['coverage_relaxation_grown'] = True
            coverage_relaxation_metrics['action_log'].append({
                'kind': 'grow',
                'node_index': int(_ni),
                'x': round(float(nodes[_ni]['x']), 4),
                'y': round(float(nodes[_ni]['y']), 4),
                'z': round(float(nodes[_ni]['z']), 4),
                'old_b15': old_b15,
                'new_b15': int(new_b15),
            })
            return True

        def _relax_try_move(component):
            centroid_x = sum(_relax_samples[i][0] for i in component) / len(component)
            centroid_y = sum(_relax_samples[i][1] for i in component) / len(component)
            centroid_z = sum(_relax_samples[i][2] for i in component) / len(component)
            candidates = set()
            for _si in component:
                sample = _relax_samples[_si]
                for _px, _py, _record in _relax_index.query(
                        sample[0], sample[1], _relax_max_radius + 2.0):
                    _ni, _nr, _nz = _record
                    if _ni >= 0 and abs(float(_nz) - sample[3]) <= 0.75:
                        candidates.add(_ni)
            best = None
            for _ni in sorted(candidates):
                if _relax_node_is_fixed(_ni):
                    continue
                n = nodes[_ni]
                surface_z = float(n['z']) - 1.2489
                radius = radius_m_local(n['b15'])
                if nearest_hard(n['x'], n['y'], surface_z) < max(2.50, radius + 0.50):
                    continue
                dx = centroid_x - float(n['x'])
                dy = centroid_y - float(n['y'])
                length = math.hypot(dx, dy)
                if length < 1e-6:
                    continue
                ux = dx / length; uy = dy / length
                for move_distance in _relax_move_steps:
                    tx = float(n['x']) + ux * move_distance
                    ty = float(n['y']) + uy * move_distance
                    if math.hypot(tx - cx, ty - cy) > R - 0.75:
                        continue
                    tz = _surface_z_at(tx, ty, surface_z)
                    if tz is None or abs(float(tz) - surface_z) > 0.35:
                        continue
                    if _model_supported_at(tx, ty, tz):
                        continue
                    required = max(0.62, radius * OUTDOOR_ACCEPTANCE_CLEARANCE_FACTOR)
                    if not walkable(tx, ty, required, surface_z=tz):
                        continue
                    if solid_detail(tx, ty):
                        continue
                    if (standing_volume_index is not None
                            and not standing_volume_index.standing_clear(tx, ty, tz)):
                        continue
                    overlap_worse = False
                    for _px, _py, _record in _relax_index.query(
                            tx, ty, radius + _relax_max_radius + 0.50):
                        _other_i, _other_r, _other_z = _record
                        if _other_i == _ni or abs(float(_other_z) - (float(tz) + 1.2489)) > 0.75:
                            continue
                        old_distance = math.hypot(float(n['x']) - _px, float(n['y']) - _py)
                        new_distance = math.hypot(tx - _px, ty - _py)
                        old_depth = max(0.0, 0.56 * (radius + _other_r) - old_distance)
                        new_depth = max(0.0, 0.56 * (radius + _other_r) - new_distance)
                        if new_depth > old_depth + 0.05:
                            overlap_worse = True
                            break
                    if overlap_worse:
                        continue
                    override = (tx, ty, radius, float(tz) + 1.2489)
                    gain = 0
                    loss = 0
                    local_sample_indices = set()
                    for _spx, _spy, _sample_i in _relax_all_sample_grid.query(
                            float(n['x']), float(n['y']),
                            radius + _relax_margin + _relax_step):
                        local_sample_indices.add(_sample_i)
                    for _spx, _spy, _sample_i in _relax_all_sample_grid.query(
                            tx, ty, radius + _relax_margin + _relax_step):
                        local_sample_indices.add(_sample_i)
                    for _sample_i in local_sample_indices:
                        _sample = _relax_samples[_sample_i]
                        old_covered, _old_residual, _old_nearest = _relax_sample_coverage(_sample)
                        new_covered, _new_residual, _new_nearest = _relax_sample_coverage(
                            _sample, skip_index=_ni,
                            override_index=_ni, override=override)
                        if not old_covered and new_covered:
                            gain += 1
                        elif old_covered and not new_covered:
                            loss += 1
                            if loss:
                                break
                    if loss or gain < 2:
                        continue
                    key = (gain, -move_distance, -_ni)
                    if best is None or key > best[0]:
                        best = (key, _ni, tx, ty, float(tz) + 1.2489)
            if best is None:
                return False
            _key, _ni, tx, ty, node_height = best
            n = nodes[_ni]
            old_x = float(n['x'])
            old_y = float(n['y'])
            n['coverage_relaxation_original_xy'] = (
                round(old_x, 4), round(old_y, 4))
            n['x'] = float(tx); n['y'] = float(ty); n['z'] = float(node_height)
            n['coverage_relaxation_moved'] = True
            coverage_relaxation_metrics['action_log'].append({
                'kind': 'move',
                'node_index': int(_ni),
                'old_x': round(old_x, 4),
                'old_y': round(old_y, 4),
                'new_x': round(float(tx), 4),
                'new_y': round(float(ty), 4),
                'z': round(float(node_height), 4),
                'distance': round(math.hypot(float(tx) - old_x,
                                             float(ty) - old_y), 4),
            })
            return True

        def _relax_try_add(component):
            ranked = []
            for _si in component:
                sample = _relax_samples[_si]
                _covered, residual, _nearest = _relax_sample_coverage(sample)
                hard_clearance = nearest_hard(sample[0], sample[1], sample[2])
                ranked.append((residual, hard_clearance, -_si, _si))
            ranked.sort(reverse=True)
            for _residual, hard_clearance, _neg_i, _si in ranked[:12]:
                sample = _relax_samples[_si]
                # Preserve clean single-lane corridors.  Geometry-side pockets
                # remain visible in the metrics but are not filled by this first
                # ordinary-field pass.
                if hard_clearance < 2.50:
                    continue
                # This is hole repair, not frontier growth. Require nearby
                # same-floor nodes on substantially different sides of the
                # offered point. A one-sided cluster is an unfinished frontier
                # and remains the walker's responsibility.
                neighbor_angles = []
                for _px, _py, _record in _relax_index.query(
                        sample[0], sample[1], 7.0):
                    _neighbor_i, _neighbor_r, _neighbor_z = _record
                    if abs(float(_neighbor_z) - sample[3]) > 0.75:
                        continue
                    _nd = math.hypot(sample[0] - _px, sample[1] - _py)
                    if _nd < 0.20:
                        continue
                    neighbor_angles.append(math.atan2(
                        _py - sample[1], _px - sample[0]))
                enclosed = False
                for _ai in range(len(neighbor_angles)):
                    for _aj in range(_ai + 1, len(neighbor_angles)):
                        _ad = abs(neighbor_angles[_ai] - neighbor_angles[_aj])
                        _ad = min(_ad, 2.0 * math.pi - _ad)
                        if _ad >= math.radians(95.0):
                            enclosed = True
                            break
                    if enclosed:
                        break
                if not enclosed:
                    continue
                before_len = len(nodes)
                if add_normal(
                        sample[0], sample[1], 'coverage_relaxation',
                        surface_z=sample[2], mode='normal',
                        spacing_mode='coverage'):
                    if len(nodes) > before_len:
                        added = nodes[-1]
                        added_radius = radius_m_local(added['b15'])
                        covered_component_samples = sum(
                            1 for _component_i in component
                            if (abs(_relax_samples[_component_i][3]
                                    - float(added['z'])) <= 0.75
                                and math.hypot(
                                    _relax_samples[_component_i][0] - added['x'],
                                    _relax_samples[_component_i][1] - added['y'])
                                <= added_radius + _relax_margin))
                        if covered_component_samples >= 2:
                            added['coverage_relaxation_added'] = True
                            coverage_relaxation_metrics['action_log'].append({
                                'kind': 'add',
                                'node_index': int(len(nodes) - 1),
                                'x': round(float(added['x']), 4),
                                'y': round(float(added['y']), 4),
                                'z': round(float(added['z']), 4),
                                'b15': int(added['b15']),
                                'covered_samples': int(covered_component_samples),
                            })
                            return True
                while len(nodes) > before_len:
                    nodes.pop()
            return False

        _relax_rebuild_node_index()
        initial_missing, initial_components = _relax_missing_components()
        coverage_relaxation_metrics.update({
            'enabled': True,
            'sample_count': len(_relax_samples),
            'initial_missing_samples': len(initial_missing),
            'initial_components': len(initial_components),
        })

        for _round in range(max(0, _relax_max_rounds)):
            if coverage_relaxation_metrics['actions'] >= _relax_max_actions:
                break
            _missing, _components = _relax_missing_components()
            actionable = []
            residual_by_sample = {item[0]: item[1] for item in _missing}
            for component in _components:
                deepest = max((residual_by_sample.get(i, 0.0) for i in component), default=0.0)
                if len(component) >= _relax_min_component or deepest >= 0.45:
                    actionable.append(component)
            if not actionable:
                break
            changed_this_round = 0
            for component in actionable:
                if coverage_relaxation_metrics['actions'] >= _relax_max_actions:
                    break
                action = None
                if _relax_try_growth(component):
                    action = 'radius_growth_actions'
                else:
                    _relax_rebuild_node_index()
                    if _relax_try_move(component):
                        action = 'move_actions'
                    else:
                        _relax_rebuild_node_index()
                        if _relax_try_add(component):
                            action = 'add_actions'
                if action is None:
                    continue
                coverage_relaxation_metrics[action] += 1
                coverage_relaxation_metrics['actions'] += 1
                changed_this_round += 1
                _relax_rebuild_node_index()
            if changed_this_round == 0:
                break

        _relax_rebuild_node_index()
        final_missing, final_components = _relax_missing_components()
        coverage_relaxation_metrics.update({
            'final_missing_samples': len(final_missing),
            'final_components': len(final_components),
            'seconds': round(_time.perf_counter() - _relax_t0, 4),
        })

    # The natural walker is deliberately only a provisional explorer.  The
    # shipped ordinary graphs do not retain its coarse b8/b12 classification:
    # after positions have stabilised, radius bytes are fitted to the measured
    # ordinary placement contract
    #
    #                  distance ~= (b15_a + b15_b) / 24.
    #
    # Doing this inside spacing_ok changes DFS history and collapses the
    # frontier.  Here it cannot affect exploration because coverage repair,
    # layered support, and the final geometry cull are already complete, while
    # links have not been built yet.  Confirmed centred corridor lanes are
    # frozen; their dedicated grower already uses the same 4/3-radius pitch.
    if not quantize_to_map_grid and nodes:
        _progress(
            71, 'Refitting ordinary radius field',
            'Solving final radius bytes against the /24 campaign placement contract')
        _refit_t0 = _time.perf_counter()
        _radius_grid = _V52Spatial(6.8)
        for _i, _n in enumerate(nodes):
            _radius_grid.insert(_n['x'], _n['y'], _i)

        _frozen_radius = {
            _i for _i, _n in enumerate(nodes)
            if _n.get('family') == 'corridor_fit'
            or _n.get('family') == 'stair_transition'
            or _n.get('corridor_axis') is not None
        }
        _capacity = []
        for _n in nodes:
            _surface_z = float(_n['z']) - 1.2489
            _clearance = nearest_hard(
                float(_n['x']), float(_n['y']), _surface_z)
            # b15/16 is the runtime acceptance disc shown by the editor, not an
            # ordinary-mode body-clearance radius.  Campaign boundary rows fit
            # their final family in the same /24 domain as pair placement
            # (notably b16 at roughly 0.67 m from a wall).  The node centre has
            # already passed collision validation above; this cap prevents a
            # family from crossing the measured ordinary clearance contract
            # without incorrectly demanding that its full acceptance disc fit.
            _cap = int(math.floor(
                max(0.0, _clearance) * 24.0 /
                max(1e-6, NODE_RADIUS_CLEARANCE_FACTOR) + 1e-6))
            _capacity.append(max(int(_n['b15']), min(48, _cap)))

        _before_b15 = [int(_n['b15']) for _n in nodes]
        _solved_b15 = list(_before_b15)
        _pair_count = 0
        _prior_pair_count = 0
        _promoted_nodes = set()
        _iterations = 0

        # Synchronous iterations make the result independent of node order.
        # Only the closest same-floor peer supplies the contract: the campaign
        # statistic is a nearest-pair law, not an all-neighbour spring system.
        # Two rounds are sufficient for the nearest-pair constraint to cross a
        # mixed-family pair.  A third round over-propagates the larger family
        # through the provisional field and drops below the campaign median.
        for _iteration in range(2):
            _next_b15 = list(_solved_b15)
            _changed = 0
            _iteration_pairs = 0
            for _i, _n in enumerate(nodes):
                if _i in _frozen_radius:
                    continue
                _best = None
                for _px, _py, _j in _radius_grid.query(
                        float(_n['x']), float(_n['y']), 6.5):
                    if _j == _i:
                        continue
                    _other = nodes[_j]
                    if abs(float(_other['z']) - float(_n['z'])) > 0.60:
                        continue
                    _distance = math.hypot(
                        float(_n['x']) - float(_other['x']),
                        float(_n['y']) - float(_other['y']))
                    if _distance < 0.35:
                        continue
                    if _best is None or _distance < _best[0]:
                        _best = (_distance, False, _j)
                for _px, _py, _pj in prior_grid.query(
                        float(_n['x']), float(_n['y']), 6.5):
                    _other = prior_nodes[_pj]
                    if abs(float(_other['z']) - float(_n['z'])) > 0.60:
                        continue
                    _distance = math.hypot(
                        float(_n['x']) - float(_other['x']),
                        float(_n['y']) - float(_other['y']))
                    if _distance < 0.35:
                        continue
                    if _best is None or _distance < _best[0]:
                        _best = (_distance, True, _pj)
                if _best is None:
                    continue
                _distance, _is_prior, _j = _best
                _iteration_pairs += 1
                if _is_prior:
                    _prior_pair_count += 1
                # Solve b_i + b_j = 24*d.  Never shrink the provisional radius:
                # shrinking would invalidate already-proven coverage.  Limit a
                # single iteration to one family step so two close small nodes
                # cannot amplify each other into an open-field family at once.
                # Solving directly at 24 and then rounding to the nearest
                # discrete family biases the field upward.  Campaign ordinary
                # populations land at about 0.94--0.96 of the ideal after that
                # family selection.  A one-byte-per-metre reserve (23*d before
                # snapping) cancels the rounding bias while validation remains
                # measured against the exact /24 contract.
                _peer_b15 = (
                    int(prior_nodes[_j]['b15'])
                    if _is_prior else int(_solved_b15[_j]))
                _wanted_raw = int(round(
                    23.0 * _distance - _peer_b15))
                _wanted_raw = max(_solved_b15[_i], _wanted_raw)
                # Ordinary campaign output is not a continuous-radius cloud.
                # It strongly prefers the working families below; intermediate
                # bytes appear where local clearance clips the selected rung.
                _ordinary_rungs = (8, 12, 16, 24, 36, 48)
                _eligible_rungs = [
                    _rung for _rung in _ordinary_rungs
                    if _rung >= _solved_b15[_i]]
                if _eligible_rungs:
                    _wanted = min(
                        _eligible_rungs,
                        key=lambda _rung: (abs(_rung - _wanted_raw), _rung))
                else:
                    _wanted = _wanted_raw
                # When the chosen family does not fit, retain the exact
                # clearance-limited byte instead of falling all the way back to
                # the previous family.  This is the source of the legitimate
                # b13/b15/b18-style campaign values around geometry.
                if _wanted > _capacity[_i]:
                    _wanted = _capacity[_i]
                _wanted = min(_solved_b15[_i] + 8, _wanted)
                if _wanted > _solved_b15[_i]:
                    _next_b15[_i] = _wanted
                    _changed += 1
                    _promoted_nodes.add(_i)
            _solved_b15 = _next_b15
            _pair_count = max(_pair_count, _iteration_pairs)
            _iterations = _iteration + 1
            if _changed == 0:
                break

        for _i, _n in enumerate(nodes):
            if _i in _frozen_radius:
                continue
            _n['b15'] = int(_solved_b15[_i])
            # b12 and placement-family provenance describe how the provisional
            # node was admitted.  Radius refitting must not silently rewrite
            # either field; keep a separate diagnostic marker instead.
            _n['ordinary_radius_refit'] = (
                int(_solved_b15[_i]) != int(_before_b15[_i]))

        _ordinary_after = [int(_n['b15']) for _n in nodes]
        ordinary_radius_refit_metrics = {
            'enabled': True,
            'contract_divisor': 24,
            'pre_snap_solve_scale': 23.0,
            'iterations': _iterations,
            'nearest_pairs': _pair_count,
            'prior_nearest_pairs': _prior_pair_count,
            'frozen_corridor_nodes': len(_frozen_radius),
            'promoted_nodes': len(_promoted_nodes),
            'before': dict(sorted(Counter(_before_b15).items())),
            'after': dict(sorted(Counter(_ordinary_after).items())),
            'seconds': round(_time.perf_counter() - _refit_t0, 4),
        }

    _progress(73, 'Building navigation links', 'Testing local visibility and connection legality')
    # Build links.
    links = {}
    adj = [set() for _ in nodes]
    link_grid = _V52Spatial(6.8)
    for i,n in enumerate(nodes):
        link_grid.insert(n['x'], n['y'], i)

    def link_blocked(i,j):
        # Exact ray-vs-segment via grid; campaign treats LOS as a minor veto
        ax,ay=nodes[i]['x'],nodes[i]['y']; bx,by=nodes[j]['x'],nodes[j]['y']
        L=math.hypot(bx-ax,by-ay)
        mx,my=(ax+bx)*0.5,(ay+by)*0.5
        if height_aware_collision:
            node_az = float(nodes[i]['z'])
            node_bz = float(nodes[j]['z'])
            _hc_lo = False
            _hc_hi = False
            for _ti in _triangles3d_near(mx,my, L*0.5+1.2):
                tri = triangles3d[_ti]
                if _segment_hits_triangle(
                        ax, ay, node_az, bx, by, node_bz, tri):
                    return True
                if not _hc_lo and _segment_hits_triangle(
                        ax, ay, node_az - 0.5, bx, by, node_bz - 0.5, tri):
                    _hc_lo = True
                if not _hc_hi and _segment_hits_triangle(
                        ax, ay, node_az + 0.5, bx, by, node_bz + 0.5, tri):
                    _hc_hi = True
            if _hc_lo or _hc_hi:
                return True
            az = float(nodes[i]['z']) - 1.2489
            bz = float(nodes[j]['z']) - 1.2489
            for _si in _segs3d_near(mx,my, L*0.5+1.2):
                x1,y1,z1,x2,y2,z2,ei=segs3d[_si]
                d1x=bx-ax; d1y=by-ay; d2x=x2-x1; d2y=y2-y1
                den=d1x*d2y-d1y*d2x
                if -1e-12<den<1e-12:
                    continue
                t=((x1-ax)*d2y-(y1-ay)*d2x)/den
                if t<=0.02 or t>=0.98:
                    continue
                u=((x1-ax)*d1y-(y1-ay)*d1x)/den
                if not 0.0<=u<=1.0:
                    continue
                support_z = az + (bz-az)*t
                segment_z = z1 + (z2-z1)*u
                if support_z + 0.20 <= segment_z <= support_z + 1.80:
                    return True
            return False
        for _si in _segs_near(mx,my, L*0.5+1.2):
            x1,y1,x2,y2,ei=segs[_si]
            d1x=bx-ax; d1y=by-ay; d2x=x2-x1; d2y=y2-y1
            den=d1x*d2y-d1y*d2x
            if -1e-12<den<1e-12: continue
            t=((x1-ax)*d2y-(y1-ay)*d2x)/den
            if t<=0.02 or t>=0.98: continue
            u=((x1-ax)*d1y-(y1-ay)*d1x)/den
            if 0.0<=u<=1.0:
                return True
        if inside_solid(mx,my):
            return True
        return False

    def _intervening_node(i,j):
        """Return True only when an intervening node already carries the route.

        Candidate edges are discovered before the backbone exists.  Rejecting
        an edge merely because a third centre lies near its segment can remove
        the only legal connection even when that third node never links to one
        or both endpoints.  The finished-graph shadow audit already uses the
        stronger contract: the intervening node must be adjacent to both ends.
        Apply that same proof here.
        """
        ax,ay=nodes[i]['x'],nodes[i]['y']; bx,by=nodes[j]['x'],nodes[j]['y']
        az=float(nodes[i]['z']); bz=float(nodes[j]['z'])
        vx=bx-ax; vy=by-ay
        d2=vx*vx+vy*vy
        if d2 <= 1e-9:
            return False
        d=math.sqrt(d2)
        mx,my=(ax+bx)*0.5,(ay+by)*0.5
        corridor=max(0.35,min(0.75,0.18*d))
        for px,py,k in link_grid.query(mx,my,d*0.5+corridor):
            if k==i or k==j:
                continue
            t=((px-ax)*vx+(py-ay)*vy)/d2
            if t<=0.10 or t>=0.90:
                continue
            expected_z = az + (bz-az)*t
            if abs(float(nodes[k]['z']) - expected_z) > 1.0:
                continue
            qx=ax+t*vx; qy=ay+t*vy
            if math.hypot(px-qx,py-qy)>corridor:
                continue
            if k not in adj[i] or k not in adj[j]:
                continue
            if math.hypot(px-ax,py-ay)<0.92*d and math.hypot(px-bx,py-by)<0.92*d:
                return True
        return False

    def _direction_redundant(i,j,angle_limit=math.pi/8.0):
        """Reject a farther edge when a closer accepted edge covers its direction."""
        ax,ay=nodes[i]['x'],nodes[i]['y']
        bx,by=nodes[j]['x'],nodes[j]['y']
        angle=math.atan2(by-ay,bx-ax)
        distance=math.hypot(bx-ax,by-ay)
        for k in adj[i]:
            kx,ky=nodes[k]['x'],nodes[k]['y']
            other_angle=math.atan2(ky-ay,kx-ax)
            delta=abs((angle-other_angle+math.pi)%math.tau-math.pi)
            if delta<angle_limit and math.hypot(kx-ax,ky-ay)<=1.08*distance:
                return True
        return False

    def _support_path_ok(i, j, step_limit):
        """Prove a direct link through the reachable support raster.

        Endpoint slope and collision visibility are insufficient: two valid
        nodes on different storeys can have a clear ray through open air.  The
        same 0.25 m support raster used by the reachability flood must be able
        to walk the entire segment in both directions.  Ordinary links use the
        normal 0.35 m step contract; verified stair links supply the stair-step
        allowance explicitly.
        """
        if not (callable(z_at) and hasattr(z_at, 'path_height')):
            return False
        first_surface_z = float(nodes[i]['z']) - 1.2489
        second_surface_z = float(nodes[j]['z']) - 1.2489
        try:
            forward_z = z_at.path_height(
                nodes[i]['x'], nodes[i]['y'], first_surface_z,
                nodes[j]['x'], nodes[j]['y'],
                step_limit=float(step_limit))
            reverse_z = z_at.path_height(
                nodes[j]['x'], nodes[j]['y'], second_surface_z,
                nodes[i]['x'], nodes[i]['y'],
                step_limit=float(step_limit))
        except Exception:
            return False
        endpoint_tolerance = GENERATOR_REACHABLE_MAX_STEP + 0.08
        return (
            forward_z is not None
            and reverse_z is not None
            and abs(float(forward_z) - second_surface_z) <= endpoint_tolerance
            and abs(float(reverse_z) - first_surface_z) <= endpoint_tolerance
        )

    def _stair_support_path_ok(i, j):
        return _support_path_ok(i, j, GENERATOR_REACHABLE_STAIR_STEP)

    ordinary_support_path_rejects = 0
    ordinary_support_path_reject_details = []

    def add_link(i,j,force=False):
        nonlocal ordinary_support_path_rejects
        if i==j or j in adj[i]:
            return False
        if len(adj[i]) >= 10 or len(adj[j]) >= 10:
            return False
        d=math.hypot(nodes[i]['x']-nodes[j]['x'], nodes[i]['y']-nodes[j]['y'])
        dz=abs(float(nodes[i]['z'])-float(nodes[j]['z']))
        stair_candidate = (
            nodes[i].get('tag') == 'layered_stair'
            or nodes[j].get('tag') == 'layered_stair'
        )
        stair_force = force and stair_candidate
        if stair_force:
            if dz > max(0.90, 1.25*d) or not _stair_support_path_ok(i, j):
                return False
        elif dz > max(0.45, 0.75*d):
            return False
        elif (not quantize_to_map_grid
              and dz > 1.80
              and not _support_path_ok(
                  i, j,
                  (GENERATOR_REACHABLE_STAIR_STEP
                   if stair_candidate else GENERATOR_REACHABLE_MAX_STEP))):
            ordinary_support_path_rejects += 1
            if len(ordinary_support_path_reject_details) < 64:
                ordinary_support_path_reject_details.append({
                    'first': int(i),
                    'second': int(j),
                    'distance': round(float(d), 4),
                    'dz': round(float(dz), 4),
                    'first_tag': str(nodes[i].get('tag', '')),
                    'second_tag': str(nodes[j].get('tag', '')),
                    'forced': bool(force),
                })
            return False
        rsum=radius_m_local(nodes[i]['b15'])+radius_m_local(nodes[j]['b15'])
        # Quantized b15=4 rails use the campaign's legal 0.50 m pitch.  The
        # natural grower's 0.85 m floor would isolate the entire fine lattice.
        min_distance = (0.30 if (stair_force or quantize_to_map_grid) else 0.85)
        if d < min_distance or d > (max(7.4, 2.4*rsum) if force else max(2.7, 1.45*rsum)):
            return False
        if not force and d < 0.56*(radius_m_local(nodes[i]['b15'])+radius_m_local(nodes[j]['b15'])):
            return False
        if link_blocked(i,j):
            return False
        if not force and _intervening_node(i,j):
            return False
        adj[i].add(j); adj[j].add(i)
        a,b=sorted((i,j))
        links[(a,b)] = (d, 'v58_seed_disc')
        return True

    _link_metrics = _gen_linking.build_links(
        nodes, adj, links, link_grid, _progress,
        add_link, link_blocked, _intervening_node,
        _direction_redundant,
        _support_path_ok, radius_m_local,
        quantize_to_map_grid,
        quantized_axis_link_count=quantized_axis_link_count)
    quantized_inherited_link_count = _link_metrics['quantized_inherited_link_count']
    quantized_layer_skeleton_link_count = _link_metrics['quantized_layer_skeleton_link_count']
    quantized_layer_local_link_count = _link_metrics['quantized_layer_local_link_count']
    quantized_cardinal_gap_candidate_count = _link_metrics['quantized_cardinal_gap_candidate_count']
    quantized_cardinal_gap_support_reject_count = _link_metrics['quantized_cardinal_gap_support_reject_count']
    quantized_cardinal_gap_collision_reject_count = _link_metrics['quantized_cardinal_gap_collision_reject_count']
    quantized_cardinal_gap_link_count = _link_metrics['quantized_cardinal_gap_link_count']
    quantized_cardinal_gap_details = _link_metrics['quantized_cardinal_gap_details']
    quantized_layer_candidate_count = _link_metrics['quantized_layer_candidate_count']
    quantized_layer_support_reject_count = _link_metrics['quantized_layer_support_reject_count']
    quantized_fallback_link_count = _link_metrics['quantized_fallback_link_count']

    _progress(87, 'Connecting stair chains', 'Repairing layered stair and landing connectivity')
    # Explicit stair-chain/landing pass. Narrow rising chains overlap heavily
    # in top view, so the ordinary angular fabric can leave them disconnected.
    # This pass applies only to verified layered_stair nodes.
    stair_indices = [
        index for index,node in enumerate(nodes)
        if node.get('tag') == 'layered_stair'
    ]
    stair_links = 0
    stair_endpoint_count = 0
    stair_endpoint_attached_count = 0
    stair_endpoint_unattached_count = 0
    stair_endpoint_max_link_distance = 0.0
    stair_endpoint_details = []
    stair_landing_network_links = 0
    stair_landing_network_details = []
    stair_landing_bridge_nodes = 0
    stair_landing_network_rejects = Counter()
    stair_landing_local_links = 0
    stair_landing_local_details = []
    stair_longitudinal_expected = 0
    stair_longitudinal_existing = 0
    stair_longitudinal_added = 0
    stair_longitudinal_provenance_added = 0
    stair_longitudinal_provenance_reasons = Counter()
    stair_longitudinal_rejects = Counter()
    stair_longitudinal_reject_details = []
    stair_landing_seam_links = 0
    stair_landing_seam_details = []
    stair_junction_links = 0
    stair_junction_details = []
    stair_junction_attempt_details = []
    stair_routed_landing_links = 0
    stair_routed_landing_bridge_nodes = 0
    stair_routed_landing_details = []
    stair_routed_landing_rejects = Counter()
    stair_routed_landing_reject_details = []
    quantized_stair_side_seam_links = 0
    quantized_stair_side_seam_details = []
    quantized_stair_side_seam_rejects = Counter()
    quantized_stair_side_seam_reject_details = []
    ladder_rung_profiles = 0
    ladder_bridge_chains = 0
    ladder_bridge_nodes_added = 0
    ladder_bridge_links_added = 0
    ladder_bridge_details = []
    ladder_old_new_seams = []

    def _add_verified_stair_longitudinal(first, second):
        """Join consecutive retained rows in one verified stair lane.

        The generic collision test sees real stair risers as blockers, while
        raster line sampling can miss a tread exactly at a row/landing seam.
        The temporary-slope fitter already proved these nodes belong to the
        same coherent support component.  Keep this bypass restricted to one
        adjacent row in the same generated flight and lane.
        """
        first_node = nodes[first]
        second_node = nodes[second]
        if (
                first_node.get('tag') != 'layered_stair'
                or second_node.get('tag') != 'layered_stair'
                or first_node.get('stair_flight') is None
                or first_node.get('stair_flight')
                != second_node.get('stair_flight')
                or first_node.get('stair_lane') is None
                or first_node.get('stair_lane')
                != second_node.get('stair_lane')
                or abs(
                    int(first_node.get('stair_row', -1000))
                    - int(second_node.get('stair_row', 1000))) != 1):
            return False
        if len(adj[first]) >= 10 or len(adj[second]) >= 10:
            return False
        distance = math.hypot(
            float(first_node['x']) - float(second_node['x']),
            float(first_node['y']) - float(second_node['y']))
        dz = abs(float(first_node['z']) - float(second_node['z']))
        if (
                distance < 0.30
                or distance > 2.0
                or dz > max(0.90, 1.25*distance)):
            return False
        adj[first].add(second)
        adj[second].add(first)
        edge_key = tuple(sorted((first, second)))
        links[edge_key] = (
            float(distance), 'stair_longitudinal_provenance')
        return True

    def _add_verified_stair_landing_seam(endpoint, landing, lane_indices):
        """Join one proven flight endpoint across its final tread/riser.

        The hard model segments include real stair risers.  Extend the fitted
        flight's collision bypass by exactly one forward, height-consistent
        ordinary landing node, after the reachable support raster proves the
        full transition in both directions.
        """
        if (
                endpoint == landing
                or landing in adj[endpoint]
                or nodes[endpoint].get('tag') != 'layered_stair'
                or nodes[landing].get('tag') == 'layered_stair'
                or len(lane_indices) < 2
                or len(adj[endpoint]) >= 10
                or len(adj[landing]) >= 10):
            return False
        if endpoint == lane_indices[0]:
            inward = lane_indices[1]
        elif endpoint == lane_indices[-1]:
            inward = lane_indices[-2]
        else:
            return False
        step_x = float(nodes[endpoint]['x']) - float(nodes[inward]['x'])
        step_y = float(nodes[endpoint]['y']) - float(nodes[inward]['y'])
        step_length = math.hypot(step_x, step_y)
        if step_length < 0.30 or step_length > 2.0:
            return False
        run_x = float(nodes[landing]['x']) - float(nodes[endpoint]['x'])
        run_y = float(nodes[landing]['y']) - float(nodes[endpoint]['y'])
        distance = math.hypot(run_x, run_y)
        if distance < 0.30 or distance > 2.8:
            return False
        forward = (run_x*step_x + run_y*step_y) / step_length
        lateral = abs(run_x*step_y - run_y*step_x) / step_length
        if forward < 0.30 or forward/distance < 0.86 or lateral > 0.65:
            return False
        step_dz = float(nodes[endpoint]['z']) - float(nodes[inward]['z'])
        seam_dz = float(nodes[landing]['z']) - float(nodes[endpoint]['z'])
        if abs(step_dz) < 0.20:
            return False
        if step_dz > 0.0:
            if seam_dz < -0.18 or seam_dz > 1.25:
                return False
        elif seam_dz > 0.18 or seam_dz < -1.25:
            return False
        if not _stair_support_path_ok(endpoint, landing):
            return False
        # This fallback exists only for the verified-riser false positive.
        if not link_blocked(endpoint, landing):
            return False
        blocker_triangles = 0
        blocker_segments = []
        if height_aware_collision:
            ax = float(nodes[endpoint]['x'])
            ay = float(nodes[endpoint]['y'])
            bx = float(nodes[landing]['x'])
            by = float(nodes[landing]['y'])
            length = math.hypot(bx-ax, by-ay)
            mx = 0.5*(ax+bx)
            my = 0.5*(ay+by)
            node_az = float(nodes[endpoint]['z'])
            node_bz = float(nodes[landing]['z'])
            for triangle_index in _triangles3d_near(
                    mx, my, length*0.5+1.2):
                if _segment_hits_triangle(
                        ax, ay, node_az, bx, by, node_bz,
                        triangles3d[triangle_index]):
                    blocker_triangles += 1
            support_az = node_az - 1.2489
            support_bz = node_bz - 1.2489
            for segment_index in _segs3d_near(
                    mx, my, length*0.5+1.2):
                x1,y1,z1,x2,y2,z2,entity_index = segs3d[segment_index]
                d1x = bx-ax
                d1y = by-ay
                d2x = x2-x1
                d2y = y2-y1
                denominator = d1x*d2y-d1y*d2x
                if -1e-12 < denominator < 1e-12:
                    continue
                t = ((x1-ax)*d2y-(y1-ay)*d2x)/denominator
                if t <= 0.02 or t >= 0.98:
                    continue
                u = ((x1-ax)*d1y-(y1-ay)*d1x)/denominator
                if not 0.0 <= u <= 1.0:
                    continue
                support_z = support_az + (support_bz-support_az)*t
                segment_z = z1 + (z2-z1)*u
                clearance = segment_z-support_z
                if 0.20 <= clearance <= 1.80:
                    blocker_segments.append({
                        'entity': int(entity_index),
                        't': round(float(t), 4),
                        'clearance': round(float(clearance), 4),
                    })
        if (
                blocker_triangles
                or not blocker_segments
                or any(
                    float(blocker['clearance'])
                    > GENERATOR_REACHABLE_STAIR_STEP + 0.08
                    for blocker in blocker_segments)):
            return False
        adj[endpoint].add(landing)
        adj[landing].add(endpoint)
        edge_key = tuple(sorted((endpoint, landing)))
        links[edge_key] = (
            float(distance), 'verified_stair_landing_seam')
        stair_landing_seam_details.append({
            'endpoint': int(endpoint),
            'landing': int(landing),
            'distance': round(float(distance), 4),
            'dz': round(float(abs(seam_dz)), 4),
            'forward': round(float(forward), 4),
            'lateral': round(float(lateral), 4),
            'blocker_triangles': int(blocker_triangles),
            'blocker_segments': blocker_segments,
        })
        return True

    def _add_verified_stair_junction(first, second):
        """Join nearby endpoints belonging to two verified stair flights.

        Switchback landings are built from real tread/riser geometry.  The
        generic collision ray can therefore reject the one short endpoint-to-
        endpoint seam even though both fitted flights and the reachable support
        raster prove a continuous staircase.  Keep this bypass narrower than
        the existing junction search: both nodes must be verified stair nodes
        from different flights, within the already accepted junction envelope,
        and the sampled support path must remain valid.
        """
        first_node = nodes[first]
        second_node = nodes[second]
        if (
                first_node.get('tag') != 'layered_stair'
                or second_node.get('tag') != 'layered_stair'
                or first_node.get('stair_flight') is None
                or second_node.get('stair_flight') is None
                or first_node.get('stair_flight')
                == second_node.get('stair_flight')
                or len(adj[first]) >= 10 or len(adj[second]) >= 10):
            return False
        distance = math.hypot(
            float(first_node['x']) - float(second_node['x']),
            float(first_node['y']) - float(second_node['y']))
        dz = abs(float(first_node['z']) - float(second_node['z']))
        if distance < 0.30 or distance > 2.8 or dz > 1.25:
            return False
        if not _stair_support_path_ok(first, second):
            return False
        # Do not bypass an arbitrary failure.  This exception exists only for
        # the real stair-riser collision false positive.
        if not link_blocked(first, second):
            return False
        adj[first].add(second)
        adj[second].add(first)
        edge_key = tuple(sorted((first, second)))
        links[edge_key] = (float(distance), 'verified_stair_junction')
        stair_junction_details.append({
            'first': int(first), 'second': int(second),
            'first_flight': int(first_node.get('stair_flight', -1)),
            'second_flight': int(second_node.get('stair_flight', -1)),
            'distance': round(float(distance), 4),
            'dz': round(float(dz), 4),
        })
        return True

    def _stair_routed_support_path(first, second, margin=1.5):
        """Find a short model-supported route around a stair landing corner.

        ``path_height`` proves only a straight segment.  Switchback landings can
        be perfectly reachable while that diagonal cuts across a raster hole.
        Search the same reachable 0.25 m support states locally instead of
        pretending the landing is a straight ramp.
        """
        if (not callable(z_at) or grid_origin is None or not reachable_layers):
            return None
        import heapq as _heapq
        start_surface = float(nodes[first]['z']) - NAV_NODE_Z_LIFT
        goal_surface = float(nodes[second]['z']) - NAV_NODE_Z_LIFT
        if abs(start_surface - goal_surface) > 1.35:
            return None
        ox, oy = float(grid_origin[0]), float(grid_origin[1])
        cell_size = float(grid_cell)
        def _cell(x, y):
            return (int(round((float(x)-ox)/cell_size)),
                    int(round((float(y)-oy)/cell_size)))
        def _state_for(x, y, preferred):
            key = _cell(x, y)
            values = tuple(float(v) for v in reachable_layers.get(key, ()))
            if not values:
                return None
            indexed = min(enumerate(values),
                          key=lambda item: abs(item[1]-float(preferred)))
            if abs(indexed[1]-float(preferred)) > 0.40:
                return None
            return (key[0], key[1], int(indexed[0]))
        start = _state_for(nodes[first]['x'], nodes[first]['y'], start_surface)
        goal = _state_for(nodes[second]['x'], nodes[second]['y'], goal_surface)
        if start is None or goal is None:
            return None
        min_x = min(float(nodes[first]['x']), float(nodes[second]['x']))-float(margin)
        max_x = max(float(nodes[first]['x']), float(nodes[second]['x']))+float(margin)
        min_y = min(float(nodes[first]['y']), float(nodes[second]['y']))-float(margin)
        max_y = max(float(nodes[first]['y']), float(nodes[second]['y']))+float(margin)
        ix0, iy0 = _cell(min_x, min_y)
        ix1, iy1 = _cell(max_x, max_y)
        if ix0 > ix1: ix0, ix1 = ix1, ix0
        if iy0 > iy1: iy0, iy1 = iy1, iy0
        z_lo = min(start_surface, goal_surface)-0.90
        z_hi = max(start_surface, goal_surface)+0.90
        def _state_z(state):
            vals = reachable_layers.get((state[0], state[1]), ())
            if not 0 <= state[2] < len(vals):
                return None
            return float(vals[state[2]])
        def _is_model_state(state):
            value = _state_z(state)
            if value is None:
                return False
            key = (state[0], state[1])
            return (
                _layer_in(model_layers, key, value, tolerance=0.10)
                or _layer_in(geometry_stair_layers, key, value, tolerance=0.10)
            )
        if not _is_model_state(start) or not _is_model_state(goal):
            return None
        queue = [(0.0, 0.0, start)]
        best = {start: 0.0}
        previous = {}
        directions = ((1,0),(-1,0),(0,1),(0,-1),
                      (1,1),(1,-1),(-1,1),(-1,-1))
        max_cost = max(5.0,
                       3.0*math.hypot(
                           float(nodes[first]['x'])-float(nodes[second]['x']),
                           float(nodes[first]['y'])-float(nodes[second]['y']))
                       / cell_size)
        while queue:
            _priority, cost, state = _heapq.heappop(queue)
            if cost != best.get(state):
                continue
            if state == goal:
                break
            if cost > max_cost:
                continue
            current_z = _state_z(state)
            for dx, dy in directions:
                nx, ny = state[0]+dx, state[1]+dy
                if nx < ix0 or nx > ix1 or ny < iy0 or ny > iy1:
                    continue
                values = reachable_layers.get((nx, ny), ())
                for support_index, raw_value in enumerate(values):
                    value = float(raw_value)
                    if value < z_lo or value > z_hi:
                        continue
                    next_state = (nx, ny, int(support_index))
                    if not _is_model_state(next_state):
                        continue
                    if abs(value-current_z) > GENERATOR_REACHABLE_STAIR_STEP + 1e-6:
                        continue
                    step_cost = math.hypot(dx, dy)
                    next_cost = cost + step_cost
                    if next_cost >= best.get(next_state, float('inf')):
                        continue
                    best[next_state] = next_cost
                    previous[next_state] = state
                    heuristic = math.hypot(nx-goal[0], ny-goal[1])
                    _heapq.heappush(queue,
                                    (next_cost+heuristic, next_cost, next_state))
        if goal not in best:
            return None
        states = [goal]
        while states[-1] != start:
            states.append(previous[states[-1]])
        states.reverse()
        points = []
        for ix, iy, support_index in states:
            value = float(reachable_layers[(ix, iy)][support_index])
            points.append((ox+ix*cell_size, oy+iy*cell_size, value))
        return points

    def _compress_stair_route(points):
        if not points or len(points) <= 2:
            return []
        selected = []
        last_selected = 0
        last_direction = None
        for index in range(1, len(points)-1):
            prev = points[index-1]
            cur = points[index]
            direction = (
                0 if abs(cur[0]-prev[0]) < 1e-8 else (1 if cur[0] > prev[0] else -1),
                0 if abs(cur[1]-prev[1]) < 1e-8 else (1 if cur[1] > prev[1] else -1))
            nextp = points[index+1]
            next_direction = (
                0 if abs(nextp[0]-cur[0]) < 1e-8 else (1 if nextp[0] > cur[0] else -1),
                0 if abs(nextp[1]-cur[1]) < 1e-8 else (1 if nextp[1] > cur[1] else -1))
            distance_from_last = math.hypot(
                cur[0]-points[last_selected][0], cur[1]-points[last_selected][1])
            height_change = abs(cur[2]-points[last_selected][2])
            if (direction != next_direction or distance_from_last >= 1.25
                    or height_change >= 0.45):
                selected.append(cur)
                last_selected = index
            last_direction = direction
        # Forced graph links still have a 0.30 m minimum.  Raster turns can
        # occur on adjacent 0.25 m cells, so coalesce those into one waypoint
        # instead of manufacturing a guaranteed too-close bridge edge.
        pruned = []
        anchor = points[0]
        for point in selected:
            if math.hypot(point[0]-anchor[0], point[1]-anchor[1]) < 0.45:
                if pruned:
                    pruned[-1] = point
                    anchor = point
                continue
            pruned.append(point)
            anchor = point
        if pruned and math.hypot(
                points[-1][0]-pruned[-1][0],
                points[-1][1]-pruned[-1][1]) < 0.45:
            pruned.pop()
        return pruned

    def _add_routed_stair_landing(endpoint, landing):
        nonlocal link_grid, stair_routed_landing_links
        nonlocal stair_routed_landing_bridge_nodes
        def _reject(reason, **extra):
            stair_routed_landing_rejects[str(reason)] += 1
            if len(stair_routed_landing_reject_details) < 64:
                record = {'endpoint': int(endpoint), 'landing': int(landing),
                          'reason': str(reason)}
                record.update(extra)
                stair_routed_landing_reject_details.append(record)
            return False
        if (endpoint == landing or landing in adj[endpoint]
                or nodes[endpoint].get('tag') != 'layered_stair'):
            return _reject('invalid_pair')
        route = _stair_routed_support_path(endpoint, landing)
        if not route or len(route) < 3:
            return _reject('no_route')
        route_length = sum(math.hypot(
            route[k][0]-route[k-1][0], route[k][1]-route[k-1][1])
            for k in range(1, len(route)))
        direct = math.hypot(
            float(nodes[endpoint]['x'])-float(nodes[landing]['x']),
            float(nodes[endpoint]['y'])-float(nodes[landing]['y']))
        if route_length > max(6.0, direct*2.5):
            return _reject('route_too_long', route_length=round(float(route_length),4), direct=round(float(direct),4))
        waypoints = _compress_stair_route(route)
        if not waypoints:
            return _reject('no_waypoints', route_cells=len(route))
        created = []
        chain = [endpoint]
        for x, y, surface_z in waypoints:
            # Reuse an existing same-support node when the route already passes
            # through one.  Otherwise create a conservative landing bridge.
            reuse = None
            for px, py, candidate in link_grid.query(x, y, 0.65):
                if candidate in chain or candidate == landing:
                    continue
                if abs(float(nodes[candidate]['z'])
                       - (float(surface_z)+NAV_NODE_Z_LIFT)) > 0.22:
                    continue
                d = math.hypot(float(px)-float(x), float(py)-float(y))
                item = (d, candidate)
                if reuse is None or item < reuse:
                    reuse = item
            if reuse is not None:
                chain.append(int(reuse[1]))
                continue
            bridge_b15, bridge_b12, _bridge_family = classify(
                float(x), float(y), float(surface_z))
            # The routed path already comes from reachable model-support states,
            # which have standing-volume clearance.  The ordinary 2-D walkable
            # test is intentionally not reused here: beside real stair risers it
            # is the exact false negative this repair exists to bypass.
            bridge_b15 = min(12, max(4, int(bridge_b15 or 8)))
            bridge_index = len(nodes)
            nodes.append({
                'x': float(x), 'y': float(y),
                'z': float(surface_z)+NAV_NODE_Z_LIFT,
                'b12': int(bridge_b12 or 1), 'b15': int(bridge_b15),
                'b16': 0, 'b17': 0, 'b18': 0,
                'tag': 'layered_floor',
                'family': 'stair_landing_bridge',
            })
            adj.append(set())
            link_grid.insert(float(x), float(y), bridge_index)
            created.append(bridge_index)
            chain.append(bridge_index)
        chain.append(landing)
        added_edges = []
        for first, second in zip(chain, chain[1:]):
            if second in adj[first]:
                continue
            if not _stair_support_path_ok(first, second):
                stair_routed_landing_rejects['segment_support'] += 1
                if len(stair_routed_landing_reject_details) < 64:
                    stair_routed_landing_reject_details.append({
                        'endpoint': int(endpoint), 'landing': int(landing),
                        'reason': 'segment_support', 'first': int(first),
                        'second': int(second)})
                for a, b in added_edges:
                    adj[a].discard(b); adj[b].discard(a)
                    links.pop(tuple(sorted((a,b))), None)
                for created_index in reversed(created):
                    for neighbor in list(adj[created_index]):
                        adj[neighbor].discard(created_index)
                        links.pop(tuple(sorted((neighbor, created_index))), None)
                    nodes.pop(); adj.pop()
                if created:
                    link_grid = _V52Spatial(6.8)
                    for node_index, node in enumerate(nodes):
                        link_grid.insert(node['x'], node['y'], node_index)
                return False
            distance = math.hypot(
                float(nodes[first]['x'])-float(nodes[second]['x']),
                float(nodes[first]['y'])-float(nodes[second]['y']))
            generic = add_link(first, second, force=True)
            if not generic:
                # Every segment in this chain has already passed the straight
                # support-raster proof above.  Generic linking can still reject
                # it for two reasons that are artifacts of stair landings: a
                # real riser intersects the collision ray, or a routed bridge
                # sits inside the ordinary 0.85 m same-floor spacing floor.
                # Keep the bypass local to this A*-proven stair route and retain
                # the normal degree/distance/vertical hard limits.
                if len(adj[first]) >= 10 or len(adj[second]) >= 10:
                    failure_reason = 'segment_degree_cap'
                elif distance < 0.30 or distance > 2.8:
                    failure_reason = 'segment_distance'
                elif abs(float(nodes[first]['z'])-float(nodes[second]['z'])) > 1.25:
                    failure_reason = 'segment_vertical'
                else:
                    failure_reason = None
                if failure_reason is not None:
                    stair_routed_landing_rejects[failure_reason] += 1
                    if len(stair_routed_landing_reject_details) < 64:
                        stair_routed_landing_reject_details.append({
                            'endpoint': int(endpoint), 'landing': int(landing),
                            'reason': failure_reason, 'first': int(first),
                            'second': int(second), 'distance': round(float(distance),4)})
                    for a, b in added_edges:
                        adj[a].discard(b); adj[b].discard(a)
                        links.pop(tuple(sorted((a,b))), None)
                    for created_index in reversed(created):
                        for neighbor in list(adj[created_index]):
                            adj[neighbor].discard(created_index)
                            links.pop(tuple(sorted((neighbor, created_index))), None)
                        nodes.pop(); adj.pop()
                    if created:
                        link_grid = _V52Spatial(6.8)
                        for node_index, node in enumerate(nodes):
                            link_grid.insert(node['x'], node['y'], node_index)
                    return False
                adj[first].add(second); adj[second].add(first)
                links[tuple(sorted((first, second)))] = (
                    float(distance), 'verified_stair_routed_landing')
            added_edges.append((first, second))
        stair_routed_landing_links += len(added_edges)
        stair_routed_landing_bridge_nodes += len(created)
        stair_routed_landing_details.append({
            'endpoint': int(endpoint), 'landing': int(landing),
            'route_cells': len(route),
            'route_length': round(float(route_length), 4),
            'bridge_nodes': [int(index) for index in created],
            'chain': [int(index) for index in chain],
        })
        return True

    # Preserve the fitted temporary-slope topology explicitly. Consecutive
    # rows form each longitudinal lane; equal rows connect adjacent lanes.
    # The generic repair below then attaches endpoints to ordinary landings.
    stair_flights = {}
    stair_endpoint_indices = set()
    stair_endpoint_lanes = {}
    for stair_index in stair_indices:
        flight = nodes[stair_index].get('stair_flight')
        lane = nodes[stair_index].get('stair_lane')
        row = nodes[stair_index].get('stair_row')
        if flight is None or lane is None or row is None:
            continue
        stair_flights.setdefault(int(flight), []).append(stair_index)
    for _flight, flight_indices in sorted(stair_flights.items()):
        flight_lanes = {}
        flight_rows = {}
        for stair_index in flight_indices:
            lane = int(nodes[stair_index]['stair_lane'])
            row = int(nodes[stair_index]['stair_row'])
            flight_lanes.setdefault(lane, []).append(stair_index)
            flight_rows.setdefault(row, []).append(stair_index)
        for lane_indices in flight_lanes.values():
            lane_indices.sort(key=lambda index: int(nodes[index]['stair_row']))
            if lane_indices:
                stair_endpoint_indices.add(lane_indices[0])
                stair_endpoint_indices.add(lane_indices[-1])
                stair_endpoint_lanes[lane_indices[0]] = lane_indices
                stair_endpoint_lanes[lane_indices[-1]] = lane_indices
            for first, second in zip(lane_indices, lane_indices[1:]):
                stair_longitudinal_expected += 1
                if second in adj[first]:
                    stair_longitudinal_existing += 1
                    continue
                if add_link(first, second, force=True):
                    stair_links += 1
                    stair_longitudinal_added += 1
                    continue
                d = math.hypot(
                    float(nodes[first]['x']) - float(nodes[second]['x']),
                    float(nodes[first]['y']) - float(nodes[second]['y']))
                dz = abs(
                    float(nodes[first]['z']) - float(nodes[second]['z']))
                rsum = (
                    radius_m_local(nodes[first]['b15'])
                    + radius_m_local(nodes[second]['b15']))
                if len(adj[first]) >= 10 or len(adj[second]) >= 10:
                    reject_reason = 'degree_cap'
                elif dz > max(0.90, 1.25*d):
                    reject_reason = 'vertical_limit'
                elif not _stair_support_path_ok(first, second):
                    reject_reason = 'support_path'
                elif d < 0.30:
                    reject_reason = 'too_close'
                elif d > max(7.4, 2.4*rsum):
                    reject_reason = 'too_far'
                elif link_blocked(first, second):
                    reject_reason = 'collision_blocked'
                else:
                    reject_reason = 'unknown'
                if _add_verified_stair_longitudinal(first, second):
                    stair_links += 1
                    stair_longitudinal_added += 1
                    stair_longitudinal_provenance_added += 1
                    stair_longitudinal_provenance_reasons[reject_reason] += 1
                    continue
                stair_longitudinal_rejects[reject_reason] += 1
                stair_longitudinal_reject_details.append({
                    'flight': int(_flight),
                    'lane': int(nodes[first]['stair_lane']),
                    'first': int(first),
                    'second': int(second),
                    'first_row': int(nodes[first]['stair_row']),
                    'second_row': int(nodes[second]['stair_row']),
                    'distance': round(float(d), 4),
                    'dz': round(float(dz), 4),
                    'first_degree': len(adj[first]),
                    'second_degree': len(adj[second]),
                    'reason': reject_reason,
                })
        for row_indices in flight_rows.values():
            row_indices.sort(key=lambda index: int(nodes[index]['stair_lane']))
            for first, second in zip(row_indices, row_indices[1:]):
                if add_link(first, second, force=True):
                    stair_links += 1

    # Quantized room cells deliberately vacate verified stair treads so the
    # sparse flight sampler, rather than the 0.50 m lattice, owns the stairs.
    # On a landing that continues beside/under the next retained stair row,
    # that leaves one special seam: the floor is level with the preceding
    # tread, while the nearest sparse stair node is already one row higher.
    # The normal collision ray then crosses the stair's broad support face and
    # mistakes it for a wall.  Authored MCOLYMPS links use exactly this seam.
    #
    # Keep the exception tied to the fitted stair provenance.  The candidate
    # must continue the direction of one adjacent lower row, return to that
    # row's exact support height, cross only one triangle, and encounter at
    # least one low hard segment whose clearance is no greater than the fitted
    # stair step.  A bare wall face has no such step segment and remains a
    # blocker.  The seam must also join components that were separate before
    # this repair, so unrelated wall collisions cannot become legal links.
    if quantize_to_map_grid and stair_indices:
        stair_row_lookup = {}
        for stair_index in stair_indices:
            flight = nodes[stair_index].get('stair_flight')
            lane = nodes[stair_index].get('stair_lane')
            row = nodes[stair_index].get('stair_row')
            if flight is None or lane is None or row is None:
                continue
            stair_row_lookup[(int(flight), int(lane), int(row))] = stair_index

        side_parent = list(range(len(nodes)))
        def _side_root(index):
            while side_parent[index] != index:
                side_parent[index] = side_parent[side_parent[index]]
                index = side_parent[index]
            return index
        def _side_union(first, second):
            first_root = _side_root(first)
            second_root = _side_root(second)
            if first_root != second_root:
                side_parent[second_root] = first_root
        for first_index, neighbors in enumerate(adj):
            for second_index in neighbors:
                if first_index < second_index:
                    _side_union(first_index, second_index)

        side_candidates = []
        for stair_index in stair_indices:
            stair_node = nodes[stair_index]
            flight = stair_node.get('stair_flight')
            lane = stair_node.get('stair_lane')
            row = stair_node.get('stair_row')
            if flight is None or lane is None or row is None:
                continue
            adjacent_rows = []
            for row_delta in (-1, 1):
                adjacent_index = stair_row_lookup.get(
                    (int(flight), int(lane), int(row) + row_delta))
                if adjacent_index is None:
                    continue
                if (float(nodes[adjacent_index]['z'])
                        < float(stair_node['z']) - 0.20):
                    adjacent_rows.append(adjacent_index)
            if not adjacent_rows:
                continue
            lower_index = max(
                adjacent_rows, key=lambda index: float(nodes[index]['z']))
            lower_node = nodes[lower_index]
            step_x = float(stair_node['x']) - float(lower_node['x'])
            step_y = float(stair_node['y']) - float(lower_node['y'])
            step_length = math.hypot(step_x, step_y)
            step_dz = float(stair_node['z']) - float(lower_node['z'])
            if (step_length < 0.30 or step_length > 2.0
                    or step_dz < 0.20 or step_dz > 1.25):
                continue
            for landing_x, landing_y, landing_index in link_grid.query(
                    stair_node['x'], stair_node['y'], 3.0):
                if (landing_index == stair_index
                        or landing_index in adj[stair_index]):
                    continue
                landing_node = nodes[landing_index]
                if (landing_node.get('tag') != 'quantized_hierarchy'
                        or not landing_node.get('quantized_map_grid', False)):
                    continue
                if abs(
                        float(landing_node['z'])
                        - float(lower_node['z'])) > 0.08:
                    continue
                if _side_root(stair_index) == _side_root(landing_index):
                    continue
                run_x = float(landing_x) - float(stair_node['x'])
                run_y = float(landing_y) - float(stair_node['y'])
                distance = math.hypot(run_x, run_y)
                if distance < 0.30 or distance > 2.8:
                    continue
                forward = (run_x*step_x + run_y*step_y) / step_length
                lateral = abs(run_x*step_y - run_y*step_x) / step_length
                if (forward < 0.30 or forward/distance < 0.86
                        or lateral > 0.65):
                    continue
                seam_dz = abs(
                    float(stair_node['z']) - float(landing_node['z']))
                if abs(seam_dz - step_dz) > 0.08:
                    continue

                ax = float(stair_node['x'])
                ay = float(stair_node['y'])
                bx = float(landing_node['x'])
                by = float(landing_node['y'])
                midpoint_x = 0.5*(ax+bx)
                midpoint_y = 0.5*(ay+by)
                blocker_triangles = []
                for triangle_index in _triangles3d_near(
                        midpoint_x, midpoint_y, distance*0.5+1.2):
                    if _segment_hits_triangle(
                            ax, ay, float(stair_node['z']),
                            bx, by, float(landing_node['z']),
                            triangles3d[triangle_index]):
                        blocker_triangles.append(int(triangle_index))
                if len(blocker_triangles) != 1:
                    quantized_stair_side_seam_rejects[
                        'triangle_count'] += 1
                    continue

                blocker_segments = []
                support_az = float(stair_node['z']) - 1.2489
                support_bz = float(landing_node['z']) - 1.2489
                for segment_index in _segs3d_near(
                        midpoint_x, midpoint_y, distance*0.5+1.2):
                    x1,y1,z1,x2,y2,z2,entity_index = segs3d[segment_index]
                    d1x = bx-ax
                    d1y = by-ay
                    d2x = x2-x1
                    d2y = y2-y1
                    denominator = d1x*d2y-d1y*d2x
                    if -1e-12 < denominator < 1e-12:
                        continue
                    t = ((x1-ax)*d2y-(y1-ay)*d2x)/denominator
                    if t <= 0.02 or t >= 0.98:
                        continue
                    u = ((x1-ax)*d1y-(y1-ay)*d1x)/denominator
                    if not 0.0 <= u <= 1.0:
                        continue
                    support_z = support_az + (support_bz-support_az)*t
                    segment_z = z1 + (z2-z1)*u
                    clearance = segment_z-support_z
                    if 0.20 <= clearance <= 1.80:
                        blocker_segments.append({
                            'entity': int(entity_index),
                            'clearance': round(float(clearance), 4),
                        })
                if not blocker_segments:
                    quantized_stair_side_seam_rejects[
                        'missing_step_segment'] += 1
                    continue
                if any(
                        float(blocker['clearance'])
                        > max(
                            GENERATOR_REACHABLE_STAIR_STEP + 0.08,
                            float(step_dz) + 0.08)
                        for blocker in blocker_segments):
                    quantized_stair_side_seam_rejects[
                        'hard_segment_above_step'] += 1
                    if len(quantized_stair_side_seam_reject_details) < 16:
                        quantized_stair_side_seam_reject_details.append({
                            'stair': int(stair_index),
                            'landing': int(landing_index),
                            'lower_row': int(lower_index),
                            'distance': round(float(distance), 4),
                            'step_dz': round(float(step_dz), 4),
                            'blocker_triangles': list(blocker_triangles),
                            'blocker_segments': blocker_segments,
                        })
                    continue
                component_pair = tuple(sorted((
                    int(_side_root(stair_index)),
                    int(_side_root(landing_index)))))
                side_candidates.append((
                    component_pair,
                    math.sqrt(distance*distance + seam_dz*seam_dz),
                    distance, stair_index, landing_index, lower_index,
                    forward, lateral, blocker_triangles,
                    list(blocker_segments)))

        accepted_per_component_pair = Counter()
        accepted_lanes_per_component_pair = defaultdict(set)
        for (
                component_pair, _distance_3d, distance, stair_index,
                landing_index, lower_index, forward, lateral,
                blocker_triangles, blocker_segments) in sorted(
                    side_candidates):
            lane_key = (
                int(nodes[stair_index].get('stair_flight', -1)),
                int(nodes[stair_index].get('stair_lane', -1)))
            if (accepted_per_component_pair[component_pair] >= 2
                    or lane_key in accepted_lanes_per_component_pair[
                        component_pair]
                    or len(adj[stair_index]) >= 10
                    or len(adj[landing_index]) >= 10):
                continue
            adj[stair_index].add(landing_index)
            adj[landing_index].add(stair_index)
            edge_key = tuple(sorted((stair_index, landing_index)))
            links[edge_key] = (
                float(distance), 'verified_quantized_stair_side_seam')
            stair_links += 1
            quantized_stair_side_seam_links += 1
            accepted_per_component_pair[component_pair] += 1
            accepted_lanes_per_component_pair[component_pair].add(lane_key)
            quantized_stair_side_seam_details.append({
                'stair': int(stair_index),
                'landing': int(landing_index),
                'lower_row': int(lower_index),
                'distance': round(float(distance), 4),
                'dz': round(abs(
                    float(nodes[stair_index]['z'])
                    - float(nodes[landing_index]['z'])), 4),
                'forward': round(float(forward), 4),
                'lateral': round(float(lateral), 4),
                'blocker_triangles': list(blocker_triangles),
                'blocker_segments': blocker_segments,
            })

    # Attach the first and last retained row of every lane directly to an
    # ordinary landing/floor node before any generic stair repair. Internal
    # rows must not consume their degree budget on nearby stacked floors and
    # leave the actual stair-to-balcony junction dependent on graph density.
    stair_endpoint_count = len(stair_endpoint_indices)
    for i in sorted(stair_endpoint_indices):
        endpoint_lane_indices = stair_endpoint_lanes.get(i)
        attached = None
        for j in adj[i]:
            if nodes[j].get('tag') == 'layered_stair':
                continue
            d = math.hypot(
                float(nodes[j]['x'])-float(nodes[i]['x']),
                float(nodes[j]['y'])-float(nodes[i]['y']))
            dz = abs(float(nodes[i]['z'])-float(nodes[j]['z']))
            allowed_dz = min(1.25, max(0.70, 0.75*d))
            if d <= 2.8 and dz <= allowed_dz:
                candidate = (math.sqrt(d*d+dz*dz), d, j)
                if attached is None or candidate < attached:
                    attached = candidate
        landing_candidates = []
        if attached is None:
            for px,py,j in link_grid.query(nodes[i]['x'],nodes[i]['y'],3.0):
                if i == j or nodes[j].get('tag') == 'layered_stair':
                    continue
                d = math.hypot(px-nodes[i]['x'],py-nodes[i]['y'])
                dz = abs(float(nodes[i]['z'])-float(nodes[j]['z']))
                allowed_dz = min(1.25, max(0.70, 0.75*d))
                if d > 2.8 or dz > allowed_dz:
                    continue
                landing_candidates.append(
                    (math.sqrt(d*d+dz*dz),d,j))
            for _distance_3d,d,j in sorted(landing_candidates):
                _direct_attached = add_link(i,j,force=True)
                _seam_attached = False
                _routed_attached = False
                if (not _direct_attached and endpoint_lane_indices is not None):
                    _seam_attached = _add_verified_stair_landing_seam(
                        i, j, endpoint_lane_indices)
                if not _direct_attached and not _seam_attached:
                    _routed_attached = _add_routed_stair_landing(i, j)
                if _direct_attached or _seam_attached or _routed_attached:
                    stair_links += 1
                    if _seam_attached:
                        stair_landing_seam_links += 1
                    attached = (_distance_3d,d,j)
                    break
        # If the ordinary grower skipped the doorway/landing node entirely,
        # the nearest established floor-network node can sit beyond the normal
        # 2.8 m direct-link envelope.  Do not give up just because that one
        # intermediate node is absent.  Search a small extended radius only for
        # an already-established NON-STAIR floor chain, then let the verified
        # reachable-support A* route create the minimum intermediate landing
        # bridge nodes.  This remains stair-local: no generic component joining
        # and no unsupported straight edge is introduced.
        if attached is None:
            extended_landing_candidates = []
            for px, py, j in link_grid.query(
                    nodes[i]['x'], nodes[i]['y'], 5.25):
                if i == j or nodes[j].get('tag') == 'layered_stair':
                    continue
                d = math.hypot(
                    float(px)-float(nodes[i]['x']),
                    float(py)-float(nodes[i]['y']))
                if d <= 2.8 or d > 5.0:
                    continue
                dz = abs(float(nodes[i]['z'])-float(nodes[j]['z']))
                if dz > 1.35:
                    continue
                # The distant target must already belong to ordinary floor
                # fabric.  A lone/spur node is not enough evidence to invent a
                # doorway route toward it.
                floor_neighbors = [
                    nb for nb in adj[j]
                    if nb != i and nodes[nb].get('tag') != 'layered_stair'
                ]
                if not floor_neighbors:
                    continue
                extended_landing_candidates.append(
                    (math.sqrt(d*d+dz*dz), d, j))
            for _distance_3d, d, j in sorted(extended_landing_candidates):
                if _add_routed_stair_landing(i, j):
                    stair_links += 1
                    attached = (_distance_3d, d, j)
                    break

        # A switchback may have no ordinary landing node because the next
        # verified flight endpoint already owns that support. Join those
        # endpoints directly instead of leaving a seed-dependent missing row.
        if attached is None:
            junction_candidates = []
            for j in stair_endpoint_indices:
                if i == j:
                    continue
                if (nodes[j].get('stair_flight')
                        == nodes[i].get('stair_flight')):
                    continue
                d = math.hypot(
                    float(nodes[j]['x'])-float(nodes[i]['x']),
                    float(nodes[j]['y'])-float(nodes[i]['y']))
                dz = abs(float(nodes[i]['z'])-float(nodes[j]['z']))
                stair_junction_attempt_details.append({
                    'endpoint': int(i), 'candidate': int(j),
                    'endpoint_flight': int(nodes[i].get('stair_flight', -1)),
                    'candidate_flight': int(nodes[j].get('stair_flight', -1)),
                    'distance': round(float(d), 4), 'dz': round(float(dz), 4),
                    'within_envelope': bool(d <= 2.8 and dz <= 1.25),
                })
                if d > 2.8 or dz > 1.25:
                    continue
                junction_candidates.append(
                    (math.sqrt(d*d+dz*dz),d,j))
            for _distance_3d,d,j in sorted(junction_candidates):
                already_linked = j in adj[i]
                generic_linked = (False if already_linked
                                  else add_link(i,j,force=True))
                support_ok = _stair_support_path_ok(i, j)
                collision_blocked = link_blocked(i, j)
                verified_linked = False
                if not already_linked and not generic_linked:
                    verified_linked = _add_verified_stair_junction(i, j)
                stair_junction_attempt_details.append({
                    'endpoint': int(i), 'candidate': int(j),
                    'endpoint_flight': int(nodes[i].get('stair_flight', -1)),
                    'candidate_flight': int(nodes[j].get('stair_flight', -1)),
                    'distance': round(float(d), 4),
                    'dz': round(abs(float(nodes[i]['z'])-float(nodes[j]['z'])), 4),
                    'within_envelope': True,
                    'already_linked': bool(already_linked),
                    'generic_linked': bool(generic_linked),
                    'support_ok': bool(support_ok),
                    'collision_blocked': bool(collision_blocked),
                    'verified_linked': bool(verified_linked),
                })
                if already_linked or generic_linked or verified_linked:
                    if not already_linked:
                        stair_links += 1
                    if verified_linked:
                        stair_junction_links += 1
                    attached = (_distance_3d,d,j)
                    break
        detail = {
            'node': int(i),
            'flight': int(nodes[i].get('stair_flight', -1)),
            'lane': int(nodes[i].get('stair_lane', -1)),
            'row': int(nodes[i].get('stair_row', -1)),
            'x': round(float(nodes[i]['x']), 5),
            'y': round(float(nodes[i]['y']), 5),
            'z': round(float(nodes[i]['z']), 5),
        }
        if attached is None:
            stair_endpoint_unattached_count += 1
            detail['attached'] = False
        else:
            _distance_3d,d,j = attached
            stair_endpoint_attached_count += 1
            stair_endpoint_max_link_distance = max(
                stair_endpoint_max_link_distance, float(d))
            detail.update({
                'attached': True,
                'distance': round(float(d), 4),
                'target': int(j),
                'target_tag': str(nodes[j].get('tag', '')),
                'target_x': round(float(nodes[j]['x']), 5),
                'target_y': round(float(nodes[j]['y']), 5),
                'target_z': round(float(nodes[j]['z']), 5),
            })
        stair_endpoint_details.append(detail)

    # Reaching one floor node is not enough when that node is itself a spur.
    # This is the MCOLYMPS balcony failure: the fitted flight reaches a valid
    # same-height landing node, but the ordinary distance limit omits the next
    # obvious edge into the already established balcony row. Repair only those
    # endpoint targets with no non-stair neighbour, and only toward a nearby
    # same-height node that already belongs to a floor chain.
    for _detail in stair_endpoint_details:
        if not _detail.get('attached'):
            continue
        _target = int(_detail.get('target', -1))
        if not 0 <= _target < len(nodes):
            continue
        if nodes[_target].get('tag') == 'layered_stair':
            continue
        _floor_neighbors = [
            _neighbor for _neighbor in adj[_target]
            if nodes[_neighbor].get('tag') != 'layered_stair'
        ]
        if _floor_neighbors:
            continue
        _network_candidates = []
        for _px, _py, _candidate in link_grid.query(
                nodes[_target]['x'], nodes[_target]['y'], 4.5):
            if (_candidate == _target
                    or nodes[_candidate].get('tag') == 'layered_stair'):
                continue
            _candidate_floor_neighbors = [
                _neighbor for _neighbor in adj[_candidate]
                if (_neighbor != _target
                    and nodes[_neighbor].get('tag') != 'layered_stair')
            ]
            if not _candidate_floor_neighbors:
                continue
            _distance = math.hypot(
                float(_px) - float(nodes[_target]['x']),
                float(_py) - float(nodes[_target]['y']))
            _dz = abs(
                float(nodes[_candidate]['z']) - float(nodes[_target]['z']))
            if _distance > 4.2 or _dz > 0.35:
                continue
            _network_candidates.append((_distance, _candidate))
        for _distance, _candidate in sorted(_network_candidates):
            if add_link(_target, _candidate, force=True):
                _edge_key = tuple(sorted((_target, _candidate)))
                links[_edge_key] = (
                    float(_distance), 'stair_landing_floor_network')
                stair_landing_network_links += 1
                stair_landing_network_details.append({
                    'landing': int(_target),
                    'network': int(_candidate),
                    'distance': round(float(_distance), 4),
                    'bridge_nodes': 0,
                    'z': round(float(nodes[_target]['z']), 5),
                })
                break
            stair_landing_network_rejects['direct_link'] += 1
            # The direct edge can legitimately be rejected by the stair/landing
            # collision geometry.  Fall through to the already-existing verified
            # support/coverage bridge repair below instead of abandoning this
            # landing candidate.
            if not _stair_support_path_ok(_target, _candidate):
                stair_landing_network_rejects['support_path'] += 1
                continue
            _target_radius = radius_m_local(nodes[_target]['b15'])
            _candidate_radius = radius_m_local(nodes[_candidate]['b15'])
            _coverage_gap = (
                float(_distance) - _target_radius - _candidate_radius)

            # When the two valid endpoint discs already cover the route, only
            # the missing short graph edge is wrong.
            if _coverage_gap <= 0.15:
                if not add_link(_target, _candidate, force=True):
                    continue
                _edge_key = tuple(sorted((_target, _candidate)))
                links[_edge_key] = (
                    float(_distance), 'stair_landing_floor_network')
                stair_landing_network_links += 1
                stair_landing_network_details.append({
                    'landing': int(_target),
                    'network': int(_candidate),
                    'distance': round(float(_distance), 4),
                    'coverage_gap': round(float(_coverage_gap), 4),
                    'bridge_nodes': 0,
                    'z': round(float(nodes[_target]['z']), 5),
                })
                break

            # A long edge alone would connect the graph while leaving the
            # balcony/corridor acceptance radii visibly uncovered. Try one
            # bridge first, then two when the locally valid radius is smaller.
            # Every bridge centre must resolve to the same reachable support
            # and pass ordinary clearance.
            _target_surface_z = float(nodes[_target]['z']) - 1.2489
            _bridge_plan = None
            for _bridge_count in (1, 2, 3, 4):
                _trial = []
                _trial_valid = True
                for _bridge_pos in range(1, _bridge_count + 1):
                    _amount = _bridge_pos / (_bridge_count + 1)
                    _bridge_x = (
                        float(nodes[_target]['x'])
                        + (float(nodes[_candidate]['x'])
                           - float(nodes[_target]['x'])) * _amount)
                    _bridge_y = (
                        float(nodes[_target]['y'])
                        + (float(nodes[_candidate]['y'])
                           - float(nodes[_target]['y'])) * _amount)
                    _bridge_surface_z = _surface_z_at(
                        _bridge_x, _bridge_y, _target_surface_z)
                    if (_bridge_surface_z is None
                            or abs(float(_bridge_surface_z)
                                   - _target_surface_z) > 0.35):
                        stair_landing_network_rejects[
                            'bridge_support'] += 1
                        _trial_valid = False
                        break
                    _bridge_b15, _bridge_b12, _bridge_family = classify(
                        _bridge_x, _bridge_y, float(_bridge_surface_z))
                    _bridge_b15 = min(16, int(_bridge_b15))
                    _bridge_used_fallback = _bridge_b15 <= 0
                    if _bridge_b15 <= 0:
                        # The ordinary 2-D fit can report zero beside a model
                        # tread/railing even though height-aware reachable
                        # support proves a valid narrow corridor. Select the
                        # largest conservative family that passes the real
                        # clearance test instead of discarding the bridge.
                        for _fallback_b15 in (16, 14, 12, 10, 8, 6, 4):
                            _fallback_radius = radius_m_local(_fallback_b15)
                            if walkable(
                                    _bridge_x, _bridge_y,
                                    max(0.20, _fallback_radius
                                        * NODE_RADIUS_CLEARANCE_FACTOR),
                                    surface_z=float(_bridge_surface_z)):
                                _bridge_b15 = int(_fallback_b15)
                                _bridge_b12 = 1
                                break
                        if _bridge_b15 <= 0:
                            stair_landing_network_rejects[
                                'bridge_no_fit'] += 1
                            _trial_valid = False
                            break
                    _bridge_radius = radius_m_local(_bridge_b15)
                    if not walkable(
                            _bridge_x, _bridge_y,
                            max((0.20 if _bridge_used_fallback else 0.62),
                                _bridge_radius
                                * NODE_RADIUS_CLEARANCE_FACTOR),
                            surface_z=float(_bridge_surface_z)):
                        stair_landing_network_rejects[
                            'bridge_walkable'] += 1
                        _trial_valid = False
                        break
                    _trial.append({
                        'x': float(_bridge_x),
                        'y': float(_bridge_y),
                        'surface_z': float(_bridge_surface_z),
                        'b12': int(_bridge_b12),
                        'b15': int(_bridge_b15),
                        'radius': float(_bridge_radius),
                    })
                if not _trial_valid:
                    continue
                _chain_points = [
                    (float(nodes[_target]['x']),
                     float(nodes[_target]['y']), _target_radius)
                ] + [
                    (_item['x'], _item['y'], _item['radius'])
                    for _item in _trial
                ] + [
                    (float(nodes[_candidate]['x']),
                     float(nodes[_candidate]['y']), _candidate_radius)
                ]
                _covered = True
                for _first_point, _second_point in zip(
                        _chain_points, _chain_points[1:]):
                    _segment_distance = math.hypot(
                        _second_point[0] - _first_point[0],
                        _second_point[1] - _first_point[1])
                    if (_segment_distance
                            > _first_point[2] + _second_point[2] + 0.15):
                        _covered = False
                        break
                if _covered:
                    _bridge_plan = _trial
                    break
                stair_landing_network_rejects['bridge_radius_gap'] += 1
            if not _bridge_plan:
                continue

            _bridge_indices = []
            for _item in _bridge_plan:
                _bridge_index = len(nodes)
                nodes.append({
                    'x': _item['x'],
                    'y': _item['y'],
                    'z': _item['surface_z'] + 1.2489,
                    'b12': _item['b12'],
                    'b15': _item['b15'],
                    'b16': 0,
                    'b17': 0,
                    'b18': 0,
                    'tag': 'layered_floor',
                    'family': 'stair_landing_bridge',
                })
                adj.append(set())
                link_grid.insert(_item['x'], _item['y'], _bridge_index)
                _bridge_indices.append(_bridge_index)
            _repair_chain = [_target] + _bridge_indices + [_candidate]
            _chain_links_ok = True
            for _first, _second in zip(
                    _repair_chain, _repair_chain[1:]):
                if not add_link(_first, _second, force=True):
                    _chain_links_ok = False
                    break
            if not _chain_links_ok:
                stair_landing_network_rejects['bridge_link'] += 1
                for _bridge_index in reversed(_bridge_indices):
                    for _neighbor in list(adj[_bridge_index]):
                        adj[_neighbor].discard(_bridge_index)
                        links.pop(
                            tuple(sorted((_neighbor, _bridge_index))), None)
                    nodes.pop()
                    adj.pop()
                link_grid = _V52Spatial(6.8)
                for _node_index, _node in enumerate(nodes):
                    link_grid.insert(
                        _node['x'], _node['y'], _node_index)
                continue
            for _first, _second in zip(
                    _repair_chain, _repair_chain[1:]):
                _edge_distance = math.hypot(
                    float(nodes[_first]['x']) - float(nodes[_second]['x']),
                    float(nodes[_first]['y']) - float(nodes[_second]['y']))
                links[tuple(sorted((_first, _second)))] = (
                    float(_edge_distance), 'stair_landing_floor_bridge')
            stair_landing_network_links += len(_repair_chain) - 1
            stair_landing_bridge_nodes += len(_bridge_indices)
            stair_landing_network_details.append({
                'landing': int(_target),
                'bridges': [int(_index) for _index in _bridge_indices],
                'network': int(_candidate),
                'distance': round(float(_distance), 4),
                'coverage_gap': round(float(_coverage_gap), 4),
                'bridge_nodes': len(_bridge_indices),
                'z': round(float(nodes[_target]['z']), 5),
            })
            break

    # Complete the short same-level fabric around every stair landing. The
    # ordinary angular pass can omit a visibly obvious edge after accepting a
    # different direction, while the forced stair attachment then survives at
    # a greater distance. Preserve all nodes and add only radius-overlapping,
    # collision-clear floor edges, capped at four floor directions per landing.
    _landing_roots = {
        int(_detail['target']) for _detail in stair_endpoint_details
        if (_detail.get('attached')
            and 0 <= int(_detail.get('target', -1)) < len(nodes)
            and nodes[int(_detail['target'])].get('tag') != 'layered_stair')
    }
    _landing_target_set = set(_landing_roots)
    for _root in sorted(_landing_roots):
        for _px, _py, _candidate in link_grid.query(
                nodes[_root]['x'], nodes[_root]['y'], 4.5):
            if nodes[_candidate].get('tag') == 'layered_stair':
                continue
            if abs(
                    float(nodes[_candidate]['z'])
                    - float(nodes[_root]['z'])) > 0.35:
                continue
            _landing_target_set.add(_candidate)
    _landing_targets = sorted(_landing_target_set)
    for _target in _landing_targets:
        _local_candidates = []
        for _px, _py, _candidate in link_grid.query(
                nodes[_target]['x'], nodes[_target]['y'], 4.5):
            if (_candidate == _target
                    or nodes[_candidate].get('tag') == 'layered_stair'
                    or _candidate in adj[_target]):
                continue
            _distance = math.hypot(
                float(_px) - float(nodes[_target]['x']),
                float(_py) - float(nodes[_target]['y']))
            _dz = abs(
                float(nodes[_candidate]['z']) - float(nodes[_target]['z']))
            _radius_sum = (
                radius_m_local(nodes[_target]['b15'])
                + radius_m_local(nodes[_candidate]['b15']))
            if _dz > 0.35 or _distance > _radius_sum + 0.15:
                continue
            _local_candidates.append((_distance, _candidate))
        for _distance, _candidate in sorted(_local_candidates):
            _floor_neighbors = [
                _neighbor for _neighbor in adj[_target]
                if nodes[_neighbor].get('tag') != 'layered_stair'
            ]
            if len(_floor_neighbors) >= 4:
                break
            _angle = math.atan2(
                float(nodes[_candidate]['y']) - float(nodes[_target]['y']),
                float(nodes[_candidate]['x']) - float(nodes[_target]['x']))
            _direction_covered = False
            for _neighbor in _floor_neighbors:
                _neighbor_angle = math.atan2(
                    float(nodes[_neighbor]['y']) - float(nodes[_target]['y']),
                    float(nodes[_neighbor]['x']) - float(nodes[_target]['x']))
                _delta = abs(
                    (_angle - _neighbor_angle + math.pi)
                    % math.tau - math.pi)
                if _delta < math.radians(30.0):
                    _direction_covered = True
                    break
            if _direction_covered:
                continue
            if not add_link(_target, _candidate, force=True):
                continue
            links[tuple(sorted((_target, _candidate)))] = (
                float(_distance), 'stair_landing_local_floor')
            stair_landing_local_links += 1
            stair_landing_local_details.append({
                'landing': int(_target),
                'floor': int(_candidate),
                'distance': round(float(_distance), 4),
                'z': round(float(nodes[_target]['z']), 5),
            })

    # Repair only endpoints and any chain node that still lacks two proven
    # neighbours. Fully connected internal rows need no proximity links.
    stair_repair_indices = sorted(
        stair_endpoint_indices
        | {index for index in stair_indices if len(adj[index]) < 2})
    for i in stair_repair_indices:
        candidates = []
        for px,py,j in link_grid.query(nodes[i]['x'],nodes[i]['y'],3.0):
            if i == j:
                continue
            d = math.hypot(px-nodes[i]['x'],py-nodes[i]['y'])
            dz = abs(float(nodes[i]['z'])-float(nodes[j]['z']))
            if d > 2.8 or dz > max(0.90,1.25*d):
                continue
            candidates.append((math.sqrt(d*d+dz*dz),j))
        for _distance_3d,j in sorted(candidates):
            if len(adj[i]) >= 4:
                break
            if add_link(i,j,force=True):
                stair_links += 1

    # A vertical ladder is not a sampled walkable surface.  Consequently the
    # floor/stair flood can correctly generate both landings yet leave them as
    # separate components because there is no support height between them.
    # Detect only regular physical rung stacks from this seed's 3-D collision,
    # then add a narrow precision chain when two different generated
    # components terminate beside the bottom and top of that exact stack.
    # This is deliberately enabled only with rooftop generation: ordinary
    # Z-on/Z-off seeds must not acquire speculative vertical shortcuts.
    if (bool(include_ladders)
            and bool(getattr(z_at, 'include_rooftops', False))
            and segs3d):
        _rung_groups = defaultdict(list)
        def _collect_rung_edge(
                _x1, _y1, _z1, _x2, _y2, _z2, _entity):
            _dz = abs(float(_z2) - float(_z1))
            _length = math.hypot(
                float(_x2) - float(_x1), float(_y2) - float(_y1))
            if _dz > 0.08 or not (0.30 <= _length <= 2.25):
                return
            _mid_x = 0.5 * (float(_x1) + float(_x2))
            _mid_y = 0.5 * (float(_y1) + float(_y2))
            _angle = (
                math.degrees(math.atan2(
                    float(_y2) - float(_y1),
                    float(_x2) - float(_x1))) % 180.0)
            _group_key = (
                int(_entity),
                round(_mid_x * 2.0) / 2.0,
                round(_mid_y * 2.0) / 2.0,
                round(_angle / 10.0) * 10.0,
            )
            _rung_groups[_group_key].append((
                0.5 * (float(_z1) + float(_z2)),
                _mid_x, _mid_y, _length))
        for _segment in segs3d:
            _collect_rung_edge(*_segment)
        # Walkable-support edges are intentionally omitted from the hard
        # segment set.  Ladder rungs in several CModels live in that class, so
        # derive the same horizontal-edge evidence from the already local,
        # disc-filtered support triangles as well.
        for (_ax, _ay, _az, _bx, _by, _bz,
             _cx3, _cy3, _cz3, _entity) in triangles3d:
            _collect_rung_edge(
                _ax, _ay, _az, _bx, _by, _bz, _entity)
            _collect_rung_edge(
                _bx, _by, _bz, _cx3, _cy3, _cz3, _entity)
            _collect_rung_edge(
                _cx3, _cy3, _cz3, _ax, _ay, _az, _entity)

        _rung_profiles = []
        for _group_key, _rows in _rung_groups.items():
            _raw_levels = sorted(float(_row[0]) for _row in _rows)
            _level_clusters = []
            for _level in _raw_levels:
                if (_level_clusters
                        and _level - _level_clusters[-1][-1] <= 0.14):
                    _level_clusters[-1].append(_level)
                else:
                    _level_clusters.append([_level])
            _levels = [
                sum(_cluster) / len(_cluster)
                for _cluster in _level_clusters
            ]
            if len(_levels) < 6 or _levels[-1] - _levels[0] < 1.50:
                continue
            _steps = [
                _second - _first
                for _first, _second in zip(_levels, _levels[1:])
            ]
            _median_step = sorted(_steps)[len(_steps) // 2]
            if not (0.20 <= _median_step <= 0.68):
                continue
            _regular_steps = sum(
                abs(_step - _median_step) <= 0.18
                for _step in _steps)
            if _regular_steps < max(4, int(math.ceil(len(_steps) * 0.70))):
                continue
            _anchor_x = sum(float(_row[1]) for _row in _rows) / len(_rows)
            _anchor_y = sum(float(_row[2]) for _row in _rows) / len(_rows)
            _rung_profiles.append({
                'entity': int(_group_key[0]),
                'x': float(_anchor_x),
                'y': float(_anchor_y),
                'min_z': float(_levels[0]),
                'max_z': float(_levels[-1]),
                'levels': len(_levels),
                'step': float(_median_step),
            })

        # A ladder can contribute duplicate collision edges at almost the same
        # position.  Keep the strongest regular profile per physical shaft.
        _rung_profiles.sort(
            key=lambda _profile: (
                -int(_profile['levels']),
                -(float(_profile['max_z']) - float(_profile['min_z']))))
        _accepted_profiles = []
        for _profile in _rung_profiles:
            if any(
                    int(_other['entity']) == int(_profile['entity'])
                    and math.hypot(
                        float(_other['x']) - float(_profile['x']),
                        float(_other['y']) - float(_profile['y'])) <= 0.75
                    and min(float(_other['max_z']), float(_profile['max_z']))
                    >= max(float(_other['min_z']), float(_profile['min_z']))
                    - 0.30
                    for _other in _accepted_profiles):
                continue
            _accepted_profiles.append(_profile)
        ladder_rung_profiles = len(_accepted_profiles)

        def _ladder_component_labels():
            _labels = {}
            _component_index = 0
            for _root in range(len(nodes)):
                if _root in _labels:
                    continue
                _labels[_root] = _component_index
                _pending = [_root]
                while _pending:
                    _current = _pending.pop()
                    for _neighbor in adj[_current]:
                        if _neighbor in _labels:
                            continue
                        _labels[_neighbor] = _component_index
                        _pending.append(_neighbor)
                _component_index += 1
            return _labels

        _component_labels = _ladder_component_labels()
        _prior_source_to_index = {
            int(_node['source_id']): int(_index)
            for _index, _node in enumerate(prior_nodes)
        }
        _prior_adjacency = [set() for _node in prior_nodes]
        for _prior_index, _prior_node in enumerate(prior_nodes):
            for _neighbor_source in _prior_node.get(
                    'source_neighbors', ()):
                _neighbor_index = _prior_source_to_index.get(
                    int(_neighbor_source))
                if (_neighbor_index is None
                        or _neighbor_index == _prior_index):
                    continue
                _prior_adjacency[_prior_index].add(_neighbor_index)
                _prior_adjacency[_neighbor_index].add(_prior_index)
        _prior_component_labels = {}
        _prior_component_index = 0
        for _prior_root in range(len(prior_nodes)):
            if _prior_root in _prior_component_labels:
                continue
            _prior_component_labels[_prior_root] = _prior_component_index
            _pending = [_prior_root]
            while _pending:
                _current = _pending.pop()
                for _neighbor in _prior_adjacency[_current]:
                    if _neighbor in _prior_component_labels:
                        continue
                    _prior_component_labels[_neighbor] = (
                        _prior_component_index)
                    _pending.append(_neighbor)
            _prior_component_index += 1
        for _profile in _accepted_profiles:
            _nearby = []
            for _px, _py, _node_index in link_grid.query(
                    float(_profile['x']), float(_profile['y']), 3.0):
                _distance = math.hypot(
                    float(_px) - float(_profile['x']),
                    float(_py) - float(_profile['y']))
                if _distance > 2.75 or len(adj[_node_index]) >= 9:
                    continue
                _node_z_value = float(nodes[_node_index]['z'])
                if not (float(_profile['min_z']) - 1.25
                        <= _node_z_value
                        <= float(_profile['max_z']) + 1.75):
                    continue
                _nearby.append((
                    False, int(_node_index), _distance, _node_z_value,
                    ('new', int(_component_labels.get(_node_index, -1))),
                    -1))
            # Existing nodes are valid ladder endpoints too.  This is what
            # makes "Z first, rooftop second" equivalent to one combined
            # rooftop pass.  They remain read-only here; the worker applies
            # the explicitly returned old/new seam after this batch finishes.
            for _px, _py, _prior_index in prior_grid.query(
                    float(_profile['x']), float(_profile['y']), 3.0):
                _distance = math.hypot(
                    float(_px) - float(_profile['x']),
                    float(_py) - float(_profile['y']))
                if _distance > 2.75:
                    continue
                _node_z_value = float(prior_nodes[_prior_index]['z'])
                if not (float(_profile['min_z']) - 1.25
                        <= _node_z_value
                        <= float(_profile['max_z']) + 1.75):
                    continue
                _source_id = int(prior_nodes[_prior_index]['source_id'])
                _nearby.append((
                    True, int(_prior_index), _distance, _node_z_value,
                    ('old', int(_prior_component_labels.get(
                        _prior_index, -1))), _source_id))
            _best_pair = None
            for (_lower_prior, _lower, _lower_distance, _lower_z,
                 _lower_component, _lower_source_id) in _nearby:
                for (_upper_prior, _upper, _upper_distance, _upper_z,
                     _upper_component, _upper_source_id) in _nearby:
                    if ((_lower_prior == _upper_prior
                                and _lower == _upper)
                            or _lower_component == _upper_component
                            or _upper_z <= _lower_z + 1.00):
                        continue
                    _vertical_gap = _upper_z - _lower_z
                    _rung_span = (
                        float(_profile['max_z'])
                        - float(_profile['min_z']))
                    if _vertical_gap > _rung_span + 2.00:
                        continue
                    if (_lower_z > float(_profile['max_z']) + 0.25
                            or _upper_z < float(_profile['min_z']) - 0.25):
                        continue
                    _score = (
                        _lower_distance + _upper_distance
                        + 0.10 * _vertical_gap)
                    _candidate_pair = (
                        _score, _vertical_gap,
                        _lower_distance + _upper_distance,
                        int(_lower_prior), int(_lower),
                        int(_upper_prior), int(_upper),
                        _lower_source_id, _upper_source_id)
                    if _best_pair is None or _candidate_pair < _best_pair:
                        _best_pair = _candidate_pair
            if _best_pair is None:
                continue

            (_score, _vertical_gap, _endpoint_distance,
             _lower_prior, _lower, _upper_prior, _upper,
             _lower_source_id, _upper_source_id) = _best_pair
            _bridge_count = max(1, int(math.ceil(_vertical_gap / 0.90)) - 1)
            if _bridge_count > 6:
                continue
            _bridge_indices = []
            _lower_node = (
                prior_nodes[_lower] if _lower_prior else nodes[_lower])
            _upper_node = (
                prior_nodes[_upper] if _upper_prior else nodes[_upper])
            _lower_z = float(_lower_node['z'])
            _upper_z = float(_upper_node['z'])
            for _position in range(1, _bridge_count + 1):
                _amount = _position / (_bridge_count + 1)
                _bridge_index = len(nodes)
                _bridge_z = _lower_z + (_upper_z - _lower_z) * _amount
                nodes.append({
                    'x': float(_profile['x']),
                    'y': float(_profile['y']),
                    'z': float(_bridge_z),
                    'b12': 1,
                    'b15': 8,
                    'b16': 0,
                    'b17': 0,
                    'b18': 0,
                    'tag': 'layered_stair',
                    'family': 'ladder_bridge',
                    'ladder_entity': int(_profile['entity']),
                })
                adj.append(set())
                link_grid.insert(
                    float(_profile['x']), float(_profile['y']),
                    _bridge_index)
                _bridge_indices.append(_bridge_index)
            _full_chain_points = [
                (float(_lower_node['x']), float(_lower_node['y']),
                 float(_lower_node['z']))
            ] + [
                (float(nodes[_index]['x']), float(nodes[_index]['y']),
                 float(nodes[_index]['z']))
                for _index in _bridge_indices
            ] + [
                (float(_upper_node['x']), float(_upper_node['y']),
                 float(_upper_node['z']))
            ]
            _chain_valid = True
            for _first_point, _second_point in zip(
                    _full_chain_points, _full_chain_points[1:]):
                _distance_3d = math.sqrt(
                    (_first_point[0] - _second_point[0]) ** 2
                    + (_first_point[1] - _second_point[1]) ** 2
                    + (_first_point[2] - _second_point[2]) ** 2)
                if _distance_3d > 3.10:
                    _chain_valid = False
                    break
            if not _chain_valid:
                for _bridge_index in reversed(_bridge_indices):
                    nodes.pop()
                    adj.pop()
                link_grid = _V52Spatial(6.8)
                for _node_index, _node in enumerate(nodes):
                    link_grid.insert(
                        _node['x'], _node['y'], _node_index)
                continue
            _chain = (
                ([] if _lower_prior else [_lower])
                + _bridge_indices
                + ([] if _upper_prior else [_upper]))
            for _first, _second in zip(_chain, _chain[1:]):
                adj[_first].add(_second)
                adj[_second].add(_first)
                _edge_key = tuple(sorted((_first, _second)))
                _edge_distance = math.hypot(
                    float(nodes[_first]['x']) - float(nodes[_second]['x']),
                    float(nodes[_first]['y']) - float(nodes[_second]['y']))
                links[_edge_key] = (
                    float(_edge_distance), 'ladder_rung_bridge')
                ladder_bridge_links_added += 1
            if _lower_prior:
                nodes[_bridge_indices[0]].setdefault(
                    'ladder_old_seam_ids', []).append(
                        int(_lower_source_id))
                ladder_old_new_seams.append((
                    int(_lower_source_id), int(_bridge_indices[0])))
            if _upper_prior:
                nodes[_bridge_indices[-1]].setdefault(
                    'ladder_old_seam_ids', []).append(
                        int(_upper_source_id))
                ladder_old_new_seams.append((
                    int(_upper_source_id), int(_bridge_indices[-1])))
            ladder_bridge_chains += 1
            ladder_bridge_nodes_added += len(_bridge_indices)
            ladder_bridge_details.append({
                'entity': int(_profile['entity']),
                'x': round(float(_profile['x']), 4),
                'y': round(float(_profile['y']), 4),
                'rung_min_z': round(float(_profile['min_z']), 4),
                'rung_max_z': round(float(_profile['max_z']), 4),
                'lower': int(_lower_source_id if _lower_prior else _lower),
                'upper': int(_upper_source_id if _upper_prior else _upper),
                'lower_prior': bool(_lower_prior),
                'upper_prior': bool(_upper_prior),
                'lower_z': round(_lower_z, 4),
                'upper_z': round(_upper_z, 4),
                'anchors': [int(_index) for _index in _bridge_indices],
            })
            # Later profiles must see the newly joined component.
            _component_labels = _ladder_component_labels()

    # A support discontinuity on small clutter can occasionally produce one
    # centre sample but no proven transition edge.  Such a node cannot aid
    # navigation and is exactly the isolated visual debris this pass is meant
    # to eliminate.  Remove only degree-zero layered_stair nodes; valid stair
    # endpoints linked directly to an ordinary landing remain untouched.
    isolated_stair_nodes = {
        index for index, node in enumerate(nodes)
        if node.get('tag') == 'layered_stair' and not adj[index]
    }
    isolated_stair_pruned = len(isolated_stair_nodes)
    if isolated_stair_nodes:
        old_to_new = {}
        kept_nodes = []
        for old_index, node in enumerate(nodes):
            if old_index in isolated_stair_nodes:
                continue
            old_to_new[old_index] = len(kept_nodes)
            kept_nodes.append(node)
        kept_adj = [set() for _node in kept_nodes]
        kept_links = {}
        for (old_a, old_b), link_value in links.items():
            if old_a not in old_to_new or old_b not in old_to_new:
                continue
            new_a = old_to_new[old_a]
            new_b = old_to_new[old_b]
            kept_adj[new_a].add(new_b)
            kept_adj[new_b].add(new_a)
            kept_links[tuple(sorted((new_a, new_b)))] = link_value
        ladder_old_new_seams = [
            (int(_old_id), int(old_to_new[_local_index]))
            for _old_id, _local_index in ladder_old_new_seams
            if _local_index in old_to_new
        ]
        for _detail in ladder_bridge_details:
            _detail['anchors'] = [
                int(old_to_new[_index])
                for _index in _detail.get('anchors', ())
                if _index in old_to_new
            ]
        nodes = kept_nodes
        adj = kept_adj
        links = kept_links
        link_grid = _V52Spatial(6.8)
        for node_index, node in enumerate(nodes):
            link_grid.insert(node['x'], node['y'], node_index)

    def comps():
        seen=set(); out=[]
        for i in range(len(nodes)):
            if i in seen:
                continue
            q=[i]; seen.add(i); comp=[]
            while q:
                u=q.pop(); comp.append(u)
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v); q.append(v)
            out.append(comp)
        return out

    # ---- fake-floor island filter ---------------------------------------
    # A slab hanging over walkable space it does not link to is not floor an
    # NPC can use.  Discriminator validated in v93_45 against mcolymps: a
    # non-largest link component where >=60% of its nodes have BOTH a model
    # support surface within 2.0 m below AND another node >=2.5 m below within
    # 3.0 m XY.  A real but unlinked ground room scores 0% on the second test
    # and is kept, which is what pure component deletion gets wrong.
    #
    # Order matters: this must run BEFORE component repair.  Joining an island
    # into the main graph destroys the isolation the classifier reads, and a
    # fake floor then becomes permanent instead of removable.
    #
    # DEFAULT OFF, deliberately.  Verified on the MCOLYMPS top floor: it flags
    # a 19-node run along the bottom of the balcony -- real walkway, same strip,
    # inside the same walls -- purely because a missing link at the corner
    # leaves it isolated.  A balcony over an atrium satisfies both criteria by
    # construction, so the island test is doing all the work, and an unlinked
    # real walkway is indistinguishable from a slab until the link is repaired.
    # Do not enable until adjacent-pair linking closes those corners; until
    # then this deletes real floor.
    fake_floor_islands = 0
    fake_floor_nodes = 0
    if (not quantize_to_map_grid
            and globals().get('GENERATOR_FAKE_FLOOR_FILTER', False)):
        _ff_layers = getattr(z_at, 'model_layers', None) if callable(z_at) else None
        _ff_origin = getattr(z_at, 'grid_origin', None) if callable(z_at) else None
        if _ff_layers and _ff_origin is not None:
            _ff_ox, _ff_oy = _ff_origin
            _ff_cell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25)
            _ff_grid = _V52Spatial(4.0)
            for _fi, _fn in enumerate(nodes):
                _ff_grid.insert(_fn['x'], _fn['y'], _fi)

            def _ff_slab_below(i):
                n = nodes[i]
                sz = float(n['z']) - 1.2489
                gi = int(round((n['x'] - _ff_ox) / _ff_cell))
                gj = int(round((n['y'] - _ff_oy) / _ff_cell))
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        for v in _ff_layers.get((gi + di, gj + dj), ()):
                            if sz - 2.0 <= float(v) <= sz + 0.35:
                                return True
                return False

            def _ff_node_below(i):
                n = nodes[i]
                for px, py, j in _ff_grid.query(n['x'], n['y'], 3.0):
                    if j == i:
                        continue
                    if float(n['z']) - float(nodes[j]['z']) < 2.5:
                        continue
                    if math.hypot(px - n['x'], py - n['y']) <= 3.0:
                        return True
                return False

            _ff_doomed = set()
            for _ff_component in sorted(comps(), key=len, reverse=True)[1:]:
                _ff_hits = 0
                for _fi in _ff_component:
                    if _ff_slab_below(_fi) and _ff_node_below(_fi):
                        _ff_hits += 1
                if _ff_hits >= 0.60 * len(_ff_component):
                    fake_floor_islands += 1
                    _ff_doomed.update(_ff_component)
            if _ff_doomed:
                fake_floor_nodes = len(_ff_doomed)
                for _fi in _ff_doomed:
                    for _fk in list(adj[_fi]):
                        adj[_fk].discard(_fi)
                        links.pop(tuple(sorted((_fi, _fk))), None)
                    adj[_fi].clear()
                _ff_keep = [i for i in range(len(nodes)) if i not in _ff_doomed]
                _ff_remap = {old: new for new, old in enumerate(_ff_keep)}
                nodes = [nodes[i] for i in _ff_keep]
                adj = [set(_ff_remap[j] for j in adj[i] if j in _ff_remap)
                       for i in _ff_keep]
                _ff_relinked = {}
                for (_a, _b), _v in links.items():
                    if _a in _ff_remap and _b in _ff_remap:
                        _ff_relinked[tuple(sorted((_ff_remap[_a],
                                                   _ff_remap[_b])))] = _v
                links.clear()
                links.update(_ff_relinked)
                # Every spatial index keyed by node index is now stale.
                node_grid = _V52Spatial(5.5)
                link_grid = _V52Spatial(6.8)
                for _fi, _fn in enumerate(nodes):
                    node_grid.insert(_fn['x'], _fn['y'], _fi)
                    link_grid.insert(_fn['x'], _fn['y'], _fi)

    # Join graph components with the closest legal edge, considering every
    # component rather than only the second largest.  The previous loop
    # examined cs[1] alone and broke out of the whole repair on the first
    # failure, so a single unjoinable component left every remaining one
    # stranded -- the reason a 46-component result reported repair_links = 0.
    # This is Kruskal over inter-component pairs: shortest first, each edge
    # still subject to the ordinary add_link legality gates.  It joins graph
    # components; it does not and cannot substitute for missing coverage.
    repair_links=0
    repair_attempts=0
    if not quantize_to_map_grid:
        _repair_cs = sorted(comps(), key=len, reverse=True)
        if len(_repair_cs) > 1:
            _repair_comp_of = {}
            for _ci, _c in enumerate(_repair_cs):
                for _v in _c:
                    _repair_comp_of[_v] = _ci
            _repair_grid = _V52Spatial(7.4)
            for _i, _n in enumerate(nodes):
                _repair_grid.insert(_n['x'], _n['y'], _i)
            _repair_pairs = []
            for _i, _n in enumerate(nodes):
                for _px, _py, _j in _repair_grid.query(_n['x'], _n['y'], 7.4):
                    if _j <= _i:
                        continue
                    if _repair_comp_of[_i] == _repair_comp_of[_j]:
                        continue
                    _d = math.hypot(_n['x'] - _px, _n['y'] - _py)
                    if _d <= 7.4:
                        _repair_pairs.append((_d, _i, _j))
            _repair_pairs.sort()
            _repair_parent = list(range(len(_repair_cs)))

            def _repair_root(index):
                while _repair_parent[index] != index:
                    _repair_parent[index] = _repair_parent[_repair_parent[index]]
                    index = _repair_parent[index]
                return index

            _repair_attempt_cap = int(globals().get(
                'GENERATOR_COMPONENT_REPAIR_ATTEMPTS', 4000))
            for _d, _i, _j in _repair_pairs:
                if repair_attempts >= _repair_attempt_cap:
                    break
                _ra = _repair_root(_repair_comp_of[_i])
                _rb = _repair_root(_repair_comp_of[_j])
                if _ra == _rb:
                    continue
                repair_attempts += 1
                if add_link(_i, _j, force=True):
                    _repair_parent[_rb] = _ra
                    repair_links += 1

    # Remove long bypass links only when a short local alternative already
    # exists. Never disconnect the graph and never strand degree-low nodes.
    pruned_links = 0
    def _edge_reachable_after_remove(src, dst):
        seen={src}; q=[src]
        while q:
            u=q.pop()
            if u == dst:
                return True
            for v in adj[u]:
                if v not in seen:
                    seen.add(v); q.append(v)
        return False

    def _short_two_hop_alternative(i,j,d):
        for k in adj[i].intersection(adj[j]):
            dik=math.hypot(nodes[i]['x']-nodes[k]['x'],nodes[i]['y']-nodes[k]['y'])
            dkj=math.hypot(nodes[k]['x']-nodes[j]['x'],nodes[k]['y']-nodes[j]['y'])
            if dik+dkj<=1.20*d:
                return True
        return False

    _edges_for_prune=[]
    for (i,j),(d,role) in list(links.items()):
        ri=radius_m_local(nodes[i]['b15']); rj=radius_m_local(nodes[j]['b15'])
        target=max(1.15, 1.56*0.5*(ri+rj))
        _edges_for_prune.append((d/max(target,1e-6), d, target, i, j))
    for ratio,d,target,i,j in sorted(_edges_for_prune,reverse=True):
        if pruned_links >= 128:
            break
        if len(adj[i]) <= 2 or len(adj[j]) <= 2:
            continue
        if j not in adj[i] or i not in adj[j]:
            continue
        if not (_intervening_node(i,j) or _short_two_hop_alternative(i,j,d)):
            continue
        adj[i].remove(j); adj[j].remove(i)
        if _edge_reachable_after_remove(i,j):
            links.pop(tuple(sorted((i,j))), None)
            pruned_links += 1
        else:
            adj[i].add(j); adj[j].add(i)

    # ---- graph contract audit ------------------------------------------
    # Placement and linking never validate against each other: coverage decides
    # where nodes go, linking decides edges, and neither checks the other's
    # result.  That produces nodes carrying no route -- their disc lies wholly
    # inside their neighbours' coverage and the graph routes around them -- and
    # edges laid straight over a node that should have carried the route
    # (forcing skips the intervening-node test entirely).
    #
    # This judges the finished graph; it never moves or adds a node.  Room
    # texture is produced by the walker and measures better than the authored
    # reference already, so placement stays untouched.  The room-safety
    # property is unique coverage: a node in a room is the only disc over some
    # of its floor, so it can never satisfy the deletion test.
    contract_links_added = 0
    contract_edges_removed = 0
    contract_nodes_removed = 0
    contract_link_rejects = Counter()
    contract_removed_indices = set()
    if not quantize_to_map_grid and globals().get('GENERATOR_GRAPH_CONTRACT', True):
        _contract_grid = _V52Spatial(6.0)
        for _ci, _cn in enumerate(nodes):
            _contract_grid.insert(_cn['x'], _cn['y'], _ci)

        # The growth-time floors are texture rules and are far stricter than the
        # authored data.  Over 212,470 campaign links, 15.4% are shorter than
        # the 0.85 m minimum distance and p1 of d/(r1+r2) is 0.450, against a
        # 0.56 growth floor.  A repair stage asking "is this pair genuinely
        # adjacent and unobstructed?" should use the authored envelope; growth
        # keeps its own thresholds so room texture is unaffected.
        _CONTRACT_MIN_D = float(globals().get('GENERATOR_CONTRACT_MIN_D', 0.30))
        _CONTRACT_OVERLAP = float(globals().get(
            'GENERATOR_CONTRACT_OVERLAP_FACTOR', 0.45))

        def _contract_try_link(i, j):
            """Link an adjacent pair, or name the gate that refuses it."""
            if j in adj[i]:
                return 'linked'
            if len(adj[i]) >= 10 or len(adj[j]) >= 10:
                return 'degree_cap'
            d = math.hypot(nodes[i]['x'] - nodes[j]['x'],
                           nodes[i]['y'] - nodes[j]['y'])
            dz = abs(float(nodes[i]['z']) - float(nodes[j]['z']))
            if dz > max(0.45, 0.75 * d):
                return 'dz_limit'
            rsum = (radius_m_local(nodes[i]['b15'])
                    + radius_m_local(nodes[j]['b15']))
            if d < _CONTRACT_MIN_D:
                return 'coincident'
            if d > max(2.7, 1.45 * rsum):
                return 'too_far'
            if d < _CONTRACT_OVERLAP * rsum:
                return 'overlap_floor'
            if link_blocked(i, j):
                return 'blocked_geometry'
            if _intervening_node(i, j):
                return 'intervening_node'
            adj[i].add(j)
            adj[j].add(i)
            links[tuple(sorted((i, j)))] = (float(d), 'graph_contract')
            return 'added'

        # A. Two discs overlapping on the same level are adjacent by the
        #    authored contract (median linked-pair disc gap 0.000 m over 88,262
        #    campaign nodes).  Offer every such unlinked pair to the ordinary
        #    gates, and record what refuses the remainder so a missing edge is a
        #    stated reason instead of silence.
        _contract_pairs = []
        for _ci, _cn in enumerate(nodes):
            _cri = radius_m_local(_cn['b15'])
            for _cpx, _cpy, _cj in _contract_grid.query(_cn['x'], _cn['y'], 6.0):
                if _cj <= _ci or _cj in adj[_ci]:
                    continue
                _co = nodes[_cj]
                if abs(float(_co['z']) - float(_cn['z'])) > 0.60:
                    continue
                _cd = math.hypot(_cn['x'] - _cpx, _cn['y'] - _cpy)
                if _cd > _cri + radius_m_local(_co['b15']):
                    continue
                _contract_pairs.append((_cd, _ci, _cj))
        _contract_pairs.sort()
        for _cd, _ci, _cj in _contract_pairs:
            _why = _contract_try_link(_ci, _cj)
            if _why == 'added':
                contract_links_added += 1
            elif _why != 'linked':
                contract_link_rejects[_why] += 1

        # B. An edge must not run over a node that already carries that route,
        #    including an edge added with force -- forcing skips the ordinary
        #    intervening-node test, which is how a long edge ends up laid across
        #    a node sitting directly on it.
        def _edge_shadowed_by(i, j):
            ax, ay = nodes[i]['x'], nodes[i]['y']
            bx, by = nodes[j]['x'], nodes[j]['y']
            az = float(nodes[i]['z'])
            bz = float(nodes[j]['z'])
            vx, vy = bx - ax, by - ay
            d2 = vx * vx + vy * vy
            if d2 <= 1e-9:
                return None
            d = math.sqrt(d2)
            corridor = max(0.35, min(0.75, 0.18 * d))
            mx, my = (ax + bx) * 0.5, (ay + by) * 0.5
            for px, py, k in _contract_grid.query(mx, my, d * 0.5 + corridor):
                if k == i or k == j:
                    continue
                t = ((px - ax) * vx + (py - ay) * vy) / d2
                if t <= 0.15 or t >= 0.85:
                    continue
                if abs(float(nodes[k]['z']) - (az + (bz - az) * t)) > 1.0:
                    continue
                qx, qy = ax + t * vx, ay + t * vy
                if math.hypot(px - qx, py - qy) > corridor:
                    continue
                if k in adj[i] and k in adj[j]:
                    return k
            return None

        for _ck in list(links.keys()):
            _ci, _cj = _ck
            if _cj not in adj[_ci]:
                continue
            if _edge_shadowed_by(_ci, _cj) is None:
                continue
            adj[_ci].discard(_cj)
            adj[_cj].discard(_ci)
            if _edge_reachable_after_remove(_ci, _cj):
                links.pop(_ck, None)
                contract_edges_removed += 1
            else:
                adj[_ci].add(_cj)
                adj[_cj].add(_ci)

        # C. A node whose disc adds no floor of its own and whose removal costs
        #    no route is pure cost.  Both conditions, never one.
        _contract_layers = (getattr(z_at, 'reachable_layers', None)
                            if callable(z_at) else None)
        _contract_origin = (getattr(z_at, 'grid_origin', None)
                            if callable(z_at) else None)
        _cgcell = float(getattr(z_at, 'grid_cell', 0.25) or 0.25)
        if _contract_layers and _contract_origin is not None:
            _cgox, _cgoy = _contract_origin
        else:
            _cgox = _cgoy = None
        _contract_prior_query = max(
            [radius_m_local(n['b15']) for n in prior_nodes] or [0.0]) + 0.10

        def _has_unique_coverage(i):
            """Prove whether this node owns any sampled part of its disc.

            Reachable-layer data makes the proof floor-aware when automatic
            Z/interior filtering is enabled.  With that optional feature off,
            sample the entire disc instead.  That fallback is deliberately
            conservative: invalid/solid-side samples retain a node, but a node
            completely swallowed by other discs can no longer escape cleanup.
            Prior-batch discs participate too, which closes the additive-seed
            case where a small new node survives only because its covering node
            belongs to the previous generation transaction.
            """
            n = nodes[i]
            r = radius_m_local(n['b15'])
            node_z = float(n['z'])
            surface_z = node_z - 1.2489
            step = max(_cgcell, 0.4)
            steps = int(max(1, round(r / step)))
            unique_samples = 0
            # Late repair families are allowed deep campaign-style overlap, but
            # one 0.4 m fringe sample is not enough justification for leaving a
            # tiny island node under an otherwise complete chain.
            required_unique_samples = (
                2 if n.get('tag') in (
                    'campaign_hole_repair', 'coverage_fill')
                and globals().get(
                    'GENERATOR_MINIMAL_REPAIR_CONTRACT', True)
                and not _model_supported_at(
                    n['x'], n['y'], surface_z) else 1)
            for gi in range(-steps, steps + 1):
                for gj in range(-steps, steps + 1):
                    px = n['x'] + gi * step
                    py = n['y'] + gj * step
                    if math.hypot(px - n['x'], py - n['y']) > r:
                        continue
                    if _contract_layers and _contract_origin is not None:
                        vals = _contract_layers.get(
                            (int(round((px - _cgox) / _cgcell)),
                             int(round((py - _cgoy) / _cgcell))))
                        if not vals:
                            continue
                        if not any(abs(float(v) - surface_z) <= 0.35
                                   for v in vals):
                            continue
                    shared = False
                    for ox2, oy2, k in _contract_grid.query(px, py, 5.0):
                        if k == i or k in contract_removed_indices:
                            continue
                        o = nodes[k]
                        if abs(float(o['z']) - node_z) > 0.50:
                            continue
                        if math.hypot(ox2 - px, oy2 - py) <= radius_m_local(
                                o['b15']):
                            shared = True
                            break
                    if not shared and _contract_prior_query > 0.10:
                        for ox2, oy2, k in prior_grid.query(
                                px, py, _contract_prior_query):
                            o = prior_nodes[k]
                            if abs(float(o['z']) - node_z) > 0.50:
                                continue
                            if math.hypot(ox2 - px, oy2 - py) <= radius_m_local(
                                    o['b15']):
                                shared = True
                                break
                    if not shared:
                        unique_samples += 1
                        if unique_samples >= required_unique_samples:
                            return True
            return False

        def _route_survives_without(i):
            nbrs = [k for k in adj[i] if k not in contract_removed_indices]
            if len(nbrs) <= 1:
                return True
            start = nbrs[0]
            pending = set(nbrs[1:])
            seen = {start, i}
            stack = [start]
            visited = 0
            while stack and pending:
                u = stack.pop()
                visited += 1
                if visited > 6000:
                    return False
                for v in adj[u]:
                    if v == i or v in contract_removed_indices or v in seen:
                        continue
                    seen.add(v)
                    pending.discard(v)
                    stack.append(v)
            return not pending

        for _ci in range(len(nodes)):
            if nodes[_ci].get('tag') in (
                    'seed', 'seed_fallback', 'layered_stair'):
                continue
            if any(nodes[_nb].get('tag') == 'layered_stair'
                   for _nb in adj[_ci]):
                # A globally redundant landing node can still be the local
                # attachment that makes a stair endpoint usable.  Stair
                # topology takes precedence over disc contraction.
                continue
            if _has_unique_coverage(_ci):
                continue
            if not _route_survives_without(_ci):
                continue
            for _ck2 in list(adj[_ci]):
                adj[_ck2].discard(_ci)
                links.pop(tuple(sorted((_ci, _ck2))), None)
            adj[_ci].clear()
            contract_removed_indices.add(_ci)
            contract_nodes_removed += 1

        if contract_removed_indices:
            _keep = [i for i in range(len(nodes))
                     if i not in contract_removed_indices]
            _remap = {old: new for new, old in enumerate(_keep)}
            nodes = [nodes[i] for i in _keep]
            adj = [set(_remap[j] for j in adj[i] if j in _remap) for i in _keep]
            _relinked = {}
            for (_a, _b), _v in links.items():
                if _a in _remap and _b in _remap:
                    _relinked[tuple(sorted((_remap[_a], _remap[_b])))] = _v
            links.clear()
            links.update(_relinked)
            # Every spatial index keyed by node index is now stale.
            node_grid = _V52Spatial(5.5)
            link_grid = _V52Spatial(6.8)
            for _ni, _nn in enumerate(nodes):
                node_grid.insert(_nn['x'], _nn['y'], _ni)
                link_grid.insert(_nn['x'], _nn['y'], _ni)

    _progress(93, 'Auditing final graph', 'Measuring coverage, components, degree, and overlap')
    _audit = _gen_audit.audit_graph(
        nodes, prior_nodes, corridor_dominated, adj, links,
        cx, cy, R, quantize_to_map_grid,
        walkable, nearest_hard, radius_m_local)
    missing_final = _audit['missing_final']
    strict_missing_reason_counts = _audit['strict_missing_reason_counts']
    total_samples = _audit['total_samples']
    covered_samples = _audit['covered_samples']
    strict_covered_samples = _audit['strict_covered_samples']
    comps_final = _audit['components']
    largest = _audit['largest']
    degs = _audit['degs']
    b15_counts = _audit['b15_counts']
    b12_counts = _audit['b12_counts']
    tag_counts = _audit['tag_counts']
    family_counts = _audit['family_counts']
    heavy = _audit['heavy']
    if quantize_to_map_grid:
        _qlc = _audit['quantized_link_counts']
        quantized_axis_link_count = _qlc['axis']
        quantized_inherited_link_count = _qlc['inherited']
        quantized_layer_skeleton_link_count = _qlc['layer_skeleton']
        quantized_layer_local_link_count = _qlc['layer_local']
        quantized_cardinal_gap_link_count = _qlc['cardinal_gap']
        quantized_fallback_link_count = _qlc['fallback']

    _progress(96, 'Writing node metadata', 'Finalizing direction and navigation byte fields')
    # B16 = largest-angular-gap midpoint of final links (campaign formula, offset 215)
    for i,n in enumerate(nodes):
        _nbs=sorted(adj[i])
        if EXTENDED_B12 and len(_nbs) >= 2:
            _angs=sorted(math.atan2(nodes[j]['y']-n['y'], nodes[j]['x']-n['x']) for j in _nbs)
            _gaps=[((_angs[(k+1)%len(_angs)]-_angs[k]) % math.tau, k) for k in range(len(_angs))]
            _g,_k=max(_gaps)
            _mid=math.degrees((_angs[_k]+_g*0.5) % math.tau)
            n['b16']=int(round(((_mid - 215.0) % 360.0)/360.0*256.0)) % 256
            if n['b16']==0: n['b16']=1
            n['b12']=int(n['b12']) | 4
        else:
            # Default: no fake directional metadata. Preserve classify() parity
            # only (0 radius-nav / 1 center-nav), keep b16 inert.
            n['b16']=0
            n['b12']=int(n['b12']) & 1

    # Final stair-topology audit after every pruning/contraction pass.  The
    # earlier counters describe attempted repairs; these counters prove the
    # links that actually survive into the exported graph.
    final_stair_lanes = {}
    for stair_index, stair_node in enumerate(nodes):
        if stair_node.get('tag') != 'layered_stair':
            continue
        flight = stair_node.get('stair_flight')
        lane = stair_node.get('stair_lane')
        row = stair_node.get('stair_row')
        if flight is None or lane is None or row is None:
            continue
        final_stair_lanes.setdefault(
            (int(flight), int(lane)), []).append(stair_index)
    final_stair_longitudinal_expected = 0
    final_stair_longitudinal_present = 0
    final_stair_longitudinal_missing = []
    for (flight, lane), lane_indices in sorted(final_stair_lanes.items()):
        lane_indices.sort(key=lambda index: int(nodes[index]['stair_row']))
        for first, second in zip(lane_indices, lane_indices[1:]):
            final_stair_longitudinal_expected += 1
            if second in adj[first]:
                final_stair_longitudinal_present += 1
            else:
                final_stair_longitudinal_missing.append({
                    'flight': int(flight),
                    'lane': int(lane),
                    'first': int(first),
                    'second': int(second),
                    'first_row': int(nodes[first]['stair_row']),
                    'second_row': int(nodes[second]['stair_row']),
                })
    final_stair_cross_row_links = 0
    final_stair_rung_links = 0
    for first in range(len(nodes)):
        first_node = nodes[first]
        if first_node.get('tag') != 'layered_stair':
            continue
        for second in adj[first]:
            if second <= first:
                continue
            second_node = nodes[second]
            if (
                    second_node.get('tag') != 'layered_stair'
                    or first_node.get('stair_flight')
                    != second_node.get('stair_flight')):
                continue
            row_delta = abs(
                int(first_node.get('stair_row', -1000))
                - int(second_node.get('stair_row', 1000)))
            lane_delta = abs(
                int(first_node.get('stair_lane', -1000))
                - int(second_node.get('stair_lane', 1000)))
            if row_delta == 1 and lane_delta >= 1:
                final_stair_cross_row_links += 1
            elif row_delta == 0 and lane_delta >= 1:
                final_stair_rung_links += 1

    # Later cleanup/contraction passes can reindex ladder anchors.  Resolve
    # requested old/new seams from markers on the surviving final nodes rather
    # than trusting their pre-prune indices.
    ladder_old_new_seams = [
        (int(_old_id), int(_node_index))
        for _node_index, _node in enumerate(nodes)
        for _old_id in _node.get('ladder_old_seam_ids', ())
    ]

    lab_nodes=[]
    for i,n in enumerate(nodes):
        lab_nodes.append(LabNode(
            n['x'], n['y'], n['z'], n['b12'], n['b15'],
            n.get('b16',0), n.get('b17',0), n.get('b18',0),
            sorted(adj[i])[:16], n.get('tag',''),
            n.get('quantized_source_keys'), bool(n.get(
                'quantized_map_grid', quantize_to_map_grid))))

    metrics={
        'version':'v64_17_inherited_cardinal_topology',
        'nodes':len(lab_nodes),
        'links':len(links),
        'center':(round(cx,3),round(cy,3)),
        'focus_radius':R,
        'placement_mode':('quantized_map_grid' if quantize_to_map_grid else 'natural_walker'),
        'quantized_grid_m':(quantized_grid_m if quantize_to_map_grid else None),
        'quantized_base_pitch_m':(float(GENERATOR_QUANTIZED_BASE_PITCH_M)
                                  if quantize_to_map_grid else None),
        'quantized_base_candidate_count':(quantized_base_candidate_count
                                          if quantize_to_map_grid else None),
        'quantized_stair_base_cells_reserved':(
            int(quantized_stair_base_cells_reserved)
            if quantize_to_map_grid else 0),
        'quantized_doorway_bridge_cells':(
            int(lattice_bridge_count)
            if quantize_to_map_grid else 0),
        'quantized_doorway_rejected_cells':(
            len(lattice_doorway_rejected)
            if quantize_to_map_grid else 0),
        'quantized_solid_not_walkable':(
            _lattice_solid_not_walkable
            if quantize_to_map_grid else 0),
        'quantized_promotions':(quantized_promotion_metrics
                                if quantize_to_map_grid else None),
        'quantized_partial_promotions':(quantized_partial_promotion_metrics
                                         if quantize_to_map_grid else None),
        'quantized_optimizer':(quantized_optimizer_metrics
                               if quantize_to_map_grid else None),
        'quantized_axis_links':(quantized_axis_link_count
                                if quantize_to_map_grid else None),
        'quantized_inherited_links':(quantized_inherited_link_count
                                     if quantize_to_map_grid else None),
        'quantized_layer_candidates':(quantized_layer_candidate_count
                                      if quantize_to_map_grid else None),
        'quantized_layer_support_rejects':(
            quantized_layer_support_reject_count
            if quantize_to_map_grid else None),
        'quantized_layer_skeleton_links':(
            quantized_layer_skeleton_link_count
            if quantize_to_map_grid else None),
        'quantized_layer_local_links':(
            quantized_layer_local_link_count
            if quantize_to_map_grid else None),
        'quantized_cardinal_gap_candidates':(
            quantized_cardinal_gap_candidate_count
            if quantize_to_map_grid else None),
        'quantized_cardinal_gap_support_rejects':(
            quantized_cardinal_gap_support_reject_count
            if quantize_to_map_grid else None),
        'quantized_cardinal_gap_collision_rejects':(
            quantized_cardinal_gap_collision_reject_count
            if quantize_to_map_grid else None),
        'quantized_cardinal_gap_links':(
            quantized_cardinal_gap_link_count
            if quantize_to_map_grid else None),
        'quantized_cardinal_gap_details':(
            quantized_cardinal_gap_details
            if quantize_to_map_grid else None),
        'quantized_fallback_links':(quantized_fallback_link_count
                                     if quantize_to_map_grid else None),
        'ordinary_radius_refit':(ordinary_radius_refit_metrics
                                 if not quantize_to_map_grid else None),
        'node_z':node_z,
        'b15_counts':b15_counts,
        'b12_counts':b12_counts,
        'tag_counts':tag_counts,
        'family_counts':family_counts,
        'grow_candidate_checks':grow_candidate_checks,
        'grow_walkable_sec':round(grow_walkable_sec, 4),
        'grow_placement_sec':round(grow_placement_sec, 4),
        'placement_profile':{
            key: (round(value, 4) if key.endswith('_sec') else int(value))
            for key, value in sorted(placement_profile.items())
        },
        'repair_added':repair_added,
        'walker_regrow_seeds':walker_regrow_seeds,
        'lane_snapped_nodes':lane_snapped_nodes[0],
        'coverage_fill_added':coverage_fill_added,
        'coverage_fill_offered':coverage_fill_offered,
        'coverage_fill_rejects':dict(sorted(coverage_fill_rejects.items())),
        'coverage_relaxation':coverage_relaxation_metrics,
        'repair_links':repair_links,
        'ordinary_support_path_rejects':int(ordinary_support_path_rejects),
        'ordinary_support_path_reject_details':ordinary_support_path_reject_details,
        'stair_links':stair_links,
        'stair_endpoint_count':stair_endpoint_count,
        'stair_endpoint_attached':stair_endpoint_attached_count,
        'stair_endpoint_unattached':stair_endpoint_unattached_count,
        'stair_endpoint_max_link_distance':round(
            float(stair_endpoint_max_link_distance), 4),
        'stair_endpoint_details':stair_endpoint_details,
        'stair_landing_network_links':stair_landing_network_links,
        'stair_landing_network_details':stair_landing_network_details,
        'stair_landing_bridge_nodes':stair_landing_bridge_nodes,
        'stair_landing_network_rejects':dict(
            sorted(stair_landing_network_rejects.items())),
        'stair_landing_local_links':stair_landing_local_links,
        'stair_landing_local_details':stair_landing_local_details,
        'stair_landing_seam_links':stair_landing_seam_links,
        'stair_landing_seam_details':stair_landing_seam_details,
        'stair_junction_links':stair_junction_links,
        'stair_junction_details':stair_junction_details,
        'stair_junction_attempt_details':stair_junction_attempt_details,
        'stair_routed_landing_links':stair_routed_landing_links,
        'stair_routed_landing_bridge_nodes':stair_routed_landing_bridge_nodes,
        'stair_routed_landing_details':stair_routed_landing_details,
        'stair_routed_landing_rejects':dict(stair_routed_landing_rejects),
        'stair_routed_landing_reject_details':stair_routed_landing_reject_details,
        'quantized_stair_side_seam_links':(
            quantized_stair_side_seam_links
            if quantize_to_map_grid else None),
        'quantized_stair_side_seam_details':(
            quantized_stair_side_seam_details
            if quantize_to_map_grid else None),
        'quantized_stair_side_seam_rejects':(
            dict(sorted(quantized_stair_side_seam_rejects.items()))
            if quantize_to_map_grid else None),
        'quantized_stair_side_seam_reject_details':(
            quantized_stair_side_seam_reject_details
            if quantize_to_map_grid else None),
        'stair_longitudinal_expected':stair_longitudinal_expected,
        'stair_longitudinal_existing':stair_longitudinal_existing,
        'stair_longitudinal_added':stair_longitudinal_added,
        'stair_longitudinal_provenance_added':
            stair_longitudinal_provenance_added,
        'stair_longitudinal_provenance_reasons':dict(
            sorted(stair_longitudinal_provenance_reasons.items())),
        'stair_longitudinal_rejects':dict(
            sorted(stair_longitudinal_rejects.items())),
        'stair_longitudinal_reject_details':stair_longitudinal_reject_details,
        'final_stair_longitudinal_expected':
            final_stair_longitudinal_expected,
        'final_stair_longitudinal_present':
            final_stair_longitudinal_present,
        'final_stair_longitudinal_missing':
            final_stair_longitudinal_missing,
        'final_stair_cross_row_links':final_stair_cross_row_links,
        'final_stair_rung_links':final_stair_rung_links,
        'isolated_stair_pruned':isolated_stair_pruned,
        'ladder_rung_profiles':ladder_rung_profiles,
        'ladder_bridge_chains':ladder_bridge_chains,
        'ladder_bridge_nodes_added':ladder_bridge_nodes_added,
        'ladder_bridge_links_added':ladder_bridge_links_added,
        'ladder_bridge_details':ladder_bridge_details,
        'ladder_old_new_seams':ladder_old_new_seams,
        'include_ladders':bool(include_ladders),
        'layered_added':layered_added,
        'layered_stair_added':layered_stair_added,
        'layered_stair_samples':layered_stair_sample_count,
        'layered_stair_candidates':layered_stair_candidate_count,
        'layered_stair_false_floor_nodes_dominated':int(
            layered_stair_false_floor_nodes_dominated),
        'transition_flights':transition_flight_count,
        'transition_lanes':transition_lane_count,
        'transition_flight_ranges':transition_flight_ranges,
        'transition_merge':dict(transition_merge_metrics),
        'transition_slope_sec':transition_slope_sec,
        'transition_network_states':transition_network_state_count,
        'transition_network_floor_candidates':transition_network_floor_candidate_count,
        'layered_floor_added':layered_floor_added,
        'rooftop_added':rooftop_added,
        'pruned_links':pruned_links,
        'fake_floor_islands':fake_floor_islands,
        'fake_floor_nodes':fake_floor_nodes,
        'contract_links_added':contract_links_added,
        'contract_edges_removed':contract_edges_removed,
        'contract_nodes_removed':contract_nodes_removed,
        'contract_link_rejects':dict(sorted(contract_link_rejects.items())),
        'disc_walkable_samples':total_samples,
        'disc_covered_samples':covered_samples,
        'disc_coverage_pct':round(covered_samples/max(1,total_samples)*100,2),
        'strict_disc_margin_m':0.20,
        'strict_disc_covered_samples':strict_covered_samples,
        'strict_disc_coverage_pct':round(
            strict_covered_samples/max(1,total_samples)*100,2),
        'strict_missing_sample_count':max(
            0, total_samples-strict_covered_samples),
        'strict_missing_reason_counts':dict(
            sorted(strict_missing_reason_counts.items())),
        'missing_sample_count':len(missing_final),
        'missing_reason_counts':dict(sorted(Counter(m['reason'] for m in missing_final).items())),
        'component_count':len(comps_final),
        'largest_component_pct':round(largest/max(1,len(lab_nodes))*100,2),
        'degree_min':min(degs) if degs else 0,
        'degree_median':sorted(degs)[len(degs)//2] if degs else 0,
        'degree_zero':sum(1 for d in degs if d==0),
        'heavy_overlap_count':heavy,
        'reject_counts':dict(reject),
        'solid_hull_count':len(hulls),
        'solid_hull_skipped_open_models':int(solid_hull_skipped),
        'standing_volume_index':(standing_volume_index.metrics()
                                 if standing_volume_index is not None else None),
        'rng_seed':rng_seed,
        'core_time_sec':round(_time.perf_counter()-t0,4),
    }
    if debug_probe is not None:
        metrics['debug_probe'] = {
            'label': debug_probe.get('label', ''),
            'record_count': len(debug_probe_records),
            'reason_counts': dict(sorted(Counter(r['reason'] for r in debug_probe_records).items())),
            'final_gate_counts': dict(sorted(Counter(r['final_gate'] for r in debug_probe_records).items())),
            'solid_layer_counts': dict(sorted(Counter(
                layer for r in debug_probe_records for layer in (r.get('solid_layers_2d') or ['none'])
            ).items())),
            'height_aware_collision': bool(height_aware_collision),
            'corridor_fit_record_count': len(corridor_fit_debug_records),
            'corridor_fit_reason_counts': dict(sorted(Counter(
                r['reason'] for r in corridor_fit_debug_records).items())),
            'corridor_fit_records': corridor_fit_debug_records,
            'records': debug_probe_records,
        }
        if _trace_provenance:
            metrics['debug_probe']['provenance'] = {
                'record_count': len(provenance_debug_records),
                'kind_counts': dict(sorted(Counter(
                    r.get('kind', '') for r in
                    provenance_debug_records).items())),
                'reason_counts': dict(sorted(Counter(
                    r.get('reason', '') for r in provenance_debug_records
                    if r.get('reason')).items())),
                'records': provenance_debug_records,
                'final_nodes': [
                    {
                        'final_index': int(i),
                        'creation_index': n.get('creation_index'),
                        'parent_index': n.get('parent_index'),
                        'x': round(float(n['x']), 4),
                        'y': round(float(n['y']), 4),
                        'z': round(float(n['z']), 4),
                        'b12': int(n.get('b12', 0)),
                        'b15': int(n.get('b15', 0)),
                        'tag': str(n.get('tag', '')),
                        'family': str(n.get('family', '')),
                        'requested_mode': str(
                            n.get('requested_mode', '')),
                        'spacing_mode': str(n.get('spacing_mode', '')),
                        'corridor_axis': n.get('corridor_axis'),
                        'corridor_hint': n.get('corridor_hint'),
                        'corridor_trial': n.get('corridor_trial'),
                        'neighbors': sorted(int(v) for v in adj[i])[:16],
                    }
                    for i, n in enumerate(nodes)
                ],
            }
    log(f"v58 seed-disc: nodes={metrics['nodes']} links={metrics['links']} coverage={metrics['disc_coverage_pct']}% repairs={repair_added} comps={metrics['component_count']}")
    _progress(98, 'Core generation complete', 'Passing the generated graph back to the editor')
    return {'nodes':lab_nodes, 'links':links, 'metrics':metrics}

# ── END V58 SEED DISC + CAMPAIGN-STYLE REPAIR GENERATOR ───────────────


# The active normal generator is generate_seed_disc_campaign_v58().



# ─────────────────────────────────────────────────────────────────────────────

# GENERATOR_CORE_END
