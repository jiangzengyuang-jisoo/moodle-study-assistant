"""Public-package source defaults, full-page rendering and CLI portability."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import library
from library_test_fixtures import temporary_directory, write_pdf


class LibraryPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory("portability-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root / "Study"
        self.state_path = self.study / "_System/state.json"
        self.state_path.parent.mkdir(parents=True)

    def test_default_state_is_repository_owned_and_not_current_directory(self):
        expected = Path(library.__file__).resolve().parents[2] / "Study/_System/state.json"
        self.assertEqual(library.DEFAULT_STATE, expected)

    def test_cli_explicit_state_works_outside_project_and_preserves_bytes(self):
        state = {"revision": 2, "courses": [{"id": "DEMO101", "name": "Synthetic course", "current_topic": None}], "materials": [], "mistakes": []}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_path.read_bytes()
        output = subprocess.run([sys.executable, str(Path(library.__file__).resolve()), "--state", str(self.state_path), "context", "--course", "DEMO101"], cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=True)
        context = json.loads(output.stdout)
        self.assertEqual(context["state_revision"], 2)
        self.assertIsNone(context["sections"]["course"]["current_topic"])
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_pdfium_renders_full_physical_page_without_poppler_or_source_changes(self):
        from PIL import Image
        source = self.study / "DEMO101/source.pdf"
        source.parent.mkdir(parents=True)
        write_pdf(source, ["Synthetic first page", None])
        state = {"revision": 3, "courses": [{"id": "DEMO101"}], "materials": [{"id": "pdf", "course_id": "DEMO101", "relative_path": "DEMO101/source.pdf", "sha256": library.digest(source)}], "mistakes": [{"id": "retained-question", "entry_type": "question"}]}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        original, state_before = source.read_bytes(), self.state_path.read_bytes()
        instance = library.Library(self.state_path)
        instance.sync()
        with patch.object(library.shutil, "which", return_value=None):
            result = instance.render("pdf", 2, dpi=144)
            first_hash = library.digest(result["path"])
            repeated = instance.render("pdf", 2, dpi=144)
        with Image.open(result["path"]) as image:
            self.assertEqual(image.size, (480, 600))
            self.assertLess(min(image.convert("L").getextrema()), 100, "the original image region must actually render")
        self.assertEqual(first_hash, library.digest(repeated["path"]))
        self.assertFalse(result["verified"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_invalid_page_and_overwrite_are_rejected(self):
        from learning_library.render import render_pdfium
        source = self.root / "one.pdf"
        write_pdf(source, ["Synthetic page"])
        with self.assertRaises(ValueError):
            render_pdfium(source, 1, 130, source)
        with self.assertRaises(ValueError):
            render_pdfium(source, 2, 130, self.root / "out.png")


if __name__ == "__main__":
    unittest.main()
