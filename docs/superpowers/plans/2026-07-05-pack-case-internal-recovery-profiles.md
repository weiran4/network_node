# Pack Case Internal Recovery Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support Pack multi-case models where all cases expose the same external ports, but each case may eliminate a different set/count of internal nodes, while preserving each case's voltage recovery formulas.

**Architecture:** Keep the existing alias-template multi-case C export for the final retained equation. When raw per-case topology differs, each Pack case must first provide a same-shaped final retained equation (`finalGMatrix`, `finalIhisVector`, `finalExternalGroups`); the backend then runs alias-template codegen on that final equation and carries a per-case recovery profile (`finalInternalGroups`, `finalK_v`, `finalK_h`) for T1_T2 voltage recovery. This is not the old DummyBranch path: any internal placeholder used for alignment is pruned before Gkk inversion and is never treated as a physical recoverable node.

**Tech Stack:** Python backend (`optimized_elimination_api.py`, `nodal_tool/*`), browser frontend (`index.html`), pytest/unittest, Node-based frontend source tests.

---

## File Map

- Modify: `E:/network_node/optimized_elimination_api.py`
  - Harden `_final_retained_profile_adapter`.
  - Preserve per-case recovery profiles after adapting to final retained equations.
  - Improve diagnostics when final retained adaptation cannot be used.
  - Keep runtime-mutable topology mismatch forbidden.

- Modify: `E:/network_node/index.html`
  - Ensure Pack case save always stores final retained fields for each case when internal nodes differ:
    `finalExternalGroups`, `finalGMatrix`, `finalIhisVector`, `finalInternalGroups`, `finalK_v`, `finalK_h`.
  - Keep old Dummy/N-Dummy behavior unchanged.
  - Add clear validation text for "same external ports, different internal recovery".

- Modify: `E:/network_node/tests/test_multicase_c_export_alias_template.py`
  - Add backend tests for final-retained adaptation with different raw internal node counts.
  - Add test that runtime-mutable topology mismatch remains rejected.

- Modify: `E:/network_node/tests/test_frontend_optimized_reuse.py`
  - Add static source tests that Pack case save/export payload preserves final retained fields.

- Optional modify/add: `E:/network_node/exports/Trf_Ctest_dummy.json`
  - Use as regression fixture if the current file is stable and not private.

- Modify: `E:/network_node/PROJECT_NOTES.md`
  - Record debugging lesson: raw topology invariants are not enough for Pack cases; final retained equation and recovery profile must be treated as two layers.
  - Do not commit if user wants this private note excluded.

---

## Core Semantics

### Existing Dummy / N-Dummy

Existing DummyBranch / N-Dummy is for cases where the raw full-node set must be padded so that multi-case templates can compare matrices. It is intentionally constrained: dummy nodes do not represent physical recovery nodes, and old N-Dummy isolated nodes are not recovered.

### New Internal Recovery Mismatch Path

This feature is for a different situation:

- Case A eliminates internal nodes `[inner]`.
- Case B eliminates internal nodes `[inner, inner2]`.
- Final external ports are identical: `[N1, N2, N3, N4]`.
- `Gred` and `Ihisred` have the same dimensions in every case.
- Recovery formulas differ by case.

Correct handling:

1. Build each case's own reduction normally.
2. Store the final retained equation for that case.
3. Store that case's recovery rows.
4. Multi-case C export uses the final retained equations as the shared alias-template source.
5. T1_T2 uses a case switch to recover only that case's actual internal nodes.

### Internal Placeholder Policy

V1 should not require users to manually add a dummy internal node just to make Gkk sizes match. The backend should use final retained equations directly.

If a future UI needs visible placeholders, introduce a separate tag such as:

```json
{
  "dummy_role": "recovery_dummy_internal",
  "recoverable": false,
  "forced_elimination": true
}
```

Rules for this tag:

- Valid only inside Pack internal case editing.
- May appear as internal in the canvas for alignment.
- Must be pruned before Gkk inversion.
- Must not enter final recovery rows.
- Must not reuse old `isolated_dummy_internal` validation rules that require the dummy to exist in every case.

---

## Task 1: Create Git Checkpoint

**Files:**
- No source change expected.

- [ ] **Step 1: Check current state**

Run:

