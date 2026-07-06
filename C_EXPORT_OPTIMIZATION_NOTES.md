# C Export Optimization Notes

This note tracks C draft optimization ideas for both single-case optimized
elimination and multi-case C export. It is intentionally implementation-facing:
items here may be partially implemented, planned, or reserved for later review.

## Implemented Or Partially Implemented

### Diagonal `Gkk` Scalar Path

When `Gkk` is proven diagonal for a case, avoid a general matrix inverse:

```text
W[k,k] = 1 / Gkk[k,k]
tmp_Grk_W[row,k] = Grk[row,k] / Gkk[k,k]
Gred[row,col] = Grr[row,col] - sum_k tmp_Grk_W[row,k] * Gkr[k,col]
Ihisred[row] = Ihisr[row] - sum_k tmp_Grk_W[row,k] * Ihisk[k]
Vk[k] = -(sum_col tmp_Grk_W[col,k] * Vr[col] + Ihisk[k] / Gkk[k,k])
```

Non-diagonal cases should keep the matrix-DAG path.

### Symmetry / Transpose Reuse

For symmetric networks:

```text
Gkr = Grk^T
W = W^T
```

After computing:

```text
A = Grk * W
```

the recovery term:

```text
W * Gkr
```

can use:

```text
W * Gkr = A^T
```

This avoids a second matrix multiplication in the recovery path.

### Whole-Entry Reuse

Detect whole-entry relationships such as:

```text
Gred[i,j] = Gred[p,q]
Gred[i,j] = -Gred[p,q]
```

without using expensive `sp.cancel`, `sp.simplify`, or `sp.factor` on large
expanded expressions. Current use is mainly for reducing repeated `varG`
reads/assignments. Future use may reduce scalar entry computation.

### Source-Level Budgeted CSE

For circuits where the original source matrix entries are already long
expressions, optimize only the source/Gfull assignment layer before any dense
Schur expansion.

Current safe scope:

- Multi-case effective alias assignment, for example `multcase_G_* = ...`.
- RAM-side final/source `g_mat_over` stamp writes, including conditional
  multi-case RAM branches.
- No-internal final `GValue` CODE writes, where dynamic source entries are
  assigned directly to `varG_*`.
- Matrix-staged final writes may share source temporaries inside one case branch,
  but the matrix DAG itself is not replaced by expanded scalar Schur formulas.

Safety rules:

- Use budgeted `sp.cse` only; do not call `sp.cancel`, `sp.factor`, or
  `sp.simplify`.
- Check expression count, `count_ops`, and string size before running CSE.
- If the group is tiny or over budget, skip CSE and keep the old code path.
- Keep all CSE replacements once CSE is accepted, because generated temporaries
  can depend on earlier temporaries.
- Apply the helper through every codegen route that can emit the same source
  assignment style. A common bug pattern is fixing `_alias_assignment_lines`
  while leaving standalone no-internal `GValue` emission on the old path.
- When a RAM branch computes `sourceG_*` or `multcase_G_*`, the same RAM branch's
  `g_mat_over` writes must reference those names. If a temp is declared but no
  stamp uses it, the RAM/CODE split is probably passing through two different
  emitters.
- When C draft semantics change, bump the matching frontend cache version.
  Saved JSON files can otherwise keep showing a stale `optimizedEliminationCache`
  or `multiCaseExportCache` result even though the backend generator has been
  fixed.
- Keep CSE comments at group level. Do not emit `sourceG_tmpN is a repeated
  source subexpression` before every temporary; it makes long RAM/CODE blocks
  hard to read. Per-variable comments should be reserved for semantic aliases
  such as `ramG_* represents G[...]` or `Ihis_* represents Ihisred[...]`.
- Source-level CSE temporaries returned by the scalar assignment planner are
  declared in `STATIC` or `LOCAL_STATIC` when they must be reused across
  RAM/CODE sections. The generated RAM/CODE body must then emit plain
  assignments such as `sourceG_tmp0 = ...;`, not `double sourceG_tmp0 = ...;`.
  Tests should scan generated drafts for duplicate `double source*tmpN`
  declarations; otherwise C drafts can look valid in one stage but fail in C
  compilation after temp lifting.
