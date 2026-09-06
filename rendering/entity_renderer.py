"""Persistent Tk canvas entity/obstacle layer."""
import math
from entity.entity_data import OBSTACLE_RADII, DEFAULT_RADIUS, NEVER_EXCLUDE
from config.ui_theme import C_NEVER_EXCL, C_ENTITY, C_DIM
from config.render_config import CMODEL_DETAIL_MIN_ZOOM
from cmodel.cmodel_access import _segments_draw_lod_rpx
from cmodel.cmodel_geom import _rotated_segments_for_heading
from rendering.viewport import _world_delta


class EntityRenderer:
    """Persistent entity/obstacle layer. Entities are static after BMS load.
    Rebuilds only when entity data changes or zoom crosses label threshold.
    During pan: items repositioned via coords(). No delete/recreate in normal path.
    """

    TAG       = 'p3_entity'
    TAG_LABEL = 'p3_entity_label'

    LABEL_ZOOM = 6.0

    def __init__(self, canvas, get_cmodel_segments=None,
                 request_cmodel_segments=None, model_outlines=None):
        self._canvas      = canvas
        self._items       = []
        self._label_items = []
        self._entity_snap = []
        self._last_zoom_had_labels = False
        self._last_cw     = None
        self._last_ch     = None
        self._get_cmodel_segments = get_cmodel_segments or (lambda tid, **kw: None)
        self._request_cmodel_segments = request_cmodel_segments or (lambda tid: None)
        self._model_outlines = model_outlines if model_outlines is not None else {}

    def clear(self):
        self._canvas.delete(self.TAG)
        self._canvas.delete(self.TAG_LABEL)
        self._items.clear()
        self._label_items.clear()
        self._entity_snap = []
        self._last_zoom_had_labels = False

    def sync(self, entities, viewport, cw, ch, enabled):
        if not enabled or not entities:
            self.clear()
            return

        show_labels = viewport.zoom > self.LABEL_ZOOM
        data_changed = (entities is not self._entity_snap and
                        len(entities) != len(self._entity_snap))
        size_changed = (cw != self._last_cw or ch != self._last_ch)
        label_changed = (show_labels != self._last_zoom_had_labels)
        not_built = not self._items

        if not_built or data_changed or size_changed or label_changed:
            self._rebuild(entities, viewport, cw, ch, show_labels)
        else:
            self._reposition(entities, viewport, cw, ch, show_labels)

    def _rebuild(self, entities, viewport, cw, ch, show_labels):
        c = self._canvas
        c.delete(self.TAG)
        c.delete(self.TAG_LABEL)
        self._items.clear()
        self._label_items.clear()

        for e in entities:
            ex, ey = e['x'], e['y']
            ecx, ecy = viewport.world_to_canvas(ex, ey, cw, ch)
            r   = OBSTACLE_RADII.get(e['type_id'], DEFAULT_RADIUS)
            rpx = max(1, r * viewport.zoom)

            if e['type_id'] in NEVER_EXCLUDE:
                item = c.create_rectangle(
                    ecx-4, ecy-4, ecx+4, ecy+4,
                    outline=C_NEVER_EXCL, width=1, tags=(self.TAG,))
                self._items.append(('rect', item, ex, ey))
            elif e['is_static']:
                outline_segs = (self._get_cmodel_segments(e['type_id'], allow_load=False)
                                or self._model_outlines.get(e['type_id']))
                if not outline_segs and viewport.zoom >= CMODEL_DETAIL_MIN_ZOOM:
                    self._request_cmodel_segments(e['type_id'])
                if outline_segs and rpx < 300 and viewport.zoom >= CMODEL_DETAIL_MIN_ZOOM:
                    outline_segs = _segments_draw_lod_rpx(outline_segs, rpx, e['type_id'])
                    hdg = math.radians(e.get('heading', 0))
                    cos_h, sin_h = math.cos(hdg), math.sin(hdg)
                    z = viewport.zoom
                    cw2 = c.winfo_width() or 800
                    ch2 = c.winfo_height() or 600
                    seg_items = []
                    rot_segs = _rotated_segments_for_heading(outline_segs, e.get('heading', 0))
                    half_w, half_h = cw2 * 0.5, ch2 * 0.5
                    off_x, off_y = viewport.offset_x, viewport.offset_y
                    base_x = _world_delta(ex, off_x)
                    base_y = _world_delta(ey, off_y)
                    for (r1, r2) in rot_segs:
                        sx1 = half_w + (base_x + r1[0]) * z
                        sy1 = half_h - (base_y + r1[1]) * z
                        sx2 = half_w + (base_x + r2[0]) * z
                        sy2 = half_h - (base_y + r2[1]) * z
                        li = c.create_line(sx1, sy1, sx2, sy2,
                                           fill=C_ENTITY, width=1,
                                           tags=(self.TAG,))
                        seg_items.append(li)
                    self._items.append(('outline', seg_items, ex, ey,
                                        outline_segs, e.get('heading',0)))
                elif rpx < 300:
                    item = c.create_oval(
                        ecx-rpx, ecy-rpx, ecx+rpx, ecy+rpx,
                        outline=C_ENTITY, width=1, dash=(3, 3),
                        tags=(self.TAG,))
                    self._items.append(('oval', item, ex, ey, r))
                else:
                    self._items.append(None)
            else:
                self._items.append(None)

            if show_labels:
                litem = c.create_text(
                    ecx, ecy, text=str(e['type_id']),
                    fill=C_DIM, font=('Consolas', 7),
                    tags=(self.TAG_LABEL,))
                self._label_items.append((litem, ex, ey))

        try:
            c.tag_lower(self.TAG)
            c.tag_lower(self.TAG_LABEL)
        except Exception:
            pass

        self._entity_snap          = entities
        self._last_zoom_had_labels = show_labels
        self._last_cw              = cw
        self._last_ch              = ch

    def _reposition(self, entities, viewport, cw, ch, show_labels):
        c = self._canvas
        zoom = viewport.zoom

        for i, entry in enumerate(self._items):
            if entry is None: continue
            kind = entry[0]
            if kind == 'rect':
                _, item, ex, ey = entry
                ecx, ecy = viewport.world_to_canvas(ex, ey, cw, ch)
                c.coords(item, ecx-4, ecy-4, ecx+4, ecy+4)
            elif kind == 'oval':
                _, item, ex, ey, r = entry
                ecx, ecy = viewport.world_to_canvas(ex, ey, cw, ch)
                rpx = max(1, r * zoom)
                if rpx < 300:
                    c.coords(item, ecx-rpx, ecy-rpx, ecx+rpx, ecy+rpx)
            elif kind == 'outline':
                _, seg_items, ex, ey, outline_segs, heading = entry
                rot_segs = _rotated_segments_for_heading(outline_segs, heading)
                for li, (r1, r2) in zip(seg_items, rot_segs):
                    half_w = (c.winfo_width() or 800) * 0.5
                    half_h = (c.winfo_height() or 600) * 0.5
                    base_x = _world_delta(ex, viewport.offset_x)
                    base_y = _world_delta(ey, viewport.offset_y)
                    sx1 = half_w + (base_x + r1[0]) * zoom
                    sy1 = half_h - (base_y + r1[1]) * zoom
                    sx2 = half_w + (base_x + r2[0]) * zoom
                    sy2 = half_h - (base_y + r2[1]) * zoom
                    c.coords(li, sx1, sy1, sx2, sy2)

        for litem, ex, ey in self._label_items:
            ecx, ecy = viewport.world_to_canvas(ex, ey, cw, ch)
            c.coords(litem, ecx, ecy)
