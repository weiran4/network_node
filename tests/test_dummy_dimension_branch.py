import unittest

import sympy as sp

from nodal_tool.dummy_finalization import (
    DummyLeaf,
    finalize_dummy_leaves,
    validate_dummy_leaf_structure,
)


class DummyDimensionBranchTests(unittest.TestCase):
    def test_leaf_finalization_cancels_anchor_epsilon_exactly(self):
        Gbase, G_EPSILON = sp.symbols("Gbase G_EPSILON", nonzero=True)
        G = sp.Matrix([[Gbase + G_EPSILON, -G_EPSILON], [-G_EPSILON, G_EPSILON]])
        Ihis = sp.Matrix([[sp.Symbol("I_anchor")], [0]])

        result = finalize_dummy_leaves(
            G,
            Ihis,
            ["A", "D"],
            [DummyLeaf(dummy_node="D", anchor_node="A", branch_id="DB1", conductance=G_EPSILON)],
        )

        self.assertEqual(result.nodes, ["A"])
        self.assertEqual(sp.simplify(result.G[0, 0] - Gbase), 0)
        self.assertEqual(result.Ihis[0, 0], sp.Symbol("I_anchor"))

        schur = G[:1, :1] - G[:1, 1:] * G[1:, 1:].inv() * G[1:, :1]
        self.assertEqual(sp.simplify(result.G[0, 0] - schur[0, 0]), 0)

    def test_dummy_leaf_rejects_extra_coupling(self):
        G_EPSILON = sp.Symbol("G_EPSILON", nonzero=True)
        G = sp.Matrix([
            [G_EPSILON, -G_EPSILON, 0],
            [-G_EPSILON, G_EPSILON, sp.Symbol("X")],
            [0, sp.Symbol("X"), 1],
        ])
        Ihis = sp.zeros(3, 1)

        with self.assertRaisesRegex(ValueError, "coupled"):
            validate_dummy_leaf_structure(
                G,
                Ihis,
                ["A", "D", "B"],
                DummyLeaf(dummy_node="D", anchor_node="A", branch_id="DB1", conductance=G_EPSILON),
            )

    def test_dummy_leaf_rejects_nonzero_ihis(self):
        G_EPSILON = sp.Symbol("G_EPSILON", nonzero=True)
        G = sp.Matrix([[G_EPSILON, -G_EPSILON], [-G_EPSILON, G_EPSILON]])
        Ihis = sp.Matrix([[0], [sp.Symbol("H")]])

        with self.assertRaisesRegex(ValueError, "Ihis"):
            validate_dummy_leaf_structure(
                G,
                Ihis,
                ["A", "D"],
                DummyLeaf(dummy_node="D", anchor_node="A", branch_id="DB1", conductance=G_EPSILON),
            )

    def test_real_network_is_unchanged_after_legal_dummy_finalization(self):
        G_EPSILON = sp.Symbol("G_EPSILON", nonzero=True)
        GA, GB, GAB, IA, IB = sp.symbols("GA GB GAB IA IB")
        base_G = sp.Matrix([[GA, GAB], [GAB, GB]])
        G = sp.Matrix([
            [GA + G_EPSILON, GAB, -G_EPSILON],
            [GAB, GB, 0],
            [-G_EPSILON, 0, G_EPSILON],
        ])
        Ihis = sp.Matrix([[IA], [IB], [0]])

        result = finalize_dummy_leaves(
            G,
            Ihis,
            ["A", "B", "D"],
            [DummyLeaf(dummy_node="D", anchor_node="A", branch_id="DB1", conductance=G_EPSILON)],
        )

        self.assertEqual(result.nodes, ["A", "B"])
        self.assertEqual(sp.simplify(result.G - base_G), sp.zeros(2, 2))
        self.assertEqual(result.Ihis, sp.Matrix([[IA], [IB]]))

    def test_multiple_dummy_leaves_are_order_independent(self):
        G_EPSILON = sp.Symbol("G_EPSILON", nonzero=True)
        GA, GB, GAB, IA, IB = sp.symbols("GA GB GAB IA IB")
        G = sp.Matrix([
            [GA + G_EPSILON, GAB, -G_EPSILON, 0],
            [GAB, GB + G_EPSILON, 0, -G_EPSILON],
            [-G_EPSILON, 0, G_EPSILON, 0],
            [0, -G_EPSILON, 0, G_EPSILON],
        ])
        Ihis = sp.Matrix([[IA], [IB], [0], [0]])
        leaves = [
            DummyLeaf(dummy_node="DA", anchor_node="A", branch_id="DB1", conductance=G_EPSILON),
            DummyLeaf(dummy_node="DB", anchor_node="B", branch_id="DB2", conductance=G_EPSILON),
        ]

        forward = finalize_dummy_leaves(G, Ihis, ["A", "B", "DA", "DB"], leaves)
        backward = finalize_dummy_leaves(G, Ihis, ["A", "B", "DA", "DB"], list(reversed(leaves)))

        self.assertEqual(forward.nodes, ["A", "B"])
        self.assertEqual(backward.nodes, ["A", "B"])
        self.assertEqual(sp.simplify(forward.G - backward.G), sp.zeros(2, 2))
        self.assertEqual(forward.Ihis, backward.Ihis)


if __name__ == "__main__":
    unittest.main()
