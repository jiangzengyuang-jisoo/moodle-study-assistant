"""Small, read-only learning context projections of the authoritative state.

This module never rates a learner or infers mastery from a submission.  Defaults
are routing/continuity views, not complete evidence.  Explicit detail reads are
lossless and may expose answer keys; callers must reserve them for requested
solutions, marking, or internal evidence checks.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from typing import Any


_COLLECTIONS = (
    "tasks", "deadlines", "materials", "mistakes", "review_queue",
    "review_attempts", "learning_feedback", "exam_profiles", "discussion_notes",
    "mock_exams", "mock_attempts", "course_updates", "pending_inputs",
    "undated_assessments", "moodle_resources", "cross_course_learning_links",
    "review_sessions", "schedules", "daily_plans", "upcoming",
)
_ANSWER_KEYS = {
    "answer", "answers", "answer_key", "answer_keys", "answer_check", "solution",
    "solutions", "correct_answer", "correct_answer_or_method", "expected_answer",
    "expected_answers", "expected", "rubric", "marking_scheme", "scoring_key",
    "assessment", "feedback", "explanation", "worked_solution", "user_answer",
    "user_answer_or_method", "user_attempt", "checks", "recent_correct",
    "correct", "is_correct", "expected_result", "rationale", "reasoning",
    "marking_points", "scoring_points", "grading_key", "hint", "hints",
}
_TIME_KEYS = ("updated_at", "answered_at", "saved_at", "recorded_at", "at", "date", "created_at")
_CURRENT_KEYS = (
    "material_id", "pages", "task_id", "work_unit_id", "topic", "stage", "next_step",
    "updated_at", "missing_material", "coverage_note",
)
_EVIDENCE_KEYS = (
    "id", "type", "entry_type", "stage", "status", "at", "date", "recorded_at", "answered_at",
    "conditions", "scaffolding", "assisted", "independent", "answer_exposed",
    "answer_previously_exposed", "hints_used", "mastery_verified", "mastery_inferred",
    "mastery_scope", "mastery_limits", "fsrs_rating", "fsrs_rated", "actual_minutes",
)
_PENDING_KEYS = (
    "id", "question_id", "variant_id", "review_id", "course_id", "title",
    "prompt", "question", "questions", "question_text", "original_question",
    "text", "statement", "body", "parts", "subquestions", "items", "options",
    "choices", "data", "tables", "images", "attachments", "instructions",
    "source", "material_id", "pages", "slides", "task_id", "status",
    "scaffolding", "conditions", "assisted", "independent", "answer_exposed",
    "answer_previously_exposed", "pending_question_answer_exposed", "hints_used",
    "created_at", "updated_at", "answered_at", "at", "date", "verified",
)


def _pending(value: Any) -> Any:
    if isinstance(value, dict):
        return _pick(value, _PENDING_KEYS)
    if isinstance(value, list):
        return [_pending(item) for item in value]
    return deepcopy(value)


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _stamp(value: Any, fallback: float = 0.0) -> float:
    if not isinstance(value, dict):
        return fallback
    stamps = []
    for key in _TIME_KEYS:
        raw = value.get(key)
        if not isinstance(raw, str):
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            stamps.append(dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp())
        except ValueError:
            pass
    return max(stamps, default=fallback)


def _escape(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _walk(value: Any, path: str = "", inherited: float = 0.0):
    if isinstance(value, dict):
        stamp = _stamp(value, inherited)
        yield path, value, stamp
        for key, item in value.items():
            yield from _walk(item, path + "/" + _escape(key), stamp)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, path + "/" + str(i), inherited)


def _safe(value: Any) -> Any:
    """Remove keyed answer/marking payloads without changing original objects."""
    if isinstance(value, dict):
        return {
            key: _safe(item) for key, item in value.items()
            if key.lower() not in _ANSWER_KEYS
            and not re.search(r"(?:^|_)(?:solution|rubric|answer_key|marking|scoring)(?:_|$)", key.lower())
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return deepcopy(value)


def _pick(value: dict, keys: tuple | list) -> dict:
    return {key: _safe(value[key]) for key in keys if key in value}


def _id(record: Any, index: int) -> str:
    if isinstance(record, dict):
        return str(record.get("id") or record.get("feedback_id") or record.get("attempt_id") or record.get("session_id") or f"@{index}")
    return f"@{index}"


def _course(state: dict, course_id: str) -> tuple[int, dict]:
    requested = course_id.casefold().replace(" ", "")
    matches = []
    for index, course in enumerate(state.get("courses", [])):
        stored_aliases = course.get("aliases") or []
        aliases = [course.get("id"), course.get("name"), *([stored_aliases] if isinstance(stored_aliases, str) else stored_aliases)]
        if any(isinstance(alias, str) and alias.casefold().replace(" ", "") == requested for alias in aliases):
            matches.append((index, course))
    if len(matches) != 1:
        raise ValueError(f"Course is unknown or ambiguous: {course_id}")
    return matches[0]


def _related(record: Any, course_id: str) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("course_id") is not None:
        return record["course_id"] == course_id
    if course_id in (record.get("course_ids") or []):
        return True
    return any(_related(item, course_id) for value in record.values() for item in (value if isinstance(value, list) else [value]) if isinstance(item, dict))


def _filter_related(value: Any, course_id: str) -> Any:
    """Keep shared container metadata, but exclude explicitly other-course rows."""
    if isinstance(value, dict):
        if value.get("course_id") not in (None, course_id):
            return None
        return {key: _filter_related(item, course_id) for key, item in value.items()}
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict) and item.get("course_id") not in (None, course_id):
                continue
            result.append(_filter_related(item, course_id))
        return result
    return deepcopy(value)


def _rows(state: dict, collection: str, course_id: str) -> list[tuple[int, dict]]:
    values = state.get(collection) or []
    if not isinstance(values, list):
        return []
    return [(i, row) for i, row in enumerate(values) if _related(row, course_id)]


class _Projection:
    def __init__(self):
        self.omitted: list[dict] = []

    def omit(self, path: str, reason: str, **extra):
        self.omitted.append({"path": path, "reason": reason, **extra})

    def mapping(self, value: Any, path: str, keys: tuple | list, budget: int | None = None):
        if value is None:
            return None
        if not isinstance(value, dict):
            if budget is not None and _size(value) > budget:
                self.omit(path, "long_field", characters=_size(value))
                return {"omitted": True, "path": path}
            return _safe(value)
        result = _pick(value, keys)
        if set(value) - set(result):
            self.omit(path, "additional_fields", fields=[key for key in value if key not in result])
        if budget is not None:
            for key in list(result):
                if _size(result) <= budget:
                    break
                # Whole lower-priority fields are omitted, never chopped text.
                if key not in ("id", "core_question"):
                    item = result.pop(key)
                    self.omit(path + "/" + _escape(key), "long_field", characters=_size(item))
        return result

    def notes(self, value: Any, path: str, budget: int = 1300):
        if value is None:
            return None
        if not isinstance(value, list):
            return self.mapping(value, path, ("text", "recorded_at", "source"), budget)
        # Recent complete notes plus pointers to all older notes; no partial text.
        ordered = sorted(enumerate(value), key=lambda row: (_stamp(row[1]), row[0]), reverse=True)
        result = []
        skipped = []
        used = 0
        for index, note in ordered:
            safe_note = _safe(note)
            if used + _size(safe_note) <= budget:
                result.append({"path": f"{path}/{index}", "record": safe_note})
                used += _size(safe_note)
            else:
                skipped.append(index)
        if skipped:
            self.omit(path, "notes_not_loaded", indices=sorted(skipped), total=len(value))
        return result


def _continuity(cursor: Any, base_path: str, projection: _Projection) -> Any:
    if cursor is None:
        return None
    if not isinstance(cursor, dict):
        return _safe(cursor)
    result = projection.mapping(cursor, base_path, _CURRENT_KEYS)
    pending = []
    handoffs = []
    continuations = []
    evidence = []
    for path, node, stamp in _walk(cursor, base_path):
        leaf = path.rsplit("/", 1)[-1]
        if leaf in ("session_handoff", "handoff"):
            handoffs.append((stamp, path, node))
        for key in ("pending_question", "pending_check", "pending_questions", "pending_checks"):
            if key in node and node[key] is not None:
                pending.append({"path": path + "/" + key, "record": _pending(node[key])})
        if "next_step" in node or "next_check" in node:
            continuations.append((stamp, path, node))
        if any(key in node for key in ("answer", "user_answer", "user_attempt", "answered_at", "conditions", "scaffolding", "assisted", "independent")):
            evidence.append((stamp, path, node))
    if handoffs:
        _, path, node = max(handoffs, key=lambda row: row[0])
        result["session_handoff"] = {"path": path, "record": _safe(node)}
    if pending:
        result["pending_questions"] = pending
        projection.omit(base_path, "answer_and_marking_fields_hidden")
    if continuations:
        _, path, node = max(continuations, key=lambda row: row[0])
        result["latest_continuation"] = {"path": path, "record": _pick(node, ("id", "next_step", "next_check", "at", "date", "updated_at", "stage", "status"))}
    if evidence:
        _, path, node = max(evidence, key=lambda row: row[0])
        # Preserve explicit assistance/independence and assessment limits.  Never
        # manufacture an independent flag from absence of a hint record.
        result["latest_attempt_context"] = {"path": path, "record": _pick(node, _EVIDENCE_KEYS), "full_attempt_available": True}
    return result


def _overview(state: dict, detail: bool) -> dict:
    if detail:
        # No course selection means all explicit course details, never credentials
        # or unrelated integration state.
        return {"state_revision": state.get("revision"), "mode": "all_courses_detail", "sections": {"courses": [build_context(state, c["id"], True) for c in state.get("courses", [])]}, "omitted": [], "answer_keys_may_be_present": True}
    courses = []
    for course in state.get("courses", []):
        cursor = course.get("teaching_cursor")
        courses.append({"id": course.get("id"), "name": course.get("name"), "aliases": deepcopy(course.get("aliases")), "current_topic": deepcopy(course.get("current_topic")), "next_step": deepcopy(cursor.get("next_step")) if isinstance(cursor, dict) else None, "detail": {"collection": "courses", "record_id": course.get("id")}})
    return {"state_revision": state.get("revision"), "state_updated_at": state.get("updated_at"), "mode": "course_overview", "summary": {"course_count": len(courses)}, "sections": {"courses": courses}, "omitted": [{"path": "/courses", "reason": "select_course_for_learning_context"}], "details": {"usage": "context --course COURSE_ID; detail COLLECTION RECORD_ID"}}


def build_context(state: dict, course_id: str | None = None, detail: bool = False) -> dict:
    """Build a fresh course view without mutating or persisting any state.

    `detail=True` explicitly includes full relevant records and answer keys.
    It is intended for authorized marking/source review, not blind tutoring
    startup.  Default omissions always point back into this state revision.
    """
    if course_id is None:
        return _overview(state, detail)
    index, course = _course(state, course_id)
    course_id = course["id"]
    base = f"/courses/{index}"
    related = {collection: _rows(state, collection, course_id) for collection in _COLLECTIONS}
    counts = {collection: len(rows) if state.get(collection) is not None else None for collection, rows in related.items()}
    entry_types = Counter(row.get("entry_type") for _, row in related["mistakes"])
    summary = {"course_id": course_id, "name": course.get("name"), "record_counts": counts, "learning_entries_by_recorded_type": dict(entry_types) if state.get("mistakes") is not None else None, "evidence_policy": "Keep recorded question/error distinctions. Completion, explanation, exposure and assisted practice do not establish independent mastery."}
    result = {"state_revision": state.get("revision"), "state_updated_at": state.get("updated_at"), "timezone": state.get("timezone"), "mode": "course_detail" if detail else "course_context", "summary": summary, "sections": {}, "omitted": [], "details": {"course": {"collection": "courses", "record_id": course_id}, "path_usage": "detail path /courses/INDEX/FIELD", "answer_policy": "Full detail can contain solutions; use only when requested or needed for authorized marking."}}
    if detail:
        result["sections"] = {"course": deepcopy(course), **{key: [_filter_related(row, course_id) for _, row in rows] for key, rows in related.items()}}
        result["answer_keys_may_be_present"] = True
        return result
    project = _Projection()
    sections = result["sections"]
    sections["course"] = {"id": course_id, "name": course.get("name"), "current_topic": deepcopy(course.get("current_topic")), "assignment_focus": project.mapping(course.get("assignment_focus"), base + "/assignment_focus", ("target", "required_chapters", "task_ids", "meaning", "api_checked_at", "mapping_updated_at")), "orientation": project.mapping(course.get("orientation"), base + "/orientation", ("core_question", "prerequisites", "required_workflow", "resource_hierarchy", "study_approach", "scope_note", "exam_focus"), 1500), "assistant_notes": project.notes(course.get("assistant_notes"), base + "/assistant_notes"), "teaching_cursor": _continuity(course.get("teaching_cursor"), base + "/teaching_cursor", project), "learning_progress": project.mapping(course.get("learning_progress"), base + "/learning_progress", ("current_chapter", "last_completed_chapter", "chapter_progress_status", "teaching_continuity", "mastery_verified", "required_chapters"))}
    # Current submitted/completed tasks remain explicitly visible; never reopen
    # group work by inferring from one person's platform record.
    focus_ids = set((course.get("assignment_focus") or {}).get("task_ids") or []) if isinstance(course.get("assignment_focus"), dict) else set()
    selected_tasks = []
    omitted_tasks = []
    closed = {"completed", "submitted", "done", "cancelled", "canceled", "archived"}
    for i, task in related["tasks"]:
        if task.get("id") not in focus_ids and str(task.get("status", "")).lower() in closed:
            omitted_tasks.append(task.get("id", f"@{i}"))
            continue
        selected_tasks.append(_pick(task, ("id", "title", "deadline_id", "status", "submission_status", "target_finish_date")))
    sections["tasks"] = selected_tasks
    linked_deadlines = {task.get("deadline_id") for task in selected_tasks}
    sections["deadlines"] = [_pick(row, ("id", "task_id", "title", "date", "time", "timezone", "date_precision", "status")) for _, row in related["deadlines"] if row.get("id") in linked_deadlines or (row.get("task_id") is None and str(row.get("status", "")).lower() not in closed)]
    if related["tasks"]:
        project.omit("/tasks", "record_summaries_and_inactive_history", record_ids=omitted_tasks, course_id=course_id)
    if related["deadlines"]:
        project.omit("/deadlines", "current_linked_deadlines_only", course_id=course_id)
    recent = sorted(related["mistakes"], key=lambda row: (_stamp(row[1]), row[0]), reverse=True)
    selected = []
    total = 0
    for index, entry in recent:
        row = _pick(entry, ("id", "entry_type", "display_title", "knowledge_point", "date", "review_status"))
        # Include the original question when compact; otherwise preserve a pointer
        # instead of a misleading truncated question or generated paraphrase.
        question = entry.get("original_question")
        if question is not None and _size(question) <= 320:
            row["original_question"] = deepcopy(question)
        elif question is not None:
            row["original_question"] = {"omitted": True, "path": f"/mistakes/{index}/original_question"}
        if len(selected) >= 5 or total + _size(row) > 1900:
            break
        selected.append(row)
        total += _size(row)
    sections["recent_learning_entries"] = selected
    if recent:
        project.omit("/mistakes", "recent_summaries_only_answers_hidden", total=len(recent), shown_ids=[row["id"] for row in selected], course_id=course_id)
    sections["exam_profiles"] = [_pick(row, ("id", "title", "exam_date", "scope_status", "topics", "excluded_topics", "format_status", "verification_status", "verified_at")) for _, row in related["exam_profiles"]]
    for collection in ("materials", "discussion_notes", "review_queue", "review_attempts", "learning_feedback", "mock_exams", "mock_attempts"):
        if related[collection]:
            project.omit("/" + collection, "load_when_relevant", total=len(related[collection]), course_id=course_id)
    result["omitted"] = project.omitted
    return result


def read_detail(state: dict, collection: str, record_id: str) -> dict:
    """Read one exact record or JSON pointer, with revision and exposure warning.

    Supported collection names are authoritative learning collections plus
    ``courses``.  ``path`` accepts RFC 6901 pointers under those same roots.
    IDs match exactly; ``@INDEX`` addresses an ID-less list entry.
    """
    allowed = {"courses", *_COLLECTIONS}
    if collection == "path":
        if not record_id.startswith("/"):
            raise ValueError("An absolute JSON pointer is required")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in record_id[1:].split("/")]
        if not parts or parts[0] not in allowed:
            raise ValueError("Only learning records may be read through this entry point")
        current: Any = state
        try:
            for part in parts:
                if isinstance(current, list):
                    if not part.isdigit():
                        raise KeyError(part)
                    current = current[int(part)]
                else:
                    current = current[part]
        except (KeyError, IndexError, TypeError) as error:
            raise KeyError(f"Unknown state path: {record_id}") from error
        path = record_id
    else:
        if collection not in allowed:
            raise ValueError(f"Unsupported learning collection: {collection}")
        values = state.get(collection) or []
        if not isinstance(values, list):
            raise ValueError(f"Collection is not a list: {collection}")
        matches = [(i, row) for i, row in enumerate(values) if _id(row, i) == record_id]
        if len(matches) != 1:
            raise KeyError(f"Record not found or ambiguous: {collection}/{record_id}")
        index, current = matches[0]
        path = f"/{collection}/{index}"
    return {"state_revision": state.get("revision"), "path": path, "record": deepcopy(current), "complete": True, "answer_keys_may_be_present": True}
