"""Regression checks use small fixtures or temporary files; no real learning writes."""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import planning


def fixture():
    return {
        "revision": 12, "updated_at": "2026-10-09T20:00:00+08:00",
        "settings": {"study_capacity_target": .8},
        "courses": [{"id": "C", "current_topic": "明日期中复习", "assignment_focus": {
            "task_ids": ["exam", "work"], "required_chapters": ["1", "2"]}}],
        "tasks": [{"id": "exam", "course_id": "C", "title": "Midterm", "type": "Midterm", "status": None, "deadline_id": "Dexam"},
                  {"id": "work", "course_id": "C", "title": "New work", "status": None, "deadline_id": "Dwork"}],
        "deadlines": [{"id": "Dexam", "task_id": "exam", "course_id": "C", "title": "Midterm", "date": "2026-10-10", "time": "09:30", "end_time": "11:30"},
                      {"id": "Dwork", "course_id": "C", "title": "New work", "date": "2026-10-17", "time": None}],
        "daily_plans": [{"date": "2026-10-09", "course_scope": ["C"], "note": "明日10月10日考试",
                         "available_study_minutes": None, "scheduled_minutes": 200,
                         "course_capacity": {"course_id": "C", "available_minutes_range": [240, 300],
                                             "applies_only_to_dates": ["2026-10-08", "2026-10-09"]}}],
        "schedules": [], "review_sessions": [], "learning_feedback": []}


