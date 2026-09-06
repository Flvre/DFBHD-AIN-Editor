"""AIN graph data model and binary NAI1/NAI2 format operations."""

import struct
import os

SENTINEL       = 0x0000
MAX_NEIGHBORS  = 16
EXTRA_COUNT    = 256


class Node:
    __slots__ = ['id','x','y','z','b12','b13','b14','b15','b16','b17','b18',
                 'neighbors','raw_bytes','quantized_source_keys','quantized_map_grid',
                 'scan_0x32']
    def __init__(self,id,x,y,z,b12=0,b13=0,b14=0,b15=36,b16=0,b17=0,b18=0,
                 neighbors=None,raw_bytes=None,quantized_source_keys=None,
                 quantized_map_grid=False,scan_0x32=None):
        self.id=id; self.x=x; self.y=y; self.z=z
        self.b12=b12; self.b13=b13; self.b14=b14; self.b15=b15
        self.b16=b16; self.b17=b17; self.b18=b18
        self.neighbors=neighbors if neighbors is not None else []
        self.raw_bytes=raw_bytes  # full 52+ bytes from AIN file for inspection
        # Editor-only provenance.  A quantized survivor owns the base lattice
        # cells from which it was promoted.  Keeping that ownership across
        # additive batches lets seams inherit topology instead of guessing by
        # distance.  These fields are deliberately not written to AIN.
        # Provenance may cross the worker boundary through dict records, where
        # tuple lattice keys are intentionally represented as JSON-safe lists.
        # Normalize every key back to an immutable (qx, qy) pair before storing
        # it in a frozenset.  Without this, quantized generation crashes while
        # reconstructing worker nodes because lists are unhashable.
        _normalized_quantized_keys = set()
        if quantized_source_keys and not isinstance(quantized_source_keys, (str, bytes)):
            for _source_key in quantized_source_keys:
                try:
                    _normalized_quantized_keys.add(
                        (int(_source_key[0]), int(_source_key[1])))
                except Exception:
                    continue
        self.quantized_source_keys = frozenset(_normalized_quantized_keys)
        self.quantized_map_grid = bool(quantized_map_grid)
        self.scan_0x32 = scan_0x32
        self.id=id; self.x=x; self.y=y; self.z=z
        self.b12=b12; self.b13=b13; self.b14=b14; self.b15=b15
        self.b16=b16; self.b17=b17; self.b18=b18
        self.neighbors=neighbors if neighbors is not None else []

    def acceptance_radius(self):
        return self.b15 * 0x1000 / 65536.0

    def to_dict(self):
        d = {'id':self.id,'x':self.x,'y':self.y,'z':self.z,
             'b12':self.b12,'b13':self.b13,'b14':self.b14,'b15':self.b15,
             'b16':self.b16,'b17':self.b17,'b18':self.b18,
             'neighbors':list(self.neighbors),
             'quantized_source_keys':[list(key) for key in self.quantized_source_keys],
             'quantized_map_grid':self.quantized_map_grid}
        if self.scan_0x32 is not None:
            d['scan_0x32'] = self.scan_0x32
        return d

    @staticmethod
    def from_dict(d):
        return Node(d['id'],d['x'],d['y'],d['z'],
                    d.get('b12',0),d.get('b13',0),d.get('b14',0),d.get('b15',36),
                    d.get('b16',0),d.get('b17',0),d.get('b18',0),
                    d.get('neighbors',[]),
                    quantized_source_keys=d.get('quantized_source_keys'),
                    quantized_map_grid=d.get('quantized_map_grid',False),
                    scan_0x32=d.get('scan_0x32'))


def write_ain(node_list, out_path, log=print, area_states=None):
    """Write a NAI2 .ain file.
    area_states: list of 256 entries x 12 bytes from read_ain().
    Written verbatim as the AreaStates tail. The engine (FUN_00407570) reads
    this directly into DAT_006800b8 (PSP flags table).
    entry[zone_id] byte 0 = 0x02 makes that zone a valid takedown target.
    If None or empty, writes 256 zero entries (no takedown zones).
    """
    n = len(node_list)
    with open(out_path, 'wb') as f:
        f.write(b'NAI2')
        f.write(struct.pack('<I', n))
        for node in node_list:
            nb = node.neighbors[:MAX_NEIGHBORS]
            nc = len(nb)
            weight = node.b15
            rec = bytearray(52)
            struct.pack_into('<i', rec, 0x00, int(node.x * 65536))
            struct.pack_into('<i', rec, 0x04, int(node.y * 65536))
            struct.pack_into('<i', rec, 0x08, int(node.z * 65536))
            rec[0x0c] = node.b12; rec[0x0d] = node.b13; rec[0x0e] = node.b14
            rec[0x0f] = weight;   rec[0x10] = node.b16; rec[0x11] = node.b17
            rec[0x12] = node.b18; rec[0x13] = nc
            scan_val = getattr(node, 'scan_0x32', None)
            for k in range(16):
                if k < nc:
                    struct.pack_into('<H', rec, 0x14 + k*2, nb[k])
                elif k == 15 and scan_val is not None:
                    struct.pack_into('<H', rec, 0x14 + k*2, scan_val & 0xFFFF)
                else:
                    struct.pack_into('<H', rec, 0x14 + k*2, SENTINEL)
            f.write(rec)
        # AreaStates tail — always 256 entries x 12 bytes
        # Preserves original takedown flags verbatim; pads remainder with zeros.
        f.write(struct.pack('<I', EXTRA_COUNT))
        blank = b'\x00' * 0xC
        for i in range(EXTRA_COUNT):
            if area_states and i < len(area_states):
                entry = area_states[i]
                if len(entry) < 0xC:
                    entry = entry + b'\x00' * (0xC - len(entry))
                f.write(entry[:0xC])
            else:
                f.write(blank)
    size = 8 + n * 52 + 4 + EXTRA_COUNT * 12
    breach_zones = []
    if area_states:
        for i, e in enumerate(area_states):
            if len(e) >= 4 and struct.unpack_from('<I', e)[0] & 0x02:
                breach_zones.append(i)
    breach_info = f" — takedown zones: {breach_zones}" if breach_zones else ""
    log(f"Written: {n} nodes, {size} bytes -> {os.path.basename(out_path)}{breach_info}")


