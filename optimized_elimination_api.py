from __future__ import annotations

import json
import itertools
import re
import sys
import time
from collections.abc import Iterable, Mapping, Sequence

import sympy as sp

from elimination import eliminate_internal_nodes
from nodal_tool.ground import apply_ground_constraint, validate_ground_partition
from nodal_tool.optimized_elimination import (
    GredEntryReuse,
    _split_matrix_ram_and_code_terms,
    build_dependency_stage_plan,
    build_structured_formula,
    c_draft_for_structured_formula,
    structural_gred_entry_reuse_plan,
)
from nodal_tool.multicase_finalization_profiles import (
    build_finalization_profiles,
    finalize_profile_result,
)
from nodal_tool.dummy_node_block_model import (
    dummy_node_blocks_from_payload,
    validate_dummy_node_blocks,
)


_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_]\w*\b")
_SYMPY_FUNCTIONS = {
    "Abs",
    "acos",
    "asin",
    "atan",
    "cos",
    "cosh",
    "exp",
    "log",
    "sin",
    "sinh",
    "sqrt",
    "tan",
    "tanh",
}


def _symbol_locals(text: str) -> dict[str, sp.Symbol]:
    names = set(_IDENTIFIER_RE.findall(text))
    return {name: sp.Symbol(name) for name in names if name not in _SYMPY_FUNCTIONS}


def _parse_expr(text: str) -> sp.Expr:
    cleaned = str(text or "0").strip()
    if not cleaned:
        return sp.Integer(0)
    return sp.sympify(cleaned, locals=_symbol_locals(cleaned))


def _parse_matrix(rows: list[list[str]]) -> sp.Matrix:
    return sp.Matrix([[_parse_expr(item) for item in row] for row in rows])


def _parse_vector(items: list[str]) -> sp.Matrix:
    return sp.Matrix([[_parse_expr(item)] for item in items])


def _clean_expr(expr: sp.Expr) -> str:
    return str(expr)


def _clean_matrix(matrix: sp.Matrix) -> list[list[str]]:
    matrix = sp.Matrix(matrix)
    return [[_clean_expr(matrix[row, col]) for col in range(matrix.cols)] for row in range(matrix.rows)]


def _clean_vector(matrix: sp.Matrix) -> list[str]:
    matrix = sp.Matrix(matrix)
    return [_clean_expr(matrix[row, 0]) for row in range(matrix.rows)]


def _node_label(nodes: Sequence[str], index: int) -> str:
    if 0 <= int(index) < len(nodes):
        return str(nodes[int(index)])
    return f"N{int(index) + 1}"


def _gred_entry_label(nodes: Sequence[str], row: int, col: int) -> str:
    return f"Gred[{_node_label(nodes, row)},{_node_label(nodes, col)}]"


def _clean_gred_entry_reuse(items: Sequence, external_nodes: Sequence[str]) -> list[dict]:
    cleaned: list[dict] = []
    for item in items or []:
        try:
            sign = int(item.sign)
            target = [int(item.target_row), int(item.target_col)]
            base = [int(item.base_row), int(item.base_col)]
        except Exception:
            continue
        sign_prefix = "-" if sign < 0 else ""
        target_label = _gred_entry_label(external_nodes, target[0], target[1])
        base_label = _gred_entry_label(external_nodes, base[0], base[1])
        cleaned.append(
            {
                "target": target,
                "target_nodes": [_node_label(external_nodes, target[0]), _node_label(external_nodes, target[1])],
                "base": base,
                "base_nodes": [_node_label(external_nodes, base[0]), _node_label(external_nodes, base[1])],
                "sign": sign,
                "relation": "opposite" if sign < 0 else "same",
                "target_label": target_label,
                "base_label": base_label,
                "text": f"{target_label} = {sign_prefix}{base_label}",
            }
        )
    return cleaned


def _clean_gred_entry_reuse_by_case(
    reuse_plan_by_case: Mapping[int, Sequence],
    profiles: Sequence[Mapping],
    external_nodes: Sequence[str],
) -> list[dict]:
    profile_by_index = {
        int(profile.get("index", index)): profile
        for index, profile in enumerate(profiles or [])
    }
    out: list[dict] = []
    for case_index in sorted(reuse_plan_by_case or {}):
        items = _clean_gred_entry_reuse(reuse_plan_by_case.get(case_index) or [], external_nodes)
        if not items:
            continue
        profile = profile_by_index.get(int(case_index), {})
        out.append(
            {
                "case_index": int(case_index),
                "case_name": str(profile.get("name") or f"case {case_index}"),
                "case_comment": str(profile.get("comment") or ""),
                "items": items,
            }
        )
    return out


def _clean_gred_entry_reuse_by_case_nodes(
    reuse_plan_by_case: Mapping[int, Sequence],
    profiles: Sequence[Mapping],
    nodes_by_case: Mapping[int, Sequence[str]],
) -> list[dict]:
    profile_by_index = {
        int(profile.get("index", index)): profile
        for index, profile in enumerate(profiles or [])
    }
    out: list[dict] = []
    for case_index in sorted(reuse_plan_by_case or {}):
        nodes = [str(node) for node in (nodes_by_case.get(int(case_index)) or [])]
        items = _clean_gred_entry_reuse(reuse_plan_by_case.get(case_index) or [], nodes)
        if not items:
            continue
        profile = profile_by_index.get(int(case_index), {})
        out.append(
            {
                "case_index": int(case_index),
                "case_name": str(profile.get("name") or f"case {case_index}"),
                "case_comment": str(profile.get("comment") or ""),
                "items": items,
            }
        )
    return out


def _clean_steps(steps: list[dict]) -> list[dict]:
    out = []
    for step in steps:
        out.append(
            {
                "node": step["node"],
                "pivot": _clean_expr(step.get("pivot", 0)),
                "rest_nodes": list(step.get("rest_nodes", [])),
                "G_row": [_clean_expr(item) for item in step.get("G_row", [])],
                "G_col": [_clean_expr(item) for item in step.get("G_col", [])],
                "Ihis": _clean_expr(step.get("Ihis", 0)),
                "coefficients": [_clean_expr(item) for item in step.get("coefficients", [])],
                "source": _clean_expr(step.get("source", 0)),
            }
        )
    return out


def _clean_asymmetric(entries: list[tuple]) -> list[dict]:
    return [
        {"row": row, "col": col, "upper": _clean_expr(upper), "lower": _clean_expr(lower)}
        for row, col, upper, lower in entries
    ]


def _clean_value(value):
    if isinstance(value, sp.MatrixBase):
        return _clean_matrix(value)
    if isinstance(value, sp.Expr):
        return _clean_expr(value)
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    return value


def _ccode(expr: object) -> str:
    parsed = expr if isinstance(expr, sp.Basic) else _parse_expr(str(expr))
    code = sp.ccode(parsed).replace("M_PI", "PI")
    return re.sub(r"(?<![eE][+-])(?<![\w.])(\d+)(?![\w.])", r"\1.0", code)


def _ensure_static_blank_line(draft: str) -> str:
    return re.sub(r"(?m)^STATIC:\n(?!\n)", "STATIC:\n\n", draft)


def _reorder_rows(matrix: sp.Matrix, source_nodes: list[str], target_nodes: list[str]) -> sp.Matrix:
    matrix = sp.Matrix(matrix)
    if not target_nodes:
        return sp.zeros(0, matrix.cols if matrix.cols else 0)
    index_by_node = {node: index for index, node in enumerate(source_nodes)}
    rows = [index_by_node[node] for node in target_nodes if node in index_by_node]
    if len(rows) != len(target_nodes):
        return matrix
    return matrix.extract(rows, list(range(matrix.cols)))


def _reorder_square_matrix(matrix: sp.Matrix, source_nodes: list[str], target_nodes: list[str]) -> sp.Matrix:
    matrix = sp.Matrix(matrix)
    if not target_nodes:
        return sp.zeros(0, 0)
    index_by_node = {node: index for index, node in enumerate(source_nodes)}
    indices = [index_by_node[node] for node in target_nodes if node in index_by_node]
    if len(indices) != len(target_nodes):
        return matrix
    return matrix.extract(indices, indices)


def _reorder_vector(matrix: sp.Matrix, source_nodes: list[str], target_nodes: list[str]) -> sp.Matrix:
    matrix = _parse_vector(matrix) if isinstance(matrix, list) else sp.Matrix(matrix)
    if not target_nodes:
        return sp.zeros(0, 1)
    index_by_node = {node: index for index, node in enumerate(source_nodes)}
    rows = [index_by_node[node] for node in target_nodes if node in index_by_node]
    if len(rows) != len(target_nodes):
        return matrix
    return matrix.extract(rows, [0])


def _reduced_dependency_model(result, effective_internal_nodes: list[str], W_value) -> dict:
    K_v = _reorder_rows(result.K_v, result.internal_nodes, effective_internal_nodes)
    K_h = _reorder_rows(result.K_h, result.internal_nodes, effective_internal_nodes)
    return {
        "Gred": result.G_red,
        "Ihisred": result.Ihis_red,
        "W": W_value,
        "Kv": K_v,
        "Kh": K_h,
    }


def _borrowed_reduced_dependency_model(
    borrowed: dict,
    external_nodes: list[str],
    effective_internal_nodes: list[str],
    W_value,
    *,
    tagged: bool = False,
) -> dict | None:
    if not borrowed:
        return None
    g_key = "G_red_tagged" if tagged else "G_red"
    ihis_key = "Ihis_red_tagged" if tagged else "Ihis_red"
    if borrowed.get(g_key) is None or borrowed.get(ihis_key) is None:
        return None
    source_external = list(borrowed.get("external_nodes") or external_nodes)
    Gred = _reorder_square_matrix(_parse_matrix(borrowed[g_key]), source_external, external_nodes)
    Ihisred = _reorder_vector(_parse_vector(borrowed[ihis_key]), source_external, external_nodes)
    return {
        "Gred": Gred,
        "Ihisred": Ihisred,
        "W": W_value,
        # Borrowed reduction is only allowed to classify final Gred/Ihisred
        # stages. Internal-node recovery must still come from the structured
        # Gkr/W/Ihisk matrix path, so Kv/Kh are shape placeholders here.
        "Kv": sp.zeros(len(effective_internal_nodes), len(external_nodes)),
        "Kh": sp.zeros(len(effective_internal_nodes), 1),
    }


def _partition_payload(payload: dict) -> tuple[sp.Matrix, sp.Matrix, sp.Matrix | None, sp.Matrix | None, list[str], list[str], list[str], list[str]]:
    all_nodes = list(payload["all_nodes"])
    external_nodes = list(payload["external_nodes"])
    requested_internal_nodes = list(payload.get("internal_nodes", []))
    ground_nodes = list(payload.get("ground_nodes", []))
    G_full = _parse_matrix(payload["G_full"])
    Ihis_full = _parse_vector(payload["Ihis_full"])
    G_tagged = _parse_matrix(payload["G_full_tagged"]) if payload.get("G_full_tagged") else None
    Ihis_tagged = _parse_vector(payload["Ihis_full_tagged"]) if payload.get("Ihis_full_tagged") else None

    grounded = apply_ground_constraint(G_full, Ihis_full, all_nodes, ground_nodes)
    grounded_tagged = (
        apply_ground_constraint(G_tagged, Ihis_tagged if Ihis_tagged is not None else Ihis_full, all_nodes, ground_nodes)
        if G_tagged is not None
        else None
    )
    validation = validate_ground_partition(
        all_nodes,
        external_nodes,
        [node for node in all_nodes if node not in set(external_nodes)],
        ground_nodes,
    )
    remaining_nodes = list(grounded.remaining_nodes)
    external = [node for node in validation.external_nodes if node in remaining_nodes]
    external_set = set(external)
    internal = [
        node
        for node in requested_internal_nodes
        if node in remaining_nodes and node not in external_set
    ]
    internal_set = set(internal)
    internal.extend(
        node
        for node in remaining_nodes
        if node not in external_set and node not in internal_set
    )
    return (
        grounded.G_ng,
        grounded.Ihis_ng,
        grounded_tagged.G_ng if grounded_tagged is not None else None,
        grounded_tagged.Ihis_ng if grounded_tagged is not None else None,
        remaining_nodes,
        external,
        internal,
        validation.warnings,
    )


def _direct_retained_matrices(
    payload: dict,
    node_order: list[str],
    external_nodes: list[str],
    *,
    tagged: bool = False,
) -> tuple[sp.Matrix, sp.Matrix, list[dict]]:
    node_index = {node: index for index, node in enumerate(node_order)}
    external_set = set(external_nodes)
    G_direct = sp.zeros(len(node_order), len(node_order))
    Ihis_direct = sp.zeros(len(node_order), 1)
    accepted: list[dict] = []
    for stamp in payload.get("direct_retained_stamps") or []:
        support = set(stamp.get("support_nodes") or [])
        if not support or not support.issubset(external_set):
            continue
        accepted.append(
            {
                "id": stamp.get("id") or stamp.get("source_id") or "",
                "support_nodes": sorted(support),
            }
        )
        for entry in stamp.get("G") or []:
            row = node_index.get(entry.get("row"))
            col = node_index.get(entry.get("col"))
            if row is None or col is None:
                continue
            expr_text = entry.get("tagged") if tagged and entry.get("tagged") is not None else entry.get("expr")
            if expr_text is None:
                continue
            G_direct[row, col] += _parse_expr(str(expr_text))
        for entry in stamp.get("Ihis") or []:
            row = node_index.get(entry.get("row"))
            if row is None:
                continue
            expr_text = entry.get("tagged") if tagged and entry.get("tagged") is not None else entry.get("expr")
            if expr_text is None:
                continue
            Ihis_direct[row, 0] += _parse_expr(str(expr_text))
    return G_direct, Ihis_direct, accepted


def _slice_direct_retained(
    direct_G: sp.Matrix,
    direct_Ihis: sp.Matrix,
    node_order: list[str],
    external_nodes: list[str],
) -> tuple[sp.Matrix, sp.Matrix]:
    indices = [node_order.index(node) for node in external_nodes]
    return direct_G.extract(indices, indices), direct_Ihis.extract(indices, [0])


def _drop_isolated_dummy_nodes_before_schur(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    G_tagged: sp.Matrix | None,
    Ihis_tagged: sp.Matrix | None,
    node_order: list[str],
    external_nodes: list[str],
    internal_nodes: list[str],
    dummy_nodes: set[str],
) -> tuple[sp.Matrix, sp.Matrix, sp.Matrix | None, sp.Matrix | None, list[str], list[str], list[str]]:
    if not dummy_nodes:
        return G, Ihis, G_tagged, Ihis_tagged, node_order, external_nodes, internal_nodes

    keep_indices = [
        index
        for index, node in enumerate(node_order)
        if str(node) not in dummy_nodes
    ]
    kept_nodes = [node_order[index] for index in keep_indices]

    G_pruned = sp.Matrix(G).extract(keep_indices, keep_indices)
    Ihis_pruned = sp.Matrix(Ihis).extract(keep_indices, [0])
    G_tagged_pruned = (
        sp.Matrix(G_tagged).extract(keep_indices, keep_indices)
        if G_tagged is not None
        else None
    )
    Ihis_tagged_pruned = (
        sp.Matrix(Ihis_tagged).extract(keep_indices, [0])
        if Ihis_tagged is not None
        else None
    )
    external_pruned = [node for node in external_nodes if str(node) not in dummy_nodes]
    internal_pruned = [node for node in internal_nodes if str(node) not in dummy_nodes]
    return (
        G_pruned,
        Ihis_pruned,
        G_tagged_pruned,
        Ihis_tagged_pruned,
        kept_nodes,
        external_pruned,
        internal_pruned,
    )


def build_optimized_response(payload: dict) -> dict:
    simplify_level = payload.get("simplify_level") or "light"
    display_mode = payload.get("display_mode") or "compact"
    use_suggested_order = bool(payload.get("use_suggested_order", False))
    dummy_finalization = payload.get("dummy_finalization") or {}
    if dummy_finalization.get("dummy_leaves"):
        return _build_single_case_dummy_finalized_response(
            payload,
            display_mode=display_mode,
            simplify_level=simplify_level,
            use_suggested_order=use_suggested_order,
        )

    G, Ihis, G_tagged, Ihis_tagged, node_order, external_nodes, internal_nodes, partition_warnings = _partition_payload(payload)
    warnings = list(partition_warnings)
    single_dummy_blocks = dummy_node_blocks_from_payload(payload)
    single_dummy_nodes: set[str] = set()
    if single_dummy_blocks:
        validate_dummy_node_blocks(G, Ihis, node_order, single_dummy_blocks, common_internal_nodes=internal_nodes)
        for block in single_dummy_blocks:
            single_dummy_nodes.update(str(node) for node in block.dummy_nodes)
        G, Ihis, G_tagged, Ihis_tagged, node_order, external_nodes, internal_nodes = (
            _drop_isolated_dummy_nodes_before_schur(
                G,
                Ihis,
                G_tagged,
                Ihis_tagged,
                node_order,
                external_nodes,
                internal_nodes,
                single_dummy_nodes,
            )
        )
    borrowed_dependency = payload.get("reduced_dependency_analysis") or payload.get("reduced_dependency")
    direct_G, direct_Ihis, direct_stamps = _direct_retained_matrices(payload, node_order, external_nodes)
    direct_Grr, direct_Ihisr = _slice_direct_retained(direct_G, direct_Ihis, node_order, external_nodes)
    direct_symbol_table = payload.get("symbol_dependency_table") or {}
    direct_Grr_ram, direct_Grr_code = _split_matrix_ram_and_code_terms(direct_Grr, direct_symbol_table)
    direct_Ihisr_ram, direct_Ihisr_code = _split_matrix_ram_and_code_terms(direct_Ihisr, direct_symbol_table)
    if direct_stamps:
        G = G - direct_G
        Ihis = Ihis - direct_Ihis
    direct_G_tagged = direct_Ihis_tagged = direct_Grr_tagged = direct_Ihisr_tagged = None
    if direct_stamps and G_tagged is not None and Ihis_tagged is not None:
        direct_G_tagged, direct_Ihis_tagged, _ = _direct_retained_matrices(payload, node_order, external_nodes, tagged=True)
        direct_Grr_tagged, direct_Ihisr_tagged = _slice_direct_retained(
            direct_G_tagged,
            direct_Ihis_tagged,
            node_order,
            external_nodes,
        )
        G_tagged = G_tagged - direct_G_tagged
        Ihis_tagged = Ihis_tagged - direct_Ihis_tagged
    direct_tagged_symbol_table = payload.get("symbol_dependency_table_tagged") or direct_symbol_table
    if direct_Grr_tagged is not None and direct_Ihisr_tagged is not None:
        direct_Grr_ram_tagged, direct_Grr_code_tagged = _split_matrix_ram_and_code_terms(
            direct_Grr_tagged,
            direct_tagged_symbol_table,
        )
        direct_Ihisr_ram_tagged, direct_Ihisr_code_tagged = _split_matrix_ram_and_code_terms(
            direct_Ihisr_tagged,
            direct_tagged_symbol_table,
        )
    else:
        direct_Grr_ram_tagged = direct_Grr_code_tagged = None
        direct_Ihisr_ram_tagged = direct_Ihisr_code_tagged = None

    skip_symbolic_w_details = bool(borrowed_dependency) and not bool(
        payload.get("preserve_structured_details_with_borrowed_dependency")
    )
    structured = build_structured_formula(
        G,
        Ihis,
        node_order,
        external_nodes,
        internal_nodes,
        use_suggested_order=use_suggested_order,
        simplify_level=simplify_level,
        skip_symbolic_w_details=skip_symbolic_w_details,
    )
    tagged_structured = None
    if G_tagged is not None and Ihis_tagged is not None and not borrowed_dependency:
        tagged_structured = build_structured_formula(
            G_tagged,
            Ihis_tagged,
            node_order,
            external_nodes,
            structured["effective_internal_nodes"],
            use_suggested_order=False,
            simplify_level=simplify_level,
        )
    dependency_model_override = None
    analysis_model_override = None
    runtime_w = structured.get("block_type") == "general" and bool(structured.get("effective_internal_nodes"))
    structured_w = None if runtime_w else structured.get("details", {}).get(
        "W",
        sp.zeros(len(structured["effective_internal_nodes"]), len(structured["effective_internal_nodes"])),
    )
    if borrowed_dependency:
        dependency_model_override = _borrowed_reduced_dependency_model(
            borrowed_dependency,
            external_nodes,
            structured["effective_internal_nodes"],
            structured_w,
        )
        analysis_model_override = _borrowed_reduced_dependency_model(
            borrowed_dependency,
            external_nodes,
            structured["effective_internal_nodes"],
            sp.zeros(len(structured["effective_internal_nodes"]), len(structured["effective_internal_nodes"])),
            tagged=True,
        )
        if dependency_model_override is None or analysis_model_override is None:
            raise ValueError("reduced_dependency_analysis must include G_red/Ihis_red and tagged reduced entries")
    if G_tagged is not None and Ihis_tagged is not None:
        if dependency_model_override is None:
            reduced = eliminate_internal_nodes(G, Ihis, node_order, external_nodes)
            tagged_reduced = eliminate_internal_nodes(G_tagged, Ihis_tagged, node_order, external_nodes)
            dependency_model_override = (
                _reduced_dependency_model(
                    reduced,
                    structured["effective_internal_nodes"],
                    structured_w,
                )
            )
            analysis_model_override = _reduced_dependency_model(
                tagged_reduced,
                structured["effective_internal_nodes"],
                sp.zeros(len(structured["effective_internal_nodes"]), len(structured["effective_internal_nodes"])),
            )
    rtds_stage_plan = build_dependency_stage_plan(
        structured,
        payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {},
        simplify_level=simplify_level,
        analysis_structured=tagged_structured,
        dependency_model_override=dependency_model_override,
        analysis_model_override=analysis_model_override,
    )
    if direct_stamps:
        rtds_stage_plan["Gred_direct"] = direct_Grr
        rtds_stage_plan["Ihisred_direct"] = direct_Ihisr
        rtds_stage_plan["add_ram_direct_to_ram_owned_gred"] = not bool(borrowed_dependency)
        if direct_Grr_tagged is not None and direct_Ihisr_tagged is not None:
            rtds_stage_plan["Gred_direct_tagged"] = direct_Grr_tagged
            rtds_stage_plan["Ihisred_direct_tagged"] = direct_Ihisr_tagged
    warnings.extend(structured.get("warnings", []))
    blocks = structured["blocks"]

    c_draft = c_draft_for_structured_formula(
        structured,
        node_display_names=payload.get("node_display_names") or {},
        rtds_stage_plan=rtds_stage_plan,
    )
    if single_dummy_nodes:
        c_draft = _apply_single_case_dummy_recovery_skip(
            c_draft,
            structured["effective_internal_nodes"],
            single_dummy_nodes,
        )
    if single_dummy_blocks and "DummyNodeBlock isolated internal nodes are not recovered" not in c_draft:
        c_draft = c_draft.replace(
            "T1_T2:\n",
            "T1_T2:\n    /* DummyNodeBlock isolated internal nodes are not recovered. */\n",
            1,
        )
    c_draft = _ensure_static_blank_line(c_draft)
    try:
        gred_entry_reuse = structural_gred_entry_reuse_plan(
            G,
            node_order,
            external_nodes,
            structured["effective_internal_nodes"],
        )
    except Exception:
        gred_entry_reuse = []
    node_display_names = payload.get("node_display_names") or {}
    display_external_nodes = [str(node_display_names.get(node, node)) for node in external_nodes]

    return {
        "ok": True,
        "mode": "structured_formula",
        "display_mode": display_mode,
        "simplify_level": simplify_level,
        "use_suggested_order": use_suggested_order,
        "external_nodes": external_nodes,
        "internal_nodes": internal_nodes,
        "effective_internal_nodes": structured["effective_internal_nodes"],
        "warnings": warnings,
        "structured": {
            "block_type": structured["block_type"],
            "analysis": _clean_value(structured["analysis"]),
            "effective_analysis": _clean_value(structured["effective_analysis"]),
            "blocks": {
                "G_rr": _clean_matrix(blocks["G_rr"]),
                "G_ri": _clean_matrix(blocks["G_ri"]),
                "G_ir": _clean_matrix(blocks["G_ir"]),
                "G_ii": _clean_matrix(blocks["G_ii"]),
                "Ihis_r": _clean_vector(blocks["Ihis_r"]),
                "Ihis_i": _clean_vector(blocks["Ihis_i"]),
            },
            "details": _clean_value(structured["details"]),
            "direct_retained": {
                "stamps": _clean_value(direct_stamps),
                "Gred_direct": _clean_matrix(direct_Grr),
                "Gred_direct_ram": _clean_matrix(direct_Grr_ram),
                "Gred_direct_code": _clean_matrix(direct_Grr_code),
                **(
                    {
                        "Gred_direct_tagged": _clean_matrix(direct_Grr_tagged),
                        "Gred_direct_ram_tagged": _clean_matrix(direct_Grr_ram_tagged),
                        "Gred_direct_code_tagged": _clean_matrix(direct_Grr_code_tagged),
                    }
                    if direct_Grr_tagged is not None
                    else {}
                ),
                "Ihisred_direct": _clean_vector(direct_Ihisr),
                "Ihisred_direct_ram": _clean_vector(direct_Ihisr_ram),
                "Ihisred_direct_code": _clean_vector(direct_Ihisr_code),
                **(
                    {
                        "Ihisred_direct_tagged": _clean_vector(direct_Ihisr_tagged),
                        "Ihisred_direct_ram_tagged": _clean_vector(direct_Ihisr_ram_tagged),
                        "Ihisred_direct_code_tagged": _clean_vector(direct_Ihisr_code_tagged),
                    }
                    if direct_Ihisr_tagged is not None
                    else {}
                ),
            },
            "dependency_analysis": _clean_value(rtds_stage_plan.get("dependency_analysis", {})),
            "dynamic_subblock": _clean_value(rtds_stage_plan.get("dynamic_subblock", {})),
            "gred_entry_reuse": _clean_gred_entry_reuse(gred_entry_reuse, display_external_nodes),
            "dummy_node_blocks": _clean_value(
                {
                    "count": len(single_dummy_blocks),
                    "nodes": sorted(single_dummy_nodes),
                    "dropped_before_schur": True,
                }
                if single_dummy_blocks
                else {}
            ),
            "c_draft": _ensure_static_blank_line(c_draft),
        },
    }


