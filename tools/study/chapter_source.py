"""Finish one timestamp-based lesson transfer; preserve all historical evidence."""
import argparse
import copy
import json
from pathlib import Path
import sys

from state_io import locked
from learning_library import versions
from course_content import authorize, index_course


@locked
def finish(path, material_id, source):
    path = Path(path).resolve()
    versions.recover(path)
    before = path.read_bytes()
    state = json.loads(before.decode('utf-8-sig'))
    authorize(state, source)
    current = next(m for m in state['materials'] if m['id'] == material_id and versions.current(m))
    expected = {'course_id': source['course_id'], 'moodle_file_sources': [{
        'module_id': source['module_id'], 'remote_filename': source['filename'],
        'filepath': source.get('filepath', '/'), **source['scope']}]}
    matches = [s for s in current.get('moodle_file_sources', [])
               if versions.keys({'course_id': current['course_id'], 'moodle_file_sources': [s]}) == versions.keys(expected)]
    if len(matches) != 1:
        raise ValueError('Source identity changed while finishing lesson')
    versions.assert_source_identity(current, expected)
    # Index checks use the normal teaching reader, while the workspace lock
    # prevents another version installation from changing either original.
    indexed = index_course(path, current['course_id'], [material_id], ensure=True)
    if not indexed['materials'] or indexed['materials'][0]['status'] != 'indexed':
        raise ValueError('New lesson is not readable; old original retained')
    versions.verify(path.parent.parent, current)
    original = copy.deepcopy(state)
    for field in ('timemodified', 'timecreated'):
        matches[0][field] = source.get(field)
    # Old unbound records can gain provenance after an authorized real transfer;
    # never replace a known conflicting identity.
    matches[0].update(source['scope'])
    # Follow only the explicit timestamp-update lineage. A failed replacement
    # can sit between the readable version and the old usable original. Validate
    # the entire eligible chain before deleting any file, including ancestors.
    by_id = {m['id']: m for m in state['materials']}
    chain, seen, child = [], {current['id']}, current
    while child.get('chapter_timestamp_update') is True and child.get('supersedes'):
        previous = by_id.get(child['supersedes'])
        if not previous or previous['id'] in seen:
            raise ValueError('Invalid previous source chain; originals retained')
        seen.add(previous['id'])
        if previous.get('superseded_by') != child['id'] or versions.keys(previous) != versions.keys(child):
            raise ValueError('Cannot remove an unverified previous source')
        versions.assert_source_identity(previous, child)
        versions.assert_source_identity(previous, current)
        status = previous.get('version_status')
        if status not in {'archived', 'deletion_pending', 'deleted', 'missing'}:
            raise ValueError('Previous original is not a managed replacement')
        target = versions.safe(path.parent.parent, previous['relative_path'])
        if status in {'archived', 'deletion_pending'}:
            if '_Archive' not in target.relative_to(path.parent.parent).parts:
                raise ValueError('Only this managed archived original may be removed')
            if any(m['id'] != previous['id'] and m.get('relative_path') == previous['relative_path']
                   for m in state['materials']):
                raise ValueError('Original path is shared; retain for review')
            if status == 'archived':
                versions.verify(path.parent.parent, previous)
            elif target.exists() and versions.sha(target) != previous['sha256']:
                raise ValueError('Previous original was edited; retained')
            previous.update(version_status='deletion_pending', deletion_reason='readable_timestamp_replacement')
        elif target.exists():
            raise ValueError('Deleted or missing historical original reappeared; retained for review')
        chain.append((previous, target))
        child = previous
    if state != original:
        versions._commit(path, before, state)
    for previous, target in chain:
        if previous.get('version_status') != 'deletion_pending':
            continue
        if target.exists():
            if versions.sha(target) != previous['sha256']:
                raise ValueError('Previous original was edited; retained')
            target.unlink()
        # A crash after unlink or between ancestors keeps honest pending/deleted
        # markers. Retry traverses them, without scanning unrelated old archives.
        before = path.read_bytes()
        previous.update(version_status='deleted', deleted_at=versions.instant().isoformat())
        versions._commit(path, before, state)
    return {'material_id': material_id, 'readable': True,
            'previous_status': chain[0][0].get('version_status') if chain else None,
            'deleted_material_ids': [m['id'] for m, _ in chain if m.get('version_status') == 'deleted']}



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--material', required=True)
    args = parser.parse_args()
    print(json.dumps(finish(args.state, args.material, json.load(sys.stdin))))
