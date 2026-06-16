from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

import sympy as sp


class SymbolDependency(str, Enum):
    RAM_CONSTANT = "RAM_CONSTANT"
    CODE_VARIABLE = "CODE_VARIABLE"
    STEP_HISTORY = "STEP_HISTORY"
    UNKNOWN = "UNKNOWN"


class ExpressionStage(str, Enum):
    RAM_INIT = "RAM_INIT"
    CODE_UPDATE = "CODE_UPDATE"
    CODE_PER_STEP = "CODE_PER_STEP"
    UNKNOWN = "UNKNOWN"


def _normalize_dependency(value: Any) -> SymbolDependency:
    if isinstance(value, SymbolDependency):
        return value
    text = str(value or "").strip().upper()
    try:
        return SymbolDependency(text)
    except ValueError:
        return SymbolDependency.UNKNOWN


def _normalize_symbol_table(symbol_table: Mapping[str, Any] | None) -> dict[str, SymbolDependency]:
    return {str(key): _normalize_dependency(value) for key, value in (symbol_table or {}).items()}


def collect_expr_dependencies(expr: sp.Expr, symbol_table: Mapping[str, Any] | None) -> dict[str, Any]:
    table = _normalize_symbol_table(symbol_table)
    dependencies: set[SymbolDependency] = set()
    unknown_symbols: list[str] = []
    for symbol in sorted(sp.sympify(expr).free_symbols, key=lambda item: item.name):
        dependency = table.get(symbol.name, SymbolDependency.UNKNOWN)
        dependencies.add(dependency)
        if dependency == SymbolDependency.UNKNOWN:
            unknown_symbols.append(symbol.name)
    return {
        "dependencies": dependencies,
        "unknown_symbols": unknown_symbols,
    }


def classify_expr_stage(expr: sp.Expr, symbol_table: Mapping[str, Any] | None) -> str:
    collected = collect_expr_dependencies(expr, symbol_table)
    dependencies: set[SymbolDependency] = collected["dependencies"]
    if SymbolDependency.UNKNOWN in dependencies:
        return ExpressionStage.UNKNOWN.value
    if SymbolDependency.STEP_HISTORY in dependencies:
        return ExpressionStage.CODE_PER_STEP.value
    if SymbolDependency.CODE_VARIABLE in dependencies:
        return ExpressionStage.CODE_UPDATE.value
    return ExpressionStage.RAM_INIT.value


def classify_matrix_stage(matrix: sp.MatrixBase | Sequence[Sequence[Any]], symbol_table: Mapping[str, Any] | None) -> list[list[str]]:
    M = sp.Matrix(matrix)
    return [
        [classify_expr_stage(M[row, col], symbol_table) for col in range(M.cols)]
        for row in range(M.rows)
    ]


def summarize_stage_matrix(stage_matrix: Sequence[Sequence[str]]) -> dict[str, int]:
    counts = {stage.value: 0 for stage in ExpressionStage}
    for row in stage_matrix:
        for stage in row:
            key = str(stage)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _stage_vector(vector: sp.MatrixBase | Sequence[Any], symbol_table: Mapping[str, Any] | None) -> list[str]:
    V = sp.Matrix(vector)
    if V.cols != 1:
        V = V.reshape(V.rows * V.cols, 1)
    return [classify_expr_stage(V[row, 0], symbol_table) for row in range(V.rows)]


def _unknown_symbols_in_expr(expr: sp.Expr, symbol_table: Mapping[str, Any] | None) -> list[str]:
    return collect_expr_dependencies(expr, symbol_table)["unknown_symbols"]


def _warn_unknowns(label: str, values: sp.MatrixBase, symbol_table: Mapping[str, Any] | None, warnings: list[str]) -> None:
    unknowns: set[str] = set()
    for value in values:
        unknowns.update(_unknown_symbols_in_expr(value, symbol_table))
    for symbol in sorted(unknowns):
        warnings.append(f"Symbol {symbol} has no dependency category. Cannot safely assign RAM/CODE stage for {label}.")


def _warn_step_history(label: str, stage_values: Sequence[Any], warnings: list[str]) -> None:
    flattened: list[str] = []
    for item in stage_values:
        if isinstance(item, list):
            flattened.extend(str(value) for value in item)
        else:
            flattened.append(str(item))
    if ExpressionStage.CODE_PER_STEP.value in flattened:
        warnings.append(f"{label} contains STEP_HISTORY dependency. Check model: G matrix should normally not depend on Ihis/history.")


def analyze_reduced_model_dependencies(model: Mapping[str, Any], symbol_table: Mapping[str, Any] | None) -> dict[str, Any]:
    warnings: list[str] = []
    normalized_table = {key: value.value for key, value in _normalize_symbol_table(symbol_table).items()}

    Gred = sp.Matrix(model.get("Gred", []))
    Ihisred = sp.Matrix(model.get("Ihisred", []))
    W = sp.Matrix(model.get("W", []))
    Kv = sp.Matrix(model.get("Kv", []))
    Kh = sp.Matrix(model.get("Kh", []))

    Gred_stage = classify_matrix_stage(Gred, normalized_table)
    W_stage = classify_matrix_stage(W, normalized_table)
    Kv_stage = classify_matrix_stage(Kv, normalized_table)
    Ihisred_stage = _stage_vector(Ihisred, normalized_table) if Ihisred.rows or Ihisred.cols else []
    Kh_stage = _stage_vector(Kh, normalized_table) if Kh.rows or Kh.cols else []

    for label, matrix in (("Gred", Gred), ("W", W), ("Kv", Kv), ("Ihisred", Ihisred), ("Kh", Kh)):
        _warn_unknowns(label, matrix, normalized_table, warnings)
    _warn_step_history("Gred", Gred_stage, warnings)
    _warn_step_history("W", W_stage, warnings)

    return {
        "symbol_table": normalized_table,
        "Gred_stage": Gred_stage,
        "Ihisred_stage": Ihisred_stage,
        "W_stage": W_stage,
        "Kv_stage": Kv_stage,
        "Kh_stage": Kh_stage,
        "summary": {
            "Gred": summarize_stage_matrix(Gred_stage),
            "W": summarize_stage_matrix(W_stage),
            "Kv": summarize_stage_matrix(Kv_stage),
            "Ihisred": summarize_stage_matrix([[stage] for stage in Ihisred_stage]),
            "Kh": summarize_stage_matrix([[stage] for stage in Kh_stage]),
        },
        "warnings": warnings,
    }
