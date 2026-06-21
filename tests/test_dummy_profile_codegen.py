import unittest

import sympy as sp

from nodal_tool.multicase_finalization_profiles import (
    build_finalization_profiles,
    finalize_profile_result,
)


class DummyProfileCodegenTests(unittest.TestCase):
    def test_profiles_preserve_case_specific_node_roles(self):
        profiles = [
            {
                "name": "Case 0",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
            },
            {
                "name": "Case 1",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
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
        ]

        result = build_finalization_profiles(profiles)

        self.assertEqual(result.super_node_order, ["N1", "N2", "N4"])
        self.assertEqual(result.case_profiles[0].final_node_order, ["N1", "N2", "N4"])
        self.assertEqual(result.case_profiles[0].final_dimension, 3)
        self.assertEqual(result.case_profiles[1].final_node_order, ["N1", "N2"])
        self.assertEqual(result.case_profiles[1].dummy_nodes, ["N4"])
        self.assertEqual(result.case_profiles[1].final_dimension, 2)
        self.assertEqual(result.case_profiles[1].node_roles["N4"], "dummy_final")
        self.assertEqual(result.case_profiles[1].node_roles["N2"], "retained_physical")

    def test_dummy_profile_finalization_removes_epsilon_anchor_stamp(self):
        R, G_EPSILON = sp.symbols("R G_EPSILON", nonzero=True)
        profiles = build_finalization_profiles([
            {
                "name": "Case 1",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
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
            }
        ])
        profile = profiles.case_profiles[0]
        G = sp.Matrix([
            [1 / R, -1 / R, 0],
            [-1 / R, 1 / R + G_EPSILON, -G_EPSILON],
            [0, -G_EPSILON, G_EPSILON],
        ])
        Ihis = sp.zeros(3, 1)

        final = finalize_profile_result(G, Ihis, ["N1", "N2", "N4"], profile)

        self.assertEqual(final.nodes, ["N1", "N2"])
        self.assertEqual(sp.simplify(final.G[0, 0] - 1 / R), 0)
        self.assertEqual(sp.simplify(final.G[0, 1] + 1 / R), 0)
        self.assertEqual(sp.simplify(final.G[1, 0] + 1 / R), 0)
        self.assertEqual(sp.simplify(final.G[1, 1] - 1 / R), 0)
        self.assertNotIn(G_EPSILON, final.G.free_symbols)
        self.assertEqual(final.Ihis, sp.zeros(2, 1))

    def test_multiple_dummy_leaves_are_removed_without_leaking_epsilon(self):
        R, G_EPSILON, G_PAD = sp.symbols("R G_EPSILON G_PAD", nonzero=True)
        profiles = build_finalization_profiles([
            {
                "name": "Case with two dummies",
                "payload": {"external_nodes": ["N1", "N2", "N4", "N5"]},
                "dummy_finalization": {
                    "dummy_leaves": [
                        {
                            "dummy_node": "N4",
                            "anchor_node": "N2",
                            "branch_id": "Dummy12",
                            "conductance": "G_EPSILON",
                        },
                        {
                            "dummy_node": "N5",
                            "anchor_node": "N1",
                            "branch_id": "Dummy13",
                            "conductance": "G_PAD",
                        },
                    ]
                },
            }
        ])
        G = sp.Matrix([
            [1 / R + G_PAD, -1 / R, 0, -G_PAD],
            [-1 / R, 1 / R + G_EPSILON, -G_EPSILON, 0],
            [0, -G_EPSILON, G_EPSILON, 0],
            [-G_PAD, 0, 0, G_PAD],
        ])
        Ihis = sp.zeros(4, 1)

        final = finalize_profile_result(G, Ihis, ["N1", "N2", "N4", "N5"], profiles.case_profiles[0])

        self.assertEqual(final.nodes, ["N1", "N2"])
        self.assertEqual(sp.simplify(final.G[0, 0] - 1 / R), 0)
        self.assertEqual(sp.simplify(final.G[0, 1] + 1 / R), 0)
        self.assertEqual(sp.simplify(final.G[1, 0] + 1 / R), 0)
        self.assertEqual(sp.simplify(final.G[1, 1] - 1 / R), 0)
        self.assertFalse({G_EPSILON, G_PAD} & final.G.free_symbols)

    def test_same_dummy_profile_is_shared_by_multiple_cases(self):
        profile_set = build_finalization_profiles([
            {
                "name": "physical",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
            },
            {
                "name": "dummy A",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N2", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            },
            {
                "name": "dummy B",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N2", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            },
        ])

        self.assertEqual(len(profile_set.unique_profiles), 2)
        self.assertEqual(profile_set.case_profiles[1].profile_id, profile_set.case_profiles[2].profile_id)
        self.assertEqual(profile_set.case_profiles[1].case_ids, (1, 2))

    def test_invalid_dummy_coupling_is_rejected(self):
        R, G_EPSILON = sp.symbols("R G_EPSILON", nonzero=True)
        profile_set = build_finalization_profiles([
            {
                "name": "bad dummy",
                "payload": {"external_nodes": ["N1", "N2", "N4"]},
                "dummy_finalization": {
                    "dummy_leaves": [
                        {"dummy_node": "N4", "anchor_node": "N2", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                    ]
                },
            }
        ])
        G = sp.Matrix([
            [1 / R, -1 / R, "1/R"],
            [-1 / R, 1 / R + G_EPSILON, -G_EPSILON],
            ["1/R", -G_EPSILON, G_EPSILON],
        ])

        with self.assertRaisesRegex(ValueError, "Dummy12.*coupled to N1"):
            finalize_profile_result(G, sp.zeros(3, 1), ["N1", "N2", "N4"], profile_set.case_profiles[0])

    def test_dummy_metadata_rejects_internal_recovery_observer_and_control_references(self):
        base = {
            "name": "dummy case",
            "payload": {"external_nodes": ["N1", "N2", "N4"]},
            "dummy_finalization": {
                "dummy_leaves": [
                    {"dummy_node": "N4", "anchor_node": "N2", "branch_id": "Dummy12", "conductance": "G_EPSILON"}
                ]
            },
        }

        with self.assertRaisesRegex(ValueError, "dummy node N4 cannot be a common physical internal node"):
            build_finalization_profiles([{**base, "payload": {**base["payload"], "internal_nodes": ["N4"]}}])

        with self.assertRaisesRegex(ValueError, "anchor node N2 cannot be a common physical internal node"):
            build_finalization_profiles([{**base, "payload": {**base["payload"], "internal_nodes": ["N2"]}}])

        with self.assertRaisesRegex(ValueError, "dummy node N4 cannot be a physical recovery node"):
            build_finalization_profiles([{**base, "payload": {**base["payload"], "effective_internal_nodes": ["N4"]}}])

        with self.assertRaisesRegex(ValueError, "dummy node N4 cannot appear in observers"):
            build_finalization_profiles([{**base, "payload": {**base["payload"], "observers": [{"from": "N2", "to": "N4"}]}}])

        with self.assertRaisesRegex(ValueError, "dummy node N4 cannot appear in controlled_sources"):
            build_finalization_profiles([{**base, "payload": {**base["payload"], "controlled_sources": [{"controlPositive": "N4"}]}}])


if __name__ == "__main__":
    unittest.main()