- The post-processing passes that hoist RAM/CODE shared temps and dedupe same
  CODE-stage temps must accept both legacy local declarations
  (`double sourceG_tmp0 = rhs;`) and the current predeclared assignment form
  (`sourceG_tmp0 = rhs;`). If they only match the legacy form, cross-stage reuse
  silently stops working after temp declarations are lifted to STATIC.

Same-stage CODE source temp reuse:

- Optimized C export runs source-level CSE separately for G and Ihis. If a source
  G entry is CODE-owned, the same repeated denominator can be emitted twice in
  the same `CODE` section, for example once as `sourceG_tmp0` and again as
  `sourceIhis_tmp0`.
- Single-case export can merge these when the emitted RHS text is exactly
  identical in the same CODE body.
- Multi-case export often emits G aliases and Ihis aliases in separate CODE-side
  `switch` blocks. It may still merge them, but only when all of these match:
  the same local case selector scope, the same case index, and the exact same
  RHS text. The lifted name should be persistent for the CODE section, for
  example `sourceGI_C1_case1_tmp0`.
- Do not share across different case indices, different local selectors, or
  different runtime conditions.
- Do not prove equality with algebra here. The pass should only remove duplicate
  same-stage declarations and rewrite later uses to the first temp name. If the
  expressions differ textually, keep the existing code.

This is intentionally different from dense `Gred` CSE. It reduces repeated
source expressions in examples such as `Trf_Ctest.json` without revisiting the
historical slow path caused by expanded dense matrices.

Debug lesson: a helper-level test is not enough for this case. `Trf_Ctest.json`
must be regenerated through the same multi-case export path used by the UI,
because repeated source temps can be split across the G-alias switch and the
Ihis-alias switch even when each individual helper looks correct.

### Multi-Case RAM Overlay Deduplication

When multi-case aliases have already absorbed the case differences, different
case profiles may produce the same RAM `g_mat_over` overlay:

```text
case 0: multcase_G_* is assigned full case-0 values
case 1: multcase_G_* is assigned full case-1 values

RAM stamp body:
g_mat_over[i][j] = multcase_G_*
setupGMatrix(dim)
```

In that situation, do not emit one identical `switch(case_id)` stamp block per
case. Build the RAM stamp body for each case first, then compare the generated
body. If all bodies are byte-for-byte identical, emit one case-invariant overlay.

This is a general codegen rule, not a fixture-specific shortcut:

- It is safe because the aliases are resolved before the overlay is stamped.
- It must only collapse after comparing the actual generated body, including
  node order, matrix dimension, zeroing loop, `g_mat_over` writes, and
  `setupGMatrix(dim)`.
- If any case has a different node set, retained dimension, RAM/CODE ownership,
  or omitted RAM entries, keep the case-specific switch.
- Do not infer equivalence from the number of cases or from matching branch
  names; compare the final stamp body.

Debug lesson: when a fixture shows repeated RAM blocks, inspect the generated
`RAM_PASS1` body after alias resolution. A test that only checks helper output
can miss the user-visible path; `Trf_Ctest.json` must be exercised through the
same multi-case export request path that the UI uses.

### Source-Level Direct/Core Split

Split stamps before Schur elimination:

```text
Gtotal = Gcore + Gdirect
Ihistotal = Ihiscore + Ihisdirect
```

- Retained-only elements go to `Gdirect/Ihisdirect`.
- Elements touching internal nodes go to `Gcore/Ihiscore`.
- Schur reduction and internal voltage recovery use only core blocks.
- Direct entries are added at final assembly and placed in RAM or CODE based on
  their dependency.

Do not compute Schur/core by subtracting direct terms from an expanded final
`Gred`.

### Multi-Case Alias Template

For multi-case export, avoid generating a full final formula per case
combination. Instead, create case-resolved aliases:

```text
cr_R1_G_eff
cr_R1_Ihis_eff
...
```

Each alias receives the full value for the active case. The downstream
reduction then runs once on the alias-template network.

No base-plus-delta compensation is used by default.

### Runtime-Mutable Alias Safety

Runtime-mutable case groups are resolved in CODE, but they still follow the
same full-value rule. When a source entry has common additive terms across all
runtime cases, keep those common terms as the residual and put only the
case-varying part into the `multcase_*` alias:

