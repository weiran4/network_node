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
    structured_dependency_model,
)
from nodal_tool.multicase_finalization_profiles import (
    build_finalization_profiles,
    finalize_profile_result,
)
from nodal_tool.dummy_node_block_model import (
    dummy_node_blocks_from_payload,
    validate_dummy_node_blocks,
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


def _source_level_cse_assignments(
    assignments: Sequence[tuple[str, object]],
    *,
    temp_prefix: str,
) -> tuple[list[tuple[str, sp.Expr]], list[tuple[str, sp.Expr]]]:
    """Budgeted source-entry CSE for C assignment groups.

    This is intentionally a shallow source/Gfull optimization.  It never calls
    cancel/factor/simplify and is skipped for tiny or oversized groups, so dense
    Gred expressions cannot pull codegen into the historical slow path.
    """
    parsed: list[tuple[str, sp.Expr]] = [
        (lhs, expr if isinstance(expr, sp.Expr) else _parse_expr(str(expr)))
        for lhs, expr in assignments
    ]
    if len(parsed) < 2:
        return [], parsed
    exprs = [expr for _lhs, expr in parsed]
    total_ops = sum(int(sp.count_ops(expr)) for expr in exprs)
    total_chars = sum(len(str(expr)) for expr in exprs)
    if total_ops < 24 and total_chars < 180:
        return [], parsed
    if len(parsed) > 80 or total_ops > 1800 or total_chars > 12000:
        return [], parsed

    replacements, reduced_exprs = sp.cse(
        exprs,
        symbols=sp.numbered_symbols(temp_prefix),
        order="none",
    )
    used_symbols = set().union(*(sp.sympify(expr).free_symbols for expr in reduced_exprs))
    live_replacements: list[tuple[sp.Symbol, sp.Expr]] = []
    for symbol, expr in reversed(replacements):
        if symbol not in used_symbols:
            continue
        parsed_expr = sp.sympify(expr)
        live_replacements.append((symbol, parsed_expr))
        used_symbols.update(parsed_expr.free_symbols)
    live_replacements.reverse()
    replacements = live_replacements
    parsed_replacements = [(str(symbol), sp.sympify(expr)) for symbol, expr in replacements]
    if not any(int(sp.count_ops(expr)) >= 2 or len(str(expr)) >= 20 for _symbol, expr in parsed_replacements):
        return [], parsed
    reduced = [
        (lhs, sp.sympify(expr))
        for (lhs, _original), expr in zip(parsed, reduced_exprs)
    ]
    return parsed_replacements, reduced


def _emit_source_level_cse_assignment_lines(
    assignments: Sequence[tuple[str, object]],
    *,
    temp_prefix: str,
    indent: int,
    substitutions: Mapping[sp.Expr, sp.Symbol] | None = None,
) -> tuple[list[str], bool]:
    if substitutions:
        assignments = [
            (lhs, _apply_source_temp_substitutions(expr, substitutions))
            for lhs, expr in assignments
        ]
    temps, reduced = _source_level_cse_assignments(assignments, temp_prefix=temp_prefix)
    prefix = " " * indent
    lines = [f"{prefix}double {name} = {_ccode(expr)};" for name, expr in temps]
    lines.extend(f"{prefix}{lhs} = {_ccode(expr)};" for lhs, expr in reduced)
    return lines, bool(temps)


def _empty_scalar_cse_stats() -> dict[str, object]:
    return {
        "enabled": True,
        "denominator_temps": 0,
        "cse_temps": 0,
        "temp_count": 0,
        "per_case_temps": 0,
        "common_temps": 0,
        "temp_names": [],
        "groups": [],
    }


def _merge_scalar_cse_stats(target: dict[str, object], update: Mapping[str, object]) -> None:
    for key in ("denominator_temps", "cse_temps", "temp_count", "per_case_temps", "common_temps"):
        target[key] = int(target.get(key, 0) or 0) + int(update.get(key, 0) or 0)
    groups = target.setdefault("groups", [])
    if isinstance(groups, list):
        groups.extend(list(update.get("groups") or []))
    temp_names = target.setdefault("temp_names", [])
    if isinstance(temp_names, list):
        temp_names.extend(str(name) for name in (update.get("temp_names") or []))


def _scalar_denominator_candidates(exprs: Sequence[sp.Expr]) -> list[tuple[sp.Expr, int]]:
    counts: dict[str, int] = {}
    first_seen: dict[str, sp.Expr] = {}
    order: list[str] = []
    for expr in exprs:
        for node in sp.preorder_traversal(sp.sympify(expr)):
            if not isinstance(node, sp.Pow):
                continue
            if sp.sympify(node.exp) != -1:
                continue
            base = sp.sympify(node.base)
            if base.is_number:
                continue
            key = _source_expr_key(base)
            if key not in counts:
                counts[key] = 0
                first_seen[key] = base
                order.append(key)
            counts[key] += 1
    return [(first_seen[key], counts[key]) for key in order]


def _emit_scalar_reuse_assignment_lines(
    assignments: Sequence[tuple[str, object]],
    *,
    temp_prefix: str,
    indent: int,
    substitutions: Mapping[sp.Expr, sp.Symbol] | None = None,
    declare_temps: bool = True,
) -> tuple[list[str], dict[str, object]]:
    """Emit scalar-expanded assignments with conservative same-scope reuse.

    The caller owns lifecycle correctness by passing only one RAM/CODE/T1_T2
    scope at a time.  This helper only performs structural substitutions inside
    that local group.
    """
    parsed = [
        (lhs, expr if isinstance(expr, sp.Expr) else _parse_expr(str(expr)))
        for lhs, expr in assignments
    ]
    stats = _empty_scalar_cse_stats()
    if not parsed:
        return [], stats

    exprs = [sp.sympify(expr) for _lhs, expr in parsed]
    denominator_substitutions: dict[sp.Expr, sp.Symbol] = dict(substitutions or {})
    denominator_temps: list[tuple[str, sp.Expr]] = []
    for base, count in _scalar_denominator_candidates(exprs):
        if count < 2:
            continue
        if sp.Pow(base, -1) in denominator_substitutions:
            continue
        name = f"{temp_prefix}_inv_den_{len(denominator_temps)}"
        symbol = sp.Symbol(name)
        denominator_temps.append((name, 1 / base))
        denominator_substitutions[sp.Pow(base, -1)] = symbol

    rewritten = [
        (lhs, _apply_source_temp_substitutions(expr, denominator_substitutions))
        for lhs, expr in parsed
    ]
    cse_temps, reduced = _source_level_cse_assignments(
        rewritten,
        temp_prefix=f"{temp_prefix}_tmp",
    )

    prefix = " " * indent
    declaration = "double " if declare_temps else ""
    lines = [f"{prefix}{declaration}{name} = {_ccode(expr)};" for name, expr in denominator_temps]
    lines.extend(f"{prefix}{declaration}{name} = {_ccode(expr)};" for name, expr in cse_temps)
    lines.extend(f"{prefix}{lhs} = {_ccode(expr)};" for lhs, expr in reduced)

    stats["denominator_temps"] = len(denominator_temps)
    stats["cse_temps"] = len(cse_temps)
    stats["temp_count"] = len(denominator_temps) + len(cse_temps)
    if "_case" in temp_prefix:
        stats["per_case_temps"] = stats["temp_count"]
    else:
        stats["common_temps"] = stats["temp_count"]
    stats["temp_names"] = [name for name, _expr in denominator_temps] + [name for name, _expr in cse_temps]
    if denominator_temps or cse_temps:
        stats["groups"] = [
            {
                "prefix": temp_prefix,
                "assignments": len(parsed),
                "denominator_temps": len(denominator_temps),
                "cse_temps": len(cse_temps),
            }
        ]
    return lines, stats


def _scalar_temp_declarations(stats: Mapping[str, object]) -> list[str]:
    names = list(dict.fromkeys(str(name) for name in (stats.get("temp_names") or [])))
    return [f"    double {name} = 0.0;" for name in names]


def _shared_scalar_denominator_temps(
    ram_exprs: Sequence[sp.Expr],
    code_exprs: Sequence[sp.Expr],
    recovery_exprs: Sequence[sp.Expr],
    *,
    symbol_table: Mapping[str, str],
    temp_prefix: str,
) -> tuple[list[tuple[str, sp.Expr]], dict[sp.Expr, sp.Symbol], dict[str, object]]:
    ordered_bases: dict[str, sp.Expr] = {}
    runtime_keys: set[str] = set()
    for base, _count in _scalar_denominator_candidates(
        [sp.sympify(expr) for expr in [*ram_exprs, *code_exprs, *recovery_exprs]]
    ):
        ordered_bases.setdefault(_source_expr_key(base), base)
    for base, _count in _scalar_denominator_candidates([sp.sympify(expr) for expr in [*code_exprs, *recovery_exprs]]):
        runtime_keys.add(_source_expr_key(base))
    temps: list[tuple[str, sp.Expr]] = []
    substitutions: dict[sp.Expr, sp.Symbol] = {}
    stats = _empty_scalar_cse_stats()
    for key, base in ordered_bases.items():
        if key not in runtime_keys:
            continue
        if _expr_stage(base, dict(symbol_table)) != "RAM":
            continue
        name = f"{temp_prefix}_shared_inv_den_{len(temps)}"
        temps.append((name, 1 / base))
        substitutions[sp.Pow(base, -1)] = sp.Symbol(name)
    if temps:
        stats["denominator_temps"] = len(temps)
        stats["temp_count"] = len(temps)
        stats["common_temps"] = len(temps)
        stats["temp_names"] = [name for name, _expr in temps]
        stats["groups"] = [
            {
                "prefix": f"{temp_prefix}_shared",
                "assignments": 0,
                "denominator_temps": len(temps),
                "cse_temps": 0,
            }
        ]
    return temps, substitutions, stats


def _resolved_cse_temps(temps: Sequence[tuple[str, sp.Expr]]) -> list[tuple[str, sp.Expr, sp.Expr]]:
    """Return local CSE temps together with their fully expanded-by-temp expression.

    "Expanded" here only means replacing earlier CSE symbols with their original
    CSE expressions.  It deliberately avoids algebraic expansion/simplification.
    """
    resolved_by_symbol: dict[sp.Symbol, sp.Expr] = {}
    out: list[tuple[str, sp.Expr, sp.Expr]] = []
    for name, expr in temps:
        parsed = sp.sympify(expr)
        resolved = parsed.xreplace(resolved_by_symbol)
        symbol = sp.Symbol(str(name))
        resolved_by_symbol[symbol] = resolved
        out.append((str(name), parsed, resolved))
    return out


def _source_expr_key(expr: sp.Expr) -> str:
    return sp.srepr(sp.sympify(expr))


def _apply_source_temp_substitutions(
    expr: object,
    substitutions: Mapping[sp.Expr, sp.Symbol] | None,
) -> sp.Expr:
    parsed = expr if isinstance(expr, sp.Expr) else _parse_expr(str(expr))
    if not substitutions:
        return sp.sympify(parsed)
    # Larger expressions first prevents a small denominator replacement from
    # hiding a larger reusable term such as Gc/(G11 + Gc).
    ordered = sorted(
        substitutions.items(),
        key=lambda item: (int(sp.count_ops(item[0])), len(str(item[0]))),
        reverse=True,
    )
    out = sp.sympify(parsed)
    # Some hoisted temps intentionally build on earlier hoisted temps:
    #   tmp0 = 1 / den
    #   tmp1 = G12 * tmp0
    # A single xreplace pass can only produce ``G12 * tmp0`` from the original
    # expression; it needs one more structural pass to collapse that to tmp1.
    # This remains cheap and deterministic: no algebraic simplify/cancel/factor.
    for _ in range(max(1, min(len(ordered), 8))):
        before = out
        for source_expr, symbol in ordered:
            out = out.xreplace({sp.sympify(source_expr): symbol})
        if out == before:
            break
    return out


def _emit_source_level_ram_stamp_lines(
    ram_items: Sequence[tuple[int, int, sp.Expr]],
    *,
    local_index: dict[int, int],
    temp_prefix: str,
    indent: int,
    alias_assignments: Sequence[tuple[str, sp.Expr]] | None = None,
    substitutions: Mapping[sp.Expr, sp.Symbol] | None = None,
) -> list[str]:
    """Emit RAM g_mat_over writes with the same bounded source-entry CSE as CODE.

    The placeholders are only used to keep reduced expressions in order; they are
    not emitted as C variables.  This keeps the optimization scoped to one RAM
    case block and avoids dense Gred algebra.
    """
    alias_assignments = [
        (lhs, _apply_source_temp_substitutions(expr, substitutions))
        for lhs, expr in (alias_assignments or [])
    ]
    placeholder_assignments = [
        (f"__ram_stamp_{index}", _apply_source_temp_substitutions(expr, substitutions))
        for index, (_row, _col, expr) in enumerate(ram_items)
    ]
    temps, reduced = _source_level_cse_assignments(
        [*alias_assignments, *placeholder_assignments],
        temp_prefix=temp_prefix,
    )
    alias_count = len(alias_assignments)
    reduced_aliases = reduced[:alias_count]
    reduced_stamps = reduced[alias_count:]
    prefix = " " * indent
    lines = [f"{prefix}double {name} = {_ccode(expr)};" for name, expr in temps]
    lines.extend(f"{prefix}{lhs} = {_ccode(expr)};" for lhs, expr in reduced_aliases)
    for (row, col, _expr), (_placeholder, reduced_expr) in zip(ram_items, reduced_stamps):
        local_row = local_index[row]
        local_col = local_index[col]
        value = _ccode(reduced_expr)
        lines.append(f"{prefix}g_mat_over[{local_row}][{local_col}] = {value};")
        if local_row != local_col:
            lines.append(f"{prefix}g_mat_over[{local_col}][{local_row}] = {value};")
    return lines


def _signed_single_symbol(expr: sp.Expr) -> tuple[int, str] | None:
    expr = sp.sympify(expr)
    if isinstance(expr, sp.Symbol):
        return 1, expr.name
    negated = -expr
    if isinstance(negated, sp.Symbol):
        return -1, negated.name
    return None


def _ram_stamp_alias_assignment(
    *,
    template_expr: sp.Expr,
    ram_expr: sp.Expr,
) -> tuple[tuple[str, sp.Expr], sp.Expr] | None:
    """Materialize a simple template alias before RAM stamping.

    If a template entry is ``-multcase_G_*`` and the concrete RAM value is
    ``-x``, assign ``multcase_G_* = x`` and stamp ``-multcase_G_*``.  This is
    source-level aliasing, not base+delta compensation.
    """
    signed = _signed_single_symbol(template_expr)
    if signed is None:
        return None
    sign, alias_name = signed
    if not alias_name.startswith("multcase_G_"):
        return None
    ram_expr = sp.sympify(ram_expr)
    if int(sp.count_ops(ram_expr)) <= 3 and len(str(ram_expr)) <= 32:
        return None
    alias_symbol = sp.Symbol(alias_name)
    alias_value = sp.Integer(sign) * ram_expr
    stamp_expr = sp.Integer(sign) * alias_symbol
    return (alias_name, alias_value), stamp_expr


def _append_ram_alias_assignment(
    assignments: list[tuple[str, sp.Expr]],
    assignment: tuple[str, sp.Expr],
) -> None:
    name, expr = assignment
    for existing_name, existing_expr in assignments:
        if existing_name == name:
            if not _expr_equal_light(existing_expr, expr):
                return
            return
    assignments.append((name, expr))


def _source_cse_scope_name(selector_name: str) -> str:
    scope = _c_identifier_name(selector_name, "case")
    if scope.endswith("_case_id"):
        return scope[: -len("_case_id")]
    return scope


_C99_FOR_LOOP_RE = re.compile(r"for \(int (?P<name>[A-Za-z_]\w*) = (?P<init>[^;]+);")
_CBUILDER_SECTION_RE = re.compile(
    r"(?m)^(?P<label>STATIC|LOCAL_STATIC|RAM(?:_PASS\d*)?|GVALUES|CODE_FUNCTIONS|CODE|BEGIN_T0|T1_T2):\n"
)
_CBUILDER_SECTION_NEEDS_BLANK_RE = re.compile(
    r"(?m)^(?P<label>STATIC|LOCAL_STATIC|RAM(?:_PASS\d*)?|GVALUES|CODE_FUNCTIONS|CODE|BEGIN_T0|T1_T2):\n(?!\n)"
)
_CBUILDER_C89_LOOP_SECTION_LABELS = {"STATIC", "LOCAL_STATIC", "CODE", "BEGIN_T0", "T1_T2"}


def _c89_for_loop_compat(draft: str) -> str:
    """Rewrite generated CBuilder loops away from C99 loop declarations."""
    if not _CBUILDER_SECTION_RE.search(draft):
        return draft

    draft = _C99_FOR_LOOP_RE.sub(lambda match: f"for ({match.group('name')} = {match.group('init')};", draft)
    matches = list(_CBUILDER_SECTION_RE.finditer(draft))
    pieces: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        section_start = match.end()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(draft)
        section = draft[section_start:section_end]
        label = match.group("label")
        if label not in _CBUILDER_C89_LOOP_SECTION_LABELS and not label.startswith("RAM"):
            pieces.append(draft[cursor:section_end])
            cursor = section_end
            continue
        names = list(dict.fromkeys(re.findall(r"\bfor \(([A-Za-z_]\w*) =", section)))
        pieces.append(draft[cursor:section_start])
        if names:
            names_to_promote = [
                name
                for name in names
                if not re.search(rf"(?m)^\s+int\s+{re.escape(name)}\s*;", section)
            ]
            for name in names:
                section = re.sub(rf"(?m)^    int\s+{re.escape(name)}\s*;\n", "", section)
            pieces.extend(f"    int {name};\n" for name in names_to_promote)
        pieces.append(section)
        cursor = section_end
    pieces.append(draft[cursor:])
    return "".join(pieces)


def _drop_unused_section_int_declarations(draft: str) -> str:
    matches = list(_CBUILDER_SECTION_RE.finditer(draft))
    if not matches:
        return draft
    pieces: list[str] = []
    cursor = 0
    declaration_re = re.compile(r"(?m)^    int\s+([A-Za-z_]\w*)\s*;\n")
    for index, match in enumerate(matches):
        section_start = match.end()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(draft)
        section = draft[section_start:section_end]
        pieces.append(draft[cursor:section_start])
        names = list(dict.fromkeys(match.group(1) for match in declaration_re.finditer(section)))
        for name in names:
            line_re = re.compile(rf"(?m)^    int\s+{re.escape(name)}\s*;\n")
            section_without_decl = line_re.sub("", section)
            if not re.search(rf"\b{re.escape(name)}\b", section_without_decl):
                section = section_without_decl
        pieces.append(section)
        cursor = section_end
    pieces.append(draft[cursor:])
    return "".join(pieces)


def _ensure_static_blank_line(draft: str) -> str:
    draft = _c89_for_loop_compat(draft)
    draft = _drop_unused_section_int_declarations(draft)
    draft = re.sub(r"\n    int row;\n    int col;\n(?=(?:/\* WARNING:|$))", "\n", draft)
    draft = re.sub(
        r"(BEGIN_T0:\n\n)    int row;\n\n(?=    /\* Resolve CODE_PER_STEP multi-case effective aliases as full values, never deltas\. \*/)",
        r"\1",
        draft,
    )
    return _CBUILDER_SECTION_NEEDS_BLANK_RE.sub(
        lambda match: f"{match.group('label')}:\n\n",
        draft,
    )


def _use_readable_dimension_names(draft: str) -> str:
    draft = re.sub(
        r"enum \{ NR = (?P<nr>\d+), NK = (?P<nk>\d+), NCASE = (?P<ncase>\d+) \};",
        "enum { RETAINED_NODES = \\g<nr>, INTERNAL_NODES = \\g<nk>, CASE_COUNT = \\g<ncase> };\n"
        "/* Dimension names:\n"
        " * RETAINED_NODES is the number of external nodes kept in the exported network.\n"
        " * INTERNAL_NODES is the number of eliminated internal nodes used by Schur/Vk recovery.\n"
        " * CASE_COUNT is the number of explicit case profiles in this draft.\n"
        " */",
        draft,
        count=1,
    )
    draft = re.sub(
        r"enum \{ NR = (?P<nr>\d+), NK = (?P<nk>\d+) \};",
        "enum { RETAINED_NODES = \\g<nr>, INTERNAL_NODES = \\g<nk> };\n"
        "/* Dimension names:\n"
        " * RETAINED_NODES is the number of external nodes kept in the exported network.\n"
        " * INTERNAL_NODES is the number of eliminated internal nodes used by Schur/Vk recovery.\n"
        " */",
        draft,
        count=1,
    )
    draft = re.sub(r"\bNR\b", "RETAINED_NODES", draft)
    draft = re.sub(r"\bNK\b", "INTERNAL_NODES", draft)
    draft = re.sub(r"\bNCASE\b", "CASE_COUNT", draft)
    return draft


def _strip_unused_dimension_enum_for_direct_gvalue_draft(draft: str) -> str:
    """Remove retained/internal enum text when the draft has no matrix DAG."""
    matrix_markers = (
        "\n    MATRIX_",
        "matrixDim(",
        "matrix_register(",
        "conditionMatrixForCODE(",
        "matrix_mult_CODE(",
        "matrix_subtract_CODE(",
        "matrix_add_CODE(",
        "MATH_matx_invert(",
        "mat_2x2_sym_inv_code(",
        "mat_3x3_sym_inv_code(",
    )
    if any(marker in draft for marker in matrix_markers):
        return draft
    draft = re.sub(
        r"enum \{ RETAINED_NODES = \d+, INTERNAL_NODES = 0 \};\n"
        r"/\* Dimension names:[\s\S]*?\*/\n\n",
        "",
        draft,
        count=1,
    )
    draft = re.sub(r"enum \{ NR = \d+, NK = 0 \};\n\n", "", draft, count=1)
    return draft


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


def _single_case_scalar_codegen_requested(payload: dict, mode: str) -> bool:
    if mode in {"force_scalar", "scalar", "expanded_scalar"}:
        return True
    if mode not in {"auto", ""}:
        return False
    return "elimination_codegen_mode" in payload or "codegen_mode" in payload


def _single_case_scalar_formula_cost(*matrices: sp.Matrix) -> int:
    total = 0
    for matrix in matrices:
        for expr in sp.Matrix(matrix):
            total += int(sp.count_ops(sp.sympify(expr)))
    return total


def _single_case_scalar_codegen_allowed(payload: dict, mode: str, rtds_stage_plan: dict) -> bool:
    if mode in {"force_scalar", "scalar", "expanded_scalar"}:
        return True
    if mode not in {"auto", ""}:
        return False
    if not _single_case_scalar_codegen_requested(payload, mode):
        return False
    nr = len(rtds_stage_plan.get("external_nodes") or [])
    nk = len(rtds_stage_plan.get("internal_nodes") or [])
    if nk == 0:
        return False
    if nk > 1 or nr > 4:
        return False
    cost = _single_case_scalar_formula_cost(
        sp.Matrix(rtds_stage_plan.get("Gred", sp.zeros(nr, nr))),
        sp.Matrix(rtds_stage_plan.get("Ihisred", sp.zeros(nr, 1))),
        sp.Matrix(rtds_stage_plan.get("Kv", sp.zeros(nk, nr))),
        sp.Matrix(rtds_stage_plan.get("Kh", sp.zeros(nk, 1))),
    )
    return cost <= 120


def _build_single_case_scalar_expanded_c_draft(
    payload: dict,
    rtds_stage_plan: dict,
    *,
    node_display_names: dict[str, str] | None = None,
) -> tuple[str, dict[str, object]]:
    node_display_names = {str(key): str(value) for key, value in (node_display_names or {}).items()}
    symbol_table = payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {}
    external_nodes = [str(node) for node in (rtds_stage_plan.get("external_nodes") or [])]
    internal_nodes = [str(node) for node in (rtds_stage_plan.get("internal_nodes") or [])]
    nr = len(external_nodes)
    nk = len(internal_nodes)
    Gred = sp.Matrix(rtds_stage_plan.get("Gred", sp.zeros(nr, nr)))
    Ihisred = sp.Matrix(rtds_stage_plan.get("Ihisred", sp.zeros(nr, 1)))
    if rtds_stage_plan.get("Gred_direct") is not None:
        Gred = Gred + sp.Matrix(rtds_stage_plan.get("Gred_direct", sp.zeros(nr, nr)))
    if rtds_stage_plan.get("Ihisred_direct") is not None:
        Ihisred = Ihisred + sp.Matrix(rtds_stage_plan.get("Ihisred_direct", sp.zeros(nr, 1)))
    Kv = sp.Matrix(rtds_stage_plan.get("Kv", sp.zeros(nk, nr)))
    Kh = sp.Matrix(rtds_stage_plan.get("Kh", sp.zeros(nk, 1)))
    symbols = _symbols_in_matrices(Gred, Ihisred, Kv, Kh)
    declarations = [f"    double {name} = 0.0;" for name in symbols]
    scalar_cse_stats = _empty_scalar_cse_stats()

    ram_entries: list[tuple[int, int, sp.Expr]] = []
    dynamic_entries: list[tuple[int, int, sp.Expr, str, str, str]] = []
    for row in range(Gred.rows):
        for col in range(Gred.cols):
            expr = sp.sympify(Gred[row, col])
            if expr == 0:
                continue
            if _expr_stage(expr, symbol_table) == "RAM":
                ram_entries.append((row, col, expr))
    for row in range(Gred.rows):
        for col in range(row, Gred.cols):
            expr = sp.sympify(Gred[row, col])
            if expr == 0 or _expr_stage(expr, symbol_table) == "RAM":
                continue
            left_label = node_display_names.get(external_nodes[row], external_nodes[row])
            right_label = node_display_names.get(external_nodes[col], external_nodes[col])
            left = _c_identifier_name(left_label, f"N{row + 1}")
            right = _c_identifier_name(right_label, f"N{col + 1}")
            dynamic_entries.append((row, col, expr, f"varG_{left}_{right}", external_nodes[row], external_nodes[col]))

    recovery_assignments = _recovery_assignment_pairs(
        {
            "recovery_nodes": internal_nodes,
            "super_nodes": external_nodes,
            "K_v": Kv,
            "K_h": Kh,
            "super_node_c_names": {node: node_display_names.get(node, node) for node in external_nodes},
            "recovery_node_c_names": {node: node_display_names.get(node, node) for node in internal_nodes},
        }
    )
    code_assignments: list[tuple[str, sp.Expr]] = [
        (var, sp.sympify(expr))
        for _row, _col, expr, var, _left, _right in dynamic_entries
    ]
    for row, node in enumerate(external_nodes):
        display = node_display_names.get(node, node)
        code_assignments.append((f"Inj{_c_identifier_name(display, f'N{row + 1}')}", sp.sympify(Ihisred[row, 0])))
    shared_temps, shared_substitutions, shared_stats = _shared_scalar_denominator_temps(
        [expr for _row, _col, expr in ram_entries],
        [expr for _lhs, expr in code_assignments],
        [expr for _lhs, expr in recovery_assignments],
        symbol_table=symbol_table,
        temp_prefix="scalar",
    )
    _merge_scalar_cse_stats(scalar_cse_stats, shared_stats)
    ram_lines: list[str] = []
    ram_stats = _empty_scalar_cse_stats()
    if ram_entries:
        ram_lines, ram_stats = _emit_scalar_reuse_assignment_lines(
            [(f"g_mat_over[{row}][{col}]", expr) for row, col, expr in ram_entries],
            temp_prefix="scalar_ram",
            indent=4,
            substitutions=shared_substitutions,
            declare_temps=False,
        )
        _merge_scalar_cse_stats(scalar_cse_stats, ram_stats)
    code_lines, code_stats = _emit_scalar_reuse_assignment_lines(
        code_assignments,
        temp_prefix="scalar_code",
        indent=4,
        substitutions=shared_substitutions,
        declare_temps=False,
    )
    _merge_scalar_cse_stats(scalar_cse_stats, code_stats)
    recovery_lines: list[str] = []
    recovery_stats = _empty_scalar_cse_stats()
    if recovery_assignments:
        recovery_lines, recovery_stats = _emit_scalar_reuse_assignment_lines(
            recovery_assignments,
            temp_prefix="scalar_t1t2",
            indent=4,
            substitutions=shared_substitutions,
            declare_temps=False,
        )
        _merge_scalar_cse_stats(scalar_cse_stats, recovery_stats)

    def symbols_in_exprs(exprs: Sequence[sp.Expr]) -> set[str]:
        out: set[str] = set()
        for expr in exprs:
            out.update(str(symbol) for symbol in sp.sympify(expr).free_symbols)
        return out

    recovery_runtime_exprs = [
        _apply_source_temp_substitutions(expr, shared_substitutions)
        for _lhs, expr in recovery_assignments
    ]
    code_runtime_exprs = [
        _apply_source_temp_substitutions(expr, shared_substitutions)
        for _lhs, expr in code_assignments
    ]
    static_user_symbols = symbols_in_exprs(code_runtime_exprs + recovery_runtime_exprs)
    static_declarations = [
        *[f"    double {name} = 0.0;" for name in symbols if name in static_user_symbols],
        *[f"    double {name} = 0.0;" for name, _expr in shared_temps],
        *_scalar_temp_declarations(code_stats),
        *_scalar_temp_declarations(recovery_stats),
    ]
    local_static_declarations = [
        *[f"    double {name} = 0.0;" for name in symbols if name not in static_user_symbols],
        *_scalar_temp_declarations(ram_stats),
    ]

    lines = [
        "#include <builtin_MATH.h>",
        "/* RTDS-style C draft using scalar-expanded Schur elimination.",
        "   No runtime matrix objects are required on this codegen path. */",
        f"enum {{ RETAINED_NODES = {nr}, INTERNAL_NODES = {nk} }};",
        "",
        "STATIC:",
        *dict.fromkeys(static_declarations),
        "",
        "LOCAL_STATIC:",
        *(list(dict.fromkeys(local_static_declarations)) or ["    /* No user symbols are required by the scalar-expanded draft. */"]),
        "",
        "RAM_PASS1:",
    ]
    if ram_entries:
        lines.extend([
            "    /* Scalar-expanded RAM-side G stamp. */",
            "    int row;",
            "    int col;",
            *[
                f"    g_mat_nods[{index}] = getNodeNum(comp, \"{node_display_names.get(node, node)}\");"
                for index, node in enumerate(external_nodes)
            ],
            f"    for (row = 0; row < {nr}; row++) {{",
            f"        for (col = 0; col < {nr}; col++) {{",
            "            g_mat_over[row][col] = 0.0;",
            "        }",
            "    }",
        ])
        for name, expr in shared_temps:
            lines.append(f"    {name} = {_ccode(expr)};")
        lines.extend(ram_lines)
        lines.append(f"    setupGMatrix({nr});")
    else:
        lines.append("    /* No RAM-side G entries: dynamic GVALUES own the reduced stamp. */")
    lines.append("")
    if dynamic_entries:
        lines.extend([
            "GVALUES:",
            "    /* Dynamic reduced-G stamp handles from scalar-expanded Schur formulas. */",
        ])
        for _row, _col, _expr, var, left, right in dynamic_entries:
            left_name = node_display_names.get(str(left), str(left))
            right_name = node_display_names.get(str(right), str(right))
            lines.append(f"    double {var} = createGValue(\"{var}\", \"{left_name}\", \"{right_name}\", 0, \"TRUE\");")
        lines.append("")
    lines.extend([
        "CODE:",
        "BEGIN_T0:",
    ])
    lines.append("    /* CODE-side scalar assignments from scalar-expanded Schur formulas. */")
    lines.extend(code_lines)
    lines.extend([
        "",
        "T1_T2:",
    ])
    if recovery_lines:
        lines.extend([
            "    /* Scalar-expanded internal-node voltage recovery after retained voltages are available. */",
            *recovery_lines,
        ])
    else:
        lines.append("    /* No internal nodes were eliminated, so there is no Vk recovery step. */")
    return _ensure_static_blank_line("\n".join(lines)), scalar_cse_stats


def build_optimized_response(payload: dict) -> dict:
    simplify_level = payload.get("simplify_level") or "light"
    display_mode = payload.get("display_mode") or "compact"
    codegen_mode = str(payload.get("elimination_codegen_mode") or payload.get("codegen_mode") or "auto")
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
    if payload.get("prefer_ram_gred_matrix_precompute"):
        rtds_stage_plan["prefer_ram_gred_matrix_precompute"] = True
    if direct_stamps:
        rtds_stage_plan["Gred_direct"] = direct_Grr
        rtds_stage_plan["Ihisred_direct"] = direct_Ihisr
        rtds_stage_plan["add_ram_direct_to_ram_owned_gred"] = not bool(borrowed_dependency)
        if direct_Grr_tagged is not None and direct_Ihisr_tagged is not None:
            rtds_stage_plan["Gred_direct_tagged"] = direct_Grr_tagged
            rtds_stage_plan["Ihisred_direct_tagged"] = direct_Ihisr_tagged
    warnings.extend(structured.get("warnings", []))
    blocks = structured["blocks"]
    if borrowed_dependency and _single_case_scalar_codegen_requested(payload, codegen_mode):
        recovery_model = structured_dependency_model(structured, simplify_level)
        rtds_stage_plan["Kv"] = recovery_model["Kv"]
        rtds_stage_plan["Kh"] = recovery_model["Kh"]

    scalar_codegen_active = (
        _single_case_scalar_codegen_requested(payload, codegen_mode)
        and _single_case_scalar_codegen_allowed(payload, codegen_mode, rtds_stage_plan)
    )
    scalar_cse_stats = _empty_scalar_cse_stats()
    scalar_cse_stats["enabled"] = False
    if scalar_codegen_active:
        c_draft, scalar_cse_stats = _build_single_case_scalar_expanded_c_draft(
            payload,
            rtds_stage_plan,
            node_display_names=payload.get("node_display_names") or {},
        )
        structured_codegen_mode = (
            "force scalar Schur expansion"
            if codegen_mode in {"force_scalar", "scalar", "expanded_scalar"}
            else "auto scalar Schur expansion"
        )
    else:
        c_draft = c_draft_for_structured_formula(
            structured,
            node_display_names=payload.get("node_display_names") or {},
            rtds_stage_plan=rtds_stage_plan,
        )
        structured_codegen_mode = "matrix Schur path"
    if single_dummy_nodes:
        c_draft = _apply_single_case_dummy_recovery_skip(
            c_draft,
            structured["effective_internal_nodes"],
            single_dummy_nodes,
        )
    if single_dummy_blocks and "DummyNodeBlock isolated internal nodes are not recovered" not in c_draft:
        c_draft = c_draft.replace(
            "T1_T2:\n",
            "T1_T2:\n\n    /* DummyNodeBlock isolated internal nodes are not recovered. */\n",
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
            "codegen_mode": structured_codegen_mode,
            "scalar_cse": scalar_cse_stats,
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
    return _use_readable_dimension_names(_ensure_static_blank_line("\n".join(lines)))


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
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return sp.zeros(0, 0)
        if cleaned.startswith("["):
            parsed = sp.sympify(cleaned, locals=_symbol_locals(cleaned))
            if isinstance(parsed, sp.MatrixBase):
                return sp.Matrix(parsed)
            if isinstance(parsed, (list, tuple)):
                if not parsed:
                    return sp.zeros(0, 0)
                if not isinstance(parsed[0], (list, tuple, sp.MatrixBase)):
                    return sp.Matrix([[_parse_expr(str(item))] for item in parsed])
                return sp.Matrix([[_parse_expr(str(item)) for item in row] for row in parsed])
        return sp.Matrix([[_parse_expr(cleaned)]])
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


def _upper_tri_matrix_subtract_lines(
    dst: str,
    lhs: str,
    rhs: str,
    dim_expr: str,
    *,
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}for (int row = 0; row < {dim_expr}; row++) {{",
        f"{indent}    for (int col = row; col < {dim_expr}; col++) {{",
        f"{indent}        set_CODE(&{dst}, row, col, get_CODE(&{lhs}, row, col) - get_CODE(&{rhs}, row, col));",
        f"{indent}    }}",
        f"{indent}}}",
    ]


def _upper_tri_matrix_add_lines(
    dst: str,
    lhs: str,
    rhs: str,
    dim_expr: str,
    *,
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}for (int row = 0; row < {dim_expr}; row++) {{",
        f"{indent}    for (int col = row; col < {dim_expr}; col++) {{",
        f"{indent}        set_CODE(&{dst}, row, col, get_CODE(&{lhs}, row, col) + get_CODE(&{rhs}, row, col));",
        f"{indent}    }}",
        f"{indent}}}",
    ]


def _upper_tri_matrix_product_lines(
    dst: str,
    lhs: str,
    rhs: str,
    dim_expr: str,
    inner_expr: str = "INTERNAL_NODES",
    *,
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}/* Symmetric product: only upper triangle of {dst} is needed downstream. */",
        f"{indent}for (int row = 0; row < {dim_expr}; row++) {{",
        f"{indent}    for (int col = row; col < {dim_expr}; col++) {{",
        f"{indent}        double acc = 0.0;",
        f"{indent}        for (int k = 0; k < {inner_expr}; k++) {{",
        f"{indent}            acc += get_CODE(&{lhs}, row, k) * get_CODE(&{rhs}, k, col);",
        f"{indent}        }}",
        f"{indent}        set_CODE(&{dst}, row, col, acc);",
        f"{indent}    }}",
        f"{indent}}}",
    ]


def _upper_tri_matrix_product_transpose_rhs_lines(
    dst: str,
    lhs: str,
    rhs: str,
    dim_expr: str,
    inner_expr: str = "INTERNAL_NODES",
    *,
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}/* Symmetry reuse: multiply by transpose({rhs}) without materializing Gkr. */",
        f"{indent}for (int row = 0; row < {dim_expr}; row++) {{",
        f"{indent}    for (int col = row; col < {dim_expr}; col++) {{",
        f"{indent}        double acc = 0.0;",
        f"{indent}        for (int k = 0; k < {inner_expr}; k++) {{",
        f"{indent}            acc += get_CODE(&{lhs}, row, k) * get_CODE(&{rhs}, col, k);",
        f"{indent}        }}",
        f"{indent}        set_CODE(&{dst}, row, col, acc);",
        f"{indent}    }}",
        f"{indent}}}",
    ]


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
    backend_placeholder_nodes = {
        str(node)
        for node in (payload.get("backend_placeholder_internal_nodes") or [])
    }
    if not common_dummy_internal_nodes and not backend_placeholder_nodes:
        return matrix
    blocks = dummy_node_blocks_from_payload(payload)
    if not blocks and not backend_placeholder_nodes:
        return matrix
    node_index = {str(node): index for index, node in enumerate(payload.get("all_nodes") or [])}
    dummy_nodes = {
        str(node)
        for block in blocks
        for node in block.dummy_nodes
        if str(node) in common_dummy_internal_nodes
    }
    dummy_nodes.update(node for node in backend_placeholder_nodes if node in node_index)
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


def _multi_case_symbol_table(profiles: Sequence[Mapping]) -> dict[str, str]:
    symbol_table: dict[str, str] = {}
    for profile in profiles or []:
        payload = profile.get("payload") or {}
        symbol_table.update(payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {})
    return symbol_table


def _promote_owner(owners: Iterable[str]) -> str:
    order = {"RAM": 0, "CODE": 1, "CODE_PER_STEP": 2}
    return max((owner for owner in owners), key=lambda owner: order.get(owner, 0), default="RAM")


def _owner_rank(owner: str) -> int:
    return {"RAM": 0, "CODE": 1, "CODE_PER_STEP": 2}.get(str(owner or "RAM"), 0)


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


def _final_group_display_name(group: object, fallback: str) -> str:
    if isinstance(group, dict):
        for key in ("display", "name", "globalNet", "node", "id"):
            value = group.get(key)
            if value not in (None, ""):
                return str(value)
    if group not in (None, ""):
        return str(group)
    return fallback


def _payload_node_display_names(payload: Mapping) -> dict[str, str]:
    display_names = {
        str(key): str(value)
        for key, value in (payload.get("node_display_names") or {}).items()
    }

    def merge_ordered_groups(nodes_key: str, groups_key: str, fallback_prefix: str) -> None:
        nodes = [str(node) for node in (payload.get(nodes_key) or [])]
        groups = payload.get(groups_key) or []
        for index, group in enumerate(groups):
            if index >= len(nodes):
                break
            node = nodes[index]
            display_names[node] = _final_group_display_name(group, f"{fallback_prefix}{index + 1}")

    merge_ordered_groups("external_nodes", "externalGroups", "N")
    merge_ordered_groups("internal_nodes", "internalGroups", "K")
    merge_ordered_groups("external_nodes", "finalExternalGroups", "N")
    merge_ordered_groups("internal_nodes", "finalInternalGroups", "K")
    return display_names


def _final_retained_profile_adapter(profiles: list[dict]) -> tuple[list[dict], list[dict]] | None:
    """Adapt pack cases with equal final ports but different internal recovery.

    Raw Pack internals are allowed to differ only after each case has already
    produced a same-shaped final retained equation.  The alias-template path can
    then operate on final G/Ihis while T1_T2 keeps the case-specific recovery.
    """
    if len(profiles) < 2:
        return None
    normalized_profiles: list[dict] = []
    recovery_profiles: list[dict] = []
    base_external_nodes: list[str] | None = None
    base_g_shape: tuple[int, int] | None = None
    base_ihis_shape: tuple[int, int] | None = None

    for profile_index, profile in enumerate(profiles):
        payload = profile.get("payload") or {}
        final_external_groups = payload.get("finalExternalGroups") or []
        final_g_value = payload.get("finalGMatrix")
        final_ihis_value = payload.get("finalIhisVector")
        if not final_external_groups or final_g_value is None or final_ihis_value is None:
            return None
        external_nodes = [
            _final_group_display_name(group, f"N{index + 1}")
            for index, group in enumerate(final_external_groups)
        ]
        if base_external_nodes is None:
            base_external_nodes = external_nodes
        elif external_nodes != base_external_nodes:
            return None

        final_g = _matrix_from_clean(final_g_value)
        final_ihis = _matrix_from_clean(final_ihis_value)
        if final_g.rows != len(external_nodes) or final_g.cols != len(external_nodes):
            return None
        if final_ihis.rows != len(external_nodes) or final_ihis.cols != 1:
            return None
        g_shape = (final_g.rows, final_g.cols)
        ihis_shape = (final_ihis.rows, final_ihis.cols)
        if base_g_shape is None:
            base_g_shape = g_shape
            base_ihis_shape = ihis_shape
        elif g_shape != base_g_shape or ihis_shape != base_ihis_shape:
            return None

        internal_groups = payload.get("finalInternalGroups") or []
        recovery_nodes = [
            _final_group_display_name(group, f"K{index + 1}")
            for index, group in enumerate(internal_groups)
        ]
        k_v = _matrix_from_clean(payload.get("finalK_v") or [])
        k_h = _matrix_from_clean(payload.get("finalK_h") or [])
        if recovery_nodes:
            if k_v.shape != (len(recovery_nodes), len(external_nodes)):
                return None
            if k_h.shape not in {(len(recovery_nodes), 1), (0, 0)}:
                return None
            if k_h.shape == (0, 0):
                k_h = sp.zeros(len(recovery_nodes), 1)
        elif k_v.shape not in {(0, 0), (0, len(external_nodes))}:
            return None

        next_profile = json.loads(json.dumps(profile))
        next_payload = json.loads(json.dumps(payload))
        final_g_clean = _clean_matrix(final_g)
        final_ihis_clean = _clean_vector(final_ihis)
        next_payload.update({
            "all_nodes": external_nodes,
            "external_nodes": external_nodes,
            "internal_nodes": [],
            "ground_nodes": [],
            "node_display_names": {node: node for node in external_nodes},
            "G_full": final_g_clean,
            "G_full_tagged": final_g_clean,
            "Ihis_full": final_ihis_clean,
            "Ihis_full_tagged": final_ihis_clean,
            "direct_retained_stamps": [],
        })
        next_profile["payload"] = next_payload
        normalized_profiles.append(next_profile)
        try:
            profile_case_id = int(profile.get("_init_profile_index", profile.get("case_id", profile_index)))
        except (TypeError, ValueError):
            profile_case_id = profile_index
        recovery_profiles.append({
            "case_ids": [profile_case_id],
            "recovery_nodes": recovery_nodes,
            "super_nodes": external_nodes,
            "K_v": _clean_matrix(k_v) if k_v.shape != (0, 0) else [],
            "K_h": _clean_vector(k_h) if k_h.shape != (0, 0) else [],
        })

    return normalized_profiles, recovery_profiles


def _gkk_placeholder_profile_adapter(profiles: list[dict]) -> tuple[list[dict], list[dict]] | None:
    """Align Pack cases with equal external ports but different raw internals.

    Missing internal nodes are backend-only placeholders: they are added as an
    isolated identity row/column so Gkk remains invertible and has no Schur
    effect.  Case-specific recovery is computed from the original payload before
    placeholders are inserted.
    """
    if len(profiles) < 2:
        return None

    base_payload = profiles[0].get("payload") or {}
    base_external_nodes = [str(node) for node in (base_payload.get("external_nodes") or [])]
    base_ground_nodes = [str(node) for node in (base_payload.get("ground_nodes") or [])]
    if not base_external_nodes:
        return None

    internal_union: list[str] = []
    internal_display_names: dict[str, str] = {}
    for profile in profiles:
        payload = profile.get("payload") or {}
        if [str(node) for node in (payload.get("external_nodes") or [])] != base_external_nodes:
            return None
        if [str(node) for node in (payload.get("ground_nodes") or [])] != base_ground_nodes:
            return None
        all_nodes = [str(node) for node in (payload.get("all_nodes") or [])]
        internal_nodes = [str(node) for node in (payload.get("internal_nodes") or [])]
        if not all_nodes:
            return None
        if any(node not in all_nodes for node in base_external_nodes):
            return None
        if any(node not in all_nodes for node in internal_nodes):
            return None
        if any(node in base_external_nodes for node in internal_nodes):
            return None
        display_names = _payload_node_display_names(payload)
        for node in internal_nodes:
            if node not in internal_union:
                internal_union.append(node)
            internal_display_names.setdefault(node, str(display_names.get(node, node)))

    if not internal_union:
        return None

    common_nodes = [*base_external_nodes, *internal_union]
    normalized_profiles: list[dict] = []
    recovery_profiles: list[dict] = []

    for profile_index, profile in enumerate(profiles):
        payload = profile.get("payload") or {}
        all_nodes = [str(node) for node in (payload.get("all_nodes") or [])]
        internal_nodes = [str(node) for node in (payload.get("internal_nodes") or [])]
        node_index = {node: index for index, node in enumerate(all_nodes)}
        missing_internal_nodes = [node for node in internal_union if node not in internal_nodes]

        try:
            G_full = _expr_matrix_from_payload(payload, "G_full")
            Ihis_full = _expr_matrix_from_payload(payload, "Ihis_full")
            reduced = _reduced_super_result_from_payload(payload)
        except Exception:
            return None
        if G_full.shape != (len(all_nodes), len(all_nodes)):
            return None
        if Ihis_full.shape != (len(all_nodes), 1):
            return None

        aligned_index = {node: index for index, node in enumerate(common_nodes)}
        aligned_G = sp.zeros(len(common_nodes), len(common_nodes))
        aligned_Ihis = sp.zeros(len(common_nodes), 1)
        for row_node in all_nodes:
            if row_node not in aligned_index:
                continue
            src_row = node_index[row_node]
            dst_row = aligned_index[row_node]
            aligned_Ihis[dst_row, 0] = Ihis_full[src_row, 0]
            for col_node in all_nodes:
                if col_node not in aligned_index:
                    continue
                aligned_G[dst_row, aligned_index[col_node]] = G_full[src_row, node_index[col_node]]

        for node in missing_internal_nodes:
            idx = aligned_index[node]
            for pos in range(len(common_nodes)):
                aligned_G[idx, pos] = 0
                aligned_G[pos, idx] = 0
            aligned_G[idx, idx] = 1
            aligned_Ihis[idx, 0] = 0

        next_profile = json.loads(json.dumps(profile))
        next_payload = json.loads(json.dumps(payload))
        display_names = _payload_node_display_names(payload)
        for node in common_nodes:
            display_names.setdefault(node, internal_display_names.get(node, node))
        aligned_G_clean = _clean_matrix(aligned_G)
        aligned_Ihis_clean = _clean_vector(aligned_Ihis)
        next_payload.update({
            "all_nodes": common_nodes,
            "external_nodes": base_external_nodes,
            "internal_nodes": internal_union,
            "ground_nodes": base_ground_nodes,
            "node_display_names": display_names,
            "G_full": aligned_G_clean,
            "G_full_tagged": aligned_G_clean,
            "Ihis_full": aligned_Ihis_clean,
            "Ihis_full_tagged": aligned_Ihis_clean,
            "backend_placeholder_internal_nodes": missing_internal_nodes,
        })
        next_profile["payload"] = next_payload
        normalized_profiles.append(next_profile)

        try:
            profile_case_id = int(profile.get("_init_profile_index", profile.get("case_id", profile_index)))
        except (TypeError, ValueError):
            profile_case_id = profile_index
        recovery_nodes = [str(node) for node in (reduced.get("internal_nodes") or [])]
        display_names = next_payload.get("node_display_names") or {}
        reduced_external_nodes = [str(node) for node in (reduced.get("external_nodes") or base_external_nodes)]
        K_v = sp.Matrix(reduced.get("K_v") or sp.zeros(0, len(base_external_nodes)))
        K_h = sp.Matrix(reduced.get("K_h") or sp.zeros(0, 1))
        recovery_profiles.append({
            "case_ids": [profile_case_id],
            "recovery_nodes": recovery_nodes,
            "super_nodes": reduced_external_nodes,
            "super_node_c_names": {
                node: str(display_names.get(node, node))
                for node in reduced_external_nodes
            },
            "recovery_node_c_names": {
                node: str(display_names.get(node, node))
                for node in recovery_nodes
            },
            "K_v": _clean_matrix(K_v) if K_v.shape != (0, 0) else [],
            "K_h": _clean_vector(K_h) if K_h.shape != (0, 0) else [],
        })

    try:
        _validate_multicase_topology(normalized_profiles)
    except ValueError:
        return None
    return normalized_profiles, recovery_profiles


def _final_retained_profile_adapter_unavailable_reason(profiles: list[dict]) -> str:
    if len(profiles) < 2:
        return "need at least two case profiles"

    base_external_nodes: list[str] | None = None
    base_g_shape: tuple[int, int] | None = None
    base_ihis_shape: tuple[int, int] | None = None
    for profile_index, profile in enumerate(profiles):
        payload = profile.get("payload") or {}
        final_external_groups = payload.get("finalExternalGroups") or []
        final_g_value = payload.get("finalGMatrix")
        final_ihis_value = payload.get("finalIhisVector")
        missing = []
        if not final_external_groups:
            missing.append("finalExternalGroups")
        if final_g_value is None:
            missing.append("finalGMatrix")
        if final_ihis_value is None:
            missing.append("finalIhisVector")
        if missing:
            return f"profile {profile_index} missing {', '.join(missing)}"

        external_nodes = [
            _final_group_display_name(group, f"N{index + 1}")
            for index, group in enumerate(final_external_groups)
        ]
        if base_external_nodes is None:
            base_external_nodes = external_nodes
        elif external_nodes != base_external_nodes:
            return (
                f"profile {profile_index} final external port order differs: "
                f"{external_nodes} != {base_external_nodes}"
            )

        try:
            final_g = _matrix_from_clean(final_g_value)
            final_ihis = _matrix_from_clean(final_ihis_value)
        except Exception as exc:  # pragma: no cover - defensive diagnostics
            return f"profile {profile_index} final retained matrix parse failed: {exc}"

        if final_g.rows != len(external_nodes) or final_g.cols != len(external_nodes):
            return (
                f"profile {profile_index} finalGMatrix shape {final_g.shape} "
                f"does not match {len(external_nodes)} final ports"
            )
        if final_ihis.rows != len(external_nodes) or final_ihis.cols != 1:
            return (
                f"profile {profile_index} finalIhisVector shape {final_ihis.shape} "
                f"does not match {len(external_nodes)} final ports"
            )

        g_shape = (final_g.rows, final_g.cols)
        ihis_shape = (final_ihis.rows, final_ihis.cols)
        if base_g_shape is None:
            base_g_shape = g_shape
            base_ihis_shape = ihis_shape
        elif g_shape != base_g_shape or ihis_shape != base_ihis_shape:
            return (
                f"profile {profile_index} final retained shape differs: "
                f"G {g_shape} / Ihis {ihis_shape} != G {base_g_shape} / Ihis {base_ihis_shape}"
            )

        internal_groups = payload.get("finalInternalGroups") or []
        recovery_nodes = [
            _final_group_display_name(group, f"K{index + 1}")
            for index, group in enumerate(internal_groups)
        ]
        try:
            k_v = _matrix_from_clean(payload.get("finalK_v") or [])
            k_h = _matrix_from_clean(payload.get("finalK_h") or [])
        except Exception as exc:  # pragma: no cover - defensive diagnostics
            return f"profile {profile_index} recovery matrix parse failed: {exc}"
        if recovery_nodes:
            if k_v.shape != (len(recovery_nodes), len(external_nodes)):
                return (
                    f"profile {profile_index} finalK_v shape {k_v.shape} "
                    f"does not match {len(recovery_nodes)} recovery nodes x {len(external_nodes)} final ports"
                )
            if k_h.shape not in {(len(recovery_nodes), 1), (0, 0)}:
                return (
                    f"profile {profile_index} finalK_h shape {k_h.shape} "
                    f"does not match {len(recovery_nodes)} recovery nodes"
                )
        elif k_v.shape not in {(0, 0), (0, len(external_nodes))}:
            return f"profile {profile_index} has finalK_v rows but no finalInternalGroups"

    return "unknown adapter incompatibility"


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


def _init_profiles_from_sample_profiles(sample_profiles: Sequence[dict]) -> list[dict]:
    """Return one codegen profile per init-time case.

    Runtime-mutable samples are added only so alias analysis can see the
    runtime case values. They must not become external case_id entries.
    """
    profiles_by_init: dict[int, dict] = {}
    for sample_index, profile in enumerate(sample_profiles):
        try:
            init_index = int(profile.get("_init_profile_index", profile.get("case_id", sample_index)))
        except (TypeError, ValueError):
            init_index = sample_index
        if init_index in profiles_by_init and profile.get("_runtime_branch_id"):
            continue
        next_profile = json.loads(json.dumps(profile))
        next_profile.pop("_runtime_branch_id", None)
        next_profile["_init_profile_index"] = init_index
        next_profile["case_id"] = int(next_profile.get("case_id", init_index) or init_index)
        name = str(next_profile.get("name") or "")
        suffix = " / runtime base"
        if name.endswith(suffix):
            next_profile["name"] = name[: -len(suffix)]
        profiles_by_init[init_index] = next_profile
    return [profiles_by_init[index] for index in sorted(profiles_by_init)]


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


def _alias_node_name(node_order: Sequence[str], index: int) -> str:
    if 0 <= index < len(node_order):
        return _c_identifier_name(str(node_order[index]), f"N{index}")
    return f"N{index}"


def _alias_name(branch_id: str, kind: str, index: int, key: str, row: int, col: int, node_order: Sequence[str]) -> str:
    branch = _c_identifier_name(branch_id, "branch")
    if kind == "G":
        node_a = _alias_node_name(node_order, row)
        node_b = _alias_node_name(node_order, col)
        base = f"multcase_G_{branch}_{node_a}_{node_b}"
    else:
        node = _alias_node_name(node_order, row)
        base = f"multcase_Ihis_{branch}_{node}"
    return base if index == 1 else f"{base}_{index}"


def _matrix_entry_alias_name(key: str, row: int, col: int, node_order: Sequence[str] = ()) -> str:
    if key == "G_full":
        node_a = _alias_node_name(node_order, row)
        node_b = _alias_node_name(node_order, col)
        return f"multcase_G_combined_{node_a}_{node_b}"
    node = _alias_node_name(node_order, row)
    return f"multcase_Ihis_combined_{node}"


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
    node_order: Sequence[str],
    values: Sequence[sp.Expr],
) -> None:
    alias = _matrix_entry_alias_name(key, row, col, node_order)
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
    node_order: Sequence[str],
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
    alias = _matrix_entry_alias_name(key, row, col, node_order)
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
    key: str,
    row: int,
    col: int,
    node_order: Sequence[str],
    profile_values: Sequence[sp.Expr],
    base_case: int,
) -> tuple[int, str]:
    sign, sequence_key = _signed_sequence_key(profile_values)
    group_key = (branch_id, kind, sequence_key)
    if group_key not in grouped:
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
        alias_base = _alias_name(branch_id, kind, 1, key, row, col, node_order)
        alias = alias_base
        suffix = 2
        while alias in aliases:
            alias = f"{alias_base}_{suffix}"
            suffix += 1
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


