import unittest
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_saved_string_matrix_fields_are_parsed_as_matrices(self):
        matrix = optimized_api._matrix_from_clean("[[G11, -G12], [G12, G22]]")
        self.assertEqual(matrix.shape, (2, 2))
        self.assertEqual(matrix[0, 0], sp.Symbol("G11"))
        self.assertEqual(matrix[0, 1], -sp.Symbol("G12"))

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
        self.assertIn("set(&Grk_code, 0, 0, Grk_A_X);", draft)
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
        self.assertIn("Case-resolved Gkk inverse", draft)
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

        source_temps = optimized_api._alias_source_temp_plan(
            aliases,
            "CODE",
            "C1_case_id",
            base_source_temps=None,
            symbol_table={},
            require_ram_safe=False,
        )
        source_lines = "\n".join(optimized_api._shared_source_temp_assignment_lines(source_temps))
        alias_lines = "\n".join(
            _alias_assignment_lines(aliases, "CODE", "C1_case_id", shared_source_temps=source_temps)
        )

        self.assertIn("sourceCodeG_C1_case0_tmp", source_lines)
        self.assertIn("sourceCodeG_C1_case1_tmp", source_lines)
        self.assertNotIn("double sourceCodeG_C1_case0_tmp", alias_lines)
        self.assertNotIn("double sourceCodeG_C1_case1_tmp", alias_lines)
        self.assertIn("sourceCodeG_C1_case0_tmp", alias_lines)
        self.assertIn("sourceCodeG_C1_case1_tmp", alias_lines)
        self.assertIn("multcase_G_C1_B_RC = ", alias_lines)

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

    def test_conditional_ram_stamp_collapses_identical_alias_overlay(self):
        entry_plans = [
            {
                "row": 0,
                "col": 0,
                "var": "varG_A_A",
                "template_expr": sp.Symbol("multcase_G_C1_N1_N1"),
                "ram_cases": [
                    {"index": 0, "expr": sp.sympify("(G11*G22 + G11*Gc)/(G11 + Gc)")},
                    {"index": 1, "expr": sp.sympify("(G11*G22 + G22*Gc)/(G22 + Gc)")},
                ],
                "code_cases": [],
            },
            {
                "row": 0,
                "col": 1,
                "var": "varG_A_B",
                "template_expr": -sp.Symbol("multcase_G_C1_N1_N2"),
                "ram_cases": [
                    {"index": 0, "expr": -sp.sympify("(G12*Gc)/(G11 + Gc)")},
                    {"index": 1, "expr": -sp.sympify("(G12*Gc)/(G22 + Gc)")},
                ],
                "code_cases": [],
            },
        ]

        lines = "\n".join(optimized_api._conditional_ram_stamp_block(
            case_id_symbol="case_id",
            profiles=[{"name": "case 0"}, {"name": "case 1"}],
            external_nodes=["A", "B"],
            entry_plans=entry_plans,
            aliases={
                "multcase_G_C1_N1_N1": {"branch_id": "C1", "owner": "RAM", "kind": "G"},
                "multcase_G_C1_N1_N2": {"branch_id": "C1", "owner": "RAM", "kind": "G"},
            },
        ))

        self.assertIn("Case-invariant RAM final-G stamp after multi-case aliases are resolved.", lines)
        self.assertNotIn("Case-conditional RAM final-G stamp", lines)
        self.assertEqual(lines.count("setupGMatrix(2);"), 1)
        self.assertIn("g_mat_over[0][0] = multcase_G_C1_N1_N1;", lines)
        self.assertIn("g_mat_over[0][1] = -multcase_G_C1_N1_N2;", lines)

    def test_trf_ctest_ram_temps_are_used_by_ram_stamp(self):
        fixture = Path("exports/Trf_Ctest.json")
        if not fixture.exists():
            self.skipTest("exports/Trf_Ctest.json is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        caches = data.get("multiCaseExportCache") or []
        if not caches:
            self.skipTest("exports/Trf_Ctest.json does not currently carry multi-case export caches")
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
                if "No RAM-side G entries: no fixed G overlay is registered." in ram_section:
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
            saw_case_invariant_ram_overlay or all(
                "No RAM-side G entries: no fixed G overlay is registered."
                in build_multi_case_response({**json.loads(cache["key"]), "mode": "multi_case_c_export"})["multi_case"]["c_draft"]
                for cache in caches
            ),
            "Trf_Ctest should either have no RAM overlay or collapse identical per-case RAM overlays into one case-invariant stamp.",
        )

    def test_trf_ctest_reuses_ram_safe_source_temps_between_g_and_ihis(self):
        fixture = Path("exports/Trf_Ctest.json")
        if not fixture.exists():
            self.skipTest("exports/Trf_Ctest.json is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        caches = data.get("multiCaseExportCache") or []
        if not caches:
            self.skipTest("exports/Trf_Ctest.json does not currently carry multi-case export caches")

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

    def test_trf_ctest_shared_sourcegi_subexpressions_are_not_redeclared_as_sourceg(self):
        G11, G12, G22, Gc = sp.symbols("G11 G12 G22 Gc")
        shared_plan = {
            "C1": {
                0: [
                    ("sourceGI_C1_case0_tmp4", G12**2),
                    ("sourceGI_C1_case0_tmp9", 1 / (G11 * G22 + G11 * Gc - G12**2)),
                    ("sourceGI_C1_case0_tmp10", G12**2 / (G11 * G22 + G11 * Gc - G12**2)),
                ],
            },
        }
        draft = (
            "RAM_PASS1:\n"
            "    sourceGI_C1_case0_tmp4 = pow(G12, 2.0);\n"
            "    sourceGI_C1_case0_tmp9 = 1.0/(G11*G22 + G11*Gc - sourceGI_C1_case0_tmp4);\n"
            "    sourceGI_C1_case0_tmp10 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;\n"
            "    switch (C1_case_id) {\n"
            "    case 0:\n"
            "        double sourceG_C1_case0_tmp0 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;\n"
            "        multcase_G_C1_N1_N1 = sourceG_C1_case0_tmp0 + G22;\n"
            "        break;\n"
            "    default:\n"
            "        double sourceG_C1_default_tmp0 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;\n"
            "        multcase_G_C1_N1_N1 = sourceG_C1_default_tmp0 + G22;\n"
            "        break;\n"
            "    }\n"
        )

        rewritten = optimized_api._apply_shared_source_temp_text_reuse(draft, shared_plan)

        self.assertIn(
            "sourceGI_C1_case0_tmp10 = sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9;",
            rewritten,
        )
        self.assertNotIn("double sourceG_C1_case0_tmp0", rewritten)
        self.assertNotIn("double sourceG_C1_default_tmp0", rewritten)
        self.assertIn("multcase_G_C1_N1_N1 = sourceGI_C1_case0_tmp10 + G22;", rewritten)
        self.assertEqual(
            rewritten.count("sourceGI_C1_case0_tmp4*sourceGI_C1_case0_tmp9"),
            1,
            "The product should be computed once as sourceGI, not redeclared as sourceG.",
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

    def test_pack_cases_with_same_final_ports_but_different_internal_recovery_prefer_gkk_template(self):
        def with_final(payload: dict, *, g: str, internal: list[str], k_v: list[list[str]], k_h: list[str]) -> dict:
            clone = json.loads(json.dumps(payload))
            clone.update({
                "finalExternalGroups": [
                    {"display": "A", "globalNet": "A"},
                    {"display": "B", "globalNet": "B"},
                ],
                "finalInternalGroups": [
                    {"display": node, "globalNet": node}
                    for node in internal
                ],
                "finalGMatrix": [[g, f"-({g})"], [f"-({g})", g]],
                "finalIhisVector": ["0", "0"],
                "finalK_v": k_v,
                "finalK_h": k_h,
            })
            return clone

        no_internal = with_final(
            _series_payload("G0", internal=False),
            g="G0",
            internal=[],
            k_v=[],
            k_h=[],
        )
        with_internal = with_final(
            _series_payload("G1", internal=True),
            g="G1",
            internal=["X"],
            k_v=[["1", "0"]],
            k_h=["Ihis_x"],
        )
        response = build_multi_case_response(
            _request(
                [
                    {"name": "Pack case 0", "case_map": {"Pack": 0}, "payload": no_internal},
                    {"name": "Pack case 1", "case_map": {"Pack": 1}, "payload": with_internal},
                ],
                deps=_deps("G0", "G1", step=("Ihis_x",)),
                case_id="case_id",
            )
        )

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertIn("multcase_G_Pack_A_A = G0;", draft)
        self.assertIn("multcase_G_Pack_A_A = G1;", draft)
        self.assertIn("INTERNAL_NODES = 1", draft)
        self.assertIn("W_code", draft)
        self.assertIn("Gkr_code", draft)
        self.assertIn('getNodeNum(comp, "A")', draft)
        self.assertNotIn('getNodeNum(comp, "X")', draft)
        self.assertIn("T1_T2:", draft)
        self.assertIn("void network_node_recover_vk_from_wgkr_only", draft)
        self.assertIn("network_node_recover_vk_from_wgkr_only(RETAINED_NODES, internal_active", draft)
        code_functions = draft.split("CODE_FUNCTIONS:", 1)[1].split("CODE:", 1)[0]
        self.assertNotIn("void network_node_recover_vk_diag", code_functions)
        self.assertNotIn("void network_node_recover_vk_matrix", code_functions)
        self.assertIn("case 1:", draft)
        self.assertIn("X = get_CODE(&Vk_code, 0, 0);", draft)
        self.assertNotIn("X = (G1/(G1 + G2))*A + (G2/(G1 + G2))*B;", draft)
        self.assertNotIn("no eliminated internal nodes", draft)
        self.assertNotIn("No internal nodes were eliminated, so there is no Vk recovery step.", draft)

    def test_trf_ctest_dummy_fixture_uses_final_ports_with_case_specific_recovery(self):
        fixture = Path("exports/Trf_Ctest_dummy.json")
        if not fixture.exists():
            self.skipTest("Trf_Ctest_dummy.json fixture is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        network_cases = data["branches"][0]["packageOriginal"]["networkCases"]
        def payload_from_saved_case(case: dict, package_branch_id: str) -> dict:
            final_external = [
                str(group.get("display") or group.get("globalNet") or group.get("id") or f"N{index + 1}")
                for index, group in enumerate(case.get("finalExternalGroups") or [])
            ]
            final_internal = [
                f"pkg-internal:{package_branch_id}:{index}"
                for index, group in enumerate(case.get("finalInternalGroups") or [])
            ]
            display_names = {}
            for node, group in zip(final_external, case.get("finalExternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            for node, group in zip(final_internal, case.get("finalInternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            all_nodes = [*final_external, *final_internal]
            node_index = {node: index for index, node in enumerate(all_nodes)}
            terminal_node: dict[str, str] = {}
            for node, group in [
                *zip(final_external, case.get("finalExternalGroups") or []),
                *zip(final_internal, case.get("finalInternalGroups") or []),
            ]:
                for member in group.get("members") or []:
                    terminal_node[str(member)] = node
            G_full = sp.zeros(len(all_nodes), len(all_nodes))
            Ihis_full = sp.zeros(len(all_nodes), 1)

            def add_entry(row_node: str | None, col_node: str | None, expr: object) -> None:
                if row_node not in node_index or col_node not in node_index:
                    return
                G_full[node_index[row_node], node_index[col_node]] += optimized_api._parse_expr(expr)

            def add_ihis(row_node: str | None, expr: object) -> None:
                if row_node not in node_index:
                    return
                Ihis_full[node_index[row_node], 0] += optimized_api._parse_expr(expr)

            for branch in case.get("branches") or []:
                branch_id = str(branch.get("id") or "")
                if branch.get("kind") == "single_phase_transformer":
                    sides = ["P", "N", "S", "T"]
                    local_g = optimized_api._matrix_from_clean(branch.get("gMatrix") or [])
                    local_ihis = optimized_api._matrix_from_clean(branch.get("ihisVector") or [])
                    for row, row_side in enumerate(sides):
                        row_node = terminal_node.get(f"{branch_id}.{row_side}")
                        add_ihis(row_node, local_ihis[row, 0])
                        for col, col_side in enumerate(sides):
                            col_node = terminal_node.get(f"{branch_id}.{col_side}")
                            add_entry(row_node, col_node, local_g[row, col])
                elif branch.get("kind") == "two_node_branch":
                    node_a = terminal_node.get(f"{branch_id}.A")
                    node_b = terminal_node.get(f"{branch_id}.B")
                    g = optimized_api._parse_expr(branch.get("g") or "0")
                    ihis = optimized_api._parse_expr(branch.get("ihis") or "0")
                    add_entry(node_a, node_a, g)
                    add_entry(node_a, node_b, -g)
                    add_entry(node_b, node_a, -g)
                    add_entry(node_b, node_b, g)
                    add_ihis(node_a, ihis)
                    add_ihis(node_b, -ihis)

            return {
                "all_nodes": all_nodes,
                "external_nodes": list(final_external),
                "internal_nodes": list(final_internal),
                "ground_nodes": [],
                "node_display_names": display_names,
                "G_full": optimized_api._clean_matrix(G_full),
                "G_full_tagged": optimized_api._clean_matrix(G_full),
                "Ihis_full": optimized_api._clean_vector(Ihis_full),
                "Ihis_full_tagged": optimized_api._clean_vector(Ihis_full),
                "direct_retained_stamps": [],
                "finalExternalGroups": case.get("finalExternalGroups"),
                "finalInternalGroups": case.get("finalInternalGroups"),
                "finalGMatrix": case.get("finalGMatrix"),
                "finalIhisVector": case.get("finalIhisVector"),
                "finalK_v": case.get("finalK_v"),
                "finalK_h": case.get("finalK_h"),
            }

        profiles = [
            {
                "case_id": index,
                "name": case.get("name") or f"case {index}",
                "case_map": {"C1": index},
                "payload": payload_from_saved_case(case, data["branches"][0].get("id") or "C1"),
            }
            for index, case in enumerate(network_cases)
        ]
        request = _request(
            profiles,
            deps=_deps("G11", "G12", "G22", "Gc", step=("Ihis_p", "Ihis_s", "IhisC1", "IhisC2")),
            case_id="case_id",
        )
        request["elimination_codegen_mode"] = "prefer_matrix"
        response = build_multi_case_response(request)

        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertIn("INTERNAL_NODES = 2", draft)
        self.assertIn("internal_active", draft)
        zero_profile = re.search(r"INTERNAL_NODES_CASE_(\d+) = 0", draft)
        self.assertIsNotNone(zero_profile)
        zero_profile_index = zero_profile.group(1)
        self.assertIn(f"int internal_active = INTERNAL_NODES_CASE_{zero_profile_index};", draft)
        self.assertIn(f"internal_profile = INTERNAL_CASE_{zero_profile_index};", draft)
        self.assertIn(
            "if (internal_active > 0) {\n"
            "        err += matrixDim(&Grk_code, RETAINED_NODES, internal_active);",
            draft,
        )
        self.assertIn("        err += matrixDim(&Gkk_code, internal_active, internal_active);", draft)
        self.assertIn("        err += matrixDim(&W_code, internal_active, internal_active);", draft)
        self.assertIn("        err += matrixDim(&Gkr_code, internal_active, RETAINED_NODES);", draft)
        self.assertIn("        err += matrixDim(&Vk_code, internal_active, 1);", draft)
        self.assertIn(
            "if (internal_active > 0) {\n"
            "        matrix_register(&Grk_code);",
            draft,
        )
        self.assertIn(
            "if (internal_active > 0) {\n"
            "            conditionMatrixForCODE(&Grk_code);",
            draft,
        )
        self.assertIn("W_code", draft)
        self.assertIn("Gkr_code", draft)
        self.assertNotIn("sourceG_tmp", draft)
        self.assertIn("RAM-side matrix Schur precompute for fixed Gred stamp.", draft)
        self.assertIn("matrix_invert(&W_ram, &Gkk_ram);", draft)
        self.assertIn("matrix_subtract(&Gred_ram, &Grr_ram, &tmp_Grk_W_Gkr_ram);", draft)
        self.assertIn('getNodeNum(comp, "N1")', draft)
        self.assertIn('getNodeNum(comp, "N4")', draft)
        self.assertNotIn('getNodeNum(comp, "inner_left")', draft)
        self.assertNotIn('getNodeNum(comp, "inner_right")', draft)
        self.assertNotIn("no eliminated internal nodes", draft)
        self.assertIn("Case-specific voltage recovery", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("inner_left =", draft)
        self.assertIn("case 2:", draft)
        self.assertIn("inner_right =", draft)
        self.assertIn("Gkk_inner_left_inner_left", draft)
        self.assertIn("Gkk[inner_left,inner_left]", draft)
        self.assertNotIn("pkg_internal_C1_0", draft)
        self.assertNotIn("Gkk_pkg_internal_C1_0_pkg_internal_C1_0", draft)
        self.assertNotIn("Gkk_k1_k1", draft)
        self.assertNotIn("Gkk[k1,k1]", draft)
        self.assertIn("case 3:", draft)
        self.assertNotIn("No internal nodes were eliminated, so there is no Vk recovery step.", draft)
        t1_t2 = draft.split("T1_T2:", 1)[1]
        hoisted_recovery = t1_t2.split("Case-specific voltage recovery", 1)[0]
        case_recovery = t1_t2.split("Case-specific voltage recovery", 1)[1]
        self.assertIn("set_CODE(&Vr_code, 0, 0, N1);", hoisted_recovery)
        self.assertIn("set_CODE(&Vr_code, 3, 0, N4);", hoisted_recovery)
        self.assertNotIn("set_CODE(&Vr_code", case_recovery)
        self.assertIn("CODE_FUNCTIONS:", draft)
        self.assertLess(draft.index("CODE_FUNCTIONS:"), draft.index("CODE:"))
        code_functions = draft.split("CODE_FUNCTIONS:", 1)[1].split("CODE:", 1)[0]
        self.assertEqual(code_functions.count("void network_node_recover_vk_diag"), 1)
        self.assertEqual(code_functions.count("void network_node_recover_vk_matrix"), 1)
        self.assertNotIn("void network_node_recover_vk_from_wgkr_only", code_functions)
        self.assertIn("Preconditions: W is diagonal", code_functions)
        self.assertIn("Preconditions: W is fully populated and symmetric", code_functions)
        self.assertIn("for (k = 0; k < internal_count; k++)", code_functions)
        self.assertIn("matrix_matXvec_CODE(tmp_W_Gkr_Vr_code, tmp_W_Gkr_code, Vr_code);", code_functions)
        recovery_case1 = case_recovery.split("case 1:", 1)[1].split("case 2:", 1)[0]
        recovery_case2 = case_recovery.split("case 2:", 1)[1].split("case 3:", 1)[0]
        recovery_case3 = case_recovery.split("case 3:", 1)[1].split("default:", 1)[0]
        self.assertIn("network_node_recover_vk_diag(RETAINED_NODES, internal_active", recovery_case1)
        self.assertIn("network_node_recover_vk_diag(RETAINED_NODES, internal_active", recovery_case2)
        self.assertIn("network_node_recover_vk_matrix(RETAINED_NODES, internal_active", recovery_case3)
        self.assertNotIn("for (int k = 0; k < internal_active; k++)", case_recovery)
        self.assertNotIn("for (int k = 0; k < INTERNAL_NODES; k++)", case_recovery)
        self.assertNotIn("matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);", recovery_case1)
        self.assertNotIn("matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);", recovery_case2)
        self.assertNotIn("matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);", recovery_case3)
        self.assertIn("Case-resolved Gkk inverse over active internal profile", draft)
        w_switch = draft.split("Case-resolved Gkk inverse over active internal profile", 1)[1].split(
            "/* ************************************************************************\n     * CODE-SIDE IHIS VALUE SETUP",
            1,
        )[0]
        w_case0 = w_switch.split("case 0:", 1)[1].split("case 1:", 1)[0]
        w_case1_case2 = w_switch.split("case 1:", 1)[1].split("case 3:", 1)[0]
        w_case3 = w_switch.split("case 3:", 1)[1].split("default:", 1)[0]
        self.assertIn("This Pack case has no active internal nodes.", w_case0)
        self.assertNotIn("set_CODE(&W_code", w_case0)
        self.assertIn("case 2:", w_case1_case2)
        self.assertIn("set_CODE(&W_code, 0, 0, 1.0 / get_CODE(&Gkk_code, 0, 0));", w_case1_case2)
        self.assertNotIn("set_CODE(&W_code, 1, 1", w_case1_case2)
        self.assertIn("mat_2x2_sym_inv_code", w_case3)
        self.assertIn(
            "if (internal_active > 0) {\n"
            "        matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
            draft,
        )
        self.assertIn("No active internal nodes: Ihisred = Ihisr.", draft)

    def test_case_specific_recovery_reuses_template_vk_block_when_available(self):
        draft = """STATIC:

    double pkg_internal_C2_0 = 0.0;

T1_T2:
    /* Internal-node voltage recovery after solved retained-node voltages are available. */
    set_CODE(&Vr_code, 0, 0, N1);
    set_CODE(&Vr_code, 1, 0, N4);
    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);
    matrix_scalarMult_CODE(&Vk_code, &tmp_W_Gkr_Vr_code, -1.0);

    /* One variable per eliminated node, in effective k order. */
    N2 = get_CODE(&Vk_code, 0, 0);
"""

        updated = optimized_api._apply_final_retained_recovery_profiles_to_draft(
            draft,
            case_id_symbol="case_id",
            recovery_profiles=[
                {
                    "case_ids": [0],
                    "recovery_nodes": ["pkg_internal_C2_0"],
                    "super_nodes": ["N1", "N4"],
                    "K_v": [["1/2", "1/2"]],
                    "K_h": ["0"],
                },
                {
                    "case_ids": [1],
                    "recovery_nodes": [],
                    "super_nodes": ["N1", "N4"],
                    "K_v": [],
                    "K_h": [],
                },
            ],
            template_internal_nodes=["pkg_internal_C2_0"],
        )

        self.assertIn("case 0:", updated)
        self.assertIn("CODE_FUNCTIONS:", updated)
        self.assertIn("void network_node_recover_vk_from_wgkr_only", updated)
        self.assertIn("network_node_recover_vk_from_wgkr_only(RETAINED_NODES, INTERNAL_NODES", updated)
        code_functions = updated.split("CODE_FUNCTIONS:", 1)[1].split("T1_T2:", 1)[0]
        self.assertNotIn("void network_node_recover_vk_diag", code_functions)
        self.assertNotIn("void network_node_recover_vk_matrix", code_functions)
        self.assertIn("pkg_internal_C2_0 = get_CODE(&Vk_code, 0, 0);", updated)
        self.assertIn("case 1:", updated)
        case1_block = updated.split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertNotIn("matrix_matXvec_CODE", case1_block)
        self.assertNotIn("matrix_scalarMult_CODE", case1_block)
        self.assertNotIn("N2 = get_CODE(&Vk_code, 0, 0);", updated)
        self.assertNotIn("pkg_internal_C2_0 = (1.0/2.0)*N1 + (1.0/2.0)*N4;", updated)

    def test_trf_ctest_dummy_small_does_not_emit_unconditional_template_recovery(self):
        fixture = Path("exports/Trf_Ctest_dummy_small.json")
        if not fixture.exists():
            self.skipTest("Trf_Ctest_dummy_small.json fixture is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        branch = next(
            item for item in data.get("branches", [])
            if item.get("packageOriginal", {}).get("networkCases")
        )
        cases = branch["packageOriginal"]["networkCases"]

        def payload_from_saved_case(case: dict, package_branch_id: str) -> dict:
            final_external = [
                str(group.get("display") or group.get("globalNet") or group.get("id") or f"N{index + 1}")
                for index, group in enumerate(case.get("finalExternalGroups") or [])
            ]
            final_internal = [
                f"pkg-internal:{package_branch_id}:{index}"
                for index, group in enumerate(case.get("finalInternalGroups") or [])
            ]
            display_names = {}
            for node, group in zip(final_external, case.get("finalExternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            for node, group in zip(final_internal, case.get("finalInternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            all_nodes = [*final_external, *final_internal]
            node_index = {node: index for index, node in enumerate(all_nodes)}
            terminal_node: dict[str, str] = {}
            for node, group in [
                *zip(final_external, case.get("finalExternalGroups") or []),
                *zip(final_internal, case.get("finalInternalGroups") or []),
            ]:
                for member in group.get("members") or []:
                    terminal_node[str(member)] = node
            G_full = sp.zeros(len(all_nodes), len(all_nodes))
            Ihis_full = sp.zeros(len(all_nodes), 1)

            def add_entry(row_node: str | None, col_node: str | None, expr: object) -> None:
                if row_node not in node_index or col_node not in node_index:
                    return
                G_full[node_index[row_node], node_index[col_node]] += optimized_api._parse_expr(expr)

            def add_ihis(row_node: str | None, expr: object) -> None:
                if row_node not in node_index:
                    return
                Ihis_full[node_index[row_node], 0] += optimized_api._parse_expr(expr)

            for saved_branch in case.get("branches") or []:
                branch_id = str(saved_branch.get("id") or "")
                if saved_branch.get("kind") != "two_node_branch":
                    continue
                node_a = terminal_node.get(f"{branch_id}.A")
                node_b = terminal_node.get(f"{branch_id}.B")
                g = optimized_api._parse_expr(saved_branch.get("g") or "0")
                ihis = optimized_api._parse_expr(saved_branch.get("ihis") or "0")
                add_entry(node_a, node_a, g)
                add_entry(node_a, node_b, -g)
                add_entry(node_b, node_a, -g)
                add_entry(node_b, node_b, g)
                add_ihis(node_a, ihis)
                add_ihis(node_b, -ihis)

            return {
                "all_nodes": all_nodes,
                "external_nodes": list(final_external),
                "internal_nodes": list(final_internal),
                "ground_nodes": [],
                "node_display_names": display_names,
                "G_full": optimized_api._clean_matrix(G_full),
                "G_full_tagged": optimized_api._clean_matrix(G_full),
                "Ihis_full": optimized_api._clean_vector(Ihis_full),
                "Ihis_full_tagged": optimized_api._clean_vector(Ihis_full),
                "direct_retained_stamps": [],
                "finalExternalGroups": case.get("finalExternalGroups"),
                "finalInternalGroups": case.get("finalInternalGroups"),
                "finalGMatrix": case.get("finalGMatrix"),
                "finalIhisVector": case.get("finalIhisVector"),
                "finalK_v": case.get("finalK_v"),
                "finalK_h": case.get("finalK_h"),
            }

        profiles = [
            {
                "case_id": index,
                "name": case.get("name") or f"case {index}",
                "case_map": {branch.get("id") or "C2": index},
                "payload": payload_from_saved_case(case, branch.get("id") or "C2"),
            }
            for index, case in enumerate(cases)
        ]
        draft = build_multi_case_response(
            _request(profiles, deps=_deps("R"), case_id="case_id")
        )["multi_case"]["c_draft"]

        self.assertIn("Case-specific voltage recovery", draft)
        self.assertIn("switch (case_id)", draft)
        self.assertNotIn("C2_case_id", draft)
        self.assertNotIn("Decode the optional global case selector into per-element local cases", draft)
        self.assertIn("void network_node_recover_vk_from_wgkr_only", draft)
        self.assertIn("network_node_recover_vk_from_wgkr_only(RETAINED_NODES, internal_active", draft)
        code_functions = draft.split("CODE_FUNCTIONS:", 1)[1].split("CODE:", 1)[0]
        self.assertNotIn("void network_node_recover_vk_diag", code_functions)
        self.assertNotIn("void network_node_recover_vk_matrix", code_functions)
        self.assertIn("N2 = get_CODE(&Vk_code, 0, 0);", draft)
        self.assertIn("Recovery-only matrix setup is skipped", draft)
        recovery_switch = draft.split("Case-specific voltage recovery", 1)[1]
        case0_block = recovery_switch.split("case 0:", 1)[1].split("case 1:", 1)[0]
        case1_block = recovery_switch.split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertIn("network_node_recover_vk_from_wgkr_only(RETAINED_NODES, internal_active", case0_block)
        self.assertNotIn("matrix_matXvec_CODE", case1_block)
        self.assertNotIn("matrix_scalarMult_CODE", case1_block)
        setup_switch = draft.split("Recovery-only matrix setup is skipped", 1)[1].split("CODE:", 1)[0]
        setup_case0 = setup_switch.split("case 0:", 1)[1].split("case 1:", 1)[0]
        self.assertNotIn("case 1:", setup_switch)
        self.assertIn("matrixDim(&Gkr_code", setup_case0)
        self.assertIn("matrix_mult(&tmp_W_Gkr_code, &W_code, &Gkr_code);", setup_case0)
        self.assertIn("matrix_register(&tmp_W_Gkr_Vr_code);", setup_case0)
        code_condition = draft.split("Recovery-only MATRIX_ conditioning", 1)[1].split("Node injection currents", 1)[0]
        self.assertIn("if (case_id == 0)", code_condition)
        self.assertIn("conditionMatrixForCODE(&Vr_code);", code_condition)
        self.assertNotIn("pkg_internal_C2_0 = get_CODE(&Vk_code, 0, 0);", draft)
        self.assertNotIn("pkg_internal_C2_0 = (1.0/2.0)*N1 + (1.0/2.0)*N4;", draft)
        self.assertNotIn("N2 = (1.0/2.0)*N1 + (1.0/2.0)*N4;", draft)
        self.assertIn("Internal-node voltage recovery after solved retained-node voltages", draft)

    def test_trf_ctest_dummy_small_varg_keeps_scalar_recovery_self_contained(self):
        fixture = Path("exports/Trf_Ctest_dummy_small_varG.json")
        if not fixture.exists():
            self.skipTest("Trf_Ctest_dummy_small_varG.json fixture is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        branch = next(
            item for item in data.get("branches", [])
            if item.get("packageOriginal", {}).get("networkCases")
        )
        cases = branch["packageOriginal"]["networkCases"]

        def payload_from_saved_case(case: dict, package_branch_id: str) -> dict:
            final_external = [
                str(group.get("display") or group.get("globalNet") or group.get("id") or f"N{index + 1}")
                for index, group in enumerate(case.get("finalExternalGroups") or [])
            ]
            final_internal = [
                f"pkg-internal:{package_branch_id}:{index}"
                for index, group in enumerate(case.get("finalInternalGroups") or [])
            ]
            display_names = {}
            for node, group in zip(final_external, case.get("finalExternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            for node, group in zip(final_internal, case.get("finalInternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            all_nodes = [*final_external, *final_internal]
            node_index = {node: index for index, node in enumerate(all_nodes)}
            terminal_node: dict[str, str] = {}
            for node, group in [
                *zip(final_external, case.get("finalExternalGroups") or []),
                *zip(final_internal, case.get("finalInternalGroups") or []),
            ]:
                for member in group.get("members") or []:
                    terminal_node[str(member)] = node
            G_full = sp.zeros(len(all_nodes), len(all_nodes))
            Ihis_full = sp.zeros(len(all_nodes), 1)

            def add_entry(row_node: str | None, col_node: str | None, expr: object) -> None:
                if row_node not in node_index or col_node not in node_index:
                    return
                G_full[node_index[row_node], node_index[col_node]] += optimized_api._parse_expr(expr)

            def add_ihis(row_node: str | None, expr: object) -> None:
                if row_node not in node_index:
                    return
                Ihis_full[node_index[row_node], 0] += optimized_api._parse_expr(expr)

            for saved_branch in case.get("branches") or []:
                branch_id = str(saved_branch.get("id") or "")
                if saved_branch.get("kind") != "two_node_branch":
                    continue
                node_a = terminal_node.get(f"{branch_id}.A")
                node_b = terminal_node.get(f"{branch_id}.B")
                g = optimized_api._parse_expr(saved_branch.get("g") or "0")
                ihis = optimized_api._parse_expr(saved_branch.get("ihis") or "0")
                add_entry(node_a, node_a, g)
                add_entry(node_a, node_b, -g)
                add_entry(node_b, node_a, -g)
                add_entry(node_b, node_b, g)
                add_ihis(node_a, ihis)
                add_ihis(node_b, -ihis)

            return {
                "all_nodes": all_nodes,
                "external_nodes": list(final_external),
                "internal_nodes": list(final_internal),
                "ground_nodes": [],
                "node_display_names": display_names,
                "G_full": optimized_api._clean_matrix(G_full),
                "G_full_tagged": optimized_api._clean_matrix(G_full),
                "Ihis_full": optimized_api._clean_vector(Ihis_full),
                "Ihis_full_tagged": optimized_api._clean_vector(Ihis_full),
                "direct_retained_stamps": [],
                "finalExternalGroups": case.get("finalExternalGroups"),
                "finalInternalGroups": case.get("finalInternalGroups"),
                "finalGMatrix": case.get("finalGMatrix"),
                "finalIhisVector": case.get("finalIhisVector"),
                "finalK_v": case.get("finalK_v"),
                "finalK_h": case.get("finalK_h"),
            }

        profiles = [
            {
                "case_id": index,
                "name": case.get("name") or f"case {index}",
                "case_map": {branch.get("id") or "C1": index},
                "payload": payload_from_saved_case(case, branch.get("id") or "C1"),
            }
            for index, case in enumerate(cases)
        ]
        request = _request(profiles, deps=_deps("R", code=("Gvar",)), case_id="case_id")
        response = build_multi_case_response({
            **request,
            "elimination_codegen_mode": "auto",
        })
        draft = response["multi_case"]["c_draft"]

        self.assertEqual(response["multi_case"]["codegen_mode"], "auto scalar Schur expansion")
        self.assertEqual(response["multi_case"]["fast_path"], "case_scalar_schur_expansion")
        self.assertNotIn("MATRIX_ Grr_code", draft)
        self.assertNotIn("MATRIX_ Gkk_code", draft)
        self.assertNotIn("MATRIX_ W_code", draft)
        self.assertNotIn("matrixDim(&Gkk_code", draft)
        self.assertNotIn("matrix_mult_CODE", draft)
        self.assertNotIn("get_CODE(&Gkk_code", draft)
        self.assertNotIn("for (int ", draft)
        self.assertNotIn("createGValue", draft)
        self.assertIn("switch (case_id)", draft)
        self.assertIn("case 0:", draft)
        self.assertIn("case 1:", draft)
        case1_code = draft.split("CODE:", 1)[1].split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertNotIn("Gkk", case1_code)
        recovery = draft.split("T1_T2:", 1)[1]
        case0_recovery = recovery.split("case 0:", 1)[1].split("case 1:", 1)[0]
        case1_recovery = recovery.split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertIn("N2 =", case0_recovery)
        static_section = draft.split("STATIC:", 1)[1].split("LOCAL_STATIC:", 1)[0]
        ram_case0 = draft.split("RAM_PASS1:", 1)[1].split("case 0:", 1)[1].split("case 1:", 1)[0]
        self.assertRegex(static_section, r"double scalar_case0_shared_inv_den_\d+ = 0\.0;")
        self.assertRegex(ram_case0, r"scalar_case0_shared_inv_den_\d+ = 1\.0/")
        self.assertIn("scalar_case0_shared_inv_den_", case0_recovery)
        self.assertNotIn("scalar_case0_t1t2_inv_den_", case0_recovery)
        self.assertIn("This Pack case has no recovered internal nodes.", case1_recovery)
        self.assertNotIn("scalar_case1_t1t2_inv_den_", case1_recovery)
        self.assertNotIn("get_CODE", recovery)
        self.assertGreater(response["multi_case"]["scalar_cse"]["denominator_temps"], 0)

    def test_trf_ctest_dummy_small_varg_force_scalar_avoids_internal_matrix_dag(self):
        fixture = Path("exports/Trf_Ctest_dummy_small_varG.json")
        if not fixture.exists():
            self.skipTest("Trf_Ctest_dummy_small_varG.json fixture is not available")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        branch = next(
            item for item in data.get("branches", [])
            if item.get("packageOriginal", {}).get("networkCases")
        )
        cases = branch["packageOriginal"]["networkCases"]

        def payload_from_saved_case(case: dict, package_branch_id: str) -> dict:
            final_external = [
                str(group.get("display") or group.get("globalNet") or group.get("id") or f"N{index + 1}")
                for index, group in enumerate(case.get("finalExternalGroups") or [])
            ]
            final_internal = [
                f"pkg-internal:{package_branch_id}:{index}"
                for index, group in enumerate(case.get("finalInternalGroups") or [])
            ]
            display_names = {}
            for node, group in zip(final_external, case.get("finalExternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            for node, group in zip(final_internal, case.get("finalInternalGroups") or []):
                display_names[node] = str(group.get("display") or node)
            all_nodes = [*final_external, *final_internal]
            node_index = {node: index for index, node in enumerate(all_nodes)}
            terminal_node: dict[str, str] = {}
            for node, group in [
                *zip(final_external, case.get("finalExternalGroups") or []),
                *zip(final_internal, case.get("finalInternalGroups") or []),
            ]:
                for member in group.get("members") or []:
                    terminal_node[str(member)] = node
            G_full = sp.zeros(len(all_nodes), len(all_nodes))
            Ihis_full = sp.zeros(len(all_nodes), 1)

            def add_entry(row_node: str | None, col_node: str | None, expr: object) -> None:
                if row_node not in node_index or col_node not in node_index:
                    return
                G_full[node_index[row_node], node_index[col_node]] += optimized_api._parse_expr(expr)

            def add_ihis(row_node: str | None, expr: object) -> None:
                if row_node not in node_index:
                    return
                Ihis_full[node_index[row_node], 0] += optimized_api._parse_expr(expr)

            for saved_branch in case.get("branches") or []:
                branch_id = str(saved_branch.get("id") or "")
                if saved_branch.get("kind") != "two_node_branch":
                    continue
                node_a = terminal_node.get(f"{branch_id}.A")
                node_b = terminal_node.get(f"{branch_id}.B")
                g = optimized_api._parse_expr(saved_branch.get("g") or "0")
                ihis = optimized_api._parse_expr(saved_branch.get("ihis") or "0")
                add_entry(node_a, node_a, g)
                add_entry(node_a, node_b, -g)
                add_entry(node_b, node_a, -g)
                add_entry(node_b, node_b, g)
                add_ihis(node_a, ihis)
                add_ihis(node_b, -ihis)

            return {
                "all_nodes": all_nodes,
                "external_nodes": list(final_external),
                "internal_nodes": list(final_internal),
                "ground_nodes": [],
                "node_display_names": display_names,
                "G_full": optimized_api._clean_matrix(G_full),
                "G_full_tagged": optimized_api._clean_matrix(G_full),
                "Ihis_full": optimized_api._clean_vector(Ihis_full),
                "Ihis_full_tagged": optimized_api._clean_vector(Ihis_full),
                "direct_retained_stamps": [],
                "finalExternalGroups": case.get("finalExternalGroups"),
                "finalInternalGroups": case.get("finalInternalGroups"),
                "finalGMatrix": case.get("finalGMatrix"),
                "finalIhisVector": case.get("finalIhisVector"),
                "finalK_v": case.get("finalK_v"),
                "finalK_h": case.get("finalK_h"),
            }

        profiles = [
            {
                "case_id": index,
                "name": case.get("name") or f"case {index}",
                "case_map": {branch.get("id") or "C1": index},
                "payload": payload_from_saved_case(case, branch.get("id") or "C1"),
            }
            for index, case in enumerate(cases)
        ]
        request = _request(profiles, deps=_deps("R", code=("Gvar",)), case_id="case_id")
        response = build_multi_case_response({
            **request,
            "elimination_codegen_mode": "force_scalar",
        })
        draft = response["multi_case"]["c_draft"]

        self.assertEqual(response["multi_case"]["codegen_mode"], "force scalar Schur expansion")
        self.assertEqual(response["multi_case"]["fast_path"], "case_scalar_schur_expansion")
        self.assertNotIn("MATRIX_ Gkk_code", draft)
        self.assertNotIn("MATRIX_ W_code", draft)
        self.assertNotIn("matrixDim(&Gkk_code", draft)
        self.assertNotIn("matrix_mult_CODE", draft)
        self.assertNotIn("get_CODE(&Gkk_code", draft)
        self.assertNotIn("createGValue", draft)
        self.assertIn("switch (case_id)", draft)
        self.assertIn("case 1:", draft)
        case1_code = draft.split("CODE:", 1)[1].split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertNotIn("Gkk", case1_code)
        recovery = draft.split("T1_T2:", 1)[1]
        case0_recovery = recovery.split("case 0:", 1)[1].split("case 1:", 1)[0]
        case1_recovery = recovery.split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertIn("N2 =", case0_recovery)
        self.assertIn("This Pack case has no recovered internal nodes.", case1_recovery)
        self.assertNotIn("get_CODE", recovery)

    def test_force_scalar_multicase_reuses_shared_retained_layout(self):
        response = build_multi_case_response({
            **_request(
                [
                    {"name": "cap case", "case_map": {"Pack": 0}, "payload": _series_payload("Gc", "Gr")},
                    {"name": "ind case", "case_map": {"Pack": 1}, "payload": _series_payload("GL", "Gr")},
                ],
                deps=_deps("Gc", "GL", "Gr"),
                case_id="case_id",
            ),
            "elimination_codegen_mode": "force_scalar",
        })

        draft = response["multi_case"]["c_draft"]
        ram_pass1 = draft.split("RAM_PASS1:", 1)[1].split("CODE:", 1)[0]
        self.assertEqual(response["multi_case"]["fast_path"], "case_scalar_schur_expansion")
        self.assertNotIn("NR_SUPER", draft)
        self.assertNotIn("NR_FINAL_MAX", draft)
        self.assertNotIn("CASE_COUNT", draft)
        self.assertEqual(ram_pass1.count('g_mat_nods[0] = getNodeNum(comp, "A");'), 1)
        self.assertEqual(ram_pass1.count('g_mat_nods[1] = getNodeNum(comp, "B");'), 1)
        self.assertEqual(ram_pass1.count("g_mat_over[row][col] = 0.0;"), 1)
        self.assertEqual(ram_pass1.count("setupGMatrix(2);"), 1)
        self.assertIn("case 0:", ram_pass1)
        self.assertIn("case 1:", ram_pass1)
        self.assertIn("g_mat_over[0][0]", ram_pass1)

    def test_force_scalar_preflight_blocks_large_multicase_before_c_codegen(self):
        large_expr = sp.Add(*[
            sp.Symbol(f"G{i}") * sp.Symbol(f"H{i}")
            for i in range(360)
        ], evaluate=False)
        final_results = []
        for case_index in range(6):
            final = SimpleNamespace(
                nodes=["A", "B"],
                G=sp.Matrix([[large_expr, -large_expr], [-large_expr, large_expr]]),
                Ihis=sp.Matrix([[large_expr], [-large_expr]]),
            )
            final_results.append({
                "index": case_index,
                "name": f"case {case_index}",
                "final": final,
                "K_v": sp.Matrix([[large_expr, large_expr]]),
                "K_h": sp.Matrix([[large_expr]]),
            })
        profile_set = SimpleNamespace(super_node_order=["A", "B"])
        payload = {
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {"name": f"case {index}", "case_map": {}, "payload": {}}
                for index in range(len(final_results))
            ],
            "elimination_codegen_mode": "force_scalar",
        }
        with patch.object(
            optimized_api,
            "_force_scalar_profile_results",
            return_value=(profile_set, final_results, [], []),
        ), patch.object(
            optimized_api,
            "_build_force_scalar_multi_case_c_draft",
            side_effect=AssertionError("large scalar C generation should be gated by preflight"),
        ):
            response = build_multi_case_response(payload)

        multi = response["multi_case"]
        preflight = multi["scalar_preflight"]
        self.assertEqual(multi["fast_path"], "case_scalar_preflight_blocked")
        self.assertEqual(preflight["severity"], "danger")
        self.assertTrue(preflight["blocked"])
        self.assertGreater(preflight["total_ops"], 0)
        self.assertIn("推荐使用矩阵", preflight["message_zh"])
        self.assertIn("仍然生成标量", preflight["action_zh"])
        self.assertIn("recommended", preflight["message_en"])
        self.assertIn("Continue", preflight["action_en"])
        self.assertIn("Scalar-expanded C draft was not generated", multi["c_draft"])

    def test_force_scalar_preflight_confirmation_allows_large_codegen(self):
        large_expr = sp.Add(*[
            sp.Symbol(f"G{i}") * sp.Symbol(f"H{i}")
            for i in range(360)
        ], evaluate=False)
        final_results = [{
            "index": 0,
            "name": "case 0",
            "final": SimpleNamespace(
                nodes=["A", "B"],
                G=sp.Matrix([[large_expr, -large_expr], [-large_expr, large_expr]]),
                Ihis=sp.Matrix([[0], [0]]),
            ),
            "K_v": sp.Matrix([[large_expr, large_expr]]),
            "K_h": sp.Matrix([[0]]),
        }]
        profile_set = SimpleNamespace(super_node_order=["A", "B"])
        payload = {
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [{"name": "case 0", "case_map": {}, "payload": {}}],
            "elimination_codegen_mode": "force_scalar",
            "force_scalar_confirmed": True,
        }
        with patch.object(
            optimized_api,
            "_force_scalar_profile_results",
            return_value=(profile_set, final_results, [], []),
        ), patch.object(
            optimized_api,
            "_build_force_scalar_multi_case_c_draft",
            return_value=("/* confirmed scalar C */", [], optimized_api._empty_scalar_cse_stats()),
        ) as build_spy:
            response = build_multi_case_response(payload)

        self.assertEqual(response["multi_case"]["fast_path"], "case_scalar_schur_expansion")
        self.assertEqual(response["multi_case"]["c_draft"], "/* confirmed scalar C */")
        self.assertFalse(response["multi_case"]["scalar_preflight"]["blocked"])
        self.assertTrue(build_spy.called)

    def test_final_retained_adapter_allows_different_raw_internal_counts(self):
        def final_pack_payload(raw_internal: list[str], final_internal: list[str], g: str) -> dict:
            raw_nodes = ["N1", "N2", *raw_internal]
            raw_size = len(raw_nodes)
            return {
                "all_nodes": raw_nodes,
                "external_nodes": ["N1", "N2"],
                "internal_nodes": raw_internal,
                "ground_nodes": [],
                "node_display_names": {node: node for node in raw_nodes},
                "G_full": [["0" for _ in range(raw_size)] for _ in range(raw_size)],
                "G_full_tagged": [["0" for _ in range(raw_size)] for _ in range(raw_size)],
                "Ihis_full": ["0" for _ in range(raw_size)],
                "Ihis_full_tagged": ["0" for _ in range(raw_size)],
                "direct_retained_stamps": [],
                "finalExternalGroups": [
                    {"display": "N1", "globalNet": "N1"},
                    {"display": "N2", "globalNet": "N2"},
                ],
                "finalInternalGroups": [
                    {"display": node, "globalNet": node}
                    for node in final_internal
                ],
                "finalGMatrix": [[g, f"-({g})"], [f"-({g})", g]],
                "finalIhisVector": ["0", "0"],
                "finalK_v": [
                    ["1", "0"] if index == 0 else ["0", "1"]
                    for index, _node in enumerate(final_internal)
                ],
                "finalK_h": [f"Ihis_{node}" for node in final_internal],
            }

        profiles = [
            {
                "case_id": 0,
                "name": "one internal",
                "case_map": {"Pack": 0},
                "payload": final_pack_payload(["inner_left"], ["inner_left"], "G0"),
            },
            {
                "case_id": 1,
                "name": "two internals",
                "case_map": {"Pack": 1},
                "payload": final_pack_payload(
                    ["inner_left", "inner_right"],
                    ["inner_left", "inner_right"],
                    "G1",
                ),
            },
        ]

        with self.assertRaisesRegex(ValueError, "topology invariant"):
            optimized_api._validate_multicase_topology(profiles)

        adapted_profiles, recovery_profiles = optimized_api._final_retained_profile_adapter(profiles)
        self.assertEqual(adapted_profiles[0]["payload"]["all_nodes"], ["N1", "N2"])
        self.assertEqual(adapted_profiles[0]["payload"]["internal_nodes"], [])
        self.assertEqual(adapted_profiles[1]["payload"]["all_nodes"], ["N1", "N2"])
        self.assertEqual(adapted_profiles[1]["payload"]["internal_nodes"], [])
        self.assertEqual(recovery_profiles[0]["recovery_nodes"], ["inner_left"])
        self.assertEqual(recovery_profiles[1]["recovery_nodes"], ["inner_left", "inner_right"])

        response = build_multi_case_response(
            _request(
                profiles,
                deps=_deps("G0", "G1", step=("Ihis_inner_left", "Ihis_inner_right")),
                case_id="case_id",
            )
        )
        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertIn('getNodeNum(comp, "N1")', draft)
        self.assertIn('getNodeNum(comp, "N2")', draft)
        self.assertNotIn('getNodeNum(comp, "inner_left")', draft)
        self.assertNotIn('getNodeNum(comp, "inner_right")', draft)
        self.assertIn("Case-specific voltage recovery", draft)
        self.assertIn("case 0:", draft)
        self.assertIn("inner_left =", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("inner_right =", draft)

    def test_gkk_placeholder_adapter_preempts_final_fields_when_raw_internal_counts_differ(self):
        def chain_payload(internal_nodes: list[str], edges: list[tuple[str, str, str]]) -> dict:
            all_nodes = ["N1", "N2", *internal_nodes]
            node_index = {node: index for index, node in enumerate(all_nodes)}
            matrix = sp.zeros(len(all_nodes), len(all_nodes))
            for left, right, conductance in edges:
                g = sp.Symbol(conductance)
                i = node_index[left]
                j = node_index[right]
                matrix[i, i] += g
                matrix[j, j] += g
                matrix[i, j] -= g
                matrix[j, i] -= g
            return {
                "all_nodes": all_nodes,
                "external_nodes": ["N1", "N2"],
                "internal_nodes": internal_nodes,
                "ground_nodes": [],
                "node_display_names": {node: node for node in all_nodes},
                "G_full": optimized_api._clean_matrix(matrix),
                "G_full_tagged": optimized_api._clean_matrix(matrix),
                "Ihis_full": ["0" for _ in all_nodes],
                "Ihis_full_tagged": ["0" for _ in all_nodes],
                "direct_retained_stamps": [],
                "finalExternalGroups": [
                    {"display": "N1", "globalNet": "N1"},
                    {"display": "N2", "globalNet": "N2"},
                ],
                "finalInternalGroups": [
                    {"display": node, "globalNet": node}
                    for node in internal_nodes
                ],
                "finalGMatrix": [["Gfinal", "-Gfinal"], ["-Gfinal", "Gfinal"]],
                "finalIhisVector": ["0", "0"],
                "finalK_v": [
                    ["1", "0"] if index == 0 else ["0", "1"]
                    for index, _node in enumerate(internal_nodes)
                ],
                "finalK_h": [f"Ihis_{node}" for node in internal_nodes],
            }

        profiles = [
            {
                "case_id": 0,
                "name": "one internal",
                "case_map": {"Pack": 0},
                "payload": chain_payload(
                    ["K1"],
                    [("N1", "K1", "Ga"), ("K1", "N2", "Gb")],
                ),
            },
            {
                "case_id": 1,
                "name": "two internals",
                "case_map": {"Pack": 1},
                "payload": chain_payload(
                    ["K1", "K2"],
                    [("N1", "K1", "Ga"), ("K1", "K2", "Gb"), ("K2", "N2", "Gc")],
                ),
            },
        ]

        with self.assertRaisesRegex(ValueError, "topology invariant"):
            optimized_api._validate_multicase_topology(profiles)

        request = _request(profiles, deps=_deps("Ga", "Gb", "Gc"), case_id="case_id")
        alias_model = _build_multicase_alias_template_payload(request)
        template_payload = alias_model["template_payload"]
        self.assertEqual(template_payload["external_nodes"], ["N1", "N2"])
        self.assertEqual(template_payload["internal_nodes"], ["K1", "K2"])
        aligned_case0 = alias_model["profiles"][0]["payload"]
        self.assertEqual(aligned_case0["backend_placeholder_internal_nodes"], ["K2"])
        aligned_g0 = sp.Matrix(aligned_case0["G_full"])
        self.assertEqual(sp.sympify(aligned_g0[3, 0]), sp.Integer(0))
        self.assertEqual(sp.sympify(aligned_g0[3, 1]), sp.Integer(0))
        self.assertEqual(sp.sympify(aligned_g0[3, 2]), sp.Integer(0))
        self.assertEqual(sp.sympify(aligned_g0[3, 3]), sp.Integer(1))

        response = build_multi_case_response(request)
        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertEqual(
            multi["internal_layout_profiles"],
            [
                {
                    "profile_id": "INTERNAL_CASE_0",
                    "case_ids": [1],
                    "ordered_active_internal_nodes": ["K1", "K2"],
                    "active_internal_count": 2,
                    "internal_to_active_index": {"K1": 0, "K2": 1},
                    "placeholder_internal_nodes": [],
                },
                {
                    "profile_id": "INTERNAL_CASE_1",
                    "case_ids": [0],
                    "ordered_active_internal_nodes": ["K1"],
                    "active_internal_count": 1,
                    "internal_to_active_index": {"K1": 0},
                    "placeholder_internal_nodes": ["K2"],
                },
            ],
        )
        self.assertIn("internal_profile = INTERNAL_CASE_1;", draft)
        self.assertIn("internal_active = INTERNAL_NODES_CASE_1;", draft)
        self.assertIn("matrixDim(&Gkk_code, internal_active, internal_active);", draft)
        self.assertIn("matrixDim(&Gkr_code, internal_active, RETAINED_NODES);", draft)
        self.assertNotIn("set_CODE(&W_code, 1, 1, 0.0);", draft)
        self.assertIn("Gkk_code", draft)
        self.assertNotIn("no eliminated internal nodes", draft)
        self.assertIn("Case-specific voltage recovery", draft)
        self.assertIn("case 0:", draft)
        self.assertIn("K1 =", draft)
        self.assertIn("case 1:", draft)
        self.assertIn("K2 =", draft)
        self.assertNotIn("multi-case topology invariant failed", draft)

    def test_internal_profile_compaction_survives_code_owned_g_aliases(self):
        def chain_payload(internal_nodes: list[str], edges: list[tuple[str, str, str]]) -> dict:
            all_nodes = ["N1", "N2", *internal_nodes]
            node_index = {node: index for index, node in enumerate(all_nodes)}
            matrix = sp.zeros(len(all_nodes), len(all_nodes))
            for left, right, conductance in edges:
                g = sp.Symbol(conductance)
                i = node_index[left]
                j = node_index[right]
                matrix[i, i] += g
                matrix[j, j] += g
                matrix[i, j] -= g
                matrix[j, i] -= g
            return {
                "all_nodes": all_nodes,
                "external_nodes": ["N1", "N2"],
                "internal_nodes": internal_nodes,
                "ground_nodes": [],
                "node_display_names": {node: node for node in all_nodes},
                "G_full": optimized_api._clean_matrix(matrix),
                "G_full_tagged": optimized_api._clean_matrix(matrix),
                "Ihis_full": ["0" for _node in all_nodes],
                "Ihis_full_tagged": ["0" for _node in all_nodes],
                "direct_retained_stamps": [],
                "finalExternalGroups": [
                    {"display": "N1", "globalNet": "N1"},
                    {"display": "N2", "globalNet": "N2"},
                ],
                "finalInternalGroups": [
                    {"display": node, "globalNet": node}
                    for node in internal_nodes
                ],
                "finalGMatrix": [["Gedge", "-Gedge"], ["-Gedge", "Gedge"]],
                "finalIhisVector": ["0", "0"],
                "finalK_v": [
                    ["1", "0"] if index == 0 else ["0", "1"]
                    for index, _node in enumerate(internal_nodes)
                ],
                "finalK_h": [f"Ihis_{node}" for node in internal_nodes],
            }

        request = _request(
            [
                {
                    "case_id": 0,
                    "name": "one internal dynamic G",
                    "case_map": {"Pack": 0},
                    "payload": chain_payload(
                        ["K1"],
                        [("N1", "K1", "Ga"), ("K1", "N2", "Gb")],
                    ),
                },
                {
                    "case_id": 1,
                    "name": "two internals dynamic G",
                    "case_map": {"Pack": 1},
                    "payload": chain_payload(
                        ["K1", "K2"],
                        [("N1", "K1", "Ga"), ("K1", "K2", "Gb"), ("K2", "N2", "Gc")],
                    ),
                },
            ],
            deps=_deps("Ga", "Gb", "Gc", "Gedge", code=("Ga", "Gc")),
            case_id="case_id",
        )

        response = build_multi_case_response(request)
        multi = response["multi_case"]
        draft = multi["c_draft"]
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertIn("internal_active", draft)
        self.assertIn("matrixDim(&Gkk_code, internal_active, internal_active);", draft)
        self.assertIn("Case-resolved Gkk inverse over active internal profile", draft)
        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);", draft)
        self.assertIn("network_node_recover_vk_from_grkw_only(RETAINED_NODES, internal_active", draft)
        self.assertIn("set_CODE(&Gkk_code", draft)
        self.assertNotIn("col < NR", draft)
        self.assertNotIn("set_CODE(&Gkk_code, 1, 1, 1.0);", draft)
        self.assertNotIn("set_CODE(&W_code, 1, 1, 0.0);", draft)
        self.assertIn("K1 = get_CODE(&Vk_code, 0, 0);", draft)
        self.assertIn("K2 = get_CODE(&Vk_code, 1, 0);", draft)

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

    def test_topology_mismatch_reports_missing_final_retained_adapter_fields(self):
        bad_payload = _series_payload("X", internal=False)
        bad_payload["all_nodes"] = ["A", "C"]
        bad_payload["external_nodes"] = ["A", "C"]
        with self.assertRaisesRegex(ValueError, "final-retained adapter unavailable: profile 0 missing"):
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
