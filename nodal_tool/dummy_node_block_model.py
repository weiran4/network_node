from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import sympy as sp


G_EPSILON = sp.Symbol("G_EPSILON")


@dataclass(frozen=True)
class DummyNodeBlock:
    block_id: str
    dummy_nodes: tuple[str, ...]
    fixed_conductance: sp.Expr = G_EPSILON


def _is_zero(expr: sp.Expr) -> bool:
    return sp.simplify(sp.sympify(expr)) == 0


def _is_equal(lhs: sp.Expr, rhs: sp.Expr) -> bool:
    return _is_zero(sp.sympify(lhs) - sp.sympify(rhs))


def dummy_node_block_from_payload(item: dict) -> DummyNodeBlock:
    nodes = item.get("dummy_nodes") or item.get("nodes") or []
    return DummyNodeBlock(
        block_id=str(item.get("block_id") or item.get("id") or "DummyNodeBlock"),
        dummy_nodes=tuple(
            str((node.get("node_id") or node.get("display_name") or node.get("name")) if isinstance(node, dict) else node)
            for node in nodes
        ),
        fixed_conductance=sp.sympify(str(item.get("fixed_conductance") or "G_EPSILON")),
    )


def dummy_node_blocks_from_payload(payload: dict) -> list[DummyNodeBlock]:
    return [
        dummy_node_block_from_payload(item)
        for item in (payload.get("dummy_node_blocks") or payload.get("isolated_dummy_blocks") or [])
    ]


def stamp_dummy_node_blocks(
    nodes: Sequence[str],
    blocks: Sequence[DummyNodeBlock],
) -> tuple[sp.Matrix, sp.Matrix]:
    node_order = [str(node) for node in nodes]
    index_by_node = {node: index for index, node in enumerate(node_order)}
    G = sp.zeros(len(node_order), len(node_order))
    Ihis = sp.zeros(len(node_order), 1)
    for block in blocks:
        g = sp.sympify(block.fixed_conductance)
        for node in block.dummy_nodes:
            if node not in index_by_node:
                raise ValueError(f"DummyNodeBlock {block.block_id}: dummy node {node} is missing")
            G[index_by_node[node], index_by_node[node]] += g
    return G, Ihis


def validate_dummy_node_blocks(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    nodes: Sequence[str],
    blocks: Sequence[DummyNodeBlock],
    *,
    common_internal_nodes: Sequence[str],
) -> None:
    node_order = [str(node) for node in nodes]
    index_by_node = {node: index for index, node in enumerate(node_order)}
    internal = {str(node) for node in common_internal_nodes}
    for block in blocks:
        g = sp.sympify(block.fixed_conductance)
        if _is_zero(g):
            raise ValueError(f"DummyNodeBlock {block.block_id}: G_EPSILON must be nonzero")
        for node in block.dummy_nodes:
            if node not in index_by_node:
                raise ValueError(f"DummyNodeBlock {block.block_id}: dummy node {node} is missing")
            if node not in internal:
                raise ValueError(
                    "DummyNodeBlock currently supports only nodes eliminated in every case. "
                    f"The node {node} is retained in this case."
                )
            index = index_by_node[node]
            if not _is_equal(G[index, index], g):
                raise ValueError(f"DummyNodeBlock {block.block_id}: dummy node {node} diagonal is not {g}")
            if not _is_zero(Ihis[index, 0]):
                raise ValueError(f"DummyNodeBlock {block.block_id}: dummy node {node} Ihis must be zero")
            for other_index, other_node in enumerate(node_order):
                if other_index == index:
                    continue
                if not _is_zero(G[index, other_index]) or not _is_zero(G[other_index, index]):
                    raise ValueError(
                        f"DummyNodeBlock {block.block_id}: dummy node {node} is coupled to {other_node}"
                    )