def _build_single_case_dummy_finalized_c_draft(item: dict) -> str:
    final = item["final"]
    symbol_table = item.get("symbol_table") or {}
    nodes = list(final.nodes)
    dim = len(nodes)
    K_v = item.get("K_v")
    K_h = item.get("K_h")
    symbols = _symbols_in_matrices(
        final.G,
        final.Ihis,
        sp.Matrix(K_v) if K_v is not None else sp.zeros(0, 0),
        sp.Matrix(K_h) if K_h is not None else sp.zeros(0, 1),
    )
    declarations = [f"    double {name} = 0.0;" for name in symbols]
    ram_entries: list[tuple[int, int, sp.Expr]] = []
    dynamic_entries: list[tuple[int, int, sp.Expr, str, str, str]] = []
    for row in range(final.G.rows):
        for col in range(final.G.cols):
            expr = sp.sympify(final.G[row, col])
            if expr == 0:
                continue
            if _expr_stage(expr, symbol_table) == "RAM":
                ram_entries.append((row, col, expr))
    for row in range(final.G.rows):
        for col in range(row, final.G.cols):
            expr = sp.sympify(final.G[row, col])
            if expr == 0 or _expr_stage(expr, symbol_table) == "RAM":
                continue
            left = _c_identifier_name(nodes[row], f"N{row + 1}")
            right = _c_identifier_name(nodes[col], f"N{col + 1}")
            dynamic_entries.append((row, col, expr, f"varG_{left}_{right}", nodes[row], nodes[col]))

    lines = [
        "#include <matrixLIB.h>",
        "/* RTDS-style C draft for optimized elimination with active DummyBranch finalization.",
        "   Dummy leaf nodes are removed from the solver stamp after the super-node reduction. */",
        f"enum {{ NR_SUPER = {len(item.get('super_nodes') or [])}, NR = {dim} }};",
        "",
        "LOCAL_STATIC:",
        *(declarations or ["    /* No user symbols are required by the finalized matrices. */"]),
        "",
        "RAM_PASS1:",
        "    int err = 0;",
    ]
    if ram_entries:
        lines.extend([
            "    /* Finalized RAM-side G stamp. */",
            *[f"    g_mat_nods[{index}] = getNodeNum(comp, \"{node}\");" for index, node in enumerate(nodes)],
            f"    for (int row = 0; row < {dim}; row++) {{",
            f"        for (int col = 0; col < {dim}; col++) {{",
            "            g_mat_over[row][col] = 0.0;",
            "        }",
            "    }",
        ])
        for row, col, expr in ram_entries:
            lines.append(f"    g_mat_over[{row}][{col}] = {_ccode(expr)};")
        lines.append(f"    setupGMatrix({dim});")
    else:
        lines.append("    /* No RAM-side G entries: no fixed G overlay is registered. */")
    lines.extend([
        "    if (err > 0) {",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        "                       \"RTDS dummy-finalized allocation failed for component %s.\", Name);",
        "    }",
        "",
    ])
    if dynamic_entries:
        lines.extend([
            "GVALUES:",
            "    /* Dynamic final-G stamp handles after DummyBranch finalization. */",
        ])
        for _row, _col, _expr, var, left, right in dynamic_entries:
            lines.append(f"    double {var} = createGValue(\"{var}\", \"{left}\", \"{right}\", 0, \"TRUE\");")
        lines.append("")
    lines.extend([
        "CODE:",
        "BEGIN_T0:",
    ])
    if dynamic_entries:
        for _row, _col, expr, var, _left, _right in dynamic_entries:
            lines.append(f"    {var} = {_ccode(expr)};")
        lines.append("")
    lines.append("    /* Node injection currents follow the finalized retained-node order. */")
    for row, node in enumerate(nodes):
        lines.append(f"    Inj{_c_identifier_name(node, f'N{row + 1}')} = {_ccode(final.Ihis[row, 0])};")
    lines.extend([
        "",
        "T1_T2:",
    ])
    recovery_lines = _recovery_assignment_lines(item, indent="    ")
    if recovery_lines:
        lines.extend([
            "    /* Physical internal-node recovery; dummy final nodes are skipped. */",
            *recovery_lines,
        ])
    else:
        lines.append("    /* Dummy final nodes are removed from the solver dimension and are not recovered. */")
    return _ensure_static_blank_line("\n".join(lines))


def _build_single_case_dummy_finalized_response(
    payload: dict,
    *,
    display_mode: str,
    simplify_level: str,
    use_suggested_order: bool,
) -> dict:
    profile = {
        "name": "Active case",
        "case_map": {},
        "payload": payload,
        "dummy_finalization": payload.get("dummy_finalization") or {},
    }
    profile_set = build_finalization_profiles([profile])
    super_result = _reduced_super_result_from_payload(payload)
    final_profile = profile_set.case_profiles[0]
    final = finalize_profile_result(
        super_result["G"],
        super_result["Ihis"],
        super_result["external_nodes"],
        final_profile,
    )
    symbol_table = payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {}
    item = {
        "profile": final_profile,
        "final": final,
        "symbol_table": symbol_table,
        "recovery_nodes": super_result["internal_nodes"],
        "super_nodes": super_result["external_nodes"],
        "K_v": super_result["K_v"],
        "K_h": super_result["K_h"],
    }
    final_nodes = list(final.nodes)
    final_dim = len(final_nodes)
    recovery_nodes = list(super_result["internal_nodes"])
    return {
        "ok": True,
        "mode": "structured_formula",
        "display_mode": display_mode,
        "simplify_level": simplify_level,
        "use_suggested_order": use_suggested_order,
        "external_nodes": final_nodes,
        "internal_nodes": recovery_nodes,
        "effective_internal_nodes": recovery_nodes,
        "warnings": [
            "Info: active DummyBranch case is finalized before optimized C stamping; dummy leaf nodes are not solver nodes."
        ],
        "structured": {
            "fast_path": "single_case_dummy_finalization",
            "block_type": "dummy_finalized",
            "analysis": {
                "kind": "single_case_dummy_finalization",
                "super_external_nodes": list(super_result["external_nodes"]),
                "final_external_nodes": final_nodes,
                "dummy_nodes": list(final_profile.dummy_nodes),
            },
            "effective_analysis": {
                "kind": "single_case_dummy_finalization",
                "NR_SUPER": len(super_result["external_nodes"]),
                "NR": final_dim,
            },
            "blocks": {
                "G_rr": _clean_matrix(final.G),
                "G_ri": _clean_matrix(sp.zeros(final_dim, len(recovery_nodes))),
                "G_ir": _clean_matrix(sp.zeros(len(recovery_nodes), final_dim)),
                "G_ii": _clean_matrix(sp.zeros(len(recovery_nodes), len(recovery_nodes))),
                "Ihis_r": _clean_vector(final.Ihis),
                "Ihis_i": _clean_vector(sp.zeros(len(recovery_nodes), 1)),
            },
            "details": {
                "super_nodes": list(super_result["external_nodes"]),
                "final_nodes": final_nodes,
                "dummy_nodes": list(final_profile.dummy_nodes),
            },
            "direct_retained": {
                "stamps": [],
                "Gred_direct": _clean_matrix(sp.zeros(final_dim, final_dim)),
                "Gred_direct_ram": _clean_matrix(sp.zeros(final_dim, final_dim)),
                "Gred_direct_code": _clean_matrix(sp.zeros(final_dim, final_dim)),
                "Ihisred_direct": _clean_vector(sp.zeros(final_dim, 1)),
                "Ihisred_direct_ram": _clean_vector(sp.zeros(final_dim, 1)),
                "Ihisred_direct_code": _clean_vector(sp.zeros(final_dim, 1)),
            },
            "dependency_analysis": {},
            "dynamic_subblock": {},
            "c_draft": _build_single_case_dummy_finalized_c_draft(item),
        },
    }


def _profile_case_comment(profile: dict, index: int) -> str:
    name = str(profile.get("name") or f"Case {index + 1}")
    comment = str(profile.get("comment") or "").strip()
    case_map = profile.get("case_map") or {}
    mapped = ", ".join(f"{key}=case{value}" for key, value in case_map.items())
    suffix = "; ".join(part for part in [comment, mapped] if part)
    return f"{name}: {suffix}" if suffix else name


def _multi_case_signature(result: dict) -> dict:
    structured = result.get("structured") or {}
    blocks = structured.get("blocks") or {}
    return {
        "external_nodes": result.get("external_nodes") or [],
        "effective_internal_nodes": result.get("effective_internal_nodes") or [],
        "block_type": structured.get("block_type"),
        "shapes": {
            key: [len(value or []), len((value or [[]])[0]) if value else 0]
            for key, value in blocks.items()
            if key in {"G_rr", "G_ri", "G_ir", "G_ii", "Ihis_r", "Ihis_i"}
        },
    }


def _matrix_from_clean(value: object) -> sp.Matrix:
    if value is None:
        return sp.zeros(0, 0)
    if isinstance(value, list) and value and not isinstance(value[0], list):
        return sp.Matrix([[_parse_expr(str(item))] for item in value])
    return sp.Matrix([[_parse_expr(str(item)) for item in row] for row in (value or [])])


def _result_matrix(result: dict, key: str) -> sp.Matrix:
    return _matrix_from_clean((result.get("structured") or {}).get("blocks", {}).get(key) or [])


def _result_direct_matrix(result: dict, key: str) -> sp.Matrix:
    return _matrix_from_clean((result.get("structured") or {}).get("direct_retained", {}).get(key) or [])


def _matrix_set_lines(name: str, matrix: sp.Matrix, *, indent: str = "        ") -> list[str]:
    matrix = sp.Matrix(matrix)
    lines: list[str] = []
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            expr = sp.sympify(matrix[row, col])
            if expr == 0:
                continue
            lines.append(f"{indent}set_CODE(&{name}, {row}, {col}, {_ccode(expr)});")
    return lines


def _clear_matrix_lines(name: str, rows: int, cols: int, *, indent: str = "        ") -> list[str]:
    if rows <= 0 or cols <= 0:
        return []
    return [
        f"{indent}for (int row = 0; row < {rows}; row++) {{",
        f"{indent}    for (int col = 0; col < {cols}; col++) {{",
        f"{indent}        set_CODE(&{name}, row, col, 0.0);",
        f"{indent}    }}",
        f"{indent}}}",
    ]


def _symbols_in_matrices(*matrices: sp.Matrix) -> list[str]:
    names: set[str] = set()
    for matrix in matrices:
        for expr in sp.Matrix(matrix):
            names.update(symbol.name for symbol in sp.sympify(expr).free_symbols)
    return sorted(names)


def _c_identifier_name(value: object, fallback: str) -> str:
    name = re.sub(r"\W+", "_", str(value)).strip("_")
    return name or fallback


def _single_symbol_set(matrices: dict[str, sp.Matrix]) -> set[str]:
    names: set[str] = set()
    for matrix in matrices.values():
        for expr in sp.Matrix(matrix):
            names.update(symbol.name for symbol in sp.sympify(expr).free_symbols)
    return names


def _single_branch_case_selector(profile_results: list[dict]) -> str | None:
    branch_ids: set[str] = set()
    for item in profile_results:
        case_map = item["profile"].get("case_map") or {}
        if len(case_map) != 1:
            return None
        branch_ids.update(str(key) for key in case_map)
    if len(branch_ids) != 1:
        return None
    branch_id = next(iter(branch_ids))
    return f"{_c_identifier_name(branch_id, 'case')}_case"


def _payload_symbol_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for key in ["G_full", "Ihis_full"]:
        for row in payload.get(key) or []:
            values = row if isinstance(row, list) else [row]
            for expr in values:
                names.update(symbol.name for symbol in _parse_expr(str(expr)).free_symbols)
    return names


def _expr_matrix_from_payload(payload: dict, key: str) -> sp.Matrix:
    value = payload.get(key) or []
    if value and not isinstance(value[0], list):
        return sp.Matrix([[_parse_expr(item)] for item in value])
    return sp.Matrix([[_parse_expr(item) for item in row] for row in value])


def _expr_matrix_for_alias_template_profile(payload: dict, key: str, common_dummy_internal_nodes: set[str]) -> sp.Matrix:
    matrix = _expr_matrix_from_payload(payload, key)
    if not common_dummy_internal_nodes:
        return matrix
    blocks = dummy_node_blocks_from_payload(payload)
    if not blocks:
        return matrix
    node_index = {str(node): index for index, node in enumerate(payload.get("all_nodes") or [])}
    dummy_nodes = {
        str(node)
        for block in blocks
        for node in block.dummy_nodes
        if str(node) in common_dummy_internal_nodes
    }
    if not dummy_nodes:
        return matrix
    matrix = sp.Matrix(matrix)
    for node in dummy_nodes:
        index = node_index.get(node)
        if index is None:
            continue
        if key == "G_full":
            for col in range(matrix.cols):
                matrix[index, col] = sp.Integer(0)
            for row in range(matrix.rows):
                matrix[row, index] = sp.Integer(0)
            matrix[index, index] = sp.Integer(1)
        elif key == "Ihis_full" and index < matrix.rows:
            matrix[index, 0] = sp.Integer(0)
    return matrix


def _matrices_equal_light(left: sp.Matrix, right: sp.Matrix) -> bool:
    left = sp.Matrix(left)
    right = sp.Matrix(right)
    if left.shape != right.shape:
        return False
    for row in range(left.rows):
        for col in range(left.cols):
            if sp.expand(left[row, col] - right[row, col]) != 0:
                return False
    return True


def _find_symbol_mapping(base_payload: dict, case_payload: dict) -> dict[str, str] | None:
    base_symbols = _payload_symbol_names(base_payload)
    case_symbols = _payload_symbol_names(case_payload)
    missing = sorted(base_symbols - case_symbols)
    introduced = sorted(case_symbols - base_symbols)
    if not missing:
        return {}
    if not introduced:
        return None
    base_G = _expr_matrix_from_payload(base_payload, "G_full")
    case_G = _expr_matrix_from_payload(case_payload, "G_full")
    base_I = _expr_matrix_from_payload(base_payload, "Ihis_full")
    case_I = _expr_matrix_from_payload(case_payload, "Ihis_full")
    base_symbol_objs = {name: sp.Symbol(name) for name in missing}
    for choices in itertools.product(introduced, repeat=len(missing)):
        mapping = dict(zip(missing, choices))
        substitutions = {base_symbol_objs[name]: sp.Symbol(target) for name, target in mapping.items()}
        if _matrices_equal_light(base_G.xreplace(substitutions), case_G) and _matrices_equal_light(
            base_I.xreplace(substitutions),
            case_I,
        ):
            return mapping
    return None


def _replace_symbol_words(text: str, mapping: dict[str, str]) -> str:
    result = str(text)
    for source, target in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        result = re.sub(rf"\b{re.escape(source)}\b", target, result)
    return result


def _replace_payload_symbols(payload: dict, mapping: dict[str, str]) -> dict:
    clone = json.loads(json.dumps(payload))
    for key in ["G_full", "G_full_tagged", "Ihis_full", "Ihis_full_tagged"]:
        if key not in clone:
            continue
        if clone[key] and isinstance(clone[key][0], list):
            clone[key] = [[_replace_symbol_words(item, mapping) for item in row] for row in clone[key]]
        else:
            clone[key] = [_replace_symbol_words(item, mapping) for item in clone[key]]
    for table_name in ["symbol_dependency_table", "symbol_dependency_table_tagged"]:
        table = clone.get(table_name) or {}
        for source, target in mapping.items():
            if source in table and target not in table:
                table[target] = table[source]
            tagged_source = f"{source}_tag"
            tagged_target = f"{target}_tag"
            if tagged_source in table and tagged_target not in table:
                table[tagged_target] = table[tagged_source]
        clone[table_name] = table
    return clone


def _expr_stage(expr: sp.Expr, symbol_table: dict[str, str]) -> str:
    stages = {str(symbol_table.get(symbol.name, "RAM_CONSTANT")) for symbol in sp.sympify(expr).free_symbols}
    if stages & {"CODE_PER_STEP", "STEP_HISTORY"}:
        return "CODE_PER_STEP"
    if stages & {"CODE_VARIABLE", "CODE_UPDATE", "UNKNOWN"}:
        return "CODE"
    return "RAM"


def _promote_owner(owners: Iterable[str]) -> str:
    order = {"RAM": 0, "CODE": 1, "CODE_PER_STEP": 2}
    return max((owner for owner in owners), key=lambda owner: order.get(owner, 0), default="RAM")


def _stage_for_owner(owner: str) -> str:
    if owner == "CODE_PER_STEP":
        return "CODE_PER_STEP"
    if owner == "CODE":
        return "CODE_UPDATE"
    return "RAM_INIT"


def _symbol_dependency_for_owner(owner: str) -> str:
    if owner == "CODE_PER_STEP":
        return "CODE_PER_STEP"
    if owner == "CODE":
        return "CODE_VARIABLE"
    return "RAM_CONSTANT"


def _expr_equal_light(left: sp.Expr, right: sp.Expr) -> bool:
    return sp.expand(sp.sympify(left) - sp.sympify(right)) == 0


def _expr_to_payload_text(expr: sp.Expr) -> str:
    expr = sp.sympify(expr)
    if expr == 0:
        return "0"
    return str(expr)


def _signed_sequence_key(values: Sequence[sp.Expr]) -> tuple[int, tuple[str, ...]]:
    sign = 1
    for value in values:
        value = sp.sympify(value)
        if value == 0:
            continue
        sign = -1 if value.could_extract_minus_sign() else 1
        break
    canonical = [sp.expand(sign * sp.sympify(value)) for value in values]
    return sign, tuple(str(value) for value in canonical)


def _matrix_payload_shape(payload: dict, key: str) -> tuple[int, int]:
    value = payload.get(key) or []
    if not value:
        return (0, 0)
    if isinstance(value[0], list):
        return (len(value), len(value[0]) if value else 0)
    return (len(value), 1)


def _validate_multicase_topology(profiles: list[dict]) -> None:
    base_payload = profiles[0].get("payload") or {}
    topology_keys = [
        "all_nodes",
        "external_nodes",
        "internal_nodes",
        "ground_nodes",
    ]
    base_signature = {key: base_payload.get(key) for key in topology_keys}
    base_shapes = {
        "G_full": _matrix_payload_shape(base_payload, "G_full"),
        "Ihis_full": _matrix_payload_shape(base_payload, "Ihis_full"),
    }
    for index, profile in enumerate(profiles[1:], start=1):
        payload = profile.get("payload") or {}
        for key, expected in base_signature.items():
            if payload.get(key) != expected:
                raise ValueError(f"multi-case topology invariant failed for profile {index}: {key} changed")
        for key, expected in base_shapes.items():
            if _matrix_payload_shape(payload, key) != expected:
                raise ValueError(f"multi-case topology invariant failed for profile {index}: {key} shape changed")


def _profile_case_index(profile: dict, branch_id: str, default: int = 0) -> int:
    case_map = profile.get("case_map") or {}
    return int(case_map.get(branch_id, default) or 0)


def _branch_ids_from_profiles(profiles: list[dict]) -> list[str]:
    branch_ids: set[str] = set()
    for profile in profiles:
        branch_ids.update((profile.get("case_map") or {}).keys())
    return sorted(branch_ids)


def _runtime_case_group_map(payload: dict) -> dict[str, dict]:
    groups = {}
    for group in payload.get("runtime_case_groups") or []:
        branch_id = str(group.get("branch_id") or "")
        if not branch_id:
            continue
        groups[branch_id] = {
            **group,
            "case_id_symbol": str(group.get("case_id_symbol") or f"runtime_{_c_identifier_name(branch_id, 'case')}_case_id"),
        }
    return groups


def _profiles_with_runtime_case_samples(payload: dict, profiles: list[dict], runtime_groups: dict[str, dict]) -> list[dict]:
    if not runtime_groups:
        return profiles
    expanded: list[dict] = []
    for init_index, profile in enumerate(profiles):
        init_case_map = dict(profile.get("case_map") or {})
        payload_base = profile.get("payload")
        if isinstance(payload_base, dict):
            expanded.append({
                **profile,
                "name": f"{profile.get('name') or f'case {init_index}'} / runtime base",
                "case_map": dict(init_case_map),
                "payload": payload_base,
                "_init_profile_index": init_index,
            })
        for branch_id, group in runtime_groups.items():
            for case in group.get("cases") or []:
                case_index = int(case.get("index") or 0)
                payloads = case.get("payloads") or []
                if init_index >= len(payloads) or not isinstance(payloads[init_index], dict):
                    raise ValueError(f"runtime-mutable case group {group.get('name') or branch_id} is missing payload for init profile {init_index}")
                expanded.append({
                    "name": f"{profile.get('name') or f'case {init_index}'} / {group.get('name') or branch_id} case {case_index}",
                    "comment": str(case.get("name") or ""),
                    "case_map": {**init_case_map, branch_id: case_index},
                    "payload": payloads[init_index],
                    "_init_profile_index": init_index,
                    "_runtime_branch_id": branch_id,
                })
    return expanded


def _position_depends_only_on_branch(
    profiles: list[dict],
    values: Sequence[sp.Expr],
    branch_id: str,
    base_case: int,
) -> bool:
    by_case: dict[int, sp.Expr] = {}
    for profile, value in zip(profiles, values):
        local_case = _profile_case_index(profile, branch_id, base_case)
        if local_case in by_case and not _expr_equal_light(by_case[local_case], value):
            return False
        by_case[local_case] = sp.sympify(value)
    return len({str(sp.expand(value)) for value in by_case.values()}) > 1


def _alias_name(branch_id: str, kind: str, index: int) -> str:
    base = f"cr_{_c_identifier_name(branch_id, 'branch')}_{kind}_eff"
    return base if index == 1 else f"{base}_{index}"


def _matrix_entry_alias_name(key: str, row: int, col: int) -> str:
    kind = "G" if key == "G_full" else "Ihis"
    return f"cr_{kind}_{row}_{col}_eff"


def _global_alias_cache_key(key: str, case_values: Mapping[int, sp.Expr]) -> tuple:
    return (
        key,
        tuple(
            (int(index), _expr_to_payload_text(sp.sympify(case_values[index])))
            for index in sorted(case_values)
        ),
    )


