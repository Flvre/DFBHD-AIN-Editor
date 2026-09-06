"""Pure CModel and render-mesh binary parsers for NovaLogic .3DI (GPM2) files.

These functions parse raw bytes into geometric primitives (segments,
triangles, grouped records).  They have no module-level mutable state
and no dependency on the editor's cache/resource layer.

CModel parsers accept ``dbg(channel, msg, *, once_key=...)`` matching ``_dbg``.
The render-mesh parser accepts ``dbg(msg)`` matching ``_wf3d_dbg``.
Optional ``dedup_edges`` flag replaces the monolith's ``CMODEL_DEDUP_EDGES``.
"""

import struct
import math


def _u32(data, off):
    return struct.unpack_from('<I', data, off)[0]


def _i16(data, off):
    return struct.unpack_from('<h', data, off)[0]


def _cmodel_face_is_collision(face_flags):
    """Return whether a CModel face participates in physical collision.

    Bit 0x0001 is the confirmed foliage/non-physical policy bit.  Bit 0x0100
    by itself marks a light-projector face, but it is also present on physical
    0x0400 faces (the common 0x0500 combination used by WareHse1).  The old
    blanket ``flags & 0x0101`` test consequently removed real floors, walls,
    railings, ladders, and stairs before the generator could classify them.

    Physical 0x0400 therefore takes precedence over the projector bit.  This
    is flag semantics, not a model-name or type-id exception, so custom models
    using the same collision policy follow the same path.
    """
    flags = int(face_flags) & 0xFFFFFFFF
    if flags & 0x0001:
        return False
    if (flags & 0x0100) and not (flags & 0x0400):
        return False
    return True


