"""Continuity, evidence and disclosure regression tests for course context."""

import copy
import json
import unittest

from learning_library.context import build_context, read_detail


def fixture():
    return {
        "revision": 8, "updated_at": "2026-10-22T01:00:00+08:00", "timezone": "Asia/Hong_Kong",
        "courses": [
            {"id": "A", "name": "Accounting", "aliases": ["会计"], "current_topic": "Ch6", "orientation": {"core_question": "Why?"}, "assistant_notes": [], "assignment_focus": {"task_ids": ["done"], "required_chapters": [6]}, "learning_progress": {"mastery_verified": False}, "teaching_cursor": {
                "next_step": "continue", "updated_at": "2026-10-18", "session_handoff": {"saved_at": "2026-10-19T03:00:00+08:00", "start_here": "old bank problem", "recent_correct": ["OLD_SOLUTION"]},
                "pending_check": {"id": "pending", "prompt": "full problem " * 1000, "status": "partially_answered_correct", "answered_at": "2026-10-19T17:00:00+08:00", "scaffolding": "shown earlier example", "answer_key": "SECRET_SOLUTION", "answer": "PARTIAL_ANSWER", "assessment": "SECRET_MARKING", "nested": {"solution": "NESTED_SECRET"}, "custom_answer_notes": "UNKNOWN_SECRET", "options": [{"text": "option A", "correct": True, "rationale": "RATIONALE_SECRET"}]},
                "checks": [{"id": "supported", "at": "2026-10-18", "user_answer": "1", "assisted": True, "independent": False, "fsrs_rating": None}, {"id": "independent", "at": "2026-10-19T18:00:00+08:00", "user_answer": "2", "assisted": False, "independent": True, "mastery_scope": "one example only", "fsrs_rating": None}],
            }},
            {"id": "B", "name": "Biology", "aliases": [], "current_topic": None, "teaching_cursor": None},
        ],
        "tasks": [{"id": "done", "course_id": "A", "title": "group submitted", "status": "completed", "submission_status": "group_representative_submitted", "deadline_id": "D"}, {"id": "other", "course_id": "B", "status": "open"}],
        "deadlines": [{"id": "D", "course_id": "A", "date": "2026-10-23", "time": None}],
        "mistakes": [{"id": "Q", "course_id": "A", "entry_type": "question", "date": "2026-10-22", "original_question": "why?", "correct_answer_or_method": "HIDDEN", "review_status": "not independently checked"}, {"id": "M", "course_id": "A", "entry_type": "mistake", "date": "2026-10-21", "original_question": "what?"}, {"id": "B-M", "course_id": "B", "entry_type": "mistake"}],
        "review_sessions": [{"id": "S", "date": "2026-10-22", "items": [{"course_id": "A", "prompt": "A only"}, {"course_id": "B", "prompt": "B excluded"}]}],
    }


