import json
import subprocess
import unittest
from pathlib import Path

import sympy as sp

from optimized_elimination_api import build_optimized_response


TEST6_FIXTURE = json.loads(Path("exports/test6.json").read_text(encoding="utf-8"))
TEST6_BRANCH = TEST6_FIXTURE["branches"][0]
TEST6_NODES = [
    TEST6_FIXTURE["nodeLabels"].get(node, node)
    for node in TEST6_FIXTURE["nodeOrder"]
]


def make_test6_payload(g_expr="X+Y+Z", ram_symbols=("X", "Y", "Z")):
    row_node, col_node = TEST6_NODES
    branch_id = TEST6_BRANCH["id"]
    branch_name = TEST6_BRANCH["name"]
    symbols = sorted(str(symbol) for symbol in sp.sympify(g_expr).free_symbols)
    ram_set = set(ram_symbols)
    tagged_expr = "+".join(f"{symbol}__bbsrc_{branch_id}" for symbol in symbols)
    neg_expr = str(-sp.sympify(g_expr))
    neg_tagged_expr = "+".join(f"-{symbol}__bbsrc_{branch_id}" for symbol in symbols)
    symbol_table = {
        symbol: ("RAM_CONSTANT" if symbol in ram_set else "CODE_VARIABLE")
        for symbol in symbols
    }
    tagged_symbol_table = {
        **symbol_table,
        **{
            f"{symbol}__bbsrc_{branch_id}": ("RAM_CONSTANT" if symbol in ram_set else "CODE_VARIABLE")
            for symbol in symbols
        },
    }
    return {
        "all_nodes": [row_node, col_node],
        "voltage_nodes": [row_node, col_node],
        "node_display_names": {row_node: row_node, col_node: col_node},
        "external_nodes": [row_node, col_node],
        "internal_nodes": [],
        "ground_nodes": [],
        "warnings": [],
        "G_full": [[g_expr, neg_expr], [neg_expr, g_expr]],
        "Ihis_full": ["0", "0"],
        "G_full_tagged": [[tagged_expr, neg_tagged_expr], [neg_tagged_expr, tagged_expr]],
        "Ihis_full_tagged": ["0", "0"],
        "direct_retained_stamps": [
            {
                "id": branch_id,
                "name": branch_name,
                "support_nodes": [row_node, col_node],
                "G": [
                    {"row": row_node, "col": row_node, "expr": g_expr, "tagged": tagged_expr},
                    {"row": row_node, "col": col_node, "expr": neg_expr, "tagged": neg_tagged_expr},
                    {"row": col_node, "col": row_node, "expr": neg_expr, "tagged": neg_tagged_expr},
                    {"row": col_node, "col": col_node, "expr": g_expr, "tagged": tagged_expr},
                ],
                "Ihis": [],
            }
        ],
        "G_symbol_usage": [
            {
                "symbol": symbol,
                "row_node": row_node,
                "col_node": col_node,
                "source_id": branch_id,
                "source_name": branch_name,
                "is_constant": symbol in ram_set,
            }
            for symbol in symbols
        ],
        "G_constant_symbols": sorted(ram_set),
        "symbol_dependency_table": symbol_table,
        "symbol_dependency_table_tagged": tagged_symbol_table,
        "mode": "structured_formula",
        "display_mode": "compact",
        "simplify_level": "light",
        "assume_spd": True,
        "use_suggested_order": False,
    }


class BranchNegativeCompositeExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("index.html", "utf8");
const formatterStart = html.indexOf("const MATH_FORMAT_MAX_DEPTH");
const formatterEnd = html.indexOf("function renderNodeEquations", formatterStart);
const termStart = html.indexOf("function addTerm");
const termEnd = html.indexOf("function nodeEquationRhs", termStart);
assert.ok(formatterStart >= 0 && formatterEnd > formatterStart, "missing formatter helpers");
assert.ok(termStart >= 0 && termEnd > termStart, "missing term helpers");

