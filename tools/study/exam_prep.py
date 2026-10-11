"""Source-bound mock papers and real attempts. No network, files, mastery or FSRS writes.

The author supplies checked questions; this module does not pretend templates are
course-grounded questions. Mutators validate a copy before changing shared state.
Answer keys require an explicit exposure record before they can be rendered.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math

import review
import study_time

TYPES = {"multiple_choice", "long_answer", "short_answer", "fill_blank", "true_false"}
LABELS = {"multiple_choice": "选择题", "long_answer": "长答题", "short_answer": "简答题",
          "fill_blank": "填空题", "true_false": "判断题"}


def _at(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return value.astimezone(study_time.local_zone())
    return review.timestamp(value).astimezone(study_time.local_zone())


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " 必须为非空文字")
    return value


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(name + " 必须为有限的" + ("正数" if positive else "非负数"))
    return value


def _evidence(value):
    if not isinstance(value, (dict, str)) or not value or (isinstance(value, str) and not value.strip()):
        raise ValueError("必须提供真实作答或操作的 evidence")
    return deepcopy(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def profile_signature(profile):
    """Ignore refresh timestamps, retain all substantive teacher specifications."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()
                    if k not in {"checked_at", "updated_at", "api_checked_at", "retrieved_at", "verified_at"}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    # Audit timestamps, previous snapshots and candidate-search hashes change
    # during routine sync and are not changes to the teacher's requirements.
    fields = ("id", "course_id", "title", "exam_date", "scope_status", "topics", "format_status",
              "duration_minutes", "sections", "total_marks", "sources", "notes", "excluded_topics", "instructions")
    return _digest(clean({key: profile[key] for key in fields if key in profile}))


@study_time.state_timezone
def _find(state, collection, identity, key="id"):
    matches = [v for v in state.get(collection, []) if v.get(key) == identity]
    if len(matches) != 1:
        raise ValueError(collection + " 中找不到唯一项目：" + str(identity))
    return matches[0]


def _unique_ids(values, name):
    if not isinstance(values, list) or not values:
        raise ValueError(name + " 不能为空")
    ids = [_text(v.get("id"), name + ".id") for v in values]
    if len(ids) != len(set(ids)):
        raise ValueError(name + " 的 id 重复")
    return {v["id"]: v for v in values}


@study_time.state_timezone
def _profile(state, payload):
    profile = _find(state, "exam_profiles", _text(payload.get("exam_id"), "exam_id"))
    if profile.get("verification_status") == "needs_review":
        raise ValueError("Moodle 考试说明有变化，须重新核读老师说明后出卷")
    mode = payload.get("mode", "full_mock")
    if mode not in {"full_mock", "full_scope_mock", "targeted_practice"}:
        raise ValueError("mode 必须为 full_mock、full_scope_mock 或 targeted_practice")
    if profile.get("format_status") not in {"confirmed", "partial"}:
        raise ValueError("老师题型待确认，不能编造考试规格")
    if mode == "full_mock" and (profile.get("format_status") != "confirmed" or profile.get("scope_status") != "confirmed"):
        raise ValueError("完整模拟卷要求老师已确认范围与题型")
    if mode == "full_scope_mock" and (profile.get("scope_status") != "confirmed"
            or profile.get("duration_minutes") is None or profile.get("total_marks") is None):
        raise ValueError("全范围模拟卷要求老师已确认范围、考试时长与总分")
    if mode == "full_scope_mock" and "duration_minutes" in payload:
        _number(payload["duration_minutes"], "mock.duration_minutes", positive=True)
        if payload["duration_minutes"] != profile["duration_minutes"]:
            raise ValueError("全范围模拟卷时长必须与老师公布的考试时长一致")
    if not profile.get("sources") or not all(s.get("authority") in {"teacher", "official_course"}
            and all(isinstance(s.get(k), str) and s[k].strip() for k in ("url", "location", "excerpt", "checked_at"))
            for s in profile["sources"]):
        raise ValueError("考试规格必须保留老师或官方课程的可追溯依据")
    for source in profile["sources"]:
        _at(source["checked_at"])
    if profile.get("duration_minutes") is not None:
        _number(profile["duration_minutes"], "duration_minutes", positive=True)
    topics = _unique_ids(profile.get("topics"), "topics")
    sections = _unique_ids(profile.get("sections"), "sections")
    for section in sections.values():
        if section.get("question_type") not in TYPES:
            raise ValueError("老师题型尚不支持或未确认")
        count = section.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count <= 0):
            raise ValueError("官方题数必须是正整数或 null")
        if mode == "full_mock" and count is None:
            raise ValueError("官方题数待确认，只能制作明确标注的专项练习")
        if section.get("total_marks") is not None:
            _number(section["total_marks"], "section.total_marks", positive=True)
        elif mode == "full_scope_mock":
            raise ValueError("全范围模拟卷要求各已公布题型的分节总分已确认；不能补造官方分值")
        if section.get("marks_per_question") is not None:
            _number(section["marks_per_question"], "section.marks_per_question", positive=True)
        if section.get("option_count") is not None and (section["question_type"] != "multiple_choice"
                or isinstance(section["option_count"], bool) or not isinstance(section["option_count"], int)
                or section["option_count"] < 2):
            raise ValueError("选择题选项数必须为至少 2 的整数")
        if all(section.get(k) is not None for k in ("count", "marks_per_question", "total_marks")) and not math.isclose(
                section["count"] * section["marks_per_question"], section["total_marks"], abs_tol=1e-9):
            raise ValueError("老师规格的题数、单题分值与分节总分相互冲突，须核对来源")
    return profile, mode, topics, sections


