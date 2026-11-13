"""Message-triggered learning loop: one state commit, then rebuild current views."""
import argparse
import study_time
import json
from pathlib import Path
import sys
import re
import state_io
from snapshot_guard import validate_current_snapshot

import review
import learning_loop
import planning
import coverage
import exam_prep
import exam_sources
import teaching_event


def mock_paper_name(mock):
    course = mock["profile_snapshot"]["course_id"]
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", v) for v in (course, mock["id"])):
        raise ValueError("课程和模拟卷编号不能用于安全文件名")
    return f"../{course}/Practice/{mock['id']}.md"


def view_path(state_path, name):
    destination = (state_path.parent / name).resolve()
    root = state_path.parent.parent.resolve()
    if not destination.is_relative_to(root):
        raise ValueError("视图路径超出学习工作区")
    return destination


@study_time.state_timezone
def feedback_view(state, now):
    day = now.astimezone(study_time.local_zone()).date().isoformat()
    lines = [f"# 学习反馈 · {day}", "", "学习结束后，用一句话告诉我：完成了什么、大约用了多久、卡在哪里。",
             "时间不清楚可以不填；自报完成与学校提交、独立掌握分别记录。", ""]
    entries = [x for x in state.get("learning_feedback", []) if x.get("date") == day]
    if not entries:
        lines += ["今天尚未收到学习反馈。", "", "示例格式：完成了___，大约___分钟，卡在___；下一步想___。"]
    for entry in entries:
        lines += ["## " + (entry.get("course_id") or "本次学习"), "",
                  "- 完成内容：" + str(entry.get("completed") or "待确认"),
                  "- 实际用时：" + (str(entry["actual_minutes"]) + "分钟" if entry.get("actual_minutes") is not None else "未提供"),
                  "- 卡点：" + str(entry.get("blocker") or "未提供"),
                  "- 下一步：" + str(entry.get("next_step") or "待结合实际安排确定"), ""]
    return "\n".join(lines) + "\n"


@study_time.state_timezone
def view_contents(state, now):
    views = planning.render_views(state, now)
    views.update(coverage.render_views(state, now))
    catalog = exam_prep.render_catalog(state, now)
    for mock in state.get("mock_exams", []):
        name = mock_paper_name(mock)
        views[name] = exam_prep.render_mock(state, mock["id"])
        catalog += f"\n[打开 {mock['title']}](<{name}>)\n"
    views["Mock Exams.md"] = catalog
    views["Today Review.md"] = learning_loop.render_today_review(state, now)
    views["Learning Feedback.md"] = feedback_view(state, now)
    mistakes = {item["id"]: item for item in state.get("mistakes", [])}
    lines = ["# 复习记录", "", "当天的具体题目请打开 [今日复习](Today%20Review.md)。",
             "未准备具体新题的条目保留在队列中，由助手核验题目后再安排；不因未做而记作答错。", ""]
    for item in state.get("review_queue", []):
        title = mistakes.get(item.get("mistake_id"), {}).get("display_title", item["id"])
        lines += ["## " + title, ""]
        ready = sum(v.get("verified") is True for v in item.get("practice_variants", []))
        lines += [f"- 已备具体变式：{ready}道" if ready else "- 具体变式待准备。"]
        card = item.get("fsrs", {}).get("card")
        if card:
            lines += ["- 下次复测：" + review.timestamp(card["due"]).astimezone(study_time.local_zone()).strftime("%Y-%m-%d %H:%M")
                      + "（学习时区；下次聊天时安排）"]
        else:
            lines += ["- 尚未启动间隔复习；保留原有学习证据，等待独立作答。"]
        lines += [""]
    views["Review Queue.md"] = "\n".join(lines)
    return views