def read_ain(path, log=print):
    with open(path,'rb') as f: data=f.read()
    # NAI1 and NAI2 use the same 52-byte node records and AreaStates tail.
    # The format revision is carried by the four-byte magic only.
    supported_magics = (b'NAI1', b'NAI2')
    if data[:4] in supported_magics:
        ain_data = data
        ain_magic = data[:4]
    elif len(data) > 0x428:
        # BMX wrapper: header is 0x428 bytes, AIN data follows
        ain_data = data[0x428:]
        if ain_data[:4] not in supported_magics:
            log("Not a supported NAI1/NAI2 or BMX file"); return [], []
        ain_magic = ain_data[:4]
        log(f"BMX wrapper detected, reading embedded {ain_magic.decode('ascii')}")
    else:
        log("Not a supported NAI1/NAI2 or BMX file"); return [], []
    n = struct.unpack_from('<I', ain_data, 4)[0]
    if n == 0 or n > 200000:
        log(f"Suspicious node count: {n}"); return [], []
    nodes = []
    for i in range(n):
        off = 8 + i * 0x34
        if off + 0x34 > len(ain_data): break
        x  = struct.unpack_from('<i', ain_data, off+0x00)[0] / 65536.0
        y  = struct.unpack_from('<i', ain_data, off+0x04)[0] / 65536.0
        z  = struct.unpack_from('<i', ain_data, off+0x08)[0] / 65536.0
        b12 = ain_data[off+0x0c]; b13 = ain_data[off+0x0d]
        b14 = ain_data[off+0x0e]; b15 = ain_data[off+0x0f]
        b16 = ain_data[off+0x10]; b17 = ain_data[off+0x11]
        b18 = ain_data[off+0x12]; nc  = ain_data[off+0x13]
        nbs = []
        for k in range(min(nc, 16)):
            v = struct.unpack_from('<H', ain_data, off+0x14+k*2)[0]
            if v != 0xFFFF and v < n:
                nbs.append(v)
        # Store exact 52 bytes for lossless round-trip
        raw = bytes(ain_data[off:off+0x34])
        scan_0x32 = None
        if nc <= 15:
            sv = struct.unpack_from('<H', ain_data, off+0x32)[0]
            if sv != 0:
                scan_0x32 = sv
        nodes.append(Node(i, x, y, z, b12, b13, b14, b15, b16, b17, b18, nbs,
                          raw_bytes=raw, scan_0x32=scan_0x32))

    # Read AreaStates tail (PSP / takedown flags)
    # FUN_00407570 reads this directly into DAT_006800b8 (PSP flags table).
    # entry[zone_id] first int32 flags: 0x02 = zone is a valid takedown target.
    area_states = []
    tail_off = 8 + n * 0x34
    if tail_off + 4 <= len(ain_data):
        tail_count = struct.unpack_from('<I', ain_data, tail_off)[0]
        if 0 < tail_count <= 512:
            data_off = tail_off + 4
            for i in range(tail_count):
                entry_off = data_off + i * 0xC
                if entry_off + 0xC <= len(ain_data):
                    area_states.append(bytes(ain_data[entry_off:entry_off+0xC]))
                else:
                    area_states.append(b'\x00' * 0xC)
            nonzero = sum(1 for e in area_states if any(b != 0 for b in e))
            if nonzero:
                log(f"AreaStates: {tail_count} entries, {nonzero} non-zero (takedown zones present)")

    log(f"Loaded {len(nodes)} nodes from {ain_magic.decode('ascii')}")
    return nodes, area_states


def connect_nodes(a, b, max_neighbors=MAX_NEIGHBORS):
    """Authoritative bidirectional connection. Always adds both directions.
    Returns True if connection was made (at least one direction was new)."""
    changed = False
    if b.id not in a.neighbors and len(a.neighbors) < max_neighbors:
        a.neighbors.append(b.id)
        changed = True
    if a.id not in b.neighbors and len(b.neighbors) < max_neighbors:
        b.neighbors.append(a.id)
        changed = True
    return changed


def sanitize_node_graph(nodes, log=print):
    """Normalize loaded/generated node references to a safe editor graph.

    Campaign oddities and malformed test clusters can contain duplicate, self,
    or out-of-range neighbor references. The runtime may tolerate them better
    than the editor UI does, so we clean them up when loading/refreshing.
    """
    n = len(nodes)
    removed = 0
    for i, node in enumerate(nodes):
        node.id = i
    for i, node in enumerate(nodes):
        clean = []
        seen = set()
        for nb in node.neighbors:
            if not isinstance(nb, int):
                removed += 1
                continue
            if nb < 0 or nb >= n or nb == i or nb in seen:
                removed += 1
                continue
            seen.add(nb)
            clean.append(nb)
        if len(clean) > MAX_NEIGHBORS:
            removed += len(clean) - MAX_NEIGHBORS
            clean = clean[:MAX_NEIGHBORS]
        node.neighbors = clean
    if removed:
        log(f"Sanitized node graph: removed {removed} invalid neighbor refs")
    return nodes