def _validate_assistant_structure(payload, sections, section_questions):
    """Declare only unpublished layout details without upgrading the profile."""
    required = {key for key, section in sections.items()
                if section.get("count") is None or section.get("marks_per_question") is None}
    supplied = payload.get("assistant_structure", [])
    if not isinstance(supplied, list) or not all(isinstance(row, dict) for row in supplied):
        raise ValueError("assistant_structure 必须为分节安排列表")
    ids = [row.get("section_id") for row in supplied]
    if any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)) or set(ids) != required:
        raise ValueError("须逐节明确登记老师未公布的题数或逐题分值，不得更改已确认结构")
    for row in supplied:
        actual = section_questions[row["section_id"]]
        count = row.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count != len(actual):
            raise ValueError("助手安排的题数必须与本节实际题数一致")
        marks = row.get("marks")
        if not isinstance(marks, list) or len(marks) != count:
            raise ValueError("助手安排须明确本节每道题的分值")
        for value, question in zip(marks, actual):
            _number(value, "assistant_structure.marks", positive=True)
            if value != question["marks"]:
                raise ValueError("助手安排的逐题分值必须与实际试卷一致")
        _text(row.get("reason"), "assistant_structure.reason")


@study_time.state_timezone
def _validate_mock(state, payload):
    _text(payload.get("id"), "id")
    _text(payload.get("title"), "title")
    profile, mode, topics, sections = _profile(state, payload)
    questions = _unique_ids(payload.get("questions"), "questions")
    assumptions = payload.get("assumptions", [])
    if not isinstance(assumptions, list) or not all(isinstance(v, str) and v.strip() for v in assumptions):
        raise ValueError("assumptions 必须为文字列表")
    materials = {v["id"]: v for v in state.get("materials", [])}
    if mode == "full_scope_mock":
        for topic in topics.values():
            ids = topic.get("material_ids")
            if (not isinstance(ids, list) or not ids or not all(isinstance(mid, str) and mid in materials
                    and materials[mid].get("course_id") == profile["course_id"] for mid in ids)):
                raise ValueError("全范围模拟卷的所有登记考点必须有齐备的本科课件：" + topic["id"])
    covered, used_materials = set(), set()
    if mode == "full_scope_mock":
        # The whole-scope claim depends on every listed source being available,
        # even when one question cites a different source for that same topic.
        used_materials.update(mid for topic in topics.values() for mid in topic["material_ids"])
    section_questions = {key: [] for key in sections}
    for q in questions.values():
        section = sections.get(q.get("section_id"))
        if section is None or q.get("question_type") != section["question_type"]:
            raise ValueError("题目必须符合老师确认的分节题型")
        _text(q.get("stem"), "stem")
        _number(q.get("marks"), "marks", positive=True)
        if section.get("marks_per_question") is not None and q["marks"] != section["marks_per_question"]:
            raise ValueError("单题分值与老师确认规格不一致")
        q_topics = q.get("topic_ids")
        if not isinstance(q_topics, list) or not q_topics or len(set(q_topics)) != len(q_topics) or not all(t in topics for t in q_topics):
            raise ValueError("题目知识点必须属于当前已确认的考试范围")
        allowed = section.get("allowed_topic_ids")
        if allowed is not None and (not isinstance(allowed, list) or not set(q_topics) <= set(allowed)):
            raise ValueError("题目考点不属于老师对该题型指定的范围")
        excluded = {v if isinstance(v, str) else v.get("id") for v in profile.get("excluded_topics", [])}
        if set(q_topics) & excluded:
            raise ValueError("题目涉及明确排除的考试知识点")
        if q.get("verified") is not True or not isinstance(q.get("verification"), dict):
            raise ValueError("题目及答案必须先核验")
        for key in ("method", "detail"):
            _text(q["verification"].get(key), "verification." + key)
        if not q.get("sources") or not isinstance(q["sources"], list):
            raise ValueError("每道题必须关联课件原页")
        source_ids = set()
        for src in q["sources"]:
            material = materials.get(src.get("material_id"))
            if material is None or material.get("course_id") != profile["course_id"]:
                raise ValueError("题目资料必须是已索引的本科资料")
            pages = src.get("pages")
            if not isinstance(pages, list) or not pages or any(isinstance(p, bool) or not isinstance(p, int) or p < 1 for p in pages):
                raise ValueError("来源 pages 必须为实际物理页码")
            if isinstance(material.get("pages"), int) and max(pages) > material["pages"]:
                raise ValueError("来源页码超出课件")
            _text(src.get("detail"), "source.detail")
            source_ids.add(material["id"])
            used_materials.add(material["id"])
        for topic in q_topics:
            if not source_ids.intersection(topics[topic].get("material_ids", [])):
                raise ValueError("题目来源未覆盖关联考点的课件；缺失资料不能当作已覆盖")
        rubric = q.get("rubric")
        if not isinstance(rubric, list) or not rubric:
            raise ValueError("题目必须提供评分点")
        for point in rubric:
            _text(point.get("point"), "rubric.point")
            _number(point.get("marks"), "rubric.marks", positive=True)
        if not math.isclose(sum(p["marks"] for p in rubric), q["marks"], abs_tol=1e-9):
            raise ValueError("评分点分值之和必须等于题目分值")
        _text(q.get("answer"), "answer")
        if q["question_type"] == "multiple_choice":
            options = q.get("options")
            if not isinstance(options, dict) or len(options) < 2:
                raise ValueError("选择题至少需要两个选项")
            if section.get("option_count") is not None and len(options) != section["option_count"]:
                raise ValueError("选择题选项数与老师确认规格不一致")
            for key, value in options.items():
                _text(key, "option key")
                _text(value, "option text")
            if len({v.strip().casefold() for v in options.values()}) != len(options) or q["answer"] not in options:
                raise ValueError("选择题必须有唯一选项和有效标准答案")
        elif q.get("options"):
            raise ValueError("非选择题不得混用 options")
        covered.update(q_topics)
        section_questions[q["section_id"]].append(q)
    missing = [t["id"] for t in topics.values() if t["id"] not in covered]
    if mode in {"full_mock", "full_scope_mock"}:
        if missing:
            raise ValueError("完整模拟卷缺少考点覆盖：" + "、".join(missing))
        for key, section in sections.items():
            actual = section_questions[key]
            if section.get("count") is not None and len(actual) != section["count"]:
                raise ValueError("模拟卷题数与老师规格不一致：" + key)
            if section.get("total_marks") is not None and not math.isclose(sum(q["marks"] for q in actual), section["total_marks"], abs_tol=1e-9):
                raise ValueError("模拟卷分节分值与老师规格不一致：" + key)
        if profile.get("total_marks") is not None:
            _number(profile["total_marks"], "total_marks", positive=True)
            if not math.isclose(sum(q["marks"] for q in questions.values()), profile["total_marks"], abs_tol=1e-9):
                raise ValueError("模拟卷总分与老师规格不一致")
    if mode == "full_scope_mock":
        _validate_assistant_structure(payload, sections, section_questions)
    if any(s.get("total_marks") is None for s in sections.values()) and not any("分值" in a or "mark" in a.lower() for a in assumptions):
        raise ValueError("老师未说明分值，须在 assumptions 明确标注分值由助手设定")
    return profile, mode, missing, {key: materials[key].get("sha256") for key in sorted(used_materials)}


