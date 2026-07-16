import unittest

import sympy as sp

from nodal_tool.optimized_elimination import (
    build_dependency_stage_plan,
    build_sliced_schur_for_block,
    build_sliced_schur_for_entry,
    build_structured_formula,
    c_draft_for_structured_formula,
    detect_rectangular_dynamic_blocks,
    find_dynamic_entries,
)


class DynamicSubblockSchurTests(unittest.TestCase):
    def test_sliced_schur_block_does_not_require_full_gred(self):
        a = sp.symbols("a0:9")
        b = sp.symbols("b0:3")
        c = sp.symbols("c0:3")
        w = sp.symbols("w")
        Grr = sp.Matrix(3, 3, a)
        Grk = sp.Matrix(3, 1, b)
        W = sp.Matrix([[w]])
        Gkr = sp.Matrix(1, 3, c)

        block = build_sliced_schur_for_block(Grr, Grk, W, Gkr, [0, 1], [0, 1])

        self.assertEqual(block.shape, (2, 2))
        self.assertEqual(block[0, 0], Grr[0, 0] - Grk[0, 0] * w * Gkr[0, 0])
        self.assertEqual(block[1, 1], Grr[1, 1] - Grk[1, 0] * w * Gkr[0, 1])

    def test_dynamic_entries_form_rectangular_block(self):
        block = detect_rectangular_dynamic_blocks([(0, 0), (0, 1), (1, 0), (1, 1)])

        self.assertTrue(block["is_rectangular"])
        self.assertEqual(block["rows"], [0, 1])
        self.assertEqual(block["cols"], [0, 1])
        self.assertEqual(block["entries"], [(0, 0), (0, 1), (1, 0), (1, 1)])

    def test_dynamic_entries_fall_back_to_sparse_updates(self):
        block = detect_rectangular_dynamic_blocks([(0, 0), (2, 1)])

        self.assertFalse(block["is_rectangular"])
        self.assertEqual(block["entries"], [(0, 0), (2, 1)])

    def test_c_draft_uses_sliced_schur_assignments_not_full_gred(self):
        Gc, Gv = sp.symbols("Gc Gv")
        nodes = ["A", "B", "C", "X"]
        G = sp.Matrix(
            [
                [Gc, 0, 0, -Gc],
                [0, Gv, 0, -Gv],
                [0, 0, Gc, 0],
                [-Gc, -Gv, 0, Gc + Gv],
            ]
        )
        structured = build_structured_formula(G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(structured, {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE"})

        entries = find_dynamic_entries(plan["dependency_analysis"]["Gred_stage"])
        self.assertEqual(entries, [(0, 0), (0, 1), (1, 0), (1, 1)])

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("Sliced Schur update for dynamic rectangular Gred block", draft)
        self.assertIn("Gred[A,B] = Grr[A,B] - Grk[A,k] * W * Gkr[k,B]", draft)
        self.assertIn("MATRIX_ Grr_dyn_code", draft)
        self.assertIn("MATRIX_ Gred_dyn_code", draft)
        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_dyn_code, &Grk_dyn_code, &W_code);", draft)
        self.assertIn("matrix_subtract_CODE(&Gred_dyn_code, &Grr_dyn_code, &tmp_Grk_W_Gkr_dyn_code);", draft)
        self.assertIn("varG_A_A = get_CODE(&Gred_dyn_code, 0, 0);", draft)
        self.assertIn("varG_A_B = get_CODE(&Gred_dyn_code, 0, 1);", draft)
        self.assertIn("varG_B_B = get_CODE(&Gred_dyn_code, 1, 1);", draft)
        self.assertNotIn("set_CODE(&Gred_code, 0, 0, get_CODE(&Gred_dyn_code", draft)
        self.assertNotIn("tmp_Grk_W_Gkr_code", draft)
        self.assertNotIn("matrix_subtract_CODE(&Gred_code", draft)
        self.assertNotIn("codeG_", draft)

    def test_sparse_gred_fallback_reuses_complex_scalar_terms(self):
        Gc, Gv = sp.symbols("Gc Gv")
        nodes = ["A", "B", "C", "X"]
        G = sp.Matrix(
            [
                [Gc, 0, 0, -Gc],
                [0, Gv, 0, -Gv],
                [0, 0, Gc, 0],
                [-Gc, -Gv, 0, Gc + Gv],
            ]
        )
        structured = build_structured_formula(G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(structured, {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE"})
        plan["dependency_analysis"]["Gred_stage"] = [
            ["CODE_UPDATE", "RAM_INIT", "RAM_INIT"],
            ["RAM_INIT", "CODE_UPDATE", "RAM_INIT"],
            ["RAM_INIT", "RAM_INIT", "RAM_INIT"],
        ]
        plan["dynamic_subblock"] = detect_rectangular_dynamic_blocks([(0, 0), (1, 1)])

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("Sliced Schur sparse updates for dynamic Gred entries", draft)
        self.assertIn("double Grr_shared_1 = 0.0;", draft)
        self.assertIn("double Grk_A_X = 0.0;", draft)
        self.assertIn("double Gkr_X_A = 0.0;", draft)
        self.assertIn("double W_1_1 = 0.0;", draft)
        self.assertIn("/* Grr_shared_1 represents Grr[A,A], Grr[C,C]: Gc. */", draft)
        self.assertIn("Grr_shared_1 = Gc;", draft)
        self.assertIn("set_CODE(&Grr_code, 0, 0, Grr_shared_1);", draft)
        self.assertIn("W_1_1 = 1.0/(Gc + Gv);", draft)
        self.assertIn("/* varG_A_A represents Gred[A,A]:", draft)
        self.assertIn("varG_A_A = Grr_shared_1 - Grk_A_X*W_1_1*Gkr_X_A;", draft)
        self.assertIn("/* varG_B_B represents Gred[B,B]:", draft)
        self.assertIn("varG_B_B = Grr_B_B - Grk_B_X*W_1_1*Gkr_X_B;", draft)
        self.assertNotIn("codeG_", draft)
        self.assertNotIn("codeG_A_A = Gc*Gv/(Gc + Gv);", draft)
        self.assertNotIn("MATRIX_ Gred_dyn_code", draft)

    def test_sparse_gred_fallback_reuses_opposite_whole_entries_without_codeG_temps(self):
        D, P, Q = sp.symbols("D P Q")
        nodes = ["A", "B", "C", "X"]
        G = sp.Matrix(
            [
                [0, 0, 0, P],
                [0, 0, 0, -P],
                [0, 0, 0, Q],
                [P, -P, Q, D],
            ]
        )
        structured = build_structured_formula(G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(
            structured,
            {"D": "RAM_CONSTANT", "P": "CODE_VARIABLE", "Q": "RAM_CONSTANT"},
        )
        plan["dependency_analysis"]["Gred_stage"] = [
            ["CODE_UPDATE", "RAM_INIT", "CODE_UPDATE"],
            ["RAM_INIT", "RAM_INIT", "CODE_UPDATE"],
            ["RAM_INIT", "RAM_INIT", "RAM_INIT"],
        ]
        plan["dynamic_subblock"] = detect_rectangular_dynamic_blocks([(0, 0), (0, 2), (1, 2)])

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("Sliced Schur sparse updates for dynamic Gred entries", draft)
        self.assertIn("varG_A_C = -Grk_A_X*W_1_1*Gkr_X_C;", draft)
        self.assertIn("varG_B_C = -varG_A_C;", draft)
        self.assertNotIn("double codeG_", draft)
        self.assertNotIn("codeG_", draft)

    def test_c_draft_groups_static_declarations_by_role(self):
        Gc, Gv, h = sp.symbols("Gc Gv h")
        nodes = ["A", "B", "X"]
        G = sp.Matrix(
            [
                [Gc, 0, -Gc],
                [0, Gv, -Gv],
                [-Gc, -Gv, Gc + Gv],
            ]
        )
        Ihis = sp.Matrix([[0], [0], [h]])
        structured = build_structured_formula(G, Ihis, nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(
            structured,
            {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE", "h": "STEP_HISTORY"},
        )

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("/* Runtime matrix objects */", draft)
        self.assertIn("/* Runtime state */", draft)
        self.assertIn("/* User G/CODE symbols */", draft)
        self.assertIn("double Gv = 0.0;", draft)
        self.assertIn("/* User Ihis/history symbols */", draft)
        self.assertIn("double h = 0.0;", draft)
        self.assertIn("/* Block-matrix scalar aliases */", draft)
        self.assertIn("double Grk_A_X = 0.0;", draft)

    def test_block_aliases_reuse_identical_c_expressions(self):
        G_rc = sp.symbols("G_rc")
        nodes = ["A", "B", "C", "x", "y", "z"]
        Grr = sp.diag(G_rc, G_rc, G_rc)
        Grk = sp.Matrix(
            [
                [-G_rc, 0, 0],
                [0, -G_rc, 0],
                [0, 0, -G_rc],
            ]
        )
        Gkr = Grk.T
        Gkk = sp.diag(G_rc, G_rc, G_rc)
        G = Grr.row_join(Grk).col_join(Gkr.row_join(Gkk))
        structured = build_structured_formula(G, sp.zeros(6, 1), nodes, ["A", "B", "C"], ["x", "y", "z"])
        plan = build_dependency_stage_plan(structured, {"G_rc": "CODE_VARIABLE"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertNotIn("Gkr_code", draft)
        self.assertIn("double Grk_shared_1 = 0.0;", draft)
        self.assertNotIn("double Grk_B_y = 0.0;", draft)
        self.assertNotIn("double Grk_C_z = 0.0;", draft)
        self.assertIn("/* Grk_shared_1 represents Grk[A,x], Grk[B,y], Grk[C,z]: -G_rc. */", draft)
        self.assertIn("set_CODE(&Grk_code, 0, 0, Grk_shared_1);", draft)
        self.assertIn("set_CODE(&Grk_code, 1, 1, Grk_shared_1);", draft)
        self.assertIn("set_CODE(&Grk_code, 2, 2, Grk_shared_1);", draft)

    def test_partial_ihisred_rows_use_sliced_updates(self):
        Gc, Gv, h = sp.symbols("Gc Gv h")
        nodes = ["A", "B", "C", "X"]
        G = sp.Matrix(
            [
                [Gc, 0, 0, -Gc],
                [0, Gv, 0, -Gv],
                [0, 0, Gc, 0],
                [-Gc, -Gv, 0, Gc + Gv],
            ]
        )
        Ihis = sp.Matrix([[0], [0], [0], [h]])
        structured = build_structured_formula(G, Ihis, nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(
            structured,
            {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE", "h": "STEP_HISTORY"},
        )

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("Sliced Ihisred updates for CODE-owned retained rows", draft)
        self.assertIn("MATRIX_ Grk_ihis_dyn_code", draft)
        self.assertIn("MATRIX_ Ihisred_dyn_code", draft)
        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_ihis_dyn_code, &Grk_ihis_dyn_code, &W_code);", draft)
        self.assertIn("matrix_subtract_CODE(&Ihisred_dyn_code, &Ihisr_ihis_dyn_code, &tmp_Grk_W_Ihisk_dyn_code);", draft)
        self.assertNotIn("set_CODE(&Ihisred_code, 0, 0, get_CODE(&Ihisred_dyn_code", draft)
        self.assertNotIn("matrix_subtract_CODE(&Ihisred_code", draft)
        self.assertIn("InjA = get_CODE(&Ihisred_dyn_code, 0, 0);", draft)
        self.assertIn("InjB = get_CODE(&Ihisred_dyn_code, 1, 0);", draft)
        self.assertIn("InjC = 0.0;", draft)

    def test_c_draft_all_dynamic_diagonal_gkk_uses_scalar_schur(self):
        Gv = sp.symbols("Gv")
        nodes = ["A", "B", "C", "X"]
        G = sp.Matrix(
            [
                [Gv, 0, 0, -Gv],
                [0, Gv, 0, -Gv],
                [0, 0, Gv, -Gv],
                [-Gv, -Gv, -Gv, 3 * Gv],
            ]
        )
        structured = build_structured_formula(G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(structured, {"Gv": "CODE_VARIABLE"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("Diagonal Gkk scalar CODE path: Gred", draft)
        self.assertIn("MATRIX_ Gred_code", draft)
        self.assertIn("int j;", draft)
        self.assertIn("for (j = i; j < RETAINED_NODES; j++)", draft)
        self.assertNotIn("for (int ", draft)
        self.assertNotIn("matrix_subtract_CODE(&Gred_code", draft)

    def test_ram_and_code_owners_do_not_overlap(self):
        Gc, Gv = sp.symbols("Gc Gv")
        nodes = ["A", "B", "C", "X"]
        G = sp.Matrix(
            [
                [Gc, 0, 0, -Gc],
                [0, Gv, 0, -Gv],
                [0, 0, Gc, 0],
                [-Gc, -Gv, 0, Gc + Gv],
            ]
        )
        structured = build_structured_formula(G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(structured, {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE"})
        Gred = sp.Matrix(plan["Gred"])
        stages = plan["dependency_analysis"]["Gred_stage"]
        ram_entries = {(r, c) for r in range(Gred.rows) for c in range(Gred.cols) if stages[r][c] == "RAM_INIT"}
        code_entries = set(find_dynamic_entries(stages))

        self.assertFalse(ram_entries & code_entries)
        self.assertEqual(build_sliced_schur_for_entry(plan["Grr"], plan["Grk"], plan["W"], plan["Gkr"], 0, 1), Gred[0, 1])

    def test_source_tagged_dependency_keeps_same_symbol_branches_separate(self):
        R, G = sp.symbols("R G")
        Rvar, Rconst = sp.symbols("R__src_variable R__src_constant")
        nodes = ["A", "B", "C", "X"]
        original_G = sp.Matrix(
            [
                [1 / R, 0, 0, -1 / R],
                [0, G, 0, -G],
                [0, 0, 1 / R, 0],
                [-1 / R, -G, 0, 1 / R + G],
            ]
        )
        tagged_G = sp.Matrix(
            [
                [1 / Rvar, 0, 0, -1 / Rvar],
                [0, G, 0, -G],
                [0, 0, 1 / Rconst, 0],
                [-1 / Rvar, -G, 0, 1 / Rvar + G],
            ]
        )
        structured = build_structured_formula(original_G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        tagged_structured = build_structured_formula(tagged_G, sp.zeros(4, 1), nodes, ["A", "B", "C"], ["X"])
        plan = build_dependency_stage_plan(
            structured,
            {"R__src_variable": "CODE_VARIABLE", "R__src_constant": "RAM_CONSTANT", "G": "CODE_VARIABLE"},
            analysis_structured=tagged_structured,
        )

        stage = plan["dependency_analysis"]["Gred_stage"]
        self.assertEqual(stage[2][2], "RAM_INIT")
        self.assertEqual(stage[0][0], "CODE_UPDATE")
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)
        self.assertIn('g_mat_nods[0] = getNodeNum(comp, "C");', draft)
        self.assertIn("g_mat_over[0][0] = 1.0/R;", draft)
        self.assertIn("setupGMatrix(1);", draft)


if __name__ == "__main__":
    unittest.main()