const context = {
  console,
  escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },
  wrapMathHighlight() {
    return "";
  },
  activeHighlightedBranches() {
    return [];
  },
  branchHighlightSourceIds() {
    return new Set();
  },
  branchHighlightColor() {
    return "#ef4444";
  },
  activeSourceHighlightBranch() {
    return null;
  },
  wrapMathSourceHighlight(_branch, html) {
    return html;
  }
};
vm.createContext(context);
vm.runInContext(`
${html.slice(formatterStart, formatterEnd)}
${html.slice(termStart, termEnd)}
function stampTwoNodeBranch(g, ihis) {
  const G = [[[], []], [[], []]];
  const Ihis = [[], []];
  addTerm(G[0][0], g, "B11");
  addTerm(G[1][1], g, "B11");
  addTerm(G[0][1], negateExpr(g), "B11");
  addTerm(G[1][0], negateExpr(g), "B11");
  addTerm(Ihis[0], ihis, "B11");
  addTerm(Ihis[1], negateExpr(ihis), "B11");
  return {
    G: G.map(row => row.map(sumTerms)),
    renderedG: G.map(row => row.map(renderTermSum)),
    Ihis: Ihis.map(sumTerms)
  };
}
globalThis.stampTwoNodeBranch = stampTwoNodeBranch;
`, context);

