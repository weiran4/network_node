# Pack Gkk Placeholder Recovery Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support Pack multi-case networks where final external ports are identical but each case has a different set of eliminated internal nodes, while preserving correct final `Gred/Ihisred` and per-case voltage recovery.

**Architecture:** Keep the existing user-visible Dummy/N-Dummy behavior unchanged. Add a backend-only recovery placeholder layer that can align internal-node axes for Pack cases, but always prunes placeholder rows before `Gkk` inversion. V1 should continue to prefer the safer final-retained adapter path; the Gkk-placeholder path is an implementation extension for cases where we want to reuse the normal Schur/Gkk optimization machinery.

**Tech Stack:** Python backend (`optimized_elimination_api.py`, `nodal_tool/*`), frontend Pack case serialization (`index.html`), pytest, existing fixtures in `exports/`.

---

## Design Summary

### Problem

Some Pack multi-case networks have:

- The same external ports in every case.
- Different internal nodes selected for elimination in each case.
- Different voltage-recovery formulas per case.

Using only black-box `Gred/Ihisred` loses `Vk` recovery. Forcing every case to have the same raw `Gfull/Gkk` dimension by adding real dummy rows can create singular `Gkk`. The correct design is to treat missing internal nodes as placeholders for alignment only, not as physical nodes to invert.

### Two-Layer Model

Layer 1: final retained equation

```text
Iret = Gred * Vret + Ihisred
```

This layer must have the same port count, names, order, and matrix shape across all Pack cases.

Layer 2: per-case recovery profile

```text
Vk_case = K_v_case * Vret + K_h_case
```

This layer may differ by case. A case with one internal node has one recovery row; a case with two internal nodes has two rows.

### Placeholder Meaning

Existing UI Dummy/N-Dummy:

- User-visible modeling helper.
- Used to align missing full-network nodes or enforce dummy finalization.
- Has connection rules and UI warnings.
- Must not be confused with physical branches.

New backend Gkk placeholder:

- Backend-only metadata.
- Represents "this case does not have this internal recovery node".
- May appear in a union internal-node order for template alignment.
- Must be removed before any inverse, determinant, diagonal check, or matrix multiplication involving `Gkk`.
- Must not create `G_EPSILON`.
- Must not enter `setupGMatrix`.
- Must not produce `Vk` recovery rows.

---

## Compatibility Rules

### Allowed

Init-time Pack case groups may use this path when:

- Final external ports match exactly.
- Final `Gred/Ihisred` dimensions match exactly.
- Case id is fixed before simulation.
- Each case can provide its own recovery profile.

### Rejected

Reject or fall back when:

- Runtime mutable case id is enabled and topology/internal dimensions differ.
- Final external port names/order differ.
- Final retained matrix shape differs.
- Recovery row count does not match that case's real internal nodes.
- A placeholder row would be inverted instead of pruned.

---

## Implementation Strategy

### V1: Final-Retained Adapter

Use this first because it is safest and already close to the current code.

For each Pack case:

1. Compute the case normally.
2. Store `finalExternalGroups`, `finalGMatrix`, `finalIhisVector`.
3. Store `finalInternalGroups`, `finalK_v`, `finalK_h`.
4. Multi-case export runs alias-template on final retained equations.
5. `T1_T2` emits case-specific recovery only for real internal nodes.

This does not require users to manually add dummy internal nodes.

### V2: Gkk Placeholder Alignment

Use this only when we intentionally want to reuse the existing `Gkk` optimization logic.

Build:

```text
union_internal_nodes = ordered union of internal nodes across Pack cases
real_internal_nodes(case) = internal nodes physically present in that case
placeholder_internal_nodes(case) = union_internal_nodes - real_internal_nodes(case)
```

Before Schur:

```text
Gkk_real = Gkk_union[real_internal_nodes, real_internal_nodes]
Grk_real = Grk_union[retained_nodes, real_internal_nodes]
Gkr_real = Gkr_union[real_internal_nodes, retained_nodes]
Ihisk_real = Ihisk_union[real_internal_nodes]
```

Then run existing optimization:

```text
W_real = inv(Gkk_real)
Gred = Grr - Grk_real * W_real * Gkr_real
Ihisred = Ihisr - Grk_real * W_real * Ihisk_real
Vk_real = -W_real * Gkr_real * Vret - W_real * Ihisk_real
```

If `real_internal_nodes` is empty:

```text
Gred = Grr
Ihisred = Ihisr
no Vk recovery for this case
```

---

## File Map

- Modify: `E:/network_node/optimized_elimination_api.py`
  - Harden `_final_retained_profile_adapter`.
  - Add adapter failure diagnostics.
  - Add optional backend-only placeholder metadata helpers.
  - Ensure placeholders are pruned before `Gkk` inversion.

