"""Check a built executable with a tiny map-independent generation request."""
import json
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile


def check(executable):
    executable = Path(executable).resolve()
    with tempfile.TemporaryDirectory(prefix='ain-exe-worker-') as temporary:
        folder = Path(temporary)
        job_path = folder / 'job.pkl'
        result_path = folder / 'result.pkl'
        job = dict(center=(0.0, 0.0), radius=8.0, terrain_z=18.5,
                   world_segments=[], entities_core=[], entities_snapshot=[],
                   existing_nodes=[], z_filter_enabled=False, quantized_enabled=False,
                   collision_segments_3d=[], collision_triangles_3d=[])
        with job_path.open('wb') as stream:
            pickle.dump(job, stream)
        request = json.dumps(dict(request_id='smoke', job_path=str(job_path),
                                  result_path=str(result_path))) + '\n'
        result = subprocess.run(
            [str(executable), '--generator-worker-server'],
            input=request + '{"command":"shutdown"}\n',
            cwd=folder, text=True, encoding='utf-8', errors='replace',
            capture_output=True, timeout=90,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not result_path.exists():
            raise AssertionError((result.returncode, result.stdout[-4000:], result.stderr[-4000:]))
        with result_path.open('rb') as stream:
            envelope = pickle.load(stream)
        assert envelope.get('ok'), envelope
        nodes = envelope['result']['nodes']
        assert nodes, 'Worker completed without any test nodes'
        assert 'request_done' in result.stdout, 'Worker progress pipe did not report completion'
        print('Frozen worker generated', len(nodes), 'nodes; progress pipe and shutdown passed.')


if __name__ == '__main__':
    check(sys.argv[1])
