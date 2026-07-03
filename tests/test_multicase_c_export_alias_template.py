import unittest
import json
import re
import time
from pathlib import Path

import sympy as sp

from elimination import eliminate_internal_nodes
import optimized_elimination_api as optimized_api
from optimized_elimination_api import (
    build_multi_case_response,
    _alias_assignment_lines,
    _build_multicase_alias_template_payload,
)


def _series_payload(g1: str, g2: str = "G2", *, internal: bool = True) -> dict:
    def tag(expr: str) -> str:
        return "0" if str(expr).strip() == "0" else f"{expr}_tag"

    if internal:
        return {
            "all_nodes": ["A", "B", "X"],
            "external_nodes": ["A", "B"],
            "internal_nodes": ["X"],
            "ground_nodes": [],
            "node_display_names": {"A": "A", "B": "B", "X": "X"},
            "G_full": [
                [g1, "0", f"-({g1})"],
                ["0", g2, f"-({g2})"],
                [f"-({g1})", f"-({g2})", f"({g1}) + ({g2})"],
            ],
            "Ihis_full": ["0", "0", "0"],
            "G_full_tagged": [
                [tag(g1), "0", f"-({tag(g1)})"],
                ["0", tag(g2), f"-({tag(g2)})"],
                [f"-({tag(g1)})", f"-({tag(g2)})", f"({tag(g1)}) + ({tag(g2)})"],
            ],
            "Ihis_full_tagged": ["0", "0", "0"],
            "direct_retained_stamps": [],
        }
    return {
        "all_nodes": ["A", "B"],
        "external_nodes": ["A", "B"],
        "internal_nodes": [],
        "ground_nodes": [],
        "node_display_names": {"A": "A", "B": "B"},
        "G_full": [[g1, f"-({g1})"], [f"-({g1})", g1]],
        "Ihis_full": ["0", "0"],
        "G_full_tagged": [[tag(g1), f"-({tag(g1)})"], [f"-({tag(g1)})", tag(g1)]],
        "Ihis_full_tagged": ["0", "0"],
        "direct_retained_stamps": [],
    }


def _deps(*symbols: str, code: tuple[str, ...] = (), step: tuple[str, ...] = ()) -> dict:
    table = {symbol: "RAM_CONSTANT" for symbol in symbols}
    table.update({symbol: "CODE_VARIABLE" for symbol in code})
    table.update({symbol: "CODE_PER_STEP" for symbol in step})
    for symbol, owner in list(table.items()):
        table[f"{symbol}_tag"] = owner
    return table


def _direct_series_payload(g1: str, g2: str) -> dict:
    def tag(expr: str) -> str:
        return "0" if str(expr).strip() == "0" else f"{expr}_tag"

    return {
        "all_nodes": ["N1", "N2", "N3"],
        "external_nodes": ["N1", "N2", "N3"],
        "internal_nodes": [],
        "ground_nodes": [],
        "node_display_names": {"N1": "N1", "N2": "N2", "N3": "N3"},
        "G_full": [
            [g1, f"-({g1})", "0"],
            [f"-({g1})", f"({g1}) + ({g2})", f"-({g2})"],
            ["0", f"-({g2})", g2],
        ],
        "Ihis_full": ["0", "0", "0"],
        "G_full_tagged": [
            [tag(g1), f"-({tag(g1)})", "0"],
            [f"-({tag(g1)})", f"({tag(g1)}) + ({tag(g2)})", f"-({tag(g2)})"],
            ["0", f"-({tag(g2)})", tag(g2)],
        ],
        "Ihis_full_tagged": ["0", "0", "0"],
        "direct_retained_stamps": [],
    }


def _request(profiles: list[dict], *, deps: dict | None = None, case_id: str = "global_case_id") -> dict:
    dep_table = deps or _deps("X", "Y", "G2")
    for profile in profiles:
        profile["payload"]["symbol_dependency_table"] = dict(dep_table)
        profile["payload"]["symbol_dependency_table_tagged"] = dict(dep_table)
    return {
        "mode": "multi_case_c_export",
        "case_id_symbol": case_id,
        "case_profiles": profiles,
    }


