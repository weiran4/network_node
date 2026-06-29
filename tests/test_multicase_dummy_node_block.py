import unittest
import re

import sympy as sp

import optimized_elimination_api as optimized_api
from optimized_elimination_api import build_multi_case_response
from nodal_tool.multicase_finalization_profiles import build_finalization_profiles, finalize_profile_result


def _profile(name, G, *, dummy_blocks=None, external_nodes=None, internal_nodes=None):
    external_nodes = external_nodes or ["A", "B"]
    internal_nodes = internal_nodes or ["a", "b"]
    return {
        "name": name,
        "case_map": {"PACK": 0 if name == "physical" else 1},
        "payload": {
            "all_nodes": ["A", "B", "a", "b"],
            "external_nodes": external_nodes,
            "internal_nodes": internal_nodes,
            "G_full": G,
            "Ihis_full": [["0"], ["0"], ["0"], ["0"]],
            "dummy_node_blocks": dummy_blocks or [],
            "symbol_dependency_table": {"G_EPSILON": "RAM_CONSTANT"},
        },
    }


class MultiCaseDummyNodeBlockTests(unittest.TestCase):
    def test_physical_internal_and_isolated_dummy_internal_share_template(self):
        physical = _profile(
            "physical",
            [
                ["Gp", "0", "-Gp", "0"],
                ["0", "Gp", "0", "-Gp"],
                ["-Gp", "0", "Gp + Ga", "-Ga"],
                ["0", "-Gp", "-Ga", "Gp + Ga"],
            ],
        )
        dummy = _profile(
            "dummy",
            [
                ["Gp", "0", "0", "0"],
                ["0", "Gp", "0", "0"],
                ["0", "0", "G_EPSILON", "0"],
                ["0", "0", "0", "G_EPSILON"],
            ],
            dummy_blocks=[
                {
                    "element_type": "dummy_node_block",
                    "block_id": "DNB1",
                    "dummy_nodes": [
                        {"node_id": "a", "display_name": "a", "dummy_role": "isolated_dummy_internal"},
                        {"node_id": "b", "display_name": "b", "dummy_role": "isolated_dummy_internal"},
                    ],
                    "dimension": 2,
                    "fixed_conductance": "G_EPSILON",
                    "forced_elimination": True,
                    "recoverable": False,
                }
            ],
        )

        response = build_multi_case_response(
            {
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": [physical, dummy],
            }
        )

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertEqual(multi["dummy_node_blocks"]["count"], 1)
        self.assertEqual(multi["dummy_node_blocks"]["common_internal_nodes"], ["a", "b"])
        self.assertEqual(
            multi["recovery_profiles"],
            [
                {"case_ids": [0], "recoverable_nodes": ["a", "b"]},
                {"case_ids": [1], "recoverable_nodes": []},
            ],
        )
        self.assertIn("case 0:", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("DummyNodeBlock isolated internal nodes are not recovered", draft)
        self.assertNotIn("case 1:\n        a = get_CODE(&Vk_code", draft)

    def test_invalid_dummy_matrix_is_rejected(self):
        dummy = _profile(
            "dummy",
            [
                ["G_EPSILON", "0", "0", "0"],
                ["0", "Gp", "0", "0"],
                ["0", "0", "0", "0"],
                ["0", "0", "0", "0"],
            ],
            external_nodes=["A", "a"],
            internal_nodes=["B", "b"],
            dummy_blocks=[
                {
                    "block_id": "DNB1",
                    "dummy_nodes": [{"node_id": "a", "display_name": "a"}],
                    "fixed_conductance": "G_EPSILON",
                }
            ],
        )

        with self.assertRaisesRegex(ValueError, "DummyNodeBlock currently supports only nodes eliminated in every case"):
            build_multi_case_response(
                {
                    "mode": "multi_case_c_export",
                    "case_id_symbol": "case_id",
                    "case_profiles": [_profile("physical", [["1", "0", "0", "0"]] * 4), dummy],
                }
            )

    def test_deferred_isolated_dummy_node_and_physical_internal_share_template(self):
        def profile(name, G, *, blocks=None):
            is_dummy = bool(blocks)
            return {
                "name": name,
                "case_map": {"PACK": 0 if name == "Y" else 1},
                "payload": {
                    "all_nodes": ["A", "B", "RC_A"],
                    "external_nodes": ["A", "B", "RC_A"] if is_dummy else ["A", "B"],
                    "internal_nodes": [] if is_dummy else ["RC_A"],
                    "G_full": G,
                    "Ihis_full": [["0"], ["0"], ["0"]],
                    "dummy_node_blocks": blocks or [],
                    "defer_dummy_node_blocks": bool(blocks),
                    "symbol_dependency_table": {"G_EPSILON": "RAM_CONSTANT", "Gy": "RAM_CONSTANT"},
                },
            }

        y_case = profile(
            "Y",
            [
                ["Gy", "-Gy", "0"],
                ["-Gy", "2*Gy", "-Gy"],
                ["0", "-Gy", "Gy"],
            ],
        )
        d_case = profile(
            "D",
            [
                ["Gy", "-Gy", "0"],
                ["-Gy", "Gy", "0"],
                ["0", "0", "G_EPSILON"],
            ],
            blocks=[
                {
                    "element_type": "dummy_node_block",
                    "block_id": "RC_dummy",
                    "dummy_nodes": [
                        {"node_id": "RC_A", "display_name": "RC_A", "dummy_role": "isolated_dummy_internal"}
                    ],
                    "dimension": 1,
                    "fixed_conductance": "G_EPSILON",
                    "forced_elimination": True,
                    "recoverable": False,
                }
            ],
        )

        response = build_multi_case_response(
            {
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "common_dummy_internal_nodes": ["RC_A"],
                "case_profiles": [y_case, d_case],
            }
        )

        multi = response["multi_case"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertEqual(multi["dummy_node_blocks"]["common_internal_nodes"], ["RC_A"])
        self.assertEqual(multi["diagnostics"]["mixed_physical_dummy_internal"], ["RC_A"])
        self.assertEqual(multi["diagnostics"]["dummy_finalization_profile_count"], 0)
        self.assertIn("case 0:", multi["c_draft"])
        self.assertIn("case 1:", multi["c_draft"])
        self.assertNotIn("NR_FINAL_MAX", multi["c_draft"])

    def test_retained_physical_and_isolated_dummy_final_uses_super_retained_projection(self):
        nodes = ["A_1", "B_1", "C_1", "G", "RC_A", "RC_B", "RC_C", "P", "N", "a", "b", "c"]
        common_six = ["A_1", "B_1", "C_1", "G", "P", "N"]
        y_final = ["A_1", "B_1", "C_1", "G", "RC_A", "RC_B", "RC_C", "P", "N"]

        def zero_matrix(n):
            return [["0" for _ in range(n)] for _ in range(n)]

        def stamp_branch(G, left, right, expr):
            i = nodes.index(left)
            j = nodes.index(right)
            G[i][i] = f"({G[i][i]}) + ({expr})"
            G[j][j] = f"({G[j][j]}) + ({expr})"
            G[i][j] = f"({G[i][j]}) - ({expr})"
            G[j][i] = f"({G[j][i]}) - ({expr})"

        def payload(name, *, y_case):
            G = zero_matrix(len(nodes))
            for node in ["a", "b", "c"]:
                G[nodes.index(node)][nodes.index(node)] = "Gcore"
            if y_case:
                stamp_branch(G, "RC_A", "a", "Grc")
                stamp_branch(G, "RC_B", "b", "Grc")
                stamp_branch(G, "RC_C", "c", "Grc")
                blocks = []
            else:
                for node in ["RC_A", "RC_B", "RC_C"]:
                    G[nodes.index(node)][nodes.index(node)] = "G_EPSILON"
                for left, right in [("a", "b"), ("b", "c"), ("c", "a")]:
                    stamp_branch(G, left, right, "Grc")
                blocks = [
                    {
                        "element_type": "dummy_node_block",
                        "block_id": "RC_dummy",
                        "dimension": 3,
                        "fixed_conductance": "G_EPSILON",
                        "forced_elimination": True,
                        "recoverable": False,
                        "dummy_nodes": [
                            {"node_id": "RC_A", "display_name": "RC_A", "dummy_role": "isolated_dummy_internal"},
                            {"node_id": "RC_B", "display_name": "RC_B", "dummy_role": "isolated_dummy_internal"},
                            {"node_id": "RC_C", "display_name": "RC_C", "dummy_role": "isolated_dummy_internal"},
                        ],
                    }
                ]
            return {
                "name": name,
                "case_map": {"YBox3": 0 if y_case else 1},
                "payload": {
                    "all_nodes": nodes,
                    "external_nodes": y_final,
                    "internal_nodes": ["a", "b", "c"],
                    "node_display_names": {node: node for node in nodes},
                    "G_full": G,
                    "Ihis_full": [["0"] for _ in nodes],
                    "dummy_node_blocks": blocks or [],
                    "defer_dummy_node_blocks": bool(blocks),
                    "symbol_dependency_table": {
                        "G_EPSILON": "RAM_CONSTANT",
                        "Gcore": "RAM_CONSTANT",
                        "Grc": "CODE_VARIABLE",
                    },
                },
            }

        case_profiles = [
            payload("case 0", y_case=True),
            payload("case 1", y_case=False),
            payload("case 2", y_case=True),
            payload("case 3", y_case=False),
            payload("case 4", y_case=True),
            payload("case 5", y_case=False),
            payload("case 6", y_case=True),
            payload("case 7", y_case=False),
        ]
        request = {
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "common_dummy_internal_nodes": ["RC_A", "RC_B", "RC_C"],
            "case_profiles": case_profiles,
        }

        response = build_multi_case_response(request)

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertIn("PACK_CASE_0 = 0, PACK_CASE_1 = 1, RETAINED_NODES_CASE_0 = 9, RETAINED_NODES_CASE_1 = 6, INTERNAL_NODES = 3", draft)
        self.assertIn("int node_active = RETAINED_NODES_CASE_0;", draft)
        self.assertIn("node_active is the retained-node count used by matrix allocation, g_mat_over, and GValue stamping", draft)
        self.assertIn("retained_profile = PACK_CASE_1;", draft)
        self.assertIn("matrixDim(&Gkr_code, INTERNAL_NODES, node_active);", draft)
        self.assertIn("matrixDim(&Vr_code, node_active, 1);", draft)
        self.assertIn("matrixDim(&tmp_W_Gkr_code, INTERNAL_NODES, node_active);", draft)
        self.assertIn("Retained layout is selected during initialization", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        err += matrixDim(&Grr_dyn_code, 3, 3);", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        matrix_register(&Grr_dyn_code);", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n            conditionMatrixForCODE(&Grr_dyn_code);", draft)
        self.assertNotIn("NR = 6, NK = 6", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        set_CODE(&Gkr_code, 0, 6, Gkr_shared_1);", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        set_CODE(&Grr_dyn_code, 0, 0, Grr_shared_1);", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        set_CODE(&Vr_code, 6, 0, RC_A);", draft)
        self.assertEqual(multi["fast_path"], "dummy_finalization_alias_template_matrix_dag")
        retained_profiles = multi["retained_layout_profiles"]
        self.assertEqual(len(retained_profiles), 2)
        self.assertEqual(retained_profiles[0]["nr_active"], 9)
        self.assertEqual(retained_profiles[0]["case_ids"], [0, 2, 4, 6])
        self.assertEqual(retained_profiles[0]["ordered_active_nodes"], common_six + ["RC_A", "RC_B", "RC_C"])
        self.assertEqual(retained_profiles[1]["nr_active"], 6)
        self.assertEqual(retained_profiles[1]["case_ids"], [1, 3, 5, 7])
        self.assertEqual(retained_profiles[1]["ordered_active_nodes"], common_six)
        self.assertEqual(multi["diagnostics"]["retained_layout_profile_count"], 2)
        self.assertEqual(multi["diagnostics"]["retained_profile_dimensions"], [9, 6])
        self.assertEqual(multi["diagnostics"]["super_then_slice_runtime_calculations"], 0)
        self.assertEqual(multi["finalization_profiles"][0]["final_node_order"], y_final)
        self.assertEqual(multi["finalization_profiles"][1]["final_node_order"], common_six)
        self.assertEqual(multi["finalization_profiles"][1]["dummy_nodes"], ["RC_A", "RC_B", "RC_C"])
        y_only_condition = "case_id == 0 || case_id == 2 || case_id == 4 || case_id == 6"
        self.assertIn(f'createGValue("varG_RC_A_RC_A", "RC_A", "RC_A", 0, "{y_only_condition}")', draft)
        gvalue_lines = [line.strip() for line in draft.splitlines() if "createGValue(" in line]
        rc_gvalue_lines = [line for line in gvalue_lines if any(node in line for node in ["RC_A", "RC_B", "RC_C"])]
        self.assertTrue(rc_gvalue_lines)
        for line in rc_gvalue_lines:
            self.assertIn(f', "{y_only_condition}")', line)
            self.assertNotIn(', "TRUE")', line)
        stamp_section = draft.split("/* Stamp dynamic Gred entries", 1)[1].split("/* CODE-SIDE IHIS", 1)[0]
        self.assertLessEqual(stamp_section.count("switch (case_id) {"), 1)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        varG_RC_A_RC_A = get_CODE(&Gred_dyn_code, 0, 0);", draft)
        self.assertRegex(
            draft,
            r"if \(retained_profile == PACK_CASE_0\) \{\n\s*varG_[A-Za-z0-9_]*RC_[ABC][A-Za-z0-9_]* = get_CODE\(&Gred(?:_dyn)?_code, \d+, \d+\);",
        )
        self.assertRegex(
            draft,
            r"case 0:\n\s*case 2:\n\s*case 4:\n\s*case 6:\n\s*InjRC_A = 0\.0;",
        )
        self.assertNotIn("RC_A = get_CODE(&Vk_code", draft)
        self.assertNotIn("RC_B = get_CODE(&Vk_code", draft)
        self.assertNotIn("RC_C = get_CODE(&Vk_code", draft)
        self.assertIn("a = get_CODE(&Vk_code", draft)
        self.assertIn("b = get_CODE(&Vk_code", draft)
        self.assertIn("c = get_CODE(&Vk_code", draft)

        alias_model = optimized_api._build_multicase_alias_template_payload(request)
        template_super = optimized_api._reduced_super_result_from_payload(alias_model["template_payload"])
        profile_set = build_finalization_profiles(case_profiles)
        aliases = alias_model["aliases"]
        for case_index, source_profile in enumerate(case_profiles):
            substitutions = optimized_api._profile_alias_substitutions(source_profile, aliases, case_index)
            template_final = finalize_profile_result(
                sp.Matrix(template_super["G"]).xreplace(substitutions),
                sp.Matrix(template_super["Ihis"]).xreplace(substitutions),
                template_super["external_nodes"],
                profile_set.case_profiles[case_index],
            )
            independent_super = optimized_api._reduced_super_result_from_payload(source_profile["payload"])
            independent_final = finalize_profile_result(
                independent_super["G"],
                independent_super["Ihis"],
                independent_super["external_nodes"],
                profile_set.case_profiles[case_index],
            )
            self.assertEqual(template_final.nodes, independent_final.nodes)
            self.assertEqual(
                (sp.Matrix(template_final.G) - sp.Matrix(independent_final.G)).applyfunc(sp.simplify),
                sp.zeros(independent_final.G.rows, independent_final.G.cols),
            )
            self.assertEqual(
                (sp.Matrix(template_final.Ihis) - sp.Matrix(independent_final.Ihis)).applyfunc(sp.simplify),
                sp.zeros(independent_final.Ihis.rows, independent_final.Ihis.cols),
            )
            self.assertEqual(
                (sp.Matrix(template_super["K_v"]).xreplace(substitutions) - sp.Matrix(independent_super["K_v"])).applyfunc(sp.simplify),
                sp.zeros(independent_super["K_v"].rows, independent_super["K_v"].cols),
            )
            self.assertEqual(
                (sp.Matrix(template_super["K_h"]).xreplace(substitutions) - sp.Matrix(independent_super["K_h"])).applyfunc(sp.simplify),
                sp.zeros(independent_super["K_h"].rows, independent_super["K_h"].cols),
            )

    def test_no_internal_constant_g_uses_active_ram_overlay_dimension(self):
        nodes = ["N1", "N4", "N6", "N5", "N3"]
        common_nodes = ["N1", "N4", "N6", "N5"]

        def profile(name: str, *, y_case: bool) -> dict:
            G = [["0" for _ in nodes] for _ in nodes]
            for index, node in enumerate(nodes):
                if node == "N3" and not y_case:
                    G[index][index] = "G_EPSILON"
                else:
                    G[index][index] = "1/R"
            if y_case:
                G[1][4] = "-1/R"
                G[4][1] = "-1/R"
            blocks = []
            if not y_case:
                blocks = [
                    {
                        "element_type": "dummy_node_block",
                        "block_id": "N3_dummy",
                        "dimension": 1,
                        "fixed_conductance": "G_EPSILON",
                        "forced_elimination": True,
                        "recoverable": False,
                        "dummy_nodes": [
                            {"node_id": "N3", "display_name": "N3", "dummy_role": "isolated_dummy_internal"}
                        ],
                    }
                ]
            return {
                "name": name,
                "case_map": {"C1": 0 if y_case else 1},
                "payload": {
                    "all_nodes": nodes,
                    "external_nodes": nodes,
                    "internal_nodes": [],
                    "node_display_names": {node: node for node in nodes},
                    "G_full": G,
                    "Ihis_full": [["0"] for _ in nodes],
                    "dummy_node_blocks": blocks,
                    "defer_dummy_node_blocks": bool(blocks),
                    "symbol_dependency_table": {
                        "R": "RAM_CONSTANT",
                        "G_EPSILON": "RAM_CONSTANT",
                    },
                },
            }

        response = build_multi_case_response(
            {
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "common_dummy_internal_nodes": ["N3"],
                "case_profiles": [
                    profile("case 0", y_case=True),
                    profile("case 1", y_case=False),
                ],
            }
        )

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertIn("PACK_CASE_0 = 0, PACK_CASE_1 = 1, RETAINED_NODES_CASE_0 = 5, RETAINED_NODES_CASE_1 = 4, INTERNAL_NODES = 0", draft)
        self.assertIn("setupGMatrix(node_active);", draft)
        self.assertIn("for (int row = 0; row < node_active; row++)", draft)
        self.assertIn("for (int col = 0; col < node_active; col++)", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        g_mat_nods[4] = getNodeNum(comp, \"N3\");", draft)
        self.assertIn("if (retained_profile == PACK_CASE_0) {\n        g_mat_over[1][4] =", draft)
        self.assertNotIn("setupGMatrix(5);", draft)
        self.assertEqual(multi["retained_layout_profiles"][0]["ordered_active_nodes"], nodes)
        self.assertEqual(multi["retained_layout_profiles"][1]["ordered_active_nodes"], common_nodes)


if __name__ == "__main__":
    unittest.main()