def _parse_gpm_cmodel(data, debug_name='', dedup_edges=True, dbg=None):
    """Parse MED CModel enough for FUN_0041B9E0-style top-view lines.

    Unlike the old renderer-mesh scanner, this only accepts true CModel
    candidates: a 0x88 CModel header followed by the blob layout that
    FUN_004419B0 fixes up into group-local vertex/face pointers.

    Some large buildings appear to have extra sections before CModel, so we
    try the direct FUN_00440DC0 offset first, then a bounded CModel-header scan.
    This scan runs only in the background loader, never during render.
    """
    if not data or len(data) < 0xEC + 0x88:
        return None
    if data[:2] != b'GP' or data[3] != 2:
        return None

    fail_reasons = []

    def fail(off, reason):
        if len(fail_reasons) < 4:
            fail_reasons.append((off, reason))
        return None

    def parse_at(cmodel_off):
        if cmodel_off < 0 or cmodel_off + 0x88 > len(data):
            return fail(cmodel_off, 'header outside file')
        try:
            blob_size = _u32(data, cmodel_off + 0x04)
            blob_off = cmodel_off + 0x88
            blob_end = blob_off + blob_size
            if blob_size <= 0 or blob_end > len(data):
                return fail(cmodel_off, f'bad blob size {blob_size:#x}')

            cnt_va      = _u32(data, cmodel_off + 0x30)
            cnt_vb      = _u32(data, cmodel_off + 0x38)
            cnt_faces   = _u32(data, cmodel_off + 0x40)
            cnt_groups  = _u32(data, cmodel_off + 0x48)
            cnt_0c      = _u32(data, cmodel_off + 0x50)
            cnt_10      = _u32(data, cmodel_off + 0x58)
            cnt_regions = _u32(data, cmodel_off + 0x60)

            if not (0 < cnt_va < 250000 and 0 <= cnt_vb < 250000 and 0 < cnt_faces < 600000 and 0 < cnt_groups < 50000):
                return fail(cmodel_off, f'implausible counts va={cnt_va} vb={cnt_vb} faces={cnt_faces} groups={cnt_groups}')

            rel_va = 0
            rel_vb = rel_va + cnt_va * 0x08
            rel_faces = rel_vb + cnt_vb * 0x08
            rel_groups = rel_faces + cnt_faces * 0x2C
            rel_0c = rel_groups + cnt_groups * 0x80
            rel_10 = rel_0c + cnt_0c * 0x0C
            rel_regions = rel_10 + cnt_10* 0x10
            rel_end_min = rel_regions + cnt_regions * 0x60
            if rel_end_min > blob_size:
                return fail(cmodel_off, f'sections exceed blob end={rel_end_min:#x} blob={blob_size:#x}')

            run_va = run_vb = run_faces = run_regions = 0
            group_counts = []
            for gi in range(cnt_groups):
                gbase = blob_off + rel_groups + gi * 0x80
                if gbase + 0x20 > blob_end:
                    return fail(cmodel_off, 'group outside blob')
                g_vcount = _u32(data, gbase + 0x04)
                g_fcount = _u32(data, gbase + 0x0C)
                g_vbcount = _u32(data, gbase + 0x14)
                g_rcount = _u32(data, gbase + 0x1C)
                if run_va + g_vcount > cnt_va or run_faces + g_fcount > cnt_faces or run_vb + g_vbcount > cnt_vb or run_regions + g_rcount > cnt_regions:
                    return fail(cmodel_off, 'group sums exceed totals')
                group_counts.append((run_va, run_faces, g_vcount, g_fcount))
                run_va += g_vcount
                run_faces += g_fcount
                run_vb += g_vbcount
                run_regions += g_rcount

            if run_va != cnt_va or run_faces != cnt_faces:
                return fail(cmodel_off, f'group sums mismatch va {run_va}/{cnt_va} faces {run_faces}/{cnt_faces}')

            segments = []
            seen = set()
            skipped_faces = 0

            def read_vertex(base_va, local_index):
                voff = blob_off + rel_va + (base_va + local_index) * 0x08
                if voff + 6 > blob_end:
                    return None
                sx = _i16(data, voff + 0)
                sy = _i16(data, voff + 2)
                return (-sy / 256.0, sx / 256.0)

            for gi, (base_va, base_face, g_vcount, g_fcount) in enumerate(group_counts):
                if g_vcount <= 0 or g_fcount <= 0:
                    continue
                for fi in range(g_fcount):
                    foff = blob_off + rel_faces + (base_face + fi) * 0x2C
                    if foff + 6 > blob_end:
                        break
                    i0 = _i16(data, foff + 0)
                    i1 = _i16(data, foff + 2)
                    i2 = _i16(data, foff + 4)
                    if i0 == i1 or i1 == i2 or i2 == i0:
                        continue
                    if not (0 <= i0 < g_vcount and 0 <= i1 < g_vcount and 0 <= i2 < g_vcount):
                        skipped_faces += 1
                        continue
                    pts = [read_vertex(base_va, i0), read_vertex(base_va, i1), read_vertex(base_va, i2)]
                    if not all(pts):
                        continue
                    edge_specs = ((i0, i1, pts[0], pts[1]), (i1, i2, pts[1], pts[2]), (i2, i0, pts[2], pts[0]))
                    for ia0, ia1, a, b in edge_specs:
                        if dedup_edges:
                            ia, ib = sorted((ia0, ia1))
                            k = (gi, ia, ib)
                            if k in seen:
                                continue
                            seen.add(k)
                        segments.append((a, b))

            if segments:
                if dbg:
                    dbg('CMODEL_PARSE', f'[3DI] {debug_name}: CModel off={cmodel_off:#x} blob={blob_off:#x} groups={cnt_groups} va={cnt_va} faces={cnt_faces} segs={len(segments)} skipped={skipped_faces}', once_key=('parsed', debug_name))
                return segments
            return fail(cmodel_off, 'valid header but zero drawable segments')
        except Exception as e:
            return fail(cmodel_off, f'exception {e}')

    try:
        b4_count = _u32(data, 0xB4)
        after_b4 = 0xEC + b4_count * 0x30
        direct = None
        if after_b4 + 4 <= len(data):
            f4_count = _u32(data, after_b4)
            direct = after_b4 + 4 + f4_count * 0x3C
            segs = parse_at(direct)
            if segs:
                return segs

        max_scan = min(len(data) - 0x88, 0x40000)
        best = None
        best_n = 0
        for off in range(0xEC, max_scan, 4):
            if direct is not None and abs(off - direct) < 4:
                continue
            segs = parse_at(off)
            if segs and len(segs) > best_n:
                best = segs
                best_n = len(segs)
                if best_n > 500:
                    break
        if best:
            if dbg:
                dbg('CMODEL_PARSE', f'[3DI] {debug_name}: used fallback CModel scan', once_key=('fallback', debug_name))
            return best

        if fail_reasons:
            detail = '; '.join(f'{o:#x}:{r}' for o, r in fail_reasons[:4])
            if dbg:
                dbg('CMODEL_FAIL', f'[3DI] {debug_name}: CModel parse failed. direct={direct if direct is not None else None} reasons={detail}', once_key=('fail', debug_name))
        return None
    except Exception as e:
        if dbg:
            dbg('CMODEL_FAIL', f'[3DI] {debug_name}: CModel parse fatal error {e}', once_key=('fatal', debug_name))
        return None


