import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
const start = html.indexOf("function renderReducedEquationsResult");
const end = html.indexOf("function renderReducedEquations", start + 1);
assert.ok(start >= 0 && end > start, "Could not locate reduced-equation renderer in index.html");

function renderReducedForTest(activeBranches = ["B1"], overrides = {}, options = {}) {
  const matrixCalls = [];
  const vectorCalls = [];
  const katexCalls = [];
  const context = {
    escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    },
    tr(value) {
      return value;
    },
    nodeDisplayName(value) {
      return value;
    },
    activeHighlightedBranches() {
      return activeBranches;
    },
    renderMatrix(rows, cols = null, taggedRows = null) {
      matrixCalls.push({ rows, cols, tagged: Boolean(taggedRows) });
      return `<matrix tagged="${Boolean(taggedRows)}"></matrix>`;
    },
    renderVectorMatrix(items, taggedItems = null) {
      vectorCalls.push({ items, tagged: Boolean(taggedItems) });
      return `<vector tagged="${Boolean(taggedItems)}"></vector>`;
    },
    renderSingleInternalFormula() {
      return "";
    },
  };
  if (options.katex !== false) {
    context.katex = {
      renderToString(latex, renderOptions) {
        katexCalls.push({ latex, renderOptions });
        return `<katex>${latex}</katex>`;
      },
    };
  }
  vm.createContext(context);
  vm.runInContext(`${html.slice(start, end)}\nglobalThis.renderReducedEquationsResult = renderReducedEquationsResult;`, context);
  const output = context.renderReducedEquationsResult(
    {
      external_nodes: ["N2", "N3"],
      internal_nodes: ["N1", "N0"],
      ground_nodes: [],
      G_red: [["a", "b"], ["c", "d"]],
      Ihis_red: ["h1", "h2"],
      K_v: [["kv1", "kv2"], ["kv3", "kv4"]],
      K_h: ["kh1", "kh2"],
      G_red_tagged: [["a__bbsrc_B1", "b__bbsrc_B1"], ["c__bbsrc_B1", "d__bbsrc_B1"]],
      Ihis_red_tagged: ["h1__bbsrc_B1", "h2__bbsrc_B1"],
      K_v_tagged: [["kv1__bbsrc_B1", "kv2__bbsrc_B1"], ["kv3__bbsrc_B1", "kv4__bbsrc_B1"]],
      K_h_tagged: ["kh1__bbsrc_B1", "kh2__bbsrc_B1"],
      G_red_latex: "\\begin{bmatrix}0 & 0 \\\\ 0 & d\\end{bmatrix}",
      Ihis_red_latex: "\\begin{bmatrix}0 \\\\ h2\\end{bmatrix}",
      K_v_latex: "\\begin{bmatrix}1 & 0 \\\\ 0 & 1\\end{bmatrix}",
      K_h_latex: "\\begin{bmatrix}0 \\\\ kh2\\end{bmatrix}",
      ...overrides,
    },
    {}
  );
  return { output, matrixCalls, vectorCalls, katexCalls };
}

{
  const { output, katexCalls } = renderReducedForTest([], {
    G_red_simplified: [["0", "0"], ["0", "d"]],
    Ihis_red_simplified: ["0", "h2"],
    K_v_simplified: [["1", "0"], ["0", "1"]],
    K_h_simplified: ["0", "kh2"],
  });
  assert.ok(output.includes("<katex>"), "default reduced view should prefer local KaTeX rendering when LaTeX is available");
  assert.ok(katexCalls.some(call => call.latex.includes("\\begin{bmatrix}0 & 0")), "Gred LaTeX should be rendered through KaTeX");
  assert.ok(katexCalls.some(call => call.latex.includes("\\begin{bmatrix}0 \\\\ h2")), "Ihisred LaTeX should be rendered through KaTeX");
}

{
  const { matrixCalls, vectorCalls, katexCalls } = renderReducedForTest([], {
    G_red_simplified: [["0", "0"], ["0", "d"]],
    Ihis_red_simplified: ["0", "h2"],
    K_v_simplified: [["1", "0"], ["0", "1"]],
    K_h_simplified: ["0", "kh2"],
  }, { katex: false });
  assert.equal(katexCalls.length, 0, "without KaTeX, no KaTeX rendering should run");
  assert.deepEqual(matrixCalls[0].rows, [["0", "0"], ["0", "d"]], "fallback Gred should use simplified backend matrix");
  assert.deepEqual(vectorCalls[2].items, ["0", "h2"], "fallback Ihisred should use simplified backend vector");
  assert.deepEqual(matrixCalls[1].rows, [["1", "0"], ["0", "1"]], "fallback Kv should use simplified backend matrix");
  assert.deepEqual(vectorCalls[5].items, ["0", "kh2"], "fallback Kh should use simplified backend vector");
}

{
  const { output, matrixCalls, vectorCalls, katexCalls } = renderReducedForTest(["B1"]);
  assert.ok(output.includes("来源高亮版本"), "highlight-enabled output should add a separate source-highlighted section");
  assert.ok(katexCalls.length >= 2, "default reduced/recovery display should use KaTeX when available");
  assert.ok(matrixCalls.some(call => call.tagged), "highlight section should use tagged matrix expressions");
  assert.ok(vectorCalls.some(call => call.tagged), "highlight section should use tagged vector expressions");
}

{
  const { output, matrixCalls, vectorCalls } = renderReducedForTest([]);
  assert.ok(!output.includes("来源高亮版本"), "without active formula highlighting, no highlighted section should be rendered");
  assert.ok(matrixCalls.every(call => !call.tagged), "without active highlighting, matrix rendering should stay untagged");
  assert.ok(vectorCalls.every(call => !call.tagged), "without active highlighting, vector rendering should stay untagged");
}

{
  const katexCode = fs.readFileSync(new URL("../vendor/katex/katex.min.js", import.meta.url), "utf8");
  const context = {};
  vm.createContext(context);
  vm.runInContext(katexCode, context);
  const rendered = context.katex.renderToString(
    "I_{N1}=\\begin{bmatrix}0 & \\frac{a+b}{c}\\\\ d & 1\\end{bmatrix}",
    { displayMode: true }
  );
  assert.ok(rendered.includes("mtable"), "bundled local KaTeX should render bmatrix markup");
  assert.ok(rendered.includes("mfrac"), "bundled local KaTeX should render fraction markup");
  assert.ok(rendered.includes("msub"), "bundled local KaTeX should render simple subscripts");
}

console.log("reduced render cases passed");
