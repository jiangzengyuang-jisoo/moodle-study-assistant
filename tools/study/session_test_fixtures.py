"""Synthetic collector protocol used by offline tests and the local benchmark."""
from session import file_sha
from state_io import atomic, read


def write_snapshot(path, value):
    request_path = path.with_name('request.json')
    request = read(request_path)
    result = dict(value)
    result.setdefault('pages', [{'courseid': i, 'course_id': c['id'], 'sections': []}
                               for c in request['scope']['courses'] for i in c['moodle_course_ids']])
    result['collection_binding'] = {'protocol': 1, 'run_id': request['run_id'],
                                    'request_sha256': file_sha(request_path)}
    atomic(path, result)