def _common_additive_terms(values: Sequence[sp.Expr]) -> sp.Expr:
    expanded_values = [sp.expand(sp.sympify(value)) for value in values]
    if not expanded_values:
        return sp.Integer(0)
    common_counts: dict[str, tuple[sp.Expr, int]] = {}
    for term in sp.Add.make_args(expanded_values[0]):
        key = sp.srepr(term)
        expr, count = common_counts.get(key, (sp.sympify(term), 0))
        common_counts[key] = (expr, count + 1)
    for value in expanded_values[1:]:
        counts: dict[str, int] = {}
        for term in sp.Add.make_args(value):
            key = sp.srepr(term)
            counts[key] = counts.get(key, 0) + 1
        next_common: dict[str, tuple[sp.Expr, int]] = {}
        for key, (expr, count) in common_counts.items():
            if key in counts:
                next_common[key] = (expr, min(count, counts[key]))
        common_counts = next_common
        if not common_counts:
            break
    result = sp.Integer(0)
    for expr, count in common_counts.values():
        result += expr * count
    return sp.expand(result)


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
    node_order: Sequence[str],
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
    residual_by_init: dict[int, sp.Expr] = {}
    case_values: dict[int, sp.Expr] = {}
    for init_index, base_value in base_by_init.items():
        values_by_case: dict[int, sp.Expr] = {}
        for case_index in local_cases:
            case_value = case_by_init.get(init_index, {}).get(case_index)
            if case_value is None:
                if case_index == base_case:
                    case_value = base_value
                else:
                    return None
            values_by_case[case_index] = sp.sympify(case_value)
        residual = _common_additive_terms([values_by_case[index] for index in local_cases])
        residual_by_init[init_index] = residual
        for case_index, case_value in values_by_case.items():
            alias_value = sp.expand(case_value - residual)
            if case_index in case_values and not _expr_equal_light(case_values[case_index], alias_value):
                return None
            case_values[case_index] = alias_value
    if all(_expr_equal_light(case_values[case_index], case_values[local_cases[0]]) for case_index in local_cases[1:]):
        return None
    for profile, value in zip(sample_profiles, values):
        init_index = int(profile.get("_init_profile_index", 0) or 0)
        local_case = _profile_case_index(profile, branch_id, base_case)
        expected = sp.expand(residual_by_init.get(init_index, sp.Integer(0)) + case_values.get(local_case, sp.Integer(0)))
        if not _expr_equal_light(expected, value):
            return None

    profile_values = [
        case_values.get(_profile_case_index(profile, branch_id, base_case), sp.Integer(0))
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
        key=key,
        row=row,
        col=col,
        node_order=node_order,
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
        node_order=node_order,
        values_by_init=residual_by_init,
    )
    return sp.expand(residual_expr + sign * sp.Symbol(alias))


def _stamp_branch_id(stamp: Mapping, branch_ids: Sequence[str], fallback_index: int) -> str:
    branch_set = {str(branch_id) for branch_id in branch_ids}
    for key in ("id", "branch_id", "branchId", "source_id", "sourceId", "name"):
        value = stamp.get(key)
        if value is not None and str(value) in branch_set:
            return str(value)
    fallback = f"__stamp_{fallback_index}"
    return fallback if fallback in branch_set else ""


def _direct_retained_local_entry_replacements(
    *,
    template_payload: dict,
    sample_profiles: list[dict],
    branch_ids: Sequence[str],
    aliases: dict[str, dict],
    grouped: dict[tuple[str, str, tuple[str, ...]], dict],
    symbol_table: dict,
    runtime_groups: Mapping[str, dict],
    global_alias_cache: dict[tuple, str],
    base_case_by_branch: Mapping[str, int],
    alias_node_order: Sequence[str] | None = None,
) -> dict[tuple[int, str, int], sp.Expr]:
    alias_node_order = [str(node) for node in (alias_node_order or template_payload.get("all_nodes") or [])]
    node_index = {
        str(node): index
        for index, node in enumerate(template_payload.get("all_nodes") or [])
    }
    profile_stamps = [
        ((profile.get("payload") or {}).get("direct_retained_stamps") or [])
        for profile in sample_profiles
    ]
    if not profile_stamps or not profile_stamps[0]:
        return {}
    local_replacements: dict[tuple[int, str, int], sp.Expr] = {}

    def replacement_for_values(
        *,
        branch_id: str,
        kind: str,
        key: str,
        row: int,
        col: int,
        values: list[sp.Expr],
    ) -> sp.Expr | None:
        if all(_expr_equal_light(values[0], value) for value in values[1:]):
            return None
        if branch_id:
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
                node_order=list(template_payload.get("all_nodes") or []),
                values=values,
                branch_id=branch_id,
                kind=kind,
                base_case=base_case_by_branch.get(branch_id, 0),
            )
            if runtime_replacement is not None:
                return runtime_replacement
            if _position_depends_only_on_branch(
                sample_profiles,
                values,
                branch_id,
                base_case_by_branch.get(branch_id, 0),
            ):
                sign, alias = _ensure_branch_profile_alias(
                    aliases=aliases,
                    grouped=grouped,
                    symbol_table=symbol_table,
                    sample_profiles=sample_profiles,
                    runtime_groups=runtime_groups,
                    branch_id=branch_id,
                    kind=kind,
                    key=key,
                    row=row,
                    col=col,
                    node_order=alias_node_order,
                    profile_values=values,
                    base_case=base_case_by_branch.get(branch_id, 0),
                )
                return sign * sp.Symbol(alias)
        temp_replacements: dict[tuple[str, int, int], sp.Expr] = {}
        _add_global_profile_alias(
            aliases=aliases,
            replacements=temp_replacements,
            symbol_table=symbol_table,
            global_alias_cache=global_alias_cache,
            key=key,
            row=row,
            col=col,
            node_order=alias_node_order,
            values=values,
        )
        return temp_replacements.get((key, row, col))

    base_stamps = profile_stamps[0]
    for stamp_index, base_stamp in enumerate(base_stamps):
        if any(stamp_index >= len(stamps) for stamps in profile_stamps):
            continue
        branch_id = _stamp_branch_id(base_stamp, branch_ids, stamp_index)
        for kind, key, col_default in (("G", "G_full", None), ("Ihis", "Ihis_full", 0)):
            base_entries = base_stamp.get(kind) or []
            for entry_index, base_entry in enumerate(base_entries):
                values: list[sp.Expr] = []
                compatible = True
                for stamps in profile_stamps:
                    stamp = stamps[stamp_index]
                    entries = stamp.get(kind) or []
                    if entry_index >= len(entries):
                        compatible = False
                        break
                    entry = entries[entry_index]
                    if str(entry.get("row")) != str(base_entry.get("row")):
                        compatible = False
                        break
                    if kind == "G" and str(entry.get("col")) != str(base_entry.get("col")):
                        compatible = False
                        break
                    values.append(_parse_expr(entry.get("expr", "0")))
                if not compatible or not values:
                    continue
                row = node_index.get(str(base_entry.get("row")))
                col = col_default if kind == "Ihis" else node_index.get(str(base_entry.get("col")))
                if row is None or col is None:
                    continue
                replacement = replacement_for_values(
                    branch_id=branch_id,
                    kind="G" if kind == "G" else "Ihis",
                    key=key,
                    row=row,
                    col=col,
                    values=values,
                )
                if replacement is not None:
                    local_replacements[(stamp_index, kind, entry_index)] = replacement
    return local_replacements


