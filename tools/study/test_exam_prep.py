"""Synthetic tests; no learning state or actual user attempts are written."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest

import exam_prep as exam
import review

AT = datetime(2026, 10, 11, 12, tzinfo=review.HK)


def fixture():
    return {"courses": [{"id": "COURSE"}], "materials": [{"id": "SLIDES", "course_id": "COURSE", "pages": 10, "sha256": "original"}],
        "mistakes": [], "review_queue": [], "exam_profiles": [{"id": "E1", "course_id": "COURSE", "title": "期中考试", "exam_date": "2026-10-21",
        "scope_status": "confirmed", "topics": [{"id": "T1", "label": "加法", "material_ids": ["SLIDES"], "sources": []}],
        "format_status": "confirmed", "duration_minutes": 30,
        "sections": [{"id": "MC", "question_type": "multiple_choice", "count": 1, "total_marks": 2},
                     {"id": "LA", "question_type": "long_answer", "count": 1, "total_marks": 4}],
        "sources": [{"url": "https://moodle.example.edu/course/view.php?id=1", "location": "Teacher announcement", "excerpt": "One MC and one long answer", "authority": "teacher", "checked_at": AT.isoformat()}],
        "notes": [], "excluded_topics": []}]}


def paper():
    def question(qid, section, typ, marks):
        return {"id": qid, "section_id": section, "question_type": typ, "topic_ids": ["T1"],
            "stem": "求 1+2 并说明。", "marks": marks, "answer": "PRIVATE_ANSWER_3", "rubric": [{"point": "PRIVATE_RUBRIC 加法结果", "marks": marks}],
            "sources": [{"material_id": "SLIDES", "pages": [2], "detail": "核对课件基本加法"}],
            "verified": True, "verification": {"method": "calculation", "detail": "1+2=3"}}
    mc = question("Q1", "MC", "multiple_choice", 2)
    mc.update(options={"A": "2", "B": "3"}, answer="B")
    return {"id": "P1", "exam_id": "E1", "title": "原创模拟 A", "questions": [mc, question("Q2", "LA", "long_answer", 4)], "assumptions": []}


def attempt(**updates):
    value = {"attempt_id": "A1", "mock_id": "P1", "submitted_at": (AT + timedelta(minutes=30)).isoformat(), "actual_minutes": 20,
        "answers": [{"question_id": "Q1", "answer": "A", "independent": True, "answer_seen_before_attempt": False,
                     "feedback": "计算得到 3；选择有误。", "evidence": "USER_MESSAGE_1"},
                    {"question_id": "Q2", "answer": "3", "independent": True, "answer_seen_before_attempt": False,
                     "feedback": "正确独立计算。", "evidence": "USER_MESSAGE_2", "awarded_marks": 4, "rubric_scores": [4]}]}
    value.update(updates)
    return value


class ExamPrepTests(unittest.TestCase):
    def full_scope_fixture(self):
        state, payload = fixture(), paper()
        profile = state["exam_profiles"][0]
        profile.update(format_status="partial", total_marks=6)
        profile["sections"][0]["marks_per_question"] = 2
        profile["sections"][1]["count"] = None
        payload.update(mode="full_scope_mock", assistant_structure=[{
            "section_id": "LA", "count": 1, "marks": [4],
            "reason": "老师未公布长题题数和逐题分值，本卷由助手安排。"}])
        return state, payload

    def test_full_scope_preserves_partial_profile_and_discloses_assistant_layout(self):
        state, payload = self.full_scope_fixture()
        result = exam.register_mock(state, payload, AT)
        self.assertEqual(result["mode"], "full_scope_mock")
        self.assertEqual(result["uncovered_topic_ids"], [])
        self.assertEqual(state["exam_profiles"][0]["format_status"], "partial")
        self.assertIsNone(state["exam_profiles"][0]["sections"][1]["count"])
        rendered = exam.render_mock(state, "P1")
        self.assertIn("全范围模拟卷", rendered)
        self.assertIn("老师未公布题数、逐题分值", rendered)
        self.assertIn("本卷安排 1 题，各题依次为 4 分，由助手设定", rendered)
        self.assertIn("未公布的结构细节由助手安排", exam.render_catalog(state, AT))
        self.assertNotIn("PRIVATE_", rendered)
        self.assertNotIn("mock_attempts", state)

    def test_full_scope_never_relaxes_full_mock(self):
        state, payload = self.full_scope_fixture()
        payload["mode"] = "full_mock"
        with self.assertRaises(ValueError):
            exam.register_mock(state, payload, AT)
        state["exam_profiles"][0]["format_status"] = "confirmed"
        with self.assertRaises(ValueError):
            exam.register_mock(state, payload, AT)

    def test_full_scope_requires_known_official_scope_types_time_and_total_marks(self):
        scenarios = [lambda s: s.update(scope_status="partial"),
                     lambda s: s.update(format_status="unknown"),
                     lambda s: s.update(duration_minutes=None),
                     lambda s: s.update(total_marks=None),
                     lambda s: s["sections"][1].update(question_type="unknown"),
                     lambda s: s["sections"][1].update(total_marks=None)]
        for mutate in scenarios:
            with self.subTest(mutate=mutate):
                state, payload = self.full_scope_fixture()
                mutate(state["exam_profiles"][0])
                before = deepcopy(state)
                with self.assertRaises(ValueError):
                    exam.register_mock(state, payload, AT)
                self.assertEqual(state, before)

    def test_full_scope_requires_explicit_accurate_assistant_structure(self):
        scenarios = [lambda p: p.pop("assistant_structure"),
                     lambda p: p["assistant_structure"].append(deepcopy(p["assistant_structure"][0])),
                     lambda p: p["assistant_structure"][0].update(section_id="MC"),
                     lambda p: p["assistant_structure"][0].update(count=2),
                     lambda p: p["assistant_structure"][0].update(marks=[3]),
                     lambda p: p["assistant_structure"][0].update(reason="")]
        for mutate in scenarios:
            with self.subTest(mutate=mutate):
                state, payload = self.full_scope_fixture()
                mutate(payload)
                before = deepcopy(state)
                with self.assertRaises(ValueError):
                    exam.register_mock(state, payload, AT)
                self.assertEqual(state, before)

    def test_full_scope_enforces_all_published_counts_and_marks(self):
        scenarios = [lambda s: s["sections"][0].update(count=2, total_marks=4),
                     lambda s: s["sections"][0].update(marks_per_question=3, total_marks=3),
                     lambda s: s["sections"][1].update(total_marks=5),
                     lambda s: s.update(total_marks=7)]
        for mutate in scenarios:
            with self.subTest(mutate=mutate):
                state, payload = self.full_scope_fixture()
                mutate(state["exam_profiles"][0])
                with self.assertRaises(ValueError):
                    exam.register_mock(state, payload, AT)
                self.assertNotIn("mock_exams", state)

    def test_full_scope_requires_every_topic_and_its_listed_materials(self):
        scenarios = [lambda s: s["exam_profiles"][0]["topics"][0].update(material_ids=[]),
                     lambda s: s["exam_profiles"][0]["topics"][0]["material_ids"].append("MISSING"),
                     lambda s: s["materials"][0].update(course_id="ANOTHER_COURSE"),
                     lambda s: s["exam_profiles"][0]["topics"].append({"id": "T2", "label": "未覆盖", "material_ids": ["SLIDES"]})]
        for mutate in scenarios:
            with self.subTest(mutate=mutate):
                state, payload = self.full_scope_fixture()
                mutate(state)
                with self.assertRaises(ValueError):
                    exam.register_mock(state, payload, AT)
                self.assertNotIn("mock_exams", state)

    def test_full_scope_new_paper_preserves_existing_targeted_paper(self):
        state, payload = self.full_scope_fixture()
        old = paper()
        old["mode"] = "targeted_practice"
        exam.register_mock(state, old, AT)
        previous = deepcopy(state["mock_exams"][0])
        payload["id"] = "P2"
        exam.register_mock(state, payload, AT)
        self.assertEqual(len(state["mock_exams"]), 2)
        self.assertEqual(state["mock_exams"][0], previous)
        self.assertTrue(exam.mock_is_current(state, state["mock_exams"][1]))

    def test_full_scope_duration_cannot_override_teacher_time(self):
        state, payload = self.full_scope_fixture()
        payload["duration_minutes"] = 50
        with self.assertRaises(ValueError):
            exam.register_mock(state, payload, AT)
        payload["duration_minutes"] = 30
        exam.register_mock(state, payload, AT)
        self.assertIn("正式考试时长：30 分钟", exam.render_mock(state, "P1"))

    def test_full_scope_freshness_tracks_every_required_material(self):
        state, payload = self.full_scope_fixture()
        state["materials"].append({"id": "SECOND_PART", "course_id": "COURSE", "pages": 4, "sha256": "second-original"})
        state["exam_profiles"][0]["topics"][0]["material_ids"].append("SECOND_PART")
        exam.register_mock(state, payload, AT)
        self.assertTrue(exam.mock_is_current(state, state["mock_exams"][0]))
        state["materials"][1]["sha256"] = "changed"
        self.assertFalse(exam.mock_is_current(state, state["mock_exams"][0]))

    def test_teacher_section_scope_cannot_be_filled_with_other_chapters(self):
        state = fixture()
        state["exam_profiles"][0]["sections"][1]["allowed_topic_ids"] = ["T2"]
        with self.assertRaises(ValueError):
            exam.register_mock(state, paper(), AT)
        self.assertNotIn("mock_exams", state)

    def prepared(self):
        state = fixture()
        exam.register_mock(state, paper(), AT)
        return state

    def test_paper_and_catalog_never_include_answer_rubric(self):
        state = self.prepared()
        output = exam.render_mock(state, "P1") + exam.render_catalog(state, AT)
        self.assertNotIn("PRIVATE_", output)
        with self.assertRaises(ValueError):
            exam.render_mock(state, "P1", answers=True)
        self.assertNotIn("mock_attempts", state)

    def test_register_idempotency_and_conflict(self):
        state = self.prepared()
        before = deepcopy(state)
        self.assertEqual(exam.register_mock(state, paper(), AT)["status"], "duplicate")
        self.assertEqual(state, before)
        changed = paper()
        changed["title"] += " changed"
        with self.assertRaises(ValueError):
            exam.register_mock(state, changed, AT)
        self.assertEqual(state, before)

    def test_scope_format_counts_marks_and_sources_rejected_atomically(self):
        scenarios = [lambda s, p: s["exam_profiles"][0].update(format_status="unknown"),
                     lambda s, p: s["exam_profiles"][0].update(scope_status="partial"),
                     lambda s, p: s["exam_profiles"][0]["sections"][0].update(count=2),
                     lambda s, p: s["exam_profiles"][0]["sections"][0].update(total_marks=3),
                     lambda s, p: p["questions"][0]["sources"][0].update(pages=[11]),
                     lambda s, p: p["questions"][0].update(topic_ids=["UNKNOWN"]),
                     lambda s, p: p["questions"][0].update(options={"A": "3", "B": "3"}),
                     lambda s, p: p["questions"][0].update(answer="C"),
                     lambda s, p: s["exam_profiles"][0].update(excluded_topics=["T1"]),
                     lambda s, p: p["questions"][0].update(verified=False)]
        for mutate in scenarios:
            with self.subTest(mutate=mutate):
                state, payload = fixture(), paper()
                mutate(state, payload)
                before = deepcopy(state)
                with self.assertRaises(ValueError):
                    exam.register_mock(state, payload, AT)
                self.assertEqual(state, before)

    def test_missing_topic_blocks_full_but_targeted_names_gap(self):
        state = fixture()
        state["exam_profiles"][0]["topics"].append({"id": "T2", "label": "缺失课件章节", "material_ids": [], "sources": []})
        with self.assertRaises(ValueError):
            exam.register_mock(state, paper(), AT)
        payload = paper()
        payload["mode"] = "targeted_practice"
        exam.register_mock(state, payload, AT)
        output = exam.render_mock(state, "P1")
        self.assertIn("缺失课件章节", output)
        self.assertIn("不能当作完整", output)

    def test_partial_format_targeted_only_known_teacher_types(self):
        state, payload = fixture(), paper()
        state["exam_profiles"][0]["format_status"] = "partial"
        state["exam_profiles"][0]["sections"][0]["count"] = None
        with self.assertRaises(ValueError):
            exam.register_mock(state, payload, AT)
        payload["mode"] = "targeted_practice"
        exam.register_mock(state, payload, AT)
        self.assertIn("专项", exam.render_catalog(state, AT))

    def test_unknown_marks_require_disclosed_assumption(self):
        state, payload = fixture(), paper()
        state["exam_profiles"][0]["sections"][0]["total_marks"] = None
        with self.assertRaises(ValueError):
            exam.register_mock(state, payload, AT)
        payload["assumptions"] = ["本卷分值由助手设定，老师尚未公布。"]
        exam.register_mock(state, payload, AT)
        self.assertIn("分值由助手设定", exam.render_mock(state, "P1"))

    def test_teacher_option_count_and_per_question_marks_enforced(self):
        state = fixture()
        section = state["exam_profiles"][0]["sections"][0]
        section.update(option_count=5, marks_per_question=2)
        with self.assertRaises(ValueError):
            exam.register_mock(state, paper(), AT)
        payload = paper()
        payload["questions"][0]["options"].update(C="4", D="5", E="6")
        exam.register_mock(state, payload, AT)
        self.assertTrue(exam.mock_is_current(state, state["mock_exams"][0]))
        state = fixture()
        state["exam_profiles"][0]["sections"][0].update(marks_per_question=3, total_marks=3)
        with self.assertRaises(ValueError):
            exam.register_mock(state, paper(), AT)

    def test_changed_moodle_evidence_requires_review_before_any_use(self):
        state = self.prepared()
        state["exam_profiles"][0]["verification_status"] = "needs_review"
        self.assertFalse(exam.mock_is_current(state, state["mock_exams"][0]))
        self.assertIn("Moodle", exam.mock_stale_reason(state, state["mock_exams"][0]))
        self.assertIn("暂停", exam.render_catalog(state, AT))
        self.assertIn("已过期", exam.render_mock(state, "P1"))
        payload = paper()
        payload["id"] = "P2"
        with self.assertRaises(ValueError):
            exam.register_mock(state, payload, AT)
        with self.assertRaises(ValueError):
            exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "查看答案"}, AT)

    def test_requirement_change_stales_paper_and_grading_not_refresh_timestamp(self):
        state = self.prepared()
        state["exam_profiles"][0]["sources"][0]["checked_at"] = (AT + timedelta(hours=1)).isoformat()
        state["exam_profiles"][0]["latest_source_check"] = {"at": (AT + timedelta(hours=1)).isoformat(), "issues": []}
        state["exam_profiles"][0]["verified_at"] = (AT + timedelta(hours=1)).isoformat()
        state["exam_profiles"][0]["reviewed_candidates_hash"] = "new-hash"
        state["exam_profiles"][0]["previous_requirements"] = [{"old": "previous"}]
        self.assertNotIn("已过期", exam.render_mock(state, "P1"))
        state["exam_profiles"][0]["duration_minutes"] = 60
        self.assertIn("已过期", exam.render_mock(state, "P1"))
        self.assertNotIn("求 1+2", exam.render_mock(state, "P1"))
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))

    def test_same_attachment_retrieval_time_does_not_stale_but_content_hash_does(self):
        state = fixture()
        source = state["exam_profiles"][0]["sources"][0]
        source.update(location="attachment:1", sha256="original-attachment",
                      retrieved_at=AT.isoformat(), verified_at=AT.isoformat())
        exam.register_mock(state, paper(), AT)
        source = state["exam_profiles"][0]["sources"][0]
        source.update(retrieved_at=(AT + timedelta(hours=1)).isoformat(),
                      verified_at=(AT + timedelta(hours=1)).isoformat())
        self.assertTrue(exam.mock_is_current(state, state["mock_exams"][0]))
        source["sha256"] = "changed-attachment-content"
        self.assertFalse(exam.mock_is_current(state, state["mock_exams"][0]))

    def test_changed_material_or_tampered_paper_stales(self):
        for field in ("source", "question"):
            state = self.prepared()
            if field == "source":
                state["materials"][0]["sha256"] = "changed"
            else:
                state["mock_exams"][0]["questions"][0]["stem"] = "changed"
            self.assertIn("已过期", exam.render_mock(state, "P1"))

    def test_exposure_tracks_answers_without_mastery_or_fsrs(self):
        state = self.prepared()
        payload = {"mock_id": "P1", "evidence": "用户要求查看答案"}
        exam.mark_mock_answers_shown(state, payload, AT + timedelta(minutes=10))
        self.assertIn("PRIVATE_ANSWER", exam.render_mock(state, "P1", answers=True))
        before = deepcopy(state)
        self.assertEqual(exam.mark_mock_answers_shown(state, payload, AT + timedelta(minutes=11))["status"], "duplicate")
        self.assertEqual(state, before)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))
        payload = attempt()
        for row in payload["answers"]:
            row["answer_seen_before_attempt"] = True
        exam.record_mock_attempt(state, payload, AT + timedelta(hours=1))
        self.assertFalse(state["mock_attempts"][0]["independent"])
        self.assertEqual(state["mistakes"], [])
        self.assertEqual(state["review_queue"], [])

    def test_answer_given_before_later_exposure_retains_real_evidence(self):
        state = self.prepared()
        exam.mark_mock_answers_shown(state, {"mock_id": "P1", "evidence": "事后展示"}, AT + timedelta(minutes=40))
        exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))
        self.assertTrue(state["mock_attempts"][0]["independent"])

    def test_actual_wrong_answers_queue_new_retest_without_fake_variants(self):
        state = self.prepared()
        result = exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))
        self.assertEqual(result["total_marks"], 4)
        self.assertEqual(len(state["mistakes"]), 1)
        self.assertEqual(len(state["review_queue"]), 1)
        self.assertEqual(state["review_queue"][0]["practice_variants"], [])
        self.assertNotIn("fsrs", state["review_queue"][0])
        self.assertNotIn("mastery", state["courses"][0])
        self.assertTrue(state["mock_attempts"][0]["question_results"][1]["correct"])
        before = deepcopy(state)
        self.assertEqual(exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))["status"], "duplicate")
        self.assertEqual(state, before)
        changed = attempt()
        changed["answers"][0]["answer"] = "B"
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, changed, AT + timedelta(hours=1))
        self.assertEqual(state, before)

    def test_repeated_real_wrong_answer_merges_history(self):
        state = self.prepared()
        exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))
        exam.record_mock_attempt(state, attempt(attempt_id="A2"), AT + timedelta(hours=2))
        self.assertEqual(len(state["mistakes"]), 1)
        self.assertEqual(state["mistakes"][0]["occurrences"], 2)
        self.assertEqual(len(state["review_queue"]), 1)

    def test_feedback_prevents_independent_repeat_after_grading(self):
        state = self.prepared()
        exam.record_mock_attempt(state, attempt(), AT + timedelta(hours=1))
        payload = attempt(attempt_id="A2", submitted_at=(AT + timedelta(hours=2)).isoformat())
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, payload, AT + timedelta(hours=2))
        self.assertEqual(state, before)
        for answer in payload["answers"]:
            answer["answer_seen_before_attempt"] = True
        exam.record_mock_attempt(state, payload, AT + timedelta(hours=2))
        self.assertFalse(state["mock_attempts"][1]["independent"])
        self.assertEqual(state["mistakes"][0]["occurrences"], 1)

    def test_invalid_later_answer_does_not_partially_write(self):
        state = self.prepared()
        payload = attempt()
        payload["answers"][1]["rubric_scores"] = [5]
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            exam.record_mock_attempt(state, payload, AT + timedelta(hours=1))
        self.assertEqual(state, before)

    def test_partial_attempt_not_fabricated_zero_for_omitted(self):
        state = self.prepared()
        payload = attempt()
        payload["answers"] = payload["answers"][:1]
        result = exam.record_mock_attempt(state, payload, AT + timedelta(hours=1))
        self.assertFalse(result["complete"])
        self.assertEqual(result["max_marks"], 2)
        self.assertEqual(len(state["mock_attempts"][0]["question_results"]), 1)


if __name__ == "__main__":
    unittest.main()
