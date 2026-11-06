"""Cross-ID exposure regressions; all sources and learning records are synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

import exam_prep as exam
import learning_loop as loop
from question_identity import question_key
from test_exam_prep import AT, fixture as exam_state, paper, attempt as exam_attempt
from test_learning_loop import fixture as review_state, attempt as review_attempt


class QuestionExposureTests(unittest.TestCase):
    def review_pair(self):
        state = review_state(2, 1)
        left, right = state["review_queue"]
        right["course_id"] = left["course_id"]
        right["practice_variants"] = deepcopy(left["practice_variants"])
        return state

    def legacy_pair(self):
        state = self.review_pair()
        package = loop.prepare(state, AT)
        # Existing states can already contain the duplicate. No new fields required.
        first = state["review_sessions"][0]["items"][0]
        second = deepcopy(first)
        second["review_id"] = "R1"
        state["review_sessions"][0]["items"] = [first, second]
        return state, package

    def test_prepare_deduplicates_same_source_question_across_review_ids(self):
        state = self.review_pair()
        package = loop.prepare(state, AT)
        self.assertEqual(len(package["items"]), 1)

    def test_legacy_duplicate_review_cannot_rate_after_exposure(self):
        state, package = self.legacy_pair()
        loop.mark_answer_shown(state, {"session_id": package["session_id"], "review_id": "R0",
            "variant_id": "V0", "shown_at": AT.isoformat(), "evidence": "shown exact question"})
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=1), review_id="R1"))
        self.assertEqual(state, before)
        assisted = review_attempt(package, AT + timedelta(minutes=1), review_id="R1",
            independent=False, rating=None, answer_seen_before_attempt=True)
        self.assertFalse(loop.record_attempt(state, assisted)["fsrs_rated"])
        self.assertEqual(state["review_attempts"][0]["answer"], assisted["answer"])

    def test_later_exposure_does_not_reject_earlier_real_review_answer(self):
        state, package = self.legacy_pair()
        loop.mark_answer_shown(state, {"session_id": package["session_id"], "review_id": "R0",
            "variant_id": "V0", "shown_at": (AT + timedelta(minutes=20)).isoformat(), "evidence": "later"})
        result = loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=10), review_id="R1"))
        self.assertTrue(result["fsrs_rated"])

    def test_earlier_exposure_can_be_backfilled_across_review_ids(self):
        state, package = self.legacy_pair()
        payload = {"session_id": package["session_id"], "review_id": "R0", "variant_id": "V0",
            "shown_at": (AT + timedelta(minutes=20)).isoformat(), "evidence": "later disclosure"}
        loop.mark_answer_shown(state, payload)
        earlier = {**payload, "review_id": "R1", "shown_at": (AT + timedelta(minutes=10)).isoformat(),
            "evidence": {"type": "user_message", "message_id": "earliest-disclosure"}}
        self.assertEqual(loop.mark_answer_shown(state, earlier)["status"], "recorded")
        for item in state["review_sessions"][0]["items"]:
            self.assertEqual(item["answer_shown_at"], earlier["shown_at"])
            self.assertEqual(item["answer_shown_evidence"], earlier["evidence"])
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=15), review_id="R1"))
        self.assertEqual(state, before)
        self.assertEqual(loop.mark_answer_shown(state, earlier)["status"], "duplicate")
        self.assertEqual(loop.mark_answer_shown(state, payload)["status"], "duplicate")
        self.assertEqual(state, before)

    def test_duplicate_exposure_still_validates_timestamp_without_mutation(self):
        state, package = self.legacy_pair()
        payload = {"session_id": package["session_id"], "review_id": "R0", "variant_id": "V0",
            "shown_at": AT.isoformat(), "evidence": "original disclosure"}
        loop.mark_answer_shown(state, payload)
        before = deepcopy(state)
        for invalid in ("not-a-date", (AT - timedelta(minutes=1)).isoformat(),
                        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
            with self.subTest(shown_at=invalid):
                with self.assertRaises(ValueError):
                    loop.mark_answer_shown(state, {**payload, "shown_at": invalid})
                self.assertEqual(state, before)

    def test_review_feedback_blocks_copy_without_rewriting_original_attempt(self):
        state, package = self.legacy_pair()
        loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=1)))
        first = deepcopy(state["review_attempts"][0])
        with self.assertRaises(ValueError):
            loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=2),
                review_id="R1", attempt_id="SECOND"))
        self.assertEqual(state["review_attempts"], [first])
        self.assertNotIn("fsrs", state["review_queue"][1])

    def test_different_course_or_changed_prompt_remains_a_new_review(self):
        for difference in ("course", "prompt"):
            with self.subTest(difference=difference):
                state = self.review_pair()
                if difference == "course":
                    state["review_queue"][1]["course_id"] = "ECON1210"
                else:
                    state["review_queue"][1]["practice_variants"][0]["prompt"] = "A genuinely new variant"
                package = loop.prepare(state, AT)
                self.assertEqual(len(package["items"]), 2)
                loop.mark_answer_shown(state, {"session_id": package["session_id"], "review_id": "R0",
                    "variant_id": "V0", "shown_at": AT.isoformat(), "evidence": "first only"})
                self.assertTrue(loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=1),
                    review_id="R1"))["fsrs_rated"])

    def mock_pair(self, change=None):
        state = exam_state()
        exam.register_mock(state, paper(), AT)
        other = paper()
        other["id"] = "P2"
        if change:
            change(state, other)
        for question in other["questions"]:
            question["id"] = "COPY-" + question["id"]
        exam.register_mock(state, other, AT)
        request = exam_attempt(attempt_id="A2", mock_id="P2")
        for answer in request["answers"]:
            answer["question_id"] = "COPY-" + answer["question_id"]
        return state, request

    def test_mock_copy_new_paper_and_question_ids_cannot_hide_exposure(self):
        state, request = self.mock_pair()
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "original answers"}, AT + timedelta(minutes=10))
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertEqual(state, before)
        for answer in request["answers"]:
            answer["answer_seen_before_attempt"] = True
        exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertFalse(state["mock_attempts"][0]["independent"])
        self.assertEqual(state["mock_exams"][0], before["mock_exams"][0])

    def test_mock_copy_cannot_repeat_independent_after_grading(self):
        state, request = self.mock_pair()
        exam.record_mock_attempt(state, exam_attempt(), AT + timedelta(hours=1))
        request["submitted_at"] = (AT + timedelta(hours=2)).isoformat()
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, request, AT + timedelta(hours=2))

    def test_mock_copy_earlier_answer_remains_independent(self):
        state, request = self.mock_pair()
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "later"}, AT + timedelta(minutes=40))
        exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertTrue(state["mock_attempts"][0]["independent"])

    def test_mock_earlier_exposure_is_appended_and_blocks_intermediate_attempt(self):
        state, request = self.mock_pair()
        payload = {"mock_id": "P1", "question_ids": ["Q1"], "evidence": "later disclosure"}
        exam.mark_mock_answers_shown(state, payload, AT + timedelta(minutes=40))
        first = deepcopy(state["mock_exams"][0]["answer_exposures"])
        earlier = {**payload, "evidence": {"message_id": "earlier-disclosure"}}
        result = exam.mark_mock_answers_shown(state, earlier, AT + timedelta(minutes=10))
        self.assertEqual(result["status"], "recorded")
        events = state["mock_exams"][0]["answer_exposures"]
        self.assertEqual(events[:1], first)
        self.assertEqual(events[1]["shown_at"], (AT + timedelta(minutes=10)).isoformat())
        self.assertEqual(events[1]["evidence"], earlier["evidence"])
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertEqual(state, before)
        self.assertEqual(exam.mark_mock_answers_shown(state, earlier, AT + timedelta(minutes=10))["status"], "duplicate")
        self.assertEqual(exam.mark_mock_answers_shown(state, payload, AT + timedelta(minutes=40))["status"], "duplicate")
        self.assertEqual(state, before)

    def test_mock_partial_backfill_only_adds_questions_without_earlier_exposure(self):
        state = exam_state()
        exam.register_mock(state, paper(), AT)
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "question_ids": ["Q1"], "evidence": "first"}, AT)
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "later both"}, AT + timedelta(minutes=40))
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "earlier both"}, AT + timedelta(minutes=10))
        self.assertEqual(state["mock_exams"][0]["answer_exposures"][-1]["question_ids"], ["Q2"])
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "invalid"}, AT - timedelta(minutes=1))
        self.assertEqual(state, before)

    def test_changed_mock_stems_remain_independent(self):
        def change(state, other):
            for question in other["questions"]:
                question["stem"] += " 改为另一组条件。"
        state, request = self.mock_pair(change)
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "first only"}, AT)
        exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertTrue(state["mock_attempts"][0]["independent"])

    def test_mock_exposure_is_per_question_not_whole_copied_paper(self):
        state, request = self.mock_pair()
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "question_ids": ["Q1"],
            "evidence": "MC only"}, AT + timedelta(minutes=10))
        request["answers"] = [request["answers"][1]]
        exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertTrue(state["mock_attempts"][0]["independent"])

    def shared_review_and_mock(self):
        state = exam_state()
        exam.register_mock(state, paper(), AT)
        question = paper()["questions"][1]
        review = review_state(1, 1)
        entry = review["review_queue"][0]
        entry["course_id"] = "COURSE"
        entry["practice_variants"][0].update(prompt=question["stem"], source=deepcopy(question["sources"][0]))
        state["review_queue"] = review["review_queue"]
        return state

    def test_mock_exposure_prevents_same_question_becoming_unseen_review(self):
        state = self.shared_review_and_mock()
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "question_ids": ["Q2"],
            "evidence": "long answer shown"}, AT + timedelta(minutes=1))
        package = loop.prepare(state, AT + timedelta(minutes=2))
        self.assertEqual(package["items"], [])
        self.assertTrue(package["needs_preparation"])

    def test_mock_exposure_withdraws_already_pending_review_on_next_prepare(self):
        state = self.shared_review_and_mock()
        package = loop.prepare(state, AT)
        original = deepcopy(state["review_sessions"][0]["items"][0])
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "question_ids": ["Q2"],
            "evidence": "same question shown in mock"}, AT + timedelta(minutes=1))
        refreshed = loop.prepare(state, AT + timedelta(minutes=2))
        self.assertEqual(refreshed["items"][0]["status"], "explained")
        self.assertNotIn("prompt", refreshed["items"][0])
        self.assertEqual(state["review_sessions"][0]["items"][0]["source"], original["source"])
        with self.assertRaises(ValueError):
            loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=2)))

    def test_review_exposure_prevents_same_question_becoming_unseen_mock(self):
        state = self.shared_review_and_mock()
        package = loop.prepare(state, AT)
        loop.mark_answer_shown(state, {"session_id": package["session_id"], "review_id": "R0",
            "variant_id": "V0", "shown_at": AT.isoformat(), "evidence": "review answer shown"})
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, exam_attempt(), AT + timedelta(hours=1))
        self.assertEqual(state, before)

    def test_later_assisted_review_preserves_earliest_exposure_and_evidence(self):
        state = self.shared_review_and_mock()
        package = loop.prepare(state, AT)
        evidence = {"type": "user_message", "message_id": "original-exposure"}
        loop.mark_answer_shown(state, {"session_id": package["session_id"], "review_id": "R0",
            "variant_id": "V0", "shown_at": AT.isoformat(), "evidence": evidence})
        loop.record_attempt(state, review_attempt(package, AT + timedelta(minutes=30),
            independent=False, answer_seen_before_attempt=True, rating=None))
        # Backfilled evidence at t20 is still after the original exposure at t0.
        request = exam_attempt(submitted_at=(AT + timedelta(minutes=20)).isoformat())
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, request, AT + timedelta(hours=1))
        self.assertEqual(state, before)
        item = state["review_sessions"][0]["items"][0]
        self.assertEqual(item["answer_shown_at"], AT.isoformat())
        self.assertEqual(item["answer_shown_evidence"], evidence)

    def test_identity_ignores_ids_and_annotations_but_not_true_variants(self):
        question = paper()["questions"][0]
        key = question_key("COURSE", question)
        copy = deepcopy(question)
        copy.update(id="new-id", answer="changed grading metadata", rubric=[])
        copy["sources"][0]["detail"] = "different audit annotation"
        copy["options"] = {"X": "3", "Y": "2"}
        self.assertEqual(question_key("COURSE", copy), key)
        self.assertNotEqual(question_key("ANOTHER_COURSE", copy), key)
        copy["options"]["X"] = "4"
        self.assertNotEqual(question_key("COURSE", copy), key)
        self.assertIsNone(question_key(None, copy))
        copy["sources"] = []
        self.assertIsNone(question_key("COURSE", copy))


if __name__ == "__main__":
    unittest.main()
