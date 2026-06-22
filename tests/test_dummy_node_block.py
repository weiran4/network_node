import unittest

import sympy as sp

from nodal_tool.dummy_node_block_model import (
    DummyNodeBlock,
    stamp_dummy_node_blocks,
    validate_dummy_node_blocks,
)
from optimized_elimination_api import build_optimized_response


class DummyNodeBlockModelTests(unittest.TestCase):
    def test_n1_block_stamps_isolated_diagonal(self):
        block = DummyNodeBlock(block_id="DNB1", dummy_nodes=("a",))
        G, Ihis = stamp_dummy_node_blocks(["A", "a"], [block])

        self.assertEqual(G, sp.Matrix([[0, 0], [0, sp.Symbol("G_EPSILON")]]))
        self.assertEqual(Ihis, sp.Matrix([[0], [0]]))
        validate_dummy_node_blocks(G, Ihis, ["A", "a"], [block], common_internal_nodes=["a"])

    def test_n2_block_is_not_two_terminal_dummy_branch(self):
        block = DummyNodeBlock(block_id="DNB2", dummy_nodes=("a", "b"))
        G, Ihis = stamp_dummy_node_blocks(["a", "b"], [block])

        self.assertEqual(
            G,
            sp.Matrix(
                [
                    [sp.Symbol("G_EPSILON"), 0],
                    [0, sp.Symbol("G_EPSILON")],
                ]
            ),
        )
        self.assertNotEqual(G[0, 1], -sp.Symbol("G_EPSILON"))
        validate_dummy_node_blocks(G, Ihis, ["a", "b"], [block], common_internal_nodes=["a", "b"])

    def test_n3_block_stamps_sparse_identity(self):
        block = DummyNodeBlock(block_id="DNB3", dummy_nodes=("a", "b", "c"))
        G, Ihis = stamp_dummy_node_blocks(["a", "b", "c"], [block])

        self.assertEqual(G, sp.diag(*([sp.Symbol("G_EPSILON")] * 3)))
        self.assertEqual(Ihis, sp.zeros(3, 1))
        validate_dummy_node_blocks(G, Ihis, ["a", "b", "c"], [block], common_internal_nodes=["a", "b", "c"])

    def test_rejects_coupling_to_real_node(self):
        block = DummyNodeBlock(block_id="DNB1", dummy_nodes=("a",))
        G, Ihis = stamp_dummy_node_blocks(["A", "a"], [block])
        G[0, 1] = sp.Symbol("X")

        with self.assertRaisesRegex(ValueError, "DNB1.*a.*coupled to A"):
            validate_dummy_node_blocks(G, Ihis, ["A", "a"], [block], common_internal_nodes=["a"])

    def test_rejects_retained_dummy_role(self):
        block = DummyNodeBlock(block_id="DNB1", dummy_nodes=("a",))
        G, Ihis = stamp_dummy_node_blocks(["A", "a"], [block])

        with self.assertRaisesRegex(ValueError, "supports only nodes eliminated in every case.*a"):
            validate_dummy_node_blocks(G, Ihis, ["A", "a"], [block], common_internal_nodes=[])

    def test_single_case_optimized_export_skips_dummy_recovery(self):
        result = build_optimized_response(
            {
                "all_nodes": ["A", "d"],
                "external_nodes": ["A"],
                "internal_nodes": ["d"],
                "G_full": [["Gp", "0"], ["0", "G_EPSILON"]],
                "Ihis_full": ["0", "0"],
                "dummy_node_blocks": [
                    {
                        "block_id": "DNB1",
                        "dummy_nodes": [{"node_id": "d", "display_name": "N4"}],
                        "fixed_conductance": "G_EPSILON",
                    }
                ],
            }
        )
        draft = result["structured"]["c_draft"]
        self.assertIn("DummyNodeBlock isolated internal nodes are not recovered", draft)
        self.assertNotIn("d = get_CODE(&Vk_code", draft)

    def test_single_case_optimized_export_drops_dummy_before_schur(self):
        result = build_optimized_response(
            {
                "all_nodes": ["A", "B", "k", "d"],
                "external_nodes": ["A", "B"],
                "internal_nodes": ["k", "d"],
                "G_full": [
                    ["Gp", "0", "-Gp", "0"],
                    ["0", "Gq", "-Gq", "0"],
                    ["-Gp", "-Gq", "Gp + Gq", "0"],
                    ["0", "0", "0", "G_EPSILON"],
                ],
                "Ihis_full": ["0", "0", "0", "0"],
                "dummy_node_blocks": [
                    {
                        "block_id": "DNB1",
                        "dummy_nodes": [{"node_id": "d", "display_name": "DUMMY"}],
                        "fixed_conductance": "G_EPSILON",
                    }
                ],
                "symbol_dependency_table": {
                    "Gp": "RAM_CONSTANT",
                    "Gq": "RAM_CONSTANT",
                    "G_EPSILON": "RAM_CONSTANT",
                },
            }
        )

        draft = result["structured"]["c_draft"]
        self.assertIn("enum { NR = 2, NK = 1 }", draft)
        self.assertEqual(result["internal_nodes"], ["k"])
        self.assertEqual(result["effective_internal_nodes"], ["k"])
        self.assertEqual(result["structured"]["dummy_node_blocks"]["dropped_before_schur"], True)
        self.assertNotIn("G_EPSILON", draft)
        self.assertNotIn("DUMMY = get_CODE(&Vk_code", draft)


if __name__ == "__main__":
    unittest.main()
