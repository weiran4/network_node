# Pack Case Gkk Dummy Placeholder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support Pack multi-case networks where all cases expose the same final external ports, but each case has a different set of eliminated internal nodes and therefore a different voltage-recovery profile.

**Architecture:** Keep the existing Pack/Dummy/N-Dummy model intact, but add a backend-only "internal elimination placeholder" layer. The placeholder aligns per-case internal-node axes for template generation, then is pruned before Gkk inversion so dummy rows never enter Schur math.

**Tech Stack:** Python backend (`optimized_elimination_api.py`, `nodal_tool/*`), frontend state serialization (`index.html`), pytest, existing JSON fixtures under `exports/`.

---

## Problem Statement

The existing Pack multi-case export has two mature paths:

- **Same raw topology:** every case has the same `G_full`, retained nodes, internal nodes, and observer/recovery layout. This can use the alias-template DAG directly.
- **Dummy/N-Dummy finalization:** cases can have different full node sets if dummy placeholders make final retained ports comparable. Dummy rows are removed before final C export.

The new case is different:

- Final external ports are identical across all cases.
- Raw internal nodes differ across cases.
- We still need per-case `Vk` recovery for real internal nodes.
- Using only `Gred/Ihisred` loses recovery information.
- Forcing raw `G_full/Gkk` dimensions to match without pruning dummy rows creates singular Gkk blocks.

The intended design is:

1. Build a union of final retained ports and union of possible internal recovery nodes.
2. Mark missing internal nodes in each case as **Gkk dummy placeholders**.
3. Before Schur inversion, prune placeholder rows/columns from that case's Gkk/Grk/Gkr/Ihisk.
4. Run the normal case-local Schur/recovery math on real internal nodes only.
5. Reassemble the final retained equation on the shared final port order.
6. Emit per-case recovery code only for real internal nodes present in that case.

---

## File Structure

**Backend**

- Modify: `optimized_elimination_api.py`
  - Add adapter diagnostics for "final ports same, internal recovery profile differs".
  - Add internal placeholder metadata to sample profiles.
  - Ensure alias-template export sees a shared final retained layout.

- Modify: `nodal_tool/optimized_elimination.py`
  - If the lower-level Schur helpers are used for profile-local recovery, keep dummy pruning isolated here or expose a helper that accepts a real-internal mask.

- Test: `tests/test_multicase_c_export_alias_template.py`
  - Add backend tests for profile-local internal pruning and final retained compatibility.

- Test: `tests/test_multicase_dummy_node_block.py`
  - Confirm old Dummy/N-Dummy behavior is unchanged.

**Frontend**

- Modify: `index.html`
  - Preserve final retained fields and internal recovery fields when saving Pack case edits.
  - Allow Pack save when final external ports match but raw internal node sets differ.
  - Continue rejecting mismatched final external ports.

- Test: `tests/test_frontend_optimized_reuse.py`
  - Add serialization/validation cases for Pack cases with different internal node sets.

**Fixtures**

- Use: `exports/Trf_Ctest_dummy.json`
  - Real user fixture for final verification.

- Optional create: `exports/test_pack_internal_recovery_profiles.json`
  - Small minimized fixture if `Trf_Ctest_dummy.json` is too large for unit tests.

**Notes**

- Modify: `PROJECT_NOTES.md`
  - Private debugging notes only. Do not commit unless explicitly requested.

---

## Design Rules

### Rule 1: Dummy Placeholder Has Two Meanings

Existing UI dummy nodes stay unchanged:

- Dummy branch/N-Dummy for case topology alignment.
- These are visible to the user and have rules around connection, internal/external marking, and C export pruning.

New backend placeholder:

- Internal-only "missing recovery node" placeholder.
- Used only to align per-case recovery metadata.
- Never shown as a normal branch.
- Never inverted.
- Never enters `setupGMatrix`.
- Never creates `G_EPSILON`.

### Rule 2: Final Retained Ports Are the Compatibility Contract

Pack cases are compatible if:

- Final external node count is identical.
- Final external node names/order are identical after Pack port mapping.
- Final `Gred` shape is identical.
- Final `Ihisred` shape is identical.

Raw `all_nodes` and raw `internal_nodes` may differ only when final retained compatibility passes.

### Rule 3: Runtime-Mutable Case Groups Stay Conservative

If a Pack case group is marked "case id can change during CODE":

