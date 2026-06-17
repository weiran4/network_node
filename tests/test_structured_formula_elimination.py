import unittest

import sympy as sp

from nodal_tool.optimized_elimination import (
    analyze_internal_block_structure,
    build_dependency_stage_plan,
    build_structured_formula,
    c_draft_for_structured_formula,
)


def assert_matrix_equal(testcase, actual, expected):
    actual = sp.Matrix(actual)
    expected = sp.Matrix(expected)
    testcase.assertEqual(actual.shape, expected.shape)
    for value, target in zip(actual, expected):
        testcase.assertEqual(sp.simplify(value - target), 0)


class StructuredFormulaEliminationTests(unittest.TestCase):
    def test_pure_diagonal_internal_block(self):
        Dx, Dy, Dz = sp.symbols("Dx Dy Dz")
        Gii = sp.diag(Dx, Dy, Dz)

        analysis = analyze_internal_block_structure(Gii, ["x", "y", "z"])
        structured = build_structured_formula(Gii, sp.zeros(3, 1), ["x", "y", "z"], [], ["x", "y", "z"])

        self.assertEqual(analysis["block_type"], "pure_diagonal")
        assert_matrix_equal(self, structured["details"]["W"], sp.diag(1 / Dx, 1 / Dy, 1 / Dz))
        self.assertEqual(len(structured["details"]["rank_update_terms"]), 3)

    def test_diagonal_plus_2x2_coupled_block(self):
        Dx, Dy, Dz, AP, AN, BP, BN, CP, CN, PP, PN, NN = sp.symbols("Dx Dy Dz AP AN BP BN CP CN PP PN NN")
        Gii = sp.Matrix(
            [
                [Dx, 0, 0, AP, AN],
                [0, Dy, 0, BP, BN],
                [0, 0, Dz, CP, CN],
                [AP, BP, CP, PP, PN],
                [AN, BN, CN, PN, NN],
            ]
        )

        analysis = analyze_internal_block_structure(Gii, ["x", "y", "z", "P", "N"])
        structured = build_structured_formula(Gii, sp.zeros(5, 1), ["x", "y", "z", "P", "N"], [], ["x", "y", "z", "P", "N"])
        details = structured["details"]

        self.assertEqual(analysis["block_type"], "diagonal_plus_coupled")
        self.assertEqual(analysis["diagonal_nodes"], ["x", "y", "z"])
        self.assertEqual(analysis["coupled_nodes"], ["P", "N"])

        assert_matrix_equal(self, details["D"], sp.diag(Dx, Dy, Dz))
        assert_matrix_equal(self, details["U"], sp.Matrix([[AP, AN], [BP, BN], [CP, CN]]))
        assert_matrix_equal(self, details["S"], sp.Matrix([[PP, PN], [PN, NN]]))

        M11 = PP - (AP**2 / Dx + BP**2 / Dy + CP**2 / Dz)
        M12 = PN - (AP * AN / Dx + BP * BN / Dy + CP * CN / Dz)
        M22 = NN - (AN**2 / Dx + BN**2 / Dy + CN**2 / Dz)
        assert_matrix_equal(self, details["M"], sp.Matrix([[M11, M12], [M12, M22]]))

        detM = M11 * M22 - M12**2
        assert_matrix_equal(self, details["M_inv"], sp.Matrix([[M22 / detM, -M12 / detM], [-M12 / detM, M11 / detM]]))
        assert_matrix_equal(self, details["W"], Gii.inv())

        draft = c_draft_for_structured_formula(structured)
        self.assertIn("double Grr", draft)
        self.assertIn("double Grk", draft)
        self.assertIn("double Gkr", draft)
        self.assertIn("double Gkk", draft)
        self.assertIn("double Ihisr", draft)
        self.assertIn("double Ihisk", draft)
        self.assertNotIn("matrix_transpose_CODE", draft)
        self.assertIn("U_T[0][0] = U[0][0];", draft)
        self.assertIn("U_T[0][1] = U[1][0];", draft)
        self.assertIn("U_T[1][2] = U[2][1];", draft)
        self.assertIn("matrix_Mul", draft)
        self.assertIn("matrix_Sub", draft)
        self.assertIn("mat_2x2_sym_inv_code", draft)
        self.assertIn("Gred", draft)
        self.assertIn("Ihisred", draft)
        self.assertIn("Vk", draft)
        self.assertIn("double Vx = Vk[0][0];", draft)
        self.assertIn("double Vy = Vk[1][0];", draft)
        self.assertIn("double Vz = Vk[2][0];", draft)
        self.assertIn("double VP = Vk[3][0];", draft)
        self.assertIn("double VN = Vk[4][0];", draft)
        self.assertIn("{0.0}", draft)

    def test_suggests_order_when_user_order_hides_blocks(self):
        Dx, Dy, Dz, AP, AN, BP, BN, CP, CN, PP, PN, NN = sp.symbols("Dx Dy Dz AP AN BP BN CP CN PP PN NN")
        natural = ["x", "y", "z", "P", "N"]
        user_order = ["P", "N", "x", "y", "z"]
        G_natural = sp.Matrix(
            [
                [Dx, 0, 0, AP, AN],
                [0, Dy, 0, BP, BN],
                [0, 0, Dz, CP, CN],
                [AP, BP, CP, PP, PN],
                [AN, BN, CN, PN, NN],
            ]
        )
        permutation = [natural.index(node) for node in user_order]
        G_user = G_natural.extract(permutation, permutation)

        analysis = analyze_internal_block_structure(G_user, user_order)

        self.assertEqual(analysis["suggested_order"], ["x", "y", "z", "P", "N"])
        self.assertIn("Suggested block order differs from user order.", analysis["warnings"])

    def test_non_symmetric_warning(self):
        Gii = sp.Matrix([[1, 2], [3, 4]])

        analysis = analyze_internal_block_structure(Gii, ["x", "y"])

        self.assertFalse(analysis["is_symmetric"])
        self.assertIn("Matrix is not symmetric; symmetric block inverse display may be invalid.", analysis["warnings"])

    def test_general_dense_block(self):
        a, b, c, d, e, f = sp.symbols("a b c d e f")
        Gii = sp.Matrix([[a, b, c], [b, d, e], [c, e, f]])

        analysis = analyze_internal_block_structure(Gii, ["x", "y", "z"])

        self.assertEqual(analysis["block_type"], "general")
        self.assertEqual(analysis["diagonal_nodes"], [])
        self.assertEqual(analysis["coupled_nodes"], ["x", "y", "z"])

    def test_c_draft_uses_1x1_m_reciprocal(self):
        Dx, Dy, AP, BP, PP = sp.symbols("Dx Dy AP BP PP")
        Gii = sp.Matrix(
            [
                [Dx, 0, AP],
                [0, Dy, BP],
                [AP, BP, PP],
            ]
        )
        structured = build_structured_formula(Gii, sp.zeros(3, 1), ["x", "y", "P"], [], ["x", "y", "P"])

        draft = c_draft_for_structured_formula(structured)

        self.assertIn("M_inv[0][0] = 1.0 / M[0][0];", draft)

    def test_c_draft_uses_display_names_for_voltage_recovery(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["gA", "gB", "gX"]
        G = sp.Matrix([[G1, 0, -G1], [0, G2, -G2], [-G1, -G2, G1 + G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["gA", "gB"], ["gX"])

        draft = c_draft_for_structured_formula(
            structured,
            node_display_names={"gA": "A", "gB": "B", "gX": "x"},
        )

        self.assertIn("double Vr[2][1] = {\n    {A},\n    {B}\n};", draft)
        self.assertIn("double Vk[1][1] = {\n    {x}\n};", draft)
        self.assertIn("double Vx = Vk[0][0];", draft)
        self.assertNotIn("VgX", draft)

    def test_c_draft_uses_3x3_symmetric_inverse(self):
        D1, D2 = sp.symbols("D1 D2")
        symbols = sp.symbols("u11 u12 u13 u21 u22 u23 s11 s12 s13 s22 s23 s33")
        u11, u12, u13, u21, u22, u23, s11, s12, s13, s22, s23, s33 = symbols
        Gii = sp.Matrix(
            [
                [D1, 0, u11, u12, u13],
                [0, D2, u21, u22, u23],
                [u11, u21, s11, s12, s13],
                [u12, u22, s12, s22, s23],
                [u13, u23, s13, s23, s33],
            ]
        )
        structured = build_structured_formula(Gii, sp.zeros(5, 1), ["d1", "d2", "a", "b", "c"], [], ["d1", "d2", "a", "b", "c"])

        draft = c_draft_for_structured_formula(structured)

        self.assertIn("mat_3x3_sym_inv_code", draft)

    def test_c_draft_warns_for_large_m_inverse(self):
        diagonal = sp.diag(*sp.symbols("D1 D2"))
        U = sp.Matrix(sp.symbols("u0:8")).reshape(2, 4)
        S_symbols = sp.symbols("s0:10")
        S = sp.Matrix(
            [
                [S_symbols[0], S_symbols[1], S_symbols[2], S_symbols[3]],
                [S_symbols[1], S_symbols[4], S_symbols[5], S_symbols[6]],
                [S_symbols[2], S_symbols[5], S_symbols[7], S_symbols[8]],
                [S_symbols[3], S_symbols[6], S_symbols[8], S_symbols[9]],
            ]
        )
        Gii = sp.Matrix.vstack(sp.Matrix.hstack(diagonal, U), sp.Matrix.hstack(U.T, S))
        nodes = ["d1", "d2", "a", "b", "c", "e"]
        structured = build_structured_formula(Gii, sp.zeros(6, 1), nodes, [], nodes)

        draft = c_draft_for_structured_formula(structured)

        self.assertIn("WARNING: M is 4x4", draft)
        self.assertIn("MATH_matx_invert(4", draft)

    def test_dependency_stage_plan_keeps_constant_schur_in_ram(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "B", "X"]
        G = sp.Matrix([[G1, 0, -G1], [0, G2, -G2], [-G1, -G2, G1 + G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "RAM_CONSTANT", "G2": "RAM_CONSTANT"})

        self.assertEqual(plan["dependency_analysis"]["Gred_stage"], [["RAM_INIT", "RAM_INIT"], ["RAM_INIT", "RAM_INIT"]])
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)
        self.assertIn("RAM_PASS1 stamps only RAM_CONSTANT Gred", draft)
        self.assertIn("g_mat_over[row][col] = 0.0;", draft)
        self.assertNotIn("Grr_code", draft)
        self.assertNotIn("Grk_code", draft)
        self.assertIn("conditionMatrixForCODE(&Gkr_code);", draft)
        self.assertIn("conditionMatrixForCODE(&W_code);", draft)
        self.assertIn("g_mat_over[0][0] = ramG_A_A;", draft)
        self.assertNotIn("Ihisred_code", draft)
        self.assertNotIn("matrix_subtract_CODE(&Ihisred_code", draft)
        self.assertIn("InjA = 0.0;", draft)
        self.assertIn("InjB = 0.0;", draft)
        self.assertIn("T1_T2:", draft)
        self.assertIn("X = get_CODE(&Vk_code, 0, 0);", draft)

    def test_external_ihis_direct_injection_prunes_unused_blocks(self):
        G1, G2, h = sp.symbols("G1 G2 h")
        nodes = ["A", "B", "X"]
        G = sp.Matrix([[G1, 0, -G1], [0, G2, -G2], [-G1, -G2, G1 + G2]])
        Ihis = sp.Matrix([[h], [-h], [0]])
        structured = build_structured_formula(G, Ihis, nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "RAM_CONSTANT", "G2": "RAM_CONSTANT", "h": "STEP_HISTORY"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertNotIn("Gred_code", draft)
        self.assertNotIn("Grr_code", draft)
        self.assertNotIn("Grk_code", draft)
        self.assertNotIn("Ihisr_code", draft)
        self.assertNotIn("Ihisk_code", draft)
        self.assertNotIn("Ihisred_code", draft)
        self.assertIn("InjA = h;", draft)
        self.assertIn("InjB = -h;", draft)
        self.assertIn("T1_T2:", draft)
        self.assertIn("conditionMatrixForCODE(&Gkr_code);", draft)
        self.assertIn("conditionMatrixForCODE(&W_code);", draft)
        self.assertNotIn("tmp_W_Ihisk_code", draft)
        self.assertNotIn("set_CODE(&Ihisk_code", draft)

    def test_ram_g_stamp_reuses_complex_scalar_terms(self):
        G1, G2 = sp.symbols("G1 G2")
        nodes = ["A", "B", "X"]
        G = sp.Matrix([[G1, 0, -G1], [0, G2, -G2], [-G1, -G2, G1 + G2]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "RAM_CONSTANT", "G2": "RAM_CONSTANT"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn("double ramG_A_A = 0.0;", draft)
        self.assertIn("/* ramG_A_A represents Gred[A,A]:", draft)
        self.assertIn("ramG_A_A =", draft)
        self.assertIn("g_mat_over[0][0] = ramG_A_A;", draft)
        self.assertIn("g_mat_over[0][1] = -ramG_A_A;", draft)

    def test_simple_ram_g_stamp_stays_direct_without_temp(self):
        R, G, h = sp.symbols("R G h")
        nodes = ["N1", "N2", "N4", "N6"]
        full_G = sp.Matrix(
            [
                [1 / R, -1 / R, 0, 0],
                [-1 / R, 1 / R + G, -G, 0],
                [0, -G, G + 1 / R, -1 / R],
                [0, 0, -1 / R, 1 / R],
            ]
        )
        structured = build_structured_formula(full_G, sp.Matrix([[0], [h], [-h], [0]]), nodes, nodes, [])
        plan = build_dependency_stage_plan(structured, {"R": "RAM_CONSTANT", "G": "CODE_VARIABLE", "h": "STEP_HISTORY"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertNotIn("ramG_", draft)
        self.assertIn("g_mat_over[0][0] = 1.0/R;", draft)

    def test_dependency_stage_plan_moves_variable_schur_to_code_update(self):
        Gc, Gv = sp.symbols("Gc Gv")
        nodes = ["A", "B", "X"]
        G = sp.Matrix([[Gc, 0, -Gc], [0, Gv, -Gv], [-Gc, -Gv, Gc + Gv]])
        structured = build_structured_formula(G, sp.zeros(3, 1), nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"Gc": "RAM_CONSTANT", "Gv": "CODE_VARIABLE"})

        self.assertEqual(plan["dependency_analysis"]["Gred_stage"], [["CODE_UPDATE", "CODE_UPDATE"], ["CODE_UPDATE", "CODE_UPDATE"]])
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)
        self.assertIn("Grk_A_k1 = -Gc;", draft)
        self.assertIn("set_CODE(&Grk_code, 0, 0, Grk_A_k1);", draft)
        self.assertIn("GVALUES:", draft)
        self.assertIn('double varG_A_A = createGValue("varG_A_A", "A", "A", 0, "TRUE");', draft)
        self.assertIn('double varG_A_B = createGValue("varG_A_B", "A", "B", 0, "TRUE");', draft)
        self.assertIn('double varG_B_B = createGValue("varG_B_B", "B", "B", 0, "TRUE");', draft)
        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);", draft)
        self.assertIn("varG_A_A = get_CODE(&Gred_code, 0, 0);", draft)
        self.assertIn("varG_A_B = get_CODE(&Gred_code, 0, 1);", draft)
        self.assertIn("varG_B_B = get_CODE(&Gred_code, 1, 1);", draft)
        self.assertNotIn("set_CODE(&Gred_code, 0, 0, Gc*Gv/(Gc + Gv));", draft)
        self.assertIn("double Gc = 0.0;", draft)

    def test_dependency_stage_plan_places_history_source_per_step(self):
        G1, G2, h = sp.symbols("G1 G2 h")
        nodes = ["A", "B", "X"]
        G = sp.Matrix([[G1, 0, -G1], [0, G2, -G2], [-G1, -G2, G1 + G2]])
        Ihis = sp.Matrix([[0], [0], [h]])
        structured = build_structured_formula(G, Ihis, nodes, ["A", "B"], ["X"])
        plan = build_dependency_stage_plan(structured, {"G1": "RAM_CONSTANT", "G2": "RAM_CONSTANT", "h": "STEP_HISTORY"})

        self.assertEqual(plan["dependency_analysis"]["Ihisred_stage"], ["CODE_PER_STEP", "CODE_PER_STEP"])
        self.assertEqual(plan["dependency_analysis"]["Kh_stage"], ["CODE_PER_STEP"])
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)
        self.assertIn("set_CODE(&Ihisk_code, 0, 0, h);", draft)
        self.assertIn("matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code", draft)
        self.assertIn("matrix_subtract_CODE(&Ihisred_code", draft)

    def test_c_draft_without_internal_nodes_uses_original_g_and_ihis(self):
        G1, G2, hA, hB = sp.symbols("G1 G2 hA hB")
        nodes = ["A", "B"]
        G = sp.Matrix([[G1 + G2, -G2], [-G2, G2]])
        Ihis = sp.Matrix([[hA], [hB]])
        structured = build_structured_formula(G, Ihis, nodes, ["A", "B"], [])
        plan = build_dependency_stage_plan(
            structured,
            {"G1": "RAM_CONSTANT", "G2": "CODE_VARIABLE", "hA": "STEP_HISTORY", "hB": "STEP_HISTORY"},
        )

        self.assertEqual(structured["block_type"], "no_elimination")
        self.assertEqual(plan["dependency_analysis"]["Gred_stage"], [["CODE_UPDATE", "CODE_UPDATE"], ["CODE_UPDATE", "CODE_UPDATE"]])
        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)
        self.assertIn("No Schur complement is required: use the original G matrix and Ihis vector directly", draft)
        self.assertIn("GVALUES:", draft)
        self.assertIn('double varG_A_A = createGValue("varG_A_A", "A", "A", 0, "TRUE");', draft)
        self.assertIn("LOCAL_STATIC:", draft)
        self.assertIn("double G1 = 0.0;", draft)
        self.assertIn("g_mat_over[0][0] = G1;", draft)
        self.assertNotIn("g_mat_over[0][1] = 0.0;", draft)
        self.assertNotIn("g_mat_over[1][0] = 0.0;", draft)
        self.assertIn("set_CODE(&G_code, 0, 0, G2);", draft)
        self.assertIn("varG_A_B = get_CODE(&G_code, 0, 1);", draft)
        self.assertIn("CODE-SIDE IHIS VALUE SETUP", draft)
        self.assertIn("InjA = hA;", draft)
        self.assertIn("No internal nodes were eliminated", draft)
        self.assertNotIn("Gred_code", draft)
        self.assertNotIn("Ihisred_code", draft)
        self.assertNotIn("W_code", draft)
        self.assertNotIn("Vk_code", draft)

    def test_no_internal_nodes_split_constant_and_dynamic_g_terms(self):
        R, G, h = sp.symbols("R G h")
        nodes = ["N1", "N2", "N4", "N6"]
        full_G = sp.Matrix(
            [
                [1 / R, -1 / R, 0, 0],
                [-1 / R, 1 / R + G, -G, 0],
                [0, -G, G + 1 / R, -1 / R],
                [0, 0, -1 / R, 1 / R],
            ]
        )
        structured = build_structured_formula(
            full_G,
            sp.Matrix([[0], [h], [-h], [0]]),
            nodes,
            nodes,
            [],
        )
        plan = build_dependency_stage_plan(
            structured,
            {"R": "RAM_CONSTANT", "G": "CODE_VARIABLE", "h": "STEP_HISTORY"},
        )

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn('g_mat_nods[0] = getNodeNum(comp, "N1");', draft)
        self.assertIn('g_mat_nods[1] = getNodeNum(comp, "N2");', draft)
        self.assertIn('g_mat_nods[2] = getNodeNum(comp, "N4");', draft)
        self.assertIn('g_mat_nods[3] = getNodeNum(comp, "N6");', draft)
        self.assertIn("setupGMatrix(4);", draft)
        self.assertIn("g_mat_over[0][0] = 1.0/R;", draft)
        self.assertIn("g_mat_over[0][1] = -1.0/R;", draft)
        self.assertIn("g_mat_over[1][1] = 1.0/R;", draft)
        self.assertIn("g_mat_over[2][2] = 1.0/R;", draft)
        self.assertIn("g_mat_over[2][3] = -1.0/R;", draft)
        self.assertNotIn("g_mat_over[0][2] = 0.0;", draft)
        self.assertNotIn("g_mat_over[3][0] = 0.0;", draft)
        self.assertNotIn("g_mat_over[1][1] = G + 1.0/R;", draft)
        self.assertIn("set_CODE(&G_code, 1, 1, G);", draft)
        self.assertIn("set_CODE(&G_code, 1, 2, -G);", draft)
        self.assertIn("set_CODE(&G_code, 2, 1, -G);", draft)
        self.assertIn("set_CODE(&G_code, 2, 2, G);", draft)
        self.assertNotIn("set_CODE(&G_code, 0, 0", draft)
        self.assertIn('double varG_N2_N2 = createGValue("varG_N2_N2", "N2", "N2", 0, "TRUE");', draft)
        self.assertIn('double varG_N2_N4 = createGValue("varG_N2_N4", "N2", "N4", 0, "TRUE");', draft)
        self.assertIn('double varG_N4_N4 = createGValue("varG_N4_N4", "N4", "N4", 0, "TRUE");', draft)

    def test_ram_g_overlay_uses_compact_node_subset(self):
        gC = sp.symbols("gC")
        nodes = ["A", "B", "C", "G", "RC_a", "RC_b", "RC_c", "P", "N", "O"]
        G = sp.zeros(len(nodes), len(nodes))
        idx = {node: index for index, node in enumerate(nodes)}
        for a, b, value in [
            ("P", "O", -gC),
            ("N", "O", -gC),
            ("O", "P", -gC),
            ("O", "N", -gC),
            ("O", "O", 2 * gC),
        ]:
            G[idx[a], idx[b]] = value
        structured = build_structured_formula(G, sp.zeros(len(nodes), 1), nodes, nodes, [])
        plan = build_dependency_stage_plan(structured, {"gC": "RAM_CONSTANT"})

        draft = c_draft_for_structured_formula(structured, rtds_stage_plan=plan)

        self.assertIn('g_mat_nods[0] = getNodeNum(comp, "P");', draft)
        self.assertIn('g_mat_nods[1] = getNodeNum(comp, "N");', draft)
        self.assertIn('g_mat_nods[2] = getNodeNum(comp, "O");', draft)
        self.assertNotIn('getNodeNum(comp, "A")', draft)
        self.assertNotIn('getNodeNum(comp, "RC_a")', draft)
        self.assertIn("g_mat_over[0][2] = -gC;", draft)
        self.assertIn("g_mat_over[1][2] = -gC;", draft)
        self.assertIn("g_mat_over[2][0] = -gC;", draft)
        self.assertIn("g_mat_over[2][1] = -gC;", draft)
        self.assertIn("g_mat_over[2][2] = 2.0*gC;", draft)
        self.assertNotIn("g_mat_over[7][9]", draft)
        self.assertIn("setupGMatrix(3);", draft)


if __name__ == "__main__":
    unittest.main()
