from __future__ import annotations

import json
import re
import sys

import sympy as sp

from nodal_tool.blackbox_validation import (
    BlackBoxBranchObserver,
    validate_blackbox_observers,
)


def _dump_json(response: dict) -> None:
    """Write CLI JSON using ASCII-safe escapes for Windows code pages."""
    json.dump(response, sys.stdout, ensure_ascii=True)


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


def _clean_expr(expr: sp.Expr) -> str:
    return str(sp.factor(sp.cancel(sp.simplify(expr))))


def _clean_observer_expr(expr: sp.Expr) -> str:
    return str(sp.expand(sp.cancel(sp.simplify(expr))))


def _clean_matrix(matrix: sp.Matrix) -> list[list[str]]:
    return [[_clean_expr(matrix[r, c]) for c in range(matrix.cols)] for r in range(matrix.rows)]


def _clean_vector(matrix: sp.Matrix) -> list[str]:
    return [_clean_expr(matrix[r, 0]) for r in range(matrix.rows)]


def _observer_from_payload(item: dict) -> BlackBoxBranchObserver:
    return BlackBoxBranchObserver(
        name=str(item.get("name") or "observer"),
        from_node=str(item.get("from_node") or item.get("from") or ""),
        to_node=str(item.get("to_node") or item.get("to") or ""),
        G=_parse_expr(item.get("G") or item.get("g") or "0"),
        Ihis=_parse_expr(item.get("Ihis") or item.get("ihis") or "0"),
        description=str(item.get("description") or ""),
    )


def main() -> None:
    payload = json.load(sys.stdin)
    nodes = [str(node) for node in payload["nodes"]]
    G_bb = sp.Matrix([[_parse_expr(item) for item in row] for row in payload["G_bb"]])
    Ihis_bb = sp.Matrix([[_parse_expr(item)] for item in payload["Ihis_bb"]])
    observers = [_observer_from_payload(item) for item in payload.get("observers", [])]

    result = validate_blackbox_observers(G_bb, Ihis_bb, nodes, observers)
    _dump_json(
        {
            "ok": True,
            "status": result.status,
            "G_obs": _clean_matrix(result.G_obs),
            "Ihis_obs": _clean_vector(result.Ihis_obs),
            "Delta_G": _clean_matrix(result.Delta_G),
            "Delta_Ihis": _clean_vector(result.Delta_Ihis),
            "warnings": result.warnings,
            "messages": result.messages,
            "unknown_symbols": sorted(str(symbol) for symbol in result.unknown_symbols),
            "observer_rows": [
                {
                    "name": row.name,
                    "from_node": row.from_node,
                    "to_node": row.to_node,
                    "C": _clean_matrix(row.C),
                    "d": _clean_expr(row.d),
                    "expression": _clean_observer_expr(row.expression),
                    "description": row.description,
                }
                for row in result.observer_rows
            ],
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _dump_json({"ok": False, "error": str(exc)})
        sys.exit(1)
