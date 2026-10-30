"""Reproducible synthetic local benchmark; never contacts Moodle or reads personal data."""
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from session import Session, ROOT
from state_io import atomic


def main():
    with tempfile.TemporaryDirectory(prefix='bench 学习 ', dir=Path(__file__).parent) as temporary:
        root = Path(temporary)
        session = Session(root, 'benchmark')
        atomic(session.state, {'revision': 1, 'settings': {},
            'courses': [{'id': f'C{i}', 'name': f'Course {i}', 'current_topic': 'Chapter 3'} for i in range(6)],
            'materials': [{'id': f'M{i}', 'course_id': f'C{i % 6}', 'relative_path': f'C{i % 6}/Lectures/{i}.pdf',
                           'filename': f'{i}.pdf', 'sha256': '0'*64} for i in range(231)],
            'mistakes': [], 'tasks': []})
        calls = 0
        def collect(path):
            nonlocal calls
            calls += 1
            time.sleep(0.25)  # Controlled simulated network, not measured school latency.
            atomic(path, {'collection_complete': True, 'pages': [], 'threads': [], 'errors': []})
        started = time.perf_counter()
        session.context(); session.start(collector=collect)
        first = (time.perf_counter()-started)*1000
        warm = []
        for _ in range(20):
            started = time.perf_counter()
            session.context(); session.start(collector=collect)
            warm.append((time.perf_counter()-started)*1000)
        cli = []
        for _ in range(10):
            started = time.perf_counter()
            subprocess.run([sys.executable, str(ROOT / 'tools/study/session.py'), '--root', str(root),
                            '--chat', 'benchmark', 'continue'], check=True, capture_output=True)
            cli.append((time.perf_counter()-started)*1000)
        def summary(values):
            return {'median_ms': round(statistics.median(values), 2),
                    'max_ms': round(max(values), 2), 'samples': len(values)}
        print(json.dumps({'synthetic_courses': 6, 'synthetic_materials': 231,
                          'simulated_network_delay_ms': 250, 'first_call_ms': round(first, 2),
                          'in_process_continuation': summary(warm), 'fresh_process_continuation': summary(cli),
                          'network_calls': calls, 'real_school_or_model_latency_measured': False}, indent=2))


if __name__ == '__main__': main()