def _public(mock, status):
    return {"status": status, "mock_id": mock["id"], "exam_id": mock["exam_id"], "mode": mock["mode"],
            "question_count": len(mock["questions"]), "total_marks": sum(q["marks"] for q in mock["questions"]),
            "uncovered_topic_ids": deepcopy(mock["uncovered_topic_ids"]), "mastery_updated": False}


@study_time.state_timezone
def register_mock(state, payload, now):
    """Validate authored questions and freeze the teacher requirements they follow."""
    request = deepcopy(payload)
    request.setdefault("mode", "full_mock")
    _digest(request)
    for old in state.get("mock_exams", []):
        if old["id"] == payload.get("id"):
            if old.get("_request") != request:
                raise ValueError("同一模拟卷 id 内容冲突，请用新 id 保留历史")
            return _public(old, "duplicate")
    profile, mode, missing, material_hashes = _validate_mock(state, request)
    mock = {**request, "mode": mode, "profile_signature": profile_signature(profile),
            "profile_snapshot": deepcopy(profile), "created_at": _at(now).isoformat(),
            "uncovered_topic_ids": missing, "material_hashes": material_hashes,
            "_request": deepcopy(request), "content_signature": _digest(request), "answer_exposures": []}
    draft = deepcopy(state)
    draft.setdefault("mock_exams", []).append(mock)
    state.clear()
    state.update(draft)
    return _public(mock, "registered")