- Raw topology must remain fixed.
- Internal node set must remain fixed.
- Matrix dimensions must remain fixed.
- Do not allow the new varying-internal-node adapter.

Reason: runtime switching cannot reallocate RTDS matrix shapes or recovery vector layouts.

### Rule 4: Init-Time Pack Case Groups May Use Recovery Profiles

If case id is fixed before simulation:

- Each case can select its own recovery profile.
- `RAM_PASS1` can choose retained dimensions and setup layout.
- `CODE`/`T1_T2` can use switch branches for profile-specific recovery.

### Rule 5: Placeholder Rows Are Pruned Before Schur

For each case:

```text
real_internal_nodes = case.internal_nodes
placeholder_internal_nodes = union_internal_nodes - real_internal_nodes

Gkk_case = Gkk_union[real_internal_nodes, real_internal_nodes]
Grk_case = Grk_union[retained_nodes, real_internal_nodes]
Gkr_case = Gkr_union[real_internal_nodes, retained_nodes]
Ihisk_case = Ihisk_union[real_internal_nodes]
```

Then:

```text
W_case = inv(Gkk_case)
Gred_case = Grr_case - Grk_case * W_case * Gkr_case
Ihisred_case = Ihisr_case - Grk_case * W_case * Ihisk_case
Vk_case = -W_case * Gkr_case * Vr - W_case * Ihisk_case
```

If `real_internal_nodes` is empty:

```text
Gred_case = Grr_case
Ihisred_case = Ihisr_case
Vk_case is omitted for that case
```

---

## Task 1: Create a Git Checkpoint

**Files:**
- No source edits.

- [ ] **Step 1: Check status**

Run:

```powershell
git status --short
```

Expected:

- Existing uncommitted changes are understood.
- `PROJECT_NOTES.md` is not staged.

- [ ] **Step 2: Commit current stable state if needed**

Run:

```powershell
git add optimized_elimination_api.py index.html tests docs exports
git commit -m "chore: checkpoint before pack recovery profile work"
```

Expected:

- Commit succeeds, or git reports nothing to commit.
- Private notes are not included.

---

## Task 2: Add Backend Adapter Tests

**Files:**
- Modify: `tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Add a minimal profile builder**

Add this helper near existing multicase helper functions:

```python
def _final_recovery_profile(
    *,
    case_id,
    final_external,
    raw_all,
    raw_external,
    raw_internal,
    g_full,
    ihis_full,
    final_g,
    final_ihis,
    final_internal=None,
    k_v=None,
    k_h=None,
):
    return {
        "case_ids": [case_id],
        "all_nodes": list(raw_all),
        "external_nodes": list(raw_external),
        "internal_nodes": list(raw_internal),
        "ground_nodes": [],
        "G_full": g_full,
        "Ihis_full": ihis_full,
        "finalExternalGroups": [{"name": name, "nodes": [name]} for name in final_external],
        "finalGMatrix": final_g,
        "finalIhisVector": final_ihis,
        "finalInternalGroups": [{"name": name, "nodes": [name]} for name in (final_internal or [])],
        "finalK_v": k_v or [],
        "finalK_h": k_h or [],
    }
