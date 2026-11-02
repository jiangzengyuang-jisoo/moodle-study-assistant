"""Account-bound bulk download installation helpers and honest index results."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def site_key(value):
    value = urlsplit(value)
    if value.scheme != 'https' or not value.hostname or value.username or value.password or value.query or value.fragment:
        raise ValueError('Invalid course content site')
    host = value.hostname.encode('idna').decode().lower()
    if ':' in host:
        host = '[' + host + ']'
    port = value.port
    authority = host + (':' + str(port) if port and port != 443 else '')
    return ('https://' + authority + value.path).rstrip('/')


def authorize(state, source):
    scope = source.get('scope') or {}
    site, user, page = site_key(scope.get('site', '')), scope.get('user_id'), scope.get('page_id')
    if type(user) is not int or user <= 0 or type(page) is not int or page <= 0:
        raise ValueError('Course content permission requires an account and course page')
    owners = []
    for course in state.get('courses', []):
        ids = course.get('moodle_course_ids')
        if not isinstance(ids, list):
            ids = [p.get('course_id') for p in (course.get('moodle_progress') or {}).get('pages', [])]
        if any(type(candidate) is int and candidate == page for candidate in ids):
            owners.append(course.get('id'))
    if set(owners) != {source.get('course_id')}:
        raise ValueError('Course content permission no longer matches selected courses')
    policy = state.get('settings', {}).get('course_content_sync') or {}
    grants = []
    if policy.get('version') == 1:
        for grant in policy.get('grants', []):
            try:
                if site_key(grant.get('site', '')) == site and type(grant.get('user_id')) is int and grant.get('user_id') == user and type(grant.get('moodle_course_id')) is int and grant.get('moodle_course_id') == page:
                    grants.append(grant)
            except (ValueError, TypeError):
                pass
    if len(grants) != 1 or grants[0].get('mode') != 'full':
        raise ValueError('Course content download permission is missing or revoked')
    try:
        datetime.fromisoformat(grants[0]['authorized_at'].replace('Z', '+00:00'))
    except (ValueError, KeyError, AttributeError):
        raise ValueError('Invalid course content authorization timestamp') from None
    return {'site': site, 'user_id': user, 'page_id': page}


def prepare_batch(state, data, row):
    source = data['source']
    scope = authorize(state, source)
    row['moodle_file_sources'][0].update(scope)
    for key in ('post_id', 'discussion_id', 'forum_id', 'section_id', 'mimetype'):
        if source.get(key) is not None:
            row['moodle_file_sources'][0][key] = source[key]
    if data.get('source_subdirectory') is True:
        identity = [scope['site'], scope['page_id'], source['module_id'], source.get('post_id'), source.get('filepath', '/'), source['filename']]
        row['source_subdirectory'] = 'source-' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    if data.get('retain_unparsed') is True:
        row['structure_validation'] = 'deferred'
    row['content_reviewed'] = False


def restore_missing(study, row, staged):
    """Restore identical missing bytes without changing an immutable material ID."""
    from learning_library import versions
    target = versions.safe(study, row['relative_path'])
    if target.exists():
        return False
    if any(part.casefold() in {'_system', 'backups', '错题本'} for part in target.relative_to(study).parts):
        raise ValueError('Cannot restore learning records as course originals')
    if versions.sha(staged) != row.get('sha256'):
        raise ValueError('Restored content does not match the registered original')
    target.parent.mkdir(parents=True, exist_ok=True)
    versions.safe(study, row['relative_path'])
    # Windows rename and POSIX link both fail atomically if an uncooperative
    # writer creates the destination after our check. Never use replace here.
    if os.name == "nt":
        staged.rename(target)
    else:
        os.link(staged, target)
        staged.unlink()
    return True


def index_course(state_path, course):
    from library import Library
    from learning_library.versions import current
    lib = Library(state_path)
    result = lib.sync(course)
    state = lib.state()
    registered = [m for m in state.get('materials', []) if m.get('course_id') == course and current(m)]
    catalog = {row['id']: row for row in lib.catalog()['materials']}
    rows = []
    for material in registered:
        row = catalog.get(material['id'])
        if not row or row.get('sha256') != material.get('sha256') or row.get('registry_sha256') != material.get('sha256'):
            rows.append({'id': material['id'], 'status': 'stale_index', 'text_units': 0,
                         'warnings': ['registry_changed_since_index_build'], 'relative_path': material['relative_path']})
        else:
            rows.append({k: row.get(k) for k in ('id', 'status', 'units', 'text_units', 'warnings', 'relative_path', 'sha256')})
    for row in rows:
        if 'extraction_failed' in (row.get('warnings') or []):
            row['status'] = 'extraction_error'
        elif row['status'] == 'indexed' and not row.get('text_units'):
            row['status'] = 'not_text_indexed'
    return {'course_id': course, 'materials': rows,
            'status_counts': {key: sum(r['status'] == key for r in rows) for key in sorted({r['status'] for r in rows})},
            'extracted': result['extracted'], 'reused': result['reused'],
            'reviewed': False, 'mastery_updated': False, 'state_written': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    index = commands.add_parser('index')
    index.add_argument('--course', required=True)
    args = parser.parse_args()
    print(json.dumps(index_course(args.state, args.course), ensure_ascii=False))


if __name__ == '__main__':
    main()