@study_time.state_timezone
def _stale_reason(state, mock):
    profile = next((p for p in state.get("exam_profiles", []) if p.get("id") == mock["exam_id"]), None)
    if profile and profile.get("verification_status") == "needs_review":
        return "Moodle 考试说明已有变化，须重新核读老师说明后更新模拟卷。"
    if profile is None or profile_signature(profile) != mock["profile_signature"]:
        return "老师的考试范围、题型或要求已变化，需要重新核对后另出新卷。"
    if profile_signature(mock["profile_snapshot"]) != mock["profile_signature"]:
        return "模拟卷保存的考试规格校验失败，需要恢复原卷或另出新卷。"
    materials = {v["id"]: v for v in state.get("materials", [])}
    if any(mid not in materials or materials[mid].get("sha256") != old for mid, old in mock.get("material_hashes", {}).items()):
        return "关联课件已变化或缺失，需要重新核对题目。"
    actual = {k: mock.get(k) for k in mock["_request"]}
    if _digest(actual) != mock["content_signature"]:
        return "模拟卷内容校验失败，需要恢复原卷或另出新卷。"
    return None


@study_time.state_timezone
def mock_is_current(state, mock):
    """Safe public freshness predicate for regenerated views."""
    return _stale_reason(state, mock) is None


@study_time.state_timezone
def mock_stale_reason(state, mock):
    return _stale_reason(state, mock)


