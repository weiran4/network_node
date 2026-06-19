import unittest
from pathlib import Path


class FrontendOptimizedReuseTests(unittest.TestCase):
    def test_optimized_request_reuses_reduced_dependency_cache(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("async function ensureReducedResult", source)
        self.assertIn("function reducedDependencyAnalysisFromResult", source)
        self.assertIn("reduced_dependency_analysis", source)
        self.assertIn("await ensureReducedResult(basePayload)", source)

    def test_optimized_payload_includes_direct_retained_stamps(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function directRetainedStampsForPayload", source)
        self.assertIn("direct_retained_stamps: directRetainedStampsForPayload", source)
        self.assertIn("direct_retained_stamps: payload.direct_retained_stamps", source)
        self.assertIn("Gred_direct", source)

    def test_multi_case_export_uses_independent_tab_and_endpoint(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('data-output="multiCaseCExport"', source)
        self.assertIn('"多Case C导出": "Multi-Case C Export"', source)
        self.assertIn('localApiUrls("/multi-case-c-export")', source)
        self.assertIn('localApiUrls("/optimized-elimination")', source)
        self.assertIn("isMissingRoute", source)
        self.assertIn("backendError: !isMissingRoute", source)
        self.assertIn('if (state.activeOutput === "multiCaseCExport")', source)
        self.assertIn('if (state.activeOutput === "optimizedElimination")', source)

    def test_multi_case_export_shows_readonly_profile_summary(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function renderMultiCaseProfileEditor")
        end = source.index("function renderMultiCaseCExportResult")
        editor_source = source[start:end]

        self.assertIn("ensureMultiCaseProfilesText(branches);", editor_source)
        self.assertIn("多 case 元件摘要", editor_source)
        self.assertIn("case_id 映射", editor_source)
        self.assertIn('<span class="formula-lhs">case ${index}</span>', editor_source)
        self.assertNotIn("data-multicase-profiles", editor_source)
        self.assertNotIn("data-multicase-case-id", editor_source)

    def test_multi_case_result_does_not_repeat_case_mapping(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function renderMultiCaseCExportResult")
        end = source.index("function multiCaseExportCacheKey")
        result_source = source[start:end]

        self.assertNotIn("已生成组合", result_source)
        self.assertNotIn("Generated Case Combinations", result_source)
        self.assertNotIn("profileHtml", result_source)

    def test_multi_case_export_shows_alias_template_summary(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function renderMultiCaseCExportResult")
        end = source.index("function multiCaseExportCacheKey")
        result_source = source[start:end]

        self.assertIn("Effective Aliases / Owners", result_source)
        self.assertIn("Template Reduction Summary", result_source)
        self.assertIn("case full value", result_source)
        self.assertIn("codegen_mode", result_source)
        self.assertIn("Topology validation", result_source)

    def test_optimized_direct_retained_matrices_render_with_tagged_sources(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("renderMatrix(direct.Gred_direct, null, direct.Gred_direct_tagged)", source)
        self.assertIn("renderMatrix(direct.Gred_direct_ram, null, direct.Gred_direct_ram_tagged)", source)
        self.assertIn("renderMatrix(direct.Gred_direct_code, null, direct.Gred_direct_code_tagged)", source)

    def test_single_multicase_branch_defaults_to_all_cases(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function defaultMultiCaseProfiles")
        end = source.index("function multiCaseProfilesKey")
        profile_source = source[start:end]

        self.assertIn("(branch.switchCases || []).forEach((_item, index)", profile_source)
        self.assertIn("{ ...caseMap, [branch.id]: index }", profile_source)
        self.assertIn("case_map: { ...caseMap }", profile_source)

    def test_multiple_multicase_branches_use_cartesian_profiles_not_active_case_sampling(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function defaultMultiCaseProfiles")
        end = source.index("function multiCaseProfilesKey")
        profile_source = source[start:end]

        self.assertIn("const profiles = [];", profile_source)
        self.assertIn("buildProfile(0, {}, []);", profile_source)
        self.assertIn("case_map: { ...caseMap }", profile_source)
        self.assertNotIn("const activeMap", profile_source)
        self.assertNotIn("return [\n        {\n          name: \"Case 1\"", profile_source)

    def test_multicase_profile_key_invalidates_old_single_profile_cache_without_active_case_dependency(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("alias-template-cartesian-profiles-v1", source)
        start = source.index("function ensureMultiCaseProfilesText")
        end = source.index("function parseMultiCaseProfiles")
        ensure_source = source[start:end]
        self.assertIn("const generated = JSON.stringify(defaultMultiCaseProfiles(branches), null, 2);", ensure_source)
        self.assertIn("state.multiCaseProfilesText = generated;", ensure_source)
        self.assertIn("state.multiCaseProfilesKey = key;", ensure_source)

    def test_multicase_payload_regenerates_profiles_before_parsing_saved_state(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function multiCaseExportPayload")
        end = source.index("async function requestMultiCaseCExport")
        payload_source = source[start:end]
        self.assertIn("ensureMultiCaseProfilesText(branches);", payload_source)
        self.assertIn("const profiles = parseMultiCaseProfiles();", payload_source)

    def test_right_panel_has_resizable_width_controls(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("id=\"panelResizeBar\"", source)
        self.assertIn("--panel-width", source)
        self.assertIn("function applyPanelWidth", source)
        self.assertIn("function startPanelResize", source)
        self.assertIn("panelResizeBar.addEventListener(\"pointerdown\", startPanelResize)", source)

    def test_internal_node_changes_invalidate_math_caches(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("state.internalNodes = state.internalNodes.filter(item => item !== id);\n          }\n          invalidateMathCaches();", source)
        self.assertIn("state.nodeOrder = Array.from(list.querySelectorAll(\".node-order-item[data-node-id]\")).map(row => row.dataset.nodeId);\n          invalidateMathCaches();", source)


if __name__ == "__main__":
    unittest.main()
