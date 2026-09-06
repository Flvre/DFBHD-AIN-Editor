"""Terrain loading pipeline — MIS/TRN parsing, colormap decode, PIL image load."""
import io
from pathlib import Path
from shared.debug_log import _dbg
from terrain.terrain import (_parse_simple_script_kv, _ensure_ext,
    _parse_trn_spatial_metadata, _extract_cpt_heightmap, _build_cpt_chd_images)
from resources.asset_resolve import _resolve_game_asset_bytes
from resources.pff_archive import _iter_pff_archives, _pff_find_candidates
from config.editor_config import _configured_additional_pff_dirs

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False


def _load_pil_image_from_bytes(raw, name=''):
    if not raw or not _PIL_AVAILABLE:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        return im.convert('RGB')
    except Exception as e:
        _dbg('TERRAIN', f'[TERRAIN] PIL failed to decode {name}: {e}', once_key=('decode_fail', name))
        return None


def _guess_mis_for_bms(bms_path):
    """Try common MIS names for a BMS path."""
    if not bms_path:
        return None
    p = Path(bms_path)
    candidates = [
        p.with_suffix('.mis'),
        p.with_suffix('.MIS'),
        p.parent / (p.stem.upper() + '.MIS'),
        p.parent / (p.stem.lower() + '.mis'),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _load_terrain_backdrop_from_mis(mis_path, game_dir=None,
        terrain_name_override=None, source_label='MIS', *, pff_cache=None):
    """Load first-pass terrain backdrop from MIS -> TRN -> polytrn_colormap.

    Returns dict with:
      ok, mis_path, terrain_name, trn_file, colormap_file, image, debug_lines
    """
    if pff_cache is None:
        pff_cache = {}
    result = {
        'ok': False,
        'mis_path': str(mis_path) if mis_path else '',
        'terrain_name': '',
        'terrain_trn': '',
        'terrain_trn_source': '',
        'colormap_file': '',
        'colormap_source': '',
        'depthmap_file': '',
        'depthmap_source': '',
        'detailmap_file': '',
        'detailmap_source': '',
        'tilestrip_file': '',
        'tileinfo_file': '',
        'tile_tga': '',
        'trn_fields': {},
        'mis_fields': {},
        'water_level_raw': None,
        'water_height_world': None,
        'water_source': '',
        'bms_water_level_raw': None,
        'water_mask': None,
        'water_mask_cache': {},
        'image': None,
        'primary_image': None,
        'secondary_image': None,
        'heightmap': None,
        'height_info': {},
        'height_display_image': None,
        'depth_display_image': None,
        'spatial': {},
        'approximate_mode': False,
        'approximate_reason': '',
        'compat_offset_x': 0.0,
        'compat_offset_y': 0.0,
        'compat_note': '',
        'debug_lines': [],
        '_phase_mode': 'medcook_yneg',
    }
    def log(msg):
        result['debug_lines'].append(msg)
        _dbg('TERRAIN', msg)

    if not mis_path and not terrain_name_override:
        log('[TERRAIN] no MIS path supplied')
        return result

    if terrain_name_override:
        mis_path = Path(mis_path) if mis_path else None
        game_dir = Path(game_dir) if game_dir else (mis_path.parent if mis_path else Path.cwd())
        terrain_name = str(terrain_name_override).strip()
        tile_tga = ''
        result['mis_path'] = str(mis_path) if mis_path else str(source_label or 'BMS')
    else:
        mis_path = Path(mis_path)
        game_dir = Path(game_dir) if game_dir else mis_path.parent

        try:
            mis_text = mis_path.read_text(encoding='latin1', errors='replace')
        except Exception as e:
            log(f'[TERRAIN] failed to read MIS {mis_path}: {e}')
            return result

        mis_kv = _parse_simple_script_kv(mis_text)
        result['mis_fields'] = dict(mis_kv)
        terrain_name = mis_kv.get('terrain', '')
        tile_tga = mis_kv.get('terrain_tile_tga', '')

        _mis_water = mis_kv.get('water_level')
        if _mis_water not in (None, ''):
            try:
                result['water_level_raw'] = int(float(_mis_water))
                result['water_height_world'] = float(_mis_water) * 0.5
                result['water_source'] = f'MIS {mis_path.name}: water_level'
                log(
                    f"[TERRAIN] water from MIS: raw={result['water_level_raw']} "
                    f"world_z={result['water_height_world']:.3f}m")
            except Exception:
                pass
    result['terrain_name'] = terrain_name
    result['tile_tga'] = _ensure_ext(tile_tga, 'tga') if tile_tga else ''

    if not terrain_name:
        src_name = mis_path.name if mis_path else str(source_label or 'BMS')
        log(f'[TERRAIN] {src_name}: no terrain field')
        return result

    trn_file = _ensure_ext(terrain_name, 'trn')
    result['terrain_trn'] = trn_file
    src_name = f'MIS {mis_path.name}' if mis_path else str(source_label or 'BMS')
    log(f'[TERRAIN] {src_name}: terrain={terrain_name!r} -> {trn_file!r}')

    trn_raw, trn_source = _resolve_game_asset_bytes(game_dir, trn_file, pff_cache=pff_cache)
    if not trn_raw:
        log(f'[TERRAIN] missing TRN asset: {trn_file}')
        try:
            for p in _iter_pff_archives(Path(game_dir), additional_dirs=_configured_additional_pff_dirs()):
                cands = _pff_find_candidates(p, trn_file, limit=8, cache=pff_cache)
                if cands:
                    log('[TERRAIN] candidates in ' + str(p) + ': ' + ', '.join(f'{n} ({s}b)' for n, s in cands))
        except Exception:
            pass
        return result
    result['terrain_trn_source'] = trn_source or ''
    log(f'[TERRAIN] loaded {trn_file} from {trn_source} ({len(trn_raw)} bytes)')

    trn_text = trn_raw.decode('latin1', errors='replace')
    trn_kv = _parse_simple_script_kv(trn_text)
    result['trn_fields'] = dict(trn_kv)
    result['spatial'] = _parse_trn_spatial_metadata(trn_text)
    sp = result['spatial']
    log(f"[TERRAIN] spatial: origin={sp.get('origin')} sectorcount={sp.get('sectorcount')} raw_rows={len(sp.get('sectors_raw') or [])} expanded=16x16 world=({sp.get('world_x0'):.0f},{sp.get('world_y0'):.0f})..({sp.get('world_x1'):.0f},{sp.get('world_y1'):.0f})")

    colormap = trn_kv.get('polytrn_colormap') or trn_kv.get('colormap') or ''
    if not colormap:
        log(f'[TERRAIN] {trn_file}: no polytrn_colormap field')
        return result
    result['colormap_file'] = colormap
    log(f'[TERRAIN] {trn_file}: polytrn_colormap={colormap!r}')

    result['depthmap_file'] = trn_kv.get('polytrn_depthmap') or trn_kv.get('depthmap') or ''
    result['detailmap_file'] = trn_kv.get('polytrn_detailmap') or trn_kv.get('detailmap') or ''
    result['tilestrip_file'] = trn_kv.get('polytrn_tilestrip') or trn_kv.get('tilestrip') or ''
    result['tileinfo_file'] = trn_kv.get('polytrn_tileinfo') or trn_kv.get('tileinfo') or ''
    if result['tilestrip_file'] or result['tileinfo_file']:
        result['approximate_mode'] = True
        result['approximate_reason'] = 'tilestrip/tileinfo assets present; editor uses first-pass quadrant terrain only'
        log('[TERRAIN] advanced poly terrain assets present; current backdrop/height model is approximate')

    polydata = trn_kv.get('polytrn_polydata') or trn_kv.get('polydata') or ''
    if polydata:
        result['polydata_file'] = polydata
        cpt_raw, cpt_source = _resolve_game_asset_bytes(game_dir, polydata, pff_cache=pff_cache)
        if cpt_raw:
            result['polydata_source'] = cpt_source or ''
            hm, hinfo = _extract_cpt_heightmap(cpt_raw, log)
            result['heightmap'] = hm
            result['height_info'] = hinfo or {}
            if hm is not None:
                log(f"[TERRAIN] heightmap ready from {polydata}: raw {hinfo.get('min_raw')}..{hinfo.get('max_raw')}")
                height_image, depth_image = _build_cpt_chd_images(hm)
                result['height_display_image'] = height_image
                result['depth_display_image'] = depth_image
                if height_image is not None and depth_image is not None:
                    log('[TERRAIN] MED C/H/D terrain views ready')
            else:
                log(f"[TERRAIN] CPT heightmap not found in {polydata}: {hinfo}")
        else:
            log(f"[TERRAIN] missing polydata asset: {polydata}")

    img_raw, img_source = _resolve_game_asset_bytes(game_dir, colormap, pff_cache=pff_cache)
    if not img_raw:
        log(f'[TERRAIN] missing colormap asset: {colormap}')
        return result
    result['colormap_source'] = img_source or ''
    log(f'[TERRAIN] loaded {colormap} from {img_source} ({len(img_raw)} bytes)')

    im = _load_pil_image_from_bytes(img_raw, colormap)
    if im is None:
        log(f'[TERRAIN] failed to decode terrain colormap: {colormap}')
        return result

    result['image'] = im
    result['primary_image'] = im

    secondary_name = result['depthmap_file'] or result['detailmap_file']
    if secondary_name:
        secondary_raw, secondary_source = _resolve_game_asset_bytes(game_dir, secondary_name, pff_cache=pff_cache)
        if secondary_raw:
            secondary_im = _load_pil_image_from_bytes(secondary_raw, secondary_name)
            if secondary_im is not None:
                result['secondary_image'] = secondary_im
                if secondary_name == result['depthmap_file']:
                    result['depthmap_source'] = secondary_source or ''
                else:
                    result['detailmap_source'] = secondary_source or ''
                log(f'[TERRAIN] secondary terrain image ready: {secondary_name} '
                    f'{secondary_im.size[0]}x{secondary_im.size[1]}')
            else:
                log(f'[TERRAIN] failed to decode secondary terrain image: {secondary_name}')
        else:
            log(f'[TERRAIN] missing secondary terrain image asset: {secondary_name}')

    result['ok'] = True
    log(f'[TERRAIN] backdrop ready: {colormap} {im.size[0]}x{im.size[1]}')
    return result
