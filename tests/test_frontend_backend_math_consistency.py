from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path

import sympy as sp


ROOT = Path(__file__).resolve().parents[1]


class FrontendBackendMathConsistencyTests(unittest.TestCase):
    def _require_node(self) -> None:
        try:
            subprocess.run(["node", "--version"], cwd=ROOT, text=True, capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            self.skipTest(f"node is not runnable in this shell: {exc}")

    def _render_with_frontend_formatter(self, backend_text: str, tagged_text: str | None = None) -> str:
        self._require_node()
        script = textwrap.dedent(
            """
            import fs from "node:fs";
            import vm from "node:vm";

            const [indexPath, expr, taggedExpr = ""] = process.argv.slice(1);
            const html = fs.readFileSync(indexPath, "utf8");
            const start = html.indexOf("const MATH_FORMAT_MAX_DEPTH");
            const end = html.indexOf("function renderNodeEquations", start);
            if (start < 0 || end <= start) throw new Error("formatter block not found");
            const context = {
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
                return [];
              },
              sourceMarkerForBranch(id) {
                return `__bbsrc_${id}`;
              },
              wrapMathSourceHighlight(_branch, html) {
                return html;
              }
            };
            vm.createContext(context);
            vm.runInContext(`${html.slice(start, end)}\\nglobalThis.formatMath = formatMath;\\nglobalThis.formatMathWithTagged = formatMathWithTagged;`, context);
            const rendered = taggedExpr ? context.formatMathWithTagged(expr, taggedExpr) : context.formatMath(expr);
            process.stdout.write(JSON.stringify({ rendered }));
            """
        )
        args = ["node", "--input-type=module", "-e", script, str(ROOT / "index.html"), backend_text]
        if tagged_text is not None:
            args.append(tagged_text)
        completed = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)["rendered"]

    def test_one_by_one_complex_expression_display_keeps_backend_signs(self) -> None:
        G12, G11, w1, Dabc, G22, Grc, w2 = sp.symbols("G12 G11 w1 Dabc G22 Grc w2")
        backend_expr = sp.factor(
            2 * (-G12**2 + (G11 + w1) * (Dabc + G22 + Grc + w2))
            / (Dabc + G22 + Grc + w2)
        )
        backend_text = str(backend_expr)
        rendered = self._render_with_frontend_formatter(backend_text)

        self.assertIn('<span class="math-op">-</span> G<sub>12</sub><sup>2</sup>', rendered)
        self.assertIn('<span class="math-op">+</span>', rendered)
        self.assertNotIn("-(G<sub>12</sub><sup>2</sup>", rendered)

    def test_one_by_one_mixed_transcendental_and_arithmetic_display(self) -> None:
        a, b, c, d, e, x, y, z, m, n, p, q = sp.symbols("a b c d e x y z m n p q")
        backend_expr = (
            sp.exp(a)
            - sp.log(b + c) * sp.sqrt(d - e)
            + x**3 / (y - z)
            - m * n
        ) / (p + q**2)
        backend_text = str(backend_expr)
        rendered = self._render_with_frontend_formatter(backend_text)

        self.assertIn("exp(a)", rendered)
        self.assertIn("log(b + c)", rendered)
        self.assertIn("sqrt(d - e)", rendered)
        self.assertIn("x<sup>3</sup>", rendered)
        self.assertIn("q<sup>2</sup>", rendered)
        self.assertIn("-m · n", rendered)
        self.assertNotIn("-(exp(a)", rendered)
        self.assertNotIn("-(m · n)", rendered)

    def test_sympy_tagged_ihisred_display_keeps_product_sum_numerator(self) -> None:
        G12, G, Vs10, Ihis_p, G11, Ihis_s = sp.symbols("G12 G Vs10 Ihis_p G11 Ihis_s")
        backend_expr = -G12 * (G * Vs10 + Ihis_p) / (G + G11) + Ihis_s
        backend_text = str(backend_expr)
        tagged_text = (
            "-G12__bbsrc_T1*(G__bbsrc_B10*Vs10 + Ihis_p__bbsrc_B10)"
            "/(G__bbsrc_B10 + G11__bbsrc_T1) + Ihis_s__bbsrc_B12"
        )

        self.assertEqual("-G12*(G*Vs10 + Ihis_p)/(G + G11) + Ihis_s", backend_text)
        rendered = self._render_with_frontend_formatter(backend_text, tagged_text)

        self.assertIn(
            'G<sub>12</sub> · (G · Vs<sub>10</sub> <span class="math-op">+</span> Ihis<sub>p</sub>)',
            rendered,
        )
        self.assertIn('<span class="math-op">+</span> Ihis<sub>s</sub>', rendered)
        self.assertNotIn(
            'G<sub>12</sub> · G · Vs<sub>10</sub> <span class="math-op">+</span> Ihis<sub>p</sub></span><span class="math-frac-den">',
            rendered,
        )

    def test_sympy_tagged_positive_ihisred_display_keeps_product_sum_numerator(self) -> None:
        G12, G, Vs10, Ihis_p, G11, Ihis_s = sp.symbols("G12 G Vs10 Ihis_p G11 Ihis_s")
        backend_expr = G12 * (G * Vs10 + Ihis_p) / (G + G11) - Ihis_s
        backend_text = str(backend_expr)
        tagged_text = (
            "G12__bbsrc_T1*(G__bbsrc_B10*Vs10 + Ihis_p__bbsrc_B10)"
            "/(G__bbsrc_B10 + G11__bbsrc_T1) - Ihis_s__bbsrc_B12"
        )

        self.assertEqual("G12*(G*Vs10 + Ihis_p)/(G + G11) - Ihis_s", backend_text)
        rendered = self._render_with_frontend_formatter(backend_text, tagged_text)

        self.assertIn(
            'G<sub>12</sub> · (G · Vs<sub>10</sub> <span class="math-op">+</span> Ihis<sub>p</sub>)',
            rendered,
        )
        self.assertIn('<span class="math-op">-</span> Ihis<sub>s</sub>', rendered)
        self.assertNotIn(
            'G<sub>12</sub> · G · Vs<sub>10</sub> <span class="math-op">+</span> Ihis<sub>p</sub></span><span class="math-frac-den">',
            rendered,
        )

if __name__ == "__main__":
    unittest.main()
