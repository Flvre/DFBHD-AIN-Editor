"""Generator graph audit — coverage, components, degree, overlap metrics."""
import math
from collections import Counter

from generator.support import _V52Spatial


def audit_graph(nodes, prior_nodes, corridor_dominated, adj, links,
                cx, cy, R, quantize_to_map_grid,
                walkable, nearest_hard, radius_m_local):
    """Audit the final generated graph for coverage, components, and overlap.

    Returns a dict of audit results consumed by the metrics assembly phase.
    """
    audit_records = []
    audit_grid = _V52Spatial(4.0)
    audit_max_radius = 0.0
    for i, n in enumerate(prior_nodes):
        rr = radius_m_local(n['b15'])
        record_index = len(audit_records)
        audit_records.append((n['x'], n['y'], rr, -1 - i))
        audit_grid.insert(n['x'], n['y'], record_index)
        audit_max_radius = max(audit_max_radius, rr)
    for i, n in enumerate(nodes):
        if i in corridor_dominated:
            continue
        rr = radius_m_local(n['b15'])
        record_index = len(audit_records)
        audit_records.append((n['x'], n['y'], rr, i))
        audit_grid.insert(n['x'], n['y'], record_index)
        audit_max_radius = max(audit_max_radius, rr)

    def _audit_coverage(x, y, margin=0.50):
        best_d = 999.0
        best_r = 0.0
        best_i = -1
        query_radius = audit_max_radius + margin
        for _px, _py, record_index in audit_grid.query(x, y, query_radius):
            nx, ny, rr, node_index = audit_records[record_index]
            d = math.hypot(nx - x, ny - y)
            if d < best_d:
                best_d = d
                best_r = rr
                best_i = node_index
        return best_d <= best_r + margin, best_d, best_r, best_i

    missing_final = []
    strict_missing_reason_counts = Counter()
    total_samples = 0
    covered_samples = 0
    strict_covered_samples = 0
    step = 0.85
    for ix in range(int(math.floor((cx - R) / step)),
                    int(math.ceil((cx + R) / step)) + 1):
        x = ix * step
        for iy in range(int(math.floor((cy - R) / step)),
                        int(math.ceil((cy + R) / step)) + 1):
            y = iy * step
            if not walkable(x, y, 0.50):
                continue
            total_samples += 1
            cov, _d, _rr, _ni = _audit_coverage(x, y, 0.50)
            if _d <= _rr + 0.20:
                strict_covered_samples += 1
            else:
                _strict_edge = R - math.hypot(x - cx, y - cy)
                _strict_nh = nearest_hard(x, y)
                if _strict_edge < 1.2:
                    _strict_reason = 'disc_edge_missing'
                elif _strict_nh < 0.75:
                    _strict_reason = 'clearance_reject'
                elif _strict_nh < 2.5:
                    _strict_reason = 'geometry_side_missing'
                else:
                    _strict_reason = 'empty_open_disc_missing'
                strict_missing_reason_counts[_strict_reason] += 1
            if cov:
                covered_samples += 1
                continue
            edge = R - math.hypot(x - cx, y - cy)
            nh = nearest_hard(x, y)
            if edge < 1.2:
                reason = 'disc_edge_missing'
            elif nh < 0.75:
                reason = 'clearance_reject'
            elif nh < 2.5:
                reason = 'geometry_side_missing'
            else:
                reason = 'empty_open_disc_missing'
            missing_final.append({'x': x, 'y': y, 'reason': reason})

    # Component analysis
    seen = set()
    components = []
    for i in range(len(nodes)):
        if i in seen:
            continue
        q = [i]
        seen.add(i)
        comp = []
        while q:
            u = q.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        components.append(comp)

    largest = max((len(c) for c in components), default=0)
    degs = [len(a) for a in adj]
    b15_counts = dict(sorted(Counter(n['b15'] for n in nodes).items()))
    b12_counts = dict(sorted(Counter(n['b12'] for n in nodes).items()))
    tag_counts = dict(sorted(Counter(n.get('tag', '') for n in nodes).items()))
    family_counts = dict(sorted(
        Counter(n.get('family', '') for n in nodes).items()))

    # Quantized link role accounting
    quantized_link_counts = None
    if quantize_to_map_grid:
        final_link_roles = Counter(
            role for _distance, role in links.values())
        axis = final_link_roles['quantized_axis']
        inherited = final_link_roles['quantized_cardinal_inherited']
        layer_skel = final_link_roles['quantized_layer_skeleton']
        layer_local = final_link_roles['quantized_layer_local']
        gap = final_link_roles['quantized_cardinal_gap_seam']
        fallback = max(0, len(links) - axis - inherited
                       - layer_skel - layer_local - gap)
        quantized_link_counts = {
            'axis': axis,
            'inherited': inherited,
            'layer_skeleton': layer_skel,
            'layer_local': layer_local,
            'cardinal_gap': gap,
            'fallback': fallback,
        }

    # Heavy overlap count
    heavy = 0
    for (i, j), (d, role) in links.items():
        if d < 0.56 * (radius_m_local(nodes[i]['b15'])
                       + radius_m_local(nodes[j]['b15'])):
            heavy += 1

    return {
        'missing_final': missing_final,
        'strict_missing_reason_counts': strict_missing_reason_counts,
        'total_samples': total_samples,
        'covered_samples': covered_samples,
        'strict_covered_samples': strict_covered_samples,
        'components': components,
        'largest': largest,
        'degs': degs,
        'b15_counts': b15_counts,
        'b12_counts': b12_counts,
        'tag_counts': tag_counts,
        'family_counts': family_counts,
        'quantized_link_counts': quantized_link_counts,
        'heavy': heavy,
    }
