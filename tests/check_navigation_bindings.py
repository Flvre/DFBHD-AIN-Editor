"""Probe actual Tk binding dispatch using an invisible diagnostic window."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]


def check(relative):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8-sig'))
    bind_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name in ('bind_events', '_bind_events')
                   and any(isinstance(c, ast.FunctionDef) and c.name == '_bind_two_d_ctrl_key'
                           for c in n.body))
    receiver = bind_fn.args.args[0].arg
    scope = {'tk': tk, 'ttk': ttk}
    exec(compile(ast.Module(body=[bind_fn], type_ignores=[]), relative, 'exec'), scope)
    root = tk.Tk()
    root.withdraw()
    root.attributes('-alpha', 0.0)
    root.geometry('220x160+0+0')
    root.canvas = tk.Canvas(root, width=140, height=50)
    root.canvas.pack()
    button = tk.Button(root)
    entry = tk.Entry(root)
    button.pack()
    entry.pack()
    root._view_mode = '2d'
    root.mode = tk.StringVar(root, 'edit')
    root.selected_nodes = {0}
    calls = []
    for n in ast.walk(bind_fn):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == receiver:
            if not hasattr(root, n.attr):
                setattr(root, n.attr, lambda *args, **kwargs: None)
    root._nudge_node = lambda x, y: calls.append(('node', x, y))
    root._pan_grid = lambda x, y: calls.append(('pan', x, y))
    root._grid_step_up = lambda: calls.append(('grid', 'up'))
    root._grid_step_down = lambda: calls.append(('grid', 'down'))
    scope[bind_fn.name](root)
    root.deiconify()
    root.update()
    events = []
    root.canvas.bind('<KeyPress>', lambda e: events.append(
        (e.keysym, e.keycode, hex(e.state))))
    try:
        for focus in (root.canvas, root, button):
            focus.focus_force()
            root.update()
            cases = [('equal', 0, None, []), ('plus', 1, None, [('grid', 'up')]),
                     ('minus', 0, None, [('grid', 'down')])]
            for key, dx, dy in [('Up', 0, 1), ('Down', 0, -1),
                                ('Left', -1, 0), ('Right', 1, 0)]:
                for state, scale in [(0, 1), (1, .1), (4, .1)]:
                    cases.append((key, state, None, [('node', dx * scale, dy * scale)]))
            if os.name == 'nt':
                cases += [(None, 0, 107, [('grid', 'up')]),
                          (None, 0, 109, [('grid', 'down')])]
                for code, dx, dy in [(104, 0, 1), (98, 0, -1),
                                     (100, -1, 0), (102, 1, 0)]:
                    for state in (0, 16):
                        cases.append((None, state, code, [('pan', dx, dy)]))
                    for state in (1, 17):
                        cases.append((None, state, code, [('node', dx * .1, dy * .1)]))
                # Main-row digits are not keypad navigation shortcuts.
                cases.append((None, 0, 56, []))
            for key, state, code, expected in cases:
                calls.clear()
                events.clear()
                kw = {'state': state}
                if key is not None:
                    kw['keysym'] = key
                if code is not None and os.name == 'nt':
                    kw['keycode'] = code
                focus.event_generate('<KeyPress>', **kw)
                root.update()
                assert calls == expected, (relative, str(focus), key, state, code,
                                            events, calls, expected)
        if os.name == 'nt':
            for focus, view in [(entry, '2d'), (root.canvas, '3d')]:
                root._view_mode = view
                focus.focus_force()
                root.update()
                for code in (104, 98, 100, 102):
                    calls.clear()
                    focus.event_generate('<KeyPress>', keycode=code, state=1)
                    root.update()
                    assert not calls, (relative, 'keypad focus/view guard', calls)
        print(relative + ': Tk arrow, numpad, plus/minus, focus and single-dispatch checks passed',
              flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    for source in ('ui/events.py',):
        check(source)
