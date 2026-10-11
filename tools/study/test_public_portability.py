"""Synthetic blank-install, timezone and public Moodle snapshot regressions."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

import daily
import exam_sources
import planning
import review
import study_time
from test_exam_sources import snapshot, state, profile


AT = datetime.fromisoformat("2026-01-10T20:30:00+00:00")


class PublicPortabilityTests(unittest.TestCase):
    def run_blank(self, initial):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "Study" / "_System" / "state.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(initial), encoding="utf-8")
        result = daily.execute(path, "run", AT)
        return path, result, json.loads(path.read_text(encoding="utf-8"))

    def test_empty_state_initializes_and_all_nine_views_are_current(self):
        path, result, value = self.run_blank({})
        self.assertTrue(daily.execute(path, "check", AT)["fresh"])
        self.assertEqual(9, len(result["views_updated"]))
        self.assertEqual([], value["review_attempts"])
        self.assertEqual([], value["mock_attempts"])
        self.assertEqual([], value["mistakes"])
        self.assertTrue(value["settings"]["learning_loop"]["enabled"])
        self.assertIn("scheduler", value["settings"]["spaced_repetition"])
        self.assertEqual("{}", path.with_name("state.previous.json").read_text(encoding="utf-8"))

    def test_utc_calendar_does_not_inherit_default_hong_kong_day(self):
        path, result, value = self.run_blank({"timezone": "UTC", "daily_plans": {}})
        self.assertEqual("2026-01-10", result["date"])
        self.assertEqual("2026-01-10", value["review_sessions"][0]["date"])
        self.assertEqual("UTC", value["planning"]["timezone"])
        self.assertEqual("UTC", value["settings"]["spaced_repetition"]["timezone"])
        self.assertTrue(daily.execute(path, "check", AT)["fresh"])
        self.assertEqual("Asia/Hong_Kong", study_time.name())
        _, default, _ = self.run_blank({})
        self.assertEqual("2026-01-11", default["date"])

    def test_date_keyed_old_public_plans_are_preserved_and_used(self):
        old = {"2026-01-10": {"available_study_minutes": 60}}
        _, _, value = self.run_blank({"timezone": "UTC", "daily_plans": old})
        self.assertEqual(old, value["daily_plans"])
        self.assertEqual(48, value["planning"]["budget_minutes"])

    def test_explicit_deadline_clock_uses_state_timezone(self):
        value = {"timezone": "UTC", "deadlines": [{"id": "D", "date": "2026-01-10", "time": "21:00"}]}
        self.assertFalse(planning._deadline_rows(value, AT)[0]["past"])

    def test_partial_null_post_collection_is_readable_but_not_registerable(self):
        snap = snapshot()
        snap.update(collection_complete=False)
        snap["threads"][0]["posts"] = None
        self.assertTrue(exam_sources.candidates(snap))
        self.assertNotIn("post:20", exam_sources.evidence_index(snap))
        with self.assertRaises(ValueError):
            exam_sources.register_profile(state(), profile(), snap, AT)

    def test_site_prefix_is_preserved_and_never_invented(self):
        snap = snapshot()
        snap["moodle_url"] = "https://campus.example.edu/learning"
        self.assertEqual("https://campus.example.edu/learning/mod/forum/discuss.php?d=2#p20",
                         exam_sources.evidence_index(snap)["post:20"]["url"])
        snap.pop("moodle_url")
        self.assertIsNone(exam_sources.evidence_index(snap)["post:20"]["url"])
        snap["threads"][0]["forum_url"] = "https://campus.example.edu/learning/mod/forum/view.php?id=9"
        self.assertEqual("https://campus.example.edu/learning/course/section.php?id=10",
                         exam_sources.evidence_index(snap)["section:1:10"]["url"])

    def test_numeric_courseid_is_resolved_from_own_state(self):
        snap, value = snapshot(), state()
        value["courses"][0]["moodle_course_ids"] = [1]
        for record in [snap["pages"][0], snap["threads"][0]]:
            record.pop("course_id")
            record["courseid"] = 1
        exam_sources.register_profile(value, profile(), snap, AT)
        self.assertEqual("verified", value["exam_profiles"][0]["verification_status"])
        self.assertEqual([], exam_sources.sync_profiles(value, snap, AT)["profiles_needing_review"])


if __name__ == "__main__":
    unittest.main()
