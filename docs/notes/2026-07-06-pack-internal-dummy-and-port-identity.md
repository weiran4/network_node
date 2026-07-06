# Pack Internal Dummy Pruning And Port Identity Notes

## Scope

This note records the July 2026 Pack multi-case fixes around:

- different internal eliminated-node counts across Pack cases;
- codegen-level pruning of missing internal dummy placeholders;
- shared `T1_T2` internal voltage recovery helpers;
- Pack case edit validation messages for external port identity.

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

## Helper Functions

`CODE_FUNCTIONS` helpers are emitted only when used:

- `network_node_recover_vk_diag(...)`
- `network_node_recover_vk_matrix(...)`
- `network_node_recover_vk_from_wgkr_only(...)`

Important assumptions:

- Diagonal helper assumes `W` is diagonal, `Gkr = transpose(Grk)`, and `tmp_Grk_W_code = Grk * W`.
- Matrix helper assumes `W` is fully populated and symmetric, not just upper triangular.
- The `from_wgkr_only` helper is only for legacy/static paths where `tmp_W_Gkr_code` already stores `W * Gkr` and no `W * Ihisk` term exists.

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
- Do not expose backend IDs as if they were user node names.
- Do not use display names alone for Pack external-port validation. Display names can be edited to hide a slot/order mistake.
- Do not helperize `Vk` recovery in a way that drops the `W * Ihisk` history-source term.

## Regression Set

Run these after changing this area:

```powershell
python -m pytest tests/test_frontend_optimized_reuse.py -q
python -m pytest tests/test_multicase_c_export_alias_template.py tests/test_multicase_common_dummy_internal_codegen.py tests/test_multicase_dummy_node_block.py tests/test_runtime_mutable_case_group.py -q
python -m py_compile optimized_elimination_api.py
```
