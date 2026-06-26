from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import sympy as sp

from reduce_api import _final_display_expr


ROOT = Path(__file__).resolve().parents[1]


class ReduceApiFinalSimplificationTests(unittest.TestCase):
    def _run_reduce_api(self, payload: dict) -> dict:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "reduce_api.py")],
            input=json.dumps(payload),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)

    def test_reduced_display_simplifies_cancelled_row_and_column_to_zero(self) -> None:
        payload = {
            "all_nodes": ["v10n", "v10p", "N5", "N6"],
            "external_nodes": ["v10p", "N5", "N6"],
            "G_full": [
                ["G11 + 1/R", "-G11 - 1/R", "G12", "-G12"],
                ["-G11 - 1/R", "G11 + 1/R", "-G12", "G12"],
                ["G12", "-G12", "G22", "-G22"],
                ["-G12", "G12", "-G22", "G22"],
            ],
            "Ihis_full": ["Ihisp", "-Ihisp", "Ihis_s", "-Ihis_s"],
            "ground_nodes": [],
        }

        result = self._run_reduce_api(payload)

        self.assertTrue(result["ok"], result)
        self.assertNotEqual("0", result["G_red"][0][0], "raw reduced expression should remain available")
        self.assertEqual(
            [["0", "0", "0"], ["0", result["G_red_simplified"][1][1], result["G_red_simplified"][1][2]], ["0", result["G_red_simplified"][2][1], result["G_red_simplified"][2][2]]],
            [
                result["G_red_simplified"][0],
                [result["G_red_simplified"][1][0], result["G_red_simplified"][1][1], result["G_red_simplified"][1][2]],
                [result["G_red_simplified"][2][0], result["G_red_simplified"][2][1], result["G_red_simplified"][2][2]],
            ],
        )
        self.assertEqual("0", result["Ihis_red_simplified"][0])
        self.assertNotIn("G_red_latex", result)
        self.assertNotIn("Ihis_red_latex", result)
        self.assertNotIn("K_v_latex", result)
        self.assertNotIn("K_h_latex", result)

    def test_display_simplification_keeps_readable_fraction_sums(self) -> None:
        G11, G12, AA, CC, G22, G_rc, w1, w2 = sp.symbols("G11 G12 AA CC G22 G_rc w1 w2")
        expr = (
            2 * G11
            - G12**2 / (CC + G22 + G_rc + w2)
            - G12**2 / (AA + G22 + G_rc + w2)
            + 2 * w1
        )

        display_expr = _final_display_expr(expr)

        self.assertEqual(str(expr), str(display_expr))
        self.assertGreater(len(str(sp.cancel(expr))), len(str(display_expr)))


if __name__ == "__main__":
    unittest.main()
