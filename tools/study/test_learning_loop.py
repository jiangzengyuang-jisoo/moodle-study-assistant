"""Learning-loop regression tests; use synthetic or temporary state only."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import learning_loop as loop
import review


AT = datetime(2026, 1, 10, 12, 0, tzinfo=review.HK)


def fixture(count=3, variants=3):
    state = {"settings": {}, "courses": [{"id": "MATH-DEMO"}, {"id": "ECON-DEMO"}],
             "tasks": [{"id": "TASK1", "course_id": "MATH-DEMO", "status": "pending"}],
             "mistakes": [], "review_queue": []}
    review.initialize(state)
    for index in range(count):
        course = "MATH-DEMO" if index % 2 == 0 else "ECON-DEMO"
        state["mistakes"].append({"id": f"M{index}", "display_title": f"能力 {index}"})
        state["review_queue"].append({
            "id": f"R{index}", "mistake_id": f"M{index}", "course_id": course,
            "status": "independent_review_pending", "priority": "High", "review_order": index,
            "prompt": "LEGACY ANSWER DO NOT SHOW", "due_date": None,
            "practice_variants": [{"id": f"V{variant}", "prompt": f"求 {index + 1} + {variant + 2}，写出计算。",
                "answer_check": ["PRIVATE_ANSWER_" + str(index + variant + 3)],
                "estimated_minutes": 5, "verified": True,
                "source": {"material_id": "MATERIAL", "pages": [1], "detail": "已核验整数运算。"}}
                for variant in range(variants)]})
    return state


def attempt(package, at=AT, **updates):
    item = next(i for i in package["items"] if i["status"] == "pending")
    result = {"attempt_id": "ATTEMPT-1", "session_id": package["session_id"],
              "review_id": item["review_id"], "variant_id": item["variant_id"],
              "answer": "我的原始作答", "feedback": "核对反馈 PRIVATE_FEEDBACK_ANSWER",
              "evidence": {"type": "user_message", "message_id": "message-1"},
              "independent": True, "answer_seen_before_attempt": False, "rating": "good",
              "reviewed_at": at.isoformat(), "actual_minutes": 5}
    result.update(updates)
    return result


class LearningLoopTests(unittest.TestCase):
    def test_repeated_prepare_reuses_one_global_budget(self):
        state = fixture(6)
        first = loop.prepare(state, AT, 15, "MATH-DEMO")
        snapshot = deepcopy(state)
        second = loop.prepare(state, AT, 30, "ECON-DEMO")
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual(second["budget_minutes"], 15)
        self.assertEqual(len(state["review_sessions"]), 1)
        self.assertEqual(len(second["items"]), 3)
        self.assertEqual(second["committed_minutes"], 15)
        self.assertEqual(snapshot["review_sessions"][0]["items"], state["review_sessions"][0]["items"])

    def test_preview_and_page_do_not_leak_answers_or_feedback(self):
        state = fixture()
        package = loop.prepare(state, AT)
        initial_output = json.dumps(package) + loop.render_today_review(state, AT)
        for secret in ("PRIVATE_ANSWER", "LEGACY ANSWER", "answer_check"):
            self.assertNotIn(secret, initial_output)
        loop.record_attempt(state, attempt(package))
        output = json.dumps(loop.prepare(state, AT)) + loop.render_today_review(state, AT)
        self.assertNotIn("PRIVATE_FEEDBACK", output)
        self.assertNotIn("我的原始作答", output)
        self.assertNotIn("PRIVATE_ANSWER", output)
        self.assertIn("我的原始作答", json.dumps(state, ensure_ascii=False))
        self.assertEqual(loop.prepare(state, AT)["items"][0]["status"], "answered")
        self.assertNotIn("prompt", loop.prepare(state, AT)["items"][0])

    def test_missing_or_unverified_questions_need_preparation(self):
        state = fixture(2)
        state["review_queue"][0]["practice_variants"] = []
        for variant in state["review_queue"][1]["practice_variants"]:
            variant["verified"] = False
        package = loop.prepare(state, AT)
        self.assertEqual(package["status"], "needs_preparation")
        self.assertEqual(package["items"], [])
        self.assertEqual(len(package["needs_preparation"]), 2)
        self.assertNotIn("LEGACY ANSWER", loop.render_today_review(state, AT))

    def test_unprepared_high_priority_does_not_hide_verified_candidates(self):
        state = fixture(10)
        for item in state["review_queue"][:9]:
            item["practice_variants"] = []
        self.assertEqual(loop.prepare(state, AT)["items"][0]["review_id"], "R9")

    def test_cross_day_unfinished_question_carries_without_consuming_variants(self):
        state = fixture(1)
        yesterday = loop.prepare(state, AT)
        for days in range(1, 6):
            today = loop.prepare(state, AT + timedelta(days=days))
            self.assertEqual(yesterday["items"][0]["variant_id"], today["items"][0]["variant_id"])
            self.assertEqual(today["committed_minutes"], 5)
        self.assertEqual(len(state["review_sessions"]), 6)
        self.assertNotIn("fsrs", state["review_queue"][0])
        self.assertNotIn("review_attempts", state)
        self.assertEqual(state["review_sessions"][0]["items"][0]["status"], "carried_forward")
        self.assertIn(yesterday["items"][0]["prompt"], loop.render_today_review(state, AT + timedelta(days=5)))

    def test_no_yesterday_questions_rendered_before_prepare(self):
        state = fixture(1)
        loop.prepare(state, AT)
        rendered = loop.render_today_review(state, AT + timedelta(days=1))
        self.assertIn("2026-01-11", rendered)
        self.assertIn("尚未准备", rendered)
        self.assertNotIn("求 1 + 2", rendered)

    def test_assisted_attempt_preserves_evidence_without_fsrs(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        result = loop.record_attempt(state, attempt(package, independent=False, rating=None,
                                                   answer_seen_before_attempt=True, actual_minutes=None))
        self.assertFalse(result["fsrs_rated"])
        self.assertFalse(result["mastery_updated"])
        self.assertIsNone(result["next_review_at"])
        self.assertNotIn("fsrs", state["review_queue"][0])
        self.assertEqual(state["review_attempts"][0]["answer"], "我的原始作答")
        self.assertIsNone(state["review_attempts"][0]["actual_minutes"])
        self.assertEqual(result["answered_minutes_unknown"], 1)

    def test_attempt_duplicate_is_idempotent_and_conflict_rejected(self):
        state = fixture(1)
        payload = attempt(loop.prepare(state, AT))
        first = loop.record_attempt(state, payload)
        snapshot = deepcopy(state)
        self.assertEqual(loop.record_attempt(state, payload)["status"], "duplicate")
        self.assertEqual(snapshot, state)
        with self.assertRaisesRegex(ValueError, "冲突"):
            loop.record_attempt(state, {**payload, "actual_minutes": 6})
        self.assertEqual(snapshot, state)
        self.assertEqual(len(state["review_queue"][0]["fsrs"]["history"]), 1)
        self.assertIsNotNone(first["next_review_at"])

    def test_minute_level_fsrs_due_retest_uses_fresh_variant(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        first = loop.record_attempt(state, attempt(package, rating="again"))
        due = review.timestamp(first["next_review_at"])
        self.assertGreater(due, AT)
        before = loop.prepare(state, due - timedelta(seconds=1))
        self.assertEqual(len(before["items"]), 1)
        at_due = loop.prepare(state, due)
        self.assertEqual(len(at_due["items"]), 2)
        pending = next(i for i in at_due["items"] if i["status"] == "pending")
        self.assertNotEqual(pending["variant_id"], package["items"][0]["variant_id"])
        second = loop.record_attempt(state, attempt(at_due, at=due, attempt_id="ATTEMPT-2"))
        self.assertIsNotNone(second["next_review_at"])
        self.assertEqual(len(state["review_queue"][0]["fsrs"]["history"]), 2)
        self.assertEqual(second["committed_minutes"], 10)

    def test_answer_exposure_blocks_independent_rating(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        payload = attempt(package)
        loop.mark_answer_shown(state, {**payload, "shown_at": AT.isoformat()})
        rendered = loop.render_today_review(state, AT)
        self.assertIn("已讲解", rendered)
        self.assertNotIn(package["items"][0]["prompt"], rendered)
        snapshot = deepcopy(state)
        with self.assertRaisesRegex(ValueError, "答案已经展示"):
            loop.record_attempt(state, payload)
        self.assertEqual(snapshot, state)
        result = loop.record_attempt(state, {**payload, "independent": False, "rating": None,
                                             "answer_seen_before_attempt": True})
        self.assertFalse(result["fsrs_rated"])

    def test_seen_flag_cannot_be_disguised_as_independent(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        with self.assertRaisesRegex(ValueError, "答案已经展示"):
            loop.record_attempt(state, attempt(package, answer_seen_before_attempt=True))
        self.assertNotIn("review_attempts", state)

    def test_duplicate_prompt_under_new_id_not_reused(self):
        state = fixture(1)
        state["review_queue"][0]["practice_variants"][1]["prompt"] = state["review_queue"][0]["practice_variants"][0]["prompt"]
        package = loop.prepare(state, AT)
        loop.record_attempt(state, attempt(package, independent=False, rating=None))
        next_package = loop.prepare(state, AT)
        self.assertEqual(next_package["items"][1]["variant_id"], "V2")

    def test_actual_time_replaces_estimate_without_inventing_unknown(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        result = loop.record_attempt(state, attempt(package, actual_minutes=17))
        self.assertEqual(result["remaining_minutes"], 0)
        self.assertEqual(result["actual_minutes_known"], 17)
        self.assertEqual(result["committed_minutes"], 17)
        self.assertEqual(loop.prepare(state, AT)["budget_minutes"], 15)

    def test_budget_reduction_defers_unstarted_work_and_does_not_reactivate(self):
        state = fixture(3)
        loop.prepare(state, AT, 15)
        reduced = loop.prepare(state, AT, 5)
        self.assertEqual(reduced["budget_minutes"], 5)
        self.assertEqual([i["status"] for i in reduced["items"]], ["pending", "deferred", "deferred"])
        repeated = loop.prepare(state, AT, 30)
        self.assertEqual(repeated["budget_minutes"], 5)
        self.assertEqual(repeated["items"], reduced["items"])
        zero = loop.prepare(state, AT, 0)
        self.assertEqual(zero["status"], "paused_for_budget")
        self.assertFalse(any(i["status"] == "pending" for i in zero["items"]))
        self.assertEqual(zero["committed_minutes"], 0)
        self.assertNotIn("求 1 + 2", loop.render_today_review(state, AT))
        tomorrow = loop.prepare(state, AT + timedelta(days=1), 15)
        self.assertEqual([i["variant_id"] for i in tomorrow["items"]], ["V0", "V0", "V0"])
        self.assertTrue(all(i["status"] == "pending" for i in tomorrow["items"]))

    def test_budget_reduction_retains_real_time_over_new_limit(self):
        state = fixture(3)
        package = loop.prepare(state, AT)
        loop.record_attempt(state, attempt(package, actual_minutes=8))
        reduced = loop.prepare(state, AT, 5)
        self.assertEqual(reduced["committed_minutes"], 8)
        self.assertEqual(reduced["remaining_minutes"], 0)
        self.assertFalse(any(i["status"] == "pending" for i in reduced["items"]))
        self.assertEqual(len(state["review_queue"][0]["fsrs"]["history"]), 1)

    def test_feedback_idempotent_preserves_submission_and_unknown_time(self):
        state = fixture(1)
        original_tasks = deepcopy(state["tasks"])
        payload = {"feedback_id": "F1", "course_id": "MATH-DEMO", "task_ids": ["TASK1"],
                   "completed": "做完第一题", "actual_minutes": None,
                   "blocker": "还不清楚条件", "next_step": "明天再做变式", "evidence": "真实对话 message-2",
                   "recorded_at": AT.isoformat()}
        loop.record_feedback(state, payload)
        snapshot = deepcopy(state)
        self.assertEqual(loop.record_feedback(state, payload)["status"], "duplicate")
        self.assertEqual(state, snapshot)
        with self.assertRaisesRegex(ValueError, "冲突"):
            loop.record_feedback(state, {**payload, "completed": "做完第二题"})
        self.assertEqual(state["tasks"], original_tasks)
        self.assertNotIn("fsrs", state["review_queue"][0])
        self.assertIsNone(state["learning_feedback"][0]["actual_minutes"])
        self.assertEqual(len(state["learning_feedback"]), 1)
        self.assertEqual(state["learning_feedback"][0]["time_scope"], "unknown")

    def test_feedback_keeps_time_scope_without_guessing_totals(self):
        state = fixture(1)
        for index, scope in enumerate(("session", "cumulative", "unknown")):
            loop.record_feedback(state, {"feedback_id": f"F{index}", "completed": "真实学习内容",
                "evidence": f"message-{index}", "actual_minutes": 10, "time_scope": scope,
                "recorded_at": AT.isoformat()})
        self.assertEqual([x["time_scope"] for x in state["learning_feedback"]], ["session", "cumulative", "unknown"])
        self.assertNotIn("learning_minutes_total", state)

    def test_late_response_uses_true_answer_day_budget_once(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        today = loop.prepare(state, AT + timedelta(days=1))
        result = loop.record_attempt(state, attempt(package, at=AT + timedelta(days=1), actual_minutes=7))
        self.assertEqual(result["committed_minutes"], 7)
        self.assertEqual(loop._budget(state["review_sessions"][0])["committed_minutes"], 0)
        self.assertEqual(loop._budget(state["review_sessions"][1])["committed_minutes"], 7)
        self.assertEqual(len(state["review_sessions"][1]["items"]), 1)
        self.assertEqual(len(state["review_queue"][0]["fsrs"]["history"]), 1)
        self.assertTrue(all(s["items"][0]["status"] == "answered" for s in state["review_sessions"]))
        with self.assertRaisesRegex(ValueError, "已作答"):
            loop.record_attempt(state, attempt(today, at=AT + timedelta(days=1), attempt_id="ATTEMPT-2"))

    def test_late_response_before_today_prepare_preserves_real_answer(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        result = loop.record_attempt(state, attempt(package, at=AT + timedelta(days=1), actual_minutes=4))
        self.assertEqual(result["committed_minutes"], 4)
        self.assertEqual(len(state["review_sessions"]), 2)
        self.assertEqual(state["review_attempts"][0]["session_id"], package["session_id"])
        self.assertEqual(state["review_sessions"][1]["items"][0]["budget_session_id"], "REVIEW-DAY-2026-01-11")

    def test_answer_exposure_time_is_validated(self):
        state = fixture(1)
        package = loop.prepare(state, AT)
        payload = {**attempt(package), "shown_at": (AT - timedelta(minutes=1)).isoformat()}
        with self.assertRaisesRegex(ValueError, "展示时间"):
            loop.mark_answer_shown(state, payload)
        self.assertEqual(state["review_sessions"][0]["items"][0]["status"], "pending")

    def test_cli_uses_temp_state_and_preserves_backup(self):
        with tempfile.TemporaryDirectory(prefix="loop-test-", dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "state.json"
            original = json.dumps(fixture(1), ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            command = [sys.executable, str(Path(loop.__file__)), "--state", str(path)]
            result = subprocess.run(command + ["prepare", "--at", AT.isoformat()], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "ready")
            self.assertEqual(path.with_name("state.previous.json").read_text(encoding="utf-8"), original)
            before = path.read_bytes()
            result = subprocess.run(command + ["today", "--at", AT.isoformat()], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("PRIVATE_ANSWER", result.stdout)
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(path.with_name("Review Queue.md").exists())
            self.assertTrue(path.with_name("Today Plan.md").exists())


if __name__ == "__main__":
    unittest.main()
