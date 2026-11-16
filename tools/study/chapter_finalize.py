"""Validate selected chapter bodies, then retire only originals replaced by this operation."""
import argparse
import copy
import json
from pathlib import Path
import sys

from library import Library
from chapter_readiness import reading_route
from learning_library import versions
from course_library import load, save_state
from state_io import workspace_lock, prepare_state, atomic


def finalize(state_path, payload):
    path = Path(state_path).resolve()
    ids = payload.get('material_ids')
    previous = payload.get('previous_ids', [])
    if not isinstance(ids, list) or not ids or not all(isinstance(mid, str) for mid in ids + previous):
        raise ValueError('Explicit current chapter materials are required')
    with workspace_lock(path):
        prepare_state(path)
        state = load(path)
        materials = {m['id']: m for m in state.get('materials', [])}
        library = Library(path)
        reading_routes = {}
        for mid in set(ids):
            material = materials.get(mid)
            if not material or not versions.current(material):
                raise ValueError('Current chapter material is unavailable')
            versions.verify(path.parent.parent, material)
            def readable():
                reading_routes[mid] = reading_route(library, mid)
            try:
                readable()
            except (ValueError, OSError, KeyError, TypeError, IndexError):
                library.sync(material['course_id'], force=True, material_ids=[mid])
                readable()
        confirm_sources = payload.get('confirm_sources', [])
        if confirm_sources:
            ledger_path = path.parent / 'LibrarySync/ledger.json'
            ledger = load(ledger_path)
            for key in confirm_sources:
                entry = ledger.get('entries', {}).get(key)
                mid = (entry or {}).get('duplicate_of') or (entry or {}).get('material_id')
                if (not entry or mid not in ids or entry.get('sha256') != materials[mid].get('sha256')
                        or not isinstance(entry.get('remote'), dict)):
                    raise ValueError('Timestamp baseline no longer identifies the validated body')
                entry.update(timestamp_ready=True, timestamp_verified_at=versions.instant().isoformat())
            atomic(ledger_path, ledger)
        # No previous file is removed until every selected current body is readable.
        draft = copy.deepcopy(state)
        deleted = []
        candidates = []
        # An unreadable replacement can itself be replaced on a later retry.
        # Follow only explicit, still-unfinished timestamp-update edges. A
        # completed earlier update or an ordinary historical archive ends it.
        replacements = {}
        completed_updates = set()
        if not confirm_sources:
            for mid in ids:
                successor = materials[mid]
                visited = set()
                while successor['id'] not in visited:
                    visited.add(successor['id'])
                    marker = successor.get('chapter_timestamp_update', {})
                    if marker.get('pending_retirement') is not True:
                        break
                    completed_updates.add(successor['id'])
                    old = materials.get(successor.get('supersedes'))
                    if not old:
                        break
                    if (old.get('superseded_by') != successor['id']
                            or old.get('course_id') != successor.get('course_id')
                            or not versions._same_authorized_source(state, old, successor)):
                        raise ValueError('Timestamp replacement chain is not verified')
                    replacements[old['id']] = mid
                    successor = old
        for old in draft.get('materials', []):
            final_id = replacements.get(old['id'], old.get('superseded_by'))
            recovering = (old.get('version_status') == 'deletion_pending'
                and old.get('deletion_reason') == 'readable_timestamp_replacement'
                and old.get('deleted_for_material_id') == final_id)
            newly_replaced = (old['id'] in previous or old['id'] in replacements) and old.get('version_status') == 'archived'
            if final_id not in ids or not (recovering or newly_replaced):
                continue
            current = materials[final_id]
            if (old.get('course_id') != current.get('course_id')
                    or not versions._same_authorized_source(state, old, current)):
                raise ValueError('Replacement source relationship is not verified')
            target = versions.safe(path.parent.parent, old['relative_path'])
            if '_Archive' not in target.relative_to(path.parent.parent).parts:
                raise ValueError('Previous original is not a managed archive')
            if any(m['id'] != old['id'] and m.get('relative_path') == old['relative_path'] for m in state.get('materials', [])):
                raise ValueError('Previous original is shared by another material')
            if (old.get('archive_sha256') != old.get('sha256')
                    or (target.exists() and (not target.is_file() or versions.sha(target) != old.get('sha256')))
                    or (not target.exists() and not recovering)):
                raise ValueError('Previous original changed; preserve it for review')
            old.update(version_status='deletion_pending', deletion_pending_at=old.get('deletion_pending_at') or versions.instant().isoformat(),
                       deletion_reason='readable_timestamp_replacement', deleted_for_material_id=current['id'])
            candidates.append((target, old['sha256'], old['id']))
        if candidates:
            # Persist explicit, scoped intent. An interrupted unlink can be resumed
            # only for this successor, never for arbitrary historical archives.
            save_state(path, draft, state)
            for target, expected, mid in candidates:
                if target.exists():
                    if versions.sha(target) != expected:
                        raise ValueError('Previous original changed during retirement')
                    target.unlink()
                before = load(path)
                after = copy.deepcopy(before)
                row = next(m for m in after['materials'] if m['id'] == mid)
                row.update(version_status='deleted', deleted_at=versions.instant().isoformat())
                save_state(path, after, before)
                deleted.append(mid)
        if completed_updates:
            before = load(path)
            after = copy.deepcopy(before)
            for row in after['materials']:
                if row['id'] in completed_updates:
                    row['chapter_timestamp_update']['pending_retirement'] = False
            save_state(path, after, before)
        return {'ready': True, 'sync_complete': True, 'reading_required': True,
                'visual_review_required': any(r['visual_review_required'] for r in reading_routes.values()),
                'reading_routes': reading_routes, 'material_ids': ids, 'deleted_original_ids': deleted,
                'mastery_updated': False, 'state_written': bool(deleted or completed_updates)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(finalize(args.state, json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