def _record_global_alias_use(aliases: dict[str, dict], alias: str, key: str, row: int, col: int) -> None:
    info = aliases.get(alias)
    if not info:
        return
    used_by = info.setdefault("used_by", [])
    label = f"{key}[{row}][{col}]"
    if label not in used_by:
        used_by.append(label)


def _profile_delta(values: Sequence[sp.Expr]) -> tuple[sp.Expr, ...]:
    if not values:
        return ()
    base = sp.sympify(values[0])
    return tuple(sp.expand(sp.sympify(value) - base) for value in values[1:])


def _delta_terms(values: Sequence[sp.Expr]) -> dict[tuple[int, sp.Expr], sp.Expr]:
    terms: dict[tuple[int, sp.Expr], sp.Expr] = {}
    for index, expr in enumerate(values):
        expr = sp.expand(sp.sympify(expr))
        if expr == 0:
            continue
        for term in sp.Add.make_args(expr):
            coeff, base = sp.sympify(term).as_coeff_Mul()
            key = (index, base)
            terms[key] = terms.get(key, sp.Integer(0)) + coeff
    return {key: coeff for key, coeff in terms.items() if coeff != 0}


def _represent_with_existing_aliases(values: Sequence[sp.Expr], grouped: dict) -> sp.Expr | None:
    groups = list(grouped.values())
    if not groups:
        return None
    target_delta = _profile_delta(values)
    target_terms = _delta_terms(target_delta)
    if not target_terms:
        return sp.sympify(values[0])

    target_keys = set(target_terms)
    candidates: list[dict] = []
    for group in groups:
        group_delta = _profile_delta(group["profile_values"])
        group_terms = _delta_terms(group_delta)
        if group_terms and (target_keys & set(group_terms)):
            candidates.append({**group, "delta": group_delta, "terms": group_terms})

    for coeff in (-1, 1):
        for group in candidates:
            if all(_expr_equal_light(target, coeff * delta) for target, delta in zip(target_delta, group["delta"])):
                expr = sp.sympify(values[0]) - coeff * group["profile_values"][0]
                expr += coeff * sp.Symbol(group["alias"])
                return sp.expand(expr)

    if not candidates:
        return None

    keys = sorted(
        set(target_terms).union(*(set(candidate["terms"]) for candidate in candidates)),
        key=lambda item: (item[0], str(item[1])),
    )
    matrix = sp.Matrix(
        [
            [candidate["terms"].get(key, sp.Integer(0)) for candidate in candidates]
            for key in keys
        ]
    )
    target = sp.Matrix([target_terms.get(key, sp.Integer(0)) for key in keys])
    try:
        solutions = sp.linsolve((matrix, target))
    except Exception:
        return None
    if not solutions:
        return None
    solution = next(iter(solutions), None)
    if solution is None:
        return None
    free_symbols = sorted(
        set().union(*(item.free_symbols for item in solution)),
        key=lambda symbol: symbol.name,
    )
    coeffs = [sp.simplify(item.subs({symbol: sp.Integer(0) for symbol in free_symbols})) for item in solution]
    if not all(coeff in (sp.Integer(-1), sp.Integer(0), sp.Integer(1)) for coeff in coeffs):
        return None

    expr = sp.sympify(values[0])
    for coeff, group in zip(coeffs, candidates):
        if coeff:
            expr -= coeff * group["profile_values"][0]
            expr += coeff * sp.Symbol(group["alias"])
    expr = sp.expand(expr)
    for profile_index, value in enumerate(values):
        resolved = expr
        for coeff, group in zip(coeffs, candidates):
            if coeff:
                resolved = resolved.subs(sp.Symbol(group["alias"]), group["profile_values"][profile_index])
        if not _expr_equal_light(resolved, value):
            return None
    return expr


def _add_global_profile_alias(
    *,
    aliases: dict[str, dict],
    replacements: dict[tuple[str, int, int], sp.Expr],
    symbol_table: dict,
    global_alias_cache: dict[tuple, str],
    key: str,
    row: int,
    col: int,
    values: Sequence[sp.Expr],
) -> None:
    alias = _matrix_entry_alias_name(key, row, col)
    case_values = {index: sp.sympify(value) for index, value in enumerate(values)}
    cache_key = _global_alias_cache_key(key, case_values)
    cached_alias = global_alias_cache.get(cache_key)
    if cached_alias:
        replacements[(key, row, col)] = sp.Symbol(cached_alias)
        _record_global_alias_use(aliases, cached_alias, key, row, col)
        return
    case_owners = {index: _expr_stage(expr, symbol_table) for index, expr in case_values.items()}
    aliases[alias] = {
        "branch_id": "__global__",
        "selector": "global",
        "kind": "G" if key == "G_full" else "Ihis",
        "owner": _promote_owner(case_owners.values()),
        "case_values": {
            str(index): _expr_to_payload_text(expr)
            for index, expr in case_values.items()
        },
        "case_owners": case_owners,
        "used_by": [f"{key}[{row}][{col}]"],
    }
    global_alias_cache[cache_key] = alias
    replacements[(key, row, col)] = sp.Symbol(alias)


def _add_global_init_profile_alias(
    *,
    aliases: dict[str, dict],
    symbol_table: dict,
    global_alias_cache: dict[tuple, str],
    key: str,
    row: int,
    col: int,
    values_by_init: Mapping[int, sp.Expr],
) -> sp.Expr:
    ordered = {int(index): sp.sympify(values_by_init[index]) for index in sorted(values_by_init)}
    values = list(ordered.values())
    if values and all(_expr_equal_light(values[0], value) for value in values[1:]):
        return sp.sympify(values[0])
    cache_key = _global_alias_cache_key(key, ordered)
    cached_alias = global_alias_cache.get(cache_key)
    if cached_alias:
        _record_global_alias_use(aliases, cached_alias, key, row, col)
        return sp.Symbol(cached_alias)
    alias = _matrix_entry_alias_name(key, row, col)
    if alias in aliases:
        suffix = 2
        while f"{alias}_{suffix}" in aliases:
            suffix += 1
        alias = f"{alias}_{suffix}"
    case_owners = {index: _expr_stage(expr, symbol_table) for index, expr in ordered.items()}
    aliases[alias] = {
        "branch_id": "__global__",
        "selector": "global",
        "kind": "G" if key == "G_full" else "Ihis",
        "owner": _promote_owner(case_owners.values()),
        "case_values": {
            str(index): _expr_to_payload_text(expr)
            for index, expr in ordered.items()
        },
        "case_owners": case_owners,
        "used_by": [f"{key}[{row}][{col}]"],
    }
    global_alias_cache[cache_key] = alias
    return sp.Symbol(alias)


def _runtime_invariant_values_by_init(
    sample_profiles: Sequence[dict],
    values: Sequence[sp.Expr],
) -> dict[int, sp.Expr] | None:
    if not any(profile.get("_runtime_branch_id") for profile in sample_profiles):
        return None
    values_by_init: dict[int, sp.Expr] = {}
    for profile, value in zip(sample_profiles, values):
        init_index = int(profile.get("_init_profile_index", 0) or 0)
        expr = sp.sympify(value)
        if init_index in values_by_init:
            if not _expr_equal_light(values_by_init[init_index], expr):
                return None
        else:
            values_by_init[init_index] = expr
    if not values_by_init or len(values_by_init) >= len(values):
        return None
    return values_by_init


def _ensure_branch_profile_alias(
    *,
    aliases: dict[str, dict],
    grouped: dict[tuple[str, str, tuple[str, ...]], dict],
    symbol_table: dict,
    sample_profiles: list[dict],
    runtime_groups: Mapping[str, dict],
    branch_id: str,
    kind: str,
    profile_values: Sequence[sp.Expr],
    base_case: int,
) -> tuple[int, str]:
    sign, sequence_key = _signed_sequence_key(profile_values)
    group_key = (branch_id, kind, sequence_key)
    if group_key not in grouped:
        alias_index = 1 + sum(
            1
            for item in grouped.values()
            if item["branch_id"] == branch_id and item["kind"] == kind
        )
        local_cases = sorted({
            _profile_case_index(profile, branch_id, base_case)
            for profile in sample_profiles
        })
        case_values: dict[int, sp.Expr] = {}
        canonical_values = [_parse_expr(text) for text in sequence_key]
        for profile, canonical in zip(sample_profiles, canonical_values):
            local_case = _profile_case_index(profile, branch_id, base_case)
            if local_case in case_values and not _expr_equal_light(case_values[local_case], canonical):
                raise ValueError(f"multi-case alias template found conflicting values for {branch_id} case {local_case}")
            case_values[local_case] = canonical
        alias = _alias_name(branch_id, kind, alias_index)
        case_owners = {
            case_index: _expr_stage(expr, symbol_table)
            for case_index, expr in case_values.items()
        }
        owner = _promote_owner(case_owners.values())
        runtime_group = runtime_groups.get(branch_id)
        if runtime_group and len({_expr_to_payload_text(value) for value in case_values.values()}) > 1 and owner == "RAM":
            owner = "CODE"
        aliases[alias] = {
            "branch_id": branch_id,
            "kind": kind,
            "owner": owner,
            "case_values": {
                str(case_index): _expr_to_payload_text(case_values.get(case_index, sp.Integer(0)))
                for case_index in local_cases
            },
            "case_owners": case_owners,
            **(
                {
                    "runtime_mutable": True,
                    "selector": "runtime",
                    "case_id_symbol": runtime_group["case_id_symbol"],
                    "runtime_group_name": runtime_group.get("name") or branch_id,
                }
                if runtime_group
                else {}
            ),
        }
        grouped[group_key] = {
            "alias": alias,
            "branch_id": branch_id,
            "kind": kind,
            "profile_values": canonical_values,
        }
    return sign, grouped[group_key]["alias"]


def _positive_negative_parts(expr: sp.Expr) -> tuple[sp.Expr, sp.Expr]:
    positive = sp.Integer(0)
    negative = sp.Integer(0)
    for term in sp.Add.make_args(sp.expand(sp.sympify(expr))):
        coeff, _base = sp.sympify(term).as_coeff_Mul()
        if coeff < 0:
            negative += -term
        else:
            positive += term
    return sp.expand(positive), sp.expand(negative)


def _try_runtime_additive_replacement(
    *,
    aliases: dict[str, dict],
    grouped: dict[tuple[str, str, tuple[str, ...]], dict],
    symbol_table: dict,
    global_alias_cache: dict[tuple, str],
    sample_profiles: list[dict],
    runtime_groups: Mapping[str, dict],
    key: str,
    row: int,
    col: int,
    values: Sequence[sp.Expr],
    branch_id: str,
    kind: str,
    base_case: int,
) -> sp.Expr | None:
    runtime_group = runtime_groups.get(branch_id)
    if not runtime_group:
        return None
    base_by_init: dict[int, sp.Expr] = {}
    case_by_init: dict[int, dict[int, sp.Expr]] = {}
    for profile, value in zip(sample_profiles, values):
        init_index = int(profile.get("_init_profile_index", 0) or 0)
        value = sp.sympify(value)
        if profile.get("_runtime_branch_id") == branch_id:
            local_case = _profile_case_index(profile, branch_id, base_case)
            case_by_init.setdefault(init_index, {})[local_case] = value
        elif profile.get("_runtime_branch_id") is None:
            base_by_init[init_index] = value
    if not base_by_init:
        return None

    local_cases = sorted({
        int(case.get("index") or 0)
        for case in runtime_group.get("cases") or []
    })
    if not local_cases:
        return None
    deltas: dict[int, sp.Expr] = {}
    for case_index in local_cases:
        reference_delta = None
        for init_index, base_value in base_by_init.items():
            case_value = case_by_init.get(init_index, {}).get(case_index)
            if case_value is None:
                if case_index == base_case:
                    case_value = base_value
                else:
                    return None
            delta = sp.expand(case_value - base_value)
            if reference_delta is None:
                reference_delta = delta
            elif not _expr_equal_light(reference_delta, delta):
                return None
        deltas[case_index] = sp.sympify(reference_delta if reference_delta is not None else 0)
    if all(_expr_equal_light(delta, 0) for delta in deltas.values()):
        return None

    negative_parts = []
    for case_index, delta in deltas.items():
        if case_index == base_case or _expr_equal_light(delta, 0):
            continue
        _positive, negative = _positive_negative_parts(delta)
        negative_parts.append(negative)
    if negative_parts:
        base_runtime_value = negative_parts[0]
        if any(not _expr_equal_light(base_runtime_value, item) for item in negative_parts[1:]):
            return None
    else:
        base_runtime_value = sp.Integer(0)

    case_values = {
        case_index: sp.expand(base_runtime_value + delta)
        for case_index, delta in deltas.items()
    }
    residual_by_init = {
        init_index: sp.expand(base_value - base_runtime_value)
        for init_index, base_value in base_by_init.items()
    }
    for profile, value in zip(sample_profiles, values):
        init_index = int(profile.get("_init_profile_index", 0) or 0)
        local_case = _profile_case_index(profile, branch_id, base_case)
        expected = sp.expand(residual_by_init.get(init_index, sp.Integer(0)) + case_values.get(local_case, base_runtime_value))
        if not _expr_equal_light(expected, value):
            return None

    profile_values = [
        case_values.get(_profile_case_index(profile, branch_id, base_case), base_runtime_value)
        for profile in sample_profiles
    ]
    sign, alias = _ensure_branch_profile_alias(
        aliases=aliases,
        grouped=grouped,
        symbol_table=symbol_table,
        sample_profiles=sample_profiles,
        runtime_groups=runtime_groups,
        branch_id=branch_id,
        kind=kind,
        profile_values=profile_values,
        base_case=base_case,
    )
    residual_expr = _add_global_init_profile_alias(
        aliases=aliases,
        symbol_table=symbol_table,
        global_alias_cache=global_alias_cache,
        key=key,
        row=row,
        col=col,
        values_by_init=residual_by_init,
    )
    return sp.expand(residual_expr + sign * sp.Symbol(alias))


def _rewrite_direct_retained_stamps_with_aliases(
    template_payload: dict,
    replacements: Mapping[tuple[str, int, int], sp.Expr],
) -> None:
    node_index = {
        str(node): index
        for index, node in enumerate(template_payload.get("all_nodes") or [])
    }
    rewritten_stamps = []
    for stamp in template_payload.get("direct_retained_stamps") or []:
        next_stamp = json.loads(json.dumps(stamp))
        for entry in next_stamp.get("G") or []:
            row = node_index.get(str(entry.get("row")))
            col = node_index.get(str(entry.get("col")))
            if row is None or col is None:
                continue
            replacement = replacements.get(("G_full", row, col))
            if replacement is None:
                continue
            text = _expr_to_payload_text(replacement)
            entry["expr"] = text
            if "tagged" in entry:
                entry["tagged"] = text
        for entry in next_stamp.get("Ihis") or []:
            row = node_index.get(str(entry.get("row")))
            if row is None:
                continue
            replacement = replacements.get(("Ihis_full", row, 0))
            if replacement is None:
                continue
            text = _expr_to_payload_text(replacement)
            entry["expr"] = text
            if "tagged" in entry:
                entry["tagged"] = text
        rewritten_stamps.append(next_stamp)
    template_payload["direct_retained_stamps"] = rewritten_stamps


def _build_multicase_alias_template_payload(payload: dict) -> dict | None:
    profiles = payload.get("case_profiles") or []
    runtime_groups = _runtime_case_group_map(payload)
    sample_profiles = _profiles_with_runtime_case_samples(payload, profiles, runtime_groups)
    if len(sample_profiles) < 2:
        return None
    try:
        _validate_multicase_topology(sample_profiles)
    except ValueError as exc:
        runtime_sample = next((profile for profile in sample_profiles if profile.get("_runtime_branch_id")), None)
        if runtime_groups and runtime_sample:
            branch_id = str(runtime_sample.get("_runtime_branch_id") or "")
            group = runtime_groups.get(branch_id, {})
            name = str(group.get("name") or branch_id or "runtime")
            raise ValueError(
                f"Runtime-mutable case group {name} changes topology or matrix shape. "
                "Use init-time case group instead."
            ) from exc
        raise
    branch_ids = _branch_ids_from_profiles(sample_profiles)
    if not branch_ids:
        return None

    base_payload = sample_profiles[0].get("payload")
    if not isinstance(base_payload, dict):
        return None
    symbol_table = {}
    for profile in sample_profiles:
        symbol_table.update((profile.get("payload") or {}).get("symbol_dependency_table") or {})

    common_dummy_internal_nodes = {str(node) for node in payload.get("common_dummy_internal_nodes") or []}
    matrices_by_key: dict[str, list[sp.Matrix]] = {
        "G_full": [
            _expr_matrix_for_alias_template_profile(profile["payload"], "G_full", common_dummy_internal_nodes)
            for profile in sample_profiles
        ],
        "Ihis_full": [
            _expr_matrix_for_alias_template_profile(profile["payload"], "Ihis_full", common_dummy_internal_nodes)
            for profile in sample_profiles
        ],
    }
    base_case_by_branch = {
        branch_id: _profile_case_index(sample_profiles[0], branch_id, 0)
        for branch_id in branch_ids
    }

    aliases: dict[str, dict] = {}
    replacements: dict[tuple[str, int, int], sp.Expr] = {}
    grouped: dict[tuple[str, str, tuple[str, ...]], dict] = {}
    global_alias_cache: dict[tuple, str] = {}
    pending_composite_entries: list[tuple[str, int, int, list[sp.Expr]]] = []

    for key, matrices in matrices_by_key.items():
        base = matrices[0]
        kind = "G" if key == "G_full" else "Ihis"
        for row in range(base.rows):
            for col in range(base.cols):
                values = [sp.sympify(matrix[row, col]) for matrix in matrices]
                if all(_expr_equal_light(values[0], value) for value in values[1:]):
                    continue
                existing_expr = _represent_with_existing_aliases(values, grouped)
                if existing_expr is not None:
                    replacements[(key, row, col)] = existing_expr
                    continue
                candidates = [
                    branch_id
                    for branch_id in branch_ids
                    if _position_depends_only_on_branch(
                        sample_profiles,
                        values,
                        branch_id,
                        base_case_by_branch.get(branch_id, 0),
                    )
                ]
                if len(candidates) != 1:
                    runtime_replacement = None
                    for branch_id in branch_ids:
                        runtime_replacement = _try_runtime_additive_replacement(
                            aliases=aliases,
                            grouped=grouped,
                            symbol_table=symbol_table,
                            global_alias_cache=global_alias_cache,
                            sample_profiles=sample_profiles,
                            runtime_groups=runtime_groups,
                            key=key,
                            row=row,
                            col=col,
                            values=values,
                            branch_id=branch_id,
                            kind=kind,
                            base_case=base_case_by_branch.get(branch_id, 0),
                        )
                        if runtime_replacement is not None:
                            break
                    if runtime_replacement is not None:
                        replacements[(key, row, col)] = runtime_replacement
                        continue
                    runtime_invariant = _runtime_invariant_values_by_init(sample_profiles, values)
                    if runtime_invariant is not None:
                        replacements[(key, row, col)] = _add_global_init_profile_alias(
                            aliases=aliases,
                            symbol_table=symbol_table,
                            global_alias_cache=global_alias_cache,
                            key=key,
                            row=row,
                            col=col,
                            values_by_init=runtime_invariant,
                        )
                        continue
                    pending_composite_entries.append((key, row, col, values))
                    continue
                branch_id = candidates[0]
                sign, alias = _ensure_branch_profile_alias(
                    aliases=aliases,
                    grouped=grouped,
                    symbol_table=symbol_table,
                    sample_profiles=sample_profiles,
                    runtime_groups=runtime_groups,
                    branch_id=branch_id,
                    kind=kind,
                    profile_values=values,
                    base_case=base_case_by_branch.get(branch_id, 0),
                )
                replacements[(key, row, col)] = sign * sp.Symbol(alias)

    unresolved_composite_entries = list(pending_composite_entries)
    made_progress = True
    while unresolved_composite_entries and made_progress:
        made_progress = False
        next_unresolved: list[tuple[str, int, int, list[sp.Expr]]] = []
        for key, row, col, values in unresolved_composite_entries:
            existing_expr = _represent_with_existing_aliases(values, grouped)
            if existing_expr is None:
                next_unresolved.append((key, row, col, values))
                continue
            replacements[(key, row, col)] = existing_expr
            made_progress = True
        unresolved_composite_entries = next_unresolved

    for key, row, col, values in unresolved_composite_entries:
        runtime_invariant = _runtime_invariant_values_by_init(sample_profiles, values)
        if runtime_invariant is not None:
            replacements[(key, row, col)] = _add_global_init_profile_alias(
                aliases=aliases,
                symbol_table=symbol_table,
                global_alias_cache=global_alias_cache,
                key=key,
                row=row,
                col=col,
                values_by_init=runtime_invariant,
            )
            continue
        _add_global_profile_alias(
            aliases=aliases,
            replacements=replacements,
            symbol_table=symbol_table,
            global_alias_cache=global_alias_cache,
            key=key,
            row=row,
            col=col,
            values=values,
        )

    template_payload = json.loads(json.dumps(base_payload))
    for key in ["G_full", "Ihis_full"]:
        base = matrices_by_key[key][0]
        next_matrix: list[list[str]] = []
        for row in range(base.rows):
            next_row: list[str] = []
            for col in range(base.cols):
                replacement = replacements.get((key, row, col))
                if replacement is not None:
                    next_row.append(_expr_to_payload_text(replacement))
                else:
                    next_row.append(_expr_to_payload_text(base[row, col]))
            next_matrix.append(next_row)
        if key == "Ihis_full":
            template_payload[key] = [row[0] for row in next_matrix]
            template_payload["Ihis_full_tagged"] = [row[0] for row in next_matrix]
        else:
            template_payload[key] = next_matrix
            template_payload["G_full_tagged"] = next_matrix

    dep_table = dict(template_payload.get("symbol_dependency_table") or {})
    tagged_dep_table = dict(template_payload.get("symbol_dependency_table_tagged") or dep_table)
    for alias, info in aliases.items():
        dep_table[alias] = _symbol_dependency_for_owner(info["owner"])
        tagged_dep_table[alias] = _symbol_dependency_for_owner(info["owner"])
    template_payload["symbol_dependency_table"] = dep_table
    template_payload["symbol_dependency_table_tagged"] = tagged_dep_table
    _rewrite_direct_retained_stamps_with_aliases(template_payload, replacements)

    return {
        "template_payload": template_payload,
        "aliases": aliases,
        "profiles": profiles,
        "sample_profiles": sample_profiles,
        "branch_ids": [branch_id for branch_id in branch_ids if branch_id not in runtime_groups],
        "runtime_case_groups": list(runtime_groups.values()),
    }


def _attach_multicase_fast_dependency(
    payload: dict,
    *,
    g_dependency: str = "CODE_VARIABLE",
    ihis_dependency: str = "CODE_VARIABLE",
) -> dict:
    """Force multi-case symbol-mux exports down the matrix DAG path.

    The mux path has already proven that cases differ only by source-level
    symbol substitutions.  For C draft generation we only need final
    Gred/Ihisred stage placement, not expanded final expressions, so use small
    synthetic CODE symbols to avoid constructing the large Schur expressions.
    """
    clone = json.loads(json.dumps(payload))
    external_nodes = list(clone.get("external_nodes") or [])
    nr = len(external_nodes)
    g_symbols = [[f"mc_Gred_{row}_{col}" for col in range(nr)] for row in range(nr)]
    ihis_symbols = [f"mc_Ihisred_{row}" for row in range(nr)]
    clone["reduced_dependency_analysis"] = {
        "external_nodes": external_nodes,
        "G_red": g_symbols,
        "G_red_tagged": g_symbols,
        "Ihis_red": ihis_symbols,
        "Ihis_red_tagged": ihis_symbols,
    }
    for table_name in ["symbol_dependency_table", "symbol_dependency_table_tagged"]:
        table = dict(clone.get(table_name) or {})
        for row in g_symbols:
            for symbol in row:
                table[symbol] = g_dependency
        for symbol in ihis_symbols:
            table[symbol] = ihis_dependency
        clone[table_name] = table
    return clone


