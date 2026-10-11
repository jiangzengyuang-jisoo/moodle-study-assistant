"""Rebuild dated study views from state; no scheduler or platform mutations.

Only an explicit invocation (normally the per-message workflow) renders files.
Historical daily plans and course commitments remain source evidence, not reusable
today text. This module neither infers completion nor allocates clock-time slots.
"""
import argparse
import study_time
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / "Study" / "_System" / "state.json"
HK = timezone(timedelta(hours=8))
VIEW_NAMES = ("Today Plan.md", "Weekly View.md", "Upcoming.md")
VERSION = 1
MARKER = re.compile(r"<!-- study-plan (\{[^\n]+\}) -->")
DONE = {"completed", "complete", "submitted", "finished", "done", "已完成", "已提交"}


def local_now(now=None):
    value = datetime.fromisoformat(now.replace("Z", "+00:00")) if isinstance(now, str) else now
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带时区；香港时间使用 +08:00")
    return value.astimezone(study_time.local_zone())


def _list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _text(value):
    if value is None:
        return "待确认"
    if isinstance(value, list):
        return "；".join(_text(x) for x in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).replace("|", "\\|").replace("\n", " ")


def is_complete(record):
    """Positive completion evidence wins over a personal account's empty record."""
    if not record:
        return False
    for key in ("status", "submission_status", "group_submission_status"):
        if str(record.get(key, "")).strip().lower() in DONE:
            return True
    if record.get("progress_confirmation") == "user_reported_group_submission":
        return True
    if record.get("group_submitted") is True or record.get("submitted_by_group_representative") is True:
        return True
    group = record.get("group_submission")
    return isinstance(group, dict) and (group.get("submitted") is True or
                                        str(group.get("status", "")).lower() in DONE)


def _status(record):
    value = record.get("status")
    return {None: "进度待确认", "": "进度待确认", "Not started": "未开始（已有记录）",
            "In progress": "进行中（已有记录）"}.get(value, _text(value))


def _deadline_when(item):
    result = item.get("date") or "日期待确认"
    if item.get("time"):
        result += " " + str(item["time"])
        if item.get("end_time"):
            result += "–" + str(item["end_time"])
    elif item.get("date"):
        result += "（时刻待确认）"
    return result


def _past(item, now):
    day = _day(item.get("date"))
    if day is None:
        return False
    if day != now.date():
        return day < now.date()
    clock = item.get("end_time") or item.get("time")
    if clock:
        try:
            return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=study_time.local_zone()) < now
        except ValueError:
            pass
    return False


def _is_exam(deadline, task):
    descriptor = " ".join(str(x or "") for x in (deadline.get("title"), task.get("type"))).lower()
    return any(word in descriptor for word in ("midterm", "mid-term", "exam", "test", "期中", "考试"))


@study_time.state_timezone
def _deadline_rows(state, now):
    tasks = {x["id"]: x for x in state.get("tasks", [])}
    by_deadline = {x["deadline_id"]: x for x in tasks.values() if x.get("deadline_id")}
    result = []
    for deadline in state.get("deadlines", []):
        task = tasks.get(deadline.get("task_id")) or by_deadline.get(deadline.get("id"), {})
        if is_complete(deadline) or is_complete(task):
            continue
        result.append({"deadline": deadline, "task": task, "date": _day(deadline.get("date")),
                       "past": _past(deadline, now), "exam": _is_exam(deadline, task)})
    return sorted(result, key=lambda row: (row["date"] or date.max,
                                           str(row["deadline"].get("time") or ""),
                                           row["deadline"].get("id", "")))


@study_time.state_timezone
def _plans_for(state, day):
    plans = state.get("daily_plans") or []
    if isinstance(plans, dict):
        plans = [dict(value, date=value.get("date", key)) for key, value in plans.items() if isinstance(value, dict)]
    return [p for p in plans if isinstance(p, dict) and p.get("date") == day.isoformat()]


