import unittest

from optimized_elimination_api import build_multi_case_response

from tests.test_multicase_c_export_alias_template import _deps, _series_payload


def _runtime_request(case0: str, case1: str, *, deps: dict | None = None) -> dict:
    profiles = [
        {
            "name": "case 0",
            "case_map": {},
            "payload": _series_payload(case0, internal=False),
        }
    ]
    dep_table = deps or _deps("G_const", code=("G_dyn",))
    profiles[0]["payload"]["symbol_dependency_table"] = dict(dep_table)
    profiles[0]["payload"]["symbol_dependency_table_tagged"] = dict(dep_table)
    return {
        "mode": "multi_case_c_export",
        "case_id_symbol": "case_id",
        "case_profiles": profiles,
        "runtime_case_groups": [
            {
                "branch_id": "R11",
                "name": "R11",
                "case_id_symbol": "runtime_R11_case_id",
                "cases": [
                    {
                        "index": 0,
                        "name": "Case 1",
                        "payloads": [_series_payload(case0, internal=False)],
                    },
                    {
                        "index": 1,
                        "name": "Case 2",
                        "payloads": [_series_payload(case1, internal=False)],
                    },
                ],
            }
        ],
    }


def _runtime_overlay_payload(runtime_expr: str) -> dict:
    return {
        "all_nodes": ["N1", "N2", "N3", "N5"],
        "external_nodes": ["N1", "N2", "N3", "N5"],
        "internal_nodes": [],
        "ground_nodes": [],
        "node_display_names": {"N1": "N1", "N2": "N2", "N3": "N3", "N5": "N5"},
        "G_full": [
            ["Ga", "-Ga", "0", "0"],
            ["-Ga", f"Ga + Gb + {runtime_expr}", "-Gb", f"-({runtime_expr})"],
            ["0", "-Gb", "Gb", "0"],
            ["0", f"-({runtime_expr})", "0", runtime_expr],
        ],
        "Ihis_full": ["0", "0", "0", "0"],
        "G_full_tagged": [
            ["Ga", "-Ga", "0", "0"],
            ["-Ga", f"Ga + Gb + {runtime_expr}", "-Gb", f"-({runtime_expr})"],
            ["0", "-Gb", "Gb", "0"],
            ["0", f"-({runtime_expr})", "0", runtime_expr],
        ],
        "Ihis_full_tagged": ["0", "0", "0", "0"],
        "direct_retained_stamps": [],
    }


def _runtime_overlay_payload_with_direct_stamps(runtime_expr: str) -> dict:
    payload = _runtime_overlay_payload(runtime_expr)
    payload["direct_retained_stamps"] = [
        {
            "id": "B1",
            "name": "R1",
            "support_nodes": ["N1", "N2"],
            "G": [
                {"row": "N1", "col": "N1", "expr": "Ga", "tagged": "Ga"},
                {"row": "N1", "col": "N2", "expr": "-Ga", "tagged": "-Ga"},
                {"row": "N2", "col": "N1", "expr": "-Ga", "tagged": "-Ga"},
                {"row": "N2", "col": "N2", "expr": "Ga", "tagged": "Ga"},
            ],
            "Ihis": [],
        },
        {
            "id": "B2",
            "name": "R2",
            "support_nodes": ["N2", "N3"],
            "G": [
                {"row": "N2", "col": "N2", "expr": "Gb", "tagged": "Gb"},
                {"row": "N2", "col": "N3", "expr": "-Gb", "tagged": "-Gb"},
                {"row": "N3", "col": "N2", "expr": "-Gb", "tagged": "-Gb"},
                {"row": "N3", "col": "N3", "expr": "Gb", "tagged": "Gb"},
            ],
            "Ihis": [],
        },
        {
            "id": "B3",
            "name": "R3",
            "support_nodes": ["N2", "N5"],
            "G": [
                {"row": "N2", "col": "N2", "expr": runtime_expr, "tagged": runtime_expr},
                {"row": "N2", "col": "N5", "expr": f"-({runtime_expr})", "tagged": f"-({runtime_expr})"},
                {"row": "N5", "col": "N2", "expr": f"-({runtime_expr})", "tagged": f"-({runtime_expr})"},
                {"row": "N5", "col": "N5", "expr": runtime_expr, "tagged": runtime_expr},
            ],
            "Ihis": [],
        },
    ]
    return payload


