import unittest
import json
from pathlib import Path
from unittest.mock import patch

import optimized_elimination_api as optimized_api
from optimized_elimination_api import build_multi_case_response


def _payload(*, physical: bool, trf_case: int = 0, ucm_case: int = 0, rc_case: int = 0) -> dict:
    nodes = ["A", "B", "x", "y", "z"]
    gtrf = "GT1" if trf_case == 0 else "GT2"
    gucm = "GU1" if ucm_case == 0 else "GU2"
    if physical:
        external = ["A", "B"]
        internal = ["x", "y", "z"]
        g_block = [
            ["Gp + Gx", "-Gp", "-Gx", "0", "0"],
            ["-Gp", "Gp + Gy", "0", "-Gy", "0"],
            ["-Gx", "0", "Gx + Gz", "-Gz", "0"],
            ["0", "-Gy", "-Gz", "Gy + Gz + Gw", "-Gw"],
            ["0", "0", "0", "-Gw", "Gw"],
        ]
        ihis = ["0", "0", "Hx", "Hy", "Hz"]
        dummy_blocks = []
        defer = False
    else:
        external = ["A", "B", "x", "y", "z"]
        internal = []
        g_block = [
            [f"{gtrf}", "0", "0", "0", "0"],
            ["0", f"{gucm}", "0", "0", "0"],
            ["0", "0", "G_EPSILON", "0", "0"],
            ["0", "0", "0", "G_EPSILON", "0"],
            ["0", "0", "0", "0", "G_EPSILON"],
        ]
        ihis = ["0", "0", "0", "0", "0"]
        dummy_blocks = [
            {
                "element_type": "dummy_node_block",
                "block_id": "RC_Dummy",
                "dimension": 3,
                "fixed_conductance": "G_EPSILON",
                "forced_elimination": True,
                "recoverable": False,
                "dummy_nodes": [
                    {"node_id": "x", "display_name": "x", "dummy_role": "isolated_dummy_internal"},
                    {"node_id": "y", "display_name": "y", "dummy_role": "isolated_dummy_internal"},
                    {"node_id": "z", "display_name": "z", "dummy_role": "isolated_dummy_internal"},
                ],
            }
        ]
        defer = True

    g_block[0][0] = f"{g_block[0][0]} + {gtrf}"
    g_block[1][1] = f"{g_block[1][1]} + {gucm}"
    return {
        "all_nodes": nodes,
        "node_display_names": {
            "A": "A",
            "B": "B",
            "x": "RC_A",
            "y": "RC_B",
            "z": "RC_C",
        },
        "external_nodes": external,
        "internal_nodes": internal,
        "G_full": g_block,
        "Ihis_full": [[item] for item in ihis],
        "dummy_node_blocks": dummy_blocks,
        "defer_dummy_node_blocks": defer,
        "symbol_dependency_table": {
            "GT1": "RAM_CONSTANT",
            "GT2": "CODE_VARIABLE",
            "GU1": "RAM_CONSTANT",
            "GU2": "CODE_VARIABLE",
            "Gp": "RAM_CONSTANT",
            "Gx": "RAM_CONSTANT",
            "Gy": "RAM_CONSTANT",
            "Gz": "RAM_CONSTANT",
            "Gw": "RAM_CONSTANT",
            "G_EPSILON": "RAM_CONSTANT",
            "Hx": "STEP_HISTORY",
            "Hy": "STEP_HISTORY",
            "Hz": "STEP_HISTORY",
        },
    }


def _profiles() -> list[dict]:
    out = []
    index = 0
    for trf in [0, 1]:
        for ucm in [0, 1]:
            for rc in [0, 1]:
                out.append({
                    "name": f"case {index}",
                    "case_map": {"Trf": trf, "UCM": ucm, "RC": rc},
                    "payload": _payload(physical=rc == 0, trf_case=trf, ucm_case=ucm, rc_case=rc),
                })
                index += 1
    return out


