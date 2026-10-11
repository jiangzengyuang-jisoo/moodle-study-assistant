"""Concrete review questions, evidence-backed attempts and brief learning feedback.

No clock, background job, submission update or mastery inference is performed here.
The first prepare call fixes the learning profile calendar day's shared review budget; repeated
calls reuse it. Answers stay in practice_variants and never enter preview output.
"""
import argparse
import study_time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys

import review

HK = review.HK
DEFAULT_STATE = review.DEFAULT_STATE
STOPPED = {"Completed", "completed", "suspended", "archived"}


def _now(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return value.astimezone(timezone.utc)
    return review.timestamp(value)


def _minutes(value, *, nullable=False):
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("分钟数必须为非负有限数字，未知实际用时请留 null")
    return value


def _text(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("缺少真实记录：" + name)
    return value


def _evidence(payload):
    value = payload.get("evidence")
    if not isinstance(value, (str, dict)) or not value:
        raise ValueError("evidence 必须保留非空原始对话或证据定位")
    if isinstance(value, str) and not value.strip():
        raise ValueError("evidence 不能为空")
    return deepcopy(value)


@study_time.state_timezone
def _due_items(state, now, course=None):
    # Same due/priority rules as review.due, before its legacy prompt selection
    # and estimate limit, so unprepared legacy entries cannot hide usable ones.
    today = now.astimezone(study_time.local_zone()).date()
    items = []
    for item in state.get("review_queue", []):
        if (course and item.get("course_id") != course) or item.get("status") in STOPPED:
            continue
        card = item.get("fsrs", {}).get("card")
        if card and review.timestamp(card["due"]) > now:
            continue
        if not card and item.get("due_date") and date.fromisoformat(item["due_date"]) > today:
            continue
        items.append(item)
    return sorted(items, key=lambda item: (
        0 if item.get("priority") == "High" else 1,
        item.get("due_date") or "9999-12-31", item.get("review_order", 999), item["id"]))


def _valid_variant(variant):
    source = variant.get("source")
    cost = variant.get("estimated_minutes")
    return (variant.get("verified") is True and isinstance(variant.get("id"), str)
            and bool(variant["id"].strip()) and isinstance(variant.get("prompt"), str)
            and bool(variant["prompt"].strip()) and isinstance(variant.get("answer_check"), list)
            and bool(variant["answer_check"])
            and all(isinstance(a, str) and a.strip() for a in variant["answer_check"])
            and isinstance(source, dict) and bool(source.get("material_id"))
            and isinstance(source.get("pages"), list) and bool(source.get("detail"))
            and isinstance(cost, (int, float)) and not isinstance(cost, bool)
            and math.isfinite(cost) and cost > 0)


def _budget(session):
    committed = sum((item.get("actual_minutes") if item.get("actual_minutes") is not None
                     else item["reserved_minutes"]) for item in session["items"]
                    if not item.get("budget_exempt"))
    actual = [item["actual_minutes"] for item in session["items"]
              if item.get("actual_minutes") is not None and not item.get("budget_exempt")]
    return {"budget_minutes": session["budget_minutes"], "committed_minutes": committed,
            "remaining_minutes": max(0, session["budget_minutes"] - committed),
            "actual_minutes_known": sum(actual) if actual else None,
            "answered_minutes_unknown": sum(item["status"] == "answered" and item.get("actual_minutes") is None
                                            and not item.get("budget_exempt")
                                            for item in session["items"])}


@study_time.state_timezone
def _session_for(state, now, minutes=15):
    today = now.astimezone(study_time.local_zone()).date().isoformat()
    sessions = state.setdefault("review_sessions", [])
    session = next((entry for entry in sessions if entry["date"] == today), None)
    if session is None:
        session = {"id": "REVIEW-DAY-" + today, "date": today,
                   "created_at": now.astimezone(study_time.local_zone()).isoformat(), "budget_minutes": minutes,
                   "budget_basis": "当天首次准备的总预算；重复运行和切换课程均不叠加",
                   "items": [], "needs_preparation": []}
        sessions.append(session)
    return session


def _reduce_budget(session, minutes, now):
    """A later capacity reduction may defer unstarted work, never erase actuals."""
    if minutes >= session["budget_minutes"]:
        return
    session["budget_minutes"] = minutes
    fixed = sum((item.get("actual_minutes") if item.get("actual_minutes") is not None
                 else item["reserved_minutes"]) for item in session["items"]
                if item["status"] != "pending" and not item.get("budget_exempt"))
    remaining = max(0, minutes - fixed)
    for item in session["items"]:
        if item["status"] != "pending":
            continue
        if item["reserved_minutes"] <= remaining:
            remaining -= item["reserved_minutes"]
        else:
            item.update(status="deferred", budget_exempt=True,
                        deferred_at=now.astimezone(study_time.local_zone()).isoformat(),
                        deferred_reason="今日预算减量；未作答，不记失败")


@study_time.state_timezone
def _next_review(state, review_id):
    item = next((q for q in state.get("review_queue", []) if q["id"] == review_id), {})
    card = item.get("fsrs", {}).get("card")
    return review.timestamp(card["due"]).astimezone(study_time.local_zone()).isoformat() if card else None


@study_time.state_timezone
def _public_session(state, session):
    # Explicit allowlist: no answer_check, submitted answer, feedback or raw source
    # detail can accidentally enter the question page or a prepare/today preview.
    items = []
    for item in session["items"]:
        visible = {key: item[key] for key in (
            "review_id", "variant_id", "course_id", "title", "status", "reserved_minutes")}
        visible["estimated_minutes"] = item["reserved_minutes"]
        if item["status"] == "pending":
            visible["prompt"] = item["prompt"]
        visible["next_review_at"] = _next_review(state, item["review_id"])
        items.append(visible)
    pending = any(item["status"] == "pending" for item in items)
    needs = session.get("needs_preparation", [])
    status = "ready" if pending else "paused_for_budget" if any(i["status"] == "deferred" for i in items) else "needs_preparation" if needs else "waiting_for_retest"
    return {"status": status,
            "session_id": session["id"], "date": session["date"], **_budget(session),
            "items": items, "needs_preparation": deepcopy(needs),
            "mastery_updated": False}


@study_time.state_timezone
def prepare(state, now, minutes=15, course=None):
    """Reuse today's shared budget and select verified, unseen, due variants.

    An unfinished, unexposed question carries forward without using a new variant.
    Old package links are retained as history; lateness creates no failure.
    Subsequent calls may fill unused budget when a new FSRS retest becomes due.
    They may reduce the budget and defer unstarted questions, but cannot increase
    it or reactivate today's deferred questions, including when course changes.
    """
    now = _now(now)
    _minutes(minutes)
    if minutes > 30:
        raise ValueError("每日总复习预算不得超过 30 分钟")
    session = _session_for(state, now, minutes)
    _reduce_budget(session, minutes, now)
    sessions = state["review_sessions"]
    used_ids = {(item["review_id"], item["variant_id"])
                for old in sessions for item in old["items"]}
    used_prompts = {(item["review_id"], item["prompt"].strip())
                    for old in sessions for item in old["items"]}
    pending_ids = {item["review_id"] for item in session["items"] if item["status"] in {"pending", "deferred"}}
    remaining = _budget(session)["remaining_minutes"]
    missing = []
    mistakes = {m["id"]: m for m in state.get("mistakes", [])}
    for item in _due_items(state, now, course):
        if item["id"] in pending_ids:
            continue
        carry = next(((old_session, old_item) for old_session in reversed(sessions)
                      if old_session["date"] < session["date"]
                      for old_item in old_session["items"]
                      if old_item["review_id"] == item["id"]
                      and old_item["status"] in {"pending", "deferred"} and not old_item.get("answer_shown_at")), None)
        if carry and carry[1]["reserved_minutes"] <= remaining:
            old_session, old_item = carry
            chosen = deepcopy(old_item)
            chosen.update(carried_from_session_id=old_session["id"],
                          prepared_at=now.astimezone(study_time.local_zone()).isoformat(), budget_exempt=False, status="pending")
            old_item.update(status="carried_forward", carried_to_session_id=session["id"], budget_exempt=True)
            session["items"].append(chosen)
            remaining -= chosen["reserved_minutes"]
            continue
        if carry:
            continue
        variants = [variant for variant in item.get("practice_variants", [])
                    if _valid_variant(variant)
                    and (item["id"], variant["id"]) not in used_ids
                    and (item["id"], variant["prompt"].strip()) not in used_prompts
                    and not variant.get("answer_shown_at")]
        if not variants:
            missing.append({"review_id": item["id"], "course_id": item.get("course_id"),
                            "reason": "需要核验过且未展示答案的新变式"})
            continue
        variant = next((v for v in variants if v["estimated_minutes"] <= remaining), None)
        if variant is None:
            continue
        chosen = {"review_id": item["id"], "variant_id": variant["id"],
                  "course_id": item.get("course_id"),
                  "title": mistakes.get(item.get("mistake_id"), {}).get("display_title", item["id"]),
                  "prompt": variant["prompt"], "source": deepcopy(variant["source"]),
                  "reserved_minutes": variant["estimated_minutes"], "status": "pending",
                  "assigned_at": now.astimezone(study_time.local_zone()).isoformat(), "actual_minutes": None}
        session["items"].append(chosen)
        remaining -= chosen["reserved_minutes"]
    session["needs_preparation"] = missing
    return _public_session(state, session)


@study_time.state_timezone
def _find_session_item(state, payload):
    session = next((s for s in state.get("review_sessions", []) if s["id"] == payload["session_id"]), None)
    if session is None:
        raise ValueError("session_id 不存在，请先准备当日复习题")
    item = next((i for i in session["items"] if i["review_id"] == payload["review_id"]
                 and i["variant_id"] == payload["variant_id"]), None)
    if item is None:
        raise ValueError("作答未关联该日包的具体变式")
    return session, item


@study_time.state_timezone
def mark_answer_shown(state, payload):
    """Call when a solution is shown before an attempt, without inventing an answer.

    The solution itself is not returned. This small audit event prevents future
    independent ratings for the same variant (or the same prompt under a new ID).
    """
    for key in ("session_id", "review_id", "variant_id"):
        _text(payload, key)
    evidence = _evidence(payload)
    _, item = _find_session_item(state, payload)
    if item.get("answer_shown_at"):
        return {"status": "duplicate", "mastery_updated": False}
    shown = _now(payload.get("shown_at"))
    if shown < _now(item["assigned_at"]) or shown > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("展示时间不能早于出题或晚于当前时间")
    for session in state["review_sessions"]:
        for linked in session["items"]:
            if linked["review_id"] == item["review_id"] and (linked["variant_id"] == item["variant_id"]
                    or linked["prompt"].strip() == item["prompt"].strip()):
                linked["answer_shown_at"] = shown.astimezone(study_time.local_zone()).isoformat()
                linked["answer_shown_evidence"] = deepcopy(evidence)
                if linked["status"] != "answered":
                    linked["status"] = "explained"
    return {"status": "recorded", "mastery_updated": False}


@study_time.state_timezone
def record_attempt(state, payload):
    """Preserve a real response and rate FSRS only for an independent attempt."""
    required = ("attempt_id", "session_id", "review_id", "variant_id", "answer", "feedback")
    request = {key: _text(payload, key) for key in required}
    for flag in ("independent", "answer_seen_before_attempt"):
        if not isinstance(payload.get(flag), bool):
            raise ValueError(flag + " 必须明确为 true 或 false")
        request[flag] = payload[flag]
    request.update(evidence=_evidence(payload),
                   actual_minutes=_minutes(payload.get("actual_minutes"), nullable=True),
                   rating=payload.get("rating"))
    if payload.get("reviewed_at"):
        request["reviewed_at"] = _now(payload["reviewed_at"]).astimezone(study_time.local_zone()).isoformat()
    for old in state.get("review_attempts", []):
        if old["attempt_id"] == request["attempt_id"]:
            if old["request"] != request:
                raise ValueError("同一 attempt_id 的内容冲突，未覆盖原记录")
            return {"status": "duplicate", "review_id": old["review_id"],
                    "next_review_at": old.get("next_review_at"), "mastery_updated": False}
    session, item = _find_session_item(state, request)
    if item["status"] not in {"pending", "explained", "carried_forward", "deferred"}:
        raise ValueError("该日包题目已作答；后续复测请准备到期的新变式")
    now = _now(payload.get("reviewed_at"))
    if now < _now(item["assigned_at"]):
        raise ValueError("作答时间不能早于题目准备时间")
    if now > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("不能记录未来的作答")
    queue_item = next((q for q in state.get("review_queue", []) if q["id"] == item["review_id"]), None)
    if queue_item is None:
        raise ValueError("关联复习项目已不存在")
    exposed = any(i.get("answer_shown_at")
                  and i["review_id"] == item["review_id"]
                  and (i["variant_id"] == item["variant_id"] or i["prompt"].strip() == item["prompt"].strip())
                  for s in state.get("review_sessions", []) for i in s["items"])
    exposed = exposed or any(v.get("answer_shown_at") and
                             (v["id"] == item["variant_id"] or v["prompt"].strip() == item["prompt"].strip())
                             for v in queue_item.get("practice_variants", []))
    if request["independent"]:
        if request["answer_seen_before_attempt"] or exposed:
            raise ValueError("这道题的答案已经展示，不能记为独立复测；请记录辅助练习并另选新变式")
        if request["rating"] not in review.RATINGS:
            raise ValueError("独立作答须依据真实表现填写 again/hard/good/easy")
        card = queue_item.get("fsrs", {}).get("card")
        if card and now < review.timestamp(card["due"]):
            raise ValueError("尚未到该项目下次复测时间；请保留真实练习为辅助记录，不提前评级")
        result = review.record(state, {"review_id": item["review_id"], "attempt_id": request["attempt_id"],
            "rating": request["rating"], "prompt": item["prompt"], "answer": request["answer"],
            "feedback": request["feedback"], "evidence": request["evidence"] if isinstance(request["evidence"], str)
            else json.dumps(request["evidence"], ensure_ascii=False, sort_keys=True),
            "independent": True, "reviewed_at": now.astimezone(study_time.local_zone()).isoformat()})
    else:
        if request["rating"] is not None:
            raise ValueError("辅助练习不填写 FSRS rating")
        result = {"status": "recorded", "next_review_at": _next_review(state, item["review_id"])}
    attempt = {**deepcopy(request), "request": request,
               "prompt": item["prompt"], "source": deepcopy(item["source"]),
               "reviewed_at": now.astimezone(study_time.local_zone()).isoformat(),
               "fsrs_rated": request["independent"], "next_review_at": result.get("next_review_at"),
               "mastery_updated": False}
    state.setdefault("review_attempts", []).append(attempt)
    # A response to yesterday's page is still a real response. Preserve its source
    # linkage while charging only the true answer day, never both daily packages.
    budget_session = _session_for(state, now)
    budget_item = next((i for i in budget_session["items"] if i["review_id"] == item["review_id"]
                        and i["variant_id"] == item["variant_id"]), None)
    if budget_item is None:
        budget_item = deepcopy(item)
        budget_item["carried_from_session_id"] = session["id"]
        budget_session["items"].append(budget_item)
    for linked_session in state["review_sessions"]:
        for linked in linked_session["items"]:
            if linked["review_id"] == item["review_id"] and linked["variant_id"] == item["variant_id"]:
                charged = linked is budget_item
                linked.update(status="answered", attempt_id=request["attempt_id"],
                    actual_minutes=request["actual_minutes"] if charged else None,
                    budget_exempt=not charged, budget_session_id=budget_session["id"],
                    answered_at=attempt["reviewed_at"], answer_shown_at=attempt["reviewed_at"],
                    answer_shown_evidence="已记录作答后的核对反馈")
    return {"status": "recorded", "review_id": item["review_id"],
            "fsrs_rated": request["independent"], "next_review_at": result.get("next_review_at"),
            "needs_new_variant": True, "mastery_updated": False, **_budget(budget_session)}


@study_time.state_timezone
def record_feedback(state, payload):
    """Record a brief user report without changing submission or mastery state."""
    request = {"feedback_id": _text(payload, "feedback_id"),
               "completed": _text(payload, "completed"), "evidence": _evidence(payload),
               "actual_minutes": _minutes(payload.get("actual_minutes"), nullable=True)}
    time_scope = payload.get("time_scope", "unknown")
    if time_scope not in {"session", "cumulative", "unknown"}:
        raise ValueError("time_scope 使用 session/cumulative/unknown；不知道是否重叠时用 unknown")
    request["time_scope"] = time_scope
    for field in ("blocker", "next_step", "course_id"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(field + " 应为文字或 null")
        request[field] = value
    task_ids = payload.get("task_ids", [])
    if not isinstance(task_ids, list) or not all(isinstance(x, str) and x for x in task_ids):
        raise ValueError("task_ids 应为已有任务编号列表")
    known_courses = {c["id"] for c in state.get("courses", [])}
    if request["course_id"] is not None and request["course_id"] not in known_courses:
        raise ValueError("course_id 不在现有课程中")
    known_tasks = {task["id"] for task in state.get("tasks", [])}
    if any(task_id not in known_tasks for task_id in task_ids):
        raise ValueError("task_ids 含不存在的任务")
    request["task_ids"] = list(task_ids)
    if payload.get("recorded_at"):
        request["recorded_at"] = _now(payload["recorded_at"]).astimezone(study_time.local_zone()).isoformat()
    if payload.get("date"):
        request["date"] = date.fromisoformat(payload["date"]).isoformat()
    for old in state.get("learning_feedback", []):
        if old["feedback_id"] == request["feedback_id"]:
            if old["request"] != request:
                raise ValueError("同一 feedback_id 的内容冲突，未覆盖原记录")
            return {"status": "duplicate", "feedback_id": request["feedback_id"], "mastery_updated": False}
    now = _now(payload.get("recorded_at"))
    if now > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("不能记录未来的学习反馈")
    feedback_date = request.get("date", now.astimezone(study_time.local_zone()).date().isoformat())
    if date.fromisoformat(feedback_date) > now.astimezone(study_time.local_zone()).date():
        raise ValueError("反馈学习日期不能晚于记录日期")
    entry = {**deepcopy(request), "request": request, "date": feedback_date,
             "recorded_at": now.astimezone(study_time.local_zone()).isoformat(),
             "submission_updated": False, "mastery_updated": False,
             "time_basis": "用户自报；与答题用时可能重合，不自动相加"}
    state.setdefault("learning_feedback", []).append(entry)
    return {"status": "recorded", "feedback_id": request["feedback_id"],
            "submission_updated": False, "mastery_updated": False}


@study_time.state_timezone
def render_today_review(state, now):
    """Render only today's live package; never show old questions as today's work."""
    now = _now(now)
    today = now.astimezone(study_time.local_zone()).date().isoformat()
    session = next((s for s in state.get("review_sessions", []) if s["date"] == today), None)
    lines = ["# 今日复习｜" + today, "", "先独立作答，再核对。复习计入当天原有预算；未做不会记为答错。", ""]
    if session is None:
        return "\n".join(lines + ["今天尚未准备复习题；请先生成当日复习包。", ""])
    result = _public_session(state, session)
    pending_minutes = sum(x["reserved_minutes"] for x in result["items"] if x["status"] == "pending")
    lines += [f"今日总预算：{result['budget_minutes']:g} 分钟；待答题已预留 {pending_minutes:g} 分钟；可额外加题 {result['remaining_minutes']:g} 分钟。", ""]
    number = 0
    for item in result["items"]:
        if item["status"] == "pending":
            number += 1
            lines += [f"## {number}. {item['course_id']} · {item['title']}", "",
                      item["prompt"], "", f"预计 {item['estimated_minutes']:g} 分钟。", ""]
        elif item["status"] == "explained":
            lines += [f"已讲解：{item['course_id']} · {item['title']}。待新变式独立验证。", ""]
        elif item["status"] == "carried_forward":
            lines += [f"已承接到后续日包：{item['course_id']} · {item['title']}。", ""]
        elif item["status"] == "deferred":
            lines += [f"今日减量暂缓：{item['course_id']} · {item['title']}。未作答，不记失败。", ""]
        else:
            lines += [f"已记录：{item['course_id']} · {item['title']}。"]
            if item["next_review_at"]:
                scheduled = review.timestamp(item["next_review_at"]).astimezone(study_time.local_zone())
                label = "复测已到期，可在剩余预算内准备新题" if scheduled <= now else "下次复测"
                lines += [f"{label}：{scheduled:%Y-%m-%d %H:%M}（学习时区）。"]
            else:
                lines += ["辅助练习已保留，待新变式独立检查；尚未更新复习间隔。"]
            lines += [""]
    if not number:
        lines += ["当前没有待答的新题；根据下次复测时间与当天剩余预算继续。", ""]
    if result["needs_preparation"]:
        lines += ["部分到期项目还需要准备并核验新变式，暂不作为可答题目。", ""]
    lines += ["完成后可直接回复：做了哪些题、大约用了多久、卡在哪里、下一步想继续什么。",
              "不知道用时可留空；自报完成不会自动标成已提交或已掌握。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("--at")
    command.add_argument("--minutes", type=float)
    command.add_argument("--course")
    commands.add_parser("attempt")
    commands.add_parser("feedback")
    commands.add_parser("answer-shown")
    command = commands.add_parser("today")
    command.add_argument("--at")
    args = parser.parse_args()
    if args.command == "today":
        state = json.loads(args.state.read_text(encoding="utf-8-sig"))
        print(render_today_review(state, _now(args.at)))
        return
    # Route every mutating CLI through the same capacity check and final state
    # commit. A direct legacy module invocation must not bypass daily budgets.
    import daily
    preparing = args.command == "prepare"
    output = daily.execute(args.state, "run" if preparing else args.command,
                           _now(args.at) if preparing else _now(None),
                           args.minutes if preparing else None, args.course if preparing else None,
                           None if preparing else json.load(sys.stdin))
    if preparing:
        output = output["review"]
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