- Modify: `E:/network_node/index.html`
  - Allow Pack save when final external ports match but internal recovery nodes differ.
  - Preserve final retained fields per case:
    `finalExternalGroups`, `finalGMatrix`, `finalIhisVector`, `finalInternalGroups`, `finalK_v`, `finalK_h`.
  - Keep runtime-mutable topology mismatch rejected.

- Modify: `E:/network_node/tests/test_multicase_c_export_alias_template.py`
  - Add backend tests for different raw internal counts but same final retained ports.
  - Add regression for `exports/Trf_Ctest_dummy.json`.

- Modify: `E:/network_node/tests/test_multicase_dummy_node_block.py`
  - Confirm old Dummy/N-Dummy behavior is unchanged.

- Modify: `E:/network_node/PROJECT_NOTES.md`
  - Private notes only; do not commit unless explicitly requested.

---

## Tasks

### Task 1: Checkpoint

- [ ] Run `git status --short`.
- [ ] Commit current source state if dirty changes are ours.
- [ ] Do not stage `PROJECT_NOTES.md` unless explicitly requested.

### Task 2: Backend Final-Retained Adapter Tests

- [ ] Add a test with two cases:
  - Case 0 raw internal nodes: `["inner_left"]`.
  - Case 1 raw internal nodes: `["inner_left", "inner_right"]`.
  - Both cases final ports: `["N1", "N2", "N3", "N4"]`.
- [ ] Assert `_final_retained_profile_adapter` normalizes payload `all_nodes` to final ports.
- [ ] Assert recovery profiles preserve the per-case internal nodes.
- [ ] Assert runtime-mutable topology mismatch remains rejected.

### Task 3: Backend Diagnostics

- [ ] When raw topology validation fails, try final-retained adapter.
- [ ] If adapter fails, include the reason:
  - missing final fields
  - final port mismatch
  - final matrix shape mismatch
  - recovery matrix shape mismatch
  - runtime-mutable topology mismatch
- [ ] Keep existing error text compatible with current tests.

### Task 4: Frontend Save/Export Preservation

- [ ] Ensure Pack case save keeps final retained fields after editing internal cases.
- [ ] Ensure `multiCaseExportPayload()` passes final fields for each case.
- [ ] Reject final external port mismatch with a clear bilingual message.
- [ ] Do not require users to add visible dummy nodes just because internal recovery counts differ.

### Task 5: Optional Gkk Placeholder Helper

- [ ] Add backend helper to compute:
  - union internal order
  - real internal mask per case
  - placeholder internal mask per case
- [ ] Add tests proving placeholder rows are not inverted.
- [ ] Keep helper unused unless a later task explicitly routes Gkk optimization through it.

### Task 6: C Export Recovery

- [ ] Multi-case C export should use final retained template for `Gred/Ihisred`.
- [ ] Emit `T1_T2` recovery switch per Pack case.
- [ ] Case with no real internal nodes emits no recovery rows.
- [ ] Case with one/two real internal nodes emits exactly one/two recovery rows.

### Task 7: Regression Verification

- [ ] Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -k "Trf_Ctest_dummy or final_ports or recovery" -q
```

- [ ] Run:

```powershell
python -m pytest tests/test_multicase_dummy_node_block.py -q
```

- [ ] Manually verify `exports/Trf_Ctest_dummy.json` through the same user path:
  1. Open app.
  2. Import JSON.
  3. Click Multi-Case C Export.
  4. Confirm no `multi-case topology invariant failed`.
  5. Confirm final ports are used for `setupGMatrix`.
  6. Confirm case-specific recovery code exists.

### Task 8: Notes

- [ ] Update `PROJECT_NOTES.md` with:
  - Final retained equation and recovery profile are separate layers.
  - Raw topology mismatch may be valid for init-time Pack cases.
  - Runtime-mutable topology mismatch is still invalid.
  - Placeholder internal nodes are alignment metadata only and must be pruned before `Gkk`.

---

## Safety Boundaries

- Do not change normal canvas branch behavior.
- Do not change single-case optimized elimination unless tests require a shared helper.
- Do not change existing Dummy/N-Dummy connection rules.
- Do not introduce `G_EPSILON` for backend-only placeholders.
- Do not call `sp.cancel`, `sp.factor`, or broad `sp.simplify` on dense expanded matrices.
- Do not allow runtime-mutable case groups to change topology or matrix dimensions.

---

## Acceptance Criteria

- `Trf_Ctest_dummy.json` exports Multi-Case C successfully.
- Final external port order is identical across cases.
- Per-case recovery rows are preserved.
- No dummy placeholder is inverted.
- Existing Dummy/N-Dummy tests still pass.
- Existing alias-template and source-temp reuse behavior stays intact.