def _rewrite_direct_retained_stamps_with_aliases(
    template_payload: dict,
    local_replacements: Mapping[tuple[int, str, int], sp.Expr],
) -> None:
    node_index = {
        str(node): index
        for index, node in enumerate(template_payload.get("all_nodes") or [])
    }
    rewritten_stamps = []
    for stamp_index, stamp in enumerate(template_payload.get("direct_retained_stamps") or []):
        next_stamp = json.loads(json.dumps(stamp))
        for entry_index, entry in enumerate(next_stamp.get("G") or []):
            row = node_index.get(str(entry.get("row")))
            col = node_index.get(str(entry.get("col")))
            if row is None or col is None:
                continue
            replacement = local_replacements.get((stamp_index, "G", entry_index))
            if replacement is None:
                continue
            text = _expr_to_payload_text(replacement)
            entry["expr"] = text
            if "tagged" in entry:
                entry["tagged"] = text
        for entry_index, entry in enumerate(next_stamp.get("Ihis") or []):
            row = node_index.get(str(entry.get("row")))
            if row is None:
                continue
            replacement = local_replacements.get((stamp_index, "Ihis", entry_index))
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
    final_recovery_profiles: list[dict] = []
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
        adapted = _gkk_placeholder_profile_adapter(sample_profiles)
        if adapted is None:
            adapted = _final_retained_profile_adapter(sample_profiles)
        if adapted is None:
            reason = _final_retained_profile_adapter_unavailable_reason(sample_profiles)
            raise ValueError(f"{exc}; final-retained adapter unavailable: {reason}") from exc
        sample_profiles, final_recovery_profiles = adapted
    branch_ids = _branch_ids_from_profiles(sample_profiles)
    if not branch_ids:
        return None

    base_payload = sample_profiles[0].get("payload")
    if not isinstance(base_payload, dict):
        return None
    node_order = [str(node) for node in base_payload.get("all_nodes") or []]
    node_display_names = _payload_node_display_names(base_payload)
    alias_node_order = [
        str(node_display_names.get(node, node))
        for node in node_order
    ]
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

    source_template_payload = json.loads(json.dumps(base_payload))
    local_direct_replacements = _direct_retained_local_entry_replacements(
        template_payload=source_template_payload,
        sample_profiles=sample_profiles,
        branch_ids=branch_ids,
        aliases=aliases,
        grouped=grouped,
        symbol_table=symbol_table,
        runtime_groups=runtime_groups,
        global_alias_cache=global_alias_cache,
        base_case_by_branch=base_case_by_branch,
        alias_node_order=alias_node_order,
    )

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
                            node_order=alias_node_order,
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
                            node_order=alias_node_order,
                            values_by_init=runtime_invariant,
                        )
                        continue
                    pending_composite_entries.append((key, row, col, values))
                    continue
                branch_id = candidates[0]
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
                    node_order=alias_node_order,
                    values=values,
                    branch_id=branch_id,
                    kind=kind,
                    base_case=base_case_by_branch.get(branch_id, 0),
                )
                if runtime_replacement is not None:
                    replacements[(key, row, col)] = runtime_replacement
                    continue
                sign, alias = _ensure_branch_profile_alias(
                    aliases=aliases,
                    grouped=grouped,
                    symbol_table=symbol_table,
                    sample_profiles=sample_profiles,
                    runtime_groups=runtime_groups,
                    branch_id=branch_id,
                    kind=kind,
                    key=key,
                    row=row,
                    col=col,
                    node_order=alias_node_order,
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
                node_order=alias_node_order,
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
            node_order=alias_node_order,
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
    _rewrite_direct_retained_stamps_with_aliases(template_payload, local_direct_replacements)
    init_profiles = _init_profiles_from_sample_profiles(sample_profiles)
    internal_layout_profiles = _internal_layout_profiles_from_recovery(
        final_recovery_profiles,
        template_payload.get("internal_nodes") or [],
    )

    return {
        "template_payload": template_payload,
        "aliases": aliases,
        "profiles": init_profiles,
        "sample_profiles": sample_profiles,
        "branch_ids": [branch_id for branch_id in branch_ids if branch_id not in runtime_groups],
        "runtime_case_groups": list(runtime_groups.values()),
        "final_recovery_profiles": final_recovery_profiles,
        "internal_layout_profiles": internal_layout_profiles,
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
            "c_draft": _strip_unused_dimension_enum_for_direct_gvalue_draft(_ensure_static_blank_line(draft)),
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


def _branch_can_use_global_case_id(profiles: Sequence[Mapping], branch_id: str) -> bool:
    if not profiles:
        return False
    for index, profile in enumerate(profiles):
        try:
            global_case = int(profile.get("case_id", index))
        except (TypeError, ValueError):
            global_case = index
        if _profile_case_index(profile, branch_id, 0) != global_case:
            return False
    return True


def _global_case_id_branch_ids(profiles: Sequence[Mapping], branch_ids: Sequence[str]) -> set[str]:
    return {
        str(branch_id)
        for branch_id in branch_ids
        if _branch_can_use_global_case_id(profiles, str(branch_id))
    }


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


def _alias_local_case_name(
    alias: str,
    info: Mapping,
    case_id_symbol: str,
    *,
    global_case_branch_ids: set[str] | None = None,
) -> str:
    branch_id = info.get("branch_id") or alias
    selector = info.get("selector")
    if selector == "global":
        return case_id_symbol
    if selector == "runtime":
        return str(info.get("case_id_symbol") or f"runtime_{_c_identifier_name(branch_id, 'branch')}_case_id")
    if str(branch_id) in (global_case_branch_ids or set()):
        return case_id_symbol
    return f"{_c_identifier_name(branch_id, 'branch')}_case_id"


def _group_alias_entries(
    aliases: dict[str, dict],
    wanted_owner: str,
    case_id_symbol: str,
    *,
    global_case_branch_ids: set[str] | None = None,
) -> dict[str, list[tuple[str, dict, dict[int, sp.Expr]]]]:
    grouped: dict[str, list[tuple[str, dict, dict[int, sp.Expr]]]] = {}
    for alias, info in aliases.items():
        if info.get("owner") != wanted_owner:
            continue
        local_name = _alias_local_case_name(
            alias,
            info,
            case_id_symbol,
            global_case_branch_ids=global_case_branch_ids,
        )
        case_values = {int(case_index): _parse_expr(expr) for case_index, expr in (info.get("case_values") or {}).items()}
        grouped.setdefault(local_name, []).append((alias, info, case_values))
    return grouped


def _alias_case_assignments(
    entries: Sequence[tuple[str, dict, dict[int, sp.Expr]]],
    case_index: int,
) -> list[tuple[str, sp.Expr]]:
    assignments: list[tuple[str, sp.Expr]] = []
    for alias, _info, case_values in entries:
        default_expr = case_values[min(case_values)] if case_values else sp.Integer(0)
        assignments.append((alias, case_values.get(case_index, default_expr)))
    return assignments


def _shared_source_temp_plan(
    aliases: dict[str, dict],
    *,
    case_id_symbol: str,
    symbol_table: Mapping[str, str] | None,
    global_case_branch_ids: set[str] | None = None,
) -> dict[str, dict[int, list[tuple[str, sp.Expr]]]]:
    """Find RAM-safe source CSE temps shared by RAM G and CODE/Ihis aliases.

    This is intentionally structural: sp.cse finds repeated source subtrees and
    we only share the exact same resolved CSE expression.  Runtime-mutable case
    selectors are skipped because RAM-assigned temps would not refresh at runtime.
    """
    symbol_table = dict(symbol_table or {})
    ram_groups = _group_alias_entries(
        aliases,
        "RAM",
        case_id_symbol,
        global_case_branch_ids=global_case_branch_ids,
    )
    code_groups: dict[str, list[tuple[str, dict, dict[int, sp.Expr]]]] = {}
    for owner in ("CODE", "CODE_PER_STEP"):
        for local_name, entries in _group_alias_entries(
            aliases,
            owner,
            case_id_symbol,
            global_case_branch_ids=global_case_branch_ids,
        ).items():
            code_groups.setdefault(local_name, []).extend(entries)
    plan: dict[str, dict[int, list[tuple[str, sp.Expr]]]] = {}
    for local_name, ram_entries in ram_groups.items():
        if local_name.startswith("runtime_") or local_name not in code_groups:
            continue
        code_entries = code_groups[local_name]
        case_indices = sorted(
            {case_index for _alias, _info, values in [*ram_entries, *code_entries] for case_index in values}
        )
        if not case_indices:
            continue
        scope = _source_cse_scope_name(local_name)
        for case_index in case_indices:
            ram_assignments = _alias_case_assignments(ram_entries, case_index)
            code_assignments = _alias_case_assignments(code_entries, case_index)
            ram_temps, _ram_reduced = _source_level_cse_assignments(
                ram_assignments,
                temp_prefix=f"__ram_shared_probe_{scope}_case{case_index}_tmp",
            )
            code_temps, _code_reduced = _source_level_cse_assignments(
                code_assignments,
                temp_prefix=f"__code_shared_probe_{scope}_case{case_index}_tmp",
            )
            if not ram_temps or not code_temps:
                continue
            ram_by_key: dict[str, sp.Expr] = {}
            for _name, _expr, resolved in _resolved_cse_temps(ram_temps):
                if _expr_stage(resolved, symbol_table) != "RAM":
                    continue
                ram_by_key.setdefault(_source_expr_key(resolved), resolved)
            shared_exprs: list[sp.Expr] = []
            seen: set[str] = set()
            for _name, _expr, resolved in _resolved_cse_temps(code_temps):
                key = _source_expr_key(resolved)
                if key not in ram_by_key or key in seen:
                    continue
                if _expr_stage(resolved, symbol_table) != "RAM":
                    continue
                seen.add(key)
                shared_exprs.append(ram_by_key[key])
            if not shared_exprs:
                continue
            case_items: list[tuple[str, sp.Expr]] = []
            for index, expr in enumerate(shared_exprs):
                case_items.append((f"sourceGI_{scope}_case{case_index}_tmp{index}", expr))
            plan.setdefault(local_name, {})[case_index] = case_items
    return plan


def _shared_source_temp_declaration_lines(
    shared_plan: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]],
    declared: set[str],
) -> list[str]:
    lines: list[str] = []
    for case_map in shared_plan.values():
        for items in case_map.values():
            for name, _expr in items:
                if name in declared:
                    continue
                lines.append(f"    double {name} = 0.0;")
                declared.add(name)
    return lines


def _shared_source_temp_substitutions(
    shared_plan: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None,
    local_name: str,
    case_index: int,
) -> dict[sp.Expr, sp.Symbol]:
    items = ((shared_plan or {}).get(local_name) or {}).get(int(case_index)) or []
    substitutions: dict[sp.Expr, sp.Symbol] = {}
    emitted: dict[sp.Expr, sp.Symbol] = {}
    symbol_full_exprs: dict[sp.Symbol, sp.Expr] = {}
    for name, expr in items:
        expr = sp.sympify(expr)
        symbol = sp.Symbol(name)
        full_expr = expr.xreplace(symbol_full_exprs)
        for candidate in (expr, sp.sympify(full_expr)):
            substitutions.setdefault(candidate, symbol)
            substitutions.setdefault(-candidate, -symbol)
        resolved = _apply_source_temp_substitutions(expr, emitted)
        substitutions.setdefault(sp.sympify(resolved), symbol)
        substitutions.setdefault(-sp.sympify(resolved), -symbol)
        for candidate in (expr, sp.sympify(full_expr), sp.sympify(resolved)):
            emitted.setdefault(candidate, symbol)
            emitted.setdefault(-candidate, -symbol)
        symbol_full_exprs[symbol] = sp.sympify(full_expr)
    return substitutions


def _shared_source_temp_substitutions_for_profile(
    shared_plan: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None,
    local_case_indices: Mapping[str, int],
) -> dict[sp.Expr, sp.Symbol]:
    substitutions: dict[sp.Expr, sp.Symbol] = {}
    for local_name, case_index in local_case_indices.items():
        substitutions.update(_shared_source_temp_substitutions(shared_plan, local_name, int(case_index)))
    return substitutions


def _local_case_indices_for_profile(
    *,
    case_id_symbol: str,
    profile_index: int,
    profile: Mapping,
    branch_ids: Sequence[str],
) -> dict[str, int]:
    indices = {case_id_symbol: int(profile_index)}
    for branch_id in branch_ids:
        local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        indices[local_name] = int(_profile_case_index(profile, branch_id, 0))
    return indices


def _shared_source_temp_assignment_lines(
    shared_plan: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]],
    *,
    comment: str = "Resolve RAM-safe source temporaries shared by G and Ihis aliases.",
) -> list[str]:
    if not shared_plan:
        return []
    lines = [f"    /* {comment} */"]
    for local_name, case_map in shared_plan.items():
        if not case_map:
            continue
        lines.append(f"    switch ({local_name}) {{")
        for case_index in sorted(case_map):
            lines.append(f"    case {case_index}:")
            emitted: dict[sp.Expr, sp.Symbol] = {}
            symbol_full_exprs: dict[sp.Symbol, sp.Expr] = {}
            for name, expr in case_map[case_index]:
                full_expr = sp.sympify(expr).xreplace(symbol_full_exprs)
                rhs = _apply_source_temp_substitutions(expr, emitted)
                lines.append(f"        {name} = {_ccode(rhs)};")
                symbol = sp.Symbol(name)
                for candidate in (sp.sympify(expr), sp.sympify(full_expr), sp.sympify(rhs)):
                    emitted.setdefault(candidate, symbol)
                    emitted.setdefault(-candidate, -symbol)
                symbol_full_exprs[symbol] = sp.sympify(full_expr)
            lines.append("        break;")
        default_index = min(case_map)
        lines.append("    default:")
        emitted = {}
        symbol_full_exprs = {}
        for name, expr in case_map[default_index]:
            full_expr = sp.sympify(expr).xreplace(symbol_full_exprs)
            rhs = _apply_source_temp_substitutions(expr, emitted)
            lines.append(f"        {name} = {_ccode(rhs)};")
            symbol = sp.Symbol(name)
            for candidate in (sp.sympify(expr), sp.sympify(full_expr), sp.sympify(rhs)):
                emitted.setdefault(candidate, symbol)
                emitted.setdefault(-candidate, -symbol)
            symbol_full_exprs[symbol] = sp.sympify(full_expr)
        lines.append("        break;")
        lines.append("    }")
    return lines


def _merge_source_temp_plans(
    *plans: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None,
) -> dict[str, dict[int, list[tuple[str, sp.Expr]]]]:
    merged: dict[str, dict[int, list[tuple[str, sp.Expr]]]] = {}
    seen_names: set[str] = set()
    for plan in plans:
        for local_name, case_map in (plan or {}).items():
            for case_index, items in case_map.items():
                out = merged.setdefault(local_name, {}).setdefault(int(case_index), [])
                for name, expr in items:
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    out.append((name, sp.sympify(expr)))
    return merged


def _source_temp_stage_overrides(
    plan: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None,
    stage: str,
) -> dict[str, str]:
    """Treat already-hoisted source temporaries as available at the given stage.

    Alias-local CSE may build on sourceGI/sourceG temps emitted by an earlier
    switch.  Those generated symbols are not user symbols, so the normal symbol
    table would mark them UNKNOWN and keep later sourceG/sourceIhis temps inside
    the alias switch.  The override preserves the staged pipeline: first switch
    computes reusable temporaries, second switch assigns full alias values only.
    """
    overrides: dict[str, str] = {}
    for case_map in (plan or {}).values():
        for items in case_map.values():
            for name, _expr in items:
                overrides[str(name)] = stage
    return overrides


def _owner_source_temp_prefix(wanted_owner: str) -> str:
    if wanted_owner == "CODE_PER_STEP":
        return "sourceIhis"
    if wanted_owner == "CODE":
        return "sourceCodeG"
    return "sourceG"


def _alias_source_temp_plan(
    aliases: dict[str, dict],
    wanted_owner: str,
    case_id_symbol: str,
    *,
    base_source_temps: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None,
    symbol_table: Mapping[str, str] | None,
    require_ram_safe: bool,
    global_case_branch_ids: set[str] | None = None,
) -> dict[str, dict[int, list[tuple[str, sp.Expr]]]]:
    """Hoist owner-local source CSE temps before the full-value alias switch.

    The alias switch should stay boring: assign multcase_* full values only.
    Any repeated source-level helper expressions are computed in an earlier
    switch for the same local case selector, then reused by G/Ihis aliases.
    """
    grouped = _group_alias_entries(
        aliases,
        wanted_owner,
        case_id_symbol,
        global_case_branch_ids=global_case_branch_ids,
    )
    if not grouped:
        return {}
    symbol_table = dict(symbol_table or {})
    if require_ram_safe:
        symbol_table.update(_source_temp_stage_overrides(base_source_temps, "RAM"))
    plan: dict[str, dict[int, list[tuple[str, sp.Expr]]]] = {}
    prefix_root = _owner_source_temp_prefix(wanted_owner)
    for local_name, entries in grouped.items():
        case_indices = sorted({case_index for _alias, _info, case_values in entries for case_index in case_values})
        if not case_indices:
            continue
        scope = _source_cse_scope_name(local_name)
        for case_index in case_indices:
            substitutions = _shared_source_temp_substitutions(base_source_temps, local_name, case_index)
            assignments = [
                (lhs, _apply_source_temp_substitutions(expr, substitutions))
                for lhs, expr in _alias_case_assignments(entries, case_index)
            ]
            temps, _reduced = _source_level_cse_assignments(
                assignments,
                temp_prefix=f"{prefix_root}_{scope}_case{case_index}_tmp",
            )
            if not temps:
                continue
            case_items: list[tuple[str, sp.Expr]] = []
            for name, expr, resolved in _resolved_cse_temps(temps):
                if require_ram_safe and _expr_stage(resolved, symbol_table) != "RAM":
                    continue
                case_items.append((name, expr))
            if case_items:
                plan.setdefault(local_name, {})[int(case_index)] = case_items
    return plan


def _apply_shared_source_temp_text_reuse(
    draft: str,
    shared_plan: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None,
) -> str:
    """Rewrite existing local sourceG temps to persistent sourceGI temps.

    Some multi-case drafts are produced by patching an existing structured draft,
    so the alias-resolution blocks already contain local sourceG_* CSE temps.
    Rebuilding that whole block would be fragile; this pass only rewrites a
    local temp when its emitted RHS is exactly the same C expression as a
    RAM-safe sourceGI temp.  The RHS may be either the original expression or
    the expression after earlier sourceGI temps have already been substituted.
    It does no algebraic equivalence checking.
    """
    if not shared_plan:
        return draft

    def rhs_to_shared_names(items: Sequence[tuple[str, sp.Expr]]) -> dict[str, str]:
        rhs_map: dict[str, str] = {}
        emitted: dict[sp.Expr, sp.Symbol] = {}
        for shared_name, expr in items:
            parsed = sp.sympify(expr)
            rhs_map.setdefault(_ccode(parsed), shared_name)
            rhs_map.setdefault(_ccode(_apply_source_temp_substitutions(parsed, emitted)), shared_name)
            emitted[parsed] = sp.Symbol(shared_name)
        return rhs_map

    def replace_local_assignments(
        source: str,
        *,
        scope: str,
        label: str,
        rhs_map: Mapping[str, str],
    ) -> str:
        pattern = re.compile(
            rf"(?m)^(?P<indent>\s*)double\s+"
            rf"(?P<local>sourceG_{re.escape(scope)}_{re.escape(label)}_tmp\d+)"
            rf"\s*=\s*(?P<rhs>[^;\n]+);\s*$"
        )
        search_start = 0
        while True:
            match = pattern.search(source, search_start)
            if not match:
                return source
            shared_name = rhs_map.get(match.group("rhs").strip())
            if not shared_name:
                search_start = match.end()
                continue
            local_temp = match.group("local")
            line_end = match.end()
            if source[line_end:line_end + 1] == "\n":
                line_end += 1
            source = source[:match.start()] + source[line_end:]
            source = re.sub(rf"\b{re.escape(local_temp)}\b", shared_name, source)
            search_start = 0

    for local_name, case_map in shared_plan.items():
        scope = _source_cse_scope_name(local_name)
        for case_index, items in case_map.items():
            draft = replace_local_assignments(
                draft,
                scope=scope,
                label=f"case{int(case_index)}",
                rhs_map=rhs_to_shared_names(items),
            )
        if case_map:
            default_index = min(case_map)
            draft = replace_local_assignments(
                draft,
                scope=scope,
                label="default",
                rhs_map=rhs_to_shared_names(case_map[default_index]),
            )
    return draft


def _lift_repeated_ram_safe_source_temps_from_text(
    draft: str,
    symbol_table: Mapping[str, str] | None,
) -> str:
    """Lift exact RAM/CODE repeated sourceG temps to persistent sourceGI temps.

    This is a conservative post-pass for structured multi-case drafts.  It uses
    the existing RAM_PASS1 line as proof that the exact RHS is available before
    CODE for that same local case.  It does no algebraic equivalence checking and
    skips RHS values that depend on another local sourceG temp.
    """
    code_pos = draft.find("CODE:")
    if code_pos < 0:
        return draft
    _ = symbol_table  # Kept for API symmetry with source-temp planning callers.
    assignment_re = re.compile(
        r"(?m)^(?P<indent>\s*)double\s+"
        r"(?P<name>sourceG_(?P<scope>.+?)_case(?P<case>\d+)_tmp\d+)"
        r"\s*=\s*(?P<rhs>[^;\n]+);\s*$"
    )
    groups: dict[tuple[int, str], list[re.Match[str]]] = {}
    for match in assignment_re.finditer(draft):
        rhs = match.group("rhs").strip()
        if "sourceG_" in rhs or "sourceGI_" in rhs:
            continue
        groups.setdefault((int(match.group("case")), rhs), []).append(match)

    replacements: list[tuple[int, int, str]] = []
    name_replacements: dict[str, str] = {}
    declarations: list[str] = []
    used_names = _declared_c_names(draft)
    for (case_index, rhs), matches in groups.items():
        has_ram = any(match.start() < code_pos for match in matches)
        has_code = any(match.start() > code_pos for match in matches)
        if not has_ram or not has_code:
            continue
        scopes = [match.group("scope") for match in matches]
        preferred_scope = next((scope for scope in scopes if scope != "case_id"), scopes[0])
        base_name = f"sourceGI_{preferred_scope}_case{case_index}_tmp0"
        shared_name = base_name
        suffix = 1
        while shared_name in used_names:
            if all(re.search(rf"\b{re.escape(match.group('name'))}\b", draft) is None for match in matches):
                break
            shared_name = f"{base_name}_{suffix}"
            suffix += 1
        if shared_name in used_names:
            continue
        used_names.add(shared_name)
        declarations.append(f"    double {shared_name} = 0.0;")
        first_ram = min((match for match in matches if match.start() < code_pos), key=lambda item: item.start())
        for match in matches:
            local_name = match.group("name")
            line_end = match.end()
            if line_end < len(draft) and draft[line_end:line_end + 1] == "\n":
                line_end += 1
            if match is first_ram:
                replacement = f"{match.group('indent')}{shared_name} = {rhs};\n"
            else:
                replacement = ""
            replacements.append((match.start(), line_end, replacement))
            name_replacements[local_name] = shared_name

    if not replacements:
        return draft
    for start, end, replacement in sorted(replacements, reverse=True):
        draft = draft[:start] + replacement + draft[end:]
    for local_name, shared_name in name_replacements.items():
        draft = re.sub(rf"\b{re.escape(local_name)}\b", shared_name, draft)
    if declarations:
        draft = _insert_after_label(draft, "STATIC:", declarations)
    return draft


def _lift_repeated_code_source_temps_from_text(draft: str) -> str:
    """Lift exact repeated CODE-side sourceG temps to persistent sourceGI temps.

    Multi-case G aliases and Ihis aliases are emitted in separate CODE-side
    switch blocks.  When both blocks independently CSE the same source entry
    expression for the same local selector/case, keep one assignment and reuse
    it.  This is intentionally textual: no algebraic equivalence checking and
    no cross-selector or cross-case sharing.
    """
    code_pos = draft.find("CODE:")
    if code_pos < 0:
        return draft
    assignment_re = re.compile(
        r"(?m)^(?P<indent>\s*)double\s+"
        r"(?P<name>sourceG_(?P<scope>.+?)_case(?P<case>\d+)_tmp\d+)"
        r"\s*=\s*(?P<rhs>[^;\n]+);\s*$"
    )
    groups: dict[tuple[str, int, str], list[re.Match[str]]] = {}
    for match in assignment_re.finditer(draft):
        if match.start() < code_pos:
            continue
        rhs = match.group("rhs").strip()
        if "sourceG_" in rhs or "sourceGI_" in rhs:
            continue
        groups.setdefault((match.group("scope"), int(match.group("case")), rhs), []).append(match)

    replacements: list[tuple[int, int, str]] = []
    name_replacements: dict[str, str] = {}
    declarations: list[str] = []
    used_names = _declared_c_names(draft)
    for (scope, case_index, rhs), matches in groups.items():
        if len(matches) < 2:
            continue
        base_name = f"sourceGI_{scope}_case{case_index}_tmp0"
        shared_name = base_name
        suffix = 1
        while shared_name in used_names:
            shared_name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(shared_name)
        declarations.append(f"    double {shared_name} = 0.0;")
        first = min(matches, key=lambda item: item.start())
        for match in matches:
            local_name = match.group("name")
            line_end = match.end()
            if line_end < len(draft) and draft[line_end:line_end + 1] == "\n":
                line_end += 1
            replacement = f"{match.group('indent')}{shared_name} = {rhs};\n" if match is first else ""
            replacements.append((match.start(), line_end, replacement))
            name_replacements[local_name] = shared_name

    if not replacements:
        return draft
    for start, end, replacement in sorted(replacements, reverse=True):
        draft = draft[:start] + replacement + draft[end:]
    for local_name, shared_name in name_replacements.items():
        draft = re.sub(rf"\b{re.escape(local_name)}\b", shared_name, draft)
    if declarations:
        draft = _insert_after_label(draft, "STATIC:", declarations)
    return draft


def _conditional_ram_case_assignments(entry_plans: Sequence[Mapping], case_index: int) -> list[tuple[str, sp.Expr]]:
    assignments: list[tuple[str, sp.Expr]] = []
    for plan in entry_plans:
        ram_case = next((item for item in plan.get("ram_cases", []) if int(item.get("index", -1)) == int(case_index)), None)
        if ram_case is None:
            continue
        expr = sp.sympify(ram_case.get("expr", 0))
        if expr == 0:
            continue
        alias_stamp = _ram_stamp_alias_assignment(
            template_expr=sp.sympify(plan.get("template_expr", expr)),
            ram_expr=expr,
        )
        if alias_stamp is not None:
            alias_assignment, _stamp_expr = alias_stamp
            _append_ram_alias_assignment(assignments, alias_assignment)
        assignments.append((f"__ram_final_g_{int(plan.get('row', 0))}_{int(plan.get('col', 0))}", expr))
    return assignments


def _conditional_shared_source_temp_plan(
    *,
    entry_plans: Sequence[Mapping],
    aliases: dict[str, dict],
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    symbol_table: Mapping[str, str] | None,
) -> dict[str, dict[int, list[tuple[str, sp.Expr]]]]:
    """Shared RAM/CODE source temps for the no-internal conditional-GValue draft."""
    symbol_table = dict(symbol_table or {})
    code_groups: dict[str, list[tuple[str, dict, dict[int, sp.Expr]]]] = {}
    for owner in ("CODE", "CODE_PER_STEP"):
        for local_name, entries in _group_alias_entries(aliases, owner, case_id_symbol).items():
            code_groups.setdefault(local_name, []).extend(entries)
    if not code_groups:
        return {}
    plan: dict[str, dict[int, list[tuple[str, sp.Expr]]]] = {}
    for local_name, code_entries in code_groups.items():
        if local_name.startswith("runtime_"):
            continue
        scope = _source_cse_scope_name(local_name)
        for case_index, _profile in enumerate(profiles or []):
            ram_assignments = _conditional_ram_case_assignments(entry_plans, case_index)
            code_assignments = _alias_case_assignments(code_entries, case_index)
            if not ram_assignments or not code_assignments:
                continue
            ram_temps, _ram_reduced = _source_level_cse_assignments(
                ram_assignments,
                temp_prefix=f"__ram_shared_probe_{scope}_case{case_index}_tmp",
            )
            code_temps, _code_reduced = _source_level_cse_assignments(
                code_assignments,
                temp_prefix=f"__code_shared_probe_{scope}_case{case_index}_tmp",
            )
            if not ram_temps or not code_temps:
                continue
            ram_by_key: dict[str, sp.Expr] = {}
            for _name, _expr, resolved in _resolved_cse_temps(ram_temps):
                if _expr_stage(resolved, symbol_table) == "RAM":
                    ram_by_key.setdefault(_source_expr_key(resolved), resolved)
            shared_exprs: list[sp.Expr] = []
            seen: set[str] = set()
            for _name, _expr, resolved in _resolved_cse_temps(code_temps):
                key = _source_expr_key(resolved)
                if key in ram_by_key and key not in seen and _expr_stage(resolved, symbol_table) == "RAM":
                    shared_exprs.append(ram_by_key[key])
                    seen.add(key)
            if shared_exprs:
                plan.setdefault(local_name, {})[case_index] = [
                    (f"sourceGI_{scope}_case{case_index}_tmp{index}", expr)
                    for index, expr in enumerate(shared_exprs)
                ]
    return plan


def _alias_assignment_lines(
    aliases: dict[str, dict],
    wanted_owner: str,
    case_id_symbol: str = "case_id",
    *,
    shared_source_temps: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None = None,
    global_case_branch_ids: set[str] | None = None,
) -> list[str]:
    grouped = _group_alias_entries(
        aliases,
        wanted_owner,
        case_id_symbol,
        global_case_branch_ids=global_case_branch_ids,
    )
    if not grouped:
        return []
    lines = [f"    /* Resolve {wanted_owner} multi-case effective aliases as full values, never deltas. */"]

    for local_name, entries in grouped.items():
        case_indices = sorted({case_index for _alias, _info, case_values in entries for case_index in case_values})
        lines.append(f"    switch ({local_name}) {{")
        for case_index in case_indices:
            lines.append(f"    case {case_index}:")
            assignments = _alias_case_assignments(entries, case_index)
            substitutions = _shared_source_temp_substitutions(shared_source_temps, local_name, case_index)
            for lhs, expr in assignments:
                resolved = _apply_source_temp_substitutions(expr, substitutions)
                lines.append(f"        {lhs} = {_ccode(resolved)};")
            lines.append("        break;")
        lines.append("    default:")
        default_case_index = min(case_indices) if case_indices else 0
        default_assignments = _alias_case_assignments(entries, default_case_index)
        substitutions = _shared_source_temp_substitutions(shared_source_temps, local_name, default_case_index)
        for lhs, expr in default_assignments:
            resolved = _apply_source_temp_substitutions(expr, substitutions)
            lines.append(f"        {lhs} = {_ccode(resolved)};")
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
    symbol_table: Mapping[str, str] | None = None,
) -> str:
    declared = _declared_c_names(draft)
    global_case_branch_ids = _global_case_id_branch_ids(profiles, branch_ids)
    decoded_branch_ids = [branch_id for branch_id in branch_ids if branch_id not in global_case_branch_ids]
    shared_source_temps = _shared_source_temp_plan(
        aliases,
        case_id_symbol=case_id_symbol,
        symbol_table=symbol_table or {},
        global_case_branch_ids=global_case_branch_ids,
    )
    ram_alias_temps = _alias_source_temp_plan(
        aliases,
        "RAM",
        case_id_symbol,
        base_source_temps=shared_source_temps,
        symbol_table=symbol_table or {},
        require_ram_safe=True,
        global_case_branch_ids=global_case_branch_ids,
    )
    ram_source_temps = _merge_source_temp_plans(shared_source_temps, ram_alias_temps)
    code_alias_temps = _alias_source_temp_plan(
        aliases,
        "CODE",
        case_id_symbol,
        base_source_temps=ram_source_temps,
        symbol_table=symbol_table or {},
        require_ram_safe=False,
        global_case_branch_ids=global_case_branch_ids,
    )
    code_source_temps = _merge_source_temp_plans(ram_source_temps, code_alias_temps)
    ihis_alias_temps = _alias_source_temp_plan(
        aliases,
        "CODE_PER_STEP",
        case_id_symbol,
        base_source_temps=code_source_temps,
        symbol_table=symbol_table or {},
        require_ram_safe=False,
        global_case_branch_ids=global_case_branch_ids,
    )
    all_source_temps = _merge_source_temp_plans(ram_source_temps, code_alias_temps, ihis_alias_temps)
    declarations: list[str] = []
    for branch_id in decoded_branch_ids:
        local_name = f"{_c_identifier_name(branch_id, 'branch')}_case_id"
        if local_name not in declared:
            declarations.append(f"    int {local_name} = 0;")
            declared.add(local_name)
    declarations.extend(_shared_source_temp_declaration_lines(all_source_temps, declared))
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
        _multicase_local_case_lines(case_id_symbol, profiles, decoded_branch_ids)
        + _shared_source_temp_assignment_lines(ram_source_temps)
        + _alias_assignment_lines(
            aliases,
            "RAM",
            case_id_symbol,
            shared_source_temps=ram_source_temps,
            global_case_branch_ids=global_case_branch_ids,
        )
    )
    draft = _insert_after_label(draft, "RAM_PASS1:", ram_lines)

    code_lines = (
        _shared_source_temp_assignment_lines(
            code_alias_temps,
            comment="Resolve CODE source temporaries used by G aliases.",
        )
        + _alias_assignment_lines(
            aliases,
            "CODE",
            case_id_symbol,
            shared_source_temps=code_source_temps,
            global_case_branch_ids=global_case_branch_ids,
        )
        + _shared_source_temp_assignment_lines(
            ihis_alias_temps,
            comment="Resolve CODE_PER_STEP source temporaries used by Ihis aliases.",
        )
        + _alias_assignment_lines(
            aliases,
            "CODE_PER_STEP",
            case_id_symbol,
            shared_source_temps=all_source_temps,
            global_case_branch_ids=global_case_branch_ids,
        )
    )
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
    draft = _apply_shared_source_temp_text_reuse(draft, all_source_temps)
    draft = _ensure_static_blank_line(draft)
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