@study_time.state_timezone
def mark_mock_answers_shown(state, payload, now):
    """Call before showing/exporting an answer key; exposure is evidence, not a score."""
    mock = _find(state, "mock_exams", payload.get("mock_id"))
    reason = _stale_reason(state, mock)
    if reason:
        raise ValueError(reason)
    question_ids = payload.get("question_ids", [q["id"] for q in mock["questions"]])
    valid = {q["id"] for q in mock["questions"]}
    if not isinstance(question_ids, list) or not question_ids or len(set(question_ids)) != len(question_ids) or not set(question_ids) <= valid:
        raise ValueError("答案展示必须关联有效且不重复的题目")
    evidence = _evidence(payload.get("evidence"))
    at = _at(now)
    if at < _at(mock["created_at"]):
        raise ValueError("答案展示时间不能早于出卷时间")
    at = at.isoformat()
    draft = deepcopy(state)
    target = _find(draft, "mock_exams", mock["id"])
    existing = {qid for e in target["answer_exposures"] for qid in e["question_ids"]}
    new = [qid for qid in question_ids if qid not in existing]
    if new:
        target["answer_exposures"].append({"shown_at": at, "question_ids": new, "evidence": evidence})
        state.clear()
        state.update(draft)
    return {"status": "recorded" if new else "duplicate", "mock_id": mock["id"], "question_ids": question_ids, "mastery_updated": False}


@study_time.state_timezone
def render_mock(state, mock_id, answers=False):
    mock = _find(state, "mock_exams", mock_id)
    reason = _stale_reason(state, mock)
    lines = ["# " + mock["title"], ""]
    if reason:
        return "\n".join(lines + ["**已过期，暂停使用。** " + reason, ""])
    profile = mock["profile_snapshot"]
    if answers:
        exposed = {qid for e in mock["answer_exposures"] for qid in e["question_ids"]}
        if not {q["id"] for q in mock["questions"]} <= exposed:
            raise ValueError("展示答案前必须先记录答案已展示；不自动推断掌握")
        lines += ["**答案与评分参考：已记录答案展示。**", ""]
    if mock["mode"] == "targeted_practice":
        lines += ["**专项模拟练习：范围或整卷结构不完整，不能当作完整考前模拟卷。**",
                  "未公布的题数、分值与练习顺序由助手安排；已公布部分以老师说明为准。", ""]
    elif mock["mode"] == "full_scope_mock":
        lines += ["**全范围模拟卷：覆盖已登记的考试范围；已公布题型、题数和分值按老师要求。**",
                  "老师尚未公布的结构细节由助手安排，不能据此推断正式考试的题量或逐题分值。", ""]
        sections = {section["id"]: section for section in profile["sections"]}
        for row in mock.get("assistant_structure", []):
            section = sections[row["section_id"]]
            unknown = [label for key, label in (("count", "题数"), ("marks_per_question", "逐题分值"))
                       if section.get(key) is None]
            lines += ["- " + LABELS[section["question_type"]] + "：老师未公布" + "、".join(unknown)
                      + "；本卷安排 " + str(row["count"]) + " 题，各题依次为 "
                      + "、".join(map(str, row["marks"])) + " 分，由助手设定。"]
        lines.append("")
    else:
        lines += ["根据老师已确认的题型、题数和考试范围编写的原创模拟卷。", ""]
    duration = profile.get("duration_minutes")
    lines += ["正式考试时长：" + (str(duration) + " 分钟" if duration else "待确认"),
              "本卷题数：" + str(len(mock["questions"])) + "；练习总分：" + str(sum(q["marks"] for q in mock["questions"])), ""]
    topics = {t["id"]: t["label"] for t in profile["topics"]}
    if mock["uncovered_topic_ids"]:
        lines += ["未覆盖：" + "、".join(topics[t] for t in mock["uncovered_topic_ids"]), ""]
    for assumption in mock.get("assumptions", []):
        lines += ["- " + assumption]
    if mock.get("assumptions"):
        lines.append("")
    display_number = 0
    for section in profile["sections"]:
        questions = [q for q in mock["questions"] if q["section_id"] == section["id"]]
        if not questions:
            continue
        lines += ["## " + LABELS[section["question_type"]], ""]
        for q in questions:
            display_number += 1
            lines += ["### Q" + str(display_number) + "（" + str(q["marks"]) + " 分）", "", q["stem"], ""]
            for key, value in q.get("options", {}).items():
                lines += ["- **" + key + ".** " + value]
            if q.get("options"):
                lines.append("")
            if answers:
                lines += ["答案：" + q["answer"], ""]
                lines += ["- " + p["point"] + "（" + str(p["marks"]) + " 分）" for p in q["rubric"]]
                lines.append("")
                materials = {m["id"]: m for m in state.get("materials", [])}
                lines += ["课件依据：" + "；".join(
                    materials[src["material_id"]].get("filename", src["material_id"]) + "，物理页 "
                    + ", ".join(map(str, src["pages"])) for src in q["sources"]), ""]
    return "\n".join(lines)