class MultiCaseCommonDummyInternalCodegenTests(unittest.TestCase):
    def test_isolated_dummy_internal_uses_shared_alias_template_not_finalization(self):
        with patch.object(optimized_api, "_build_dummy_finalized_multi_case_c_draft", wraps=optimized_api._build_dummy_finalized_multi_case_c_draft) as finalization_spy:
            response = build_multi_case_response({
                "mode": "multi_case_c_export",
                "case_id_symbol": "case_id",
                "common_dummy_internal_nodes": ["x", "y", "z"],
                "case_profiles": _profiles(),
            })

        multi = response["multi_case"]
        draft = multi["c_draft"]
        diagnostics = multi["diagnostics"]
        self.assertEqual(finalization_spy.call_count, 0)
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertEqual(diagnostics["global_case_count"], 8)
        self.assertEqual(diagnostics["local_case_group_count"], 3)
        self.assertEqual(diagnostics["mixed_physical_dummy_internal"], ["x", "y", "z"])
        self.assertEqual(diagnostics["dummy_finalization_profile_count"], 0)
        self.assertEqual(diagnostics["per_case_full_reduction_count"], 0)
        self.assertEqual(diagnostics["per_case_final_substitution_count"], 0)
        self.assertEqual(diagnostics["expanded_scalar_ccode_count"], 0)
        self.assertIn("matrix_mult_CODE", draft)
        self.assertIn("Recovery profiles: DummyNodeBlock isolated internal nodes are not recovered.", draft)
        self.assertEqual(draft.count("RC_A = get_CODE(&Vk_code"), 1)
        self.assertEqual(draft.count("RC_B = get_CODE(&Vk_code"), 1)
        self.assertEqual(draft.count("RC_C = get_CODE(&Vk_code"), 1)
        self.assertNotIn("    x = get_CODE(&Vk_code", draft)
        self.assertNotIn("    y = get_CODE(&Vk_code", draft)
        self.assertNotIn("    z = get_CODE(&Vk_code", draft)
        self.assertNotIn("G_EPSILON", draft)
        self.assertNotIn("One variable per eliminated node, in effective k order.", draft)
        self.assertNotIn("NR_FINAL_MAX", draft)
        self.assertNotIn("Piecewise", draft)

    def test_trf_rcy_ucm_reports_per_profile_gred_entry_reuse(self):
        data = json.loads(Path("exports/Trf_RCY_UCM.json").read_text(encoding="utf-8"))
        cache_key = data["multiCaseExportCache"][0]["key"]
        payload = json.loads(cache_key)
        payload["mode"] = "multi_case_c_export"

        response = build_multi_case_response(payload)

        draft = response["multi_case"]["c_draft"]
        stamp_section = draft.split("/* Stamp dynamic Gred entries", 1)[1].split("/* Ihisred", 1)[0]
        common_assignment = "varG_A_1_G = get_CODE(&Gred_code, 0, 3);"
        self.assertEqual(stamp_section.count(common_assignment), 1)
        self.assertLess(stamp_section.index(common_assignment), stamp_section.index("switch (case_id) {"))
        groups = response["multi_case"].get("gred_entry_reuse_by_case") or []
        by_case = {
            group.get("case_index"): {
                item.get("text")
                for item in (group.get("items") or [])
            }
            for group in groups
        }
        self.assertIn("Gred[B_1,RC_A] = -Gred[A_1,RC_A]", by_case.get(6, set()))
        self.assertIn("Gred[C_1,RC_B] = -Gred[B_1,RC_B]", by_case.get(6, set()))
        self.assertIn("Gred[C_1,RC_C] = -Gred[A_1,RC_C]", by_case.get(6, set()))

    def test_trf_rcy_ucm_force_scalar_preflight_blocks_large_scalar_codegen(self):
        data = json.loads(Path("exports/Trf_RCY_UCM.json").read_text(encoding="utf-8"))
        cache_key = data["multiCaseExportCache"][0]["key"]
        payload = json.loads(cache_key)
        payload["mode"] = "multi_case_c_export"
        payload["elimination_codegen_mode"] = "force_scalar"

        response = build_multi_case_response(payload)

        multi = response["multi_case"]
        preflight = multi["scalar_preflight"]
        self.assertEqual(multi["fast_path"], "case_scalar_preflight_blocked")
        self.assertEqual(preflight["severity"], "danger")
        self.assertTrue(preflight["blocked"])
        self.assertGreater(preflight["total_ops"], 10000)
        self.assertIn("Scalar-expanded C draft was not generated", multi["c_draft"])

    def test_trf_rcy_ucm_profiled_diagonal_cases_use_scalar_path_without_bare_nr(self):
        data = json.loads(Path("exports/Trf_RCY_UCM.json").read_text(encoding="utf-8"))
        cache_key = data["multiCaseExportCache"][0]["key"]
        payload = json.loads(cache_key)
        payload["mode"] = "multi_case_c_export"

        response = build_multi_case_response(payload)

        draft = response["multi_case"]["c_draft"]
        self.assertIn(
            "enum { PACK_CASE_0 = 0, PACK_CASE_1 = 1, RETAINED_NODES_CASE_0 = 9, RETAINED_NODES_CASE_1 = 6, INTERNAL_NODES = 3 };",
            draft,
        )
        self.assertIn("/* Multi-case retained-layout constants:", draft)
        self.assertIn("PACK_CASE_n identifies a group of case_id values", draft)
        self.assertIn("RETAINED_NODES_CASE_n is the active retained-node count", draft)
        self.assertIn("INTERNAL_NODES is the number of eliminated internal nodes", draft)
        self.assertNotIn("for (int col = 0; col < NR; col++)", draft)
        self.assertIn("network_node_recover_vk_diag(node_active, INTERNAL_NODES", draft)
        self.assertIn("network_node_recover_vk_matrix(node_active, INTERNAL_NODES", draft)
        self.assertIn("Case-resolved diagonal Gkk scalar Schur/Ihis path", draft)
        scalar_marker = draft.index("Case-resolved diagonal Gkk scalar Schur/Ihis path")
        diagonal_block_start = draft.index("case 4:", scalar_marker)
        diagonal_block_end = draft.index("case 0:", diagonal_block_start)
        diagonal_block = draft[diagonal_block_start:diagonal_block_end]
        self.assertIn("case 6:", diagonal_block)
        self.assertNotIn("case 11:", draft)
        self.assertIn("double IC_his0 = 0.0;", draft)
        self.assertNotIn("double inv_gkk_diag[INTERNAL_NODES];", draft)
        self.assertIn("get_CODE(&Grk_code, row, k) * get_CODE(&W_code, k, k)", diagonal_block)
        self.assertIn("get_CODE(Ihisk_code, k, 0) * get_CODE(W_code, k, k)", draft)
        self.assertEqual(diagonal_block.count("/ get_CODE(&Gkk_code, k, k)"), 0)
        self.assertNotIn("/ gkk_diag", diagonal_block)
        self.assertNotIn("matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);", diagonal_block)
        self.assertNotIn("matrix_mult_CODE(&tmp_Grk_W_Gkr_code, &tmp_Grk_W_code, &Gkr_code);", diagonal_block)
        fallback_block = draft[diagonal_block_end:]
        self.assertIn("matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);", fallback_block)
        self.assertNotIn("matrix_mult_CODE(&tmp_Grk_W_Gkr_code, &tmp_Grk_W_code, &Gkr_code);", fallback_block)
        self.assertIn("Symmetry reuse: multiply by transpose(Grk_code) without materializing Gkr.", fallback_block)
        self.assertIn("for (col = row; col < node_active; col++)", fallback_block)
        self.assertNotIn("for (int ", draft)
        self.assertIn("CODE_FUNCTIONS:", draft)
        code_functions = draft.split("CODE_FUNCTIONS:", 1)[1].split("CODE:", 1)[0]
        self.assertIn("void network_node_recover_vk_diag", code_functions)
        self.assertIn("void network_node_recover_vk_matrix", code_functions)
        self.assertNotIn("void network_node_recover_vk_from_wgkr_only", code_functions)
        vk_switch = draft.split("Case-resolved diagonal Gkk scalar Vk recovery path", 1)[1].split(
            "/* One variable per eliminated node",
            1,
        )[0]
        t1_t2_prelude = draft.split("T1_T2:", 1)[1].split(
            "/* Case-resolved diagonal Gkk scalar Vk recovery path",
            1,
        )[0]
        self.assertNotIn("int row;", t1_t2_prelude)
        self.assertNotIn("int col;", t1_t2_prelude)
        self.assertIn("network_node_recover_vk_diag(node_active, INTERNAL_NODES", vk_switch)
        self.assertIn("network_node_recover_vk_matrix(node_active, INTERNAL_NODES", vk_switch)
        self.assertNotIn("for (int k = 0; k < INTERNAL_NODES; k++)", vk_switch)
        self.assertNotIn("matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);", vk_switch)


if __name__ == "__main__":
    unittest.main()