```

- [ ] **Step 2: Add adapter acceptance test**

Add:

```python
def test_final_retained_adapter_allows_different_internal_recovery_profiles():
    from optimized_elimination_api import _final_retained_profile_adapter

    profiles = [
        _final_recovery_profile(
            case_id=0,
            final_external=["P1", "P2"],
            raw_all=["P1", "P2", "inner_a"],
            raw_external=["P1", "P2"],
            raw_internal=["inner_a"],
            g_full=[["G1", "-G1", "0"], ["-G1", "G1+Gi", "-Gi"], ["0", "-Gi", "Gi"]],
            ihis_full=[["0"], ["0"], ["0"]],
            final_g=[["Gred11_a", "Gred12_a"], ["Gred12_a", "Gred22_a"]],
            final_ihis=[["Ired1_a"], ["Ired2_a"]],
            final_internal=["inner_a"],
            k_v=[["Kv_a_1", "Kv_a_2"]],
            k_h=[["Kh_a"]],
        ),
        _final_recovery_profile(
            case_id=1,
            final_external=["P1", "P2"],
            raw_all=["P1", "P2", "inner_b", "inner_c"],
            raw_external=["P1", "P2"],
            raw_internal=["inner_b", "inner_c"],
            g_full=[["G2", "-G2", "0", "0"], ["-G2", "G2+Gb", "-Gb", "0"], ["0", "-Gb", "Gb+Gc", "-Gc"], ["0", "0", "-Gc", "Gc"]],
            ihis_full=[["0"], ["0"], ["0"], ["0"]],
            final_g=[["Gred11_b", "Gred12_b"], ["Gred12_b", "Gred22_b"]],
            final_ihis=[["Ired1_b"], ["Ired2_b"]],
            final_internal=["inner_b", "inner_c"],
            k_v=[["Kv_b1_1", "Kv_b1_2"], ["Kv_b2_1", "Kv_b2_2"]],
            k_h=[["Kh_b1"], ["Kh_b2"]],
        ),
    ]

    adapted = _final_retained_profile_adapter(profiles)

    assert adapted is not None
    adapted_profiles, recovery_profiles = adapted
    assert [p["external_nodes"] for p in adapted_profiles] == [["P1", "P2"], ["P1", "P2"]]
    assert [p["internal_nodes"] for p in adapted_profiles] == [[], []]
    assert adapted_profiles[0]["G_full"] == profiles[0]["finalGMatrix"]
    assert adapted_profiles[1]["Ihis_full"] == profiles[1]["finalIhisVector"]
    assert recovery_profiles[0]["recovery_nodes"] == ["inner_a"]
    assert recovery_profiles[1]["recovery_nodes"] == ["inner_b", "inner_c"]
```

- [ ] **Step 3: Run the new test**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -k final_retained_adapter -q
```

Expected:

- New test passes if adapter exists.
- If it fails, failure explains missing final fields or adapter shape checks.

---

## Task 3: Improve Adapter Diagnostics

**Files:**
- Modify: `optimized_elimination_api.py`
- Test: `tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Add reason-returning adapter helper**

Add beside `_final_retained_profile_adapter`:

```python
def _final_retained_profile_adapter_reason(profiles: list[dict]) -> tuple[list[dict] | None, list[dict], str | None]:
    adapted = _final_retained_profile_adapter(profiles)
    if adapted is not None:
        adapted_profiles, recovery_profiles = adapted
        return adapted_profiles, recovery_profiles, None

    missing = []
    for idx, profile in enumerate(profiles):
        payload = profile.get("payload") if isinstance(profile.get("payload"), dict) else profile
        for key in ("finalExternalGroups", "finalGMatrix", "finalIhisVector"):
            if not payload.get(key):
                missing.append(f"profile {idx}: missing {key}")
    if missing:
        return None, [], "; ".join(missing)
    return None, [], "final retained fields are present but shapes or port order do not match"
```

- [ ] **Step 2: Use diagnostics in alias-template path**

In the topology mismatch handler, replace the bare adapter call with:

```python
adapted_profiles, final_recovery_profiles, adapter_reason = _final_retained_profile_adapter_reason(sample_profiles)
if adapted_profiles is None:
    raise ValueError(
        f"{exc}; final-retained recovery adapter could not apply: {adapter_reason}"
    ) from exc
sample_profiles = adapted_profiles
```

- [ ] **Step 3: Add regression test for useful error**

Add:

```python
def test_final_retained_adapter_error_mentions_missing_final_fields():
    from optimized_elimination_api import _build_multicase_alias_template_payload

    payload = {
        "multi_case": True,
        "case_profiles": [
            {
                "case_ids": [0],
                "all_nodes": ["A", "B"],
                "external_nodes": ["A"],
                "internal_nodes": ["B"],
                "ground_nodes": [],
                "G_full": [["1", "-1"], ["-1", "1"]],
                "Ihis_full": [["0"], ["0"]],
            },
            {
                "case_ids": [1],
                "all_nodes": ["A", "B", "C"],
                "external_nodes": ["A"],
                "internal_nodes": ["B", "C"],
                "ground_nodes": [],
                "G_full": [["1", "-1", "0"], ["-1", "2", "-1"], ["0", "-1", "1"]],
                "Ihis_full": [["0"], ["0"], ["0"]],
            },
        ],
    }

    with pytest.raises(ValueError) as excinfo:
        _build_multicase_alias_template_payload(payload)
    assert "final-retained recovery adapter could not apply" in str(excinfo.value)
    assert "missing finalExternalGroups" in str(excinfo.value)
```

- [ ] **Step 4: Run diagnostic test**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -k final_retained_adapter -q
```

Expected:

- Both adapter tests pass.

---

