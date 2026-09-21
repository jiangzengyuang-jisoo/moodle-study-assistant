"""FSRS integration for the existing shared study state. No background process."""
import argparse
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import tempfile

from fsrs import Card, Rating, Scheduler

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / "Study" / "_System" / "state.json"
HK = timezone(timedelta(hours=8))
RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}
LABELS = {"again": "未独立答出", "hard": "独立答出但困难", "good": "独立答出", "easy": "独立轻松答出"}


def timestamp(value=None):
    result = datetime.fromisoformat(value) if value else datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("时间必须包含时区，例如 +08:00")
    return result.astimezone(timezone.utc)


def scheduler(state):
    config = state["settings"]["spaced_repetition"]["scheduler"]
    return Scheduler.from_dict(config)


def initialize(state):
    settings = state.setdefault("settings", {})
    if "spaced_repetition" not in settings:
        settings["spaced_repetition"] = {
            "engine": "py-fsrs", "scheduler": Scheduler(enable_fuzzing=False).to_dict(),
            "timezone": "Asia/Hong_Kong", "daily_minutes": [15, 30],
            "real_independent_attempts_only": True,
            "mastery_is_not_inferred_from_schedule": True,
        }
    state.setdefault("integrations", {})["study_tools"] = {
        "status": "configured", "fsrs_version": version("fsrs"), "sympy_version": version("sympy"),
        "review_entry": "tools/study/review.py",
        "math_entry": "tools/study/math_check.py",
        "instructions": "docs/TOOLS.md", "local_only": True,
    }


def record(state, payload):
    required = ("review_id", "attempt_id", "rating", "prompt", "answer", "feedback", "evidence")
    for key in required:
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"缺少真实作答信息：{key}")
    if payload.get("independent") is not True or payload["rating"] not in RATINGS:
        raise ValueError("仅记录真实独立尝试；rating 使用 again/hard/good/easy")
    evidence = {key: payload[key] for key in required}
    evidence["independent"] = True
    # A retry must neither produce a second rating nor silently replace evidence.
    for item in state["review_queue"]:
        for old in item.get("fsrs", {}).get("history", []):
            if old["attempt_id"] == payload["attempt_id"]:
                if any(old.get(key) != value for key, value in evidence.items()):
                    raise ValueError("同一 attempt_id 的内容冲突，未覆盖原记录")
                if payload.get("reviewed_at") and timestamp(payload["reviewed_at"]) != timestamp(old["reviewed_at"]):
                    raise ValueError("同一 attempt_id 的作答时间冲突")
                return {"status": "duplicate", "review_id": item["id"]}
    item = next((x for x in state["review_queue"] if x["id"] == payload["review_id"]), None)
    if item is None:
        raise ValueError("复习项目不存在；先关联已有错题或学习问题")
    if not any(m["id"] == item.get("mistake_id") for m in state.get("mistakes", [])):
        raise ValueError("缺少关联的本人错题或学习问题")
    now = timestamp(payload.get("reviewed_at"))
    if now > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("不能记录未来的作答")
    history = item.get("fsrs", {}).get("history", [])
    card_data = item.get("fsrs", {}).get("card")
    card = Card.from_dict(card_data) if card_data else Card(due=now)
    if card.last_review and now <= card.last_review:
        raise ValueError("作答时间必须晚于该项目上一条记录")
    card, log = scheduler(state).review_card(card, RATINGS[payload["rating"]], review_datetime=now)
    evidence.update(reviewed_at=now.astimezone(HK).isoformat(), log=log.to_dict())
    item["fsrs"] = {"card": card.to_dict(), "history": history + [evidence]}
    item["due_date"] = card.due.astimezone(HK).date().isoformat()
    item["status"] = "scheduled_review"
    item["updated_at"] = now.astimezone(HK).isoformat()
    return {"status": "recorded", "review_id": item["id"],
            "next_review_at": card.due.astimezone(HK).isoformat(),
            "feedback": payload["feedback"], "mastery_updated": False}


def questions(item, mistake):
    # Existing legacy 'prompt' sometimes contains the answer, so prefer test prompts.
    return item.get("independent_prompts") or ([mistake["check_question"]] if mistake.get("check_question")
                                              else [item.get("prompt", "请先准备独立检查题")])


