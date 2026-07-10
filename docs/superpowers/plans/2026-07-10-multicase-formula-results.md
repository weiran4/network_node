# Multi-Case Formula Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Branch Currents, Node Equations, and Reduced results against the same global multi-case profiles used by Multi-Case C Export, without mutating the active editor case.

**Architecture:** Add a small set of frontend helpers in `index.html` that normalize profile display data and evaluate existing formula builders inside a temporary profile context. Branch Currents remains single-profile with a selector; Node Equations renders all profiles synchronously; Reduced builds materialized payloads per profile and processes them sequentially so each profile appears as soon as it finishes.

**Tech Stack:** Single-file vanilla HTML/CSS/JS frontend in `index.html`; existing `/reduce-system` backend API; Python unittest source-shape tests in `tests/test_frontend_optimized_reuse.py`; existing Node frontend formatter tests.

## Global Constraints

- Multi-case formula views use Multi-Case C Export profile text, ordering, and `case_id` semantics.
- Formula evaluation must restore `activeSwitchCase` and packaged `activeNetworkCase` in a `finally` block.
- Formula views must not call `commitHistory()`, model export, persistence, or full app state mutation when evaluating profiles.
- Branch Currents gets a compact `Case` selector and displays one selected profile at a time.
- Node Equations displays all profiles from top to bottom.
- Reduced displays all profiles from top to bottom and runs reduction sequentially, not with `Promise.all`.
- Single-case behavior remains unchanged.
- Invalid profile JSON shows a localized error instead of silently using the active editor case.
- No changes to Multi-Case C Export, Python Draft, model JSON, optimized elimination, or backend math are required.

---

### Task 1: Add Source-Shape Tests for Profile Helpers

**Files:**
- Modify: `tests/test_frontend_optimized_reuse.py`
- Read: `index.html`

**Interfaces:**
- Produces source assertions for `multiCaseFormulaProfiles`, `runWithCaseProfile`, `profileCaseSummary`, and `profileSectionHeader`.
- Later tasks must add those exact function names in `index.html`.

- [ ] **Step 1: Write the failing tests**

Add these tests near the existing frontend multi-case tests in `tests/test_frontend_optimized_reuse.py`:

```python
    def test_formula_views_have_multicase_profile_helpers(self):
        source = self.source
        self.assertIn("function multiCaseFormulaProfiles(", source)
        self.assertIn("function runWithCaseProfile(profile, callback)", source)
        self.assertIn("function profileCaseSummary(profile, branches = multiCaseEligibleBranches())", source)
        self.assertIn("function profileSectionHeader(profile, index)", source)

        helper_start = source.index("function runWithCaseProfile(profile, callback)")
        helper_end = source.index("function profileCaseSummary", helper_start)
        helper_source = source[helper_start:helper_end]
        self.assertIn("try {", helper_source)
        self.assertIn("finally {", helper_source)
        self.assertIn("branch.activeSwitchCase = activeSwitchCase;", helper_source)
        self.assertIn("if (branch.packageOriginal) applyPackageNetworkCase(branch, activeNetworkCase);", helper_source)
        self.assertNotIn("commitHistory(", helper_source)
        self.assertNotIn("saveToStorage(", helper_source)

    def test_formula_profile_helpers_use_multicase_profiles_text(self):
        source = self.source
        start = source.index("function multiCaseFormulaProfiles(")
        end = source.index("function runWithCaseProfile", start)
        helper_source = source[start:end]
        self.assertIn("const branches = multiCaseEligibleBranches();", helper_source)
        self.assertIn("ensureMultiCaseProfilesText(branches);", helper_source)
        self.assertIn("parseMultiCaseProfiles()", helper_source)
        self.assertIn("case_id: index", helper_source)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_formula_views_have_multicase_profile_helpers tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_formula_profile_helpers_use_multicase_profiles_text`

Expected: FAIL because the helper functions do not exist yet.

- [ ] **Step 3: Commit only after this task is green**

Commit message after implementation and passing tests: `feat: add multi-case formula profile helpers`.

### Task 2: Implement Profile Helper Layer

