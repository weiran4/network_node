import unittest
import sympy as sp

from optimized_elimination_api import build_multi_case_response, _apply_conditional_final_gvalues_to_structured_draft
from nodal_tool.optimized_elimination import GredEntryReuse
from tests.test_multicase_c_export_alias_template import _deps, _request, _series_payload


class MultiCaseConditionalGValueTests(unittest.TestCase):
    def test_structural_varG_reuse_is_kept_only_inside_matching_case_condition(self):
        G = sp.Symbol("G")
        profiles = [
            {"name": "case 0", "payload": {"symbol_dependency_table": {"G": "CODE_VARIABLE"}}},
            {"name": "case 1", "payload": {"symbol_dependency_table": {"G": "CODE_VARIABLE"}}},
        ]
        draft = "\n".join(
            [
                '    double varG_A_A = createGValue("varG_A_A", "A", "A", 0, "TRUE");',
                '    double varG_A_B = createGValue("varG_A_B", "A", "B", 0, "TRUE");',
                '    double varG_B_B = createGValue("varG_B_B", "B", "B", 0, "TRUE");',
                "    /* No RAM-side G entries: no fixed G overlay is registered. */",
                "    varG_A_A = get_CODE(&Gred_code, 0, 0);",
                "    varG_A_B = get_CODE(&Gred_code, 0, 1);",
                "    varG_B_B = get_CODE(&Gred_code, 1, 1);",
            ]
        )

        rewritten, conditions = _apply_conditional_final_gvalues_to_structured_draft(
            draft,
            case_id_symbol="case_id",
            profiles=profiles,
            aliases={},
            template_gred=sp.Matrix([[G, -G], [-G, G]]),
            external_nodes=["A", "B"],
            reuse_plan=[
                GredEntryReuse(target_row=0, target_col=1, base_row=0, base_col=0, sign=-1),
                GredEntryReuse(target_row=1, target_col=1, base_row=0, base_col=0, sign=1),
            ],
        )

        self.assertEqual(
            {item["var"]: item["condition"] for item in conditions},
            {
                "varG_A_A": "case_id == 0 || case_id == 1",
                "varG_A_B": "case_id == 0 || case_id == 1",
                "varG_B_B": "case_id == 0 || case_id == 1",
            },
        )
        self.assertIn("varG_A_A = get_CODE(&Gred_code, 0, 0);", rewritten)
        self.assertIn("varG_A_B = -varG_A_A;", rewritten)
        self.assertIn("varG_B_B = varG_A_A;", rewritten)
        self.assertNotIn("varG_A_B = get_CODE(&Gred_code, 0, 1);", rewritten)
        self.assertNotIn("varG_B_B = get_CODE(&Gred_code, 1, 1);", rewritten)

    def test_structural_varG_reuse_can_differ_by_case_combination(self):
        G = sp.Symbol("G")
        profiles = [
            {"name": "case 0", "payload": {"symbol_dependency_table": {"G": "CODE_VARIABLE"}}},
            {"name": "case 1", "payload": {"symbol_dependency_table": {"G": "CODE_VARIABLE"}}},
        ]
        draft = "\n".join(
            [
                '    double varG_A_A = createGValue("varG_A_A", "A", "A", 0, "TRUE");',
                '    double varG_A_B = createGValue("varG_A_B", "A", "B", 0, "TRUE");',
                "    /* No RAM-side G entries: no fixed G overlay is registered. */",
                "    varG_A_A = get_CODE(&Gred_code, 0, 0);",
                "    varG_A_B = get_CODE(&Gred_code, 0, 1);",
            ]
        )

        rewritten, _ = _apply_conditional_final_gvalues_to_structured_draft(
            draft,
            case_id_symbol="case_id",
            profiles=profiles,
            aliases={},
            template_gred=sp.Matrix([[G, -G], [-G, G]]),
            external_nodes=["A", "B"],
            reuse_plan_by_case={
                0: [GredEntryReuse(target_row=0, target_col=1, base_row=0, base_col=0, sign=-1)],
                1: [],
            },
        )

        self.assertIn(
            "switch (case_id) {\n"
            "    case 0:\n"
            "        varG_A_A = get_CODE(&Gred_code, 0, 0);\n"
            "        varG_A_B = -varG_A_A;\n"
            "        break;\n"
            "    case 1:\n"
            "        varG_A_A = get_CODE(&Gred_code, 0, 0);\n"
            "        varG_A_B = get_CODE(&Gred_code, 0, 1);",
            rewritten,
        )

    def test_direct_final_entry_uses_case_conditional_gvalue_for_mixed_ram_code_cases(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "case 0", "case_map": {"R1": 0}, "payload": _series_payload("G_const", internal=False)},
                    {"name": "case 1", "case_map": {"R1": 1}, "payload": _series_payload("G_dynamic", internal=False)},
                ],
                deps=_deps("G_const", code=("G_dynamic",)),
                case_id="case_id",
            )
        )

        draft = response["multi_case"]["c_draft"]
        warnings = "\n".join(response["warnings"])
        self.assertIn("case_id == 1", draft)
        self.assertIn('createGValue("varG_A_B"', draft)
        self.assertIn("case 0:", draft)
        self.assertIn("g_mat_over[0][1] = -G_const;", draft)
        self.assertIn("g_mat_over[1][0] = -G_const;", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("/* CODE-owned case: no RAM stamp for varG_A_B. */", draft)
        self.assertIn("multcase_G_R1_A_A = G_const;", draft)
        self.assertIn("multcase_G_R1_A_A = G_dynamic;", draft)
        self.assertIn(
            "switch (case_id) {\n"
            "    case 1:\n"
            "        varG_A_A = multcase_G_R1_A_A;\n"
            "        varG_A_B = -multcase_G_R1_A_A;\n"
            "        varG_B_B = multcase_G_R1_A_A;",
            draft,
        )
        self.assertNotIn("set_CODE(&G_code, 0, 0, multcase_G_R1_A_A);", draft)
        self.assertNotIn("set_CODE(&Gred_code", draft)
        self.assertNotIn("G_dynamic - G_const", draft)
        self.assertNotIn("varG_A_B = 0.0", draft)
        self.assertIn("case_id is fixed before simulation", warnings)
        self.assertIn("must not change at runtime", warnings)

    def test_multiple_code_cases_share_one_gvalue_condition_without_ram_case_assignment(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "case 0", "case_map": {"R1": 0}, "payload": _series_payload("G_const", internal=False)},
                    {"name": "case 1", "case_map": {"R1": 1}, "payload": _series_payload("G_dynamic_1", internal=False)},
                    {"name": "case 2", "case_map": {"R1": 2}, "payload": _series_payload("G_dynamic_2", internal=False)},
                    {"name": "case 3", "case_map": {"R1": 3}, "payload": _series_payload("G_const_3", internal=False)},
                ],
                deps=_deps("G_const", "G_const_3", code=("G_dynamic_1", "G_dynamic_2")),
                case_id="case_id",
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn('createGValue("varG_A_B", "A", "B", 0, "case_id == 1 || case_id == 2")', draft)
        self.assertIn("g_mat_over[0][1] = -G_const;", draft)
        self.assertIn("g_mat_over[0][1] = -G_const_3;", draft)
        self.assertIn("multcase_G_R1_A_A = G_const;", draft)
        self.assertIn("multcase_G_R1_A_A = G_dynamic_1;", draft)
        self.assertIn("multcase_G_R1_A_A = G_dynamic_2;", draft)
        self.assertIn("multcase_G_R1_A_A = G_const_3;", draft)
        self.assertIn(
            "switch (case_id) {\n"
            "    case 1:\n"
            "        varG_A_A = multcase_G_R1_A_A;\n"
            "        varG_A_B = -multcase_G_R1_A_A;\n"
            "        varG_B_B = multcase_G_R1_A_A;\n"
            "        break;\n"
            "    case 2:\n"
            "        varG_A_A = multcase_G_R1_A_A;\n"
            "        varG_A_B = -multcase_G_R1_A_A;\n"
            "        varG_B_B = multcase_G_R1_A_A;",
            draft,
        )
        self.assertNotIn("set_CODE(&G_code, 0, 0, multcase_G_R1_A_A);", draft)
        self.assertNotIn("set_CODE(&Gred_code", draft)
        self.assertNotIn(
            "switch (case_id) {\n"
            "    case 1:\n"
            "    case 2:\n"
            "        varG_A_A = get_CODE(&G_code, 0, 0);\n"
            "        varG_A_B = get_CODE(&G_code, 0, 1);\n"
            "        varG_B_B = get_CODE(&G_code, 1, 1);",
            draft,
        )
        self.assertNotIn("varG_A_B = 0.0", draft)
        self.assertNotIn("G_dynamic_1 - G_const", draft)
        self.assertNotIn("G_dynamic_2 - G_const", draft)

    def test_same_symbol_can_be_ram_in_one_case_and_code_in_another(self):
        profiles = [
            {"name": "case 0", "case_map": {"R1": 0}, "payload": _series_payload("G_mode", internal=False)},
            {"name": "case 1", "case_map": {"R1": 1}, "payload": _series_payload("G_mode", internal=False)},
        ]
        profiles[0]["payload"]["symbol_dependency_table"] = _deps("G_mode")
        profiles[0]["payload"]["symbol_dependency_table_tagged"] = _deps("G_mode")
        profiles[1]["payload"]["symbol_dependency_table"] = _deps(code=("G_mode",))
        profiles[1]["payload"]["symbol_dependency_table_tagged"] = _deps(code=("G_mode",))

        response = build_multi_case_response(
            {
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": profiles,
            }
        )

        draft = response["multi_case"]["c_draft"]
        warnings = "\n".join(response["warnings"])
        self.assertIn('createGValue("varG_A_B", "A", "B", 0, "case_id == 1")', draft)
        self.assertIn("case 0:", draft)
        self.assertIn("g_mat_over[0][1] = -G_mode;", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("/* CODE-owned case: no RAM stamp for varG_A_B. */", draft)
        self.assertIn("varG_A_B = -G_mode;", draft)
        self.assertNotIn("G_mode - G_mode", draft)
        self.assertNotIn("varG_A_B = 0.0", draft)
        self.assertIn("mixed RAM/CODE ownership", warnings)

    def test_internal_schur_cases_use_full_alias_values_and_match_single_case_results(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "case 0", "case_map": {"R1": 0}, "payload": _series_payload("G_const", "G2", internal=True)},
                    {"name": "case 1", "case_map": {"R1": 1}, "payload": _series_payload("G_dynamic", "G2", internal=True)},
                ],
                deps=_deps("G_const", "G2", code=("G_dynamic",)),
                case_id="case_id",
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn("multcase_G_R1_A_A = G_const;", draft)
        self.assertIn("multcase_G_R1_A_A = G_dynamic;", draft)
        self.assertNotIn("G_dynamic - G_const", draft)
        self.assertIn('createGValue("varG_A_A", "A", "A", 0, "case_id == 1")', draft)
        self.assertIn('createGValue("varG_A_B", "A", "B", 0, "case_id == 1")', draft)
        self.assertIn('createGValue("varG_B_B", "B", "B", 0, "case_id == 1")', draft)
        self.assertNotIn('createGValue("varG_A_A", "A", "A", 0, "TRUE")', draft)
        self.assertIn("T1_T2:", draft)
        self.assertIn("Internal-node voltage recovery after solved retained-node voltages are available", draft)
        self.assertIn("Diagonal Gkk scalar CODE path: Vk[k] = -(transpose(Grk)[k,*] * Vr + Ihisk[k]) / Gkk[k,k].", draft)
        self.assertIn("vk_sum += get_CODE(&Grk_code, j, k) * get_CODE(&Vr_code, j, 0);", draft)
        self.assertNotIn("matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code", draft)
        self.assertNotIn("matrix_scalarMult_CODE(&Vk_code", draft)
        self.assertIn("X = get_CODE(&Vk_code, 0, 0);", draft)
        self.assertNotIn("This conditional GValue export assumes no runtime case switching", draft)

        alias = sp.Symbol("multcase_G_R1_A_A")
        template_gred = sp.Matrix(response["multi_case"]["template"]["Gred"])
        for concrete in [sp.Symbol("G_const"), sp.Symbol("G_dynamic")]:
            substituted = template_gred.xreplace({alias: concrete})
            expected = sp.Matrix([[concrete * sp.Symbol("G2") / (concrete + sp.Symbol("G2")),
                                   -concrete * sp.Symbol("G2") / (concrete + sp.Symbol("G2"))],
                                  [-concrete * sp.Symbol("G2") / (concrete + sp.Symbol("G2")),
                                   concrete * sp.Symbol("G2") / (concrete + sp.Symbol("G2"))]])
            self.assertEqual(sp.simplify(substituted - expected), sp.zeros(2, 2))

    def test_conditional_gvalue_rewrites_structured_draft_when_display_names_differ(self):
        def relabeled_payload(g1: str, g2: str) -> dict:
            return {
                "all_nodes": ["N1", "N2", "N3"],
                "external_nodes": ["N1", "N3"],
                "internal_nodes": ["N2"],
                "ground_nodes": [],
                "node_display_names": {"N1": "n11a", "N2": "n11b", "N3": "N4"},
                "G_full": [
                    [g1, f"-({g1})", "0"],
                    [f"-({g1})", f"({g1}) + ({g2})", f"-({g2})"],
                    ["0", f"-({g2})", g2],
                ],
                "Ihis_full": ["0", "0", "0"],
                "G_full_tagged": [
                    [f"{g1}_tag", f"-({g1}_tag)", "0"],
                    [f"-({g1}_tag)", f"({g1}_tag) + ({g2}_tag)", f"-({g2}_tag)"],
                    ["0", f"-({g2}_tag)", f"{g2}_tag"],
                ],
                "Ihis_full_tagged": ["0", "0", "0"],
                "direct_retained_stamps": [],
            }

        response = build_multi_case_response(
            _request(
                [
                    {"name": "case 0", "case_map": {"R1": 0, "R2": 0}, "payload": relabeled_payload("G1", "G3")},
                    {"name": "case 1", "case_map": {"R1": 0, "R2": 1}, "payload": relabeled_payload("G1", "G4")},
                    {"name": "case 2", "case_map": {"R1": 1, "R2": 0}, "payload": relabeled_payload("G2", "G3")},
                    {"name": "case 3", "case_map": {"R1": 1, "R2": 1}, "payload": relabeled_payload("G2", "G4")},
                ],
                deps=_deps("G1", "G3", code=("G2", "G4")),
                case_id="case_id",
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn('createGValue("varG_n11a_n11a", "n11a", "n11a", 0, "case_id == 1 || case_id == 2 || case_id == 3")', draft)
        self.assertIn('createGValue("varG_n11a_N4", "n11a", "N4", 0, "case_id == 1 || case_id == 2 || case_id == 3")', draft)
        self.assertIn('createGValue("varG_N4_N4", "N4", "N4", 0, "case_id == 1 || case_id == 2 || case_id == 3")', draft)
        self.assertNotIn('createGValue("varG_n11a_n11a", "n11a", "n11a", 0, "TRUE")', draft)
        self.assertIn("varG_n11a_n11a = get_CODE(&Gred_code, 0, 0);", draft)
        self.assertIn("varG_n11a_N4 = get_CODE(&Gred_code, 0, 1);", draft)
        self.assertIn("varG_N4_N4 = get_CODE(&Gred_code, 1, 1);", draft)
        for case_index in [1, 2, 3]:
            self.assertIn(
                f"case {case_index}:\n"
                "        varG_n11a_n11a = get_CODE(&Gred_code, 0, 0);\n"
                "        varG_n11a_N4 = get_CODE(&Gred_code, 0, 1);\n"
                "        varG_N4_N4 = get_CODE(&Gred_code, 1, 1);",
                draft,
            )
        self.assertEqual(draft.count("switch (case_id) {"), 3)
        self.assertIn("T1_T2:", draft)


if __name__ == "__main__":
    unittest.main()