const cases = {
  xyz: context.stampTwoNodeBranch("X+Y+Z", "0"),
  composite: context.stampTwoNodeBranch("AA + G22 + G_rc + w2", "0"),
  ihis: context.stampTwoNodeBranch("G", "Ihis_a + Ihis_b + Ihis_c")
};
process.stdout.write(JSON.stringify(cases));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=".",
            text=True,
            capture_output=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        cls.results = json.loads(completed.stdout)

    def assert_expr_equal(self, actual, expected):
        self.assertEqual(sp.simplify(sp.sympify(actual) - sp.sympify(expected)), 0, actual)

    def test_two_node_branch_negates_xyz_conductance_as_whole_expression(self):
        result = self.results["xyz"]
        self.assert_expr_equal(result["G"][0][0], "X + Y + Z")
        self.assert_expr_equal(result["G"][1][1], "X + Y + Z")
        self.assert_expr_equal(result["G"][0][1], "-X - Y - Z")
        self.assert_expr_equal(result["G"][1][0], "-X - Y - Z")
        self.assertTrue(result["renderedG"][0][1].startswith("-("), result["renderedG"][0][1])
        self.assertTrue(result["renderedG"][0][1].endswith(")"), result["renderedG"][0][1])
        self.assertTrue(result["renderedG"][1][0].startswith("-("), result["renderedG"][1][0])
        self.assertTrue(result["renderedG"][1][0].endswith(")"), result["renderedG"][1][0])
        self.assertNotIn("-X + Y + Z", result["renderedG"][0][1])
        self.assert_expr_equal(result["Ihis"][0], "0")
        self.assert_expr_equal(result["Ihis"][1], "0")

    def test_two_node_branch_negates_long_conductance_as_whole_expression(self):
        result = self.results["composite"]
        expected = "-AA - G22 - G_rc - w2"
        self.assert_expr_equal(result["G"][0][1], expected)
        self.assert_expr_equal(result["G"][1][0], expected)

    def test_two_node_branch_negates_history_current_as_whole_expression(self):
        result = self.results["ihis"]
        self.assert_expr_equal(result["Ihis"][0], "Ihis_a + Ihis_b + Ihis_c")
        self.assert_expr_equal(result["Ihis"][1], "-Ihis_a - Ihis_b - Ihis_c")

    def test_no_internal_direct_retained_branch_keeps_full_ram_g_in_c_draft(self):
        response = build_optimized_response(make_test6_payload())
        draft = response["structured"]["c_draft"]
        self.assertIn("double X = 0.0;", draft)
        self.assertIn("double Y = 0.0;", draft)
        self.assertIn("double Z = 0.0;", draft)
        self.assertIn("/* ramG_N2_N2 represents G[N2,N2]: X + Y + Z. */", draft)
        self.assertIn("ramG_N2_N2 = X + Y + Z;", draft)
        self.assertIn("g_mat_over[0][0] = ramG_N2_N2;", draft)
        self.assertIn("g_mat_over[0][1] = -ramG_N2_N2;", draft)
        self.assertIn("g_mat_over[1][0] = -ramG_N2_N2;", draft)
        self.assertIn("g_mat_over[1][1] = ramG_N2_N2;", draft)
        self.assertNotIn("No RAM-side G entries", draft)

    def test_no_internal_direct_retained_branch_splits_partial_ram_and_code_terms(self):
        response = build_optimized_response(make_test6_payload(ram_symbols=("X", "Y")))
        direct = response["structured"]["direct_retained"]
        self.assertEqual(direct["Gred_direct"], [["X + Y + Z", "-X - Y - Z"], ["-X - Y - Z", "X + Y + Z"]])
        self.assertEqual(direct["Gred_direct_ram"], [["X + Y", "-X - Y"], ["-X - Y", "X + Y"]])
        self.assertEqual(direct["Gred_direct_code"], [["Z", "-Z"], ["-Z", "Z"]])
        self.assertEqual(
            direct["Gred_direct_tagged"],
            [
                ["X__bbsrc_B11 + Y__bbsrc_B11 + Z__bbsrc_B11", "-X__bbsrc_B11 - Y__bbsrc_B11 - Z__bbsrc_B11"],
                ["-X__bbsrc_B11 - Y__bbsrc_B11 - Z__bbsrc_B11", "X__bbsrc_B11 + Y__bbsrc_B11 + Z__bbsrc_B11"],
            ],
        )
        self.assertEqual(
            direct["Gred_direct_ram_tagged"],
            [["X__bbsrc_B11 + Y__bbsrc_B11", "-X__bbsrc_B11 - Y__bbsrc_B11"], ["-X__bbsrc_B11 - Y__bbsrc_B11", "X__bbsrc_B11 + Y__bbsrc_B11"]],
        )
        self.assertEqual(
            direct["Gred_direct_code_tagged"],
            [["Z__bbsrc_B11", "-Z__bbsrc_B11"], ["-Z__bbsrc_B11", "Z__bbsrc_B11"]],
        )
        draft = response["structured"]["c_draft"]
        self.assertIn("double X = 0.0;", draft)
        self.assertIn("double Y = 0.0;", draft)
        self.assertIn("double Z = 0.0;", draft)
        self.assertIn("g_mat_over[0][0] = X + Y;", draft)
        self.assertIn("g_mat_over[0][1] = -X - Y;", draft)
        self.assertIn("g_mat_over[1][0] = -X - Y;", draft)
        self.assertIn("varG_N2_N2 = Z;", draft)
        self.assertIn("varG_N2_N4 = -Z;", draft)
        self.assertNotIn("set_CODE(&G_code", draft)
        self.assertNotIn("get_CODE(&G_code", draft)

    def test_no_internal_direct_retained_branch_keeps_all_dynamic_g_in_code(self):
        response = build_optimized_response(make_test6_payload(ram_symbols=()))
        draft = response["structured"]["c_draft"]
        self.assertIn("double X = 0.0;", draft)
        self.assertIn("double Y = 0.0;", draft)
        self.assertIn("double Z = 0.0;", draft)
        self.assertIn("No RAM-side G entries", draft)
        self.assertIn("/* G_N2_N2 represents G[N2,N2]: X + Y + Z. */", draft)
        self.assertIn("G_N2_N2 = X + Y + Z;", draft)
        self.assertIn("varG_N2_N2 = G_N2_N2;", draft)
        self.assertIn("varG_N2_N4 = -G_N2_N2;", draft)
        self.assertIn("varG_N4_N4 = G_N2_N2;", draft)
        self.assertNotIn("set_CODE(&G_code", draft)
        self.assertNotIn("get_CODE(&G_code", draft)

    def test_no_internal_direct_retained_branch_splits_long_expression_terms(self):
        response = build_optimized_response(
            make_test6_payload(g_expr="AA + G22 + G_rc + w2", ram_symbols=("G22", "G_rc"))
        )
        draft = response["structured"]["c_draft"]
        self.assertIn("g_mat_over[0][0] = G22 + G_rc;", draft)
        self.assertIn("g_mat_over[0][1] = -G22 - G_rc;", draft)
        self.assertIn("g_mat_over[1][0] = -G22 - G_rc;", draft)
        self.assertIn("varG_N2_N2 = AA + w2;", draft)
        self.assertIn("varG_N2_N4 = -AA - w2;", draft)
        self.assertNotIn("set_CODE(&G_code", draft)


if __name__ == "__main__":
    unittest.main()