```text
case 0: A*Z + Y + Gc_0
case 1: A*Z + Y + Gc_1

residual: A*Z + Y
alias:    Gc_0 / Gc_1
final:    A*Z + Y + multcase_*
```

Do not create complement expressions such as:

```text
Gc_0 + Gc_1 - multcase_*
```

Those are another form of base/delta compensation and can make no-internal
multi-case direct stamps mathematically hard to audit.

### Dummy / N-Dummy Finalization

Dummy constructs are used to keep multi-case topology compatible. They should
not increase the final C draft unnecessarily:

- Dummy-only retained nodes can be finalized out before C generation.
- N-Dummy nodes that exist only to match eliminated internal nodes should be
  treated as forced-elimination bookkeeping.
- After finalization, reduce matrix dimensions where safe.

## Planned Optimizations

### Upper-Triangle Only For Diagonal Scalar `Gred`

When the reduced conductance matrix is symmetric, diagonal scalar generation can
compute only the upper triangle:

```c
for (int row = 0; row < nr_active; ++row) {
    for (int col = row; col < nr_active; ++col) {
        ...
    }
}
```

This is safe if later code only reads upper-triangular reduced entries for
`varG` stamping. If a later matrix operation reads full `Gred_code`, either keep
the full write or mirror each entry into the lower triangle.

### Use `Grk/Gkr` Sparsity In Diagonal Scalar Path

The diagonal scalar path currently resembles:

```text
for row in retained:
  for col in retained:
    for k in internal:
```

This assumes every `Grk[row,k]` and `Gkr[k,col]` may be nonzero. Electrical
networks are often sparse. For each internal node `k`, precompute retained
neighbors:

```text
neighbors[k] = retained nodes connected through Grk[:,k] or Gkr[k,:]
```

Then accumulate only those pairs:

```text
for k:
  inv = 1 / Gkk[k,k]
  for row in neighbors[k]:
    for col in neighbors[k]:
      Gred[row,col] -= Grk[row,k] * inv * Gkr[k,col]
```

Expected cost changes from:

```text
NR * NR * NK
```

to roughly:

```text
sum_k degree(k)^2
```

This can be combined with upper-triangle-only generation by emitting only
`row <= col` pairs.

### Direct Scalar Stamp For `NK = 0`

If there are no internal nodes, no Schur complement is needed. Dynamic G entries
can often be assigned directly:

```c
varG_A_B = expression;
```

instead of:

```c
set_CODE(&G_code, ...);
varG_A_B = get_CODE(&G_code, ...);
```

### Avoid Registering RAM-Only Matrices

Matrices used only in `RAM_PASS1` do not need CODE-side `MATRIX_` allocation,
`matrix_register`, or `conditionMatrixForCODE`.

Keep CODE-side matrix objects only when they are actually read or written in
runtime sections.

### Diagonal Plus Small Coupled Block

For `Gkk` that is mostly diagonal with a small coupled subblock, use the block
inverse structure rather than a full inverse. If the coupled block is size 2 or
3, prefer the RTDS symmetric fast inverse helpers.

Future refinement: allow more of the diagonal portion to remain scalar and only
use matrix operations for the small coupled block.

### Optional Manual Scalar Preview

Keep the default optimized C export path unchanged, but consider adding a
separate user-triggered preview button such as "convert diagonal `Gkk` case to
scalar code". This is an experimental/readability tool, not a model mutation.

The button should be enabled only when the current selected case/profile has a
structurally diagonal `Gkk`. For multi-case exports, each case must be checked
independently; diagonal cases can preview scalar code while non-diagonal cases
must keep the normal matrix-DAG path.

Expected behavior:

- Generate a reversible scalar-code variant from the same backend math result.
- Let the user compare the matrix-DAG draft and the scalar draft.
- Keep the existing draft as the default fallback if the scalar version is too
  long, harder to review, or fails a budget check.
- Do not save the scalar variant into the circuit JSON unless the user
  explicitly accepts it as an export preference.

Safety rules:

- Use the existing structural `Gkk` diagonal proof; do not use `sp.cancel`,
  `sp.factor`, or broad `sp.simplify` to justify the conversion.
- Respect RAM/CODE ownership and runtime-mutable case constraints.
- Do not force scalar generation for dummy-finalized or multi-profile cases
  whose active retained/internal dimensions differ unless the case branch has
  already been proven compatible.