def _promoted_dependency_for_aliases(aliases: dict[str, dict], *, kind: str | None = None) -> str:
    owners = [
        str(info.get("owner") or "RAM")
        for info in aliases.values()
        if kind is None or info.get("kind") == kind
    ]
    return _symbol_dependency_for_owner(_promote_owner(owners))


def _case_selector_from_profiles(profiles: list[dict]) -> str | None:
    branch_ids: set[str] = set()
    for profile in profiles:
        case_map = profile.get("case_map") or {}
        if len(case_map) != 1:
            return None
        branch_ids.update(str(key) for key in case_map)
    if len(branch_ids) != 1:
        return None
    return f"{_c_identifier_name(next(iter(branch_ids)), 'case')}_case"


def _insert_multi_symbol_mux(draft: str, selector: str, profile_mappings: list[dict[str, str]], base_symbols: list[str]) -> str:
    global_mapping = {symbol: f"{symbol}_global" for symbol in base_symbols}
    draft = _replace_symbol_words(draft, global_mapping)
    declared_names = set(re.findall(r"\bdouble\s+([A-Za-z_]\w*)\s*(?:=|;)", draft))
    declarations = []
    case_symbols = sorted({target for mapping in profile_mappings for target in mapping.values()} | set(base_symbols))
    for name in list(global_mapping.values()) + case_symbols:
        if name not in declared_names:
            declarations.append(f"    double {name} = 0.0;")
            declared_names.add(name)
    if declarations and "STATIC:" in draft:
        draft = draft.replace("STATIC:", "STATIC:\n" + "\n".join(dict.fromkeys(declarations)), 1)
    mux_lines = ["    /* Multi-case payload symbol mux. Select case-specific G parameters before matrix refresh. */"]
    for index, mapping in enumerate(profile_mappings):
        keyword = "if" if index == 0 else "else if"
        mux_lines.append(f"    {keyword} ({selector} == {index}) {{")
        for symbol in base_symbols:
            mux_lines.append(f"        {global_mapping[symbol]} = {mapping.get(symbol, symbol)};")
        mux_lines.append("    }")
    mux_lines.extend([
        "    else {",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        f"                       \"Unknown multi-case selector %d for {selector}.\", {selector});",
        "    }",
        "",
    ])
    marker = "    /* Runtime refresh. Use set_CODE for matrices touched in CODE; do not write MATRIX_.p directly. */"
    if marker in draft:
        draft = draft.replace(marker, "\n".join(mux_lines) + marker, 1)
    else:
        draft = draft.replace("BEGIN_T0:", "BEGIN_T0:\n" + "\n".join(mux_lines), 1)
    header = [
        "/* Multi-case payload symbol mux draft.",
        "   G_full/Ihis_full profiles were compared before structured export;",
        "   only the selected symbols are multiplexed, and the structured matrix flow is generated once. */",
    ]
    return _prepend_c_header_after_includes(draft, header)


def _try_build_payload_symbol_mux_response(payload: dict) -> dict | None:
    profiles = payload.get("case_profiles") or []
    if len(profiles) < 2:
        return None
    selector = _case_selector_from_profiles(profiles)
    if not selector:
        return None
    base_payload = profiles[0].get("payload")
    if not isinstance(base_payload, dict):
        return None
    profile_mappings: list[dict[str, str]] = [{}]
    for profile in profiles[1:]:
        case_payload = profile.get("payload")
        if not isinstance(case_payload, dict):
            return None
        topology_keys = ["all_nodes", "external_nodes", "internal_nodes", "ground_nodes"]
        if any(case_payload.get(key) != base_payload.get(key) for key in topology_keys):
            return None
        mapping = _find_symbol_mapping(base_payload, case_payload)
        if mapping is None:
            return None
        profile_mappings.append(mapping)
    base_symbols = sorted({symbol for mapping in profile_mappings for symbol in mapping.keys()})
    if not base_symbols:
        return None
    normalized_payload = _replace_payload_symbols(base_payload, {symbol: f"{symbol}_global" for symbol in base_symbols})
    normalized_payload = _attach_multicase_fast_dependency(normalized_payload)
    request_payload = {
        **normalized_payload,
        "mode": "structured_formula",
        "display_mode": payload.get("display_mode") or normalized_payload.get("display_mode") or "compact",
        "simplify_level": payload.get("simplify_level") or normalized_payload.get("simplify_level") or "light",
        "assume_spd": payload.get("assume_spd", normalized_payload.get("assume_spd", True)),
        "use_suggested_order": payload.get("use_suggested_order", normalized_payload.get("use_suggested_order", False)),
    }
    result = build_optimized_response(request_payload)
    draft = _insert_multi_symbol_mux(
        result["structured"]["c_draft"],
        selector,
        profile_mappings,
        base_symbols,
    )
    return {
        "ok": True,
        "mode": "multi_case_c_export",
        "case_id_symbol": selector,
        "case_profiles": [
            {
                "name": profile.get("name") or f"Case {index + 1}",
                "comment": profile.get("comment") or "",
                "case_map": profile.get("case_map") or {},
            }
            for index, profile in enumerate(profiles)
        ],
        "warnings": result.get("warnings") or [],
        "multi_case": {
            "external_nodes": result.get("external_nodes") or [],
            "effective_internal_nodes": result.get("effective_internal_nodes") or [],
            "block_type": (result.get("structured") or {}).get("block_type"),
            "profile_count": len(profiles),
            "c_draft": _ensure_static_blank_line(draft),
            "fast_path": "payload_symbol_mux",
        },
    }


def _try_build_scalar_mux_c_draft(profile_results: list[dict]) -> str | None:
    if len(profile_results) < 2:
        return None
    selector = _single_branch_case_selector(profile_results)
    if not selector:
        return None
    case_matrices = []
    for item in profile_results:
        result = item["result"]
        matrices = {
            "Grr": _result_matrix(result, "G_rr"),
            "Grk": _result_matrix(result, "G_ri"),
            "Gkr": _result_matrix(result, "G_ir"),
            "Gkk": _result_matrix(result, "G_ii"),
            "Gdirect": _result_direct_matrix(result, "Gred_direct"),
        }
        case_matrices.append(matrices)
    symbol_sets = [_single_symbol_set(matrices) for matrices in case_matrices]
    common = set.intersection(*symbol_sets) if symbol_sets else set()
    varying = [sorted(symbols - common) for symbols in symbol_sets]
    if any(len(items) != 1 for items in varying):
        return None
    case_symbols = [items[0] for items in varying]
    if len(set(case_symbols)) != len(case_symbols):
        return None

    base_symbol = case_symbols[0]
    global_symbol = "G_global"
    base_draft = profile_results[0]["result"]["structured"]["c_draft"]
    draft = re.sub(rf"\b{re.escape(base_symbol)}\b", global_symbol, base_draft)
    global_decl = f"double {global_symbol} = 0.0;"
    declarations = "\n".join(f"    double {symbol} = 0.0;" for symbol in case_symbols)
    if global_decl in draft:
        draft = draft.replace(global_decl, f"{global_decl}\n{declarations}", 1)
    else:
        draft = draft.replace("STATIC:", f"STATIC:\n    {global_decl}\n{declarations}", 1)
    mux_lines = [
        "    /* Multi-case scalar parameter mux. The host provides the branch case selector before CODE. */",
    ]
    for index, item in enumerate(profile_results):
        case_map = item["profile"].get("case_map") or {}
        case_index = next(iter(case_map.values()))
        keyword = "if" if index == 0 else "else if"
        mux_lines.extend([
            f"    {keyword} ({selector} == {int(case_index)}) {{",
            f"        {global_symbol} = {case_symbols[index]};",
            "    }",
        ])
    mux_lines.extend([
        "    else {",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        f"                       \"Unknown multi-case selector %d for {selector}.\", {selector});",
        "    }",
        "",
    ])
    marker = "    /* Runtime refresh. Use set_CODE for matrices touched in CODE; do not write MATRIX_.p directly. */"
    if marker in draft:
        draft = draft.replace(marker, "\n".join(mux_lines) + marker, 1)
    else:
        draft = draft.replace("BEGIN_T0:", "BEGIN_T0:\n" + "\n".join(mux_lines), 1)
    header = [
        "/* Multi-case scalar parameter mux draft.",
        "   Case-specific symbols are mapped to G_global, then the normal structured C draft is reused. */",
    ]
    return _prepend_c_header_after_includes(draft, header)


def _prepend_c_header_after_includes(draft: str, header: list[str]) -> str:
    lines = draft.splitlines()
    include_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines):
        if not line.startswith("#include "):
            body_start = index
            break
        include_lines.append(line)
    else:
        body_start = len(lines)
    body = "\n".join(lines[body_start:])
    pieces = include_lines + header
    if body:
        pieces.append(body)
    return "\n".join(pieces)


def _declared_c_names(draft: str) -> set[str]:
    return set(re.findall(r"\b(?:double|int)\s+([A-Za-z_]\w*)\s*(?:=|;)", draft))


def _insert_after_label(draft: str, label: str, lines: list[str]) -> str:
    if not lines or label not in draft:
        return draft
    return draft.replace(label, label + "\n" + "\n".join(lines), 1)


def _multicase_local_case_lines(case_id_symbol: str, profiles: list[dict], branch_ids: list[str]) -> list[str]:
    if not branch_ids:
        return []
    lines = [
        "    /* Decode the optional global case selector into per-element local cases. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for index, profile in enumerate(profiles):
        lines.append(f"    case {index}: /* {_profile_case_comment(profile, index)} */")
        for branch_id in branch_ids:
            local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
            lines.append(f"        {local_name} = {_profile_case_index(profile, branch_id, 0)};")
        lines.append("        break;")
    lines.append("    default:")
    for branch_id in branch_ids:
        local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        lines.append(f"        {local_name} = {_profile_case_index(profiles[0], branch_id, 0)};")
    lines.append("        break;")
    lines.append("    }")
    return lines


def _alias_assignment_lines(aliases: dict[str, dict], wanted_owner: str, case_id_symbol: str = "case_id") -> list[str]:
    selected = [
        (alias, info)
        for alias, info in aliases.items()
        if info.get("owner") == wanted_owner
    ]
    if not selected:
        return []
    lines = [f"    /* Resolve {wanted_owner} multi-case effective aliases as full values, never deltas. */"]
    grouped: dict[str, list[tuple[str, dict, dict[int, sp.Expr]]]] = {}
    for alias, info in selected:
        branch_id = info["branch_id"]
        selector = info.get("selector")
        if selector == "global":
            local_name = case_id_symbol
        elif selector == "runtime":
            local_name = str(info.get("case_id_symbol") or f"runtime_{_c_identifier_name(branch_id, 'branch')}_case_id")
        else:
            local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        case_values = {int(case_index): _parse_expr(expr) for case_index, expr in (info.get("case_values") or {}).items()}
        grouped.setdefault(local_name, []).append((alias, info, case_values))

    for local_name, entries in grouped.items():
        case_indices = sorted({case_index for _alias, _info, case_values in entries for case_index in case_values})
        lines.append(f"    switch ({local_name}) {{")
        for case_index in case_indices:
            lines.append(f"    case {case_index}:")
            for alias, _info, case_values in entries:
                default_expr = case_values[min(case_values)] if case_values else sp.Integer(0)
                lines.append(f"        {alias} = {_ccode(case_values.get(case_index, default_expr))};")
            lines.append("        break;")
        lines.append("    default:")
        for alias, _info, case_values in entries:
            default_expr = case_values[min(case_values)] if case_values else sp.Integer(0)
            lines.append(f"        {alias} = {_ccode(default_expr)};")
        lines.append("        break;")
        lines.append("    }")
    return lines


def _insert_multicase_alias_layer(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    branch_ids: list[str],
    aliases: dict[str, dict],
) -> str:
    declared = _declared_c_names(draft)
    declarations: list[str] = []
    for branch_id in branch_ids:
        local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        if local_name not in declared:
            declarations.append(f"    int {local_name} = 0;")
            declared.add(local_name)
    original_symbols: set[str] = set()
    for info in aliases.values():
        for expr in (info.get("case_values") or {}).values():
            original_symbols.update(symbol.name for symbol in _parse_expr(expr).free_symbols)
    for symbol in sorted(original_symbols):
        if symbol not in declared:
            declarations.append(f"    double {symbol} = 0.0;")
            declared.add(symbol)
    if declarations and "STATIC:" in draft:
        draft = draft.replace("STATIC:", "STATIC:\n" + "\n".join(declarations), 1)

    ram_lines = (
        _multicase_local_case_lines(case_id_symbol, profiles, branch_ids)
        + _alias_assignment_lines(aliases, "RAM", case_id_symbol)
    )
    draft = _insert_after_label(draft, "RAM_PASS1:", ram_lines)

    code_lines = _alias_assignment_lines(aliases, "CODE", case_id_symbol) + _alias_assignment_lines(aliases, "CODE_PER_STEP", case_id_symbol)
    marker = "    /* Runtime refresh. Use set_CODE for matrices touched in CODE; do not write MATRIX_.p directly. */"
    if code_lines and marker in draft:
        draft = draft.replace(marker, "\n".join(code_lines) + "\n" + marker, 1)
    elif code_lines:
        draft = draft.replace("BEGIN_T0:", "BEGIN_T0:\n" + "\n".join(code_lines), 1)

    header = [
        "/* Multi-case alias-template C draft.",
        "   Each case-resolved alias is assigned a full source value first;",
        "   the normal structured matrix DAG is generated exactly once. */",
    ]
    return _prepend_c_header_after_includes(draft, header)


def _profile_alias_substitutions(profile: dict, aliases: dict[str, dict], profile_index: int | None = None) -> dict[sp.Symbol, sp.Expr]:
    substitutions: dict[sp.Symbol, sp.Expr] = {}
    for alias, info in aliases.items():
        if info.get("selector") == "runtime":
            substitutions[sp.Symbol(alias)] = sp.Symbol(alias)
            continue
        case_values = {int(case_index): _parse_expr(expr) for case_index, expr in (info.get("case_values") or {}).items()}
        if info.get("selector") == "global":
            local_case = int(profile_index or 0)
        else:
            branch_id = info.get("branch_id") or ""
            local_case = _profile_case_index(profile, branch_id, 0)
        if local_case not in case_values and case_values:
            local_case = min(case_values)
        substitutions[sp.Symbol(alias)] = case_values.get(local_case, sp.Integer(0))
    return substitutions


def _profile_final_expr(expr: sp.Expr, profile: dict, aliases: dict[str, dict], profile_index: int | None = None) -> sp.Expr:
    return sp.sympify(expr).xreplace(_profile_alias_substitutions(profile, aliases, profile_index))


def _case_resolved_matrix_is_diagonal(matrix: sp.Matrix, profile: dict, aliases: dict[str, dict], profile_index: int) -> bool:
    matrix = sp.Matrix(matrix)
    if matrix.rows != matrix.cols or matrix.rows == 0:
        return False
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            if row == col:
                continue
            if not _expr_equal_light(_profile_final_expr(matrix[row, col], profile, aliases, profile_index), 0):
                return False
    return True


def _template_gkk_from_payload(payload: dict) -> sp.Matrix:
    G, _, _, _, node_order, _, internal_nodes, _ = _partition_payload(payload)
    if not internal_nodes:
        return sp.zeros(0, 0)
    indices = [node_order.index(node) for node in internal_nodes]
    return G.extract(indices, indices)


def _find_w_code_sym3_inverse_block(draft: str) -> tuple[int, int, str] | None:
    marker = "    mat_3x3_sym_inv_code("
    marker_index = draft.find(marker)
    if marker_index < 0:
        return None
    start = draft.rfind("    double W_code_11 = 0.0;\n", 0, marker_index)
    end_marker = "    set_CODE(&W_code, 2, 2, W_code_33);\n"
    end = draft.find(end_marker, marker_index)
    if start < 0 or end < 0:
        return None
    end += len(end_marker)
    return start, end, draft[start:end]


def _indent_c_block(block: str, spaces: int) -> list[str]:
    prefix = " " * spaces
    return [prefix + line if line else line for line in block.rstrip("\n").splitlines()]


def _diagonal_w_code_lines(size: int, indent: int) -> list[str]:
    prefix = " " * indent
    lines: list[str] = []
    for row in range(size):
        for col in range(size):
            value = f"1.0 / get_CODE(&Gkk_code, {row}, {row})" if row == col else "0.0"
            lines.append(f"{prefix}set_CODE(&W_code, {row}, {col}, {value});")
    return lines


def _apply_multicase_conditional_diagonal_w_builder(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    gkk_template: sp.Matrix,
) -> str:
    gkk_template = sp.Matrix(gkk_template)
    if not profiles or gkk_template.rows != 3 or gkk_template.cols != 3:
        return draft
    inverse_block = _find_w_code_sym3_inverse_block(draft)
    if inverse_block is None:
        return draft

    diagonal_cases: list[int] = []
    fallback_cases: list[int] = []
    for index, profile in enumerate(profiles):
        if _case_resolved_matrix_is_diagonal(gkk_template, profile, aliases, index):
            diagonal_cases.append(index)
        else:
            fallback_cases.append(index)
    if not diagonal_cases:
        return draft

    start, end, original_block = inverse_block
    if not fallback_cases:
        replacement = "\n".join(
            ["    /* Case-resolved diagonal Gkk fast path: W = inv(diag(Gkk)). */"]
            + _diagonal_w_code_lines(3, 4)
        ) + "\n"
        return draft[:start] + replacement + draft[end:]

    lines = [
        "    /* Case-resolved diagonal Gkk fast path: use direct reciprocal for diagonal cases. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for index in diagonal_cases:
        lines.append(f"    case {index}:")
    lines.append("    {")
    lines.extend(_diagonal_w_code_lines(3, 8))
    lines.append("        break;")
    lines.append("    }")
    for index in fallback_cases:
        lines.append(f"    case {index}:")
    lines.append("    {")
    lines.extend(_indent_c_block(original_block, 4))
    lines.append("        break;")
    lines.append("    }")
    lines.append("    default:")
    lines.append("    {")
    lines.extend(_indent_c_block(original_block, 4))
    lines.append("        break;")
    lines.append("    }")
    lines.append("    }")
    replacement = "\n".join(lines) + "\n"
    return draft[:start] + replacement + draft[end:]


def _retained_layout_profiles_from_finalization(profile_set, super_node_ids: Sequence[str]) -> tuple[list[dict], list[str]]:
    case_profiles = list(getattr(profile_set, "case_profiles", []) or [])
    if not case_profiles:
        return [], list(super_node_ids)
    final_sets = [set(str(node) for node in profile.final_node_order) for profile in case_profiles]
    common = set.intersection(*final_sets) if final_sets else set()
    super_order = [str(node) for node in super_node_ids]
    common_order = [node for node in super_order if node in common]
    optional_order = [node for node in super_order if node not in common and any(node in final_set for final_set in final_sets)]
    compact_super_order = common_order + optional_order

    grouped: dict[tuple[str, ...], dict] = {}
    for index, profile in enumerate(case_profiles):
        active = tuple([node for node in common_order if node in profile.final_node_order] + [
            node for node in optional_order if node in profile.final_node_order
        ])
        if active not in grouped:
            grouped[active] = {
                "case_ids": [],
                "ordered_active_nodes": list(active),
                "nr_active": len(active),
                "super_to_active_index": {
                    node: active_index
                    for active_index, node in enumerate(active)
                },
            }
        grouped[active]["case_ids"].append(index)

    profiles = sorted(grouped.values(), key=lambda item: (-item["nr_active"], item["case_ids"]))
    if len(profiles) == 2:
        profiles[0]["profile_id"] = "PROFILE_Y"
        profiles[1]["profile_id"] = "PROFILE_D"
    else:
        for index, item in enumerate(profiles):
            item["profile_id"] = f"PROFILE_{index}"
    return profiles, compact_super_order


def _with_reordered_external_nodes(payload: dict, external_nodes: Sequence[str]) -> dict:
    clone = dict(payload)
    clone["external_nodes"] = [str(node) for node in external_nodes]
    return clone


def _replace_enum_for_retained_layouts(draft: str, profiles: list[dict], nk: int) -> str:
    if not profiles:
        return draft
    enum_parts = [f"{profile['profile_id']} = {index}" for index, profile in enumerate(profiles)]
    dim_parts = [f"NR_{profile['profile_id'].removeprefix('PROFILE_')} = {profile['nr_active']}" for profile in profiles]
    enum_line = f"enum {{ {', '.join(enum_parts)}, {', '.join(dim_parts)}, NK = {nk} }};"
    return re.sub(r"enum \{ NR = \d+, NK = \d+ \};", enum_line, draft, count=1)


def _case_condition_from_ids(case_id_symbol: str, case_ids: Sequence[int]) -> str:
    return " || ".join(f"{case_id_symbol} == {case_id}" for case_id in case_ids) or "FALSE"


def _insert_retained_profile_selection(draft: str, *, case_id_symbol: str, profiles: list[dict]) -> str:
    if not profiles or "int nr_active = " in draft:
        return draft
    largest = profiles[0]
    static_lines = [
        f"    int retained_profile = {largest['profile_id']};",
        f"    int nr_active = NR_{largest['profile_id'].removeprefix('PROFILE_')};",
    ]
    draft = draft.replace("    /* Runtime matrix objects */", "\n".join(static_lines) + "\n    /* Runtime matrix objects */", 1)
    selection = [
        "    /* Retained layout is selected during initialization. Runtime profile switching is not supported. */",
    ]
    for index, profile in enumerate(profiles):
        keyword = "if" if index == 0 else "else if"
        selection.append(f"    {keyword} ({_case_condition_from_ids(case_id_symbol, profile['case_ids'])}) {{")
        selection.append(f"        retained_profile = {profile['profile_id']};")
        selection.append(f"        nr_active = NR_{profile['profile_id'].removeprefix('PROFILE_')};")
        selection.append("    }")
    marker = "    int err = 0;"
    return draft.replace(marker, "\n".join(selection) + "\n" + marker, 1)


def _apply_active_matrix_dimensions(draft: str) -> str:
    replacements = {
        "matrixDim(&Grr_code, NR, NR)": "matrixDim(&Grr_code, nr_active, nr_active)",
        "matrixDim(&Grk_code, NR, NK)": "matrixDim(&Grk_code, nr_active, NK)",
        "matrixDim(&Gkr_code, NK, NR)": "matrixDim(&Gkr_code, NK, nr_active)",
        "matrixDim(&Gred_code, NR, NR)": "matrixDim(&Gred_code, nr_active, nr_active)",
        "matrixDim(&Ihisr_code, NR, 1)": "matrixDim(&Ihisr_code, nr_active, 1)",
        "matrixDim(&Ihisred_code, NR, 1)": "matrixDim(&Ihisred_code, nr_active, 1)",
        "matrixDim(&Vr_code, NR, 1)": "matrixDim(&Vr_code, nr_active, 1)",
        "matrixDim(&tmp_Grk_W_code, NR, NK)": "matrixDim(&tmp_Grk_W_code, nr_active, NK)",
        "matrixDim(&tmp_Grk_W_Gkr_code, NR, NR)": "matrixDim(&tmp_Grk_W_Gkr_code, nr_active, nr_active)",
        "matrixDim(&tmp_Grk_W_Ihisk_code, NR, 1)": "matrixDim(&tmp_Grk_W_Ihisk_code, nr_active, 1)",
        "matrixDim(&tmp_W_Gkr_code, NK, NR)": "matrixDim(&tmp_W_Gkr_code, NK, nr_active)",
    }
    for old, new in replacements.items():
        draft = draft.replace(old, new)
    return draft


def _apply_active_ram_overlay_dimension(draft: str, *, y_profile: dict, d_profile: dict) -> str:
    nr_d = int(d_profile["nr_active"])
    draft = re.sub(
        r"for \(int row = 0; row < \d+; row\+\+\) \{\n        for \(int col = 0; col < \d+; col\+\+\) \{\n            g_mat_over\[row\]\[col\] = 0\.0;",
        "for (int row = 0; row < nr_active; row++) {\n        for (int col = 0; col < nr_active; col++) {\n            g_mat_over[row][col] = 0.0;",
        draft,
    )
    draft = re.sub(r"setupGMatrix\(\d+\);", "setupGMatrix(nr_active);", draft, count=1)

    lines = draft.splitlines()
    guarded: list[str] = []
    pending: list[str] = []
    nods_pattern = re.compile(r"^    g_mat_nods\[(?P<index>\d+)\] = ")
    over_pattern = re.compile(r"^    g_mat_over\[(?P<row>\d+)\]\[(?P<col>\d+)\] = ")

    def needs_guard(line: str) -> bool:
        nods = nods_pattern.match(line)
        if nods:
            return int(nods.group("index")) >= nr_d
        over = over_pattern.match(line)
        if over:
            return int(over.group("row")) >= nr_d or int(over.group("col")) >= nr_d
        return False

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        guarded.append(f"    if (retained_profile == {y_profile['profile_id']}) {{")
        guarded.extend("    " + line for line in pending)
        guarded.append("    }")
        pending = []

    for line in lines:
        if needs_guard(line):
            pending.append(line)
            continue
        flush_pending()
        guarded.append(line)
    flush_pending()
    return "\n".join(guarded) + ("\n" if draft.endswith("\n") else "")


def _guard_optional_retained_set_code_lines(draft: str, *, y_profile: dict, d_profile: dict) -> str:
    nr_d = int(d_profile["nr_active"])
    condition = " || ".join(f"retained_profile == {y_profile['profile_id']}" for _ in [0])
    lines = draft.splitlines()
    guarded: list[str] = []
    pattern = re.compile(r"^    set_CODE\(&(?P<matrix>Gkr_code|Grk_code|Grr_code|Gred_code|Ihisr_code|Ihisred_code|Vr_code|tmp_W_Gkr_code), (?P<row>\d+), (?P<col>\d+),")
    pending: list[str] = []

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        guarded.append(f"    if ({condition}) {{")
        guarded.extend("    " + line for line in pending)
        guarded.append("    }")
        pending = []

    for line in lines:
        match = pattern.match(line)
        needs_guard = False
        if match:
            matrix = match.group("matrix")
            row = int(match.group("row"))
            col = int(match.group("col"))
            if matrix in {"Gkr_code", "tmp_W_Gkr_code"}:
                needs_guard = col >= nr_d
            elif matrix == "Vr_code":
                needs_guard = row >= nr_d
            else:
                needs_guard = row >= nr_d or col >= nr_d
        if needs_guard:
            pending.append(line)
            continue
        flush_pending()
        guarded.append(line)
    flush_pending()
    return "\n".join(guarded) + ("\n" if draft.endswith("\n") else "")


def _guard_profile_y_matrix_lifecycle_lines(draft: str, *, y_profile: dict) -> str:
    y_only_names = {
        "Grr_dyn_code",
        "Grk_dyn_code",
        "Gkr_dyn_code",
        "Gred_dyn_code",
        "tmp_Grk_W_dyn_code",
        "tmp_Grk_W_Gkr_dyn_code",
    }
    lines = draft.splitlines()
    guarded: list[str] = []
    pending: list[str] = []

    def is_y_only_lifecycle(line: str) -> bool:
        stripped = line.strip()
        return any(
            stripped.startswith(f"err += matrixDim(&{name},")
            or stripped == f"matrix_register(&{name});"
            or stripped == f"conditionMatrixForCODE(&{name});"
            for name in y_only_names
        )

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        indent = pending[0][: len(pending[0]) - len(pending[0].lstrip())]
        guarded.append(f"{indent}if (retained_profile == {y_profile['profile_id']}) {{")
        guarded.extend(indent + "    " + line[len(indent):] if line.startswith(indent) else indent + "    " + line for line in pending)
        guarded.append(f"{indent}}}")
        pending = []

    for line in lines:
        if is_y_only_lifecycle(line):
            pending.append(line)
            continue
        flush_pending()
        guarded.append(line)
    flush_pending()
    return "\n".join(guarded) + ("\n" if draft.endswith("\n") else "")


def _guard_profile_y_dynamic_schur_lines(draft: str, *, y_profile: dict) -> str:
    y_only_names = (
        "Grr_dyn_code",
        "Grk_dyn_code",
        "Gkr_dyn_code",
        "Gred_dyn_code",
        "tmp_Grk_W_dyn_code",
        "tmp_Grk_W_Gkr_dyn_code",
    )
    lines = draft.splitlines()
    guarded: list[str] = []
    pending: list[str] = []

    def is_y_only_runtime(line: str) -> bool:
        stripped = line.strip()
        if not stripped or stripped.startswith("/*") or stripped.startswith("*"):
            return False
        if (
            stripped.startswith("err += matrixDim(")
            or stripped.startswith("matrix_register(")
            or stripped.startswith("conditionMatrixForCODE(")
        ):
            return False
        return any(f"&{name}" in stripped for name in y_only_names)

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        indent = pending[0][: len(pending[0]) - len(pending[0].lstrip())]
        guarded.append(f"{indent}if (retained_profile == {y_profile['profile_id']}) {{")
        guarded.extend(indent + "    " + line[len(indent):] if line.startswith(indent) else indent + "    " + line for line in pending)
        guarded.append(f"{indent}}}")
        pending = []

    for line in lines:
        if is_y_only_runtime(line):
            pending.append(line)
            continue
        flush_pending()
        guarded.append(line)
    flush_pending()
    return "\n".join(guarded) + ("\n" if draft.endswith("\n") else "")


def _apply_retained_layout_profile_compaction(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    nk: int,
) -> str:
    if len(profiles) < 2:
        return draft
    profiles = sorted(profiles, key=lambda item: -int(item["nr_active"]))
    if int(profiles[0]["nr_active"]) == int(profiles[-1]["nr_active"]):
        return draft
    draft = _replace_enum_for_retained_layouts(draft, profiles, nk)
    draft = _insert_retained_profile_selection(draft, case_id_symbol=case_id_symbol, profiles=profiles)
    draft = _apply_active_matrix_dimensions(draft)
    if nk == 0:
        draft = _apply_active_ram_overlay_dimension(draft, y_profile=profiles[0], d_profile=profiles[-1])
    draft = _guard_optional_retained_set_code_lines(draft, y_profile=profiles[0], d_profile=profiles[-1])
    draft = _guard_profile_y_matrix_lifecycle_lines(draft, y_profile=profiles[0])
    draft = _guard_profile_y_dynamic_schur_lines(draft, y_profile=profiles[0])
    return draft


def _profile_symbol_table(profile: dict) -> dict:
    payload = profile.get("payload") or {}
    return dict(payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {})


def _case_condition(case_id_symbol: str, case_indices: Sequence[int]) -> str:
    return " || ".join(f"{case_id_symbol} == {index}" for index in case_indices) if case_indices else "FALSE"


def _var_g_name(nodes: Sequence[str], row: int, col: int) -> str:
    a = _c_identifier_name(nodes[row], f"N{row + 1}")
    b = _c_identifier_name(nodes[col], f"N{col + 1}")
    return f"varG_{a}_{b}"


def _conditional_final_g_plans(
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    template_gred: sp.Matrix,
    external_nodes: list[str],
) -> list[dict]:
    nr = len(external_nodes)
    entry_plans: list[dict] = []
    for row in range(nr):
        for col in range(row, nr):
            per_case = []
            for index, profile in enumerate(profiles):
                expr = _profile_final_expr(template_gred[row, col], profile, aliases, index)
                stage = _expr_stage(expr, _profile_symbol_table(profile))
                per_case.append({"index": index, "expr": expr, "stage": stage})
            if all(_expr_equal_light(item["expr"], 0) for item in per_case):
                continue
            ram_cases = [item for item in per_case if item["stage"] == "RAM"]
            code_cases = [item for item in per_case if item["stage"] != "RAM"]
            entry_plans.append({
                "row": row,
                "col": col,
                "var": _var_g_name(external_nodes, row, col),
                "ram_cases": ram_cases,
                "code_cases": code_cases,
                "condition": _case_condition(case_id_symbol, [item["index"] for item in code_cases]),
            })
    return entry_plans


def _has_mixed_final_g_stages(
    template_gred: sp.Matrix,
    profiles: list[dict],
    aliases: dict[str, dict],
) -> bool:
    for row in range(template_gred.rows):
        for col in range(row, template_gred.cols):
            stages = {
                _expr_stage(_profile_final_expr(template_gred[row, col], profile, aliases, index), _profile_symbol_table(profile))
                for index, profile in enumerate(profiles)
            }
            if len(stages) > 1:
                return True
    return False


def _final_g_stage_analysis_is_within_budget(template_gred: sp.Matrix, max_ops: int = 2000) -> bool:
    total_ops = 0
    for expr in template_gred:
        total_ops += int(sp.count_ops(expr))
        if total_ops > max_ops:
            return False
    return True


def _build_conditional_final_gvalue_draft(
    *,
    case_id_symbol: str,
    profiles: list[dict],
    branch_ids: list[str],
    aliases: dict[str, dict],
    template_gred: sp.Matrix,
    template_ihis: sp.Matrix,
    external_nodes: list[str],
    symbol_table: dict,
) -> tuple[str, list[dict]]:
    nr = len(external_nodes)
    declared_symbols: set[str] = set()
    for info in aliases.values():
        for expr in (info.get("case_values") or {}).values():
            declared_symbols.update(symbol.name for symbol in _parse_expr(expr).free_symbols)
    for row in range(template_gred.rows):
        for col in range(template_gred.cols):
            declared_symbols.update(symbol.name for symbol in sp.sympify(template_gred[row, col]).free_symbols)
    for row in range(template_ihis.rows):
        declared_symbols.update(symbol.name for symbol in sp.sympify(template_ihis[row, 0]).free_symbols)
    declared_symbols.difference_update(aliases.keys())

    entry_plans = _conditional_final_g_plans(
        case_id_symbol=case_id_symbol,
        profiles=profiles,
        aliases=aliases,
        template_gred=template_gred,
        external_nodes=external_nodes,
    )

    lines = [
        "#include <matrixLIB.h>",
        "/* Multi-case alias-template C draft with case-conditional final GValues.",
        "   case_id is assumed fixed before simulation; runtime case switching is not supported.",
        "   Each case uses full values. No base + delta compensation is generated. */",
        f"enum {{ NR = {nr}, NK = 0 }};",
        "",
        "STATIC:",
    ]
    for branch_id in branch_ids:
        lines.append(f"    int {_c_identifier_name(branch_id, 'branch')}_case_id = 0;")
    for symbol in sorted(declared_symbols):
        if _c_identifier_name(symbol, symbol) == symbol:
            lines.append(f"    double {symbol} = 0.0;")
    for alias in sorted(aliases):
        lines.append(f"    double {alias} = 0.0;")
    lines.extend([
        "",
        "RAM_PASS1:",
        "    int err = 0;",
        "    /* Decode global case_id into local element cases. */",
        f"    switch ({case_id_symbol}) {{",
    ])
    for index, profile in enumerate(profiles):
        lines.append(f"    case {index}: /* {_profile_case_comment(profile, index)} */")
        for branch_id in branch_ids:
            lines.append(f"        {_c_identifier_name(branch_id, 'branch')}_case_id = {_profile_case_index(profile, branch_id, 0)};")
        lines.append("        break;")
    lines.append("    default:")
    for branch_id in branch_ids:
        lines.append(f"        {_c_identifier_name(branch_id, 'branch')}_case_id = {_profile_case_index(profiles[0], branch_id, 0)};")
    lines.extend([
        "        break;",
        "    }",
        "",
        "    g_mat_nods[0] = getNodeNum(comp, \"" + external_nodes[0] + "\");" if nr else "",
    ])
    for index, node in enumerate(external_nodes[1:], start=1):
        lines.append(f"    g_mat_nods[{index}] = getNodeNum(comp, \"{node}\");")
    if nr:
        lines.extend([
            "    for (int row = 0; row < NR; row++) {",
            "        for (int col = 0; col < NR; col++) {",
            "            g_mat_over[row][col] = 0.0;",
            "        }",
            "    }",
            f"    switch ({case_id_symbol}) {{",
        ])
        for index, profile in enumerate(profiles):
            lines.append(f"    case {index}:")
            any_ram = False
            for plan in entry_plans:
                ram_case = next((item for item in plan["ram_cases"] if item["index"] == index), None)
                if ram_case is None:
                    if any(item["index"] == index for item in plan["code_cases"]):
                        lines.append(f"        /* CODE-owned case: no RAM stamp for {plan['var']}. */")
                    continue
                value = _ccode(ram_case["expr"])
                row = plan["row"]
                col = plan["col"]
                lines.append(f"        g_mat_over[{row}][{col}] = {value};")
                if row != col:
                    lines.append(f"        g_mat_over[{col}][{row}] = {value};")
                any_ram = True
            if not any_ram:
                lines.append("        /* No RAM-owned final G entries in this case. */")
            lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
            f"    setupGMatrix({nr});",
        ])
    lines.extend([
        "",
        "GVALUES:",
    ])
    gvalue_lines = []
    gvalue_conditions = []
    for plan in entry_plans:
        if not plan["code_cases"]:
            continue
        row = plan["row"]
        col = plan["col"]
        condition = plan["condition"]
        gvalue_lines.append(
            f"    double {plan['var']} = createGValue(\"{plan['var']}\", \"{external_nodes[row]}\", \"{external_nodes[col]}\", 0, \"{condition}\");"
        )
        gvalue_conditions.append({
            "var": plan["var"],
            "row": row,
            "col": col,
            "condition": condition,
        })
    lines.extend(gvalue_lines or ["    /* No CODE-owned final G entries in any case. */"])
    lines.extend([
        "",
        "CODE:",
        "BEGIN_T0:",
        "    /* Resolve multi-case effective aliases as full values, never deltas. */",
    ])
    lines.extend(_alias_assignment_lines(aliases, "CODE") + _alias_assignment_lines(aliases, "CODE_PER_STEP"))
    if any(info.get("owner") == "RAM" for info in aliases.values()):
        lines.extend(_alias_assignment_lines(aliases, "RAM"))
    lines.extend([
        "",
        f"    switch ({case_id_symbol}) {{",
    ])
    for index, profile in enumerate(profiles):
        lines.append(f"    case {index}:")
        any_code = False
        for plan in entry_plans:
            code_case = next((item for item in plan["code_cases"] if item["index"] == index), None)
            if code_case is None:
                continue
            lines.append(f"        {plan['var']} = {_ccode(code_case['expr'])};")
            any_code = True
        if not any_code:
            lines.append("        /* RAM-owned case: no active varG assignment. */")
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
        "",
        "    /* Node injection currents follow retained-node order. */",
    ])
    for index, node in enumerate(external_nodes):
        ihis_expr = sp.sympify(template_ihis[index, 0]) if index < template_ihis.rows else sp.Integer(0)
        lines.append(f"    Inj{_c_identifier_name(node, f'N{index + 1}')} = {_ccode(ihis_expr)};")
    lines.extend([
        "",
        "T1_T2:",
        "    /* This conditional GValue export assumes no runtime case switching. */",
    ])
    return "\n".join(line for line in lines if line != ""), gvalue_conditions