@study_time.state_timezone
def _capacity(state, day):
    plans = _plans_for(state, day)
    # Never import a historical day's capacity into today.
    available = next((p["available_study_minutes"] for p in reversed(plans)
                      if _number(p.get("available_study_minutes"))), None)
    confirmed_budget = next((p.get("budget_minutes", p.get("scheduled_minutes"))
                             for p in reversed(plans)
                             if not p.get("course_scope") and not p.get("course_capacity") and
                             _number(p.get("budget_minutes", p.get("scheduled_minutes")))), None)
    target = state.get("settings", {}).get("study_capacity_target", .8)
    target = min(.85, max(.7, target if _number(target) else .8))
    budget = None
    if available is not None:
        ceiling = math.floor(available * .85)
        budget = min(confirmed_budget, ceiling) if confirmed_budget is not None else math.floor(available * target)
    courses = []
    for plan in plans:
        capacity = plan.get("course_capacity")
        if not isinstance(capacity, dict):
            continue
        dates = capacity.get("applies_only_to_dates")
        if dates and day.isoformat() not in dates:
            continue
        interval = capacity.get("available_minutes_range")
        if not isinstance(interval, list) or len(interval) != 2 or not all(_number(v) for v in interval):
            continue
        minimum, maximum = interval
        if maximum < minimum:
            continue
        original = plan.get("budget_minutes", plan.get("scheduled_minutes"))
        core = min(original, math.floor(minimum * .85)) if _number(original) else math.floor(minimum * target)
        courses.append({"course_id": capacity.get("course_id"), "available_minutes_range": interval,
                        "budget_minutes": core, "original_budget_minutes": original})
    return {"mode": "capacity_known" if available is not None else "preliminary",
            "available_minutes": available, "budget_minutes": budget, "course_budgets": courses}


@study_time.state_timezone
def _feedback(state, day):
    return [x for x in state.get("learning_feedback", []) if x.get("date") == day.isoformat()]


@study_time.state_timezone
def review_budget_limit(state, now=None, requested=15):
    """Today's total review ceiling, not an amount to charge on every invocation.

    Item actual/reserved minutes are already charged by learning_loop. Brief
    feedback can overlap those minutes, so neither feedback nor completed items
    are subtracted here. Callers must retain consumed history when a limit falls.
    """
    if not _number(requested) or requested > 30:
        raise ValueError("每日复习预算应为 0–30 分钟")
    day = local_now(now).date()
    limits = [requested]
    capacity = _capacity(state, day)
    if capacity["budget_minutes"] is not None:
        limits.append(capacity["budget_minutes"])
    for plan in _plans_for(state, day):
        for field in ("review_minutes_total", "review_minutes"):
            if _number(plan.get(field)):
                limits.append(plan[field])
        # A directly recorded total budget remains a ceiling even if the day's
        # available capacity was not recorded. Course-only budgets stay local.
        if not plan.get("course_scope") and not plan.get("course_capacity"):
            for field in ("budget_minutes", "scheduled_minutes"):
                if _number(plan.get(field)):
                    limits.append(plan[field])
    return min(limits)


def _reported_minutes(feedback):
    """Only combine explicitly distinct learning sessions; never add review time."""
    entries = [x for x in feedback if _number(x.get("actual_minutes"))]
    if not entries:
        return {"total": None, "basis": None, "count": 0}
    cumulative = [(index, x) for index, x in enumerate(entries) if x.get("time_scope") == "cumulative"]
    if cumulative:
        _, latest = max(cumulative, key=lambda pair: (pair[1].get("recorded_at") or "", pair[0]))
        return {"total": latest["actual_minutes"], "basis": "cumulative", "count": len(entries)}
    ids = [x.get("feedback_id") for x in entries]
    if all(x.get("time_scope") == "session" for x in entries) and all(ids) and len(ids) == len(set(ids)):
        return {"total": sum(x["actual_minutes"] for x in entries), "basis": "session", "count": len(entries)}
    return {"total": None, "basis": "unclear", "count": len(entries)}