def _parse_gpm_cmodel_3d(data, debug_name='', return_triangles=False,
                         collision_only=False, collision_policy_key=None,
                         dedup_edges=True, dbg=None, collision_policy_out=None):
    """Parse compact/CModel triangles as real 3D local-space line segments.

    This mirrors _parse_gpm_cmodel's validated CModel-header scan, but keeps
    the compact vertex Z component instead of collapsing everything to the 2D
    top-view footprint. Output coordinates use the same local ground axes as
    the existing 2D outline path: local X = -rawY/256, local Y/depth = rawX/256,
    local Z/height = rawZ/256.
    """
    if not data or len(data) < 0xEC + 0x88:
        return None
    if data[:2] != b'GP' or data[3] != 2:
        return None

    fail_reasons = []

    def fail(off, reason):
        if len(fail_reasons) < 4:
            fail_reasons.append((off, reason))
        return None

    def parse_at(cmodel_off):
        if cmodel_off < 0 or cmodel_off + 0x88 > len(data):
            return fail(cmodel_off, 'header outside file')
        try:
            blob_size = _u32(data, cmodel_off + 0x04)
            blob_off = cmodel_off + 0x88
            blob_end = blob_off + blob_size
            if blob_size <= 0 or blob_end > len(data):
                return fail(cmodel_off, f'bad blob size {blob_size:#x}')

            cnt_va      = _u32(data, cmodel_off + 0x30)
            cnt_vb      = _u32(data, cmodel_off + 0x38)
            cnt_faces   = _u32(data, cmodel_off + 0x40)
            cnt_groups  = _u32(data, cmodel_off + 0x48)
            cnt_0c      = _u32(data, cmodel_off + 0x50)
            cnt_10      = _u32(data, cmodel_off + 0x58)
            cnt_regions = _u32(data, cmodel_off + 0x60)

            if not (0 < cnt_va < 250000 and 0 <= cnt_vb < 250000 and 0 < cnt_faces < 600000 and 0 < cnt_groups < 50000):
                return fail(cmodel_off, f'implausible counts va={cnt_va} vb={cnt_vb} faces={cnt_faces} groups={cnt_groups}')

            rel_va = 0
            rel_vb = rel_va + cnt_va * 0x08
            rel_faces = rel_vb + cnt_vb * 0x08
            rel_groups = rel_faces + cnt_faces * 0x2C
            rel_0c = rel_groups + cnt_groups * 0x80
            rel_10 = rel_0c + cnt_0c * 0x0C
            rel_regions = rel_10 + cnt_10 * 0x10
            rel_end_min = rel_regions + cnt_regions * 0x60
            if rel_end_min > blob_size:
                return fail(cmodel_off, f'sections exceed blob end={rel_end_min:#x} blob={blob_size:#x}')

            run_va = run_vb = run_faces = run_regions = 0
            group_counts = []
            for gi in range(cnt_groups):
                gbase = blob_off + rel_groups + gi * 0x80
                if gbase + 0x20 > blob_end:
                    return fail(cmodel_off, 'group outside blob')
                g_vcount = _u32(data, gbase + 0x04)
                g_fcount = _u32(data, gbase + 0x0C)
                g_vbcount = _u32(data, gbase + 0x14)
                g_rcount = _u32(data, gbase + 0x1C)
                if run_va + g_vcount > cnt_va or run_faces + g_fcount > cnt_faces or run_vb + g_vbcount > cnt_vb or run_regions + g_rcount > cnt_regions:
                    return fail(cmodel_off, 'group sums exceed totals')
                group_counts.append((run_va, run_faces, g_vcount, g_fcount))
                run_va += g_vcount
                run_faces += g_fcount
                run_vb += g_vbcount
                run_regions += g_rcount

            if run_va != cnt_va or run_faces != cnt_faces:
                return fail(cmodel_off, f'group sums mismatch va {run_va}/{cnt_va} faces {run_faces}/{cnt_faces}')

            segments = []
            triangles = []
            seen = set()
            skipped_faces = 0
            skipped_non_collision_faces = 0
            recovered_collision_faces = 0

            def read_vertex(base_va, local_index):
                voff = blob_off + rel_va + (base_va + local_index) * 0x08
                if voff + 6 > blob_end:
                    return None
                sx = _i16(data, voff + 0)
                sy = _i16(data, voff + 2)
                sz = _i16(data, voff + 4)
                return (-sy / 256.0, sx / 256.0, sz / 256.0)

            for gi, (base_va, base_face, g_vcount, g_fcount) in enumerate(group_counts):
                if g_vcount <= 0 or g_fcount <= 0:
                    continue
                for fi in range(g_fcount):
                    foff = blob_off + rel_faces + (base_face + fi) * 0x2C
                    if foff + 6 > blob_end:
                        break
                    if collision_only and foff + 0x28 <= blob_end:
                        face_flags = _u32(data, foff + 0x24)
                        if not _cmodel_face_is_collision(face_flags):
                            skipped_non_collision_faces += 1
                            continue
                        if ((face_flags & 0x0001) == 0
                                and (face_flags & 0x0500) == 0x0500):
                            recovered_collision_faces += 1
                    i0 = _i16(data, foff + 0)
                    i1 = _i16(data, foff + 2)
                    i2 = _i16(data, foff + 4)
                    if i0 == i1 or i1 == i2 or i2 == i0:
                        continue
                    if not (0 <= i0 < g_vcount and 0 <= i1 < g_vcount and 0 <= i2 < g_vcount):
                        skipped_faces += 1
                        continue
                    pts = [read_vertex(base_va, i0), read_vertex(base_va, i1), read_vertex(base_va, i2)]
                    if not all(pts):
                        continue
                    triangles.append(tuple(pts))
                    edge_specs = ((i0, i1, pts[0], pts[1]), (i1, i2, pts[1], pts[2]), (i2, i0, pts[2], pts[0]))
                    for ia0, ia1, a, b in edge_specs:
                        if dedup_edges:
                            ia, ib = sorted((ia0, ia1))
                            k = (gi, ia, ib)
                            if k in seen:
                                continue
                            seen.add(k)
                        segments.append((a, b))

            result = triangles if return_triangles else segments
            if result:
                if collision_only and collision_policy_key is not None and collision_policy_out is not None:
                    collision_policy_out[collision_policy_key] = bool(
                        recovered_collision_faces)
                if dbg:
                    dbg('CMODEL_PARSE', f'[3DI] {debug_name}: CModel3D off={cmodel_off:#x} blob={blob_off:#x} groups={cnt_groups} va={cnt_va} faces={cnt_faces} segs={len(segments)} triangles={len(triangles)} skipped={skipped_faces} noncollision={skipped_non_collision_faces}', once_key=('parsed3d', debug_name, bool(return_triangles), bool(collision_only)))
                return result
            return fail(cmodel_off, 'valid header but zero drawable segments')
        except Exception as e:
            return fail(cmodel_off, f'exception {e}')

    try:
        b4_count = _u32(data, 0xB4)
        after_b4 = 0xEC + b4_count * 0x30
        direct = None
        if after_b4 + 4 <= len(data):
            f4_count = _u32(data, after_b4)
            direct = after_b4 + 4 + f4_count * 0x3C
            segs = parse_at(direct)
            if segs:
                return segs

        max_scan = min(len(data) - 0x88, 0x40000)
        best = None
        best_n = 0
        for off in range(0xEC, max_scan, 4):
            if direct is not None and abs(off - direct) < 4:
                continue
            segs = parse_at(off)
            if segs and len(segs) > best_n:
                best = segs
                best_n = len(segs)
                if best_n > 500:
                    break
        if best:
            if dbg:
                dbg('CMODEL_PARSE', f'[3DI] {debug_name}: used fallback CModel3D scan', once_key=('fallback3d', debug_name))
            return best

        if fail_reasons:
            detail = '; '.join(f'{o:#x}:{r}' for o, r in fail_reasons[:4])
            if dbg:
                dbg('CMODEL_FAIL', f'[3DI] {debug_name}: CModel3D parse failed. direct={direct if direct is not None else None} reasons={detail}', once_key=('fail3d', debug_name))
        return None
    except Exception as e:
        if dbg:
            dbg('CMODEL_FAIL', f'[3DI] {debug_name}: CModel3D parse fatal error {e}', once_key=('fatal3d', debug_name))
        return None


