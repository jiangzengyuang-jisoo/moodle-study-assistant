"""Synthetic OCR sidecar tests: no real OCR, school access, or learning writes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import library
from library_test_fixtures import temporary_directory
from learning_library import ocr
from learning_library.extract import EXTRACTOR_VERSION


class OcrTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory("ocr-")
        self.root = Path(self.temp.name)
        self.study = self.root / "Study"
        self.state_path = self.study / "_System/state.json"
        self.state_path.parent.mkdir(parents=True)
        self.state = {"revision": 8, "courses": [{"id": "A"}, {"id": "B"}],
                      "materials": [], "mistakes": [{"id": "real-record-sentinel"}]}
        self.payloads = {}
        self.run_calls = []
        self.render_calls = []
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.lib = library.Library(self.state_path)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(library.ROOT / "tmp"))
        self.temp.cleanup()

    def add(self, mid="scan", texts=("",), kind="pdf_page", course="A", solutions=False):
        ext = {"pdf_page": "pdf", "image": "png", "section": "docx"}[kind]
        relative = f"{course}/Materials/{mid}.{ext}"
        source = self.study / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(("ORIGINAL " + mid).encode())
        self.state["materials"].append({"id": mid, "course_id": course, "relative_path": relative,
            "title": mid, "sha256": library.digest(source), "contains_solutions": solutions})
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.payloads[source.name] = {"format": ext, "unit_kind": kind, "extractor_version": EXTRACTOR_VERSION,
            "warnings": [], "verified": False, "pages": [{"page": i, "text": text, "heading": "",
            "flags": ["needs_visual_review"], "verified": False} for i, text in enumerate(texts, 1)]}
        return source

    def sync(self):
        with patch("learning_library.extract.extract_document", side_effect=lambda path: copy.deepcopy(self.payloads[path.name])):
            self.lib.sync()

    def row(self, mid="scan"):
        return next(row for row in self.lib.fresh_rows()[1] if row["id"] == mid)

    def render(self, mid, unit, dpi=130, include_solutions=False):
        self.render_calls.append((mid, unit, dpi, include_solutions))
        row = self.row(mid)
        if row["unit_kind"] == "image":
            path = self.study / row["relative_path"]
        else:
            path = self.lib.folder / "renders" / row["sha256"] / f"page-{unit}-{dpi}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"IMAGE {mid} PAGE {unit}".encode())
        return {"path": str(path), "source_sha256": row["sha256"]}

    def batch(self, args, **kwargs):
        manifest = Path(args[args.index("-ManifestPath") + 1])
        self.assertTrue(manifest.is_relative_to(self.lib.folder / "ocr"))
        self.assertEqual(args[args.index("-LanguageTag") + 1], "en-US")
        self.assertGreaterEqual(kwargs["timeout"], 120)
        self.assertLessEqual(kwargs["timeout"], 1800)
        entries = library.read_json(manifest)
        self.run_calls.append(entries)
        results = [{"schema": 1, "id": item["id"], "engine": ocr.ENGINE,
            "source_image": item["path"], "source_sha256": library.digest(item["path"]),
            "language": "en-US", "width": 100, "height": 80, "elapsed_ms": 4,
            "text": "candidate " + item["id"][:8], "lines": ["candidate line"],
            "text_angle": None, "verified": False, "errors": []} for item in entries]
        output = {"engine": ocr.ENGINE, "verified": False, "requested": len(entries), "results": results}
        return subprocess.CompletedProcess(args, 0, json.dumps(output).encode("utf-8"), b"")

    def build(self, **kwargs):
        with patch.object(self.lib, "render", side_effect=self.render), patch.object(ocr.subprocess, "run", side_effect=self.batch):
            return ocr.build_ocr(self.lib, **kwargs)

    def protected(self):
        return {str(p): p.read_bytes() for p in self.study.rglob("*")
                if p.is_file() and (not p.is_relative_to(self.lib.folder) or p.is_relative_to(self.lib.folder / "cache")
                                   or p == self.lib.folder / "catalog.json")}

    def record_path(self, mid="scan", unit=1):
        key, _ = ocr._identity(self.row(mid), unit, "en-US")
        entry = library.read_json(self.lib.folder / "ocr/index.json")["records"][key]
        return self.lib.folder / "ocr/records" / entry["file"]

    def test_only_blank_pdf_and_image_units_use_one_batch_and_never_touch_learning_or_text_cache(self):
        self.add(texts=("", "existing source text", ""))
        self.add("photo", kind="image", solutions=True)
        self.add("structured", texts=("", ""), kind="section")
        self.sync()
        before = self.protected()
        result = self.build()
        self.assertEqual((result["eligible_units"], result["queued"], result["written"], result["failed"]), (3, 3, 3, 0))
        self.assertEqual(result["skipped_structural_units"], 2)
        self.assertEqual(len(self.run_calls), 1)
        self.assertTrue(all(call[3] is True for call in self.render_calls), "internal indexing explicitly includes solution images")
        self.assertFalse(result["verified"])
        self.assertFalse(result["state_written"])
        self.assertEqual(self.protected(), before)
        candidate = ocr.load_ocr(self.lib, self.row(), 1)
        self.assertIn("candidate", candidate["text"])
        self.assertFalse(candidate["verified"])
        self.assertIsNone(candidate["confidence"])
        self.assertIn("needs_visual_review", candidate["flags"])
        self.assertIsNone(ocr.load_ocr(self.lib, self.row(), 2))
        self.assertIsNone(ocr.load_ocr(self.lib, self.row("structured"), 1))
        self.assertEqual(list((self.lib.folder / "ocr").glob(".ocr-manifest-*")), [])
        second = self.build()
        self.assertEqual((second["reused"], second["queued"], second["written"]), (3, 0, 0))
        self.assertEqual(len(self.run_calls), 1)
        self.assertEqual(self.protected(), before)

    def test_course_filter_and_identical_sources_do_not_duplicate_ocr(self):
        source = self.add()
        same = self.add("duplicate")
        same.write_bytes(source.read_bytes())
        self.state["materials"][-1]["sha256"] = library.digest(same)
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.add("other", course="B")
        self.sync()
        result = self.build(course="A")
        self.assertEqual(result["eligible_units"], 2)
        self.assertEqual(result["deduplicated"], 1)
        self.assertEqual(result["written"], 1)
        self.assertIsNotNone(ocr.load_ocr(self.lib, self.row("duplicate"), 1))
        self.assertIsNone(ocr.load_ocr(self.lib, self.row("other"), 1))

    def test_same_size_same_mtime_source_change_rejects_old_candidate(self):
        source = self.add()
        self.sync(); self.build()
        row = self.row()
        stamp = source.stat()
        source.write_bytes(b"X" * stamp.st_size)
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertIsNone(ocr.load_ocr(self.lib, row, 1))
        result = self.build()
        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["unavailable"][0]["reason"], "source_changed_rebuild_required")

    def test_corrupt_record_and_render_image_are_rejected_then_rebuilt(self):
        self.add(); self.sync(); self.build()
        record = self.record_path()
        record.write_text("broken JSON", encoding="utf-8")
        self.assertIsNone(ocr.load_ocr(self.lib, self.row(), 1))
        self.assertEqual(self.build()["written"], 1)
        candidate = ocr.load_ocr(self.lib, self.row(), 1)
        Path(candidate["source_image"]).write_bytes(b"altered render")
        self.assertIsNone(ocr.load_ocr(self.lib, self.row(), 1))
        self.assertEqual(self.build()["written"], 1)
        self.assertIsNotNone(ocr.load_ocr(self.lib, self.row(), 1))

    def test_provenance_and_verified_tampering_rejected_even_if_index_checksum_recomputed(self):
        self.add(); self.sync(); self.build()
        path = self.record_path()
        record = library.read_json(path)
        record["verified"] = True
        library.atomic_json(path, record)
        index = library.read_json(self.lib.folder / "ocr/index.json")
        next(iter(index["records"].values()))["sha256"] = library.digest(path)
        library.atomic_json(self.lib.folder / "ocr/index.json", index)
        self.assertIsNone(ocr.load_ocr(self.lib, self.row(), 1))

    def test_failed_force_retry_keeps_previous_success_and_finishes_other_pages(self):
        self.add(texts=("", "")); self.sync(); self.build()
        first_before = ocr.load_ocr(self.lib, self.row(), 1)
        first_file = self.record_path(unit=1)
        original = first_file.read_bytes()
        def partial(args, **kwargs):
            process = self.batch(args, **kwargs)
            output = json.loads(process.stdout)
            output["results"][0].update(text=None, lines=[], errors=[{"message": "recognizer unavailable"}])
            process.stdout = json.dumps(output).encode()
            return process
        with patch.object(self.lib, "render", side_effect=self.render), patch.object(ocr.subprocess, "run", side_effect=partial):
            result = ocr.build_ocr(self.lib, force=True)
        self.assertEqual((result["written"], result["failed"]), (1, 1))
        self.assertTrue(result["failures"][0]["previous_success_preserved"])
        self.assertEqual(first_file.read_bytes(), original)
        self.assertEqual(ocr.load_ocr(self.lib, self.row(), 1), first_before)

    def test_timeout_does_not_replace_previous_success_and_cleans_manifest(self):
        self.add(); self.sync(); self.build()
        index_before = (self.lib.folder / "ocr/index.json").read_bytes()
        with patch.object(self.lib, "render", side_effect=self.render), patch.object(ocr.subprocess, "run", side_effect=subprocess.TimeoutExpired("ocr", 120)):
            result = ocr.build_ocr(self.lib, force=True)
        self.assertEqual(result["failed"], 1)
        self.assertTrue(result["failures"][0]["previous_success_preserved"])
        self.assertEqual((self.lib.folder / "ocr/index.json").read_bytes(), index_before)
        self.assertEqual(list((self.lib.folder / "ocr").glob(".ocr-manifest-*")), [])

    def test_missing_duplicate_and_wrong_image_hash_results_are_individual_failures(self):
        self.add(texts=("", "", "")); self.sync()
        def bad(args, **kwargs):
            process = self.batch(args, **kwargs)
            output = json.loads(process.stdout)
            first, second, third = output["results"]
            third["source_sha256"] = "0" * 64
            output["results"] = [first, first, third]  # duplicate first; absent second
            process.stdout = json.dumps(output).encode()
            return process
        with patch.object(self.lib, "render", side_effect=self.render), patch.object(ocr.subprocess, "run", side_effect=bad):
            result = ocr.build_ocr(self.lib)
        self.assertEqual((result["written"], result["failed"]), (0, 3))
        self.assertIsNone(ocr.load_ocr(self.lib, self.row(), 1))

    def test_source_change_during_batch_is_detected_before_any_candidate_is_saved(self):
        source = self.add(); self.sync()
        def changed(args, **kwargs):
            process = self.batch(args, **kwargs)
            source.write_bytes(b"new original source")
            return process
        with patch.object(self.lib, "render", side_effect=self.render), patch.object(ocr.subprocess, "run", side_effect=changed):
            result = ocr.build_ocr(self.lib)
        self.assertEqual((result["written"], result["failed"]), (0, 1))

    def test_render_failure_and_empty_batch_both_remain_visible(self):
        self.add(texts=("", "")); self.sync()
        def render(mid, unit, **kwargs):
            if unit == 1:
                raise ValueError("render failed")
            return self.render(mid, unit, **kwargs)
        def empty(args, **kwargs):
            process = self.batch(args, **kwargs)
            output = json.loads(process.stdout); output["results"] = []
            process.stdout = json.dumps(output).encode()
            return process
        with patch.object(self.lib, "render", side_effect=render), patch.object(ocr.subprocess, "run", side_effect=empty):
            result = ocr.build_ocr(self.lib)
        self.assertEqual(result["failed"], 2)

    def test_one_render_timeout_does_not_cancel_other_pages(self):
        self.add(texts=("", "")); self.sync()
        def render(mid, unit, **kwargs):
            if unit == 1:
                raise subprocess.TimeoutExpired("pdftoppm", 90)
            return self.render(mid, unit, **kwargs)
        with patch.object(self.lib, "render", side_effect=render), patch.object(ocr.subprocess, "run", side_effect=self.batch):
            result = ocr.build_ocr(self.lib)
        self.assertEqual((result["written"], result["failed"]), (1, 1))

    def test_blank_ocr_is_not_evidence_of_blankness_or_verification(self):
        self.add(); self.sync()
        def blank(args, **kwargs):
            process = self.batch(args, **kwargs)
            output = json.loads(process.stdout)
            output["results"][0].update(text="", lines=[])
            process.stdout = json.dumps(output).encode()
            return process
        with patch.object(self.lib, "render", side_effect=self.render), patch.object(ocr.subprocess, "run", side_effect=blank):
            result = ocr.build_ocr(self.lib)
        self.assertEqual(result["blank_results"], 1)
        record = ocr.load_ocr(self.lib, self.row(), 1)
        self.assertFalse(record["verified"])
        self.assertIn("blank_result_does_not_prove_blank_page", record["flags"])

    def test_build_lock_rejects_overlapping_builds(self):
        self.add(); self.sync()
        with library.build_lock(self.lib.folder), patch.object(ocr.subprocess, "run") as run:
            with self.assertRaises(OSError):
                ocr.build_ocr(self.lib)
            run.assert_not_called()

    def test_derived_symlink_is_rejected_before_writes(self):
        self.add(); self.sync()
        outside = self.root / "outside"
        outside.mkdir()
        original_resolve = Path.resolve
        redirected = self.lib.folder / "ocr"
        def resolves_like_junction(path, *args, **kwargs):
            if path == redirected:
                return original_resolve(outside)
            return original_resolve(path, *args, **kwargs)
        # The Windows host disallows creating links; exercise the same resolved
        # path boundary without requesting elevated symlink privileges.
        with patch.object(Path, "resolve", resolves_like_junction), self.assertRaises(ValueError):
            self.build()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