def _find_w_code_symmetric_inverse_block(draft: str, size: int) -> tuple[int, int, str] | None:
    marker = f"    mat_{size}x{size}_sym_inv_code("
    marker_index = draft.find(marker)
    if marker_index < 0:
        return None
    start = draft.rfind("    double W_code_11 = 0.0;\n", 0, marker_index)
    end_marker = f"    set_CODE(&W_code, {size - 1}, {size - 1}, W_code_{size}{size});\n"
    end = draft.find(end_marker, marker_index)
    if start < 0 or end < 0:
        return None
    end += len(end_marker)
    return start, end, draft[start:end]


def _find_w_code_sym3_inverse_block(draft: str) -> tuple[int, int, str] | None:
    return _find_w_code_symmetric_inverse_block(draft, 3)


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


def _active_diagonal_w_code_lines(size: int, active_rows: set[int], indent: int) -> list[str]:
    prefix = " " * indent
    lines: list[str] = []
    for row in range(size):
        for col in range(size):
            value = f"1.0 / get_CODE(&Gkk_code, {row}, {row})" if row == col and row in active_rows else "0.0"
            lines.append(f"{prefix}set_CODE(&W_code, {row}, {col}, {value});")
    return lines


def _active_internal_rows_by_case(
    recovery_profiles: Sequence[Mapping] | None,
    template_internal_nodes: Sequence[str] | None,
) -> dict[int, set[int]]:
    node_to_row = {str(node): row for row, node in enumerate(template_internal_nodes or [])}
    rows_by_case: dict[int, set[int]] = {}
    for profile in recovery_profiles or []:
        rows = {
            node_to_row[str(node)]
            for node in (profile.get("recovery_nodes") or [])
            if str(node) in node_to_row
        }
        for case_id in profile.get("case_ids") or []:
            rows_by_case[int(case_id)] = set(rows)
    return rows_by_case


def _internal_layout_profiles_from_recovery(
    recovery_profiles: Sequence[Mapping] | None,
    template_internal_nodes: Sequence[str] | None,
) -> list[dict]:
    template_internal_nodes = [str(node) for node in (template_internal_nodes or [])]
    if not template_internal_nodes:
        return []
    grouped: dict[tuple[str, ...], dict] = {}
    for profile in recovery_profiles or []:
        active = tuple(
            node
            for node in template_internal_nodes
            if node in {str(item) for item in (profile.get("recovery_nodes") or [])}
        )
        placeholder = [node for node in template_internal_nodes if node not in set(active)]
        entry = grouped.setdefault(
            active,
            {
                "case_ids": [],
                "ordered_active_internal_nodes": list(active),
                "active_internal_count": len(active),
                "internal_to_active_index": {
                    node: index
                    for index, node in enumerate(active)
                },
                "placeholder_internal_nodes": placeholder,
            },
        )
        for case_id in profile.get("case_ids") or []:
            case_index = int(case_id)
            entry["case_ids"].append(case_index)
    if not grouped:
        return []
    profiles = sorted(grouped.values(), key=lambda item: (-int(item["active_internal_count"]), item["case_ids"]))
    if len(profiles) < 2:
        return []
    if int(profiles[0]["active_internal_count"]) == int(profiles[-1]["active_internal_count"]):
        return []
    for index, item in enumerate(profiles):
        item["case_ids"] = sorted(dict.fromkeys(int(case_id) for case_id in item["case_ids"]))
        item["profile_id"] = f"INTERNAL_CASE_{index}"
    return profiles


def _replace_enum_for_internal_layouts(draft: str, profiles: Sequence[Mapping]) -> str:
    enum_match = re.search(r"enum \{ ([^}]*INTERNAL_NODES\s*=\s*\d+[^}]*) \};", draft)
    if enum_match is None:
        return draft
    enum_body = enum_match.group(1)
    additions: list[str] = []
    for index, profile in enumerate(profiles):
        additions.append(f"INTERNAL_CASE_{index} = {index}")
    for index, profile in enumerate(profiles):
        additions.append(f"INTERNAL_NODES_CASE_{index} = {int(profile.get('active_internal_count') or 0)}")
    replacement = f"enum {{ {enum_body}, {', '.join(additions)} }};"
    return draft[:enum_match.start()] + replacement + draft[enum_match.end():]


def _internal_default_profile_index(profiles: Sequence[Mapping]) -> int:
    for profile in profiles:
        if int(profile.get("active_internal_count") or 0) == 0:
            return int(str(profile.get("profile_id") or "INTERNAL_CASE_0").rsplit("_", 1)[-1])
    return int(str((profiles[0].get("profile_id") if profiles else "INTERNAL_CASE_0") or "INTERNAL_CASE_0").rsplit("_", 1)[-1])


def _insert_internal_profile_state(draft: str, profiles: Sequence[Mapping]) -> str:
    if "int internal_active =" in draft:
        return draft
    default_index = _internal_default_profile_index(profiles)
    default = f"INTERNAL_CASE_{default_index}"
    default_count = f"INTERNAL_NODES_CASE_{default_index}"
    lines = [
        f"    int internal_profile = {default};",
        f"    int internal_active = {default_count};",
    ]
    marker = "    /* Runtime state */\n"
    if marker in draft:
        return draft.replace(marker, marker + "\n".join(lines) + "\n", 1)
    return draft.replace("STATIC:\n", "STATIC:\n" + "\n".join(lines) + "\n", 1)


def _insert_internal_profile_selection(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
) -> str:
    if "Select active internal-node profile" in draft:
        return draft
    lines = [
        "    /* Select active internal-node profile; backend placeholders are pruned before Schur matrix setup. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in profiles:
        for case_id in profile.get("case_ids") or []:
            lines.append(f"    case {int(case_id)}:")
        profile_index = int(str(profile.get("profile_id") or "INTERNAL_CASE_0").rsplit("_", 1)[-1])
        lines.extend([
            f"        internal_profile = INTERNAL_CASE_{profile_index};",
            f"        internal_active = INTERNAL_NODES_CASE_{profile_index};",
            "        break;",
        ])
    default_index = _internal_default_profile_index(profiles)
    lines.extend([
        "    default:",
        f"        internal_profile = INTERNAL_CASE_{default_index};",
        f"        internal_active = INTERNAL_NODES_CASE_{default_index};",
        "        break;",
        "    }",
    ])
    marker = "    int err = 0;\n"
    if marker in draft:
        return draft.replace(marker, "\n".join(lines) + "\n" + marker, 1)
    return draft.replace("RAM_PASS1:\n", "RAM_PASS1:\n" + "\n".join(lines) + "\n", 1)


def _apply_internal_active_matrix_dimensions(draft: str) -> str:
    replacements = {
        "matrixDim(&Grk_code, RETAINED_NODES, INTERNAL_NODES);": "matrixDim(&Grk_code, RETAINED_NODES, internal_active);",
        "matrixDim(&Gkr_code, INTERNAL_NODES, RETAINED_NODES);": "matrixDim(&Gkr_code, internal_active, RETAINED_NODES);",
        "matrixDim(&Gkk_code, INTERNAL_NODES, INTERNAL_NODES);": "matrixDim(&Gkk_code, internal_active, internal_active);",
        "matrixDim(&W_code, INTERNAL_NODES, INTERNAL_NODES);": "matrixDim(&W_code, internal_active, internal_active);",
        "matrixDim(&Ihisk_code, INTERNAL_NODES, 1);": "matrixDim(&Ihisk_code, internal_active, 1);",
        "matrixDim(&Vk_code, INTERNAL_NODES, 1);": "matrixDim(&Vk_code, internal_active, 1);",
        "matrixDim(&tmp_Grk_W_code, RETAINED_NODES, INTERNAL_NODES);": "matrixDim(&tmp_Grk_W_code, RETAINED_NODES, internal_active);",
        "matrixDim(&tmp_W_Gkr_code, INTERNAL_NODES, RETAINED_NODES);": "matrixDim(&tmp_W_Gkr_code, internal_active, RETAINED_NODES);",
        "matrixDim(&tmp_W_Gkr_Vr_code, INTERNAL_NODES, 1);": "matrixDim(&tmp_W_Gkr_Vr_code, internal_active, 1);",
        "matrixDim(&tmp_W_Ihisk_code, INTERNAL_NODES, 1);": "matrixDim(&tmp_W_Ihisk_code, internal_active, 1);",
        "matrixDim(&tmp_Vk_sum_code, INTERNAL_NODES, 1);": "matrixDim(&tmp_Vk_sum_code, internal_active, 1);",
    }
    for old, new in replacements.items():
        draft = draft.replace(old, new)
    return _guard_internal_active_matrix_lifecycle(draft)


_INTERNAL_ACTIVE_MATRIX_OBJECTS = {
    "Grk_code",
    "Gkr_code",
    "Gkk_code",
    "W_code",
    "Ihisk_code",
    "Vk_code",
    "tmp_Grk_W_code",
    "tmp_Grk_W_Ihisk_code",
    "tmp_W_Gkr_code",
    "tmp_W_Gkr_Vr_code",
    "tmp_W_Ihisk_code",
    "tmp_Vk_sum_code",
}


def _line_touches_internal_active_matrix(line: str, verb: str) -> bool:
    for name in _INTERNAL_ACTIVE_MATRIX_OBJECTS:
        if f"{verb}(&{name}" in line:
            return True
    return False


def _guard_internal_active_lines(draft: str, *, verb: str) -> str:
    lines = draft.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _line_touches_internal_active_matrix(line, verb):
            indent = line[: len(line) - len(line.lstrip(" "))]
            if out and out[-1].strip() == "if (internal_active > 0) {":
                out.append(line)
                index += 1
                continue
            out.append(f"{indent}if (internal_active > 0) {{")
            while index < len(lines) and _line_touches_internal_active_matrix(lines[index], verb):
                out.append("    " + lines[index])
                index += 1
            out.append(f"{indent}}}")
            continue
        out.append(line)
        index += 1
    return "\n".join(out) + ("\n" if draft.endswith("\n") else "")


def _guard_internal_active_matrix_lifecycle(draft: str) -> str:
    for verb in ("matrixDim", "matrix_register", "conditionMatrixForCODE"):
        draft = _guard_internal_active_lines(draft, verb=verb)
    return draft


_LARGE_RECOVERY_MATRIX_OBJECTS = {
    "tmp_W_Gkr_Vr_code",
    "tmp_W_Ihisk_code",
    "tmp_Vk_sum_code",
}


def _line_touches_large_recovery_matrix(line: str) -> bool:
    return any(f"&{name}" in line for name in _LARGE_RECOVERY_MATRIX_OBJECTS)


def _split_large_recovery_matrix_lifecycle(draft: str) -> str:
    lines = draft.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() != "if (internal_active > 0) {":
            out.append(line)
            index += 1
            continue
        block_start = index
        block_lines: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip() != "}":
            block_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            out.extend(lines[block_start:])
            break
        index += 1
        if not any(_line_touches_large_recovery_matrix(item) for item in block_lines):
            out.append(line)
            out.extend(block_lines)
            out.append(f"{line[: len(line) - len(line.lstrip(' '))]}}}")
            continue
        regular_lines = [item for item in block_lines if not _line_touches_large_recovery_matrix(item)]
        large_lines = [item for item in block_lines if _line_touches_large_recovery_matrix(item)]
        indent = line[: len(line) - len(line.lstrip(" "))]
        if regular_lines:
            out.append(line)
            out.extend(regular_lines)
            out.append(f"{indent}}}")
        if large_lines:
            out.append(f"{indent}if (internal_active == 2) {{")
            out.extend(large_lines)
            out.append(f"{indent}}}")
    if draft.endswith("\n"):
        return "\n".join(out) + "\n"
    return "\n".join(out)


def _guard_internal_active_ihis_schur(draft: str) -> str:
    if "internal_active" not in draft or "No active internal nodes: Ihisred = Ihisr." in draft:
        return draft
    old_with_grkw_refresh = "\n".join([
        "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
        "    /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
        "    matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
        "    matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
    ])
    old_precomputed_grkw = "\n".join([
        "    /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
        "    matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
        "    matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
    ])
    if old_with_grkw_refresh in draft:
        old = old_with_grkw_refresh
        active_lines = [
            "        matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
            "        /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
            "        matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
            "        matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
        ]
    elif old_precomputed_grkw in draft:
        old = old_precomputed_grkw
        active_lines = [
            "        /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
            "        matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
            "        matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
        ]
    else:
        return draft
    new = "\n".join([
        "    if (internal_active > 0) {",
        *active_lines,
        "    }",
        "    else {",
        "        int row;",
        "        /* No active internal nodes: Ihisred = Ihisr. */",
        "        for (row = 0; row < RETAINED_NODES; row++) {",
        "            set_CODE(&Ihisred_code, row, 0, get_CODE(&Ihisr_code, row, 0));",
        "        }",
        "    }",
    ])
    return draft.replace(old, new, 1)


def _replace_internal_profile_ihis_schur(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
) -> str:
    old_with_grkw_refresh = "\n".join([
        "    if (internal_active > 0) {",
        "        matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
        "        /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
        "        matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
        "        matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
        "    }",
        "    else {",
        "        int row;",
        "        /* No active internal nodes: Ihisred = Ihisr. */",
        "        for (row = 0; row < RETAINED_NODES; row++) {",
        "            set_CODE(&Ihisred_code, row, 0, get_CODE(&Ihisr_code, row, 0));",
        "        }",
        "    }",
    ])
    old_precomputed_grkw = "\n".join([
        "    if (internal_active > 0) {",
        "        /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
        "        matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
        "        matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
        "    }",
        "    else {",
        "        int row;",
        "        /* No active internal nodes: Ihisred = Ihisr. */",
        "        for (row = 0; row < RETAINED_NODES; row++) {",
        "            set_CODE(&Ihisred_code, row, 0, get_CODE(&Ihisr_code, row, 0));",
        "        }",
        "    }",
    ])
    if old_with_grkw_refresh in draft:
        old = old_with_grkw_refresh
        grkw_precomputed = False
    elif old_precomputed_grkw in draft:
        old = old_precomputed_grkw
        grkw_precomputed = True
    else:
        return draft
    lines = [
        "    /* Case-resolved Ihis Schur update over active internal profile. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in profiles:
        case_ids = _ram_profile_case_ids(profile)
        if not case_ids:
            continue
        for case_id in case_ids:
            lines.append(f"    case {case_id}:")
        active_count = int(profile.get("active_internal_count") or 0)
        lines.append("    {")
        if active_count == 0:
            lines.extend([
                "        int row;",
                "        /* No active internal nodes: Ihisred = Ihisr. */",
                "        for (row = 0; row < RETAINED_NODES; row++) {",
                "            set_CODE(&Ihisred_code, row, 0, get_CODE(&Ihisr_code, row, 0));",
                "        }",
            ])
        elif active_count == 1:
            if grkw_precomputed:
                lines.extend([
                    "        int row;",
                    "        double grkw = 0.0;",
                    "        double h0 = get_CODE(&Ihisk_code, 0, 0);",
                    "        for (row = 0; row < RETAINED_NODES; row++) {",
                    "            grkw = get_CODE(&tmp_Grk_W_code, row, 0);",
                    "            set_CODE(&Ihisred_code, row, 0,",
                    "                     get_CODE(&Ihisr_code, row, 0) - grkw * h0);",
                    "        }",
                ])
            else:
                lines.extend([
                    "        int row;",
                    "        double grk = 0.0;",
                    "        double inv0 = get_CODE(&W_code, 0, 0);",
                    "        double t0 = inv0 * get_CODE(&Ihisk_code, 0, 0);",
                    "        for (row = 0; row < RETAINED_NODES; row++) {",
                    "            grk = get_CODE(&Grk_code, row, 0);",
                    "            set_CODE(&tmp_Grk_W_code, row, 0, grk * inv0);",
                    "            set_CODE(&Ihisred_code, row, 0,",
                    "                     get_CODE(&Ihisr_code, row, 0) - grk * t0);",
                    "        }",
                ])
        else:
            if not grkw_precomputed:
                lines.append("        matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);")
            lines.extend([
                "        /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
                "        matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
                "        matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
            ])
        lines.extend([
            "        break;",
            "    }",
        ])
    lines.extend([
        "    default:",
        "        break;",
        "    }",
    ])
    return draft.replace(old, "\n".join(lines), 1)


_SET_CODE_RE = re.compile(
    r"    set_CODE\(&(?P<name>Grk_code|Gkr_code|Gkk_code|Ihisk_code), "
    r"(?P<row>\d+), (?P<col>\d+), (?P<expr>[^;]+)\);\n"
)

_SET_RAM_INTERNAL_RE = re.compile(
    r"    set\(&(?P<name>Grk_code|Gkr_code|Gkk_code|Grk_ram|Gkr_ram|Gkk_ram), "
    r"(?P<row>\d+), (?P<col>\d+), (?P<expr>[^;]+)\);\n"
)


def _remap_internal_set_code_line(
    name: str,
    row: int,
    col: int,
    expr: str,
    active_index: Mapping[str, int],
    template_internal_nodes: Sequence[str],
    *,
    retained_count: int,
) -> str | None:
    node_to_global = {node: index for index, node in enumerate(template_internal_nodes)}
    global_to_active = {
        node_to_global[node]: int(active)
        for node, active in active_index.items()
        if node in node_to_global
    }
    if name == "Grk_code":
        if col not in global_to_active:
            return None
        return f"        set_CODE(&Grk_code, {row}, {global_to_active[col]}, {expr});"
    if name == "Gkr_code":
        if row not in global_to_active:
            return None
        return f"        set_CODE(&Gkr_code, {global_to_active[row]}, {col}, {expr});"
    if name == "Gkk_code":
        if row not in global_to_active or col not in global_to_active:
            return None
        return f"        set_CODE(&Gkk_code, {global_to_active[row]}, {global_to_active[col]}, {expr});"
    if name == "Ihisk_code":
        if row not in global_to_active:
            return None
        return f"        set_CODE(&Ihisk_code, {global_to_active[row]}, {col}, {expr});"
    return None


def _remap_internal_set_ram_line(
    name: str,
    row: int,
    col: int,
    expr: str,
    active_index: Mapping[str, int],
    template_internal_nodes: Sequence[str],
    *,
    retained_count: int,
) -> str | None:
    node_to_global = {node: index for index, node in enumerate(template_internal_nodes)}
    global_to_active = {
        node_to_global[node]: int(active)
        for node, active in active_index.items()
        if node in node_to_global
    }
    if name == "Grk_code":
        if col not in global_to_active:
            return None
        return f"        set(&Grk_code, {row}, {global_to_active[col]}, {expr});"
    if name == "Gkr_code":
        if row not in global_to_active:
            return None
        return f"        set(&Gkr_code, {global_to_active[row]}, {col}, {expr});"
    if name == "Gkk_code":
        if row not in global_to_active or col not in global_to_active:
            return None
        return f"        set(&Gkk_code, {global_to_active[row]}, {global_to_active[col]}, {expr});"
    return None


def _active_internal_entry_expr(
    entries: Sequence[tuple[str, int, int, str]],
    name: str,
    *,
    active_row: int,
    active_col: int,
    active_index: Mapping[str, int],
    template_internal_nodes: Sequence[str],
) -> str | None:
    node_to_global = {node: index for index, node in enumerate(template_internal_nodes)}
    global_to_active = {
        node_to_global[node]: int(active)
        for node, active in active_index.items()
        if node in node_to_global
    }
    for entry_name, row, col, expr in entries:
        if entry_name != name:
            continue
        mapped_row = global_to_active.get(row) if name in {"Gkr_code", "Gkk_code"} else row
        mapped_col = global_to_active.get(col) if name in {"Grk_code", "Gkk_code"} else col
        if mapped_row == active_row and mapped_col == active_col:
            return expr
    return None


def _remove_unused_matrix_object(draft: str, name: str) -> str:
    name_re = re.compile(rf"\b{re.escape(name)}\b")
    removable_patterns = [
        re.compile(rf"^\s*MATRIX_\s+{re.escape(name)}\s*=\s*\{{0\}};\s*$"),
        re.compile(rf"^\s*err\s*\+=\s*matrixDim\(&{re.escape(name)}, [^;]+\);\s*$"),
        re.compile(rf"^\s*matrix_register\(&{re.escape(name)}\);\s*$"),
        re.compile(rf"^\s*conditionMatrixForCODE\(&{re.escape(name)}\);\s*$"),
    ]

    def is_removable(line: str) -> bool:
        return any(pattern.match(line) for pattern in removable_patterns)

    touched_lines = [line for line in draft.splitlines() if name_re.search(line)]
    if any(not is_removable(line) for line in touched_lines):
        return draft
    lines = [line for line in draft.splitlines() if not (name_re.search(line) and is_removable(line))]
    return "\n".join(lines) + ("\n" if draft.endswith("\n") else "")


def _replace_ram_fixed_internal_matrix_precompute(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    template_internal_nodes: Sequence[str],
    retained_count: int,
) -> str:
    marker = "    /* ************************************************************************\n     * RAM-SIDE FIXED G MATRIX PRECOMPUTE"
    start = draft.find(marker)
    if start < 0:
        return draft
    lifecycle_match = re.search(
        r"\n    (?:if \(internal_active > 0\) \{\n        matrix_register\(|matrix_register\()",
        draft[start:],
    )
    if lifecycle_match is None:
        return draft
    end = start + lifecycle_match.start() + 1
    block = draft[start:end]
    entry_matches = list(_SET_RAM_INTERNAL_RE.finditer(block))
    if not entry_matches:
        entry_matches = list(_SET_RAM_INTERNAL_RE.finditer(draft[:start]))
    entries = []
    for match in entry_matches:
        name = match.group("name").replace("_ram", "_code")
        entries.append(
            (
                name,
                int(match.group("row")),
                int(match.group("col")),
                match.group("expr"),
            )
        )
    if not entries:
        return draft
    needs_tmp_w_gkr = "tmp_W_Gkr_code" in block
    has_ram_schur_scratch = all(
        marker in draft
        for marker in (
            "MATRIX_ W_ram",
            "MATRIX_ tmp_Grk_W_ram",
            "matrix_mult(&tmp_Grk_W_ram",
        )
    )
    has_code_schur_scratch = "matrix_mult(&tmp_Grk_W_code, &Grk_ram, &W_code)" in draft
    lines = [
        "    /* ************************************************************************",
        "     * RAM-SIDE FIXED G MATRIX PRECOMPUTE",
        "     * These G-related symbols and matrices depend only on RAM-known data.",
        "     * Prepare active-profile matrices in RAM; CODE only conditions pointers.",
        "     * ************************************************************************ */",
        "",
        "",
        "",
        "    /* Active internal profile fixed-G matrix setup; backend placeholder rows are not written. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in profiles:
        for case_id in profile.get("case_ids") or []:
            lines.append(f"    case {int(case_id)}:")
        active_index = profile.get("internal_to_active_index") or {}
        active_count = len(active_index)
        if active_count == 0:
            lines.append("        /* This Pack case has no active internal nodes. */")
            lines.append("        break;")
            continue
        emitted: list[str] = []
        seen: set[str] = set()
        for name, row, col, expr in entries:
            remapped = _remap_internal_set_ram_line(
                name,
                row,
                col,
                expr,
                active_index,
                template_internal_nodes,
                retained_count=retained_count,
            )
            if remapped and remapped not in seen:
                emitted.append(remapped)
                seen.add(remapped)
        if active_count > 1 and not (has_ram_schur_scratch or has_code_schur_scratch):
            lines.extend(emitted)
        if active_count == 1:
            gkk_expr = _active_internal_entry_expr(
                entries,
                "Gkk_code",
                active_row=0,
                active_col=0,
                active_index=active_index,
                template_internal_nodes=template_internal_nodes,
            )
            if not gkk_expr:
                return draft
            lines.append(f"        set(&W_code, 0, 0, 1.0 / ({gkk_expr}));")
            for retained_row in range(retained_count):
                grk_expr = _active_internal_entry_expr(
                    entries,
                    "Grk_code",
                    active_row=retained_row,
                    active_col=0,
                    active_index=active_index,
                    template_internal_nodes=template_internal_nodes,
                )
                if grk_expr:
                    lines.append(
                        f"        set(&tmp_Grk_W_code, {retained_row}, 0, ({grk_expr}) * get(&W_code, 0, 0));"
                    )
                else:
                    lines.append(f"        set(&tmp_Grk_W_code, {retained_row}, 0, 0.0);")
        elif has_code_schur_scratch:
            lines.append(
                "        /* W_code and tmp_Grk_W_code were computed during the RAM Gred Schur precompute. */"
            )
        elif has_ram_schur_scratch:
            lines.extend(
                [
                    "        /* Reuse RAM Schur scratch: W_ram and tmp_Grk_W_ram are already active-profile sized. */",
                    "        for (row = 0; row < internal_active; row++) {",
                    "            for (col = 0; col < internal_active; col++) {",
                    "                set(&W_code, row, col, get(&W_ram, row, col));",
                    "            }",
                    "        }",
                    f"        for (row = 0; row < {retained_count}; row++) {{",
                    "            for (col = 0; col < internal_active; col++) {",
                    "                set(&tmp_Grk_W_code, row, col, get(&tmp_Grk_W_ram, row, col));",
                    "            }",
                    "        }",
                ]
            )
        else:
            lines.append("        err += matrix_invert(&W_code, &Gkk_code);")
            lines.append("        err += matrix_mult(&tmp_Grk_W_code, &Grk_code, &W_code);")
        if needs_tmp_w_gkr and active_count > 1:
            lines.extend(
                [
                    "        /* Symmetry reuse: W * Gkr = transpose(Grk * W). */",
                    "        for (row = 0; row < internal_active; row++) {",
                    f"            for (col = 0; col < {retained_count}; col++) {{",
                    "                set(&tmp_W_Gkr_code, row, col, get(&tmp_Grk_W_code, col, row));",
                    "            }",
                    "        }",
                ]
            )
        lines.append("        break;")
    lines.extend(
        [
            "    default:",
            "        break;",
            "    }",
            "    if (err > 0) {",
            '        reportError_RW("network_node", STOP_IMMEDIATELY_CONDITION,',
            '                       "RTDS RAM active-profile fixed-G precompute failed for component %s.", Name);',
            "    }",
            "",
        ]
    )
    updated = draft[:start] + "\n".join(lines) + "\n" + draft[end:]
    if has_ram_schur_scratch or has_code_schur_scratch:
        for matrix_name in ("Grk_code", "Gkr_code", "Gkk_code"):
            updated = _remove_unused_matrix_object(updated, matrix_name)
    if has_code_schur_scratch:
        for matrix_name in ("W_ram", "tmp_Grk_W_ram"):
            updated = _remove_unused_matrix_object(updated, matrix_name)
    return updated


def _replace_internal_matrix_set_code_blocks(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    template_internal_nodes: Sequence[str],
    retained_count: int,
) -> str:
    matches = list(_SET_CODE_RE.finditer(draft))
    if not matches:
        return draft
    matrix_entries = [
        (
            match.group("name"),
            int(match.group("row")),
            int(match.group("col")),
            match.group("expr"),
        )
        for match in matches
    ]
    g_entries = [entry for entry in matrix_entries if entry[0] != "Ihisk_code"]
    ihisk_entries = [entry for entry in matrix_entries if entry[0] == "Ihisk_code"]

    def build_switch(entries: Sequence[tuple[str, int, int, str]], title: str) -> str:
        lines = [
            f"    /* {title}; backend placeholder rows are not written. */",
            f"    switch ({case_id_symbol}) {{",
        ]
        for profile in profiles:
            for case_id in profile.get("case_ids") or []:
                lines.append(f"    case {int(case_id)}:")
            active_index = profile.get("internal_to_active_index") or {}
            if not active_index:
                lines.append("        /* This Pack case has no active internal nodes. */")
            else:
                emitted: list[str] = []
                for name, row, col, expr in entries:
                    line = _remap_internal_set_code_line(
                        name,
                        row,
                        col,
                        expr,
                        active_index,
                        template_internal_nodes,
                        retained_count=retained_count,
                    )
                    if line is not None:
                        emitted.append(line)
                lines.extend(dict.fromkeys(emitted) or ["        /* This Pack case has no active entries for this matrix. */"])
            lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
        ])
        return "\n".join(lines) + "\n"

    g_insertion = build_switch(g_entries, "Active internal profile G matrix setup") if g_entries else ""
    ihisk_insertion = build_switch(ihisk_entries, "Active internal profile Ihisk setup") if ihisk_entries else ""
    parts: list[str] = []
    last = 0
    inserted = False
    for match in matches:
        parts.append(draft[last:match.start()])
        if not inserted and g_insertion:
            parts.append(g_insertion)
            inserted = True
        last = match.end()
    parts.append(draft[last:])
    draft = "".join(parts)
    if ihisk_insertion:
        marker = "    /* ************************************************************************\n     * CODE-SIDE IHIS VALUE SETUP"
        start = draft.find(marker)
        if start >= 0:
            draft = draft[:start] + ihisk_insertion + draft[start:]
    return draft


def _active_gkk_template(
    gkk_template: sp.Matrix,
    profile: Mapping,
    template_internal_nodes: Sequence[str],
) -> sp.Matrix:
    node_to_row = {str(node): index for index, node in enumerate(template_internal_nodes)}
    rows = [
        node_to_row[str(node)]
        for node in (profile.get("ordered_active_internal_nodes") or [])
        if str(node) in node_to_row
    ]
    if not rows:
        return sp.zeros(0, 0)
    return sp.Matrix(gkk_template).extract(rows, rows)


def _w_inverse_lines_for_internal_profile(
    *,
    profile: Mapping,
    profile_index: int,
    gkk_template: sp.Matrix,
    case_profile: Mapping,
    aliases: Mapping[str, Mapping],
    template_internal_nodes: Sequence[str],
    indent: int = 8,
) -> list[str]:
    active_count = int(profile.get("active_internal_count") or 0)
    prefix = " " * indent
    if active_count == 0:
        return [f"{prefix}/* This Pack case has no active internal nodes. */"]
    active_gkk = _active_gkk_template(gkk_template, profile, template_internal_nodes)
    if _case_resolved_matrix_is_diagonal(active_gkk, case_profile, aliases, profile_index):
        return _diagonal_w_code_lines(active_count, indent)
    if active_count == 1:
        return [f"{prefix}set_CODE(&W_code, 0, 0, 1.0 / get_CODE(&Gkk_code, 0, 0));"]
    if active_count == 2:
        return [
            f"{prefix}double W_code_11 = 0.0;",
            f"{prefix}double W_code_12 = 0.0;",
            f"{prefix}double W_code_22 = 0.0;",
            f"{prefix}mat_2x2_sym_inv_code(get_CODE(&Gkk_code, 0, 0), get_CODE(&Gkk_code, 0, 1), get_CODE(&Gkk_code, 1, 1),",
            f"{prefix}                     &W_code_11, &W_code_12, &W_code_22);",
            f"{prefix}set_CODE(&W_code, 0, 0, W_code_11);",
            f"{prefix}set_CODE(&W_code, 0, 1, W_code_12);",
            f"{prefix}set_CODE(&W_code, 1, 0, W_code_12);",
            f"{prefix}set_CODE(&W_code, 1, 1, W_code_22);",
        ]
    if active_count == 3:
        return [
            f"{prefix}double W_code_11 = 0.0;",
            f"{prefix}double W_code_12 = 0.0;",
            f"{prefix}double W_code_13 = 0.0;",
            f"{prefix}double W_code_22 = 0.0;",
            f"{prefix}double W_code_23 = 0.0;",
            f"{prefix}double W_code_33 = 0.0;",
            f"{prefix}mat_3x3_sym_inv_code(get_CODE(&Gkk_code, 0, 0), get_CODE(&Gkk_code, 0, 1), get_CODE(&Gkk_code, 0, 2),",
            f"{prefix}                     get_CODE(&Gkk_code, 1, 1), get_CODE(&Gkk_code, 1, 2), get_CODE(&Gkk_code, 2, 2),",
            f"{prefix}                     &W_code_11, &W_code_12, &W_code_13, &W_code_22, &W_code_23, &W_code_33);",
            f"{prefix}set_CODE(&W_code, 0, 0, W_code_11);",
            f"{prefix}set_CODE(&W_code, 0, 1, W_code_12);",
            f"{prefix}set_CODE(&W_code, 0, 2, W_code_13);",
            f"{prefix}set_CODE(&W_code, 1, 0, W_code_12);",
            f"{prefix}set_CODE(&W_code, 1, 1, W_code_22);",
            f"{prefix}set_CODE(&W_code, 1, 2, W_code_23);",
            f"{prefix}set_CODE(&W_code, 2, 0, W_code_13);",
            f"{prefix}set_CODE(&W_code, 2, 1, W_code_23);",
            f"{prefix}set_CODE(&W_code, 2, 2, W_code_33);",
        ]
    return [f"{prefix}MATH_matx_invert(&W_code, &Gkk_code);"]


def _replace_internal_profile_w_inverse(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    case_profiles: Sequence[Mapping],
    aliases: Mapping[str, Mapping],
    gkk_template: sp.Matrix,
    template_internal_nodes: Sequence[str],
) -> str:
    marker = "    /* Case-resolved Gkk inverse"
    start = draft.find(marker)
    if start < 0:
        return draft
    end_marker = "    /* ************************************************************************\n     * CODE-SIDE IHIS VALUE SETUP"
    end = draft.find(end_marker, start)
    ready_marker = "        rtds_matrix_code_ready = 1;"
    ready_end = draft.find(ready_marker, start)
    if ready_end >= 0 and (end < 0 or ready_end < end):
        end = ready_end
    if end < 0:
        for fallback_marker in (
            "    /* Node injection currents follow the retained-node order of the reduced system. */",
            "\nT1_T2:",
        ):
            end = draft.find(fallback_marker, start)
            if end >= 0:
                break
    if end < 0:
        return draft
    original_region = draft[start:end]
    case_by_id = {
        int(profile.get("case_id", index) or index): profile
        for index, profile in enumerate(case_profiles)
    }
    lines = [
        "    /* Case-resolved Gkk inverse over active internal profile; backend placeholders are pruned. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in profiles:
        profile_cases = [int(case_id) for case_id in (profile.get("case_ids") or [])]
        for case_id in profile_cases:
            lines.append(f"    case {case_id}:")
        representative = profile_cases[0] if profile_cases else 0
        lines.append("    {")
        lines.extend(
            _w_inverse_lines_for_internal_profile(
                profile=profile,
                profile_index=representative,
                gkk_template=gkk_template,
                case_profile=case_by_id.get(representative, {}),
                aliases=aliases,
                template_internal_nodes=template_internal_nodes,
                indent=8,
            )
        )
        lines.append("        break;")
        lines.append("    }")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
    ])
    if "matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);" in original_region:
        lines.extend([
            "    if (internal_active > 0) {",
            "        matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
            "    }",
        ])
    if "matrix_mult_CODE(&tmp_W_Gkr_code, &W_code, &Gkr_code);" in original_region:
        lines.extend([
            "    if (internal_active == 2) {",
            "        matrix_mult_CODE(&tmp_W_Gkr_code, &W_code, &Gkr_code);",
            "    }",
        ])
    lines.append("")
    return draft[:start] + "\n".join(lines) + draft[end:]