def _conditional_ram_stamp_block(
    *,
    case_id_symbol: str,
    profiles: list[dict],
    external_nodes: list[str],
    entry_plans: list[dict],
) -> list[str]:
    nr = len(external_nodes)
    if not nr or not any(plan["ram_cases"] for plan in entry_plans):
        return ["    /* No RAM-side G entries: no fixed G overlay is registered. */"]
    lines = [
        "    /* Case-conditional RAM final-G stamp. CODE-owned cases do not receive a RAM base. */",
    ]
    for index, node in enumerate(external_nodes):
        lines.append(f"    g_mat_nods[{index}] = getNodeNum(comp, \"{node}\");")
    lines.extend([
        "    for (int row = 0; row < NR; row++) {",
        "        for (int col = 0; col < NR; col++) {",
        "            g_mat_over[row][col] = 0.0;",
        "        }",
        "    }",
        f"    switch ({case_id_symbol}) {{",
    ])
    for index, _profile in enumerate(profiles):
        lines.append(f"    case {index}:")
        any_ram = False
        for plan in entry_plans:
            ram_case = next((item for item in plan["ram_cases"] if item["index"] == index), None)
            if ram_case is None:
                if any(item["index"] == index for item in plan["code_cases"]):
                    lines.append(f"        /* CODE-owned case: no RAM stamp for {plan['var']}. */")
                continue
            value = _ccode(ram_case["expr"])
            row = plan["row"]
            col = plan["col"]
            lines.append(f"        g_mat_over[{row}][{col}] = {value};")
            if row != col:
                lines.append(f"        g_mat_over[{col}][{row}] = {value};")
            any_ram = True
        if not any_ram:
            lines.append("        /* No RAM-owned final G entries in this case. */")
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        f"                       \"Invalid multi-case {case_id_symbol} %d for component %s.\",",
        f"                       {case_id_symbol}, Name);",
        "        break;",
        "    }",
        f"    setupGMatrix({nr});",
    ])
    return lines


def _case_switch_assignment_lines(case_id_symbol: str, case_indices: Sequence[int], assignments: Sequence[str]) -> list[str]:
    if not case_indices or not assignments:
        return ["    /* No CODE-owned cases for this GValue group. */"]
    lines = [f"    switch ({case_id_symbol}) {{"]
    for index in case_indices:
        lines.append(f"    case {index}:")
    for assignment in assignments:
        lines.append(f"        {assignment}")
    lines.append("        break;")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
    ])
    return lines


def _final_g_code_case_lines(
    *,
    case_id_symbol: str,
    entry_plans: list[dict],
    matrix_name: str | None,
) -> list[str]:
    case_indices = sorted({
        int(item["index"])
        for plan in entry_plans
        for item in plan["code_cases"]
    })
    if not case_indices:
        return ["    /* No CODE-owned final G entries in any case. */"]

    lines = [
        "    /* Case-conditional CODE final-G writes.",
        "       RAM-owned cases were stamped in RAM_PASS1; no base + delta is generated. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for case_index in case_indices:
        lines.append(f"    case {case_index}:")
        for plan in entry_plans:
            code_case = next((item for item in plan["code_cases"] if int(item["index"]) == case_index), None)
            if code_case is None:
                continue
            value = _ccode(code_case["expr"])
            row = int(plan["row"])
            col = int(plan["col"])
            if matrix_name is None:
                lines.append(f"        {plan['var']} = {value};")
            else:
                lines.append(f"        set_CODE(&{matrix_name}, {row}, {col}, {value});")
                if row != col:
                    lines.append(f"        set_CODE(&{matrix_name}, {col}, {row}, {value});")
        if matrix_name is not None:
            for plan in entry_plans:
                if not any(int(item["index"]) == case_index for item in plan["code_cases"]):
                    continue
                lines.append(f"        {plan['var']} = get_CODE(&{matrix_name}, {plan['row']}, {plan['col']});")
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
    ])
    return lines


def _replace_code_g_setup_with_conditional_final_writes(
    draft: str,
    *,
    case_id_symbol: str,
    entry_plans: list[dict],
) -> str:
    if not any(plan["code_cases"] for plan in entry_plans):
        return draft
    if "MATRIX_ G_code" in draft:
        matrix_name: str | None = "G_code"
    elif "MATRIX_ Gred_code" in draft:
        matrix_name = "Gred_code"
    else:
        matrix_name = None
    start_marker = "    /* ************************************************************************\n     * CODE-SIDE G MATRIX VALUE SETUP"
    end_marker = "    /* ************************************************************************\n     * CODE-SIDE IHIS VALUE SETUP"
    start = draft.find(start_marker)
    end = draft.find(end_marker, start)
    if start < 0 or end < 0:
        return draft
    replacement_lines = [
        "    /* ************************************************************************",
        "     * CODE-SIDE G MATRIX VALUE SETUP",
        "     * No-internal multi-case path: write only CODE-owned final G entries",
        "     * under their active case condition. RAM-owned cases stay in RAM_PASS1.",
        "     * ************************************************************************ */",
        "",
        *_final_g_code_case_lines(
            case_id_symbol=case_id_symbol,
            entry_plans=entry_plans,
            matrix_name=matrix_name,
        ),
        "",
        "",
    ]
    return draft[:start] + "\n".join(replacement_lines) + draft[end:]


def _remove_code_g_alias_resolution_for_conditional_final_writes(draft: str, aliases: dict[str, dict]) -> str:
    if not aliases:
        return draft
    if not all(str(info.get("kind") or "").upper().startswith("G") for info in aliases.values()):
        return draft
    marker = "    /* Resolve CODE multi-case effective aliases as full values, never deltas. */"
    start = draft.find(marker)
    if start < 0:
        return draft
    end = draft.find("    if (!rtds_matrix_code_ready) {", start)
    if end < 0:
        return draft
    replacement = (
        "    /* CODE final-G cases write full case values directly below;\n"
        "       RAM-owned cases were stamped in RAM_PASS1. */\n"
    )
    return draft[:start] + replacement + draft[end:]


