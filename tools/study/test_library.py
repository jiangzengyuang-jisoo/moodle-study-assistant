"""Course-library regressions: synthetic sources, no user state or network."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import library
from library_test_fixtures import temporary_directory, write_pdf
from learning_library.extract import EXTRACTOR_VERSION


def extracted(text="source text", count=1, unit_kind="pdf_page"):
    return {"format": "pdf", "unit_kind": unit_kind, "extractor_version": EXTRACTOR_VERSION,
            "warnings": ["text_extraction_is_not_source_verification"], "verified": False,
            "pages": [{"page": i, "text": text + (str(i) if count > 1 else ""),
                       "heading": "unit " + str(i), "flags": ["needs_visual_review"],
                       "verified": False} for i in range(1, count + 1)]}


class LibraryTests(unittest.TestCase):
    def test_specific_compound_does_not_expand_to_generic_complex_number_topic(self):
        terms=library.query_terms('复数方根')
        self.assertIn('nth roots',terms)
        self.assertNotIn('complex numbers',terms)

    def test_matching_whole_question_precedes_a_generic_expanded_alias(self):
        self.material('specific',text='SCARCITY\nPOCKET MONEY DAY')
        self.material('general',text='Money monetary money monetary money monetary',title='Money monetary')
        self.lib.sync()
        found=self.lib.search('SCARCITY POCKET MONEY','A')
        self.assertEqual(found['results'][0]['material_id'],'specific')

    def setUp(self):
        self.temp = temporary_directory("library-")
        self.root = Path(self.temp.name)
        self.study = self.root / "Study"
        self.state_path = self.study / "_System" / "state.json"
        self.state_path.parent.mkdir(parents=True)
        self.state = {"revision": 17, "courses": [{"id": "A"}, {"id": "B"}], "materials": []}
        self.write_state()
        self.lib = library.Library(self.state_path)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(library.ROOT / "tmp"))
        self.temp.cleanup()

    def write_state(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def material(self, mid="m1", course="A", relative=None, text="original material", **extra):
        relative = relative or f"{course}/Materials/{mid}.txt"
        source = self.study / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding="utf-8")
        row = {"id": mid, "course_id": course, "title": mid, "filename": source.name,
               "relative_path": relative, "sha256": library.digest(source), **extra}
        self.state["materials"].append(row)
        self.write_state()
        return source, row

    def row(self, mid="m1"):
        return next(row for row in self.lib.catalog()["materials"] if row["id"] == mid)

    def corrupt_cache(self, mid="m1", value=None):
        path = self.lib.folder / self.row(mid)["cache"]
        if value is None:
            path.write_text("not JSON", encoding="utf-8")
        else:
            data = library.read_json(path)
            data["pages"][0]["text"] = value
            library.atomic_json(path, data)
        return path

    def test_same_size_same_mtime_source_edit_cannot_reuse_old_text(self):
        source, _ = self.material(text="before")
        self.lib.sync()
        original_time = source.stat()
        source.write_text("after!", encoding="utf-8")
        os.utime(source, ns=(original_time.st_atime_ns, original_time.st_mtime_ns))
        self.assertEqual(source.stat().st_size, original_time.st_size)
        self.assertEqual(source.stat().st_mtime_ns, original_time.st_mtime_ns)
        with self.assertRaises(ValueError):
            self.lib.read("m1")
        report = self.lib.sync()
        self.assertEqual(report["extracted"], 1)
        self.assertEqual(report["reused"], 0)
        self.assertEqual(self.lib.read("m1", around=0)["pages"][0]["text"], "after!")

    def test_corrupt_cache_rejected_before_read_and_rebuilt(self):
        self.material()
        self.lib.sync()
        self.corrupt_cache()
        with self.assertRaises(ValueError):
            self.lib.read("m1")
        report = self.lib.sync()
        self.assertEqual(report["extracted"], 1)
        self.assertEqual(self.lib.read("m1", around=0)["pages"][0]["text"], "original material")

    def test_new_registry_id_cannot_launder_existing_corrupt_cache(self):
        self.material()
        self.lib.sync()
        self.corrupt_cache(value="POISONED cached content")
        self.state["materials"][0]["id"] = "replacement-id"
        self.write_state()
        report = self.lib.sync()
        self.assertEqual(report["extracted"], 1, "a cache with an untrusted digest must be extracted again")
        self.assertEqual(self.lib.read("replacement-id", around=0)["pages"][0]["text"], "original material")

    def test_added_removed_and_reassigned_materials_follow_latest_state(self):
        self.material("removed", "A")
        self.material("moved", "B")
        self.lib.sync()
        self.state["materials"] = [m for m in self.state["materials"] if m["id"] != "removed"]
        self.state["materials"][0]["course_id"] = "A"
        self.material("new", "A")
        fresh = self.lib.outline("A")
        self.assertEqual({i["id"] for i in fresh["unavailable"]}, {"moved", "new"})
        expected_state = self.state_path.read_bytes()
        self.lib.sync("A")
        result = self.lib.outline("A")
        self.assertEqual({m["id"] for m in result["materials"]}, {"moved", "new"})
        self.assertEqual(result["unavailable"], [])
        self.assertNotIn("removed", {m["id"] for m in self.lib.catalog()["materials"]})
        self.assertEqual(self.state_path.read_bytes(), expected_state)

    def test_semantic_mapping_updates_without_reextracting_and_hash_is_current(self):
        source, row = self.material(text="not related to the requested topic")
        with patch("learning_library.extract.extract_document", return_value=extracted("ordinary text")):
            self.lib.sync()
        row["title"] = "New name"
        row["page_map"] = [{"start": 1, "end": 1, "topic": "polar form"}]
        row["sha256"] = "0" * 64  # This new page map is registered to different source bytes.
        self.write_state()
        _, rows, _ = self.lib.fresh_rows("A")
        self.assertEqual(rows[0]["title"], "New name")
        self.assertEqual(library.page_topics(rows[0], 1), [], "changed registry hash must disable mismatched page labels")
        row["sha256"] = library.digest(source)
        self.write_state()
        _, rows, _ = self.lib.fresh_rows("A")
        self.assertEqual(library.page_topics(rows[0], 1), ["polar form"])

    def test_state_backups_and_source_bytes_never_change(self):
        self.material()
        backup = self.state_path.with_suffix(".json.bak")
        backup.write_bytes(b"SENTINEL PREVIOUS STATE")
        source_backup = self.study / "A/Materials/m1.txt.bak"
        source_backup.write_bytes(b"SENTINEL ORIGINAL BACKUP")
        before = {str(p.relative_to(self.study)): p.read_bytes() for p in self.study.rglob("*") if p.is_file()}
        self.lib.sync()
        self.lib.sync()
        self.lib.search("original", "A")
        self.lib.read("m1", around=0)
        self.lib.outline("A")
        after = {str(p.relative_to(self.study)): p.read_bytes() for p in self.study.rglob("*")
                 if p.is_file() and not p.is_relative_to(self.lib.folder)}
        self.assertEqual(after, before)

    def test_cross_page_reads_return_complete_units_and_surroundings(self):
        self.material(relative="A/Materials/source.pdf")
        long_text = "full content " * 2500
        payload = extracted(long_text, count=4)
        with patch("learning_library.extract.extract_document", return_value=copy.deepcopy(payload)):
            self.lib.sync()
        result = self.lib.read("m1", start=2, end=3, around=1)
        self.assertEqual(result["included"], [1, 4])
        self.assertEqual(result["pages"], payload["pages"])
        self.assertFalse(result["verified"])

    def test_solution_search_read_outline_and_render_are_hidden_by_default(self):
        self.material(relative="A/Materials/solutions.jpg", contains_solutions=True,
                      page_map=[{"start": 1, "end": 1, "topic": "secret answer explanation"}])
        with patch("learning_library.extract.extract_document", return_value=extracted("secret solution", unit_kind="image")):
            self.lib.sync()
        result = self.lib.search("secret", "A")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["excluded_solution_materials"], ["m1"])
        with self.assertRaises(ValueError):
            self.lib.read("m1", around=0)
        outline = self.lib.outline("A")
        self.assertTrue(outline["materials"][0]["solutions_hidden"])
        self.assertEqual(outline["materials"][0]["headings"], [])
        self.assertEqual(outline["materials"][0]["page_map"], [])
        self.assertIn("secret solution", self.lib.read("m1", around=0, include_solutions=True)["pages"][0]["text"])
        with self.assertRaises(ValueError, msg="render must apply the same default solution gate"):
            self.lib.render("m1", 1)

    def test_outline_accounts_for_unsupported_missing_and_new_materials(self):
        archive, _ = self.material("archive", relative="A/Materials/archive.zip")
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("inside.txt", "unexpanded text")
        source, _ = self.material("missing")
        source.unlink()
        self.lib.sync()
        self.material("added-after-index")
        outline = self.lib.outline("A")
        ids = {m["id"] for m in outline["materials"]} | {m["id"] for m in outline["unavailable"]}
        self.assertEqual(ids, {"archive", "missing", "added-after-index"})
        archive_entry = next(m for m in outline["materials"] if m["id"] == "archive")
        self.assertIn("unsupported_archive", archive_entry["warnings"])
        self.assertFalse(outline["coverage_complete"])

    def test_source_path_cannot_escape_study(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside original", encoding="utf-8")
        self.state["materials"] = [{"id": "escaped", "course_id": "A", "relative_path": "../outside.txt"}]
        self.write_state()
        self.lib.sync()
        _, rows, issues = self.lib.fresh_rows()
        self.assertEqual(rows, [])
        self.assertEqual({i["id"] for i in issues}, {"escaped"})
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside original")

    def test_derived_and_backup_sources_are_not_promoted_to_originals(self):
        for mid, relative in [("derived", "_System/Library/derived.txt"),
                              ("backup", "A/Backups/old.txt"),
                              ("state-backup", "_System/state.json.bak")]:
            self.material(mid, relative=relative)
        self.lib.sync()
        _, rows, issues = self.lib.fresh_rows()
        self.assertEqual(rows, [], "derived system/cache/backup files must not become teaching originals")
        self.assertEqual({i["id"] for i in issues}, {"derived", "backup", "state-backup"})

    def test_cache_dir_cannot_be_original_material_directory(self):
        self.material()
        original_directory = self.study / "A/Materials"
        before = {p.name for p in original_directory.iterdir()}
        with self.assertRaises(ValueError):
            library.Library(self.state_path, original_directory).sync()
        self.assertEqual({p.name for p in original_directory.iterdir()}, before)

    def test_catalog_cache_path_must_stay_inside_derived_cache(self):
        self.material()
        self.lib.sync()
        outside = self.root / "outside-cache.json"
        payload = extracted("OUTSIDE CACHE TEXT", unit_kind="section")
        payload["source_sha256"] = self.row()["sha256"]
        library.atomic_json(outside, payload)
        catalog = self.lib.catalog()
        catalog["materials"][0]["cache"] = str(outside)
        catalog["materials"][0]["cache_sha256"] = library.digest(outside)
        library.atomic_json(self.lib.folder / "catalog.json", catalog)
        with self.assertRaises(ValueError, msg="even a matching digest must not allow an external cache path"):
            self.lib.read("m1", around=0)

    def test_solution_semantic_labels_do_not_depend_on_filename(self):
        for number, metadata in enumerate((
            {"kind": "solutions"}, {"category": "answer_key"}, {"title": "Chapter 1 Answer Key"},
        ), 1):
            self.material("labeled" + str(number), text="sensitive answer text", **metadata)
        self.lib.sync()
        result = self.lib.search("sensitive", "A")
        self.assertEqual(result["results"], [])
        self.assertEqual(set(result["excluded_solution_materials"]), {"labeled1", "labeled2", "labeled3"})
        for mid in result["excluded_solution_materials"]:
            self.assertIn("sensitive", self.lib.read(mid, around=0, include_solutions=True)["pages"][0]["text"])

    def test_render_cache_corruption_or_wrong_page_manifest_forces_rerender(self):
        import subprocess
        from PIL import Image
        self.material(relative="A/Materials/render-source.pdf")
        with patch("learning_library.extract.extract_document", return_value=extracted("page text")):
            self.lib.sync()

        def render(cmd, **kwargs):
            Image.new("RGB", (24, 30), "white").save(str(cmd[-1]) + ".png")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with patch.object(library.shutil, "which", return_value="mock-pdftoppm"), patch.object(library.subprocess, "run", side_effect=render) as runner:
            first = self.lib.render("m1", 1)
            image = Path(first["path"])
            original_png = image.read_bytes()
            self.lib.render("m1", 1)
            self.assertEqual(runner.call_count, 1, "matching render cache should be reused")
            image.write_bytes(b"corrupted image bytes")
            self.lib.render("m1", 1)
            self.assertEqual(runner.call_count, 2)
            self.assertEqual(image.read_bytes(), original_png)
            manifest = image.with_suffix(".json")
            record = library.read_json(manifest)
            record["unit"] = 999
            library.atomic_json(manifest, record)
            self.lib.render("m1", 1)
            self.assertEqual(runner.call_count, 3, "source hash alone cannot prove a render belongs to this page")
            manifest.write_text("invalid JSON", encoding="utf-8")
            self.lib.render("m1", 1)
            self.assertEqual(runner.call_count, 4)

    def test_real_pdf_roundtrip_preserves_physical_pages_and_original(self):
        source, row = self.material(relative="A/Materials/real.pdf")
        write_pdf(source, ["Definition of continuity", "A continuation of the proof"])
        row["sha256"] = library.digest(source)
        self.write_state()
        original = source.read_bytes()
        state_before = self.state_path.read_bytes()
        report = self.lib.sync()
        self.assertEqual(report["units"], 2)
        result = self.lib.search("continuity", "A")
        self.assertEqual(result["results"][0]["unit"], 1)
        read = self.lib.read("m1", start=1, end=2, around=0)
        self.assertEqual(read["unit_kind"], "pdf_page")
        self.assertIn("continuation", read["pages"][1]["text"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.state_path.read_bytes(), state_before)


if __name__ == "__main__":
    unittest.main()
