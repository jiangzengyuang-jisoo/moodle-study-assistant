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
    def test_section_media_change_preserves_public_site_and_course_mapping(self):
        result, snap, p = state(), snapshot(), profile()
        result["courses"][0]["moodle_course_ids"] = [1]
        snap["moodle_url"] = "https://school.example.test/campus"
        snap["pages"][0].pop("course_id")
        section = snap["pages"][0]["sections"][0]
        section["summary"] += ' <img src="https://example.test/old.png">'
        p["sources"] = [{"authority": "official_course", "location": "section:1:10", "excerpt": "Chapters 1–4"}]
        exam_sources.register_profile(result, p, snap, NOW)
        original = deepcopy(result["exam_profiles"][0]["sources"])
        self.assertEqual("https://school.example.test/campus/course/section.php?id=10", original[0]["url"])
        section["summary"] = section["summary"].replace("old.png", "new.png")
        self.assertEqual(["E"], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])
        self.assertEqual(original, result["exam_profiles"][0]["sources"])

    def test_forum_image_and_link_changes_require_review_without_changing_text(self):
        for tag, attribute in (("img", "src"), ("a", "href"), ("source", "srcset")):
            with self.subTest(tag=tag):
                result, snap = state(), snapshot()
                post = snap["threads"][0]["posts"][0]
                post["message"] += f' <{tag} {attribute}="https://example.test/old.png">'
                exam_sources.register_profile(result, profile(), snap, NOW)
                sources = deepcopy(result["exam_profiles"][0]["sources"])
                post["message"] = post["message"].replace("old.png", "new.png")
                answer = exam_sources.sync_profiles(result, snap, NOW)
                self.assertEqual(["E"], answer["profiles_needing_review"])
                self.assertEqual(sources, result["exam_profiles"][0]["sources"])

    def test_forum_attachment_add_remove_and_same_url_metadata_changes_require_review(self):
        attachment = {"filename": "rules.pdf", "fileurl": "https://example.test/rules.pdf",
                      "filesize": 100, "timemodified": 1}
        for before, after in (([], [attachment]), ([attachment], []),
                              ([attachment], [{**attachment, "timemodified": 2}])):
            with self.subTest(before=before, after=after):
                result, snap = state(), snapshot()
                post = snap["threads"][0]["posts"][0]
                post["attachments"] = deepcopy(before)
                exam_sources.register_profile(result, profile(), snap, NOW)
                post["attachments"] = deepcopy(after)
                self.assertEqual(["E"], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])

    def test_inline_file_metadata_and_post_edit_require_review(self):
        for field, before, after in (("messageinlinefiles", [{"timemodified": 1}], [{"timemodified": 2}]),
                                     ("modified", 100, 200)):
            with self.subTest(field=field):
                result, snap = state(), snapshot()
                post = snap["threads"][0]["posts"][0]
                post[field] = before
                exam_sources.register_profile(result, profile(), snap, NOW)
                post[field] = after
                self.assertEqual(["E"], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])

    def test_unchanged_media_remains_verified_and_does_not_mutate_snapshot(self):
        result, snap = state(), snapshot()
        post = snap["threads"][0]["posts"][0]
        post["message"] += ' <img alt="Exam format" src="https://example.test/rules.png">'
        post["attachments"] = [{"filename": "rules.pdf", "timemodified": 1}]
        original = deepcopy(snap)
        exam_sources.register_profile(result, profile(), snap, NOW)
        self.assertEqual([], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])
        self.assertEqual("verified", result["exam_profiles"][0]["verification_status"])
        self.assertEqual(original, snap)

    def test_plain_text_legacy_hash_is_unchanged(self):
        record = {"course_id": "C", "location": "post:20", "author": "Teacher", "author_id": 7,
                  "text": "Midterm 10 multiple choice questions", "authority": "unreviewed_forum_author"}
        self.assertEqual(exam_sources.digest(record), exam_sources.evidence_index(snapshot())["post:20"]["content_hash"])

    def test_html_attribute_order_and_wrapping_do_not_invalidate_unchanged_image(self):
        result, snap = state(), snapshot()
        post = snap["threads"][0]["posts"][0]
        post["message"] += ' <img alt="Exam format" src="https://example.test/rules.png">'
        exam_sources.register_profile(result, profile(), snap, NOW)
        post["message"] = '<p>10 multiple choice questions</p><img src="https://example.test/rules.png" alt="Exam format" />'
        self.assertEqual([], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])

    def test_module_embedded_image_change_requires_source_review(self):
        result, snap, p = state(), snapshot(), profile()
        module = snap["pages"][0]["sections"][0]["modules"][0]
        module["description"] += ' <img src="https://example.test/old.png">'
        p["sources"] = [{"authority": "official_course", "location": "module:11", "excerpt": "10 multiple choice questions"}]
        exam_sources.register_profile(result, p, snap, NOW)
        module["description"] = module["description"].replace("old.png", "new.png")
        self.assertEqual(["E"], exam_sources.sync_profiles(result, snap, NOW)["profiles_needing_review"])

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
