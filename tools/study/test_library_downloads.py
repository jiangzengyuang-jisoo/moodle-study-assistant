"""Answer visibility through versioned installation, using isolated local files."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from library import Library, digest, is_solution
from material_versions import install_download


class LibraryDownloadTests(unittest.TestCase):
    def test_solution_name_tokens_do_not_match_unrelated_words(self):
        for name in ("Tutorial 7 sol.pdf", "Tutorial_8_sols.pdf", "T9_SOL.pdf"):
            self.assertTrue(is_solution({"filename": name}), name)
        for name in ("Solar energy.pdf", "Solow growth.pdf", "Consolidation.txt"):
            self.assertFalse(is_solution({"filename": name}), name)

    def test_installed_solution_flag_and_sol_name_stay_hidden(self):
        for name, flag in (("Practice.txt", True), ("Tutorial 7 sol.txt", None)):
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                    prefix=".library-download-test-", dir=Path(__file__).resolve().parent) as directory:
                study = Path(directory).resolve() / "Study"
                state_path = study / "_System/state.json"
                state_path.parent.mkdir(parents=True)
                history = {"id": "known-attempt", "user_answer": "retained", "independent": False}
                state_path.write_text(json.dumps({"revision": 1, "courses": [{"id": "A"}],
                    "materials": [], "review_attempts": [history]}), encoding="utf-8")
                staged = study / "_Inbox" / name
                staged.parent.mkdir()
                staged.write_text("SECRET_SOLUTION: x equals 42", encoding="utf-8")
                installed = install_download(state_path, {
                    "source": {"course_id": "A", "module_id": 123, "filename": name,
                               "category": "Tutorials", "contains_solutions": flag},
                    "relative_path": staged.relative_to(study).as_posix(), "sha256": digest(staged)})
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                if flag is True:
                    self.assertIs(saved["materials"][0]["contains_solutions"], True)
                self.assertEqual(saved["review_attempts"], [history])
                lib = Library(state_path)
                mid = installed["material_id"]
                with self.assertRaises(ValueError):
                    lib.read(mid)
                self.assertEqual(lib.search("SECRET_SOLUTION", "A")["results"], [])
                self.assertEqual(lib.read(mid, include_solutions=True)["pages"][0]["text"],
                                 "SECRET_SOLUTION: x equals 42")

    def _check_identical_solution_upgrade(self, initial_flag, upgrade_source, *, explicit_old=False):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix=".library-label-upgrade-test-", dir=root) as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(root))
            study = Path(directory).resolve() / "Study"
            state_path = study / "_System/state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({"revision": 1, "courses": [{"id": "A"}],
                "materials": [], "review_attempts": []}), encoding="utf-8")
            staged = study / "_Inbox/Practice.txt"
            staged.parent.mkdir()
            content = "SECRET_SOLUTION: x equals 42"
            staged.write_text(content, encoding="utf-8")
            request = {"source": {"course_id": "A", "module_id": 123,
                                  "filename": "Practice.txt", "title": "Practice",
                                  "category": "Tutorials", "contains_solutions": initial_flag},
                       "relative_path": "_Inbox/Practice.txt", "sha256": digest(staged)}
            first = install_download(state_path, request)
            mid = first["material_id"]
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            material_before = deepcopy(saved["materials"][0])
            self.assertIs(material_before["contains_solutions"], initial_flag)
            saved["courses"][0]["teaching_cursor"] = {"material_id": mid, "pages": [1], "next_step": "retained"}
            saved["mistakes"] = [{"id": "M", "material_id": mid, "pages": [1], "entry_type": "question"}]
            saved["review_attempts"] = [{"id": "known-attempt", "material_id": mid,
                                        "source": {"relative_path": material_before["relative_path"]},
                                        "user_answer": "retained", "independent": False}]
            state_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            note = study / "A/错题本/记录.md"
            note.parent.mkdir(parents=True)
            note.write_text(f"# Original notes\n\nMaterial {mid}, page 1\n[{mid}](<{material_before['relative_path']}>)\n",
                            encoding="utf-8")
            note_before = note.read_bytes()
            source_file = study / material_before["relative_path"]
            source_before = source_file.read_bytes()
            lib = Library(state_path)
            self.assertEqual(lib.read(mid)["pages"][0]["text"], content)

            # A later authorized classification changes metadata, not file bytes.
            staged.write_bytes(source_before)
            upgrade = deepcopy(request)
            upgrade["source"].update(upgrade_source)
            if explicit_old:
                upgrade["old_id"] = mid
            before_upgrade = state_path.read_bytes()
            result = install_download(state_path, upgrade)
            after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIs(after["materials"][0]["contains_solutions"], True)
            self.assertEqual(result["status"], "identical")
            self.assertTrue(result["state_written"])
            self.assertEqual(result["material_id"], mid)
            self.assertEqual(result["relative_path"], material_before["relative_path"])
            expected_material = {**material_before, "contains_solutions": True}
            self.assertEqual(after["materials"], [expected_material])
            self.assertEqual(after["revision"], saved["revision"] + 1)
            self.assertEqual({k: v for k, v in after.items() if k not in {"revision", "updated_at", "materials"}},
                             {k: v for k, v in saved.items() if k not in {"revision", "updated_at", "materials"}})
            self.assertEqual(source_file.read_bytes(), source_before)
            self.assertEqual(digest(source_file), material_before["sha256"])
            self.assertEqual(note.read_bytes(), note_before)
            self.assertEqual(state_path.with_name("state.previous.json").read_bytes(), before_upgrade)
            with self.assertRaises(ValueError):
                lib.read(mid)
            self.assertEqual(lib.search("SECRET_SOLUTION", "A")["results"], [])
            self.assertEqual(lib.read(mid, include_solutions=True)["pages"][0]["text"], content)

            # Repeated confirmation and missing/negative flags cannot undo an
            # established answer gate, or create a new state/backup revision.
            final_bytes = state_path.read_bytes()
            backup_bytes = state_path.with_name("state.previous.json").read_bytes()
            for flag in (True, False, None):
                retry = deepcopy(request)
                retry["source"]["contains_solutions"] = flag
                repeated = install_download(state_path, retry)
                self.assertEqual(repeated["status"], "identical")
                self.assertFalse(repeated["state_written"])
                self.assertEqual(repeated["material_id"], mid)
                self.assertEqual(state_path.read_bytes(), final_bytes)
                self.assertEqual(state_path.with_name("state.previous.json").read_bytes(), backup_bytes)
                self.assertEqual(note.read_bytes(), note_before)
                self.assertEqual(source_file.read_bytes(), source_before)
                with self.assertRaises(ValueError):
                    lib.read(mid)

    def test_identical_file_upgrades_solution_flag_without_changing_learning_records(self):
        for initial_flag in (None, False):
            with self.subTest(initial_flag=initial_flag):
                self._check_identical_solution_upgrade(initial_flag, {"contains_solutions": True})

    def test_identical_file_answer_title_upgrades_neutral_filename(self):
        self._check_identical_solution_upgrade(None, {"title": "Practice Solutions"})

    def test_explicit_old_id_identical_content_from_new_module_upgrades_label(self):
        self._check_identical_solution_upgrade(
            None, {"module_id": 124, "contains_solutions": True}, explicit_old=True)


if __name__ == "__main__":
    unittest.main()