class RuntimeMutableCaseGroupTests(unittest.TestCase):
    def test_no_runtime_group_keeps_export_free_of_runtime_selectors(self):
        profiles = [
            {
                "name": "case 0",
                "case_map": {"R11": 0},
                "payload": _series_payload("G_const", internal=False),
            },
            {
                "name": "case 1",
                "case_map": {"R11": 1},
                "payload": _series_payload("G_dyn", internal=False),
            },
        ]
        deps = _deps("G_const", code=("G_dyn",))
        for profile in profiles:
            profile["payload"]["symbol_dependency_table"] = dict(deps)
            profile["payload"]["symbol_dependency_table_tagged"] = dict(deps)

        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": profiles,
        })

        self.assertFalse(any(info.get("runtime_mutable") for info in response["multi_case"]["aliases"].values()))
        self.assertNotIn("runtime_R11_case_id", response["multi_case"]["c_draft"])
        self.assertEqual(response["multi_case"]["diagnostics"]["runtime_case_group_count"], 0)

    def test_runtime_only_group_does_not_emit_empty_global_case_switch(self):
        response = build_multi_case_response(_runtime_request("G_const", "G_dyn"))
        draft = response["multi_case"]["c_draft"]

        self.assertIn("switch (runtime_R11_case_id)", draft)
        self.assertNotIn("Decode the optional global case selector into per-element local cases", draft)
        self.assertNotIn("switch (case_id) {\n    case 0: /* case 0: init-time base profile */", draft)

    def test_runtime_group_does_not_expand_init_case_count(self):
        response = build_multi_case_response(_runtime_request("G_const", "G_dyn"))
        multi = response["multi_case"]

        self.assertEqual(multi["profile_count"], 1)
        self.assertEqual(multi["diagnostics"]["init_time_case_count"], 1)
        self.assertEqual(multi["diagnostics"]["runtime_case_group_count"], 1)
        self.assertEqual(multi["diagnostics"]["runtime_case_count_per_group"], {"R11": 2})

    def test_runtime_alias_is_full_value_code_switch_not_delta(self):
        response = build_multi_case_response(_runtime_request("G_const", "G_dyn"))
        draft = response["multi_case"]["c_draft"]
        aliases = response["multi_case"]["aliases"]

        self.assertIn("multcase_G_R11_A_A", aliases)
        self.assertEqual(aliases["multcase_G_R11_A_A"]["owner"], "CODE")
        self.assertTrue(aliases["multcase_G_R11_A_A"]["runtime_mutable"])
        self.assertIn("switch (runtime_R11_case_id)", draft)
        self.assertIn("multcase_G_R11_A_A = G_const;", draft)
        self.assertIn("multcase_G_R11_A_A = G_dyn;", draft)
        self.assertNotIn("G_dyn - G_const", draft)
        self.assertNotIn("G_const - G_dyn", draft)

    def test_runtime_case_id_is_not_used_in_gvalue_condition(self):
        response = build_multi_case_response(_runtime_request("G_const", "G_dyn"))
        draft = response["multi_case"]["c_draft"]
        create_lines = [line for line in draft.splitlines() if "createGValue" in line]

        self.assertTrue(create_lines)
        self.assertFalse(any("runtime_R11_case_id" in line for line in create_lines))
        self.assertEqual(response["multi_case"]["diagnostics"]["runtime_case_used_in_gvalue_condition"], 0)

    def test_runtime_group_is_excluded_from_init_time_cartesian_product(self):
        dep_table = _deps("R0", "R1", "S0", "S1", code=("U0", "U1"))
        init_profiles = []
        for r_case, r_expr in enumerate(["R0", "R1"]):
            for s_case, s_expr in enumerate(["S0", "S1"]):
                payload = _series_payload(f"{r_expr} + {s_expr} + U0", internal=False)
                payload["symbol_dependency_table"] = dict(dep_table)
                payload["symbol_dependency_table_tagged"] = dict(dep_table)
                init_profiles.append({
                    "name": f"R{r_case} S{s_case}",
                    "case_map": {"R": r_case, "S": s_case},
                    "payload": payload,
                })
        runtime_payloads = []
        for runtime_expr in ["U0", "U1"]:
            per_init = []
            for profile in init_profiles:
                r_case = profile["case_map"]["R"]
                s_case = profile["case_map"]["S"]
                r_expr = ["R0", "R1"][r_case]
                s_expr = ["S0", "S1"][s_case]
                payload = _series_payload(f"{r_expr} + {s_expr} + {runtime_expr}", internal=False)
                payload["symbol_dependency_table"] = dict(dep_table)
                payload["symbol_dependency_table_tagged"] = dict(dep_table)
                per_init.append(payload)
            runtime_payloads.append(per_init)

        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": init_profiles,
            "runtime_case_groups": [
                {
                    "branch_id": "U",
                    "name": "UCM",
                    "case_id_symbol": "runtime_UCM_case_id",
                    "cases": [
                        {"index": 0, "name": "Block", "payloads": runtime_payloads[0]},
                        {"index": 1, "name": "Deblock", "payloads": runtime_payloads[1]},
                    ],
                }
            ],
        })

        self.assertEqual(response["multi_case"]["profile_count"], 4)
        diagnostics = response["multi_case"]["diagnostics"]
        self.assertEqual(diagnostics["init_time_case_count"], 4)
        self.assertEqual(diagnostics["runtime_case_group_count"], 1)
        self.assertEqual(diagnostics["runtime_case_count_per_group"], {"UCM": 2})
        self.assertNotEqual(diagnostics["init_time_case_count"], 8)
        self.assertTrue(any(info.get("runtime_mutable") for info in response["multi_case"]["aliases"].values()))
        self.assertIn("switch (runtime_UCM_case_id)", response["multi_case"]["c_draft"])

    def test_unchanged_runtime_expression_does_not_force_runtime_alias(self):
        response = build_multi_case_response(_runtime_request("G_const", "G_const", deps=_deps("G_const")))

        self.assertEqual(response["multi_case"]["aliases"], {})
        self.assertNotIn("switch (runtime_R11_case_id)", response["multi_case"]["c_draft"])

    def test_runtime_group_changing_matrix_shape_is_rejected(self):
        bad_payload = _series_payload("G_dyn")
        bad_payload["all_nodes"] = ["A", "B", "X"]
        bad_payload["external_nodes"] = ["A", "B", "X"]
        bad_payload["node_display_names"] = {"A": "A", "B": "B", "X": "X"}
        bad_payload["G_full"] = [
            ["G_dyn", "-G_dyn", "0"],
            ["-G_dyn", "G_dyn", "0"],
            ["0", "0", "0"],
        ]
        bad_payload["Ihis_full"] = ["0", "0", "0"]
        bad_payload["G_full_tagged"] = bad_payload["G_full"]
        bad_payload["Ihis_full_tagged"] = bad_payload["Ihis_full"]

        request = _runtime_request("G_const", "G_dyn")
        request["runtime_case_groups"][0]["cases"][1]["payloads"] = [bad_payload]

        with self.assertRaisesRegex(ValueError, "Runtime-mutable case group R11 changes topology or matrix shape"):
            build_multi_case_response(request)

    def test_runtime_group_changing_retained_profile_is_rejected(self):
        bad_payload = _series_payload("G_dyn", internal=True)
        bad_payload["symbol_dependency_table"] = dict(_deps("G_const", code=("G_dyn",)))
        bad_payload["symbol_dependency_table_tagged"] = dict(_deps("G_const", code=("G_dyn",)))

        request = _runtime_request("G_const", "G_dyn", deps=_deps("G_const", code=("G_dyn",)))
        request["case_profiles"][0]["payload"] = _series_payload("G_const", internal=False)
        request["runtime_case_groups"][0]["cases"][1]["payloads"] = [bad_payload]

        with self.assertRaisesRegex(ValueError, "Runtime-mutable case group R11 changes topology or matrix shape"):
            build_multi_case_response(request)

    def test_runtime_group_changing_history_vector_length_is_rejected(self):
        bad_payload = _series_payload("G_dyn", internal=False)
        bad_payload["Ihis_full"] = ["0", "0", "I_extra"]
        bad_payload["Ihis_full_tagged"] = ["0", "0", "I_extra"]
        bad_payload["symbol_dependency_table"] = dict(_deps("G_const", code=("G_dyn",), step=("I_extra",)))
        bad_payload["symbol_dependency_table_tagged"] = dict(_deps("G_const", code=("G_dyn",), step=("I_extra",)))

        request = _runtime_request("G_const", "G_dyn", deps=_deps("G_const", code=("G_dyn",), step=("I_extra",)))
        request["runtime_case_groups"][0]["cases"][1]["payloads"] = [bad_payload]

        with self.assertRaisesRegex(ValueError, "Runtime-mutable case group R11 changes topology or matrix shape"):
            build_multi_case_response(request)

    def test_runtime_history_alias_promotes_to_code_per_step_full_value(self):
        def payload(ihis: str) -> dict:
            item = _series_payload("G_const", internal=False)
            item["Ihis_full"] = [ihis, f"-({ihis})"]
            item["Ihis_full_tagged"] = [ihis, f"-({ihis})"]
            return item

        deps = _deps("G_const", "I_const", step=("I_step",))
        request = {
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {
                    "name": "case 0",
                    "case_map": {},
                    "payload": payload("I_const"),
                }
            ],
            "runtime_case_groups": [
                {
                    "branch_id": "R11",
                    "name": "R11",
                    "case_id_symbol": "runtime_R11_case_id",
                    "cases": [
                        {"index": 0, "name": "Case 1", "payloads": [payload("I_const")]},
                        {"index": 1, "name": "Case 2", "payloads": [payload("I_step")]},
                    ],
                }
            ],
        }
        for profile in [request["case_profiles"][0], *request["runtime_case_groups"][0]["cases"]]:
            payloads = [profile["payload"]] if "payload" in profile else profile["payloads"]
            for item in payloads:
                item["symbol_dependency_table"] = dict(deps)
                item["symbol_dependency_table_tagged"] = dict(deps)

        response = build_multi_case_response(request)
        ihis_aliases = {
            alias: info
            for alias, info in response["multi_case"]["aliases"].items()
            if info.get("kind") == "Ihis"
        }
        self.assertTrue(ihis_aliases)
        self.assertTrue(all(info["owner"] == "CODE_PER_STEP" for info in ihis_aliases.values()))
        draft = response["multi_case"]["c_draft"]
        self.assertIn("switch (runtime_R11_case_id)", draft)
        self.assertIn("I_const", draft)
        self.assertIn("I_step", draft)
        self.assertNotIn("I_step - I_const", draft)

    def test_runtime_invariant_history_alias_collapses_to_init_cases(self):
        def payload(ihis: str) -> dict:
            item = _series_payload("G_const", internal=False)
            item["Ihis_full"] = [ihis, f"-({ihis})"]
            item["Ihis_full_tagged"] = [ihis, f"-({ihis})"]
            item["symbol_dependency_table"] = dict(deps)
            item["symbol_dependency_table_tagged"] = dict(deps)
            return item

        deps = _deps("G_const", step=("I_init0", "I_init1"))
        init_payloads = [payload("I_init0"), payload("I_init1")]
        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {"name": "case 0", "case_map": {"S": 0}, "payload": init_payloads[0]},
                {"name": "case 1", "case_map": {"S": 1}, "payload": init_payloads[1]},
            ],
            "runtime_case_groups": [
                {
                    "branch_id": "U",
                    "name": "UCM",
                    "case_id_symbol": "runtime_UCM_case_id",
                    "cases": [
                        {"index": 0, "name": "Block", "payloads": init_payloads},
                        {"index": 1, "name": "Deblock", "payloads": init_payloads},
                    ],
                }
            ],
        })

        ihis_aliases = {
            alias: info
            for alias, info in response["multi_case"]["aliases"].items()
            if info.get("kind") == "Ihis"
        }
        self.assertTrue(ihis_aliases)
        self.assertTrue(all(not info.get("runtime_mutable") for info in ihis_aliases.values()))
        self.assertTrue(all(set(info.get("case_values", {})) <= {"0", "1"} for info in ihis_aliases.values()))
        self.assertFalse(any(len(info.get("case_values", {})) > 2 for info in ihis_aliases.values()))

    def test_runtime_alias_enters_shared_internal_matrix_dag_not_final_gred_switch(self):
        deps = _deps("G2", "G_const", code=("G_dyn",))
        request = _runtime_request("G_const", "G_dyn", deps=deps)
        request["case_profiles"][0]["payload"] = _series_payload("G_const", internal=True)
        request["case_profiles"][0]["payload"]["symbol_dependency_table"] = dict(deps)
        request["case_profiles"][0]["payload"]["symbol_dependency_table_tagged"] = dict(deps)
        for case, expr in zip(request["runtime_case_groups"][0]["cases"], ["G_const", "G_dyn"]):
            payload = _series_payload(expr, internal=True)
            payload["symbol_dependency_table"] = dict(deps)
            payload["symbol_dependency_table_tagged"] = dict(deps)
            case["payloads"] = [payload]

        response = build_multi_case_response(request)
        draft = response["multi_case"]["c_draft"]

        self.assertIn("switch (runtime_R11_case_id)", draft)
        self.assertIn("multcase_G_R11_A_A = G_const;", draft)
        self.assertIn("multcase_G_R11_A_A = G_dyn;", draft)
        self.assertIn("Gkk_k1_k1 = G2 + multcase_G_R11_A_A;", draft)
        self.assertIn("Diagonal Gkk scalar CODE path", draft)
        self.assertIn("double inv_gkk_diag[INTERNAL_NODES];", draft)
        self.assertIn("inv_gkk_diag[k] = 1.0 / get_CODE(&Gkk_code, k, k);", draft)
        self.assertIn("schur -= get_CODE(&Grk_code, i, k) * get_CODE(&Gkr_code, k, j) * inv_gkk_diag[k];", draft)
        self.assertNotIn("matrix_subtract_CODE(&Gred_code, &Grr_code, &tmp_Grk_W_Gkr_code);", draft)
        self.assertFalse(any("runtime_R11_case_id" in line for line in draft.splitlines() if "createGValue" in line))
        self.assertNotIn("case 0:\n        varG", draft)
        self.assertEqual(response["multi_case"]["diagnostics"]["per_runtime_case_final_gred_expansion_count"], 0)

    def test_runtime_case_keeps_fixed_overlay_in_ram_for_no_internal_network(self):
        deps = _deps("Ga", "Gb", code=("Gc_0", "Gc_1"))
        base_payload = _runtime_overlay_payload("Gc_0")
        base_payload["symbol_dependency_table"] = dict(deps)
        base_payload["symbol_dependency_table_tagged"] = dict(deps)
        runtime_payloads = []
        for expr in ["Gc_0", "Gc_1"]:
            payload = _runtime_overlay_payload(expr)
            payload["symbol_dependency_table"] = dict(deps)
            payload["symbol_dependency_table_tagged"] = dict(deps)
            runtime_payloads.append(payload)

        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {"name": "case 0", "case_map": {}, "payload": base_payload},
            ],
            "runtime_case_groups": [
                {
                    "branch_id": "B3",
                    "name": "B3",
                    "case_id_symbol": "runtime_R3_case_id",
                    "cases": [
                        {"index": 0, "name": "Case 0", "payloads": [runtime_payloads[0]]},
                        {"index": 1, "name": "Case 1", "payloads": [runtime_payloads[1]]},
                    ],
                }
            ],
        })
        draft = response["multi_case"]["c_draft"]

        self.assertIn("g_mat_over[1][1] = Ga + Gb;", draft)
        self.assertNotIn("g_mat_over[1][3] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[3][1] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[3][3] = -Ga - Gb", draft)
        self.assertIn("setupGMatrix(3);", draft)
        self.assertNotIn("setupGMatrix(4);", draft)
        self.assertIn("multcase_G_B3_N2_N2 = Gc_0;", draft)
        self.assertIn("multcase_G_B3_N2_N2 = Gc_1;", draft)
        self.assertIn("varG_N2_N2 = multcase_G_B3_N2_N2;", draft)
        self.assertIn("varG_N2_N5 = -multcase_G_B3_N2_N2;", draft)
        self.assertIn("varG_N5_N5 = multcase_G_B3_N2_N2;", draft)
        self.assertNotIn("multcase_G_B3_N2_N2 = Ga + Gb + Gc_0;", draft)
        self.assertNotIn("multcase_G_B3_N2_N2 = Ga + Gb + Gc_1;", draft)

    def test_runtime_overlay_direct_preview_uses_local_stamp_values(self):
        deps = _deps("Ga", "Gb", code=("Gc_0", "Gc_1"))
        base_payload = _runtime_overlay_payload_with_direct_stamps("Gc_0")
        base_payload["symbol_dependency_table"] = dict(deps)
        base_payload["symbol_dependency_table_tagged"] = dict(deps)
        request = {
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {
                    "name": "case 0",
                    "case_map": {},
                    "payload": base_payload,
                }
            ],
            "runtime_case_groups": [
                {
                    "branch_id": "B3",
                    "name": "R3",
                    "case_id_symbol": "runtime_R3_case_id",
                    "cases": [
                        {
                            "index": 0,
                            "name": "Case 0",
                            "payloads": [_runtime_overlay_payload_with_direct_stamps("Gc_0")],
                        },
                        {
                            "index": 1,
                            "name": "Case 1",
                            "payloads": [_runtime_overlay_payload_with_direct_stamps("Gc_1")],
                        },
                    ],
                }
            ],
        }
        for case in request["runtime_case_groups"][0]["cases"]:
            payload = case["payloads"][0]
            payload["symbol_dependency_table"] = dict(deps)
            payload["symbol_dependency_table_tagged"] = dict(deps)

        response = build_multi_case_response(request)
        multi = response["multi_case"]
        direct = multi["template_direct_retained"]
        gdirect_text = str(direct["Gred_direct"])
        ram_text = str(direct["Gred_direct_ram"])
        code_text = str(direct["Gred_direct_code"])

        self.assertIn("Ga + Gb + multcase_G_B3_N2_N2", gdirect_text)
        self.assertIn("Ga + Gb", ram_text)
        self.assertIn("multcase_G_B3_N2_N2", code_text)
        self.assertNotIn("3*Ga", gdirect_text)
        self.assertNotIn("3*Gb", gdirect_text)
        self.assertNotIn("3*multcase_G_B3_N2_N2", gdirect_text)
        self.assertNotIn("RETAINED_NODES", multi["c_draft"])
        self.assertNotIn("INTERNAL_NODES", multi["c_draft"])

    def test_init_case_mixed_expression_keeps_fixed_overlay_out_of_code_alias(self):
        deps = _deps("Ga", "Gb", "Y", "Gc_0", code=("A", "Z", "Gc_1"))
        profiles = []
        for index, expr in enumerate(["Y + Gc_0", "A*Z + Y + Gc_1"]):
            payload = _runtime_overlay_payload_with_direct_stamps(expr)
            payload["symbol_dependency_table"] = dict(deps)
            payload["symbol_dependency_table_tagged"] = dict(deps)
            profiles.append({
                "name": f"case {index}",
                "case_map": {"B3": index},
                "payload": payload,
            })

        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": profiles,
        })
        multi = response["multi_case"]
        draft = multi["c_draft"]
        direct = multi["template_direct_retained"]
        gdirect_text = str(direct["Gred_direct"])
        ram_text = str(direct["Gred_direct_ram"])
        code_text = str(direct["Gred_direct_code"])

        self.assertIn("Ga + Gb + multcase_G_B3_N2_N2", gdirect_text)
        self.assertIn("Ga + Gb", ram_text)
        self.assertIn("multcase_G_B3_N2_N2", code_text)
        self.assertNotIn("3*Ga", gdirect_text)
        self.assertNotIn("3*Gb", gdirect_text)
        self.assertNotIn("3*multcase_G_B3_N2_N2", gdirect_text)
        self.assertNotIn("3 · Ga", gdirect_text)
        self.assertNotIn("3 · Gb", gdirect_text)

        self.assertIn("g_mat_over[1][1] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[1][3] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[3][1] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[3][3] = -Ga - Gb", draft)
        self.assertNotIn("multcase_G_B3_N2_N2 = Ga + Gb", draft)
        self.assertNotIn("multcase_G_B3_N2_N2 = A*Z + Ga + Gb", draft)

    def test_init_case_source_stamp_alias_keeps_case_specific_ram_terms_out_of_code(self):
        deps_case0 = _deps("Ga", "Gb", code=("A", "Z", "Y", "Gc_0"))
        deps_case1 = _deps("Ga", "Gb", "Y", code=("A", "Z", "Gc_1"))
        profiles = []
        for index, (expr, deps) in enumerate([
            ("A*Z + Gc_0 + Y", deps_case0),
            ("A*Z + Gc_1 + Y", deps_case1),
        ]):
            payload = _runtime_overlay_payload_with_direct_stamps(expr)
            payload["symbol_dependency_table"] = dict(deps)
            payload["symbol_dependency_table_tagged"] = dict(deps)
            profiles.append({
                "name": f"case {index}",
                "case_map": {"B3": index},
                "payload": payload,
            })

        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": profiles,
        })
        draft = response["multi_case"]["c_draft"]
        direct = response["multi_case"]["template_direct_retained"]
        gdirect_text = str(direct["Gred_direct"])

        self.assertIn("Ga + Gb + multcase_G_B3_N2_N2", gdirect_text)
        self.assertNotIn("3*Ga", gdirect_text)
        self.assertNotIn("3*Gb", gdirect_text)
        self.assertNotIn("3*multcase_G_B3_N2_N2", gdirect_text)

        self.assertIn("g_mat_over[1][1] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[1][3] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[3][1] = Ga + Gb", draft)
        self.assertNotIn("g_mat_over[3][3] = -Ga - Gb", draft)

        self.assertIn("g_mat_over[1][1] = Ga + Gb + Y;", draft)
        self.assertIn("g_mat_over[1][3] = -Y;", draft)
        self.assertIn("g_mat_over[3][3] = Y;", draft)
        self.assertIn("setupGMatrix(3);", draft)
        self.assertIn("setupGMatrix(4);", draft)
        ram_switch = draft.split("Case-conditional RAM final-G stamp", 1)[1]
        case0_block = ram_switch.split("case 0:", 1)[1].split("case 1:", 1)[0]
        case1_block = ram_switch.split("case 1:", 1)[1].split("default:", 1)[0]
        self.assertIn('g_mat_nods[2] = getNodeNum(comp, "N3");', case0_block)
        self.assertNotIn('getNodeNum(comp, "N5")', case0_block)
        self.assertIn('g_mat_nods[3] = getNodeNum(comp, "N5");', case1_block)
        self.assertIn("multcase_G_B3_N2_N2 = A*Z + Gc_0 + Y;", draft)
        self.assertIn("varG_N2_N2 = multcase_G_B3_N2_N2;", draft)
        self.assertIn("varG_N2_N2 = A*Z + Gc_1;", draft)
        self.assertIn("varG_N2_N5 = -A*Z - Gc_1;", draft)
        self.assertNotIn("multcase_G_B3_N2_N2 = A*Z + Ga + Gb", draft)
        self.assertNotIn("varG_N2_N2 = A*Z + Gc_1 + Y;", draft)
        self.assertNotIn("Gc_0 + Gc_1 -", draft)
        self.assertNotIn("RETAINED_NODES", draft)
        self.assertNotIn("INTERNAL_NODES", draft)

    def test_runtime_mutable_source_stamp_does_not_use_complement_alias(self):
        deps = _deps("Ga", "Gb", "Y", code=("A", "Z", "Gc_0", "Gc_1"))
        base_payload = _runtime_overlay_payload_with_direct_stamps("A*Z + Gc_0 + Y")
        base_payload["symbol_dependency_table"] = dict(deps)
        base_payload["symbol_dependency_table_tagged"] = dict(deps)
        runtime_payloads = []
        for expr in ["A*Z + Gc_0 + Y", "A*Z + Gc_1 + Y"]:
            payload = _runtime_overlay_payload_with_direct_stamps(expr)
            payload["symbol_dependency_table"] = dict(deps)
            payload["symbol_dependency_table_tagged"] = dict(deps)
            runtime_payloads.append(payload)

        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "case_profiles": [
                {"name": "case 0", "case_map": {}, "payload": base_payload},
            ],
            "runtime_case_groups": [
                {
                    "branch_id": "B3",
                    "name": "R3",
                    "case_id_symbol": "runtime_R3_case_id",
                    "cases": [
                        {"index": 0, "name": "Case 0", "payloads": [runtime_payloads[0]]},
                        {"index": 1, "name": "Case 1", "payloads": [runtime_payloads[1]]},
                    ],
                }
            ],
        })

        draft = response["multi_case"]["c_draft"]
        direct = response["multi_case"]["template_direct_retained"]
        gdirect_text = str(direct["Gred_direct"])
        code_text = str(direct["Gred_direct_code"])

        self.assertNotIn("Gc_0 + Gc_1 -", draft)
        self.assertNotIn("Gc_0 + Gc_1 -", gdirect_text)
        self.assertNotIn("Gc_0 + Gc_1 -", code_text)
        self.assertNotIn("Gc_0 + Gc_1 -", str(response["multi_case"]["aliases"]))
        self.assertNotIn("multcase_G_B3_N2_N5", draft)
        self.assertIn("multcase_G_B3_N2_N2 = Gc_0;", draft)
        self.assertIn("multcase_G_B3_N2_N2 = Gc_1;", draft)
        self.assertIn("G_N2_N2 = A*Z + multcase_G_B3_N2_N2;", draft)
        self.assertIn("varG_N2_N2 = G_N2_N2;", draft)
        self.assertIn("varG_N2_N5 = -G_N2_N2;", draft)
        self.assertIn("varG_N5_N5 = G_N2_N2;", draft)


if __name__ == "__main__":
    unittest.main()