_SET_RAM_RE = re.compile(
    r"    set\(&(?P<name>Grr_ram|Grk_ram|Gkr_ram|Gkk_ram), "
    r"(?P<row>\d+), (?P<col>\d+), (?P<expr>[^;]+)\);\n"
)
_GRED_RAM_ASSIGN_RE = re.compile(
    r"    (?P<lhs>g_mat_over\[\d+\]\[\d+\]) = get\(&Gred_ram, "
    r"(?P<row>\d+), (?P<col>\d+)\);\n"
)


def _ram_profile_case_ids(profile: Mapping) -> list[int]:
    return [int(case_id) for case_id in (profile.get("case_ids") or [])]


def _ram_active_global_rows(profile: Mapping, template_internal_nodes: Sequence[str]) -> list[int]:
    node_to_row = {str(node): index for index, node in enumerate(template_internal_nodes)}
    return [
        node_to_row[str(node)]
        for node in (profile.get("ordered_active_internal_nodes") or [])
        if str(node) in node_to_row
    ]


def _ram_entry_map(matches: Sequence[re.Match]) -> dict[str, dict[tuple[int, int], str]]:
    entries: dict[str, dict[tuple[int, int], str]] = {
        "Grr_ram": {},
        "Grk_ram": {},
        "Gkr_ram": {},
        "Gkk_ram": {},
    }
    for match in matches:
        entries[match.group("name")][
            (int(match.group("row")), int(match.group("col")))
        ] = match.group("expr")
    return entries


def _ram_precompute_alias_lines(region: str) -> list[str]:
    kept: list[str] = []
    for line in region.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/*") and " represents " in stripped:
            kept.append(line)
            continue
        if re.match(r"^[A-Za-z_]\w*\s*=", stripped):
            kept.append(line)
    return kept


def _ram_base_expr(entries: Mapping[str, Mapping[tuple[int, int], str]], row: int, col: int) -> str:
    return entries.get("Grr_ram", {}).get((row, col), "0.0")


def _ram_schur_1x1_expr(
    entries: Mapping[str, Mapping[tuple[int, int], str]],
    row: int,
    col: int,
    active_global: int,
    inv_name: str = "inv0",
) -> str:
    base = _ram_base_expr(entries, row, col)
    grk = entries.get("Grk_ram", {}).get((row, active_global), "0.0")
    gkr = entries.get("Gkr_ram", {}).get(
        (active_global, col),
        entries.get("Grk_ram", {}).get((col, active_global), "0.0"),
    )
    if grk == "0.0" or gkr == "0.0":
        return base
    return f"{base} - ({grk})*{inv_name}*({gkr})"


def _ram_remapped_set_lines(
    entries: Mapping[str, Mapping[tuple[int, int], str]],
    active_rows: Sequence[int],
    *,
    include_gkr: bool = True,
) -> list[str]:
    active_index = {global_row: active_row for active_row, global_row in enumerate(active_rows)}
    lines: list[str] = []
    for (row, col), expr in sorted(entries.get("Grr_ram", {}).items()):
        lines.append(f"        set(&Grr_ram, {row}, {col}, {expr});")
    for (row, col), expr in sorted(entries.get("Grk_ram", {}).items()):
        if col in active_index:
            lines.append(f"        set(&Grk_ram, {row}, {active_index[col]}, {expr});")
    if include_gkr:
        for (row, col), expr in sorted(entries.get("Gkr_ram", {}).items()):
            if row in active_index:
                lines.append(f"        set(&Gkr_ram, {active_index[row]}, {col}, {expr});")
    for (row, col), expr in sorted(entries.get("Gkk_ram", {}).items()):
        if row in active_index and col in active_index:
            lines.append(f"        set(&Gkk_ram, {active_index[row]}, {active_index[col]}, {expr});")
    return lines


def _replace_ram_gred_precompute_for_internal_profiles(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    template_internal_nodes: Sequence[str],
) -> str:
    marker = "    /* RAM-side matrix Schur precompute for fixed Gred stamp. */"
    start = draft.find(marker)
    if start < 0:
        return draft
    end = draft.find("    setupGMatrix", start)
    if end < 0:
        return draft
    region = draft[start:end]
    set_matches = list(_SET_RAM_RE.finditer(region))
    gred_assign_matches = list(_GRED_RAM_ASSIGN_RE.finditer(region))
    if not set_matches or not gred_assign_matches:
        return draft

    entries = _ram_entry_map(set_matches)
    use_grk_transpose_for_gkr = not bool(entries.get("Gkr_ram"))
    gred_assignments = [
        (match.group("lhs"), int(match.group("row")), int(match.group("col")))
        for match in gred_assign_matches
    ]
    use_code_schur_scratch = "MATRIX_ W_code" in draft and "MATRIX_ tmp_Grk_W_code" in draft
    lines = [
        "    /* RAM-side active-profile Schur precompute for fixed Gred stamp. */",
        *_ram_precompute_alias_lines(region),
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in profiles:
        case_ids = _ram_profile_case_ids(profile)
        if not case_ids:
            continue
        for case_id in case_ids:
            lines.append(f"    case {case_id}:")
        active_rows = _ram_active_global_rows(profile, template_internal_nodes)
        active_count = len(active_rows)
        lines.append("    {")
        if active_count == 0:
            lines.append("        /* This Pack case has no active internal nodes. */")
            for lhs, row, col in gred_assignments:
                lines.append(f"        {lhs} = {_ram_base_expr(entries, row, col)};")
        elif active_count == 1:
            if use_code_schur_scratch:
                lines.append("        err += matrixDim(&W_code, internal_active, internal_active);")
                lines.append("        err += matrixDim(&tmp_Grk_W_code, RETAINED_NODES, internal_active);")
            active_global = active_rows[0]
            gkk = entries.get("Gkk_ram", {}).get((active_global, active_global), "0.0")
            lines.append(f"        double inv0 = 1.0 / ({gkk});")
            for lhs, row, col in gred_assignments:
                lines.append(f"        {lhs} = {_ram_schur_1x1_expr(entries, row, col, active_global)};")
        else:
            w_target = "W_code" if use_code_schur_scratch else "W_ram"
            tmp_grk_w_target = "tmp_Grk_W_code" if use_code_schur_scratch else "tmp_Grk_W_ram"
            lines.extend([
                "        err += matrixDim(&Grr_ram, RETAINED_NODES, RETAINED_NODES);",
                "        err += matrixDim(&Grk_ram, RETAINED_NODES, internal_active);",
                *(["        err += matrixDim(&Gkr_ram, internal_active, RETAINED_NODES);"] if not use_grk_transpose_for_gkr else []),
                "        err += matrixDim(&Gkk_ram, internal_active, internal_active);",
                f"        err += matrixDim(&{w_target}, internal_active, internal_active);",
                "        err += matrixDim(&Gred_ram, RETAINED_NODES, RETAINED_NODES);",
                f"        err += matrixDim(&{tmp_grk_w_target}, RETAINED_NODES, internal_active);",
                "        err += matrixDim(&tmp_Grk_W_Gkr_ram, RETAINED_NODES, RETAINED_NODES);",
                "        if (err > 0) {",
                '            reportError_RW("network_node", STOP_IMMEDIATELY_CONDITION,',
                '                           "RTDS RAM active-profile matrix allocation failed for component %s.", Name);',
                "        }",
            ])
            lines.extend(_ram_remapped_set_lines(entries, active_rows, include_gkr=not use_grk_transpose_for_gkr))
            lines.extend([
                f"        err += matrix_invert(&{w_target}, &Gkk_ram);",
                f"        err += matrix_mult(&{tmp_grk_w_target}, &Grk_ram, &{w_target});",
            ])
            if use_grk_transpose_for_gkr:
                lines.extend([
                    "        /* Symmetry reuse: multiply by transpose(Grk_ram) without materializing Gkr_ram. */",
                    "        for (row = 0; row < RETAINED_NODES; row++) {",
                    "            for (col = row; col < RETAINED_NODES; col++) {",
                    "                double acc = 0.0;",
                    "                int k;",
                    "                for (k = 0; k < internal_active; k++) {",
                    f"                    acc += get(&{tmp_grk_w_target}, row, k) * get(&Grk_ram, col, k);",
                    "                }",
                    "                set(&tmp_Grk_W_Gkr_ram, row, col, acc);",
                    "                if (col != row) {",
                    "                    set(&tmp_Grk_W_Gkr_ram, col, row, acc);",
                    "                }",
                    "            }",
                    "        }",
                ])
            else:
                lines.append(f"        err += matrix_mult(&tmp_Grk_W_Gkr_ram, &{tmp_grk_w_target}, &Gkr_ram);")
            lines.extend([
                "        err += matrix_subtract(&Gred_ram, &Grr_ram, &tmp_Grk_W_Gkr_ram);",
            ])
            for lhs, row, col in gred_assignments:
                lines.append(f"        {lhs} = get(&Gred_ram, {row}, {col});")
        lines.append("        break;")
        lines.append("    }")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
        "    if (err > 0) {",
        '        reportError_RW("network_node", STOP_IMMEDIATELY_CONDITION,',
        '                       "RTDS RAM active-profile Schur precompute failed for component %s.", Name);',
        "    }",
    ])
    updated = draft[:start] + "\n".join(lines) + "\n" + draft[end:]
    if use_code_schur_scratch:
        setup_index = updated.find("    setupGMatrix", start)
        if setup_index >= 0:
            head = updated[:setup_index]
            tail = updated[setup_index:]
            for duplicate_dim in (
                "        err += matrixDim(&W_code, internal_active, internal_active);\n",
                "        err += matrixDim(&tmp_Grk_W_code, RETAINED_NODES, internal_active);\n",
            ):
                tail = tail.replace(duplicate_dim, "", 1)
            updated = head + tail
        updated = updated.replace("    if (internal_active > 0) {\n    }\n", "")
    return updated


def _apply_internal_layout_profile_compaction(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    aliases: Mapping[str, Mapping],
    gkk_template: sp.Matrix,
    template_internal_nodes: Sequence[str],
    internal_layout_profiles: Sequence[Mapping],
    retained_count: int,
) -> str:
    if not internal_layout_profiles:
        return draft
    draft = _replace_enum_for_internal_layouts(draft, internal_layout_profiles)
    draft = _insert_internal_profile_state(draft, internal_layout_profiles)
    draft = _insert_internal_profile_selection(draft, case_id_symbol=case_id_symbol, profiles=internal_layout_profiles)
    draft = _apply_internal_active_matrix_dimensions(draft)
    draft = _split_large_recovery_matrix_lifecycle(draft)
    draft = _replace_ram_gred_precompute_for_internal_profiles(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=internal_layout_profiles,
        template_internal_nodes=template_internal_nodes,
    )
    draft = _replace_internal_matrix_set_code_blocks(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=internal_layout_profiles,
        template_internal_nodes=template_internal_nodes,
        retained_count=retained_count,
    )
    draft = _replace_internal_profile_w_inverse(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=internal_layout_profiles,
        case_profiles=profiles,
        aliases=aliases,
        gkk_template=gkk_template,
        template_internal_nodes=template_internal_nodes,
    )
    draft = _replace_ram_fixed_internal_matrix_precompute(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=internal_layout_profiles,
        template_internal_nodes=template_internal_nodes,
        retained_count=retained_count,
    )
    draft = _guard_internal_active_ihis_schur(draft)
    draft = _replace_internal_profile_ihis_schur(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=internal_layout_profiles,
    )
    draft = draft.replace("for (int k = 0; k < INTERNAL_NODES; k++)", "for (int k = 0; k < internal_active; k++)")
    draft = draft.replace("for (int row = 0; row < INTERNAL_NODES; row++)", "for (int row = 0; row < internal_active; row++)")
    return draft


