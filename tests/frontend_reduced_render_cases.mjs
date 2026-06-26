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
      ...overrides,
    },
    {}
  );
  return { output, matrixCalls, vectorCalls, katexCalls };
}

{
  const { output, matrixCalls, vectorCalls, katexCalls } = renderReducedForTest([], {
    G_red_simplified: [["0", "0"], ["0", "d"]],
    Ihis_red_simplified: ["0", "h2"],
    K_v_simplified: [["1", "0"], ["0", "1"]],
    K_h_simplified: ["0", "kh2"],
  });
  assert.ok(!output.includes("<katex>"), "default reduced view should not depend on KaTeX rendering");
  assert.equal(katexCalls.length, 0, "KaTeX should not be called by the default reduced view");
  assert.deepEqual(matrixCalls[0].rows, [["0", "0"], ["0", "d"]], "default Gred should use simplified backend matrix");
  assert.deepEqual(vectorCalls[2].items, ["0", "h2"], "default Ihisred should use simplified backend vector");
  assert.deepEqual(matrixCalls[1].rows, [["1", "0"], ["0", "1"]], "default Kv should use simplified backend matrix");
  assert.deepEqual(vectorCalls[5].items, ["0", "kh2"], "default Kh should use simplified backend vector");
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
  assert.equal(katexCalls.length, 0, "highlight mode should not re-enable KaTeX for the default reduced view");
  assert.ok(matrixCalls.some(call => call.tagged), "highlight section should use tagged matrix expressions");
  assert.ok(vectorCalls.some(call => call.tagged), "highlight section should use tagged vector expressions");
}

{
  const { output, matrixCalls, vectorCalls } = renderReducedForTest([]);
  assert.ok(!output.includes("来源高亮版本"), "without active formula highlighting, no highlighted section should be rendered");
  assert.ok(matrixCalls.every(call => !call.tagged), "without active highlighting, matrix rendering should stay untagged");
  assert.ok(vectorCalls.every(call => !call.tagged), "without active highlighting, vector rendering should stay untagged");
}

console.log("reduced render cases passed");