- Run the same generated-code checks as normal C export, including undefined
  enum names, duplicate declarations, stale cache versions, and no unused
  MATRIX initialization.

### Auto Dummy Nodes For Pack Multi-Case

When a packed multi-case component contains cases with different internal
topologies but the same external interface, the editor could automatically add
dummy alignment nodes for the missing internal nodes instead of requiring the
user to place N-Dummy blocks manually.

The intended behavior is only a workflow aid:

- Detect which canonical internal nodes are present in some pack cases but
  missing in others.
- Add isolated dummy alignment nodes in the missing cases.
- Preserve the external node count, names, and order.
- Keep dummy nodes as topology-compatibility placeholders; final C export should
  still remove or neutralize dummy-only rows/columns where safe.

This should remain separate from normal branch editing. It must not change
ordinary single-case circuits or allow dummy nodes to alter physical topology.

### Pack Cases With Different Internal Elimination Sets

A packed multi-case component is allowed to use different internal eliminated
nodes in different init-time cases as long as the external port signature is the
same. The editor save guard should therefore validate only the external port
count, names, and order. Internal node groups, `K_v/K_h`, final recovery data,
and dummy-finalized rows are case-local data saved inside each packaged case.

This is different from a runtime-mutable case group. A runtime-mutable group can
change only expressions during CODE; it must not change topology, node order,
retained/internal dimensions, or recovery layout. If a bug appears around Pack
case saving, first check whether the failing guard is incorrectly comparing
`internalGroups` instead of only `externalGroups`.

## Debugging Lessons

### Multi-Case Export Cache Coverage

Saved JSON files may contain several `multiCaseExportCache` entries. A bug can
exist in cache entry 1 or later while cache entry 0 looks correct. When checking
multi-case C export regressions, rebuild every cache payload:

```text
for cache in multiCaseExportCache:
  payload = json.loads(cache["key"])
  build_multi_case_response(payload)
```

Do not conclude that a fix works from the first cached payload only.

### Conditional Final-G RAM Stamp Owns `g_mat_over`

When the conditional final-G path writes complete RAM-owned entries directly to
`g_mat_over`, the earlier RAM `multcase_G_*` alias switch must be removed. If
both layers remain, the code may compute:

```c
multcase_G_... = ...;
```

and then never use it in `g_mat_over`. This is dead code and can also mislead
debugging because it looks like the alias layer is active when the final-G RAM
stamp has already taken ownership.

### RAM/CODE Source Temp Sharing Must Check The Actual Draft Path

Complex source-level `G_full` and `Ihis_full` expressions can share the same
small subexpressions. For example, `Trf_Ctest.json` has RAM-side G terms and
CODE-side Ihis terms that both use `1/(G11 + Gc)`. If the selected case is
init-time fixed and `RAM_PASS1` already computes the same RHS, a persistent
`sourceGI_*` temp can be declared in `STATIC`, assigned in `RAM_PASS1`, and reused
later by CODE/Ihis.

Two debug rules matter here:

- Validate through every `multiCaseExportCache` entry, not only the first helper
  path. The UI may hit the conditional-final-G structured post-process path
  while a unit helper hits the alias-template path.
- For the text-level safety pass, an exact RHS already emitted in `RAM_PASS1` is
  the safety evidence. A conservative symbol table may classify one symbol as
  CODE because another case needs CODE, but the already-generated RAM line shows
  this exact init-time case can compute that RHS before CODE.
- Shared source temporaries can be emitted in stages. A later source expression
  may already be rewritten through earlier `sourceGI_*` temporaries, for example
  `sourceGI_C1_case0_tmp4 * sourceGI_C1_case0_tmp9`. Reuse detection must compare
  both forms: the original SymPy expression and the already-substituted emitted
  C expression. Otherwise `RAM_PASS1` can recompute the same product as a local
  `sourceG_*` temp even though a persistent `sourceGI_*` temp already exists.
- This check must stay structural/textual. Do not use `cancel`, `simplify`, or
  `factor` merely to prove source-temp equality; if the exact emitted RHS does
  not match, leave the generated code unchanged.
- Empty fixture caches are not regression coverage. If a saved JSON has an empty
  `multiCaseExportCache`, cache-driven tests must skip explicitly or build a
  minimal direct request/helper-level regression.