@study_time.state_timezone
def _capacity_line(state, day):
    capacity = _capacity(state, day)
    feedback = _feedback(state, day)
    reported = _reported_minutes(feedback)
    lines = []
    if capacity["available_minutes"] is None:
        lines.append("初步计划：当天实际可用学习时间待确认，暂不分配总时长或时间槽。")
    else:
        lines.append(f"已知可用 {capacity['available_minutes']:g} 分钟；学习上限安排 {capacity['budget_minutes']:g} 分钟，保留缓冲。")
        if reported["total"] is not None:
            left = max(0, capacity["budget_minutes"] - reported["total"])
            basis = "最近累计反馈" if reported["basis"] == "cumulative" else "明确独立学习段合计"
            lines.append(f"{basis} {reported['total']:g} 分钟；按该反馈，原预算内剩余至多 {left:g} 分钟（不另加逐题复习用时）。")
        elif reported["count"]:
            lines.append(f"已有{reported['count']}条用时反馈；统计范围或重复情况待确认，暂不合计或推算剩余时间。")
    for course in capacity["course_budgets"]:
        lo, hi = course["available_minutes_range"]
        lines.append(f"{course['course_id']} 已确认可用 {lo:g}–{hi:g} 分钟；保留核心 {course['budget_minutes']:g} 分钟（包含复习），仅适用本日。")
        if _number(course["original_budget_minutes"]) and course["original_budget_minutes"] > course["budget_minutes"]:
            lines.append("原课程预算超过保留缓冲后的容量；原记录保留，本视图暂按容量上限展示。")
    if day.weekday() >= 5:
        lines.append("周末保留休息与运动；未明确的空档不当作可用容量。")
    return " ".join(lines)


@study_time.state_timezone
def _events(state, day):
    """Only explicit attendance blocks; cancellations never imply free capacity."""
    result = []
    day_plans = _plans_for(state, day)
    explicit_ids = {event_id for plan in day_plans for event_id in _list(plan.get("fixed_commitment_event_ids"))}
    for schedule in state.get("schedules", []):
        for event in schedule.get("events", []):
            if event.get("date"):
                matches = event["date"] == day.isoformat()
            else:
                matches = event.get("weekday") == day.strftime("%A") and schedule.get("type") != "dated_activity_course"
            if not matches or (event.get("default_block_for_attendance") is not True and event.get("id") not in explicit_ids):
                continue
            exceptions = [e for e in schedule.get("exceptions", [])
                          if e.get("start_date", "9999") <= day.isoformat() <= e.get("end_date", "0000")
                          and (not e.get("course_id") or e["course_id"] == event.get("course_id"))
                          and (not e.get("event_id") or e["event_id"] == event.get("id"))
                          and (not e.get("types") or event.get("type") in e["types"])]
            exception = exceptions[-1] if exceptions else {}
            label = event.get("title") or " ".join(str(x) for x in (event.get("course_id"), event.get("type")) if x)
            if exception.get("held") is False:
                suffix = "停课待确认，暂保留该时段" if exception.get("tentative") else "本日停课"
                result.append(f"{label}：{suffix}")
                continue
            start, end = event.get("start"), event.get("end")
            clock = f"{start}–{end}" if start and end else (start or "时刻待确认")
            venue = f"，{event['venue']}" if event.get("venue") else ""
            result.append(f"{clock} {label}{venue}")
    for plan in day_plans:
        for slot in plan.get("scheduled_slots", []):
            if not isinstance(slot, dict):
                continue
            label = slot.get("title") or slot.get("task") or slot.get("course_id") or "已记录学习安排"
            start, end = slot.get("start"), slot.get("end")
            clock = f"{start}–{end}" if start and end else (start or "时刻待确认")
            qualifier = "已确认" if slot.get("confirmed") is True else "原计划安排，确认情况见原记录"
            result.append(f"{clock} {label}（{qualifier}）")
    return result


