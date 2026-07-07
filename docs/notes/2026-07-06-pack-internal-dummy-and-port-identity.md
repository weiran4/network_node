# Pack Internal Dummy Pruning And Port Identity Notes

## Scope

This note records the July 2026 Pack multi-case fixes around:

- different internal eliminated-node counts across Pack cases;
- codegen-level pruning of missing internal dummy placeholders;
- shared `T1_T2` internal voltage recovery helpers;
- Pack case edit validation messages for external port identity;
- the matrix-vs-scalar optimized C export mode split.

## Architecture

Pack multi-case export now keeps two layers separate:

1. **Final retained contract**
   - `finalExternalGroups`, `finalGMatrix`, and `finalIhisVector` define the shared external equation used by alias-template C export.
   - External ports must keep the same slot order, display name, and backend identity across Pack cases.

2. **Per-case internal recovery profile**
   - `finalInternalGroups`, `finalK_v`, and `finalK_h` describe which eliminated internal nodes exist in each Pack case.
   - The alias-template path may keep a union internal-node order for mapping and diagnostics, but each case selects an `internal_profile` with its own compressed `internal_active` count.

Missing internal nodes are backend placeholders only. They are not physical nodes and must not enter effective `Gkk/Gkr/Grk/Ihisk/W/Vk` matrix operations.

## Codegen Rules

- If `internal_active == 0`, do not call `matrixDim`, `matrix_register`, `conditionMatrixForCODE`, `set_CODE`, or matrix operations for internal matrices.
- Internal-related matrices are allocated, registered, and conditioned only inside `if (internal_active > 0)` guards.
- Zero-internal cases use `Ihisred = Ihisr` directly.
- One-internal diagonal profiles may use scalar voltage recovery.
- Non-diagonal profiles keep the matrix recovery path and reuse `tmp_Grk_W_code` symmetry.
- `INTERNAL_NODES` can still represent the maximum union count for static diagnostics, but runtime loops and matrix dimensions must use `internal_active` when profiles differ.
- Generated RTDS/CBuilder C must not use C99 loop-variable declarations such as `for (int row = 0; ...)`. Declare loop variables first (`int row; int col; int k;`) and emit `for (row = 0; ...)`.
- Variable lifetime follows CBuilder sections:
  - `STATIC:` is persistent and shared across RAM/CODE/T1_T2. Use it for runtime variables and RAM-precomputed scalars that are reused later in CODE/T1_T2.
  - `LOCAL_STATIC:` is RAM/setup-side storage. Use it for RAM-only symbols and RAM-only temporary scalars.
  - CODE/T1_T2 temporaries that are assigned at runtime but declared outside local helper blocks belong in `STATIC:`.
- If a scalar expression such as `1.0/(G8 + G9)` is computed in RAM and then reused in T1_T2, emit one persistent `STATIC` variable, assign it in RAM, and reference it in T1_T2. Do not emit a second T1_T2-only denominator temp for the same RAM-only expression.

## Helper Functions

`CODE_FUNCTIONS` helpers are emitted only when used:

- `network_node_recover_vk_diag(...)`
- `network_node_recover_vk_matrix(...)`
- `network_node_recover_vk_from_wgkr_only(...)`
- `network_node_recover_vk_from_grkw_only(...)`

Important assumptions:

- Diagonal helper assumes `W` is diagonal, `Gkr = transpose(Grk)`, and `tmp_Grk_W_code = Grk * W`.
- Matrix helper assumes `W` is fully populated and symmetric, not just upper triangular.
- The `from_wgkr_only` helper is only for legacy/static paths where `tmp_W_Gkr_code` already stores `W * Gkr` and no `W * Ihisk` term exists.
- The `from_grkw_only` helper is only for recovery-only paths where `tmp_Grk_W_code = Grk * W`, symmetry gives `W * Gkr = transpose(Grk * W)`, and no `W * Ihisk` term exists.
- Do not replace scalar diagonal recovery with a helper unless all matrices used by that helper are declared and allocated on that path.

## Dynamic GValues And Internal Profiles

Mixed Pack cases can combine different internal profiles with different G constant ownership. For example, one case can have an active internal node and a CODE-owned variable G, while another case has no internal nodes and only RAM constants.

In that shape, generated CODE must keep Schur refresh logic case-specific:

- cases with CODE-owned final GValues may update `Grr/Grk/Gkr/Gkk`, invert/update `W`, and refresh `Gred`;
- cases with no CODE-owned final GValues must not run the dynamic Schur update block;
- zero-internal cases must still bypass internal matrix reads even if another case in the same Pack needs them.

The RAM final-G replacement also must stop before the matrix lifecycle section. Accidentally swallowing `matrixDim`/`matrix_register` setup causes undeclared or unallocated runtime matrices later in CODE.

## Elimination Codegen Mode

Optimized C export has two user-facing codegen modes:

- `prefer_matrix`: default. Generate the shared `Gkk/W/Grk/Gkr` matrix Schur path where applicable.
- `force_scalar`: expert/test mode. Each init-time case is reduced to scalar final `G/Ihis/Kv/Kh` formulas and the generated C skips runtime `Gkk/W/Grk/Gkr` `MATRIX_` objects.

The UI should expose this as a concise two-choice toggle near the C draft actions:

- `矩阵 / Matrix`
- `标量 / Scalar`

Do not reintroduce the old large settings card or "auto recommendation" wording. Old saved `auto` values may still be accepted for compatibility, but the frontend normalizes them back to the matrix path.

The force-scalar path is still Schur elimination mathematically. It only changes code generation: per-case scalar final stamps, case-conditional `GValue` entries for CODE-owned final G terms, and direct scalar internal-node recovery. It preserves the important guards:

- zero-internal cases produce no internal matrix or recovery work;
- dynamic GValue refresh is scoped to the cases that need it;
- recovered internal nodes use user-facing node names from `node_display_names`;
- dummy internal placeholders are never emitted as `Gkk = 1` / `W = 0` rows.

Scalar-expanded codegen also has its own conservative reuse layer:

- no `MATRIX_`, `matrixDim`, `matrix_register`, or `conditionMatrixForCODE` output;
- repeated same-stage denominators such as `1.0/(G1 + G2)` are reused;
- RAM-only temporaries stay in `LOCAL_STATIC:` unless a later CODE/T1_T2 section needs the same value;
- RAM-computed values reused by CODE or T1_T2 must be persistent `STATIC:` variables assigned during RAM;
- RAM-stage denominator temps should be hoisted once and shared across RAM/CODE/T1_T2 when the denominator base depends only on RAM constants. Do not regenerate equivalent `scalar_code_inv_den_*` or `scalar_t1t2_inv_den_*` temps for the same RAM-only denominator.
- Multi-case force-scalar RAM overlays should reuse retained-node layout work when every case has the same final node order: set `g_mat_nods`, clear `g_mat_over`, and call `setupGMatrix(dim)` once around the case switch; keep only case-specific formula assignments inside the switch.
- Do not emit explanatory dimension enums in force-scalar multi-case C unless the generated C actually references them. Unused `NR_SUPER` / `NR_FINAL_MAX` / `CASE_COUNT` constants are noise and can trigger compiler warnings.
- In matrix-mode dummy multi-case exports with no eliminated internal nodes, alias case values must be synchronized from finalized per-case `G/Ihis`, not raw source matrices. Otherwise dummy conductances such as `G_EPSILON` can leak back through RAM alias switches after the dummy node has been removed.
- The same dummy-finalized alias synchronization must include `direct_retained_stamps`, not only `G_full` / `Ihis_full`. Shared aliases can cover multiple equivalent physical positions; per case, resolve all surviving positions and rewrite the alias only when they agree, or use `0` when no position survives.
- `setupGMatrix(...)` must receive a concrete retained dimension for each layout profile. Do not emit `setupGMatrix(node_active)` for dummy cases whose external retained count differs; branch by `retained_profile` and call `setupGMatrix(RETAINED_NODES_CASE_n)`.

Rollback point before this feature: commit `4bd3464 Guard pack dynamic internal profile codegen` on branch `codex/ui-engineering-polish`.

## Force-Scalar Performance Guardrail

Force-scalar is intentionally available for small circuits and diagnostics, but it can explode on large multi-case formulas. `Trf_RCY_UCM.json` is the current warning example: forcing scalar expansion creates very large per-case expressions, and profiling showed most time in repeated symbol/stage scans over huge formulas before C text emission.

Observed shape from that case:

- 8 init-time scalar profiles;
- total formula cost around 47k operations;
- several cases produce 40k-70k character scalar expression groups;
- `_symbols_in_matrices` and repeated `_expr_stage` scans dominate runtime.

Recommended next guardrail before making force-scalar a normal user workflow:

- keep matrix as the default;
- preflight scalar formula cost before generation;
- warn or require a second confirmation when a multi-case scalar export exceeds the threshold;
- still allow force-scalar for expert debugging after the user explicitly accepts the cost.

Implemented guardrail:

- `force_scalar` multi-case export computes a lightweight `scalar_preflight` summary before C text generation.
- The preflight counts expression operations and bounded text size from final `G/Ihis/Kv/Kh` formulas. It must not call the expensive C emitter, `_symbols_in_matrices`, or repeated stage/symbol scans.
- If severity is `danger` and the request is not confirmed, the backend returns `fast_path = case_scalar_preflight_blocked` with a short placeholder C comment instead of generating scalar C.
- The frontend shows a bilingual warning card and a second action, `仍然生成标量 / Continue scalar generation`, which resubmits with `force_scalar_confirmed = true`.
- Cache keys include `force_scalar_confirmed` so blocked preflight results and confirmed scalar drafts cannot be mixed.

## Pack G Constant Edit Sync

The side-panel G constant controls edit the Pack branch view, but export uses the active `packageOriginal.networkCases[index]` snapshot. Any `packedG.*` edit or batch "set all constant/non-constant" action must copy the current packaged branches back into the active network case before rendering/exporting.

