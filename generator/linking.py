"""Generator navigation link building — visibility mesh, cardinal fabric, gap seams."""
import math
from collections import Counter

from generator.support import GENERATOR_REACHABLE_MAX_STEP


def build_links(nodes, adj, links, link_grid, _progress,
                add_link, link_blocked, _intervening_node,
                _direction_redundant,
                _support_path_ok, radius_m_local,
                quantize_to_map_grid,
                quantized_axis_link_count=0):
    """Build the navigation link graph.

    Mutates adj and links in place via add_link.
    Returns a dict of link-phase metrics.
    """
    metrics = {
        'quantized_inherited_link_count': 0,
        'quantized_layer_skeleton_link_count': 0,
        'quantized_layer_local_link_count': 0,
        'quantized_cardinal_gap_candidate_count': 0,
        'quantized_cardinal_gap_support_reject_count': 0,
        'quantized_cardinal_gap_collision_reject_count': 0,
        'quantized_cardinal_gap_link_count': 0,
        'quantized_cardinal_gap_details': [],
        'quantized_layer_candidate_count': 0,
        'quantized_layer_support_reject_count': 0,
        'quantized_fallback_link_count': 0,
    }

    if quantize_to_map_grid:
        _build_quantized_links(
            nodes, adj, links, link_grid,
            add_link, _direction_redundant, _support_path_ok,
            link_blocked, radius_m_local, metrics)

    candidate_edges = []
    candidates_by_node = [[] for _ in nodes]
    for i, n in enumerate(nodes):
        if quantize_to_map_grid:
            continue
        _link_query_r = 9.2
        for px, py, j in link_grid.query(n['x'], n['y'], _link_query_r):
            if i >= j:
                continue
            d = math.hypot(px - n['x'], py - n['y'])
            dz = abs(float(n['z']) - float(nodes[j]['z']))
            if dz > max(0.45, 0.75 * d):
                continue
            ri = radius_m_local(n['b15'])
            rj = radius_m_local(nodes[j]['b15'])
            rsum = ri + rj
            _candidate_min = (max(0.30, 0.56 * rsum)
                              if quantize_to_map_grid else
                              max(0.85, 0.56 * rsum))
            if d < _candidate_min or d > max(2.7, 1.45 * rsum):
                continue
            if link_blocked(i, j) or _intervening_node(i, j):
                continue
            candidate_edges.append((d, i, j))
            candidates_by_node[i].append((d, j))
            candidates_by_node[j].append((d, i))

    candidate_edges.sort()

    _progress(
        80,
        'Building graph backbone',
        ('Completing inherited cardinal fabric with controlled visibility directions'
         if quantize_to_map_grid else
         'Connecting nearest visible neighbors'),
    )
    for d, i, j in candidate_edges:
        if len(adj[i]) >= 2 and len(adj[j]) >= 2:
            continue
        if _direction_redundant(i, j) or _direction_redundant(j, i):
            continue
        add_link(i, j)

    fabric_degree_limit = 5 if quantize_to_map_grid else 6
    for d, i, j in candidate_edges:
        if j in adj[i]:
            continue
        if (len(adj[i]) >= fabric_degree_limit
                or len(adj[j]) >= fabric_degree_limit):
            continue
        if _direction_redundant(i, j) or _direction_redundant(j, i):
            continue
        add_link(i, j)

    for i, cand in enumerate(candidates_by_node):
        if len(adj[i]) >= 2:
            continue
        for d, j in sorted(cand):
            if len(adj[i]) >= 2:
                break
            add_link(i, j)

    if quantize_to_map_grid:
        m = metrics
        m['quantized_fallback_link_count'] = max(
            0, len(links) - quantized_axis_link_count
            - m['quantized_inherited_link_count']
            - m['quantized_layer_skeleton_link_count']
            - m['quantized_layer_local_link_count']
            - m['quantized_cardinal_gap_link_count'])

    return metrics


