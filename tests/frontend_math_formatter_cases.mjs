import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../index.html", import.meta.url), "utf8");
assert.match(
  html,
  /\.output-tabs\s*\{[\s\S]*?overflow-x:\s*auto;[\s\S]*?scrollbar-width:\s*thin;/,
  "Output tabs should keep horizontal scrolling with a thin scrollbar"
);
assert.match(
  html,
  /\.output-tabs::\-webkit-scrollbar\s*\{[\s\S]*?height:\s*6px;/,
  "Output tabs should use a compact WebKit scrollbar"
);
assert.match(
  html,
  /\.output-tabs::\-webkit-scrollbar-thumb\s*\{[\s\S]*?background:/,
  "Output tabs should style the draggable scrollbar thumb"
);
assert.match(
  html,
  /\.output-body,[\s\S]*?\.modal-output-body\s*\{[\s\S]*?scrollbar-width:\s*thin;/,
  "Output bodies should use compact vertical scrollbars"
);
assert.match(
  html,
  /\.output-body::\-webkit-scrollbar,[\s\S]*?\.modal-output-body::\-webkit-scrollbar\s*\{[\s\S]*?width:\s*8px;/,
  "Output bodies should use a narrow WebKit scrollbar"
);
const start = html.indexOf("const MATH_FORMAT_MAX_DEPTH");
const end = html.indexOf("function renderNodeEquations", start);
assert.ok(start >= 0 && end > start, "Could not locate math formatter block in index.html");

const formatterSource = html.slice(start, end);
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
vm.runInContext(`${formatterSource}\nglobalThis.formatMath = formatMath;\nglobalThis.formatMathWithTagged = formatMathWithTagged;`, context);

const cases = [
  {
    name: "negative term inside a product is not negated as a whole sum",
    expr: "2*(-G12**2 + (G11 + w1)*(Dabc + G22 + Grc + w2))/(Dabc + G22 + Grc + w2)",
    mustContain: [
      "-G<sub>12</sub><sup>2</sup>",
      '<span class="math-op">+</span>'
    ],
    mustNotContain: [
      "-(G<sub>12</sub><sup>2</sup>"
    ]
  },
  {
    name: "explicit negated parenthesized sum remains a negated sum",
    expr: "-(a + b)",
    mustContain: ["-(a <span class=\"math-op\">+</span> b)"]
  },
  {
    name: "leading negative term plus another term stays additive",
    expr: "-a + b",
    mustContain: ["-a <span class=\"math-op\">+</span> b"],
    mustNotContain: ["-(a <span class=\"math-op\">+</span> b)"]
  },
  {
    name: "difference of squares keeps the minus between terms",
    expr: "x**2 - y**2",
    mustContain: ["x<sup>2</sup> <span class=\"math-op\">-</span> y<sup>2</sup>"]
  },
  {
    name: "fraction keeps denominator grouping",
    expr: "a**2/(b + c)",
    mustContain: [
      '<span class="math-frac">',
      "a<sup>2</sup>",
      'b <span class="math-op">+</span> c'
    ]
  },
  {
    name: "multiplication of sums keeps both parentheses",
    expr: "(a - b)*(c + d)",
    mustContain: [
      '(a <span class="math-op">-</span> b)',
      " · ",
      '(c <span class="math-op">+</span> d)'
    ]
  },
  {
    name: "sqrt expression is displayed without changing signs",
    expr: "sqrt(a - b) + c",
    mustContain: [
      "sqrt(a - b)",
      '<span class="math-op">+</span> c'
    ]
  },
  {
    name: "nested negative rational expression keeps inner additive sign",
    expr: "(-x**2 + y)/(z - w)",
    mustContain: [
      "-x<sup>2</sup>",
      '<span class="math-op">+</span> y',
      'z <span class="math-op">-</span> w'
    ],
    mustNotContain: [
      "-(x<sup>2</sup> <span class=\"math-op\">+</span> y)"
    ]
  },
  {
    name: "mixed exp log sqrt powers and arithmetic keep local signs",
    expr: "(exp(a) - log(b + c)*sqrt(d - e) + x**3/(y - z) - m*n)/(p + q**2)",
    mustContain: [
      "exp(a)",
      "log(b + c)",
      "sqrt(d - e)",
      "x<sup>3</sup>",
      '<span class="math-op">-</span> m · n',
      "q<sup>2</sup>"
    ],
    mustNotContain: [
      "-(exp(a)",
      "-(m · n)"
    ]
  }
];

for (const testCase of cases) {
  const rendered = context.formatMath(testCase.expr);
  for (const expected of testCase.mustContain || []) {
    assert.ok(
      rendered.includes(expected),
      `${testCase.name}: expected rendered HTML to include ${expected}\nRendered: ${rendered}`
    );
  }
  for (const forbidden of testCase.mustNotContain || []) {
    assert.ok(
      !rendered.includes(forbidden),
      `${testCase.name}: rendered HTML should not include ${forbidden}\nRendered: ${rendered}`
    );
  }
}

const taggedCases = [
  {
    name: "tagged negative fraction keeps parenthesized product numerator",
    display: "-G12*(G*Vs10 + Ihis_p)/(G + G11) + Ihis_s",
    tagged: "-G12__bbsrc_T1*(G__bbsrc_B10*Vs10 + Ihis_p__bbsrc_B10)/(G__bbsrc_B10 + G11__bbsrc_T1) + Ihis_s__bbsrc_B12",
    mustContain: [
      "G<sub>12</sub> · (G · Vs<sub>10</sub> <span class=\"math-op\">+</span> Ihis<sub>p</sub>)",
      "<span class=\"math-op\">+</span> Ihis<sub>s</sub>"
    ],
    mustNotContain: [
      "G<sub>12</sub> · G · Vs<sub>10</sub> <span class=\"math-op\">+</span> Ihis<sub>p</sub></span><span class=\"math-frac-den\">"
    ]
  },
  {
    name: "tagged positive fraction keeps parenthesized product numerator",
    display: "G12*(G*Vs10 + Ihis_p)/(G + G11) - Ihis_s",
    tagged: "G12__bbsrc_T1*(G__bbsrc_B10*Vs10 + Ihis_p__bbsrc_B10)/(G__bbsrc_B10 + G11__bbsrc_T1) - Ihis_s__bbsrc_B12",
    mustContain: [
      "G<sub>12</sub> · (G · Vs<sub>10</sub> <span class=\"math-op\">+</span> Ihis<sub>p</sub>)",
      "<span class=\"math-op\">-</span> Ihis<sub>s</sub>"
    ]
  }
];

const dangerousTaggedCases = [
  {
    name: "tagged fraction does not flatten a leading negative grouped product",
    display: "-a*(b + c)/(d + e) + h",
    tagged: "-a__bbsrc_A*(b__bbsrc_B + c__bbsrc_C)/(d__bbsrc_D + e__bbsrc_E) + h__bbsrc_H",
    mustContain: [
      "a · (b <span class=\"math-op\">+</span> c)",
      "<span class=\"math-op\">+</span> h"
    ],
    mustNotContain: [
      "a · b <span class=\"math-op\">+</span> c</span><span class=\"math-frac-den\">"
    ]
  },
  {
    name: "tagged fraction does not flatten a grouped product with subtraction",
    display: "a*(b - c)/(d + e) - h",
    tagged: "a__bbsrc_A*(b__bbsrc_B - c__bbsrc_C)/(d__bbsrc_D + e__bbsrc_E) - h__bbsrc_H",
    mustContain: [
      "a · (b <span class=\"math-op\">-</span> c)",
      "<span class=\"math-op\">-</span> h"
    ],
    mustNotContain: [
      "a · b <span class=\"math-op\">-</span> c</span><span class=\"math-frac-den\">"
    ]
  },
  {
    name: "tagged fraction keeps a grouped first factor before another multiplier",
    display: "-(a + b)*c/(d + e)",
    tagged: "-(a__bbsrc_A + b__bbsrc_B)*c__bbsrc_C/(d__bbsrc_D + e__bbsrc_E)",
    mustContain: [
      "(a <span class=\"math-op\">+</span> b) · c"
    ],
    mustNotContain: [
      "a <span class=\"math-op\">+</span> b · c</span><span class=\"math-frac-den\">"
    ]
  },
  {
    name: "tagged fraction keeps multiple grouped product factors",
    display: "a*(b + c)*(d - e)/f",
    tagged: "a__bbsrc_A*(b__bbsrc_B + c__bbsrc_C)*(d__bbsrc_D - e__bbsrc_E)/f__bbsrc_F",
    mustContain: [
      "a · (b <span class=\"math-op\">+</span> c) · (d <span class=\"math-op\">-</span> e)"
    ],
    mustNotContain: [
      "a · b <span class=\"math-op\">+</span> c · d",
      "c · d <span class=\"math-op\">-</span> e</span><span class=\"math-frac-den\">"
    ]
  }
];

for (const testCase of [...taggedCases, ...dangerousTaggedCases]) {
  const rendered = context.formatMathWithTagged(testCase.display, testCase.tagged);
  for (const expected of testCase.mustContain || []) {
    assert.ok(
      rendered.includes(expected),
      `${testCase.name}: expected rendered HTML to include ${expected}\nRendered: ${rendered}`
    );
  }
  for (const forbidden of testCase.mustNotContain || []) {
    assert.ok(
      !rendered.includes(forbidden),
      `${testCase.name}: rendered HTML should not include ${forbidden}\nRendered: ${rendered}`
    );
  }
}

console.log(`math formatter cases passed: ${cases.length + taggedCases.length + dangerousTaggedCases.length}`);