@study_time.state_timezone
def _priorities(state, now, rows):
    tasks = {t["id"]: t for t in state.get("tasks", [])}
    all_deadlines = {d.get("id"): d for d in state.get("deadlines", [])}
    deadline_by_task = {r["task"].get("id"): r for r in rows if r["task"].get("id")}
    ordered = []
    for p in _plans_for(state, now.date()):
        ordered.extend(_list(p.get("candidate_task_ids")))
    horizon = now.date() + timedelta(days=6)
    ordered.extend(r["task"]["id"] for r in rows if r["task"].get("id") and not r["past"] and
                   r["date"] and r["date"] <= horizon)
    for course in state.get("courses", []):
        ordered.extend(_list(course.get("assignment_focus", {}).get("task_ids")))
    result, seen = [], set()
    for task_id in ordered:
        task = tasks.get(task_id)
        if not task or task_id in seen or is_complete(task) or is_complete(all_deadlines.get(task.get("deadline_id"))):
            continue
        row = deadline_by_task.get(task_id)
        if row and (row["past"] or is_complete(row["deadline"])):
            continue
        seen.add(task_id)
        result.append((task, row))
    explicit_ids = [task_id for plan in _plans_for(state, now.date()) for task_id in _list(plan.get("candidate_task_ids"))]
    return sorted(result, key=lambda entry: (0, explicit_ids.index(entry[0]["id"]))
                  if entry[0]["id"] in explicit_ids else
                  (1, entry[1]["date"] if entry[1] and entry[1]["date"] else date.max))


def _task_line(task, row, state):
    title = task.get("title") or "任务名称待确认"
    line = f"{task.get('course_id', '')} — {title}"
    if row:
        line += f"；{_deadline_when(row['deadline'])}"
    line += f"；{_status(task)}"
    chapters = task.get("required_chapters")
    if not chapters:
        course = next((c for c in state.get("courses", []) if c.get("id") == task.get("course_id")), {})
        focus = course.get("assignment_focus", {})
        if task.get("id") in _list(focus.get("task_ids")):
            chapters = focus.get("required_chapters")
    if chapters:
        line += "；范围：" + "、".join(str(c) for c in _list(chapters))
    return _text(line)


@study_time.state_timezone
def refresh_plan_state(state, now=None):
    """Refresh stable derived metadata only; caller owns atomic state persistence."""
    moment = local_now(now)
    rows = _deadline_rows(state, moment)
    value = {"schema_version": VERSION, "plan_date": moment.date().isoformat(),
             "timezone": study_time.name(), **_capacity(state, moment.date()),
             "candidate_task_ids": [t["id"] for t, _ in _priorities(state, moment, rows)],
             "past_exam_task_ids_pending_feedback": [r["task"].get("id") for r in rows
                                                     if r["past"] and r["exam"] and r["task"].get("id")],
             "review_in_existing_budget": True, "trigger": "project_message_or_explicit_command",
             "background_run": False}
    state.setdefault("planning", {}).update(value)
    return state["planning"]


@study_time.state_timezone
def _review_lines(state, now):
    sessions = [s for s in state.get("review_sessions", []) if s.get("date") == now.date().isoformat()]
    if not sessions:
        return ["今天复习题待生成；日常复习共15–30分钟，计入原学习预算。"]
    session = sessions[-1]
    items = session.get("items", [])
    done = sum(x.get("status") in {"completed", "answered", "graded", "recorded"} for x in items)
    budget = session.get("budget_minutes")
    label = f"{budget:g}分钟" if _number(budget) else "时长待确认"
    return [f"今天共{len(items)}题，已记录作答{done}题；共用{label}预算，包含在原学习预算内。",
            "打开 [今日复习](<Today Review.md>) 按顺序独立作答；同日继续同一份题包。",
            "记录真实作答后安排后续复测；没有消息时不推送、不运行后台任务。"]


@study_time.state_timezone
def _feedback_lines(state, now):
    feedback = _feedback(state, now.date())
    result = []
    for item in feedback:
        minutes = item.get("actual_minutes")
        actual = f"{minutes:g}分钟" if _number(minutes) else "用时待确认"
        line = f"{item.get('course_id') or '本次学习'}：{_text(item.get('completed'))}；{actual}"
        if item.get("blocker"):
            line += f"；卡点：{_text(item['blocker'])}"
        if item.get("next_step"):
            line += f"；下一步：{_text(item['next_step'])}"
        result.append(line)
    if not result:
        result.append("今天尚无学习结束反馈；未反馈不等于未完成。")
    recent = [x for x in state.get("learning_feedback", []) if x.get("next_step") and
              _day(x.get("date")) and now.date() - timedelta(days=7) <= _day(x["date"]) < now.date()]
    tasks = {t["id"]: t for t in state.get("tasks", [])}
    shown = {x.get("course_id") for x in feedback if x.get("next_step")}
    for item in sorted(recent, key=lambda x: (x["date"], x.get("recorded_at", "")), reverse=True):
        course_id = item.get("course_id")
        if course_id in shown:
            continue
        task_ids = _list(item.get("task_ids"))
        if task_ids and all(is_complete(tasks.get(t)) for t in task_ids):
            continue
        shown.add(course_id)
        result.append(f"{course_id or '本次学习'} 最近反馈（{item['date']}）的下一步：{_text(item['next_step'])}；是否已完成待确认。")
    result.append("结束时直接说：完成了哪些题，大约用了多久，下一步卡在哪里。")
    return result