- Keep runtime modules compatible with the Python shipped on other machines.
  Module-level type aliases such as `tuple[...]` and `dict[...]` can fail on old
  Python versions before any API logic runs. Use `typing.Tuple` / `typing.Dict`
  in runtime code unless the project has explicitly dropped those versions.

### Source-Level CSE Temporaries Need Liveness Checks

`sp.cse` can emit chained temporaries. A temporary is valid even if it is not
written directly to `g_mat_over`, as long as another live temporary uses it.
Regression tests should check transitive usage, not only direct `g_mat_over`
mentions.

Conversely, a `multcase_G_*` or `sourceG_*` value that is assigned and never
referenced later in the same RAM/CODE block is a real codegen smell.

### Dimension Enums Are Not Decorative

Optimized C export should emit retained/internal dimension enums only when the
generated C draft still uses those names. No-internal direct/original-G paths can
stamp `g_mat_over` with literal compact dimensions and do not need:

```c
enum { RETAINED_NODES = n, INTERNAL_NODES = 0 };
```

Leaving that enum in the draft is harmless to compilation but confusing during
review, especially after the no-Schur path has already avoided runtime matrix
objects. Strip an unused `NR/NK` enum before renaming dimensions. If later code
references `NR` or `NK`, keep the readable `RETAINED_NODES/INTERNAL_NODES` enum
and comments.

Important exception: retained-profile multi-case drafts need their profile enum
whenever generated code references `PACK_CASE_n`, `RETAINED_NODES_CASE_n`,
`retained_profile`, or `node_active`. This is true even when
`INTERNAL_NODES = 0` and no runtime `MATRIX_` objects are required. The cleanup
pass must not assume that a no-elimination path can drop every enum; if the old
`NR/NK` enum has already been removed, retained-layout compaction must insert a
fresh `PACK_CASE_n / RETAINED_NODES_CASE_n / INTERNAL_NODES` enum before the
`STATIC` section.

### Matrix Runtime Init Must Have Live MATRIX Users

`initializeMatricesForCode()` and `rtds_matrix_code_ready` are only needed when
the generated CODE/T1_T2 sections use runtime `MATRIX_` objects or
`conditionMatrixForCODE`. A no-internal-node path that writes RAM `g_mat_over`
and assigns CODE `GValue`/`Inj` scalars directly should not emit this ready block.

This is more than cosmetic: a stray ready block suggests a matrix DAG exists
when the draft is actually scalar-only, and it makes code review harder. Keep the
block for Schur/Vk/Ihis matrix paths; strip it for direct no-elimination drafts.

The same rule applies to allocation error counters. If a draft only uses
`g_mat_over/setupGMatrix` plus scalar `GValue`/`Inj` assignments and emits no
`err += matrixDim(...)`, then it should not emit `int err = 0` or the
`RTDS matrix allocation failed` check. Keep that check only on paths with live
runtime `MATRIX_` allocation.

### Frontend Warnings Must Use the Language Layer

Backend warnings are often rendered by specific frontend tabs rather than the
generic error path. Those renderers must call the same localization helper used
by optimized export warnings. A common miss is a frontend-rendered reduced
diagnostic, such as a retained node with zero `Gred` row/column and zero
`Ihisred`, staying Chinese-only in English mode.

Rule of thumb: if a warning is shown inside a formula card, route it through
`localizedBackendWarning(...)` or `optText(...)` at the render site. Do not rely
on the backend string being magically translated.

### API Error JSON Must Be Encoding-Safe

The command-line API scripts are subprocess boundaries. On Windows, another
machine may run Python with a non-UTF-8 stdout code page. If an API catches a
math/topology error and writes Chinese text with `ensure_ascii=False`, stdout can
raise `UnicodeEncodeError` while trying to report the original error. The browser
then receives a traceback or partial JSON and reports a misleading JSON parse
failure.

Keep CLI JSON output ASCII-safe with `ensure_ascii=True`. `JSON.parse` restores
the original Chinese string in the browser, while the subprocess transport only
contains `\uXXXX` escapes. Treat a clean singular-Gkk warning and a broken JSON
traceback as two different layers: the former is a circuit/elimination issue; the
latter is an API transport bug.

### Backend Error Display Must Respect UI Language

