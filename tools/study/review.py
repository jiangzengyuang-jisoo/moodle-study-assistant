"""FSRS integration for the existing shared study state. No background process."""
import state_io
import argparse
import study_time
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
from state_io import atomic_text

from fsrs import Card, Rating, Scheduler

ROOT = Path(__file__).resolve().parents[2]
# Personal data may live outside the installed program directory (STUDY_DATA_ROOT);
# without the override the data root is the program root, exactly as before.
DATA_ROOT = Path(os.environ.get("STUDY_DATA_ROOT") or ROOT).resolve()
DEFAULT_STATE = DATA_ROOT / "Study" / "_System" / "state.json"
HK = timezone(timedelta(hours=8))
RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}


def timestamp(value=None):
    result = datetime.fromisoformat(value) if value else datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("时间必须包含时区，例如 +08:00")
    return result.astimezone(timezone.utc)


def _package_version(name):
    """Probe an optional package without failing initialization when absent.

    The plugin's minimal runtime installs fsrs and the document readers; the
    mathematical verification package (sympy) is optional and only needed by
    math_check.py, so a missing one must not block review initialization.
    """
    try:
        return version(name)
    except Exception:
        return None


@study_time.state_timezone
def scheduler(state):
    config = state["settings"]["spaced_repetition"]["scheduler"]
    return Scheduler.from_dict(config)


@study_time.state_timezone
def initialize(state):
    settings = state.setdefault("settings", {})
    if "spaced_repetition" not in settings:
        settings["spaced_repetition"] = {
            "engine": "py-fsrs", "scheduler": Scheduler(enable_fuzzing=False).to_dict(),
            "timezone": study_time.name(), "daily_minutes": [15, 30],
            "real_independent_attempts_only": True,
            "mastery_is_not_inferred_from_schedule": True,
        }
    state.setdefault("integrations", {}).setdefault("study_tools", {}).update({
        "status": "configured", "fsrs_version": _package_version("fsrs"),
        "sympy_version": _package_version("sympy"),
        "review_entry": "tools/study/review.py",
        "math_entry": "tools/study/math_check.py",
        "instructions": "docs/TOOLS.md", "local_only": True,
    })


@study_time.state_timezone
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
    evidence.update(reviewed_at=now.astimezone(study_time.local_zone()).isoformat(), log=log.to_dict())
    item["fsrs"] = {"card": card.to_dict(), "history": history + [evidence]}
    item["due_date"] = card.due.astimezone(study_time.local_zone()).date().isoformat()
    item["status"] = "scheduled_review"
    item["updated_at"] = now.astimezone(study_time.local_zone()).isoformat()
    return {"status": "recorded", "review_id": item["id"],
            "next_review_at": card.due.astimezone(study_time.local_zone()).isoformat(),
            "feedback": payload["feedback"], "mastery_updated": False}


@state_io.locked
def save(path, state, original):
    state_io.prepare_state(path)
    with state_io.timed(path, "state_save"):
        if path.read_bytes() != original:
            raise ValueError("共享状态刚被另一任务更新，请重新读取后重试")
        state["revision"] = state.get("revision", 0) + 1
        with study_time.using_state(state):
            state["updated_at"] = datetime.now(study_time.local_zone()).isoformat()
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
    if args.command == "record":
        raise ValueError("旧作答入口已退役：请用 daily.py attempt 关联具体日包和变式，以核对答案曝光并同步计划")
    with state_io.workspace_lock(args.state):
        state_io.prepare_state(args.state)
        if not args.state.exists():
            raise ValueError(
                '学习档案主状态缺失；不会创建空白档案，也不会覆盖 state.previous.json。'
                '请先备份 Study/_System，再由助手核对并恢复主状态后继续。')
        original = args.state.read_bytes()
        state = json.loads(original.decode("utf-8-sig"))
        if args.command == "due":
            import copy
            import learning_loop
            import planning
            now = timestamp(args.at)
            with study_time.using_state(state):
                output = learning_loop.prepare(copy.deepcopy(state), now,
                                               planning.review_budget_limit(state, now, args.minutes), args.course,
                                               remaining_minutes=planning.remaining_study_minutes(state, now))
            output["read_only_preview"] = True
            output["next_step"] = "正式题包请运行 daily.py run；此预览未保存新题或修改学习状态"
        else:
            if args.command == "init":
                before = json.dumps(state, sort_keys=True)
                initialize(state)
                if json.dumps(state, sort_keys=True) != before:
                    save(args.state, state, original)
            import daily
            daily.execute(args.state, "render", timestamp())
            output = ({"status": "configured", "review_history_created": False}
                      if args.command == "init" else {"status": "rendered"})
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
