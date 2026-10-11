"""Temporary end-to-end checks of Moodle-bound mocks, views and actual evidence."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

import coverage
import daily
import exam_prep
import review
from test_exam_prep import AT, fixture, paper, attempt


def snapshot():
    return {"collection_complete": True, "moodle_url": "https://moodle.example.edu", "finished_at": AT.isoformat(), "pages": [],
        "threads": [{"course_id": "COURSE", "discussion": {"discussion": 2}, "posts": [
            {"id": 20, "subject": "Midterm", "message": "One MC and one long answer", "author": {"id": 7, "fullname": "Teacher"}}]}]}


class ExamIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Study"
        self.path = self.root / "_System" / "state.json"
        self.path.parent.mkdir(parents=True)
        self.initial = fixture()
        self.profile = self.initial.pop("exam_profiles")[0]
        self.profile["review_evidence"] = "Synthetic official post and instructor role verified"
        self.profile["sources"][0].update(location="post:20", author_id=7, role_evidence="Synthetic course orientation teacher")
        self.initial.update(revision=1, updated_at=AT.isoformat(), settings={}, integrations={}, activity_log=[],
                            tasks=[{"id": "DONE", "course_id": "COURSE", "status": "Completed", "submission_status": "submitted"}],
                            deadlines=[], schedules=[], daily_plans=[], weekly_plan={}, upcoming=[],
                            submissions=[{"task_id": "DONE", "status": "submitted", "independent_mastery": None}])
        self.initial["courses"][0].update(name="Course", current_topic="Addition", mastery=[{"existing_evidence": "preserve"}])
        self.initial["mistakes"] = [{"id": "ORIGINAL", "course_id": "COURSE", "display_title": "Existing question", "entry_type": "question"}]
        self.initial["review_queue"] = [{"id": "ORIGINAL-REVIEW", "course_id": "COURSE", "mistake_id": "ORIGINAL", "status": "suspended",
             "fsrs": {"card": {"due": "2027-01-01T00:00:00+08:00"}, "history": [{"existing_evidence": "preserve"}]}}]
        review.initialize(self.initial)
        self.path.write_text(json.dumps(self.initial, ensure_ascii=False), encoding="utf-8")
        self.notebook = self.root / "COURSE" / "错题本" / "记录.md"
        self.notebook.parent.mkdir(parents=True)
        self.notebook.write_text("# Existing notebook\n\nUSER ORIGINAL TEXT\n", encoding="utf-8")
        self.snap = snapshot()

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def prepare(self):
        daily.execute(self.path, "exam-profile", AT, payload=self.profile, snapshot=self.snap)
        return daily.execute(self.path, "mock-register", AT, payload=paper(), snapshot=self.snap)

    def invariant(self):
        current = self.read()
        for key in ("courses", "tasks", "submissions", "schedules"):
            self.assertEqual(current[key], self.initial[key])
        self.assertEqual(current["review_queue"][0], self.initial["review_queue"][0])
        self.assertEqual(current["mistakes"][0], self.initial["mistakes"][0])

    def test_profile_register_mock_and_all_views_without_answer_or_learning_mutations(self):
        result = self.prepare()
        self.assertEqual(len(result["views_updated"]), 10)
        self.assertTrue(daily.execute(self.path, "check", AT)["fresh"])
        self.assertIn("Exam Coverage.md", result["views_updated"])
        self.assertIn("Weekly Reflection.md", result["views_updated"])
        self.assertIn("Mock Exams.md", result["views_updated"])
        self.assertIn("../COURSE/Practice/P1.md", result["views_updated"])
        self.assertTrue(self.path.with_name("state.previous.json").exists())
        for name in result["views_updated"]:
            self.assertNotIn("PRIVATE_", daily.view_path(self.path, name).read_text(encoding="utf-8"))
        self.assertEqual([], self.read().get("mock_attempts", []))
        self.assertEqual(self.read()["mock_exams"][0]["answer_exposures"], [])
        self.invariant()

    def test_wrong_attempt_links_queue_and_managed_notebook_preserving_user_text(self):
        self.prepare()
        now = AT + timedelta(minutes=30)
        result = daily.execute(self.path, "mock-attempt", now, payload=attempt())
        current = self.read()
        self.assertEqual(len(current["mock_attempts"]), 1)
        self.assertEqual(len(current["review_queue"]), 2)
        self.assertEqual(current["review_queue"][1]["practice_variants"], [])
        self.assertNotIn("fsrs", current["review_queue"][1])
        self.assertEqual(current["review_queue"][1]["topic_ids"], ["T1"])
        self.assertEqual(len(result["review_ids"]), 1)
        self.assertTrue(daily.execute(self.path, "check", now)["fresh"])
        text = self.notebook.read_text(encoding="utf-8")
        self.assertIn("USER ORIGINAL TEXT", text)
        self.assertIn("模拟考试中的实际卡点", text)
        self.assertEqual(text.count(daily.NOTEBOOK_START), 1)
        daily.execute(self.path, "render", now)
        self.assertEqual(text, self.notebook.read_text(encoding="utf-8"))
        for name in ("Mock Exams.md", "../COURSE/Practice/P1.md"):
            self.assertNotIn("PRIVATE_", daily.view_path(self.path, name).read_text(encoding="utf-8"))
        self.invariant()

    def test_grading_at_submission_time_retains_independent_coverage_before_feedback(self):
        self.prepare()
        now = AT + timedelta(minutes=30)
        payload = attempt()
        payload["answers"][0]["answer"] = "B"
        daily.execute(self.path, "mock-attempt", now, payload=payload)
        state = self.read()
        events = coverage.evidence_events(state, now)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["independent"] and e["correct"] for e in events))
        self.assertEqual(coverage.topic_status(state, "COURSE", "T1", events, now)["status"], "独立做对（单次）")
        self.assertIn("独立做对（单次）", self.path.with_name("Exam Coverage.md").read_text(encoding="utf-8"))
        self.assertEqual(len(state["mock_exams"][0]["answer_exposures"]), 1)
        self.assertEqual(len(state["mistakes"]), 1)
        self.invariant()

    def test_answers_command_records_exposure_and_does_not_publish_key_in_views(self):
        self.prepare()
        now = AT + timedelta(minutes=10)
        result = daily.execute(self.path, "mock-answers", now, payload={"mock_id": "P1", "evidence": "Explicit synthetic request for answer key"})
        self.assertIn("PRIVATE_ANSWER", result["answer_key"])
        self.assertEqual(len(self.read()["mock_exams"][0]["answer_exposures"]), 1)
        for name in result["views_updated"]:
            self.assertNotIn("PRIVATE_", daily.view_path(self.path, name).read_text(encoding="utf-8"))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            daily.execute(self.path, "mock-attempt", AT + timedelta(minutes=30), payload=attempt())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], self.read().get("mock_attempts", []))
        self.invariant()

    def test_source_refresh_timestamp_keeps_paper_but_changed_teacher_post_stops_it(self):
        self.prepare()
        later = AT + timedelta(minutes=15)
        self.snap["finished_at"] = later.isoformat()
        daily.execute(self.path, "run", later, snapshot=self.snap)
        state = self.read()
        self.assertTrue(exam_prep.mock_is_current(state, state["mock_exams"][0]))
        self.assertNotIn("已过期", daily.view_path(self.path, "../COURSE/Practice/P1.md").read_text(encoding="utf-8"))
        self.snap["threads"][0]["posts"][0]["message"] = "Two MC and one long answer"
        later += timedelta(minutes=1)
        daily.execute(self.path, "run", later, snapshot=self.snap)
        text = daily.view_path(self.path, "../COURSE/Practice/P1.md").read_text(encoding="utf-8")
        self.assertIn("已过期", text)
        self.assertNotIn("求 1+2", text)
        self.assertTrue(daily.execute(self.path, "check", later)["fresh"])
        self.assertEqual(self.read()["exam_profiles"][0]["verification_status"], "needs_review")
        self.assertIn("考试说明待重新核读", self.path.with_name("Exam Coverage.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            daily.execute(self.path, "mock-attempt", AT + timedelta(minutes=30), payload=attempt())
        self.invariant()

    def test_individual_hint_marks_only_requested_question_without_releasing_key(self):
        self.prepare()
        qid = self.read()["mock_exams"][0]["questions"][0]["id"]
        now = AT + timedelta(minutes=10)
        result = daily.execute(self.path, "mock-answer-shown", now,
            payload={"mock_id": "P1", "question_ids": [qid], "evidence": "One actual hint in synthetic conversation"})
        self.assertNotIn("answer_key", result)
        exposures = self.read()["mock_exams"][0]["answer_exposures"]
        self.assertEqual(exposures[0]["question_ids"], [qid])
        self.assertTrue(daily.execute(self.path, "check", now)["fresh"])
        self.assertEqual([], self.read().get("mock_attempts", []))
        self.invariant()

    def test_stale_managed_notebook_block_detected_and_rebuilt_without_erasing_other_text(self):
        self.prepare()
        now = AT + timedelta(minutes=30)
        daily.execute(self.path, "mock-attempt", now, payload=attempt())
        old = self.notebook.read_text(encoding="utf-8")
        self.notebook.write_text(old.replace("核对反馈：", "STALE FEEDBACK:") + "\nUSER LATER NOTE\n", encoding="utf-8")
        self.assertFalse(daily.execute(self.path, "check", now)["fresh"])
        daily.execute(self.path, "render", now)
        text = self.notebook.read_text(encoding="utf-8")
        self.assertIn("USER ORIGINAL TEXT", text)
        self.assertIn("USER LATER NOTE", text)
        self.assertNotIn("STALE FEEDBACK", text)
        self.assertEqual(text.count(daily.NOTEBOOK_START), 1)
        self.assertTrue(daily.execute(self.path, "check", now)["fresh"])


if __name__ == "__main__":
    unittest.main()
