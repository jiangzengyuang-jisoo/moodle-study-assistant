"""End-to-end checks of state commits and user-facing plans, using temporary data."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import daily
import review


class DailyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.now = review.timestamp("2026-10-07T15:00:00+08:00")
        self.state = {
            "revision": 1, "updated_at": "2026-10-07T14:00:00+08:00",
            "settings": {}, "integrations": {}, "activity_log": [],
            "courses": [{"id": "MATH-DEMO", "name": "Mathematics", "mastery": [], "current_topic": "Limits"}],
            "tasks": [{"id": "TASK", "course_id": "MATH-DEMO", "title": "Practice", "status": None,
                       "deadline_id": "DL", "remaining_workload_minutes": None}],
            "deadlines": [{"id": "DL", "task_id": "TASK", "course_id": "MATH-DEMO", "title": "Practice",
                           "date": "2026-10-09", "time": None}],
            "schedules": [], "daily_plans": [], "weekly_plan": {}, "upcoming": [],
            "mistakes": [{"id": "M", "course_id": "MATH-DEMO", "entry_type": "question", "display_title": "Limits"}],
            "review_queue": [{"id": "R", "course_id": "MATH-DEMO", "mistake_id": "M", "priority": "High",
                              "status": "independent_review_pending", "due_date": None,
                              "practice_variants": [
                                  {"id": "V1", "prompt": "求 lim(x→2)(x²−4)/(x−2)，并说明原式在2处是否有定义。",
                                   "answer_check": ["SECRET_RUBRIC_4"], "verified": True, "estimated_minutes": 5},
                                  {"id": "V2", "prompt": "求 lim(x→3)(x²−9)/(x−3)，并解释约分条件。",
                                   "answer_check": ["SECRET_RUBRIC_6"], "verified": True, "estimated_minutes": 5}]}],
        }
        for variant in self.state["review_queue"][0]["practice_variants"]:
            variant["source"] = {"material_id": "TEST-MATERIAL", "pages": [1], "detail": "临时测试题源"}
        review.initialize(self.state)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_idempotent_run_and_midnight_rebuild_without_learning_claims(self):
        output = daily.execute(self.path, "run", self.now)
        after = self.path.read_bytes()
        self.assertEqual(self.read()["courses"], self.state["courses"])
        self.assertNotIn("fsrs", self.read()["review_queue"][0])
        for text in [json.dumps(output)] + [p.read_text(encoding="utf-8") for p in self.path.parent.glob("*.md")]:
            self.assertNotIn("SECRET_RUBRIC", text)
        daily.execute(self.path, "run", self.now)
        self.assertEqual(self.path.read_bytes(), after)
        next_day = review.timestamp("2026-10-08T00:01:00+08:00")
        self.assertFalse(daily.execute(self.path, "check", next_day)["fresh"])
        daily.execute(self.path, "run", next_day)
        self.assertTrue(daily.execute(self.path, "check", next_day)["fresh"])
        self.assertIn("2026-10-08", self.path.with_name("Today Plan.md").read_text(encoding="utf-8"))
        self.assertEqual(self.read()["tasks"], self.state["tasks"])

    def test_feedback_and_attempt_update_all_views_without_changing_submission(self):
        daily.execute(self.path, "run", self.now)
        session = self.read()["review_sessions"][0]
        sid = session.get("session_id", session.get("id"))
        item = session["items"][0]
        attempt = {"attempt_id": "A", "session_id": sid, "review_id": "R", "variant_id": item["variant_id"],
                   "answer": "4；原式在2没有定义，但邻近可约分为x+2。", "feedback": "本题独立正确",
                   "evidence": "临时测试中的真实作答占位", "independent": True, "answer_seen_before_attempt": False,
                   "rating": "good", "reviewed_at": "2026-10-07T15:05:00+08:00", "actual_minutes": 5}
        now = review.timestamp(attempt["reviewed_at"])
        daily.execute(self.path, "attempt", now, payload=attempt)
        state = self.read()
        self.assertEqual(len(state["review_queue"][0]["fsrs"]["history"]), 1)
        self.assertTrue(daily.execute(self.path, "check", now)["fresh"])
        feedback = {"feedback_id": "F", "date": "2026-10-07", "recorded_at": "2026-10-07T15:06:00+08:00",
                    "course_id": "MATH-DEMO", "task_ids": ["TASK"], "completed": "完成极限练习",
                    "actual_minutes": 20, "blocker": "分段函数仍需练习", "next_step": "下次练分段函数",
                    "evidence": "临时测试学习反馈"}
        now = review.timestamp(feedback["recorded_at"])
        daily.execute(self.path, "feedback", now, payload=feedback)
        after = self.path.read_bytes()
        daily.execute(self.path, "feedback", now, payload=feedback)
        self.assertEqual(self.path.read_bytes(), after)
        self.assertEqual(self.read()["tasks"], self.state["tasks"])
        self.assertEqual(self.read()["courses"], self.state["courses"])
        self.assertIn("分段函数", self.path.with_name("Learning Feedback.md").read_text(encoding="utf-8"))
        self.assertTrue(daily.execute(self.path, "check", now)["fresh"])

    def test_state_change_invalidates_existing_views(self):
        daily.execute(self.path, "run", self.now)
        newer = self.read()
        newer["revision"] += 1
        self.path.write_text(json.dumps(newer), encoding="utf-8")
        self.assertFalse(daily.execute(self.path, "check", self.now)["fresh"])
        daily.execute(self.path, "render", self.now)
        self.assertTrue(daily.execute(self.path, "check", self.now)["fresh"])

    def test_missing_question_page_and_changed_feedback_page_are_stale(self):
        daily.execute(self.path, "run", self.now)
        self.path.with_name("Today Review.md").unlink()
        self.assertFalse(daily.execute(self.path, "check", self.now)["fresh"])
        daily.execute(self.path, "render", self.now)
        self.path.with_name("Learning Feedback.md").write_text("旧反馈", encoding="utf-8")
        self.assertFalse(daily.execute(self.path, "check", self.now)["fresh"])

    def test_review_fits_confirmed_small_capacity(self):
        state = self.read()
        state["daily_plans"] = [{"date": "2026-10-07", "available_study_minutes": 10}]
        self.path.write_text(json.dumps(state), encoding="utf-8")
        daily.execute(self.path, "run", self.now)
        session = self.read()["review_sessions"][0]
        self.assertLessEqual(session["budget_minutes"], 8)
        self.assertLessEqual(sum(x["reserved_minutes"] for x in session["items"]), 8)

    def test_ordinary_repeat_keeps_confirmed_budget_but_explicit_reduction_applies(self):
        daily.execute(self.path, "run", self.now, minutes=30)
        daily.execute(self.path, "run", self.now)
        self.assertEqual(self.read()["review_sessions"][0]["budget_minutes"], 30)
        daily.execute(self.path, "run", self.now, minutes=0)
        self.assertEqual(self.read()["review_sessions"][0]["budget_minutes"], 0)
        self.assertFalse(any(x["status"] == "pending" for x in self.read()["review_sessions"][0]["items"]))

    def test_legacy_cli_cannot_bypass_answer_exposure_checks(self):
        state = self.read()
        state["settings"]["learning_loop"] = {"enabled": True}
        self.path.write_text(json.dumps(state), encoding="utf-8")
        before = self.path.read_bytes()
        result = subprocess.run([sys.executable, str(Path(review.__file__)), "--state", str(self.path), "record"],
                                input="{}", capture_output=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("daily.py attempt", result.stderr)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