def _parse_gpm_cmodel_3d_grouped(data, debug_name='', dbg=None):
    """Parse compact/CModel triangles as grouped 3D local-space line segments.

    Returns [(group_index, a, b, importance), ...] where a/b are local
    (x, y/depth, z) points and importance is boundary/crease/flat/noise.
    This keeps the CModel "Addr block"/group provenance instead of
    flattening all lines into one anonymous mesh.  The normal renderer can still
    flatten these records, but the 3D view can now color/filter by group.
    """
    if not data or len(data) < 0xEC + 0x88:
        return None
    if data[:2] != b'GP' or data[3] != 2:
        return None

    fail_reasons = []

    def fail(off, reason):
        if len(fail_reasons) < 4:
            fail_reasons.append((off, reason))
        return None

    def parse_at(cmodel_off):
        if cmodel_off < 0 or cmodel_off + 0x88 > len(data):
            return fail(cmodel_off, 'header outside file')
        try:
            blob_size = _u32(data, cmodel_off + 0x04)
            blob_off = cmodel_off + 0x88
            blob_end = blob_off + blob_size
            if blob_size <= 0 or blob_end > len(data):
                return fail(cmodel_off, f'bad blob size {blob_size:#x}')

            cnt_va      = _u32(data, cmodel_off + 0x30)
            cnt_vb      = _u32(data, cmodel_off + 0x38)
            cnt_faces   = _u32(data, cmodel_off + 0x40)
            cnt_groups  = _u32(data, cmodel_off + 0x48)
            cnt_0c      = _u32(data, cmodel_off + 0x50)
            cnt_10      = _u32(data, cmodel_off + 0x58)
            cnt_regions = _u32(data, cmodel_off + 0x60)

            if not (0 < cnt_va < 250000 and 0 <= cnt_vb < 250000 and 0 < cnt_faces < 600000 and 0 < cnt_groups < 50000):
                return fail(cmodel_off, f'implausible counts va={cnt_va} vb={cnt_vb} faces={cnt_faces} groups={cnt_groups}')

            rel_va = 0
            rel_vb = rel_va + cnt_va * 0x08
            rel_faces = rel_vb + cnt_vb * 0x08
            rel_groups = rel_faces + cnt_faces * 0x2C
            rel_0c = rel_groups + cnt_groups * 0x80
            rel_10 = rel_0c + cnt_0c * 0x0C
            rel_regions = rel_10 + cnt_10 * 0x10
            rel_end_min = rel_regions + cnt_regions * 0x60
            if rel_end_min > blob_size:
                return fail(cmodel_off, f'sections exceed blob end={rel_end_min:#x} blob={blob_size:#x}')

            run_va = run_vb = run_faces = run_regions = 0
            group_counts = []
            for gi in range(cnt_groups):
                gbase = blob_off + rel_groups + gi * 0x80
                if gbase + 0x20 > blob_end:
                    return fail(cmodel_off, 'group outside blob')
                g_vcount = _u32(data, gbase + 0x04)
                g_fcount = _u32(data, gbase + 0x0C)
                g_vbcount = _u32(data, gbase + 0x14)
                g_rcount = _u32(data, gbase + 0x1C)
                if run_va + g_vcount > cnt_va or run_faces + g_fcount > cnt_faces or run_vb + g_vbcount > cnt_vb or run_regions + g_rcount > cnt_regions:
                    return fail(cmodel_off, 'group sums exceed totals')
                group_counts.append((run_va, run_faces, g_vcount, g_fcount))
                run_va += g_vcount
                run_faces += g_fcount
                run_vb += g_vbcount
                run_regions += g_rcount

            if run_va != cnt_va or run_faces != cnt_faces:
                return fail(cmodel_off, f'group sums mismatch va {run_va}/{cnt_va} faces {run_faces}/{cnt_faces}')

            records = []
            skipped_faces = 0

            def read_vertex(base_va, local_index):
                voff = blob_off + rel_va + (base_va + local_index) * 0x08
                if voff + 6 > blob_end:
                    return None
                sx = _i16(data, voff + 0)
                sy = _i16(data, voff + 2)
                sz = _i16(data, voff + 4)
                return (-sy / 256.0, sx / 256.0, sz / 256.0)

            def vkey(p):
                return (round(float(p[0]) * 1024), round(float(p[1]) * 1024), round(float(p[2]) * 1024))

            def edge_key(a, b):
                ka, kb = vkey(a), vkey(b)
                return (ka, kb) if ka <= kb else (kb, ka)

            def tri_normal(p0, p1, p2):
                ax, ay, az = p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]
                bx, by, bz = p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2]
                nx = ay*bz - az*by
                ny = az*bx - ax*bz
                nz = ax*by - ay*bx
                l = (nx*nx + ny*ny + nz*nz) ** 0.5
                if l <= 1e-9:
                    return None
                return (nx/l, ny/l, nz/l)

            def edge_len(a, b):
                dx, dy, dz = a[0]-b[0], a[1]-b[1], a[2]-b[2]
                return (dx*dx + dy*dy + dz*dz) ** 0.5

            for gi, (base_va, base_face, g_vcount, g_fcount) in enumerate(group_counts):
                if g_vcount <= 0 or g_fcount <= 0:
                    continue
                edges = {}
                for fi in range(g_fcount):
                    foff = blob_off + rel_faces + (base_face + fi) * 0x2C
                    if foff + 6 > blob_end:
                        break
                    i0 = _i16(data, foff + 0)
                    i1 = _i16(data, foff + 2)
                    i2 = _i16(data, foff + 4)
                    if i0 == i1 or i1 == i2 or i2 == i0:
                        continue
                    if not (0 <= i0 < g_vcount and 0 <= i1 < g_vcount and 0 <= i2 < g_vcount):
                        skipped_faces += 1
                        continue
                    pts = [read_vertex(base_va, i0), read_vertex(base_va, i1), read_vertex(base_va, i2)]
                    if not all(pts):
                        continue
                    n = tri_normal(pts[0], pts[1], pts[2])
                    if n is None:
                        continue
                    for a, b in ((pts[0], pts[1]), (pts[1], pts[2]), (pts[2], pts[0])):
                        k = edge_key(a, b)
                        rec = edges.get(k)
                        if rec is None:
                            rec = {"a": a, "b": b, "normals": [], "count": 0}
                            edges[k] = rec
                        rec["normals"].append(n)
                        rec["count"] += 1

                for rec in edges.values():
                    a = rec["a"]; b = rec["b"]
                    count = int(rec.get("count", 0))
                    normals = rec.get("normals") or []
                    importance = "flat"
                    if edge_len(a, b) < 0.035:
                        importance = "noise"
                    if count <= 1:
                        importance = "boundary"
                    elif len(normals) >= 2:
                        crease = False
                        for ii in range(len(normals)):
                            for jj in range(ii + 1, len(normals)):
                                d = normals[ii][0]*normals[jj][0] + normals[ii][1]*normals[jj][1] + normals[ii][2]*normals[jj][2]
                                if d < 0.966:
                                    crease = True
                                    break
                            if crease:
                                break
                        if crease:
                            importance = "crease"
                    records.append((gi, a, b, importance))

            if records:
                group_n = len(set(r[0] for r in records))
                if dbg:
                    dbg('CMODEL_PARSE', f'[3DI] {debug_name}: CModel3D grouped off={cmodel_off:#x} groups={cnt_groups} visible_groups={group_n} records={len(records)} skipped={skipped_faces}', once_key=('parsed3d_grouped', debug_name))
                return records
            return fail(cmodel_off, 'valid header but zero drawable grouped segments')
        except Exception as e:
            return fail(cmodel_off, f'exception {e}')

    try:
        b4_count = _u32(data, 0xB4)
        after_b4 = 0xEC + b4_count * 0x30
        direct = None
        if after_b4 + 4 <= len(data):
            f4_count = _u32(data, after_b4)
            direct = after_b4 + 4 + f4_count * 0x3C
            recs = parse_at(direct)
            if recs:
                return recs

        max_scan = min(len(data) - 0x88, 0x40000)
        best = None
        best_n = 0
        for off in range(0xEC, max_scan, 4):
            if direct is not None and abs(off - direct) < 4:
                continue
            recs = parse_at(off)
            if recs and len(recs) > best_n:
                best = recs
                best_n = len(recs)
                if best_n > 500:
                    break
        if best:
            if dbg:
                dbg('CMODEL_PARSE', f'[3DI] {debug_name}: used fallback grouped CModel3D scan', once_key=('fallback3d_grouped', debug_name))
            return best

        if fail_reasons:
            detail = '; '.join(f'{o:#x}:{r}' for o, r in fail_reasons[:4])
            if dbg:
                dbg('CMODEL_FAIL', f'[3DI] {debug_name}: grouped CModel3D parse failed. direct={direct if direct is not None else None} reasons={detail}', once_key=('fail3d_grouped', debug_name))
        return None
    except Exception as e:
        if dbg:
            dbg('CMODEL_FAIL', f'[3DI] {debug_name}: grouped CModel3D parse fatal error {e}', once_key=('fatal3d_grouped', debug_name))
        return None