## Task 4: Preserve Final Retained Fields In Frontend Pack Save

**Files:**
- Modify: `index.html`
- Test: `tests/test_frontend_optimized_reuse.py`

- [ ] **Step 1: Locate the Pack case serialization**

Inspect these functions:

```text
packageNetworkCaseFromBranch
applyPackageNetworkCase
packagedCaseFromReduction
finishPackageCaseEdit
buildPackagedReductionPayload
```

Expected:

- Final fields are copied whenever a Pack case is saved or loaded:
  - `finalExternalGroups`
  - `finalInternalGroups`
  - `finalGMatrix`
  - `finalIhisVector`
  - `finalK_v`
  - `finalK_h`

- [ ] **Step 2: Add a reusable final-field copier**

Add near existing payload copy helpers:

```javascript
function copyFinalRetainedFields(target, source) {
  if (!target || !source) return target;
  const keys = [
    'finalExternalGroups',
    'finalInternalGroups',
    'finalGMatrix',
    'finalIhisVector',
    'finalK_v',
    'finalK_h',
  ];
  keys.forEach((key) => {
    if (source[key] !== undefined) target[key] = cloneData(source[key]);
  });
  return target;
}
```

- [ ] **Step 3: Use copier in Pack case save/load paths**

Apply it where Pack case objects are created:

```javascript
copyFinalRetainedFields(caseData, result || finalData || subsystem);
```

Use the concrete variable names from each function. Do not overwrite final fields with `undefined`.

- [ ] **Step 4: Add frontend test**

In `tests/test_frontend_optimized_reuse.py`, add a test that simulates a saved Pack case with:

```javascript
{
  finalExternalGroups: [{ name: 'N1' }, { name: 'N2' }],
  finalInternalGroups: [{ name: 'inner_left' }],
  finalGMatrix: [['G11', 'G12'], ['G12', 'G22']],
  finalIhisVector: [['I1'], ['I2']],
  finalK_v: [['Kv11', 'Kv12']],
  finalK_h: [['Kh1']],
}
```

Assert the saved case still contains all six keys.

- [ ] **Step 5: Run frontend test**

Run:

```powershell
python -m pytest tests/test_frontend_optimized_reuse.py -q
```

Expected:

- Test passes.

---

## Task 5: Relax Pack Edit Validation For Final-Port-Compatible Cases

**Files:**
- Modify: `index.html`
- Test: `tests/test_frontend_optimized_reuse.py`

- [ ] **Step 1: Find save validation**

Search:

```powershell
rg -n "external port|外部|internal|finalExternalGroups|finishPackageCaseEdit" index.html
```

- [ ] **Step 2: Change validation rule**

Current strict rule likely compares raw node counts/order. Replace with:

```javascript
function packCasesHaveSameFinalExternalPorts(baseCase, nextCase) {
  const baseFinal = (baseCase.finalExternalGroups || []).map((g) => g.name || g.id);
  const nextFinal = (nextCase.finalExternalGroups || []).map((g) => g.name || g.id);
  if (!baseFinal.length || !nextFinal.length) return false;
  return baseFinal.length === nextFinal.length && baseFinal.every((name, idx) => name === nextFinal[idx]);
}
```

Save may proceed when:

```javascript
rawExternalPortsMatch || packCasesHaveSameFinalExternalPorts(baseCase, nextCase)
```

Still reject:

- Final external ports differ.
- Runtime-mutable group changes raw topology.
- Missing final retained fields.

- [ ] **Step 3: Add UI message**

When internal recovery profiles differ but final ports match, show:

```text
Pack cases share the same final external ports. Internal recovery nodes differ by case, so C export will use per-case recovery profiles.
```

Chinese:

```text
这些 Pack 工况的最终外部端口一致，但内部恢复节点按工况不同；C 导出会使用每个工况自己的电压恢复 profile。
```

- [ ] **Step 4: Add validation tests**

Add tests:

```python
def test_pack_save_allows_same_final_ports_with_different_internal_recovery():
    # Use existing frontend helper harness.
    # Base case finalExternalGroups = N1,N2,N3,N4 and finalInternalGroups = inner_left.
    # Next case finalExternalGroups = N1,N2,N3,N4 and finalInternalGroups = inner_right.
    # Assert validation returns ok.
```

