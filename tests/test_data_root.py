"""Program root / data root separation through STUDY_DATA_ROOT.

These tests use temporary directories and a synthetic state only. They never
read or write the real Study/, .private/ or config.local.json of the deployment.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUDY_TOOLS = ROOT / "tools/study"
MOODLE_TOOLS = ROOT / "tools/moodle"
PYTHON = sys.executable
NODE = os.environ.get("STUDY_NODE") or shutil.which("node")


def clean_environment():
    """Inherit the machine environment but never an inherited data-root override."""
    env = {key: value for key, value in os.environ.items()
           if key not in {"STUDY_DATA_ROOT", "STUDY_CHAT_ID", "STUDY_PYTHON"}}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run(command, env, cwd=ROOT, timeout=180):
    return subprocess.run([str(part) for part in command], cwd=str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def program_state_bytes():
    path = ROOT / "Study/_System/state.json"
    return path.read_bytes() if path.exists() else None


def program_config_bytes():
    path = ROOT / "config.local.json"
    return path.read_bytes() if path.exists() else None


class DataRootDefaultsTests(unittest.TestCase):
    def test_defaults_are_the_program_root_without_the_override(self):
        script = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(STUDY_TOOLS)!r})\n"
            f"sys.path.insert(0, {str(MOODLE_TOOLS)!r})\n"
            "import review, planning, library, session, product_tour, auth\n"
            "print(json.dumps({"
            "'review_root': str(review.ROOT), 'review_state': str(review.DEFAULT_STATE),"
            "'planning_root': str(planning.ROOT), 'planning_state': str(planning.DEFAULT_STATE),"
            "'library_root': str(library.ROOT), 'library_state': str(library.DEFAULT_STATE),"
            "'session_root': str(session.ROOT), 'session_state': str(session.DATA_ROOT),"
            "'tour_root': str(product_tour.ROOT), 'tour_state': str(product_tour.DATA_ROOT),"
            "'auth_root': str(auth.ROOT), 'auth_state': str(auth.DATA_ROOT)}))\n"
        )
        result = run([PYTHON, "-B", "-c", script], clean_environment())
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        program = str(ROOT)
        state = str(ROOT / "Study/_System/state.json")
        for key in ("review_root", "planning_root", "library_root", "session_root",
                    "tour_root", "auth_root"):
            self.assertEqual(program, value[key])
        for key in ("review_state", "planning_state", "library_state"):
            self.assertEqual(state, value[key])
        for key in ("session_state", "tour_state", "auth_state"):
            self.assertEqual(program, value[key])


class DataRootEntryPointTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()
        self.env = clean_environment()
        self.env["STUDY_DATA_ROOT"] = str(self.data)
        self.env["TMP"] = self.temp.name
        self.env["TEMP"] = self.temp.name

    def entry(self, command, timeout=180):
        result = run([PYTHON, "-B"] + command, self.env, timeout=timeout)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def create_state(self):
        if NODE is None:
            self.skipTest("Node is required to create the initial blank state")
        result = run([NODE, ROOT / "tools/moodle/run.mjs", "init"], self.env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((self.data / "Study/_System/state.json").is_file())

    def test_python_entry_points_write_only_the_data_root(self):
        program_state = program_state_bytes()
        program_config = program_config_bytes()
        self.create_state()
        self.entry([STUDY_TOOLS / "review.py", "init"])
        self.entry([STUDY_TOOLS / "daily.py", "run"])
        self.entry([STUDY_TOOLS / "daily.py", "check"])
        self.entry([STUDY_TOOLS / "library.py", "sync"])
        self.entry([STUDY_TOOLS / "product_tour.py", "status"])
        self.entry([STUDY_TOOLS / "session.py", "--chat", "data-root-test-chat", "operation-id"])
        self.assertTrue((self.data / "Study/_System/state.json").is_file())
        self.assertTrue((self.data / "config.local.json").is_file())
        # The release marker is a program asset; a fresh data root must not
        # silently fall back to the legacy Codex configuration.
        self.assertFalse((self.data / ".codex/config.toml").exists())
        # session.py --root defaults to the data root, so its runtime lands there.
        operations = self.data / "Study/_System/Runtime/operations"
        self.assertTrue(operations.is_dir())
        self.assertTrue(any(operations.iterdir()))
        self.assertEqual(program_state, program_state_bytes())
        self.assertEqual(program_config, program_config_bytes())

    def test_auth_defaults_read_the_data_root(self):
        system = self.data / "Study/_System"
        system.mkdir(parents=True)
        state = {"schema_version": 1, "revision": 0, "settings": {
            "profile_account_scope": {"site": "https://school.example", "user_id": 17}}}
        (system / "state.json").write_text(json.dumps(state), encoding="utf-8")
        script = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(MOODLE_TOOLS)!r})\n"
            "import auth\n"
            "print(json.dumps({'data_root': str(auth.DATA_ROOT),"
            "'accounts': auth.profile_accounts()}))\n"
        )
        result = run([PYTHON, "-B", "-c", script], self.env)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(str(self.data), value["data_root"])
        self.assertEqual(["profile_account_scope"], [row["source"] for row in value["accounts"]])
        self.assertEqual("https://school.example", value["accounts"][0]["site"])


if __name__ == "__main__":
    unittest.main()
