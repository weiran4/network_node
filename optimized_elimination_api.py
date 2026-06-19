from __future__ import annotations

import json
import itertools
import re
import sys
from collections.abc import Iterable, Sequence

import sympy as sp

from elimination import eliminate_internal_nodes
from nodal_tool.ground import apply_ground_constraint, validate_ground_partition
from nodal_tool.optimized_elimination import (
    _split_matrix_ram_and_code_terms,
    build_dependency_stage_plan,
    build_structured_formula,
    c_draft_for_structured_formula,
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
            G_direct[row, col] += sp.sympify(str(expr_text))
        for entry in stamp.get("Ihis") or []:
            row = node_index.get(entry.get("row"))
            if row is None:
                continue
            expr_text = entry.get("tagged") if tagged and entry.get("tagged") is not None else entry.get("expr")
            if expr_text is None:
                continue
            Ihis_direct[row, 0] += sp.sympify(str(expr_text))
    return G_direct, Ihis_direct, accepted


def _slice_direct_retained(
    direct_G: sp.Matrix,
    direct_Ihis: sp.Matrix,
    node_order: list[str],
    external_nodes: list[str],
) -> tuple[sp.Matrix, sp.Matrix]:
    indices = [node_order.index(node) for node in external_nodes]
    return direct_G.extract(indices, indices), direct_Ihis.extract(indices, [0])


def build_optimized_response(payload: dict) -> dict:
    simplify_level = payload.get("simplify_level") or "light"
    display_mode = payload.get("display_mode") or "compact"
    use_suggested_order = bool(payload.get("use_suggested_order", False))

    G, Ihis, G_tagged, Ihis_tagged, node_order, external_nodes, internal_nodes, partition_warnings = _partition_payload(payload)
    warnings = list(partition_warnings)
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

    structured = build_structured_formula(
        G,
        Ihis,
        node_order,
        external_nodes,
        internal_nodes,
        use_suggested_order=use_suggested_order,
        simplify_level=simplify_level,
        skip_symbolic_w_details=bool(borrowed_dependency),
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
        if direct_Grr_tagged is not None and direct_Ihisr_tagged is not None:
            rtds_stage_plan["Gred_direct_tagged"] = direct_Grr_tagged
            rtds_stage_plan["Ihisred_direct_tagged"] = direct_Ihisr_tagged
    warnings.extend(structured.get("warnings", []))
    blocks = structured["blocks"]

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
            "c_draft": c_draft_for_structured_formula(
                structured,
                node_display_names=payload.get("node_display_names") or {},
                rtds_stage_plan=rtds_stage_plan,
            ),
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
        return sp.Matrix([[sp.sympify(str(item))] for item in value])
    return sp.Matrix([[sp.sympify(str(item)) for item in row] for row in (value or [])])


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


def _represent_with_existing_aliases(values: Sequence[sp.Expr], grouped: dict) -> sp.Expr | None:
    groups = list(grouped.values())
    if not groups:
        return None
    choices = [-1, 0, 1]
    for coeffs in itertools.product(choices, repeat=len(groups)):
        if not any(coeffs):
            continue
        residuals: list[sp.Expr] = []
        for profile_index, value in enumerate(values):
            residual = sp.sympify(value)
            for coeff, group in zip(coeffs, groups):
                if coeff:
                    residual -= coeff * group["profile_values"][profile_index]
            residuals.append(sp.expand(residual))
        if all(_expr_equal_light(residuals[0], residual) for residual in residuals[1:]):
            expr = residuals[0]
            for coeff, group in zip(coeffs, groups):
                if coeff:
                    expr += coeff * sp.Symbol(group["alias"])
            return sp.expand(expr)
    return None


def _build_multicase_alias_template_payload(payload: dict) -> dict | None:
    profiles = payload.get("case_profiles") or []
    if len(profiles) < 2:
        return None
    _validate_multicase_topology(profiles)
    branch_ids = _branch_ids_from_profiles(profiles)
    if not branch_ids:
        return None

    base_payload = profiles[0].get("payload")
    if not isinstance(base_payload, dict):
        return None
    symbol_table = {}
    for profile in profiles:
        symbol_table.update((profile.get("payload") or {}).get("symbol_dependency_table") or {})

    matrices_by_key: dict[str, list[sp.Matrix]] = {
        "G_full": [_expr_matrix_from_payload(profile["payload"], "G_full") for profile in profiles],
        "Ihis_full": [_expr_matrix_from_payload(profile["payload"], "Ihis_full") for profile in profiles],
    }
    base_case_by_branch = {
        branch_id: _profile_case_index(profiles[0], branch_id, 0)
        for branch_id in branch_ids
    }

    aliases: dict[str, dict] = {}
    replacements: dict[tuple[str, int, int], sp.Expr] = {}
    grouped: dict[tuple[str, str, tuple[str, ...]], dict] = {}
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
                        profiles,
                        values,
                        branch_id,
                        base_case_by_branch.get(branch_id, 0),
                    )
                ]
                if len(candidates) != 1:
                    pending_composite_entries.append((key, row, col, values))
                    continue
                branch_id = candidates[0]
                sign, sequence_key = _signed_sequence_key(values)
                group_key = (branch_id, kind, sequence_key)
                if group_key not in grouped:
                    alias_index = 1 + sum(1 for item in grouped.values() if item["branch_id"] == branch_id and item["kind"] == kind)
                    local_cases = sorted({
                        _profile_case_index(profile, branch_id, base_case_by_branch.get(branch_id, 0))
                        for profile in profiles
                    })
                    case_values: dict[int, sp.Expr] = {}
                    canonical_values = [_parse_expr(text) for text in sequence_key]
                    for profile, canonical in zip(profiles, canonical_values):
                        local_case = _profile_case_index(profile, branch_id, base_case_by_branch.get(branch_id, 0))
                        if local_case in case_values and not _expr_equal_light(case_values[local_case], canonical):
                            raise ValueError(f"multi-case alias template found conflicting values for {branch_id} case {local_case}")
                        case_values[local_case] = canonical
                    alias = _alias_name(branch_id, kind, alias_index)
                    case_owners = {
                        case_index: _expr_stage(expr, symbol_table)
                        for case_index, expr in case_values.items()
                    }
                    owner = _promote_owner(case_owners.values())
                    aliases[alias] = {
                        "branch_id": branch_id,
                        "kind": kind,
                        "owner": owner,
                        "case_values": {
                            str(case_index): _expr_to_payload_text(case_values.get(case_index, sp.Integer(0)))
                            for case_index in local_cases
                        },
                        "case_owners": case_owners,
                    }
                    grouped[group_key] = {
                        "alias": alias,
                        "branch_id": branch_id,
                        "kind": kind,
                        "profile_values": canonical_values,
                    }
                replacements[(key, row, col)] = sign * sp.Symbol(grouped[group_key]["alias"])

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

    if unresolved_composite_entries:
        key, row, col, _values = unresolved_composite_entries[0]
        raise ValueError(
            f"multi-case alias template cannot represent {key}[{row},{col}] with local branch aliases"
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

    return {
        "template_payload": template_payload,
        "aliases": aliases,
        "profiles": profiles,
        "branch_ids": branch_ids,
    }


def _attach_multicase_fast_dependency(payload: dict) -> dict:
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
                table[symbol] = "CODE_VARIABLE"
        for symbol in ihis_symbols:
            table[symbol] = "CODE_VARIABLE"
        clone[table_name] = table
    return clone


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
            "c_draft": draft,
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


def _alias_assignment_lines(aliases: dict[str, dict], wanted_owner: str) -> list[str]:
    selected = {
        alias: info
        for alias, info in aliases.items()
        if info.get("owner") == wanted_owner
    }
    if not selected:
        return []
    lines = [f"    /* Resolve {wanted_owner} multi-case effective aliases as full values, never deltas. */"]
    for alias, info in selected.items():
        branch_id = info["branch_id"]
        local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        case_values = {int(case_index): _parse_expr(expr) for case_index, expr in (info.get("case_values") or {}).items()}
        lines.append(f"    switch ({local_name}) {{")
        for case_index in sorted(case_values):
            lines.append(f"    case {case_index}:")
            lines.append(f"        {alias} = {_ccode(case_values[case_index])};")
            lines.append("        break;")
        default_expr = case_values[min(case_values)] if case_values else sp.Integer(0)
        lines.append("    default:")
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
        + _alias_assignment_lines(aliases, "RAM")
    )
    draft = _insert_after_label(draft, "RAM_PASS1:", ram_lines)

    code_lines = _alias_assignment_lines(aliases, "CODE") + _alias_assignment_lines(aliases, "CODE_PER_STEP")
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