@study_time.state_timezone
def _mistake(state, mock, question, result, attempt_id, at):
    identity = "MOCK-MISTAKE-" + mock["id"] + "-" + question["id"]
    old = next((m for m in state.setdefault("mistakes", []) if m["id"] == identity), None)
    event = {"at": at, "attempt_id": attempt_id, "answer": result["answer"], "feedback": result["feedback"],
             "evidence": deepcopy(result["evidence"]), "awarded_marks": result["awarded_marks"]}
    if old:
        old.setdefault("mock_attempt_history", []).append(event)
        old["occurrences"] = old.get("occurrences", 1) + 1
    else:
        source = question["sources"][0]
        old = {"id": identity, "course_id": mock["profile_snapshot"]["course_id"], "entry_type": "mistake",
               "display_title": mock["title"] + " " + question["id"], "original_question": question["stem"],
               "user_answer_or_method": result["answer"], "correct_answer_or_method": question["answer"],
               "knowledge_point": [t["label"] for t in mock["profile_snapshot"]["topics"] if t["id"] in question["topic_ids"]],
               "why_wrong": result["feedback"], "confusion_point": None, "mistake_type": None,
               "date": at[:10], "resolution_status": "已保留真实作答及评分，待针对性讲解和新变式复测",
               "review_status": "需要准备未展示答案的新变式", "occurrences": 1,
               "source": {"type": "mock_exam_attempt", "mock_id": mock["id"], "exam_id": mock["exam_id"],
                          "question_id": question["id"], "material_id": source["material_id"], "pages": source["pages"]},
               "topic_ids": deepcopy(question["topic_ids"]), "exam_topic_ids": deepcopy(question["topic_ids"]), "mock_attempt_history": [event]}
        state["mistakes"].append(old)
    queue_id = "REVIEW-" + identity
    if not any(r["id"] == queue_id for r in state.setdefault("review_queue", [])):
        src = question["sources"][0]
        state["review_queue"].append({"id": queue_id, "course_id": old["course_id"], "mistake_id": identity,
            "material_id": src["material_id"], "pages": src["pages"], "status": "independent_review_pending",
            "priority": "High", "due_date": None, "created_at": at, "updated_at": at,
            "exam_id": mock["exam_id"], "topic_ids": deepcopy(question["topic_ids"]), "exam_topic_ids": deepcopy(question["topic_ids"]),
            "practice_variants": [], "independent_prompts": [],
            "note": "真实模拟作答暴露的卡点；需要核验新变式，不把原题看过答案后的重答当作复测。"})
    return queue_id