@study_time.state_timezone
def _metadata(state, now, name):
    return {"version": VERSION, "view": name, "plan_date": now.date().isoformat(),
            "timezone": study_time.name(), "source_revision": state.get("revision"),
            "source_updated_at": state.get("updated_at")}


@study_time.state_timezone
def _wrap(state, now, name, lines):
    marker = json.dumps(_metadata(state, now, name), ensure_ascii=False, separators=(",", ":"))
    return f"<!-- study-plan {marker} -->\n\n" + "\n".join(lines).rstrip() + "\n"


@study_time.state_timezone
def render_views(state, now=None):
    """Pure rendering; never copy an old Today/Weekly document or mutate state."""
    now = local_now(now)
    today = now.date()
    rows = _deadline_rows(state, now)
    priorities = _priorities(state, now, rows)
    future = [r for r in rows if not r["past"] and r["date"]]
    past = [r for r in rows if r["past"]]
    today_lines = [f"# {today.isoformat()} 今日学习计划", "", _capacity_line(state, today), "",
                   "## 当前优先事项", ""]
    today_lines += [f"- {_task_line(t, r, state)}" for t, r in priorities[:5]] or ["- 当前没有可确认的待推进目标；保留已有学习记录，待新任务或学习反馈。"]
    today_lines += ["", "## 今日复习与后续复测", ""] + ["- " + x for x in _review_lines(state, now)]
    today_lines += ["", "## 固定安排", ""]
    today_lines += ["- " + x for x in _events(state, today)] or ["- 没有已记录的本日固定活动；这不代表全天可用。"]
    today_lines += ["- 通勤、用餐、休息按实际安排保留；未知时长不自动填入。", "", "## 学习反馈", ""]
    today_lines += ["- " + x for x in _feedback_lines(state, now)]
    past_exams = [r for r in past if r["exam"]]
    if past_exams:
        today_lines += ["", "## 已过考试日期，待本人反馈", ""]
        today_lines += [f"- {r['deadline'].get('course_id', '')} — {r['deadline'].get('title', '')}：{_deadline_when(r['deadline'])}；参加情况与考后卡点待确认。" for r in past_exams]
    today_lines += ["", "其余节点见 [未来七天](<Weekly View.md>) 和 [后续事项](<Upcoming.md>)。已提交事项不重新排入待办；历史分科计划保留在原日期。"]

    week_end = today + timedelta(days=6)
    week_lines = [f"# {today.isoformat()} 至 {week_end.isoformat()} 学习安排", "",
                  "按固定活动、近期节点和已确认容量查看；未确认容量的日期仅给初步优先级。", "",
                  "| 日期 | 固定安排与节点 | 容量与建议 |", "|---|---|---|"]
    for offset in range(7):
        day = today + timedelta(days=offset)
        commitments = _events(state, day)
        commitments += [f"{r['deadline'].get('course_id', '')} {r['deadline'].get('title', '')}：{_deadline_when(r['deadline'])}；{_status(r['task'])}"
                        for r in future if r["date"] == day]
        week_lines.append(f"| {day.isoformat()} | {_text('；'.join(commitments) or '无已记录固定节点')} | {_text(_capacity_line(state, day))} |")
    week_lines += ["", "## 本周推进顺序", ""]
    week_lines += [f"- {_task_line(t, r, state)}" for t, r in priorities[:5]] or ["- 待最新任务要求或学习反馈确定。"]
    week_lines += ["", "每天复习共15–30分钟，计入当天同一预算；已完成的复习不会因再次刷新而重新加量。未来日期的具体题包到当天按真实进度生成。"]

    upcoming_lines = [f"# 后续事项 · {today.isoformat()}", "", "按学习档案时区显示。只显示尚无完成确认的节点；未知进度不记成未开始或积压。", "",
                      "## 七天后的节点", "", "| 课程与事项 | 时间 | 当前记录 |", "|---|---|---|"]
    later = [r for r in future if r["date"] > week_end]
    upcoming_lines += [f"| {_text(r['deadline'].get('course_id', ''))} — {_text(r['deadline'].get('title', ''))} | {_deadline_when(r['deadline'])} | {_status(r['task'])} |" for r in later]
    if not later:
        upcoming_lines.append("| 暂无已记录节点 | — | — |")
    undated = [r for r in rows if r["date"] is None]
    assessments = [x for x in state.get("undated_assessments", []) if not x.get("date") and not is_complete(x)]
    if undated or assessments:
        upcoming_lines += ["", "## 日期待确认", ""]
        upcoming_lines += [f"- {r['deadline'].get('course_id', '')} — {r['deadline'].get('title', '')}：日期待确认。" for r in undated]
        upcoming_lines += [f"- {x.get('course_id', '')} — {x.get('title') or x.get('event') or '考核'}：日期待确认。" for x in assessments]
    if past:
        upcoming_lines += ["", "## 已过日期，完成情况待核实", "", "这些是待核实记录，不自动计入积压工时，也不推断已经参加考试。", ""]
        upcoming_lines += [f"- {r['deadline'].get('course_id', '')} — {r['deadline'].get('title', '')}：{_deadline_when(r['deadline'])}；{'考试反馈待确认' if r['exam'] else _status(r['task'])}。" for r in past]
    future_sport = []
    for schedule in state.get("schedules", []):
        if schedule.get("type") == "dated_activity_course":
            for event in schedule.get("events", []):
                if _day(event.get("date")) and _day(event["date"]) > week_end and event.get("default_block_for_attendance"):
                    future_sport.append(f"{event['date']} {event.get('start') or '时刻待确认'}{'–' + event['end'] if event.get('end') else ''} {event.get('title') or schedule.get('name', '')}")
    if future_sport:
        upcoming_lines += ["", "## 后续固定活动", ""] + ["- " + x for x in future_sport]
    return {name: _wrap(state, now, name, lines) for name, lines in zip(VIEW_NAMES, (today_lines, week_lines, upcoming_lines))}