def _apply_multicase_conditional_diagonal_w_builder(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    gkk_template: sp.Matrix,
    recovery_profiles: Sequence[Mapping] | None = None,
    template_internal_nodes: Sequence[str] | None = None,
) -> str:
    gkk_template = sp.Matrix(gkk_template)
    size = int(gkk_template.rows)
    if not profiles or size not in {2, 3} or gkk_template.cols != size:
        return draft
    inverse_block = _find_w_code_symmetric_inverse_block(draft, size)
    if inverse_block is None:
        return draft

    active_rows_by_case = _active_internal_rows_by_case(recovery_profiles, template_internal_nodes)
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
    if not fallback_cases and not active_rows_by_case:
        replacement = "\n".join(
            ["    /* Case-resolved diagonal Gkk fast path: W = inv(diag(Gkk)). */"]
            + _diagonal_w_code_lines(size, 4)
        ) + "\n"
        return draft[:start] + replacement + draft[end:]

    lines = [
        "    /* Case-resolved Gkk inverse. Placeholder-only rows are skipped for cases with fewer internals. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for index in diagonal_cases:
        lines.append(f"    case {index}:")
        lines.append("    {")
        active_rows = active_rows_by_case.get(index, set(range(size)))
        lines.extend(_active_diagonal_w_code_lines(size, active_rows, 8))
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


def _multicase_diagonal_case_groups(
    *,
    profiles: list[dict],
    aliases: dict[str, dict],
    gkk_template: sp.Matrix,
) -> tuple[list[int], list[int]]:
    gkk_template = sp.Matrix(gkk_template)
    if not profiles or gkk_template.rows != gkk_template.cols or gkk_template.rows == 0:
        return [], list(range(len(profiles)))
    diagonal_cases: list[int] = []
    fallback_cases: list[int] = []
    for index, profile in enumerate(profiles):
        if _case_resolved_matrix_is_diagonal(gkk_template, profile, aliases, index):
            diagonal_cases.append(index)
        else:
            fallback_cases.append(index)
    return diagonal_cases, fallback_cases


def _case_group_switch_prefix(case_id_symbol: str, case_indices: Sequence[int]) -> list[str]:
    return [f"    case {index}:" for index in case_indices]


def _diagonal_gkk_scalar_gred_lines(
    active_nr_expr: str,
    *,
    internal_expr: str = "INTERNAL_NODES",
    indent: int = 8,
) -> list[str]:
    prefix = " " * indent
    return [
        f"{prefix}/* Diagonal Gkk scalar Schur path: reuse W[k,k] = 1/Gkk[k,k]. */",
        f"{prefix}for (int row = 0; row < {active_nr_expr}; row++) {{",
        f"{prefix}    for (int k = 0; k < {internal_expr}; k++) {{",
        f"{prefix}        double grk_w = get_CODE(&Grk_code, row, k) * get_CODE(&W_code, k, k);",
        f"{prefix}        set_CODE(&tmp_Grk_W_code, row, k, grk_w);",
        f"{prefix}    }}",
        f"{prefix}}}",
        f"{prefix}for (int row = 0; row < {active_nr_expr}; row++) {{",
        f"{prefix}    for (int col = row; col < {active_nr_expr}; col++) {{",
        f"{prefix}        double schur_acc = 0.0;",
        f"{prefix}        for (int k = 0; k < {internal_expr}; k++) {{",
        f"{prefix}            schur_acc += get_CODE(&tmp_Grk_W_code, row, k) * get_CODE(&Grk_code, col, k);",
        f"{prefix}        }}",
        f"{prefix}        set_CODE(&Gred_code, row, col, get_CODE(&Grr_code, row, col) - schur_acc);",
        f"{prefix}    }}",
        f"{prefix}}}",
    ]


def _diagonal_gkk_scalar_ihis_lines(
    active_nr_expr: str,
    *,
    internal_expr: str = "INTERNAL_NODES",
    indent: int = 8,
) -> list[str]:
    prefix = " " * indent
    return [
        f"{prefix}/* Diagonal Gkk scalar Ihisred path: Ihisred = Ihisr - tmp_Grk_W * Ihisk. */",
        f"{prefix}for (int row = 0; row < {active_nr_expr}; row++) {{",
        f"{prefix}    double ihis_acc = 0.0;",
        f"{prefix}    for (int k = 0; k < {internal_expr}; k++) {{",
        f"{prefix}        ihis_acc += get_CODE(&tmp_Grk_W_code, row, k) * get_CODE(&Ihisk_code, k, 0);",
        f"{prefix}    }}",
        f"{prefix}    set_CODE(&Ihisred_code, row, 0, get_CODE(&Ihisr_code, row, 0) - ihis_acc);",
        f"{prefix}}}",
    ]


def _diagonal_gkk_scalar_vk_lines(
    active_nr_expr: str,
    *,
    internal_expr: str = "INTERNAL_NODES",
    indent: int = 8,
) -> list[str]:
    prefix = " " * indent
    return [
        f"{prefix}/* Diagonal Gkk scalar recovery: W*Gkr is transpose(tmp_Grk_W). */",
        f"{prefix}for (int k = 0; k < {internal_expr}; k++) {{",
        f"{prefix}    double core_v = 0.0;",
        f"{prefix}    for (int col = 0; col < {active_nr_expr}; col++) {{",
        f"{prefix}        core_v += get_CODE(&tmp_Grk_W_code, col, k) * get_CODE(&Vr_code, col, 0);",
        f"{prefix}    }}",
        f"{prefix}    double hist_v = get_CODE(&Ihisk_code, k, 0) * get_CODE(&W_code, k, k);",
        f"{prefix}    set_CODE(&Vk_code, k, 0, -(core_v + hist_v));",
        f"{prefix}}}",
    ]


def _wrap_multicase_diagonal_scalar_block(
    *,
    case_id_symbol: str,
    diagonal_cases: Sequence[int],
    fallback_cases: Sequence[int],
    diagonal_lines: Sequence[str],
    fallback_lines: Sequence[str],
) -> list[str]:
    lines = [f"    switch ({case_id_symbol}) {{"]
    lines.extend(_case_group_switch_prefix(case_id_symbol, diagonal_cases))
    lines.append("    {")
    lines.extend(diagonal_lines)
    lines.append("        break;")
    lines.append("    }")
    if fallback_cases:
        lines.extend(_case_group_switch_prefix(case_id_symbol, fallback_cases))
        lines.append("    {")
        lines.extend(_indent_c_block("\n".join(fallback_lines), 4))
        lines.append("        break;")
        lines.append("    }")
    lines.append("    default:")
    lines.append("    {")
    lines.extend(_indent_c_block("\n".join(fallback_lines), 4))
    lines.append("        break;")
    lines.append("    }")
    lines.append("    }")
    return lines


def _apply_multicase_conditional_diagonal_scalar_paths(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    gkk_template: sp.Matrix,
    active_nr_expr: str = "NR",
) -> str:
    schur_ihis_already_rewritten = "Case-resolved diagonal Gkk scalar Schur/Ihis path" in draft
    gkk_template = sp.Matrix(gkk_template)
    if not profiles or gkk_template.rows != gkk_template.cols or gkk_template.rows == 0:
        return draft

    nr_exprs = list(dict.fromkeys([active_nr_expr, "NR", "RETAINED_NODES", "node_active"]))
    internal_exprs = ["NK", "INTERNAL_NODES"]

    gred_old = "\n".join([
        "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
        "    /* Full Gred CODE path: all reduced entries are CODE-owned, so a full Schur update is allowed. */",
        "    matrix_mult_CODE(&tmp_Grk_W_Gkr_code, &tmp_Grk_W_code, &Gkr_code);",
        "    matrix_subtract_CODE(&Gred_code, &Grr_code, &tmp_Grk_W_Gkr_code);",
    ])
    gred_candidates = [gred_old]
    for dim_expr in nr_exprs:
        for comment in (
            "    /* Full Gred CODE path: all reduced entries are CODE-owned, so a full Schur update is allowed. */",
            "    /* Full Gred CODE path: compute the dense product, then write only the upper triangle used by GValue stamps. */",
        ):
            gred_candidates.append("\n".join([
                "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
                comment,
                "    matrix_mult_CODE(&tmp_Grk_W_Gkr_code, &tmp_Grk_W_code, &Gkr_code);",
                "    /* Gred is symmetric; only the upper triangle is needed for dynamic GValue stamps. */",
                *_upper_tri_matrix_subtract_lines("Gred_code", "Grr_code", "tmp_Grk_W_Gkr_code", dim_expr),
            ]))
            for internal_expr in internal_exprs:
                gred_candidates.append("\n".join([
                    "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
                    comment,
                    *_upper_tri_matrix_product_lines(
                        "tmp_Grk_W_Gkr_code",
                        "tmp_Grk_W_code",
                        "Gkr_code",
                        dim_expr,
                        internal_expr,
                    ),
                    *_upper_tri_matrix_subtract_lines("Gred_code", "Grr_code", "tmp_Grk_W_Gkr_code", dim_expr),
                ]))
    gred_fallback = [
        "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
        "    /* Full Gred CODE path: compute the dense product, then write only the upper triangle used by GValue stamps. */",
        *_upper_tri_matrix_product_transpose_rhs_lines("tmp_Grk_W_Gkr_code", "tmp_Grk_W_code", "Grk_code", active_nr_expr),
        *_upper_tri_matrix_subtract_lines("Gred_code", "Grr_code", "tmp_Grk_W_Gkr_code", active_nr_expr),
    ]
    diagonal_cases, fallback_cases = _multicase_diagonal_case_groups(
        profiles=profiles,
        aliases=aliases,
        gkk_template=gkk_template,
    )
    if not diagonal_cases:
        for candidate in gred_candidates:
            if candidate in draft:
                draft = draft.replace(candidate, "\n".join(gred_fallback), 1)
                break
        return draft

    if not schur_ihis_already_rewritten:
        gred_new = "\n".join(
            ["    /* Case-resolved diagonal Gkk scalar Schur/Ihis path. */"]
            + _wrap_multicase_diagonal_scalar_block(
                case_id_symbol=case_id_symbol,
                diagonal_cases=diagonal_cases,
                fallback_cases=fallback_cases,
                diagonal_lines=_diagonal_gkk_scalar_gred_lines(active_nr_expr),
                fallback_lines=gred_fallback,
            )
        )
        for candidate in gred_candidates:
            if candidate in draft:
                draft = draft.replace(candidate, gred_new, 1)
                break
        else:
            gred_pattern = re.compile(
                r"    matrix_mult_CODE\(&tmp_Grk_W_code, &Grk_code, &W_code\);\n"
                r"    /\* Full Gred CODE path:[\s\S]*?"
                r"(?=\n    /\* Stamp dynamic Gred entries)",
            )
            draft, replaced = gred_pattern.subn(gred_new, draft, count=1)
            if replaced == 0:
                return draft

        ihis_old = "\n".join([
            "    matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
            "    matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
        ])
        ihis_new = "\n".join(
            ["    /* Case-resolved diagonal Gkk scalar Ihisred path. */"]
            + _wrap_multicase_diagonal_scalar_block(
                case_id_symbol=case_id_symbol,
                diagonal_cases=diagonal_cases,
                fallback_cases=fallback_cases,
                diagonal_lines=_diagonal_gkk_scalar_ihis_lines(active_nr_expr),
                fallback_lines=ihis_old.splitlines(),
            )
        )
        if ihis_old in draft:
            draft = draft.replace(ihis_old, ihis_new, 1)

    vk_candidates = []
    for internal_expr in internal_exprs:
        for dim_expr in nr_exprs:
            vk_candidates.append("\n".join([
                "    /* Symmetry reuse: W * Gkr = transpose(Grk * W). */",
                f"    for (int row = 0; row < {internal_expr}; row++) {{",
                f"        for (int col = 0; col < {dim_expr}; col++) {{",
                "            set_CODE(&tmp_W_Gkr_code, row, col, get_CODE(&tmp_Grk_W_code, col, row));",
                "        }",
                "    }",
                "    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);",
                "    matrix_matXvec_CODE(&tmp_W_Ihisk_code, &W_code, &Ihisk_code);",
                "    matrix_add_CODE(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code);",
                "    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);",
            ]))
    vk_fallback = [
        f"    network_node_recover_vk_matrix({active_nr_expr}, INTERNAL_NODES, "
        "&tmp_Grk_W_code, &tmp_W_Gkr_code, &Vr_code, &W_code, &Ihisk_code, "
        "&tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code, &tmp_Vk_sum_code, &Vk_code);",
    ]
    vk_new = "\n".join(
        ["    /* Case-resolved diagonal Gkk scalar Vk recovery path. */"]
        + _wrap_multicase_diagonal_scalar_block(
            case_id_symbol=case_id_symbol,
            diagonal_cases=diagonal_cases,
            fallback_cases=fallback_cases,
            diagonal_lines=[
                f"        network_node_recover_vk_diag({active_nr_expr}, INTERNAL_NODES, "
                "&tmp_Grk_W_code, &Vr_code, &Ihisk_code, &W_code, &Vk_code);",
            ],
            fallback_lines=vk_fallback,
        )
    )
    for candidate in vk_candidates:
        if candidate in draft:
            draft = draft.replace(candidate, vk_new, 1)
            draft = _ensure_vk_recovery_code_functions(draft)
            break
    else:
        vk_pattern = re.compile(
            r"    /\* Symmetry reuse: W \* Gkr = transpose\(Grk \* W\)\. \*/\n"
            r"    for \((?:int )?row = 0; row < (?:NK|INTERNAL_NODES|internal_active); row\+\+\) \{\n"
            r"        for \((?:int )?col = 0; col < (?:NR|RETAINED_NODES|node_active); col\+\+\) \{\n"
            r"            set_CODE\(&tmp_W_Gkr_code, row, col, get_CODE\(&tmp_Grk_W_code, col, row\)\);\n"
            r"        \}\n"
            r"    \}\n"
            r"    matrix_matXvec_CODE\(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code\);\n"
            r"    matrix_matXvec_CODE\(&tmp_W_Ihisk_code, &W_code, &Ihisk_code\);\n"
            r"    matrix_add_CODE\(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code\);\n"
            r"    matrix_scalarMult_CODE\(&Vk_code, &tmp_Vk_sum_code, -1\.0\);"
        )
        draft, replaced = vk_pattern.subn(vk_new, draft, count=1)
        if replaced:
            draft = _ensure_vk_recovery_code_functions(draft)
        else:
            draft = draft.replace("for (int col = 0; col < NR; col++)", f"for (int col = 0; col < {active_nr_expr}; col++)")
            draft = draft.replace("for (int col = 0; col < RETAINED_NODES; col++)", f"for (int col = 0; col < {active_nr_expr}; col++)")
    return draft


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
    for index, item in enumerate(profiles):
        item["profile_id"] = f"PACK_CASE_{index}"
        item["retained_nodes_id"] = f"RETAINED_NODES_CASE_{index}"
    return profiles, compact_super_order


def _with_reordered_external_nodes(payload: dict, external_nodes: Sequence[str]) -> dict:
    clone = dict(payload)
    clone["external_nodes"] = [str(node) for node in external_nodes]
    return clone


def _replace_enum_for_retained_layouts(draft: str, profiles: list[dict], nk: int) -> str:
    if not profiles:
        return draft
    enum_parts = [f"{profile['profile_id']} = {index}" for index, profile in enumerate(profiles)]
    dim_parts = [f"{profile['retained_nodes_id']} = {profile['nr_active']}" for profile in profiles]
    enum_line = (
        f"enum {{ {', '.join(enum_parts)}, {', '.join(dim_parts)}, INTERNAL_NODES = {nk} }};"
    )
    enum_comment = [
        "/* Multi-case retained-layout constants:",
        " * PACK_CASE_n identifies a group of case_id values that share the same retained-node layout.",
        " * RETAINED_NODES_CASE_n is the active retained-node count for that layout after dummy finalization.",
        " * INTERNAL_NODES is the number of eliminated internal nodes used by the shared Schur/Vk workflow.",
        " * node_active is selected from RETAINED_NODES_CASE_n during RAM initialization; runtime layout switching is not supported.",
        " */",
    ]
    enum_replacement = enum_line + "\n" + "\n".join(enum_comment)
    enum_pattern = (
        r"enum \{ (?:NR = \d+, NK = \d+|RETAINED_NODES = \d+, INTERNAL_NODES = \d+) \};"
        r"(?:\n/\* Dimension names:[\s\S]*?\*/)?"
    )
    draft_with_enum, replacement_count = re.subn(enum_pattern, enum_replacement, draft, count=1)
    if replacement_count:
        return draft_with_enum

    # No-elimination retained-profile paths may have had the old NR/NK enum
    # removed before profile compaction runs.  If generated code still uses
    # PACK_CASE_n / RETAINED_NODES_CASE_n, the profile enum is required.
    insertion = enum_replacement + "\n\n"
    lifecycle_marker = "/* RTDS lifecycle placement"
    if lifecycle_marker in draft:
        return draft.replace(lifecycle_marker, insertion + lifecycle_marker, 1)
    static_marker = "STATIC:"
    if static_marker in draft:
        return draft.replace(static_marker, insertion + static_marker, 1)
    return insertion + draft


def _case_condition_from_ids(case_id_symbol: str, case_ids: Sequence[int]) -> str:
    return " || ".join(f"{case_id_symbol} == {case_id}" for case_id in case_ids) or "FALSE"


def _insert_retained_profile_selection(draft: str, *, case_id_symbol: str, profiles: list[dict]) -> str:
    if not profiles or "int node_active = " in draft:
        return draft
    largest = profiles[0]
    static_lines = [
        "    /* Active retained layout for the selected Pack case group. */",
        f"    int retained_profile = {largest['profile_id']};",
        "    /* node_active is the retained-node count used by matrix allocation, g_mat_over, and GValue stamping. */",
        f"    int node_active = {largest['retained_nodes_id']};",
    ]
    draft = draft.replace("    /* Runtime matrix objects */", "\n".join(static_lines) + "\n    /* Runtime matrix objects */", 1)
    selection = [
        "    /* Retained layout is selected during initialization. Runtime profile switching is not supported. */",
    ]
    for index, profile in enumerate(profiles):
        keyword = "if" if index == 0 else "else if"
        selection.append(f"    {keyword} ({_case_condition_from_ids(case_id_symbol, profile['case_ids'])}) {{")
        selection.append(f"        retained_profile = {profile['profile_id']};")
        selection.append(f"        node_active = {profile['retained_nodes_id']};")
        selection.append("    }")
    marker = "    int err = 0;"
    return draft.replace(marker, "\n".join(selection) + "\n" + marker, 1)


def _apply_active_matrix_dimensions(draft: str) -> str:
    replacements = {
        "matrixDim(&Grr_code, NR, NR)": "matrixDim(&Grr_code, node_active, node_active)",
        "matrixDim(&Grk_code, NR, NK)": "matrixDim(&Grk_code, node_active, NK)",
        "matrixDim(&Gkr_code, NK, NR)": "matrixDim(&Gkr_code, NK, node_active)",
        "matrixDim(&Gred_code, NR, NR)": "matrixDim(&Gred_code, node_active, node_active)",
        "matrixDim(&Ihisr_code, NR, 1)": "matrixDim(&Ihisr_code, node_active, 1)",
        "matrixDim(&Ihisred_code, NR, 1)": "matrixDim(&Ihisred_code, node_active, 1)",
        "matrixDim(&Vr_code, NR, 1)": "matrixDim(&Vr_code, node_active, 1)",
        "matrixDim(&tmp_Grk_W_code, NR, NK)": "matrixDim(&tmp_Grk_W_code, node_active, NK)",
        "matrixDim(&tmp_Grk_W_Gkr_code, NR, NR)": "matrixDim(&tmp_Grk_W_Gkr_code, node_active, node_active)",
        "matrixDim(&tmp_Grk_W_Ihisk_code, NR, 1)": "matrixDim(&tmp_Grk_W_Ihisk_code, node_active, 1)",
        "matrixDim(&tmp_W_Gkr_code, NK, NR)": "matrixDim(&tmp_W_Gkr_code, NK, node_active)",
        "matrixDim(&Grr_code, RETAINED_NODES, RETAINED_NODES)": "matrixDim(&Grr_code, node_active, node_active)",
        "matrixDim(&Grk_code, RETAINED_NODES, INTERNAL_NODES)": "matrixDim(&Grk_code, node_active, INTERNAL_NODES)",
        "matrixDim(&Gkr_code, INTERNAL_NODES, RETAINED_NODES)": "matrixDim(&Gkr_code, INTERNAL_NODES, node_active)",
        "matrixDim(&Gred_code, RETAINED_NODES, RETAINED_NODES)": "matrixDim(&Gred_code, node_active, node_active)",
        "matrixDim(&Ihisr_code, RETAINED_NODES, 1)": "matrixDim(&Ihisr_code, node_active, 1)",
        "matrixDim(&Ihisred_code, RETAINED_NODES, 1)": "matrixDim(&Ihisred_code, node_active, 1)",
        "matrixDim(&Vr_code, RETAINED_NODES, 1)": "matrixDim(&Vr_code, node_active, 1)",
        "matrixDim(&tmp_Grk_W_code, RETAINED_NODES, INTERNAL_NODES)": "matrixDim(&tmp_Grk_W_code, node_active, INTERNAL_NODES)",
        "matrixDim(&tmp_Grk_W_Gkr_code, RETAINED_NODES, RETAINED_NODES)": "matrixDim(&tmp_Grk_W_Gkr_code, node_active, node_active)",
        "matrixDim(&tmp_Grk_W_Ihisk_code, RETAINED_NODES, 1)": "matrixDim(&tmp_Grk_W_Ihisk_code, node_active, 1)",
        "matrixDim(&tmp_W_Gkr_code, INTERNAL_NODES, RETAINED_NODES)": "matrixDim(&tmp_W_Gkr_code, INTERNAL_NODES, node_active)",
    }
    for old, new in replacements.items():
        draft = draft.replace(old, new)
    draft = draft.replace("< RETAINED_NODES;", "< node_active;")
    return draft


def _rename_internal_node_constant_for_retained_layouts(draft: str) -> str:
    return re.sub(r"\bNK\b", "INTERNAL_NODES", draft)


def _apply_active_ram_overlay_dimension(draft: str, *, y_profile: dict, d_profile: dict) -> str:
    nr_d = int(d_profile["nr_active"])
    draft = re.sub(
        r"for \(int row = 0; row < \d+; row\+\+\) \{\n        for \(int col = 0; col < \d+; col\+\+\) \{\n            g_mat_over\[row\]\[col\] = 0\.0;",
        "for (int row = 0; row < node_active; row++) {\n        for (int col = 0; col < node_active; col++) {\n            g_mat_over[row][col] = 0.0;",
        draft,
    )
    setup_lines = [
        f"if (retained_profile == {y_profile['profile_id']}) {{",
        f"        setupGMatrix({y_profile['retained_nodes_id']});",
        "    } else {",
        f"        setupGMatrix({d_profile['retained_nodes_id']});",
        "    }",
    ]
    draft = re.sub(r"setupGMatrix\(\d+\);", "\n".join(setup_lines), draft, count=1)

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
    draft = _rename_internal_node_constant_for_retained_layouts(draft)
    return draft


def _sync_dummy_finalized_alias_values(
    aliases: dict[str, dict],
    template_payload: dict,
    final_results: Sequence[Mapping],
) -> None:
    if not aliases or not final_results:
        return
    template_nodes = [str(node) for node in (template_payload.get("external_nodes") or [])]
    alias_positions: dict[str, list[tuple[str, int, int]]] = {}

    def record_expr(expr: object, kind: str, row: int, col: int) -> None:
        parsed = _parse_expr(expr)
        if not parsed.is_Symbol:
            return
        alias = str(parsed)
        if alias in aliases:
            position = (kind, row, col)
            positions = alias_positions.setdefault(alias, [])
            if position not in positions:
                positions.append(position)

    for row, values in enumerate(template_payload.get("G_full") or []):
        for col, expr in enumerate(values or []):
            record_expr(expr, "G", row, col)
    for row, expr in enumerate(template_payload.get("Ihis_full") or []):
        record_expr(expr, "Ihis", row, 0)
    node_index = {node: index for index, node in enumerate(template_nodes)}
    for stamp in template_payload.get("direct_retained_stamps") or []:
        for entry in stamp.get("G") or []:
            row = node_index.get(str(entry.get("row")))
            col = node_index.get(str(entry.get("col")))
            if row is not None and col is not None:
                record_expr(entry.get("expr"), "G", row, col)
        for entry in stamp.get("Ihis") or []:
            row = node_index.get(str(entry.get("row")))
            if row is not None:
                record_expr(entry.get("expr"), "Ihis", row, 0)

    def resolved_final_value(
        item: Mapping,
        kind: str,
        row: int,
        col: int,
    ) -> tuple[bool, sp.Expr | None]:
        if row >= len(template_nodes) or (kind == "G" and col >= len(template_nodes)):
            return False, None
        row_node = template_nodes[row]
        col_node = template_nodes[col] if kind == "G" else None
        final = item.get("final")
        final_nodes = [str(node) for node in (getattr(final, "nodes", []) if final is not None else [])]
        if row_node not in final_nodes or (kind == "G" and col_node not in final_nodes):
            return True, None
        final_row = final_nodes.index(row_node)
        if kind == "G":
            final_col = final_nodes.index(str(col_node))
            return True, sp.sympify(final.G[final_row, final_col])
        return True, sp.sympify(final.Ihis[final_row, 0])

    def same_alias_position_value(left: sp.Expr, right: sp.Expr) -> bool:
        # This path runs before code generation on possibly huge multi-case
        # expressions.  Do not call expand/simplify here; if structural equality
        # is not obvious, keep the original alias values instead.
        return bool(left == right)

    for alias, positions in alias_positions.items():
        next_values: dict[str, str] = {}
        next_owners: dict[int, str] = {}
        unresolved = False
        for item in final_results:
            case_index = int(item.get("index", 0))
            active_values: list[sp.Expr] = []
            for kind, row, col in positions:
                ok, position_value = resolved_final_value(item, kind, row, col)
                if not ok:
                    unresolved = True
                    break
                if position_value is not None:
                    active_values.append(position_value)
            if unresolved:
                break
            if not active_values:
                value = sp.Integer(0)
            else:
                value = active_values[0]
                if any(not same_alias_position_value(value, other) for other in active_values[1:]):
                    unresolved = True
                    break
            next_values[str(case_index)] = _expr_to_payload_text(value)
            next_owners[case_index] = _expr_stage(value, item.get("symbol_table") or {})
        if unresolved:
            continue
        info = aliases[alias]
        if next_values and next_values != info.get("case_values"):
            info["case_values"] = next_values
            info["case_owners"] = next_owners
            info["owner"] = _promote_owner(next_owners.values())


def _profile_symbol_table(profile: dict) -> dict:
    payload = profile.get("payload") or {}
    return dict(payload.get("symbol_dependency_table_tagged") or payload.get("symbol_dependency_table") or {})


def _case_condition(case_id_symbol: str, case_indices: Sequence[int]) -> str:
    return " || ".join(f"{case_id_symbol} == {index}" for index in case_indices) if case_indices else "FALSE"


def _var_g_name(nodes: Sequence[str], row: int, col: int) -> str:
    a = _c_identifier_name(nodes[row], f"N{row + 1}")
    b = _c_identifier_name(nodes[col], f"N{col + 1}")
    return f"varG_{a}_{b}"


def _split_expr_by_stage_light(expr: sp.Expr, symbol_table: dict[str, str]) -> tuple[sp.Expr, sp.Expr]:
    """Split a small source-level final-G expression into RAM and CODE terms.

    This helper is intentionally used only by no-internal conditional GValue
    exports. It does not prove Schur equivalence or expand large reduced
    expressions; it only separates additive source terms that are already final
    GValue inputs.
    """
    expr = sp.sympify(expr)
    if expr == 0:
        return sp.Integer(0), sp.Integer(0)
    expanded = sp.expand(expr) if int(sp.count_ops(expr)) <= 200 else expr
    ram_terms: list[sp.Expr] = []
    code_terms: list[sp.Expr] = []
    for term in sp.Add.make_args(expanded):
        if _expr_stage(term, symbol_table) == "RAM":
            ram_terms.append(term)
        else:
            code_terms.append(term)
    ram_expr = sp.Add(*ram_terms) if ram_terms else sp.Integer(0)
    code_expr = sp.Add(*code_terms) if code_terms else sp.Integer(0)
    return ram_expr, code_expr


def _alias_stage_symbol_table(profile: dict, aliases: dict[str, dict]) -> dict[str, str]:
    table = _profile_symbol_table(profile)
    for alias, info in aliases.items():
        table[alias] = _symbol_dependency_for_owner(str(info.get("owner") or "RAM"))
    return table


def _split_final_g_expr_preserving_aliases(
    *,
    template_expr: sp.Expr,
    resolved_expr: sp.Expr,
    profile: dict,
    aliases: dict[str, dict],
    profile_index: int,
) -> tuple[sp.Expr, sp.Expr]:
    """Split final-G entry while keeping useful multi-case aliases in C output.

    The resolved expression is authoritative for RAM/CODE ownership in a specific
    case.  The template expression is preferred only when substituting aliases
    back to this case gives the same RAM or CODE piece.  This preserves readable
    RAM aliases such as multcase_G_C1_N1_N1 without forcing mixed-owner aliases
    into RAM-owned cases.
    """
    resolved_ram, resolved_code = _split_expr_by_stage_light(
        resolved_expr,
        _profile_symbol_table(profile),
    )
    template_ram, template_code = _split_expr_by_stage_light(
        template_expr,
        _alias_stage_symbol_table(profile, aliases),
    )

    def choose(candidate: sp.Expr, expected: sp.Expr) -> sp.Expr:
        candidate = sp.sympify(candidate)
        expected = sp.sympify(expected)
        if _expr_equal_light(expected, 0):
            return sp.Integer(0)
        if not _expr_equal_light(candidate, 0):
            resolved_candidate = _profile_final_expr(candidate, profile, aliases, profile_index)
            if _expr_equal_light(resolved_candidate, expected):
                return candidate
        return expected

    return choose(template_ram, resolved_ram), choose(template_code, resolved_code)


def _conditional_final_g_plans(
    *,
    case_id_symbol: str,
    profiles: list[dict],
    aliases: dict[str, dict],
    template_gred: sp.Matrix,
    external_nodes: list[str],
    split_ram_code_terms: bool = False,
) -> list[dict]:
    nr = len(external_nodes)
    entry_plans: list[dict] = []
    for row in range(nr):
        for col in range(row, nr):
            per_case = []
            ram_cases = []
            code_cases = []
            template_expr = sp.sympify(template_gred[row, col])
            for index, profile in enumerate(profiles):
                expr = _profile_final_expr(template_expr, profile, aliases, index)
                symbol_table = _profile_symbol_table(profile)
                if split_ram_code_terms:
                    ram_expr, code_expr = _split_final_g_expr_preserving_aliases(
                        template_expr=template_expr,
                        resolved_expr=expr,
                        profile=profile,
                        aliases=aliases,
                        profile_index=index,
                    )
                    per_case.append({"index": index, "expr": ram_expr + code_expr, "stage": _expr_stage(expr, symbol_table)})
                    if not _expr_equal_light(ram_expr, 0):
                        ram_cases.append({"index": index, "expr": ram_expr, "stage": "RAM"})
                    if not _expr_equal_light(code_expr, 0):
                        code_cases.append({"index": index, "expr": code_expr, "stage": _expr_stage(code_expr, symbol_table)})
                else:
                    stage = _expr_stage(expr, symbol_table)
                    item = {"index": index, "expr": expr, "stage": stage}
                    per_case.append(item)
                    if stage == "RAM":
                        ram_cases.append(item)
                    else:
                        code_cases.append(item)
            if all(_expr_equal_light(item["expr"], 0) for item in per_case):
                continue
            entry_plans.append({
                "row": row,
                "col": col,
                "var": _var_g_name(external_nodes, row, col),
                "template_expr": template_expr,
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
) -> tuple[str, list[dict], dict[str, object]]:
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
        split_ram_code_terms=True,
    )
    shared_source_temps = _conditional_shared_source_temp_plan(
        entry_plans=entry_plans,
        aliases=aliases,
        case_id_symbol=case_id_symbol,
        profiles=profiles,
        symbol_table=symbol_table,
    )

    lines = [
        "#include <matrixLIB.h>",
        "/* Multi-case alias-template C draft with case-conditional final GValues.",
        "   case_id is assumed fixed before simulation; runtime case switching is not supported.",
        "   Each case uses full values. No base + delta compensation is generated. */",
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
    declared_static_names = set(declared_symbols) | set(aliases)
    declared_static_names.update(f"{_c_identifier_name(branch_id, 'branch')}_case_id" for branch_id in branch_ids)
    lines.extend(_shared_source_temp_declaration_lines(shared_source_temps, declared_static_names))
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
    ])
    lines.extend(_shared_source_temp_assignment_lines(shared_source_temps))
    if shared_source_temps:
        lines.append("")
    lines.append("    g_mat_nods[0] = getNodeNum(comp, \"" + external_nodes[0] + "\");" if nr else "")
    for index, node in enumerate(external_nodes[1:], start=1):
        lines.append(f"    g_mat_nods[{index}] = getNodeNum(comp, \"{node}\");")
    if nr:
        lines.extend([
            f"    for (int row = 0; row < {nr}; row++) {{",
            f"        for (int col = 0; col < {nr}; col++) {{",
            "            g_mat_over[row][col] = 0.0;",
            "        }",
            "    }",
            f"    switch ({case_id_symbol}) {{",
        ])
        for index, profile in enumerate(profiles):
            lines.append(f"    case {index}:")
            any_ram = False
            ram_items: list[tuple[int, int, sp.Expr]] = []
            ram_alias_assignments: list[tuple[str, sp.Expr]] = []
            for plan in entry_plans:
                ram_case = next((item for item in plan["ram_cases"] if item["index"] == index), None)
                if ram_case is None:
                    if any(item["index"] == index for item in plan["code_cases"]):
                        lines.append(f"        /* CODE-owned case: no RAM stamp for {plan['var']}. */")
                    continue
                expr = sp.sympify(ram_case["expr"])
                if expr == 0:
                    continue
                alias_stamp = _ram_stamp_alias_assignment(
                    template_expr=sp.sympify(plan.get("template_expr", expr)),
                    ram_expr=expr,
                )
                if alias_stamp is not None:
                    alias_assignment, stamp_expr = alias_stamp
                    _append_ram_alias_assignment(ram_alias_assignments, alias_assignment)
                    expr = stamp_expr
                ram_items.append((int(plan["row"]), int(plan["col"]), expr))
                any_ram = True
            if ram_items:
                local_case_indices = _local_case_indices_for_profile(
                    case_id_symbol=case_id_symbol,
                    profile_index=index,
                    profile=profile,
                    branch_ids=branch_ids,
                )
                lines.extend(_emit_source_level_ram_stamp_lines(
                    ram_items,
                    local_index={index: index for index in range(nr)},
                    temp_prefix=f"sourceG_{_c_identifier_name(case_id_symbol, 'case')}_case{index}_tmp",
                    indent=8,
                    alias_assignments=ram_alias_assignments,
                    substitutions=_shared_source_temp_substitutions_for_profile(
                        shared_source_temps,
                        local_case_indices,
                    ),
                ))
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
    lines.extend(
        _alias_assignment_lines(
            aliases,
            "CODE",
            case_id_symbol,
            shared_source_temps=shared_source_temps,
        )
        + _alias_assignment_lines(
            aliases,
            "CODE_PER_STEP",
            case_id_symbol,
            shared_source_temps=shared_source_temps,
        )
    )
    if any(info.get("owner") == "RAM" for info in aliases.values()):
        lines.extend(
            _alias_assignment_lines(
                aliases,
                "RAM",
                case_id_symbol,
                shared_source_temps=shared_source_temps,
            )
        )
    lines.extend([
        "",
    ])
    lines.extend(_final_g_code_case_lines(
        case_id_symbol=case_id_symbol,
        entry_plans=entry_plans,
        matrix_name=None,
    ))
    lines.extend([
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
    draft = "\n".join(line for line in lines if line != "")
    draft = _lift_repeated_ram_safe_source_temps_from_text(draft, symbol_table)
    return draft, gvalue_conditions


def _conditional_ram_stamp_block(
    *,
    case_id_symbol: str,
    profiles: list[dict],
    external_nodes: list[str],
    entry_plans: list[dict],
    aliases: dict[str, dict] | None = None,
    shared_source_temps: Mapping[str, Mapping[int, Sequence[tuple[str, sp.Expr]]]] | None = None,
) -> list[str]:
    nr = len(external_nodes)
    if not nr or not any(plan["ram_cases"] for plan in entry_plans):
        return ["    /* No RAM-side G entries: no fixed G overlay is registered. */"]

    def _case_stamp_body(index: int, *, indent: int) -> list[str]:
        prefix = " " * indent
        ram_items: list[tuple[int, int, sp.Expr]] = []
        ram_alias_assignments: list[tuple[str, sp.Expr]] = []
        body: list[str] = []
        for plan in entry_plans:
            ram_case = next((item for item in plan["ram_cases"] if item["index"] == index), None)
            if ram_case is None:
                if any(item["index"] == index for item in plan["code_cases"]):
                    body.append(f"{prefix}/* CODE-owned case: no RAM stamp for {plan['var']}. */")
                continue
            expr = sp.sympify(ram_case["expr"])
            if expr == 0:
                continue
            alias_stamp = _ram_stamp_alias_assignment(
                template_expr=sp.sympify(plan.get("template_expr", expr)),
                ram_expr=expr,
            )
            if alias_stamp is not None:
                alias_assignment, stamp_expr = alias_stamp
                alias_name, _alias_expr = alias_assignment
                if alias_name not in (aliases or {}):
                    _append_ram_alias_assignment(ram_alias_assignments, alias_assignment)
                expr = stamp_expr
            ram_items.append((int(plan["row"]), int(plan["col"]), expr))
        if not ram_items:
            body.append(f"{prefix}/* No RAM-owned final G entries in this case. */")
            return body
        branch_ids = sorted({
            str(info.get("branch_id"))
            for info in (aliases or {}).values()
            if info.get("branch_id")
        })
        local_case_indices = _local_case_indices_for_profile(
            case_id_symbol=case_id_symbol,
            profile_index=index,
            profile=profiles[index],
            branch_ids=branch_ids,
        )
        used_indices = sorted({idx for row, col, _expr in ram_items for idx in (row, col)})
        local_index = {global_index: local for local, global_index in enumerate(used_indices)}
        dim = len(used_indices)
        for local, global_index in enumerate(used_indices):
            body.append(f"{prefix}g_mat_nods[{local}] = getNodeNum(comp, \"{external_nodes[global_index]}\");")
        body.extend([
            f"{prefix}for (int row = 0; row < {dim}; row++) {{",
            f"{prefix}    for (int col = 0; col < {dim}; col++) {{",
            f"{prefix}        g_mat_over[row][col] = 0.0;",
            f"{prefix}    }}",
            f"{prefix}}}",
        ])
        body.extend(_emit_source_level_ram_stamp_lines(
            ram_items,
            local_index=local_index,
            temp_prefix=f"sourceG_{_c_identifier_name(case_id_symbol, 'case')}_case{index}_tmp",
            indent=indent,
            alias_assignments=ram_alias_assignments,
            substitutions=_shared_source_temp_substitutions_for_profile(
                shared_source_temps,
                local_case_indices,
            ),
        ))
        body.append(f"{prefix}setupGMatrix({dim});")
        return body

    case_bodies = [_case_stamp_body(index, indent=4) for index, _profile in enumerate(profiles)]
    if case_bodies and all(body == case_bodies[0] for body in case_bodies[1:]):
        return [
            "    /* Case-invariant RAM final-G stamp after multi-case aliases are resolved. */",
            *case_bodies[0],
        ]

    lines = [
        "    /* Case-conditional RAM final-G stamp. Each init-time case gets its own compact RAM overlay. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for index, _profile in enumerate(profiles):
        lines.append(f"    case {index}:")
        body = _case_stamp_body(index, indent=8)
        lines.extend(body)
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        f"                       \"Invalid multi-case {case_id_symbol} %d for component %s.\",",
        f"                       {case_id_symbol}, Name);",
        "        break;",
        "    }",
    ])
    return lines


def _replace_ram_g_setup_with_conditional_ram_block(draft: str, ram_block: str) -> str:
    start_marker = "    /* ************************************************************************\n     * RAM-SIDE G MATRIX VALUE SETUP"
    start = draft.find(start_marker)
    if start < 0:
        return draft.replace("    /* No RAM-side G entries: no fixed G overlay is registered. */", ram_block, 1)
    end_candidates = [
        draft.find("\n    err += matrixDim(", start),
        draft.find("\n    if (internal_active > 0) {\n        err += matrixDim(", start),
        draft.find("\n    if (err > 0) {", start),
        draft.find("\nGVALUES:", start),
    ]
    end_candidates = [index for index in end_candidates if index >= 0]
    end = min(end_candidates) if end_candidates else -1
    if end < 0:
        return draft.replace("    /* No RAM-side G entries: no fixed G overlay is registered. */", ram_block, 1)
    header_end = draft.find("*/", start)
    if header_end < 0 or header_end > end:
        return draft
    header_end += 2
    replacement = draft[start:header_end] + "\n\n\n" + ram_block + "\n\n"
    return draft[:start] + replacement + draft[end:]


def _guard_code_schur_update_for_conditional_gvalues(
    draft: str,
    *,
    case_id_symbol: str,
    profiles: Sequence[Mapping],
    entry_plans: Sequence[Mapping],
) -> str:
    if "Case-specific CODE-side Schur update for cases with dynamic final GValues." in draft:
        return draft
    code_case_indices = sorted({
        int(item["index"])
        for plan in entry_plans
        for item in (plan.get("code_cases") or [])
    })
    if not code_case_indices:
        return draft
    profile_indices = list(range(len(profiles)))
    if set(code_case_indices) == set(profile_indices):
        return draft
    start_marker = "    /* Runtime refresh. Use set_CODE for matrices touched in CODE; do not write MATRIX_.p directly. */"
    end_marker = "    /* Stamp dynamic Gred entries"
    start = draft.find(start_marker)
    end = draft.find(end_marker, start)
    if start < 0 or end < 0:
        return draft
    block = draft[start:end].rstrip("\n")
    non_code_case_indices = [index for index in profile_indices if index not in set(code_case_indices)]
    lines = [
        "    /* Case-specific CODE-side Schur update for cases with dynamic final GValues. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for index in code_case_indices:
        lines.append(f"    case {index}:")
    lines.extend(_indent_c_block(block, 4))
    lines.extend([
        "        break;",
    ])
    for index in non_code_case_indices:
        lines.append(f"    case {index}:")
    if non_code_case_indices:
        lines.extend([
            "        /* This Pack case has no CODE-side Schur update. */",
            "        break;",
        ])
    lines.extend([
        "    default:",
        "        break;",
        "    }",
        "",
    ])
    return draft[:start] + "\n".join(lines) + draft[end:]


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
        set_items: list[tuple[dict, sp.Expr]] = []
        direct_assignments: list[tuple[str, sp.Expr]] = []
        for plan in entry_plans:
            code_case = next((item for item in plan["code_cases"] if int(item["index"]) == case_index), None)
            if code_case is None:
                continue
            expr = sp.sympify(code_case["expr"])
            row = int(plan["row"])
            col = int(plan["col"])
            if matrix_name is None:
                direct_assignments.append((plan["var"], expr))
            else:
                set_items.append((plan, expr))
        if matrix_name is None:
            cse_lines, used_temps = _emit_source_level_cse_assignment_lines(
                direct_assignments,
                temp_prefix=f"sourceG_{_c_identifier_name(case_id_symbol, 'case')}_case{case_index}_tmp",
                indent=8,
            )
            if used_temps:
                lines.append("    {")
                lines.extend(cse_lines)
                lines.append("        break;")
                lines.append("    }")
            else:
                lines.extend(cse_lines)
                lines.append("        break;")
            continue

        temp_assignments = [
            (f"__entry_{index}", expr)
            for index, (_plan, expr) in enumerate(set_items)
        ]
        temps, reduced = _source_level_cse_assignments(
            temp_assignments,
            temp_prefix=f"sourceG_{_c_identifier_name(case_id_symbol, 'case')}_case{case_index}_tmp",
        )
        if temps:
            lines.append("    {")
            for name, expr in temps:
                lines.append(f"        double {name} = {_ccode(expr)};")
        reduced_expr_by_lhs = {lhs: expr for lhs, expr in reduced}
        for index, (plan, expr) in enumerate(set_items):
            value = _ccode(reduced_expr_by_lhs.get(f"__entry_{index}", expr))
            row = int(plan["row"])
            col = int(plan["col"])
            lines.append(f"        set_CODE(&{matrix_name}, {row}, {col}, {value});")
            if row != col:
                lines.append(f"        set_CODE(&{matrix_name}, {col}, {row}, {value});")
        if matrix_name is not None:
            for plan in entry_plans:
                if not any(int(item["index"]) == case_index for item in plan["code_cases"]):
                    continue
                lines.append(f"        {plan['var']} = get_CODE(&{matrix_name}, {plan['row']}, {plan['col']});")
        lines.append("        break;")
        if temps:
            lines.append("    }")
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
    code_aliases = [
        alias
        for alias, info in aliases.items()
        if _owner_rank(str(info.get("owner") or "RAM")) >= _owner_rank("CODE")
    ]
    if not code_aliases:
        return draft
    marker = "    /* Resolve CODE multi-case effective aliases as full values, never deltas. */"
    start = draft.find(marker)
    if start < 0:
        return draft
    end = draft.find("    if (!rtds_matrix_code_ready) {", start)
    if end < 0:
        return draft
    later_code = draft[end:]
    if any(re.search(rf"\b{re.escape(alias)}\b", later_code) for alias in code_aliases):
        return draft
    replacement = (
        "    /* CODE final-G cases write full case values directly below;\n"
        "       RAM-owned cases were stamped in RAM_PASS1. */\n"
    )
    return draft[:start] + replacement + draft[end:]


def _remove_ram_g_alias_resolution_for_conditional_final_writes(draft: str, aliases: dict[str, dict]) -> str:
    if not aliases:
        return draft
    ram_aliases = {
        alias: info
        for alias, info in aliases.items()
        if info.get("owner") == "RAM"
    }
    if not ram_aliases:
        return draft
    if not all(str(info.get("kind") or "").upper().startswith("G") for info in ram_aliases.values()):
        return draft

    for alias in ram_aliases:
        draft = re.sub(
            rf"\n    double {re.escape(alias)} = 0\.0;",
            "",
            draft,
        )

    marker = "    /* Resolve RAM multi-case effective aliases as full values, never deltas. */"
    start = draft.find(marker)
    if start < 0:
        return draft
    end = draft.find("    int err = 0;", start)
    if end < 0:
        return draft
    replacement = (
        "    /* RAM final-G cases write full case values directly to g_mat_over below. */\n"
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
        split_ram_code_terms=prune_code_matrix_writes,
    )
    branch_ids = sorted({
        str(info.get("branch_id"))
        for info in aliases.values()
        if info.get("branch_id")
    })
    shared_source_temps = _conditional_shared_source_temp_plan(
        entry_plans=entry_plans,
        aliases=aliases,
        case_id_symbol=case_id_symbol,
        profiles=profiles,
        symbol_table=_multi_case_symbol_table(profiles),
    )
    shared_decl_lines = _shared_source_temp_declaration_lines(
        shared_source_temps,
        _declared_c_names(draft),
    )
    if shared_decl_lines:
        draft = _insert_after_label(draft, "STATIC:", shared_decl_lines)
    shared_assignment_lines = _shared_source_temp_assignment_lines(shared_source_temps)
    if shared_assignment_lines:
        marker = "    int err = 0;"
        replacement = "\n".join(shared_assignment_lines) + "\n" + marker
        draft = draft.replace(marker, replacement, 1)
        draft = _apply_shared_source_temp_text_reuse(draft, shared_source_temps)
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
        aliases=aliases,
        shared_source_temps=shared_source_temps,
    ))
    draft = _replace_ram_g_setup_with_conditional_ram_block(draft, ram_block)

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
            draft = _lift_repeated_ram_safe_source_temps_from_text(
                draft,
                _multi_case_symbol_table(profiles),
            )
            draft = _lift_repeated_code_source_temps_from_text(draft)
            draft = _guard_code_schur_update_for_conditional_gvalues(
                draft,
                case_id_symbol=case_id_symbol,
                profiles=profiles,
                entry_plans=entry_plans,
            )
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
    draft = _lift_repeated_ram_safe_source_temps_from_text(
        draft,
        _multi_case_symbol_table(profiles),
    )
    draft = _lift_repeated_code_source_temps_from_text(draft)
    draft = _guard_code_schur_update_for_conditional_gvalues(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=profiles,
        entry_plans=entry_plans,
    )
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
    requested_codegen_mode = str(payload.get("elimination_codegen_mode") or payload.get("codegen_mode") or "auto")
    if requested_codegen_mode in {"prefer_matrix", "matrix"}:
        request_payload["prefer_ram_gred_matrix_precompute"] = True
    result = build_optimized_response(request_payload)
    template_G, template_Ihis, _, _, template_nodes, template_external, _, _ = _partition_payload(template_payload)
    template_reduced = eliminate_internal_nodes(template_G, template_Ihis, template_nodes, template_external)
    case_id_symbol = str(payload.get("case_id_symbol") or "global_case_id")
    display_names = template_payload.get("node_display_names") or {}
    c_external_nodes = [str(display_names.get(node, node)) for node in template_external]
    symbol_table = {}
    for profile in alias_model.get("sample_profiles") or alias_model["profiles"]:
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
                symbol_table=symbol_table,
            )
            draft = _apply_multicase_conditional_diagonal_w_builder(
                draft,
                case_id_symbol=case_id_symbol,
                profiles=alias_model["profiles"],
                aliases=aliases,
                gkk_template=_template_gkk_from_payload(template_payload),
                recovery_profiles=alias_model.get("final_recovery_profiles") or [],
                template_internal_nodes=result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
            )
            draft = _apply_multicase_conditional_diagonal_scalar_paths(
                draft,
                case_id_symbol=case_id_symbol,
                profiles=alias_model["profiles"],
                aliases=aliases,
                gkk_template=_template_gkk_from_payload(template_payload),
                active_nr_expr="NR",
            )
            draft = _apply_internal_layout_profile_compaction(
                draft,
                case_id_symbol=case_id_symbol,
                profiles=alias_model["profiles"],
                aliases=aliases,
                gkk_template=_template_gkk_from_payload(template_payload),
                template_internal_nodes=result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
                internal_layout_profiles=alias_model.get("internal_layout_profiles") or [],
                retained_count=len(template_payload.get("external_nodes") or result.get("external_nodes") or []),
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
        draft = _apply_final_retained_recovery_profiles_to_draft(
            draft,
            case_id_symbol=case_id_symbol,
            recovery_profiles=alias_model.get("final_recovery_profiles") or [],
            template_internal_nodes=result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
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

    has_runtime_mutable_g_alias = any(
        bool(info.get("runtime_mutable"))
        and str(info.get("kind") or "").upper().startswith("G")
        for info in aliases.values()
    )
    no_internal_final_g_can_be_split = (
        bool(aliases)
        and not has_internal_recovery
        and not has_runtime_mutable_g_alias
        and _final_g_stage_analysis_is_within_budget(template_reduced.G_red)
    )
    if aliases and (has_mixed_final_g or no_internal_final_g_can_be_split):
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
        if has_mixed_final_g:
            warnings.append(
                "Warning: final G entries have mixed RAM/CODE ownership across cases. "
                "CODE-owned entries are enabled with case conditions. "
                "case_id is fixed before simulation and must not change at runtime."
            )
        elif not has_internal_recovery:
            warnings.append(
                "Info: no-internal multi-case final G entries were split into RAM and CODE terms. "
                "RAM terms are stamped in RAM_PASS1; CODE terms are assigned directly to GValue handles."
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
            for index, profile in enumerate(payload.get("case_profiles") or alias_model["profiles"])
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "multi_case": {
            "codegen_mode": codegen_mode,
            "external_nodes": result.get("external_nodes") or [],
            "effective_internal_nodes": result.get("effective_internal_nodes") or [],
            "block_type": (result.get("structured") or {}).get("block_type"),
            "profile_count": len(payload.get("case_profiles") or alias_model["profiles"]),
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
            "internal_layout_profiles": alias_model.get("internal_layout_profiles") or [],
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
            "c_draft": _strip_unused_dimension_enum_for_direct_gvalue_draft(_ensure_static_blank_line(draft)),
            "fast_path": fast_path,
        },
    }


def _build_multi_case_c_draft(case_id_symbol: str, profile_results: list[dict]) -> str:
    scalar_mux = _try_build_scalar_mux_c_draft(profile_results)
    if scalar_mux:
        return _strip_unused_dimension_enum_for_direct_gvalue_draft(
            _use_readable_dimension_names(_ensure_static_blank_line(scalar_mux))
        )
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
        *(_upper_tri_matrix_product_lines("tmp_Grk_W_Gkr_code", "tmp_Grk_W_code", "Gkr_code", "NR", "NK") if nk else []),
        *(_upper_tri_matrix_subtract_lines("Gschur_code", "Grr_code", "tmp_Grk_W_Gkr_code", "NR") if nk else ["    /* No internal nodes: Gschur = Grr. */"]),
        *_upper_tri_matrix_add_lines("Gfinal_code", "Gschur_code", "Gdirect_code", "NR"),
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
    return _use_readable_dimension_names(_ensure_static_blank_line("\n".join(lines)))


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


def _remove_default_voltage_recovery_block(draft: str) -> str:
    start_marker = "    /* Internal-node voltage recovery after solved retained-node voltages are available. */\n"
    start = draft.find(start_marker)
    if start < 0:
        return draft
    assignment_marker = "    /* One variable per eliminated node, in effective k order. */\n"
    assignment_start = draft.find(assignment_marker, start)
    if assignment_start < 0:
        return draft
    pos = assignment_start + len(assignment_marker)
    assignment_re = re.compile(r"    [A-Za-z_]\w* = get_CODE\(&Vk_code, \d+, 0\);\n")
    while True:
        match = assignment_re.match(draft, pos)
        if not match:
            break
        pos = match.end()
    if draft.startswith("\n", pos):
        pos += 1
    return draft[:start] + draft[pos:]


def _vk_recovery_code_functions_blocks() -> dict[str, str]:
    return {
        "diag": """void network_node_recover_vk_diag(int retained_count, int internal_count,
                                  MATRIX_ *tmp_Grk_W_code,
                                  MATRIX_ *Vr_code,
                                  MATRIX_ *Ihisk_code,
                                  MATRIX_ *W_code,
                                  MATRIX_ *Vk_code)
{
    int k;
    int col;

    /* Diagonal Gkk scalar recovery: W*Gkr is transpose(tmp_Grk_W). */
    /* Preconditions: W is diagonal, Gkr = transpose(Grk), tmp_Grk_W_code = Grk * W. */
    for (k = 0; k < internal_count; k++) {
        double core_v = 0.0;
        for (col = 0; col < retained_count; col++) {
            core_v += get_CODE(tmp_Grk_W_code, col, k) * get_CODE(Vr_code, col, 0);
        }
        double hist_v = get_CODE(Ihisk_code, k, 0) * get_CODE(W_code, k, k);
        set_CODE(Vk_code, k, 0, -(core_v + hist_v));
    }
}""",

        "matrix": """void network_node_recover_vk_matrix(int retained_count, int internal_count,
                                    MATRIX_ *tmp_Grk_W_code,
                                    MATRIX_ *tmp_W_Gkr_code,
                                    MATRIX_ *Vr_code,
                                    MATRIX_ *W_code,
                                    MATRIX_ *Ihisk_code,
                                    MATRIX_ *tmp_W_Gkr_Vr_code,
                                    MATRIX_ *tmp_W_Ihisk_code,
                                    MATRIX_ *tmp_Vk_sum_code,
                                    MATRIX_ *Vk_code)
{
    int row;
    int col;

    /* Symmetry reuse: W * Gkr = transpose(Grk * W). */
    /* Preconditions: W is fully populated and symmetric, Gkr = transpose(Grk), tmp_Grk_W_code = Grk * W. */
    for (row = 0; row < internal_count; row++) {
        for (col = 0; col < retained_count; col++) {
            set_CODE(tmp_W_Gkr_code, row, col, get_CODE(tmp_Grk_W_code, col, row));
        }
    }
    matrix_matXvec_CODE(tmp_W_Gkr_Vr_code, tmp_W_Gkr_code, Vr_code);
    matrix_matXvec_CODE(tmp_W_Ihisk_code, W_code, Ihisk_code);
    matrix_add_CODE(tmp_Vk_sum_code, tmp_W_Gkr_Vr_code, tmp_W_Ihisk_code);
    matrix_scalarMult_CODE(Vk_code, tmp_Vk_sum_code, -1.0);
}""",

        "matrix_wgkr": """void network_node_recover_vk_matrix_from_wgkr(MATRIX_ *tmp_W_Gkr_code,
                                              MATRIX_ *Vr_code,
                                              MATRIX_ *W_code,
                                              MATRIX_ *Ihisk_code,
                                              MATRIX_ *tmp_W_Gkr_Vr_code,
                                              MATRIX_ *tmp_W_Ihisk_code,
                                              MATRIX_ *tmp_Vk_sum_code,
                                              MATRIX_ *Vk_code)
{
    /* Fixed-G path: tmp_W_Gkr_code already stores W * Gkr. */
    matrix_matXvec_CODE(tmp_W_Gkr_Vr_code, tmp_W_Gkr_code, Vr_code);
    matrix_matXvec_CODE(tmp_W_Ihisk_code, W_code, Ihisk_code);
    matrix_add_CODE(tmp_Vk_sum_code, tmp_W_Gkr_Vr_code, tmp_W_Ihisk_code);
    matrix_scalarMult_CODE(Vk_code, tmp_Vk_sum_code, -1.0);
}""",

        "wgkr_only": """void network_node_recover_vk_from_wgkr_only(int retained_count, int internal_count,
                                            MATRIX_ *tmp_W_Gkr_code,
                                            MATRIX_ *Vr_code,
                                            MATRIX_ *Vk_code)
{
    int row;
    int col;

    /* Legacy/static path: tmp_W_Gkr_code already stores W * Gkr, and no W*Ihisk term is present. */
    for (row = 0; row < internal_count; row++) {
        double core_v = 0.0;
        for (col = 0; col < retained_count; col++) {
            core_v += get_CODE(tmp_W_Gkr_code, row, col) * get_CODE(Vr_code, col, 0);
        }
        set_CODE(Vk_code, row, 0, -core_v);
    }
}""",

        "grkw_only": """void network_node_recover_vk_from_grkw_only(int retained_count, int internal_count,
                                             MATRIX_ *tmp_Grk_W_code,
                                             MATRIX_ *Vr_code,
                                             MATRIX_ *Vk_code)
{
    int row;
    int col;

    /* Recovery-only path: tmp_Grk_W_code stores Grk * W, and no W*Ihisk term is present. */
    /* Preconditions: W is fully populated and symmetric, Gkr = transpose(Grk), tmp_Grk_W_code = Grk * W. */
    for (row = 0; row < internal_count; row++) {
        double core_v = 0.0;
        for (col = 0; col < retained_count; col++) {
            core_v += get_CODE(tmp_Grk_W_code, col, row) * get_CODE(Vr_code, col, 0);
        }
        set_CODE(Vk_code, row, 0, -core_v);
    }
}""",
    }


def _ensure_vk_recovery_code_functions(draft: str) -> str:
    helper_specs = [
        ("diag", "network_node_recover_vk_diag(", "void network_node_recover_vk_diag("),
        ("matrix", "network_node_recover_vk_matrix(", "void network_node_recover_vk_matrix("),
        ("matrix_wgkr", "network_node_recover_vk_matrix_from_wgkr(", "void network_node_recover_vk_matrix_from_wgkr("),
        ("wgkr_only", "network_node_recover_vk_from_wgkr_only(", "void network_node_recover_vk_from_wgkr_only("),
        ("grkw_only", "network_node_recover_vk_from_grkw_only(", "void network_node_recover_vk_from_grkw_only("),
    ]
    blocks_by_name = _vk_recovery_code_functions_blocks()
    needed_blocks: list[str] = []
    for key, call_marker, definition_marker in helper_specs:
        if definition_marker in draft:
            continue
        if call_marker in draft:
            needed_blocks.append(blocks_by_name[key])
    if not needed_blocks:
        return draft
    block = "\n\n".join(needed_blocks).rstrip()
    if "CODE_FUNCTIONS:" in draft:
        return draft.replace("CODE_FUNCTIONS:\n", "CODE_FUNCTIONS:\n\n" + block + "\n", 1)
    for marker in ("CODE:\n", "BEGIN_T0:\n", "T1_T2:\n"):
        if marker in draft:
            return draft.replace(marker, "CODE_FUNCTIONS:\n\n" + block + "\n\n" + marker, 1)
    return draft.rstrip() + "\n\nCODE_FUNCTIONS:\n\n" + block + "\n"


def _apply_final_retained_recovery_profiles_to_draft(
    draft: str,
    *,
    case_id_symbol: str,
    recovery_profiles: Sequence[dict] | None,
    template_internal_nodes: Sequence[str] | None = None,
) -> str:
    """Add T1_T2 recovery for Pack cases reduced to common final ports.

    The G/Ihis template is built from same-shaped final retained equations.
    Recovered internal voltages are case-specific and therefore must be emitted
    separately instead of forcing raw pack internals to have identical topology.
    """
    profiles = list(recovery_profiles or [])
    if not profiles:
        return draft
    if not any(profile.get("recovery_nodes") for profile in profiles):
        return draft

    template_internal_nodes = [str(node) for node in (template_internal_nodes or [])]
    template_node_to_row = {node: row for row, node in enumerate(template_internal_nodes)}

    for row, node in enumerate(template_internal_nodes or []):
        c_name = _c_identifier_name(str(node), f"K{row + 1}")
        draft = draft.replace(
            f"    {c_name} = get_CODE(&Vk_code, {row}, 0);\n",
            "",
        )

    def recovery_profile_c_name(profile: dict, node: object, row: int) -> str:
        node_key = str(node)
        c_names = profile.get("recovery_node_c_names") or {}
        return _c_identifier_name(str(c_names.get(node_key, node_key)), f"K{row + 1}")

    def super_profile_c_name(profile: dict, node: object, col: int) -> str:
        node_key = str(node)
        c_names = profile.get("super_node_c_names") or {}
        return _c_identifier_name(str(c_names.get(node_key, node_key)), f"V{col + 1}")

    def can_use_template_vk_recovery() -> bool:
        if not template_node_to_row:
            return False
        if "    /* One variable per eliminated node, in effective k order. */\n" not in draft:
            return False
        for profile in profiles:
            for node in profile.get("recovery_nodes") or []:
                if str(node) not in template_node_to_row:
                    return False
        return True

    def default_recovery_preamble() -> str:
        start_marker = "    /* Internal-node voltage recovery after solved retained-node voltages are available. */\n"
        assignment_marker = "    /* One variable per eliminated node, in effective k order. */\n"
        start = draft.find(start_marker)
        if start < 0:
            return ""
        assignment_start = draft.find(assignment_marker, start)
        if assignment_start < 0:
            return ""
        return draft[start:assignment_start].rstrip("\n")

    def case_indented_recovery_preamble(preamble: str) -> list[str]:
        lines: list[str] = []
        for line in preamble.splitlines():
            if line.startswith("    "):
                line = line[4:]
            lines.append(("        " + line) if line else "")
        return lines

    def split_recovery_preamble(preamble: str) -> tuple[list[str], str]:
        """Hoist common Vr loads while keeping Vk math case-specific."""
        hoisted: list[str] = []
        compute: list[str] = []
        in_vr_setup = True
        for line in preamble.splitlines():
            stripped = line.strip()
            if in_vr_setup and (
                stripped == "/* Internal-node voltage recovery after solved retained-node voltages are available. */"
                or stripped.startswith("set_CODE(&Vr_code,")
                or not stripped
            ):
                hoisted.append(line)
                continue
            in_vr_setup = False
            compute.append(line)
        return hoisted, "\n".join(compute).rstrip("\n")

    def vk_internal_count_expr() -> str:
        return "internal_active" if "internal_active" in draft else "INTERNAL_NODES"

    def vk_diag_helper_call(active_nr_expr: str) -> str:
        return (
            f"    network_node_recover_vk_diag({active_nr_expr}, {vk_internal_count_expr()}, "
            "&tmp_Grk_W_code, &Vr_code, &Ihisk_code, &W_code, &Vk_code);"
        )

    def vk_matrix_helper_call(active_nr_expr: str) -> str:
        return (
            f"    network_node_recover_vk_matrix({active_nr_expr}, {vk_internal_count_expr()}, "
            "&tmp_Grk_W_code, &tmp_W_Gkr_code, &Vr_code, &W_code, &Ihisk_code, "
            "&tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code, &tmp_Vk_sum_code, &Vk_code);"
        )

    def vk_matrix_wgkr_helper_call(active_nr_expr: str) -> str:
        return (
            "    network_node_recover_vk_matrix_from_wgkr(&tmp_W_Gkr_code, &Vr_code, &W_code, &Ihisk_code, "
            "&tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code, &tmp_Vk_sum_code, &Vk_code);"
        )

    def vk_wgkr_only_helper_call(active_nr_expr: str) -> str:
        return (
            f"    network_node_recover_vk_from_wgkr_only({active_nr_expr}, {vk_internal_count_expr()}, "
            "&tmp_W_Gkr_code, &Vr_code, &Vk_code);"
        )

    def vk_grkw_only_helper_call(active_nr_expr: str) -> str:
        return (
            f"    network_node_recover_vk_from_grkw_only({active_nr_expr}, {vk_internal_count_expr()}, "
            "&tmp_Grk_W_code, &Vr_code, &Vk_code);"
        )

    def replace_vk_recovery_with_helper(block: str, active_nr_expr: str, *, diagonal: bool) -> str:
        helper_call = vk_diag_helper_call(active_nr_expr) if diagonal else vk_matrix_helper_call(active_nr_expr)
        full_matrix_replaced = False
        retained_exprs = list(dict.fromkeys([active_nr_expr, "RETAINED_NODES", "NR", "node_active"]))
        simple_matrix_recovery = "\n".join([
            "    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);",
            "    matrix_scalarMult_CODE(&Vk_code, &tmp_W_Gkr_Vr_code, -1.0);",
        ])
        precomputed_wgkr_matrix_recovery = "\n".join([
            "    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);",
            "    matrix_matXvec_CODE(&tmp_W_Ihisk_code, &W_code, &Ihisk_code);",
            "    matrix_add_CODE(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code);",
            "    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);",
        ])
        if precomputed_wgkr_matrix_recovery in block:
            replacement = helper_call if diagonal else vk_matrix_wgkr_helper_call(active_nr_expr)
            block = block.replace(precomputed_wgkr_matrix_recovery, replacement)
            full_matrix_replaced = True
        for internal_expr in ("INTERNAL_NODES", "internal_active"):
            for retained_expr in retained_exprs:
                symmetry_reuse = "\n".join([
                    "    /* Symmetry reuse: W * Gkr = transpose(Grk * W). */",
                    f"    for (int row = 0; row < {internal_expr}; row++) {{",
                    f"        for (int col = 0; col < {retained_expr}; col++) {{",
                    "            set_CODE(&tmp_W_Gkr_code, row, col, get_CODE(&tmp_Grk_W_code, col, row));",
                    "        }",
                    "    }",
                ])
                matrix_recovery = "\n".join([
                    symmetry_reuse,
                    "    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);",
                    "    matrix_matXvec_CODE(&tmp_W_Ihisk_code, &W_code, &Ihisk_code);",
                    "    matrix_add_CODE(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code);",
                    "    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);",
                ])
                if matrix_recovery in block:
                    block = block.replace(matrix_recovery, helper_call)
                    full_matrix_replaced = True
                simple_recovery_from_grkw = "\n".join([
                    symmetry_reuse,
                    simple_matrix_recovery,
                ])
                if simple_recovery_from_grkw in block:
                    block = block.replace(
                        simple_recovery_from_grkw,
                        "\n".join([
                            "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
                            vk_grkw_only_helper_call(active_nr_expr),
                        ]),
                    )
                    full_matrix_replaced = True
        symmetry_reuse_pattern = (
            r"    /\* Symmetry reuse: W \* Gkr = transpose\(Grk \* W\)\. \*/\n"
            r"    for \((?:int )?row = 0; row < (?:INTERNAL_NODES|internal_active); row\+\+\) \{\n"
            r"        for \((?:int )?col = 0; col < (?:RETAINED_NODES|NR|node_active); col\+\+\) \{\n"
            r"            set_CODE\(&tmp_W_Gkr_code, row, col, get_CODE\(&tmp_Grk_W_code, col, row\)\);\n"
            r"        \}\n"
            r"    \}"
        )
        full_matrix_pattern = re.compile(
            symmetry_reuse_pattern
            + r"\n    matrix_matXvec_CODE\(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code\);"
            + r"\n    matrix_matXvec_CODE\(&tmp_W_Ihisk_code, &W_code, &Ihisk_code\);"
            + r"\n    matrix_add_CODE\(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code\);"
            + r"\n    matrix_scalarMult_CODE\(&Vk_code, &tmp_Vk_sum_code, -1\.0\);"
        )
        block, replaced = full_matrix_pattern.subn(helper_call, block, count=1)
        if replaced:
            full_matrix_replaced = True
        simple_from_grkw_pattern = re.compile(
            symmetry_reuse_pattern
            + r"\n    matrix_matXvec_CODE\(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code\);"
            + r"\n    matrix_scalarMult_CODE\(&Vk_code, &tmp_W_Gkr_Vr_code, -1\.0\);"
        )
        block, replaced = simple_from_grkw_pattern.subn(
            "\n".join([
                "    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);",
                vk_grkw_only_helper_call(active_nr_expr),
            ]),
            block,
            count=1,
        )
        if replaced:
            full_matrix_replaced = True
        if simple_matrix_recovery in block:
            block = block.replace(simple_matrix_recovery, vk_wgkr_only_helper_call(active_nr_expr))
            full_matrix_replaced = True
        return block

    def diagonal_w_case_ids_from_draft() -> set[int]:
        marker = "Case-resolved Gkk inverse."
        start = draft.find(marker)
        if start < 0:
            return set()
        end = draft.find("CODE-SIDE IHIS VALUE SETUP", start)
        if end < 0:
            end = draft.find("set_CODE(&Ihisr_code", start)
        block = draft[start:end if end >= 0 else len(draft)]
        scalar_cases: set[int] = set()
        pending_cases: list[int] = []
        body_lines: list[str] = []
        for line in block.splitlines():
            match = re.match(r"\s*case\s+(\d+):", line)
            if match:
                pending_cases.append(int(match.group(1)))
                continue
            if pending_cases:
                body_lines.append(line)
                if "break;" in line:
                    body = "\n".join(body_lines)
                    if "1.0 / get_CODE(&Gkk_code" in body and "mat_" not in body:
                        scalar_cases.update(pending_cases)
                    pending_cases = []
                    body_lines = []
        return scalar_cases

    def guard_recovery_only_matrix_setup(next_draft: str, active_case_ids: Sequence[int]) -> str:
        if not active_case_ids:
            return next_draft
        shared_matrix_markers = (
            "MATRIX_ Grr_code",
            "MATRIX_ Grk_code",
            "MATRIX_ Gred_code",
            "MATRIX_ Ihisr_code",
            "MATRIX_ Ihisred_code",
            "tmp_Grk_W_code",
            "tmp_Grk_W_Gkr_code",
            "tmp_Grk_W_Ihisk_code",
        )
        if any(marker in next_draft for marker in shared_matrix_markers):
            return next_draft

        def indent_case_body(block: str) -> list[str]:
            out: list[str] = []
            for line in block.rstrip("\n").splitlines():
                if line.startswith("    "):
                    line = line[4:]
                out.append(("        " + line) if line else "")
            return out

        ram_start_marker = "    err += matrixDim(&Gkr_code"
        ram_end_marker = "    matrix_register(&tmp_W_Gkr_Vr_code);\n"
        ram_start = next_draft.find(ram_start_marker)
        ram_end = next_draft.find(ram_end_marker, ram_start)
        if ram_start >= 0 and ram_end >= 0:
            ram_end += len(ram_end_marker)
            ram_block = next_draft[ram_start:ram_end]
            guarded_ram = [
                "    /* Recovery-only matrix setup is skipped for Pack cases with no recovered internal nodes. */",
                f"    switch ({case_id_symbol}) {{",
            ]
            for case_id in active_case_ids:
                guarded_ram.append(f"    case {case_id}:")
            guarded_ram.extend(indent_case_body(ram_block))
            guarded_ram.extend([
                "        break;",
                "    default:",
                "        break;",
                "    }",
            ])
            next_draft = next_draft[:ram_start] + "\n".join(guarded_ram) + "\n" + next_draft[ram_end:]

        code_start_marker = "    if (!rtds_matrix_code_ready) {\n"
        code_end_marker = "        rtds_matrix_code_ready = 1;\n    }\n"
        code_start = next_draft.find(code_start_marker)
        code_end = next_draft.find(code_end_marker, code_start)
        if code_start >= 0 and code_end >= 0:
            code_end += len(code_end_marker)
            code_block = next_draft[code_start:code_end]
            condition = _case_condition_from_ids(case_id_symbol, active_case_ids)
            guarded_code = [
                "    /* Recovery-only MATRIX_ conditioning is needed only when this Pack case recovers internals. */",
                f"    if ({condition}) {{",
                *indent_case_body(code_block),
                "    }",
            ]
            next_draft = next_draft[:code_start] + "\n".join(guarded_code) + "\n" + next_draft[code_end:]

        return next_draft

    declared = set(re.findall(r"\bdouble\s+([A-Za-z_]\w*)\b", draft))
    recovery_names: list[str] = []
    recovery_name_set: set[str] = set()
    recovery_symbol_names: set[str] = set()
    external_c_names: set[str] = set()
    use_template_vk_recovery = can_use_template_vk_recovery()
    recovery_preamble = default_recovery_preamble() if use_template_vk_recovery else ""
    hoisted_recovery_preamble: list[str] = []
    case_recovery_preamble = recovery_preamble
    scalar_case_recovery_preamble = ""
    diagonal_w_case_ids: set[int] = set()
    if recovery_preamble:
        hoisted_recovery_preamble, case_recovery_preamble = split_recovery_preamble(recovery_preamble)
        matrix_case_recovery_preamble = replace_vk_recovery_with_helper(
            case_recovery_preamble,
            "node_active" if "node_active" in case_recovery_preamble else "RETAINED_NODES",
            diagonal=False,
        )
        scalar_case_recovery_preamble = replace_vk_recovery_with_helper(
            case_recovery_preamble,
            "node_active" if "node_active" in case_recovery_preamble else "RETAINED_NODES",
            diagonal=True,
        )
        if scalar_case_recovery_preamble != matrix_case_recovery_preamble:
            diagonal_w_case_ids = diagonal_w_case_ids_from_draft()
        case_recovery_preamble = matrix_case_recovery_preamble
    active_recovery_case_ids: list[int] = []

    for profile in profiles:
        for col, node in enumerate(profile.get("super_nodes") or []):
            external_c_names.add(super_profile_c_name(profile, node, col))
        for row, node in enumerate(profile.get("recovery_nodes") or []):
            c_name = recovery_profile_c_name(profile, node, row)
            if c_name not in recovery_name_set:
                recovery_name_set.add(c_name)
                recovery_names.append(c_name)
        for key in ("K_v", "K_h"):
            value = profile.get(key)
            if value is None or value == []:
                continue
            try:
                matrix = sp.Matrix(value)
            except Exception:
                continue
            if not use_template_vk_recovery:
                recovery_symbol_names.update(_symbols_in_matrices(matrix))

    declarations: list[str] = []
    for name in recovery_names:
        if name not in declared:
            declarations.append(f"    double {name} = 0.0;")
            declared.add(name)

    recovery_symbol_names = {
        _c_identifier_name(name, name)
        for name in recovery_symbol_names
        if _c_identifier_name(name, name) not in declared
        and _c_identifier_name(name, name) not in recovery_name_set
        and _c_identifier_name(name, name) not in external_c_names
    }
    if recovery_symbol_names:
        if declarations:
            declarations.append("")
        declarations.append("    /* User symbols used only by case-specific voltage recovery. */")
        declarations.extend(f"    double {name} = 0.0;" for name in sorted(recovery_symbol_names))

    if declarations:
        static_marker = "STATIC:\n"
        if static_marker in draft:
            draft = draft.replace(
                static_marker,
                static_marker + "\n" + "\n".join(declarations) + "\n",
                1,
            )

    lines = [
        "    /* Case-specific voltage recovery for Pack cases with different internal reductions. */",
        f"    switch ({case_id_symbol}) {{",
    ]
    for profile in profiles:
        case_ids = [int(case_id) for case_id in (profile.get("case_ids") or [])]
        if not case_ids:
            continue
        if use_template_vk_recovery and (profile.get("recovery_nodes") or []):
            active_recovery_case_ids.extend(case_ids)
        for case_id in case_ids:
            lines.append(f"    case {case_id}:")
        if use_template_vk_recovery:
            assignment_lines = []
            for row, node in enumerate(profile.get("recovery_nodes") or []):
                template_row = row if "internal_active" in draft else template_node_to_row[str(node)]
                c_name = recovery_profile_c_name(profile, node, row)
                assignment_lines.append(f"        {c_name} = get_CODE(&Vk_code, {template_row}, 0);")
            selected_preamble = case_recovery_preamble
            if (
                len(profile.get("recovery_nodes") or []) == 1
                or (diagonal_w_case_ids and all(case_id in diagonal_w_case_ids for case_id in case_ids))
            ):
                selected_preamble = scalar_case_recovery_preamble
            if assignment_lines and selected_preamble:
                lines.extend(case_indented_recovery_preamble(selected_preamble))
        else:
            assignment_lines = _recovery_assignment_lines(profile)
        if assignment_lines:
            lines.extend(assignment_lines)
        else:
            lines.append("        /* This Pack case has no recovered internal nodes. */")
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
    ])

    stale_comments = [
        "    /* No internal nodes were eliminated, so there is no Vk recovery step. */",
        "    /* DummyNodeBlock isolated internal nodes are not recovered. */",
    ]
    for comment in stale_comments:
        draft = draft.replace(comment + "\n", "")
        draft = draft.replace(comment, "")
    if use_template_vk_recovery:
        if recovery_preamble:
            draft = _remove_default_voltage_recovery_block(draft)
            marker = "T1_T2:\n"
            if marker in draft:
                hoisted = "\n".join(hoisted_recovery_preamble).rstrip("\n")
                prefix = (hoisted + "\n") if hoisted else ""
                draft = draft.replace(marker, marker + prefix + "\n".join(lines) + "\n", 1)
                draft = guard_recovery_only_matrix_setup(draft, active_recovery_case_ids)
                return _ensure_vk_recovery_code_functions(draft)
        assignment_marker = "    /* One variable per eliminated node, in effective k order. */\n"
        start = draft.find(assignment_marker)
        if start >= 0:
            pos = start + len(assignment_marker)
            assignment_re = re.compile(r"    [A-Za-z_]\w* = get_CODE\(&Vk_code, \d+, 0\);\n")
            while True:
                match = assignment_re.match(draft, pos)
                if not match:
                    break
                pos = match.end()
            if draft.startswith("\n", pos):
                pos += 1
            return draft[:start] + "\n".join(lines) + "\n" + draft[pos:]
    draft = _remove_default_voltage_recovery_block(draft)
    marker = "T1_T2:\n"
    if marker in draft:
        return draft.replace(marker, marker + "\n".join(lines) + "\n", 1)
    return draft.rstrip() + "\nT1_T2:\n" + "\n".join(lines) + "\n"


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
    assignments = _recovery_assignment_pairs(item)
    return [f"{indent}{lhs} = {_ccode(rhs)};" for lhs, rhs in assignments]