class PlanningTests(unittest.TestCase):
    def test_carryover_keeps_dictionary_plan_and_profile_timezone(self):
        state = fixture()
        state["timezone"] = "UTC"
        state["deadlines"][1]["date"] = "2026-10-09"
        state["tasks"][1]["status"] = "In progress"
        state["daily_plans"] = {"2026-10-10": {"candidate_task_ids": ["work"]}}
        before = deepcopy(state)
        views = planning.render_views(state, "2026-10-11T02:00:00+08:00")
        self.assertIn("# 2026-10-10", views["Today Plan.md"])
        self.assertIn("New work", views["Today Plan.md"].split("## 今日复习")[0])
        tomorrow = planning.render_views(state, "2026-10-11T00:00:00+00:00")
        self.assertNotIn("New work", tomorrow["Today Plan.md"].split("## 今日复习")[0])
        self.assertEqual(before, state)

    def test_recurring_timetable_respects_inclusive_validity_dates(self):
        state = fixture()
        state["schedules"] = [{"type": "recurring_course_timetable", "applicable_from": "2026-11-09",
            "term_end": "2026-11-16", "events": [{"id": "tutorial", "title": "Term tutorial",
            "weekday": "Monday", "start": "12:00", "end": "13:00", "default_block_for_attendance": True}]}]
        original = deepcopy(state)
        for day, expected in (("2026-11-02", False), ("2026-11-09", True),
                              ("2026-11-16", True), ("2026-11-23", False)):
            with self.subTest(day=day):
                views = planning.render_views(state, day + "T08:00:00+08:00")
                self.assertEqual(expected, "Term tutorial" in views["Today Plan.md"])
                self.assertEqual(expected, "Term tutorial" in views["Weekly View.md"])
        self.assertEqual(original, state)

    def test_unknown_term_dates_do_not_remove_recurring_attendance(self):
        state = fixture()
        state["schedules"] = [{"type": "recurring_course_timetable", "applicable_from": None,
            "term_end": None, "events": [{"id": "tutorial", "title": "Term tutorial",
            "weekday": "Monday", "default_block_for_attendance": True}]}]
        self.assertTrue(planning._events(state, date(2026, 11, 2)))
        state["schedules"][0]["applicable_from"] = "2026-11-09"
        self.assertEqual([], planning._events(state, date(2026, 11, 2)))
        self.assertTrue(planning._events(state, date(2027, 1, 4)))

    def test_explicit_dated_makeup_outside_regular_term_is_preserved(self):
        state = fixture()
        state["schedules"] = [{"type": "recurring_course_timetable", "applicable_from": "2026-11-09",
            "term_end": "2026-11-16", "events": [{"id": "makeup", "title": "Confirmed makeup",
            "date": "2026-11-23", "default_block_for_attendance": True}]}]
        self.assertIn("Confirmed makeup", " ".join(planning._events(state, date(2026, 11, 23))))

    def test_explicit_today_carryover_keeps_confirmed_unfinished_work(self):
        for evidence in ({"status": "In progress"}, {"status": "Not started"},
                         {"remaining_workload_minutes": 30},
                         {"remaining_workload_minutes": {"min": 10, "max": 30}}):
            with self.subTest(evidence=evidence):
                state = fixture()
                state["deadlines"][1]["date"] = "2026-10-10"
                state["tasks"][1].update(evidence)
                state["daily_plans"].append({"date": "2026-10-11", "candidate_task_ids": ["work"]})
                before = deepcopy(state)
                views = planning.render_views(state, "2026-10-11T12:00:00+08:00")
                self.assertIn("New work", views["Today Plan.md"].split("## 今日复习")[0])
                self.assertIn("New work", views["Weekly View.md"].split("## 本周推进顺序")[1])
                self.assertEqual(state, before)

    def test_carryover_does_not_reopen_unknown_completed_or_historical_exam(self):
        for evidence in ({"status": None}, {"status": "Completed", "remaining_workload_minutes": 30},
                         {"status": "In progress", "group_submitted": True},
                         {"status": "In progress", "type": "Exam"}):
            with self.subTest(evidence=evidence):
                state = fixture()
                state["deadlines"][1]["date"] = "2026-10-10"
                state["tasks"][1].update(evidence)
                state["daily_plans"].append({"date": "2026-10-11", "candidate_task_ids": ["work"]})
                priorities = planning.refresh_plan_state(state, "2026-10-11T12:00:00+08:00")["candidate_task_ids"]
                self.assertNotIn("work", priorities)

    def test_stale_explicit_plan_does_not_auto_carry_overdue_work(self):
        state = fixture()
        state["deadlines"][1]["date"] = "2026-10-10"
        state["tasks"][1]["status"] = "In progress"
        state["daily_plans"].append({"date": "2026-10-10", "candidate_task_ids": ["work"]})
        self.assertNotIn("work", planning.refresh_plan_state(state, "2026-10-11T12:00:00+08:00")["candidate_task_ids"])

    def test_new_course_null_focus_daily_run_and_check_preserve_unknown_progress(self):
        state = {"revision": 1, "settings": {}, "courses": [{"id": "NEW", "moodle_course_ids": [100],
                 "current_topic": None, "assignment_focus": None, "teaching_cursor": None}],
                 "materials": [], "tasks": [], "deadlines": [], "mistakes": []}
        with tempfile.TemporaryDirectory(prefix=".planning-init-test-", dir=Path(__file__).parent) as directory:
            path = Path(directory) / "Study/_System/state.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(state), encoding="utf-8")
            command = [sys.executable, str(Path(planning.__file__).with_name("daily.py")),
                       "--state", str(path), "--at", "2026-11-02T12:00:00+08:00"]
            # First blank initialization, then metadata arrives before chapter mapping.
            for has_task in (False, True):
                if has_task:
                    current = json.loads(path.read_text(encoding="utf-8"))
                    current["tasks"] = [{"id": "T1", "course_id": "NEW", "title": "New homework", "status": None, "deadline_id": "D1"}]
                    current["deadlines"] = [{"id": "D1", "task_id": "T1", "course_id": "NEW", "date": "2026-11-03", "time": None}]
                    path.write_text(json.dumps(current), encoding="utf-8")
                result = subprocess.run(command + ["run"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run(command + ["check"], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["fresh"])
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["courses"], state["courses"])
                self.assertEqual(saved["materials"], [])
                today = (path.parent / "Today Plan.md").read_text(encoding="utf-8")
                self.assertNotIn("未开始", today)
                if has_task:
                    self.assertIn("New homework", today)
                    self.assertIsNone(saved["tasks"][0]["status"])

    def test_hong_kong_midnight_changes_date_and_never_reuses_yesterday(self):
        state = fixture()
        before = planning.render_views(state, "2026-10-10T15:59:00+00:00")
        after = planning.render_views(state, "2026-10-10T16:01:00+00:00")
        self.assertIn("# 2026-10-11", after["Today Plan.md"])
        self.assertNotIn("明日10月10日", after["Today Plan.md"])
        self.assertFalse(planning.check_freshness(state, before, "2026-10-10T16:01:00+00:00")["fresh"])
        priorities = after["Today Plan.md"].split("## 今日复习")[0]
        self.assertNotIn("Midterm", priorities)
        self.assertIn("参加情况与考后卡点待确认", after["Today Plan.md"])
        self.assertIsNone(state["tasks"][0]["status"])

    def test_submitted_and_group_representative_tasks_never_reopen(self):
        for completion in ({"status": "Completed", "submission_status": "new"},
                           {"progress_confirmation": "user_reported_group_submission", "personal_account_submission_status": "no_submission"},
                           {"group_submission": {"submitted": True}},
                           {"submission_status": "submitted"}):
            with self.subTest(completion=completion):
                state = fixture()
                state["tasks"][1].update(completion)
                result = planning.render_views(state, "2026-10-11T12:00:00+08:00")
                self.assertNotIn("New work", "".join(result.values()))
        state = fixture()
        state["deadlines"][1]["status"] = "Completed"
        self.assertNotIn("New work", planning.render_views(state, "2026-10-11T12:00:00+08:00")["Today Plan.md"])

    def test_unknown_capacity_stays_null_and_date_only_has_no_invented_clock(self):
        state = fixture()
        original = deepcopy(state["daily_plans"])
        meta = planning.refresh_plan_state(state, "2026-10-11T12:00:00+08:00")
        self.assertIsNone(meta["available_minutes"])
        self.assertIsNone(meta["budget_minutes"])
        self.assertEqual("preliminary", meta["mode"])
        views = planning.render_views(state, "2026-10-11T12:00:00+08:00")
        self.assertIn("初步计划", views["Today Plan.md"])
        self.assertIn("2026-10-17（时刻待确认）", views["Today Plan.md"])
        self.assertNotIn("2026-10-17 23:59", "".join(views.values()))
        self.assertEqual(original, state["daily_plans"])
        self.assertNotIn("未开始", views["Today Plan.md"])

    def test_course_budget_is_preserved_only_for_its_dates(self):
        state = fixture()
        original = deepcopy(state)
        text = planning.render_views(state, "2026-10-09T12:00:00+08:00")["Today Plan.md"]
        self.assertIn("240–300", text)
        self.assertIn("核心 200", text)
        later = planning.render_views(state, "2026-10-11T12:00:00+08:00")["Today Plan.md"]
        self.assertNotIn("240–300", later)
        self.assertEqual(original, state)

    def test_source_revision_and_modified_view_detected(self):
        state = fixture()
        now = "2026-10-11T12:00:00+08:00"
        views = planning.render_views(state, now)
        self.assertTrue(planning.check_freshness(state, views, now)["fresh"])
        state["revision"] += 1
        self.assertFalse(planning.check_freshness(state, views, now)["fresh"])
        views = planning.render_views(state, now)
        views["Today Plan.md"] += "过期的旧分科计划\n"
        self.assertFalse(planning.check_freshness(state, views, now)["fresh"])

    def test_same_day_exam_end_changes_freshness(self):
        state = fixture()
        views = planning.render_views(state, "2026-10-10T09:00:00+08:00")
        self.assertFalse(planning.check_freshness(state, views, "2026-10-10T12:00:00+08:00")["fresh"])

    def test_repeated_refresh_is_idempotent(self):
        state = fixture()
        planning.refresh_plan_state(state, "2026-10-11T12:00:00+08:00")
        first = deepcopy(state)
        planning.refresh_plan_state(state, "2026-10-11T14:00:00+08:00")
        self.assertEqual(first, state)

    def test_fixed_schedule_exceptions_preserve_tentative_blocks(self):
        state = fixture()
        state["schedules"] = [{"type": "recurring_course_timetable", "events": [
            {"id": "one", "course_id": "C", "type": "Tutorial", "weekday": "Monday", "start": "12:00", "end": "12:50", "default_block_for_attendance": True},
            {"id": "two", "course_id": "B", "type": "Tutorial", "weekday": "Monday", "start": "13:00", "end": "13:50", "default_block_for_attendance": True},
            {"id": "three", "course_id": "C", "type": "Lecture", "weekday": "Monday", "start": "14:00", "end": "14:50", "default_block_for_attendance": False}],
            "exceptions": [
                {"course_id": "C", "types": ["Tutorial"], "start_date": "2026-10-12", "end_date": "2026-10-30", "held": False, "tentative": True},
                {"course_id": "B", "types": ["Tutorial"], "start_date": "2026-10-12", "end_date": "2026-10-23", "held": False, "tentative": False}]}]
        text = planning.render_views(state, "2026-10-12T08:00:00+08:00")["Today Plan.md"]
        self.assertIn("停课待确认，暂保留该时段", text)
        self.assertIn("B Tutorial：本日停课", text)
        self.assertNotIn("14:00", text)
        self.assertIn("初步计划", text)

    def test_known_capacity_and_feedback_share_original_budget(self):
        state = fixture()
        state["daily_plans"].append({"date": "2026-10-11", "available_study_minutes": 100, "budget_minutes": 80})
        state["learning_feedback"].append({"feedback_id": "F", "date": "2026-10-11", "course_id": "C", "completed": "练习Q1", "actual_minutes": 30, "time_scope": "session", "blocker": "符号", "next_step": "检查Q2"})
        state["review_sessions"] = [{"date": "2026-10-11", "budget_minutes": 15, "items": [{"status": "answered"}, {"status": "pending"}]}]
        text = planning.render_views(state, "2026-10-11T12:00:00+08:00")["Today Plan.md"]
        self.assertIn("学习上限安排 80", text)
        self.assertIn("剩余至多 50", text)
        self.assertIn("已记录作答1题", text)
        self.assertIn("下一步：检查Q2", text)
        self.assertEqual(80, state["daily_plans"][-1]["budget_minutes"])

    def test_feedback_unknown_ranges_never_sum_and_distinct_sessions_do(self):
        state = fixture()
        state["daily_plans"].append({"date": "2026-10-11", "available_study_minutes": 100})
        state["learning_feedback"] = [
            {"feedback_id": "F1", "date": "2026-10-11", "actual_minutes": 10},
            {"feedback_id": "F2", "date": "2026-10-11", "actual_minutes": 20}]
        now = "2026-10-11T12:00:00+08:00"
        text = planning.render_views(state, now)["Today Plan.md"]
        self.assertIn("暂不合计或推算剩余", text)
        self.assertNotIn("剩余至多 50", text)
        for item in state["learning_feedback"]:
            item["time_scope"] = "session"
        text = planning.render_views(state, now)["Today Plan.md"]
        self.assertIn("独立学习段合计 30", text)
        self.assertIn("剩余至多 50", text)
        state["learning_feedback"][1]["feedback_id"] = "F1"
        self.assertIn("暂不合计或推算剩余", planning.render_views(state, now)["Today Plan.md"])

    def test_cumulative_feedback_uses_latest_without_adding_review_time(self):
        state = fixture()
        state["daily_plans"].append({"date": "2026-10-11", "available_study_minutes": 100})
        state["learning_feedback"] = [
            {"feedback_id": "F1", "date": "2026-10-11", "actual_minutes": 10, "time_scope": "cumulative", "recorded_at": "2026-10-11T10:00:00+08:00"},
            {"feedback_id": "F2", "date": "2026-10-11", "actual_minutes": 20, "time_scope": "cumulative", "recorded_at": "2026-10-11T11:00:00+08:00"}]
        state["review_sessions"] = [{"date": "2026-10-11", "budget_minutes": 15,
                                     "items": [{"status": "answered", "actual_minutes": 5}]}]
        text = planning.render_views(state, "2026-10-11T12:00:00+08:00")["Today Plan.md"]
        self.assertIn("最近累计反馈 20", text)
        self.assertIn("剩余至多 60", text)
        self.assertNotIn("剩余至多 55", text)

    def test_review_budget_ceiling_uses_only_today_and_never_recharges_feedback(self):
        state = fixture()
        now = "2026-10-11T12:00:00+08:00"
        self.assertEqual(15, planning.review_budget_limit(state, now))
        state["daily_plans"][0]["review_minutes_total"] = 2
        self.assertEqual(15, planning.review_budget_limit(state, now))
        state["daily_plans"].append({"date": "2026-10-11", "available_study_minutes": 10})
        self.assertEqual(8, planning.review_budget_limit(state, now))
        state["learning_feedback"] = [{"date": "2026-10-11", "actual_minutes": 5, "time_scope": "cumulative"}]
        self.assertEqual(8, planning.review_budget_limit(state, now))
        state["daily_plans"][-1]["review_minutes_total"] = 6
        self.assertEqual(6, planning.review_budget_limit(state, now))
        self.assertEqual(0, planning.review_budget_limit(state, now, requested=0))
        state["daily_plans"][-1]["review_minutes"] = 4
        self.assertEqual(4, planning.review_budget_limit(state, now))
        self.assertRaises(ValueError, planning.review_budget_limit, state, now, requested=31)

    def test_recent_feedback_and_confirmed_slots_are_preserved(self):
        state = fixture()
        state["daily_plans"].append({"date": "2026-10-11", "scheduled_slots": [
            {"start": "15:00", "end": "15:40", "course_id": "C", "confirmed": True}]})
        state["learning_feedback"].append({"date": "2026-10-10", "course_id": "C", "task_ids": ["work"], "next_step": "完成Q2"})
        today = planning.render_views(state, "2026-10-11T12:00:00+08:00")["Today Plan.md"]
        self.assertIn("15:00–15:40 C（已确认）", today)
        self.assertIn("下一步：完成Q2", today)
        tomorrow = planning.render_views(state, "2026-10-12T12:00:00+08:00")["Today Plan.md"]
        self.assertNotIn("15:00–15:40 C", tomorrow)

    def test_cli_render_and_check_use_temporary_files(self):
        with tempfile.TemporaryDirectory(prefix=".planning-test-", dir=Path(__file__).parent) as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            command = [sys.executable, str(Path(planning.__file__)), "render", "--state", str(path), "--at", "2026-10-11T12:00:00+08:00"]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, result.returncode, result.stderr)
            result = subprocess.run(command + ["--check"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, result.returncode, result.stderr)
            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["revision"] += 1
            path.write_text(json.dumps(changed), encoding="utf-8")
            result = subprocess.run(command + ["--check"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(1, result.returncode)


if __name__ == "__main__":
    unittest.main()
