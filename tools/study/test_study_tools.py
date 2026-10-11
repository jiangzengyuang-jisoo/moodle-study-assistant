"""Integration checks use temporary learning state, never the user's live history."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def call(script, *args, payload=None):
    result = subprocess.run(
        [sys.executable, str(HERE / script), *map(str, args)],
        input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        encoding="utf-8", capture_output=True,
    )
    return result, json.loads(result.stdout) if result.stdout.strip() else None


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.original = {
            "revision": 4, "settings": {"brief_send_authorized": False},
            "integrations": {"moodle": {"status": "connected"}},
            "courses": [{"id": "MATH-DEMO", "mastered": []}],
            "tasks": [{"id": "A1", "status": "Completed"}],
            "mistakes": [{"id": "M1", "course_id": "MATH-DEMO", "entry_type": "question",
                          "display_title": "极限", "check_question": "求极限"}],
            "review_queue": [
                {"id": "R1", "course_id": "MATH-DEMO", "mistake_id": "M1",
                 "priority": "High", "due_date": None, "status": "independent_review_pending",
                 "independent_prompts": ["不看答案，求极限"], "answer_check": ["隐藏答案"],
                 "estimated_minutes": {"min": 3, "max": 5}},
                {"id": "R2", "course_id": "MATH-DEMO", "mistake_id": "M1",
                 "priority": "Normal", "due_date": "2030-01-01", "prompt": "未来项目"},
            ], "activity_log": [],
        }
        self.path.write_text(json.dumps(self.original), encoding="utf-8")

    def command(self, *args, payload=None, ok=True):
        result, data = call("review.py", "--state", self.path, *args, payload=payload)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return data

    def attempt(self, **changes):
        data = {"review_id": "R1", "attempt_id": "attempt-1", "rating": "good",
                "prompt": "独立求 lim x→1 (x²−1)/(x−1)", "answer": "2，因为可约为x+1且x≠1",
                "feedback": "本题正确，保留原定义域限制", "evidence": "用户当前对话中的独立作答",
                "independent": True, "reviewed_at": "2026-09-19T23:59:00+08:00"}
        return dict(data, **changes)

    def test_init_and_preview_do_not_invent_progress_or_expose_answers(self):
        self.command("init")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        for key in ("review_queue", "mistakes", "tasks", "courses"):
            self.assertEqual(state[key], self.original[key])
        after = self.path.read_bytes()
        preview = self.command("due", "--at", "2026-09-21T20:00:00+08:00", "--minutes", "5")
        self.assertEqual([item["id"] for item in preview["items"]], ["R1"])
        self.assertEqual(preview["estimated_minutes"], 5)
        self.assertNotIn("隐藏答案", json.dumps(preview, ensure_ascii=False))
        self.assertEqual(self.path.read_bytes(), after)
        self.assertEqual(self.command("due", "--minutes", "4")["items"], [])
        self.command("init")
        self.assertEqual(self.path.read_bytes(), after)

    def test_record_persists_real_evidence_hk_date_and_deduplicates(self):
        self.command("init")
        self.command("record", payload=self.attempt())
        state = json.loads(self.path.read_text(encoding="utf-8"))
        item = state["review_queue"][0]
        self.assertEqual(item["due_date"], "2026-09-20")
        self.assertTrue(item["fsrs"]["card"])
        self.assertEqual(len(item["fsrs"]["history"]), 1)
        self.assertEqual(item["fsrs"]["history"][0]["answer"], self.attempt()["answer"])
        for key in ("tasks", "courses", "mistakes"):
            self.assertEqual(state[key], self.original[key])
        self.assertEqual(state["integrations"]["moodle"], self.original["integrations"]["moodle"])
        after = self.path.read_bytes()
        self.assertEqual(self.command("record", payload=self.attempt())["status"], "duplicate")
        self.assertEqual(self.path.read_bytes(), after)
        self.command("record", payload=self.attempt(answer="不同答案"), ok=False)
        self.assertEqual(self.path.read_bytes(), after)
        self.assertTrue(self.path.with_name("state.previous.json").exists())
        self.assertIn("2026-09-20", self.path.with_name("Review Queue.md").read_text(encoding="utf-8"))

    def test_rejects_assisted_empty_or_out_of_order_attempt_without_writing(self):
        self.command("init")
        before = self.path.read_bytes()
        for changes in ({"independent": False}, {"evidence": ""}, {"rating": "mastered"},
                        {"reviewed_at": "2026-09-21T20:00:00"}, {"review_id": "missing"}):
            self.command("record", payload=self.attempt(**changes), ok=False)
            self.assertEqual(self.path.read_bytes(), before)
        self.command("record", payload=self.attempt())
        before = self.path.read_bytes()
        self.command("record", payload=self.attempt(attempt_id="older", reviewed_at="2026-09-18T12:00:00+08:00"), ok=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_recall_is_sooner_than_easy_and_due_filter_uses_time(self):
        self.command("init")
        self.command("record", payload=self.attempt(rating="again"))
        again = json.loads(self.path.read_text(encoding="utf-8"))["review_queue"][0]["fsrs"]["card"]["due"]
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        self.command("init")
        self.command("record", payload=self.attempt(rating="easy"))
        easy = json.loads(self.path.read_text(encoding="utf-8"))["review_queue"][0]["fsrs"]["card"]["due"]
        self.assertLess(again, easy)
        self.assertEqual(self.command("due", "--at", "2026-09-20T00:00:00+08:00")["items"], [])


class MathTests(unittest.TestCase):
    def math(self, data):
        result, output = call("math_check.py", payload=data)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output

    def test_limits_distinguish_two_sides_and_removable_hole(self):
        data = self.math({"operation": "limit", "expression": "1/x", "variable": "x", "point": "0"})
        self.assertEqual(data["status"], "does_not_exist")
        self.assertEqual((data["left"], data["right"]), ("-oo", "oo"))
        data = self.math({"operation": "limit", "expression": "(x**2-1)/(x-1)", "variable": "x", "point": "1"})
        self.assertEqual(data["result"], "2")
        self.assertFalse(data["original_value_checked"])

    def test_real_assumptions_and_equivalence_do_not_erase_domain_conditions(self):
        self.assertEqual(self.math({"operation": "simplify", "expression": "sqrt(x**2)"})["result"], "Abs(x)")
        positive = self.math({"operation": "simplify", "expression": "sqrt(x**2)", "symbols": {"x": "positive"}})
        self.assertEqual(positive["result"], "x")
        check = self.math({"operation": "equivalent", "expression": "(x**2-1)/(x-1)", "other": "x+1"})
        self.assertTrue(check["equal_on_common_domain"])
        self.assertFalse(check["same_domain_verified"])
        check = self.math({"operation": "equivalent", "expression": "cos(acos(x)+pi)", "other": "x"})
        self.assertFalse(check["equal_on_common_domain"])

    def test_calculus_and_complex_numbers_use_exact_arithmetic(self):
        cases = [
            ({"operation": "differentiate", "expression": "x**3", "variable": "x"}, "3*x**2"),
            ({"operation": "integrate", "expression": "2*x", "variable": "x"}, "x**2"),
            ({"operation": "evaluate", "expression": "(1+I)**2"}, "2*I"),
            ({"operation": "evaluate", "expression": "0.1+0.2"}, "3/10"),
        ]
        for request, result in cases:
            with self.subTest(request=request):
                self.assertEqual(self.math(request)["result"], result)

    def test_rejects_executable_input_and_undefined_value(self):
        for expression in ("__import__('os').getcwd()", "x.__class__", "[x for x in (1,2)]", "2**999999"):
            result, _ = call("math_check.py", payload={"operation": "simplify", "expression": expression})
            self.assertNotEqual(result.returncode, 0)
        data = self.math({"operation": "evaluate", "expression": "(x**2-1)/(x-1)", "values": {"x": "1"}})
        self.assertEqual(data["status"], "undefined")

    def test_oscillation_is_not_a_limit_and_undefined_identity_is_not_checked(self):
        data = self.math({"operation": "limit", "expression": "sin(1/x)", "point": "0"})
        self.assertEqual(data["status"], "does_not_exist")
        data = self.math({"operation": "equivalent", "expression": "1/0", "other": "1/0"})
        self.assertEqual(data["status"], "undefined")
        self.assertIsNone(data["equal_on_common_domain"])


if __name__ == "__main__":
    unittest.main()
