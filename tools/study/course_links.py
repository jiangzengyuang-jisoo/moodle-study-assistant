"""Read-only course ownership for old review answers without rewriting evidence.

Review attempts historically store session/review IDs rather than course_id.
Resolve those joins conservatively: conflicting claims or duplicate targets do
not assign an answer to any course. Question text is only for local matching;
callers should expose the returned paths, not dump linked records.
"""
from __future__ import annotations


def _rows(state, key):
    value = state.get(key)
    return [(i, row) for i, row in enumerate(value) if isinstance(row, dict)] if isinstance(value, list) else []


def _claims(row):
    """None means no claim; an empty set is an invalid/conflicting claim."""
    if not isinstance(row, dict):
        return None
    claims = []
    if row.get('course_id') is not None:
        value = row['course_id']
        claims.append({value} if isinstance(value, str) and value else set())
    if row.get('course_ids') is not None:
        value = row['course_ids']
        claims.append(set(value) if isinstance(value, list) and value
                      and all(isinstance(item, str) and item for item in value) else set())
    return set.intersection(*claims) if claims else None


def review_attempt_context(state, record):
    """Return status, unique course_id, linked_paths and safe local search_text.

    Missing session snapshots can fall back to the queue, and explicit legacy
    course IDs remain usable. Existing but contradictory/ambiguous targets never
    fall back to a guessed course. Neither state nor answer records are changed.
    """
    def result(status, course_id=None, linked=()):
        return {'status': status, 'course_id': course_id,
                'linked_paths': tuple(path for path, _ in linked),
                'search_text': tuple(text for _, row in linked
                                     for key in ('topic', 'title', 'prompt')
                                     if isinstance((text := row.get(key)), str))}

    if not isinstance(record, dict):
        return result('unresolved')
    request = record.get('request')
    request = request if isinstance(request, dict) else {}
    claims = [claim for row in (record, request) if (claim := _claims(row)) is not None]
    identifiers = {}
    for key in ('session_id', 'review_id', 'variant_id'):
        values = [row[key] for row in (record, request) if row.get(key) is not None]
        if any(not isinstance(value, str) or not value for value in values):
            return result('conflict')
        if len(set(values)) > 1:
            return result('conflict')
        identifiers[key] = values[0] if values else None
    linked = []
    session_id, review_id, variant_id = (identifiers[key] for key in ('session_id', 'review_id', 'variant_id'))
    sessions = [(i, row) for i, row in _rows(state, 'review_sessions') if session_id and row.get('id') == session_id]
    if len(sessions) > 1:
        return result('ambiguous')
    if sessions:
        index, session = sessions[0]
        items = [(i, row) for i, row in _rows(session, 'items')
                 if (review_id is None or row.get('review_id') == review_id)
                 and (variant_id is None or row.get('variant_id') == variant_id)]
        if len(items) != 1:
            return result('ambiguous' if items else 'conflict')
        item_index, item = items[0]
        linked.append((f'/review_sessions/{index}/items/{item_index}', item))
        for row in (session, item):
            claim = _claims(row)
            if claim is not None:
                claims.append(claim)
        review_id = review_id or item.get('review_id')
        variant_id = variant_id or item.get('variant_id')
    queue = [(i, row) for i, row in _rows(state, 'review_queue') if review_id and row.get('id') == review_id]
    if len(queue) > 1:
        return result('ambiguous')
    if queue:
        index, item = queue[0]
        claim = _claims(item)
        if claim is not None:
            claims.append(claim)
        # Queue-level prompt is a legacy review field and can contain an answer;
        # only a specifically identified practice variant supplies question text.
        linked.append((f'/review_queue/{index}', {key: item[key] for key in ('topic', 'title') if key in item}))
        variants = [(i, row) for i, row in _rows(item, 'practice_variants')
                    if variant_id and row.get('id') == variant_id]
        if len(variants) > 1:
            return result('ambiguous')
        if variants:
            variant_index, variant = variants[0]
            claim = _claims(variant)
            if claim is not None:
                claims.append(claim)
            linked.append((f'/review_queue/{index}/practice_variants/{variant_index}', variant))
    if not claims:
        return result('unresolved')
    owners = set.intersection(*claims)
    if not owners:
        return result('conflict')
    if len(owners) != 1:
        return result('ambiguous')
    return result('resolved', next(iter(owners)), linked)


def record_course_ids(state, record, collection=None):
    """Return explicit owners, or a uniquely resolved legacy review owner.

    Collection must be supplied for review attempts; other collections retain
    their explicit shared-course membership without inferring nested ownership.
    """
    if collection == 'review_attempts':
        resolved = review_attempt_context(state, record)
        return frozenset([resolved['course_id']]) if resolved['status'] == 'resolved' else frozenset()
    return frozenset(_claims(record) or ())
