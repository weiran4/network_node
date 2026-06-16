import unittest

import sympy as sp

from nodal_tool.dependency_analysis import (
    classify_expr_stage,
    classify_matrix_stage,
    analyze_reduced_model_dependencies,
)


class DependencyAnalysisTests(unittest.TestCase):
    def test_constant_expression_is_ram_init(self):
        G1, G2 = sp.symbols("G1 G2")
        stage = classify_expr_stage(G1 * G2 / (G1 + G2), {"G1": "RAM_CONSTANT", "G2": "RAM_CONSTANT"})
        self.assertEqual(stage, "RAM_INIT")

    def test_constant_variable_coupling_is_code_update(self):
        Gc, Gv = sp.symbols("Gc Gv")
        stage = classify_expr_stage(Gc * Gv / (Gc + Gv), {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE"})
        self.assertEqual(stage, "CODE_UPDATE")

    def test_history_dependency_is_code_per_step(self):
        G1, G2, h = sp.symbols("G1 G2 h")
        stage = classify_expr_stage(G1 * h / (G1 + G2), {"G1": "RAM_CONSTANT", "G2": "RAM_CONSTANT", "h": "STEP_HISTORY"})
        self.assertEqual(stage, "CODE_PER_STEP")

    def test_unknown_symbol_is_unknown_with_warning(self):
        G1, Gx = sp.symbols("G1 Gx")
        stage = classify_expr_stage(G1 * Gx, {"G1": "RAM_CONSTANT"})
        self.assertEqual(stage, "UNKNOWN")
        result = analyze_reduced_model_dependencies(
            {"Gred": sp.Matrix([[G1 * Gx]]), "Ihisred": sp.zeros(1, 1), "W": sp.zeros(1, 1), "Kv": sp.zeros(1, 1), "Kh": sp.zeros(1, 1)},
            {"G1": "RAM_CONSTANT"},
        )
        self.assertTrue(any("Symbol Gx has no dependency category" in warning for warning in result["warnings"]))

    def test_matrix_classification(self):
        G1, Gc, Gv, h = sp.symbols("G1 Gc Gv h")
        matrix = sp.Matrix([[G1, Gc * Gv / (Gc + Gv)], [h, 0]])
        stages = classify_matrix_stage(
            matrix,
            {"G1": "RAM_CONSTANT", "Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE", "h": "STEP_HISTORY"},
        )
        self.assertEqual(stages, [["RAM_INIT", "CODE_UPDATE"], ["CODE_PER_STEP", "RAM_INIT"]])

    def test_gred_history_dependency_warns(self):
        G1, h = sp.symbols("G1 h")
        result = analyze_reduced_model_dependencies(
            {"Gred": sp.Matrix([[G1 + h]]), "Ihisred": sp.zeros(1, 1), "W": sp.zeros(1, 1), "Kv": sp.zeros(1, 1), "Kh": sp.zeros(1, 1)},
            {"G1": "RAM_CONSTANT", "h": "STEP_HISTORY"},
        )
        self.assertTrue(any("Gred contains STEP_HISTORY dependency" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