```powershell
git status --short
```

Expected:

- If clean, continue.
- If dirty, inspect changes and do not overwrite user work.

- [ ] **Step 2: Commit current stable state if dirty changes are ours**

Run only after reviewing changed files:

```powershell
git add optimized_elimination_api.py index.html tests
git commit -m "chore: checkpoint before pack internal recovery work"
```

Expected:

- Commit succeeds, or no commit is needed because the tree is clean.

---

## Task 2: Add Backend Test For Final Retained Adapter

**Files:**
- Modify: `E:/network_node/tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Add minimal profile builder**

Add this helper near existing multi-case helper tests:

```python
def _pack_case_profile(
    case_id,
    raw_nodes,
    raw_internal_nodes,
    final_ports,
    final_internal_nodes,
    final_g,
    final_ihis,
    final_k_v,
    final_k_h,
):
    return {
        "case_id": case_id,
        "case_map": {"YBox1": case_id},
        "payload": {
            "all_nodes": list(raw_nodes),
            "external_nodes": [node for node in raw_nodes if node not in raw_internal_nodes],
            "internal_nodes": list(raw_internal_nodes),
            "ground_nodes": [],
            "G_full": [[0 for _ in raw_nodes] for _ in raw_nodes],
            "Ihis_full": [[0] for _ in raw_nodes],
            "finalExternalGroups": [{"display": node, "node_id": node} for node in final_ports],
            "finalInternalGroups": [{"display": node, "node_id": node} for node in final_internal_nodes],
            "finalGMatrix": final_g,
            "finalIhisVector": final_ihis,
            "finalK_v": final_k_v,
            "finalK_h": final_k_h,
        },
    }
```

- [ ] **Step 2: Add failing test for different internal counts**

Add:

```python
def test_final_retained_adapter_allows_different_raw_internal_counts(self):
    from optimized_elimination_api import _final_retained_profile_adapter

    profiles = [
        _pack_case_profile(
            0,
            raw_nodes=["N1", "N2", "inner"],
            raw_internal_nodes=["inner"],
            final_ports=["N1", "N2"],
            final_internal_nodes=["inner"],
            final_g=[["G11", "-G12"], ["-G12", "G22"]],
            final_ihis=[["Ihis1"], ["Ihis2"]],
            final_k_v=[["Kv11", "Kv12"]],
            final_k_h=[["Kh1"]],
        ),
        _pack_case_profile(
            1,
            raw_nodes=["N1", "N2", "inner", "inner2"],
            raw_internal_nodes=["inner", "inner2"],
            final_ports=["N1", "N2"],
            final_internal_nodes=["inner", "inner2"],
            final_g=[["G11b", "-G12b"], ["-G12b", "G22b"]],
            final_ihis=[["Ihis1b"], ["Ihis2b"]],
            final_k_v=[["Kv11b", "Kv12b"], ["Kv21b", "Kv22b"]],
            final_k_h=[["Kh1b"], ["Kh2b"]],
        ),
    ]

    adapted = _final_retained_profile_adapter(profiles)

    self.assertIsNotNone(adapted)
    normalized, recovery = adapted
    self.assertEqual(normalized[0]["payload"]["all_nodes"], ["N1", "N2"])
    self.assertEqual(normalized[1]["payload"]["all_nodes"], ["N1", "N2"])
    self.assertEqual(normalized[0]["payload"]["internal_nodes"], [])
    self.assertEqual(normalized[1]["payload"]["internal_nodes"], [])
    self.assertEqual(recovery[0]["recovery_nodes"], ["inner"])
    self.assertEqual(recovery[1]["recovery_nodes"], ["inner", "inner2"])
```

- [ ] **Step 3: Run test and verify failure or pass**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py::MultiCaseCExportAliasTemplateTests::test_final_retained_adapter_allows_different_raw_internal_counts -q
```

Expected before implementation:

- If it fails, failure should explain the adapter gap.
- If it already passes, keep it as regression coverage and continue to end-to-end test.

---

## Task 3: Add Backend Test For Multi-Case Export With Final Recovery