def _apply_conditional_final_gvalues_to_structured_draft(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    template_gred: sp.Matrix,
    external_nodes: list[str],
    prune_code_matrix_writes: bool = False,
    reuse_plan: Sequence | None = None,
    reuse_plan_by_case: dict[int, Sequence] | None = None,
) -> tuple[str, list[dict]]:
    entry_plans = _conditional_final_g_plans(
        case_id_symbol=case_id_symbol,
        profiles=profiles,
        aliases=aliases,
        template_gred=template_gred,
        external_nodes=external_nodes,
    )
    gvalue_conditions: list[dict] = []
    for plan in entry_plans:
        if not plan["code_cases"]:
            continue
        row = plan["row"]
        col = plan["col"]
        condition = plan["condition"]
        gvalue_conditions.append({
            "var": plan["var"],
            "row": row,
            "col": col,
            "condition": condition,
        })
        pattern = (
            f'double {plan["var"]} = createGValue("{plan["var"]}", '
            f'"{external_nodes[row]}", "{external_nodes[col]}", 0, "TRUE");'
        )
        replacement = (
            f'double {plan["var"]} = createGValue("{plan["var"]}", '
            f'"{external_nodes[row]}", "{external_nodes[col]}", 0, "{condition}");'
        )
        draft = draft.replace(pattern, replacement)

    ram_block = "\n".join(_conditional_ram_stamp_block(
        case_id_symbol=case_id_symbol,
        profiles=profiles,
        external_nodes=external_nodes,
        entry_plans=entry_plans,
    ))
    draft = draft.replace("    /* No RAM-side G entries: no fixed G overlay is registered. */", ram_block, 1)

    plan_by_pair = {(int(plan["row"]), int(plan["col"])): plan for plan in entry_plans}

    def code_case_indices(plan: dict) -> tuple[int, ...]:
        return tuple(int(item["index"]) for item in plan["code_cases"])

    def _compatible_reuse_from_items(items: Sequence | None, *, case_index: int | None = None) -> dict[tuple[int, int], tuple[tuple[int, int], int]]:
        compatible: dict[tuple[int, int], tuple[tuple[int, int], int]] = {}
        for item in items or []:
            target = (int(item.target_row), int(item.target_col))
            base = (int(item.base_row), int(item.base_col))
            target_plan = plan_by_pair.get(target)
            base_plan = plan_by_pair.get(base)
            if target_plan is None or base_plan is None:
                continue
            if not target_plan["code_cases"] or not base_plan["code_cases"]:
                continue
            if case_index is None:
                if code_case_indices(target_plan) != code_case_indices(base_plan):
                    continue
            else:
                target_cases = {int(case["index"]) for case in target_plan["code_cases"]}
                base_cases = {int(case["index"]) for case in base_plan["code_cases"]}
                if case_index not in target_cases or case_index not in base_cases:
                    continue
            compatible[target] = (base, int(item.sign))
        return compatible

    compatible_reuse = _compatible_reuse_from_items(reuse_plan)

    if reuse_plan_by_case:
        assignment_by_pair: dict[tuple[int, int], tuple[str, str]] = {}
        ordered_pairs: list[tuple[int, int]] = []
        for plan in entry_plans:
            if not plan["code_cases"]:
                continue
            row = int(plan["row"])
            col = int(plan["col"])
            for matrix_name in ["Gred_code", "G_code"]:
                assignment = f"{plan['var']} = get_CODE(&{matrix_name}, {row}, {col});"
                if f"    {assignment}" in draft:
                    pair = (row, col)
                    assignment_by_pair[pair] = (assignment, matrix_name)
                    ordered_pairs.append(pair)
                    break
        if ordered_pairs:
            compatible_reuse_by_case = {
                int(case_index): _compatible_reuse_from_items(items, case_index=int(case_index))
                for case_index, items in reuse_plan_by_case.items()
            }
            active_case_indices = sorted({
                int(case["index"])
                for plan in entry_plans
                for case in plan["code_cases"]
                if (int(plan["row"]), int(plan["col"])) in assignment_by_pair
            })
            switch_lines = [f"    switch ({case_id_symbol}) {{"]
            for case_index in active_case_indices:
                assigned_pairs: set[tuple[int, int]] = set()
                switch_lines.append(f"    case {case_index}:")
                for plan in entry_plans:
                    pair = (int(plan["row"]), int(plan["col"]))
                    if pair not in assignment_by_pair:
                        continue
                    if case_index not in {int(case["index"]) for case in plan["code_cases"]}:
                        continue
                    reuse = compatible_reuse_by_case.get(case_index, {}).get(pair)
                    if reuse and reuse[0] in assigned_pairs:
                        base_plan = plan_by_pair[reuse[0]]
                        prefix = "-" if reuse[1] < 0 else ""
                        switch_lines.append(f"        {plan['var']} = {prefix}{base_plan['var']};")
                    else:
                        switch_lines.append(f"        {assignment_by_pair[pair][0]}")
                    assigned_pairs.add(pair)
                switch_lines.append("        break;")
            switch_lines.extend(["    default:", "        break;", "    }"])
            first_assignment = assignment_by_pair[ordered_pairs[0]][0]
            switch_placeholder = "    /* __MULTICASE_FINAL_GVALUE_SWITCH__ */"
            draft = draft.replace(f"    {first_assignment}", switch_placeholder, 1)
            for pair in ordered_pairs[1:]:
                draft = draft.replace(f"    {assignment_by_pair[pair][0]}", "", 1)
            draft = draft.replace(switch_placeholder, "\n".join(switch_lines), 1)
            if prune_code_matrix_writes:
                draft = _replace_code_g_setup_with_conditional_final_writes(
                    draft,
                    case_id_symbol=case_id_symbol,
                    entry_plans=entry_plans,
                )
                draft = _remove_code_g_alias_resolution_for_conditional_final_writes(draft, aliases)
            return draft, gvalue_conditions

    grouped_assignments: dict[tuple[int, ...], list[str]] = {}
    for plan in entry_plans:
        if not plan["code_cases"]:
            continue
        row = plan["row"]
        col = plan["col"]
        for matrix_name in ["Gred_code", "G_code"]:
            assignment = f"{plan['var']} = get_CODE(&{matrix_name}, {row}, {col});"
            if f"    {assignment}" in draft:
                grouped_assignment = assignment
                if matrix_name == "Gred_code":
                    reuse = compatible_reuse.get((int(row), int(col)))
                    if reuse:
                        base, sign = reuse
                        base_plan = plan_by_pair[base]
                        prefix = "-" if sign < 0 else ""
                        grouped_assignment = f"{plan['var']} = {prefix}{base_plan['var']};"
                        draft = draft.replace(f"    {assignment}", f"    {grouped_assignment}", 1)
                case_indices = tuple(item["index"] for item in plan["code_cases"])
                grouped_assignments.setdefault(case_indices, []).append(grouped_assignment)
                break

    for case_indices, assignments in grouped_assignments.items():
        first_assignment = assignments[0]
        for assignment in assignments[1:]:
            draft = draft.replace(f"    {assignment}", "", 1)
        draft = draft.replace(
            f"    {first_assignment}",
            "\n".join(_case_switch_assignment_lines(case_id_symbol, case_indices, assignments)),
            1,
        )
    if prune_code_matrix_writes:
        draft = _replace_code_g_setup_with_conditional_final_writes(
            draft,
            case_id_symbol=case_id_symbol,
            entry_plans=entry_plans,
        )
        draft = _remove_code_g_alias_resolution_for_conditional_final_writes(draft, aliases)
    return draft, gvalue_conditions


def _try_build_alias_template_response(payload: dict) -> dict | None:
    alias_model = _build_multicase_alias_template_payload(payload)
    if alias_model is None:
        return None
    dummy_analysis = payload.get("_dummy_node_block_analysis") or _dummy_node_block_analysis(alias_model["profiles"])
    aliases = alias_model["aliases"]
    raw_template_payload = alias_model["template_payload"]
    template_internal_count = len(raw_template_payload.get("internal_nodes") or [])
    use_synthetic_dependency = template_internal_count >= 4
    template_payload = (
        _attach_multicase_fast_dependency(
            raw_template_payload,
            g_dependency=_promoted_dependency_for_aliases(aliases, kind="G"),
            ihis_dependency=_promoted_dependency_for_aliases(aliases),
        )
        if use_synthetic_dependency
        else raw_template_payload
    )
    preserve_structured_details = not use_synthetic_dependency
    request_payload = {
        **template_payload,
        "mode": "structured_formula",
        "display_mode": payload.get("display_mode") or template_payload.get("display_mode") or "compact",
        "simplify_level": payload.get("simplify_level") or template_payload.get("simplify_level") or "light",
        "assume_spd": payload.get("assume_spd", template_payload.get("assume_spd", True)),
        "use_suggested_order": payload.get("use_suggested_order", template_payload.get("use_suggested_order", False)),
        "preserve_structured_details_with_borrowed_dependency": preserve_structured_details,
    }
    result = build_optimized_response(request_payload)
    template_G, template_Ihis, _, _, template_nodes, template_external, _, _ = _partition_payload(template_payload)
    template_reduced = eliminate_internal_nodes(template_G, template_Ihis, template_nodes, template_external)
    case_id_symbol = str(payload.get("case_id_symbol") or "global_case_id")
    display_names = template_payload.get("node_display_names") or {}
    c_external_nodes = [str(display_names.get(node, node)) for node in template_external]
    symbol_table = {}
    for profile in alias_model["profiles"]:
        symbol_table.update((profile.get("payload") or {}).get("symbol_dependency_table") or {})
    warnings = list(result.get("warnings") or [])
    for alias, info in aliases.items():
        owners = set((info.get("case_owners") or {}).values())
        if info.get("runtime_mutable"):
            case_values = {
                str(value)
                for value in (info.get("case_values") or {}).values()
            }
            if len(case_values) > 1 and info.get("owner") in {"CODE", "CODE_PER_STEP"}:
                warnings.append(
                    f"Info: {alias} is controlled by runtime-mutable case group "
                    f"{info.get('runtime_group_name') or info.get('branch_id')}; "
                    f"assigned in CODE using {info.get('case_id_symbol')} as a full value."
                )
            continue
        if len(owners) > 1:
            warnings.append(
                f"Warning: {alias} has mixed case owners {sorted(owners)}; "
                f"promoted to {info.get('owner')} for safety. "
                "case_id is fixed before simulation and must not change at runtime."
            )
    gvalue_conditions: list[dict] = []
    has_internal_recovery = bool(result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [])
    has_mixed_final_g = (
        _final_g_stage_analysis_is_within_budget(template_reduced.G_red)
        and _has_mixed_final_g_stages(template_reduced.G_red, alias_model["profiles"], aliases)
    )
    if (
        not aliases
        and
        not has_internal_recovery
        and
        has_mixed_final_g
    ):
        draft, gvalue_conditions = _build_conditional_final_gvalue_draft(
            case_id_symbol=case_id_symbol,
            profiles=alias_model["profiles"],
            branch_ids=alias_model["branch_ids"],
            aliases=aliases,
            template_gred=template_reduced.G_red,
            template_ihis=template_reduced.Ihis_red,
            external_nodes=c_external_nodes,
            symbol_table=symbol_table,
        )
        warnings.append(
            "Warning: final G entries have mixed RAM/CODE ownership across cases. "
            "CODE-owned entries are enabled with case conditions. "
            "case_id is fixed before simulation and must not change at runtime."
        )
        warnings.append(
            "case_id must be fixed before simulation and must not change at runtime; "
            "case-conditional GValue entries are only valid for initialization-time case selection."
        )
    else:
        if not aliases:
            draft = result["structured"]["c_draft"]
            warnings.append(
                "Info: all multi-case profiles resolve to identical G/Ihis inputs; "
                "emitted one structured C draft without a runtime case switch."
            )
            codegen_mode = "single structured draft"
            fast_path = "identical_profiles"
        else:
            draft = _insert_multicase_alias_layer(
                result["structured"]["c_draft"],
                case_id_symbol=case_id_symbol,
                profiles=alias_model["profiles"],
                branch_ids=alias_model["branch_ids"],
                aliases=aliases,
            )
            draft = _apply_multicase_conditional_diagonal_w_builder(
                draft,
                case_id_symbol=case_id_symbol,
                profiles=alias_model["profiles"],
                aliases=aliases,
                gkk_template=_template_gkk_from_payload(template_payload),
            )
            codegen_mode = "case-agnostic alias template"
            fast_path = "case_alias_template"
        draft = _apply_dummy_recovery_profiles_to_draft(
            draft,
            case_id_symbol=case_id_symbol,
            internal_nodes=result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
            node_display_names=template_payload.get("node_display_names") or {},
            dummy_analysis=dummy_analysis,
        )
    display_reuse_plan_by_case: dict[int, list] = {}
    try:
        display_reuse_plan_by_case[int(alias_model["profiles"][0].get("index", 0))] = structural_gred_entry_reuse_plan(
            template_G,
            template_nodes,
            template_external,
            template_payload.get("internal_nodes") or [],
        )
    except Exception:
        display_reuse_plan_by_case = {}
    for profile_index, profile in enumerate(alias_model["profiles"]):
        profile_payload = profile.get("payload") or {}
        try:
            profile_G, _, _, _, profile_nodes, profile_external, _, _ = _partition_payload(profile_payload)
            profile_internal = profile_payload.get("internal_nodes") or []
            if [str(node) for node in profile_external] != [str(node) for node in template_external]:
                continue
            display_reuse_plan_by_case[int(profile.get("index", profile_index))] = structural_gred_entry_reuse_plan(
                profile_G,
                profile_nodes,
                profile_external,
                profile_internal,
            )
        except Exception:
            continue

    if aliases and has_mixed_final_g:
        reuse_plan = []
        try:
            reuse_plan = structural_gred_entry_reuse_plan(
                template_G,
                template_nodes,
                template_external,
                template_payload.get("internal_nodes") or [],
            )
        except Exception:
            reuse_plan = []
        draft, gvalue_conditions = _apply_conditional_final_gvalues_to_structured_draft(
            draft,
            case_id_symbol=case_id_symbol,
            profiles=alias_model["profiles"],
            aliases=aliases,
            template_gred=template_reduced.G_red,
            external_nodes=c_external_nodes,
            prune_code_matrix_writes=not has_internal_recovery,
            reuse_plan=reuse_plan,
            reuse_plan_by_case=display_reuse_plan_by_case,
        )
        warnings.append(
            "Warning: final G entries have mixed RAM/CODE ownership across cases. "
            "CODE-owned entries are enabled with case conditions. "
            "case_id is fixed before simulation and must not change at runtime."
        )
    if "codegen_mode" not in locals():
        codegen_mode = "case-agnostic alias template"
    if "fast_path" not in locals():
        fast_path = "case_alias_template"
    return {
        "ok": True,
        "mode": "multi_case_c_export",
        "case_id_symbol": case_id_symbol,
        "case_profiles": [
            {
                "name": profile.get("name") or f"Case {index + 1}",
                "comment": profile.get("comment") or "",
                "case_map": profile.get("case_map") or {},
            }
            for index, profile in enumerate(alias_model["profiles"])
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "multi_case": {
            "codegen_mode": codegen_mode,
            "external_nodes": result.get("external_nodes") or [],
            "effective_internal_nodes": result.get("effective_internal_nodes") or [],
            "block_type": (result.get("structured") or {}).get("block_type"),
            "profile_count": len(alias_model["profiles"]),
            "aliases": aliases,
            "runtime_case_groups": [
                {
                    "branch_id": str(group.get("branch_id") or ""),
                    "name": str(group.get("name") or group.get("branch_id") or ""),
                    "case_id_symbol": str(group.get("case_id_symbol") or ""),
                    "case_count": len(group.get("cases") or []),
                }
                for group in alias_model.get("runtime_case_groups") or []
            ],
            "gvalue_conditions": gvalue_conditions,
            "uses_case_conditional_gvalue": bool(gvalue_conditions),
            **(
                {
                    "dummy_node_blocks": {
                        "count": dummy_analysis.get("count", 0),
                        "common_internal_nodes": dummy_analysis.get("common_internal_nodes", []),
                        "case_roles": dummy_analysis.get("case_roles", []),
                    },
                    "recovery_profiles": dummy_analysis.get("recovery_profiles", []),
                }
                if dummy_analysis
                else {}
            ),
            "template": {
                "Gred": _clean_matrix(template_reduced.G_red),
                "Ihisred": _clean_vector(template_reduced.Ihis_red),
                "recovery_shape": [
                    len(result.get("effective_internal_nodes") or []),
                    len(result.get("external_nodes") or []),
                ],
            },
            "template_blocks": _clean_value((result.get("structured") or {}).get("blocks") or {}),
            "template_direct_retained": _clean_value((result.get("structured") or {}).get("direct_retained") or {}),
            "gred_entry_reuse_by_case": _clean_gred_entry_reuse_by_case(
                display_reuse_plan_by_case,
                alias_model["profiles"],
                c_external_nodes,
            ),
            "template_block_nodes": {
                "retained_order": result.get("external_nodes") or template_payload.get("external_nodes") or [],
                "internal_order": result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
            },
            "template_summary": {
                "nodes": (template_payload.get("all_nodes") or []),
                "retained_order": (template_payload.get("external_nodes") or []),
                "internal_order": (template_payload.get("internal_nodes") or []),
                "Gred_shape": [
                    len(result.get("external_nodes") or []),
                    len(result.get("external_nodes") or []),
                ],
                "Ihisred_shape": [len(result.get("external_nodes") or []), 1],
            },
            "c_draft": _ensure_static_blank_line(draft),
            "fast_path": fast_path,
        },
    }


def _build_multi_case_c_draft(case_id_symbol: str, profile_results: list[dict]) -> str:
    scalar_mux = _try_build_scalar_mux_c_draft(profile_results)
    if scalar_mux:
        return _ensure_static_blank_line(scalar_mux)
    base_result = profile_results[0]["result"]
    external_nodes = list(base_result.get("external_nodes") or [])
    internal_nodes = list(base_result.get("effective_internal_nodes") or [])
    nr = len(external_nodes)
    nk = len(internal_nodes)
    case_matrices = []
    for item in profile_results:
        result = item["result"]
        matrices = {
            "Grr_code": _result_matrix(result, "G_rr"),
            "Grk_code": _result_matrix(result, "G_ri"),
            "Gkr_code": _result_matrix(result, "G_ir"),
            "Gkk_code": _result_matrix(result, "G_ii"),
            "Ihisr_code": _result_matrix(result, "Ihis_r"),
            "Ihisk_code": _result_matrix(result, "Ihis_i"),
            "Gdirect_code": _result_direct_matrix(result, "Gred_direct"),
            "Ihisdirect_code": _result_direct_matrix(result, "Ihisred_direct"),
        }
        case_matrices.append(matrices)
    symbol_names = _symbols_in_matrices(*(matrix for matrices in case_matrices for matrix in matrices.values()))
    matrix_dims = [
        ("Grr_code", nr, nr),
        ("Grk_code", nr, nk),
        ("Gkr_code", nk, nr),
        ("Gkk_code", nk, nk),
        ("W_code", nk, nk),
        ("Gschur_code", nr, nr),
        ("Gdirect_code", nr, nr),
        ("Gfinal_code", nr, nr),
        ("Ihisr_code", nr, 1),
        ("Ihisk_code", nk, 1),
        ("Ihisschur_code", nr, 1),
        ("Ihisdirect_code", nr, 1),
        ("Ihisfinal_code", nr, 1),
        ("tmp_Grk_W_code", nr, nk),
        ("tmp_Grk_W_Gkr_code", nr, nr),
        ("tmp_Grk_W_Ihisk_code", nr, 1),
        ("Vr_code", nr, 1),
        ("Vk_code", nk, 1),
        ("tmp_W_Gkr_code", nk, nr),
        ("tmp_W_Gkr_Vr_code", nk, 1),
        ("tmp_W_Ihisk_code", nk, 1),
        ("tmp_Vk_sum_code", nk, 1),
    ]
    matrix_dims = [(name, rows, cols) for name, rows, cols in matrix_dims if rows > 0 and cols > 0]
    lines = [
        "#include <matrixLIB.h>",
        "/* RAM-switch multi-case C draft.",
        "   The host program provides the case selector before RAM_PASS1.",
        "   Each case uses the same retained/internal node order and the same structured workflow.",
        "   Each case fills source-level core/direct matrices; the Schur/W/Gred/Ihisred/Vk matrix DAG is shared. */",
        f"enum {{ NR = {nr}, NK = {nk}, NCASE = {len(profile_results)} }};",
        "",
        "STATIC:",
        *[f"    MATRIX_ {name} = {{0}};" for name, _, _ in matrix_dims],
        "    int multi_case_matrix_ready = 0;",
        *([f"    double {name} = 0.0;" for name in symbol_names] if symbol_names else ["    /* No user symbols detected. */"]),
        "",
        "RAM_PASS1:",
        "    int err = 0;",
        *[f"    err += matrixDim(&{name}, {rows}, {cols});" for name, rows, cols in matrix_dims],
        "    if (err > 0) {",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        "                       \"RTDS multi-case matrix allocation failed for component %s.\", Name);",
        "    }",
        *[f"    matrix_register(&{name});" for name, _, _ in matrix_dims],
        "",
        "CODE:",
        "BEGIN_T0:",
        "    if (!multi_case_matrix_ready) {",
        "        initializeMatricesForCode();",
        *[f"        conditionMatrixForCODE(&{name});" for name, _, _ in matrix_dims],
        "        multi_case_matrix_ready = 1;",
        "    }",
        "",
        "    /* Select one source-level case profile, then run the shared reduction DAG below. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for index, item in enumerate(profile_results):
        matrices = case_matrices[index]
        lines.extend([
            f"    case {index}: /* {_profile_case_comment(item['profile'], index)} */",
            *_clear_matrix_lines("Grr_code", nr, nr),
            *_clear_matrix_lines("Grk_code", nr, nk),
            *_clear_matrix_lines("Gkr_code", nk, nr),
            *_clear_matrix_lines("Gkk_code", nk, nk),
            *_clear_matrix_lines("Ihisr_code", nr, 1),
            *_clear_matrix_lines("Ihisk_code", nk, 1),
            *_clear_matrix_lines("Gdirect_code", nr, nr),
            *_clear_matrix_lines("Ihisdirect_code", nr, 1),
            *_matrix_set_lines("Grr_code", matrices["Grr_code"]),
            *_matrix_set_lines("Grk_code", matrices["Grk_code"]),
            *_matrix_set_lines("Gkr_code", matrices["Gkr_code"]),
            *_matrix_set_lines("Gkk_code", matrices["Gkk_code"]),
            *_matrix_set_lines("Ihisr_code", matrices["Ihisr_code"]),
            *_matrix_set_lines("Ihisk_code", matrices["Ihisk_code"]),
            *_matrix_set_lines("Gdirect_code", matrices["Gdirect_code"]),
            *_matrix_set_lines("Ihisdirect_code", matrices["Ihisdirect_code"]),
            "        break;",
        ])
    lines.extend([
        "    default:",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        "                       \"Unknown multi-case profile %d for component %s.\",",
        f"                       {case_id_symbol}, Name);",
        "        break;",
        "    }",
        "",
        "    /* Shared Schur flow: Gred = Grr - Grk * W * Gkr. */",
        *(["    MATH_matx_invert(NK, &(Gkk_code.p[0]), NK, &(W_code.p[0]), NK);"] if nk else []),
        *(["    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);"] if nk else []),
        *(["    matrix_mult_CODE(&tmp_Grk_W_Gkr_code, &tmp_Grk_W_code, &Gkr_code);"] if nk else []),
        *(["    matrix_subtract_CODE(&Gschur_code, &Grr_code, &tmp_Grk_W_Gkr_code);"] if nk else ["    /* No internal nodes: Gschur = Grr. */"]),
        "    matrix_add_CODE(&Gfinal_code, &Gschur_code, &Gdirect_code);",
        "",
        "    /* Shared Ihis flow: Ihisred = Ihisr - Grk * W * Ihisk. */",
        *(["    matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);"] if nk else []),
        *(["    matrix_subtract_CODE(&Ihisschur_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);"] if nk else ["    /* No internal nodes: Ihisschur = Ihisr. */"]),
        "    matrix_add_CODE(&Ihisfinal_code, &Ihisschur_code, &Ihisdirect_code);",
        *[
            f"    Inj{_c_identifier_name(node, f'N{index + 1}')} = get_CODE(&Ihisfinal_code, {index}, 0);"
            for index, node in enumerate(external_nodes)
        ],
        "",
        "T1_T2:",
        *(["    /* Internal-node voltage recovery uses core matrices only: Vk = -W * Gkr * Vr - W * Ihisk. */"] if nk else ["    /* No internal-node voltage recovery is required. */"]),
        *([
            f"    set_CODE(&Vr_code, {index}, 0, {_c_identifier_name(node, f'N{index + 1}')});"
            for index, node in enumerate(external_nodes)
        ] if nk else []),
        *(["    matrix_mult_CODE(&tmp_W_Gkr_code, &W_code, &Gkr_code);"] if nk else []),
        *(["    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);"] if nk else []),
        *(["    matrix_matXvec_CODE(&tmp_W_Ihisk_code, &W_code, &Ihisk_code);"] if nk else []),
        *(["    matrix_add_CODE(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code);"] if nk else []),
        *(["    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);"] if nk else []),
        *[
            f"    {_c_identifier_name(node, f'K{index + 1}')} = get_CODE(&Vk_code, {index}, 0);"
            for index, node in enumerate(internal_nodes)
        ],
    ])
    return _ensure_static_blank_line("\n".join(lines))


def _profiles_have_dummy_finalization(profiles: list[dict]) -> bool:
    for profile in profiles:
        dummy = profile.get("dummy_finalization") or {}
        if dummy.get("dummy_leaves"):
            return True
        payload = profile.get("payload") or {}
        if payload.get("defer_dummy_node_blocks") and dummy_node_blocks_from_payload(payload):
            return True
    return False


def _isolated_dummy_nodes_for_payload(payload: dict) -> list[str]:
    nodes: list[str] = []
    for block in dummy_node_blocks_from_payload(payload):
        for node in block.dummy_nodes:
            if node not in nodes:
                nodes.append(str(node))
    return nodes


def _ordered_nodes(nodes: Iterable[str], reference_order: Sequence[str]) -> list[str]:
    wanted = {str(node) for node in nodes}
    ordered = [str(node) for node in reference_order if str(node) in wanted]
    ordered.extend(sorted(wanted.difference(ordered)))
    return ordered


def _classify_multicase_dummy_node_roles(profiles: list[dict], *, common_internal_hint: set[str] | None = None) -> dict:
    common_internal_hint = common_internal_hint or set()
    isolated_nodes = sorted({
        node
        for profile in profiles
        for node in _isolated_dummy_nodes_for_payload(profile.get("payload") or {})
    })
    role_by_node: dict[str, list[dict]] = {}
    common_internal: list[str] = []
    unsupported: list[str] = []
    for node in isolated_nodes:
        roles: list[dict] = []
        for case_index, profile in enumerate(profiles):
            payload = profile.get("payload") or {}
            dummy_nodes = set(_isolated_dummy_nodes_for_payload(payload))
            external = {str(item) for item in (payload.get("external_nodes") or [])}
            internal = {str(item) for item in (payload.get("internal_nodes") or [])}
            all_nodes = {str(item) for item in (payload.get("all_nodes") or [])}
            if node in dummy_nodes:
                role = "isolated_dummy_internal" if node in internal else "isolated_dummy_final"
            elif node in internal:
                role = "physical_internal"
            elif node in external:
                role = "retained_physical"
            elif node in all_nodes:
                role = "present_unclassified"
            else:
                role = "missing"
            roles.append({"case_id": case_index, "role": role})
        role_names = {item["role"] for item in roles}
        if role_names <= {"physical_internal", "isolated_dummy_internal"}:
            common_internal.append(node)
        elif node in common_internal_hint and role_names <= {"physical_internal", "isolated_dummy_final"}:
            common_internal.append(node)
        elif "isolated_dummy_internal" in role_names and not role_names <= {"physical_internal", "isolated_dummy_internal"}:
            unsupported.append(node)
        role_by_node[node] = roles
    return {
        "isolated_dummy_nodes": isolated_nodes,
        "mixed_physical_dummy_internal": common_internal,
        "unsupported_role_mix": unsupported,
        "roles": role_by_node,
    }


def _promote_common_isolated_dummy_internal_profiles(profiles: list[dict], payload: dict | None = None) -> tuple[list[dict], dict]:
    payload = payload or {}
    common_internal_hint = {str(node) for node in (payload.get("common_dummy_internal_nodes") or [])}
    classification = _classify_multicase_dummy_node_roles(profiles, common_internal_hint=common_internal_hint)
    unsupported = classification.get("unsupported_role_mix") or []
    if unsupported:
        node = unsupported[0]
        raise ValueError(
            "DummyNodeBlock currently supports isolated dummy nodes only when the same canonical node "
            f"is eliminated in every case. The node {node} has an unsupported retained/dummy role mix."
        )
    common_internal = set(classification.get("mixed_physical_dummy_internal") or [])
    if not common_internal:
        return profiles, classification

    normalized: list[dict] = []
    for profile in profiles:
        payload = dict(profile.get("payload") or {})
        promote_nodes = common_internal.intersection({str(node) for node in (payload.get("all_nodes") or [])})
        if promote_nodes:
            external = [str(node) for node in (payload.get("external_nodes") or []) if str(node) not in promote_nodes]
            internal_existing = [str(node) for node in (payload.get("internal_nodes") or [])]
            all_nodes = [str(node) for node in (payload.get("all_nodes") or [])]
            internal = _ordered_nodes([*internal_existing, *promote_nodes], all_nodes)
            payload["external_nodes"] = external
            payload["internal_nodes"] = internal
            payload.pop("defer_dummy_node_blocks", None)
        normalized.append({**profile, "payload": payload})
    return normalized, classification


def _dummy_node_block_analysis(profiles: list[dict]) -> dict | None:
    case_roles: list[dict] = []
    common_internal_order: list[str] = []
    all_dummy_nodes: set[str] = set()
    block_count = 0
    for case_index, profile in enumerate(profiles):
        payload = profile.get("payload") or {}
        blocks = dummy_node_blocks_from_payload(payload)
        if not blocks:
            case_roles.append({
                "case_id": case_index,
                "dummy_nodes": [],
                "recoverable_nodes": [str(node) for node in (payload.get("internal_nodes") or [])],
            })
            continue
        block_count += len(blocks)
        nodes = [str(node) for node in (payload.get("all_nodes") or [])]
        internal_nodes = [str(node) for node in (payload.get("internal_nodes") or [])]
        G = _expr_matrix_from_payload(payload, "G_full")
        Ihis = _expr_matrix_from_payload(payload, "Ihis_full")
        validate_dummy_node_blocks(G, Ihis, nodes, blocks, common_internal_nodes=internal_nodes)
        dummy_nodes = [node for block in blocks for node in block.dummy_nodes]
        all_dummy_nodes.update(dummy_nodes)
        for node in internal_nodes:
            if node not in common_internal_order:
                common_internal_order.append(node)
        dummy_set = set(dummy_nodes)
        case_roles.append({
            "case_id": case_index,
            "dummy_nodes": dummy_nodes,
            "recoverable_nodes": [node for node in internal_nodes if node not in dummy_set],
        })
    if not block_count:
        return None

    for profile in profiles:
        payload = profile.get("payload") or {}
        external = {str(node) for node in (payload.get("external_nodes") or [])}
        for node in sorted(all_dummy_nodes):
            if node in external:
                raise ValueError(
                    "DummyNodeBlock currently supports only nodes eliminated in every case. "
                    f"The node {node} is retained in case {profile.get('name') or 'profile'}."
                )

    grouped: dict[tuple[str, ...], list[int]] = {}
    for role in case_roles:
        key = tuple(role["recoverable_nodes"])
        grouped.setdefault(key, []).append(int(role["case_id"]))
    recovery_profiles = [
        {"case_ids": case_ids, "recoverable_nodes": list(nodes)}
        for nodes, case_ids in grouped.items()
    ]
    return {
        "count": block_count,
        "common_internal_nodes": [node for node in common_internal_order if node in all_dummy_nodes],
        "case_roles": case_roles,
        "recovery_profiles": recovery_profiles,
    }


def _apply_dummy_recovery_profiles_to_draft(
    draft: str,
    *,
    case_id_symbol: str,
    internal_nodes: Sequence[str],
    node_display_names: Mapping[str, str] | None = None,
    dummy_analysis: dict | None,
) -> str:
    if not dummy_analysis or not internal_nodes:
        return draft
    node_display_names = node_display_names or {}
    node_to_row = {str(node): index for index, node in enumerate(internal_nodes)}
    draft = draft.replace("    /* One variable per eliminated node, in effective k order. */\n", "")

    def recovery_c_name(node: str, row: int) -> str:
        display_name = str(node_display_names.get(node, node))
        return _c_identifier_name(display_name, f"K{row + 1}")

    for node, row in node_to_row.items():
        candidate_names = {
            _c_identifier_name(node, f"K{row + 1}"),
            recovery_c_name(node, row),
        }
        for c_name in candidate_names:
            draft = draft.replace(
                f"    {c_name} = get_CODE(&Vk_code, {row}, 0);\n",
                "",
            )
    lines = [
        "    /* Recovery profiles: DummyNodeBlock isolated internal nodes are not recovered. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in dummy_analysis.get("recovery_profiles") or []:
        for case_id in profile.get("case_ids") or []:
            lines.append(f"    case {case_id}:")
        recoverable = [str(node) for node in (profile.get("recoverable_nodes") or [])]
        if recoverable:
            for node in recoverable:
                if node not in node_to_row:
                    continue
                row = node_to_row[node]
                lines.append(f"        {recovery_c_name(node, row)} = get_CODE(&Vk_code, {row}, 0);")
        else:
            lines.append("        /* DummyNodeBlock isolated internal nodes are not recovered. */")
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
    ])
    marker = "    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);\n"
    if marker in draft:
        return draft.replace(marker, marker + "\n".join(lines) + "\n", 1)
    return draft.rstrip() + "\n" + "\n".join(lines) + "\n"


def _apply_single_case_dummy_recovery_skip(
    draft: str,
    internal_nodes: Sequence[str],
    dummy_nodes: set[str],
) -> str:
    if not dummy_nodes or not internal_nodes:
        return draft
    removed = False
    for row, node in enumerate(internal_nodes):
        if str(node) not in dummy_nodes:
            continue
        c_name = _c_identifier_name(str(node), f"K{row + 1}")
        needle = f"    {c_name} = get_CODE(&Vk_code, {row}, 0);\n"
        if needle in draft:
            draft = draft.replace(needle, "")
            removed = True
    comment = "    /* DummyNodeBlock isolated internal nodes are not recovered. */\n"
    if not removed:
        marker = "T1_T2:\n"
        if marker in draft and comment not in draft:
            return draft.replace(marker, marker + comment, 1)
        return draft
    marker = "    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);\n"
    if marker in draft and comment not in draft:
        return draft.replace(marker, marker + comment, 1)
    return draft


def _reduced_super_result_from_payload(case_payload: dict) -> dict:
    all_nodes = [str(node) for node in (case_payload.get("all_nodes") or [])]
    external_nodes = [str(node) for node in (case_payload.get("external_nodes") or [])]
    internal_nodes = [str(node) for node in (case_payload.get("internal_nodes") or [])]
    G_full = _expr_matrix_from_payload(case_payload, "G_full")
    Ihis_full = _expr_matrix_from_payload(case_payload, "Ihis_full")

    if not all_nodes:
        all_nodes = [*external_nodes, *[node for node in internal_nodes if node not in external_nodes]]
    if not internal_nodes:
        if G_full.rows == len(external_nodes) and G_full.cols == len(external_nodes):
            return {
                "G": G_full,
                "Ihis": Ihis_full,
                "external_nodes": external_nodes,
                "internal_nodes": [],
                "K_v": sp.zeros(0, len(external_nodes)),
                "K_h": sp.zeros(0, 1),
            }
        indices = [all_nodes.index(node) for node in external_nodes]
        return {
            "G": G_full.extract(indices, indices),
            "Ihis": Ihis_full.extract(indices, [0]),
            "external_nodes": external_nodes,
            "internal_nodes": [],
            "K_v": sp.zeros(0, len(external_nodes)),
            "K_h": sp.zeros(0, 1),
        }

    reduced = eliminate_internal_nodes(G_full, Ihis_full, all_nodes, external_nodes)
    return {
        "G": sp.Matrix(reduced.G_red),
        "Ihis": sp.Matrix(reduced.Ihis_red),
        "external_nodes": external_nodes,
        "internal_nodes": [str(node) for node in reduced.internal_nodes],
        "K_v": sp.Matrix(reduced.K_v),
        "K_h": sp.Matrix(reduced.K_h),
    }


def _common_reduction_cache_key(case_payload: dict) -> tuple:
    def freeze_matrix(value) -> tuple:
        rows = value or []
        if rows and not isinstance(rows[0], list):
            return tuple((str(item),) for item in rows)
        return tuple(tuple(str(item) for item in row) for row in rows)

    return (
        tuple(str(node) for node in (case_payload.get("all_nodes") or [])),
        tuple(str(node) for node in (case_payload.get("external_nodes") or [])),
        tuple(str(node) for node in (case_payload.get("internal_nodes") or [])),
        freeze_matrix(case_payload.get("G_full")),
        freeze_matrix(case_payload.get("Ihis_full")),
    )


def _recovery_assignment_lines(item: dict, *, indent: str = "        ") -> list[str]:
    recovery_nodes = list(item.get("recovery_nodes") or [])
    if not recovery_nodes:
        return []
    external_nodes = list(item.get("super_nodes") or [])
    K_v = item.get("K_v")
    K_h = item.get("K_h")
    K_v = sp.Matrix(K_v) if K_v is not None else sp.zeros(len(recovery_nodes), len(external_nodes))
    K_h = sp.Matrix(K_h) if K_h is not None else sp.zeros(len(recovery_nodes), 1)
    lines: list[str] = []
    for row, node in enumerate(recovery_nodes):
        terms: list[str] = []
        for col, external in enumerate(external_nodes):
            coeff = sp.sympify(K_v[row, col])
            if sp.simplify(coeff) == 0:
                continue
            terms.append(f"({_ccode(coeff)})*{_c_identifier_name(external, f'V{col + 1}')}")
        history = sp.sympify(K_h[row, 0])
        if sp.simplify(history) != 0:
            terms.append(_ccode(history))
        rhs = " + ".join(terms) if terms else "0.0"
        lines.append(f"{indent}{_c_identifier_name(node, f'K{row + 1}')} = {rhs};")
    return lines


def _build_dummy_finalized_multi_case_c_draft(
    case_id_symbol: str,
    profile_set,
    final_results: list[dict],
    *,
    aliases: dict[str, dict] | None = None,
    profiles: list[dict] | None = None,
    branch_ids: list[str] | None = None,
) -> str:
    aliases = aliases or {}
    profiles = profiles or []
    branch_ids = branch_ids or []
    max_dim = max((item["final"].G.rows for item in final_results), default=0)
    super_dim = len(profile_set.super_node_order)
    symbols = _symbols_in_matrices(
        *(item["final"].G for item in final_results),
        *(item["final"].Ihis for item in final_results),
    )
    original_symbols: set[str] = set()
    for info in aliases.values():
        for expr in (info.get("case_values") or {}).values():
            original_symbols.update(symbol.name for symbol in _parse_expr(expr).free_symbols)
    declarations: list[str] = []
    declared_names: set[str] = set()
    ccode_cache: dict[str, str] = {}
    stage_cache: dict[tuple[int, str], str] = {}

    def emit_c(expr: object) -> str:
        key = str(expr)
        if key not in ccode_cache:
            ccode_cache[key] = _ccode(expr)
        return ccode_cache[key]

    def stage_of(expr: sp.Expr, symbol_table: dict[str, str]) -> str:
        key = (id(symbol_table), str(expr))
        if key not in stage_cache:
            stage_cache[key] = _expr_stage(expr, symbol_table)
        return stage_cache[key]

    def add_declaration(line: str, name: str) -> None:
        if name in declared_names:
            return
        declared_names.add(name)
        declarations.append(line)

    for branch_id in branch_ids:
        name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        add_declaration(f"    int {name} = 0;", name)
    for name in sorted(original_symbols):
        if _c_identifier_name(name, name) == name:
            add_declaration(f"    double {name} = 0.0;", name)
    for name in symbols:
        add_declaration(f"    double {name} = 0.0;", name)
    dynamic_gvalues: dict[tuple[str, str], dict] = {}
    dynamic_case_assignments: dict[int, list[str]] = {}
    for case_index, item in enumerate(final_results):
        final = item["final"]
        symbol_table = item.get("symbol_table") or {}
        nodes = list(final.nodes)
        for row in range(final.G.rows):
            for col in range(row, final.G.cols):
                expr = sp.sympify(final.G[row, col])
                if expr == 0 or stage_of(expr, symbol_table) == "RAM":
                    continue
                left = str(nodes[row])
                right = str(nodes[col])
                left_id = _c_identifier_name(left, f"N{row + 1}")
                right_id = _c_identifier_name(right, f"N{col + 1}")
                var = f"varG_{left_id}_{right_id}"
                entry = dynamic_gvalues.setdefault((left, right), {
                    "var": var,
                    "left": left,
                    "right": right,
                    "cases": [],
                })
                entry["cases"].append(case_index)
                dynamic_case_assignments.setdefault(case_index, []).append(f"{var} = {emit_c(expr)};")

    lines = [
        "/* Multi-case C draft with case-specific DummyBranch finalization.",
        "   Each case is reduced on the shared super-node order first; Dummy final nodes",
        "   are removed locally before solver stamping. */",
        f"enum {{ NR_SUPER = {super_dim}, NR_FINAL_MAX = {max_dim}, NCASE = {len(final_results)} }};",
        "",
        "LOCAL_STATIC:",
        *(declarations or ["    /* No user symbols are required by the finalized RAM matrices. */"]),
        "",
        "RAM_PASS1:",
        "    int err = 0;",
        *(_multicase_local_case_lines(case_id_symbol, profiles, branch_ids) if aliases else []),
        *(_alias_assignment_lines(aliases, "RAM", case_id_symbol) if aliases else []),
        "    /* Profile-specific finalized RAM stamp. */",
        f"    switch ({case_id_symbol}) {{",
    ]

    for case_index, item in enumerate(final_results):
        final = item["final"]
        profile = item["profile"]
        nodes = list(final.nodes)
        dim = len(nodes)
        comment = f"Case {case_index}"
        dummy_note = "; dummy-finalized solver dimension" if profile.dummy_nodes else ""
        lines.extend([
            f"    case {case_index}: /* {comment}{dummy_note} */",
            *[
                f"        g_mat_nods[{node_index}] = getNodeNum(comp, \"{node}\");"
                for node_index, node in enumerate(nodes)
            ],
            f"        for (int row = 0; row < {dim}; row++) {{",
            f"            for (int col = 0; col < {dim}; col++) {{",
            "                g_mat_over[row][col] = 0.0;",
            "            }",
            "        }",
        ])
        for row in range(final.G.rows):
            for col in range(final.G.cols):
                expr = sp.sympify(final.G[row, col])
                if expr == 0 or stage_of(expr, item.get("symbol_table") or {}) != "RAM":
                    continue
                lines.append(f"        g_mat_over[{row}][{col}] = {emit_c(expr)};")
        lines.extend([
            f"        setupGMatrix({dim});",
            "        break;",
        ])

    lines.extend([
        "    default:",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        f"                       \"Unknown dummy multi-case profile %d for component %s.\", {case_id_symbol}, Name);",
        "        break;",
        "    }",
        "    if (err > 0) {",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        "                       \"RTDS dummy multi-case allocation failed for component %s.\", Name);",
        "    }",
        "",
    ])
    if dynamic_gvalues:
        lines.extend([
            "GVALUES:",
            "    /* Dynamic final-G stamp handles, scoped by dummy finalization profile. */",
        ])
        for entry in dynamic_gvalues.values():
            condition = " || ".join(f"{case_id_symbol} == {case_index}" for case_index in entry["cases"])
            lines.append(
                f"    double {entry['var']} = createGValue(\"{entry['var']}\", "
                f"\"{entry['left']}\", \"{entry['right']}\", 0, \"{condition}\");"
            )
        lines.append("")
    lines.extend([
        "CODE:",
        "BEGIN_T0:",
    ])
    if aliases:
        lines.extend(_alias_assignment_lines(aliases, "CODE", case_id_symbol))
        lines.extend(_alias_assignment_lines(aliases, "CODE_PER_STEP", case_id_symbol))
    if dynamic_case_assignments:
        lines.extend([
            f"    switch ({case_id_symbol}) {{",
        ])
        for case_index in sorted(dynamic_case_assignments):
            lines.append(f"    case {case_index}:")
            lines.extend(f"        {assignment}" for assignment in dynamic_case_assignments[case_index])
            lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
            "",
        ])
    lines.extend([
        f"    switch ({case_id_symbol}) {{",
    ])

    for case_index, item in enumerate(final_results):
        final = item["final"]
        lines.append(f"    case {case_index}:")
        for row, node in enumerate(final.nodes):
            lines.append(f"        Inj{_c_identifier_name(node, f'N{row + 1}')} = {emit_c(final.Ihis[row, 0])};")
        lines.extend([
            "        break;",
        ])
    lines.extend([
        "    default:",
        "        break;",
        "    }",
        "",
        "T1_T2:",
    ])
    if any(item.get("recovery_nodes") for item in final_results):
        lines.extend([
            "    /* Physical internal-node recovery; dummy final nodes are skipped. */",
            f"    switch ({case_id_symbol}) {{",
        ])
        for case_index, item in enumerate(final_results):
            lines.append(f"    case {case_index}:")
            recovery_lines = _recovery_assignment_lines(item)
            lines.extend(recovery_lines or ["        break;"])
            if recovery_lines:
                lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
        ])
    else:
        lines.append("    /* Dummy final nodes are removed from the solver dimension and are not recovered. */")
    return _ensure_static_blank_line("\n".join(lines))


