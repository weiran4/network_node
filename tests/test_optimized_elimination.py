import unittest
import json
import io
import subprocess
import sys

import sympy as sp

from elimination import eliminate_internal_nodes
from nodal_tool.optimized_elimination import (
    build_dependency_stage_plan,
    build_structured_formula,
    check_symmetric,
    cse_c_draft_for_formula_mode,
    c_draft_for_structured_formula,
    sequential_symmetric_eliminate,
)


def assert_matrix_equal(testcase, actual, expected):
    actual = sp.Matrix(actual)
    expected = sp.Matrix(expected)
    testcase.assertEqual(actual.shape, expected.shape)
    for value, target in zip(actual, expected):
        testcase.assertEqual(sp.simplify(value - target), 0)


class OptimizedEliminationTests(unittest.TestCase):
    def _basic_multicase_payload(self, *, g1: str, g2: str, h: str = "0") -> dict:
        return {
            "all_nodes": ["A", "B", "X"],
            "external_nodes": ["A", "B"],
            "internal_nodes": ["X"],
            "ground_nodes": [],
            "G_full": [
                [g1, "0", f"-({g1})"],
                ["0", g2, f"-({g2})"],
                [f"-({g1})", f"-({g2})", f"({g1}) + ({g2})"],
            ],
            "Ihis_full": ["0", "0", h],
            "G_full_tagged": [
                [f"{g1}_tag", "0", f"-({g1}_tag)"],
                ["0", f"{g2}_tag", f"-({g2}_tag)"],
                [f"-({g1}_tag)", f"-({g2}_tag)", f"({g1}_tag) + ({g2}_tag)"],
            ],
            "Ihis_full_tagged": ["0", "0", f"{h}_tag" if h != "0" else "0"],
            "node_display_names": {"A": "A", "B": "B", "X": "X"},
            "symbol_dependency_table": {
                g1: "RAM_CONSTANT",
                g2: "RAM_CONSTANT",
                f"{g1}_tag": "RAM_CONSTANT",
                f"{g2}_tag": "RAM_CONSTANT",
            },
            "symbol_dependency_table_tagged": {
                g1: "RAM_CONSTANT",
                g2: "RAM_CONSTANT",
                f"{g1}_tag": "RAM_CONSTANT",
                f"{g2}_tag": "RAM_CONSTANT",
            },
        }

    def test_multi_case_api_generates_ram_switch_collection_draft(self):
        payload = {
            "mode": "multi_case_c_export",
            "case_id_symbol": "topology_case",
            "case_profiles": [
                {
                    "name": "YY",
                    "comment": "TX=Y, RC=Y",
                    "case_map": {"tx": 0},
                    "payload": self._basic_multicase_payload(g1="G1", g2="G2"),
                },
                {
                    "name": "DD",
                    "comment": "TX=D, RC=D",
                    "case_map": {"tx": 1},
                    "payload": self._basic_multicase_payload(g1="G3", g2="G2"),
                },
            ],
        }

        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(payload),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)

        self.assertTrue(response["ok"], response)
        draft = response["multi_case"]["c_draft"]
        self.assertEqual(response["multi_case"]["fast_path"], "case_alias_template")
        self.assertIn("Multi-case alias-template C draft", draft)
        self.assertIn("cr_tx_G_eff", draft)
        self.assertIn("switch (tx_case_id)", draft)
        self.assertIn("cr_tx_G_eff = G1;", draft)
        self.assertIn("cr_tx_G_eff = G3;", draft)
        self.assertNotIn("TODO", draft)
        self.assertIn("set_CODE(&Gkr_code, 0, 0, Gkr_k1_A);", draft)
        self.assertIn("Gkr_k1_A = -cr_tx_G_eff;", draft)
        self.assertIn("matrix_mult_CODE", draft)

    def test_two_conductances_in_series(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "X", "B"]
        G = sp.Matrix([[G1, -G1, 0], [-G1, G1 + G2, -G2], [0, -G2, G2]])
        Ihis = sp.zeros(3, 1)

        result = sequential_symmetric_eliminate(G, Ihis, nodes, ["A", "B"], ["X"])

        G_eq = G1 * G2 / (G1 + G2)
        assert_matrix_equal(self, result["G_red"], sp.Matrix([[G_eq, -G_eq], [-G_eq, G_eq]]))
        assert_matrix_equal(self, result["Ihis_red"], sp.zeros(2, 1))
        recovery = result["recovery_steps"][0]
        self.assertEqual(recovery["node"], "X")
        assert_matrix_equal(self, sp.Matrix([recovery["coefficients"]]), sp.Matrix([[G1 / (G1 + G2), G2 / (G1 + G2)]]))

    def test_two_internal_nodes_in_reverse_order(self):
        G1, G2, G3 = sp.symbols("G1 G2 G3")
        nodes = ["A", "X", "Y", "B"]
        G = sp.Matrix(
            [
                [G1, -G1, 0, 0],
                [-G1, G1 + G2, -G2, 0],
                [0, -G2, G2 + G3, -G3],
                [0, 0, -G3, G3],
            ]
        )
        result = sequential_symmetric_eliminate(G, sp.zeros(4, 1), nodes, ["A", "B"], ["X", "Y"])

        self.assertEqual(result["elimination_order"], ["Y", "X"])
        G_eq = G1 * G2 * G3 / (G1 * G2 + G1 * G3 + G2 * G3)
        assert_matrix_equal(self, result["G_red"], sp.Matrix([[G_eq, -G_eq], [-G_eq, G_eq]]))

    def test_matches_existing_schur_result_on_small_symmetric_network(self):
        G1, G2, G3, h = sp.symbols("G1 G2 G3 h")
        nodes = ["A", "X", "B"]
        G = sp.Matrix([[G1 + G3, -G1, -G3], [-G1, G1 + G2, -G2], [-G3, -G2, G2 + G3]])
        Ihis = sp.Matrix([[h], [0], [-h]])

        existing = eliminate_internal_nodes(G, Ihis, nodes, ["A", "B"])
        optimized = sequential_symmetric_eliminate(G, Ihis, nodes, ["A", "B"], ["X"])

        assert_matrix_equal(self, optimized["G_red"], existing.G_red)
        assert_matrix_equal(self, optimized["Ihis_red"], existing.Ihis_red)

    def test_non_symmetric_warning(self):
        G = sp.Matrix([[1, 2], [3, 4]])

        symmetric, mismatches = check_symmetric(G)

        self.assertFalse(symmetric)
        self.assertEqual(mismatches[0][:2], (0, 1))
        result = sequential_symmetric_eliminate(G, sp.zeros(2, 1), ["A", "X"], ["A"], ["X"])
        self.assertIn("Matrix is not symmetric. SPD optimized elimination may be invalid.", result["warnings"])

    def test_formula_c_draft_uses_cse_temporaries(self):
        G1, G2 = sp.symbols("G1 G2")
        common = G1 * G2 / (G1 + G2)
        draft = cse_c_draft_for_formula_mode(sp.Matrix([[common, -common], [-common, common]]), sp.zeros(2, 1))

        self.assertIn("double t", draft)
        self.assertIn("Gred[0][0]", draft)
        self.assertLess(draft.count("G1 + G2"), 2)

    def test_structured_c_draft_declares_matrix_error_counter(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "X", "B"]
        G = sp.Matrix([[G1, -G1, 0], [-G1, G1 + G2, -G2], [0, -G2, G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "CODE_VARIABLE", "G2": "CODE_VARIABLE"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("RAM_PASS1:\n    int err = 0;", draft)
        self.assertNotIn("STATIC:\n    int err", draft)

    def test_structured_c_draft_includes_matrixlib_when_using_matrix_objects(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "X", "B"]
        G = sp.Matrix([[G1, -G1, 0], [-G1, G1 + G2, -G2], [0, -G2, G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "CODE_VARIABLE", "G2": "CODE_VARIABLE"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("MATRIX_", draft)
        self.assertIn("#include <matrixLIB.h>", draft)

    def test_code_stage_g_matrices_are_not_prefilled_with_ram_set_calls(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "X", "B"]
        G = sp.Matrix([[G1, -G1, 0], [-G1, G1 + G2, -G2], [0, -G2, G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "CODE_VARIABLE", "G2": "CODE_VARIABLE"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertNotIn("Same MATRIX_ objects are used from RAM and CODE when needed.", draft)
        self.assertNotIn("set(&Gkr_code", draft)
        self.assertNotIn("set(&W_code", draft)
        self.assertIn("set_CODE(&Gkr_code", draft)
        self.assertIn("set_CODE(&W_code", draft)

    def test_no_elimination_c_draft_declares_matrix_error_counter(self):
        G1, G2 = sp.symbols("G1 G2")
        G = sp.Matrix([[G1, -G1], [-G1, G1 + G2]])
        structured = build_structured_formula(G, sp.zeros(2, 1), ["A", "B"], ["A", "B"], [])
        plan = build_dependency_stage_plan(structured, {"G1": "CODE_VARIABLE", "G2": "CODE_VARIABLE"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("RAM_PASS1:\n    int err = 0;", draft)
        self.assertNotIn("STATIC:\n    int err", draft)

    def test_dependency_stage_plan_accepts_borrowed_reduced_model_without_structured_inverse(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "X", "B"]
        G = sp.Matrix([[G1, -G1, 0], [-G1, G1 + G2, -G2], [0, -G2, G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        borrowed_model = {
            "Gred": sp.Matrix([[G1, 0], [0, G2]]),
            "Ihisred": sp.zeros(2, 1),
            "W": sp.zeros(1, 1),
            "Kv": sp.zeros(1, 2),
            "Kh": sp.zeros(1, 1),
        }

        import nodal_tool.optimized_elimination as optimized

        original = optimized.structured_dependency_model
        try:
            optimized.structured_dependency_model = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("structured dependency model should not be used")
            )
            plan = build_dependency_stage_plan(
                structured,
                {"G1": "RAM_CONSTANT", "G2": "CODE_VARIABLE"},
                dependency_model_override=borrowed_model,
            )
        finally:
            optimized.structured_dependency_model = original

        self.assertEqual(plan["dependency_analysis"]["Gred_stage"], [["RAM_INIT", "RAM_INIT"], ["RAM_INIT", "CODE_UPDATE"]])
        self.assertEqual(plan["Gred"], borrowed_model["Gred"])

    def test_general_stage_draft_inverts_gkk_at_runtime_when_w_is_not_symbolic(self):
        G1, G2, G3, G4, G5, G6 = sp.symbols("G1 G2 G3 G4 G5 G6")
        nodes = ["A", "X", "Y", "B"]
        G = sp.Matrix(
            [
                [G1, -G1, 0, 0],
                [-G1, G1 + G2 + G5, -G5, -G2],
                [0, -G5, G3 + G4 + G5, -G4],
                [0, -G2, -G4, G2 + G4],
            ]
        )
        structured = build_structured_formula(G, sp.zeros(4, 1), nodes, ["A", "B"], ["X", "Y"])
        borrowed_model = {
            "Gred": sp.Matrix([[G1, 0], [0, G2 + G4]]),
            "Ihisred": sp.zeros(2, 1),
            "W": None,
            "Kv": sp.zeros(2, 2),
            "Kh": sp.zeros(2, 1),
        }

        plan = build_dependency_stage_plan(
            structured,
            {name: "CODE_VARIABLE" for name in ["G1", "G2", "G3", "G4", "G5", "G6"]},
            dependency_model_override=borrowed_model,
        )
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("MATRIX_ Gkk_code", draft)
        self.assertIn("MATH_matx_invert(NK, &(Gkk_code.p[0]), NK, &(W_code.p[0]), NK);", draft)
        self.assertNotIn("1.0/(G1*G3", draft)

    def test_diagonal_plus_coupled_can_skip_symbolic_w_details_for_borrowed_stage(self):
        nodes = ["A", "B", "x", "y", "z", "P", "N"]
        symbols = sp.symbols("gA gB ax by dx dy dz xP xN yP yN zP zN PP PN NN")
        gA, gB, ax, by, dx, dy, dz, xP, xN, yP, yN, zP, zN, PP, PN, NN = symbols
        G = sp.Matrix(
            [
                [gA, 0, ax, 0, 0, 0, 0],
                [0, gB, 0, by, 0, 0, 0],
                [ax, 0, dx, 0, 0, xP, xN],
                [0, by, 0, dy, 0, yP, yN],
                [0, 0, 0, 0, dz, zP, zN],
                [0, 0, xP, yP, zP, PP, PN],
                [0, 0, xN, yN, zN, PN, NN],
            ]
        )

        structured = build_structured_formula(
            G,
            sp.zeros(7, 1),
            nodes,
            ["A", "B"],
            ["x", "y", "z", "P", "N"],
            skip_symbolic_w_details=True,
        )
        borrowed_model = {
            "Gred": sp.Matrix([[gA, 0], [0, gB]]),
            "Ihisred": sp.zeros(2, 1),
            "W": structured["details"]["W"],
            "Kv": sp.zeros(5, 2),
            "Kh": sp.zeros(5, 1),
        }

        self.assertEqual(structured["block_type"], "diagonal_plus_coupled")
        self.assertEqual(sp.Matrix(structured["details"]["M"]), sp.zeros(2, 2))
        self.assertEqual(sp.Matrix(structured["details"]["W"]).shape, (5, 5))
        plan = build_dependency_stage_plan(
            structured,
            {str(symbol): "CODE_VARIABLE" for symbol in symbols},
            dependency_model_override=borrowed_model,
        )
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("Build M = S - U^T * inv_D * U", draft)
        self.assertIn("MATRIX_ W_DD", draft)
        self.assertIn("matrix_mult_CODE(&tmp_W_Gkr_code, &W_code, &Gkr_code);", draft)

    def test_runtime_w_ihisred_rows_use_structural_mask_not_placeholder_cancellation(self):
        g, Dx, Dy, xp, xn, yp, yn, PP, PN, NN, h = sp.symbols("g Dx Dy xp xn yp yn PP PN NN h")
        nodes = ["A", "x", "y", "P", "N"]
        G = sp.Matrix(
            [
                [0, g, -g, 0, 0],
                [g, Dx, 0, xp, xn],
                [-g, 0, Dy, yp, yn],
                [0, xp, yp, PP, PN],
                [0, xn, yn, PN, NN],
            ]
        )
        Ihis = sp.Matrix([[0], [h], [h], [0], [0]])
        structured = build_structured_formula(
            G,
            Ihis,
            nodes,
            ["A"],
            ["x", "y", "P", "N"],
            skip_symbolic_w_details=True,
        )
        borrowed_model = {
            "Gred": sp.Matrix([[0]]),
            "Ihisred": sp.Matrix([[sp.Symbol("long_A")]]),
            "W": structured["details"]["W"],
            "Kv": sp.zeros(4, 1),
            "Kh": sp.zeros(4, 1),
        }
        analysis_model = {
            **borrowed_model,
            "Ihisred": sp.Matrix([[sp.Symbol("long_A_tag")]]),
        }

        self.assertEqual(structured["block_type"], "diagonal_plus_coupled")
        self.assertEqual(sp.simplify((sp.Matrix(structured["blocks"]["G_ri"]) * sp.Matrix(structured["details"]["W"]) * sp.Matrix(structured["blocks"]["Ihis_i"]))[0, 0]), 0)
        plan = build_dependency_stage_plan(
            structured,
            {"long_A_tag": "STEP_HISTORY"},
            dependency_model_override=borrowed_model,
            analysis_model_override=analysis_model,
        )
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);", draft)
        self.assertIn("InjA = get_CODE(&Ihisred_code, 0, 0);", draft)
        self.assertNotIn("InjA = long_A;", draft)

    def test_optimized_api_borrows_tagged_reduced_dependency_for_structured_draft(self):
        payload = {
            "all_nodes": ["A", "X", "Y", "B"],
            "external_nodes": ["A", "B"],
            "internal_nodes": ["X", "Y"],
            "ground_nodes": [],
            "G_full": [
                ["G1", "-G1", "0", "0"],
                ["-G1", "G1 + G2 + G5", "-G5", "-G2"],
                ["0", "-G5", "G3 + G4 + G5", "-G4"],
                ["0", "-G2", "-G4", "G2 + G4"],
            ],
            "Ihis_full": ["0", "0", "0", "0"],
            "G_full_tagged": [
                ["G1_tag", "-G1_tag", "0", "0"],
                ["-G1_tag", "G1_tag + G2_tag + G5_tag", "-G5_tag", "-G2_tag"],
                ["0", "-G5_tag", "G3_tag + G4_tag + G5_tag", "-G4_tag"],
                ["0", "-G2_tag", "-G4_tag", "G2_tag + G4_tag"],
            ],
            "Ihis_full_tagged": ["0", "0", "0", "0"],
            "symbol_dependency_table_tagged": {
                "G1_tag": "CODE_VARIABLE",
                "G2_tag": "CODE_VARIABLE",
                "G3_tag": "CODE_VARIABLE",
                "G4_tag": "CODE_VARIABLE",
                "G5_tag": "CODE_VARIABLE",
            },
        }

        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(payload),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)

        self.assertTrue(response["ok"], response)
        draft = response["structured"]["c_draft"]
        self.assertIn("RTDS-style C draft for structured node elimination", draft)
        self.assertIn("MATRIX_ Gkk_code", draft)
        self.assertIn("MATH_matx_invert(NK, &(Gkk_code.p[0]), NK, &(W_code.p[0]), NK);", draft)
        self.assertNotIn("Sequential runtime C draft", draft)

    def test_optimized_api_uses_supplied_reduced_dependency_without_re_eliminating(self):
        payload = {
            "all_nodes": ["A", "X", "B"],
            "external_nodes": ["A", "B"],
            "internal_nodes": ["X"],
            "ground_nodes": [],
            "G_full": [
                ["G1", "-G1", "0"],
                ["-G1", "G1 + G2", "-G2"],
                ["0", "-G2", "G2"],
            ],
            "Ihis_full": ["0", "0", "0"],
            "G_full_tagged": [
                ["G1_tag", "-G1_tag", "0"],
                ["-G1_tag", "G1_tag + G2_tag", "-G2_tag"],
                ["0", "-G2_tag", "G2_tag"],
            ],
            "Ihis_full_tagged": ["0", "0", "0"],
            "symbol_dependency_table_tagged": {
                "G1_tag": "RAM_CONSTANT",
                "G2_tag": "CODE_VARIABLE",
            },
            "reduced_dependency_analysis": {
                "external_nodes": ["A", "B"],
                "G_red": [["G1*G2/(G1 + G2)", "-G1*G2/(G1 + G2)"], ["-G1*G2/(G1 + G2)", "G1*G2/(G1 + G2)"]],
                "Ihis_red": ["0", "0"],
                "G_red_tagged": [
                    ["G1_tag*G2_tag/(G1_tag + G2_tag)", "-G1_tag*G2_tag/(G1_tag + G2_tag)"],
                    ["-G1_tag*G2_tag/(G1_tag + G2_tag)", "G1_tag*G2_tag/(G1_tag + G2_tag)"],
                ],
                "Ihis_red_tagged": ["0", "0"],
            },
        }

        import optimized_elimination_api as api

        original_eliminate = api.eliminate_internal_nodes
        original_stdin = sys.stdin
        original_stdout = sys.stdout
        try:
            api.eliminate_internal_nodes = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("optimized API should reuse supplied reduced dependency analysis")
            )
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            api.main()
            response = json.loads(sys.stdout.getvalue())
        finally:
            api.eliminate_internal_nodes = original_eliminate
            sys.stdin = original_stdin
            sys.stdout = original_stdout

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["structured"]["dependency_analysis"]["Gred_stage"][0][0], "CODE_UPDATE")
        self.assertIn("RTDS-style C draft for structured node elimination", response["structured"]["c_draft"])

    def test_optimized_api_preserves_diagonal_plus_coupled_w_and_ihis_matrix_paths(self):
        payload = {
            "all_nodes": ["A", "B", "x", "y", "z", "P", "N"],
            "external_nodes": ["A", "B"],
            "internal_nodes": ["x", "y", "z", "P", "N"],
            "ground_nodes": [],
            "G_full": [
                ["gA", "0", "ax", "0", "0", "0", "0"],
                ["0", "gB", "0", "by", "0", "0", "0"],
                ["ax", "0", "dx", "0", "0", "xP", "xN"],
                ["0", "by", "0", "dy", "0", "yP", "yN"],
                ["0", "0", "0", "0", "dz", "zP", "zN"],
                ["0", "0", "xP", "yP", "zP", "PP", "PN"],
                ["0", "0", "xN", "yN", "zN", "PN", "NN"],
            ],
            "Ihis_full": ["hA", "hB", "hx", "hy", "hz", "hP", "hN"],
            "G_full_tagged": [
                ["gA_tag", "0", "ax_tag", "0", "0", "0", "0"],
                ["0", "gB_tag", "0", "by_tag", "0", "0", "0"],
                ["ax_tag", "0", "dx_tag", "0", "0", "xP_tag", "xN_tag"],
                ["0", "by_tag", "0", "dy_tag", "0", "yP_tag", "yN_tag"],
                ["0", "0", "0", "0", "dz_tag", "zP_tag", "zN_tag"],
                ["0", "0", "xP_tag", "yP_tag", "zP_tag", "PP_tag", "PN_tag"],
                ["0", "0", "xN_tag", "yN_tag", "zN_tag", "PN_tag", "NN_tag"],
            ],
            "Ihis_full_tagged": ["hA_tag", "hB_tag", "hx_tag", "hy_tag", "hz_tag", "hP_tag", "hN_tag"],
            "symbol_dependency_table_tagged": {
                **{
                    name: "CODE_VARIABLE"
                    for name in [
                        "gA_tag",
                        "gB_tag",
                        "ax_tag",
                        "by_tag",
                        "dx_tag",
                        "dy_tag",
                        "dz_tag",
                        "xP_tag",
                        "xN_tag",
                        "yP_tag",
                        "yN_tag",
                        "zP_tag",
                        "zN_tag",
                        "PP_tag",
                        "PN_tag",
                        "NN_tag",
                    ]
                },
                **{name: "STEP_HISTORY" for name in ["hA_tag", "hB_tag", "hx_tag", "hy_tag", "hz_tag", "hP_tag", "hN_tag"]},
            },
        }

        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(payload),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["structured"]["block_type"], "diagonal_plus_coupled")
        draft = response["structured"]["c_draft"]
        self.assertIn("Build M = S - U^T * inv_D * U", draft)
        self.assertIn("mat_2x2_sym_inv_code", draft)
        self.assertIn("#include <builtin_MATH.h>", draft)
        self.assertNotIn("MATH_matx_invert(2, &(M.p[0]), 2, &(M_inv.p[0]), 2);", draft)
        self.assertIn("MATRIX_ W_DD", draft)
        self.assertIn("matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);", draft)
        self.assertIn("InjA = get_CODE(&Ihisred_code, 0, 0);", draft)
        self.assertNotIn("for (int row = 0; row < 0; row++)", draft)
        self.assertNotIn("g_mat_over[row][col] = 0.0;", draft)

    def test_optimized_api_strips_retained_retained_direct_stamp_from_schur_core(self):
        payload = {
            "all_nodes": ["P", "X", "N"],
            "external_nodes": ["P", "N"],
            "internal_nodes": ["X"],
            "ground_nodes": [],
            "G_full": [
                ["G1 + gC", "-G1", "-gC"],
                ["-G1", "G1 + G2", "-G2"],
                ["-gC", "-G2", "G2 + gC"],
            ],
            "Ihis_full": ["h", "0", "-h"],
            "G_full_tagged": [
                ["G1_tag + gC_tag", "-G1_tag", "-gC_tag"],
                ["-G1_tag", "G1_tag + G2_tag", "-G2_tag"],
                ["-gC_tag", "-G2_tag", "G2_tag + gC_tag"],
            ],
            "Ihis_full_tagged": ["h_tag", "0", "-h_tag"],
            "symbol_dependency_table_tagged": {
                "G1_tag": "RAM_CONSTANT",
                "G2_tag": "RAM_CONSTANT",
                "gC_tag": "CODE_VARIABLE",
                "h_tag": "STEP_HISTORY",
            },
            "reduced_dependency_analysis": {
                "external_nodes": ["P", "N"],
                "G_red": [
                    ["G1*G2/(G1 + G2) + gC", "-G1*G2/(G1 + G2) - gC"],
                    ["-G1*G2/(G1 + G2) - gC", "G1*G2/(G1 + G2) + gC"],
                ],
                "Ihis_red": ["h", "-h"],
                "G_red_tagged": [
                    ["G1_tag*G2_tag/(G1_tag + G2_tag) + gC_tag", "-G1_tag*G2_tag/(G1_tag + G2_tag) - gC_tag"],
                    ["-G1_tag*G2_tag/(G1_tag + G2_tag) - gC_tag", "G1_tag*G2_tag/(G1_tag + G2_tag) + gC_tag"],
                ],
                "Ihis_red_tagged": ["h_tag", "-h_tag"],
            },
            "direct_retained_stamps": [
                {
                    "id": "direct_PN",
                    "support_nodes": ["P", "N"],
                    "G": [
                        {"row": "P", "col": "P", "expr": "gC", "tagged": "gC_tag"},
                        {"row": "P", "col": "N", "expr": "-gC", "tagged": "-gC_tag"},
                        {"row": "N", "col": "P", "expr": "-gC", "tagged": "-gC_tag"},
                        {"row": "N", "col": "N", "expr": "gC", "tagged": "gC_tag"},
                    ],
                    "Ihis": [
                        {"row": "P", "expr": "h", "tagged": "h_tag"},
                        {"row": "N", "expr": "-h", "tagged": "-h_tag"},
                    ],
                }
            ],
        }

        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(payload),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)

        self.assertTrue(response["ok"], response)
        blocks = response["structured"]["blocks"]
        self.assertEqual(blocks["G_rr"], [["G1", "0"], ["0", "G2"]])
        self.assertEqual(response["structured"]["direct_retained"]["Gred_direct"], [["gC", "-gC"], ["-gC", "gC"]])
        draft = response["structured"]["c_draft"]
        self.assertIn("double gC = 0.0;", draft)
        self.assertIn("get_CODE(&Gred_code, 0, 0) + gC", draft)
        self.assertIn("InjP = h;", draft)
        self.assertIn("InjN = -h;", draft)

    def test_optimized_api_does_not_strip_stamp_touching_internal_node(self):
        payload = {
            "all_nodes": ["P", "X", "N"],
            "external_nodes": ["P", "N"],
            "internal_nodes": ["X"],
            "ground_nodes": [],
            "G_full": [
                ["G1 + gC", "-G1 - gC", "0"],
                ["-G1 - gC", "G1 + G2 + gC", "-G2"],
                ["0", "-G2", "G2"],
            ],
            "Ihis_full": ["0", "0", "0"],
            "G_full_tagged": [
                ["G1_tag + gC_tag", "-G1_tag - gC_tag", "0"],
                ["-G1_tag - gC_tag", "G1_tag + G2_tag + gC_tag", "-G2_tag"],
                ["0", "-G2_tag", "G2_tag"],
            ],
            "Ihis_full_tagged": ["0", "0", "0"],
            "symbol_dependency_table_tagged": {
                "G1_tag": "RAM_CONSTANT",
                "G2_tag": "RAM_CONSTANT",
                "gC_tag": "RAM_CONSTANT",
            },
            "reduced_dependency_analysis": {
                "external_nodes": ["P", "N"],
                "G_red": [["0", "0"], ["0", "0"]],
                "Ihis_red": ["0", "0"],
                "G_red_tagged": [["0", "0"], ["0", "0"]],
                "Ihis_red_tagged": ["0", "0"],
            },
            "direct_retained_stamps": [
                {
                    "id": "unsafe",
                    "support_nodes": ["P", "X"],
                    "G": [
                        {"row": "P", "col": "P", "expr": "gC", "tagged": "gC_tag"},
                        {"row": "P", "col": "X", "expr": "-gC", "tagged": "-gC_tag"},
                        {"row": "X", "col": "P", "expr": "-gC", "tagged": "-gC_tag"},
                        {"row": "X", "col": "X", "expr": "gC", "tagged": "gC_tag"},
                    ],
                }
            ],
        }

        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(payload),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)

        self.assertTrue(response["ok"], response)
        blocks = response["structured"]["blocks"]
        self.assertIn("gC", blocks["G_ri"][0][0])
        self.assertIn("gC", blocks["G_ii"][0][0])
        self.assertEqual(response["structured"]["direct_retained"]["Gred_direct"], [["0", "0"], ["0", "0"]])

    def test_direct_ram_constant_stamps_in_ram_even_when_schur_entry_is_dynamic(self):
        payload = {
            "all_nodes": ["N1", "N2", "N4", "N6"],
            "external_nodes": ["N1", "N4", "N6"],
            "internal_nodes": ["N2"],
            "ground_nodes": [],
            "G_full": [
                ["2*A", "-A", "0", "-A"],
                ["-A", "A + G", "-G", "0"],
                ["0", "-G", "A + G", "-A"],
                ["-A", "0", "-A", "2*A"],
            ],
            "Ihis_full": ["0", "0", "0", "0"],
            "G_full_tagged": [
                ["2*A_tag", "-A_tag", "0", "-A_tag"],
                ["-A_tag", "A_tag + G_tag", "-G_tag", "0"],
                ["0", "-G_tag", "A_tag + G_tag", "-A_tag"],
                ["-A_tag", "0", "-A_tag", "2*A_tag"],
            ],
            "Ihis_full_tagged": ["0", "0", "0", "0"],
            "symbol_dependency_table": {
                "A": "RAM_CONSTANT",
                "G": "CODE_VARIABLE",
            },
            "symbol_dependency_table_tagged": {
                "A": "RAM_CONSTANT",
                "G": "CODE_VARIABLE",
                "A_tag": "RAM_CONSTANT",
                "G_tag": "CODE_VARIABLE",
            },
            "reduced_dependency_analysis": {
                "external_nodes": ["N1", "N4", "N6"],
                "G_red": [
                    ["2*A - A*A/(A + G)", "-A*G/(A + G)", "-A"],
                    ["-A*G/(A + G)", "A + G - G*G/(A + G)", "-A"],
                    ["-A", "-A", "2*A"],
                ],
                "Ihis_red": ["0", "0", "0"],
                "G_red_tagged": [
                    ["2*A_tag - A_tag*A_tag/(A_tag + G_tag)", "-A_tag*G_tag/(A_tag + G_tag)", "-A_tag"],
                    ["-A_tag*G_tag/(A_tag + G_tag)", "A_tag + G_tag - G_tag*G_tag/(A_tag + G_tag)", "-A_tag"],
                    ["-A_tag", "-A_tag", "2*A_tag"],
                ],
                "Ihis_red_tagged": ["0", "0", "0"],
            },
            "direct_retained_stamps": [
                {
                    "id": "R_N1_N6",
                    "support_nodes": ["N1", "N6"],
                    "G": [
                        {"row": "N1", "col": "N1", "expr": "A", "tagged": "A_tag"},
                        {"row": "N1", "col": "N6", "expr": "-A", "tagged": "-A_tag"},
                        {"row": "N6", "col": "N1", "expr": "-A", "tagged": "-A_tag"},
                        {"row": "N6", "col": "N6", "expr": "A", "tagged": "A_tag"},
                    ],
                    "Ihis": [],
                },
                {
                    "id": "R_N4_N6",
                    "support_nodes": ["N4", "N6"],
                    "G": [
                        {"row": "N4", "col": "N4", "expr": "A", "tagged": "A_tag"},
                        {"row": "N4", "col": "N6", "expr": "-A", "tagged": "-A_tag"},
                        {"row": "N6", "col": "N4", "expr": "-A", "tagged": "-A_tag"},
                        {"row": "N6", "col": "N6", "expr": "A", "tagged": "A_tag"},
                    ],
                    "Ihis": [],
                },
            ],
        }

        completed = subprocess.run(
            [sys.executable, "optimized_elimination_api.py"],
            input=json.dumps(payload),
            cwd=".",
            text=True,
            capture_output=True,
            check=True,
        )
        response = json.loads(completed.stdout)

        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["structured"]["direct_retained"]["Gred_direct"],
            [["A", "0", "-A"], ["0", "A", "-A"], ["-A", "-A", "2*A"]],
        )
        blocks = response["structured"]["blocks"]
        self.assertEqual(blocks["G_rr"], [["A", "0", "0"], ["0", "G", "0"], ["0", "0", "0"]])
        self.assertEqual(blocks["G_ir"], [["-A", "-G", "0"]])
        self.assertEqual(blocks["Ihis_i"], ["0"])
        draft = response["structured"]["c_draft"]
        self.assertIn("g_mat_over", draft)
        self.assertIn("g_mat_over[2][2] = 2.0*A;", draft)
        self.assertIn("g_mat_over[0][2] = -A;", draft)
        self.assertIn("g_mat_over[1][2] = -A;", draft)
        self.assertNotIn("createGValue(\"varG_N1_N6\"", draft)
        self.assertNotIn("createGValue(\"varG_N4_N6\"", draft)
        self.assertNotIn("createGValue(\"varG_N6_N6\"", draft)
        self.assertIn("createGValue(\"varG_N1_N1\"", draft)
        self.assertIn("createGValue(\"varG_N1_N4\"", draft)
        self.assertIn("createGValue(\"varG_N4_N4\"", draft)
        self.assertIn("set_CODE(&Vr_code, 2, 0, N6);", draft)
        self.assertNotIn("set_CODE(&Gkr_code, 0, 2", draft)

    def test_direct_split_draft_does_not_simplify_final_gred_minus_direct(self):
        A, G, D = sp.symbols("A G D")
        nodes = ["N1", "N2", "N4"]
        G_core = sp.Matrix(
            [
                [A, -A, 0],
                [-A, A + G, -G],
                [0, -G, G],
            ]
        )
        structured = build_structured_formula(G_core, sp.zeros(3, 1), nodes, ["N1", "N4"], ["N2"])
        plan = build_dependency_stage_plan(
            structured,
            {"A": "RAM_CONSTANT", "G": "CODE_VARIABLE", "D": "RAM_CONSTANT"},
            dependency_model_override={
                "Gred": sp.Matrix([[A - A * A / (A + G) + D, -A * G / (A + G) - D], [-A * G / (A + G) - D, G - G * G / (A + G) + D]]),
                "Ihisred": sp.zeros(2, 1),
                "W": sp.Matrix([[1 / (A + G)]]),
                "Kv": sp.zeros(1, 2),
                "Kh": sp.zeros(1, 1),
            },
            analysis_model_override={
                "Gred": sp.Matrix([[A - A * A / (A + G) + D, -A * G / (A + G) - D], [-A * G / (A + G) - D, G - G * G / (A + G) + D]]),
                "Ihisred": sp.zeros(2, 1),
                "W": sp.zeros(1, 1),
                "Kv": sp.zeros(1, 2),
                "Kh": sp.zeros(1, 1),
            },
        )
        plan["Gred_direct"] = sp.Matrix([[D, -D], [-D, D]])

        import nodal_tool.optimized_elimination as optimized

        original_simplify = optimized.sp.simplify

        def reject_matrix_simplify(expr, *args, **kwargs):
            if isinstance(expr, sp.MatrixBase):
                raise AssertionError("C draft must not simplify borrowed final Gred matrices")
            return original_simplify(expr, *args, **kwargs)

        try:
            optimized.sp.simplify = reject_matrix_simplify
            draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)
        finally:
            optimized.sp.simplify = original_simplify

        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_Gkr_code, &tmp_Grk_W_code, &Gkr_code);", draft)
        self.assertIn("matrix_subtract_CODE(&Gred_code, &Grr_code, &tmp_Grk_W_Gkr_code);", draft)
        self.assertNotIn("A**2/(A + G)", draft)



if __name__ == "__main__":
    unittest.main()
