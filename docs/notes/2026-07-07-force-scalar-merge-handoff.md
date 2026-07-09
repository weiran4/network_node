# Force Scalar / Internal Dummy Merge Handoff

## Current Repository State

- Workspace: `E:\network_node`
- Current branch: `codex/ui-engineering-polish`
- Remote: `origin https://github.com/weiran4/network_node.git`
- Remote HEAD: `origin/codex/ui-engineering-polish`
- Latest pushed commit: `e568820 Add force scalar preflight guard`
- Feature branch merged: `codex/force-scalar-elimination`
- Merge type: fast-forward into `codex/ui-engineering-polish`

Useful commands for a new Codex conversation:

```powershell
cd E:\network_node
git switch codex/ui-engineering-polish
git status --short
git log --oneline --decorate -6
```

## What Was Completed

### Pack / multi-case internal dummy pruning

- Missing internal dummy nodes are pruned at codegen level before effective `Gkk/Gkr/Grk/Ihisk/W/Vk` matrix operations.
- Per-case `internal_profile` / `internal_active` is used when Pack cases have different internal node counts.
- Zero-internal cases avoid 0x0 `matrixLIB` usage and skip internal recovery matrices entirely.
- User-defined internal node names are used in voltage recovery, for example `inner_left`, `inner_right`.
- External Pack port validation now distinguishes user display names from backend port identity.

### Matrix vs scalar C export

- UI is simplified to two modes near the C draft actions:
  - `矩阵 / Matrix`
  - `标量 / Scalar`
- Default mode is matrix.
- Old `auto` values are normalized to matrix for compatibility.
- The old large optimized-elimination settings card was removed.

### Force-scalar codegen

- Force-scalar is a separate C codegen route, not a new mathematical reduction.
- It does not emit runtime `MATRIX_`, `matrixDim`, `matrix_register`, or `conditionMatrixForCODE`.
- It has stage-aware scalar reuse:
  - RAM-only temps stay in `LOCAL_STATIC:`.
  - RAM values reused by `T1_T2` are persistent `STATIC:` variables.
  - Repeated denominators are reused when safe.
- CBuilder compatibility rule is enforced more broadly: generated C should avoid C99 `for (int ...)` loop declarations.

### CBuilder section label spacing

- All generated CBuilder section labels must be followed by a blank line.
- This applies to optimized-elimination C and multi-case C, including `STATIC:`, `LOCAL_STATIC:`, `RAM_PASS1:`, `GVALUES:`, `CODE_FUNCTIONS:`, `CODE:`, `BEGIN_T0:`, and `T1_T2:`.
- Do not emit declarations or statements immediately after a section label. In particular, avoid `RAM_PASS1:` followed directly by `int row;` and `BEGIN_T0:` followed directly by `int row;`.
- Keep the spacing rule as a final C-draft normalization step so future insertions after labels inherit the same RTDS/CBuilder-safe format.

### Force-scalar preflight guard

- Multi-case `force_scalar` now computes a lightweight `scalar_preflight` before C draft generation.
- If estimated expression size is too large, backend returns:
  - `fast_path = case_scalar_preflight_blocked`
  - a short placeholder C comment
  - bilingual warning text and metrics
- Frontend shows a warning card and a second action:
  - `仍然生成标量 / Continue scalar generation`
- Confirmation resubmits with `force_scalar_confirmed = true`.
- Cache key includes `force_scalar_confirmed`, so blocked preflight and confirmed C output do not mix.
- `Trf_RCY_UCM.json` is covered by regression and should be blocked before full scalar C generation.

## Files Touched

Major implementation files:

- `optimized_elimination_api.py`
- `nodal_tool/optimized_elimination.py`
- `index.html`

Tests:

- `tests/test_frontend_optimized_reuse.py`
- `tests/test_multicase_c_export_alias_template.py`
- `tests/test_multicase_common_dummy_internal_codegen.py`
- `tests/test_multicase_dummy_node_block.py`
- `tests/test_dynamic_subblock_schur.py`
- `tests/test_optimized_elimination.py`

Fixtures:

- `exports/Trf_Ctest_dummy_small_varG.json` was committed because tests reference it.
- `exports/Trf_RCY_UCM_constant.json` remains untracked and was intentionally not committed because no test currently references it.

Notes:

- `docs/notes/2026-07-06-pack-internal-dummy-and-port-identity.md` was updated with architecture, CBuilder lifecycle rules, scalar preflight guardrails, and easy mistakes.

## Verification Already Run

Before merge:

```powershell
python -m py_compile optimized_elimination_api.py nodal_tool\optimized_elimination.py
python -m pytest tests/test_frontend_optimized_reuse.py tests/test_multicase_c_export_alias_template.py tests/test_optimized_elimination.py tests/test_multicase_common_dummy_internal_codegen.py tests/test_multicase_dummy_node_block.py tests/test_dynamic_subblock_schur.py -q
git diff --check
```

Result:

- `150 passed, 2 skipped`
- only existing SymPy deprecation warnings
- `git diff --check` only reported Git CRLF warnings, no whitespace errors

After merge:

```powershell
python -m py_compile optimized_elimination_api.py nodal_tool\optimized_elimination.py
git push origin codex/ui-engineering-polish
```

Push result:

```text
4bd3464..e568820  codex/ui-engineering-polish -> codex/ui-engineering-polish
```

## Current Loose End

Current `git status --short` after push:

```text
?? exports/Trf_RCY_UCM_constant.json
```

This file is local and untracked. Do not assume it should be deleted or committed without checking its purpose.

## Suggested Next Conversation Prompt

```markdown
We are continuing in `E:\network_node` on branch `codex/ui-engineering-polish`.
The force-scalar/internal-dummy feature branch was already fast-forward merged and pushed at commit `e568820`.

Please first run:

```powershell
git status --short
git log --oneline --decorate -6
```

Important context:
- Default optimized C export is matrix.
- Scalar export is explicit and now guarded by `scalar_preflight`.
- Large multi-case scalar export such as `Trf_RCY_UCM.json` should show a warning and require confirmation before generating scalar C.
- `exports/Trf_RCY_UCM_constant.json` is untracked and should not be touched unless we decide what it is for.
```
