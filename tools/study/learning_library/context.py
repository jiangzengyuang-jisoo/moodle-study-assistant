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

from course_links import record_course_ids


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
    "material_id", "pages", "task_id", "work_unit_id", "chapter", "section", "topic", "stage", "next_step",
    "updated_at", "missing_material", "coverage_note",
)
_EVIDENCE_KEYS = (
    "id", "topic", "chapter", "material_id", "type", "entry_type", "stage", "status", "at", "date", "recorded_at", "answered_at", "occurred_at",
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


_POSITION_KEYS = (
    "id", "topic", "chapter", "section", "stage", "next_step", "next_check", "status",
    "material_id", "pages", "task_id", "work_unit_id", "missing_material", "coverage_note",
    "occurred_at", "event_at", "at", "date", "updated_at",
)
_ACTIVE_BRANCHES = ("latest_followup", "followup", "followups", "session_handoff", "handoff")
_PENDING_FIELDS = ("pending_question", "pending_check", "pending_questions", "pending_checks")


def _progress_time(node: dict, inherited=(None, None, None), *, event=False):
    # Occurrence wins over later bookkeeping. Legacy timestamps remain explicitly
    # labelled because old records do not consistently distinguish these times.
    keys = ("occurred_at",) if event else (
        "occurred_at", "event_at", "at", "answered_at", "date", "updated_at",
        "saved_at", "recorded_at", "created_at")
    for key in keys:
        raw = node.get(key)
        if not isinstance(raw, str):
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            stamp = dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
            return stamp, raw, key
        except ValueError:
            # An invalid explicit occurrence must not become a save-time event.
            if key in ("occurred_at", "event_at"):
                return None, None, key
    return inherited


def _progress_nodes(value: Any, path: str, inherited=(None, None, None)):
    """Only active continuity containers, never source, checks or history."""
    if isinstance(value, dict):
        if value.get("affects_current") is False:
            return
        clock = _progress_time(value, inherited)
        yield path, value, clock
        for key in _ACTIVE_BRANCHES:
            if key in value:
                yield from _progress_nodes(value[key], path + "/" + key, clock)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _progress_nodes(item, path + "/" + str(index), inherited)


def teaching_progress(course: dict, base_path: str = "/courses/0") -> dict:
    """Select the same saved teaching position for overview, context and memory.

    This is not a chapter maximum or an inference from prose/title/assignment
    scope. Missing or conflicting evidence remains visible for source review.
    """
    candidates = []
    for path, node, clock in _progress_nodes(course.get("teaching_cursor"), base_path + "/teaching_cursor"):
        if any(node.get(key) is not None for key in ("topic", "chapter", "section", "stage", "next_step", "next_check")):
            candidates.append((clock, path, node, node.get("source")))
    events = course.get("teaching_events") or []
    if isinstance(events, list):
        for index, event in enumerate(events):
            if not isinstance(event, dict) or event.get("affects_current") is not True:
                continue
            position = event.get("position")
            if isinstance(position, dict) and position:
                candidates.append((_progress_time(event, event=True),
                                   f"{base_path}/teaching_events/{index}/position", position, event.get("source")))
    result = {"status": "missing", "path": None, "record": None, "as_of": None,
              "conflict": False, "limitations": ["saved_records_only_not_complete_chat_history"]}
    if candidates:
        newest = max((row[0][0] for row in candidates if row[0][0] is not None), default=None)
        current = [row for row in candidates if row[0][0] == newest]
        # Timestamp and record identity are not part of teaching-position equality.
        semantic_keys = tuple(key for key in _POSITION_KEYS if key not in
                              {"id", "occurred_at", "event_at", "at", "date", "updated_at"}) + _PENDING_FIELDS
        signatures = {json.dumps(_pick(row[2], semantic_keys), sort_keys=True, ensure_ascii=False) for row in current}
        result["as_of"] = current[0][0][1]
        if len(signatures) > 1:
            result.update(status="conflict", conflict=True,
                          candidates=[{"path": row[1], "as_of": row[0][1]} for row in current])
            result["limitations"].append("same_time_positions_conflict_review_sources")
        else:
            clock, path, node, source = current[-1]
            record = _pick(node, _POSITION_KEYS)
            if _size(record) > 1400:
                record = {"omitted": True, "detail_path": path}
            result.update(status="recorded", path=path, record=record, as_of=clock[1], as_of_field=clock[2])
            if source is not None:
                result["source_path"] = path.removesuffix("/position") + "/source" if "/teaching_events/" in path else path + "/source"
                if isinstance(source, dict):
                    summary = _pick(source, ("kind", "thread_id", "turn_ids", "reviewed_at"))
                    result["source"] = summary if _size(summary) <= 800 else {"omitted": True, "detail_path": result["source_path"]}
            if clock[2] not in ("occurred_at", "event_at"):
                result["limitations"].append("legacy_time_may_describe_recording_not_occurrence")
        if any(row[0][0] is None for row in candidates):
            result["limitations"].append("undated_positions_cannot_be_ordered")
    else:
        newest = None
    notes = course.get("assistant_notes")
    if notes:
        review = []
        for index, note in enumerate(notes if isinstance(notes, list) else [notes]):
            stamp = _progress_time(note)[0] if isinstance(note, dict) else None
            if stamp is None or newest is None or stamp > newest:
                review.append(base_path + "/assistant_notes" + ("/" + str(index) if isinstance(notes, list) else ""))
        if review:
            result["notes_review"] = {"required": True, "paths": review[-8:], "total": len(review)}
            result["limitations"].append("newer_or_undated_notes_need_review_no_position_inferred")
    return result


def _progress_dimensions(course: dict, base_path: str) -> dict:
    focus = course.get("assignment_focus")
    scope = _pick(focus, ("target", "required_chapters", "task_ids", "meaning")) if isinstance(focus, dict) else _safe(focus)
    evidence = [base_path + "/" + field for field in
                ("topic_evidence", "teaching_events", "teaching_cursor", "learning_progress") if course.get(field) is not None]
    return {"assignment_scope": scope,
            "mastery": {"status": "not_assessed", "evidence_paths": evidence}}


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
    if collection == 'review_attempts':
        return [(i, row) for i, row in enumerate(values)
                if course_id in record_course_ids(state, row, collection)]
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


def _continuity(cursor: Any, base_path: str, projection: _Projection,
                progress: dict | None = None, course: dict | None = None) -> Any:
    course_path = base_path.rsplit("/", 1)[0]
    course = course if course is not None else {"teaching_cursor": cursor}
    progress = progress if progress is not None else teaching_progress(course, course_path)
    if cursor is None and progress["status"] == "missing" and not course.get("teaching_events"):
        return None
    if cursor is not None and not isinstance(cursor, dict):
        return _safe(cursor)
    # Preserve omission locators for legacy fields while displaying the selected
    # position, rather than an older root cursor alongside a newer continuation.
    projection.mapping(cursor, base_path, _CURRENT_KEYS)
    result = _pick(progress.get("record") or {}, _CURRENT_KEYS)
    selected_path = progress.get("path")
    if selected_path is None and progress["status"] == "missing":
        selected_path = base_path
    active_nodes = list(_progress_nodes(cursor, base_path))
    if selected_path and "/teaching_events/" in selected_path:
        index = int(selected_path.split("/teaching_events/", 1)[1].split("/", 1)[0])
        active_nodes.append((selected_path, course["teaching_events"][index]["position"], (None, None, None)))
    pending, prior_pending, resolved, handoffs, evidence = [], [], [], [], []
    for path, node, clock in active_nodes:
        leaf = path.rsplit("/", 1)[-1]
        if selected_path and (path == selected_path or path.startswith(selected_path + "/")) and leaf in ("session_handoff", "handoff"):
            handoffs.append((clock[0] or 0.0, path, node))
        for key in _PENDING_FIELDS:
            if node.get(key) is not None:
                locator = path + "/" + key
                if path == selected_path:
                    items = list(enumerate(node[key])) if isinstance(node[key], list) else [(None, node[key])]
                    for index, question in items:
                        question_path = locator + ("/" + str(index) if index is not None else "")
                        status = question.get("status") if isinstance(question, dict) else None
                        if status in {"answered", "skipped", "answer_shown", "completed", "solved", "closed", "cancelled", "canceled", "resolved"}:
                            resolved.append({"path": question_path, "status": status})
                        else:
                            pending.append({"path": question_path, "record": _pending(question)})
                else:
                    prior_pending.append({"path": locator, "status": "prior_position_needs_review"})
    # Historical answers remain evidence and are never current pending prompts.
    for path, node, stamp in _walk(cursor, base_path):
        if any(key in node for key in ("answer", "user_answer", "user_attempt", "answered_at", "conditions", "scaffolding", "assisted", "independent")):
            evidence.append((_progress_time(node)[0] or stamp, path, node))
    for index, event in enumerate(course.get("teaching_events") or []):
        if not isinstance(event, dict):
            continue
        stamp = _progress_time(event, event=True)[0] or 0.0
        for answer_index, attempt in enumerate(event.get("attempts") or []):
            if isinstance(attempt, dict) and attempt.get("user_answer") is not None:
                answer = attempt["user_answer"]
                if not isinstance(answer, str) or answer.strip():
                    path = f"{course_path}/teaching_events/{index}/attempts/{answer_index}"
                    evidence.append((stamp, path, {**attempt, "occurred_at": event.get("occurred_at")}))
    if handoffs:
        _, path, node = max(handoffs, key=lambda row: row[0])
        result["session_handoff"] = {"path": path, "record": _safe(node)}
    if pending:
        result["pending_questions"] = pending
        projection.omit(base_path, "answer_and_marking_fields_hidden")
    if prior_pending:
        result["prior_pending_questions"] = prior_pending
    if resolved:
        result["resolved_questions"] = resolved
    if progress["status"] == "recorded":
        result["latest_continuation"] = {"path": progress["path"], "record": deepcopy(progress["record"])}
    elif progress["conflict"]:
        result["progress_conflict"] = True
    if evidence:
        _, path, node = max(evidence, key=lambda row: row[0])
        result["latest_attempt_context"] = {"path": path, "record": _pick(node, _EVIDENCE_KEYS), "full_attempt_available": True,
                                            "scope_note": "Historical evidence only; verify its topic and conditions before applying it to the current lesson."}
    return result


def _overview(state: dict, detail: bool) -> dict:
    if detail:
        # No course selection means all explicit course details, never credentials
        # or unrelated integration state.
        return {"state_revision": state.get("revision"), "mode": "all_courses_detail", "sections": {"courses": [build_context(state, c["id"], True) for c in state.get("courses", [])]}, "omitted": [], "answer_keys_may_be_present": True}
    courses = []
    for index, course in enumerate(state.get("courses", [])):
        base = f"/courses/{index}"
        progress = teaching_progress(course, base)
        record = progress.get("record") or {}
        courses.append({"id": course.get("id"), "name": course.get("name"), "aliases": deepcopy(course.get("aliases")),
                        "current_topic": deepcopy(course.get("current_topic")),
                        "next_step": deepcopy(record.get("next_step", record.get("next_check"))),
                        "teaching_progress": progress, **_progress_dimensions(course, base),
                        "detail": {"collection": "courses", "record_id": course.get("id")}})
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
    from learning_library.versions import current
    related['materials'] = [(i, row) for i, row in related['materials'] if current(row)]
    counts = {collection: len(rows) if state.get(collection) is not None else None for collection, rows in related.items()}
    entry_types = Counter(row.get("entry_type") for _, row in related["mistakes"])
    summary = {"course_id": course_id, "name": course.get("name"), "record_counts": counts, "learning_entries_by_recorded_type": dict(entry_types) if state.get("mistakes") is not None else None, "evidence_policy": "Keep recorded question/error distinctions. Completion, explanation, exposure and assisted practice do not establish independent mastery."}
    result = {"state_revision": state.get("revision"), "state_updated_at": state.get("updated_at"), "timezone": state.get("timezone"), "mode": "course_detail" if detail else "course_context", "summary": summary, "sections": {}, "omitted": [], "details": {"course": {"collection": "courses", "record_id": course_id}, "path_usage": "detail path /courses/INDEX/FIELD", "answer_policy": "Full detail can contain solutions; use only when requested or needed for authorized marking."}}
    if detail:
        result["sections"] = {"course": deepcopy(course), **{key: [_filter_related(row, course_id) for _, row in rows] for key, rows in related.items()}}
        result["answer_keys_may_be_present"] = True
        return result
    progress = teaching_progress(course, base)
    project = _Projection()
    sections = result["sections"]
    # The current request decides what this turn is.  Course continuity, tasks,
    # deadlines and exam profiles are background, not a preparation agenda.
    sections["request_policy"] = {
        "decides": "当前用户请求决定本轮任务类型；以下课程接续、作业、截止日期和考试信息只是背景。",
        "do_not": [
            "保存了考试信息或长期目标包含考试表现，不代表本轮是备考。",
            "普通章节讲解不以考试范围开头。",
            "普通讲解不默认插入考试日期、考试范围、题型或占比，也不为此额外查询考试资料或完整刷新。",
            "“Not required for the exam”只表示考试要求，不能直接作为普通讲解跳过内容的理由。",
            "明确章节且本地已有资料时只读本章接续并做精准章节核对，不绕到完整采集、考试准备或学习计划流程。",
        ],
        "chapter_explanation": "围绕本章问题，结合学习证据分轮推进，默认讲练交替：每轮讲透一个核心概念（具体例子、逐步推理、LaTeX 公式与每个新符号的含义），再留适量练习或理解检查等待回答。重要内容经必要解释或示范后安排适量练习，不必每个小知识点都测，也不能连续讲完整章才首次检测；重要知识点完成讲解后默认请本人脱稿向零基础学生解释，可合并内容、与练习穿插。继续/下一步按教学阶段进入反馈、练习或推进，不自动跳过练习；跳过/直接给答案只影响当前题，不自动公布答案；只有明确要求整章先讲完（多轮继续讲、暂不配检测）/不出题时，才在指定范围内改变方式，范围结束恢复默认；明确要求一次讲完整章/完整讲义/总结或不要互动时才在本轮整章交付；意图不清时只澄清一次，不循环追问。",
        "teaching_output": "普通讲解不查询或汇报无关公告、在线练习和旧章未答题，不附固定核验尾注/错误码。必要本章核对内部留证，失败不记成功、不声称最新版；只在影响当前解释或需要本人处理时提示，主动询问时如实回答。保存仅限实际已讲单元，不以读完整章代替已讲。",
        "background_fields": ["course.current_topic", "course.orientation", "tasks", "deadlines", "exam_profiles"],
        "exam_info_usage": "只在当前请求涉及备考、考点或考试安排，或该项信息确实影响当前解释时使用；保留原始考试信息与学习记录。",
    }
    from learning_library.memory import correction_context
    sections['memory_review'] = correction_context(course, base)
    sections['memory_review']['query_usage'] = 'memory --course ' + course_id + ' --query TOPIC'
    sections["course"] = {"id": course_id, "name": course.get("name"), "current_topic": deepcopy(course.get("current_topic")), "assignment_focus": project.mapping(course.get("assignment_focus"), base + "/assignment_focus", ("target", "required_chapters", "task_ids", "meaning", "api_checked_at", "mapping_updated_at")), "orientation": project.mapping(course.get("orientation"), base + "/orientation", ("core_question", "prerequisites", "required_workflow", "resource_hierarchy", "study_approach", "scope_note", "exam_focus"), 1500), "assistant_notes": project.notes(course.get("assistant_notes"), base + "/assistant_notes"), "teaching_progress": progress, **_progress_dimensions(course, base), "teaching_cursor": _continuity(course.get("teaching_cursor"), base + "/teaching_cursor", project, progress, course), "learning_progress": project.mapping(course.get("learning_progress"), base + "/learning_progress", ("current_chapter", "last_completed_chapter", "chapter_progress_status", "teaching_continuity", "mastery_verified", "required_chapters"))}
    sections['course']['continuity_policy'] = {
        'current_position': progress.get('path'),
        'history_lookup': 'evidence --course ' + course_id + ' --query TOPIC',
        'missing_evidence': '未查到记录不等于未学过；判断缺口前查跨章节记录及其原文。',
        'coverage_and_mastery': '覆盖核对和学习表现分开；用户自报讲过不证明独立掌握。',
        'material_upload': '先按用户当前意图核对材料，不因上传作业自动重讲或出检查题。',
        'course_focus': 'orientation/current_topic 是长期课程安排与作业/考试背景，不是本轮任务；讲解章节时不得用它替代本章教学主线或作开头。',
    }
    for field, keys in (
        ('topic_evidence', ('id', 'topic', 'chapter', 'level', 'at', 'passed', 'conditions', 'corrects')),
        ('coverage_records', ('id', 'scope', 'status', 'at', 'corrects')),
        ('review_backlog', ('id', 'topic', 'chapter', 'status', 'next_step')),
    ):
        rows = course.get(field)
        if rows is not None:
            # Bounded routing metadata only. Full observations retain original
            # answers/sources in state and are reached through detail pointers.
            selected = rows[-5:] if isinstance(rows, list) else []
            offset = len(rows) - len(selected) if isinstance(rows, list) else 0
            sections['course'][field] = [project.mapping(row, base + '/' + field + '/' + str(offset + i), keys, 800)
                                         for i, row in enumerate(selected) if isinstance(row, dict)]
            project.omit(base + '/' + field, 'learning_history_metadata_only',
                         total=len(rows) if isinstance(rows, list) else None)
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