@study_time.state_timezone
def write_views(state, state_path, now):
    """Views are disposable; never overwrite a newer state or claim stale views are current."""
    expected = state_path.read_bytes()
    disk = json.loads(expected.decode("utf-8-sig"))
    if disk != state:
        raise ValueError("学习状态已变化，请读取最新版后重建计划")
    views = view_contents(state, now)
    for name, text in views.items():
        if state_path.read_bytes() != expected:
            raise ValueError("生成期间学习状态已变化，请重新运行 daily.py run")
        destination = view_path(state_path, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        state_io.atomic_text(destination, text, skip_unchanged=True)
    if state_path.read_bytes() != expected:
        raise ValueError("学习状态已变化，当前视图需重新生成")
    sync_mock_notebooks(state, state_path)
    if state_path.read_bytes() != expected:
        raise ValueError("错题本生成期间学习状态已变化，请重新运行 daily.py run")
    return list(views)


NOTEBOOK_START = "<!-- BEGIN MOCK EXAM MISTAKES -->"
NOTEBOOK_END = "<!-- END MOCK EXAM MISTAKES -->"


@study_time.state_timezone
def sync_mock_notebooks(state, state_path, check=False):
    issues = []
    for course in state.get("courses", []):
        section = exam_prep.render_mock_notebook_section(state, course["id"])
        if not section:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", course["id"]):
            raise ValueError("课程编号无效")
        destination = view_path(state_path, f"../{course['id']}/错题本/记录.md")
        old = destination.read_text(encoding="utf-8") if destination.exists() else f"# {course['id']} 错题本\n"
        block = NOTEBOOK_START + "\n" + section.rstrip() + "\n" + NOTEBOOK_END
        pattern = re.compile(re.escape(NOTEBOOK_START) + r".*?" + re.escape(NOTEBOOK_END), re.S)
        found = pattern.search(old)
        if check:
            if not found or found.group() != block:
                issues.append(course["id"] + " 错题本模拟题记录待更新")
        else:
            content = pattern.sub(lambda match: block, old) if found else old.rstrip() + "\n\n" + block + "\n"
            if content != old:
                destination.parent.mkdir(parents=True, exist_ok=True)
                state_io.atomic_text(destination, content, skip_unchanged=True)
    return issues


@state_io.locked
def execute(state_path, command, now, minutes=None, course=None, payload=None, snapshot=None, operation_id=None):
    state_path = Path(state_path)
    # A crashed material move must be reconciled before any normal state write
    # can change the hash used by its recovery journal.
    state_io.prepare_state(state_path)
    original = state_path.read_bytes()
    state = json.loads(original.decode("utf-8-sig"))
    if snapshot is not None:
        # Before idempotent replays too: even view/receipt rewrites must not
        # silently acknowledge stale school evidence after a newer check.
        validate_current_snapshot(state_path, snapshot, state)
    if operation_id is None and payload:
        operation_id = (payload.get("attempt_id") or payload.get("feedback_id")
                        or payload.get("event_id"))
    operation = None
    if operation_id:
        key = state_io.digest(str(operation_id))
        request = state_io.digest(dict(command=command, minutes=minutes, course=course, payload=payload, snapshot=snapshot))
        operation = (key, request)
        old = state.get("operation_receipts", {}).get(key)
        if old:
            if old["request_sha256"] != request:
                raise ValueError("同一操作编号内容冲突；未重复保存")
            files = write_views(state, state_path, now)
            state_io.atomic(state_path.parent / "Runtime" / "operations" / (key + ".json"),
                            {"phase": "complete", "revision": state.get("revision")})
            return {**old["result"], "duplicate": True, "views_updated": files}
    with study_time.using_state(state):
        return _execute_loaded(state, state_path, original, command, now, minutes, course, payload,
                               snapshot, operation, operation_id)


def _execute_teaching_event(state, state_path, original, now, payload, operation=None, operation_id=None):
    """One small teaching event: read latest -> merge -> one atomic save -> one view rebuild.

    The event only supplies this turn's change. Plan metadata is refreshed inside
    the same transaction, so a cross-day save and a teaching change are committed
    together. A merged state identical to the latest one is a real no-op: no save
    and no view rebuild. Retries are handled by ``execute`` receipts + the
    ``operations/<key>.json`` two-phase marker, exactly like other events.
    """
    if payload is None:
        raise ValueError("teaching-event 需要标准输入 JSON 事件")
    meta = teaching_event.merge(state, payload, now, operation_id=operation_id)
    merged = meta['state']
    plan_before = json.dumps(merged.get("planning"), sort_keys=True, ensure_ascii=False)
    planning.refresh_plan_state(merged, now)
    if json.dumps(merged.get("planning"), sort_keys=True, ensure_ascii=False) != plan_before:
        meta['changed_fields'] = list(meta['changed_fields']) + ["planning"]
    if state_io.same_json(state, merged):
        return teaching_event.describe_result(meta, status="unchanged",
                                              revision=state.get("revision"),
                                              state_written=False, views_updated=[])
    # review.save does not validate drafts; the full guard set runs here first.
    state_io.validate_draft(state, merged)
    revision = state.get("revision", 0) + 1
    if operation:
        key, request = operation
        receipt = teaching_event.describe_result(meta, status="recorded", revision=revision,
                                                 state_written=True, views_updated=[])
        merged.setdefault("operation_receipts", {})[key] = {
            "request_sha256": request, "result": receipt, "committed_at": now.isoformat()}
    review.save(state_path, merged, original)
    marker = None
    if operation:
        marker = state_path.parent / "Runtime" / "operations" / (operation[0] + ".json")
        state_io.atomic(marker, {"phase": "views_pending", "revision": merged.get("revision")})
    with state_io.timed(state_path, "view_generation"):
        files = write_views(merged, state_path, now)
    if marker:
        state_io.atomic(marker, {"phase": "complete", "revision": merged.get("revision")})
    return teaching_event.describe_result(meta, status="recorded",
                                          revision=merged.get("revision"),
                                          state_written=True, views_updated=files)


def _execute_loaded(state, state_path, original, command, now, minutes, course, payload, snapshot,
                    operation=None, operation_id=None):
    if command == "check":
        expected = view_contents(state, now)
        # Match the writer: a legacy BOM or CRLF does not make a page stale.
        views = {name: view_path(state_path, name).read_text(encoding="utf-8-sig")
                 if view_path(state_path, name).exists() else ""
                 for name in expected}
        result = planning.check_freshness(state, views, now, expected=expected)
        for name, text in expected.items():
            if name not in planning.VIEW_NAMES and views[name].replace("\r\n", "\n") != text:
                result["issues"].append(name + ": stale_or_missing")
        result["fresh"] = not result["issues"]
        result["issues"].extend(sync_mock_notebooks(state, state_path, check=True))
        result["fresh"] = not result["issues"]
        return result
    if command == "exam-intake":
        if snapshot is None:
            raise ValueError("考试说明核读需要本轮 Moodle snapshot")
        return {"collection_complete": snapshot.get("collection_complete"),
                "candidates": exam_sources.candidates(snapshot, state)}
    if command == "teaching-event":
        return _execute_teaching_event(state, state_path, original, now, payload, operation, operation_id)
    before = json.dumps(state, sort_keys=True, ensure_ascii=False)
    # Upgrade old or minimal archives gently; no learner evidence is invented.
    review.initialize(state)
    for key in ("courses", "tasks", "deadlines", "materials", "schedules", "mistakes", "review_queue",
                "review_sessions", "review_attempts", "learning_feedback", "exam_profiles", "mock_exams", "mock_attempts"):
        state.setdefault(key, [])
    state["settings"].setdefault("learning_loop", {}).update(enabled=True)
    state["settings"]["learning_loop"].setdefault("default_review_minutes", 15)
    state["settings"].setdefault("exam_preparation", {}).setdefault("enabled", True)
    result = {}
    if snapshot is not None:
        result = exam_sources.sync_profiles(state, snapshot, now)
    if command == "attempt":
        result = learning_loop.record_attempt(state, payload)
    elif command == "feedback":
        result = learning_loop.record_feedback(state, payload)
    elif command == "answer-shown":
        result = learning_loop.mark_answer_shown(state, payload)
    elif command == "exam-profile":
        if snapshot is None:
            raise ValueError("登记考试要求需要本轮 Moodle snapshot")
        result = exam_sources.register_profile(state, payload, snapshot, now)
    elif command == "mock-register":
        result = exam_prep.register_mock(state, payload, now)
    elif command == "mock-attempt":
        result = exam_prep.record_mock_attempt(state, payload, now)
    elif command == "mock-answers":
        result = exam_prep.mark_mock_answers_shown(state, payload, now)
        result["answer_key"] = exam_prep.render_mock(state, payload["mock_id"], answers=True)
    elif command == "mock-answer-shown":
        result = exam_prep.mark_mock_answers_shown(state, payload, now)
    elif command == "exam-sync" and snapshot is None:
        raise ValueError("核对考试说明需要本轮 Moodle snapshot")
    if command in {"run", "attempt", "feedback", "answer-shown", "mock-attempt"}:
        day = now.astimezone(study_time.local_zone()).date().isoformat()
        existing = next((s for s in state.get("review_sessions", []) if s["date"] == day), None)
        requested = minutes if minutes is not None else (existing["budget_minutes"] if existing else
                    state.get("settings", {}).get("learning_loop", {}).get("default_review_minutes", 15))
        budget = planning.review_budget_limit(state, now, requested)
        remaining = planning.remaining_study_minutes(state, now)
        session = learning_loop.prepare(state, now, budget, course, remaining_minutes=remaining)
    else:
        session = None
    planning.refresh_plan_state(state, now)
    if operation:
        key, request = operation
        state.setdefault("operation_receipts", {})[key] = {"request_sha256": request, "result": result,
                                                           "committed_at": now.isoformat()}
    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        review.save(state_path, state, original)
    if operation:
        marker = state_path.parent / "Runtime" / "operations" / (operation[0] + ".json")
        state_io.atomic(marker, {"phase": "views_pending", "revision": state.get("revision")})
    with state_io.timed(state_path, "view_generation"):
        files = write_views(state, state_path, now)
    if operation:
        state_io.atomic(marker, {"phase": "complete", "revision": state.get("revision")})
    return {**result, "date": now.astimezone(study_time.local_zone()).date().isoformat(),
            "views_updated": files, "review": session, "mastery_inferred": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=review.DEFAULT_STATE)
    parser.add_argument("--at", help="带时区的核对时间；日常运行省略")
    parser.add_argument("command", choices=["run", "attempt", "feedback", "answer-shown", "render", "check",
        "exam-intake", "exam-sync", "exam-profile", "mock-register", "mock-attempt", "mock-answers",
        "mock-answer-shown", "teaching-event"])
    parser.add_argument("--minutes", type=int, help="当日首次预算或明确减量；省略则沿用当天已确认预算")
    parser.add_argument("--course")
    parser.add_argument("--operation-id", help="同一事件重试必须沿用编号")
    parser.add_argument("--snapshot", type=Path, help="本轮 Moodle 只读采集文件；核对来源变化")
    args = parser.parse_args()
    payload = json.load(sys.stdin) if args.command in {"attempt", "feedback", "answer-shown", "exam-profile",
                                                     "mock-register", "mock-attempt", "mock-answers",
                                                     "mock-answer-shown", "teaching-event"} else None
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig")) if args.snapshot else None
    result = execute(args.state, args.command, review.timestamp(args.at), args.minutes, args.course, payload, snapshot, args.operation_id)
    print(json.dumps(result, ensure_ascii=False))
    if args.command == "check" and not result.get("fresh"):
        raise SystemExit(1)


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