**Files:**
- Modify: `E:/network_node/tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Add test using `_build_multicase_alias_template_payload`**

Add:

```python
def test_alias_template_uses_final_retained_equation_when_raw_topology_differs(self):
    from optimized_elimination_api import _build_multicase_alias_template_payload

    payload = {
        "case_profiles": [
            _pack_case_profile(
                0,
                raw_nodes=["N1", "N2", "inner"],
                raw_internal_nodes=["inner"],
                final_ports=["N1", "N2"],
                final_internal_nodes=["inner"],
                final_g=[["G11", "-G12"], ["-G12", "G22"]],
                final_ihis=[["Ihis1"], ["Ihis2"]],
                final_k_v=[["Kv11", "Kv12"]],
                final_k_h=[["Kh1"]],
            ),
            _pack_case_profile(
                1,
                raw_nodes=["N1", "N2", "inner", "inner2"],
                raw_internal_nodes=["inner", "inner2"],
                final_ports=["N1", "N2"],
                final_internal_nodes=["inner", "inner2"],
                final_g=[["G11b", "-G12b"], ["-G12b", "G22b"]],
                final_ihis=[["Ihis1b"], ["Ihis2b"]],
                final_k_v=[["Kv11b", "Kv12b"], ["Kv21b", "Kv22b"]],
                final_k_h=[["Kh1b"], ["Kh2b"]],
            ),
        ],
        "runtime_case_groups": [],
    }

    model = _build_multicase_alias_template_payload(payload)

    self.assertIsNotNone(model)
    self.assertEqual(len(model["final_recovery_profiles"]), 2)
    self.assertEqual(model["profiles"][0]["payload"]["all_nodes"], ["N1", "N2"])
    self.assertEqual(model["profiles"][1]["payload"]["all_nodes"], ["N1", "N2"])
```

- [ ] **Step 2: Add runtime mutable rejection test**

Add:

```python
def test_runtime_mutable_topology_change_is_rejected_even_with_final_fields(self):
    from optimized_elimination_api import _build_multicase_alias_template_payload

    profiles = [
        _pack_case_profile(
            0,
            raw_nodes=["N1", "N2", "inner"],
            raw_internal_nodes=["inner"],
            final_ports=["N1", "N2"],
            final_internal_nodes=["inner"],
            final_g=[["G11", "-G12"], ["-G12", "G22"]],
            final_ihis=[["Ihis1"], ["Ihis2"]],
            final_k_v=[["Kv11", "Kv12"]],
            final_k_h=[["Kh1"]],
        ),
        _pack_case_profile(
            1,
            raw_nodes=["N1", "N2", "inner", "inner2"],
            raw_internal_nodes=["inner", "inner2"],
            final_ports=["N1", "N2"],
            final_internal_nodes=["inner", "inner2"],
            final_g=[["G11b", "-G12b"], ["-G12b", "G22b"]],
            final_ihis=[["Ihis1b"], ["Ihis2b"]],
            final_k_v=[["Kv11b", "Kv12b"], ["Kv21b", "Kv22b"]],
            final_k_h=[["Kh1b"], ["Kh2b"]],
        ),
    ]
    profiles[1]["_runtime_branch_id"] = "YBox1"

    payload = {
        "case_profiles": profiles,
        "runtime_case_groups": [{"branch_id": "YBox1", "runtime_case_id_symbol": "runtime_YBox1_case_id"}],
    }

    with self.assertRaisesRegex(ValueError, "Runtime-mutable multi-case group changes topology"):
        _build_multicase_alias_template_payload(payload)
```

- [ ] **Step 3: Run tests**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -q
```

Expected:

- New adapter tests pass.
- Existing alias-template tests still pass.

---

## Task 4: Harden Backend Adapter Diagnostics

**Files:**
- Modify: `E:/network_node/optimized_elimination_api.py`

- [ ] **Step 1: Replace silent `None` causes with local reason tracking**

In `_final_retained_profile_adapter`, keep return type unchanged for callers, but add a private helper:

```python
def _final_retained_profile_adapter_reason(profiles: list[dict]) -> tuple[tuple[list[dict], list[dict]] | None, str | None]:
    ...
```

Rules:

- Return `(adapted, None)` on success.
- Return `(None, "missing finalExternalGroups/finalGMatrix/finalIhisVector on profile 2")` on failure.
- Use existing `_final_retained_profile_adapter` as a wrapper:

```python
def _final_retained_profile_adapter(profiles: list[dict]) -> tuple[list[dict], list[dict]] | None:
    adapted, _reason = _final_retained_profile_adapter_reason(profiles)
    return adapted
```

- [ ] **Step 2: Use reason in topology error path**

In `_build_multicase_alias_template_payload`, change:

```python
adapted = _final_retained_profile_adapter(sample_profiles)
if adapted is None:
    raise
```

to:

```python
adapted, adapter_reason = _final_retained_profile_adapter_reason(sample_profiles)
if adapted is None:
    if adapter_reason:
        raise ValueError(f"{exc}; final retained adapter unavailable: {adapter_reason}") from exc
    raise
```

- [ ] **Step 3: Confirm runtime mutable still rejects before adapter**

Keep:

```python
if runtime_groups and runtime_sample:
    raise ValueError(...)
```

before the adapter attempt. This protects runtime-mutable C code from changing topology/matrix dimensions during CODE.

- [ ] **Step 4: Run backend tests**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py tests/test_multicase_dummy_node_block.py -q
```

Expected:

- Pass.
- No old DummyNodeBlock behavior changes.

---

## Task 5: Preserve Final Fields In Frontend Pack Case Save

**Files:**
- Modify: `E:/network_node/index.html`
- Modify: `E:/network_node/tests/test_frontend_optimized_reuse.py`

- [ ] **Step 1: Add source test for final retained fields**

Add to `tests/test_frontend_optimized_reuse.py`:

```python
def test_pack_case_save_preserves_final_retained_fields(self):
    source = Path("index.html").read_text(encoding="utf-8")
    self.assertIn("finalExternalGroups", source)
    self.assertIn("finalGMatrix", source)
    self.assertIn("finalIhisVector", source)
    self.assertIn("finalInternalGroups", source)
    self.assertIn("finalK_v", source)
    self.assertIn("finalK_h", source)
```

- [ ] **Step 2: Ensure `packagedCaseFromReduction` stores final fields**

In `packagedCaseFromReduction(...)`, keep or add:

```javascript
const finalExternalGroups = packagedCaseExternalRows(finalSubsystem.externalGroups);
const finalInternalGroups = packagedCaseInternalRows(finalSubsystem.internalGroups);
return {
  ...
  finalExternalGroups,
  finalInternalGroups,
  finalGMatrix: result.G_red,
  finalIhisVector: result.Ihis_red,
  finalK_v: result.K_v,
  finalK_h: result.K_h,
};
```

If actual result keys differ, use the existing keys that the current function already uses for `G_red`, `Ihis_red`, `K_v`, `K_h`.

- [ ] **Step 3: Ensure `packageNetworkCaseFromBranch` copies final fields**

Keep or add:

```javascript
finalExternalGroups: structuredClone(pkg.finalExternalGroups || pkg.externalGroups || []),
finalInternalGroups: structuredClone(pkg.finalInternalGroups || pkg.internalGroups || []),
finalGMatrix: structuredClone(pkg.finalGMatrix || pkg.G_local || []),
finalIhisVector: structuredClone(pkg.finalIhisVector || pkg.Ihis_local || []),
finalK_v: structuredClone(pkg.finalK_v || pkg.K_v || []),
finalK_h: structuredClone(pkg.finalK_h || pkg.K_h || []),
```

- [ ] **Step 4: Ensure active case apply restores final fields**

In `applyPackageNetworkCase(...)`, keep or add:

```javascript
pkg.finalExternalGroups = structuredClone(active.finalExternalGroups || active.externalGroups || []);
pkg.finalInternalGroups = structuredClone(active.finalInternalGroups || active.internalGroups || []);
pkg.finalGMatrix = structuredClone(active.finalGMatrix || active.G_local || []);
pkg.finalIhisVector = structuredClone(active.finalIhisVector || active.Ihis_local || []);
pkg.finalK_v = structuredClone(active.finalK_v || active.K_v || []);
pkg.finalK_h = structuredClone(active.finalK_h || active.K_h || []);
```

- [ ] **Step 5: Run frontend static tests**

Run:

```powershell
python -m pytest tests/test_frontend_optimized_reuse.py -q
```

Expected:

- Pass.

---

## Task 6: End-To-End Fixture Test For `Trf_Ctest_dummy.json`

**Files:**
- Modify or add: `E:/network_node/tests/test_multicase_c_export_alias_template.py`
- Optional read fixture: `E:/network_node/exports/Trf_Ctest_dummy.json`

- [ ] **Step 1: Add fixture existence guard**

Add test:

```python
def test_trf_ctest_dummy_fixture_has_final_fields_if_present(self):
    fixture = Path("exports/Trf_Ctest_dummy.json")
    if not fixture.exists():
        self.skipTest("Trf_Ctest_dummy.json fixture is not present")
    data = json.loads(fixture.read_text(encoding="utf-8"))
    text = json.dumps(data, ensure_ascii=False)
    self.assertIn("finalExternalGroups", text)
    self.assertIn("finalGMatrix", text)
    self.assertIn("finalIhisVector", text)
    self.assertIn("finalK_v", text)
