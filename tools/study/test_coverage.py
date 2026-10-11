"""Coverage/reflection regressions: real independent evidence, dates and unknowns."""
from copy import deepcopy
import unittest

import coverage


NOW = "2026-10-11T20:00:00+08:00"


def fixture():
    return {"exam_profiles": [{"id": "E", "course_id": "C", "title": "期中", "exam_date": "2026-10-21",
                               "scope_status": "confirmed", "format_status": "partial",
                               "topics": [{"id": "T", "label": "概念一", "material_ids": ["M"]},
                                          {"id": "T2", "label": "概念二", "material_ids": []}]}],
            "review_queue": [{"id": "R", "course_id": "C", "title": "问题一", "topic_ids": ["T"],
                              "practice_variants": [{"id": "V", "prompt": "问题", "topic_ids": ["T"]}]}],
            "review_attempts": [], "review_sessions": [], "learning_feedback": [], "mock_attempts": [],
            "mock_exams": [{"id": "MO", "course_id": "C", "questions": [{"id": "Q", "topic_ids": ["T"]}]}]}


def attempt(identifier="A", at="2026-10-10T12:00:00+08:00", rating="good", **changes):
    result = {"attempt_id": identifier, "review_id": "R", "variant_id": "V", "prompt": "问题",
              "reviewed_at": at, "independent": True, "answer_seen_before_attempt": False,
              "answer": "实际作答", "evidence": "真实对话#1", "rating": rating,
              "next_review_at": "2026-10-20T12:00:00+08:00"}
    result.update(changes)
    return result


def feedback(identifier="F", **changes):
    result = {"feedback_id": identifier, "date": "2026-10-11", "recorded_at": "2026-10-11T18:00:00+08:00",
              "completed": "复习概念", "evidence": "真实反馈", "time_scope": "session", "actual_minutes": 20}
    result.update(changes)
    return result


def status(state, topic="T"):
    now = coverage.planning.local_now(NOW)
    return coverage.topic_status(state, "C", topic, coverage.evidence_events(state, now), now)["status"]