```python
def test_pack_save_rejects_different_final_external_ports():
    # Base finalExternalGroups = N1,N2,N3,N4.
    # Next finalExternalGroups = N1,N2,N3.
    # Assert validation rejects.
```

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_frontend_optimized_reuse.py -q
```

Expected:

- New validation tests pass.

---

## Task 6: Generate Recovery Profiles In C Export

**Files:**
- Modify: `optimized_elimination_api.py`
- Test: `tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Inspect existing recovery emitter**

Search:

```powershell
rg -n "_apply_final_retained_recovery_profiles_to_draft|recovery_profiles|finalK_v|finalK_h" optimized_elimination_api.py
```

- [ ] **Step 2: Ensure adapted profiles call recovery emitter**

In `_build_multicase_alias_template_payload`, after the adapter path sets `final_recovery_profiles`, ensure the draft builder receives it and final C output contains profile-specific `Vk` recovery.

Expected behavior:

- Case with one internal node emits one recovered voltage.
- Case with two internal nodes emits two recovered voltages.
- Case with no internal nodes emits no recovery assignments.

- [ ] **Step 3: Add C draft test**

Add:

```python
def test_alias_template_emits_profile_specific_recovery_for_final_retained_adapter():
    # Build two profiles with same final ports and different finalInternalGroups.
    # Call _build_multicase_alias_template_payload.
    # Assert generated code includes switch/case recovery sections for each profile.
    # Assert it does not mention placeholder nodes.
```

Concrete assertions:

```python
assert "inner_a" in c_code
assert "inner_b" in c_code
assert "placeholder" not in c_code.lower()
assert "G_EPSILON" not in c_code
```

- [ ] **Step 4: Run test**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -k profile_specific_recovery -q
```

Expected:

- Test passes.

---

## Task 7: Verify Real Fixture `Trf_Ctest_dummy.json`

**Files:**
- Use: `exports/Trf_Ctest_dummy.json`
- Modify tests only if fixture needs normalization.

- [ ] **Step 1: Inspect fixture final fields**

Run:

```powershell
python -c "import json; d=json.load(open('exports/Trf_Ctest_dummy.json',encoding='utf-8')); print('loaded', type(d).__name__)"
```

Expected:

- JSON loads.

- [ ] **Step 2: Add a fixture-based regression test**

In `tests/test_multicase_c_export_alias_template.py`, add:

```python
def test_trf_ctest_dummy_multicase_export_uses_final_retained_adapter():
    payload = load_export_payload("exports/Trf_Ctest_dummy.json")
    c_code = generate_multicase_c_from_export(payload)
    assert "multi-case topology invariant failed" not in c_code
    assert "G_EPSILON" not in c_code
    assert "final retained" in c_code or "recovery profile" in c_code
```

Use existing fixture loader/export helper names from the test file. Do not invent new API if an existing helper exists.

- [ ] **Step 3: Run fixture test**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -k trf_ctest_dummy -q
```

Expected:

- Test passes.
- If it fails, error must identify missing frontend final fields or backend shape mismatch.

- [ ] **Step 4: Manual browser verification**

Open `http://127.0.0.1:4177/`, import `exports/Trf_Ctest_dummy.json`, click `Multi-Case C Export`.

Expected:

- No `multi-case topology invariant failed`.
- C draft appears.
- Case mapping lists all Pack cases.
- Per-case recovery information exists when a case has internal nodes.

---

## Task 8: Preserve Existing Dummy/N-Dummy Behavior

**Files:**
- Test: `tests/test_multicase_dummy_node_block.py`
- Test: `tests/test_multicase_c_export_alias_template.py`

- [ ] **Step 1: Run dummy regression**

Run:

```powershell
python -m pytest tests/test_multicase_dummy_node_block.py -q
```

Expected:

- All tests pass.

- [ ] **Step 2: Add no-G_EPSILON assertion if missing**

If no test already checks this, add:

```python
def test_dummy_finalization_does_not_emit_g_epsilon_in_c_export():
    c_code = generate_dummy_fixture_c_code()
    assert "G_EPSILON" not in c_code
```