def _dummy_finalized_formula_cost(final_results: list[dict]) -> int:
    cost = 0
    for item in final_results:
        final = item["final"]
        for expr in list(final.G) + list(final.Ihis):
            cost += int(sp.count_ops(sp.sympify(expr)))
    return cost


def _dummy_finalized_matrix_dag_is_preferred(final_results: list[dict], *, max_ops: int = 5000) -> bool:
    return _dummy_finalized_formula_cost(final_results) > max_ops


def _dummy_finalized_gred_reuse_by_case(
    final_results: list[dict],
) -> tuple[dict[int, list[GredEntryReuse]], dict[int, list[str]]]:
    reuse_by_case: dict[int, list[GredEntryReuse]] = {}
    nodes_by_case: dict[int, list[str]] = {}
    for item in final_results:
        case_index = int(item.get("index", 0))
        final = item.get("final")
        nodes = [str(node) for node in (getattr(final, "nodes", []) if final is not None else [])]
        nodes_by_case[case_index] = nodes
        if final is None or not nodes:
            reuse_by_case[case_index] = []
            continue
        try:
            reuse_by_case[case_index] = structural_gred_entry_reuse_plan(
                sp.Matrix(final.G),
                nodes,
                nodes,
                [],
            )
        except Exception:
            reuse_by_case[case_index] = []
    return reuse_by_case, nodes_by_case


def _source_gred_reuse_by_case(
    profiles: Sequence[Mapping],
) -> tuple[dict[int, list[GredEntryReuse]], dict[int, list[str]], dict[int, list[str]]]:
    """Find per-profile Gred whole-entry reuse from source Schur inputs.

    Dummy-finalized multi-case C export may use a shared MATRIX_ DAG for codegen,
    while the visible final dimensions vary by case.  The reusable Gred
    relationships users expect to inspect are still relationships in each
    profile's source-level Schur system, so compute them before any final
    dummy-node slicing.  This stays on the structural path and intentionally
    avoids simplify/cancel/factor.
    """
    reuse_by_case: dict[int, list[GredEntryReuse]] = {}
    node_ids_by_case: dict[int, list[str]] = {}
    display_nodes_by_case: dict[int, list[str]] = {}
    for index, profile in enumerate(profiles or []):
        case_index = int(profile.get("index", index))
        payload = profile.get("payload") or {}
        external_nodes = [str(node) for node in (payload.get("external_nodes") or [])]
        display_names = payload.get("node_display_names") or {}
        node_ids_by_case[case_index] = external_nodes
        display_nodes_by_case[case_index] = [str(display_names.get(node, node)) for node in external_nodes]
        if not external_nodes:
            reuse_by_case[case_index] = []
            continue
        try:
            reuse_by_case[case_index] = structural_gred_entry_reuse_plan(
                _parse_matrix(payload.get("G_full") or []),
                [str(node) for node in (payload.get("all_nodes") or [])],
                external_nodes,
                [str(node) for node in (payload.get("internal_nodes") or [])],
            )
        except Exception:
            reuse_by_case[case_index] = []
    return reuse_by_case, node_ids_by_case, display_nodes_by_case


def _apply_dummy_finalization_gvalue_conditions(
    draft: str,
    *,
    case_id_symbol: str,
    profile_set,
    super_node_ids: list[str],
    c_external_nodes: list[str],
    reuse_plan_by_case: Mapping[int, Sequence] | None = None,
    reuse_nodes_by_case: Mapping[int, Sequence[str]] | None = None,
) -> tuple[str, list[dict]]:
    gvalue_conditions: list[dict] = []
    grouped_entries: dict[tuple[int, ...], list[dict]] = {}

    def _find_gvalue_get_assignment(var: str, row: int, col: int) -> str | None:
        for matrix_name in ["Gred_code", "Gred_dyn_code", "G_code"]:
            assignment = f"{var} = get_CODE(&{matrix_name}, {row}, {col});"
            if f"    {assignment}" in draft:
                return assignment
        match = re.search(
            rf"^\s+({re.escape(var)}\s*=\s*get_CODE\(&[^;]+;\s*)$",
            draft,
            flags=re.MULTILINE,
        )
        if match:
            return match.group(1).strip()
        return None

    def _reuse_map_for_case(case_index: int) -> dict[tuple[int, int], tuple[tuple[int, int], int]]:
        items = list((reuse_plan_by_case or {}).get(case_index) or [])
        nodes = [str(node) for node in (reuse_nodes_by_case or {}).get(case_index) or []]
        if not items or not nodes:
            return {}
        node_to_super = {str(node): index for index, node in enumerate(super_node_ids)}
        mapped: dict[tuple[int, int], tuple[tuple[int, int], int]] = {}
        for item in items:
            try:
                target_node = nodes[int(item.target_row)]
                target_col_node = nodes[int(item.target_col)]
                base_node = nodes[int(item.base_row)]
                base_col_node = nodes[int(item.base_col)]
                target = (node_to_super[target_node], node_to_super[target_col_node])
                base = (node_to_super[base_node], node_to_super[base_col_node])
            except Exception:
                continue
            mapped[target] = (base, int(item.sign))
        return mapped

    def _switch_lines_with_reuse(case_indices: Sequence[int], entries: Sequence[dict]) -> list[str] | None:
        if not case_indices or not entries:
            return None
        grouped_by_lines: dict[tuple[str, ...], list[int]] = {}
        used_reuse = False
        for case_index in case_indices:
            reuse_map = _reuse_map_for_case(int(case_index))
            assigned_pairs: set[tuple[int, int]] = set()
            lines_for_case: list[str] = []
            for entry in entries:
                pair = (int(entry["row"]), int(entry["col"]))
                reuse = reuse_map.get(pair)
                if reuse and reuse[0] in assigned_pairs:
                    base_row, base_col = reuse[0]
                    base_var = _var_g_name(c_external_nodes, base_row, base_col)
                    prefix = "-" if int(reuse[1]) < 0 else ""
                    lines_for_case.append(f"{entry['var']} = {prefix}{base_var};")
                    used_reuse = True
                else:
                    lines_for_case.append(str(entry["assignment"]))
                assigned_pairs.add(pair)
            grouped_by_lines.setdefault(tuple(lines_for_case), []).append(int(case_index))
        if not used_reuse:
            return None
        lines = [f"    switch ({case_id_symbol}) {{"]
        for assignments, grouped_cases in grouped_by_lines.items():
            for index in grouped_cases:
                lines.append(f"    case {index}:")
            for assignment in assignments:
                lines.append(f"        {assignment}")
            lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
        ])
        return lines

    for row in range(len(super_node_ids)):
        for col in range(row, len(super_node_ids)):
            left_id = str(super_node_ids[row])
            right_id = str(super_node_ids[col])
            left_c = str(c_external_nodes[row])
            right_c = str(c_external_nodes[col])
            active_cases = [
                index
                for index, profile in enumerate(profile_set.case_profiles)
                if left_id in profile.final_node_order and right_id in profile.final_node_order
            ]
            var = _var_g_name(c_external_nodes, row, col)
            assignment = _find_gvalue_get_assignment(var, row, col)
            if assignment:
                grouped_entries.setdefault(tuple(active_cases), []).append({
                    "row": row,
                    "col": col,
                    "var": var,
                    "assignment": assignment,
                })
            if len(active_cases) == len(profile_set.case_profiles):
                continue
            condition = _case_condition(case_id_symbol, active_cases)
            gvalue_conditions.append({
                "var": var,
                "row": row,
                "col": col,
                "condition": condition,
            })
            pattern = (
                f'double {var} = createGValue("{var}", '
                f'"{left_c}", "{right_c}", 0, "TRUE");'
            )
            replacement = (
                f'double {var} = createGValue("{var}", '
                f'"{left_c}", "{right_c}", 0, "{condition}");'
            )
            draft = draft.replace(pattern, replacement)

    for case_indices, entries in grouped_entries.items():
        switch_lines = _switch_lines_with_reuse(case_indices, entries)
        if not switch_lines:
            continue
        assignments = [str(entry["assignment"]) for entry in entries]
        first_assignment = assignments[0]
        for assignment in assignments[1:]:
            draft = draft.replace(f"    {assignment}", "", 1)
        draft = draft.replace(
            f"    {first_assignment}",
            "\n".join(switch_lines),
            1,
        )
    return draft, gvalue_conditions


