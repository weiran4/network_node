import unittest

from optimized_elimination_api import build_optimized_response


class OptimizedDummyCExportTests(unittest.TestCase):
    def test_single_case_optimized_export_finalizes_dummy_leaf(self):
        response = build_optimized_response({
            "mode": "structured_formula",
            "all_nodes": ["N1", "N2", "N4"],
            "external_nodes": ["N1", "N2", "N4"],
            "internal_nodes": [],
            "ground_nodes": [],
            "node_display_names": {"N1": "N1", "N2": "N2", "N4": "N4"},
            "G_full": [
                ["1/R", "-1/R", "0"],
                ["-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                ["0", "-G_EPSILON", "G_EPSILON"],
            ],
            "Ihis_full": ["0", "0", "0"],
            "G_full_tagged": [
                ["1/R", "-1/R", "0"],
                ["-1/R", "1/R + G_EPSILON", "-G_EPSILON"],
                ["0", "-G_EPSILON", "G_EPSILON"],
            ],
            "Ihis_full_tagged": ["0", "0", "0"],
            "direct_retained_stamps": [],
            "symbol_dependency_table": {"R": "RAM_CONSTANT", "G_EPSILON": "RAM_CONSTANT"},
            "symbol_dependency_table_tagged": {"R": "RAM_CONSTANT", "G_EPSILON": "RAM_CONSTANT"},
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
        })

        draft = response["structured"]["c_draft"]

        self.assertEqual(response["structured"]["fast_path"], "single_case_dummy_finalization")
        self.assertEqual(response["external_nodes"], ["N1", "N2"])
        self.assertNotIn('getNodeNum(comp, "N4")', draft)
        self.assertNotIn("G_EPSILON", draft)
        self.assertNotIn("InjN4", draft)
        self.assertIn("setupGMatrix(2);", draft)


if __name__ == "__main__":
    unittest.main()
