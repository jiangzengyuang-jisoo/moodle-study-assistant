"""Course-level authorization, batch installation and merged-subject indexing.

All fixtures are invented local files inside an isolated Study folder: no
network, no real learner archive, no .private reads.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

from course_library import authorize, build_index, install_batch, status
from library import Library, digest
from library_test_fixtures import write_pdf
from material_versions import install_download

HERE = Path(__file__).resolve().parent
TEMP_ROOT = HERE.parents[1] / "tmp" / "library-tests"
SCRIPT = HERE / "course_library.py"
RUN_ID = "a" * 32


def isolated_directory(prefix):
    """Fresh ignored folder for one isolated synthetic Study tree."""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    folder = TEMP_ROOT / (prefix + uuid.uuid4().hex)
    folder.mkdir()
    return folder


class CourseLibraryTests(unittest.TestCase):
    LEARNING_COLLECTIONS = ("tasks", "mistakes", "review_attempts", "learning_feedback",
                            "refresh_receipts", "materials", "courses")

    def setUp(self):
        self.folder = isolated_directory("course-library-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.study = self.folder / "Study"
        self.state_path = self.study / "_System" / "state.json"
        self.state_path.parent.mkdir(parents=True)
        self.state = {
            "revision": 3,
            "updated_at": "2026-10-01T00:00:00+00:00",
            "settings": {"brief_send_authorized": False},
            "courses": [
                {"id": "moodle-146265", "name": "MATH1013", "moodle_course_ids": [146265],
                 "current_topic": "Subsection 4.3",
                 "assignment_focus": {"topics": ["4.3"], "task_ids": ["task-math"]}},
                {"id": "moodle-142648", "name": "MATH1013 1C", "moodle_course_ids": [142648],
                 "merged_into": "moodle-146265", "current_topic": "same as primary"},
                {"id": "moodle-146580", "name": "ACCT1101", "moodle_course_ids": [146580]},
            ],
            "materials": [],
            "tasks": [{"id": "task-math", "course_id": "moodle-146265", "status": "pending"}],
            "mistakes": [{"id": "mistake-1", "course_id": "moodle-146265",
                          "entry_type": "question", "answer": "retained"}],
            "review_attempts": [{"id": "attempt-1", "user_answer": "保留原答", "independent": False}],
            "learning_feedback": [{"id": "feedback-1", "completed": "retained"}],
            "refresh_receipts": [{"id": "receipt-1", "run_id": "b" * 32}],
        }
        self.save()

    def save(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")

    def read(self):
        return json.loads(self.state_path.read_text(encoding="utf-8-sig"))

    def previous(self):
        return json.loads((self.state_path.parent / "state.previous.json").read_text(encoding="utf-8"))

    def stage(self, name, content):
        staging = self.study / "_Inbox" / ".staging" / RUN_ID / uuid.uuid4().hex
        staging.mkdir(parents=True)
        path = staging / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        elif isinstance(content, (list, tuple)):
            write_pdf(path, list(content))
        else:
            path.write_text(content, encoding="utf-8")
        return {"relative_path": path.relative_to(self.study).as_posix(),
                "sha256": digest(path), "bytes": path.stat().st_size}

    def item(self, staged, filename, *, module=4143012, filepath="/",
             course_id="moodle-146265", moodle_courseid=146265, category="Lectures",
             title=None, archive_subdir=None, extra=None, source_pages=None, item_key=None):
        item = {
            "item_key": item_key or f"{course_id}|{module}|{filepath}|{filename}",
            "source": {"course_id": course_id, "module_id": module, "filename": filename,
                       "filepath": filepath,
                       "url": ("https://moodle.hku.hk/websocket/pluginfile.php/1/"
                               "mod_resource/content/0/file.pdf?forcedownload=1"),
                       "activity_url": f"https://moodle.hku.hk/mod/resource/view.php?id={module}",
                       "module_url": f"https://moodle.hku.hk/mod/resource/view.php?id={module}",
                       "title": title or Path(filename).stem, "category": category},
            "relative_path": staged["relative_path"], "sha256": staged["sha256"],
            "bytes": staged["bytes"],
        }
        if archive_subdir is not None:
            item["archive_subdir"] = archive_subdir
        if extra is not None:
            item["extra"] = extra
        if source_pages is not None:
            item["source_pages"] = source_pages
        return item

    def batch(self, *items, snapshot=None):
        return {"run_id": RUN_ID, "snapshot": snapshot, "items": list(items)}

    def authorization(self, *pages, mode="full", scope="本课程全部可下载资料"):
        return {"mode": mode, "scope": scope, "scope_version": 1,
                "courses": [{"course_id": course_id, "moodle_courseid": page_id, "mode": mode}
                            for course_id, page_id in pages]}

    def material(self, filename):
        return next(material for material in self.read()["materials"]
                    if material["filename"] == filename)

    def test_authorize_persists_merges_and_keeps_learning_records(self):
        before = self.read()
        result = authorize(self.state_path, {
            "mode": "full", "scope": "本课程全部可下载资料", "scope_version": 1,
            "courses": [
                {"course_id": "moodle-146265", "moodle_courseid": 146265, "mode": "full"},
                {"course_id": "moodle-146580", "moodle_courseid": 146580, "mode": "info_only"}]})
        state = self.read()
        auth = state["settings"]["course_library_authorization"]
        self.assertTrue(result["saved"])
        self.assertTrue(result["state_written"])
        self.assertEqual(result["revision"], before["revision"] + 1)
        self.assertEqual(result["mode"], "full")
        self.assertEqual(result["authorized_courses"], ["moodle-146265"])
        self.assertEqual(result["info_only_courses"], ["moodle-146580"])
        self.assertEqual(auth["schema"], 1)
        self.assertEqual(auth["scope"], "本课程全部可下载资料")
        self.assertEqual(auth["scope_version"], 1)
        self.assertTrue(auth["new_course_requires_authorization"])
        self.assertFalse(auth["per_file_approval_required"])
        self.assertFalse(auth["background_refresh"])
        self.assertEqual(set(auth["courses"]), {"moodle-146265", "moodle-146580"})
        self.assertEqual(auth["courses"]["moodle-146265"]["moodle_courseid"], 146265)
        self.assertEqual(auth["courses"]["moodle-146265"]["mode"], "full")
        self.assertEqual(auth["courses"]["moodle-146580"]["mode"], "info_only")
        self.assertEqual({key: auth["courses"]["moodle-146265"][key]
                          for key in ("moodle_courseid", "mode", "scope_version")},
                         {"moodle_courseid": 146265, "mode": "full", "scope_version": 1})
        self.assertTrue(auth["updated_at"].endswith("+00:00"))
        self.assertEqual(state["updated_at"], auth["updated_at"])
        for key in self.LEARNING_COLLECTIONS:
            self.assertEqual(state[key], before[key], key)
        self.assertEqual(self.previous(), before)

        first_entry = auth["courses"]["moodle-146265"]
        authorize(self.state_path, self.authorization(("moodle-146580", 146580), scope="本课程全部可下载资料"))
        merged = self.read()
        merged_auth = merged["settings"]["course_library_authorization"]
        self.assertEqual(merged_auth["courses"]["moodle-146265"], first_entry)
        self.assertEqual(merged_auth["courses"]["moodle-146580"]["mode"], "full")

        # Repeating the same authorization must not manufacture a new revision.
        state_bytes = self.state_path.read_bytes()
        previous_bytes = (self.state_path.parent / "state.previous.json").read_bytes()
        again = authorize(self.state_path, self.authorization(("moodle-146580", 146580),
                                                             scope="本课程全部可下载资料"))
        self.assertTrue(again["saved"])
        self.assertFalse(again["state_written"])
        self.assertEqual(again["revision"], merged["revision"])
        self.assertEqual(self.state_path.read_bytes(), state_bytes)
        self.assertEqual((self.state_path.parent / "state.previous.json").read_bytes(), previous_bytes)

    def test_authorize_rejects_invalid_requests_without_writing(self):
        before = self.state_path.read_bytes()
        base = self.authorization(("moodle-146265", 146265))
        invalid = [
            {**base, "mode": "partial"},
            {**base, "scope": ""},
            {**base, "scope": "x" * 2001},
            {**base, "courses": []},
            {**base, "courses": [{"course_id": "moodle-999", "moodle_courseid": 999, "mode": "full"}]},
            {**base, "courses": [{"course_id": "moodle-146265", "moodle_courseid": 146580, "mode": "full"}]},
            {**base, "courses": [{"course_id": "moodle-146265", "moodle_courseid": 146265,
                                  "mode": "partial"}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    authorize(self.state_path, payload)
                self.assertEqual(self.state_path.read_bytes(), before)
                self.assertFalse((self.state_path.parent / "state.previous.json").exists())

    def test_install_batch_registers_download_and_text_capture(self):
        lecture = self.stage("Lecture 01.pdf", ["Lecture one: separable equations"])
        capture = self.stage("forum-2273024.md",
                             "# Forum 2273024\n\nVoluntary course discussion.\n")
        before = self.read()
        pages = [{"course_id": "moodle-146265", "moodle_courseid": 146265,
                  "page_url": "https://moodle.hku.hk/course/view.php?id=146265",
                  "section": "Class Materials", "section_index": 2, "module_id": 4143012,
                  "module_name": "Lecture 01", "module_type": "resource"}]
        result = install_batch(self.state_path, self.batch(
            self.item(lecture, "Lecture 01.pdf", category="Lectures", source_pages=pages,
                      extra={"kind": "Moodle course file"}),
            self.item(capture, "forum-2273024.md", module=900001, filepath="/forum/2273024/",
                      course_id="moodle-142648", moodle_courseid=142648, category="Other",
                      extra={"kind": "Moodle text capture",
                             "capture": {"kind": "forum", "item_count": 12,
                                         "captured_at": "2026-11-02T10:00:00+00:00",
                                         "snapshot_run_id": "c" * 32,
                                         "source_pages": ["moodle-142648"]}},
                      source_pages=[{"course_id": "moodle-142648", "moodle_courseid": 142648,
                                     "page_url": "https://moodle.hku.hk/course/view.php?id=142648"}])))
        self.assertEqual([item["status"] for item in result["items"]], ["promoted", "promoted"])
        state = self.read()
        lecture_row = self.material("Lecture 01.pdf")
        capture_row = self.material("forum-2273024.md")
        self.assertEqual(lecture_row["course_id"], "moodle-146265")
        self.assertEqual(lecture_row["relative_path"], "moodle-146265/Lectures/Lecture 01.pdf")
        self.assertTrue((self.study / lecture_row["relative_path"]).is_file())
        self.assertEqual(lecture_row["sha256"], lecture["sha256"])
        self.assertEqual(lecture_row["source_pages"], pages)
        self.assertEqual(lecture_row["kind"], "Moodle course file")
        # A secondary page's material belongs to the primary subject directory.
        self.assertEqual(capture_row["course_id"], "moodle-146265")
        self.assertEqual(capture_row["relative_path"], "moodle-146265/Other/forum-2273024.md")
        self.assertTrue((self.study / capture_row["relative_path"]).is_file())
        self.assertEqual(capture_row["kind"], "Moodle text capture")
        self.assertEqual(capture_row["capture"]["item_count"], 12)
        self.assertEqual(capture_row["capture"]["source_pages"], ["moodle-142648"])
        self.assertEqual(len(capture_row["source_pages"]), 1)
        self.assertEqual(result["state_revision"], before["revision"] + 2)
        for row in state["materials"]:
            self.assertIsNot(row.get("verified"), True)
            self.assertNotIn("mastery", row)
        self.assertEqual(state["courses"], before["courses"])
        for key in ("tasks", "mistakes", "review_attempts", "learning_feedback", "refresh_receipts"):
            self.assertEqual(state[key], before[key], key)
        self.assertEqual(self.previous()["revision"], before["revision"] + 1)

    def test_identical_duplicate_and_same_name_archive_dirs(self):
        notes = self.stage("notes.pdf", ["module 11101 notes"])
        first_item = self.item(notes, "notes.pdf", module=11101, archive_subdir="module-11101")
        first = install_batch(self.state_path, self.batch(first_item))
        self.assertEqual(first["items"][0]["status"], "promoted")
        material_id = first["items"][0]["material_id"]
        state_after_first = self.state_path.read_bytes()

        # The same source re-downloaded into a fresh staging file is identical.
        restaged = self.item(self.stage("notes.pdf", ["module 11101 notes"]), "notes.pdf",
                             module=11101, archive_subdir="module-11101")
        repeated = install_batch(self.state_path, self.batch(restaged))
        self.assertEqual(repeated["items"][0]["status"], "identical")
        self.assertEqual(repeated["items"][0]["material_id"], material_id)
        self.assertEqual(self.state_path.read_bytes(), state_after_first)

        copy = self.stage("notes-copy.pdf", ["module 11101 notes"])
        duplicate = install_batch(self.state_path, self.batch(
            self.item(copy, "notes.pdf", module=11102, archive_subdir="module-11102")))
        self.assertEqual(duplicate["items"][0]["status"], "duplicate")
        self.assertEqual(duplicate["items"][0]["duplicate_of"], material_id)
        self.assertEqual(self.state_path.read_bytes(), state_after_first)

        other = self.stage("notes-other.pdf", ["module 11103 different notes"])
        second = install_batch(self.state_path, self.batch(
            self.item(other, "notes.pdf", module=11103, archive_subdir="module-11103")))
        self.assertEqual(second["items"][0]["status"], "promoted")
        state = self.read()
        self.assertEqual(len(state["materials"]), 2)
        self.assertEqual({material["archive_subdir"] for material in state["materials"]},
                         {"module-11101", "module-11103"})
        self.assertEqual({material["relative_path"] for material in state["materials"]},
                         {"moodle-146265/Lectures/module-11101/notes.pdf",
                          "moodle-146265/Lectures/module-11103/notes.pdf"})
        self.assertEqual(digest(self.study / "moodle-146265/Lectures/module-11101/notes.pdf"),
                         notes["sha256"])
        self.assertEqual(digest(self.study / "moodle-146265/Lectures/module-11103/notes.pdf"),
                         other["sha256"])

    def test_same_source_new_bytes_supersedes_and_archives_old(self):
        original = self.stage("Lecture 01.pdf", ["version one"])
        first = install_batch(self.state_path, self.batch(
            self.item(original, "Lecture 01.pdf", module=4143012, category="Lectures")))
        old_id = first["items"][0]["material_id"]
        self.assertEqual(first["items"][0]["relative_path"],
                         "moodle-146265/Lectures/Lecture 01.pdf")
        newer = self.stage("Lecture 01.pdf", ["version two updated"])
        second = install_batch(self.state_path, self.batch(
            self.item(newer, "Lecture 01.pdf", module=4143012, category="Lectures")))
        self.assertEqual(second["items"][0]["status"], "promoted")
        self.assertEqual(second["items"][0]["archived_id"], old_id)
        new_id = second["items"][0]["material_id"]
        state = self.read()
        old = next(material for material in state["materials"] if material["id"] == old_id)
        new = next(material for material in state["materials"] if material["id"] == new_id)
        self.assertEqual(new["supersedes"], old_id)
        self.assertEqual(new["version_status"], "current")
        self.assertEqual(new["relative_path"], "moodle-146265/Lectures/Lecture 01.pdf")
        self.assertEqual(old["version_status"], "archived")
        self.assertEqual(old["superseded_by"], new_id)
        self.assertIn("_Archive", old["relative_path"])
        self.assertTrue((self.study / old["relative_path"]).is_file())
        self.assertEqual(digest(self.study / old["relative_path"]), original["sha256"])
        self.assertEqual(digest(self.study / new["relative_path"]), newer["sha256"])

    def test_install_batch_errors_do_not_touch_state(self):
        good = self.stage("ok.pdf", ["good content"])
        bad = self.stage("bad.pdf", ["bad content"])
        outside = self.study / "moodle-146265" / "Other" / "outside.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes((self.study / bad["relative_path"]).read_bytes())
        before = self.read()
        wrong_hash = {**self.item(bad, "bad.pdf", module=500002, category="Other"),
                      "item_key": "wrong-hash", "sha256": "0" * 64}
        escaped = {**self.item(bad, "bad.pdf", module=500003, category="Other"),
                   "item_key": "escaped", "relative_path": "../outside.pdf"}
        reserved = self.item(bad, "bad.pdf", module=500004, category="Other",
                             extra={"course_id": "moodle-146580"})
        reserved["item_key"] = "reserved"
        unknown = {**self.item(bad, "bad.pdf", module=500005, category="Other"),
                   "item_key": "unknown"}
        unknown["source"] = {**unknown["source"], "course_id": "moodle-999"}
        wrong_bytes = {**self.item(bad, "bad.pdf", module=500006, category="Other"),
                       "item_key": "wrong-bytes", "bytes": 1}
        not_inbox = self.item(bad, "outside.pdf", module=500007, category="Other")
        not_inbox.update(item_key="not-inbox", relative_path=outside.relative_to(self.study).as_posix(),
                         sha256=digest(outside), bytes=outside.stat().st_size)
        unsafe_path = self.item(bad, "bad.pdf", module=500008, category="Other")
        unsafe_path["item_key"] = "unsafe-filepath"
        unsafe_path["source"] = {**unsafe_path["source"], "filepath": "../escape/"}
        missing = {**self.item(bad, "bad.pdf", module=500009, category="Other"),
                   "item_key": "missing",
                   "relative_path": "_Inbox/.staging/missing/missing.pdf"}
        good_item = self.item(good, "ok.pdf", module=500001, category="Other",
                              item_key="good")
        result = install_batch(self.state_path, self.batch(
            wrong_hash, escaped, reserved, unknown, wrong_bytes, not_inbox,
            unsafe_path, missing, good_item))
        statuses = {item["item_key"]: item["status"] for item in result["items"]}
        self.assertEqual(statuses, {"wrong-hash": "error", "escaped": "error",
                                    "reserved": "error", "unknown": "error",
                                    "wrong-bytes": "error", "not-inbox": "error",
                                    "unsafe-filepath": "error", "missing": "error",
                                    "good": "promoted"})
        for entry in result["items"]:
            if entry["status"] == "error":
                self.assertTrue(entry["error"])
                self.assertIsNone(entry["material_id"])
        state = self.read()
        self.assertEqual([material["filename"] for material in state["materials"]], ["ok.pdf"])
        self.assertEqual(self.previous()["revision"], before["revision"])

    def test_build_index_reaches_materials_through_merged_course(self):
        lecture = self.stage("Lecture 02.pdf", ["inverse functions and one-to-one maps"])
        installed = install_batch(self.state_path, self.batch(
            self.item(lecture, "Lecture 02.pdf", module=4143013, category="Lectures")))
        material_id = installed["items"][0]["material_id"]
        report = build_index(self.state_path, {"courses": ["moodle-142648", "moodle-146265"]})
        self.assertEqual(list(report["courses"]), ["moodle-146265"])
        self.assertEqual(report["courses"]["moodle-146265"]["materials"], 1)
        self.assertEqual(report["state_revision"], self.read()["revision"])
        library = Library(self.state_path)
        search = library.search("inverse functions", "moodle-142648")
        self.assertEqual({result["material_id"] for result in search["results"]}, {material_id})
        self.assertEqual(search["results"][0]["course_id"], "moodle-146265")
        outline = library.outline("moodle-142648")
        self.assertEqual([material["id"] for material in outline["materials"]], [material_id])
        self.assertEqual(outline["current_topic"], "Subsection 4.3")
        reading = library.read(material_id)
        self.assertIn("inverse functions", reading["pages"][0]["text"])
        state, rows, issues = library.fresh_rows("moodle-142648")
        self.assertEqual([row["id"] for row in rows], [material_id])
        self.assertEqual(issues, [])
        self.assertEqual(state["revision"], self.read()["revision"])
        self.assertEqual(library.sync("moodle-142648")["materials"], 1)

    def test_status_is_read_only_and_counts_current_and_archived(self):
        authorize(self.state_path, self.authorization(("moodle-146265", 146265)))
        first = self.stage("Lecture 03.pdf", ["one"])
        install_batch(self.state_path, self.batch(
            self.item(first, "Lecture 03.pdf", module=4143020, category="Lectures")))
        second = self.stage("Lecture 03.pdf", ["two"])
        install_batch(self.state_path, self.batch(
            self.item(second, "Lecture 03.pdf", module=4143020, category="Lectures")))
        before = self.state_path.read_bytes()
        result = status(self.state_path)
        self.assertEqual(result["materials"], {"current": 1, "archived": 1})
        self.assertEqual(result["last_sync"], None)
        self.assertEqual(result["authorization"]["courses"]["moodle-146265"]["mode"], "full")
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_cli_round_trip_and_error_exit(self):
        payload = self.authorization(("moodle-146580", 146580), mode="info_only", scope="只同步课程信息")
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), "--state", str(self.state_path),
                              "authorize"], input=json.dumps(payload, ensure_ascii=False),
                             capture_output=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(json.loads(run.stdout)["saved"])
        status_run = subprocess.run([sys.executable, "-B", str(SCRIPT), "--state",
                                     str(self.state_path), "status"],
                                    capture_output=True, encoding="utf-8")
        self.assertEqual(status_run.returncode, 0, status_run.stderr)
        saved = json.loads(status_run.stdout)
        self.assertEqual(saved["authorization"]["courses"]["moodle-146580"]["mode"], "info_only")
        before = self.state_path.read_bytes()
        bad = subprocess.run([sys.executable, "-B", str(SCRIPT), "--state", str(self.state_path),
                              "authorize"], input=json.dumps({**payload, "mode": "invalid"}),
                             capture_output=True, encoding="utf-8")
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(bad.stdout, "")
        self.assertIn("error", json.loads(bad.stderr))
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_install_download_can_defer_catalog_rebuild(self):
        lecture = self.stage("Lecture 04.pdf", ["deferred index"])
        data = {"source": {"course_id": "moodle-146265", "module_id": 600001,
                           "filename": "Lecture 04.pdf", "category": "Lectures",
                           "filepath": "/"},
                "relative_path": lecture["relative_path"], "sha256": lecture["sha256"],
                "bytes": lecture["bytes"], "extra": {"kind": "Moodle course file"}}
        result = install_download(self.state_path, data, sync=False)
        self.assertEqual(result["status"], "promoted")
        library_folder = self.state_path.parent / "Library"
        self.assertFalse((library_folder / "catalog.json").exists())
        build_index(self.state_path, {"courses": ["moodle-146265"]})
        self.assertTrue((library_folder / "catalog.json").exists())
        state, rows, issues = Library(self.state_path).fresh_rows("moodle-146265")
        self.assertEqual([row["id"] for row in rows], [result["material_id"]])
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
