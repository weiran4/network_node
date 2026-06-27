import ast
from pathlib import Path

import pytest
import sympy as sp

from nodal_tool.optimized_elimination import structural_gred_entry_reuse_plan


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixture_value(path: Path, name: str):
    prefix = f"{name} = "
    lines = path.read_text(encoding="utf-8").splitlines()
    namespace = {"sp": sp}
    for line in lines:
        if "= sp.symbols(" in line:
            exec(line, namespace)
            break
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix) :]
            if name.endswith("_nodes") or name == "all_nodes":
                return ast.literal_eval(value)
            exec(line, namespace)
            return namespace[name]
    raise AssertionError(f"{name} not found in {path}")


def _load_reuse_fixture(filename: str):
    path = FIXTURE_DIR / filename
    return {
        "G_full": _fixture_value(path, "G_full"),
        "all_nodes": _fixture_value(path, "all_nodes"),
        "external_nodes": _fixture_value(path, "external_nodes"),
        "internal_nodes": _fixture_value(path, "internal_nodes"),
    }


def _target_base_signs(reuse_plan):
    return {
        (item.target_row, item.target_col): (item.base_row, item.base_col, item.sign)
        for item in reuse_plan
    }


def test_three_internal_fixture_finds_known_nonzero_whole_entry_reuse_without_expensive_simplification(monkeypatch):
    fixture = _load_reuse_fixture("test5_3internal_symbolic_output.py")

    def forbidden(*args, **kwargs):  # pragma: no cover - only used if helper regresses
        raise AssertionError("structural reuse helper must not call expensive SymPy simplification")

    monkeypatch.setattr(sp, "cancel", forbidden)
    monkeypatch.setattr(sp, "simplify", forbidden)
    monkeypatch.setattr(sp, "factor", forbidden)

    plan = structural_gred_entry_reuse_plan(
        fixture["G_full"],
        fixture["all_nodes"],
        fixture["external_nodes"],
        fixture["internal_nodes"],
    )

    expected = {
        (1, 4): (0, 4, -1),  # B,RC_a = -A,RC_a
        (2, 5): (1, 5, -1),  # C,RC_b = -B,RC_b
        (2, 6): (0, 6, -1),  # C,RC_c = -A,RC_c
    }
    assert _target_base_signs(plan) == expected


def test_five_internal_dense_fixture_does_not_invent_whole_entry_reuse(monkeypatch):
    fixture = _load_reuse_fixture("test5_symbolic_output.py")

    def forbidden(*args, **kwargs):  # pragma: no cover - only used if helper regresses
        raise AssertionError("structural reuse helper must not call expensive SymPy simplification")

    monkeypatch.setattr(sp, "cancel", forbidden)
    monkeypatch.setattr(sp, "simplify", forbidden)
    monkeypatch.setattr(sp, "factor", forbidden)

    plan = structural_gred_entry_reuse_plan(
        fixture["G_full"],
        fixture["all_nodes"],
        fixture["external_nodes"],
        fixture["internal_nodes"],
    )

    assert plan == []