@study_time.state_timezone
def check_freshness(state, views, now=None):
    """Check date/source revision and content, including same-day end-time rollover."""
    now = local_now(now)
    expected = render_views(state, now)
    issues = []
    for name in VIEW_NAMES:
        content = views.get(name)
        if content is None:
            issues.append(f"{name}: missing")
            continue
        match = MARKER.search(content)
        try:
            metadata = json.loads(match.group(1)) if match else None
        except (ValueError, TypeError):
            metadata = None
        if metadata != _metadata(state, now, name):
            issues.append(f"{name}: stale_date_or_source")
        elif content.replace("\r\n", "\n") != expected[name]:
            issues.append(f"{name}: stale_content")
    return {"fresh": not issues, "issues": issues}


def write_views(views, output_dir):
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in views.items():
        destination = directory / name
        if destination.exists() and destination.read_text(encoding="utf-8-sig") == text:
            continue
        fd, temporary = tempfile.mkstemp(prefix=".plan-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("render",))
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--at", help="Timezone-aware ISO time for deterministic temporary-state checks")
    parser.add_argument("--check", action="store_true", help="Only check; never write views or state")
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding="utf-8-sig"))
    with study_time.using_state(state):
        moment = local_now(args.at)
    output = args.output_dir or args.state.parent
    if args.check:
        views = {name: (output / name).read_text(encoding="utf-8-sig") for name in VIEW_NAMES if (output / name).exists()}
        result = check_freshness(state, views, moment)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["fresh"] else 1
    write_views(render_views(state, moment), output)
    print(json.dumps({"status": "rendered", "date": moment.date().isoformat(), "views": list(VIEW_NAMES)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
