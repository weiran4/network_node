import unittest
import json
import time
from pathlib import Path

import sympy as sp

from elimination import eliminate_internal_nodes
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
        self.assertIn("cr_R1_G_eff", draft)
        self.assertIn("cr_R1_G_eff = X;", draft)
        self.assertIn("cr_R1_G_eff = X + Y;", draft)
        self.assertNotIn("X + Y - X", draft)
        self.assertNotIn("base + delta", draft)
        self.assertLessEqual(draft.count("Gred ="), 1)

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
        self.assertEqual(aliases["cr_R1_G_eff"]["owner"], "CODE")
        self.assertTrue(any("cr_R1_G_eff" in warning and "case_id is fixed before simulation" in warning for warning in response["warnings"]))
        draft = response["multi_case"]["c_draft"]
        self.assertIn("BEGIN_T0:", draft)
        self.assertIn("cr_R1_G_eff = G_const;", draft)
        self.assertIn("cr_R1_G_eff = G_dynamic;", draft)
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
        self.assertEqual(aliases["cr_R1_G_eff"]["owner"], "CODE_PER_STEP")
        self.assertTrue(any("cr_R1_G_eff" in warning and "case_id is fixed before simulation" in warning for warning in response["warnings"]))
        draft = response["multi_case"]["c_draft"]
        self.assertIn("cr_R1_G_eff = H_const;", draft)
        self.assertIn("cr_R1_G_eff = H_step;", draft)

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
        self.assertIn("cr_R1_G_eff", draft)
        self.assertIn("cr_R2_G_eff", draft)
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
        self.assertIn("cr_R1_G_eff", composite)
        self.assertIn("cr_R2_G_eff", composite)

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

        self.assertEqual(alias_model["template_payload"]["G_full"][0][0], "cr_G_0_0_eff")
        alias = alias_model["aliases"]["cr_G_0_0_eff"]
        self.assertEqual(alias["selector"], "global")
        self.assertEqual(alias["case_values"]["3"], "Dabc + G22 + Grc + w2")

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

    def test_same_branch_code_aliases_share_one_local_case_switch(self):
        aliases = {
            "cr_C1_G_eff": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {"0": "AA + G22 + G_rc + w2", "1": "Dabc + G22 + G_rc + w2"},
            },
            "cr_C1_G_eff_2": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {"0": "BB + G22 + G_rc + w2", "1": "Dabc + G22 + G_rc + w2"},
            },
            "cr_C1_G_eff_3": {
                "branch_id": "C1",
                "owner": "CODE",
                "case_values": {"0": "CC + G22 + G_rc + w2", "1": "Dabc + G22 + G_rc + w2"},
            },
        }

        lines = "\n".join(_alias_assignment_lines(aliases, "CODE"))

        self.assertEqual(lines.count("switch (C1_case_id)"), 1)
        self.assertIn("case 0:\n        cr_C1_G_eff = AA + G22 + G_rc + w2;\n        cr_C1_G_eff_2 = BB + G22 + G_rc + w2;\n        cr_C1_G_eff_3 = CC + G22 + G_rc + w2;", lines)
        self.assertIn("case 1:\n        cr_C1_G_eff = Dabc + G22 + G_rc + w2;\n        cr_C1_G_eff_2 = Dabc + G22 + G_rc + w2;\n        cr_C1_G_eff_3 = Dabc + G22 + G_rc + w2;", lines)

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
        self.assertIn("cr_B11_G_eff", draft)
        self.assertIn("cr_B12_G_eff", draft)
        self.assertIn("switch (case_id)", draft)
        self.assertIn("switch (B11_case_id)", draft)
        self.assertIn("switch (B12_case_id)", draft)
        self.assertIn("case 3:", draft)
        self.assertIn("cr_B11_G_eff = G1;", draft)
        self.assertIn("cr_B11_G_eff = G2;", draft)
        self.assertIn("cr_B12_G_eff = G3;", draft)
        self.assertIn("cr_B12_G_eff = G4;", draft)
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
        self.assertIn("cr_R1_G_eff = 0.0;", draft)
        self.assertIn("cr_R1_G_eff = X;", draft)
        self.assertIn("g_mat_over[0][0] = cr_R1_G_eff;", draft)

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
        alias = sp.Symbol("cr_R1_G_eff")

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
