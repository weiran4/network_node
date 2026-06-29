import json
import subprocess
import sys
import unittest
from pathlib import Path


class MultiCaseVarGFixtureTests(unittest.TestCase):
    def test_varG_fixture_generates_scalar_mux_for_R11_cases(self):
        data = json.loads(Path("exports/varG_test.json").read_text(encoding="utf-8"))
        branches = data.get("branches") or []
        r11 = next(branch for branch in branches if branch.get("id") == "B11")
        cases = r11.get("switchCases") or []
        self.assertEqual([case.get("g") for case in cases], ["G1", "G2"])

        def payload_for(g_symbol):
            return {
                "all_nodes": ["n10a", "N4", "N6", "n10b"],
                "external_nodes": ["n10a", "N4", "N6"],
                "internal_nodes": ["n10b"],
                "ground_nodes": [],
                "node_display_names": {"n10a": "n10a", "N4": "N4", "N6": "N6", "n10b": "n10b"},
                "G_full": [
                    ["1/R", "0", "-1/R", "-1/R"],
                    ["0", g_symbol, "0", f"-({g_symbol})"],
                    ["-1/R", "0", "2/R", "0"],
                    ["-1/R", f"-({g_symbol})", "0", f"1/R + ({g_symbol})"],
                ],
                "Ihis_full": ["Ihis1 + Ihis4", "-Ihis2 + Ihis3", "-Ihis3 - Ihis4", "-Ihis1 + Ihis2"],
                "G_full_tagged": [
                    ["1/R_tag", "0", "-1/R_tag", "-1/R_tag"],
                    ["0", f"{g_symbol}_tag", "0", f"-({g_symbol}_tag)"],
                    ["-1/R_tag", "0", "2/R_tag", "0"],
                    ["-1/R_tag", f"-({g_symbol}_tag)", "0", f"1/R_tag + ({g_symbol}_tag)"],
                ],
                "Ihis_full_tagged": ["Ihis1 + Ihis4", "-Ihis2 + Ihis3", "-Ihis3 - Ihis4", "-Ihis1 + Ihis2"],
                "symbol_dependency_table": {
                    "R": "RAM_CONSTANT",
                    "R_tag": "RAM_CONSTANT",
                    g_symbol: "CODE_VARIABLE",
                    f"{g_symbol}_tag": "CODE_VARIABLE",
                    "Ihis1": "STEP_HISTORY",
                    "Ihis2": "STEP_HISTORY",
                    "Ihis3": "STEP_HISTORY",
                    "Ihis4": "STEP_HISTORY",
                },
                "symbol_dependency_table_tagged": {
                    "R": "RAM_CONSTANT",
                    "R_tag": "RAM_CONSTANT",
                    g_symbol: "CODE_VARIABLE",
                    f"{g_symbol}_tag": "CODE_VARIABLE",
                    "Ihis1": "STEP_HISTORY",
                    "Ihis2": "STEP_HISTORY",
                    "Ihis3": "STEP_HISTORY",
                    "Ihis4": "STEP_HISTORY",
                },
                "direct_retained_stamps": [
                    {
                        "id": "B13",
                        "support_nodes": ["n10a", "N6"],
                        "G": [
                            {"row": "n10a", "col": "n10a", "expr": "1/R", "tagged": "1/R_tag"},
                            {"row": "n10a", "col": "N6", "expr": "-1/R", "tagged": "-1/R_tag"},
                            {"row": "N6", "col": "n10a", "expr": "-1/R", "tagged": "-1/R_tag"},
                            {"row": "N6", "col": "N6", "expr": "1/R", "tagged": "1/R_tag"},
                        ],
                        "Ihis": [],
                    }
                ],
            }

        request = {
            "mode": "multi_case_c_export",
            "case_profiles": [
                {
                    "name": cases[0].get("name"),
                    "comment": "R11 = Case 1",
                    "case_map": {"B11": 0},
                    "payload": payload_for("G1"),
                },
                {
                    "name": cases[1].get("name"),
                    "comment": "R11 = Case 2",
                    "case_map": {"B11": 1},
                    "payload": payload_for("G2"),
                },
            ],
        }
        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(request),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)
        self.assertTrue(response["ok"], response)
        direct_preview = response["multi_case"]["template_direct_retained"]
        self.assertEqual(
            direct_preview["Gred_direct"],
            [["1/R", "0", "-1/R"], ["0", "0", "0"], ["-1/R", "0", "1/R"]],
        )
        draft = response["multi_case"]["c_draft"]
        self.assertEqual(response["multi_case"]["fast_path"], "case_alias_template")
        self.assertIn("Multi-case alias-template C draft", draft)
        self.assertIn("multcase_G_B11_N4_N4", draft)
        self.assertIn("switch (B11_case_id)", draft)
        self.assertIn("multcase_G_B11_N4_N4 = G1;", draft)
        self.assertIn("multcase_G_B11_N4_N4 = G2;", draft)
        self.assertIn("Grr_N4_N4 = multcase_G_B11_N4_N4;", draft)
        self.assertIn("g_mat_over[0][1] = -1.0/R;", draft)
        self.assertIn("g_mat_over[1][0] = -1.0/R;", draft)
        self.assertIn("g_mat_over[1][1] = 2.0/R;", draft)
        self.assertNotIn('varG_n10a_N6 = createGValue', draft)
        self.assertNotIn("G2 - G1", draft)
        self.assertNotIn("Not found", draft)


if __name__ == "__main__":
    unittest.main()