def _recovery_assignment_pairs(item: dict) -> list[tuple[str, sp.Expr]]:
    recovery_nodes = list(item.get("recovery_nodes") or [])
    if not recovery_nodes:
        return []
    external_nodes = list(item.get("super_nodes") or [])
    super_node_c_names = {
        str(key): str(value)
        for key, value in (item.get("super_node_c_names") or {}).items()
    }
    recovery_node_c_names = {
        str(key): str(value)
        for key, value in (item.get("recovery_node_c_names") or {}).items()
    }
    K_v = item.get("K_v")
    K_h = item.get("K_h")
    K_v = sp.Matrix(K_v) if K_v is not None else sp.zeros(len(recovery_nodes), len(external_nodes))
    K_h = sp.Matrix(K_h) if K_h is not None else sp.zeros(len(recovery_nodes), 1)
    assignments: list[tuple[str, sp.Expr]] = []
    for row, node in enumerate(recovery_nodes):
        rhs = sp.Integer(0)
        for col, external in enumerate(external_nodes):
            coeff = sp.sympify(K_v[row, col])
            if sp.simplify(coeff) == 0:
                continue
            external_name = super_node_c_names.get(str(external), str(external))
            rhs += coeff * sp.Symbol(_c_identifier_name(external_name, f"V{col + 1}"))
        history = sp.sympify(K_h[row, 0])
        if sp.simplify(history) != 0:
            rhs += history
        recovery_name = recovery_node_c_names.get(str(node), str(node))
        assignments.append((_c_identifier_name(recovery_name, f"K{row + 1}"), sp.sympify(rhs)))
    return assignments


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
        "    /* RAM G aliases are intentionally not materialized here: each finalized profile",
        "       writes its complete RAM-owned G entries directly to g_mat_over below. */",
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
    return _use_readable_dimension_names(_ensure_static_blank_line("\n".join(lines)))