```

- [ ] **Step 2: Add actual export regression if helper exists**

If tests already include a helper that converts app JSON into a multi-case export payload, use that helper. The assertion must be:

```python
self.assertNotIn("multi-case topology invariant failed", result_or_error)
self.assertIn("final retained", draft_or_summary.lower())
self.assertIn("T1_T2", draft)
```

Do not invent a weak test that only checks for function names. The test must exercise the same export path the browser uses.

- [ ] **Step 3: Manual browser verification**

Run project, import `exports/Trf_Ctest_dummy.json`, then click `Multi-Case C Export`.

Expected:

- No error `multi-case topology invariant failed for profile 1: all_nodes_changed`.
- C draft is generated.
- Final retained G/Ihis is common-dimensional.
- T1_T2 has case-specific recovery rows.

---

## Task 7: Recovery Code Generation Safety

**Files:**
- Modify: `E:/network_node/optimized_elimination_api.py`
- Modify: `E:/network_node/tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Confirm `_apply_final_retained_recovery_profiles_to_draft` supports different row counts**

Inspect `_apply_final_retained_recovery_profiles_to_draft(...)`. It must emit case-specific recovery like:

```c
switch (case_id) {
case 0:
    /* recover inner */
    ...
    break;
case 1:
    /* recover inner and inner2 */
    ...
    break;
}
```

- [ ] **Step 2: Add test asserting different recovery row counts**

Add:

```python
def test_final_recovery_profiles_keep_case_specific_recovery_rows(self):
    from optimized_elimination_api import _apply_final_retained_recovery_profiles_to_draft

    draft = "T1_T2:\n    /* No internal nodes were eliminated, so there is no Vk recovery step. */\n"
    recovery_profiles = [
        {
            "case_ids": [0],
            "recovery_nodes": ["inner"],
            "super_nodes": ["N1", "N2"],
            "K_v": [["Kv11", "Kv12"]],
            "K_h": [["Kh1"]],
        },
        {
            "case_ids": [1],
            "recovery_nodes": ["inner", "inner2"],
            "super_nodes": ["N1", "N2"],
            "K_v": [["Kv11b", "Kv12b"], ["Kv21b", "Kv22b"]],
            "K_h": [["Kh1b"], ["Kh2b"]],
        },
    ]

    out = _apply_final_retained_recovery_profiles_to_draft(draft, recovery_profiles=recovery_profiles)

    self.assertIn("case 0", out)
    self.assertIn("case 1", out)
    self.assertIn("inner", out)
    self.assertIn("inner2", out)
```

Adjust the function call to match the actual signature. If it needs extra arguments, pass the minimal safe defaults used by existing tests.

