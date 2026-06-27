import unittest
from unittest.mock import patch

import optimized_elimination_api as optimized_api
from optimized_elimination_api import build_multi_case_response


def _deps(*symbols: str) -> dict:
    return {symbol: "RAM_CONSTANT" for symbol in symbols}


def _payload(g_matrix: list[list[str]], deps: dict | None = None) -> dict:
    deps = deps or _deps("R", "G_EPSILON")
    return {
        "all_nodes": ["N1", "N2", "N4"],
        "external_nodes": ["N1", "N2", "N4"],
        "internal_nodes": [],
        "ground_nodes": [],
        "node_display_names": {"N1": "N1", "N2": "N2", "N4": "N4"},
        "G_full": g_matrix,
        "Ihis_full": ["0", "0", "0"],
        "G_full_tagged": g_matrix,
        "Ihis_full_tagged": ["0", "0", "0"],
        "direct_retained_stamps": [],
        "symbol_dependency_table": deps,
        "symbol_dependency_table_tagged": deps,
    }


def _block_after_marker(draft: str, marker: str, start_text: str, end_text: str = "default:") -> str:
    offset = draft.index(marker)
    start = draft.index(start_text, offset)
    end = draft.index(end_text, start)
    return draft[start:end]


class MultiCaseDummyCExportTests(unittest.TestCase):
    def test_no_dummy_profiles_do_not_enter_dummy_finalization_path(self):
        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {
                    "name": "Case 0",
                    "case_map": {"R1": 0},
                    "payload": _payload([
                        ["1/R", "-1/R", "0"],
                        ["-1/R", "2/R", "-1/R"],
                        ["0", "-1/R", "1/R"],
                    ]),
                },
                {
                    "name": "Case 1",
                    "case_map": {"R1": 1},
                    "payload": _payload([
                        ["1/R", "-1/R", "0"],
                        ["-1/R", "2/R", "-1/R"],
                        ["0", "-1/R", "1/R"],
                    ]),
                },
            ],
        })

        multi = response["multi_case"]
        self.assertNotEqual(multi["codegen_mode"], "case-specific dummy finalization")
        self.assertNotEqual(multi["fast_path"], "dummy_finalization_profiles")

    def test_dummy_case_gets_profile_specific_solver_dimension(self):
        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {
                    "name": "Case 0 physical N4",
                    "case_map": {"YBox1": 0},
                    "payload": _payload([
                        ["1/R", "-1/R", "0"],
                        ["-1/R", "2/R", "-1/R"],
                        ["0", "-1/R", "1/R"],
                    ]),
                },
                {
                    "name": "Case 1 dummy N4",
                    "case_map": {"YBox1": 1},
                    "payload": _payload([
                        ["1/R", "-1/R", "0"],
                        ["-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                        ["0", "-G_EPSILON", "G_EPSILON"],
                    ]),
                    "dummy_finalization": {
                        "dummy_leaves": [
                            {
                                "dummy_node": "N4",
                                "anchor_node": "N2",
                                "branch_id": "Dummy12",
                                "conductance": "G_EPSILON",
                            }
                        ]
                    },
                },
            ],
        })

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["codegen_mode"], "case-agnostic alias template with dummy finalization")
        self.assertEqual(multi["fast_path"], "dummy_finalization_alias_template")
        self.assertIn("NR_SUPER = 3", draft)
        self.assertIn("NR_FINAL_MAX = 3", draft)
        self.assertIn("case 0:", draft)
        self.assertIn("setupGMatrix(3);", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("setupGMatrix(2);", draft)
        case1_block = _block_after_marker(draft, "Profile-specific finalized RAM stamp", "case 1:")
        self.assertIn('g_mat_nods[0] = getNodeNum(comp, "N1");', case1_block)
        self.assertIn('g_mat_nods[1] = getNodeNum(comp, "N2");', case1_block)
        self.assertNotIn("N4", case1_block)
        self.assertNotIn("G_EPSILON", case1_block)
        self.assertNotIn("InjN4", case1_block)
        self.assertNotIn("MATH_matx_invert", draft)
        self.assertEqual(multi["finalization_profiles"][1]["final_node_order"], ["N1", "N2"])
        reuse_groups = multi["gred_entry_reuse_by_case"]
        self.assertEqual([group["case_index"] for group in reuse_groups], [0, 1])
        case1_reuse = reuse_groups[1]["items"]
        self.assertIn(
            {
                "target": [0, 1],
                "target_nodes": ["N1", "N2"],
                "base": [0, 0],
                "base_nodes": ["N1", "N1"],
                "sign": -1,
                "relation": "opposite",
                "target_label": "Gred[N1,N2]",
                "base_label": "Gred[N1,N1]",
                "text": "Gred[N1,N2] = -Gred[N1,N1]",
            },
            case1_reuse,
        )

    def test_dynamic_physical_dummy_pair_gets_profile_conditional_gvalue(self):
        deps = {"R": "RAM_CONSTANT", "G_EPSILON": "RAM_CONSTANT", "G_DYN": "CODE_VARIABLE"}
        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {
                    "name": "Case 0 physical dynamic N4",
                    "case_map": {"YBox1": 0},
                    "payload": _payload([
                        ["1/R", "-1/R", "0"],
                        ["-1/R", "1/R + G_DYN", "-G_DYN"],
                        ["0", "-G_DYN", "G_DYN"],
                    ], deps),
                },
                {
                    "name": "Case 1 dummy N4",
                    "case_map": {"YBox1": 1},
                    "payload": _payload([
                        ["1/R", "-1/R", "0"],
                        ["-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                        ["0", "-G_EPSILON", "G_EPSILON"],
                    ], deps),
                    "dummy_finalization": {
                        "dummy_leaves": [
                            {
                                "dummy_node": "N4",
                                "anchor_node": "N2",
                                "branch_id": "Dummy12",
                                "conductance": "G_EPSILON",
                            }
                        ]
                    },
                },
            ],
        })

        draft = response["multi_case"]["c_draft"]
        self.assertIn("GVALUES:", draft)
        self.assertIn('"case_id == 0"', draft)
        self.assertIn('createGValue("varG_N2_N2", "N2", "N2", 0, "case_id == 0")', draft)
        self.assertIn('createGValue("varG_N2_N4", "N2", "N4", 0, "case_id == 0")', draft)
        self.assertIn('createGValue("varG_N4_N4", "N4", "N4", 0, "case_id == 0")', draft)
        self.assertIn("varG_N2_N2 = G_DYN + 1.0/R;", draft)
        self.assertIn("varG_N2_N4 = -G_DYN;", draft)
        case1_block = _block_after_marker(draft, "Profile-specific finalized RAM stamp", "case 1:")
        self.assertNotIn("varG_N4", case1_block)
        self.assertNotIn("G_DYN", case1_block)

    def test_physical_internal_recovery_is_kept_while_dummy_is_skipped(self):
        deps = _deps("R", "G_EPSILON")
        base_payload = {
            "all_nodes": ["N1", "N2", "N3", "N4"],
            "external_nodes": ["N1", "N3", "N4"],
            "internal_nodes": ["N2"],
            "ground_nodes": [],
            "node_display_names": {"N1": "N1", "N2": "N2", "N3": "N3", "N4": "N4"},
            "G_full": [
                ["1/R", "-1/R", "0", "0"],
                ["-1/R", "2/R", "-1/R", "0"],
                ["0", "-1/R", "2/R", "-1/R"],
                ["0", "0", "-1/R", "1/R"],
            ],
            "Ihis_full": ["0", "0", "0", "0"],
            "G_full_tagged": [
                ["1/R", "-1/R", "0", "0"],
                ["-1/R", "2/R", "-1/R", "0"],
                ["0", "-1/R", "2/R", "-1/R"],
                ["0", "0", "-1/R", "1/R"],
            ],
            "Ihis_full_tagged": ["0", "0", "0", "0"],
            "direct_retained_stamps": [],
            "symbol_dependency_table": deps,
            "symbol_dependency_table_tagged": deps,
        }
        dummy_payload = {
            **base_payload,
            "external_nodes": ["N1", "N3", "N4"],
            "internal_nodes": ["N2"],
            "G_full": [
                ["1/R", "-1/R", "0", "0"],
                ["-1/R", "2/R", "-1/R", "0"],
                ["0", "-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                ["0", "0", "-G_EPSILON", "G_EPSILON"],
            ],
            "G_full_tagged": [
                ["1/R", "-1/R", "0", "0"],
                ["-1/R", "2/R", "-1/R", "0"],
                ["0", "-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                ["0", "0", "-G_EPSILON", "G_EPSILON"],
            ],
        }
        with patch.object(optimized_api, "eliminate_internal_nodes", wraps=optimized_api.eliminate_internal_nodes) as spy:
            response = build_multi_case_response({
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": [
                    {"name": "physical", "case_map": {"YBox1": 0}, "payload": base_payload},
                    {
                        "name": "dummy",
                        "case_map": {"YBox1": 1},
                        "payload": dummy_payload,
                        "dummy_finalization": {
                            "dummy_leaves": [
                                {"dummy_node": "N4", "anchor_node": "N3", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                            ]
                        },
                    },
                ],
            })

        draft = response["multi_case"]["c_draft"]
        self.assertEqual(spy.call_count, 1)
        self.assertIn("T1_T2:", draft)
        self.assertIn("N2 =", draft)
        self.assertNotIn("N4 = get_CODE(&Vk_code", draft)
        case1_block = _block_after_marker(draft, "Profile-specific finalized RAM stamp", "case 1:")
        self.assertNotIn("InjN4 = 0.0;", case1_block)

    def test_identical_common_physical_reduction_is_cached_for_dummy_profiles(self):
        deps = _deps("R", "G_EPSILON")
        payload = {
            "all_nodes": ["N1", "N2", "N3", "N4"],
            "external_nodes": ["N1", "N3", "N4"],
            "internal_nodes": ["N2"],
            "ground_nodes": [],
            "node_display_names": {"N1": "N1", "N2": "N2", "N3": "N3", "N4": "N4"},
            "G_full": [
                ["1/R", "-1/R", "0", "0"],
                ["-1/R", "2/R", "-1/R", "0"],
                ["0", "-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                ["0", "0", "-G_EPSILON", "G_EPSILON"],
            ],
            "Ihis_full": ["0", "0", "0", "0"],
            "G_full_tagged": [
                ["1/R", "-1/R", "0", "0"],
                ["-1/R", "2/R", "-1/R", "0"],
                ["0", "-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                ["0", "0", "-G_EPSILON", "G_EPSILON"],
            ],
            "Ihis_full_tagged": ["0", "0", "0", "0"],
            "direct_retained_stamps": [],
            "symbol_dependency_table": deps,
            "symbol_dependency_table_tagged": deps,
        }
        profiles = [
            {
                "name": "dummy A",
                "case_map": {"YBox1": 0},
                "payload": payload,
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N3", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            },
            {
                "name": "dummy B",
                "case_map": {"YBox1": 1},
                "payload": payload,
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N3", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            },
        ]

        with patch.object(optimized_api, "eliminate_internal_nodes", wraps=optimized_api.eliminate_internal_nodes) as spy:
            response = build_multi_case_response({
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": profiles,
            })

        self.assertEqual(response["multi_case"]["fast_path"], "dummy_finalization_profiles")
        self.assertEqual(spy.call_count, 1)

    def test_case_resolved_alias_template_reduces_common_physical_network_once_with_dummy_profiles(self):
        deps = _deps("G_A", "G_B", "G_EPSILON")

        def payload(g_expr: str) -> dict:
            return {
                "all_nodes": ["N1", "N2", "N3", "N4"],
                "external_nodes": ["N1", "N3", "N4"],
                "internal_nodes": ["N2"],
                "ground_nodes": [],
                "node_display_names": {"N1": "N1", "N2": "N2", "N3": "N3", "N4": "N4"},
                "G_full": [
                    [g_expr, f"-({g_expr})", "0", "0"],
                    [f"-({g_expr})", f"({g_expr}) + 1/R", "-1/R", "0"],
                    ["0", "-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                    ["0", "0", "-G_EPSILON", "G_EPSILON"],
                ],
                "Ihis_full": ["0", "0", "0", "0"],
                "G_full_tagged": [
                    [g_expr, f"-({g_expr})", "0", "0"],
                    [f"-({g_expr})", f"({g_expr}) + 1/R", "-1/R", "0"],
                    ["0", "-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                    ["0", "0", "-G_EPSILON", "G_EPSILON"],
                ],
                "Ihis_full_tagged": ["0", "0", "0", "0"],
                "direct_retained_stamps": [],
                "symbol_dependency_table": {**deps, "R": "RAM_CONSTANT"},
                "symbol_dependency_table_tagged": {**deps, "R": "RAM_CONSTANT"},
            }

        profiles = [
            {
                "name": "dummy A",
                "case_map": {"R1": 0, "YBox1": 0},
                "payload": payload("G_A"),
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N3", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            },
            {
                "name": "dummy B",
                "case_map": {"R1": 1, "YBox1": 1},
                "payload": payload("G_B"),
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N3", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            },
        ]

        with patch.object(optimized_api, "eliminate_internal_nodes", wraps=optimized_api.eliminate_internal_nodes) as spy:
            response = build_multi_case_response({
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": profiles,
            })

        draft = response["multi_case"]["c_draft"]
        self.assertEqual(spy.call_count, 1)
        self.assertIn("cr_G_0_0_eff", draft)
        self.assertIn("cr_G_0_0_eff = G_A;", draft)
        self.assertIn("cr_G_0_0_eff = G_B;", draft)
        self.assertIn("N2 =", draft)


if __name__ == "__main__":
    unittest.main()
