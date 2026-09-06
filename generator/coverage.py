"""Generator coverage gap repair — fills reachable holes left by the initial grower."""
import math
import random
from collections import Counter


def repair_coverage_gaps(cx, cy, R, nodes, quantize_to_map_grid, rng_seed,
                         walkable, coverage_fn, nearest_hard,
                         _surface_z_at, classify,
                         _generated_parent_edge_covering_point,
                         spacing_ok, add_node_raw, radius_m_local,
                         NODE_RADIUS_CLEARANCE_FACTOR):
    """Run coverage gap repair passes. Returns (repair_added, repair_log)."""

    def find_missing(step=0.85):
        missing = []
        ix0 = int(math.floor((cx - R) / step))
        ix1 = int(math.ceil((cx + R) / step))
        iy0 = int(math.floor((cy - R) / step))
        iy1 = int(math.ceil((cy + R) / step))
        for ix in range(ix0, ix1 + 1):
            x = ix * step
            for iy in range(iy0, iy1 + 1):
                y = iy * step
                if not walkable(x, y, 0.50):
                    continue
                cov, d, rr, ni = coverage_fn(nodes, x, y, 0.50)
                if cov:
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
                missing.append({'x': x, 'y': y, 'reason': reason})
        return missing

    def cluster_missing(missing, step=0.85):
        idx = {(round(m['x'] / step), round(m['y'] / step)): i
               for i, m in enumerate(missing)}
        seen = set()
        clusters = []
        for i, m in enumerate(missing):
            if i in seen:
                continue
            q = [i]
            seen.add(i)
            mem = []
            while q:
                u = q.pop()
                mem.append(u)
                ux, uy = missing[u]['x'], missing[u]['y']
                kx, ky = round(ux / step), round(uy / step)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        v = idx.get((kx + dx, ky + dy))
                        if v is not None and v not in seen:
                            seen.add(v)
                            q.append(v)
            rs = Counter(missing[j]['reason'] for j in mem)
            mx = sum(missing[j]['x'] for j in mem) / len(mem)
            my = sum(missing[j]['y'] for j in mem) / len(mem)
            maxrad = max(
                [math.hypot(missing[j]['x'] - mx, missing[j]['y'] - my)
                 for j in mem] or [0.85])
            clusters.append({
                'samples': len(mem),
                'centroid': (mx, my),
                'maxrad': maxrad,
                'reason': rs.most_common(1)[0][0],
                'reason_counts': dict(rs),
                'members': mem,
                '_member_set': set(mem),
            })
        clusters.sort(key=lambda c: c['samples'], reverse=True)
        return clusters

    def repair_candidate_ok(x, y, b15, surface_z=None):
        rm = radius_m_local(b15)
        if surface_z is None:
            surface_z = _surface_z_at(x, y)
        if surface_z is None:
            return False, 'no_surface_z'
        if _generated_parent_edge_covering_point(
                x, y, float(surface_z) + 1.2489) is not None:
            return False, 'generated_parent_edge_covered'
        if not walkable(
                x, y, max(0.62, rm * NODE_RADIUS_CLEARANCE_FACTOR),
                surface_z=surface_z):
            return False, 'not_walkable'
        ok_norm, info_norm = spacing_ok(
            x, y, b15, 'normal', surface_z=surface_z)
        if ok_norm:
            return True, 'normal_accept'
        ok_rep, info_rep = spacing_ok(
            x, y, b15, 'repair', surface_z=surface_z)
        if not ok_rep:
            return False, 'repair_spacing_reject'
        if info_rep.get('deep_overlap_count', 0) >= 2:
            return False, 'heavy_overlap_guard'
        return True, 'campaign_repair_accept'

    repair_missing_grid = {}
    repair_missing_grid_cell = 4.0

    def score_candidate(x, y, b15, missing, cluster):
        rm = radius_m_local(b15)
        covered_cluster = 0
        covered_total = 0
        query_radius = rm + 0.50
        gx0 = int(math.floor((x - query_radius) / repair_missing_grid_cell))
        gx1 = int(math.floor((x + query_radius) / repair_missing_grid_cell))
        gy0 = int(math.floor((y - query_radius) / repair_missing_grid_cell))
        gy1 = int(math.floor((y + query_radius) / repair_missing_grid_cell))
        member_set = cluster['_member_set']
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for mi in repair_missing_grid.get((gx, gy), ()):
                    m = missing[mi]
                    if math.hypot(m['x'] - x, m['y'] - y) > query_radius:
                        continue
                    covered_total += 1
                    if mi in member_set:
                        covered_cluster += 1
        edge = R - math.hypot(x - cx, y - cy)
        nh = nearest_hard(x, y)
        return (covered_cluster * 2.0 + covered_total * 0.8
                + (0.25 if nh < 2.5 else 0.0)
                - (0.6 if edge < 2.0 else 0.0)), covered_cluster, covered_total

    repair_log = []
    repair_added = 0
    for pass_i in range(0 if quantize_to_map_grid else 3):
        missing = find_missing()
        repair_missing_grid.clear()
        for missing_index, missing_sample in enumerate(missing):
            grid_key = (
                int(math.floor(
                    missing_sample['x'] / repair_missing_grid_cell)),
                int(math.floor(
                    missing_sample['y'] / repair_missing_grid_cell)),
            )
            repair_missing_grid.setdefault(grid_key, []).append(missing_index)
        clusters = cluster_missing(missing)
        if not clusters:
            break
        added_this_pass = 0
        for cidx, cluster in enumerate(clusters):
            if repair_added >= 34:
                break
            if (cluster['samples'] < 3
                    and cluster['reason'] == 'disc_edge_missing'):
                continue
            ccx, ccy = cluster['centroid']
            cr = max(0.75, min(3.8, cluster['maxrad'] + 0.75))
            rrng = random.Random(
                (rng_seed ^ (pass_i * 104729) ^ (cidx * 7919)
                 ^ (cluster['samples'] * 31337)) & 0xffffffff)
            candidates = [(ccx, ccy, 'centroid')]
            for k in range(40):
                a = rrng.random() * math.tau
                r = cr * math.sqrt(rrng.random())
                candidates.append(
                    (ccx + math.cos(a) * r, ccy + math.sin(a) * r, 'jitter'))
            best = None
            for x, y, src in candidates:
                candidate_surface_z = _surface_z_at(x, y)
                if candidate_surface_z is None:
                    continue
                if not walkable(x, y, 0.62, surface_z=candidate_surface_z):
                    continue
                b15, b12, fam = classify(x, y, surface_z=candidate_surface_z)
                ok, accept_type = repair_candidate_ok(
                    x, y, b15, surface_z=candidate_surface_z)
                if not ok:
                    continue
                score, cov_cluster, cov_total = score_candidate(
                    x, y, b15, missing, cluster)
                if cov_cluster < 2 and cov_total < 4:
                    continue
                if accept_type == 'campaign_repair_accept':
                    score += 0.35
                if best is None or score > best['score']:
                    best = {
                        'score': score, 'x': x, 'y': y,
                        'b15': b15, 'b12': b12, 'family': fam,
                        'surface_z': candidate_surface_z,
                        'accept_type': accept_type,
                        'covered_cluster': cov_cluster,
                        'covered_total': cov_total,
                        'cluster_samples': cluster['samples'],
                        'cluster_reason': cluster['reason'],
                    }
            if best:
                repair_index = add_node_raw(
                    best['x'], best['y'], best['b15'], best['b12'],
                    'campaign_hole_repair', best['family'],
                    surface_z=best['surface_z'])
                if repair_index is None:
                    continue
                repair_log.append(best)
                repair_added += 1
                added_this_pass += 1
        if added_this_pass == 0:
            break

    return repair_added, repair_log