**Files:**
- Modify: `index.html`
- Test: `tests/test_frontend_optimized_reuse.py`

**Interfaces:**
- Add `multiCaseFormulaProfiles()` returning `[]` for no eligible branches and otherwise normalized profiles with `case_id`, `name`, `comment`, and `case_map`.
- Add `runWithCaseProfile(profile, callback)` restoring branch case state in `finally`.
- Add `profileCaseSummary(profile, branches = multiCaseEligibleBranches())`.
- Add `profileSectionHeader(profile, index)`.

- [ ] **Step 1: Implement minimal helpers**

Place the helpers immediately after `parseMultiCaseProfiles()` in `index.html`:

```javascript
    function multiCaseFormulaProfiles() {
      const branches = multiCaseEligibleBranches();
      if (!branches.length) return [];
      ensureMultiCaseProfilesText(branches);
      return parseMultiCaseProfiles().map((profile, index) => ({
        case_id: index,
        name: profile.name || defaultCaseName(index),
        comment: profile.comment || "",
        case_map: profile.case_map || {}
      }));
    }

    function runWithCaseProfile(profile, callback) {
      const originals = state.branches.map(branch => [branch, Number(branch.activeSwitchCase) || 0, Number(branch.packageOriginal?.activeNetworkCase) || 0]);
      try {
        Object.entries(profile?.case_map || {}).forEach(([branchId, caseIndex]) => {
          const branch = findBranch(branchId);
          if (!branch) return;
          normalizeSwitchCases(branch);
          if (!branch.switchCases?.length) return;
          branch.activeSwitchCase = Math.max(0, Math.min(branch.switchCases.length - 1, Number(caseIndex) || 0));
          if (branch.packageOriginal) applyPackageNetworkCase(branch, branch.activeSwitchCase);
        });
        return callback();
      } finally {
        originals.forEach(([branch, activeSwitchCase, activeNetworkCase]) => {
          branch.activeSwitchCase = activeSwitchCase;
          if (branch.packageOriginal) applyPackageNetworkCase(branch, activeNetworkCase);
        });
      }
    }

    function profileCaseSummary(profile, branches = multiCaseEligibleBranches()) {
      const branchById = new Map(branches.map(branch => [branch.id, branch]));
      const entries = Object.entries(profile?.case_map || {}).map(([branchId, caseIndex]) => {
        const branch = branchById.get(branchId) || findBranch(branchId);
        const index = Math.max(0, Number(caseIndex) || 0);
        const branchLabel = branch?.name || branch?.id || branchId;
        return `${branchLabel} = ${displayCaseName(branch?.switchCases?.[index], index)}`;
      });
      return entries.join("; ") || optText("使用当前工况", "Use current active cases");
    }

    function profileSectionHeader(profile, index) {
      const caseId = Number(profile?.case_id ?? index);
      const name = String(profile?.name || defaultCaseName(caseId));
      const comment = String(profile?.comment || "").trim();
      const title = comment ? `case ${caseId}: ${name} - ${comment}` : `case ${caseId}: ${name}`;
      return `
        <div class="formula-card formula-profile-heading">
          <div class="formula-name">${escapeHtml(title)}</div>
          <div class="formula-empty">${escapeHtml(profileCaseSummary(profile))}</div>
        </div>
      `;
    }
```