def _build_quantized_links(nodes, adj, links, link_grid,
                           add_link, _direction_redundant, _support_path_ok,
                           link_blocked, radius_m_local, metrics):
    """Build quantized cardinal fabric, layer links, and gap seams."""
    cell_owner = {}
    for node_index, node in enumerate(nodes):
        for source_key in node.get('quantized_source_keys', ()):
            cell_owner[source_key] = node_index
    inherited_boundaries = Counter()
    for (cell_qx, cell_qy), owner_index in cell_owner.items():
        for step_qx, step_qy in ((2, 0), (0, 2)):
            other_index = cell_owner.get(
                (cell_qx + step_qx, cell_qy + step_qy))
            if other_index is None or other_index == owner_index:
                continue
            edge_key = tuple(sorted((owner_index, other_index)))
            inherited_boundaries[edge_key] += 1
    inherited_candidates = sorted(
        inherited_boundaries,
        key=lambda pair: (
            math.hypot(nodes[pair[0]]['x'] - nodes[pair[1]]['x'],
                       nodes[pair[0]]['y'] - nodes[pair[1]]['y']),
            -inherited_boundaries[pair], pair))

    # Kruskal spanning skeleton over inherited cardinal borders
    union_parent = list(range(len(nodes)))

    def _topology_root(index):
        while union_parent[index] != index:
            union_parent[index] = union_parent[union_parent[index]]
            index = union_parent[index]
        return index

    def _topology_union(first, second):
        first_root = _topology_root(first)
        second_root = _topology_root(second)
        if first_root != second_root:
            union_parent[second_root] = first_root

    for inherited_i, inherited_j in inherited_candidates:
        if _topology_root(inherited_i) == _topology_root(inherited_j):
            continue
        if add_link(inherited_i, inherited_j, force=True):
            _topology_union(inherited_i, inherited_j)
            edge_key = tuple(sorted((inherited_i, inherited_j)))
            edge_distance = math.hypot(
                nodes[inherited_i]['x'] - nodes[inherited_j]['x'],
                nodes[inherited_i]['y'] - nodes[inherited_j]['y'])
            links[edge_key] = (
                edge_distance, 'quantized_cardinal_inherited')
            metrics['quantized_inherited_link_count'] += 1

    for inherited_i, inherited_j in inherited_candidates:
        if inherited_j in adj[inherited_i]:
            continue
        if len(adj[inherited_i]) >= 5 or len(adj[inherited_j]) >= 5:
            continue
        if (_direction_redundant(inherited_i, inherited_j)
                or _direction_redundant(inherited_j, inherited_i)):
            continue
        if add_link(inherited_i, inherited_j, force=True):
            edge_key = tuple(sorted((inherited_i, inherited_j)))
            edge_distance = math.hypot(
                nodes[inherited_i]['x'] - nodes[inherited_j]['x'],
                nodes[inherited_i]['y'] - nodes[inherited_j]['y'])
            links[edge_key] = (
                edge_distance, 'quantized_cardinal_inherited')
            metrics['quantized_inherited_link_count'] += 1

    # Layer fabric for ownerless floor/rooftop nodes
    quantized_layer_nodes = {
        node_index for node_index, node in enumerate(nodes)
        if (bool(node.get('quantized_map_grid', False))
            and not (node.get('quantized_source_keys', ()) or ())
            and node.get('tag') in ('layered_floor', 'rooftop'))
    }
    quantized_layer_candidates = []
    quantized_layer_candidate_pairs = set()
    for layer_index in sorted(quantized_layer_nodes):
        layer_node = nodes[layer_index]
        for other_x, other_y, other_index in link_grid.query(
                layer_node['x'], layer_node['y'], 9.2):
            if other_index == layer_index:
                continue
            pair = tuple(sorted((layer_index, other_index)))
            if pair in quantized_layer_candidate_pairs:
                continue
            other_node = nodes[other_index]
            if other_node.get('tag') == 'layered_stair':
                continue
            distance = math.hypot(
                float(other_x) - float(layer_node['x']),
                float(other_y) - float(layer_node['y']))
            dz = abs(
                float(layer_node['z']) - float(other_node['z']))
            if dz > max(0.45, 0.75 * distance):
                continue
            radius_sum = (
                radius_m_local(layer_node['b15'])
                + radius_m_local(other_node['b15']))
            if (distance < max(0.30, 0.56 * radius_sum)
                    or distance > max(2.7, 1.45 * radius_sum)):
                continue
            quantized_layer_candidate_pairs.add(pair)
            quantized_layer_candidates.append(
                (distance, layer_index, other_index, dz))
    quantized_layer_candidates.sort()
    metrics['quantized_layer_candidate_count'] = len(
        quantized_layer_candidates)

    quantized_layer_support_cache = {}
    quantized_layer_failed_pairs = set()

    def _quantized_layer_support_ok(first, second, dz):
        if dz <= GENERATOR_REACHABLE_MAX_STEP + 0.08:
            return True
        pair = tuple(sorted((first, second)))
        cached = quantized_layer_support_cache.get(pair)
        if cached is not None:
            return cached
        accepted = _support_path_ok(
            first, second, GENERATOR_REACHABLE_MAX_STEP)
        quantized_layer_support_cache[pair] = bool(accepted)
        if not accepted:
            metrics['quantized_layer_support_reject_count'] += 1
        return bool(accepted)

    # Layer skeleton via Kruskal
    layer_parent = list(range(len(nodes)))

    def _layer_root(index):
        while layer_parent[index] != index:
            layer_parent[index] = layer_parent[layer_parent[index]]
            index = layer_parent[index]
        return index

    def _layer_union(first, second):
        first_root = _layer_root(first)
        second_root = _layer_root(second)
        if first_root != second_root:
            layer_parent[second_root] = first_root

    for first_index, neighbors in enumerate(adj):
        for second_index in neighbors:
            if first_index < second_index:
                _layer_union(first_index, second_index)
    for distance, first_index, second_index, dz in quantized_layer_candidates:
        if _layer_root(first_index) == _layer_root(second_index):
            continue
        edge_key = tuple(sorted((first_index, second_index)))
        if edge_key in quantized_layer_failed_pairs:
            continue
        if not _quantized_layer_support_ok(
                first_index, second_index, dz):
            quantized_layer_failed_pairs.add(edge_key)
            continue
        if add_link(first_index, second_index, force=True):
            _layer_union(first_index, second_index)
            links[edge_key] = (
                float(distance), 'quantized_layer_skeleton')
            metrics['quantized_layer_skeleton_link_count'] += 1
        else:
            quantized_layer_failed_pairs.add(edge_key)

    for distance, first_index, second_index, dz in quantized_layer_candidates:
        if second_index in adj[first_index]:
            continue
        edge_key = tuple(sorted((first_index, second_index)))
        if edge_key in quantized_layer_failed_pairs:
            continue
        if len(adj[first_index]) >= 4 or len(adj[second_index]) >= 4:
            continue
        if (_direction_redundant(first_index, second_index)
                or _direction_redundant(second_index, first_index)):
            continue
        if not _quantized_layer_support_ok(
                first_index, second_index, dz):
            quantized_layer_failed_pairs.add(edge_key)
            continue
        if add_link(first_index, second_index, force=True):
            links[edge_key] = (
                float(distance), 'quantized_layer_local')
            metrics['quantized_layer_local_link_count'] += 1
        else:
            quantized_layer_failed_pairs.add(edge_key)

    # Cardinal gap seams — bridge one missing 0.50 m base cell
    gap_parent = list(range(len(nodes)))

    def _gap_root(index):
        while gap_parent[index] != index:
            gap_parent[index] = gap_parent[gap_parent[index]]
            index = gap_parent[index]
        return index

    def _gap_union(first, second):
        first_root = _gap_root(first)
        second_root = _gap_root(second)
        if first_root != second_root:
            gap_parent[second_root] = first_root

    for first_index, neighbors in enumerate(adj):
        for second_index in neighbors:
            if second_index > first_index:
                _gap_union(first_index, second_index)

    gap_source_sets = [
        set(node.get('quantized_source_keys', ())) for node in nodes]
    gap_candidate_pairs = set()
    gap_candidates = []
    for first_index, first_node in enumerate(nodes):
        if (first_node.get('tag') != 'quantized_hierarchy'
                or not first_node.get('quantized_map_grid', False)
                or not gap_source_sets[first_index]):
            continue
        for _other_x, _other_y, second_index in link_grid.query(
                first_node['x'], first_node['y'], 1.7):
            if second_index <= first_index:
                continue
            second_node = nodes[second_index]
            if (second_node.get('tag') != 'quantized_hierarchy'
                    or not second_node.get('quantized_map_grid', False)
                    or not gap_source_sets[second_index]):
                continue
            pair = (first_index, second_index)
            if pair in gap_candidate_pairs:
                continue
            gap_candidate_pairs.add(pair)
            distance = math.hypot(
                float(first_node['x']) - float(second_node['x']),
                float(first_node['y']) - float(second_node['y']))
            dz = abs(
                float(first_node['z']) - float(second_node['z']))
            if (distance < 0.50 or distance > 1.60
                    or dz <= 1.0e-4
                    or dz > GENERATOR_REACHABLE_MAX_STEP + 1.0e-6):
                continue
            second_sources = gap_source_sets[second_index]
            missing_cell = None
            for source_qx, source_qy in gap_source_sets[first_index]:
                for delta_qx, delta_qy in (
                        (4, 0), (-4, 0), (0, 4), (0, -4)):
                    other_key = (
                        int(source_qx) + delta_qx,
                        int(source_qy) + delta_qy)
                    if other_key not in second_sources:
                        continue
                    middle_key = (
                        int(source_qx) + delta_qx // 2,
                        int(source_qy) + delta_qy // 2)
                    if middle_key in cell_owner:
                        continue
                    missing_cell = middle_key
                    break
                if missing_cell is not None:
                    break
            if missing_cell is None:
                continue
            gap_candidates.append((
                distance, dz, first_index, second_index, missing_cell))
    metrics['quantized_cardinal_gap_candidate_count'] = len(gap_candidates)
    for distance, dz, first_index, second_index, missing_cell in sorted(
            gap_candidates):
        if _gap_root(first_index) == _gap_root(second_index):
            continue
        if not _support_path_ok(
                first_index, second_index,
                GENERATOR_REACHABLE_MAX_STEP):
            metrics['quantized_cardinal_gap_support_reject_count'] += 1
            continue
        if link_blocked(first_index, second_index):
            metrics['quantized_cardinal_gap_collision_reject_count'] += 1
            continue
        if not add_link(first_index, second_index, force=True):
            continue
        _gap_union(first_index, second_index)
        edge_key = tuple(sorted((first_index, second_index)))
        links[edge_key] = (
            float(distance), 'quantized_cardinal_gap_seam')
        metrics['quantized_cardinal_gap_link_count'] += 1
        metrics['quantized_cardinal_gap_details'].append({
            'first': int(first_index),
            'second': int(second_index),
            'distance': round(float(distance), 4),
            'dz': round(float(dz), 4),
            'missing_source_cell': tuple(
                int(value) for value in missing_cell),
        })
