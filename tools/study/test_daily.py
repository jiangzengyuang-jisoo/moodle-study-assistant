"""End-to-end checks of state commits and user-facing plans, using temporary data."""
from copy import deepcopy
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
            "materials": [{"id": "TEST-MATERIAL", "course_id": "MATH-DEMO", "version_status": "current"}],
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

    def test_attempt_save_then_view_crash_does_not_rate_fsrs_twice(self):
        from unittest.mock import patch
        daily.execute(self.path, "run", self.now)
        session = self.read()["review_sessions"][0]
        item = session["items"][0]
        payload = {"attempt_id": "CRASH-ATTEMPT", "session_id": session.get("session_id", session.get("id")),
                   "review_id": "R", "variant_id": item["variant_id"], "answer": "4", "feedback": "independent",
                   "evidence": "synthetic test", "independent": True, "answer_seen_before_attempt": False,
                   "rating": "good", "reviewed_at": "2026-10-07T15:05:00+08:00", "actual_minutes": 5}
        now = review.timestamp(payload["reviewed_at"])
        with patch.object(daily, "write_views", side_effect=OSError("crash")):
            with self.assertRaises(OSError): daily.execute(self.path, "attempt", now, payload=payload)
        daily.execute(self.path, "attempt", now, payload=payload)
        self.assertEqual(len(self.read()["review_attempts"]), 1)
        self.assertEqual(len(self.read()["review_queue"][0]["fsrs"]["history"]), 1)

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


    def _budget_fixture(self, available=10, count=1, costs=(5, 5)):
        state = self.read()
        state["daily_plans"] = [{"date": "2026-10-07", "available_study_minutes": available}]
        template = deepcopy(state["review_queue"][0])
        state["review_queue"] = []
        for index in range(count):
            item = deepcopy(template)
            item["id"] = f"BUDGET-R{index}"
            for number, variant in enumerate(item["practice_variants"]):
                variant.update(prompt=f"Budget question {index}-{number}",
                               estimated_minutes=costs[number])
            state["review_queue"].append(item)
        self.path.write_text(json.dumps(state), encoding="utf-8")

    def _budget_feedback(self, minutes, scope="cumulative", identifier="BUDGET-F"):
        payload = {"feedback_id": identifier, "date": "2026-10-07",
                   "recorded_at": "2026-10-07T15:10:00+08:00",
                   "completed": "Synthetic study", "evidence": "Synthetic budget test",
                   "actual_minutes": minutes, "time_scope": scope}
        now = review.timestamp(payload["recorded_at"])
        return daily.execute(self.path, "feedback", now, payload=payload)

    def _budget_attempt(self, package, at, independent=True, minutes=5):
        item = package["items"][0]
        return daily.execute(self.path, "attempt", at, payload={
            "attempt_id": "BUDGET-A", "session_id": package["session_id"],
            "review_id": item["review_id"], "variant_id": item["variant_id"],
            "answer": "Synthetic response", "feedback": "Synthetic feedback",
            "evidence": "Synthetic budget test", "independent": independent,
            "answer_seen_before_attempt": False, "rating": "good" if independent else None,
            "reviewed_at": at.isoformat(), "actual_minutes": minutes})

    def test_exhausted_feedback_before_first_package_assigns_nothing_and_replays(self):
        self._budget_fixture()
        self.assertEqual(self.read().get("review_sessions", []), [])
        result = self._budget_feedback(8)
        self.assertEqual(result["review"]["budget_minutes"], 0)
        self.assertEqual(result["review"]["items"], [])
        self.assertIn("原预算内剩余至多 0 分钟",
                      self.path.with_name("Today Plan.md").read_text(encoding="utf-8"))
        after = self.path.read_bytes()
        self._budget_feedback(8)
        daily.execute(self.path, "run", review.timestamp("2026-10-07T15:10:00+08:00"))
        self.assertEqual(self.path.read_bytes(), after)
        self.assertTrue(daily.execute(self.path, "check", review.timestamp("2026-10-07T15:10:00+08:00"))["fresh"])

    def test_partial_feedback_selects_only_fitting_work_and_does_not_recharge(self):
        self._budget_fixture(costs=(5, 3))
        package = self._budget_feedback(5)["review"]
        self.assertEqual([i["estimated_minutes"] for i in package["items"]], [3])
        at = review.timestamp("2026-10-07T15:13:00+08:00")
        self._budget_attempt(package, at, independent=False, minutes=3)
        result = daily.execute(self.path, "run", at)
        self.assertEqual(result["review"]["remaining_minutes"], 0)
        self.assertFalse(any(i["status"] == "pending" for i in result["review"]["items"]))
        self.assertTrue(daily.execute(self.path, "check", at)["fresh"])

    def test_cumulative_feedback_including_review_does_not_deduct_it_twice(self):
        self._budget_fixture(available=25, count=3)
        package = daily.execute(self.path, "run", self.now)["review"]
        self._budget_attempt(package, review.timestamp("2026-10-07T15:05:00+08:00"))
        history = deepcopy(self.read()["review_attempts"])
        fsrs = deepcopy(self.read()["review_queue"][0]["fsrs"])
        package = self._budget_feedback(10)["review"]  # Total 20, remaining 10.
        self.assertEqual(sum(i["estimated_minutes"] for i in package["items"]
                             if i["status"] == "pending"), 10)
        self.assertEqual(package["budget_minutes"], 15)  # 5 already used + 10 allowed.
        package = self._budget_feedback(20, identifier="BUDGET-F2")["review"]
        self.assertEqual([i["status"] for i in package["items"]], ["answered", "deferred", "deferred"])
        self.assertEqual(self.read()["review_attempts"], history)
        self.assertEqual(self.read()["review_queue"][0]["fsrs"], fsrs)
        self.assertTrue(daily.execute(self.path, "check", review.timestamp("2026-10-07T15:10:00+08:00"))["fresh"])

    def test_exhausted_feedback_preserves_exposure_and_defers_unstarted_work(self):
        self._budget_fixture(available=25, count=3)
        package = daily.execute(self.path, "run", self.now)["review"]
        item = package["items"][0]
        daily.execute(self.path, "answer-shown", self.now, payload={
            "session_id": package["session_id"], "review_id": item["review_id"],
            "variant_id": item["variant_id"], "evidence": "Synthetic shown hint",
            "shown_at": self.now.isoformat()})
        original = deepcopy(self.read()["review_sessions"][0]["items"][0])
        package = self._budget_feedback(20)["review"]
        self.assertFalse(any(i["status"] == "pending" for i in package["items"]))
        self.assertEqual(self.read()["review_sessions"][0]["items"][0], original)
        self.assertEqual([i["status"] for i in package["items"]][1:], ["deferred", "deferred"])

    def test_distinct_session_feedback_exhausts_shared_capacity(self):
        self._budget_fixture()
        self._budget_feedback(2, scope="session", identifier="BUDGET-F1")
        package = self._budget_feedback(6, scope="session", identifier="BUDGET-F2")["review"]
        self.assertEqual([i["status"] for i in package["items"]], ["deferred"])
        self.assertEqual(package["remaining_minutes"], 0)
        self.assertIn("原预算内剩余至多 0 分钟",
                      self.path.with_name("Today Plan.md").read_text(encoding="utf-8"))

    def test_unknown_feedback_scope_does_not_invent_remaining_capacity(self):
        self._budget_fixture()
        package = self._budget_feedback(8, scope="unknown")["review"]
        self.assertEqual(package["budget_minutes"], 8)
        self.assertEqual([i["status"] for i in package["items"]], ["pending"])
        self.assertIn("暂不合计或推算剩余时间",
                      self.path.with_name("Today Plan.md").read_text(encoding="utf-8"))

    def test_explicit_total_budget_without_available_minutes_still_limits_feedback(self):
        self._budget_fixture()
        state = self.read()
        state["daily_plans"] = [{"date": "2026-10-07", "budget_minutes": 8}]
        self.path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self._budget_feedback(8)["review"]["items"], [])


    def test_due_preview_obeys_exhausted_feedback_without_writing_state(self):
        self._budget_fixture()
        state = self.read()
        state["settings"]["learning_loop"] = {"enabled": True}
        state["learning_feedback"] = [{"feedback_id": "PREVIEW-F", "date": "2026-10-07",
                                      "actual_minutes": 8, "time_scope": "cumulative"}]
        self.path.write_text(json.dumps(state), encoding="utf-8")
        before = self.path.read_bytes()
        result = subprocess.run([sys.executable, "-B", str(Path(review.__file__)),
                                 "--state", str(self.path), "due", "--at", self.now.isoformat()],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["read_only_preview"])
        self.assertEqual(output["items"], [])
        self.assertEqual(self.path.read_bytes(), before)


    def test_unknown_total_capacity_is_not_assumed_exhausted(self):
        self._budget_fixture()
        state = self.read()
        state["daily_plans"] = []
        self.path.write_text(json.dumps(state), encoding="utf-8")
        package = self._budget_feedback(100)["review"]
        self.assertEqual([i["status"] for i in package["items"]], ["pending"])


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