def _profile_alias_substitutions(profile: dict, aliases: dict[str, dict]) -> dict[sp.Symbol, sp.Expr]:
    substitutions: dict[sp.Symbol, sp.Expr] = {}
    for alias, info in aliases.items():
        branch_id = info.get("branch_id") or ""
        local_case = _profile_case_index(profile, branch_id, 0)
        case_values = {int(case_index): _parse_expr(expr) for case_index, expr in (info.get("case_values") or {}).items()}
        if local_case not in case_values and case_values:
            local_case = min(case_values)
        substitutions[sp.Symbol(alias)] = case_values.get(local_case, sp.Integer(0))
    return substitutions


def _profile_final_expr(expr: sp.Expr, profile: dict, aliases: dict[str, dict]) -> sp.Expr:
    return sp.sympify(expr).xreplace(_profile_alias_substitutions(profile, aliases))


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
                expr = _profile_final_expr(template_gred[row, col], profile, aliases)
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
                _expr_stage(_profile_final_expr(template_gred[row, col], profile, aliases), _profile_symbol_table(profile))
                for profile in profiles
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


def _apply_conditional_final_gvalues_to_structured_draft(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    template_gred: sp.Matrix,
    external_nodes: list[str],
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

    grouped_assignments: dict[tuple[int, ...], list[str]] = {}
    for plan in entry_plans:
        if not plan["code_cases"]:
            continue
        row = plan["row"]
        col = plan["col"]
        for matrix_name in ["Gred_code", "G_code"]:
            assignment = f"{plan['var']} = get_CODE(&{matrix_name}, {row}, {col});"
            if f"    {assignment}" in draft:
                case_indices = tuple(item["index"] for item in plan["code_cases"])
                grouped_assignments.setdefault(case_indices, []).append(assignment)
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
    return draft, gvalue_conditions


def _try_build_alias_template_response(payload: dict) -> dict | None:
    alias_model = _build_multicase_alias_template_payload(payload)
    if alias_model is None:
        return None
    template_payload = alias_model["template_payload"]
    request_payload = {
        **template_payload,
        "mode": "structured_formula",
        "display_mode": payload.get("display_mode") or template_payload.get("display_mode") or "compact",
        "simplify_level": payload.get("simplify_level") or template_payload.get("simplify_level") or "light",
        "assume_spd": payload.get("assume_spd", template_payload.get("assume_spd", True)),
        "use_suggested_order": payload.get("use_suggested_order", template_payload.get("use_suggested_order", False)),
    }
    result = build_optimized_response(request_payload)
    template_G, template_Ihis, _, _, template_nodes, template_external, _, _ = _partition_payload(template_payload)
    template_reduced = eliminate_internal_nodes(template_G, template_Ihis, template_nodes, template_external)
    case_id_symbol = str(payload.get("case_id_symbol") or "global_case_id")
    aliases = alias_model["aliases"]
    display_names = template_payload.get("node_display_names") or {}
    c_external_nodes = [str(display_names.get(node, node)) for node in template_external]
    symbol_table = {}
    for profile in alias_model["profiles"]:
        symbol_table.update((profile.get("payload") or {}).get("symbol_dependency_table") or {})
    warnings = list(result.get("warnings") or [])
    for alias, info in aliases.items():
        owners = set((info.get("case_owners") or {}).values())
        if len(owners) > 1:
            warnings.append(
                f"Warning: {alias} has mixed RAM/CODE ownership across cases. "
                "This export assumes case_id is fixed before simulation and must not change at runtime. "
                "If case_id changes during runtime, RAM-stamped values will not be withdrawn and results may be incorrect."
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
            "This export assumes case_id is fixed before simulation and must not change at runtime. "
            "If case_id changes during runtime, RAM-stamped values will not be withdrawn and results may be incorrect."
        )
        warnings.append(
            "case_id must be fixed before simulation and must not change at runtime; "
            "case-conditional GValue entries are only valid for initialization-time case selection."
        )
    else:
        if not aliases:
            return None
        draft = _insert_multicase_alias_layer(
            result["structured"]["c_draft"],
            case_id_symbol=case_id_symbol,
            profiles=alias_model["profiles"],
            branch_ids=alias_model["branch_ids"],
            aliases=aliases,
        )
        if has_mixed_final_g:
            draft, gvalue_conditions = _apply_conditional_final_gvalues_to_structured_draft(
                draft,
                case_id_symbol=case_id_symbol,
                profiles=alias_model["profiles"],
                aliases=aliases,
                template_gred=template_reduced.G_red,
                external_nodes=c_external_nodes,
            )
            warnings.append(
                "Warning: final G entries have mixed RAM/CODE ownership across cases. "
                "This export assumes case_id is fixed before simulation and must not change at runtime. "
                "If case_id changes during runtime, RAM-stamped values will not be withdrawn and results may be incorrect."
            )
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
            "codegen_mode": "case-agnostic alias template",
            "external_nodes": result.get("external_nodes") or [],
            "effective_internal_nodes": result.get("effective_internal_nodes") or [],
            "block_type": (result.get("structured") or {}).get("block_type"),
            "profile_count": len(alias_model["profiles"]),
            "aliases": aliases,
            "gvalue_conditions": gvalue_conditions,
            "uses_case_conditional_gvalue": bool(gvalue_conditions),
            "template": {
                "Gred": _clean_matrix(template_reduced.G_red),
                "Ihisred": _clean_vector(template_reduced.Ihis_red),
                "recovery_shape": [
                    len(result.get("effective_internal_nodes") or []),
                    len(result.get("external_nodes") or []),
                ],
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
            "c_draft": draft,
            "fast_path": "case_alias_template",
        },
    }


def _build_multi_case_c_draft(case_id_symbol: str, profile_results: list[dict]) -> str:
    scalar_mux = _try_build_scalar_mux_c_draft(profile_results)
    if scalar_mux:
        return scalar_mux
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
    return "\n".join(lines)


def build_multi_case_response(payload: dict) -> dict:
    profiles = payload.get("case_profiles") or []
    if not profiles:
        raise ValueError("case_profiles is required for multi-case C export")
    alias_response = _try_build_alias_template_response(payload)
    if alias_response is not None:
        return alias_response
    fast_response = _try_build_payload_symbol_mux_response(payload)
    if fast_response is not None:
        return fast_response
    profile_results: list[dict] = []
    signature: dict | None = None
    warnings: list[str] = []
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

    case_id_symbol = str(payload.get("case_id_symbol") or "case_id")
    return {
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
