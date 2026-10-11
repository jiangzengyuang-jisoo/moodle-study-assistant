"""Exam requirements must retain official provenance and fail closed on changes."""
from copy import deepcopy
from datetime import datetime
import unittest

import exam_sources


NOW = datetime.fromisoformat("2026-10-11T20:00:00+08:00")


def snapshot():
    return {"collection_complete": True, "moodle_url": "https://moodle.example.edu", "finished_at": NOW.isoformat(),
            "pages": [{"course_id": "C", "courseid": 1, "sections": [{"id": 10, "name": "Midterm", "summary": "Chapters 1–4",
                       "modules": [{"id": 11, "name": "Exam rules", "description": "10 multiple choice questions"}]}]}],
            "threads": [{"course_id": "C", "discussion": {"discussion": 2}, "posts": [
                {"id": 20, "subject": "Midterm", "message": "10 multiple choice questions", "author": {"id": 7, "fullname": "Teacher"}}]}]}


def state():
    return {"courses": [{"id": "C"}], "materials": [{"id": "M", "course_id": "C", "pages": 5},
                                                       {"id": "FOREIGN", "course_id": "OTHER", "pages": 3}], "exam_profiles": []}


def profile():
    return {"id": "E", "course_id": "C", "title": "Midterm", "scope_status": "confirmed", "format_status": "confirmed",
            "review_evidence": "Read the official teacher post and confirmed the role against course orientation",
            "sources": [{"authority": "teacher", "location": "post:20", "author_id": 7,
                         "role_evidence": "Course instructor listed in course orientation", "excerpt": "10 multiple choice questions"}],
            "topics": [{"id": "T", "label": "Topic", "material_ids": ["M"]}], "sections": []}


def registered():
    result, snap = state(), snapshot()
    exam_sources.register_profile(result, profile(), snap, NOW)
    return result, snap


