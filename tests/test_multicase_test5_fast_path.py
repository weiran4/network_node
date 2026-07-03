import json
import subprocess
import sys
import time
import unittest
from pathlib import Path


class MultiCaseTest5FastPathTests(unittest.TestCase):
    def test_test5_multicase_uses_case_alias_template_fast_path(self):
        data = json.loads(Path("exports/test5.json").read_text(encoding="utf-8"))
        cached = (data.get("reducedEquationCache") or [{}])[0].get("key")
        if not cached:
            self.skipTest("exports/test5.json does not include the legacy reducedEquationCache payload")
        payload = json.loads(cached)["payload"]

        def case_payload(case_index):
            clone = json.loads(json.dumps(payload))
            if case_index == 1:
                for row in range(len(clone["G_full"])):
                    for col in range(len(clone["G_full"][row])):
                        clone["G_full"][row][col] = (
                            clone["G_full"][row][col]
                            .replace("AA", "Dabc")
                            .replace("BB", "Dabc")
                            .replace("CC", "Dabc")
                        )
                        clone["G_full_tagged"][row][col] = (
                            clone["G_full_tagged"][row][col]
                            .replace("AA", "Dabc")
                            .replace("BB", "Dabc")
                            .replace("CC", "Dabc")
                        )
                for table_name in ["symbol_dependency_table", "symbol_dependency_table_tagged"]:
                    table = clone.get(table_name) or {}
                    table["Dabc"] = "CODE_VARIABLE"
            return clone

        request = {
            "mode": "multi_case_c_export",
            "case_profiles": [
                {"name": "Case 1", "case_map": {"C1": 0}, "payload": case_payload(0)},
                {"name": "Case 2", "case_map": {"C1": 1}, "payload": case_payload(1)},
            ],
        }
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(request),
            cwd=".",
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        elapsed = time.perf_counter() - started
        response = json.loads(completed.stdout)
        self.assertTrue(response["ok"], response)
        self.assertLess(elapsed, 30)
        self.assertEqual(response["multi_case"]["fast_path"], "case_alias_template")
        self.assertEqual(response["multi_case"]["codegen_mode"], "case-agnostic alias template")
        draft = response["multi_case"]["c_draft"]
        self.assertIn("Multi-case alias-template C draft", draft)
        self.assertIn("switch (C1_case_id)", draft)
        self.assertIn("Dabc", draft)
        self.assertIn("multcase_G_C1", draft)
        self.assertNotIn("_global", draft)


if __name__ == "__main__":
    unittest.main()