def _force_scalar_profile_results(payload: dict) -> tuple[object, list[dict], list[str], list[dict]]:
    profiles = payload.get("case_profiles") or []
    profile_set = build_finalization_profiles(profiles)
    final_results: list[dict] = []
    warnings: list[str] = [
        "Info: force_scalar codegen expands each init-time case to scalar Schur formulas and skips runtime Gkk/W MATRIX_ objects."
    ]
    diagnoses: list[dict] = []

    for case_index, source_profile in enumerate(profiles):
        case_payload = source_profile.get("payload") or {}
        super_result = _reduced_super_result_from_payload(case_payload)
        final_profile = profile_set.case_profiles[case_index]
        final = finalize_profile_result(
            super_result["G"],
            super_result["Ihis"],
            super_result["external_nodes"],
            final_profile,
        )
        display_names = {
            str(key): str(value)
            for key, value in (case_payload.get("node_display_names") or {}).items()
        }
        internal_count = len(super_result["internal_nodes"])
        if internal_count > 3:
            warnings.append(
                f"Warning: case {case_index} force_scalar expands {internal_count} internal nodes; "
                "generated C may become large."
            )
        diagnoses.append({
            "case_id": case_index,
            "internal_active": internal_count,
            "recommended_mode": "scalar",
            "reason": (
                "No active internal node; scalar path bypasses Schur matrices."
                if internal_count == 0
                else "force_scalar requested; Schur result is emitted as case-specific scalar formulas."
            ),
        })
        final_results.append({
            "index": case_index,
            "name": source_profile.get("name") or f"Case {case_index}",
            "profile": final_profile,
            "final": final,
            "symbol_table": case_payload.get("symbol_dependency_table_tagged") or case_payload.get("symbol_dependency_table") or {},
            "recovery_nodes": super_result["internal_nodes"],
            "super_nodes": super_result["external_nodes"],
            "K_v": super_result["K_v"],
            "K_h": super_result["K_h"],
            "super_node_c_names": display_names,
            "recovery_node_c_names": display_names,
            "node_c_names": display_names,
        })

    return profile_set, final_results, list(dict.fromkeys(warnings)), diagnoses


def _build_force_scalar_multi_case_c_draft(
    case_id_symbol: str,
    profile_set,
    final_results: list[dict],
) -> tuple[str, list[dict], dict[str, object]]:
    symbols = _symbols_in_matrices(
        *(item["final"].G for item in final_results),
        *(item["final"].Ihis for item in final_results),
        *(sp.Matrix(item.get("K_v") or []) for item in final_results),
        *(sp.Matrix(item.get("K_h") or []) for item in final_results),
    )
    declarations = [f"    double {name} = 0.0;" for name in symbols if _c_identifier_name(name, name) == name]
    ccode_cache: dict[str, str] = {}
    stage_cache: dict[tuple[int, str], str] = {}
    dynamic_gvalues: dict[tuple[str, str], dict] = {}
    dynamic_case_assignments: dict[int, list[tuple[str, sp.Expr]]] = {}
    gvalue_conditions: list[dict] = []
    scalar_cse_stats = _empty_scalar_cse_stats()

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

    def node_c_name(item: dict, node: object, index: int) -> str:
        names = item.get("node_c_names") or {}
        return _c_identifier_name(str(names.get(str(node), node)), f"N{index + 1}")

    final_node_orders = [
        tuple(str(node) for node in item["final"].nodes)
        for item in final_results
    ]
    shared_retained_nodes = (
        list(final_node_orders[0])
        if final_node_orders and all(order == final_node_orders[0] for order in final_node_orders)
        else None
    )

    for item in final_results:
        case_index = int(item.get("index", 0))
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
                left_name = node_c_name(item, left, row)
                right_name = node_c_name(item, right, col)
                var = f"varG_{left_name}_{right_name}"
                entry = dynamic_gvalues.setdefault((left, right), {
                    "var": var,
                    "left": left,
                    "right": right,
                    "cases": [],
                })
                entry["cases"].append(case_index)
                dynamic_case_assignments.setdefault(case_index, []).append((var, expr))

    static_declarations: list[str] = []
    local_static_declarations: list[str] = list(declarations)
    case_shared_temps: dict[int, list[tuple[str, sp.Expr]]] = {}
    case_ram_lines: dict[int, list[str]] = {}
    case_code_lines: dict[int, list[str]] = {}
    case_inj_lines: dict[int, list[str]] = {}
    case_recovery_lines: dict[int, list[str]] = {}

    for item in final_results:
        case_index = int(item.get("index", 0))
        final = item["final"]
        symbol_table = item.get("symbol_table") or {}
        ram_assignments: list[tuple[str, sp.Expr]] = []
        for row in range(final.G.rows):
            for col in range(final.G.cols):
                expr = sp.sympify(final.G[row, col])
                if expr == 0 or stage_of(expr, symbol_table) != "RAM":
                    continue
                ram_assignments.append((f"g_mat_over[{row}][{col}]", expr))
        recovery_assignments = _recovery_assignment_pairs(item)
        inj_assignments = [
            (f"Inj{node_c_name(item, node, row)}", sp.sympify(final.Ihis[row, 0]))
            for row, node in enumerate(final.nodes)
        ]
        runtime_assignments = [
            *dynamic_case_assignments.get(case_index, []),
            *inj_assignments,
        ]
        shared_temps, shared_substitutions, shared_stats = _shared_scalar_denominator_temps(
            [expr for _lhs, expr in ram_assignments],
            [expr for _lhs, expr in runtime_assignments],
            [expr for _lhs, expr in recovery_assignments],
            symbol_table=symbol_table,
            temp_prefix=f"scalar_case{case_index}",
        )
        case_shared_temps[case_index] = shared_temps
        _merge_scalar_cse_stats(scalar_cse_stats, shared_stats)
        static_declarations.extend(f"    double {name} = 0.0;" for name, _expr in shared_temps)

        ram_lines, ram_stats = _emit_scalar_reuse_assignment_lines(
            ram_assignments,
            temp_prefix=f"scalar_case{case_index}_ram",
            indent=8,
            substitutions=shared_substitutions,
            declare_temps=False,
        )
        case_ram_lines[case_index] = ram_lines
        _merge_scalar_cse_stats(scalar_cse_stats, ram_stats)
        local_static_declarations.extend(_scalar_temp_declarations(ram_stats))

        if case_index in dynamic_case_assignments:
            code_lines, code_stats = _emit_scalar_reuse_assignment_lines(
                dynamic_case_assignments[case_index],
                temp_prefix=f"scalar_case{case_index}_code",
                indent=8,
                substitutions=shared_substitutions,
                declare_temps=False,
            )
            case_code_lines[case_index] = code_lines
            _merge_scalar_cse_stats(scalar_cse_stats, code_stats)
            static_declarations.extend(_scalar_temp_declarations(code_stats))

        inj_lines, inj_stats = _emit_scalar_reuse_assignment_lines(
            inj_assignments,
            temp_prefix=f"scalar_case{case_index}_inj",
            indent=8,
            substitutions=shared_substitutions,
            declare_temps=False,
        )
        case_inj_lines[case_index] = inj_lines
        _merge_scalar_cse_stats(scalar_cse_stats, inj_stats)
        static_declarations.extend(_scalar_temp_declarations(inj_stats))

        if recovery_assignments:
            recovery_lines, recovery_stats = _emit_scalar_reuse_assignment_lines(
                recovery_assignments,
                temp_prefix=f"scalar_case{case_index}_t1t2",
                indent=8,
                substitutions=shared_substitutions,
                declare_temps=False,
            )
            case_recovery_lines[case_index] = recovery_lines
            _merge_scalar_cse_stats(scalar_cse_stats, recovery_stats)
            static_declarations.extend(_scalar_temp_declarations(recovery_stats))

    lines = [
        "/* Multi-case C draft with scalar-expanded Schur formulas.",
        "   Each init-time case is reduced to scalar final G/Ihis formulas;",
        "   runtime Gkk/W MATRIX_ objects are intentionally not generated. */",
        "",
        "STATIC:",
        *dict.fromkeys(static_declarations),
        "",
        "LOCAL_STATIC:",
        *(list(dict.fromkeys(local_static_declarations)) or ["    /* No user symbols are required by the scalar-expanded formulas. */"]),
        "",
        "RAM_PASS1:",
        "    int err = 0;",
        "    int row;",
        "    int col;",
    ]
    if shared_retained_nodes is not None:
        shared_dim = len(shared_retained_nodes)
        lines.append("    /* Shared retained-node layout for scalar-expanded RAM G stamps. */")
        for node_index, node in enumerate(shared_retained_nodes):
            lines.append(f"    g_mat_nods[{node_index}] = getNodeNum(comp, \"{node}\");")
        lines.extend([
            f"    for (row = 0; row < {shared_dim}; row++) {{",
            f"        for (col = 0; col < {shared_dim}; col++) {{",
            "            g_mat_over[row][col] = 0.0;",
            "        }",
            "    }",
            "    /* Case-specific scalar-expanded RAM G values. */",
            f"    switch ({case_id_symbol}) {{",
        ])
    else:
        lines.extend([
            "    /* Case-specific scalar-expanded RAM G stamp. */",
            f"    switch ({case_id_symbol}) {{",
        ])
    for item in final_results:
        case_index = int(item.get("index", 0))
        final = item["final"]
        nodes = list(final.nodes)
        dim = len(nodes)
        lines.append(f"    case {case_index}:")
        if shared_retained_nodes is None:
            for node_index, node in enumerate(nodes):
                lines.append(f"        g_mat_nods[{node_index}] = getNodeNum(comp, \"{node}\");")
            lines.extend([
                f"        for (row = 0; row < {dim}; row++) {{",
                f"            for (col = 0; col < {dim}; col++) {{",
                "                g_mat_over[row][col] = 0.0;",
                "            }",
                "        }",
            ])
        for name, expr in case_shared_temps.get(case_index, []):
            lines.append(f"        {name} = {_ccode(expr)};")
        lines.extend(case_ram_lines.get(case_index, []))
        if shared_retained_nodes is None:
            lines.append(f"        setupGMatrix({dim});")
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        err = 1;",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        f"                       \"Unknown scalar-expanded multi-case profile %d for component %s.\", {case_id_symbol}, Name);",
        "        break;",
        "    }",
    ])
    if shared_retained_nodes is not None:
        lines.extend([
            "    if (err == 0) {",
            f"        setupGMatrix({len(shared_retained_nodes)});",
            "    }",
        ])
    lines.extend([
        "    if (err > 0) {",
        "        reportError_RW(\"network_node\", STOP_IMMEDIATELY_CONDITION,",
        "                       \"RTDS scalar-expanded allocation failed for component %s.\", Name);",
        "    }",
        "",
    ])
    if dynamic_gvalues:
        lines.extend([
            "GVALUES:",
            "    /* Dynamic scalar-expanded final-G stamp handles. */",
        ])
        for entry in dynamic_gvalues.values():
            condition = " || ".join(f"{case_id_symbol} == {case_index}" for case_index in sorted(set(entry["cases"])))
            lines.append(
                f"    double {entry['var']} = createGValue(\"{entry['var']}\", "
                f"\"{entry['left']}\", \"{entry['right']}\", 0, \"{condition}\");"
            )
            gvalue_conditions.append({
                "var": entry["var"],
                "left": entry["left"],
                "right": entry["right"],
                "condition": condition,
            })
        lines.append("")
    lines.extend([
        "CODE:",
        "BEGIN_T0:",
    ])
    if dynamic_case_assignments:
        lines.extend([
            "    /* Case-specific CODE-side scalar GValue refresh. */",
            f"    switch ({case_id_symbol}) {{",
        ])
        for case_index in sorted(dynamic_case_assignments):
            lines.append(f"    case {case_index}:")
            lines.extend(case_code_lines.get(case_index, []))
            lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
            "",
        ])
    lines.extend([
        "    /* Node injection currents follow the scalar-expanded retained-node order. */",
        f"    switch ({case_id_symbol}) {{",
    ])
    for item in final_results:
        case_index = int(item.get("index", 0))
        final = item["final"]
        lines.append(f"    case {case_index}:")
        lines.extend(case_inj_lines.get(case_index, []))
        lines.append("        break;")
    lines.extend([
        "    default:",
        "        break;",
        "    }",
        "",
        "T1_T2:",
    ])
    if any(item.get("recovery_nodes") for item in final_results):
        lines.extend([
            "    /* Case-specific scalar-expanded internal-node voltage recovery. */",
            f"    switch ({case_id_symbol}) {{",
        ])
        for item in final_results:
            case_index = int(item.get("index", 0))
            lines.append(f"    case {case_index}:")
            recovery_lines = case_recovery_lines.get(case_index, [])
            if recovery_lines:
                lines.extend(recovery_lines)
            else:
                lines.append("        /* This Pack case has no recovered internal nodes. */")
            lines.append("        break;")
        lines.extend([
            "    default:",
            "        break;",
            "    }",
        ])
    else:
        lines.append("    /* No internal nodes were eliminated, so there is no Vk recovery step. */")
    return _use_readable_dimension_names(_ensure_static_blank_line("\n".join(lines))), gvalue_conditions, scalar_cse_stats


def _build_force_scalar_multi_case_response(payload: dict) -> dict:
    profiles = payload.get("case_profiles") or []
    case_id_symbol = str(payload.get("case_id_symbol") or "case_id")
    profile_set, final_results, warnings, diagnoses = _force_scalar_profile_results(payload)
    confirmed = bool(payload.get("force_scalar_confirmed") or payload.get("scalar_preflight_confirmed"))
    scalar_preflight = _force_scalar_preflight_summary(final_results, confirmed=confirmed)
    if scalar_preflight.get("blocked"):
        return _build_force_scalar_preflight_blocked_response(
            payload,
            case_id_symbol,
            profile_set,
            final_results,
            warnings,
            diagnoses,
            scalar_preflight,
        )
    return _build_scalar_expanded_multi_case_response(
        payload,
        case_id_symbol,
        profile_set,
        final_results,
        warnings,
        diagnoses,
        scalar_preflight=scalar_preflight,
        codegen_mode="force scalar Schur expansion",
    )


def _build_scalar_expanded_multi_case_response(
    payload: dict,
    case_id_symbol: str,
    profile_set,
    final_results: list[dict],
    warnings: list[str],
    diagnoses: list[dict],
    *,
    scalar_preflight: dict[str, object] | None = None,
    codegen_mode: str,
) -> dict:
    profiles = payload.get("case_profiles") or []
    c_draft, gvalue_conditions, scalar_cse_stats = _build_force_scalar_multi_case_c_draft(
        case_id_symbol,
        profile_set,
        final_results,
    )
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
        "warnings": warnings,
        "multi_case": {
            "codegen_mode": codegen_mode,
            "fast_path": "case_scalar_schur_expansion",
            "profile_count": len(profiles),
            "aliases": {},
            "gvalue_conditions": gvalue_conditions,
            "uses_case_conditional_gvalue": bool(gvalue_conditions),
            "scalar_cse": scalar_cse_stats,
            "scalar_preflight": scalar_preflight or _force_scalar_preflight_summary(
                final_results,
                confirmed=True,
            ),
            "external_nodes": profile_set.super_node_order,
            "effective_internal_nodes": [],
            "block_type": "scalar_expanded",
            "recovery_profiles": diagnoses,
            "template_summary": {
                "super_nodes": profile_set.super_node_order,
                "NR_SUPER": len(profile_set.super_node_order),
                "NR_FINAL_MAX": max((item["final"].G.rows for item in final_results), default=0),
                "profile_dimensions": [item["final"].G.rows for item in final_results],
            },
            "c_draft": c_draft,
        },
    }


def _try_build_auto_scalar_multi_case_response(payload: dict) -> dict | None:
    has_explicit_codegen_mode = "elimination_codegen_mode" in payload or "codegen_mode" in payload
    if not has_explicit_codegen_mode:
        return None
    if str(payload.get("elimination_codegen_mode") or payload.get("codegen_mode") or "auto") != "auto":
        return None
    if payload.get("runtime_case_groups"):
        return None
    profiles = payload.get("case_profiles") or []
    if not profiles or _profiles_have_dummy_finalization(profiles):
        return None
    try:
        profile_set, final_results, _warnings, diagnoses = _force_scalar_profile_results(payload)
    except Exception:
        return None
    max_internal = max((int(item.get("internal_active") or 0) for item in diagnoses), default=0)
    max_retained = max((int(item["final"].G.rows) for item in final_results), default=0)
    formula_cost = _dummy_finalized_formula_cost(final_results)
    if max_internal > 1 or max_retained > 4 or formula_cost > 120:
        return None
    warnings = [
        "Info: auto codegen selected scalar Schur expansion for a low-complexity init-time multi-case network; "
        "runtime Gkk/W MATRIX_ objects are skipped."
    ]
    for diagnosis in diagnoses:
        if int(diagnosis.get("internal_active") or 0) == 0:
            diagnosis["reason"] = "No active internal node; scalar path bypasses Schur matrices."
        else:
            diagnosis["reason"] = "Auto selected scalar Schur expansion because this case has at most one active internal node and low expression cost."
    return _build_scalar_expanded_multi_case_response(
        payload,
        str(payload.get("case_id_symbol") or "case_id"),
        profile_set,
        final_results,
        warnings,
        diagnoses,
        codegen_mode="auto scalar Schur expansion",
    )


def _dummy_finalized_formula_cost(final_results: list[dict]) -> int:
    cost = 0
    for item in final_results:
        final = item["final"]
        for expr in list(final.G) + list(final.Ihis):
            cost += int(sp.count_ops(sp.sympify(expr)))
    return cost


_FORCE_SCALAR_PREFLIGHT_WARN_OPS = 3000
_FORCE_SCALAR_PREFLIGHT_DANGER_OPS = 12000
_FORCE_SCALAR_PREFLIGHT_WARN_CHARS = 30000
_FORCE_SCALAR_PREFLIGHT_DANGER_CHARS = 120000
_FORCE_SCALAR_PREFLIGHT_DANGER_EXPR_CHARS = 40000


def _force_scalar_preflight_exprs(final_results: Sequence[Mapping]) -> list[sp.Expr]:
    exprs: list[sp.Expr] = []
    for item in final_results:
        final = item.get("final")
        if final is not None:
            exprs.extend(sp.sympify(expr) for expr in list(getattr(final, "G", [])))
            exprs.extend(sp.sympify(expr) for expr in list(getattr(final, "Ihis", [])))
        K_v = item.get("K_v")
        if K_v is not None:
            exprs.extend(sp.sympify(expr) for expr in list(sp.Matrix(K_v)))
        K_h = item.get("K_h")
        if K_h is not None:
            exprs.extend(sp.sympify(expr) for expr in list(sp.Matrix(K_h)))
    return exprs


def _force_scalar_preflight_summary(
    final_results: list[dict],
    *,
    confirmed: bool = False,
) -> dict[str, object]:
    total_ops = 0
    total_chars = 0
    max_expr_ops = 0
    max_expr_chars = 0
    expr_count = 0
    case_count = len(final_results)
    max_retained = 0
    max_internal = 0
    recovery_expr_count = 0
    per_case: list[dict[str, object]] = []
    truncated = False

    for item in final_results:
        final = item.get("final")
        case_exprs: list[sp.Expr] = []
        retained_count = int(getattr(getattr(final, "G", None), "rows", 0) or 0) if final is not None else 0
        internal_count = int(getattr(sp.Matrix(item.get("K_v") if item.get("K_v") is not None else []), "rows", 0) or 0)
        if final is not None:
            case_exprs.extend(sp.sympify(expr) for expr in list(getattr(final, "G", [])))
            case_exprs.extend(sp.sympify(expr) for expr in list(getattr(final, "Ihis", [])))
        K_v = item.get("K_v")
        if K_v is not None:
            case_exprs.extend(sp.sympify(expr) for expr in list(sp.Matrix(K_v)))
        K_h = item.get("K_h")
        if K_h is not None:
            case_exprs.extend(sp.sympify(expr) for expr in list(sp.Matrix(K_h)))
        case_ops = 0
        case_chars = 0
        for expr in case_exprs:
            expr_count += 1
            ops = int(sp.count_ops(expr))
            chars = len(str(expr))
            total_ops += ops
            total_chars += chars
            case_ops += ops
            case_chars += chars
            max_expr_ops = max(max_expr_ops, ops)
            max_expr_chars = max(max_expr_chars, chars)
            if total_ops >= _FORCE_SCALAR_PREFLIGHT_DANGER_OPS or total_chars >= _FORCE_SCALAR_PREFLIGHT_DANGER_CHARS:
                truncated = True
                break
        max_retained = max(max_retained, retained_count)
        max_internal = max(max_internal, internal_count)
        recovery_expr_count += internal_count
        per_case.append({
            "case_id": int(item.get("index", len(per_case)) or 0),
            "retained_count": retained_count,
            "internal_count": internal_count,
            "expr_count": len(case_exprs),
            "ops": case_ops,
            "chars": case_chars,
        })
        if truncated:
            break

    severity = "ok"
    if total_ops >= _FORCE_SCALAR_PREFLIGHT_DANGER_OPS or total_chars >= _FORCE_SCALAR_PREFLIGHT_DANGER_CHARS or max_expr_chars >= _FORCE_SCALAR_PREFLIGHT_DANGER_EXPR_CHARS:
        severity = "danger"
    elif total_ops >= _FORCE_SCALAR_PREFLIGHT_WARN_OPS or total_chars >= _FORCE_SCALAR_PREFLIGHT_WARN_CHARS:
        severity = "warn"

    blocked = severity == "danger" and not confirmed
    message_zh = (
        f"标量展开预估很大：{case_count} 个 case，约 {total_ops} 个表达式操作，最长表达式约 {max_expr_chars} 字符。"
        "推荐使用矩阵路线；如需调试，可确认后继续生成标量。"
        if severity == "danger"
        else (
            f"标量展开预估中等：{case_count} 个 case，约 {total_ops} 个表达式操作。生成前请确认代码长度可接受。"
            if severity == "warn"
            else f"标量展开预估较小：{case_count} 个 case，约 {total_ops} 个表达式操作。"
        )
    )
    message_en = (
        f"Scalar expansion is estimated to be large: {case_count} cases, about {total_ops} expression operations, "
        f"and the longest expression is about {max_expr_chars} characters. The matrix path is recommended; "
        "continue only for debugging or inspection."
        if severity == "danger"
        else (
            f"Scalar expansion is estimated to be moderate: {case_count} cases and about {total_ops} expression operations. "
            "Check that the generated code size is acceptable before continuing."
            if severity == "warn"
            else f"Scalar expansion is estimated to be small: {case_count} cases and about {total_ops} expression operations."
        )
    )
    return {
        "enabled": True,
        "severity": severity,
        "blocked": blocked,
        "confirmed": bool(confirmed),
        "case_count": case_count,
        "expr_count": expr_count,
        "recovery_expr_count": recovery_expr_count,
        "max_retained": max_retained,
        "max_internal": max_internal,
        "total_ops": total_ops,
        "max_expr_ops": max_expr_ops,
        "total_chars": total_chars,
        "max_expr_chars": max_expr_chars,
        "truncated": truncated,
        "warn_ops": _FORCE_SCALAR_PREFLIGHT_WARN_OPS,
        "danger_ops": _FORCE_SCALAR_PREFLIGHT_DANGER_OPS,
        "warn_chars": _FORCE_SCALAR_PREFLIGHT_WARN_CHARS,
        "danger_chars": _FORCE_SCALAR_PREFLIGHT_DANGER_CHARS,
        "message_zh": message_zh,
        "message_en": message_en,
        "action_zh": "仍然生成标量",
        "action_en": "Continue scalar generation",
        "per_case": per_case,
    }


def _build_force_scalar_preflight_blocked_response(
    payload: dict,
    case_id_symbol: str,
    profile_set,
    final_results: list[dict],
    warnings: list[str],
    diagnoses: list[dict],
    preflight: dict[str, object],
) -> dict:
    profiles = payload.get("case_profiles") or []
    c_draft = (
        "/* Scalar-expanded C draft was not generated because the preflight estimate is large.\n"
        "   Use the matrix path, or confirm force_scalar generation to continue. */"
    )
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
        "warnings": warnings,
        "multi_case": {
            "codegen_mode": "force scalar Schur expansion",
            "fast_path": "case_scalar_preflight_blocked",
            "profile_count": len(profiles),
            "aliases": {},
            "gvalue_conditions": [],
            "uses_case_conditional_gvalue": False,
            "scalar_cse": _empty_scalar_cse_stats(),
            "scalar_preflight": preflight,
            "external_nodes": list(getattr(profile_set, "super_node_order", []) or []),
            "effective_internal_nodes": [],
            "block_type": "scalar_expanded",
            "recovery_profiles": diagnoses,
            "template_summary": {
                "super_nodes": list(getattr(profile_set, "super_node_order", []) or []),
                "NR_SUPER": len(getattr(profile_set, "super_node_order", []) or []),
                "NR_FINAL_MAX": max((item["final"].G.rows for item in final_results), default=0),
                "profile_dimensions": [item["final"].G.rows for item in final_results],
            },
            "c_draft": c_draft,
        },
    }


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
    ordered_entries: list[dict] = []

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

    def _merged_switch_lines_with_reuse(entries: Sequence[dict]) -> list[str] | None:
        if not entries:
            return None
        case_indices = sorted({
            int(case_index)
            for entry in entries
            for case_index in (entry.get("active_cases") or [])
        })
        if not case_indices:
            return None
        case_lines: dict[int, list[str]] = {}
        used_reuse = False
        for case_index in case_indices:
            reuse_map = _reuse_map_for_case(int(case_index))
            assigned_pairs: set[tuple[int, int]] = set()
            lines_for_case: list[str] = []
            for entry in entries:
                if case_index not in entry.get("active_case_set", set()):
                    continue
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
            if lines_for_case:
                case_lines[int(case_index)] = lines_for_case
        if not used_reuse:
            return None
        common_lines: list[str] = []
        if case_lines:
            common_candidates = set.intersection(*(set(lines) for lines in case_lines.values()))
            # Keep this conservative: only hoist direct matrix reads. Reuse assignments
            # can depend on case-local aliases, so they stay inside the case branch.
            common_candidates = {
                line for line in common_candidates
                if "= get_CODE(" in line
            }
            first_case = next(iter(case_lines.values()))
            common_lines = [
                line for line in first_case
                if line in common_candidates
            ]
            if common_lines:
                common_set = set(common_lines)
                case_lines = {
                    index: [line for line in lines if line not in common_set]
                    for index, lines in case_lines.items()
                }
        grouped_by_lines: dict[tuple[str, ...], list[int]] = {}
        for case_index, lines_for_case in case_lines.items():
            if lines_for_case:
                grouped_by_lines.setdefault(tuple(lines_for_case), []).append(int(case_index))
        lines = [f"    {line}" for line in common_lines]
        if not grouped_by_lines:
            return lines
        if lines:
            lines.append("")
        lines.append(f"    switch ({case_id_symbol}) {{")
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
                entry = {
                    "active_cases": tuple(active_cases),
                    "active_case_set": {int(index) for index in active_cases},
                    "row": row,
                    "col": col,
                    "var": var,
                    "assignment": assignment,
                }
                ordered_entries.append(entry)
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

    switch_lines = _merged_switch_lines_with_reuse(ordered_entries)
    if switch_lines:
        assignments = [str(entry["assignment"]) for entry in ordered_entries]
        first_assignment = assignments[0]
        for assignment in assignments[1:]:
            draft = draft.replace(f"    {assignment}", "", 1)
        draft = draft.replace(
            f"    {first_assignment}",
            "\n".join(switch_lines),
            1,
        )
        draft = re.sub(r"\n{4,}", "\n\n\n", draft)
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
            f"Inj{node_c} = mc_Ihisred_{row};",
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
        inactive_zero_lines = []
        for assignment in assignments:
            target = assignment.split("=", 1)[0].strip()
            if target.startswith("Inj"):
                inactive_zero_lines.append(f"        {target} = 0.0;")
        switch_lines = _case_switch_assignment_lines(case_id_symbol, case_indices, assignments)
        if inactive_zero_lines:
            default_index = switch_lines.index("    default:")
            switch_lines[default_index + 1:default_index + 1] = inactive_zero_lines
        draft = draft.replace(
            f"    {first_assignment}",
            "\n".join(switch_lines),
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
    use_synthetic_dependency = bool(aliases) or template_internal_count >= 4
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
        symbol_table=_multi_case_symbol_table(alias_model["profiles"]),
    )
    draft = _apply_multicase_conditional_diagonal_w_builder(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=alias_model["profiles"],
        aliases=aliases,
        gkk_template=_template_gkk_from_payload(template_payload),
        recovery_profiles=alias_model.get("final_recovery_profiles") or [],
        template_internal_nodes=result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
    )
    draft = _apply_multicase_conditional_diagonal_scalar_paths(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=alias_model["profiles"],
        aliases=aliases,
        gkk_template=_template_gkk_from_payload(template_payload),
        active_nr_expr="node_active",
    )
    draft = _apply_internal_layout_profile_compaction(
        draft,
        case_id_symbol=case_id_symbol,
        profiles=alias_model["profiles"],
        aliases=aliases,
        gkk_template=_template_gkk_from_payload(template_payload),
        template_internal_nodes=result.get("effective_internal_nodes") or template_payload.get("internal_nodes") or [],
        internal_layout_profiles=alias_model.get("internal_layout_profiles") or [],
        retained_count=len(template_payload.get("external_nodes") or result.get("external_nodes") or []),
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
    codegen_mode = str(payload.get("elimination_codegen_mode") or payload.get("codegen_mode") or "auto")
    use_matrix_dag_draft = bool(
        alias_model is not None
        and aliases
        and (
            codegen_mode == "prefer_matrix"
            or codegen_mode == "matrix"
            or has_isolated_dummy_final_nodes
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
        if template_payload is not None and not (template_payload.get("internal_nodes") or []):
            _sync_dummy_finalized_alias_values(aliases, template_payload, final_results)
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
            "internal_layout_profiles": (alias_model or {}).get("internal_layout_profiles") or [],
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
    internal_layout_profiles = multi.get("internal_layout_profiles") or []
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
        "internal_layout_profile_count": len(internal_layout_profiles),
        "internal_profile_dimensions": [int(item.get("active_internal_count") or 0) for item in internal_layout_profiles],
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
    codegen_mode = str(payload.get("elimination_codegen_mode") or payload.get("codegen_mode") or "auto")
    if codegen_mode == "force_scalar":
        response = time_call("generate_c_text", _build_force_scalar_multi_case_response, payload)
        return _attach_multicase_diagnostics(response, started, dummy_role_classification, timing_ms)
    auto_scalar_response = time_call("auto_scalar_probe", _try_build_auto_scalar_multi_case_response, payload)
    if auto_scalar_response is not None:
        return _attach_multicase_diagnostics(auto_scalar_response, started, dummy_role_classification, timing_ms)
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
    _dump_json(response)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _dump_json({"ok": False, "error": str(exc)})
        sys.exit(1)