@study_time.state_timezone
def record_mock_attempt(state, payload, now):
    request = deepcopy(payload)
    _digest(request)
    attempt_id = _text(payload.get("attempt_id"), "attempt_id")
    for old in state.get("mock_attempts", []):
        if old["attempt_id"] == attempt_id:
            if old.get("_request") != request:
                raise ValueError("同一 attempt_id 的真实记录冲突，未覆盖原作答")
            return {"status": "duplicate", "attempt_id": attempt_id, "total_marks": old["total_marks"], "mastery_updated": False}
    mock = _find(state, "mock_exams", payload.get("mock_id"))
    reason = _stale_reason(state, mock)
    if reason:
        raise ValueError(reason)
    now = _at(now)
    submitted = _at(payload.get("submitted_at") or now)
    if submitted > now + timedelta(minutes=5) or submitted < _at(mock["created_at"]):
        raise ValueError("作答时间不能早于出卷或晚于当前时间")
    actual = payload.get("actual_minutes")
    if actual is not None:
        _number(actual, "actual_minutes")
    supplied = payload.get("answers")
    if not isinstance(supplied, list) or not supplied:
        raise ValueError("必须提交真实 answers，未作答不能按零分写入")
    qids = [a.get("question_id") for a in supplied]
    if len(set(qids)) != len(qids):
        raise ValueError("同一作答不能重复题目")
    questions = {q["id"]: q for q in mock["questions"]}
    results = []
    for answer in supplied:
        question = questions.get(answer.get("question_id"))
        if question is None:
            raise ValueError("作答题号不属于该模拟卷")
        _text(answer.get("answer"), "answer")
        evidence = _evidence(answer.get("evidence"))
        _text(answer.get("feedback"), "feedback")
        if type(answer.get("independent")) is not bool or type(answer.get("answer_seen_before_attempt")) is not bool:
            raise ValueError("每题必须明确 independent 和 answer_seen_before_attempt")
        exposed = any(question["id"] in e["question_ids"] and _at(e["shown_at"]) <= submitted for e in mock["answer_exposures"])
        if exposed and not answer["answer_seen_before_attempt"]:
            raise ValueError("题目答案已展示，不能记成未见答案的独立作答")
        if question["question_type"] == "multiple_choice":
            selected = answer["answer"].strip()
            if selected not in question["options"]:
                raise ValueError("选择题答案须为一个有效选项键，不能猜测用户意思")
            marks = question["marks"] if selected == question["answer"] else 0
            if answer.get("awarded_marks") is not None and answer["awarded_marks"] != marks:
                raise ValueError("选择题人工分值与唯一标准答案不一致")
            scores = None
            method = "exact_option"
        else:
            marks = _number(answer.get("awarded_marks"), "awarded_marks")
            scores = answer.get("rubric_scores")
            if not isinstance(scores, list) or len(scores) != len(question["rubric"]):
                raise ValueError("人工评分须逐项提供 rubric_scores")
            for score, point in zip(scores, question["rubric"]):
                _number(score, "rubric_score")
                if score > point["marks"]:
                    raise ValueError("单个评分点得分超出分值")
            if not math.isclose(sum(scores), marks, abs_tol=1e-9):
                raise ValueError("评分点得分之和与总得分不一致")
            method = "manual_rubric"
        if marks > question["marks"]:
            raise ValueError("得分超出题目分值")
        eligible = answer["independent"] and not answer["answer_seen_before_attempt"] and not exposed
        results.append({"question_id": question["id"], "topic_ids": deepcopy(question["topic_ids"]),
            "answer": answer["answer"], "independent": answer["independent"],
            "answer_seen_before_attempt": answer["answer_seen_before_attempt"], "eligible_independent": eligible,
            "correct": math.isclose(marks, question["marks"], abs_tol=1e-9), "awarded_marks": marks,
            "max_marks": question["marks"], "rubric_scores": scores, "grading_method": method,
            "feedback": answer["feedback"], "evidence": evidence})
    entry = {"attempt_id": attempt_id, "mock_id": mock["id"], "exam_id": mock["exam_id"],
        "course_id": mock["profile_snapshot"]["course_id"], "submitted_at": submitted.isoformat(),
        "recorded_at": now.isoformat(), "actual_minutes": actual, "time_scope": "session",
        "independent": all(r["eligible_independent"] for r in results),
        "answer_seen_before_attempt": any(r["answer_seen_before_attempt"] for r in results),
        "evidence": (_evidence(payload["evidence"]) if payload.get("evidence") is not None
                     else {"per_question": [r["evidence"] for r in results]}),
        "question_results": results, "total_marks": sum(r["awarded_marks"] for r in results),
        "max_marks": sum(r["max_marks"] for r in results), "paper_max_marks": sum(q["marks"] for q in questions.values()),
        "complete": set(qids) == set(questions), "profile_signature": mock["profile_signature"], "_request": request}
    draft = deepcopy(state)
    queued = []
    for result in results:
        if result["eligible_independent"] and not result["correct"]:
            queued.append(_mistake(draft, mock, questions[result["question_id"]], result, attempt_id, submitted.isoformat()))
    # Grading feedback may disclose the method/key. Preserve the pre-feedback
    # evidence above, then prevent a later repeat of this same paper from being
    # counted as an unseen independent check. A new variant is needed for retest.
    target = _find(draft, "mock_exams", mock["id"])
    target["answer_exposures"].append({"shown_at": now.isoformat(), "question_ids": qids,
        "evidence": {"type": "mock_grading_feedback", "attempt_id": attempt_id,
                     "detail": "本次评分反馈已产生；之后重答这些原题不能视为未见答案的复测。"}})
    draft.setdefault("mock_attempts", []).append(entry)
    state.clear()
    state.update(draft)
    return {"status": "recorded", "attempt_id": attempt_id, "total_marks": entry["total_marks"],
            "max_marks": entry["max_marks"], "complete": entry["complete"], "review_ids": queued,
            "mastery_updated": False, "fsrs_updated": False}


