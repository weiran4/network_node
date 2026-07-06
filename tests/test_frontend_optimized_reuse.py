import re
import unittest
from pathlib import Path


class FrontendOptimizedReuseTests(unittest.TestCase):
    def test_optimized_request_reuses_reduced_dependency_cache(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("async function ensureReducedResult", source)
        self.assertIn("function reducedDependencyAnalysisFromResult", source)
        self.assertIn("function cachedReducedDependencyAnalysis", source)
        self.assertIn("function reducedDependencyAnalysisForOptimizedPayload", source)
        self.assertIn("await ensureReducedResult(payload)", source)
        self.assertIn("reduced_dependency_analysis", source)
        self.assertIn("const reducedDependency = cachedReducedDependencyAnalysis(basePayload)", source)
        self.assertIn("const reducedDependency = await reducedDependencyAnalysisForOptimizedPayload(basePayload)", source)

    def test_old_reduced_cache_keys_are_migrated_for_optimized_reuse(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function migratedReducedCacheKey", source)
        self.assertIn("parsed.version === REDUCED_CACHE_VERSION", source)
        self.assertIn("payload: parsed.payload", source)
        restore_start = source.index("function restoreReducedCacheSnapshot")
        restore_end = source.index("function restoreCacheSnapshot")
        restore_source = source[restore_start:restore_end]
        self.assertIn("const migratedKey = migratedReducedCacheKey(entry.key);", restore_source)
        self.assertIn("if (migratedKey) cacheSet(reducedCache, migratedKey, value);", restore_source)

    def test_project_import_clears_in_memory_derived_caches_before_restore(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function clearDerivedResultCaches", source)
        for cache_name in [
            "reducedCache",
            "branchCurrentCache",
            "pythonDraftCache",
            "optimizedEliminationCache",
            "multiCaseExportCache",
        ]:
            self.assertIn(f"{cache_name}.clear();", source)

        start = source.index("function applyProjectUiState")
        end = source.index("function appendCircuitState")
        apply_source = source[start:end]
        self.assertLess(
            apply_source.index("clearDerivedResultCaches();"),
            apply_source.index("restoreVersionedCacheSnapshot(optimizedEliminationCache"),
        )
        self.assertLess(
            apply_source.index("clearDerivedResultCaches();"),
            apply_source.index("restoreVersionedCacheSnapshot(multiCaseExportCache"),
        )

    def test_background_optimized_precompute_skips_without_reduced_dependency(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("async function precomputeReducedAndOptimized")
        end = source.index("function renderOptimizedControls")
        precompute_source = source[start:end]
        self.assertIn("const reducedDependency = cachedReducedDependencyAnalysis(basePayload);", precompute_source)
        self.assertIn("if (!reducedDependency) return;", precompute_source)
        self.assertNotIn("await ensureReducedResult(basePayload)", precompute_source)

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
        self.assertIn("Full Expression", result_source)
        self.assertIn("caseOwners", result_source)
        self.assertIn("promoted owner", result_source)
        self.assertIn("Mixed owners: RAM cases stay in RAM; CODE cases are active only under their case condition.", result_source)
        self.assertIn("按 case 条件启用的 GValue", result_source)
        self.assertIn("case_id is assumed fixed before simulation. Runtime case switching is not supported.", result_source)
        self.assertIn("启用条件", result_source)
        self.assertIn("gvalue_conditions", result_source)
        self.assertIn("uses_case_conditional_gvalue", result_source)
        self.assertNotIn("Template Reduction Summary", result_source)
        self.assertNotIn("Topology validation", result_source)

    def test_c_export_reuse_summary_is_visible_even_when_empty(self):
        source = Path("index.html").read_text(encoding="utf-8")

        optimized_start = source.index("function renderOptimizedGredEntryReuse")
        optimized_end = source.index("function renderOptimizedFormulaResult")
        optimized_source = source[optimized_start:optimized_end]
        self.assertIn("未发现可安全复用的 Gred entry", optimized_source)
        self.assertIn("No safe reusable Gred entries were detected", optimized_source)
        self.assertNotIn("if (!Array.isArray(items) || !items.length) return \"\";", optimized_source)

        multi_start = source.index("function renderMultiCaseGredEntryReuse")
        multi_end = source.index("function displayedMultiCaseCDraft")
        if multi_end < multi_start:
            multi_end = source.index("function renderMultiCaseCExportResult")
        multi_source = source[multi_start:multi_end]
        self.assertIn("未发现可安全复用的 Gred entry", multi_source)
        self.assertIn("No safe reusable Gred entries were detected", multi_source)
        self.assertNotIn("if (!visibleGroups.length) return \"\";", multi_source)

    def test_multi_case_export_shows_template_visualization(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function multiCaseCompactedEntryDefinitions")
        end = source.index("function renderMultiCaseCExportResult")
        visualization_source = source[start:end]

        self.assertIn("Multi-Case Template Visualization", visualization_source)
        self.assertIn("case-resolved aliases", visualization_source)
        self.assertIn("Compacted entry definitions", visualization_source)
        self.assertIn("G_total = G_core + G_direct", visualization_source)
        self.assertIn("Gfinal = Gschur + Gdirect", visualization_source)
        self.assertIn("Grr_core, Grk_core", visualization_source)
        self.assertIn("Gdirect, 0", visualization_source)
        self.assertIn("Grr_core - Grk_core*W*Gkr_core + Gdirect", visualization_source)
        self.assertIn("Grr_core", visualization_source)
        self.assertIn("template_direct_retained", visualization_source)
        self.assertIn("Gdirect_RAM", visualization_source)
        self.assertIn("Ihisdirect", visualization_source)
        self.assertIn("No-internal-node direct-only path", visualization_source)
        self.assertIn("Gred = Gdirect", visualization_source)
        self.assertIn("Ihisred_final", visualization_source)
        self.assertIn("Schur core / direct-retained block relation", visualization_source)
        self.assertIn("hasNonZeroMatrixValue", visualization_source)
        self.assertNotIn("hasMatrixValue", visualization_source)
        self.assertNotIn("hasVectorValue", visualization_source)
        self.assertIn("template_blocks", visualization_source)
        self.assertIn("Grk", visualization_source)
        self.assertIn("Gkr", visualization_source)
        self.assertIn("Gkk", visualization_source)
        self.assertIn("renderMultiCaseBlockMatrix", visualization_source)
        self.assertNotIn("renderMultiCaseAliasDefinitionLines", visualization_source)

    def test_multi_case_display_filters_dependency_category_c_comments(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function displayedMultiCaseCDraft")
        end = source.index("function renderMultiCaseCExportResult")
        filter_source = source[start:end]

        self.assertIn("no dependency category", filter_source)
        self.assertIn("displayedMultiCaseCDraft(result.multi_case?.c_draft)", source)
        self.assertNotIn('escapeHtml(result.multi_case?.c_draft || "")', source)

    def test_optimized_direct_retained_matrices_render_with_tagged_sources(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("renderMatrix(direct.Gred_direct, null, direct.Gred_direct_tagged)", source)
        self.assertIn("renderMatrix(direct.Gred_direct_ram, null, direct.Gred_direct_ram_tagged)", source)
        self.assertIn("renderMatrix(direct.Gred_direct_code, null, direct.Gred_direct_code_tagged)", source)

    def test_optimized_actual_block_uses_tagged_g_full_for_highlighting(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function blockMatrixCellTaggedValue", source)
        self.assertIn("return payload.G_full_tagged?.[row]?.[col] ?? null;", source)
        block_start = source.index("function renderFormulaModeActualBlock")
        block_end = source.index("function renderStructuredFormulaSummary")
        block_source = source[block_start:block_end]
        self.assertIn("const taggedValue = blockMatrixCellTaggedValue(payload, rowEntry, colEntry, nodeIndex);", block_source)
        self.assertIn("formatMathWithTagged(value, taggedValue)", block_source)
        self.assertNotIn(">${formatMath(value)}</span>", block_source)

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

        self.assertIn("alias-template-cartesian-profiles-runtime-v1", source)
        start = source.index("function ensureMultiCaseProfilesText")
        end = source.index("function parseMultiCaseProfiles")
        ensure_source = source[start:end]
        self.assertIn("const initBranches = initTimeMultiCaseBranches(branches);", ensure_source)
        self.assertIn("const generated = JSON.stringify(defaultMultiCaseProfiles(initBranches), null, 2);", ensure_source)
        self.assertIn("state.multiCaseProfilesText = generated;", ensure_source)
        self.assertIn("state.multiCaseProfilesKey = key;", ensure_source)

    def test_runtime_mutable_case_group_ui_and_payload_are_isolated_to_multicase_export(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('data-field="runtimeMutableCaseGroup"', source)
        self.assertIn("function initTimeMultiCaseBranches", source)
        self.assertIn("function runtimeMutableCaseBranches", source)
        self.assertIn("runtime_case_groups", source)
        self.assertIn("Runtime-mutable case 诊断", source)
        self.assertIn("runtime case id 变量", source)

    def test_runtime_mutable_case_ui_uses_status_card_and_locks_manual_g_constant_hint(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("runtime-case-card", source)
        self.assertIn("运行时工况切换", source)
        self.assertIn("只允许在 CODE 阶段切换数值表达式", source)
        self.assertIn("runtimeMutableGConstantNotice", source)
        self.assertIn("常数/变量归属由后端按所有 case 自动分析", source)
        self.assertIn("data-field=\"gIsConstant\" disabled", source)
        self.assertIn("data-field=\"constantGSymbols\" disabled", source)

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

    def test_multicase_cache_version_invalidates_stale_direct_residual_drafts(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn(
            'const MULTI_CASE_EXPORT_CACHE_VERSION = "multi-case-runtime-mutable-v5-direct-residual-split";',
            source,
        )
        self.assertNotIn("multi-case-runtime-mutable-v4-source-stage-split", source)

    def test_optimized_cache_version_invalidates_stale_source_cse_drafts(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn(
            'const OPTIMIZED_ELIMINATION_CACHE_VERSION = "optimized-c-export-v5-source-cse-static-assign";',
            source,
        )
        self.assertNotIn("optimized-c-export-v2-no-stale-template", source)

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
        self.assertIn('restoreVersionedCacheSnapshot(optimizedEliminationCache, data.optimizedEliminationCache || [], ["result"], OPTIMIZED_ELIMINATION_CACHE_VERSION)', load_source)
        self.assertIn('restoreVersionedCacheSnapshot(multiCaseExportCache, data.multiCaseExportCache || [], ["result"], MULTI_CASE_EXPORT_CACHE_VERSION)', load_source)

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

    def test_canvas_supports_cut_shortcut_without_text_editor_interception(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function cutSelectedBranches", source)
        self.assertIn('copySelectedBranches({ silent: true })', source)
        self.assertIn('event.key.toLowerCase() === "x"', source)
        self.assertIn("if (isTextEditingTarget(event.target)) return;", source)
        self.assertIn('optText(`已剪切 ${count} 个元件`', source)
        self.assertIn('`Cut ${count} elements`', source)

    def test_canvas_copy_shortcuts_do_not_intercept_selected_output_text(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function hasReadableTextSelection", source)
        keydown_start = source.index('document.addEventListener("keydown"')
        keydown_end = source.index("function escapeHtml", keydown_start)
        keydown_source = source[keydown_start:keydown_end]
        self.assertIn('if ((event.ctrlKey || event.metaKey) && hasReadableTextSelection() && (event.key.toLowerCase() === "c" || event.key.toLowerCase() === "x")) return;', keydown_source)
        self.assertLess(
            keydown_source.index('if ((event.ctrlKey || event.metaKey) && hasReadableTextSelection()'),
            keydown_source.index('if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c")'),
        )

    def test_formula_output_cards_do_not_force_max_content_width(self):
        source = Path("index.html").read_text(encoding="utf-8")

        list_start = source.index(".formula-list {")
        list_end = source.index(".output-body-formulas .formula-list")
        list_css = source[list_start:list_end]
        self.assertIn("width: 100%;", list_css)
        self.assertIn("min-width: 0;", list_css)
        self.assertNotIn("width: max-content;", list_css)

        card_start = source.index(".formula-card {")
        card_end = source.index(".output-body-formulas .formula-card")
        card_css = source[card_start:card_end]
        self.assertIn("width: auto;", card_css)
        self.assertIn("max-width: 100%;", card_css)
        self.assertIn("overflow-x: auto;", card_css)
        self.assertNotIn("width: max-content;", card_css)
        self.assertIn('"关闭": "Close"', source)
        self.assertIn('"全屏": "Fullscreen"', source)
        self.assertIn('"退出全屏": "Exit Fullscreen"', source)
        self.assertIn('"拖动排序": "Drag to reorder"', source)
        self.assertIn('"切换外部/内部节点": "Toggle external/internal node"', source)
        self.assertIn('button.title = tr(active ? "退出全屏" : "全屏");', source)
        self.assertIn('const rowInternalButtonTitle = group =>', source)
        self.assertIn('return tr("切换外部/内部节点");', source)
        self.assertIn('title="${escapeHtml(rowInternalButtonTitle(group))}"', source)
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
        self.assertIn('`Pack failed: ${localizedBackendErrorMessage(error)}`', source)
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

    def test_multi_case_g_constant_editor_has_batch_controls(self):
        source = Path("index.html").read_text(encoding="utf-8")

        editor_start = source.index("function constantGEditor")
        editor_end = source.index("function transformerTerminalDisplayName")
        editor_source = source[editor_start:editor_end]
        self.assertIn("branch.switchCases.length > 1", editor_source)
        self.assertIn("定制 case", editor_source)
        self.assertIn('select data-field="switchActive"', editor_source)
        self.assertIn("displayCaseName(item, index)", editor_source)
        self.assertIn('data-case-gconst-action="allConstant"', editor_source)
        self.assertIn('data-case-gconst-action="allVariable"', editor_source)
        self.assertIn('data-case-gconst-action="applyCurrent"', editor_source)
        self.assertIn("isRuntimeMutableCaseGroup(branch)", editor_source)

        self.assertIn("function bindCaseGConstButtons", source)
        self.assertIn("function applyCaseGConstBatch", source)
        batch_start = source.index("function applyCaseGConstBatch")
        batch_end = source.index("function alignPortRoots")
        batch_source = source[batch_start:batch_end]
        self.assertIn("branch.switchCases.forEach", batch_source)
        self.assertIn("item.gIsConstant = Boolean(isConstant);", batch_source)
        self.assertIn('item.constantGSymbols = "";', batch_source)
        self.assertIn("renderAfterSidePanelEdit();", batch_source)

    def test_custom_n_port_defaults_to_constant_g(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function createCustomNPort")
        end = source.index("function openCustomNPortDialog")
        create_source = source[start:end]
        self.assertIn("gIsConstant: true,", create_source)
        self.assertNotIn("gIsConstant: false,", create_source)

    def test_packaged_network_cases_have_edit_mode_and_boundary_guard(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function normalizePackageNetworkCases", source)
        self.assertIn("function startPackageCaseEdit", source)
        self.assertIn("async function finishPackageCaseEdit", source)
        self.assertIn("state.packageCaseEdit", source)
        self.assertIn("validatePackageCaseExternalSignature", source)
        self.assertIn("packageCaseGroupSignature", source)
        self.assertIn("外部端口不一致，不能完成这个 Pack 工况", source)
        start = source.index("function validatePackageCaseExternalSignature")
        end = source.index("function dummyBranchesForPackCase", start)
        guard_source = source[start:end]
        self.assertIn("packageCasePortMismatchMessage", guard_source)
        self.assertIn("externalGroups", guard_source)
        self.assertNotIn("internalGroups", guard_source)
        self.assertIn("data-package-network-action=\"edit\"", source)
        self.assertIn("data-package-network-action=\"duplicate\"", source)
        self.assertIn("id=\"packageCaseEditOverlay\"", source)
        self.assertIn("function renderPackageCaseEditOverlay", source)
        self.assertIn("data-package-case-overlay-action=\"finish\"", source)
        self.assertIn("renderPackageCaseEditOverlay();", source)

    def test_packaged_network_case_name_is_independently_editable(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('"Pack 工况名称": "Pack Case Name"', source)
        self.assertIn('editableField("Pack 工况名称", "packageNetworkCaseName"', source)
        self.assertIn('if (field === "packageNetworkCaseName")', source)
        self.assertIn("pkg.networkCases[index].name = name;", source)
        self.assertIn("branch.switchCases[index].name = name;", source)
        self.assertIn('if (field === "packageNetworkCaseName") return', source)

    def test_legacy_dummy_dimension_branch_is_compat_only_not_a_user_action(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("dummy_dimension_branch", source)
        self.assertIn("function createDummyDimensionBranch", source)
        self.assertIn("if (!state.packageCaseEdit) return;", source)
        self.assertIn("G_EPSILON", source)
        self.assertIn("dummyTerminalSide", source)
        self.assertNotIn("data-package-case-overlay-action=\"dummy\"", source)
        self.assertNotIn("新增 Dummy 支路", source)
        self.assertIn("if (isDummyTerminal(branch, portSide))", source)
        self.assertIn("function validateDummyBranchesForPackCase", source)
        self.assertIn("const internalGroupIds = new Set((subsystem.internalGroups || []).map(group => group.id));", source)
        self.assertIn("function buildDummyAdjustedPackReduction", source)
        self.assertIn("dummyAdjusted.reductionPayload", source)
        self.assertIn("dummyAdjusted.finalSubsystem", source)
        self.assertIn("dummyAdjusted.trimmedResult", source)

    def test_dummy_node_block_is_pack_edit_only_and_forwarded_to_multicase(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('"新增 N-Dummy 节点块": "Add N-Dummy Node Block"', source)
        self.assertIn("dummy_node_block", source)
        self.assertIn("function createDummyNodeBlock", source)
        self.assertIn("function createDummyNodeBlock(x = state.viewCenter.x, y = state.viewCenter.y, count = 1)", source)
        self.assertIn("N-Dummy 节点块只能在 Pack 工况编辑中新增。", source)
        self.assertNotIn("window.prompt(tr(\"N-Dummy 节点数量\")", source)
        self.assertIn("data-package-case-overlay-action=\"dummyNodeBlock\"", source)
        self.assertIn("function dummyNodeBlocksForPackCase", source)
        self.assertIn("function dummyNodeBlockPayloadForCase", source)
        self.assertIn("dummy_node_blocks", source)
        self.assertIn("isolated_dummy_internal", source)
        self.assertIn("Forced elimination", source)
        self.assertIn("Voltage recovery: No", source)

    def test_dummy_node_block_keeps_canvas_port_role_until_reduced_payload(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function dummyNodeBlocksForReducedPayload", source)
        self.assertIn("payload.dummy_node_blocks = dummyNodeBlocks", source)
        self.assertNotIn("internal: isolatedDummyInternal || state.internalNodes.includes(globalNet)", source)
        self.assertNotIn("isolatedDummyIds.has(group.id) || group.virtualInternal || state.internalNodes.includes(group.id)", source)
        self.assertIn("const rowInternal = group => !rowGround(group) && !dummyProtectedIds.has(group.id) && (group.virtualInternal || state.internalNodes.includes(group.id));", source)

    def test_dummy_node_block_never_generates_branch_current_observer(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('if (branch.kind === "dummy_node_block") return "";', source)
        self.assertIn('if (branch.kind === "dummy_node_block") return [];', source)
        self.assertIn('if (inner.kind === "dummy_node_block") return [];', source)

    def test_packaged_dummy_node_block_g_constant_property_is_locked(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('function isFixedDummyGElement(branch)', source)
        self.assertIn('if (isFixedDummyGElement(inner)) return false;', source)
        self.assertIn('if (isFixedDummyGElement(inner)) {', source)
        self.assertIn('inner.gIsConstant = true;', source)
        self.assertIn('inner.constantGSymbols = "G_EPSILON";', source)

    def test_packaged_n_dummy_ports_are_marked_and_payload_uses_current_groups(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('...dummyNodeBlocksForPackCase(sourceBranches).flatMap(item => terminalSides(item).map(side => terminalKey(item.id, side)))', source)
        self.assertIn('const derivedBlocks = dummyNodeBlockPayloadForCase(sourceBranches, groups);', source)
        self.assertIn('const sourceBranchId = block.source_branch_id || block.sourceBranchId || block.block_id || "";', source)
        self.assertIn('const match = groupRefs.find(item => item.group.id === node.node_id)', source)
        self.assertIn('display_name: nodeDisplayName(payloadNode) || group.display || node.display_name || payloadNode', source)
        self.assertIn('dummyProtectedIds.has(group.id) || groupHasDummyTerminal(group)', source)

    def test_pack_case_edit_restores_canvas_collection_after_save_or_cancel(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("returnCanvases: structuredClone(state.canvases)", source)
        self.assertIn("returnActiveCanvasId: state.activeCanvasId", source)
        self.assertIn("restorePackageCaseEditOuterCanvas(edit);", source)
        self.assertIn("function restorePackageCaseEditOuterCanvas(edit)", source)
        self.assertIn("state.canvases = structuredClone(edit.returnCanvases || state.canvases);", source)
        self.assertIn("saveActiveCanvas();", source)

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
        self.assertIn("function isDummyNodeBlockTerminal", source)
        self.assertIn("isDummyTerminal(branch, parsed.side) || isDummyNodeBlockTerminal(branch, parsed.side) || isPackagedDummyTerminal(branch, parsed.side)", source)
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
        self.assertIn("const rowInternalButtonAttr = group =>", source)
        self.assertIn("dummyProtectedIds.has(group.id)) return \"disabled\";", source)
        self.assertIn("rowInternalButtonAttr(group)", source)
        self.assertIn("dummyProtectedNetIds().has(id)", source)

    def test_pack_case_edit_overlay_shows_required_external_ports(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function packageCasePortRows", source)
        self.assertIn("function packageCasePortIdentity", source)
        self.assertIn("function packageCasePortDisplayName", source)
        self.assertIn("function packageCasePortDebugLabel", source)
        self.assertIn("function packageCasePortMismatchMessage", source)
        self.assertIn("Pack 外部节点必须保持", source)
        self.assertIn("Required Pack external nodes", source)
        self.assertIn("=> `${index + 1}. ${packageCasePortDisplayName(group, index)}`", source)
        self.assertIn("packageCasePortIdentity(group, index)", source)
        self.assertIn("Pack 外部端口必须保持原来的槽位、顺序和名称：", source)
        self.assertIn("当前不一致：", source)
        self.assertIn('console.warn("Pack external port mismatch"', source)
        self.assertIn("需要端口身份：", source)
        self.assertIn("当前端口身份：", source)
        self.assertIn("端口身份是后端固定 ID，用来判断外部端口槽位和顺序；即使节点显示名相同，端口身份变了也不能保存。", source)
        self.assertIn("Port identity is the fixed backend ID used to validate external port slots and order; saving is blocked if it changes even when the displayed node name is unchanged.", source)
        self.assertIn("第 ${index + 1} 个外部端口应为 ${expectedName}（端口身份 ${expectedId}），当前为 ${currentName}（端口身份 ${currentId}）", source)
        self.assertIn("External port ${index + 1} should be ${expectedName} (port identity ${expectedId}); current is ${currentName} (port identity ${currentId})", source)
        self.assertIn("需要保持：", source)
        self.assertIn("当前为：", source)
        self.assertNotIn("return `${netId}. ${name}`;", source)
        self.assertNotIn("return `${index + 1}. ${name} -> ${port}`;", source)
        self.assertIn("packageCasePortRows(shell.packageOriginal?.externalGroups || [])", source)
        self.assertIn("packageCasePortMismatchMessage(branch.packageOriginal?.externalGroups || [], subsystem.externalGroups || []", source)
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

    def test_pack_case_profile_payload_forwards_final_retained_fields(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function finalRetainedFieldsForSinglePackPayload(payload)", source)
        self.assertIn("packagedBranches.length !== 1", source)
        self.assertIn("state.branches.length !== 1", source)
        self.assertIn("finalExternalGroups", source)
        self.assertIn("finalGMatrix", source)
        self.assertIn("finalIhisVector", source)

        payload_start = source.index("function payloadForCaseProfile")
        payload_end = source.index("function dummyFinalizationForCaseProfile")
        payload_source = source[payload_start:payload_end]
        self.assertIn("const payload = optimizedEliminationPayload({ deferDummyNodeBlocks: true });", payload_source)
        self.assertIn("...finalRetainedFieldsForSinglePackPayload(payload)", payload_source)

    def test_pack_case_edit_preserves_saved_node_order(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("nodeOrder: structuredClone(pkg.nodeOrder || [])", source)
        self.assertIn("pkg.nodeOrder = structuredClone(active.nodeOrder || [])", source)
        self.assertIn("nodeOrder: structuredClone(currentNodeOrder())", source)
        self.assertIn("state.nodeOrder = structuredClone(caseData.nodeOrder || [])", source)

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

    def test_optimized_dummy_pruning_notice_is_localized(self):
        source = Path("index.html").read_text(encoding="utf-8")

        start = source.index("function renderOptimizedWarningBox")
        end = source.index("function localizedBackendWarning")
        warning_source = source[start:end]

        self.assertIn("optimizedDummyPrunedNodes(result, payload)", warning_source)
        self.assertIn("N-Dummy isolated nodes were validated and pruned before Schur reduction / C export", warning_source)
        self.assertIn("N-Dummy 孤立节点已验证，并在 Schur 消元 / C 导出前裁掉", warning_source)

        helper_start = source.index("function optimizedDummyPrunedNodeInfo")
        helper_end = source.index("function renderDummyPrunedNotice")
        helper_source = source[helper_start:helper_end]
        self.assertIn("payload?.node_display_names", helper_source)
        self.assertIn("displayToNode", helper_source)
        self.assertIn("dropped_before_schur", helper_source)
        self.assertIn("dummyBlocks.common_internal_nodes", helper_source)
        self.assertIn("structured.analysis?.dummy_nodes", helper_source)
        self.assertIn("structured.details?.dummy_nodes", helper_source)
        self.assertIn("structured.finalization_profiles", helper_source)
        self.assertIn("multi.finalization_profiles", helper_source)
        self.assertIn("multi.diagnostics?.mixed_physical_dummy_internal", helper_source)
        self.assertIn("payload?.dummy_node_blocks", helper_source)

        block_start = source.index("function renderFormulaModeActualBlock")
        block_end = source.index("function renderStructuredFormulaSummary")
        block_source = source[block_start:block_end]
        self.assertIn("dummy-pruned-cell", block_source)
        self.assertIn("renderDummyPrunedNotice(result, displayNames, payload)", block_source)

        structured_start = source.index("function renderStructuredBlocks")
        structured_end = source.index("function renderPureDiagonalStructured")
        structured_source = source[structured_start:structured_end]
        self.assertIn("sourceInternalOrder.some(node => dummyPrunedSet.has(String(node)))", structured_source)
        self.assertIn("internal_nodes: previewInternalOrder", structured_source)
        self.assertIn("这些 N-Dummy 行/列只用于验证原始矩阵", source)
        self.assertIn("These N-Dummy rows/columns are shown for source-matrix validation", source)

        self.assertIn(".actual-block-cell.dummy-pruned-cell", source)
        self.assertIn(".actual-block-dummy-pruned-notice", source)

    def test_right_panel_has_resizable_width_controls(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("id=\"panelResizeBar\"", source)
        self.assertIn("--panel-width", source)
        self.assertIn("function applyPanelWidth", source)
        self.assertIn("function startPanelResize", source)
        self.assertIn("panelResizeBar.addEventListener(\"pointerdown\", startPanelResize)", source)

    def test_node_exposure_controls_use_unambiguous_labels(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn('"取消暴露": "Unexpose"', source)
        self.assertIn('"未暴露": "Unexposed"', source)
        self.assertIn('tr("未暴露")', source)
        self.assertIn('rowExposure(group).exposed ? "取消暴露" : "暴露"', source)

    def test_output_modal_keeps_wide_optimized_blocks_locally_scrollable(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn(".modal-output-body .formula-list", source)
        self.assertIn(".modal-output-body .formula-card", source)
        self.assertIn(".modal-output-body .actual-block-preview", source)
        self.assertIn("overflow-x: auto;", source[source.index(".modal-output-body .formula-card"):source.index(".output .formula-card > .formula-name:first-child")])
        self.assertIn("width: 100%;", source[source.index(".modal-output-body .actual-block-preview"):source.index(".actual-block-column-labels")])

    def test_drag_end_auto_glues_when_at_least_one_terminal_is_floating(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("const AUTO_GLUE_FLOATING_TERMINAL_DISTANCE = 20;", source)
        self.assertIn("function terminalIsFloatingForAutoGlue", source)
        self.assertIn("function terminalCanReceiveAutoGlue", source)
        self.assertIn("function autoGlueConnectionForPair", source)
        self.assertIn("if (terminalIsDummyNode(terminal)) return false;", source)
        self.assertIn("if (state.wires.some(wire => wire.from === terminal || wire.to === terminal)) return false;", source)
        self.assertIn("return group.length === 1;", source)
        self.assertIn("function nearestFloatingTerminalToGlue", source)
        self.assertIn("function nearestWireTerminalToGlue", source)
        self.assertIn("function distancePointToSegment", source)
        self.assertIn("const terminalFloating = terminalIsFloatingForAutoGlue(terminal, lookup, groups);", source)
        self.assertIn("const candidateFloating = terminalIsFloatingForAutoGlue(candidate, lookup, groups);", source)
        self.assertIn("if (!terminalFloating && !candidateFloating) return null;", source)
        self.assertIn("if (!connection) return;", source)
        self.assertIn("if (Math.abs(distance - best.distance) < 0.001) best.ambiguous = true;", source)
        self.assertIn("function autoGlueDraggedFloatingTerminals", source)
        self.assertIn("connectTerminals(match.connection.from, match.connection.to);", source)

        pointerup_start = source.index('window.addEventListener("pointerup"')
        pointerup_source = source[pointerup_start:source.index("state.drag = null;", pointerup_start)]
        self.assertIn("autoGlueDraggedFloatingTerminals(Object.keys(state.drag.origins));", pointerup_source)

    def test_auto_glue_can_target_existing_wire_segments(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function nearestWireTerminalToGlue", source)
        self.assertIn("if (!terminalIsFloatingForAutoGlue(terminal, lookup, groups)) return null;", source)
        self.assertIn("const mid = snappedWireMid(wire, wire.mid ||", source)
        self.assertIn("distancePointToSegment(point, p1, mid)", source)
        self.assertIn("distancePointToSegment(point, mid, p2)", source)
        self.assertIn("return nearestWireTerminalToGlue(terminal, excludedBranchIds);", source)
        self.assertIn("best = { terminal: target.terminal, connection: { from: terminal, to: target.terminal }, distance, ambiguous: false };", source)

    def test_auto_glue_feedback_previews_and_confirms_connection(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn(".terminal.auto-glue-preview", source)
        self.assertIn(".terminal.auto-glue-flash", source)
        self.assertIn(".auto-glue-feedback-line", source)
        self.assertIn("autoGluePreview: null", source)
        self.assertIn("autoGlueFlash: null", source)
        self.assertIn("function updateAutoGluePreview", source)
        self.assertIn("function showAutoGlueFlash", source)
        self.assertIn("已自动连接悬空端点", source)
        self.assertIn("state.autoGlueFlashTimer = window.setTimeout", source)
        self.assertIn("function renderAutoGlueFeedbackLines", source)
        self.assertIn("function applyAutoGlueTerminalFeedback", source)
        self.assertIn("updateAutoGluePreview(Object.keys(state.drag.origins));", source)

    def test_internal_node_changes_invalidate_math_caches(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("state.internalNodes = state.internalNodes.filter(item => item !== id);\n          }\n          invalidateMathCaches();", source)
        self.assertIn("state.nodeOrder = Array.from(list.querySelectorAll(\".node-order-item[data-node-id]\")).map(row => row.dataset.nodeId);\n          invalidateMathCaches();", source)

    def test_backend_error_messages_use_display_language(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function localizedBackendErrorMessage(error)", source)
        self.assertIn('const bilingualSeparator = " / ";', source)
        self.assertIn("raw.lastIndexOf(bilingualSeparator)", source)
        self.assertIn("return translateText(raw);", source)

        optimized_start = source.index("async function renderOptimizedEliminationAsync")
        optimized_source = source[optimized_start:source.index("function packagedObserverEntries", optimized_start)]
        self.assertIn("localizedBackendErrorMessage(error)", optimized_source)
        self.assertNotIn("error.message || String(error)", optimized_source)

        reduced_start = source.index("async function renderReducedEquationsAsync")
        reduced_source = source[reduced_start:source.index("function currentReducedHtml", reduced_start)]
        self.assertIn("localizedBackendErrorMessage(error)", reduced_source)
        self.assertNotIn("节点消去失败：${escapeHtml(error.message", reduced_source)


if __name__ == "__main__":
    unittest.main()
