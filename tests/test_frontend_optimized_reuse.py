import re
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
        self.assertIn("按 case 条件启用的 GValue", result_source)
        self.assertIn("case_id is assumed fixed before simulation. Runtime case switching is not supported.", result_source)
        self.assertIn("启用条件", result_source)
        self.assertIn("gvalue_conditions", result_source)
        self.assertIn("uses_case_conditional_gvalue", result_source)

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

    def test_multicase_cache_key_ignores_language_for_backend_result_reuse(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function multiCaseExportCacheKey")
        end = source.index("async function renderMultiCaseCExportAsync")
        cache_key_source = source[start:end]
        self.assertNotIn("language: state.language", cache_key_source)
        self.assertIn("case_id_symbol: payload.case_id_symbol", cache_key_source)
        self.assertIn("case_profiles", cache_key_source)

    def test_export_state_persists_optimized_and_multicase_caches(self):
        source = Path("index.html").read_text(encoding="utf-8")

        export_start = source.index("function circuitStateJson")
        export_end = source.index("function applyProjectUiState")
        export_source = source[export_start:export_end]
        self.assertIn('optimizedEliminationCache: currentCacheSnapshot(optimizedEliminationCache, ["result"])', export_source)
        self.assertIn('multiCaseExportCache: currentCacheSnapshot(multiCaseExportCache, ["result"])', export_source)

        load_start = source.index("function loadCircuitState")
        load_end = source.index("function appendCircuitState")
        load_source = source[load_start:load_end]
        self.assertIn('restoreCacheSnapshot(optimizedEliminationCache, data.optimizedEliminationCache || [], ["result"])', load_source)
        self.assertIn('restoreCacheSnapshot(multiCaseExportCache, data.multiCaseExportCache || [], ["result"])', load_source)

    def test_export_dialog_supports_native_save_as_picker(self):
        source = Path("index.html").read_text(encoding="utf-8")
        server_source = Path("local_server.py").read_text(encoding="utf-8")
        helper_source = Path("save_as_dialog.py").read_text(encoding="utf-8")

        self.assertIn('id="saveExportAsFile"', source)
        self.assertIn("async function saveExportTextAsFile", source)
        self.assertIn("async function saveViaNativeDialog", source)
        self.assertIn("window.showSaveFilePicker", source)
        self.assertIn("createWritable()", source)
        self.assertIn('localApiUrls("/save-circuit-as")', source)
        self.assertIn("当前浏览器和本地保存服务都无法打开保存路径选择器", source)
        self.assertIn('document.getElementById("saveExportAsFile").addEventListener("click", saveExportTextAsFile)', source)
        self.assertIn('"另存为..."', source)
        self.assertIn('"Save As..."', source)
        self.assertIn('if parsed.path == "/save-circuit-as"', server_source)
        self.assertIn('run_save_as_dialog(self.read_json())', server_source)
        self.assertIn("filedialog.asksaveasfilename", helper_source)
        self.assertIn('file_path.write_text(data, encoding="utf-8")', helper_source)

    def test_export_dialog_text_uses_language_aware_strings(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('"可另存到任意文件夹；“保存 JSON”仍会保存到 E:\\\\network_node\\\\exports。输入新文件名，或选择已有文件名覆盖。"', source)
        self.assertIn('"Use Save As to choose any folder; Save JSON still writes to E:\\\\network_node\\\\exports. Enter a new file name or select an existing file to overwrite."', source)
        self.assertIn('existingExportFiles.innerHTML = `<option value="">${escapeHtml(tr("不覆盖已有文件"))}</option>`;', source)
        self.assertIn('optText("所有画布都没有可导出的元件"', source)
        self.assertIn('optText("准备保存"', source)
        self.assertIn('optText("保存失败：本地保存服务不可用"', source)
        self.assertIn('optText("已复制 JSON"', source)
        self.assertIn('optText("已选中 JSON"', source)
        self.assertIn('optText(`已复制 ${branches.length} 个元件`', source)
        self.assertIn('optText(`已导入 ${file.name}：新增 ${result.branches} 个元件`', source)
        self.assertIn('optText("导入失败"', source)
        self.assertIn('optText("已调整画布顺序"', source)
        self.assertIn('"关闭": "Close"', source)
        self.assertIn('"全屏": "Fullscreen"', source)
        self.assertIn('"退出全屏": "Exit Fullscreen"', source)
        self.assertIn('"拖动排序": "Drag to reorder"', source)
        self.assertIn('"切换外部/内部节点": "Toggle external/internal node"', source)
        self.assertIn('button.title = tr(active ? "退出全屏" : "全屏");', source)
        self.assertIn('escapeHtml(tr("切换外部/内部节点"))', source)
        self.assertIn('optText(`重命名 ${rowName(group)}`', source)

    def test_editor_group_titles_and_pack_alerts_are_language_aware(self):
        source = Path("index.html").read_text(encoding="utf-8")
        zh_to_en = source[source.index("const zhToEn = {"):source.index("function tr")]
        keys = set(re.findall(r'^\s*"([^"]+)":', zh_to_en, flags=re.MULTILINE))
        editor_titles = set(re.findall(r'editorGroup\("[^"]+",\s*"([^"]+)"', source))

        self.assertFalse(editor_titles - keys)
        self.assertIn('"G 常数属性": "G Constant Properties"', source)
        self.assertIn('alert(tr("选中的部分没有外部边界节点，不能打包成可连接的 Y 节点黑盒。"));', source)
        self.assertIn('alert(subsystem.warnings.map(item => tr(item)).join("\\n"));', source)
        self.assertIn('alert(optText(', source)
        self.assertIn('`Pack failed: ${error.message || String(error)}`', source)
        self.assertIn('`Local G matrix must be ${ports.length} x ${ports.length} and match port order ${ports.join(", ")}.`', source)
        self.assertIn('`Observed branch ${index + 1}: V_${ref} in the formula has no matching packaged node.`', source)
        self.assertIn('"连接节点：依次点击两个端子": "Wire nodes: click two terminals in sequence"', source)
        self.assertIn('"点击画布添加二端口支路": "Click canvas to add a 2-port branch"', source)
        self.assertIn('"点击画布添加电压源串联 G": "Click canvas to add a voltage source with series G"', source)
        self.assertIn('"工具": "Tools"', source)
        self.assertIn('"电路画布": "Circuit Canvas"', source)
        self.assertIn('"无": "None"', source)
        self.assertIn('statusEl.textContent = tr(tool === "wire"', source)

    def test_canvas_hitboxes_stay_compact_for_dense_wiring(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("const BRANCH_HIT_PAD_X = 10;", source)
        self.assertIn("const BRANCH_HIT_PAD_Y = 8;", source)
        self.assertIn("const PORT_DRAG_HIT_RADIUS = 14;", source)
        self.assertIn('r="${PORT_DRAG_HIT_RADIUS}"', source)
        self.assertIn('bodyX - BRANCH_HIT_PAD_X', source)
        self.assertNotIn('r="24" data-port-side', source)
        self.assertNotIn('bodyX - 48', source)
        self.assertNotIn('bodyWidth + 96', source)

    def test_switch_cases_store_constant_g_metadata_per_case(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function switchCaseFromBranch")
        end = source.index("function activeSwitchCase")
        switch_source = source[start:end]
        self.assertIn("item.gIsConstant = branch.gIsConstant !== false;", switch_source)
        self.assertIn("item.constantGSymbols = branch.constantGSymbols || \"\";", switch_source)
        self.assertIn("if (item.gIsConstant === undefined)", switch_source)
        self.assertIn("if (item.constantGSymbols === undefined)", switch_source)

    def test_constant_g_editor_reads_and_writes_active_case_metadata(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function branchGIsConstant", source)
        self.assertIn("function branchConstantGSymbolsText", source)

        editor_start = source.index("function constantGEditor")
        editor_end = source.index("function transformerTerminalDisplayName")
        editor_source = source[editor_start:editor_end]
        self.assertIn("branchGIsConstant(branch)", editor_source)
        self.assertIn("branchConstantGSymbolsText(branch)", editor_source)

        update_start = source.index("if (field === \"gIsConstant\")")
        update_end = source.index("if (field === \"width\" || field === \"height\")")
        update_source = source[update_start:update_end]
        self.assertIn("const activeCase = activeSwitchCase(branch);", update_source)
        self.assertIn("activeCase.gIsConstant", update_source)
        self.assertIn("activeCase.constantGSymbols", update_source)

    def test_packaged_network_cases_have_edit_mode_and_boundary_guard(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function normalizePackageNetworkCases", source)
        self.assertIn("function startPackageCaseEdit", source)
        self.assertIn("async function finishPackageCaseEdit", source)
        self.assertIn("state.packageCaseEdit", source)
        self.assertIn("validatePackageCaseExternalSignature", source)
        self.assertIn("packageCaseGroupSignature", source)
        self.assertIn("外部端口不一致，不能完成这个 Pack 工况", source)
        self.assertIn("内部节点不一致，不能完成这个 Pack 工况", source)
        self.assertIn("data-package-network-action=\"edit\"", source)
        self.assertIn("data-package-network-action=\"duplicate\"", source)
        self.assertIn("id=\"packageCaseEditOverlay\"", source)
        self.assertIn("function renderPackageCaseEditOverlay", source)
        self.assertIn("data-package-case-overlay-action=\"finish\"", source)
        self.assertIn("renderPackageCaseEditOverlay();", source)

    def test_dummy_dimension_branch_is_pack_edit_only_and_fixed(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("dummy_dimension_branch", source)
        self.assertIn("function createDummyDimensionBranch", source)
        self.assertIn("if (!state.packageCaseEdit) return;", source)
        self.assertIn("G_EPSILON", source)
        self.assertIn("dummyTerminalSide", source)
        self.assertIn("data-package-case-overlay-action=\"dummy\"", source)
        self.assertIn("if (isDummyTerminal(branch, portSide))", source)
        self.assertIn("function validateDummyBranchesForPackCase", source)
        self.assertIn("function buildDummyAdjustedPackReduction", source)
        self.assertIn("dummyAdjusted.reductionPayload", source)
        self.assertIn("dummyAdjusted.finalSubsystem", source)
        self.assertIn("dummyAdjusted.trimmedResult", source)

    def test_dummy_pack_case_preserves_super_stamp_and_separate_final_result(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("superExternalGroups", source)
        self.assertIn("superGMatrix", source)
        self.assertIn("finalExternalGroups", source)
        self.assertIn("finalGMatrix", source)
        self.assertIn("dummyAdjusted.superResult", source)
        self.assertIn("packageOriginal.externalGroups = structuredClone(active.superExternalGroups || active.externalGroups || [])", source)
        self.assertIn("branch.gMatrix = active.superGMatrix || active.gMatrix || branch.gMatrix || \"[]\"", source)

    def test_dummy_anchor_internal_toggle_is_blocked(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function dummyAnchorNetIds", source)
        self.assertIn("dummyProtectedNetIds().has(id)", source)
        self.assertIn("Dummy anchor 不能被设为内部节点。", source)
        self.assertIn("Dummy anchor cannot be marked as an internal node.", source)

    def test_packaged_dummy_external_port_inherits_dummy_interaction_rules(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function packagedDummyPortSides", source)
        self.assertIn("function packagedDummyAnchorPortSides", source)
        self.assertIn("function isPackagedDummyTerminal", source)
        self.assertIn("return Boolean(branch && (isDummyTerminal(branch, parsed.side) || isPackagedDummyTerminal(branch, parsed.side)));", source)
        self.assertIn("function dummyNetIds", source)
        self.assertIn("dummyNetIds().has(id)", source)
        self.assertIn("packagedDummyPortSides(branch).has(side)", source)
        self.assertIn("packagedDummyAnchorPortSides(branch).has(side)", source)
        self.assertIn("node-dummy-badge", source)
        self.assertIn("function sanitizeDummyProtectedInternalNodes", source)
        self.assertIn("const protectedIds = dummyProtectedNetIds();", source)
        self.assertGreaterEqual(source.count("sanitizeDummyProtectedInternalNodes();"), 2)

    def test_dummy_connected_anchor_net_cannot_render_as_internal(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function dummyProtectedNetIds", source)
        self.assertIn("const protectedIds = dummyProtectedNetIds();", source)
        self.assertIn("const dummyProtectedIds = dummyProtectedNetIds();", source)
        self.assertIn("!dummyProtectedIds.has(group.id)", source)
        self.assertIn("dummyProtectedIds.has(group.id) ? \"disabled\" :", source)
        self.assertIn("dummyProtectedNetIds().has(id)", source)

    def test_pack_case_edit_overlay_shows_required_external_ports(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function packageCasePortRows", source)
        self.assertIn("function packageCasePortMismatchMessage", source)
        self.assertIn("Pack 外部端口必须保持", source)
        self.assertIn("Required Pack external ports", source)
        self.assertIn("Expected:", source)
        self.assertIn("Current:", source)
        self.assertIn("packageCasePortRows(shell.packageOriginal?.externalGroups || [])", source)
        self.assertIn("packageCasePortRows(subsystem.externalGroups || [])", source)
        self.assertIn("renderPackageCasePortReminder(shell)", source)

    def test_packaged_network_cases_participate_in_multicase_export(self):
        source = Path("index.html").read_text(encoding="utf-8")

        normalize_start = source.index("function normalizeSwitchCases")
        normalize_end = source.index("function activeSwitchCase")
        normalize_source = source[normalize_start:normalize_end]
        self.assertIn("normalizePackageNetworkCases(branch);", normalize_source)
        package_block = normalize_source[
            normalize_source.index('if (branch?.packageOriginal) {'):
            normalize_source.index('if (!Array.isArray(branch.switchCases)')
        ]
        self.assertNotIn("delete branch.switchCases", package_block)

        eligible_start = source.index("function multiCaseEligibleBranches")
        eligible_end = source.index("function defaultMultiCaseProfiles")
        eligible_source = source[eligible_start:eligible_end]
        self.assertIn("normalizeSwitchCases(branch);", eligible_source)
        self.assertIn("branch.switchCases.length > 1", eligible_source)

    def test_packaged_dummy_metadata_is_forwarded_to_multicase_export(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function dummyFinalizationForCaseProfile(profile, payload)", source)
        self.assertIn("payload?.direct_retained_stamps || []", source)
        self.assertIn("support_nodes || []", source)
        self.assertIn("const portIndex = (branch.ports || []).indexOf(port);", source)
        self.assertIn("if (portIndex >= 0 && supportNodes[portIndex]) return supportNodes[portIndex];", source)
        self.assertIn("active.superExternalGroups || active.externalGroups || []", source)
        self.assertIn("dummyBranchesForPackCase(sourceBranches).forEach(dummy =>", source)
        self.assertIn("dummy_node: nodeForPackPort(branch, dummyGroup.port, dummyGroup.display)", source)
        self.assertIn("anchor_node: nodeForPackPort(branch, anchorGroup.port, anchorGroup.display)", source)
        self.assertIn("conductance: dummy.g || \"G_EPSILON\"", source)
        self.assertIn("const dummy_finalization = dummyFinalizationForCaseProfile(profile, payload);", source)
        self.assertIn("...(dummy_finalization ? { dummy_finalization } : {})", source)
        self.assertIn("const dummy_finalization = dummyFinalizationForCaseProfile(activeDummyFinalizationProfile(), payload);", source)
        self.assertIn("function activeDummyFinalizationProfile()", source)
        self.assertIn("dummy_finalization: payload.dummy_finalization", source)

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