@study_time.state_timezone
def render_catalog(state, now):
    lines = ["# 考前模拟练习", "", "更新日期：" + _at(now).date().isoformat(), "",
             "模拟题依据老师在 Moodle 确认的考试要求与本地课件编写；题目和答案分开，只有真实作答才进入学习记录。", ""]
    for profile in state.get("exam_profiles", []):
        lines += ["## " + profile["course_id"] + " · " + profile["title"], ""]
        if profile.get("verification_status") == "needs_review":
            lines += ["Moodle 考试说明有变化，等待重新核读老师说明；暂停出卷和评分。", ""]
        elif profile.get("format_status") == "unknown":
            lines += ["题型待老师确认，暂不生成考试模拟卷。", ""]
        mocks = [m for m in state.get("mock_exams", []) if m["exam_id"] == profile["id"]]
        if not mocks:
            lines += ["尚无已核验的模拟卷。", ""]
        for mock in mocks:
            reason = _stale_reason(state, mock)
            status = ("已过期，需按最新要求重出" if reason else "完整模拟卷" if mock["mode"] == "full_mock"
                      else "全范围模拟卷，未公布的结构细节由助手安排" if mock["mode"] == "full_scope_mock"
                      else "专项练习，范围或整卷结构不完整")
            lines += ["- " + mock["title"] + "（" + status + "；" + str(len(mock["questions"])) + " 题）"]
        if mocks:
            lines.append("")
    return "\n".join(lines)


@study_time.state_timezone
def render_mock_notebook_section(state, course_id):
    """Only the managed mock-attempt subsection, leaving the existing notebook intact."""
    entries = [m for m in state.get("mistakes", []) if m.get("course_id") == course_id
               and m.get("source", {}).get("type") == "mock_exam_attempt"]
    if not entries:
        return ""
    lines = ["## 模拟考试中的实际卡点", ""]
    for entry in entries:
        history = entry.get("mock_attempt_history", [])
        last = history[-1] if history else {}
        lines += ["### " + entry["display_title"], "", entry["original_question"], "",
                  "本人思路：" + str(last.get("answer", entry.get("user_answer_or_method")) or "待确认"), "",
                  "核对反馈：" + str(last.get("feedback", entry.get("why_wrong")) or "待确认"), "",
                  "解法参考：" + str(entry.get("correct_answer_or_method") or "待确认"), "",
                  "复习状态：需要未展示答案的新变式，后续真实独立作答再调整复测安排。", ""]
    return "\n".join(lines)