def due(state, now, minutes=15, course=None):
    if not 0 <= minutes <= 30:
        raise ValueError("单次复习预算应为 0–30 分钟，计入当天原有预算")
    mistakes = {m["id"]: m for m in state.get("mistakes", [])}
    candidates = []
    for item in state["review_queue"]:
        if course and item["course_id"] != course:
            continue
        if item.get("status") in {"Completed", "completed", "suspended", "archived"}:
            continue
        card = item.get("fsrs", {}).get("card")
        if card and timestamp(card["due"]) > now:
            continue
        if not card and item.get("due_date") and date.fromisoformat(item["due_date"]) > now.astimezone(HK).date():
            continue
        candidates.append(item)
    candidates.sort(key=lambda x: (0 if x.get("priority") == "High" else 1,
                                  x.get("due_date") or "9999-12-31", x.get("review_order", 999), x["id"]))
    chosen, total = [], 0
    for item in candidates:
        estimate = item.get("estimated_minutes")
        cost = estimate.get("max", 5) if isinstance(estimate, dict) else 5
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost <= 0:
            cost = 5
        if total + cost > minutes:
            continue
        mistake = mistakes.get(item.get("mistake_id"), {})
        chosen.append({"id": item["id"], "course_id": item["course_id"],
                       "title": mistake.get("display_title", item["id"]),
                       "questions": questions(item, mistake), "estimated_minutes": cost,
                       "estimate_basis": "助手估算，包含在当天复习预算内",
                       "material_id": item.get("material_id"), "pages": item.get("pages"),
                       "first_independent_check": not bool(item.get("fsrs"))})
        total += cost
    return {"date": now.astimezone(HK).date().isoformat(), "budget_minutes": minutes,
            "estimated_minutes": total, "remaining_candidates": len(candidates) - len(chosen), "items": chosen}


def render(state):
    mistakes = {m["id"]: m for m in state.get("mistakes", [])}
    lines = ["# 智能复习", "", "每天在原有复习预算内选取约 15–30 分钟。先独立作答，再核对并调整下次复习；讲过不等于掌握。", ""]
    for item in state["review_queue"]:
        mistake = mistakes.get(item.get("mistake_id"), {})
        lines += ["## " + mistake.get("display_title", item["id"]), ""]
        for prompt in questions(item, mistake):
            lines += ["- " + prompt]
        card = item.get("fsrs", {}).get("card")
        if card:
            history = item["fsrs"]["history"]
            lines += ["", "最近表现：" + LABELS[history[-1]["rating"]],
                      "", "下次复习：" + timestamp(card["due"]).astimezone(HK).strftime("%Y-%m-%d %H:%M") + "（香港时间；消息触发时安排）"]
        else:
            lines += ["", "状态：待首次独立检查，尚未计算个人复习间隔。"]
        lines += [""]
    return "\n".join(lines)


def atomic_text(path, content):
    handle, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.suffix == ".json":
            json.loads(Path(name).read_text(encoding="utf-8"))
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


def save(path, state, original):
    if path.read_bytes() != original:
        raise ValueError("共享状态刚被另一任务更新，请重新读取后重试")
    state["revision"] = state.get("revision", 0) + 1
    state["updated_at"] = datetime.now(HK).isoformat()
    content = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    json.loads(content)
    atomic_text(path.with_name("state.previous.json"), original.decode("utf-8-sig"))
    if path.read_bytes() != original:
        raise ValueError("共享状态刚被另一任务更新，请重新读取后重试")
    atomic_text(path, content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    preview = commands.add_parser("due")
    preview.add_argument("--minutes", type=int, default=15)
    preview.add_argument("--at")
    preview.add_argument("--course")
    commands.add_parser("record")
    commands.add_parser("render")
    args = parser.parse_args()
    original = args.state.read_bytes()
    state = json.loads(original.decode("utf-8-sig"))
    if args.command == "due":
        output = due(state, timestamp(args.at), args.minutes, args.course)
    else:
        before = json.dumps(state, sort_keys=True)
        if args.command == "init":
            initialize(state)
            output = {"status": "configured", "review_history_created": False}
        elif args.command == "record":
            output = record(state, json.load(sys.stdin))
        else:
            output = {"status": "rendered"}
        if json.dumps(state, sort_keys=True) != before:
            save(args.state, state, original)
        atomic_text(args.state.with_name("Review Queue.md"), render(state))
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