class CoverageTests(unittest.TestCase):
    def test_empty_data_is_not_zero_mastery_and_views_are_pure(self):
        state = fixture()
        original = deepcopy(state)
        views = coverage.render_views(state, NOW)
        self.assertEqual(original, state)
        self.assertEqual(set(coverage.VIEW_NAMES), set(views))
        self.assertIn("未独立测试", views["Exam Coverage.md"])
        self.assertIn("待补课件", views["Exam Coverage.md"])
        self.assertIn("暂不足以判断", views["Weekly Reflection.md"])
        self.assertNotIn("掌握率", "".join(views.values()))

    def test_submission_grades_and_questions_do_not_establish_test_evidence(self):
        state = fixture()
        state["courses"] = [{"id": "C", "mastery": [{"topic_ids": ["T"], "score": 100}]}]
        state["tasks"] = [{"status": "Completed", "topic_ids": ["T"], "grade": 100}]
        self.assertEqual("未独立测试", status(state))

    def test_only_explicit_links_count_no_chapter_guessing(self):
        state = fixture()
        state["review_queue"][0].pop("topic_ids")
        state["review_queue"][0]["practice_variants"][0].pop("topic_ids")
        state["review_queue"][0]["title"] = "概念一"
        state["review_attempts"] = [attempt(source={"material_id": "M"})]
        self.assertEqual("未独立测试", status(state))
        state["review_attempts"][0]["topic_ids"] = ["T"]
        self.assertEqual("独立做对（单次）", status(state))

    def test_assisted_exposed_missing_evidence_and_future_do_not_upgrade(self):
        for changes in ({"independent": False}, {"answer_seen_before_attempt": True},
                        {"evidence": " "}, {"answer": ""}, {"reviewed_at": "2026-10-12T12:00:00+08:00"}):
            with self.subTest(changes=changes):
                state = fixture()
                state["review_attempts"] = [attempt(**changes)]
                self.assertEqual("未独立测试", status(state))

    def test_answer_exposure_time_checks_prior_not_post_feedback(self):
        state = fixture()
        state["review_attempts"] = [attempt()]
        state["review_sessions"] = [{"items": [{"review_id": "R", "variant_id": "V",
                                                 "answer_shown_at": "2026-10-10T12:00:00+08:00"}]}]
        self.assertEqual("独立做对（单次）", status(state))
        state["review_sessions"][0]["items"][0]["answer_shown_at"] = "2026-10-10T11:00:00+08:00"
        self.assertEqual("未独立测试", status(state))

    def test_same_day_successes_do_not_become_cross_day_stability(self):
        state = fixture()
        state["review_attempts"] = [attempt(), attempt("B", "2026-10-10T13:00:00+08:00", variant_id="V2")]
        self.assertEqual("独立做对（单次）", status(state))
        self.assertIn("跨日独立复测：0", coverage.render_reflection(state, NOW))
        state["review_attempts"][1]["reviewed_at"] = "2026-10-11T13:00:00+08:00"
        self.assertEqual("独立做对（多次）", status(state))
        self.assertIn("跨日独立复测：1", coverage.render_reflection(state, NOW))

    def test_hard_means_difficult_not_wrong_and_errors_need_success_on_two_later_days(self):
        state = fixture()
        state["review_attempts"] = [attempt(rating="hard")]
        self.assertEqual("仍不稳定", status(state))
        self.assertIn("重复失误：0", coverage.render_reflection(state, NOW))
        state["review_attempts"] = [attempt(at="2026-10-07T12:00:00+08:00", rating="again"),
                                   attempt("B", "2026-10-09T12:00:00+08:00")]
        self.assertEqual("仍不稳定", status(state))
        state["review_attempts"].append(attempt("C", "2026-10-10T12:00:00+08:00"))
        self.assertEqual("独立做对（多次）", status(state))

    def test_current_due_supersedes_old_due_and_is_course_specific(self):
        state = fixture()
        state["review_attempts"] = [attempt(next_review_at="2026-10-10T14:00:00+08:00")]
        self.assertEqual("到期复测", status(state))
        state["review_queue"][0]["fsrs"] = {"card": {"due": "2026-10-20T12:00:00+08:00"}}
        self.assertEqual("独立做对（单次）", status(state))
        state["review_queue"].append({"id": "foreign", "course_id": "OTHER", "topic_ids": ["T"], "due_date": "2026-10-01"})
        self.assertEqual("独立做对（单次）", status(state))

    def test_hk_window_and_past_exam_are_separate(self):
        state = fixture()
        state["exam_profiles"][0]["exam_date"] = "2026-10-10"
        state["review_attempts"] = [attempt(at="2026-10-04T15:59:59+00:00")]
        text = coverage.render_views(state, NOW)
        self.assertIn("已过日期的考试", text["Exam Coverage.md"])
        self.assertNotIn("## 待准备的考试", text["Exam Coverage.md"])
        self.assertIn("复习：独立 0", text["Weekly Reflection.md"])
        state["review_attempts"][0]["reviewed_at"] = "2026-10-04T16:00:00+00:00"
        self.assertIn("复习：独立 1", coverage.render_reflection(state, NOW))

    def test_unknown_scope_and_date_never_claim_full_scope_or_future_date(self):
        state = fixture()
        state["exam_profiles"][0].update(exam_date=None, scope_status="partial")
        text = coverage.render_coverage(state, NOW)
        self.assertIn("考试日期待确认", text)
        self.assertIn("不能视为完整考试清单", text)

    def test_mock_flags_and_real_evidence_required_deduplicated_per_question(self):
        state = fixture()
        row = {"question_id": "Q", "answer": "真实答案", "evidence": "作答记录", "independent": True,
               "answer_seen_before_attempt": False, "eligible_independent": True, "correct": True}
        state["mock_attempts"] = [{"attempt_id": "MA", "mock_id": "MO", "course_id": "C",
                                   "submitted_at": "2026-10-10T13:00:00+08:00", "actual_minutes": 90,
                                   "time_scope": "session",
                                   "evidence": "试卷", "question_results": [row, deepcopy(row)]}]
        self.assertEqual("独立做对（单次）", status(state))
        self.assertIn("模拟题：独立 1", coverage.render_reflection(state, NOW))
        self.assertIn("模拟答题用时：已记录部分合计 90 分钟", coverage.render_reflection(state, NOW))
        state["learning_feedback"] = [feedback(actual_minutes=120, time_scope="cumulative")]
        text = coverage.render_reflection(state, NOW)
        self.assertIn("反馈用时 120 分钟", text)
        self.assertNotIn("210 分钟", text)
        for result in state["mock_attempts"][0]["question_results"]:
            result["eligible_independent"] = False
        self.assertEqual("未独立测试", status(state))
        self.assertIn("辅助 1", coverage.render_reflection(state, NOW))

    def test_repeated_errors_only_independent_observed_failures_with_links(self):
        state = fixture()
        state["review_attempts"] = [attempt(rating="again"), attempt("B", rating="again", independent=False)]
        self.assertIn("重复失误：0", coverage.render_reflection(state, NOW))
        state["review_attempts"][1]["independent"] = True
        self.assertIn("重复失误：1", coverage.render_reflection(state, NOW))
        self.assertIn("优先重练 C · 概念一", coverage.render_reflection(state, NOW))

    def test_time_unknown_partial_and_cumulative_do_not_double_count(self):
        state = fixture()
        state["learning_feedback"] = [feedback(), feedback("F2", actual_minutes=None)]
        self.assertIn("反馈用时 待确认", coverage.render_reflection(state, NOW))
        state["learning_feedback"][1].update(actual_minutes=50, time_scope="cumulative")
        text = coverage.render_reflection(state, NOW)
        self.assertIn("反馈用时 50 分钟", text)
        self.assertNotIn("70 分钟", text)
        state["learning_feedback"] = [feedback(time_scope="unknown")]
        self.assertIn("反馈用时 待确认", coverage.render_reflection(state, NOW))

    def test_reflection_priorities_max_three_actual_feedback_and_no_inferred_submission(self):
        state = fixture()
        state["learning_feedback"] = [feedback(str(i), next_step=f"继续练习{i}", blocker="不懂条件") for i in range(5)]
        text = coverage.render_reflection(state, NOW)
        section = text.split("## 接下来最多三项")[1]
        self.assertEqual(3, sum(line.startswith(("1.", "2.", "3.")) for line in section.splitlines()))
        self.assertNotIn("4.", section)
        self.assertIn("不懂条件", text)
        self.assertNotIn("已提交", text)

    def test_retest_can_use_prior_week_evidence_without_counting_old_work(self):
        state = fixture()
        state["review_attempts"] = [attempt("old", "2026-10-01T12:00:00+08:00"), attempt("new")]
        text = coverage.render_reflection(state, NOW)
        self.assertIn("复习：独立 1", text)
        self.assertIn("跨日独立复测：1", text)


if __name__ == "__main__":
    unittest.main()
