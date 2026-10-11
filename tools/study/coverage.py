"""Evidence-backed exam coverage and seven-day reflection; pure derived views.

Question availability, submissions and grades never establish skill mastery. A
topic is linked only by an explicit topic_ids field, never guessed from chapters.
All windows use the learning profile calendar days and all writes belong to daily.py.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

import planning
import study_time


VIEW_NAMES = ("Exam Coverage.md", "Weekly Reflection.md")


def _text(value):
    if value is None or value == "":
        return "待确认"
    if isinstance(value, list):
        return "；".join(_text(item) for item in value)
    if isinstance(value, dict):
        return _text(value.get("label") or value.get("detail") or value.get("title") or "待确认")
    return str(value).replace("|", "\\|").replace("\n", " ")


def _time(value):
    if not value:
        return None
    try:
        return planning.local_now(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _evidence(value):
    return bool(value.strip()) if isinstance(value, str) else isinstance(value, dict) and bool(value)


def _ids(value):
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def _topics(*records):
    return sorted({topic for record in records if isinstance(record, dict)
                   for topic in _ids(record.get("topic_ids"))})


def _answer_present(value):
    return isinstance(value, str) and bool(value.strip())


@study_time.state_timezone
def _previously_exposed(state, attempt, queue, at):
    """Feedback shown after this attempt does not invalidate its own evidence."""
    variant_id = attempt.get("variant_id")
    prompt = attempt.get("prompt")
    candidates = list(queue.get("practice_variants", []))
    candidates += [item for session in state.get("review_sessions", [])
                   for item in session.get("items", []) if item.get("review_id") == queue.get("id")]
    for candidate in candidates:
        same = candidate.get("variant_id", candidate.get("id")) == variant_id
        same = same or bool(prompt and candidate.get("prompt") == prompt)
        shown = _time(candidate.get("answer_shown_at"))
        if same and shown and shown < at:
            return True
    return False


@study_time.state_timezone
def evidence_events(state, now):
    """Normalize actual answer evidence without mutating records or inventing links."""
    now = planning.local_now(now)
    queue = {item.get("id"): item for item in state.get("review_queue", [])}
    exams = {item.get("id"): item for item in state.get("mock_exams", [])}
    result, seen = [], set()
    for attempt in state.get("review_attempts", []):
        attempt_id, at = attempt.get("attempt_id"), _time(attempt.get("reviewed_at"))
        key = ("review", attempt_id)
        if (not attempt_id or key in seen or not at or at > now or
                not _evidence(attempt.get("evidence")) or not _answer_present(attempt.get("answer"))):
            continue
        seen.add(key)
        item = queue.get(attempt.get("review_id"), {})
        variant = next((v for v in item.get("practice_variants", [])
                        if v.get("id") == attempt.get("variant_id")), {})
        independent = (attempt.get("independent") is True and
                       attempt.get("answer_seen_before_attempt") is False and
                       not _previously_exposed(state, attempt, item, at))
        outcome = attempt.get("correct")
        if not isinstance(outcome, bool):
            outcome = {"again": False, "hard": True, "good": True, "easy": True}.get(attempt.get("rating"))
        result.append({"id": key, "kind": "review", "at": at,
                       "course_id": attempt.get("course_id") or item.get("course_id"),
                       "topic_ids": _topics(attempt, variant, item), "independent": independent,
                       "correct": outcome, "difficulty": attempt.get("rating") == "hard",
                       "skill_ref": attempt.get("review_id"),
                       "question_ref": attempt.get("variant_id"),
                       "next_due": _time(attempt.get("next_review_at")),
                       "actual_minutes": attempt.get("actual_minutes")})
    for attempt in state.get("mock_attempts", []):
        attempt_id = attempt.get("attempt_id")
        at = _time(attempt.get("submitted_at"))
        if not attempt_id or not at or at > now or not _evidence(attempt.get("evidence")):
            continue
        exam = exams.get(attempt.get("mock_id"), {})
        questions = {q.get("id"): q for q in exam.get("questions", [])}
        for row in attempt.get("question_results", []):
            question_id = row.get("question_id")
            key = ("mock", attempt_id, question_id)
            if (not question_id or key in seen or not _evidence(row.get("evidence")) or
                    not _answer_present(row.get("answer"))):
                continue
            seen.add(key)
            independent = (row.get("eligible_independent") is True and row.get("independent") is True
                           and row.get("answer_seen_before_attempt") is False)
            correct = row.get("correct") if isinstance(row.get("correct"), bool) else None
            result.append({"id": key, "kind": "mock", "at": at,
                           "course_id": attempt.get("course_id") or exam.get("course_id"),
                           "topic_ids": _topics(row, questions.get(question_id, {})),
                           "independent": independent, "correct": correct, "difficulty": False,
                           "skill_ref": None, "question_ref": str(attempt.get("mock_id")) + ":" + question_id,
                           "next_due": None, "actual_minutes": None})
    return sorted(result, key=lambda event: (event["at"], str(event["id"])))


@study_time.state_timezone
def _topic_due(state, course_id, topic_id, events):
    # Use the current card when present; a historical due date is superseded.
    current = []
    for item in state.get("review_queue", []):
        if item.get("course_id") != course_id or item.get("status") in {"Completed", "completed", "suspended", "archived"}:
            continue
        linked = _topics(item, *item.get("practice_variants", []))
        if topic_id not in linked:
            continue
        value = item.get("fsrs", {}).get("card", {}).get("due")
        at = _time(value)
        if at is None and _day(item.get("due_date")):
            at = planning.local_now(str(_day(item["due_date"])) + "T00:00:00+08:00")
        if at:
            current.append(at)
    if current:
        return min(current)
    # Only the latest independent attempt's next due can remain active.
    return events[-1]["next_due"] if events else None


@study_time.state_timezone
def topic_status(state, course_id, topic_id, events, now):
    linked = [event for event in events if event["course_id"] == course_id and topic_id in event["topic_ids"]]
    independent = [event for event in linked if event["independent"]]
    if not independent:
        return {"status": "未独立测试", "independent": 0, "assisted": len(linked), "last_at": None}
    latest = independent[-1]
    due = _topic_due(state, course_id, topic_id, independent)
    successes = [event for event in independent if event["correct"] is True]
    # One later correct response does not erase a prior unresolved failure.
    last_problem = max((event["at"] for event in independent
                        if event["correct"] is False or event["difficulty"]), default=None)
    success_days = {event["at"].date() for event in successes
                    if last_problem is None or event["at"] > last_problem}
    if due and due <= now:
        status = "到期复测"
    elif latest["correct"] is not True or (last_problem and len(success_days) < 2):
        status = "仍不稳定"
    elif len(success_days) < 2:
        status = "独立做对（单次）"
    else:
        status = "独立做对（多次）"
    return {"status": status, "independent": len(independent), "assisted": len(linked) - len(independent),
            "last_at": latest["at"], "next_due": due}


def _scope_label(value):
    return {"confirmed": "老师已确认", "teacher_confirmed": "老师已确认", "partial": "仅部分确认",
            "pending": "待确认", "unknown": "待确认"}.get(value, _text(value))


@study_time.state_timezone
def _profile_lines(state, profile, events, now):
    course_id = profile.get("course_id")
    day = _day(profile.get("exam_date"))
    lines = ["### " + _text(course_id) + " · " + _text(profile.get("title")), "",
             f"考试日期：{day.isoformat() if day else '待确认'}；范围：{_scope_label(profile.get('scope_status'))}；题型：{_scope_label(profile.get('format_status'))}。", ""]
    if profile.get("verification_status") == "needs_review":
        lines[2] = "上次核读记录：" + lines[2]
        lines += ["**考试说明待重新核读。以下保留历史范围与作答证据，暂不能视为当前完整考前清单。**", ""]
    if profile.get("scope_status") not in {"confirmed", "teacher_confirmed"}:
        lines += ["范围尚未完整确认；以下只展示已有明确来源的内容，不能视为完整考试清单。", ""]
    topics = profile.get("topics", [])
    if not topics:
        lines += ["尚无可核对的考点清单；待老师说明和课件对应关系确认后补齐。", ""]
    else:
        lines += ["| 已列考点 | 课件准备 | 作答证据状态 | 独立 / 辅助作答 | 最近独立作答 |",
                  "| --- | --- | --- | --- | --- |"]
        for topic in topics:
            result = topic_status(state, course_id, topic.get("id"), events, now)
            last = result["last_at"].strftime("%m-%d") if result["last_at"] else "尚无"
            materials = "已有关联课件" if _ids(topic.get("material_ids")) else "待补课件"
            lines += [f"| {_text(topic.get('label'))} | {materials} | {result['status']} | {result['independent']} / {result['assisted']} | {last} |"]
        lines += ["", "这些考点是当前整理粒度；清单长度和题目数量不代表完整技能覆盖或掌握比例。", ""]
    if profile.get("excluded_topics"):
        lines += ["已明确排除：" + _text(profile["excluded_topics"]) + "。", ""]
    if profile.get("notes"):
        lines += [_text(profile["notes"]), ""]
    return lines


@study_time.state_timezone
def render_coverage(state, now, events=None):
    now = planning.local_now(now)
    events = evidence_events(state, now) if events is None else events
    profiles = state.get("exam_profiles", [])
    lines = [f"# 考前覆盖检查｜{now:%Y-%m-%d}", "", "以老师明确说明的考试范围为依据，只用有来源的真实独立作答判断测试状态。已交作业、看过答案和有题可做均不等于掌握。", ""]
    groups = [("待准备的考试", [p for p in profiles if _day(p.get("exam_date")) and _day(p["exam_date"]) >= now.date()]),
              ("考试日期待确认", [p for p in profiles if not _day(p.get("exam_date"))]),
              ("已过日期的考试（保留证据）", [p for p in profiles if _day(p.get("exam_date")) and _day(p["exam_date"]) < now.date()])]
    if not profiles:
        lines += ["考试档案尚未建立；范围、题型及覆盖情况待确认。", ""]
    for title, members in groups:
        if members:
            lines += ["## " + title, ""]
            for profile in sorted(members, key=lambda item: (item.get("exam_date") or "9999", item.get("id", ""))):
                lines += _profile_lines(state, profile, events, now)
    lines += ["“单次”表示只有一个日期的成功证据；同日重复做对不会升级为跨日稳定。困难作答显示“仍不稳定”，不会算成答错；失误或困难之后需至少两个后续日期的成功证据才显示“多次”，仍不自动标记掌握。", ""]
    return "\n".join(lines)


def _skill_keys(event):
    if event["topic_ids"]:
        return [(event["course_id"], "topic", topic) for topic in event["topic_ids"]]
    if event["skill_ref"]:
        return [(event["course_id"], "review", event["skill_ref"])]
    return [(event["course_id"], "question", event["question_ref"])]


@study_time.state_timezone
def _feedback_entries(state, start, end, now):
    result, seen = [], set()
    for entry in state.get("learning_feedback", []):
        day, recorded = _day(entry.get("date")), _time(entry.get("recorded_at"))
        if (not day or not start <= day <= end or (recorded and recorded > now) or
                not _evidence(entry.get("evidence")) or not _answer_present(entry.get("completed"))):
            continue
        key = entry.get("feedback_id")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return sorted(result, key=lambda entry: (entry["date"], entry.get("recorded_at") or ""))


@study_time.state_timezone
def _answer_time_lines(state, events):
    """Show answer-time evidence separately; it may overlap learning feedback."""
    lines = []
    reviews = [event for event in events if event["kind"] == "review"]
    if reviews:
        known = [event["actual_minutes"] for event in reviews if planning._number(event["actual_minutes"])]
        unknown = len(reviews) - len(known)
        label = f"已记录部分合计 {sum(known):g} 分钟" if known else "尚无已知时长"
        lines += [f"- 复习答题用时：{label}" + (f"；另有 {unknown} 题次待确认。" if unknown else "。")]
    mock_ids = {event["id"][1] for event in events if event["kind"] == "mock"}
    mocks = {}
    for attempt in state.get("mock_attempts", []):
        if attempt.get("attempt_id") in mock_ids:
            mocks.setdefault(attempt["attempt_id"], attempt)
    if mocks:
        durations = [{"feedback_id": key, "recorded_at": value.get("submitted_at"),
                      "actual_minutes": value.get("actual_minutes"), "time_scope": value.get("time_scope")}
                     for key, value in mocks.items()]
        # A mock's measured duration must refer to that session, never an
        # unspecified daily cumulative figure across multiple mock attempts.
        known = [value["actual_minutes"] for value in durations
                 if value.get("time_scope") == "session" and planning._number(value.get("actual_minutes"))]
        unknown = len(durations) - len(known)
        label = f"已记录部分合计 {sum(known):g} 分钟" if known else "尚无可合并时长"
        lines += [f"- 模拟答题用时：{label}" + (f"；另有 {unknown} 次待确认。" if unknown else "。")]
    return lines


@study_time.state_timezone
def render_reflection(state, now, events=None):
    now = planning.local_now(now)
    start, end = now.date() - timedelta(days=6), now.date()
    events = evidence_events(state, now) if events is None else events
    recent = [event for event in events if start <= event["at"].date() <= end]
    feedback = _feedback_entries(state, start, end, now)
    counts = Counter((event["kind"], event["independent"]) for event in recent)
    lines = [f"# 学习周复盘｜{start}–{end}", "", "仅汇总已记录的实际活动；没有记录不表示没有学习。题目数、完成反馈和模拟成绩均不自动转为掌握度。", "",
             "## 实际作答", "",
             f"- 复习：独立 {counts['review', True]} 题次，辅助 {counts['review', False]} 题次。",
             f"- 模拟题：独立 {counts['mock', True]} 题次，辅助 {counts['mock', False]} 题次。"]
    # Earlier evidence outside this window can establish a genuine later retest.
    prior_days, retests, repeated_errors = defaultdict(set), 0, set()
    wrong_counts = Counter()
    for event in events:
        if not event["independent"]:
            continue
        keys = _skill_keys(event)
        is_recent = start <= event["at"].date() <= end
        if is_recent and any(any(day < event["at"].date() for day in prior_days[key]) for key in keys):
            retests += 1
        for key in keys:
            prior_days[key].add(event["at"].date())
            if event["correct"] is False:
                wrong_counts[key] += 1
                if is_recent and wrong_counts[key] >= 2:
                    repeated_errors.add(key)
    lines += [f"- 跨日独立复测：{retests} 题次；重复失误：{len(repeated_errors)} 个明确关联的考点或复习项目。", ""]
    if not recent and not feedback:
        lines += ["本周暂无新流程的实际作答或学习反馈，暂不足以判断效果、复测表现或排程是否合适。", ""]
    elif not retests:
        lines += ["尚无本周跨日复测证据；暂不能据此判断延迟保持情况。", ""]
    lines += ["## 实际完成与卡点", ""]
    if feedback:
        for entry in feedback:
            lines += [f"- {entry['date']} · {_text(entry.get('course_id') or '学习反馈')}：{_text(entry['completed'])}。" +
                      (f" 卡点：{_text(entry['blocker'])}。" if entry.get("blocker") else "")]
    else:
        lines += ["尚无学习完成反馈；作答记录已单独统计，不代替作业提交确认。"]
    lines += ["", "## 用时与计划", "", "按天沿用学习反馈的累计 / 独立时段口径；答题用时可能包含其中，因此不另行相加。", ""]
    answer_times = _answer_time_lines(state, recent)
    if answer_times:
        lines += answer_times + [""]
    day_groups = defaultdict(list)
    for entry in feedback:
        day_groups[entry["date"]].append(entry)
    if not day_groups:
        lines += ["实际学习总用时待确认；不以零分钟代替缺失记录。", ""]
    for day, entries in sorted(day_groups.items()):
        reported = planning._reported_minutes(entries)
        capacity = planning._capacity(state, date.fromisoformat(day))
        total, budget = reported["total"], capacity["budget_minutes"]
        if reported["basis"] != "cumulative" and any(not planning._number(entry.get("actual_minutes")) for entry in entries):
            total = None
        actual_label = f"{total:g} 分钟" if total is not None else "待确认（缺时长或时段可能重叠）"
        budget_label = f"{budget:g} 分钟" if budget is not None else "待确认"
        difference = f"；实际比预算{'多' if total >= budget else '少'} {abs(total - budget):g} 分钟" if total is not None and budget is not None else ""
        lines += [f"- {day}：反馈用时 {actual_label}；当日预算 {budget_label}{difference}。"]
    priorities = []
    labels = {(p.get("course_id"), t.get("id")): t.get("label")
              for p in state.get("exam_profiles", []) for t in p.get("topics", [])}
    for key in sorted(repeated_errors, key=str):
        label = labels.get((key[0], key[2])) if key[1] == "topic" else None
        if not label and key[1] == "review":
            label = next((q.get("title") for q in state.get("review_queue", []) if q.get("id") == key[2]), None)
        priorities.append(f"优先重练 {_text(key[0])} · {_text(label or key[2])}：已有重复失误的独立证据。")
    for entry in reversed(feedback):
        if entry.get("next_step"):
            proposal = f"落实已记录下一步：{_text(entry['next_step'])}。"
            if proposal not in priorities:
                priorities.append(proposal)
        elif entry.get("blocker"):
            proposal = f"先解决已记录卡点：{_text(entry['blocker'])}。"
            if proposal not in priorities:
                priorities.append(proposal)
    if not priorities:
        priorities.append("完成一组独立作答并保留核对反馈；待有跨日复测和真实用时后再判断调整方向。")
    lines += ["", "## 接下来最多三项", ""] + [f"{index}. {text}" for index, text in enumerate(priorities[:3], 1)]
    lines += ["", "这些建议来自已有记录，不会自动增加当天预算或改写提交状态。", ""]
    return "\n".join(lines)


@study_time.state_timezone
def render_views(state, now):
    now = planning.local_now(now)
    events = evidence_events(state, now)
    return {"Exam Coverage.md": render_coverage(state, now, events),
            "Weekly Reflection.md": render_reflection(state, now, events)}
