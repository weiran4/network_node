from __future__ import annotations

import json
import re
import sys

import sympy as sp

from nodal_tool.ground import apply_ground_constraint, validate_ground_partition
from nodal_tool.optimized_elimination import (
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


def main() -> None:
    payload = json.load(sys.stdin)
    simplify_level = payload.get("simplify_level") or "light"
    display_mode = payload.get("display_mode") or "compact"
    use_suggested_order = bool(payload.get("use_suggested_order", False))

    G, Ihis, G_tagged, Ihis_tagged, node_order, external_nodes, internal_nodes, partition_warnings = _partition_payload(payload)
    warnings = list(partition_warnings)

    structured = build_structured_formula(
        G,
        Ihis,
        node_order,
        external_nodes,
        internal_nodes,
        use_suggested_order=use_suggested_order,
        simplify_level=simplify_level,
    )
    tagged_structured = None
    if G_tagged is not None and Ihis_tagged is not None:
        tagged_structured = build_structured_formula(
            G_tagged,
            Ihis_tagged,
            node_order,
            external_nodes,
            structured["effective_internal_nodes"],
            use_suggested_order=False,
            simplify_level=simplify_level,
        )
    rtds_stage_plan = build_dependency_stage_plan(
        structured,
        payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {},
        simplify_level=simplify_level,
        analysis_structured=tagged_structured,
    )
    warnings.extend(structured.get("warnings", []))
    blocks = structured["blocks"]

    response = {
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
            "dependency_analysis": _clean_value(rtds_stage_plan.get("dependency_analysis", {})),
            "dynamic_subblock": _clean_value(rtds_stage_plan.get("dynamic_subblock", {})),
            "c_draft": c_draft_for_structured_formula(
                structured,
                node_display_names=payload.get("node_display_names") or {},
                rtds_stage_plan=rtds_stage_plan,
            ),
        },
    }
    json.dump(response, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.exit(1)