- [ ] **Step 3: Run recovery tests**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -q
```

Expected:

- Pass.
- Recovery code differs per case when recovery row count differs.

---

## Task 8: UI Validation Text

**Files:**
- Modify: `E:/network_node/index.html`

- [ ] **Step 1: Add user-facing validation copy**

When saving Pack case editing, if final external ports match but internal nodes differ, show success/help text equivalent to:

Chinese:

```text
该 Pack 工况的外部端口与其它工况一致；内部消元节点可不同。多Case C导出会使用每个工况的最终 retained 方程，并按工况恢复对应内部节点电压。
```

English:

```text
This Pack case keeps the same external ports as the other cases. Internal eliminated nodes may differ; Multi-Case C Export will use each case's final retained equation and recover that case's own internal voltages.
```

- [ ] **Step 2: Keep hard failure when final external ports differ**

If final external ports differ, keep the existing save/export error:

```text
External port count, names, and order must stay identical across Pack cases.
```

- [ ] **Step 3: Run translation scan**

Run:

```powershell
rg -n "[\u4e00-\u9fff]" README.md index.html nodal_tool optimized_elimination_api.py tests
```

Expected:

- Chinese text in user-facing strings must have an English counterpart.
- English UI mode must not display Chinese-only warnings.

---

## Task 9: Regression Matrix

**Files:**
- No source change expected unless tests fail.

- [ ] **Step 1: Run focused backend tests**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py tests/test_multicase_dummy_node_block.py tests/test_multicase_common_dummy_internal_codegen.py -q
```

Expected:

- Pass.

- [ ] **Step 2: Run optimized elimination regression**

Run:

```powershell
python -m pytest tests/test_optimized_elimination.py tests/test_frontend_optimized_reuse.py -q
```

Expected:

- Pass.

- [ ] **Step 3: Run frontend formatter regression**

Run:

```powershell
node tests/frontend_math_formatter_cases.mjs
```

Expected:

- Pass.

- [ ] **Step 4: Manual cases**

In browser, verify:

- `Trf_Ctest_dummy.json`: Multi-Case C Export succeeds.
- `Trf_Ctest.json`: existing source-level temp reuse remains.
- `dummy.json`: no undefined `PACK_CASE_0` / retained enum regression.
- `Trf_RCY_UCM.json`: no slowdown, no dummy regression, no topology error.

---

## Task 10: Documentation / Notes

**Files:**
- Modify: `E:/network_node/PROJECT_NOTES.md`

- [ ] **Step 1: Add debugging note**

Add:

```markdown
### Pack Multi-Case: Raw Topology vs Final Retained Topology

When Pack cases have the same external ports but different internal eliminated nodes, raw `G_full/Ihis_full` topology is allowed to differ only after each case has been reduced to the same final retained equation. Multi-Case C Export must run alias-template logic on `finalGMatrix/finalIhisVector`, not raw `G_full`, and must carry per-case `finalK_v/finalK_h` recovery profiles into T1_T2.

Do not solve this by reusing old DummyBranch semantics. Old dummy nodes are finalization placeholders and are not recovered. If a visible internal placeholder is ever needed for editing, tag it separately as `recovery_dummy_internal` and prune it before Gkk inversion.

Debug checklist:
- First check whether each packaged case stores `finalExternalGroups/finalGMatrix/finalIhisVector`.
- Then check whether `finalExternalGroups` names/order match.
- Then check recovery matrix shapes: `finalK_v` rows must equal `finalInternalGroups`, columns must equal final retained ports.
- Runtime-mutable case groups must still reject topology changes.
```

- [ ] **Step 2: Do not commit private note unless requested**

Run:

```powershell
git status --short
```

If `PROJECT_NOTES.md` is changed, keep it unstaged unless user explicitly asks to commit private notes.

---

## Safety Boundaries

- Do not change old DummyBranch / N-Dummy semantics.
- Do not allow runtime-mutable case groups to change topology, retained dimensions, or recovery row count.
- Do not pad Gkk with zero rows and invert it. Placeholder rows must be pruned before inversion.
- Do not use `sp.cancel`, `sp.factor`, or broad `sp.simplify` to decide topology compatibility.
- Do not discard recovery formulas just because final retained equations match.
- Do not special-case `Trf_Ctest_dummy.json`; tests must express the general condition.

---

## Self-Review

- Spec coverage: The plan covers same external ports, different internal eliminated nodes, recovery preservation, dummy placeholder semantics, frontend final-field preservation, backend adapter behavior, runtime-mutable safety, and real fixture verification.
- Placeholder scan: No task uses TBD/TODO-style placeholders; any optional fixture path is guarded by explicit skip logic.
- Type consistency: The plan consistently uses `finalExternalGroups`, `finalGMatrix`, `finalIhisVector`, `finalInternalGroups`, `finalK_v`, `finalK_h`, and `recovery_dummy_internal`.

