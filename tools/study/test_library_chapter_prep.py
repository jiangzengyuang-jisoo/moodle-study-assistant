"""Chapter-prep regressions: synthetic sources, no user state or network."""
import copy
import hashlib
import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import library
from library_test_fixtures import temporary_directory, write_pdf
from learning_library.extract import EXTRACTOR_VERSION


def extracted(pages, unit_kind="pdf_page"):
    return {"format": "pdf", "unit_kind": unit_kind, "extractor_version": EXTRACTOR_VERSION,
            "warnings": ["text_extraction_is_not_source_verification"], "verified": False,
            "pages": pages}


def make_pages(count, text=lambda number: f"unit {number} text"):
    return [{"page": number, "text": text(number), "heading": f"Heading {number}",
             "flags": ["needs_visual_review"], "verified": False}
            for number in range(1, count + 1)]


def topic6_pages():
    pages = make_pages(62)
    pages[1]["text"] = ""
    pages[1]["flags"] = ["needs_visual_review", "no_extractable_text",
                         "contains_images_or_drawings", "needs_ocr"]
    pages[30]["text"] = "Y = A K^(1/3) L^(2/3)"
    pages[30]["flags"] = ["needs_visual_review", "possible_formula"]
    return pages


class ChapterPrepTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory("chapter-prep-")
        self.root = Path(self.temp.name)
        self.study = self.root / "Study"
        self.state_path = self.study / "_System" / "state.json"
        self.state_path.parent.mkdir(parents=True)
        self.state = {"revision": 7, "timezone": "Asia/Hong_Kong",
                      "courses": [{"id": "top", "name": "Synthetic course",
                                   "current_topic": "Topic 6",
                                   "teaching_cursor": {}}],
                      "materials": []}
        self.payloads = {}
        self.write_state()
        self.lib = library.Library(self.state_path)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(library.ROOT / "tmp"))
        self.temp.cleanup()

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def write_state(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def material(self, mid="topic6", filename="Topic6.pdf", title="Topic6", pages=None,
                 module_id=501, section="Lecture Slides & Notes", section_index=5,
                 course="top", contains_solutions=None, relative=None, payload=None):
        relative = relative or f"{course}/Lectures/{filename}"
        source = self.study / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            source.write_bytes(("source " + mid).encode("utf-8"))
        row = {"id": mid, "course_id": course, "title": title, "filename": filename,
               "relative_path": relative, "sha256": library.digest(source),
               "moodle_file_sources": [{"course_id": course, "module_id": module_id,
                                        "remote_filename": filename, "filepath": "/"}],
               "source_pages": [{"course_id": course, "moodle_courseid": 146000,
                                 "section": section, "section_index": section_index,
                                 "module_id": module_id, "module_name": title,
                                 "module_type": "resource"}]}
        if contains_solutions is not None:
            row["contains_solutions"] = contains_solutions
        self.state["materials"].append(row)
        self.write_state()
        self.payloads[filename] = payload if payload is not None else extracted(pages or make_pages(3))
        return source, row

    def index(self):
        def fake(path):
            return copy.deepcopy(self.payloads[Path(path).name])
        with patch("learning_library.extract.extract_document", side_effect=fake):
            return self.lib.sync()

    def prep(self, **kwargs):
        kwargs.setdefault("render", "none")
        return self.lib.chapter_prep("top", "Topic 6", **kwargs)

    def state_bytes(self):
        return self.state_path.read_bytes()

    def checks_bytes(self):
        folder = self.study / "_System" / "ChapterChecks"
        if not folder.exists():
            return {}
        return {str(path.relative_to(self.study)): path.read_bytes()
                for path in folder.rglob("*") if path.is_file()}

    def full_json(self, value):
        return json.dumps(value, ensure_ascii=False)

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------
    def test_preferred_tool_keeps_scope_and_requires_real_chat_identity(self):
        self.material()
        self.index()
        before = self.state_bytes()
        by_section = self.prep(chat="主聊天会话")
        check = by_section["strict_check"]
        self.assertEqual(check["preferred"], "mcp")
        self.assertEqual(check["command_role"], "fallback_only")
        self.assertEqual(check["tool"], {"name": "study_check_chapter", "arguments": {
            "course_id": "top", "section": "Topic 6", "chat_id": "主聊天会话"}, "missing_arguments": []})
        no_chat = self.prep()["strict_check"]["tool"]
        self.assertEqual(no_chat["missing_arguments"], ["chat_id"])
        self.assertNotIn("chat_id", no_chat["arguments"])
        by_modules = self.lib.chapter_prep("top", modules=[501], render="none", chat="主聊天会话")
        self.assertEqual(by_modules["strict_check"]["tool"]["arguments"], {
            "course_id": "top", "module_ids": [501], "chat_id": "主聊天会话"})
        self.assertEqual(before, self.state_bytes())

    def test_topic_six_unique_matches_full_text_and_visual_candidates(self):
        self.material(pages=topic6_pages())
        self.index()
        state_before, checks_before = self.state_bytes(), self.checks_bytes()
        with patch("socket.socket", side_effect=AssertionError("network attempted")):
            result = self.prep()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["resolution"]["basis"], "local_materials")
        self.assertEqual(result["resolution"]["module_ids"], [501])
        self.assertEqual(len(result["materials"]), 1)
        entry = result["materials"][0]
        self.assertEqual(entry["units"], 62)
        self.assertEqual(entry["text_units"], 61)
        self.assertEqual(entry["unit_range"], [1, 62])
        self.assertEqual(result["coverage"]["units_total"], 62)
        self.assertEqual(result["coverage"]["units_text"], 61)
        self.assertFalse(result["coverage"]["verified"])
        self.assertIn(2, result["coverage"]["visual_review_required"])
        text_pages = result["text"]["materials"][0]["pages"]
        self.assertEqual(len(text_pages), 62)
        self.assertEqual(set(text_pages[0]), {"page", "heading", "text", "flags"})
        self.assertEqual(text_pages[1]["text"], "")
        self.assertIn("no_extractable_text", text_pages[1]["flags"])
        self.assertNotIn("needs_visual_review", text_pages[1]["flags"])
        self.assertIn("possible_formula", text_pages[30]["flags"])
        candidates = result["visuals"]["candidates"]
        self.assertEqual(candidates[0]["unit"], 2)
        self.assertEqual(candidates[0]["priority"], 100)
        self.assertIn("no_text", candidates[0]["why"])
        formula = next(item for item in candidates if item["unit"] == 31)
        self.assertGreaterEqual(formula["priority"], 70)
        self.assertFalse(result["visuals"]["rendered"])
        self.assertEqual(result["limits"]["network"], False)
        self.assertEqual(result["limits"]["state_written"], False)
        self.assertEqual(state_before, self.state_bytes(), "chapter-prep must not write state")
        self.assertEqual(checks_before, self.checks_bytes(), "chapter-prep must not write receipts")

    def test_ambiguous_tutorial_file_section_conflict(self):
        self.material(mid="tut4", filename="Tutorial 4.pdf", title="Tutorial 4",
                      module_id=502, section="Tutorial 3 (Oct 5 - Oct 9)", pages=make_pages(3))
        self.index()
        result = self.lib.chapter_prep("top", "Tutorial 4", render="none")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["resolution"]["status"], "ambiguous")
        self.assertEqual(result["materials"], [])
        self.assertEqual(result["text"]["materials"], [])
        self.assertEqual(result["visuals"]["rendered"], [])
        self.assertTrue(any(candidate["conflict"] for candidate in result["resolution"]["candidates"]))

    def test_bare_number_with_two_families_is_ambiguous(self):
        self.material(mid="topic6", filename="Topic6.pdf", title="Topic6", pages=make_pages(3))
        self.material(mid="tut6", filename="Tutorial 6.pdf", title="Tutorial 6",
                      module_id=502, pages=make_pages(3))
        self.index()
        result = self.lib.chapter_prep("top", "6", render="none")
        self.assertEqual(result["status"], "ambiguous")
        self.assertGreaterEqual(result["resolution"]["candidate_count"], 2)
        self.assertIn("纯数字", result["resolution"]["reason"])

    def test_date_like_section_is_not_a_chapter_number(self):
        self.material(mid="notes", filename="Oct 5 Notes.pdf", title="Oct 5 Notes",
                      module_id=503, section="General", pages=make_pages(2))
        self.index()
        result = self.lib.chapter_prep("top", "Oct 5", render="none")
        self.assertEqual(result["status"], "no_materials")
        self.assertTrue(result["resolution"].get("date_like"))
        self.assertEqual(result["materials"], [])
        self.assertIn("日期", result["omitted"][0]["next_read"])

    def test_explicit_modules_map_exactly(self):
        self.material(mid="topic6", filename="Topic6.pdf", title="Topic6", pages=make_pages(2))
        self.material(mid="other", filename="Other.pdf", title="Other",
                      module_id=502, pages=make_pages(2))
        self.index()
        result = self.lib.chapter_prep("top", modules=[501], render="none")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["resolution"]["basis"], "explicit_modules")
        self.assertEqual(result["resolution"]["module_ids"], [501])
        self.assertEqual([entry["id"] for entry in result["materials"]], ["topic6"])

    def test_explicit_modules_without_local_material_is_no_materials(self):
        self.material(mid="topic6", pages=make_pages(2))
        self.index()
        result = self.lib.chapter_prep("top", modules=[999], render="none")
        self.assertEqual(result["status"], "no_materials")
        self.assertEqual(result["materials"], [])
        self.assertEqual(result["text"]["materials"], [])
        self.assertTrue(result["omitted"])
        mixed = self.lib.chapter_prep("top", modules=[501, 999], render="none")
        self.assertEqual(mixed["status"], "ready")
        self.assertEqual(mixed["resolution"]["module_ids"], [501, 999])
        self.assertEqual([entry["id"] for entry in mixed["materials"]], ["topic6"])

    def test_merged_course_page_resolves_to_primary_subject(self):
        self.state["courses"].append({"id": "SEC", "name": "Section page",
                                      "merged_into": "top"})
        self.write_state()
        self.material(pages=make_pages(3))
        self.index()
        result = self.lib.chapter_prep("SEC", "Topic 6", render="none")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["course"]["id"], "top")

    def test_no_materials_does_not_fabricate_and_gives_local_section_hint(self):
        result = self.lib.chapter_prep("top", "Topic 9", render="none")
        self.assertEqual(result["status"], "no_materials")
        self.assertEqual(result["materials"], [])
        self.assertEqual(result["text"]["materials"], [])
        self.assertEqual(result["visuals"]["rendered"], [])
        self.assertFalse(result["coverage"]["units_total"])
        course_page = ("# Demo course\n\n## [5] Lecture Slides & Notes\n\n"
                       "- `resource` Topic9 — https://example.invalid/x\n")
        source = self.study / "top" / "Other" / "course-page-top.md"
        source.parent.mkdir(parents=True)
        source.write_text(course_page, encoding="utf-8")
        self.material(mid="capture", filename="course-page-top.md", title="course page capture",
                      module_id=146000, section=None, relative="top/Other/course-page-top.md")
        with patch("learning_library.extract.extract_document",
                   return_value=extracted([{"page": 1, "text": course_page,
                                            "heading": "Demo course", "flags": [],
                                            "verified": False}], unit_kind="section")):
            self.lib.sync()
        result = self.lib.chapter_prep("top", "Topic 9", render="none")
        self.assertEqual(result["status"], "no_materials")
        self.assertIn("Lecture Slides & Notes", result["resolution"]["available_sections"])

    # ------------------------------------------------------------------
    # text budget
    # ------------------------------------------------------------------
    def test_text_budget_omits_whole_pages_with_next_read_command(self):
        self.material(pages=make_pages(10))
        self.index()
        result = self.lib.chapter_prep("top", "Topic 6", render="none", text_budget=30)
        material = result["text"]["materials"][0]
        self.assertLess(len(material["pages"]), 10)
        self.assertTrue(material["included_units"][0] == 1)
        # Complete pages only: text is identical to the cached page text.
        for page in material["pages"]:
            self.assertEqual(page["text"], f"unit {page['page']} text")
        self.assertEqual(len(result["text"]["omitted"]), 1)
        omitted = result["text"]["omitted"][0]
        self.assertEqual(omitted["from_unit"], len(material["pages"]) + 1)
        self.assertIn("library.cmd read", omitted["next_read"])
        self.assertIn("--start", omitted["next_read"])
        self.assertIn(result["materials"][0]["id"], omitted["next_read"])

    def test_text_outline_and_none_return_no_pages(self):
        self.material(pages=make_pages(4))
        self.index()
        for mode in ("outline", "none"):
            result = self.lib.chapter_prep("top", "Topic 6", render="none", text=mode)
            self.assertEqual(result["text"]["materials"], [])
            self.assertEqual(result["text"]["returned_characters"], 0)
            self.assertEqual(len(result["outline"]["materials"][0]["headings"]), 4)

    # ------------------------------------------------------------------
    # stale cache / source changes
    # ------------------------------------------------------------------
    def test_source_change_marks_needs_sync_and_never_returns_old_text(self):
        relative = "top/Lectures/Topic6-notes.txt"
        source = self.study / relative
        source.parent.mkdir(parents=True)
        source.write_text("OLD_CHAPTER_TEXT\n", encoding="utf-8")
        self.state["materials"].append({
            "id": "notes", "course_id": "top", "title": "Topic 6 notes",
            "filename": "Topic6-notes.txt", "relative_path": relative,
            "sha256": library.digest(source),
            "moodle_file_sources": [{"course_id": "top", "module_id": 501,
                                     "remote_filename": "Topic6-notes.txt", "filepath": "/"}],
            "source_pages": [{"course_id": "top", "moodle_courseid": 146000,
                              "section": "Lecture Slides & Notes", "section_index": 5,
                              "module_id": 501, "module_name": "Topic 6 notes",
                              "module_type": "resource"}]})
        self.write_state()
        with patch("learning_library.extract.extract_document",
                   return_value=extracted([{"page": 1, "text": "OLD_CHAPTER_TEXT",
                                            "heading": "Topic 6 notes", "flags": [],
                                            "verified": False}], unit_kind="section")):
            self.lib.sync()
        ready = self.lib.chapter_prep("top", "Topic 6", render="none")
        self.assertEqual(ready["status"], "ready")
        self.assertIn("OLD_CHAPTER_TEXT",
                      self.full_json(ready["text"]["materials"]))
        source.write_text("NEW_CHAPTER_TEXT\n", encoding="utf-8")
        stale = self.lib.chapter_prep("top", "Topic 6", render="none")
        self.assertEqual(stale["status"], "needs_sync")
        self.assertEqual(stale["materials"][0]["status"], "needs_sync")
        self.assertEqual(stale["materials"][0]["cache_hit"], False)
        self.assertEqual(stale["text"]["materials"], [])
        rendered_json = self.full_json(stale)
        self.assertNotIn("OLD_CHAPTER_TEXT", rendered_json)
        self.assertTrue(stale["cache"]["invalid"])
        self.assertIn("sync --course top", stale["cache"]["sync_command"])
        self.assertGreaterEqual(stale["cache"]["extract_unavailable"], 1)

    def test_solution_material_is_hidden_by_default(self):
        self.material(mid="sol", filename="Topic6-solutions.txt", title="Topic 6 solutions",
                      contains_solutions=True, pages=make_pages(2))
        self.index()
        hidden = self.lib.chapter_prep("top", "Topic 6", render="none")
        self.assertEqual(hidden["status"], "ready")
        self.assertTrue(hidden["materials"][0]["solutions_hidden"])
        self.assertEqual(hidden["text"]["materials"], [])
        self.assertEqual(hidden["coverage"]["units_solutions_hidden"], 2)
        self.assertTrue(any(item["reason"] == "solutions_hidden_by_default"
                            for item in hidden["text"]["omitted"]))
        shown = self.lib.chapter_prep("top", "Topic 6", render="none", include_solutions=True)
        self.assertEqual(len(shown["text"]["materials"]), 1)
        self.assertFalse(shown["materials"][0]["solutions_hidden"])

    # ------------------------------------------------------------------
    # batch render
    # ------------------------------------------------------------------
    def real_pdf_material(self, mid="pdf", filename="Topic9.pdf", title="Topic9"):
        source = self.study / "top" / "Lectures" / filename
        source.parent.mkdir(parents=True)
        write_pdf(source, ["first page", None, "third page"])
        self.state["materials"].append({
            "id": mid, "course_id": "top", "title": title, "filename": filename,
            "relative_path": f"top/Lectures/{filename}", "sha256": library.digest(source),
            "moodle_file_sources": [{"course_id": "top", "module_id": 501,
                                     "remote_filename": filename, "filepath": "/"}],
            "source_pages": [{"course_id": "top", "moodle_courseid": 146000,
                              "section": "Lecture Slides & Notes", "section_index": 5,
                              "module_id": 501, "module_name": title,
                              "module_type": "resource"}]})
        self.write_state()
        self.lib.sync()
        return source

    @staticmethod
    def write_png(command, **kwargs):
        from PIL import Image
        Image.new("RGB", (24, 30), "white").save(str(command[-1]) + ".png")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def test_render_many_validates_source_once_and_reuses_cache(self):
        self.real_pdf_material()
        original_read = library.Library.read
        calls = {"count": 0}

        def counted_read(instance, *args, **kwargs):
            calls["count"] += 1
            return original_read(instance, *args, **kwargs)

        with patch.object(library.shutil, "which", return_value="mock-pdftoppm"), \
                patch.object(library.subprocess, "run", side_effect=self.write_png) as runner, \
                patch.object(library.Library, "read", counted_read):
            first = self.lib.render_many("pdf", [1, 2])
            self.assertEqual(len(first), 2)
            self.assertEqual(calls["count"], 1, "one source validation for the whole batch")
            self.assertEqual(runner.call_count, 2)
            second = self.lib.render_many("pdf", [1, 2])
            self.assertEqual(runner.call_count, 2, "matching render cache must be reused")
            self.assertTrue(all(item["cached"] for item in second))
            image = Path(first[0]["path"])
            original_png = image.read_bytes()
            image.write_bytes(b"corrupted image bytes")
            self.lib.render_many("pdf", [1, 2])
            self.assertEqual(runner.call_count, 3)
            self.assertEqual(image.read_bytes(), original_png)
            manifest = image.with_suffix(".json")
            record = library.read_json(manifest)
            record["unit"] = 999
            library.atomic_json(manifest, record)
            self.lib.render_many("pdf", [1, 2])
            self.assertEqual(runner.call_count, 4)
        with self.assertRaises(ValueError):
            self.lib.render_many("pdf", [4])
        with self.assertRaises(ValueError):
            self.lib.render_many("pdf", [])

    def test_chapter_prep_renders_candidates_reuses_cache_and_reports_errors(self):
        self.real_pdf_material()
        with patch.object(library.shutil, "which", return_value=None), \
                patch("learning_library.render.render_pdfium", side_effect=self.write_png_pdfium):
            first = self.lib.chapter_prep("top", "Topic 9", render="auto", render_limit=2)
            self.assertEqual(first["status"], "ready")
            rendered = first["visuals"]["rendered"]
            self.assertTrue(rendered, "the image-only PDF page must be a render candidate")
            self.assertTrue(Path(rendered[0]["path"]).is_file())
            self.assertFalse(rendered[0]["verified"])
            self.assertGreaterEqual(first["cache"]["render_created"], 1)
            second = self.lib.chapter_prep("top", "Topic 9", render="auto", render_limit=2)
            self.assertEqual(second["cache"]["render_created"], 0)
            self.assertGreaterEqual(second["cache"]["render_reused"], 1)
        with patch.object(library.shutil, "which", return_value=None), \
                patch("learning_library.render.render_pdfium",
                      side_effect=ValueError("render failed")):
            for cached in (self.lib.folder / "renders").rglob("page-*"):
                cached.unlink()
            broken = self.lib.chapter_prep("top", "Topic 9", render="auto", render_limit=1)
            self.assertEqual(broken["visuals"]["rendered"], [])
            self.assertTrue(broken["visuals"]["render_errors"])
            self.assertNotIn("path", self.full_json(broken["visuals"]["rendered"]))

    @staticmethod
    def write_png_pdfium(source, unit, dpi, output):
        from PIL import Image
        Image.new("RGB", (24, 30), "white").save(str(output))

    # ------------------------------------------------------------------
    # continuity / receipts / read-only guards
    # ------------------------------------------------------------------
    def test_continuity_keeps_only_chapter_records_and_hides_answers(self):
        self.material(pages=make_pages(3))
        self.index()
        self.state["courses"][0]["teaching_cursor"] = {
            "chapter": "Topic 6: Aggregate Production",
            "topic": "aggregate production",
            "stage": "first_explanation_delivered_pending_check",
            "material_id": "topic6",
            "pages": [1, 2, 3],
            "next_step": "wait for answer",
            "updated_at": "2026-11-08T03:27:54+08:00",
            "pending_check": {"id": "check-6", "status": "unanswered",
                              "prompt": "Explain diminishing returns.",
                              "answer": "SECRET_ANSWER",
                              "answer_key": "SECRET_KEY"},
            "checks": [{"id": "check-6", "topic": "aggregate production",
                        "status": "unanswered", "user_answer": "SECRET_USER_ANSWER"}],
            "history": [{"chapter": "Topic 5", "next_step": "old"}],
        }
        self.state["courses"][0]["review_backlog"] = [
            {"id": "backlog-6", "chapter": "Topic 6: Aggregate Production",
             "topic": "returns to scale", "status": "pending", "next_step": "retest"},
            {"id": "backlog-5", "chapter": "Topic 5: Loanable Funds",
             "topic": "loanable funds", "status": "pending", "next_step": "retest"},
        ]
        self.write_state()
        result = self.lib.chapter_prep("top", "Topic 6", render="none", text="outline")
        continuity = result["continuity"]
        self.assertEqual(continuity["current_position"]["chapter"],
                         "Topic 6: Aggregate Production")
        self.assertTrue(continuity["chapter_label_matches_current_position"])
        self.assertTrue(continuity["pending_question"]["same_chapter"])
        self.assertEqual(continuity["pending_question"]["prompt"],
                         "Explain diminishing returns.")
        self.assertEqual([item["id"] for item in continuity["review_backlog"]],
                         ["backlog-6"])
        blob = self.full_json(continuity)
        for secret in ("SECRET_ANSWER", "SECRET_KEY", "SECRET_USER_ANSWER"):
            self.assertNotIn(secret, blob)
        self.assertNotIn('"tasks"', self.full_json(result))
        self.assertNotIn('"deadlines"', self.full_json(result))
        self.assertNotIn('"exam_profiles"', self.full_json(result))
        self.assertTrue(any("background_not_loaded" in item["reason"]
                            for item in continuity["omitted"]))

    def test_strict_receipt_summary_is_background_only_and_unmodified(self):
        self.material(pages=make_pages(2))
        self.index()
        chat = "session-synthetic-chat"
        folder = self.study / "_System" / "ChapterChecks" / "top"
        folder.mkdir(parents=True)
        receipt = {"schema": 2, "mode": "strict", "checked_at": "2026-11-07T19:24:22.274Z",
                   "chat": {"key": hashlib.sha256(
                       json.dumps(chat, sort_keys=True, ensure_ascii=False).encode()).hexdigest()},
                   "request": {"section": "Topic 6", "modules": None},
                   "verdict": "unchanged",
                   "verified": {"complete": True},
                   "scope": {"module_ids": [501]}}
        receipt_path = folder / "topic-6.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        before = receipt_path.read_bytes()
        result = self.lib.chapter_prep("top", "Topic 6", render="none", chat=chat)
        summary = result["strict_check"]["last_receipt"]
        self.assertTrue(summary["found"])
        self.assertTrue(summary["verified_complete"])
        self.assertTrue(summary["chat_key_matches"])
        self.assertIn("不是本轮核验", summary["note"])
        self.assertTrue(result["strict_check"]["required"])
        self.assertIn("chapter-check", result["strict_check"]["command"])
        self.assertEqual(receipt_path.read_bytes(), before,
                         "chapter-prep must never rewrite a chapter-check receipt")
        no_chat = self.lib.chapter_prep("top", "Topic 6", render="none")
        self.assertIsNone(no_chat["strict_check"]["last_receipt"]["chat_key_matches"])

    def test_interrupted_material_transaction_is_rejected(self):
        self.material(pages=make_pages(2))
        self.index()
        (self.study / "_System" / "material-version-transaction.json").write_text(
            json.dumps({"before_sha": "x"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.lib.chapter_prep("top", "Topic 6", render="none")

    def test_no_network_or_external_command_during_prepare(self):
        self.material(pages=make_pages(3))
        self.index()
        with patch("socket.socket", side_effect=AssertionError("network attempted")), \
                patch.object(library.subprocess, "run",
                             side_effect=AssertionError("external command attempted")):
            result = self.lib.chapter_prep("top", "Topic 6", render="none")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["limits"]["network"])
        self.assertFalse(result["limits"]["maintenance"])
        self.assertFalse(result["limits"]["sync"])
        self.assertFalse(result["limits"]["ocr"])

    # ------------------------------------------------------------------
    # CLI
    # ------------------------------------------------------------------
    def test_cli_chapter_prep_prints_json_and_preserves_state(self):
        self.material(pages=topic6_pages())
        self.index()
        before = self.state_bytes()
        output = subprocess.run(
            [sys.executable, str(Path(library.__file__).resolve()),
             "--state", str(self.state_path),
             "chapter-prep", "--course", "top", "--section", "Topic 6",
             "--text", "outline", "--render", "none", "--read-only"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=True)
        parsed = json.loads(output.stdout)
        self.assertEqual(parsed["status"], "ready")
        self.assertEqual(parsed["materials"][0]["units"], 62)
        self.assertEqual(self.state_bytes(), before)

    def test_cli_render_units_batch(self):
        self.real_pdf_material()
        output = subprocess.run(
            [sys.executable, str(Path(library.__file__).resolve()),
             "--state", str(self.state_path),
             "render", "pdf", "--units", "1,3"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(output.returncode, 0, output.stderr)
        parsed = json.loads(output.stdout)
        self.assertEqual([item["unit"] for item in parsed["renders"]], [1, 3])
        self.assertTrue(all(Path(item["path"]).is_file() for item in parsed["renders"]))
        help_output = subprocess.run(
            [sys.executable, str(Path(library.__file__).resolve()), "render", "--help"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=True)
        self.assertIn("--units", help_output.stdout)
        self.assertIn("--unit", help_output.stdout)


if __name__ == "__main__":
    unittest.main()
