from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import sympy as sp

from .dummy_finalization import DummyFinalizationResult, DummyLeaf, finalize_dummy_leaves


_DUMMY_FORBIDDEN_NODE_REFERENCE_FIELDS = (
    "observers",
    "observer_entries",
    "branch_current_observers",
    "current_observers",
    "controlled_sources",
    "control_sources",
    "controlled_current_sources",
)


@dataclass(frozen=True)
class FinalizationProfile:
    profile_id: int
    case_ids: tuple[int, ...]
    super_node_order: tuple[str, ...]
    final_node_order: list[str]
    dummy_nodes: list[str]
    dummy_leaf_specs: tuple[DummyLeaf, ...]
    isolated_dummy_nodes: tuple[str, ...]
    final_dimension: int
    node_roles: dict[str, str]


@dataclass(frozen=True)
class FinalizationProfileSet:
    super_node_order: list[str]
    case_profiles: list[FinalizationProfile]
    unique_profiles: list[FinalizationProfile]


def _dummy_leaf_from_payload(item: dict) -> DummyLeaf:
    return DummyLeaf(
        dummy_node=str(item["dummy_node"]),
        anchor_node=str(item["anchor_node"]),
        branch_id=str(item.get("branch_id") or item.get("id") or "DummyBranch"),
        conductance=sp.sympify(str(item.get("conductance") or "0")),
    )


def _isolated_dummy_nodes_from_payload(payload: dict) -> tuple[str, ...]:
    nodes: list[str] = []
    for block in payload.get("dummy_node_blocks") or []:
        for item in block.get("dummy_nodes") or []:
            node = item.get("node_id") or item.get("node") or item.get("display_name")
            if node is not None:
                nodes.append(str(node))
    return tuple(dict.fromkeys(nodes))


def _profile_signature(final_nodes: Sequence[str], leaves: Sequence[DummyLeaf], isolated_nodes: Sequence[str] = ()) -> tuple:
    leaf_sig = tuple(
        (leaf.dummy_node, leaf.anchor_node, leaf.branch_id, str(sp.sympify(leaf.conductance)))
        for leaf in leaves
    )
    return tuple(final_nodes), leaf_sig, tuple(isolated_nodes)


def _contains_node_reference(value, node: str) -> bool:
    if isinstance(value, str):
        return value == node
    if isinstance(value, dict):
        return any(_contains_node_reference(item, node) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_node_reference(item, node) for item in value)
    return False


def _validate_dummy_metadata_context(profile: dict, leaves: Sequence[DummyLeaf], super_nodes: Sequence[str]) -> None:
    payload = profile.get("payload") or {}
    case_name = str(profile.get("name") or "case profile")
    internal_nodes = {str(node) for node in (payload.get("internal_nodes") or [])}
    recovery_nodes = {str(node) for node in (payload.get("effective_internal_nodes") or payload.get("physical_recovery_nodes") or [])}

    for leaf in leaves:
        prefix = f"{case_name} / DummyBranch {leaf.branch_id}"
        if leaf.dummy_node in internal_nodes:
            raise ValueError(f"{prefix}: dummy node {leaf.dummy_node} cannot be a common physical internal node")
        if leaf.anchor_node in internal_nodes:
            raise ValueError(f"{prefix}: anchor node {leaf.anchor_node} cannot be a common physical internal node")
        if leaf.dummy_node in recovery_nodes:
            raise ValueError(f"{prefix}: dummy node {leaf.dummy_node} cannot be a physical recovery node")
        if leaf.dummy_node not in super_nodes:
            raise ValueError(f"{prefix}: dummy node {leaf.dummy_node} is not in super nodes")
        if leaf.anchor_node not in super_nodes:
            raise ValueError(f"{prefix}: anchor node {leaf.anchor_node} is not in super nodes")
        for field in _DUMMY_FORBIDDEN_NODE_REFERENCE_FIELDS:
            if _contains_node_reference(payload.get(field), leaf.dummy_node):
                raise ValueError(f"{prefix}: dummy node {leaf.dummy_node} cannot appear in {field}")


def _validate_isolated_dummy_context(profile: dict, isolated_nodes: Sequence[str], super_nodes: Sequence[str]) -> None:
    payload = profile.get("payload") or {}
    case_name = str(profile.get("name") or "case profile")
    internal_nodes = {str(node) for node in (payload.get("internal_nodes") or [])}
    recovery_nodes = {str(node) for node in (payload.get("effective_internal_nodes") or payload.get("physical_recovery_nodes") or [])}
    for node in isolated_nodes:
        prefix = f"{case_name} / N-Dummy {node}"
        if node not in super_nodes:
            raise ValueError(f"{prefix}: isolated dummy node is not in super nodes")
        if node in internal_nodes:
            raise ValueError(f"{prefix}: isolated dummy node must stay in the super retained template")
        if node in recovery_nodes:
            raise ValueError(f"{prefix}: isolated dummy node cannot be a physical recovery node")
        for field in _DUMMY_FORBIDDEN_NODE_REFERENCE_FIELDS:
            if _contains_node_reference(payload.get(field), node):
                raise ValueError(f"{prefix}: isolated dummy node cannot appear in {field}")


