"""Exercise real project I/O methods without starting Tk or generator workers."""
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
METHODS = {
    '_project_json_safe', '_build_project_data', 'save_project', 'load_project',
    '_restore_generator_seed_history', '_autosave_tick',
    '_retry_previous_generator_seed',
}


def io_harness(relative):
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    original = next(c for c in tree.body if isinstance(c, ast.ClassDef)
                    and any(isinstance(m, ast.FunctionDef) and m.name == 'save_project'
                            for m in c.body))
    methods = [m for m in original.body if isinstance(m, ast.FunctionDef)
               and m.name in METHODS]
    cls = ast.ClassDef(name=original.name, bases=[], keywords=[], body=methods,
                       decorator_list=[])
    scope = dict(__file__=str(path), os=os, json=json, filedialog=Mock(),
                 messagebox=Mock(), _configured_additional_pff_dirs=lambda: [],
                 _save_additional_pff_dirs=Mock(),
                 sanitize_node_graph=lambda nodes, status: nodes,
                 Node=SimpleNamespace(from_dict=lambda data: data))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(path), 'exec'), scope)
    editor = scope[original.name]()
    editor.nodes = []
    editor.zones = {1: 'test'}
    editor.zone_meta = {1: {'nodes': {2, 3}, 'tiles': {(1., 2., 3.)}}}
    editor._ensure_zone_meta = lambda key: editor.zone_meta[key]
    editor.bms_path = os.path.abspath('missing-test-map.bms')
    editor.terrain_z = 18.5
    editor._previous_generator_seeds = []
    editor._last_generator_metrics = {'nested': {'set': {1, 2}, 'tuple': (3, 4)}}
    editor._pil_renderer = None
    editor._autosave_enabled = SimpleNamespace(get=lambda: True)
    for name in ('status', '_start_autosave_timer', '_refresh_previous_generator_seeds_window',
                 '_reset_transient_editor_state', '_reset_zfilter', '_rebuild_hit_tester',
                 '_compute_debug_ranges', '_update_stats', 'redraw',
                 '_invalidate_pff_resource_context', 'fit_view', 'refresh_3d_views'):
        setattr(editor, name, Mock())
    return editor, scope


class ProjectSeedHistoryTests(unittest.TestCase):
    def test_save_load_retry_and_autosave(self):
        for source in ('app.py',):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                editor, scope = io_harness(source)
                seed = dict(index=1, time='12:34:56', kind='Seed', x=12.25, y=-45.5,
                            radius=80., target_node_z=36.5, bms_path=editor.bms_path,
                            settings=dict(z_filter_enabled=True, rooftops_enabled=True,
                                          highest_broad_rooftops_only=True, ladders_enabled=True,
                                          quantized_enabled=True, ignore_destroyable_objects=False,
                                          ignore_vehicles=True))
                editor._previous_generator_seeds = [seed]
                path = str(Path(folder) / 'project.json')
                scope['filedialog'].asksaveasfilename.return_value = path
                editor.save_project()
                saved = json.loads(Path(path).read_text())
                self.assertEqual(saved['previous_generator_seeds'], [seed])
                self.assertEqual(saved['last_generator_metrics']['nested']['set'], [1, 2])
                self.assertEqual(saved['zone_meta']['1']['tiles'], [[1., 2., 3.]])

                restored, load_scope = io_harness(source)
                restored._previous_generator_seeds = [{'old': True}]
                load_scope['filedialog'].askopenfilename.return_value = path
                restored.load_project()
                self.assertEqual(restored._previous_generator_seeds, [seed])
                restored._refresh_previous_generator_seeds_window.assert_called_once_with(
                    select_last=True)
                if hasattr(restored, '_retry_previous_generator_seed'):
                    restored._selected_previous_generator_seed = lambda: restored._previous_generator_seeds[0]
                    calls = []
                    def run(x, y, **kwargs):
                        calls.append((x, y, kwargs, restored._seed_generate_radius,
                                      restored._seed_generate_quantized,
                                      restored._seed_generate_ladders,
                                      restored._seed_generate_z_filter,
                                      restored._seed_generate_rooftops,
                                      restored._seed_generate_highest_rooftops,
                                      restored._seed_ignore_destroyable,
                                      restored._seed_ignore_vehicles))
                    restored._run_generator_at = run
                    restored._retry_previous_generator_seed()
                    self.assertEqual(calls, [(12.25, -45.5, {'target_node_z': 36.5},
                                              80., True, True, True, True, True, False, True)])
                    load_scope['messagebox'].showwarning.assert_not_called()
                editor._previous_generator_seeds[0]['radius'] = 90.
                editor._autosave_tick()
                self.assertEqual(json.loads(Path(path).read_text())[
                    'previous_generator_seeds'][0]['radius'], 90.)
                del saved['previous_generator_seeds']
                Path(path).write_text(json.dumps(saved))
                restored.load_project()
                self.assertEqual(restored._previous_generator_seeds, [])

    def test_invalid_entries_and_history_limit(self):
        editor, _ = io_harness('app.py')
        valid = dict(x=1, y=2, radius=3, settings={}, target_node_z=None)
        editor._restore_generator_seed_history({'previous_generator_seeds': [
            None, {}, dict(valid, x=float('nan')), dict(valid, radius=-1),
            dict(valid, settings=['bad']), valid]})
        self.assertEqual(len(editor._previous_generator_seeds), 1)
        editor._restore_generator_seed_history({'previous_generator_seeds': [valid] * 502})
        self.assertEqual(len(editor._previous_generator_seeds), 500)
        self.assertEqual(editor._previous_generator_seeds[-1]['index'], 500)
        editor._restore_generator_seed_history({'previous_generator_seeds': {}})
        self.assertEqual(editor._previous_generator_seeds, [])


if __name__ == '__main__':
    unittest.main()