Some backend errors intentionally use a bilingual form such as
`Chinese explanation / English explanation` so one API response can serve both
language modes. The frontend must not render raw `error.message` directly in
math/codegen tabs. Route backend errors through `localizedBackendErrorMessage()`:
Chinese mode keeps the full message, while English mode extracts the text after
` / `. This keeps JSON transport fixes separate from display-language fixes.

### Singular `Gkk` Is A Topology Warning, Not A Formatting Bug

Schur elimination solves the internal-node equation:

```text
Gkk * Vk = -(Gkr * Vr + Ihisk)
```

Therefore the eliminated internal block `Gkk` must be invertible. If `Gkk` is
singular, the selected internal nodes contain at least one voltage mode that is
not uniquely determined by the retained nodes and sources. Typical causes are a
floating common mode, an ideal voltage-source/constraint loop, or a missing
ground/reference path.

Debugging rule: do not hide this warning by forcing an inverse. The user-facing
message should explain the physical fix:

- keep one of the nodes as retained/external reference,
- add a real ground/reference/admittance path, or
- use a future MNA/constraint-aware reduction path for ideal source constraints.

### Pack Cases With Different Internals Need A Gkk Placeholder Adapter

Pack multi-case editing can produce cases with the same external ports but
different internal eliminated nodes and voltage-recovery formulas. This is not a
reason to abandon the structured Schur path. For init-time Pack cases, align the
case profiles with backend-only placeholder internal nodes so every case can use
the same `Grr/Grk/Gkr/Gkk` layout.

The safe bridge is:

1. Keep the user-visible external port order fixed across all cases.
2. Build the union of internal eliminated nodes across the Pack cases.
3. For a case that does not physically contain one of those internal nodes, add
   an isolated backend placeholder row/column to `Gkk` with identity conductance
   and zero `Ihis`. This makes the template dimensions match without changing
   the physical network.
4. Before solving the real internal-node recovery for a case, skip placeholder
   rows/columns and emit only the recovery work that case needs.
5. Use the existing matrix-DAG Schur flow for real `Gkk` work; diagonal or
   placeholder-only rows may still be optimized away by the normal structural
   codegen rules.

The older final-retained adapter is kept as a compatibility fallback for saved
payloads that already provide only final retained `G/Ihis` data. It should not be
the first choice when raw `G_full/Ihis_full` data can be aligned with
placeholder internals.

Frontend debugging rule: the backend adapter only works if the multi-case
profile payload forwards `finalExternalGroups`, `finalGMatrix`, `finalIhisVector`,
and the case recovery fields from the active Pack case. If the UI still reports
`all_nodes changed` for a Pack whose exposed ports match, inspect the frontend
payload first; the saved case data may be correct while the export request is
still sending the raw per-case internal topology.
4. Cases with no recovered internal nodes simply skip recovery.

This preserves the normal multi-case alias-template rule while keeping voltage
recovery for cases that actually had internal nodes.

Generated C names must use the user's final node names, not backend placeholder
ids. For example, if the UI node list names the internal nodes `inner_left` and
`inner_right`, recovery variables and matrix aliases should use names such as
`inner_left`, `inner_right`, `Gkk_inner_left_inner_left`, and
`multcase_G_C1_inner_left_inner_left`. Backend ids such as
`pkg_internal_C1_0` are only internal bookkeeping and should not leak into the C
draft.

Debugging gotcha: saved project JSON may store `finalGMatrix`,
`finalIhisVector`, `finalK_v`, and `finalK_h` as stringified matrix expressions
such as `"[[G11, -G11], ...]"`, not as nested arrays. Matrix parsing helpers must
recognize that format before checking shape. If a string matrix is passed
directly to `sp.Matrix`, it can be interpreted as a character sequence, causing
false dimension failures and making the backend fall back to the wrong path.

## Safety Rules

- Do not use large expanded final `Gred` expressions to infer core/direct or
  Schur structure.
- Do not rely on `sp.cancel`, `sp.simplify`, or `sp.factor` for large dense
  matrices during C export.
- Multi-case `case_id` is treated as init-time fixed unless a case group is
  explicitly marked runtime-mutable; runtime-mutable groups may only change
  expressions, not topology or dimensions.
- When uncertain, fall back to the existing matrix-DAG path.