def build_finalization_profiles(profiles: Sequence[dict]) -> FinalizationProfileSet:
    if not profiles:
        raise ValueError("case profiles are required")
    first_payload = profiles[0].get("payload") or {}
    super_nodes = [str(node) for node in (first_payload.get("external_nodes") or [])]
    if not super_nodes:
        raise ValueError("dummy finalization requires a super external node order")

    unique_by_signature: dict[tuple, FinalizationProfile] = {}
    unique_profiles: list[FinalizationProfile] = []
    case_profiles: list[FinalizationProfile] = []
    case_signatures: list[tuple] = []

    for case_id, profile in enumerate(profiles):
        payload = profile.get("payload") or {}
        current_super = [str(node) for node in (payload.get("external_nodes") or [])]
        if current_super != super_nodes:
            raise ValueError(
                "dummy finalization requires all cases to share the same super external node order"
            )

        dummy_payload = profile.get("dummy_finalization") or {}
        leaves = tuple(_dummy_leaf_from_payload(item) for item in (dummy_payload.get("dummy_leaves") or []))
        _validate_dummy_metadata_context(profile, leaves, super_nodes)
        isolated_nodes = _isolated_dummy_nodes_from_payload(payload) if payload.get("defer_dummy_node_blocks") else ()
        _validate_isolated_dummy_context(profile, isolated_nodes, super_nodes)
        dummy_nodes = [leaf.dummy_node for leaf in leaves]
        for node in isolated_nodes:
            if node not in dummy_nodes:
                dummy_nodes.append(node)
        dummy_set = set(dummy_nodes)
        for leaf in leaves:
            if leaf.dummy_node not in super_nodes:
                raise ValueError(f"DummyBranch {leaf.branch_id}: dummy node {leaf.dummy_node} is not in super nodes")
            if leaf.anchor_node not in super_nodes:
                raise ValueError(f"DummyBranch {leaf.branch_id}: anchor node {leaf.anchor_node} is not in super nodes")
            if leaf.anchor_node in dummy_set:
                raise ValueError(f"DummyBranch {leaf.branch_id}: anchor node cannot be another dummy node")

        final_nodes = [node for node in super_nodes if node not in dummy_set]
        node_roles = {
            node: ("dummy_final" if node in dummy_set else "retained_physical")
            for node in super_nodes
        }
        signature = _profile_signature(final_nodes, leaves, isolated_nodes)
        case_signatures.append(signature)
        if signature in unique_by_signature:
            base = unique_by_signature[signature]
            merged = FinalizationProfile(
                profile_id=base.profile_id,
                case_ids=tuple([*base.case_ids, case_id]),
                super_node_order=base.super_node_order,
                final_node_order=base.final_node_order,
                dummy_nodes=base.dummy_nodes,
                dummy_leaf_specs=base.dummy_leaf_specs,
                isolated_dummy_nodes=base.isolated_dummy_nodes,
                final_dimension=base.final_dimension,
                node_roles=base.node_roles,
            )
            unique_by_signature[signature] = merged
            unique_profiles[base.profile_id] = merged
            continue

        built = FinalizationProfile(
            profile_id=len(unique_profiles),
            case_ids=(case_id,),
            super_node_order=tuple(super_nodes),
            final_node_order=final_nodes,
            dummy_nodes=dummy_nodes,
            dummy_leaf_specs=leaves,
            isolated_dummy_nodes=tuple(isolated_nodes),
            final_dimension=len(final_nodes),
            node_roles=node_roles,
        )
        unique_by_signature[signature] = built
        unique_profiles.append(built)

    return FinalizationProfileSet(
        super_node_order=super_nodes,
        case_profiles=[unique_by_signature[signature] for signature in case_signatures],
        unique_profiles=unique_profiles,
    )


def finalize_profile_result(
    G_super_red: sp.Matrix,
    Ihis_super_red: sp.Matrix,
    super_node_order: Sequence[str],
    profile: FinalizationProfile,
) -> DummyFinalizationResult:
    current_nodes = [str(node) for node in super_node_order]
    current_G = sp.Matrix(G_super_red)
    current_Ihis = sp.Matrix(Ihis_super_red)
    if profile.dummy_leaf_specs:
        leaf_result = finalize_dummy_leaves(
            current_G,
            current_Ihis,
            current_nodes,
            profile.dummy_leaf_specs,
        )
        current_G = sp.Matrix(leaf_result.G)
        current_Ihis = sp.Matrix(leaf_result.Ihis)
        current_nodes = list(leaf_result.nodes)
    if profile.isolated_dummy_nodes:
        drop = set(profile.isolated_dummy_nodes)
        keep = [index for index, node in enumerate(current_nodes) if node not in drop]
        current_G = current_G.extract(keep, keep)
        current_Ihis = current_Ihis.extract(keep, [0])
        current_nodes = [current_nodes[index] for index in keep]
    return DummyFinalizationResult(
        G=current_G,
        Ihis=current_Ihis,
        nodes=current_nodes,
    )