class MultiCaseAliasTemplateTests(unittest.TestCase):
    def test_cc_named_user_symbol_is_not_parsed_as_sympy_complex_field(self):
        matrix = optimized_api._matrix_from_clean([["AA + CC", "BB + CC"]])
        self.assertEqual(matrix[0, 0], sp.Symbol("AA") + sp.Symbol("CC"))
        self.assertEqual(matrix[0, 1], sp.Symbol("BB") + sp.Symbol("CC"))

    def test_direct_retained_stamp_cc_symbol_can_accumulate(self):
        payload = {
            "direct_retained_stamps": [
                {
                    "id": "box",
                    "support_nodes": ["A", "B"],
                    "G": [
                        {"row": "A", "col": "A", "expr": "AA + CC"},
                        {"row": "A", "col": "A", "expr": "BB"},
                    ],
                    "Ihis": [{"row": "A", "expr": "Ihis_CC"}],
                }
            ]
        }
        G, Ihis, accepted = optimized_api._direct_retained_matrices(payload, ["A", "B"], ["A", "B"])
        self.assertEqual(accepted[0]["id"], "box")
        self.assertEqual(G[0, 0], sp.Symbol("AA") + sp.Symbol("BB") + sp.Symbol("CC"))
        self.assertEqual(Ihis[0, 0], sp.Symbol("Ihis_CC"))

    def test_alias_template_rewrites_direct_retained_stamps(self):
        def payload(value: str) -> dict:
            data = _series_payload(value, internal=False)
            data["direct_retained_stamps"] = [
                {
                    "id": "R1",
                    "support_nodes": ["A", "B"],
                    "G": [
                        {"row": "A", "col": "A", "expr": value, "tagged": value},
                        {"row": "A", "col": "B", "expr": f"-({value})", "tagged": f"-({value})"},
                        {"row": "B", "col": "A", "expr": f"-({value})", "tagged": f"-({value})"},
                        {"row": "B", "col": "B", "expr": value, "tagged": value},
                    ],
                    "Ihis": [],
                }
            ]
            data["symbol_dependency_table"] = _deps("X", "Y")
            data["symbol_dependency_table_tagged"] = _deps("X", "Y")
            return data

        alias_model = _build_multicase_alias_template_payload(
            {
                "case_profiles": [
                    {"name": "case 0", "case_map": {"R1": 0}, "payload": payload("X")},
                    {"name": "case 1", "case_map": {"R1": 1}, "payload": payload("X + Y")},
                ]
            }
        )

        stamp_entries = alias_model["template_payload"]["direct_retained_stamps"][0]["G"]
        self.assertEqual(stamp_entries[0]["expr"], "multcase_G_R1_A_A")
        self.assertEqual(stamp_entries[1]["expr"], "-multcase_G_R1_A_A")
        self.assertEqual(stamp_entries[2]["tagged"], "-multcase_G_R1_A_A")
        self.assertEqual(stamp_entries[3]["tagged"], "multcase_G_R1_A_A")

    def test_single_branch_two_cases_use_full_value_effective_alias(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 Case 0", "case_map": {"R1": 0}, "payload": _series_payload("X", internal=False)},
                    {"name": "R1 Case 1", "case_map": {"R1": 1}, "payload": _series_payload("X + Y", internal=False)},
                ],
                deps=_deps("X", "Y"),
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertEqual(response["multi_case"]["codegen_mode"], "case-agnostic alias template")
        self.assertIn("STATIC:\n\n", draft)
        self.assertIn("multcase_G_R1_A_A = X;", draft)
        self.assertIn("multcase_G_R1_A_A = X + Y;", draft)
        self.assertIn("g_mat_over[0][0] = multcase_G_R1_A_A;", draft)
        self.assertIn("g_mat_over[0][1] = -multcase_G_R1_A_A;", draft)
        self.assertNotIn("X + Y - X", draft)
        self.assertNotIn("base + delta", draft)
        self.assertLessEqual(draft.count("Gred ="), 1)

    def test_alias_template_response_exposes_template_block_preview(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 Case 0", "case_map": {"R1": 0}, "payload": _series_payload("X")},
                    {"name": "R1 Case 1", "case_map": {"R1": 1}, "payload": _series_payload("X + Y")},
                ],
                deps=_deps("X", "Y", "G2"),
            )
        )

        multi = response["multi_case"]
        blocks = multi["template_blocks"]
        self.assertIn("G_rr", blocks)
        self.assertIn("G_ri", blocks)
        self.assertIn("G_ir", blocks)
        self.assertIn("G_ii", blocks)
        self.assertEqual(blocks["G_ri"][0][0], "-multcase_G_R1_A_A")
        self.assertEqual(blocks["G_ir"][0][0], "-multcase_G_R1_A_A")
        self.assertEqual(multi["template_block_nodes"]["retained_order"], ["A", "B"])
        self.assertEqual(multi["template_block_nodes"]["internal_order"], ["X"])

    def test_ram_owned_multicase_alias_precomputes_static_blocks_before_runtime_ihis(self):
        def payload(value: str) -> dict:
            data = _series_payload(value)
            data["Ihis_full"] = ["0", "0", "h"]
            data["Ihis_full_tagged"] = ["0", "0", "h_tag"]
            return data

        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 Case 0", "case_map": {"R1": 0}, "payload": payload("X")},
                    {"name": "R1 Case 1", "case_map": {"R1": 1}, "payload": payload("X + Y")},
                ],
                deps=_deps("X", "Y", "G2", step=("h",)),
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn("multcase_G_R1_A_A = X;", draft)
        self.assertIn("multcase_G_R1_A_A = X + Y;", draft)
        self.assertIn("set(&Grk_code, 0, 0, Grk_A_k1);", draft)
        self.assertIn("set(&W_code, 0, 0, W_1_1);", draft)
        self.assertIn("matrix_mult(&tmp_Grk_W_code, &Grk_code, &W_code);", draft)
        self.assertIn("Symmetry reuse: W * Gkr = transpose(Grk * W).", draft)
        self.assertIn("set(&tmp_W_Gkr_code, row, col, get(&tmp_Grk_W_code, col, row));", draft)
        self.assertNotIn("matrix_mult(&tmp_W_Gkr_code, &W_code, &Gkr_code);", draft)
        self.assertNotIn("set_CODE(&Grk_code", draft)
        self.assertNotIn("matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);", draft)
        self.assertNotIn("matrix_mult_CODE(&tmp_W_Gkr_code, &W_code, &Gkr_code);", draft)
        self.assertNotIn("matrix_register(&Grk_code);", draft)
        self.assertNotIn("matrix_register(&Gkr_code);", draft)
        self.assertNotIn("conditionMatrixForCODE(&Grk_code);", draft)
        self.assertNotIn("conditionMatrixForCODE(&Gkr_code);", draft)
        self.assertIn("matrix_register(&W_code);", draft)
        self.assertIn("conditionMatrixForCODE(&W_code);", draft)
        self.assertIn("matrix_register(&tmp_Grk_W_code);", draft)
        self.assertIn("matrix_register(&tmp_W_Gkr_code);", draft)
        self.assertIn("matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);", draft)

    def test_identical_profiles_collapse_to_single_structured_draft(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "Pack Case 1", "case_map": {"YBox1": 0}, "payload": _series_payload("X")},
                    {"name": "Pack Case 2", "case_map": {"YBox1": 1}, "payload": _series_payload("X")},
                ],
                deps=_deps("X", "G2"),
                case_id="case_id",
            )
        )

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "identical_profiles")
        self.assertEqual(multi["codegen_mode"], "single structured draft")
        self.assertNotIn("RAM-switch multi-case C draft", draft)
        self.assertNotIn("NCASE = 2", draft)
        self.assertNotIn("switch (case_id)", draft)
        self.assertIn("RTDS-style C draft", draft)

    def test_ram_code_mixed_owner_promotes_alias_to_code_without_ram_base_delta(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 Case 0", "case_map": {"R1": 0}, "payload": _series_payload("G_const", internal=False)},
                    {"name": "R1 Case 1", "case_map": {"R1": 1}, "payload": _series_payload("G_dynamic", internal=False)},
                ],
                deps=_deps("G_const", code=("G_dynamic",)),
            )
        )

        aliases = response["multi_case"]["aliases"]
        self.assertEqual(aliases["multcase_G_R1_A_A"]["owner"], "CODE")
        self.assertTrue(any("multcase_G_R1_A_A" in warning and "case_id is fixed before simulation" in warning for warning in response["warnings"]))
        draft = response["multi_case"]["c_draft"]
        self.assertIn("BEGIN_T0:", draft)
        self.assertIn("g_mat_over[0][1] = -G_const;", draft)
        self.assertIn("multcase_G_R1_A_A = G_const;", draft)
        self.assertIn("multcase_G_R1_A_A = G_dynamic;", draft)
        self.assertIn("varG_A_A = multcase_G_R1_A_A;", draft)
        self.assertIn("varG_A_B = -multcase_G_R1_A_A;", draft)
        self.assertIn("varG_B_B = multcase_G_R1_A_A;", draft)
        self.assertNotIn("set_CODE(&G_code", draft)
        self.assertNotIn("G_dynamic - G_const", draft)

    def test_step_history_case_promotes_alias_to_code_per_step(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 Case 0", "case_map": {"R1": 0}, "payload": _series_payload("H_const", internal=False)},
                    {"name": "R1 Case 1", "case_map": {"R1": 1}, "payload": _series_payload("H_step", internal=False)},
                ],
                deps=_deps("H_const", step=("H_step",)),
            )
        )

        aliases = response["multi_case"]["aliases"]
        self.assertEqual(aliases["multcase_G_R1_A_A"]["owner"], "CODE_PER_STEP")
        self.assertTrue(any("multcase_G_R1_A_A" in warning and "case_id is fixed before simulation" in warning for warning in response["warnings"]))
        draft = response["multi_case"]["c_draft"]
        self.assertIn("multcase_G_R1_A_A = H_const;", draft)
        self.assertIn("multcase_G_R1_A_A = H_step;", draft)

    def test_two_branches_do_not_generate_cartesian_final_gred_blocks(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "00", "case_map": {"R1": 0, "R2": 0}, "payload": _series_payload("X", "P")},
                    {"name": "01", "case_map": {"R1": 0, "R2": 1}, "payload": _series_payload("X", "Qv")},
                    {"name": "10", "case_map": {"R1": 1, "R2": 0}, "payload": _series_payload("Y", "P")},
                    {"name": "11", "case_map": {"R1": 1, "R2": 1}, "payload": _series_payload("Y", "Qv")},
                ],
                deps=_deps("X", "Y", "P", "Qv"),
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn("multcase_G_R1_A_A", draft)
        self.assertIn("multcase_G_R2_B_B", draft)
        self.assertIn("switch (global_case_id)", draft)
        self.assertIn("R1_case_id = 1;", draft)
        self.assertIn("R2_case_id = 1;", draft)
        self.assertLessEqual(draft.count("Shared Schur flow"), 1)
        self.assertLess(draft.count("Gred"), 8)

    def test_composite_entries_use_existing_aliases_without_exponential_search(self):
        nodes = [f"N{i}" for i in range(6)]

        def payload(r1_value: str, r2_value: str) -> dict:
            matrix = [["0" for _ in nodes] for _ in nodes]
            matrix[0][0] = r1_value
            matrix[0][1] = r2_value
            for index in range(1, 5):
                matrix[index][0] = f"{index + 1}*({r1_value})"
                matrix[index][1] = f"{index + 1}*({r2_value})"
                matrix[index][2] = f"{index + 2}*({r1_value})"
            matrix[5][5] = f"({r1_value}) + ({r2_value})"
            return {
                "all_nodes": nodes,
                "external_nodes": nodes,
                "internal_nodes": [],
                "ground_nodes": [],
                "node_display_names": {node: node for node in nodes},
                "G_full": matrix,
                "G_full_tagged": matrix,
                "Ihis_full": ["0" for _ in nodes],
                "Ihis_full_tagged": ["0" for _ in nodes],
                "direct_retained_stamps": [],
                "symbol_dependency_table": _deps("A0", "A1", "B0", "B1"),
                "symbol_dependency_table_tagged": _deps("A0", "A1", "B0", "B1"),
            }

        profiles = [
            {"name": "case 0", "case_map": {"R1": 0, "R2": 0}, "payload": payload("A0", "B0")},
            {"name": "case 1", "case_map": {"R1": 0, "R2": 1}, "payload": payload("A0", "B1")},
            {"name": "case 2", "case_map": {"R1": 1, "R2": 0}, "payload": payload("A1", "B0")},
            {"name": "case 3", "case_map": {"R1": 1, "R2": 1}, "payload": payload("A1", "B1")},
        ]

        start = time.perf_counter()
        alias_model = _build_multicase_alias_template_payload({"case_profiles": profiles})
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 1.0)
        composite = alias_model["template_payload"]["G_full"][5][5]
        self.assertIn("multcase_G_R1_N0_N0", composite)
        self.assertIn("multcase_G_R2_N0_N1", composite)

    def test_overlapping_case_sources_fall_back_to_profile_resolved_input_alias(self):
        nodes = ["N1", "N2"]

        def payload(value: str) -> dict:
            return {
                "all_nodes": nodes,
                "external_nodes": nodes,
                "internal_nodes": [],
                "ground_nodes": [],
                "node_display_names": {node: node for node in nodes},
                "G_full": [[value, "0"], ["0", "0"]],
                "G_full_tagged": [[value, "0"], ["0", "0"]],
                "Ihis_full": ["0", "0"],
                "Ihis_full_tagged": ["0", "0"],
                "direct_retained_stamps": [],
                "symbol_dependency_table": _deps("AA", "Dabc", "G22", "Grc", "w2"),
                "symbol_dependency_table_tagged": _deps("AA", "Dabc", "G22", "Grc", "w2"),
            }

        profiles = [
            {"name": "case 0", "case_map": {"C1": 0, "C2": 0}, "payload": payload("AA + 2*G22 + Grc + 2*w2")},
            {"name": "case 1", "case_map": {"C1": 0, "C2": 1}, "payload": payload("Dabc + 2*G22 + Grc + 2*w2")},
            {"name": "case 2", "case_map": {"C1": 1, "C2": 0}, "payload": payload("AA + G22 + Grc + w2")},
            {"name": "case 3", "case_map": {"C1": 1, "C2": 1}, "payload": payload("Dabc + G22 + Grc + w2")},
        ]

        alias_model = _build_multicase_alias_template_payload({"case_profiles": profiles})

        self.assertEqual(alias_model["template_payload"]["G_full"][0][0], "multcase_G_combined_N1_N1")
        alias = alias_model["aliases"]["multcase_G_combined_N1_N1"]
        self.assertEqual(alias["selector"], "global")
        self.assertEqual(alias["case_values"]["3"], "Dabc + G22 + Grc + w2")

    def test_identical_global_matrix_case_sequences_reuse_one_alias(self):
        nodes = ["N1", "N2"]

        def payload(value: str) -> dict:
            return {
                "all_nodes": nodes,
                "external_nodes": nodes,
                "internal_nodes": [],
                "ground_nodes": [],
                "node_display_names": {node: node for node in nodes},
                "G_full": [[value, "0"], ["0", value]],
                "G_full_tagged": [[value, "0"], ["0", value]],
                "Ihis_full": ["0", "0"],
                "Ihis_full_tagged": ["0", "0"],
                "direct_retained_stamps": [],
                "symbol_dependency_table": _deps("AA", "Dabc", "G22", "Grc", "w2"),
                "symbol_dependency_table_tagged": _deps("AA", "Dabc", "G22", "Grc", "w2"),
            }

        profiles = [
            {"name": "case 0", "case_map": {"C1": 0, "C2": 0}, "payload": payload("AA + 2*G22 + Grc + 2*w2")},
            {"name": "case 1", "case_map": {"C1": 0, "C2": 1}, "payload": payload("Dabc + 2*G22 + Grc + 2*w2")},
            {"name": "case 2", "case_map": {"C1": 1, "C2": 0}, "payload": payload("AA + G22 + Grc + w2")},
            {"name": "case 3", "case_map": {"C1": 1, "C2": 1}, "payload": payload("Dabc + G22 + Grc + w2")},
        ]

        alias_model = _build_multicase_alias_template_payload({"case_profiles": profiles})

        global_g_aliases = [
            alias
            for alias, info in alias_model["aliases"].items()
            if info.get("selector") == "global" and info.get("kind") == "G"
        ]
        self.assertEqual(global_g_aliases, ["multcase_G_combined_N1_N1"])
        self.assertEqual(alias_model["template_payload"]["G_full"][0][0], "multcase_G_combined_N1_N1")
        self.assertEqual(alias_model["template_payload"]["G_full"][1][1], "multcase_G_combined_N1_N1")
        self.assertEqual(alias_model["aliases"]["multcase_G_combined_N1_N1"]["used_by"], ["G_full[0][0]", "G_full[1][1]"])

    def test_general_symmetric_3x3_gkk_uses_fast_inverse(self):
        def payload(diagonal: str, offdiag: str) -> dict:
            deps = _deps("P", "Q", "D0", "D1", "C0", "C1", code=("P", "Q", "D0", "D1", "C0", "C1"))
            return {
                "all_nodes": ["A", "B", "K1", "K2", "K3"],
                "external_nodes": ["A", "B"],
                "internal_nodes": ["K1", "K2", "K3"],
                "ground_nodes": [],
                "node_display_names": {"A": "A", "B": "B", "K1": "K1", "K2": "K2", "K3": "K3"},
                "G_full": [
                    ["P", "0", "-P", "0", "0"],
                    ["0", "Q", "0", "-Q", "0"],
                    ["-P", "0", diagonal, offdiag, offdiag],
                    ["0", "-Q", offdiag, diagonal, offdiag],
                    ["0", "0", offdiag, offdiag, diagonal],
                ],
                "G_full_tagged": [
                    ["P", "0", "-P", "0", "0"],
                    ["0", "Q", "0", "-Q", "0"],
                    ["-P", "0", diagonal, offdiag, offdiag],
                    ["0", "-Q", offdiag, diagonal, offdiag],
                    ["0", "0", offdiag, offdiag, diagonal],
                ],
                "Ihis_full": ["0", "0", "0", "0", "0"],
                "Ihis_full_tagged": ["0", "0", "0", "0", "0"],
                "direct_retained_stamps": [],
                "symbol_dependency_table": deps,
                "symbol_dependency_table_tagged": deps,
            }

        response = build_multi_case_response(
            {
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": [
                    {"name": "case 0", "case_map": {"R1": 0}, "payload": payload("D0", "C0")},
                    {"name": "case 1", "case_map": {"R1": 1}, "payload": payload("D1", "C1")},
                ],
            }
        )

        draft = response["multi_case"]["c_draft"]
        self.assertEqual(response["multi_case"]["block_type"], "general")
        self.assertIn("mat_3x3_sym_inv_code", draft)
        self.assertNotIn("MATH_matx_invert(NK", draft)

    def test_mixed_case_diagonal_gkk_uses_conditional_diagonal_w_fast_path(self):
        def payload(diagonal: str, offdiag: str) -> dict:
            deps = _deps("P", "Q", "D0", "D1", "C1", code=("P", "Q", "D0", "D1", "C1"))
            return {
                "all_nodes": ["A", "B", "K1", "K2", "K3"],
                "external_nodes": ["A", "B"],
                "internal_nodes": ["K1", "K2", "K3"],
                "ground_nodes": [],
                "node_display_names": {"A": "A", "B": "B", "K1": "K1", "K2": "K2", "K3": "K3"},
                "G_full": [
                    ["P", "0", "-P", "0", "0"],
                    ["0", "Q", "0", "-Q", "0"],
                    ["-P", "0", diagonal, offdiag, offdiag],
                    ["0", "-Q", offdiag, diagonal, offdiag],
                    ["0", "0", offdiag, offdiag, diagonal],
                ],
                "G_full_tagged": [
                    ["P", "0", "-P", "0", "0"],
                    ["0", "Q", "0", "-Q", "0"],
                    ["-P", "0", diagonal, offdiag, offdiag],
                    ["0", "-Q", offdiag, diagonal, offdiag],
                    ["0", "0", offdiag, offdiag, diagonal],
                ],
                "Ihis_full": ["0", "0", "0", "0", "0"],
                "Ihis_full_tagged": ["0", "0", "0", "0", "0"],
                "direct_retained_stamps": [],
                "symbol_dependency_table": deps,
                "symbol_dependency_table_tagged": deps,
            }

        response = build_multi_case_response(
            {
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "case_profiles": [
                    {"name": "diagonal", "case_map": {"R1": 0}, "payload": payload("D0", "0")},
                    {"name": "dense", "case_map": {"R1": 1}, "payload": payload("D1", "C1")},
                ],
            }
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn("Case-resolved diagonal Gkk fast path", draft)
        self.assertIn("case 0:", draft)
        self.assertIn("set_CODE(&W_code, 0, 0, 1.0 / get_CODE(&Gkk_code, 0, 0));", draft)
        self.assertIn("set_CODE(&W_code, 0, 1, 0.0);", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("mat_3x3_sym_inv_code", draft)
        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);", draft)

    def test_same_branch_code_aliases_share_one_local_case_switch(self):
        aliases = {
            "multcase_G_C1_A_A": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {"0": "AA + G22 + G_rc + w2", "1": "Dabc + G22 + G_rc + w2"},
            },
            "multcase_G_C1_B_B": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {"0": "BB + G22 + G_rc + w2", "1": "Dabc + G22 + G_rc + w2"},
            },
            "multcase_G_C1_C_C": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {"0": "CC + G22 + G_rc + w2", "1": "Dabc + G22 + G_rc + w2"},
            },
        }

        lines = "\n".join(_alias_assignment_lines(aliases, "CODE"))

        self.assertEqual(lines.count("switch (C1_case_id)"), 1)
        self.assertIn("case 0:\n        multcase_G_C1_A_A = AA + G22 + G_rc + w2;\n        multcase_G_C1_B_B = BB + G22 + G_rc + w2;\n        multcase_G_C1_C_C = CC + G22 + G_rc + w2;", lines)
        self.assertIn("case 1:\n        multcase_G_C1_A_A = Dabc + G22 + G_rc + w2;\n        multcase_G_C1_B_B = Dabc + G22 + G_rc + w2;\n        multcase_G_C1_C_C = Dabc + G22 + G_rc + w2;", lines)

    def test_source_level_code_alias_assignments_use_budgeted_cse(self):
        aliases = {
            "multcase_G_C1_A_RC": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {
                    "0": "G12*Grc/(AA + G22 + Grc + w2 + PP + PN) + AP*G12/(AA + G22 + Grc + w2 + PP + PN)",
                    "1": "G12*Grc/(Dabc + G22 + Grc + w2 + PP + PN) + AP*G12/(Dabc + G22 + Grc + w2 + PP + PN)",
                },
            },
            "multcase_G_C1_B_RC": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {
                    "0": "-G12*Grc/(AA + G22 + Grc + w2 + PP + PN) + BP*G12/(AA + G22 + Grc + w2 + PP + PN)",
                    "1": "-G12*Grc/(Dabc + G22 + Grc + w2 + PP + PN) + BP*G12/(Dabc + G22 + Grc + w2 + PP + PN)",
                },
            },
            "multcase_G_C1_C_RC": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {
                    "0": "CP*G12/(AA + G22 + Grc + w2 + PP + PN)",
                    "1": "CP*G12/(Dabc + G22 + Grc + w2 + PP + PN)",
                },
            },
        }

        lines = "\n".join(_alias_assignment_lines(aliases, "CODE"))

        self.assertIn("double sourceG_C1_case0_tmp", lines)
        self.assertIn("double sourceG_C1_case1_tmp", lines)
        self.assertLess(lines.count("AA + G22 + Grc + PN + PP + w2"), 3)
        self.assertLess(lines.count("Dabc + G22 + Grc + PN + PP + w2"), 3)
        self.assertIn("multcase_G_C1_B_RC = ", lines)

    def test_source_level_final_gvalue_writes_use_budgeted_cse(self):
        entry_plans = [
            {
                "row": 0,
                "col": 0,
                "var": "varG_A_A",
                "code_cases": [
                    {
                        "index": 0,
                        "expr": sp.sympify("G12*Grc/(AA + G22 + Grc + w2 + PP + PN) + AP*G12/(AA + G22 + Grc + w2 + PP + PN)"),
                    }
                ],
            },
            {
                "row": 0,
                "col": 1,
                "var": "varG_A_B",
                "code_cases": [
                    {
                        "index": 0,
                        "expr": sp.sympify("-G12*Grc/(AA + G22 + Grc + w2 + PP + PN) + BP*G12/(AA + G22 + Grc + w2 + PP + PN)"),
                    }
                ],
            },
            {
                "row": 1,
                "col": 1,
                "var": "varG_B_B",
                "code_cases": [
                    {
                        "index": 0,
                        "expr": sp.sympify("CP*G12/(AA + G22 + Grc + w2 + PP + PN)"),
                    }
                ],
            },
        ]

        lines = "\n".join(optimized_api._final_g_code_case_lines(
            case_id_symbol="case_id",
            entry_plans=entry_plans,
            matrix_name=None,
        ))

        self.assertIn("double sourceG_case_id_case0_tmp", lines)
        self.assertLess(lines.count("AA + G22 + Grc + PN + PP + w2"), 3)
        self.assertIn("varG_A_B = ", lines)

    def test_source_level_ram_stamp_writes_use_budgeted_cse(self):
        shared_den = "AA + G22 + Grc + w2 + PP + PN"
        entry_plans = [
            {
                "row": 0,
                "col": 0,
                "var": "varG_A_A",
                "ram_cases": [
                    {
                        "index": 0,
                        "expr": sp.sympify(f"G12*Grc/({shared_den}) + AP*G12/({shared_den})"),
                    }
                ],
                "code_cases": [],
            },
            {
                "row": 0,
                "col": 1,
                "var": "varG_A_B",
                "ram_cases": [
                    {
                        "index": 0,
                        "expr": sp.sympify(f"-G12*Grc/({shared_den}) + BP*G12/({shared_den})"),
                    }
                ],
                "code_cases": [],
            },
            {
                "row": 1,
                "col": 1,
                "var": "varG_B_B",
                "ram_cases": [
                    {
                        "index": 0,
                        "expr": sp.sympify(f"CP*G12/({shared_den})"),
                    }
                ],
                "code_cases": [],
            },
        ]

        lines = "\n".join(optimized_api._conditional_ram_stamp_block(
            case_id_symbol="case_id",
            profiles=[{"name": "case 0"}],
            external_nodes=["A", "B"],
            entry_plans=entry_plans,
        ))

        self.assertIn("double sourceG_case_id_case0_tmp", lines)
        self.assertLess(lines.count("AA + G22 + Grc + PN + PP + w2"), 3)
        self.assertRegex(lines, r"g_mat_over\[0\]\[1\] = .*sourceG_case_id_case0_tmp")

    def test_trf_ctest_ram_temps_are_used_by_ram_stamp(self):
        fixture = Path("exports/Trf_Ctest.json")
        if not fixture.exists():
            self.skipTest("exports/Trf_Ctest.json is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        caches = data.get("multiCaseExportCache") or []
        self.assertTrue(caches, "Trf_Ctest.json should carry multi-case export caches")
        saw_case_invariant_ram_overlay = False
        for cache_index, cache in enumerate(caches):
            with self.subTest(cache_index=cache_index):
                payload = json.loads(cache["key"])
                payload["mode"] = "multi_case_c_export"

                response = build_multi_case_response(payload)
                draft = response["multi_case"]["c_draft"]
                ram_section = draft.split("RAM_PASS1:", 1)[1].split("GVALUES:", 1)[0]

                case_id_symbol = payload.get("case_id_symbol") or "case_id"
                case_scope = re.escape(str(case_id_symbol))
                ram_temp_names = set(re.findall(rf"\bdouble\s+(sourceG_{case_scope}_\w+)\s*=", ram_section))
                ram_alias_names = set(re.findall(r"\b(multcase_G_\w+)\s*=", ram_section))

                for name in sorted(ram_temp_names | ram_alias_names):
                    usage_lines = [
                        line for line in ram_section.splitlines()
                        if re.search(rf"\b{re.escape(name)}\b", line)
                        and not re.match(rf"\s*(?:double\s+)?{re.escape(name)}\s*=", line)
                    ]
                    self.assertTrue(usage_lines, f"{name} is computed in RAM but never used")

                if "setupGMatrix" not in ram_section:
                    continue

                self.assertIn(
                    "multcase_G_C1_N1_N1 =",
                    ram_section,
                    "Every Trf_Ctest RAM stamp cache must resolve complex source entries into reusable aliases first.",
                )
                self.assertIn("g_mat_over[0][0] = multcase_G_C1_N1_N1;", ram_section)
                self.assertIn("g_mat_over[0][1] = -multcase_G_C1_N1_N2;", ram_section)
                self.assertIn("g_mat_over[3][3] = multcase_G_C1_N4_N4;", ram_section)
                if "Case-invariant RAM final-G stamp after multi-case aliases are resolved." in ram_section:
                    saw_case_invariant_ram_overlay = True
                    self.assertNotIn(
                        "Case-conditional RAM final-G stamp",
                        ram_section,
                        "Identical RAM overlays should not be duplicated once every entry is expressed through multcase aliases.",
                    )
                    self.assertEqual(
                        ram_section.count("setupGMatrix(4);"),
                        1,
                        "The identical 4-node RAM overlay should be registered once, not once per case.",
                    )
                self.assertNotRegex(
                    ram_section,
                    r"g_mat_over\[[^\n]+sourceG_C1_case\d+_tmp",
                    "Trf_Ctest RAM stamp must use source-level aliases instead of re-expanded per-case temps.",
                )
        self.assertTrue(
            saw_case_invariant_ram_overlay,
            "At least one Trf_Ctest cache should collapse identical per-case RAM overlays into one case-invariant stamp.",
        )

    def test_trf_ctest_reuses_ram_safe_source_temps_between_g_and_ihis(self):
        fixture = Path("exports/Trf_Ctest.json")
        if not fixture.exists():
            self.skipTest("exports/Trf_Ctest.json is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        caches = data.get("multiCaseExportCache") or []
        self.assertTrue(caches, "Trf_Ctest.json should carry multi-case export caches")

        saw_shared_source_temp = False
        for cache_index, cache in enumerate(caches):
            with self.subTest(cache_index=cache_index):
                payload = json.loads(cache["key"])
                payload["mode"] = "multi_case_c_export"

                response = build_multi_case_response(payload)
                draft = response["multi_case"]["c_draft"]
                if "1.0/(G11 + Gc)" not in draft:
                    continue

                saw_shared_source_temp = True
                self.assertIn(
                    "sourceGI_C1_case1_tmp0",
                    draft,
                    "RAM-safe source CSE shared by G and Ihis should be lifted to a persistent sourceGI temp.",
                )
                self.assertNotRegex(
                    draft,
                    r"CODE:[\s\S]*double\s+sourceG_C1_case1_tmp0\s*=\s*1\.0/\(G11 \+ Gc\)",
                    "CODE Ihis should reuse the RAM-safe sourceGI temp instead of redeclaring a local sourceG temp.",
                )
                self.assertNotIn(
                    "double sourceG_case_id_case1_tmp0 = 1.0/(G11 + Gc);",
                    draft,
                    "RAM G should use the lifted sourceGI temp instead of keeping a local sourceG temp.",
                )

        self.assertTrue(
            saw_shared_source_temp,
            "Trf_Ctest should include at least one case where G and Ihis share a RAM-safe source CSE.",
        )

    def test_mult_case_test_fixture_uses_alias_template_not_single_profile_fallback(self):
        data = json.loads(Path("exports/mult_case_test.json").read_text(encoding="utf-8"))
        branches = {branch["id"]: branch for branch in data["branches"]}
        self.assertEqual([case.get("g") for case in branches["B11"].get("switchCases", [])], ["G1", "G2"])
        self.assertEqual([case.get("g") for case in branches["B12"].get("switchCases", [])], ["G3", "G4"])

        request = _request(
            [
                {
                    "name": "Combination 1",
                    "comment": "R1 = Case 1; R2 = Case 1",
                    "case_map": {"B11": 0, "B12": 0},
                    "payload": _direct_series_payload("G1", "G3"),
                },
                {
                    "name": "Combination 2",
                    "comment": "R1 = Case 2; R2 = Case 1",
                    "case_map": {"B11": 1, "B12": 0},
                    "payload": _direct_series_payload("G2", "G3"),
                },
                {
                    "name": "Combination 3",
                    "comment": "R1 = Case 1; R2 = Case 2",
                    "case_map": {"B11": 0, "B12": 1},
                    "payload": _direct_series_payload("G1", "G4"),
                },
                {
                    "name": "Combination 4",
                    "comment": "R1 = Case 2; R2 = Case 2",
                    "case_map": {"B11": 1, "B12": 1},
                    "payload": _direct_series_payload("G2", "G4"),
                },
            ],
            deps=_deps("G1", "G2", "G3", "G4"),
            case_id="case_id",
        )

        response = build_multi_case_response(request)
        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertEqual(multi["codegen_mode"], "case-agnostic alias template")
        self.assertEqual(multi["profile_count"], 4)
        self.assertNotIn("RAM-switch multi-case C draft", draft)
        self.assertNotIn("NCASE = 1", draft)
        self.assertIn("multcase_G_B11_N1_N1 = G1;", draft)
        self.assertIn("multcase_G_B11_N1_N1 = G2;", draft)
        self.assertIn("multcase_G_B12_N2_N3 = G3;", draft)
        self.assertIn("multcase_G_B12_N2_N3 = G4;", draft)
        self.assertIn("switch (case_id)", draft)
        self.assertIn("case 3:", draft)
        self.assertIn("g_mat_over[0][0] = multcase_G_B11_N1_N1;", draft)
        self.assertIn("g_mat_over[1][2] = -multcase_G_B12_N2_N3;", draft)
        self.assertNotIn("G2 - G1", draft)
        self.assertNotIn("G4 - G3", draft)

    def test_zero_and_nonzero_cases_keep_template_entry(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 zero", "case_map": {"R1": 0}, "payload": _series_payload("0", internal=False)},
                    {"name": "R1 nonzero", "case_map": {"R1": 1}, "payload": _series_payload("X", internal=False)},
                ],
                deps=_deps("X"),
            )
        )

        draft = response["multi_case"]["c_draft"]
        self.assertIn("multcase_G_R1_A_A = 0.0;", draft)
        self.assertIn("multcase_G_R1_A_A = X;", draft)
        self.assertIn("case 0:", draft)
        self.assertIn("g_mat_over[0][0] = multcase_G_R1_A_A;", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("g_mat_over[0][1] = -multcase_G_R1_A_A;", draft)

    def test_rejects_case_that_changes_topology(self):
        bad_payload = _series_payload("X", internal=False)
        bad_payload["all_nodes"] = ["A", "C"]
        bad_payload["external_nodes"] = ["A", "C"]
        with self.assertRaisesRegex(ValueError, "topology"):
            build_multi_case_response(
                _request(
                    [
                        {"name": "ok", "case_map": {"R1": 0}, "payload": _series_payload("X", internal=False)},
                        {"name": "bad", "case_map": {"R1": 1}, "payload": bad_payload},
                    ],
                    deps=_deps("X"),
                )
            )

    def test_alias_template_matches_single_case_reduction_after_substitution(self):
        response = build_multi_case_response(
            _request(
                [
                    {"name": "R1 X", "case_map": {"R1": 0}, "payload": _series_payload("X")},
                    {"name": "R1 XY", "case_map": {"R1": 1}, "payload": _series_payload("X + Y")},
                ],
                deps=_deps("X", "Y", "G2"),
            )
        )
        template_gred = sp.Matrix(response["multi_case"]["template"]["Gred"])
        alias = sp.Symbol("multcase_G_R1_A_A")

        for expr in [sp.Symbol("X"), sp.Symbol("X") + sp.Symbol("Y")]:
            single_payload = _series_payload(str(expr))
            single_g = sp.Matrix([[sp.sympify(item) for item in row] for row in single_payload["G_full"]])
            single_ihis = sp.Matrix([[sp.sympify(item)] for item in single_payload["Ihis_full"]])
            single_gred = eliminate_internal_nodes(
                single_g,
                single_ihis,
                single_payload["all_nodes"],
                single_payload["external_nodes"],
            ).G_red
            substituted = template_gred.xreplace({alias: expr})
            self.assertEqual(sp.simplify(substituted - single_gred), sp.zeros(2, 2))


if __name__ == "__main__":
    unittest.main()