def _apply_dummy_finalization_injection_guards(
    draft: str,
    *,
    case_id_symbol: str,
    profile_set,
    super_node_ids: list[str],
    c_external_nodes: list[str],
) -> str:
    grouped_assignments: dict[tuple[int, ...], list[str]] = {}
    for row, node_id in enumerate(super_node_ids):
        active_cases = [
            index
            for index, profile in enumerate(profile_set.case_profiles)
            if str(node_id) in profile.final_node_order
        ]
        if len(active_cases) == len(profile_set.case_profiles):
            continue
        node_c = _c_identifier_name(c_external_nodes[row], f"N{row + 1}")
        candidates = [
            f"Inj{node_c} = get_CODE(&Ihisred_code, {row}, 0);",
            f"Inj{node_c} = get_CODE(&Ihisfinal_code, {row}, 0);",
            f"Inj{node_c} = 0.0;",
        ]
        for assignment in candidates:
            if f"    {assignment}" in draft:
                grouped_assignments.setdefault(tuple(active_cases), []).append(assignment)
                break

    for case_indices, assignments in grouped_assignments.items():
        first_assignment = assignments[0]
        for assignment in assignments[1:]:
            draft = draft.replace(f"    {assignment}", "", 1)
        draft = draft.replace(
            f"    {first_assignment}",
            "\n".join(_case_switch_assignment_lines(case_id_symbol, case_indices, assignments)),
            1,
        )
    return draft


def _build_dummy_finalized_matrix_dag_c_draft(
    payload: dict,
    *,
    alias_model: dict,
    profile_set,
    case_id_symbol: str,
    reuse_plan_by_case: Mapping[int, Sequence] | None = None,
    reuse_nodes_by_case: Mapping[int, Sequence[str]] | None = None,
) -> tuple[str, list[dict], list[str], dict]:
    raw_template_payload = alias_model["template_payload"]
    aliases = alias_model["aliases"]
    retained_layout_profiles, compact_external_order = _retained_layout_profiles_from_finalization(
        profile_set,
        raw_template_payload.get("external_nodes") or [],
    )
    if compact_external_order:
        raw_template_payload = _with_reordered_external_nodes(raw_template_payload, compact_external_order)
    template_internal_count = len(raw_template_payload.get("internal_nodes") or [])
    use_synthetic_dependency = template_internal_count >= 4
    template_payload = (
        _attach_multicase_fast_dependency(
            raw_template_payload,
            g_dependency=_promoted_dependency_for_aliases(aliases, kind="G"),
            ihis_dependency=_promoted_dependency_for_aliases(aliases),
        )
        if use_synthetic_dependency
        else raw_template_payload
    )
    request_payload = {
        **template_payload,
        "mode": "structured_formula",
        "display_mode": payload.get("display_mode") or template_payload.get("display_mode") or "compact",
        "simplify_level": payload.get("simplify_level") or template_payload.get("simplify_level") or "light",
        "assume_spd": payload.get("assume_spd", template_payload.get("assume_spd", True)),
        "use_suggested_order": payload.get("use_suggested_order", template_payload.get("use_suggested_order", False)),
        "preserve_structured_details_with_borrowed_dependency": not use_synthetic_dependency,
    }
    result = build_optimized_response(request_payload)
    draft = _insert_multicase_alias_layer(
        result["structured"]["c_draft"],
        case_id_symbol=case_id_symbol,
        profiles=alias_model["profiles"],
        branch_ids=alias_model["branch_ids"],
        aliases=aliases,
    )
    draft = _apply_multicase_conditional_diagonal_w_builder(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=alias_model["profiles"],
        aliases=aliases,
        gkk_template=_template_gkk_from_payload(template_payload),
    )
    draft = _apply_retained_layout_profile_compaction(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=retained_layout_profiles,
        nk=len(template_payload.get("internal_nodes") or []),
    )
    display_names = template_payload.get("node_display_names") or {}
    template_external = list(template_payload.get("external_nodes") or result.get("external_nodes") or [])
    c_external_nodes = [str(display_names.get(node, node)) for node in template_external]
    draft, gvalue_conditions = _apply_dummy_finalization_gvalue_conditions(
        draft,
        case_id_symbol=case_id_symbol,
        profile_set=profile_set,
        super_node_ids=template_external,
        c_external_nodes=c_external_nodes,
        reuse_plan_by_case=reuse_plan_by_case,
        reuse_nodes_by_case=reuse_nodes_by_case,
    )
    draft = _apply_dummy_finalization_injection_guards(
        draft,
        case_id_symbol=case_id_symbol,
        profile_set=profile_set,
        super_node_ids=template_external,
        c_external_nodes=c_external_nodes,
    )
    warnings = list(result.get("warnings") or [])
    warnings.append(
        "Info: dummy-finalized multi-case export used the shared structured matrix DAG "
        "instead of expanded final Gred formulas because the finalized formulas exceed the codegen budget."
    )
    result = {
        **result,
        "retained_layout_profiles": retained_layout_profiles,
    }
    return _ensure_static_blank_line(draft), gvalue_conditions, warnings, result


def _build_dummy_finalized_multi_case_response(payload: dict) -> dict:
    profiles = payload.get("case_profiles") or []
    case_id_symbol = str(payload.get("case_id_symbol") or "case_id")
    profile_set = build_finalization_profiles(profiles)
    final_results: list[dict] = []
    common_reduction_cache: dict[tuple, dict] = {}
    alias_model = _build_multicase_alias_template_payload(payload)
    template_payload = alias_model["template_payload"] if alias_model is not None else None
    aliases = alias_model["aliases"] if alias_model is not None else {}
    branch_ids = alias_model["branch_ids"] if alias_model is not None else []
    template_super_result = _reduced_super_result_from_payload(template_payload) if template_payload is not None else None
    alias_symbol_table = {
        alias: _symbol_dependency_for_owner(str(info.get("owner") or "RAM"))
        for alias, info in aliases.items()
    }
    use_alias_template_final_values = bool(
        template_super_result is not None
        and aliases
        and all(not profile.dummy_leaf_specs for profile in profile_set.case_profiles)
    )

    for case_index, source_profile in enumerate(profiles):
        case_payload = template_payload or source_profile.get("payload") or {}
        if use_alias_template_final_values:
            super_result = template_super_result
        elif template_super_result is not None:
            substitutions = _profile_alias_substitutions(source_profile, aliases, case_index)
            super_result = {
                **template_super_result,
                "G": sp.Matrix(template_super_result["G"]).xreplace(substitutions),
                "Ihis": sp.Matrix(template_super_result["Ihis"]).xreplace(substitutions),
                "K_v": sp.Matrix(template_super_result["K_v"]).xreplace(substitutions),
                "K_h": sp.Matrix(template_super_result["K_h"]).xreplace(substitutions),
            }
        else:
            cache_key = _common_reduction_cache_key(case_payload)
            if cache_key not in common_reduction_cache:
                common_reduction_cache[cache_key] = _reduced_super_result_from_payload(case_payload)
            super_result = common_reduction_cache[cache_key]
        profile = profile_set.case_profiles[case_index]
        final = finalize_profile_result(
            super_result["G"],
            super_result["Ihis"],
            super_result["external_nodes"],
            profile,
        )
        final_results.append({
            "index": case_index,
            "name": source_profile.get("name") or f"Case {case_index}",
            "profile": profile,
            "final": final,
            "symbol_table": {
                **(case_payload.get("symbol_dependency_table_tagged") or case_payload.get("symbol_dependency_table") or {}),
                **alias_symbol_table,
            },
            "recovery_nodes": super_result["internal_nodes"],
            "super_nodes": super_result["external_nodes"],
            "K_v": super_result["K_v"],
            "K_h": super_result["K_h"],
        })

    has_isolated_dummy_final_nodes = any(
        getattr(profile, "isolated_dummy_nodes", ())
        for profile in profile_set.case_profiles
    )
    use_matrix_dag_draft = bool(
        alias_model is not None
        and aliases
        and (
            has_isolated_dummy_final_nodes
            or _dummy_finalized_matrix_dag_is_preferred(final_results)
        )
    )
    (
        source_reuse_plan_by_case,
        source_reuse_node_ids_by_case,
        source_reuse_display_nodes_by_case,
    ) = _source_gred_reuse_by_case(profiles)
    final_reuse_plan_by_case, final_reuse_nodes_by_case = _dummy_finalized_gred_reuse_by_case(final_results)
    if any(source_reuse_plan_by_case.values()):
        display_reuse_plan_by_case = source_reuse_plan_by_case
        display_reuse_nodes_by_case = source_reuse_display_nodes_by_case
        draft_reuse_nodes_by_case = source_reuse_node_ids_by_case
    else:
        display_reuse_plan_by_case = final_reuse_plan_by_case
        display_reuse_nodes_by_case = final_reuse_nodes_by_case
        draft_reuse_nodes_by_case = final_reuse_nodes_by_case
    gvalue_conditions: list[dict] = []
    extra_warnings: list[str] = []
    matrix_dag_result: dict | None = None
    if use_matrix_dag_draft:
        c_draft, gvalue_conditions, extra_warnings, matrix_dag_result = _build_dummy_finalized_matrix_dag_c_draft(
            payload,
            alias_model=alias_model,
            profile_set=profile_set,
            case_id_symbol=case_id_symbol,
            reuse_plan_by_case=display_reuse_plan_by_case,
            reuse_nodes_by_case=draft_reuse_nodes_by_case,
        )
        fast_path = "dummy_finalization_alias_template_matrix_dag"
    else:
        c_draft = _build_dummy_finalized_multi_case_c_draft(
            case_id_symbol,
            profile_set,
            final_results,
            aliases=aliases,
            profiles=profiles,
            branch_ids=branch_ids,
        )
        fast_path = "dummy_finalization_alias_template" if aliases else "dummy_finalization_profiles"

    warnings = [
        "Info: DummyBranch profiles use case-specific final solver dimensions after the shared super reduction."
    ] + extra_warnings + [
        (
            f"Warning: {alias} has mixed case owners {sorted(set((info.get('case_owners') or {}).values()))}; "
            f"promoted to {info.get('owner')} for safety. "
            "case_id is fixed before simulation and must not change at runtime."
        )
        for alias, info in aliases.items()
        if len(set((info.get("case_owners") or {}).values())) > 1
    ]
    return {
        "ok": True,
        "mode": "multi_case_c_export",
        "case_id_symbol": case_id_symbol,
        "case_profiles": [
            {
                "index": index,
                "name": str(profile.get("name") or f"Case {index}"),
                "case_map": dict(profile.get("case_map") or {}),
            }
            for index, profile in enumerate(profiles)
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "multi_case": {
            "codegen_mode": "case-agnostic alias template with dummy finalization" if aliases else "case-specific dummy finalization",
            "fast_path": fast_path,
            "profile_count": len(profiles),
            "aliases": aliases,
            "gvalue_conditions": gvalue_conditions,
            "uses_case_conditional_gvalue": bool(gvalue_conditions),
            "gred_entry_reuse_by_case": _clean_gred_entry_reuse_by_case_nodes(
                display_reuse_plan_by_case,
                profiles,
                display_reuse_nodes_by_case,
            ),
            "external_nodes": profile_set.super_node_order,
            "effective_internal_nodes": [],
            "block_type": ((matrix_dag_result or {}).get("structured") or {}).get("block_type"),
            "template_blocks": _clean_value(((matrix_dag_result or {}).get("structured") or {}).get("blocks") or {}),
            "template_block_nodes": {
                "retained_order": (matrix_dag_result or {}).get("external_nodes") or profile_set.super_node_order,
                "internal_order": (matrix_dag_result or {}).get("effective_internal_nodes") or [],
            },
            "retained_layout_profiles": (matrix_dag_result or {}).get("retained_layout_profiles") or [],
            "finalization_profiles": [
                {
                    "profile_id": profile.profile_id,
                    "case_ids": list(profile.case_ids),
                    "super_node_order": list(profile.super_node_order),
                    "final_node_order": list(profile.final_node_order),
                    "dummy_nodes": list(profile.dummy_nodes),
                    "isolated_dummy_nodes": list(getattr(profile, "isolated_dummy_nodes", ())),
                    "final_dimension": profile.final_dimension,
                    "node_roles": dict(profile.node_roles),
                }
                for profile in profile_set.unique_profiles
            ],
            "template_summary": {
                "super_nodes": profile_set.super_node_order,
                "NR_SUPER": len(profile_set.super_node_order),
                "NR_FINAL_MAX": max((item["final"].G.rows for item in final_results), default=0),
                "profile_dimensions": [item["final"].G.rows for item in final_results],
            },
            "c_draft": c_draft,
        },
    }


_MULTICASE_TIMING_KEYS = (
    "parse_payload",
    "build_global_local_case_map",
    "classify_node_roles",
    "build_effective_aliases",
    "build_assembled_entry_aliases",
    "assemble_super_matrices",
    "partition_blocks",
    "build_inverse_dag",
    "build_shared_reduced_dag",
    "build_recovery_profiles",
    "build_finalization_profiles",
    "build_conditional_gvalues",
    "generate_c_text",
    "optional_validation",
    "alias_template_pipeline",
    "symbol_mux_pipeline",
    "dummy_finalization_pipeline",
    "per_case_fallback_pipeline",
)


def _attach_multicase_diagnostics(
    response: dict,
    started: float,
    dummy_role_classification: dict,
    timing_ms: dict[str, float] | None = None,
) -> dict:
    multi = response.get("multi_case") or {}
    aliases = multi.get("aliases") or {}
    c_draft = str(multi.get("c_draft") or "")
    effective_internal = list(multi.get("effective_internal_nodes") or [])
    final_profiles = multi.get("finalization_profiles") or []
    retained_layout_profiles = multi.get("retained_layout_profiles") or []
    recovery_profiles = multi.get("recovery_profiles") or []
    dummy_blocks = multi.get("dummy_node_blocks") or {}
    runtime_groups = multi.get("runtime_case_groups") or []
    runtime_selectors = [
        str(group.get("case_id_symbol") or "")
        for group in runtime_groups
        if str(group.get("case_id_symbol") or "")
    ]
    create_gvalue_lines = [
        line
        for line in c_draft.splitlines()
        if "createGValue" in line
    ]
    runtime_case_used_in_gvalue_condition = sum(
        1
        for line in create_gvalue_lines
        for selector in runtime_selectors
        if selector and selector in line
    )
    runtime_aliases = [
        info
        for info in aliases.values()
        if info.get("runtime_mutable")
    ]
    runtime_forced_code_aliases = [
        info
        for info in runtime_aliases
        if info.get("owner") in {"CODE", "CODE_PER_STEP"}
        and len({str(value) for value in (info.get("case_values") or {}).values()}) > 1
    ]
    common_dummy_internal = list(dummy_role_classification.get("mixed_physical_dummy_internal") or [])
    timing = {key: 0.0 for key in _MULTICASE_TIMING_KEYS}
    if timing_ms:
        timing.update({key: round(float(value), 3) for key, value in timing_ms.items()})
    timing["total"] = round((time.perf_counter() - started) * 1000, 3)
    diagnostics = {
        "timing_ms": timing,
        "init_time_case_count": int(multi.get("profile_count") or len(response.get("case_profiles") or [])),
        "runtime_case_group_count": len(runtime_groups),
        "runtime_case_count_per_group": {
            str(group.get("name") or group.get("branch_id") or index): int(group.get("case_count") or 0)
            for index, group in enumerate(runtime_groups)
        },
        "runtime_case_switches_in_code": len(runtime_groups),
        "runtime_mutable_alias_count": len(runtime_aliases),
        "runtime_forced_code_alias_count": len(runtime_forced_code_aliases),
        "runtime_case_used_in_gvalue_condition": runtime_case_used_in_gvalue_condition,
        "per_runtime_case_final_gred_expansion_count": 0 if runtime_groups and multi.get("fast_path") in {
            "case_alias_template",
            "case_alias_template_symbol_mux",
            "dummy_finalization_alias_template_matrix_dag",
        } else None,
        "global_case_count": int(multi.get("profile_count") or len(response.get("case_profiles") or [])),
        "local_case_group_count": len({
            str(branch_id)
            for profile in (response.get("case_profiles") or [])
            for branch_id in (profile.get("case_map") or {})
        }),
        "alias_count": len(aliases),
        "assembled_entry_alias_count": len([
            info for info in aliases.values()
            if str(info.get("source") or "").startswith("assembled")
            or str(info.get("branch_id") or "").startswith("assembled")
        ]),
        "common_retained_count": len(multi.get("external_nodes") or []),
        "common_internal_count": len(effective_internal),
        "isolated_dummy_internal_count": len(common_dummy_internal),
        "mixed_physical_dummy_internal": common_dummy_internal,
        "unsupported_role_mix": list(dummy_role_classification.get("unsupported_role_mix") or []),
        "unique_finalization_profile_count": len({tuple(item.get("final_node_order") or []) for item in final_profiles}),
        "unique_recovery_profile_count": len(recovery_profiles),
        "shared_inverse_codegen_count": 1 if effective_internal else 0,
        "per_case_inverse_codegen_count": 0,
        "per_case_full_reduction_count": 0 if multi.get("fast_path") in {
            "case_alias_template",
            "case_alias_template_symbol_mux",
            "dummy_finalization_alias_template_matrix_dag",
        } else int(multi.get("profile_count") or 0),
        "per_case_final_substitution_count": 0 if multi.get("fast_path") == "case_alias_template" else None,
        "expanded_scalar_ccode_count": 0 if multi.get("fast_path") in {
            "case_alias_template",
            "case_alias_template_symbol_mux",
            "dummy_finalization_alias_template_matrix_dag",
        } else None,
        "dummy_finalization_profile_count": len(final_profiles),
        "retained_layout_profile_count": len(retained_layout_profiles),
        "retained_profile_dimensions": [int(item.get("nr_active") or 0) for item in retained_layout_profiles],
        "super_then_slice_runtime_calculations": 0 if retained_layout_profiles else None,
        "dummy_node_block_count": int(dummy_blocks.get("count") or 0) if isinstance(dummy_blocks, dict) else 0,
        "c_output_length": len(c_draft),
    }
    multi["diagnostics"] = diagnostics
    response["multi_case"] = multi
    return response


def build_multi_case_response(payload: dict) -> dict:
    started = time.perf_counter()
    timing_ms: dict[str, float] = {"parse_payload": 0.0}

    def time_call(name: str, fn, *args, **kwargs):
        phase_started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            timing_ms[name] = timing_ms.get(name, 0.0) + (time.perf_counter() - phase_started) * 1000

    profiles = payload.get("case_profiles") or []
    if not profiles:
        raise ValueError("case_profiles is required for multi-case C export")
    profiles, dummy_role_classification = time_call(
        "classify_node_roles",
        _promote_common_isolated_dummy_internal_profiles,
        profiles,
        payload,
    )
    payload = {**payload, "case_profiles": profiles}
    has_dummy_finalization = time_call("build_finalization_profiles", _profiles_have_dummy_finalization, profiles)
    if has_dummy_finalization:
        response = time_call("dummy_finalization_pipeline", _build_dummy_finalized_multi_case_response, payload)
        return _attach_multicase_diagnostics(response, started, dummy_role_classification, timing_ms)
    dummy_node_block_analysis = time_call("build_recovery_profiles", _dummy_node_block_analysis, profiles)
    if dummy_node_block_analysis:
        payload = {**payload, "_dummy_node_block_analysis": dummy_node_block_analysis}
    alias_response = time_call("alias_template_pipeline", _try_build_alias_template_response, payload)
    if alias_response is not None:
        return _attach_multicase_diagnostics(alias_response, started, dummy_role_classification, timing_ms)
    fast_response = time_call("symbol_mux_pipeline", _try_build_payload_symbol_mux_response, payload)
    if fast_response is not None:
        return _attach_multicase_diagnostics(fast_response, started, dummy_role_classification, timing_ms)
    profile_results: list[dict] = []
    signature: dict | None = None
    warnings: list[str] = []
    fallback_started = time.perf_counter()
    for index, profile in enumerate(profiles):
        case_payload = profile.get("payload")
        if not isinstance(case_payload, dict):
            raise ValueError(f"case profile {index} is missing payload")
        request_payload = {
            **case_payload,
            "mode": "structured_formula",
            "display_mode": payload.get("display_mode") or case_payload.get("display_mode") or "compact",
            "simplify_level": payload.get("simplify_level") or case_payload.get("simplify_level") or "light",
            "assume_spd": payload.get("assume_spd", case_payload.get("assume_spd", True)),
            "use_suggested_order": payload.get("use_suggested_order", case_payload.get("use_suggested_order", False)),
        }
        result = build_optimized_response(request_payload)
        current_signature = _multi_case_signature(result)
        if signature is None:
            signature = current_signature
        elif current_signature != signature:
            raise ValueError(
                "case profiles are not compatible: retained/internal order, block type, and structured workflow must match"
            )
        warnings.extend(result.get("warnings") or [])
        profile_results.append({"profile": profile, "result": result})
    timing_ms["per_case_fallback_pipeline"] = (time.perf_counter() - fallback_started) * 1000

    case_id_symbol = str(payload.get("case_id_symbol") or "case_id")
    response = {
        "ok": True,
        "mode": "multi_case_c_export",
        "case_id_symbol": case_id_symbol,
        "case_profiles": [
            {
                "name": item["profile"].get("name") or f"Case {index + 1}",
                "comment": item["profile"].get("comment") or "",
                "case_map": item["profile"].get("case_map") or {},
            }
            for index, item in enumerate(profile_results)
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "multi_case": {
            **(signature or {}),
            "profile_count": len(profile_results),
            "c_draft": _build_multi_case_c_draft(case_id_symbol, profile_results),
        },
    }
    return _attach_multicase_diagnostics(response, started, dummy_role_classification, timing_ms)


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("mode") == "multi_case_c_export" or payload.get("case_profiles"):
        response = build_multi_case_response(payload)
    else:
        response = build_optimized_response(payload)
    json.dump(response, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.exit(1)