class ExamSourcesTests(unittest.TestCase):
    def test_assessment_attachment_preview_needs_real_trace_and_tracks_entry_changes(self):
        result, p, snap = state(), profile(), snapshot()
        source = {"authority":"official_course", "location":"attachment:11", "module_id":11,
            "sha256":"a"*64,"pages":[2],"excerpt":"Teacher assessment slide: ten MC",
            "verification_method":"online_in_memory_assessment_preview","course_file_saved":False,
            "preview_evidence":"Actually read via official API in memory; no file saved"}
        p["sources"].append(source)
        exam_sources.register_profile(result,p,snap,NOW)
        recorded=result["exam_profiles"][0]["sources"][-1]
        self.assertEqual(recorded["content_hash"],"a"*64)
        snap["pages"][0]["sections"][0]["modules"][0]["contentsinfo"]={"lastmodified":123}
        exam_sources.sync_profiles(result,snap,NOW)
        self.assertEqual(result["exam_profiles"][0]["verification_status"],"needs_review")
        for change in ({"course_file_saved":True},{"sha256":"unknown"},{"preview_evidence":""},{"module_id":99}):
            invalid=deepcopy(p)
            invalid["sources"][-1].update(change)
            with self.assertRaises(ValueError):
                exam_sources.register_profile(state(),invalid,snapshot(),NOW)

    def test_verified_registration_anchors_excerpt_author_and_corpus(self):
        result, snap = registered()
        p = result["exam_profiles"][0]
        self.assertEqual("verified", p["verification_status"])
        self.assertEqual(exam_sources.candidate_signature(snap, "C"), p["reviewed_candidates_hash"])
        self.assertTrue(p["sources"][0]["content_hash"])
        self.assertIn("#p20", p["sources"][0]["url"])

    def test_changed_official_source_requires_review_even_if_candidate_hash_overridden(self):
        result, snap = registered()
        snap["threads"][0]["posts"][0]["message"] = "12 multiple choice questions"
        result["exam_profiles"][0]["reviewed_candidates_hash"] = exam_sources.candidate_signature(snap, "C")
        answer = exam_sources.sync_profiles(result, snap, NOW)
        self.assertEqual(["E"], answer["profiles_needing_review"])
        self.assertTrue(any("post:20" in problem for problem in result["exam_profiles"][0]["latest_source_check"]["issues"]))

    def test_new_candidate_triggers_review_without_changing_original_post(self):
        result, snap = registered()
        snap["threads"][0]["posts"].append({"id": 21, "subject": "Clarification", "message": "There are 4 long answer questions",
                                                   "author": {"id": 7, "fullname": "Teacher"}})
        exam_sources.sync_profiles(result, snap, NOW)
        self.assertEqual("needs_review", result["exam_profiles"][0]["verification_status"])

    def test_failed_collection_preserves_prior_requirements_and_review_is_sticky(self):
        result, snap = registered()
        original = deepcopy(result["exam_profiles"][0]["sources"])
        exam_sources.sync_profiles(result, {"collection_complete": False}, NOW)
        self.assertEqual(original, result["exam_profiles"][0]["sources"])
        answer = exam_sources.sync_profiles(result, snap, NOW)
        self.assertEqual(["E"], answer["profiles_needing_review"])
        self.assertEqual("needs_review", result["exam_profiles"][0]["verification_status"])
        self.assertTrue(result["exam_profiles"][0]["pending_source_issues"])
        exam_sources.register_profile(result, profile(), snap, NOW)
        self.assertEqual("verified", result["exam_profiles"][0]["verification_status"])
        self.assertNotIn("pending_source_issues", result["exam_profiles"][0])

    def test_student_role_author_mismatch_and_blank_proof_rejected_atomically(self):
        for change in ({"authority": "student"}, {"authority": "official_course"}, {"author_id": 8}, {"role_evidence": " "}):
            with self.subTest(change=change):
                result, p = state(), profile()
                p["sources"][0].update(change)
                original = deepcopy(result)
                with self.assertRaises(ValueError):
                    exam_sources.register_profile(result, p, snapshot(), NOW)
                self.assertEqual(original, result)

    def test_excerpt_mismatch_missing_source_and_cross_course_rejected(self):
        for change in ({"excerpt": "20 questions"}, {"excerpt": " "}, {"location": "post:99"}):
            p = profile()
            p["sources"][0].update(change)
            with self.assertRaises(ValueError):
                exam_sources.register_profile(state(), p, snapshot(), NOW)
        snap = snapshot()
        snap["threads"][0]["course_id"] = "OTHER"
        with self.assertRaises(ValueError):
            exam_sources.register_profile(state(), profile(), snap, NOW)

    def test_no_registration_on_failed_sync(self):
        with self.assertRaises(ValueError):
            exam_sources.register_profile(state(), profile(), {"collection_complete": False}, NOW)

    def test_material_locator_course_and_page_validation(self):
        p = profile()
        p["sources"] = [{"authority": "official_course", "location": "material:M", "material_id": "M", "pages": [2], "excerpt": "Exam details"}]
        exam_sources.register_profile(state(), p, snapshot(), NOW)
        for changes in ({"location": "material:OTHER"}, {"pages": [6]}, {"pages": [True]}, {"pages": [-1]},
                        {"location": "material:FOREIGN", "material_id": "FOREIGN"}):
            with self.subTest(changes=changes):
                changed = deepcopy(p)
                changed["sources"][0].update(changes)
                with self.assertRaises(ValueError):
                    exam_sources.register_profile(state(), changed, snapshot(), NOW)

    def test_invalid_structure_and_foreign_topic_material_rejected(self):
        for change in ({"review_evidence": " "}, {"sources": [None]}, {"topics": [{}]},
                       {"topics": [{"id": "T", "label": "Topic", "material_ids": ["FOREIGN"]}]}):
            with self.subTest(change=change):
                p = profile()
                p.update(change)
                with self.assertRaises(ValueError):
                    exam_sources.register_profile(state(), p, snapshot(), NOW)

    def test_candidate_in_other_course_does_not_invalidate_profile(self):
        result, snap = registered()
        snap["threads"].append({"course_id": "OTHER", "discussion": {"discussion": 3}, "posts": [
            {"id": 21, "subject": "Exam", "message": "New format", "author": {"id": 9}}]})
        self.assertEqual([], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])
        self.assertEqual("verified", result["exam_profiles"][0]["verification_status"])


if __name__ == "__main__":
    unittest.main()