class LibraryContextTests(unittest.TestCase):
    def test_current_pending_preserved_full_and_newer_than_handoff(self):
        state = fixture()
        cursor = build_context(state, "A")["sections"]["course"]["teaching_cursor"]
        self.assertEqual(cursor["pending_questions"][0]["record"]["prompt"], state["courses"][0]["teaching_cursor"]["pending_check"]["prompt"])
        self.assertEqual(cursor["pending_questions"][0]["record"]["status"], "partially_answered_correct")
        self.assertEqual(cursor["session_handoff"]["record"]["saved_at"], "2026-10-19T03:00:00+08:00")

    def test_default_hides_answers_recursively(self):
        rendered = json.dumps(build_context(fixture(), "A"))
        for secret in ("SECRET_SOLUTION", "PARTIAL_ANSWER", "SECRET_MARKING", "NESTED_SECRET", "OLD_SOLUTION", "HIDDEN", "UNKNOWN_SECRET", "RATIONALE_SECRET"):
            self.assertNotIn(secret, rendered)
        pending = build_context(fixture(), "A")["sections"]["course"]["teaching_cursor"]["pending_questions"][0]["record"]
        self.assertEqual(pending["options"], [{"text": "option A"}])

    def test_independent_and_assisted_are_recorded_not_inferred(self):
        state = fixture()
        cursor = build_context(state, "A")["sections"]["course"]["teaching_cursor"]
        record = cursor["latest_attempt_context"]["record"]
        self.assertTrue(record["independent"])
        self.assertFalse(record["assisted"])
        self.assertIsNone(record["fsrs_rating"])
        self.assertEqual(record["mastery_scope"], "one example only")
        state["courses"][0]["teaching_cursor"]["checks"].pop()
        state["courses"][0]["teaching_cursor"].pop("pending_check")
        record = build_context(state, "A")["sections"]["course"]["teaching_cursor"]["latest_attempt_context"]["record"]
        self.assertTrue(record["assisted"])
        self.assertFalse(record["independent"])

    def test_question_not_mistake_and_submission_not_mastery(self):
        result = build_context(fixture(), "A")
        self.assertEqual(result["summary"]["learning_entries_by_recorded_type"], {"question": 1, "mistake": 1})
        self.assertFalse(result["sections"]["course"]["learning_progress"]["mastery_verified"])
        self.assertEqual(result["sections"]["tasks"][0]["submission_status"], "group_representative_submitted")
        self.assertEqual(result["sections"]["tasks"][0]["status"], "completed")
        self.assertIsNone(result["sections"]["deadlines"][0]["time"])

    def test_no_mutation_and_alias(self):
        state = fixture()
        original = copy.deepcopy(state)
        result = build_context(state, "会计")
        result["sections"]["course"]["teaching_cursor"]["pending_questions"][0]["record"]["prompt"] = "modified"
        full = build_context(state, "A", True)
        full["sections"]["course"]["name"] = "modified"
        read_detail(state, "courses", "A")["record"]["name"] = "modified"
        self.assertEqual(state, original)

    def test_course_filter_including_shared_session(self):
        full = build_context(fixture(), "A", True)
        self.assertEqual([r["id"] for r in full["sections"]["tasks"]], ["done"])
        self.assertEqual(full["sections"]["review_sessions"][0]["items"], [{"course_id": "A", "prompt": "A only"}])
        self.assertNotIn("B-M", json.dumps(full))

    def test_explicit_detail_is_lossless(self):
        state = fixture()
        raw = state["courses"][0]["teaching_cursor"]["pending_check"]
        result = read_detail(state, "path", "/courses/0/teaching_cursor/pending_check")
        self.assertEqual(result["record"], raw)
        self.assertEqual(result["state_revision"], 8)
        self.assertTrue(result["complete"])
        self.assertEqual(read_detail(state, "mistakes", "Q")["record"], state["mistakes"][0])

    def test_long_notes_have_explicit_lossless_pointer(self):
        state = fixture()
        state["courses"][0]["assistant_notes"] = [{"text": "long" * 1000}]
        result = build_context(state, "A")
        omission = next(x for x in result["omitted"] if x["path"] == "/courses/0/assistant_notes")
        self.assertEqual(omission["indices"], [0])
        self.assertEqual(read_detail(state, "path", omission["path"])["record"], state["courses"][0]["assistant_notes"])

    def test_unknown_course_and_sensitive_path_rejected(self):
        with self.assertRaises(ValueError):
            build_context(fixture(), "unknown")
        with self.assertRaises(ValueError):
            read_detail(fixture(), "path", "/integrations")
        with self.assertRaises(KeyError):
            read_detail(fixture(), "path", "/courses/-1")

    def test_unknown_stays_null_and_overview_small(self):
        result = build_context(fixture(), "B")
        self.assertIsNone(result["sections"]["course"]["current_topic"])
        self.assertIsNone(result["sections"]["course"]["teaching_cursor"])
        self.assertIsNone(result["summary"]["record_counts"]["materials"])
        self.assertLess(len(json.dumps(build_context(fixture()))), 3000)


if __name__ == "__main__":
    unittest.main()
