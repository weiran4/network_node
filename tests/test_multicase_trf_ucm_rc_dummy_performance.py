import time
import unittest

from optimized_elimination_api import build_multi_case_response
from tests.test_multicase_common_dummy_internal_codegen import _profiles


class MultiCaseTrfUcmRcDummyPerformanceTests(unittest.TestCase):
    def test_eight_case_trf_ucm_rc_dummy_export_is_seconds_not_minutes(self):
        started = time.perf_counter()
        response = build_multi_case_response({
            "mode": "multi_case_c_export",
            "case_id_symbol": "case_id",
            "common_dummy_internal_nodes": ["x", "y", "z"],
            "case_profiles": _profiles(),
        })
        elapsed = time.perf_counter() - started

        multi = response["multi_case"]
        diagnostics = multi["diagnostics"]
        self.assertLess(elapsed, 30.0)
        self.assertEqual(multi["fast_path"], "case_alias_template")
        self.assertEqual(diagnostics["global_case_count"], 8)
        self.assertEqual(diagnostics["shared_inverse_codegen_count"], 1)
        self.assertEqual(diagnostics["per_case_inverse_codegen_count"], 0)
        self.assertEqual(diagnostics["per_case_full_reduction_count"], 0)
        self.assertGreater(diagnostics["c_output_length"], 0)
        timing = diagnostics["timing_ms"]
        for key in [
            "parse_payload",
            "build_global_local_case_map",
            "classify_node_roles",
            "build_effective_aliases",
            "build_assembled_entry_aliases",
            "assemble_super_matrices",
            "partition_blocks",
            "build_inverse_dag",
            "build_shared_reduced_dag",
            "build_recovery_profiles",
            "build_finalization_profiles",
            "build_conditional_gvalues",
            "generate_c_text",
            "optional_validation",
            "alias_template_pipeline",
            "dummy_finalization_pipeline",
            "per_case_fallback_pipeline",
            "total",
        ]:
            self.assertIn(key, timing)
        self.assertGreater(timing["alias_template_pipeline"], 0)
        self.assertEqual(timing["dummy_finalization_pipeline"], 0.0)
        self.assertEqual(timing["per_case_fallback_pipeline"], 0.0)


if __name__ == "__main__":
    unittest.main()