- [ ] **Step 3: Run alias template regression**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py -q
```

Expected:

- Existing multicase tests pass.

---

## Task 9: Update User-Facing Errors And Translations

**Files:**
- Modify: `index.html`
- Modify: `optimized_elimination_api.py` if backend error messages are user-facing.

- [ ] **Step 1: Add English/Chinese messages**

Add localized messages:

English:

```text
The Pack cases have different internal recovery nodes, but their final external ports match. C export will use a per-case recovery profile.
```

Chinese:

```text
这些 Pack 工况的内部恢复节点不同，但最终外部端口一致。C 导出将使用每个工况自己的恢复 profile。
```

Error English:

```text
Pack cases cannot be combined: final external port order differs.
```

Error Chinese:

```text
无法合并这些 Pack 工况：最终外部端口顺序不一致。
```

- [ ] **Step 2: Check language mode**

In English mode:

- No Chinese-only warning in Pack save.
- No Chinese-only error in Multi-Case C export.

In Chinese mode:

- Chinese messages show cleanly.
- English fallback is acceptable only for backend technical suffix.

---

## Task 10: Update Private Debug Notes

**Files:**
- Modify: `PROJECT_NOTES.md`

- [ ] **Step 1: Add architecture note**

Append:

```markdown
### Pack final-retained recovery profile adapter

When Pack cases have identical final external ports but different raw/internal nodes, do not force raw topology invariance. Use final retained fields (`finalExternalGroups`, `finalGMatrix`, `finalIhisVector`) as the alias-template contract, and keep `finalInternalGroups/K_v/K_h` as per-case recovery profiles. Missing internal nodes are backend placeholders only; prune them before Gkk inversion. Runtime-mutable case groups still require fixed topology and dimensions.
```

- [ ] **Step 2: Add debugging note**

Append:

```markdown
If `multi-case topology invariant failed` appears for a Pack with matching final ports, inspect whether the frontend payload includes all final retained fields. Do not assume old cache until a newly imported fixture and frontend export path have been tested.
```

- [ ] **Step 3: Do not stage private note**

Run:

```powershell
git status --short PROJECT_NOTES.md
```

Expected:

- File may be modified locally.
- Do not commit unless user explicitly requests.

---

## Task 11: Full Regression

**Files:**
- No source edits.

- [ ] **Step 1: Run focused backend tests**

Run:

```powershell
python -m pytest tests/test_multicase_c_export_alias_template.py tests/test_multicase_dummy_node_block.py tests/test_multicase_conditional_gvalue.py -q
```

Expected:

- All pass.

- [ ] **Step 2: Run optimized elimination tests**

Run:

```powershell
python -m pytest tests/test_optimized_elimination.py tests/test_reduce_api_final_simplification.py -q
```

Expected:

- All pass.

- [ ] **Step 3: Run frontend math formatter regression**

Run:

```powershell
node tests/frontend_math_formatter_cases.mjs
```

Expected:

- Node test exits with code 0.

- [ ] **Step 4: Browser smoke test**

Use the actual app:

1. Import `exports/Trf_Ctest_dummy.json`.
2. Click `Reduced`.
3. Click `Optimized Elimination / C Export`.
4. Click `Multi-Case C Export`.

Expected:

- No topology invariant error.
- No G_EPSILON in C draft.
- No undefined enum constants.
- No Chinese-only warning in English mode.

---

## Task 12: Commit Implementation

**Files:**
- Stage source, tests, docs, fixtures if needed.
- Do not stage `PROJECT_NOTES.md`.

- [ ] **Step 1: Review diff**

Run:

```powershell
git diff -- optimized_elimination_api.py index.html tests docs exports
```

Expected:

- Diff matches this plan.
- No unrelated UI or formatting churn.

- [ ] **Step 2: Stage**

Run:

```powershell
git add optimized_elimination_api.py index.html tests docs exports
```

- [ ] **Step 3: Commit**

Run:

```powershell
git commit -m "feat: support pack cases with internal recovery profiles"
```

Expected:

- Commit succeeds.

---

## Safety Checklist

- [ ] Does not change ordinary single-case behavior.
- [ ] Does not change existing Dummy/N-Dummy user rules.
- [ ] Does not emit `G_EPSILON` for finalized dummy placeholders.
- [ ] Does not allow runtime-mutable topology/dimension changes.
- [ ] Does not invert placeholder Gkk rows.
- [ ] Does not rely on `sp.cancel`, `sp.factor`, or `sp.simplify` for topology compatibility.
- [ ] Preserves per-case voltage recovery.
- [ ] Uses actual `Trf_Ctest_dummy.json` frontend path before claiming fixed.

---

## Execution Options

Plan complete and saved to `docs/superpowers/plans/2026-07-05-pack-case-gkk-dummy-placeholders.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, better for avoiding another false-positive fix.
2. **Inline Execution** - execute tasks in this session using executing-plans, with manual checkpoints and real fixture verification.