def _parse_render_mesh_3d(data, prefer_lod='best', dbg=None):
    """Parse MED-style 3DI render LOD wire geometry.

    Large-building parser hardening:
    Some large buildings do not place CModel exactly at the simple direct
    FUN_00440DC0 offset used by the first render parser.  The 2D CModel path
    already solved this by scanning for a *valid* CModel header.  Do the same
    here, then continue the confirmed MED stream from that CModel:

        CModel header + blob -> render vertices -> extra section -> LODs

    Returns local render-space line segments in raw render vertex convention:
        vertex +0/+4/+8 = X/Y/Z
    The 3D window maps that as X/Y horizontal and Z vertical.
    """

    if dbg: dbg(f"PARSE_RENDER: enter bytes={len(data) if data else 0}")
    if not data or len(data) < 0xEC + 0x88:
        if dbg: dbg("PARSE_RENDER: fail short/not data")
        return None
    if data[:3] not in (b'GPM', b'GPS', b'GPP') or data[3] != 2:
        if dbg: dbg(f"PARSE_RENDER: fail bad magic={data[:4]!r}")
        return None

    def u32(off):
        if off < 0 or off + 4 > len(data):
            raise ValueError('u32 outside data')
        return struct.unpack_from('<I', data, off)[0]

    def u16(off):
        if off < 0 or off + 2 > len(data):
            raise ValueError('u16 outside data')
        return struct.unpack_from('<H', data, off)[0]

    def f32(off):
        if off < 0 or off + 4 > len(data):
            raise ValueError('f32 outside data')
        return struct.unpack_from('<f', data, off)[0]

    family = data[:3]
    lod_count = u32(0x1C)
    vert_count = u32(0x88)
    if dbg: dbg(f"PARSE_RENDER: header family={family.decode(errors='ignore')} lod_count={lod_count} vert_count={vert_count}")
    if not (0 < vert_count < 300000) or not (0 <= lod_count <= 16):
        if dbg: dbg("PARSE_RENDER: fail bad vert_count/lod_count")
        return None

    if family == b'GPP':
        stride_candidates = [0x3C]
        index_off = 0x30
    elif family == b'GPS':
        stride_candidates = [0x30, 0x2C]
        index_off = 0x28
    else:
        stride_candidates = [0x2C, 0x30]
        index_off = 0x28

    def valid_cmodel_at(cmodel_off):
        """Return (score, blob_size, blob_end) for a plausible CModel header."""
        if cmodel_off < 0 or cmodel_off + 0x88 > len(data):
            return None
        try:
            blob_size = u32(cmodel_off + 0x04)
            blob_start = cmodel_off + 0x88
            blob_end = blob_start + blob_size
            if not (0 < blob_size <= len(data) - blob_start):
                return None
            cnt_va      = u32(cmodel_off + 0x30)
            cnt_vb      = u32(cmodel_off + 0x38)
            cnt_faces   = u32(cmodel_off + 0x40)
            cnt_groups  = u32(cmodel_off + 0x48)
            cnt_0c      = u32(cmodel_off + 0x50)
            cnt_10      = u32(cmodel_off + 0x58)
            cnt_regions = u32(cmodel_off + 0x60)
            if not (0 < cnt_va < 250000 and 0 <= cnt_vb < 250000 and 0 < cnt_faces < 600000 and 0 < cnt_groups < 50000):
                return None
            rel_va = 0
            rel_vb = rel_va + cnt_va * 0x08
            rel_faces = rel_vb + cnt_vb * 0x08
            rel_groups = rel_faces + cnt_faces * 0x2C
            rel_0c = rel_groups + cnt_groups * 0x80
            rel_10 = rel_0c + cnt_0c * 0x0C
            rel_regions = rel_10 + cnt_10 * 0x10
            rel_end_min = rel_regions + cnt_regions * 0x60
            if rel_end_min > blob_size:
                return None

            run_va = run_vb = run_faces = run_regions = 0
            for gi in range(min(cnt_groups, 50000)):
                gbase = blob_start + rel_groups + gi * 0x80
                if gbase + 0x20 > blob_end:
                    return None
                g_vcount = u32(gbase + 0x04)
                g_fcount = u32(gbase + 0x0C)
                g_vbcount = u32(gbase + 0x14)
                g_rcount = u32(gbase + 0x1C)
                if run_va + g_vcount > cnt_va or run_faces + g_fcount > cnt_faces or run_vb + g_vbcount > cnt_vb or run_regions + g_rcount > cnt_regions:
                    return None
                run_va += g_vcount
                run_faces += g_fcount
                run_vb += g_vbcount
                run_regions += g_rcount
            if run_va != cnt_va or run_faces != cnt_faces:
                return None
            score = cnt_faces + cnt_va + cnt_groups * 10
            return (score, blob_size, blob_end)
        except Exception:
            return None

    candidates = []
    direct = None
    try:
        b4_count = u32(0xB4)
        after_b4 = 0xEC + b4_count * 0x30
        if after_b4 + 4 <= len(data):
            f4_count = u32(after_b4)
            direct = after_b4 + 4 + f4_count * 0x3C
            v = valid_cmodel_at(direct)
            if v:
                candidates.append((direct, v, 'direct'))
    except Exception as _e:
        if dbg: dbg(f"PARSE_RENDER: direct cmodel calc exception={_e}")

    # Fallback scan copied conceptually from _parse_gpm_cmodel: only accept true
    # CModel headers with coherent aggregate sections and group sums. This is the
    # key fix for big building files whose pre-CModel layout differs.
    max_scan = min(len(data) - 0x88, 0x50000)
    best_scan = None
    for off in range(0xEC, max_scan, 4):
        if direct is not None and abs(off - direct) < 4:
            continue
        v = valid_cmodel_at(off)
        if not v:
            continue
        if best_scan is None or v[0] > best_scan[1][0]:
            best_scan = (off, v, 'scan')
            if v[0] > 5000:
                break
    if best_scan:
        candidates.append(best_scan)

    if not candidates:
        if dbg: dbg(f"PARSE_RENDER: fail no valid CModel header direct={direct if direct is not None else None}")
        return None

    # Try best-scoring CModel candidates first.
    candidates.sort(key=lambda t: (0 if t[2] == 'direct' else 1, -t[1][0]))

    def vertex_block_ok(vstart, vstride):
        if vstart < 0 or vstart + vert_count * vstride > len(data):
            return False
        idxs = sorted(set([0, 1, 2, max(0, vert_count // 2), vert_count - 1]))
        good = 0
        for vi in idxs:
            off = vstart + vi * vstride
            try:
                x, y, z = f32(off), f32(off + 4), f32(off + 8)
            except Exception:
                return False
            if math.isfinite(x) and math.isfinite(y) and math.isfinite(z) and abs(x) < 50000 and abs(y) < 50000 and abs(z) < 50000:
                good += 1
        return good >= max(1, len(idxs) - 1)

    def blob_off(blob_start, blob_size, raw, fallback=None):
        if raw and 0 <= raw < blob_size:
            return blob_start + raw
        if raw and blob_start <= raw < blob_start + blob_size:
            return raw
        return fallback

    def parse_from_cmodel(cmodel_off, cinfo, tag):
        score, cmodel_blob_size, cmodel_end = cinfo
        vert_start = cmodel_end
        chosen_stride = None
        for vs in stride_candidates:
            if vertex_block_ok(vert_start, vs):
                chosen_stride = vs
                break
        if chosen_stride is None:
            if dbg: dbg(f"PARSE_RENDER: {tag} cmodel=0x{cmodel_off:x} fail no valid vertex stride at 0x{vert_start:x}")
            return []
        if dbg: dbg(f"PARSE_RENDER: {tag} cmodel=0x{cmodel_off:x} blob={cmodel_blob_size} vertex_start=0x{vert_start:x} stride=0x{chosen_stride:x}")

        def read_vert(idx):
            if not (0 <= idx < vert_count):
                return None
            off = vert_start + idx * chosen_stride
            try:
                x, y, z = f32(off), f32(off + 4), f32(off + 8)
            except Exception:
                return None
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                return None
            if abs(x) > 100000 or abs(y) > 100000 or abs(z) > 100000:
                return None
            return (x, y, z)

        lod_stream = vert_start + vert_count * chosen_stride
        # FUN_00440DC0 reads an extra section after render verts before LODs.
        if lod_stream + 4 <= len(data):
            try:
                extra_count = u32(lod_stream)
                extra_end = lod_stream + 4 + extra_count * 0x3C
                if 0 <= extra_count < 200000 and extra_end <= len(data):
                    if dbg: dbg(f"PARSE_RENDER: {tag} extra_count={extra_count} extra_end=0x{extra_end:x}")
                    lod_stream = extra_end
            except Exception:
                pass

        def parse_face_list(start, count, blob_start, blob_end, edge_pts):
            if not start or count <= 0 or start < blob_start or start >= blob_end:
                return start
            cur = start
            for _ in range(min(count, 250000)):
                if cur + index_off > blob_end:
                    break
                try:
                    vcount = u16(cur + 0x08)
                    ftype = u32(cur + 0x0C)
                    base_index = u32(cur + 0x10)
                except Exception:
                    break
                if not (2 <= vcount <= 65535):
                    break
                idx_start = cur + index_off
                face_end = idx_start + vcount * 2
                if face_end > blob_end:
                    break
                if ftype not in (0, 1):
                    cur = face_end
                    continue
                try:
                    idxs = [base_index + u16(idx_start + i * 2) for i in range(vcount)]
                except Exception:
                    break

                def add_edge(ia, ib):
                    if ia == ib or ia >= vert_count or ib >= vert_count:
                        return
                    k = (ia, ib) if ia < ib else (ib, ia)
                    if k in edge_pts:
                        return
                    a = read_vert(ia)
                    b = read_vert(ib)
                    if a is not None and b is not None:
                        edge_pts[k] = (a, b)

                if ftype == 0:
                    tri_n = vcount - (vcount % 3)
                    for i in range(0, tri_n, 3):
                        i0, i1, i2 = idxs[i], idxs[i + 1], idxs[i + 2]
                        add_edge(i0, i1); add_edge(i1, i2); add_edge(i2, i0)
                else:
                    if vcount >= 2:
                        add_edge(idxs[0], idxs[1])
                    for i in range(2, vcount):
                        add_edge(idxs[i - 1], idxs[i])
                        add_edge(idxs[i - 2], idxs[i])
                cur = face_end
            return cur

        lod_segments = []
        pos = lod_stream
        for li in range(lod_count):
            if pos + 0x88 > len(data):
                break
            try:
                lod_blob_size = u32(pos + 0x00)
                group_count = u32(pos + 0x10)
                group_off_units = u32(pos + 0x18)
            except Exception:
                break
            blob_start = pos + 0x88
            blob_end = blob_start + lod_blob_size
            next_lod = blob_end
            if not (0 < lod_blob_size <= len(data) - blob_start and 0 <= group_count < 10000):
                if dbg: dbg(f"PARSE_RENDER: {tag} LOD{li} suspicious blob=0x{lod_blob_size:x} groups={group_count} pos=0x{pos:x}")
                break
            group_start = blob_start + group_off_units * 0x0C
            if group_start < blob_start or group_start + group_count * 0x48 > blob_end:
                if dbg: dbg(f"PARSE_RENDER: {tag} LOD{li} bad groups start=0x{group_start:x} blob=[0x{blob_start:x},0x{blob_end:x})")
                pos = next_lod
                continue

            desc_cursor = group_start + group_count * 0x48
            edge_pts = {}
            for gi in range(group_count):
                g = group_start + gi * 0x48
                try:
                    raw_desc = u32(g + 0x00)
                    desc_count = u32(g + 0x04)
                except Exception:
                    continue
                if desc_count <= 0 or desc_count > 4096:
                    continue
                desc_start = blob_off(blob_start, lod_blob_size, raw_desc, desc_cursor)
                if not desc_start or desc_start < blob_start or desc_start + desc_count * 0x20 > blob_end:
                    desc_start = desc_cursor
                if desc_start < blob_start or desc_start + desc_count * 0x20 > blob_end:
                    continue
                desc_cursor = max(desc_cursor, desc_start + desc_count * 0x20)
                face_cursor = desc_start + desc_count * 0x20
                for di in range(desc_count):
                    d = desc_start + di * 0x20
                    try:
                        raw_a = u32(d + 0x00); count_a = u32(d + 0x04)
                        raw_b = u32(d + 0x08); count_b = u32(d + 0x0C)
                    except Exception:
                        continue
                    if count_a > 250000 or count_b > 250000:
                        continue
                    a_start = blob_off(blob_start, lod_blob_size, raw_a, face_cursor)
                    after_a = parse_face_list(a_start, count_a, blob_start, blob_end, edge_pts)
                    face_cursor = max(face_cursor, after_a or face_cursor)
                    b_start = blob_off(blob_start, lod_blob_size, raw_b, face_cursor)
                    after_b = parse_face_list(b_start, count_b, blob_start, blob_end, edge_pts)
                    face_cursor = max(face_cursor, after_b or face_cursor)

            segs = list(edge_pts.values())
            if dbg: dbg(f"PARSE_RENDER: {tag} LOD{li} blob=0x{lod_blob_size:x} groups={group_count} edges={len(segs)}")
            if segs:
                lod_segments.append((li, segs))
            pos = next_lod

        if not lod_segments:
            return []
        if dbg: dbg("PARSE_RENDER: lod results " + ", ".join(f"LOD{li}={len(segs)}" for li, segs in lod_segments))
        if prefer_lod == 'first':
            return lod_segments[0][1]
        if prefer_lod == 'last':
            return lod_segments[-1][1]
        return max(lod_segments, key=lambda t: len(t[1]))[1]

    best = []
    best_tag = None
    for cmodel_off, cinfo, tag in candidates[:4]:
        segs = parse_from_cmodel(cmodel_off, cinfo, tag)
        if len(segs) > len(best):
            best = segs
            best_tag = tag
        if best and len(best) > 128:
            break

    if not best:
        if dbg: dbg("PARSE_RENDER: fail no drawable LOD segments from CModel candidates")
        return None
    if dbg: dbg(f"PARSE_RENDER: return {best_tag} segs={len(best)}")
    return best