- [ ] **Step 2: Run helper tests**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_formula_views_have_multicase_profile_helpers tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_formula_profile_helpers_use_multicase_profiles_text`

Expected: PASS.

- [ ] **Step 3: Run syntax check**

Run: `node --check index.html`

Expected: fails because `node --check` cannot parse raw HTML, or passes if the local setup already extracts script. If it fails with HTML parsing, run the existing JS-focused test command in Task 6 instead.

### Task 3: Add Branch Currents Case Selector

**Files:**
- Modify: `index.html`
- Modify: `tests/test_frontend_optimized_reuse.py`

**Interfaces:**
- Add `state.formulaCaseProfileIndex`.
- Add `clampedFormulaCaseProfileIndex(profiles)`.
- Add `renderFormulaCaseSelector(profiles, selectedIndex)`.
- Branch Currents formulas and reduced branch current payloads are built inside `runWithCaseProfile(selectedProfile, ...)`.

- [ ] **Step 1: Write failing tests**

Add this source-shape test:

```python
    def test_branch_currents_render_case_selector_and_profile_context(self):
        source = self.source
        self.assertIn("formulaCaseProfileIndex", source)
        self.assertIn("function clampedFormulaCaseProfileIndex(profiles)", source)
        self.assertIn("function renderFormulaCaseSelector(profiles, selectedIndex)", source)
        render_start = source.index('outputText.className = "output-body output-body-formulas";')
        render_end = source.index("function renderReducedBranchCurrentDisabledNote", render_start)
        render_source = source[render_start:render_end]
        self.assertIn("const profiles = multiCaseFormulaProfiles();", render_source)
        self.assertIn("renderFormulaCaseSelector(profiles, selectedProfileIndex)", render_source)
        self.assertIn("runWithCaseProfile(selectedProfile, () => currentFormulaEntriesForCanvas())", render_source)
        self.assertIn("renderReducedBranchCurrentsAsync(token, selectedProfile)", render_source)
        self.assertIn('data-formula-case-profile', source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_branch_currents_render_case_selector_and_profile_context`

Expected: FAIL because selector and state are not implemented.

- [ ] **Step 3: Implement selector and state**

In the `state` object add:

```javascript
      formulaCaseProfileIndex: 0,
```

Near profile helpers add:

```javascript
    function clampedFormulaCaseProfileIndex(profiles) {
      const count = profiles.length;
      if (!count) {
        state.formulaCaseProfileIndex = 0;
        return 0;
      }
      const index = Math.max(0, Math.min(count - 1, Number(state.formulaCaseProfileIndex) || 0));
      state.formulaCaseProfileIndex = index;
      return index;
    }

    function renderFormulaCaseSelector(profiles, selectedIndex) {
      if (!profiles.length) return "";
      const options = profiles.map((profile, index) => {
        const summary = profileCaseSummary(profile);
        const label = `case ${profile.case_id ?? index}: ${profile.name || defaultCaseName(index)} - ${summary}`;
        return `<option value="${index}" ${index === selectedIndex ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }).join("");
      return `
        <div class="formula-toolbar">
          <label class="formula-select-label">
            <span>${escapeHtml(optText("Case", "Case"))}</span>
            <select data-formula-case-profile>${options}</select>
          </label>
        </div>
      `;
    }
```

- [ ] **Step 4: Wire Branch Currents render**

In the default Branch Currents path in `renderOutput()`:

```javascript
      const profiles = multiCaseFormulaProfiles();
      const selectedProfileIndex = clampedFormulaCaseProfileIndex(profiles);
      const selectedProfile = profiles[selectedProfileIndex] || null;
      const formulas = selectedProfile
        ? runWithCaseProfile(selectedProfile, () => currentFormulaEntriesForCanvas())
        : currentFormulaEntriesForCanvas();
      const token = ++branchCurrentRenderToken;
      const reducedHtml = state.showReducedBranchCurrents
        ? (currentReducedBranchCurrentHtml(selectedProfile) || renderReducedBranchCurrentPlaceholder(selectedProfile))
        : renderReducedBranchCurrentDisabledNote();
      outputText.innerHTML = `<div class="formula-list">${renderFormulaCaseSelector(profiles, selectedProfileIndex)}${formulas.map(renderFormulaCard).join("")}${reducedHtml}</div>`;
      if (state.showReducedBranchCurrents) renderReducedBranchCurrentsAsync(token, selectedProfile);
```

Update `renderReducedBranchCurrentPlaceholder`, `renderReducedBranchCurrentsAsync`, `buildReducedBranchCurrentPayload`, and `currentReducedBranchCurrentHtml` to accept `profile = null` and use `runWithCaseProfile(profile, ...)` when present.

- [ ] **Step 5: Add event listener**

In the existing output event delegation area, add:

```javascript
      const formulaCaseSelect = event.target.closest("[data-formula-case-profile]");
      if (formulaCaseSelect) {
        state.formulaCaseProfileIndex = Number(formulaCaseSelect.value) || 0;
        renderOutput();
        return;
      }
```

- [ ] **Step 6: Run branch selector test**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_branch_currents_render_case_selector_and_profile_context`

Expected: PASS.

### Task 4: Render Node Equations for Every Profile

**Files:**
- Modify: `index.html`
- Modify: `tests/test_frontend_optimized_reuse.py`

**Interfaces:**
- Add `renderSingleNodeEquations()` containing current `renderNodeEquations()` body.
- Update `renderNodeEquations()` to render all `multiCaseFormulaProfiles()` via `runWithCaseProfile`.

- [ ] **Step 1: Write failing test**

Add:

```python
    def test_node_equations_render_all_multicase_profiles(self):
        source = self.source
        self.assertIn("function renderSingleNodeEquations()", source)
        start = source.index("function renderNodeEquations()")
        end = source.index("function renderSingleNodeEquations()", start)
        node_source = source[start:end]
        self.assertIn("const profiles = multiCaseFormulaProfiles();", node_source)
        self.assertIn("profiles.map((profile, index) =>", node_source)
        self.assertIn("profileSectionHeader(profile, index)", node_source)
        self.assertIn("runWithCaseProfile(profile, () => renderSingleNodeEquations())", node_source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_node_equations_render_all_multicase_profiles`

Expected: FAIL because the split function is not implemented.

- [ ] **Step 3: Implement split renderer**

Change `renderNodeEquations()` to:

```javascript
    function renderNodeEquations() {
      let profiles;
      try {
        profiles = multiCaseFormulaProfiles();
      } catch (error) {
        return `<div class="formula-list"><div class="formula-empty">${escapeHtml(localizedBackendErrorMessage(error))}</div></div>`;
      }
      if (!profiles.length) return renderSingleNodeEquations();
      return `
        <div class="formula-list">
          ${profiles.map((profile, index) => {
            try {
              return `${profileSectionHeader(profile, index)}${runWithCaseProfile(profile, () => renderSingleNodeEquations())}`;
            } catch (error) {
              return `${profileSectionHeader(profile, index)}<div class="formula-card"><div class="formula-empty">${escapeHtml(localizedBackendErrorMessage(error))}</div></div>`;
            }
          }).join("")}
        </div>
      `;
    }

    function renderSingleNodeEquations() {
      ...
    }
```

Move the existing `renderNodeEquations()` body into `renderSingleNodeEquations()`, keeping its returned `.formula-list` wrapper for single-case compatibility.

- [ ] **Step 4: Run node equation test**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_node_equations_render_all_multicase_profiles`

Expected: PASS.

### Task 5: Render Reduced Profiles Sequentially

**Files:**
- Modify: `index.html`
- Modify: `tests/test_frontend_optimized_reuse.py`

**Interfaces:**
- Add `renderReducedProfilePlaceholder(profile, index)`.
- Add `renderReducedProfileIntoToken(token, profile, index, payload)`.
- Add `replaceReducedProfileHtml(token, index, html)`.
- Update `renderReducedEquationsAsync(token)` to build profile payloads and await each profile inside a `for` loop.

- [ ] **Step 1: Write failing test**

Add:

```python
    def test_reduced_equations_render_profiles_sequentially(self):
        source = self.source
        self.assertIn("function renderReducedProfilePlaceholder(profile, index)", source)
        self.assertIn("async function renderReducedProfileIntoToken(token, profile, index, payload)", source)
        self.assertIn("function replaceReducedProfileHtml(token, index, html)", source)
        start = source.index("async function renderReducedEquationsAsync(token)")
        end = source.index("function currentReducedHtml", start)
        reduced_source = source[start:end]
        self.assertIn("const profiles = multiCaseFormulaProfiles();", reduced_source)
        self.assertIn("for (let index = 0; index < profilePayloads.length; index += 1)", reduced_source)
        self.assertIn("await renderReducedProfileIntoToken(token, profile, index, payload);", reduced_source)
        self.assertNotIn("Promise.all", reduced_source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_reduced_equations_render_profiles_sequentially`

Expected: FAIL because sequential profile rendering does not exist.

- [ ] **Step 3: Extract single-payload renderer**

Keep the existing single-case behavior by extracting the body of `renderReducedEquationsAsync(token)` into:

```javascript
    async function renderSingleReducedPayloadIntoToken(token, payload, replaceHtml) {
      const staticHtml = reducedStaticMessage(payload);
      if (staticHtml) {
        replaceHtml(staticHtml);
        return;
      }
      const key = reducedCacheKey(payload);
      const cachedEntry = cacheGet(reducedCache, key);
      ...
    }
```

The `replaceHtml` callback must be called only after token checks in the caller.

- [ ] **Step 4: Implement profile placeholders and replacements**

Add:

```javascript
    function renderReducedProfilePlaceholder(profile, index) {
      return `
        <div class="formula-profile-section" data-reduced-profile-index="${index}">
          ${profileSectionHeader(profile, index)}
          <div class="formula-card">
            <div class="formula-empty">${escapeHtml(optText("正在用 SymPy 化简节点消去方程...", "Reducing this case with SymPy..."))}</div>
          </div>
        </div>
      `;
    }

    function replaceReducedProfileHtml(token, index, html) {
      if (token !== reducedRenderToken || state.activeOutput !== "reducedEquations") return;
      const target = document.querySelector(`[data-reduced-profile-index="${index}"]`);
      if (target) target.innerHTML = html;
    }
```

- [ ] **Step 5: Implement sequential multi-case reduced render**

At the top of `renderReducedEquationsAsync(token)`, parse profiles. If profiles exist:

```javascript
      const profiles = multiCaseFormulaProfiles();
      if (profiles.length) {
        const profilePayloads = profiles.map(profile => ({
          profile,
          payload: runWithCaseProfile(profile, () => buildReducedPayload())
        }));
        if (token === reducedRenderToken) {
          outputText.innerHTML = `<div class="formula-list">${profilePayloads.map(({ profile }, index) => renderReducedProfilePlaceholder(profile, index)).join("")}</div>`;
        }
        for (let index = 0; index < profilePayloads.length; index += 1) {
          const { profile, payload } = profilePayloads[index];
          await renderReducedProfileIntoToken(token, profile, index, payload);
          if (token !== reducedRenderToken || state.activeOutput !== "reducedEquations") break;
        }
        return;
      }
```

Implement `renderReducedProfileIntoToken` to call the single-payload renderer and wrap success/error with `profileSectionHeader(profile, index)`.

- [ ] **Step 6: Run reduced sequential test**

Run: `python -m unittest tests.test_frontend_optimized_reuse.TestFrontendOptimizedReuse.test_reduced_equations_render_profiles_sequentially`

Expected: PASS.

### Task 6: Regression Verification and Commit

**Files:**
- Verify: `index.html`
- Verify: `tests/test_frontend_optimized_reuse.py`
- Verify: `tests/frontend_math_formatter_cases.mjs`

**Interfaces:**
- No new interfaces.

- [ ] **Step 1: Run focused frontend source tests**

Run: `python -m unittest tests.test_frontend_optimized_reuse`

Expected: PASS.

- [ ] **Step 2: Run existing Node frontend tests**

Run: `node tests\frontend_math_formatter_cases.mjs`

Expected: PASS.

- [ ] **Step 3: Run syntax-sensitive Python tests if quick enough**

Run: `python -m unittest tests.test_frontend_backend_math_consistency tests.test_structured_formula_elimination`

Expected: PASS.

- [ ] **Step 4: Inspect git diff**

Run: `git diff -- index.html tests/test_frontend_optimized_reuse.py docs/superpowers/plans/2026-07-10-multicase-formula-results.md`

Expected: Diff only contains planned formula view behavior and tests.

- [ ] **Step 5: Commit implementation**

Run:

```bash
git add index.html tests/test_frontend_optimized_reuse.py docs/superpowers/plans/2026-07-10-multicase-formula-results.md
git commit -m "feat: show formulas by multi-case profile"
```

Expected: Commit succeeds on `codex/ui-engineering-polish`.

