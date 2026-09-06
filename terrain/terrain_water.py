"""Terrain water level helpers — BMS water attach, water mask, navigable height."""
import struct
from pathlib import Path
from shared.debug_log import _dbg
from terrain.terrain import _terrain_water_height_world, _terrain_sample_height


def _attach_bms_water_level(info, bms_path):
    """Attach/verify BMS uint32 water level stored at header offset 0x98."""
    if not isinstance(info, dict) or not bms_path:
        return info
    try:
        with open(bms_path, 'rb') as stream:
            stream.seek(0x98)
            raw_bytes = stream.read(4)
        if len(raw_bytes) != 4:
            return info
        raw = int(struct.unpack('<I', raw_bytes)[0])
        info['bms_water_level_raw'] = raw

        current = info.get('water_level_raw')
        if current in (None, ''):
            info['water_level_raw'] = raw
            info['water_height_world'] = float(raw) * 0.5
            info['water_source'] = f'BMS {Path(bms_path).name} @0x98'
        else:
            try:
                if int(float(current)) != raw:
                    _dbg(
                        'TERRAIN',
                        f"[TERRAIN] MIS/BMS water mismatch: MIS={current} BMS={raw}",
                        once_key=('water_mismatch', str(bms_path), current, raw))
            except Exception:
                pass
        return info
    except Exception:
        return info


def _build_terrain_water_mask(info):
    """Build/cache a 1024x1024 RGBA mask for CPT samples below water."""
    if not isinstance(info, dict):
        return None
    cached = info.get('water_mask')
    if cached is not None:
        return cached

    hm = info.get('heightmap')
    water_z = _terrain_water_height_world(info)
    if hm is None or water_z is None or len(hm) < 1024 * 1024:
        return None

    try:
        from PIL import Image
        threshold = float(water_z) * 256.0
        alpha = bytes(
            92 if float(hm[i]) < threshold - 1.0 else 0
            for i in range(1024 * 1024)
        )
        a = Image.frombytes('L', (1024, 1024), alpha)
        layer = Image.new('RGBA', (1024, 1024), (24, 92, 148, 0))
        layer.putalpha(a)
        info['water_mask'] = layer
        info['water_mask_cache'] = {(1024, 1024): layer}
        return layer
    except Exception as exc:
        _dbg('TERRAIN', f'[TERRAIN] water mask build failed: {exc}',
             once_key=('water_mask_fail', str(exc)))
        return None


def _terrain_water_mask_for_size(info, size):
    """Return cached water mask scaled to the active terrain image size."""
    base = _build_terrain_water_mask(info)
    if base is None:
        return None
    try:
        size = (int(size[0]), int(size[1]))
        cache = info.setdefault('water_mask_cache', {})
        if size in cache:
            return cache[size]
        from PIL import Image
        scaled = base.resize(size, Image.NEAREST)
        cache[size] = scaled
        return scaled
    except Exception:
        return base


def _terrain_navigable_height(info, wx, wy, *, water_epsilon=0.05):
    """Sample dry terrain; submerged terrain is not navigation support."""
    height = _terrain_sample_height(info, wx, wy)
    if height is None:
        return None
    water_height = _terrain_water_height_world(info)
    if (water_height is not None
            and float(height) < float(water_height) - float(water_epsilon)):
        return None
    return float(height)