## Frontend Port Identity UX

During Pack case edit, users see node display names such as `N1`, `N2`, `N3`, `N4`, but the backend also tracks fixed port identities such as `N6`, `N2`, `N5`, `N4`.

Validation must use backend identity plus display name plus order. A case is invalid if a user swaps backend identities but renames the nodes back to the expected display names.

The error message should show both:

- user-facing slot names: `1. N1, 2. N2, ...`;
- backend port identities: `1. N6, 2. N2, ...`.

Use the term "port identity" and explain that it is a fixed backend ID used to validate external port slots and order. Avoid mixed labels like `N6. N1`; they make backend IDs look like user node names.

## Easy Mistakes

- Do not treat a 0x0 matrix as safe. The RTDS `matrixLIB` path should be assumed not to support zero-dimensional matrices.
- Do not set missing internal dummy `Gkk` rows to `1` and `W` rows to `0` in generated code. That is mathematically harmless in some formulas but still leaves dummy work in CODE.
- Do not let `default` internal profile fall back to the maximum-internal profile when a zero-internal profile exists.
- Do not run CODE-side Schur refresh unconditionally just because one Pack case has CODE-owned GValues.
- Do not let conditional RAM final-G replacement consume the matrix lifecycle block.
- Do not treat `force_scalar` as a new mathematical reduction. It is a C codegen mode and must produce the same final Schur equations.
- Do not let force-scalar CODE-owned G terms leak into RAM-only cases.
- Do not assume force-scalar is always simpler. For large multi-case systems, scalar expression growth can be worse than the matrix path.
- Do not make scalar preflight as expensive as scalar generation. The preflight is a guardrail and should stay cheaper than full C emission.
- Do not fix C99 `for (int ...)` only in one export branch. The no-C99-loop rule applies to scalar-expanded, matrix Schur, dummy-finalized, retained-layout, and alias-template generated C.
- Do not put RAM-only temporary scalars in `STATIC:` unless CODE/T1_T2 also needs them; use `LOCAL_STATIC:` for RAM-only temporaries.
- Do not put a RAM-computed scalar that T1_T2 needs in `LOCAL_STATIC:`; `LOCAL_STATIC` should not be assumed available across runtime phases.
- Do not declare user symbols used by scalar-expanded CODE/T1_T2 formulas in `LOCAL_STATIC:`. If a symbol appears in runtime assignments or voltage recovery, it belongs in `STATIC:` even if some RAM formulas also reference it.
- Do not optimize denominator reuse per section only. First find RAM-only denominator bases used by runtime sections, hoist them to `STATIC:` temps assigned in RAM, then substitute those temps into RAM/CODE/T1_T2.
- Do not repeat `g_mat_nods`, zero-fill loops, or `setupGMatrix` per force-scalar case when all cases stamp the same retained ports in the same order.
- Do not reuse raw source alias case values after dummy finalization. The C draft should only see dummy conductances if a surviving physical final node really depends on them.
- Do not scan only `G_full` / `Ihis_full` when cleaning dummy-finalized aliases; matrix-mode no-internal exports can source RAM aliases from `direct_retained_stamps`.
- Do not run dummy-finalized alias value synchronization on matrix-DAG cases that still have eliminated internal nodes. The sync was introduced for no-internal direct-retained dummy layouts; applying it to transformer/UCM cases can push large expressions into expensive comparison paths.
- Do not call algebraic equality helpers such as `_expr_equal_light()` from alias cleanup or other pre-codegen mux bookkeeping. Those helpers may expand large SymPy expressions; use structural equality only and skip the optimization when equality is not obvious.
- For dummy-finalized multi-case matrix DAG drafts with aliases, prefer synthetic reduced-dependency placeholders instead of recomputing full symbolic `Gred` just for dependency details. Small internal-node counts can still have huge expressions.
- Do not pass runtime-selected retained dimensions into `setupGMatrix`; use generated constants per retained layout.
- Do not restore `state.language` from saved circuit/project JSON. Startup demo files and shared project exports may carry old `"language": "zh"` metadata; loading them should not override the app/session default language.
- Do not trust Pack branch G constant edits unless the active network-case snapshot has been synchronized.
- Do not expose backend IDs as if they were user node names.
- Do not use display names alone for Pack external-port validation. Display names can be edited to hide a slot/order mistake.
- Do not helperize `Vk` recovery in a way that drops the `W * Ihisk` history-source term.

## Regression Set

Run these after changing this area:

```powershell
python -m pytest tests/test_frontend_optimized_reuse.py -q
python -m pytest tests/test_multicase_c_export_alias_template.py tests/test_multicase_common_dummy_internal_codegen.py tests/test_multicase_dummy_node_block.py tests/test_runtime_mutable_case_group.py -q
python -m pytest tests/test_dynamic_subblock_schur.py tests/test_structured_formula_elimination.py tests/test_optimized_elimination.py -q
python -m py_compile optimized_elimination_api.py nodal_tool\optimized_elimination.py
git diff --check
```
