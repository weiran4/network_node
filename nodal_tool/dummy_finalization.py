from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import sympy as sp


@dataclass(frozen=True)
class DummyLeaf:
    dummy_node: str
    anchor_node: str
    branch_id: str
    conductance: sp.Expr


@dataclass(frozen=True)
class DummyFinalizationResult:
    G: sp.Matrix
    Ihis: sp.Matrix
    nodes: list[str]


def _is_zero(expr: sp.Expr) -> bool:
    value = sp.simplify(_normalize_symbol_assumptions(sp.sympify(expr)))
    return value == 0


def _is_equal(lhs: sp.Expr, rhs: sp.Expr) -> bool:
    return _is_zero(sp.sympify(lhs) - sp.sympify(rhs))


def _normalize_symbol_assumptions(expr: sp.Expr) -> sp.Expr:
    replacements = {symbol: sp.Symbol(symbol.name) for symbol in sp.sympify(expr).free_symbols}
    return sp.sympify(expr).xreplace(replacements)


def _align_symbols_by_name(expr: sp.Expr, *targets: sp.Expr) -> sp.Expr:
    by_name: dict[str, sp.Symbol] = {}
    for target in targets:
        for symbol in sp.sympify(target).free_symbols:
            by_name.setdefault(symbol.name, symbol)
    replacements = {
        symbol: by_name[symbol.name]
        for symbol in sp.sympify(expr).free_symbols
        if symbol.name in by_name
    }
    return sp.sympify(expr).xreplace(replacements)


def validate_dummy_leaf_structure(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    nodes: Sequence[str],
    leaf: DummyLeaf,
) -> tuple[int, int]:
    node_list = [str(node) for node in nodes]
    if leaf.dummy_node not in node_list:
        raise ValueError(f"DummyBranch {leaf.branch_id}: dummy node {leaf.dummy_node} is missing")
    if leaf.anchor_node not in node_list:
        raise ValueError(f"DummyBranch {leaf.branch_id}: anchor node {leaf.anchor_node} is missing")
    dummy_index = node_list.index(leaf.dummy_node)
    anchor_index = node_list.index(leaf.anchor_node)
    if dummy_index == anchor_index:
        raise ValueError(f"DummyBranch {leaf.branch_id}: dummy and anchor nodes must be distinct")

    g = sp.sympify(leaf.conductance)
    if _is_zero(g):
        raise ValueError(f"DummyBranch {leaf.branch_id}: conductance must be nonzero")
    if not _is_equal(G[dummy_index, dummy_index], g):
        raise ValueError(f"DummyBranch {leaf.branch_id}: dummy diagonal is not {g}")
    if not _is_equal(G[dummy_index, anchor_index], -g):
        raise ValueError(f"DummyBranch {leaf.branch_id}: dummy-anchor entry is not {-g}")
    if not _is_equal(G[anchor_index, dummy_index], -g):
        raise ValueError(f"DummyBranch {leaf.branch_id}: anchor-dummy entry is not {-g}")
    if not _is_zero(Ihis[dummy_index, 0]):
        raise ValueError(f"DummyBranch {leaf.branch_id}: dummy Ihis must be zero")

    for index, node in enumerate(node_list):
        if index in {dummy_index, anchor_index}:
            continue
        if not _is_zero(G[dummy_index, index]) or not _is_zero(G[index, dummy_index]):
            raise ValueError(
                f"DummyBranch {leaf.branch_id}: dummy node {leaf.dummy_node} is coupled to {node}"
            )
    return dummy_index, anchor_index


def finalize_dummy_leaves(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    nodes: Sequence[str],
    leaves: Sequence[DummyLeaf],
) -> DummyFinalizationResult:
    current_G = sp.Matrix(G)
    current_Ihis = sp.Matrix(Ihis)
    current_nodes = [str(node) for node in nodes]

    for leaf in leaves:
        dummy_index, anchor_index = validate_dummy_leaf_structure(current_G, current_Ihis, current_nodes, leaf)
        g = _align_symbols_by_name(
            sp.sympify(leaf.conductance),
            current_G[anchor_index, anchor_index],
            current_G[dummy_index, dummy_index],
        )

        # Specialized Schur for a validated dummy leaf:
        # Gaa <- Gaa - (-g) * (1/g) * (-g) == Gaa - g.
        current_G[anchor_index, anchor_index] = sp.simplify(current_G[anchor_index, anchor_index] - g)
        keep = [index for index in range(len(current_nodes)) if index != dummy_index]
        current_G = current_G.extract(keep, keep)
        current_Ihis = current_Ihis.extract(keep, [0])
        current_nodes = [current_nodes[index] for index in keep]

    return DummyFinalizationResult(G=current_G, Ihis=current_Ihis, nodes=current_nodes)
