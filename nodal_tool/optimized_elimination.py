from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

import sympy as sp

from .dependency_analysis import analyze_reduced_model_dependencies, classify_expr_stage


@dataclass(frozen=True)
class GredEntryReuse:
    target_row: int
    target_col: int
    base_row: int
    base_col: int
    sign: int


_PolyKey = tuple[tuple[tuple[str, ...], int], ...]
_Poly = dict[tuple[str, ...], int]


def _expr_is_exact_zero(expr: sp.Expr) -> bool:
    return sp.sympify(expr) == 0


def _expr_is_light_zero(expr: sp.Expr, *, max_ops: int = 80) -> bool:
    expr = sp.sympify(expr)
    if expr == 0 or expr.is_zero is True:
        return True
    try:
        if int(sp.count_ops(expr, visual=False)) > max_ops:
            return False
        return sp.expand(expr) == 0
    except Exception:
        return False


def _signed_expr_atom(expr: sp.Expr) -> tuple[int, str]:
    expr = sp.sympify(expr)
    if _expr_is_exact_zero(expr):
        return 1, "0"
    if expr.could_extract_minus_sign():
        return -1, sp.srepr(-expr)
    return 1, sp.srepr(expr)


def _poly_add(left: _Poly, right: _Poly, scale: int = 1) -> _Poly:
    out: _Poly = dict(left)
    for factors, coeff in right.items():
        new_coeff = out.get(factors, 0) + scale * coeff
        if new_coeff:
            out[factors] = new_coeff
        else:
            out.pop(factors, None)
    return out


def _poly_atom(name: str, coeff: int = 1) -> _Poly:
    if coeff == 0:
        return {}
    return {(name,): coeff}


def _poly_from_expr(expr: sp.Expr) -> _Poly:
    sign, atom = _signed_expr_atom(expr)
    if atom == "0":
        return {}
    return _poly_atom(atom, sign)


def _poly_mul(left: _Poly, right: _Poly) -> _Poly:
    if not left or not right:
        return {}
    out: _Poly = {}
    for left_factors, left_coeff in left.items():
        for right_factors, right_coeff in right.items():
            factors = tuple(sorted((*left_factors, *right_factors)))
            coeff = left_coeff * right_coeff
            new_coeff = out.get(factors, 0) + coeff
            if new_coeff:
                out[factors] = new_coeff
            else:
                out.pop(factors, None)
    return out


def _poly_key(poly: _Poly) -> _PolyKey:
    return tuple(sorted(poly.items()))


def _poly_neg_key(key: _PolyKey) -> _PolyKey:
    return tuple((factors, -coeff) for factors, coeff in key)


def _structural_w_polys(Gkk: sp.Matrix) -> list[list[_Poly]]:
    Gkk = sp.Matrix(Gkk)
    nk = Gkk.rows
    if nk == 0:
        return []
    if all(_expr_is_exact_zero(Gkk[row, col]) for row in range(nk) for col in range(nk) if row != col):
        return [
            [_poly_atom(f"Wdiag_{row}") if row == col else {} for col in range(nk)]
            for row in range(nk)
        ]
    return [
        [_poly_atom(f"W_{min(row, col)}_{max(row, col)}") for col in range(nk)]
        for row in range(nk)
    ]


def _structural_gred_entry_poly(
    Grr: sp.Matrix,
    Grk: sp.Matrix,
    Gkr: sp.Matrix,
    W_polys: Sequence[Sequence[_Poly]],
    row: int,
    col: int,
) -> _Poly:
    poly = _poly_from_expr(Grr[row, col])
    nk = Grk.cols
    for k_row in range(nk):
        grk_poly = _poly_from_expr(Grk[row, k_row])
        if not grk_poly:
            continue
        for k_col in range(nk):
            w_poly = W_polys[k_row][k_col]
            if not w_poly:
                continue
            gkr_poly = _poly_from_expr(Gkr[k_col, col])
            if not gkr_poly:
                continue
            term = _poly_mul(_poly_mul(grk_poly, w_poly), gkr_poly)
            poly = _poly_add(poly, term, scale=-1)
    return poly


def structural_gred_entry_reuse_plan(
    G_full: sp.Matrix,
    node_order: Sequence[str],
    retained_nodes: Sequence[str],
    internal_nodes: Sequence[str],
) -> list[GredEntryReuse]:
    """Find exact structural whole-entry reuse in Gred without algebraic simplification.

    The helper compares the expression tree implied by
    Grr - Grk * W * Gkr. It intentionally does not call SymPy equivalence
    routines; uncertain entries simply do not get reused.
    """
    G_full = sp.Matrix(G_full)
    node_order = list(node_order)
    retained_nodes = list(retained_nodes)
    internal_nodes = list(internal_nodes)
    _validate_partition(node_order, retained_nodes, internal_nodes)
    if G_full.shape != (len(node_order), len(node_order)):
        raise ValueError("G_full shape must match node_order")

    ordered_indices = [node_order.index(node) for node in [*retained_nodes, *internal_nodes]]
    ordered_G = G_full.extract(ordered_indices, ordered_indices)
    nr = len(retained_nodes)
    nk = len(internal_nodes)
    Grr = ordered_G[:nr, :nr]
    Grk = ordered_G[:nr, nr:nr + nk]
    Gkr = ordered_G[nr:nr + nk, :nr]
    Gkk = ordered_G[nr:nr + nk, nr:nr + nk]
    return structural_gred_entry_reuse_plan_from_blocks(Grr, Grk, Gkr, Gkk)


def structural_gred_entry_reuse_plan_from_blocks(
    Grr: sp.Matrix,
    Grk: sp.Matrix,
    Gkr: sp.Matrix,
    Gkk: sp.Matrix,
) -> list[GredEntryReuse]:
    Grr = sp.Matrix(Grr)
    Grk = sp.Matrix(Grk)
    Gkr = sp.Matrix(Gkr)
    Gkk = sp.Matrix(Gkk)
    nr = Grr.rows
    nk = Gkk.rows
    if Grr.shape != (nr, nr):
        raise ValueError("Grr must be square")
    if Grk.shape != (nr, nk):
        raise ValueError("Grk shape must be NR x NK")
    if Gkr.shape != (nk, nr):
        raise ValueError("Gkr shape must be NK x NR")
    if Gkk.shape != (nk, nk):
        raise ValueError("Gkk must be NK x NK")

    W_polys = _structural_w_polys(Gkk)

    seen: dict[_PolyKey, tuple[int, int]] = {}
    reuse: list[GredEntryReuse] = []
    for row in range(nr):
        for col in range(row, nr):
            key = _poly_key(_structural_gred_entry_poly(Grr, Grk, Gkr, W_polys, row, col))
            if not key:
                continue
            base = seen.get(key)
            if base is not None:
                reuse.append(GredEntryReuse(row, col, base[0], base[1], 1))
                continue
            neg_base = seen.get(_poly_neg_key(key))
            if neg_base is not None:
                reuse.append(GredEntryReuse(row, col, neg_base[0], neg_base[1], -1))
                continue
            seen[key] = (row, col)
    return reuse


def _as_column_vector(vec: sp.Matrix, expected_rows: int, name: str) -> sp.Matrix:
    out = sp.Matrix(vec)
    if out.shape == (expected_rows,):
        out = sp.Matrix(expected_rows, 1, list(out))
    if out.shape != (expected_rows, 1):
        raise ValueError(f"{name} must be a {expected_rows}x1 column vector, got {out.shape}")
    return out


def _validate_partition(
    node_order: Sequence[str],
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
) -> None:
    if len(set(node_order)) != len(node_order):
        raise ValueError("node_order must not contain duplicates")
    if len(set(external_nodes)) != len(external_nodes):
        raise ValueError("external_nodes must not contain duplicates")
    if len(set(internal_nodes)) != len(internal_nodes):
        raise ValueError("internal_nodes must not contain duplicates")

    missing_external = [node for node in external_nodes if node not in node_order]
    missing_internal = [node for node in internal_nodes if node not in node_order]
    if missing_external:
        raise ValueError(f"external_nodes contains nodes not present in node_order: {missing_external}")
    if missing_internal:
        raise ValueError(f"internal_nodes contains nodes not present in node_order: {missing_internal}")

    overlap = sorted(set(external_nodes) & set(internal_nodes))
    if overlap:
        raise ValueError(f"external_nodes and internal_nodes overlap: {overlap}")


def simplify_expr(expr: sp.Expr, simplify_level: str = "light") -> sp.Expr:
    level = (simplify_level or "light").lower()
    if level == "none":
        return expr
    if level == "full":
        return sp.simplify(expr)
    return sp.cancel(expr)


def light_simplify_matrix(M: sp.Matrix) -> sp.Matrix:
    return sp.Matrix(M).applyfunc(sp.cancel)


def _simplify_matrix(M: sp.Matrix, simplify_level: str = "light") -> sp.Matrix:
    return sp.Matrix(M).applyfunc(lambda expr: simplify_expr(expr, simplify_level))


def check_symmetric(G: sp.Matrix) -> tuple[bool, list[tuple[int, int, sp.Expr, sp.Expr]]]:
    G = sp.Matrix(G)
    if G.rows != G.cols:
        return False, []
    mismatches: list[tuple[int, int, sp.Expr, sp.Expr]] = []
    for row in range(G.rows):
        for col in range(row + 1, G.cols):
            if sp.simplify(G[row, col] - G[col, row]) != 0:
                mismatches.append((row, col, G[row, col], G[col, row]))
    return not mismatches, mismatches


def _extract_partition(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    node_order: Sequence[str],
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
) -> tuple[sp.Matrix, sp.Matrix, list[str], list[int]]:
    node_order = list(node_order)
    external_nodes = list(external_nodes)
    internal_nodes = list(internal_nodes)
    _validate_partition(node_order, external_nodes, internal_nodes)

    G = sp.Matrix(G)
    n = len(node_order)
    if G.shape != (n, n):
        raise ValueError(f"G must have shape {(n, n)}, got {G.shape}")
    Ihis = _as_column_vector(sp.Matrix(Ihis), n, "Ihis")

    ordered_nodes = external_nodes + internal_nodes
    permutation = [node_order.index(node) for node in ordered_nodes]
    return G.extract(permutation, permutation), Ihis.extract(permutation, [0]), ordered_nodes, permutation


def sequential_symmetric_eliminate(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    node_order: Sequence[str],
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
    simplify_level: str = "light",
    reverse_internal_order: bool = True,
    assume_spd: bool = True,
) -> dict:
    """Eliminate internal nodes one pivot at a time without forming inv(Gii)."""

    external_nodes = list(external_nodes)
    internal_nodes = list(internal_nodes)
    current_G, current_Ihis, current_nodes, _ = _extract_partition(
        G, Ihis, node_order, external_nodes, internal_nodes
    )
    warnings: list[str] = []
    symmetric, mismatches = check_symmetric(current_G)
    if not symmetric:
        warnings.append("Matrix is not symmetric. SPD optimized elimination may be invalid.")

    elimination_order = list(reversed(internal_nodes)) if reverse_internal_order else list(internal_nodes)
    elimination_steps = []
    recovery_steps = []

    for node in elimination_order:
        if node not in current_nodes:
            continue
        pivot_index = current_nodes.index(node)
        keep = [index for index in range(len(current_nodes)) if index != pivot_index]
        rest_nodes = [current_nodes[index] for index in keep]
        pivot = current_G[pivot_index, pivot_index]
        if pivot == 0:
            raise ZeroDivisionError(f"zero pivot for internal node {node}")

        row = current_G.extract([pivot_index], keep)
        col = current_G.extract(keep, [pivot_index])
        kept_G = current_G.extract(keep, keep)
        kept_Ihis = current_Ihis.extract(keep, [0])
        node_Ihis = current_Ihis[pivot_index, 0]

        next_G = kept_G - (col * row) / pivot
        next_Ihis = kept_Ihis - col * node_Ihis / pivot
        if assume_spd and symmetric:
            for row_index in range(next_G.rows):
                for col_index in range(row_index + 1, next_G.cols):
                    value = simplify_expr(next_G[row_index, col_index], simplify_level)
                    next_G[row_index, col_index] = value
                    next_G[col_index, row_index] = value

        next_G = _simplify_matrix(next_G, simplify_level)
        next_Ihis = _simplify_matrix(next_Ihis, simplify_level)
        recovery_coeffs = [simplify_expr(-row[0, col_index] / pivot, simplify_level) for col_index in range(row.cols)]
        recovery_source = simplify_expr(-node_Ihis / pivot, simplify_level)

        elimination_steps.append(
            {
                "node": node,
                "pivot": pivot,
                "rest_nodes": rest_nodes,
                "G_row": [row[0, col_index] for col_index in range(row.cols)],
                "G_col": [col[row_index, 0] for row_index in range(col.rows)],
                "Ihis": node_Ihis,
            }
        )
        recovery_steps.append(
            {
                "node": node,
                "rest_nodes": rest_nodes,
                "coefficients": recovery_coeffs,
                "source": recovery_source,
            }
        )

        current_G = next_G
        current_Ihis = next_Ihis
        current_nodes = rest_nodes

    return {
        "G_red": current_G,
        "Ihis_red": current_Ihis,
        "remaining_nodes": current_nodes,
        "external_nodes": external_nodes,
        "internal_nodes": internal_nodes,
        "elimination_order": elimination_order,
        "elimination_steps": elimination_steps,
        "recovery_steps": recovery_steps,
        "warnings": warnings,
        "symmetric": symmetric,
        "asymmetric_entries": mismatches[:20],
    }


def _ccode(expr: sp.Expr) -> str:
    code = sp.ccode(expr).replace("M_PI", "PI")
    return re.sub(r"(?<![eE][+-])(?<![\w.])(\d+)(?![\w.])", r"\1.0", code)


def find_dynamic_entries(stage_matrix: Sequence[Sequence[str]]) -> list[tuple[int, int]]:
    entries: list[tuple[int, int]] = []
    for row, values in enumerate(stage_matrix or []):
        for col, stage in enumerate(values):
            if str(stage) == "CODE_UPDATE":
                entries.append((row, col))
    return entries


def _find_code_owned_entries(stage_matrix: Sequence[Sequence[str]]) -> list[tuple[int, int]]:
    entries: list[tuple[int, int]] = []
    for row, values in enumerate(stage_matrix or []):
        for col, stage in enumerate(values):
            if str(stage) != "RAM_INIT":
                entries.append((row, col))
    return entries


def detect_rectangular_dynamic_blocks(dynamic_entries: Sequence[tuple[int, int]]) -> dict:
    entries = sorted({(int(row), int(col)) for row, col in dynamic_entries})
    if not entries:
        return {"entries": [], "rows": [], "cols": [], "is_rectangular": False, "blocks": []}
    rows = sorted({row for row, _ in entries})
    cols = sorted({col for _, col in entries})
    rectangle = {(row, col) for row in rows for col in cols}
    is_rectangular = rectangle == set(entries)
    return {
        "entries": entries,
        "rows": rows if is_rectangular else [],
        "cols": cols if is_rectangular else [],
        "is_rectangular": is_rectangular,
        "blocks": [{"rows": rows, "cols": cols, "entries": entries}] if is_rectangular else [],
    }


def _slice_matrix(matrix: sp.Matrix, rows: Sequence[int], cols: Sequence[int]) -> sp.Matrix:
    matrix = sp.Matrix(matrix)
    return sp.Matrix([[matrix[row, col] for col in cols] for row in rows])


def build_sliced_schur_for_block(
    Grr: sp.Matrix,
    Grk: sp.Matrix,
    W: sp.Matrix,
    Gkr: sp.Matrix,
    rows: Sequence[int],
    cols: Sequence[int],
) -> sp.Matrix:
    rows = [int(row) for row in rows]
    cols = [int(col) for col in cols]
    if not rows or not cols:
        return sp.zeros(len(rows), len(cols))
    Grr_BC = _slice_matrix(sp.Matrix(Grr), rows, cols)
    Grk_Bk = _slice_matrix(sp.Matrix(Grk), rows, range(sp.Matrix(Grk).cols))
    Gkr_kC = _slice_matrix(sp.Matrix(Gkr), range(sp.Matrix(Gkr).rows), cols)
    return sp.simplify(Grr_BC - Grk_Bk * sp.Matrix(W) * Gkr_kC)


def build_sliced_schur_for_entry(
    Grr: sp.Matrix,
    Grk: sp.Matrix,
    W: sp.Matrix,
    Gkr: sp.Matrix,
    row: int,
    col: int,
) -> sp.Expr:
    return sp.simplify(build_sliced_schur_for_block(Grr, Grk, W, Gkr, [row], [col])[0, 0])


def _symbol_name_set(items: Sequence[str] | None) -> set[str]:
    return {str(item).strip() for item in (items or []) if str(item).strip()}


def _split_expr_by_ram_symbols(expr: sp.Expr, ram_symbols: set[str]) -> tuple[sp.Expr, sp.Expr]:
    expr = sp.sympify(expr)
    if expr == 0:
        return sp.Integer(0), sp.Integer(0)
    terms = expr.as_ordered_terms() if isinstance(expr, sp.Add) else [expr]
    ram_terms: list[sp.Expr] = []
    code_terms: list[sp.Expr] = []
    for term in terms:
        free_names = {symbol.name for symbol in term.free_symbols}
        if not free_names or free_names <= ram_symbols:
            ram_terms.append(term)
        else:
            code_terms.append(term)
    return sp.Add(*ram_terms) if ram_terms else sp.Integer(0), sp.Add(*code_terms) if code_terms else sp.Integer(0)


def _extract_symbols_from_expr(expr: sp.Expr) -> set[str]:
    return {symbol.name for symbol in sp.sympify(expr).free_symbols}


def build_rtds_stage_plan(
    G: sp.Matrix,
    node_order: Sequence[str],
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
    constant_symbols: Sequence[str] | None = None,
    symbol_usage: Sequence[dict] | None = None,
) -> dict:
    """Split G symbols into RAM-safe and CODE-stage parts for RTDS C snippets.

    A symbol is RAM-safe only if the user marked it constant and no occurrence
    touches an eliminated/internal node. Expressions are split term-by-term, so
    X + Y + Z can place only Z in RAM when Z is the only RAM-safe symbol.
    """
    G = sp.Matrix(G)
    node_order = list(node_order)
    external_nodes = list(external_nodes)
    internal_nodes = list(internal_nodes)
    constant_set = _symbol_name_set(constant_symbols)
    internal_set = set(internal_nodes)
    index_by_node = {node: index for index, node in enumerate(node_order)}
    external_indices = [index_by_node[node] for node in external_nodes]

    disqualified: set[str] = set()
    for item in symbol_usage or []:
        symbol = str(item.get("symbol", "")).strip()
        if not symbol or symbol not in constant_set:
            continue
        if item.get("row_node") in internal_set or item.get("col_node") in internal_set:
            disqualified.add(symbol)

    for row, row_node in enumerate(node_order):
        for col, col_node in enumerate(node_order):
            if row_node not in internal_set and col_node not in internal_set:
                continue
            disqualified.update(_extract_symbols_from_expr(G[row, col]) & constant_set)

    ram_symbols = constant_set - disqualified
    ram_G = sp.zeros(G.rows, G.cols)
    code_G = sp.Matrix(G)
    external_set = set(external_nodes)
    for row, row_node in enumerate(node_order):
        if row_node not in external_set:
            continue
        for col, col_node in enumerate(node_order):
            if col_node not in external_set:
                continue
            ram_expr, code_expr = _split_expr_by_ram_symbols(G[row, col], ram_symbols)
            ram_G[row, col] = ram_expr
            code_G[row, col] = code_expr

    ram_G_rr = ram_G.extract(external_indices, external_indices) if external_indices else sp.zeros(0, 0)
    code_blocks = _permute_internal_blocks(
        code_G,
        sp.zeros(G.rows, 1),
        node_order,
        external_nodes,
        internal_nodes,
    )
    return {
        "constant_symbols": sorted(constant_set),
        "ram_symbols": sorted(ram_symbols),
        "code_symbols": sorted((constant_set - ram_symbols) | (set().union(*[
            _extract_symbols_from_expr(G[row, col]) for row in range(G.rows) for col in range(G.cols)
        ]) - constant_set if G.rows and G.cols else set())),
        "disqualified_constant_symbols": sorted(disqualified),
        "ram_G": ram_G,
        "code_G": code_G,
        "ram_G_rr": ram_G_rr,
        "code_blocks": code_blocks,
        "external_nodes": external_nodes,
        "internal_nodes": internal_nodes,
        "node_order": node_order,
    }


def analyze_internal_block_structure(Gii: sp.Matrix, internal_nodes: Sequence[str]) -> dict:
    Gii = sp.Matrix(Gii)
    internal_nodes = list(internal_nodes)
    if Gii.shape != (len(internal_nodes), len(internal_nodes)):
        raise ValueError("Gii shape must match internal_nodes")
    if not internal_nodes:
        return {
            "is_symmetric": True,
            "diagonal_nodes": [],
            "coupled_nodes": [],
            "suggested_order": [],
            "warnings": [],
            "block_type": "no_elimination",
            "asymmetric_entries": [],
        }

    is_symmetric, mismatches = check_symmetric(Gii)
    warnings: list[str] = []
    if not is_symmetric:
        warnings.append("Matrix is not symmetric; symmetric block inverse display may be invalid.")

    edges: list[tuple[int, int]] = []
    for row in range(Gii.rows):
        for col in range(row + 1, Gii.cols):
            if sp.simplify(Gii[row, col]) != 0 or sp.simplify(Gii[col, row]) != 0:
                edges.append((row, col))

    if not edges:
        diagonal_nodes = list(internal_nodes)
        coupled_nodes: list[str] = []
    else:
        # Pick a small vertex cover as the coupled core S. The remaining nodes form
        # D and therefore have no mutual off-diagonal couplings inside D.
        best_cover: tuple[int, ...] | None = None
        n = len(internal_nodes)
        for mask in range(1, 1 << n):
            cover = tuple(index for index in range(n) if mask & (1 << index))
            if best_cover is not None and len(cover) >= len(best_cover):
                continue
            cover_set = set(cover)
            if all(row in cover_set or col in cover_set for row, col in edges):
                best_cover = cover
        coupled_index_set = set(best_cover or range(n))
        diagonal_nodes = [node for index, node in enumerate(internal_nodes) if index not in coupled_index_set]
        coupled_nodes = [node for index, node in enumerate(internal_nodes) if index in coupled_index_set]
        if len(diagonal_nodes) < 2:
            diagonal_nodes = []
            coupled_nodes = list(internal_nodes)

    if diagonal_nodes and coupled_nodes:
        block_type = "diagonal_plus_coupled"
    elif diagonal_nodes and not coupled_nodes:
        block_type = "pure_diagonal"
    else:
        block_type = "general"

    suggested_order = diagonal_nodes + coupled_nodes
    if suggested_order != internal_nodes:
        warnings.append("Suggested block order differs from user order.")

    return {
        "is_symmetric": is_symmetric,
        "diagonal_nodes": diagonal_nodes,
        "coupled_nodes": coupled_nodes,
        "suggested_order": suggested_order,
        "warnings": warnings,
        "block_type": block_type,
        "asymmetric_entries": mismatches[:20],
    }


def _inverse_diagonal(D: sp.Matrix) -> sp.Matrix:
    return sp.diag(*[1 / D[index, index] for index in range(D.rows)])


def _permute_internal_blocks(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    node_order: Sequence[str],
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
) -> dict:
    ordered_G, ordered_Ihis, ordered_nodes, _ = _extract_partition(G, Ihis, node_order, external_nodes, internal_nodes)
    n_e = len(external_nodes)
    n_i = len(internal_nodes)
    e_idx = list(range(n_e))
    i_idx = list(range(n_e, n_e + n_i))
    return {
        "ordered_nodes": ordered_nodes,
        "G_rr": ordered_G.extract(e_idx, e_idx),
        "G_ri": ordered_G.extract(e_idx, i_idx),
        "G_ir": ordered_G.extract(i_idx, e_idx),
        "G_ii": ordered_G.extract(i_idx, i_idx),
        "Ihis_r": ordered_Ihis.extract(e_idx, [0]),
        "Ihis_i": ordered_Ihis.extract(i_idx, [0]),
    }


def build_structured_formula(
    G: sp.Matrix,
    Ihis: sp.Matrix,
    node_order: Sequence[str],
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
    use_suggested_order: bool = False,
    simplify_level: str = "light",
    skip_symbolic_w_details: bool = False,
) -> dict:
    external_nodes = list(external_nodes)
    internal_nodes = list(internal_nodes)
    initial = _permute_internal_blocks(G, Ihis, node_order, external_nodes, internal_nodes)
    analysis = analyze_internal_block_structure(initial["G_ii"], internal_nodes)
    effective_internal_nodes = list(analysis["suggested_order"]) if use_suggested_order else internal_nodes
    blocks = _permute_internal_blocks(G, Ihis, node_order, external_nodes, effective_internal_nodes)
    effective_analysis = analyze_internal_block_structure(blocks["G_ii"], effective_internal_nodes)

    Gii = blocks["G_ii"]
    details: dict = {}
    block_type = effective_analysis["block_type"]
    diagonal_nodes = effective_analysis["diagonal_nodes"]
    coupled_nodes = effective_analysis["coupled_nodes"]
    node_pos = {node: index for index, node in enumerate(effective_internal_nodes)}

    if block_type == "no_elimination":
        details = {"W": sp.zeros(0, 0)}
    elif block_type == "pure_diagonal":
        D_values = [Gii[index, index] for index in range(Gii.rows)]
        W = sp.diag(*[1 / value for value in D_values])
        details = {
            "D_values": D_values,
            "W": W,
            "rank_update_terms": [
                {
                    "node": node,
                    "D": D_values[index],
                    "u": blocks["G_ri"].extract(list(range(blocks["G_ri"].rows)), [index]),
                    "H": blocks["Ihis_i"][index, 0],
                }
                for index, node in enumerate(effective_internal_nodes)
            ],
        }
    elif block_type == "diagonal_plus_coupled":
        d_idx = [node_pos[node] for node in diagonal_nodes]
        s_idx = [node_pos[node] for node in coupled_nodes]
        D = Gii.extract(d_idx, d_idx)
        U = Gii.extract(d_idx, s_idx)
        S = Gii.extract(s_idx, s_idx)
        D_inv = _inverse_diagonal(D)
        if skip_symbolic_w_details:
            M = sp.zeros(S.rows, S.cols)
            detM = None
            M_inv = None
            W = sp.ones(Gii.rows, Gii.cols)
        else:
            M = _simplify_matrix(S - U.T * D_inv * U, simplify_level)
            if M.shape == (2, 2):
                M11, M12, M22 = M[0, 0], M[0, 1], M[1, 1]
                detM = simplify_expr(M11 * M22 - M12**2, simplify_level)
                M_inv = sp.Matrix([[M22 / detM, -M12 / detM], [-M12 / detM, M11 / detM]])
            else:
                detM = None
                M_inv = sp.MatrixSymbol("M_inv", M.rows, M.cols)
            if isinstance(M_inv, sp.MatrixBase):
                W = sp.Matrix.vstack(
                    sp.Matrix.hstack(D_inv + D_inv * U * M_inv * U.T * D_inv, -D_inv * U * M_inv),
                    sp.Matrix.hstack(-M_inv * U.T * D_inv, M_inv),
                )
            else:
                W = None
        details = {
            "D": D,
            "U": U,
            "S": S,
            "D_inv": D_inv,
            "M": M,
            "M_inv": M_inv if isinstance(M_inv, sp.MatrixBase) else None,
            "detM": detM,
            "W": W,
            "diagonal_nodes": diagonal_nodes,
            "coupled_nodes": coupled_nodes,
        }
    else:
        details = {"W_symbol": "Gii^{-1}"}

    return {
        "external_nodes": external_nodes,
        "user_internal_nodes": internal_nodes,
        "effective_internal_nodes": effective_internal_nodes,
        "use_suggested_order": use_suggested_order,
        "analysis": analysis,
        "effective_analysis": effective_analysis,
        "blocks": blocks,
        "block_type": block_type,
        "details": details,
        "warnings": list(dict.fromkeys([*analysis["warnings"], *effective_analysis["warnings"]])),
    }


def structured_dependency_model(structured: dict, simplify_level: str = "light") -> dict:
    blocks = structured.get("blocks", {})
    Grr = sp.Matrix(blocks.get("G_rr", []))
    Grk = sp.Matrix(blocks.get("G_ri", []))
    Gkr = sp.Matrix(blocks.get("G_ir", []))
    Gkk = sp.Matrix(blocks.get("G_ii", []))
    Ihisr = sp.Matrix(blocks.get("Ihis_r", []))
    Ihisk = sp.Matrix(blocks.get("Ihis_i", []))
    details = structured.get("details", {})
    W = details.get("W")
    if W is None:
        W = Gkk.inv() if Gkk.rows else sp.zeros(0, 0)
    W = sp.Matrix(W)
    if Gkk.rows == 0:
        Gred = _simplify_matrix(Grr, simplify_level) if Grr.rows else sp.zeros(0, 0)
        Ihisred = _simplify_matrix(Ihisr, simplify_level) if Ihisr.rows else sp.zeros(0, 1)
    else:
        Gred = _simplify_matrix(Grr - Grk * W * Gkr, simplify_level) if Grr.rows else sp.zeros(0, 0)
        Ihisred = _simplify_matrix(Ihisr - Grk * W * Ihisk, simplify_level) if Ihisr.rows else sp.zeros(0, 1)
    Kv = _simplify_matrix(-W * Gkr, simplify_level) if W.rows and Gkr.cols else sp.zeros(W.rows, Gkr.cols)
    Kh = _simplify_matrix(-W * Ihisk, simplify_level) if W.rows else sp.zeros(0, 1)
    return {
        "Gred": Gred,
        "Ihisred": Ihisred,
        "W": W,
        "Kv": Kv,
        "Kh": Kh,
    }


def _dependency_model_from_override(model: dict, nr: int, nk: int) -> tuple[dict, bool]:
    W_value = model.get("W", None)
    w_runtime_inverse = W_value is None
    W = sp.zeros(nk, nk) if w_runtime_inverse else sp.Matrix(W_value)
    return (
        {
            "Gred": sp.Matrix(model.get("Gred", sp.zeros(nr, nr))),
            "Ihisred": _as_column_vector(sp.Matrix(model.get("Ihisred", sp.zeros(nr, 1))), nr, "Ihisred"),
            "W": W,
            "Kv": sp.Matrix(model.get("Kv", sp.zeros(nk, nr))),
            "Kh": _as_column_vector(sp.Matrix(model.get("Kh", sp.zeros(nk, 1))), nk, "Kh") if nk else sp.zeros(0, 1),
        },
        w_runtime_inverse,
    )


def build_dependency_stage_plan(
    structured: dict,
    symbol_table: dict[str, str] | None = None,
    simplify_level: str = "light",
    analysis_structured: dict | None = None,
    dependency_model_override: dict | None = None,
    analysis_model_override: dict | None = None,
) -> dict:
    blocks = structured.get("blocks", {})
    nr = sp.Matrix(blocks.get("G_rr", [])).rows
    nk = sp.Matrix(blocks.get("G_ii", [])).rows
    w_runtime_inverse = False
    if dependency_model_override is None:
        model = structured_dependency_model(structured, simplify_level)
    else:
        model, w_runtime_inverse = _dependency_model_from_override(dependency_model_override, nr, nk)
    if analysis_model_override is not None:
        analysis_model, _ = _dependency_model_from_override(analysis_model_override, nr, nk)
    elif dependency_model_override is not None:
        analysis_model = model
    else:
        analysis_model = structured_dependency_model(analysis_structured or structured, simplify_level)
    analysis = analyze_reduced_model_dependencies(analysis_model, symbol_table or {})
    dynamic_entries = find_dynamic_entries(analysis.get("Gred_stage") or [])
    dynamic_block = detect_rectangular_dynamic_blocks(dynamic_entries)
    return {
        **model,
        "Grr": sp.Matrix(blocks.get("G_rr", [])),
        "Grk": sp.Matrix(blocks.get("G_ri", [])),
        "Gkr": sp.Matrix(blocks.get("G_ir", [])),
        "Gkk": sp.Matrix(blocks.get("G_ii", [])),
        "Ihisr": sp.Matrix(blocks.get("Ihis_r", [])),
        "Ihisk": sp.Matrix(blocks.get("Ihis_i", [])),
        "Gred_direct": sp.Matrix(structured.get("Gred_direct", sp.zeros(nr, nr))),
        "Ihisred_direct": _as_column_vector(
            sp.Matrix(structured.get("Ihisred_direct", sp.zeros(nr, 1))),
            nr,
            "Ihisred_direct",
        ),
        "block_type": structured.get("block_type"),
        "details": structured.get("details", {}),
        "W_runtime_inverse": w_runtime_inverse,
        "dependency_analysis": analysis,
        "dynamic_subblock": {
            **dynamic_block,
            "owner_matrix": analysis.get("Gred_stage") or [],
            "ram_entries": [
                (row, col)
                for row, values in enumerate(analysis.get("Gred_stage") or [])
                for col, stage in enumerate(values)
                if stage == "RAM_INIT"
            ],
            "code_entries": dynamic_entries,
            "unknown_entries": [
                (row, col)
                for row, values in enumerate(analysis.get("Gred_stage") or [])
                for col, stage in enumerate(values)
                if stage == "UNKNOWN"
            ],
        },
        "external_nodes": list(structured.get("external_nodes", [])),
        "internal_nodes": list(structured.get("effective_internal_nodes", [])),
    }


def cse_c_draft_for_formula_mode(G_red: sp.Matrix, Ihis_red: sp.Matrix) -> str:
    G_red = sp.Matrix(G_red)
    Ihis_red = sp.Matrix(Ihis_red)
    expressions = [G_red[row, col] for row in range(G_red.rows) for col in range(G_red.cols)]
    expressions.extend(Ihis_red[row, 0] for row in range(Ihis_red.rows))
    replacements, reduced = sp.cse(expressions, symbols=sp.numbered_symbols("t"))
    lines = ["// Formula mode C draft. Generated from explicit sequential Schur results."]
    for symbol, expr in replacements:
        lines.append(f"double {symbol} = {_ccode(expr)};")
    cursor = 0
    for row in range(G_red.rows):
        for col in range(G_red.cols):
            lines.append(f"Gred[{row}][{col}] = {_ccode(reduced[cursor])};")
            cursor += 1
    for row in range(Ihis_red.rows):
        lines.append(f"Ihis_red[{row}] = {_ccode(reduced[cursor])};")
        cursor += 1
    return "\n".join(lines)


def _c_matrix_literal(matrix: sp.Matrix, name: str) -> str:
    matrix = sp.Matrix(matrix)
    if matrix.rows == 0 or matrix.cols == 0:
        return f"double {name}[1][1] = {{ {{0.0}} }};  /* Empty {matrix.rows}x{matrix.cols} block. */"
    rows = []
    for row in range(matrix.rows):
        items = ", ".join(_ccode(matrix[row, col]) for col in range(matrix.cols))
        rows.append(f"    {{{items}}}")
    return f"double {name}[{matrix.rows}][{matrix.cols}] = {{\n" + ",\n".join(rows) + "\n};"


def _c_vector_literal(vector: sp.Matrix, name: str) -> str:
    vector = _as_column_vector(sp.Matrix(vector), sp.Matrix(vector).rows, name)
    if vector.rows == 0:
        return f"double {name}[1][1] = {{ {{0.0}} }};  /* Empty 0x1 block. */"
    rows = [f"    {{{_ccode(vector[row, 0])}}}" for row in range(vector.rows)]
    return f"double {name}[{vector.rows}][1] = {{\n" + ",\n".join(rows) + "\n};"


def _c_zero_matrix(name: str, rows: int, cols: int) -> str:
    if rows == 0 or cols == 0:
        return f"double {name}[1][1] = {{ {{0.0}} }};  /* Empty {rows}x{cols} workspace. */"
    return f"double {name}[{rows}][{cols}] = {{0.0}};"


def _c_matrix_set_lines(
    matrix: sp.Matrix,
    matrix_name: str,
    fn: str = "set",
    indent: str = "    ",
    skip_zero: bool = False,
) -> list[str]:
    matrix = sp.Matrix(matrix)
    lines: list[str] = []
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            if skip_zero and sp.simplify(matrix[row, col]) == 0:
                continue
            lines.append(f"{indent}{fn}(&{matrix_name}, {row}, {col}, {_ccode(matrix[row, col])});")
    return lines


def _c_vector_set_lines(vector: sp.Matrix, matrix_name: str, fn: str = "set", indent: str = "    ") -> list[str]:
    vector = _as_column_vector(sp.Matrix(vector), sp.Matrix(vector).rows, matrix_name)
    return [f"{indent}{fn}(&{matrix_name}, {row}, 0, {_ccode(vector[row, 0])});" for row in range(vector.rows)]


def _c_condition_lines(names: Sequence[str], indent: str = "        ") -> list[str]:
    return [f"{indent}conditionMatrixForCODE(&{name});" for name in names]


def _c_register_lines(names: Sequence[str], indent: str = "    ") -> list[str]:
    return [f"{indent}matrix_register(&{name});" for name in names]


def _c_copy_subblock(dst: str, src: str, row_offset: int, col_offset: int, rows: int, cols: int) -> list[str]:
    if rows == 0 or cols == 0:
        return []
    lines = []
    for row in range(rows):
        for col in range(cols):
            lines.append(f"{dst}[{row + row_offset}][{col + col_offset}] = {src}[{row}][{col}];")
    return lines


def _c_sym_inverse_call(matrix_name: str, inverse_name: str, dim: int) -> list[str]:
    if dim <= 0:
        return []
    if dim == 1:
        return [f"{inverse_name}[0][0] = 1.0 / {matrix_name}[0][0];"]
    if dim == 2:
        return [
            f"mat_2x2_sym_inv_code({matrix_name}[0][0], {matrix_name}[0][1], {matrix_name}[1][1],",
            f"                     &{inverse_name}[0][0], &{inverse_name}[0][1], &{inverse_name}[1][1]);",
            f"{inverse_name}[1][0] = {inverse_name}[0][1];",
        ]
    if dim == 3:
        return [
            f"mat_3x3_sym_inv_code({matrix_name}[0][0], {matrix_name}[0][1], {matrix_name}[0][2],",
            f"                     {matrix_name}[1][1], {matrix_name}[1][2],",
            f"                     {matrix_name}[2][2],",
            f"                     &{inverse_name}[0][0], &{inverse_name}[0][1], &{inverse_name}[0][2],",
            f"                     &{inverse_name}[1][1], &{inverse_name}[1][2],",
            f"                     &{inverse_name}[2][2]);",
            f"{inverse_name}[1][0] = {inverse_name}[0][1];",
            f"{inverse_name}[2][0] = {inverse_name}[0][2];",
            f"{inverse_name}[2][1] = {inverse_name}[1][2];",
        ]
    return [
        f"/* WARNING: {matrix_name} is {dim}x{dim}; RTDS fast symmetric inverse helpers only cover 2x2 and 3x3. */",
        f"MATH_matx_invert({dim}, &({matrix_name}[0][0]), {dim}, &({inverse_name}[0][0]), {dim});",
    ]


def _c_manual_transpose_assignments(src: str, dst: str, rows: int, cols: int) -> list[str]:
    lines = []
    for row in range(cols):
        for col in range(rows):
            lines.append(f"{dst}[{row}][{col}] = {src}[{col}][{row}];")
    return lines


def _c_symbol_name(name: str) -> str:
    suffix = re.sub(r"\W", "_", str(name or "n"))
    if not suffix or suffix[0].isdigit():
        suffix = f"n_{suffix}"
    return suffix


def _c_node_variable_name(node: str, node_display_names: dict[str, str] | None = None) -> str:
    return _c_symbol_name(_c_display_node(node, node_display_names))


def _c_voltage_variable_name(node: str, node_display_names: dict[str, str] | None = None) -> str:
    display = (node_display_names or {}).get(str(node), str(node))
    suffix = _c_symbol_name(display)
    return f"V{suffix}"


def _c_node_symbol_vector(name: str, nodes: Sequence[str], node_display_names: dict[str, str] | None = None) -> str:
    nodes = list(nodes)
    if not nodes:
        return f"double {name}[1][1] = {{ {{0.0}} }};  /* Empty symbolic node vector. */"
    rows = []
    for node in nodes:
        display = (node_display_names or {}).get(str(node), str(node))
        rows.append(f"    {{{_c_symbol_name(display)}}}")
    return f"double {name}[{len(nodes)}][1] = {{\n" + ",\n".join(rows) + "\n};"


def _c_display_node(node: str, node_display_names: dict[str, str] | None = None) -> str:
    return (node_display_names or {}).get(str(node), str(node))


def _stage_entries(matrix: sp.Matrix, stages: Sequence[Sequence[str]], wanted: str) -> sp.Matrix:
    matrix = sp.Matrix(matrix)
    out = sp.zeros(matrix.rows, matrix.cols)
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            if row < len(stages) and col < len(stages[row]) and stages[row][col] == wanted:
                out[row, col] = matrix[row, col]
    return out


def _stage_vector_entries(vector: sp.Matrix, stages: Sequence[str], wanted: str) -> sp.Matrix:
    vector = _as_column_vector(sp.Matrix(vector), sp.Matrix(vector).rows, "stage_vector")
    out = sp.zeros(vector.rows, 1)
    for row in range(vector.rows):
        if row < len(stages) and stages[row] == wanted:
            out[row, 0] = vector[row, 0]
    return out


def _split_expr_ram_and_code(expr: sp.Expr, symbol_table: dict[str, str]) -> tuple[sp.Expr, sp.Expr]:
    ram_expr = sp.Integer(0)
    code_expr = sp.Integer(0)
    for term in sp.Add.make_args(sp.expand(sp.sympify(expr))):
        if classify_expr_stage(term, symbol_table) == "RAM_INIT":
            ram_expr += term
        else:
            code_expr += term
    return sp.simplify(ram_expr), sp.simplify(code_expr)


def _split_matrix_ram_and_code_terms(matrix: sp.Matrix, symbol_table: dict[str, str]) -> tuple[sp.Matrix, sp.Matrix]:
    matrix = sp.Matrix(matrix)
    ram_matrix = sp.zeros(matrix.rows, matrix.cols)
    code_matrix = sp.zeros(matrix.rows, matrix.cols)
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            ram_matrix[row, col], code_matrix[row, col] = _split_expr_ram_and_code(matrix[row, col], symbol_table)
    return ram_matrix, code_matrix


def _split_matrix_by_entry_stage(matrix: sp.Matrix, symbol_table: dict[str, str]) -> tuple[sp.Matrix, sp.Matrix]:
    matrix = sp.Matrix(matrix)
    ram_matrix = sp.zeros(matrix.rows, matrix.cols)
    code_matrix = sp.zeros(matrix.rows, matrix.cols)
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            if classify_expr_stage(matrix[row, col], symbol_table) == "RAM_INIT":
                ram_matrix[row, col] = matrix[row, col]
            else:
                code_matrix[row, col] = matrix[row, col]
    return ram_matrix, code_matrix


def _matrix_symbol_names(*matrices: sp.Matrix) -> set[str]:
    names: set[str] = set()
    for matrix in matrices:
        matrix = sp.Matrix(matrix)
        for value in matrix:
            names.update(symbol.name for symbol in sp.sympify(value).free_symbols)
    return names


def _matrix_has_nonzero(matrix: sp.Matrix) -> bool:
    matrix = sp.Matrix(matrix)
    return any(sp.simplify(value) != 0 for value in matrix)


def _matrix_is_ram_stage(matrix: sp.Matrix, symbol_table: dict[str, str]) -> bool:
    matrix = sp.Matrix(matrix)
    return all(classify_expr_stage(sp.sympify(value), symbol_table) == "RAM_INIT" for value in matrix)


def _matrix_is_symmetric_light(matrix: sp.Matrix) -> bool:
    matrix = sp.Matrix(matrix)
    if matrix.rows != matrix.cols:
        return False
    for row in range(matrix.rows):
        for col in range(row + 1, matrix.cols):
            if not _expr_is_light_zero(matrix[row, col] - matrix[col, row]):
                return False
    return True


def _matrix_is_diagonal_light(matrix: sp.Matrix) -> bool:
    matrix = sp.Matrix(matrix)
    if matrix.rows != matrix.cols:
        return False
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            if row == col:
                continue
            if not _expr_is_light_zero(matrix[row, col]):
                return False
    return True


def _matrix_is_transpose_light(left: sp.Matrix, right: sp.Matrix) -> bool:
    left = sp.Matrix(left)
    right = sp.Matrix(right)
    if left.rows != right.cols or left.cols != right.rows:
        return False
    for row in range(left.rows):
        for col in range(left.cols):
            if not _expr_is_light_zero(left[row, col] - right[col, row]):
                return False
    return True


def _ram_overlay_node_subset(matrix: sp.Matrix, nodes: Sequence[str]) -> tuple[list[str], dict[int, int]]:
    matrix = sp.Matrix(matrix)
    touched: set[int] = set()
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            if sp.simplify(matrix[row, col]) != 0:
                touched.add(row)
                touched.add(col)
    overlay_nodes = [node for index, node in enumerate(nodes) if index in touched]
    compact_index = {original_index: compact for compact, original_index in enumerate(index for index in range(len(nodes)) if index in touched)}
    return overlay_nodes, compact_index


def _c_double_declarations(names: Sequence[str], indent: str = "    ") -> list[str]:
    lines: list[str] = []
    for name in sorted({_c_symbol_name(item) for item in names if str(item).strip()}):
        lines.append(f"{indent}double {name} = 0.0;")
    return lines


def _c_declaration_group(title: str, names: Sequence[str], indent: str = "    ") -> list[str]:
    declarations = _c_double_declarations(names, indent)
    if not declarations:
        return []
    return [f"{indent}/* {title} */", *declarations]


def _c_should_cse_scalar(expr: sp.Expr) -> bool:
    expr = sp.simplify(expr)
    if expr == 0:
        return False
    return int(sp.count_ops(expr, visual=False)) >= 3


def _c_signed_canonical_expr(expr: sp.Expr) -> tuple[sp.Expr, int]:
    expr = sp.simplify(expr)
    if expr == 0:
        return expr, 1
    if expr.could_extract_minus_sign():
        return sp.simplify(-expr), -1
    return expr, 1


_SOURCE_CSE_MAX_ASSIGNMENTS = 80
_SOURCE_CSE_MAX_OPS = 2500
_SOURCE_CSE_MAX_CHARS = 18000
_SOURCE_CSE_MIN_SAVINGS = 24


def _c_source_cse_prefix(fallback_prefix: str) -> str:
    clean = _c_symbol_name(fallback_prefix or "source")
    if clean.startswith(("ramG", "codeG", "G_", "Gred", "G")):
        return "sourceG_tmp"
    if clean.startswith(("Ihis", "Inj")):
        return "sourceIhis_tmp"
    return f"{clean}_source_tmp"


def _c_repeated_denominator_temps(
    exprs: Sequence[sp.Expr],
    fallback_prefix: str,
) -> tuple[list[tuple[str, sp.Expr]], list[sp.Expr]]:
    if len(exprs) < 2:
        return [], list(exprs)
    denom_info: dict[str, dict] = {}
    fractions: list[tuple[sp.Expr, sp.Expr]] = []
    for index, expr in enumerate(exprs):
        numer, denom = sp.sympify(expr).as_numer_denom()
        fractions.append((sp.sympify(numer), sp.sympify(denom)))
        if denom == 1:
            continue
        ops = int(sp.count_ops(denom, visual=False))
        if ops <= 0 or ops > 80 or len(str(denom)) > 600:
            continue
        key = sp.srepr(denom)
        item = denom_info.setdefault(key, {"denom": denom, "count": 0, "first_index": index})
        item["count"] += 1

    repeated = [
        item
        for item in denom_info.values()
        if int(item["count"]) >= 2
    ]
    if not repeated:
        return [], list(exprs)

    prefix = _c_source_cse_prefix(fallback_prefix)
    temps: list[tuple[str, sp.Expr]] = []
    denom_key_to_symbol: dict[str, sp.Symbol] = {}
    for temp_index, item in enumerate(sorted(repeated, key=lambda value: int(value["first_index"]))):
        name = f"{prefix}{temp_index}"
        denom = sp.sympify(item["denom"])
        temps.append((name, 1 / denom))
        denom_key_to_symbol[sp.srepr(denom)] = sp.Symbol(name)

    rewritten: list[sp.Expr] = []
    for original, (numer, denom) in zip(exprs, fractions):
        symbol = denom_key_to_symbol.get(sp.srepr(denom))
        if symbol is None:
            rewritten.append(sp.sympify(original))
        else:
            rewritten.append(sp.Mul(numer, symbol, evaluate=False))
    return temps, rewritten


def _c_source_level_cse(
    exprs: Sequence[sp.Expr],
    fallback_prefix: str,
) -> tuple[list[tuple[str, sp.Expr]], list[sp.Expr]]:
    """Bounded CSE inside one scalar assignment batch.

    This is intentionally a code-generation optimization only.  It never uses
    cancel/factor/full simplification to prove equivalence, and it silently
    returns the original expressions if the batch is too large.
    """
    normalized = [sp.sympify(expr) for expr in exprs]
    if len(normalized) < 2 or len(normalized) > _SOURCE_CSE_MAX_ASSIGNMENTS:
        return [], normalized
    total_ops = sum(int(sp.count_ops(expr, visual=False)) for expr in normalized)
    if total_ops < 12 or total_ops > _SOURCE_CSE_MAX_OPS:
        return [], normalized
    total_chars = sum(len(str(expr)) for expr in normalized)
    if total_chars < 96 or total_chars > _SOURCE_CSE_MAX_CHARS:
        return [], normalized

    denominator_temps, normalized = _c_repeated_denominator_temps(normalized, fallback_prefix)
    try:
        replacements, reduced = sp.cse(
            normalized,
            symbols=sp.numbered_symbols(_c_source_cse_prefix(fallback_prefix), start=len(denominator_temps)),
            order="none",
        )
    except Exception:
        return [], normalized
    if not replacements:
        return denominator_temps, normalized

    try:
        before_cost = sum(len(_ccode(expr)) for expr in exprs)
        after_cost = (
            sum(len(_ccode(expr)) for _name, expr in denominator_temps)
            + sum(len(_ccode(expr)) for _name, expr in replacements)
            + sum(len(_ccode(expr)) for expr in reduced)
        )
    except Exception:
        return denominator_temps, normalized
    if not denominator_temps and after_cost + _SOURCE_CSE_MIN_SAVINGS >= before_cost:
        return [], normalized

    temps = [(str(name), sp.sympify(expr)) for name, expr in replacements]
    return [*denominator_temps, *temps], [sp.sympify(expr) for expr in reduced]


def _c_scalar_assignment_cse(
    assignments: Sequence[tuple[str, sp.Expr, str | None, str | None, str | None]],
    fallback_prefix: str,
    indent: str = "    ",
) -> tuple[list[str], list[str], list[str]]:
    original_exprs = [sp.simplify(expr) for _target, expr, _comment, _suggested, _label in assignments]
    source_temps, source_reduced_exprs = _c_source_level_cse(original_exprs, fallback_prefix)
    normalized = [
        (target, sp.simplify(expr), comment)
        for (target, _raw_expr, comment, _suggested, _label), expr in zip(assignments, source_reduced_exprs)
    ]
    groups: dict[str, dict] = {}
    occurrences: list[tuple[str | None, int, sp.Expr]] = []
    for index, expr in enumerate(source_reduced_exprs):
        expr = sp.simplify(expr)
        if expr == 0:
            occurrences.append((None, 1, expr))
            continue
        canonical, sign = _c_signed_canonical_expr(expr)
        if sp.simplify(expr - sign * canonical) != 0:
            occurrences.append((None, 1, expr))
            continue
        key = sp.srepr(canonical)
        group = groups.setdefault(
            key,
            {
                "canonical": canonical,
                "count": 0,
                "first_index": index,
                "preferred_index": None,
            },
        )
        group["count"] += 1
        if sign > 0 and group["preferred_index"] is None:
            group["preferred_index"] = index
        occurrences.append((key, sign, expr))

    temp_keys: set[str] = set()
    for key, group in groups.items():
        canonical = group["canonical"]
        ops = int(sp.count_ops(canonical, visual=False))
        if _c_should_cse_scalar(canonical) or (group["count"] > 1 and ops >= 2):
            temp_keys.add(key)

    used_names: set[str] = set()
    key_to_temp: dict[str, str] = {}
    temp_names: list[str] = []
    compute_lines: list[str] = []

    if source_temps:
        compute_lines.append(f"{indent}/* Repeated source-level subexpressions for this generated block. */")
    for name, expr in source_temps:
        temp_names.append(name)
        compute_lines.append(f"{indent}{name} = {_ccode(expr)};")

    for key in sorted(temp_keys, key=lambda item: groups[item]["first_index"]):
        group = groups[key]
        preferred_index = group["preferred_index"]
        if preferred_index is None:
            preferred_index = group["first_index"]
        _, _, _, suggested_name, formula_label = assignments[preferred_index]
        canonical = group["canonical"]
        base_name = _c_symbol_name(suggested_name or f"{fallback_prefix}_{preferred_index}")
        name = base_name
        suffix = 1
        while name in used_names:
            suffix += 1
            name = f"{base_name}_{suffix}"
        used_names.add(name)
        key_to_temp[key] = name
        temp_names.append(name)
        label = formula_label or assignments[preferred_index][0]
        compute_lines.append(f"{indent}/* {name} represents {label}: {_ccode(canonical)}. */")
        compute_lines.append(f"{indent}{name} = {_ccode(canonical)};")

    expr_codes: list[str] = []
    for key, sign, expr in occurrences:
        if key is None or key not in key_to_temp:
            expr_codes.append(_ccode(expr))
            continue
        temp = key_to_temp[key]
        expr_codes.append(temp if sign > 0 else f"-{temp}")

    assignment_lines: list[str] = []
    for (target, _, comment), expr_code in zip(normalized, expr_codes):
        if comment:
            assignment_lines.append(f"{indent}{comment}")
        if "{expr}" in target:
            assignment_lines.append(f"{indent}{target.replace('{expr}', expr_code)};")
        else:
            assignment_lines.append(f"{indent}{target} = {expr_code};")
    return temp_names, compute_lines, assignment_lines


def _c_section_warning(title: str, body: Sequence[str], indent: str = "    ") -> list[str]:
    bar = "*" * 72
    lines = [f"{indent}/* {bar}", f"{indent} * {title}"]
    lines.extend(f"{indent} * {line}" for line in body)
    lines.append(f"{indent} * {bar} */")
    lines.extend(["", "", ""])
    return lines


def _var_g_name(row_node: str, col_node: str, node_display_names: dict[str, str] | None = None) -> str:
    row_name = _c_node_variable_name(row_node, node_display_names)
    col_name = _c_node_variable_name(col_node, node_display_names)
    return f"varG_{row_name}_{col_name}"


def _scalar_g_name(prefix: str, row_node: str, col_node: str, node_display_names: dict[str, str] | None = None) -> str:
    row_name = _c_node_variable_name(row_node, node_display_names)
    col_name = _c_node_variable_name(col_node, node_display_names)
    return f"{prefix}_{row_name}_{col_name}"


def _block_element_name(
    block: str,
    row: int,
    col: int,
    external_nodes: Sequence[str],
    node_display_names: dict[str, str] | None = None,
) -> str:
    if block == "Grr":
        return _scalar_g_name("Grr", external_nodes[row], external_nodes[col], node_display_names)
    if block == "Grk":
        row_name = _c_node_variable_name(external_nodes[row], node_display_names)
        return f"Grk_{row_name}_k{col + 1}"
    if block == "Gkr":
        col_name = _c_node_variable_name(external_nodes[col], node_display_names)
        return f"Gkr_k{row + 1}_{col_name}"
    if block == "Gkk":
        return f"Gkk_k{row + 1}_k{col + 1}"
    if block == "W":
        return f"W_{row + 1}_{col + 1}"
    raise ValueError(f"Unsupported block alias kind: {block}")


def _block_element_label(
    block: str,
    row: int,
    col: int,
    external_nodes: Sequence[str],
    node_display_names: dict[str, str] | None = None,
) -> str:
    if block == "Grr":
        return f"Grr[{_c_display_node(external_nodes[row], node_display_names)},{_c_display_node(external_nodes[col], node_display_names)}]"
    if block == "Grk":
        return f"Grk[{_c_display_node(external_nodes[row], node_display_names)},k{col + 1}]"
    if block == "Gkr":
        return f"Gkr[k{row + 1},{_c_display_node(external_nodes[col], node_display_names)}]"
    if block == "Gkk":
        return f"Gkk[k{row + 1},k{col + 1}]"
    if block == "W":
        return f"W[{row + 1},{col + 1}]"
    raise ValueError(f"Unsupported block alias kind: {block}")


def _block_alias_entries(
    matrix: sp.Matrix,
    block: str,
    external_nodes: Sequence[str],
    node_display_names: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    matrix = sp.Matrix(matrix)
    entries: list[dict[str, object]] = []
    grouped: dict[str, list[tuple[int, int, str, sp.Expr]]] = {}
    order: list[str] = []
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            expr = sp.simplify(matrix[row, col])
            if expr == 0:
                continue
            label = _block_element_label(block, row, col, external_nodes, node_display_names)
            expr_key = sp.srepr(expr)
            if expr_key not in grouped:
                grouped[expr_key] = []
                order.append(expr_key)
            grouped[expr_key].append((row, col, label, expr))
    shared_index = 0
    for expr_key in order:
        items = grouped[expr_key]
        labels = [label for _, _, label, _ in items]
        expr = items[0][3]
        if len(items) == 1:
            row, col, _, _ = items[0]
            name = _block_element_name(block, row, col, external_nodes, node_display_names)
        else:
            shared_index += 1
            name = f"{block}_shared_{shared_index}"
        for item_index, (row, col, label, item_expr) in enumerate(items):
            entries.append(
                {
                    "row": row,
                    "col": col,
                    "name": name,
                    "label": label,
                    "labels": labels,
                    "expr": item_expr,
                    "primary": item_index == 0,
                }
            )
    return entries


def _block_alias_compute_lines(entries: Sequence[dict[str, object]], indent: str = "    ") -> list[str]:
    lines: list[str] = []
    for entry in entries:
        if not bool(entry.get("primary", True)):
            continue
        name = str(entry["name"])
        labels = [str(label) for label in entry.get("labels", [entry["label"]])]
        label = ", ".join(labels)
        expr = sp.sympify(entry["expr"])
        lines.append(f"{indent}/* {name} represents {label}: {_ccode(expr)}. */")
        lines.append(f"{indent}{name} = {_ccode(expr)};")
    return lines


def _matrix_set_alias_lines(
    entries: Sequence[dict[str, object]],
    matrix_name: str,
    fn: str = "set",
    row_map: Sequence[int] | None = None,
    col_map: Sequence[int] | None = None,
    indent: str = "    ",
) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        row = int(entry["row"])
        col = int(entry["col"])
        if row_map is not None:
            if row not in row_map:
                continue
            target_row = list(row_map).index(row)
        else:
            target_row = row
        if col_map is not None:
            if col not in col_map:
                continue
            target_col = list(col_map).index(col)
        else:
            target_col = col
        lines.append(f"{indent}{fn}(&{matrix_name}, {target_row}, {target_col}, {entry['name']});")
    return lines


def _matrix_copy_subblock_code(
    dst: str,
    src: str,
    row_offset: int,
    col_offset: int,
    rows: int,
    cols: int,
    fn: str = "set_CODE",
    getter: str = "get_CODE",
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}{fn}(&{dst}, {row + row_offset}, {col + col_offset}, {getter}(&{src}, {row}, {col}));"
        for row in range(rows)
        for col in range(cols)
    ]


def _alias_lookup(entries: Sequence[dict[str, object]]) -> dict[tuple[int, int], str]:
    return {(int(entry["row"]), int(entry["col"])): str(entry["name"]) for entry in entries}


def _runtime_w_alias_entries(nk: int) -> list[dict[str, object]]:
    return [
        {
            "row": row,
            "col": col,
            "name": f"get_CODE(&W_code, {row}, {col})",
            "label": f"W[{row + 1},{col + 1}]",
            "labels": [f"W[{row + 1},{col + 1}]"],
            "expr": sp.Integer(0),
            "primary": False,
        }
        for row in range(nk)
        for col in range(nk)
    ]


def _diagonal_plus_coupled_w_matrix_names(details: dict) -> list[str]:
    D = sp.Matrix(details.get("D", []))
    U = sp.Matrix(details.get("U", []))
    S = sp.Matrix(details.get("S", []))
    kd = D.rows
    ks = S.rows
    if not kd or not ks or U.shape != (kd, ks):
        return []
    return [
        "D",
        "U",
        "S",
        "inv_D",
        "U_T",
        "tmp_UT_invD",
        "tmp_UT_invD_U",
        "M",
        "M_inv",
        "tmp_Dinv_U",
        "tmp_Dinv_U_Minv",
        "tmp_Dinv_U_Minv_UT",
        "tmp_Dinv_U_Minv_UT_Dinv",
        "W_DD",
        "W_DS",
        "tmp_Minv_UT",
        "W_SD",
        "W_SS",
    ]


def _diagonal_plus_coupled_w_matrix_dims(details: dict) -> list[tuple[str, int, int]]:
    D = sp.Matrix(details.get("D", []))
    U = sp.Matrix(details.get("U", []))
    S = sp.Matrix(details.get("S", []))
    kd = D.rows
    ks = S.rows
    if not kd or not ks or U.shape != (kd, ks):
        return []
    return [
        ("D", kd, kd),
        ("U", kd, ks),
        ("S", ks, ks),
        ("inv_D", kd, kd),
        ("U_T", ks, kd),
        ("tmp_UT_invD", ks, kd),
        ("tmp_UT_invD_U", ks, ks),
        ("M", ks, ks),
        ("M_inv", ks, ks),
        ("tmp_Dinv_U", kd, ks),
        ("tmp_Dinv_U_Minv", kd, ks),
        ("tmp_Dinv_U_Minv_UT", kd, kd),
        ("tmp_Dinv_U_Minv_UT_Dinv", kd, kd),
        ("W_DD", kd, kd),
        ("W_DS", kd, ks),
        ("tmp_Minv_UT", ks, kd),
        ("W_SD", ks, kd),
        ("W_SS", ks, ks),
    ]


def _diagonal_plus_coupled_w_code_lines(details: dict, fn: str = "set_CODE", getter: str = "get_CODE") -> list[str]:
    D = sp.Matrix(details.get("D", []))
    U = sp.Matrix(details.get("U", []))
    S = sp.Matrix(details.get("S", []))
    kd = D.rows
    ks = S.rows
    if not kd or not ks or U.shape != (kd, ks):
        return []
    lines = [
        "    /* Build W = inverse(Gkk) from Gkk = [[D, U], [U^T, S]]. */",
        "    /* Build M = S - U^T * inv_D * U using RTDS MATRIX_ helpers. */",
        *_c_matrix_set_lines(D, "D", fn=fn),
        *_c_matrix_set_lines(U, "U", fn=fn),
        *_c_matrix_set_lines(S, "S", fn=fn),
    ]
    for index in range(kd):
        lines.append(f"    {fn}(&inv_D, {index}, {index}, 1.0 / {getter}(&D, {index}, {index}));")
    for row in range(ks):
        for col in range(kd):
            lines.append(f"    {fn}(&U_T, {row}, {col}, {getter}(&U, {col}, {row}));")
    lines.extend(
        [
            "    matrix_mult_CODE(&tmp_UT_invD, &U_T, &inv_D);",
            "    matrix_mult_CODE(&tmp_UT_invD_U, &tmp_UT_invD, &U);",
            "    matrix_subtract_CODE(&M, &S, &tmp_UT_invD_U);",
            *_matrix_code_sym_inverse_lines("M", "M_inv", ks, fn=fn, getter=getter),
            "    matrix_mult_CODE(&tmp_Dinv_U, &inv_D, &U);",
            "    matrix_mult_CODE(&tmp_Dinv_U_Minv, &tmp_Dinv_U, &M_inv);",
            "    matrix_mult_CODE(&tmp_Dinv_U_Minv_UT, &tmp_Dinv_U_Minv, &U_T);",
            "    matrix_mult_CODE(&tmp_Dinv_U_Minv_UT_Dinv, &tmp_Dinv_U_Minv_UT, &inv_D);",
            "    matrix_add_CODE(&W_DD, &inv_D, &tmp_Dinv_U_Minv_UT_Dinv);",
            "    matrix_scalarMult_CODE(&W_DS, &tmp_Dinv_U_Minv, -1.0);",
            "    matrix_mult_CODE(&tmp_Minv_UT, &M_inv, &U_T);",
            "    matrix_mult_CODE(&W_SD, &tmp_Minv_UT, &inv_D);",
            "    matrix_scalarMult_CODE(&W_SD, &W_SD, -1.0);",
        ]
    )
    lines.extend(_matrix_copy_subblock_code("W_SS", "M_inv", 0, 0, ks, ks, fn=fn, getter=getter))
    lines.extend(_matrix_copy_subblock_code("W_code", "W_DD", 0, 0, kd, kd, fn=fn, getter=getter))
    lines.extend(_matrix_copy_subblock_code("W_code", "W_DS", 0, kd, kd, ks, fn=fn, getter=getter))
    lines.extend(_matrix_copy_subblock_code("W_code", "W_SD", kd, 0, ks, kd, fn=fn, getter=getter))
    lines.extend(_matrix_copy_subblock_code("W_code", "W_SS", kd, kd, ks, ks, fn=fn, getter=getter))
    return lines


def _matrix_code_sym_inverse_lines(
    matrix_name: str,
    inverse_name: str,
    dim: int,
    fn: str = "set_CODE",
    getter: str = "get_CODE",
) -> list[str]:
    if dim <= 0:
        return []
    if dim == 1:
        return [f"    {fn}(&{inverse_name}, 0, 0, 1.0 / {getter}(&{matrix_name}, 0, 0));"]
    if dim == 2:
        return [
            f"    double {inverse_name}_11 = 0.0;",
            f"    double {inverse_name}_12 = 0.0;",
            f"    double {inverse_name}_22 = 0.0;",
            (
                f"    mat_2x2_sym_inv_code({getter}(&{matrix_name}, 0, 0), "
                f"{getter}(&{matrix_name}, 0, 1), {getter}(&{matrix_name}, 1, 1),"
            ),
            f"                         &{inverse_name}_11, &{inverse_name}_12, &{inverse_name}_22);",
            f"    {fn}(&{inverse_name}, 0, 0, {inverse_name}_11);",
            f"    {fn}(&{inverse_name}, 0, 1, {inverse_name}_12);",
            f"    {fn}(&{inverse_name}, 1, 0, {inverse_name}_12);",
            f"    {fn}(&{inverse_name}, 1, 1, {inverse_name}_22);",
        ]
    if dim == 3:
        return [
            f"    double {inverse_name}_11 = 0.0;",
            f"    double {inverse_name}_12 = 0.0;",
            f"    double {inverse_name}_13 = 0.0;",
            f"    double {inverse_name}_22 = 0.0;",
            f"    double {inverse_name}_23 = 0.0;",
            f"    double {inverse_name}_33 = 0.0;",
            (
                f"    mat_3x3_sym_inv_code({getter}(&{matrix_name}, 0, 0), "
                f"{getter}(&{matrix_name}, 0, 1), {getter}(&{matrix_name}, 0, 2),"
            ),
            (
                f"                         {getter}(&{matrix_name}, 1, 1), "
                f"{getter}(&{matrix_name}, 1, 2),"
            ),
            f"                         {getter}(&{matrix_name}, 2, 2),",
            (
                f"                         &{inverse_name}_11, &{inverse_name}_12, &{inverse_name}_13,"
            ),
            f"                         &{inverse_name}_22, &{inverse_name}_23,",
            f"                         &{inverse_name}_33);",
            f"    {fn}(&{inverse_name}, 0, 0, {inverse_name}_11);",
            f"    {fn}(&{inverse_name}, 0, 1, {inverse_name}_12);",
            f"    {fn}(&{inverse_name}, 0, 2, {inverse_name}_13);",
            f"    {fn}(&{inverse_name}, 1, 0, {inverse_name}_12);",
            f"    {fn}(&{inverse_name}, 1, 1, {inverse_name}_22);",
            f"    {fn}(&{inverse_name}, 1, 2, {inverse_name}_23);",
            f"    {fn}(&{inverse_name}, 2, 0, {inverse_name}_13);",
            f"    {fn}(&{inverse_name}, 2, 1, {inverse_name}_23);",
            f"    {fn}(&{inverse_name}, 2, 2, {inverse_name}_33);",
        ]
    return [
        f"    /* WARNING: {matrix_name} is {dim}x{dim}; RTDS fast symmetric inverse helpers only cover 2x2 and 3x3. */",
        f"    MATH_matx_invert({dim}, &({matrix_name}.p[0]), {dim}, &({inverse_name}.p[0]), {dim});",
    ]


def _matrix_transpose_copy_lines(
    dst: str,
    src: str,
    rows: str,
    cols: str,
    *,
    setter: str = "set_CODE",
    getter: str = "get_CODE",
    indent: str = "    ",
    comment: str | None = None,
) -> list[str]:
    lines: list[str] = []
    if comment:
        lines.append(f"{indent}/* {comment} */")
    lines.extend(
        [
            f"{indent}for (int row = 0; row < {rows}; row++) {{",
            f"{indent}    for (int col = 0; col < {cols}; col++) {{",
            f"{indent}        {setter}(&{dst}, row, col, {getter}(&{src}, col, row));",
            f"{indent}    }}",
            f"{indent}}}",
        ]
    )
    return lines


def _diagonal_gkk_scalar_gred_code_lines(has_direct: bool) -> list[str]:
    lines = [
        "    /* Diagonal Gkk scalar CODE path: Gred = Grr - sum_k Grk[i,k] * Gkr[k,j] / Gkk[k,k]. */",
        "    {",
        "        double inv_gkk_diag[NK];",
        "        for (int k = 0; k < NK; k++) {",
        "            inv_gkk_diag[k] = 1.0 / get_CODE(&Gkk_code, k, k);",
        "        }",
        "        for (int i = 0; i < NR; i++) {",
        "            for (int j = i; j < NR; j++) {",
        "                double schur = get_CODE(&Grr_code, i, j);",
        "                for (int k = 0; k < NK; k++) {",
        "                    schur -= get_CODE(&Grk_code, i, k) * get_CODE(&Gkr_code, k, j) * inv_gkk_diag[k];",
        "                }",
    ]
    if has_direct:
        lines.append("                schur += get_CODE(&Gred_code, i, j);")
    lines.extend(
        [
            "                set_CODE(&Gred_code, i, j, schur);",
            "            }",
            "        }",
            "    }",
        ]
    )
    return lines


def _upper_tri_matrix_subtract_code_lines(
    dst: str,
    lhs: str,
    rhs: str,
    dim: str = "NR",
    *,
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}for (int row = 0; row < {dim}; row++) {{",
        f"{indent}    for (int col = row; col < {dim}; col++) {{",
        f"{indent}        set_CODE(&{dst}, row, col, get_CODE(&{lhs}, row, col) - get_CODE(&{rhs}, row, col));",
        f"{indent}    }}",
        f"{indent}}}",
    ]


def _upper_tri_matrix_product_code_lines(
    dst: str,
    lhs: str,
    rhs: str,
    dim: str = "NR",
    inner_dim: str = "NK",
    *,
    indent: str = "    ",
) -> list[str]:
    return [
        f"{indent}/* Symmetric product: only upper triangle of {dst} is needed downstream. */",
        f"{indent}for (int row = 0; row < {dim}; row++) {{",
        f"{indent}    for (int col = row; col < {dim}; col++) {{",
        f"{indent}        double acc = 0.0;",
        f"{indent}        for (int k = 0; k < {inner_dim}; k++) {{",
        f"{indent}            acc += get_CODE(&{lhs}, row, k) * get_CODE(&{rhs}, k, col);",
        f"{indent}        }}",
        f"{indent}        set_CODE(&{dst}, row, col, acc);",
        f"{indent}    }}",
        f"{indent}}}",
    ]


def _diagonal_gkk_scalar_ihisred_code_lines(has_direct: bool) -> list[str]:
    lines = [
        "    /* Diagonal Gkk scalar CODE path: Ihisred = Ihisr - sum_k Grk[i,k] * Ihisk[k] / Gkk[k,k]. */",
        "    {",
        "        double inv_gkk_diag[NK];",
        "        for (int k = 0; k < NK; k++) {",
        "            inv_gkk_diag[k] = 1.0 / get_CODE(&Gkk_code, k, k);",
        "        }",
        "        for (int i = 0; i < NR; i++) {",
        "            double ihis = get_CODE(&Ihisr_code, i, 0);",
        "            for (int k = 0; k < NK; k++) {",
        "                ihis -= get_CODE(&Grk_code, i, k) * get_CODE(&Ihisk_code, k, 0) * inv_gkk_diag[k];",
        "            }",
    ]
    if has_direct:
        lines.append("            ihis += get_CODE(&Ihisred_code, i, 0);")
    lines.extend(
        [
            "            set_CODE(&Ihisred_code, i, 0, ihis);",
            "        }",
            "    }",
        ]
    )
    return lines


def _diagonal_gkk_scalar_vk_code_lines(need_vr: bool, need_ihisk: bool) -> list[str]:
    lines = [
        "    /* Diagonal Gkk scalar CODE path: Vk[k] = -(Gkr[k,*] * Vr + Ihisk[k]) / Gkk[k,k]. */",
        "    {",
        "        double inv_gkk_diag[NK];",
        "        for (int k = 0; k < NK; k++) {",
        "            inv_gkk_diag[k] = 1.0 / get_CODE(&Gkk_code, k, k);",
        "        }",
        "        for (int k = 0; k < NK; k++) {",
        "            double vk_sum = 0.0;",
    ]
    if need_vr:
        lines.extend(
            [
                "            for (int j = 0; j < NR; j++) {",
                "                vk_sum += get_CODE(&Gkr_code, k, j) * get_CODE(&Vr_code, j, 0);",
                "            }",
            ]
        )
    if need_ihisk:
        lines.append("            vk_sum += get_CODE(&Ihisk_code, k, 0);")
    lines.extend(
        [
            "            set_CODE(&Vk_code, k, 0, -vk_sum * inv_gkk_diag[k]);",
            "        }",
            "    }",
        ]
    )
    return lines


def _gred_alias_formula(
    row: int,
    col: int,
    Grr_aliases: Sequence[dict[str, object]],
    Grk_aliases: Sequence[dict[str, object]],
    W_aliases: Sequence[dict[str, object]],
    Gkr_aliases: Sequence[dict[str, object]],
    nk: int,
) -> str:
    grr = _alias_lookup(Grr_aliases)
    grk = _alias_lookup(Grk_aliases)
    w = _alias_lookup(W_aliases)
    gkr = _alias_lookup(Gkr_aliases)
    expr = grr.get((row, col), "0.0")
    for left_k in range(nk):
        grk_name = grk.get((row, left_k))
        if not grk_name:
            continue
        for right_k in range(nk):
            w_name = w.get((left_k, right_k))
            gkr_name = gkr.get((right_k, col))
            if not w_name or not gkr_name:
                continue
            product = f"{grk_name}*{w_name}*{gkr_name}"
            expr = f"{expr} - {product}" if expr != "0.0" else f"-{product}"
    return expr


def _upper_triangular_node_pairs(nodes: Sequence[str]) -> list[tuple[int, int, str, str]]:
    pairs: list[tuple[int, int, str, str]] = []
    for row, row_node in enumerate(nodes):
        for col in range(row, len(nodes)):
            pairs.append((row, col, row_node, nodes[col]))
    return pairs


def _upper_triangular_nonzero_node_pairs(
    nodes: Sequence[str],
    matrix: sp.Matrix,
) -> list[tuple[int, int, str, str]]:
    matrix = sp.Matrix(matrix)
    pairs: list[tuple[int, int, str, str]] = []
    for row, row_node in enumerate(nodes):
        for col in range(row, len(nodes)):
            if row < matrix.rows and col < matrix.cols and sp.simplify(matrix[row, col]) != 0:
                pairs.append((row, col, row_node, nodes[col]))
    return pairs


def _upper_triangular_stage_node_pairs(
    nodes: Sequence[str],
    stage_matrix: Sequence[Sequence[str]],
    stages: set[str],
) -> list[tuple[int, int, str, str]]:
    pairs: list[tuple[int, int, str, str]] = []
    for row, row_node in enumerate(nodes):
        for col in range(row, len(nodes)):
            upper_stage = str(stage_matrix[row][col]) if row < len(stage_matrix) and col < len(stage_matrix[row]) else "UNKNOWN"
            lower_stage = str(stage_matrix[col][row]) if col < len(stage_matrix) and row < len(stage_matrix[col]) else upper_stage
            if upper_stage in stages or lower_stage in stages:
                pairs.append((row, col, row_node, nodes[col]))
    return pairs


def _c_matrix_set_nonzero_lines(matrix: sp.Matrix, name: str, setter: str = "set_CODE") -> list[str]:
    matrix = sp.Matrix(matrix)
    lines: list[str] = []
    for row in range(matrix.rows):
        for col in range(matrix.cols):
            value = sp.simplify(matrix[row, col])
            if value != 0:
                lines.append(f"    {setter}(&{name}, {row}, {col}, {_ccode(value)});")
    return lines


def _c_matrix_add_nonzero_lines(
    matrix: sp.Matrix,
    name: str,
    setter: str = "set_CODE",
    getter: str = "get_CODE",
    *,
    upper_triangle_only: bool = False,
) -> list[str]:
    matrix = sp.Matrix(matrix)
    lines: list[str] = []
    for row in range(matrix.rows):
        col_start = row if upper_triangle_only else 0
        for col in range(col_start, matrix.cols):
            value = sp.simplify(matrix[row, col])
            if value != 0:
                lines.append(
                    f"    {setter}(&{name}, {row}, {col}, {getter}(&{name}, {row}, {col}) + {_ccode(value)});"
                )
    return lines


def _c_add_expr(base: str, addition: sp.Expr) -> str:
    addition = sp.simplify(addition)
    if addition == 0:
        return base
    if base == "0.0":
        return _ccode(addition)
    return f"({base}) + ({_ccode(addition)})"


def _c_vector_add_nonzero_lines(vector: sp.Matrix, name: str, setter: str = "set_CODE", getter: str = "get_CODE") -> list[str]:
    vector = _as_column_vector(sp.Matrix(vector), sp.Matrix(vector).rows, name)
    lines: list[str] = []
    for row in range(vector.rows):
        value = sp.simplify(vector[row, 0])
        if value != 0:
            lines.append(
                f"    {setter}(&{name}, {row}, 0, {getter}(&{name}, {row}, 0) + {_ccode(value)});"
            )
    return lines


def _c_emit_rtds_stage_sections(
    plan: dict | None,
    node_display_names: dict[str, str] | None = None,
) -> list[str]:
    if not plan:
        return []
    external_nodes = list(plan.get("external_nodes", []))
    internal_nodes = list(plan.get("internal_nodes", []))
    analysis = plan.get("dependency_analysis") or {}
    Grr = sp.Matrix(plan.get("Grr", []))
    Grk = sp.Matrix(plan.get("Grk", []))
    Gkr = sp.Matrix(plan.get("Gkr", []))
    Gkk = sp.Matrix(plan.get("Gkk", []))
    Ihisr = sp.Matrix(plan.get("Ihisr", []))
    Ihisk = sp.Matrix(plan.get("Ihisk", []))
    Gred = sp.Matrix(plan.get("Gred", []))
    Gred_direct = sp.Matrix(plan.get("Gred_direct", sp.zeros(len(external_nodes), len(external_nodes))))
    W = sp.Matrix(plan.get("W", []))
    w_runtime_inverse = bool(plan.get("W_runtime_inverse"))
    block_type = str(plan.get("block_type") or "")
    details = plan.get("details") or {}
    Gred_stage = analysis.get("Gred_stage") or []
    symbol_table = analysis.get("symbol_table") or {}
    ram_Gred_direct, code_Gred_direct = _split_matrix_ram_and_code_terms(Gred_direct, symbol_table)
    ram_Gred = _stage_entries(Gred, Gred_stage, "RAM_INIT")
    add_ram_direct_to_ram_owned_gred = bool(plan.get("add_ram_direct_to_ram_owned_gred"))
    for row in range(ram_Gred_direct.rows):
        for col in range(ram_Gred_direct.cols):
            stage = str(Gred_stage[row][col]) if row < len(Gred_stage) and col < len(Gred_stage[row]) else "UNKNOWN"
            if stage != "RAM_INIT" or add_ram_direct_to_ram_owned_gred:
                ram_Gred[row, col] += ram_Gred_direct[row, col]
    code_gred_entries = _find_code_owned_entries(Gred_stage)
    direct_code_entries = [
        (row, col)
        for row in range(code_Gred_direct.rows)
        for col in range(code_Gred_direct.cols)
        if sp.simplify(code_Gred_direct[row, col]) != 0
    ]
    code_gred_entries = sorted({*code_gred_entries, *direct_code_entries})
    dynamic_gred = bool(code_gred_entries)
    full_gred_code_path = bool(dynamic_gred and code_gred_entries and len(code_gred_entries) == Gred.rows * Gred.cols)
    partial_gred_code_path = bool(dynamic_gred and not full_gred_code_path)
    dynamic_subblock = {
        **detect_rectangular_dynamic_blocks(code_gred_entries),
        "owner_matrix": Gred_stage,
        "ram_entries": [
            (row, col)
            for row in range(ram_Gred.rows)
            for col in range(ram_Gred.cols)
            if sp.simplify(ram_Gred[row, col]) != 0
        ],
        "code_entries": code_gred_entries,
        "unknown_entries": [
            (row, col)
            for row in range(Gred.rows)
            for col in range(Gred.cols)
            if row < len(Gred_stage)
            and col < len(Gred_stage[row])
            and str(Gred_stage[row][col]) == "UNKNOWN"
        ],
    }
    gred_dyn_rows = [int(row) for row in dynamic_subblock.get("rows", [])]
    gred_dyn_cols = [int(col) for col in dynamic_subblock.get("cols", [])]
    rectangular_gred_dyn_path = bool(partial_gred_code_path and dynamic_subblock.get("is_rectangular") and gred_dyn_rows and gred_dyn_cols)
    need_gred_code = bool(dynamic_gred and (full_gred_code_path or not rectangular_gred_dyn_path))
    Ihisred = _as_column_vector(sp.Matrix(plan.get("Ihisred", [])), len(external_nodes), "Ihisred")
    Ihisred_direct = _as_column_vector(
        sp.Matrix(plan.get("Ihisred_direct", sp.zeros(len(external_nodes), 1))),
        len(external_nodes),
        "Ihisred_direct",
    )
    Ihisred_stage = [str(stage) for stage in (analysis.get("Ihisred_stage") or [])]
    runtime_w_matrix_path = bool(w_runtime_inverse or (block_type == "diagonal_plus_coupled" and sp.Matrix(details.get("W", [])).rows))
    Ihisred_correction = sp.Matrix(Grk) * W * sp.Matrix(Ihisk) if Grk.rows and W.rows and Ihisk.rows and not runtime_w_matrix_path else sp.zeros(len(external_nodes), 1)
    ihisred_dyn_rows = [
        row
        for row in range(len(external_nodes))
        if (
            (
                any(sp.simplify(Grk[row, col]) != 0 for col in range(Grk.cols))
                and any(sp.simplify(Ihisk[col, 0]) != 0 for col in range(Ihisk.rows))
            )
            if runtime_w_matrix_path
            else row < Ihisred_correction.rows and sp.simplify(Ihisred_correction[row, 0]) != 0
        )
        and (row >= len(Ihisred_stage) or Ihisred_stage[row] != "RAM_INIT")
    ]
    partial_ihisred_code_path = bool(ihisred_dyn_rows and len(ihisred_dyn_rows) < len(external_nodes))
    full_ihisred_code_path = bool(ihisred_dyn_rows and not partial_ihisred_code_path)
    need_ihisred_code = full_ihisred_code_path
    vk_from_vr = W * sp.Matrix(Gkr) if W.rows and Gkr.rows and not w_runtime_inverse else sp.zeros(len(internal_nodes), len(external_nodes))
    vk_from_ihisk = W * sp.Matrix(Ihisk) if W.rows and Ihisk.rows and not w_runtime_inverse else sp.zeros(len(internal_nodes), 1)
    need_vk_recovery = bool(internal_nodes)
    need_vk_vr_path = bool(
        need_vk_recovery
        and (_matrix_has_nonzero(Gkr) if w_runtime_inverse else _matrix_has_nonzero(vk_from_vr))
    )
    need_vk_ihis_path = bool(
        need_vk_recovery
        and (_matrix_has_nonzero(Ihisk) if w_runtime_inverse else _matrix_has_nonzero(vk_from_ihisk))
    )
    need_tmp_grk_w_code = full_gred_code_path or full_ihisred_code_path
    need_Grr_code = bool(full_gred_code_path or (dynamic_gred and not rectangular_gred_dyn_path))
    need_Grk_code = bool(full_gred_code_path or full_ihisred_code_path or (dynamic_gred and not rectangular_gred_dyn_path))
    need_Gkr_code = bool(full_gred_code_path or need_vk_vr_path or (dynamic_gred and not rectangular_gred_dyn_path))
    need_W_code = bool(dynamic_gred or full_ihisred_code_path or partial_ihisred_code_path or need_vk_vr_path or need_vk_ihis_path)
    need_Gkk_code = bool(w_runtime_inverse and need_W_code)
    structured_w_builder = bool(block_type == "diagonal_plus_coupled" and need_W_code and not w_runtime_inverse)
    need_Ihisr_code = bool(full_ihisred_code_path)
    need_Ihisk_code = bool(full_ihisred_code_path or partial_ihisred_code_path or need_vk_ihis_path)
    need_Vr_code = bool(need_vk_vr_path)
    need_Vk_code = bool(need_vk_recovery)
    need_tmp_w_gkr_code = bool(need_vk_vr_path)
    need_tmp_w_gkr_vr_code = bool(need_vk_vr_path)
    need_tmp_w_ihisk_code = bool(need_vk_ihis_path)
    need_tmp_vk_sum_code = bool(need_vk_vr_path and need_vk_ihis_path)
    gkk_diagonal_light = bool(Gkk.rows and _matrix_is_diagonal_light(Gkk))
    ram_static_matrix_precompute_allowed = bool(
        not dynamic_gred
        and not w_runtime_inverse
        and not structured_w_builder
        and _matrix_is_ram_stage(Grk, symbol_table)
        and _matrix_is_ram_stage(Gkr, symbol_table)
        and _matrix_is_ram_stage(W, symbol_table)
    )
    diagonal_gkk_scalar_code_path = bool(
        gkk_diagonal_light
        and need_W_code
        and not ram_static_matrix_precompute_allowed
        and not structured_w_builder
        and not w_runtime_inverse
        and (full_gred_code_path or full_ihisred_code_path or need_vk_vr_path or need_vk_ihis_path)
    )
    if diagonal_gkk_scalar_code_path:
        need_tmp_grk_w_code = False
        need_W_code = False
        need_Gkk_code = True
        need_tmp_w_gkr_code = False
        need_tmp_w_gkr_vr_code = False
        need_tmp_w_ihisk_code = False
        need_tmp_vk_sum_code = False
    reuse_w_gkr_from_grk_w = bool(
        not diagonal_gkk_scalar_code_path
        and need_tmp_w_gkr_code
        and need_tmp_grk_w_code
        and _matrix_is_symmetric_light(Gkk)
        and _matrix_is_transpose_light(Grk, Gkr)
    )
    ram_precompute_grk_w = bool(need_tmp_grk_w_code and ram_static_matrix_precompute_allowed)
    ram_precompute_w_gkr = bool(need_tmp_w_gkr_code and ram_static_matrix_precompute_allowed)
    ram_reuse_w_gkr_from_grk_w = bool(
        ram_precompute_w_gkr and ram_precompute_grk_w and reuse_w_gkr_from_grk_w
    )
    use_fast_symmetric_gkk_inverse = bool(
        need_W_code
        and
        need_Gkk_code
        and Gkk.rows in (1, 2, 3)
        and _matrix_is_symmetric_light(Gkk)
    )
    var_g_pairs = (
        _upper_triangular_stage_node_pairs(external_nodes, Gred_stage, {"CODE_UPDATE", "UNKNOWN", "CODE_PER_STEP"})
        if dynamic_gred
        else []
    )
    direct_var_pairs = _upper_triangular_nonzero_node_pairs(external_nodes, code_Gred_direct) if dynamic_gred else []
    seen_var_pairs = {(row, col) for row, col, _, _ in var_g_pairs}
    var_g_pairs.extend(pair for pair in direct_var_pairs if (pair[0], pair[1]) not in seen_var_pairs)
    code_g_matrices = []
    if need_Grr_code:
        code_g_matrices.append(Grr)
    if (need_Grk_code and not ram_precompute_grk_w) or partial_ihisred_code_path:
        code_g_matrices.append(Grk)
    if need_Gkr_code and not ram_precompute_w_gkr:
        code_g_matrices.append(Gkr)
    if need_Gkk_code:
        code_g_matrices.append(Gkk)
    if need_W_code and not (ram_precompute_grk_w or ram_precompute_w_gkr):
        code_g_matrices.append(W)
    if dynamic_gred:
        code_g_matrices.append(code_Gred_direct)
    code_g_symbol_names = _matrix_symbol_names(*code_g_matrices)
    code_ihis_matrices = [Ihisred]
    if _matrix_has_nonzero(Ihisred_direct):
        code_ihis_matrices.append(Ihisred_direct)
    if need_Ihisr_code or partial_ihisred_code_path:
        code_ihis_matrices.append(Ihisr)
    if need_Ihisk_code:
        code_ihis_matrices.append(Ihisk)
    code_ihis_symbol_names = _matrix_symbol_names(*code_ihis_matrices) - code_g_symbol_names
    code_symbol_names = code_g_symbol_names | code_ihis_symbol_names
    ram_symbol_names = _matrix_symbol_names(ram_Gred) - code_symbol_names
    if not internal_nodes:
        Gred_no_elim = sp.Matrix(Gred) + sp.Matrix(Gred_direct)
        Ihisred_no_elim = _as_column_vector(sp.Matrix(Ihisred), len(external_nodes), "Ihisred") + Ihisred_direct
        symbol_table = analysis.get("symbol_table") or {}
        ram_Gred_no_elim, code_G_no_elim = _split_matrix_ram_and_code_terms(Gred_no_elim, symbol_table)
        ram_overlay_nodes_no_elim, ram_overlay_index_no_elim = _ram_overlay_node_subset(ram_Gred_no_elim, external_nodes)
        dynamic_gred_no_elim = any(sp.simplify(value) != 0 for value in code_G_no_elim)
        var_g_pairs_no_elim = (
            _upper_triangular_nonzero_node_pairs(external_nodes, code_G_no_elim) if dynamic_gred_no_elim else []
        )
        ram_g_assignments_no_elim = [
            (
                f"g_mat_over[{ram_overlay_index_no_elim[row]}][{ram_overlay_index_no_elim[col]}]",
                ram_Gred_no_elim[row, col],
                None,
                _scalar_g_name("ramG", external_nodes[row], external_nodes[col], node_display_names),
                f"G[{_c_display_node(external_nodes[row], node_display_names)},{_c_display_node(external_nodes[col], node_display_names)}]",
            )
            for row in range(ram_Gred_no_elim.rows)
            for col in range(ram_Gred_no_elim.cols)
            if sp.simplify(ram_Gred_no_elim[row, col]) != 0
        ]
        ram_g_temp_names_no_elim, ram_g_compute_lines_no_elim, ram_g_assignment_lines_no_elim = _c_scalar_assignment_cse(
            ram_g_assignments_no_elim,
            "ramG",
        )
        code_g_assignments_no_elim = [
            (
                _var_g_name(row_node, col_node, node_display_names),
                code_G_no_elim[row, col],
                None,
                _scalar_g_name("G", row_node, col_node, node_display_names),
                f"G[{_c_display_node(row_node, node_display_names)},{_c_display_node(col_node, node_display_names)}]",
            )
            for row, col, row_node, col_node in var_g_pairs_no_elim
        ]
        code_g_temp_names_no_elim, code_g_compute_lines_no_elim, code_g_assignment_lines_no_elim = _c_scalar_assignment_cse(
            code_g_assignments_no_elim,
            "G",
        )
        ihis_assignments_no_elim = [
            (
                f"Inj{_c_node_variable_name(node, node_display_names)}",
                Ihisred_no_elim[index, 0],
                None,
                None,
                f"Ihisred[{_c_display_node(node, node_display_names)}]",
            )
            for index, node in enumerate(external_nodes)
        ]
        ihis_temp_names_no_elim, ihis_compute_lines_no_elim, ihis_assignment_lines_no_elim = _c_scalar_assignment_cse(
            ihis_assignments_no_elim,
            "Ihis",
        )
        no_elim_code_names: list[str] = []
        no_elim_g_symbol_names = _matrix_symbol_names(code_G_no_elim)
        no_elim_ihis_symbol_names = _matrix_symbol_names(Ihisred_no_elim) - no_elim_g_symbol_names
        no_elim_symbol_names = no_elim_g_symbol_names | no_elim_ihis_symbol_names
        no_elim_ram_symbols = _matrix_symbol_names(ram_Gred_no_elim) - no_elim_symbol_names
        lines = [
            "/* RTDS lifecycle placement for a network with no eliminated internal nodes.",
            "   No Schur complement is required: use the original G matrix and Ihis vector directly. */",
            "STATIC:",
            "    /* Runtime matrix objects */",
            "    /* No runtime MATRIX_ objects are required on this no-elimination path. */",
            *_c_declaration_group("User G/CODE symbols", no_elim_g_symbol_names),
            *_c_declaration_group("User Ihis/history symbols", no_elim_ihis_symbol_names),
            *_c_declaration_group("CODE G scalar aliases", code_g_temp_names_no_elim),
            *_c_declaration_group("CODE Ihis scalar aliases", ihis_temp_names_no_elim),
            "",
            "LOCAL_STATIC:",
            *_c_declaration_group("User RAM-only G symbols", no_elim_ram_symbols),
            *_c_declaration_group("RAM G stamp scalar aliases", ram_g_temp_names_no_elim),
            "",
            "RAM_PASS1:",
            *_c_section_warning(
                "RAM-SIDE G MATRIX VALUE SETUP",
                [
                    "No internal nodes are selected, so fixed RAM G stamping uses the original G matrix.",
                    "Assign or compute every G-related value before writing g_mat_over.",
                    "Dynamic entries are registered in GVALUES and refreshed in CODE.",
                ],
            ),
        ]
        for index, node in enumerate(ram_overlay_nodes_no_elim):
            lines.append(f'    g_mat_nods[{index}] = getNodeNum(comp, "{_c_display_node(node, node_display_names)}");')
        if ram_overlay_nodes_no_elim:
            lines.extend([
                "    /* g_mat_over is provided by the PSYS/CBuilder runtime; initialize, do not define it here. */",
                f"    for (int row = 0; row < {len(ram_overlay_nodes_no_elim)}; row++) {{",
                f"        for (int col = 0; col < {len(ram_overlay_nodes_no_elim)}; col++) {{",
                "            g_mat_over[row][col] = 0.0;",
                "        }",
                "    }",
                *ram_g_compute_lines_no_elim,
                *ram_g_assignment_lines_no_elim,
            ])
        lines.extend([
            (
                f"    setupGMatrix({len(ram_overlay_nodes_no_elim)});"
                if ram_overlay_nodes_no_elim
                else "    /* No RAM-side G entries: no fixed G overlay is registered. */"
            ),
            "",
            *_c_register_lines(no_elim_code_names),
        ])
        if dynamic_gred_no_elim:
            lines.extend([
                "",
                "GVALUES:",
                "    /* Dynamic original-G stamp handles, ordered by upper triangle. */",
                *[
                    (
                        f'    double {_var_g_name(row_node, col_node, node_display_names)} = '
                        f'createGValue("{_var_g_name(row_node, col_node, node_display_names)}", '
                        f'"{_c_display_node(row_node, node_display_names)}", '
                        f'"{_c_display_node(col_node, node_display_names)}", 0, "TRUE");'
                    )
                    for _, _, row_node, col_node in var_g_pairs_no_elim
                ],
            ])
        lines.extend([
            "",
            "CODE:",
            "BEGIN_T0:",
        ])
        if dynamic_gred_no_elim:
            lines.extend([
                *_c_section_warning(
                    "CODE-SIDE G MATRIX VALUE SETUP",
                [
                    "No internal-node elimination is required.",
                    "Assign the non-RAM G terms split from the original G matrix directly to GValue handles.",
                    "No CODE matrix staging is needed because there is no Schur or Vk matrix DAG.",
                ],
            ),
                *code_g_compute_lines_no_elim,
                *code_g_assignment_lines_no_elim,
                "",
            ])
        lines.extend([
            *_c_section_warning(
                "CODE-SIDE IHIS VALUE SETUP",
                [
                    "Update runtime Ihis/history-source values before node-current injection.",
                    "No internal nodes are eliminated, so assign original Ihis entries directly to Inj.",
                    "The row order follows the original retained-node order.",
                ],
            ),
            "    /* Node injection currents follow the retained-node order of the original system. */",
            *ihis_compute_lines_no_elim,
            *ihis_assignment_lines_no_elim,
            "",
            "T1_T2:",
            "    /* No internal nodes were eliminated, so there is no Vk recovery step. */",
            "",
        ])
        return lines
    ram_overlay_nodes, ram_overlay_index = _ram_overlay_node_subset(ram_Gred, external_nodes)
    ram_g_assignments = [
        (
            f"g_mat_over[{ram_overlay_index[row]}][{ram_overlay_index[col]}]",
            ram_Gred[row, col],
            None,
            _scalar_g_name("ramG", external_nodes[row], external_nodes[col], node_display_names),
            f"Gred[{_c_display_node(external_nodes[row], node_display_names)},{_c_display_node(external_nodes[col], node_display_names)}]",
        )
        for row in range(ram_Gred.rows)
        for col in range(ram_Gred.cols)
        if sp.simplify(ram_Gred[row, col]) != 0
    ]
    ram_g_temp_names, ram_g_compute_lines, ram_g_assignment_lines = _c_scalar_assignment_cse(
        ram_g_assignments,
        "ramG",
    )
    Grr_alias_entries = _block_alias_entries(Grr, "Grr", external_nodes, node_display_names)
    Grk_alias_entries = _block_alias_entries(Grk, "Grk", external_nodes, node_display_names)
    Gkr_alias_entries = _block_alias_entries(Gkr, "Gkr", external_nodes, node_display_names)
    Gkk_alias_entries = _block_alias_entries(Gkk, "Gkk", external_nodes, node_display_names)
    W_alias_entries = _block_alias_entries(W, "W", external_nodes, node_display_names)
    W_formula_alias_entries = _runtime_w_alias_entries(W.rows or Gkk.rows) if (w_runtime_inverse or structured_w_builder) else W_alias_entries
    need_W_scalar_aliases = bool(
        dynamic_gred
        and not full_gred_code_path
        and not w_runtime_inverse
        and not structured_w_builder
    )
    block_alias_entries = [
        *(Grr_alias_entries if need_Grr_code or rectangular_gred_dyn_path else []),
        *(Grk_alias_entries if need_Grk_code or rectangular_gred_dyn_path or partial_ihisred_code_path else []),
        *(Gkr_alias_entries if need_Gkr_code or rectangular_gred_dyn_path else []),
        *(Gkk_alias_entries if need_Gkk_code else []),
        *(W_alias_entries if (need_W_code or need_W_scalar_aliases) and not w_runtime_inverse and not structured_w_builder else []),
    ]
    ram_precompute_alias_entries = [
        *(Grk_alias_entries if ram_precompute_grk_w else []),
        *(W_alias_entries if ram_precompute_grk_w or ram_precompute_w_gkr else []),
        *(Gkr_alias_entries if ram_precompute_w_gkr and not ram_reuse_w_gkr_from_grk_w else []),
    ]
    ram_precompute_alias_keys = {
        (str(entry["name"]), int(entry["row"]), int(entry["col"]))
        for entry in ram_precompute_alias_entries
    }
    code_block_alias_entries = [
        entry
        for entry in block_alias_entries
        if (str(entry["name"]), int(entry["row"]), int(entry["col"])) not in ram_precompute_alias_keys
    ]
    w_builder_matrix_dims = _diagonal_plus_coupled_w_matrix_dims(details) if structured_w_builder else []
    w_builder_matrix_names = [name for name, _, _ in w_builder_matrix_dims]
    var_g_pair_set = {(row, col) for row, col, _, _ in var_g_pairs}
    gred_var_reuse: dict[tuple[int, int], tuple[tuple[int, int], int]] = {}
    if dynamic_gred:
        try:
            for item in structural_gred_entry_reuse_plan_from_blocks(Grr, Grk, Gkr, Gkk):
                target = (item.target_row, item.target_col)
                base = (item.base_row, item.base_col)
                if target in var_g_pair_set and base in var_g_pair_set:
                    gred_var_reuse[target] = (base, item.sign)
        except Exception:
            gred_var_reuse = {}
    sparse_gred_scalar_assignments = (
        [
            (
                (row, col),
                _var_g_name(row_node, col_node, node_display_names),
                (
                    f"/* Gred[{_c_display_node(row_node, node_display_names)},"
                    f"{_c_display_node(col_node, node_display_names)}] = "
                    f"Grr[{_c_display_node(row_node, node_display_names)},"
                    f"{_c_display_node(col_node, node_display_names)}] - "
                    f"Grk[{_c_display_node(row_node, node_display_names)},k] * W * "
                    f"Gkr[k,{_c_display_node(col_node, node_display_names)}]. */"
                ),
                _scalar_g_name("codeG", row_node, col_node, node_display_names),
                _c_add_expr(
                    _gred_alias_formula(
                        row,
                        col,
                        Grr_alias_entries,
                        Grk_alias_entries,
                        W_formula_alias_entries,
                        Gkr_alias_entries,
                        W.rows or Gkk.rows,
                    ),
                    code_Gred_direct[row, col] if row < code_Gred_direct.rows and col < code_Gred_direct.cols else sp.Integer(0),
                    ),
                f"Gred[{_c_display_node(row_node, node_display_names)},{_c_display_node(col_node, node_display_names)}]",
            )
            for row, col, row_node, col_node in var_g_pairs
        ]
        if dynamic_gred and not full_gred_code_path and not rectangular_gred_dyn_path
        else []
    )
    code_gred_temp_names: list[str] = []
    var_g_name_by_pair = {
        (row, col): _var_g_name(row_node, col_node, node_display_names)
        for row, col, row_node, col_node in var_g_pairs
    }
    code_gred_compute_lines = []
    assigned_sparse_var_g_pairs: set[tuple[int, int]] = set()
    for pair, target, comment, _name, expr, label in sparse_gred_scalar_assignments:
        reuse = gred_var_reuse.get(pair)
        if reuse and reuse[0] in assigned_sparse_var_g_pairs:
            base_name = var_g_name_by_pair[reuse[0]]
            prefix = "-" if reuse[1] < 0 else ""
            code_gred_compute_lines.extend([
                f"    /* {target} reuses {label}: {prefix}{base_name}. */",
                f"    {target} = {prefix}{base_name};",
            ])
        else:
            code_gred_compute_lines.extend([
                f"    /* {target} represents {label}: {expr}. */",
                f"    {comment}",
                f"    {target} = {expr};",
            ])
        assigned_sparse_var_g_pairs.add(pair)
    code_matrix_names = []
    if need_Grr_code:
        code_matrix_names.append("Grr_code")
    if need_Grk_code:
        code_matrix_names.append("Grk_code")
    if need_Gkr_code:
        code_matrix_names.append("Gkr_code")
    if need_Gkk_code:
        code_matrix_names.append("Gkk_code")
    if need_W_code:
        code_matrix_names.append("W_code")
    code_matrix_names.extend(w_builder_matrix_names)
    if need_Ihisr_code:
        code_matrix_names.append("Ihisr_code")
    if need_Ihisk_code:
        code_matrix_names.append("Ihisk_code")
    if need_Vr_code:
        code_matrix_names.append("Vr_code")
    if need_Vk_code:
        code_matrix_names.append("Vk_code")
    if need_tmp_w_gkr_code:
        code_matrix_names.append("tmp_W_Gkr_code")
    if need_tmp_w_gkr_vr_code:
        code_matrix_names.append("tmp_W_Gkr_Vr_code")
    if need_tmp_w_ihisk_code:
        code_matrix_names.append("tmp_W_Ihisk_code")
    if need_tmp_vk_sum_code:
        code_matrix_names.append("tmp_Vk_sum_code")
    if need_gred_code:
        code_matrix_names.insert(4, "Gred_code")
    if rectangular_gred_dyn_path:
        code_matrix_names[5:5] = [
            "Grr_dyn_code",
            "Grk_dyn_code",
            "Gkr_dyn_code",
            "Gred_dyn_code",
            "tmp_Grk_W_dyn_code",
            "tmp_Grk_W_Gkr_dyn_code",
        ]
    if partial_ihisred_code_path:
        code_matrix_names[5:5] = [
            "Ihisr_ihis_dyn_code",
            "Grk_ihis_dyn_code",
            "Ihisred_dyn_code",
            "tmp_Grk_W_ihis_dyn_code",
            "tmp_Grk_W_Ihisk_dyn_code",
        ]
    if need_ihisred_code:
        code_matrix_names.insert(7, "Ihisred_code")
    if need_tmp_grk_w_code:
        code_matrix_names.insert(10, "tmp_Grk_W_code")
    if full_gred_code_path and not diagonal_gkk_scalar_code_path:
        code_matrix_names.insert(11, "tmp_Grk_W_Gkr_code")
    if full_ihisred_code_path and not diagonal_gkk_scalar_code_path:
        code_matrix_names.insert(12, "tmp_Grk_W_Ihisk_code")
    code_runtime_matrix_names = list(code_matrix_names)
    # RAM-only MATRIX_ objects need matrixDim plus RAM helpers, but not register/condition.
    # Keep precomputed result matrices registered when CODE later reads them.
    if ram_precompute_grk_w and "Grk_code" in code_runtime_matrix_names:
        code_runtime_matrix_names.remove("Grk_code")
    if ram_precompute_w_gkr and "Gkr_code" in code_runtime_matrix_names:
        code_runtime_matrix_names.remove("Gkr_code")
    if (
        (ram_precompute_grk_w or ram_precompute_w_gkr)
        and not need_tmp_w_ihisk_code
        and "W_code" in code_runtime_matrix_names
    ):
        code_runtime_matrix_names.remove("W_code")
    lines = [
        "/* RTDS lifecycle placement generated from final-expression dependency analysis.",
        "   RAM_PASS1 stamps only RAM_CONSTANT Gred entries through g_mat_over.",
        "   CODE/BEGIN_T0 computes only dynamic reduced-G and non-direct Ihisred paths.",
        "   T1_T2 reads solved node voltages and recovers eliminated-node voltages when needed. */",
        "STATIC:",
        "    /* Runtime matrix objects */",
        *(["    MATRIX_ Grr_code = {0};"] if need_Grr_code else []),
        *(["    MATRIX_ Grk_code = {0};"] if need_Grk_code else []),
        *(["    MATRIX_ Gkr_code = {0};"] if need_Gkr_code else []),
        *(["    MATRIX_ Gkk_code = {0};"] if need_Gkk_code else []),
        *(["    MATRIX_ W_code = {0};"] if need_W_code else []),
        *[f"    MATRIX_ {name} = {{0}};" for name in w_builder_matrix_names],
        *(["    MATRIX_ Gred_code = {0};"] if need_gred_code else []),
        *(["    MATRIX_ Grr_dyn_code = {0};"] if rectangular_gred_dyn_path else []),
        *(["    MATRIX_ Grk_dyn_code = {0};"] if rectangular_gred_dyn_path else []),
        *(["    MATRIX_ Gkr_dyn_code = {0};"] if rectangular_gred_dyn_path else []),
        *(["    MATRIX_ Gred_dyn_code = {0};"] if rectangular_gred_dyn_path else []),
        *(["    MATRIX_ tmp_Grk_W_dyn_code = {0};"] if rectangular_gred_dyn_path else []),
        *(["    MATRIX_ tmp_Grk_W_Gkr_dyn_code = {0};"] if rectangular_gred_dyn_path else []),
        *(["    MATRIX_ Ihisr_code = {0};"] if need_Ihisr_code else []),
        *(["    MATRIX_ Ihisk_code = {0};"] if need_Ihisk_code else []),
        *(["    MATRIX_ Ihisred_code = {0};"] if need_ihisred_code else []),
        *(["    MATRIX_ Ihisr_ihis_dyn_code = {0};"] if partial_ihisred_code_path else []),
        *(["    MATRIX_ Grk_ihis_dyn_code = {0};"] if partial_ihisred_code_path else []),
        *(["    MATRIX_ Ihisred_dyn_code = {0};"] if partial_ihisred_code_path else []),
        *(["    MATRIX_ tmp_Grk_W_ihis_dyn_code = {0};"] if partial_ihisred_code_path else []),
        *(["    MATRIX_ tmp_Grk_W_Ihisk_dyn_code = {0};"] if partial_ihisred_code_path else []),
        *(["    MATRIX_ Vr_code = {0};"] if need_Vr_code else []),
        *(["    MATRIX_ Vk_code = {0};"] if need_Vk_code else []),
        *(["    MATRIX_ tmp_Grk_W_code = {0};"] if need_tmp_grk_w_code else []),
        *(["    MATRIX_ tmp_Grk_W_Gkr_code = {0};"] if full_gred_code_path and not diagonal_gkk_scalar_code_path else []),
        *(["    MATRIX_ tmp_Grk_W_Ihisk_code = {0};"] if full_ihisred_code_path and not diagonal_gkk_scalar_code_path else []),
        *(["    MATRIX_ tmp_W_Gkr_code = {0};"] if need_tmp_w_gkr_code else []),
        *(["    MATRIX_ tmp_W_Gkr_Vr_code = {0};"] if need_tmp_w_gkr_vr_code else []),
        *(["    MATRIX_ tmp_W_Ihisk_code = {0};"] if need_tmp_w_ihisk_code else []),
        *(["    MATRIX_ tmp_Vk_sum_code = {0};"] if need_tmp_vk_sum_code else []),
        "    /* Runtime state */",
        "    int rtds_matrix_code_ready = 0;",
        *_c_declaration_group("User G/CODE symbols", code_g_symbol_names),
        *_c_declaration_group("User Ihis/history symbols", code_ihis_symbol_names),
        *_c_declaration_group("Block-matrix scalar aliases", [str(entry["name"]) for entry in block_alias_entries]),
        *_c_declaration_group("Dynamic G stamp scalar aliases", code_gred_temp_names),
        "",
        "LOCAL_STATIC:",
        *_c_declaration_group("User RAM-only G symbols", ram_symbol_names),
        *_c_declaration_group("RAM G stamp scalar aliases", ram_g_temp_names),
        "",
        "RAM_PASS1:",
        "    int err = 0;",
        *_c_section_warning(
            "RAM-SIDE G MATRIX VALUE SETUP",
            [
                "Assign or compute every G-related value before any RAM-side use.",
                "This section stamps only fixed g_mat_over entries; CODE-owned matrices are refreshed in CODE.",
            ],
        ),
    ]
    for index, node in enumerate(ram_overlay_nodes):
        lines.append(f'    g_mat_nods[{index}] = getNodeNum(comp, "{_c_display_node(node, node_display_names)}");')
    if ram_overlay_nodes:
        lines.extend([
            "    /* g_mat_over is provided by the PSYS/CBuilder runtime; initialize, do not define it here. */",
            f"    for (int row = 0; row < {len(ram_overlay_nodes)}; row++) {{",
            f"        for (int col = 0; col < {len(ram_overlay_nodes)}; col++) {{",
            "            g_mat_over[row][col] = 0.0;",
            "        }",
            "    }",
            *ram_g_compute_lines,
            *ram_g_assignment_lines,
        ])
    if ram_overlay_nodes:
        lines.append(f"    setupGMatrix({len(ram_overlay_nodes)});")
    else:
        lines.append("    /* No RAM-side G entries: no fixed G overlay is registered. */")
    lines.extend([
        "",
        *(["    err += matrixDim(&Grr_code, NR, NR);"] if need_Grr_code else []),
        *(["    err += matrixDim(&Grk_code, NR, NK);"] if need_Grk_code else []),
        *(["    err += matrixDim(&Gkr_code, NK, NR);"] if need_Gkr_code else []),
        *(["    err += matrixDim(&Gkk_code, NK, NK);"] if need_Gkk_code else []),
        *(["    err += matrixDim(&W_code, NK, NK);"] if need_W_code else []),
        *[f"    err += matrixDim(&{name}, {rows}, {cols});" for name, rows, cols in w_builder_matrix_dims],
        *(["    err += matrixDim(&Gred_code, NR, NR);"] if need_gred_code else []),
        *( [f"    err += matrixDim(&Grr_dyn_code, {len(gred_dyn_rows)}, {len(gred_dyn_cols)});"] if rectangular_gred_dyn_path else [] ),
        *( [f"    err += matrixDim(&Grk_dyn_code, {len(gred_dyn_rows)}, NK);"] if rectangular_gred_dyn_path else [] ),
        *( [f"    err += matrixDim(&Gkr_dyn_code, NK, {len(gred_dyn_cols)});"] if rectangular_gred_dyn_path else [] ),
        *( [f"    err += matrixDim(&Gred_dyn_code, {len(gred_dyn_rows)}, {len(gred_dyn_cols)});"] if rectangular_gred_dyn_path else [] ),
        *( [f"    err += matrixDim(&tmp_Grk_W_dyn_code, {len(gred_dyn_rows)}, NK);"] if rectangular_gred_dyn_path else [] ),
        *( [f"    err += matrixDim(&tmp_Grk_W_Gkr_dyn_code, {len(gred_dyn_rows)}, {len(gred_dyn_cols)});"] if rectangular_gred_dyn_path else [] ),
        *(["    err += matrixDim(&Ihisr_code, NR, 1);"] if need_Ihisr_code else []),
        *(["    err += matrixDim(&Ihisk_code, NK, 1);"] if need_Ihisk_code else []),
        *(["    err += matrixDim(&Ihisred_code, NR, 1);"] if need_ihisred_code else []),
        *( [f"    err += matrixDim(&Ihisr_ihis_dyn_code, {len(ihisred_dyn_rows)}, 1);"] if partial_ihisred_code_path else [] ),
        *( [f"    err += matrixDim(&Grk_ihis_dyn_code, {len(ihisred_dyn_rows)}, NK);"] if partial_ihisred_code_path else [] ),
        *( [f"    err += matrixDim(&Ihisred_dyn_code, {len(ihisred_dyn_rows)}, 1);"] if partial_ihisred_code_path else [] ),
        *( [f"    err += matrixDim(&tmp_Grk_W_ihis_dyn_code, {len(ihisred_dyn_rows)}, NK);"] if partial_ihisred_code_path else [] ),
        *( [f"    err += matrixDim(&tmp_Grk_W_Ihisk_dyn_code, {len(ihisred_dyn_rows)}, 1);"] if partial_ihisred_code_path else [] ),
        *(["    err += matrixDim(&Vr_code, NR, 1);"] if need_Vr_code else []),
        *(["    err += matrixDim(&Vk_code, NK, 1);"] if need_Vk_code else []),
        *(["    err += matrixDim(&tmp_Grk_W_code, NR, NK);"] if need_tmp_grk_w_code else []),
        *(["    err += matrixDim(&tmp_Grk_W_Gkr_code, NR, NR);"] if full_gred_code_path and not diagonal_gkk_scalar_code_path else []),
        *(["    err += matrixDim(&tmp_Grk_W_Ihisk_code, NR, 1);"] if full_ihisred_code_path and not diagonal_gkk_scalar_code_path else []),
        *(["    err += matrixDim(&tmp_W_Gkr_code, NK, NR);"] if need_tmp_w_gkr_code else []),
        *(["    err += matrixDim(&tmp_W_Gkr_Vr_code, NK, 1);"] if need_tmp_w_gkr_vr_code else []),
        *(["    err += matrixDim(&tmp_W_Ihisk_code, NK, 1);"] if need_tmp_w_ihisk_code else []),
        *(["    err += matrixDim(&tmp_Vk_sum_code, NK, 1);"] if need_tmp_vk_sum_code else []),
        "    if (err > 0) {",
        '        reportError_RW("network_node", STOP_IMMEDIATELY_CONDITION,',
        '                       "RTDS matrix allocation failed for component %s.", Name);',
        "    }",
        *(
            [
                *_c_section_warning(
                    "RAM-SIDE STATIC MATRIX PRECOMPUTE",
                    [
                        "These Schur matrices depend only on RAM constants.",
                        "Precompute reusable products in RAM; CODE only multiplies them by runtime vectors.",
                    ],
                ),
                *_block_alias_compute_lines(ram_precompute_alias_entries),
                *(_matrix_set_alias_lines(Grk_alias_entries, "Grk_code", "set") if ram_precompute_grk_w else []),
                *(_matrix_set_alias_lines(W_alias_entries, "W_code", "set") if ram_precompute_grk_w or ram_precompute_w_gkr else []),
                *(
                    _matrix_set_alias_lines(Gkr_alias_entries, "Gkr_code", "set")
                    if ram_precompute_w_gkr and not ram_reuse_w_gkr_from_grk_w
                    else []
                ),
                *(["    matrix_mult(&tmp_Grk_W_code, &Grk_code, &W_code);"] if ram_precompute_grk_w else []),
                *(
                    _matrix_transpose_copy_lines(
                        "tmp_W_Gkr_code",
                        "tmp_Grk_W_code",
                        "NK",
                        "NR",
                        setter="set",
                        getter="get",
                        comment="Symmetry reuse: W * Gkr = transpose(Grk * W).",
                    )
                    if ram_reuse_w_gkr_from_grk_w
                    else (["    matrix_mult(&tmp_W_Gkr_code, &W_code, &Gkr_code);"] if ram_precompute_w_gkr else [])
                ),
                "",
            ]
            if ram_precompute_grk_w or ram_precompute_w_gkr
            else []
        ),
        *_c_register_lines(code_runtime_matrix_names),
        "",
    ])
    if dynamic_gred:
        lines.extend([
            "GVALUES:",
            "    /* Dynamic reduced-G stamp handles, ordered by Gred upper triangle. */",
            *[
                (
                    f'    double {_var_g_name(row_node, col_node, node_display_names)} = '
                    f'createGValue("{_var_g_name(row_node, col_node, node_display_names)}", '
                    f'"{_c_display_node(row_node, node_display_names)}", '
                    f'"{_c_display_node(col_node, node_display_names)}", 0, "TRUE");'
                )
                for _, _, row_node, col_node in var_g_pairs
            ],
            "",
        ])
    lines.extend([
        "CODE:",
        "BEGIN_T0:",
        "    if (!rtds_matrix_code_ready) {",
        "        initializeMatricesForCode();",
        *_c_condition_lines(code_runtime_matrix_names),
        "        rtds_matrix_code_ready = 1;",
        "    }",
        "",
        "",
        *(
            [
                *_c_section_warning(
                    "CODE-SIDE G MATRIX VALUE SETUP",
                    [
                        "Update runtime G-related symbols and matrices before the reduction math below.",
                        "Only matrices required by dynamic G, Ihis reduction, or Vk recovery are refreshed.",
                    ],
                ),
                "    /* Runtime refresh. Use set_CODE for matrices touched in CODE; do not write MATRIX_.p directly. */",
                *_block_alias_compute_lines(code_block_alias_entries),
                *(_matrix_set_alias_lines(Grr_alias_entries, "Grr_code", "set_CODE") if need_Grr_code else []),
                *(_matrix_set_alias_lines(Grk_alias_entries, "Grk_code", "set_CODE") if need_Grk_code and not ram_precompute_grk_w else []),
                *(_matrix_set_alias_lines(Gkr_alias_entries, "Gkr_code", "set_CODE") if need_Gkr_code and not ram_precompute_w_gkr else []),
                *(_matrix_set_alias_lines(Gkk_alias_entries, "Gkk_code", "set_CODE") if need_Gkk_code else []),
                *(
                    _matrix_code_sym_inverse_lines("Gkk_code", "W_code", Gkk.rows)
                    if use_fast_symmetric_gkk_inverse
                    else (["    MATH_matx_invert(NK, &(Gkk_code.p[0]), NK, &(W_code.p[0]), NK);"] if need_Gkk_code and need_W_code else [])
                ),
                *(_diagonal_plus_coupled_w_code_lines(details) if structured_w_builder else []),
                *(
                    _matrix_set_alias_lines(W_alias_entries, "W_code", "set_CODE")
                    if need_W_code and not w_runtime_inverse and not structured_w_builder and not (ram_precompute_grk_w or ram_precompute_w_gkr)
                    else []
                ),
                *(_matrix_set_alias_lines(Grr_alias_entries, "Grr_dyn_code", "set_CODE", row_map=gred_dyn_rows, col_map=gred_dyn_cols) if rectangular_gred_dyn_path else []),
                *(_matrix_set_alias_lines(Grk_alias_entries, "Grk_dyn_code", "set_CODE", row_map=gred_dyn_rows, col_map=list(range(Grk.cols))) if rectangular_gred_dyn_path else []),
                *(_matrix_set_alias_lines(Gkr_alias_entries, "Gkr_dyn_code", "set_CODE", row_map=list(range(Gkr.rows)), col_map=gred_dyn_cols) if rectangular_gred_dyn_path else []),
                "",
            ]
            if code_block_alias_entries
            else []
        ),
        *(
            [
                *_c_section_warning(
                    "CODE-SIDE IHIS VALUE SETUP",
                    [
                        "Update runtime Ihis/history-source values before per-step injection math.",
                        "Only non-direct Ihisred rows are computed through MATRIX_ CODE helpers.",
                    ],
                ),
                *(_c_vector_set_lines(Ihisr, "Ihisr_code", "set_CODE") if need_Ihisr_code else []),
                *(_c_vector_set_lines(Ihisk, "Ihisk_code", "set_CODE") if need_Ihisk_code else []),
                *(_c_matrix_set_lines(_slice_matrix(Ihisr, ihisred_dyn_rows, [0]), "Ihisr_ihis_dyn_code", "set_CODE") if partial_ihisred_code_path else []),
                *(_matrix_set_alias_lines(Grk_alias_entries, "Grk_ihis_dyn_code", "set_CODE", row_map=ihisred_dyn_rows, col_map=list(range(Grk.cols))) if partial_ihisred_code_path else []),
                "",
            ]
            if need_Ihisr_code or need_Ihisk_code or partial_ihisred_code_path
            else []
        ),
        *(["    matrix_mult_CODE(&tmp_Grk_W_code, &Grk_code, &W_code);"] if need_tmp_grk_w_code and not ram_precompute_grk_w else []),
    ])
    if dynamic_gred and full_gred_code_path:
        if diagonal_gkk_scalar_code_path:
            lines.extend(
                [
                    *_diagonal_gkk_scalar_gred_code_lines(False),
                    *(_c_matrix_add_nonzero_lines(code_Gred_direct, "Gred_code", upper_triangle_only=True) if _matrix_has_nonzero(code_Gred_direct) else []),
                    "    /* Stamp dynamic Gred entries in row-major upper-triangular order. */",
                ]
            )
        else:
            lines.extend([
                "    /* Full Gred CODE path: all reduced entries are CODE-owned, so a full Schur update is allowed. */",
                *_upper_tri_matrix_product_code_lines("tmp_Grk_W_Gkr_code", "tmp_Grk_W_code", "Gkr_code"),
                "    /* Gred is symmetric; only the upper triangle is needed for dynamic GValue stamps. */",
                *_upper_tri_matrix_subtract_code_lines("Gred_code", "Grr_code", "tmp_Grk_W_Gkr_code"),
                *(_c_matrix_add_nonzero_lines(code_Gred_direct, "Gred_code", upper_triangle_only=True) if _matrix_has_nonzero(code_Gred_direct) else []),
                "    /* Stamp dynamic Gred entries in row-major upper-triangular order. */",
            ])
        assigned_var_g_pairs: set[tuple[int, int]] = set()
        for row, col, row_node, col_node in var_g_pairs:
            target = (row, col)
            target_name = _var_g_name(row_node, col_node, node_display_names)
            reuse = gred_var_reuse.get(target)
            if reuse and reuse[0] in assigned_var_g_pairs:
                base_row, base_col = reuse[0]
                base_name = _var_g_name(external_nodes[base_row], external_nodes[base_col], node_display_names)
                prefix = "-" if reuse[1] < 0 else ""
                lines.append(f"    {target_name} = {prefix}{base_name};")
            else:
                lines.append(f"    {target_name} = get_CODE(&Gred_code, {row}, {col});")
            assigned_var_g_pairs.add(target)
        lines.append("")
    elif dynamic_gred:
        if rectangular_gred_dyn_path:
            row_names = ", ".join(_c_display_node(external_nodes[row], node_display_names) for row in gred_dyn_rows)
            col_names = ", ".join(_c_display_node(external_nodes[col], node_display_names) for col in gred_dyn_cols)
            dynamic_comment = [
                "    /* Sliced Schur update for dynamic rectangular Gred block.",
                f"       Rows: [{row_names}]",
                f"       Cols: [{col_names}]",
                "       Gred[B,C] = Grr[B,C] - Grk[B,k] * W * Gkr[k,C]. */",
            ]
        else:
            dynamic_comment = [
                "    /* Sliced Schur sparse updates for dynamic Gred entries.",
                "       Each assignment uses Gred[i,j] = Grr[i,j] - Grk[i,k] * W * Gkr[k,j]. */",
            ]
        lines.extend([
            *dynamic_comment,
            *(
                [
                    "    matrix_mult_CODE(&tmp_Grk_W_dyn_code, &Grk_dyn_code, &W_code);",
                    "    matrix_mult_CODE(&tmp_Grk_W_Gkr_dyn_code, &tmp_Grk_W_dyn_code, &Gkr_dyn_code);",
                    "    matrix_subtract_CODE(&Gred_dyn_code, &Grr_dyn_code, &tmp_Grk_W_Gkr_dyn_code);",
                    *_c_matrix_add_nonzero_lines(
                        _slice_matrix(code_Gred_direct, gred_dyn_rows, gred_dyn_cols),
                        "Gred_dyn_code",
                    ),
                ]
                if rectangular_gred_dyn_path
                else []
            ),
            "    /* Stamp dynamic Gred entries directly from the sliced dynamic block. */",
            *(
                [
                    (
                        f"    /* Gred[{_c_display_node(row_node, node_display_names)},"
                        f"{_c_display_node(col_node, node_display_names)}] = "
                        f"Grr[{_c_display_node(row_node, node_display_names)},"
                        f"{_c_display_node(col_node, node_display_names)}] - "
                        f"Grk[{_c_display_node(row_node, node_display_names)},k] * W * "
                        f"Gkr[k,{_c_display_node(col_node, node_display_names)}]. */\n"
                        f"    {_var_g_name(row_node, col_node, node_display_names)} = "
                        f"get_CODE(&Gred_dyn_code, {gred_dyn_rows.index(row)}, {gred_dyn_cols.index(col)});"
                    )
                    for row, col, row_node, col_node in var_g_pairs
                ]
                if rectangular_gred_dyn_path
                else code_gred_compute_lines
            ),
            "",
        ])
    if partial_ihisred_code_path:
        lines.extend([
            "    /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
            "    /* Sliced Ihisred updates for CODE-owned retained rows. */",
            "    matrix_mult_CODE(&tmp_Grk_W_ihis_dyn_code, &Grk_ihis_dyn_code, &W_code);",
            "    matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_dyn_code, &tmp_Grk_W_ihis_dyn_code, &Ihisk_code);",
            "    matrix_subtract_CODE(&Ihisred_dyn_code, &Ihisr_ihis_dyn_code, &tmp_Grk_W_Ihisk_dyn_code);",
            *_c_vector_add_nonzero_lines(_slice_matrix(Ihisred_direct, ihisred_dyn_rows, [0]), "Ihisred_dyn_code"),
            *[
                (
                    f"    /* Ihisred[{_c_display_node(external_nodes[index], node_display_names)}] = "
                    f"Ihisr[{_c_display_node(external_nodes[index], node_display_names)}] - "
                    f"Grk[{_c_display_node(external_nodes[index], node_display_names)},k] * W * Ihisk. */\n"
                    f"    /* Inj{_c_node_variable_name(external_nodes[index], node_display_names)} reads Ihisred_dyn_code[{ihisred_dyn_rows.index(index)}][0] below. */"
                )
                for index in ihisred_dyn_rows
            ],
        ])
    elif full_ihisred_code_path:
        if diagonal_gkk_scalar_code_path:
            lines.extend(
                [
                    "    /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
                    *_diagonal_gkk_scalar_ihisred_code_lines(False),
                    *_c_vector_add_nonzero_lines(Ihisred_direct, "Ihisred_code"),
                ]
            )
        else:
            lines.extend([
                "    /* Ihisred is a per-step injection vector: Ihisred = Ihisr - Grk * W * Ihisk. */",
                "    matrix_matXvec_CODE(&tmp_Grk_W_Ihisk_code, &tmp_Grk_W_code, &Ihisk_code);",
                "    matrix_subtract_CODE(&Ihisred_code, &Ihisr_code, &tmp_Grk_W_Ihisk_code);",
                *_c_vector_add_nonzero_lines(Ihisred_direct, "Ihisred_code"),
            ])
    lines.extend([
        "    /* Node injection currents follow the retained-node order of the reduced system. */",
        *[
            (
                (
                    f"    Inj{_c_node_variable_name(node, node_display_names)} = "
                    f"get_CODE(&Ihisred_dyn_code, {ihisred_dyn_rows.index(index)}, 0);"
                    if index in ihisred_dyn_rows
                    else f"    Inj{_c_node_variable_name(node, node_display_names)} = {_ccode(Ihisred[index, 0])};"
                )
                if partial_ihisred_code_path
                else (
                    f"    Inj{_c_node_variable_name(node, node_display_names)} = get_CODE(&Ihisred_code, {index}, 0);"
                    if full_ihisred_code_path
                    else f"    Inj{_c_node_variable_name(node, node_display_names)} = {_ccode(Ihisred[index, 0])};"
                )
            )
            for index, node in enumerate(external_nodes)
        ],
        "",
    ])
    if need_vk_recovery:
        lines.extend([
            "T1_T2:",
            "    /* Internal-node voltage recovery after solved retained-node voltages are available. */",
            *(
                [
                    f"    set_CODE(&Vr_code, {index}, 0, {_c_symbol_name(_c_display_node(node, node_display_names))});"
                    for index, node in enumerate(external_nodes)
                ]
                if need_vk_vr_path
                else []
            ),
            *(
                _diagonal_gkk_scalar_vk_code_lines(need_vk_vr_path, need_vk_ihis_path)
                if diagonal_gkk_scalar_code_path
                else [
                    *(
                        _matrix_transpose_copy_lines(
                            "tmp_W_Gkr_code",
                            "tmp_Grk_W_code",
                            "NK",
                            "NR",
                            comment="Symmetry reuse: W * Gkr = transpose(Grk * W).",
                        )
                        if need_vk_vr_path and not ram_precompute_w_gkr and reuse_w_gkr_from_grk_w
                        else (["    matrix_mult_CODE(&tmp_W_Gkr_code, &W_code, &Gkr_code);"] if need_vk_vr_path and not ram_precompute_w_gkr else [])
                    ),
                    *(["    matrix_matXvec_CODE(&tmp_W_Gkr_Vr_code, &tmp_W_Gkr_code, &Vr_code);"] if need_vk_vr_path else []),
                    *(["    matrix_matXvec_CODE(&tmp_W_Ihisk_code, &W_code, &Ihisk_code);"] if need_vk_ihis_path else []),
                    *(["    matrix_add_CODE(&tmp_Vk_sum_code, &tmp_W_Gkr_Vr_code, &tmp_W_Ihisk_code);"] if need_tmp_vk_sum_code else []),
                    *(
                        ["    matrix_scalarMult_CODE(&Vk_code, &tmp_Vk_sum_code, -1.0);"]
                        if need_tmp_vk_sum_code
                        else (
                            ["    matrix_scalarMult_CODE(&Vk_code, &tmp_W_Gkr_Vr_code, -1.0);"]
                            if need_vk_vr_path
                            else (
                                ["    matrix_scalarMult_CODE(&Vk_code, &tmp_W_Ihisk_code, -1.0);"]
                                if need_vk_ihis_path
                                else [f"    set_CODE(&Vk_code, {index}, 0, 0.0);" for index in range(len(internal_nodes))]
                            )
                        )
                    ),
                ]
            ),
            "",
            "    /* One variable per eliminated node, in effective k order. */",
            *[
                f"    {_c_node_variable_name(node, node_display_names)} = get_CODE(&Vk_code, {index}, 0);"
                for index, node in enumerate(internal_nodes)
            ],
            "",
        ])
    warnings = analysis.get("warnings") or []
    lines.extend([*[f"/* WARNING: {warning} */" for warning in warnings], ""])
    return lines


def _c_emit_rtds_reduction_tail(
    nr: int,
    nk: int,
    external_nodes: Sequence[str],
    internal_nodes: Sequence[str],
    node_display_names: dict[str, str] | None = None,
) -> list[str]:
    return [
        "",
        "/* Final Schur complement:",
        "   Gred    = Grr - Grk * W * Gkr",
        "   Ihisred = Ihisr - Grk * W * Ihisk",
        "   Vk      = -W * Gkr * Vr - W * Ihisk */",
        _c_zero_matrix("tmp_Grk_W", nr, nk),
        _c_zero_matrix("tmp_Grk_W_Gkr", nr, nr),
        _c_zero_matrix("tmp_Grk_W_Ihisk", nr, 1),
        _c_zero_matrix("Gred", nr, nr),
        _c_zero_matrix("Ihisred", nr, 1),
        "matrix_Mul(NR, NK, NK, tmp_Grk_W, Grk, W);",
        "matrix_Mul(NR, NK, NR, tmp_Grk_W_Gkr, tmp_Grk_W, Gkr);",
        "matrix_Sub(NR, NR, Gred, Grr, tmp_Grk_W_Gkr);",
        "matrix_Mul(NR, NK, 1, tmp_Grk_W_Ihisk, tmp_Grk_W, Ihisk);",
        "matrix_Sub(NR, 1, Ihisred, Ihisr, tmp_Grk_W_Ihisk);",
        "",
        "/* Internal-node voltage recovery. Vr/Vk start as symbolic node-name placeholders. */",
        _c_node_symbol_vector("Vr", external_nodes, node_display_names),
        _c_zero_matrix("tmp_W_Gkr", nk, nr),
        _c_zero_matrix("tmp_W_Gkr_Vr", nk, 1),
        _c_zero_matrix("tmp_W_Ihisk", nk, 1),
        _c_zero_matrix("tmp_Vk_sum", nk, 1),
        _c_node_symbol_vector("Vk", internal_nodes, node_display_names),
        "matrix_Mul(NK, NK, NR, tmp_W_Gkr, W, Gkr);",
        "matrix_Mul(NK, NR, 1, tmp_W_Gkr_Vr, tmp_W_Gkr, Vr);",
        "matrix_Mul(NK, NK, 1, tmp_W_Ihisk, W, Ihisk);",
        "matrix_Add(NK, 1, tmp_Vk_sum, tmp_W_Gkr_Vr, tmp_W_Ihisk);",
        "matrix_Scale(NK, 1, -1.0, Vk, tmp_Vk_sum);",
        "",
        "/* One variable per eliminated node, in effective k order. */",
        *[
            f"double {_c_voltage_variable_name(node, node_display_names)} = Vk[{index}][0];"
            for index, node in enumerate(internal_nodes)
        ],
    ]


_SOURCE_TEMP_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?:(?P<decl>double)\s+)?(?P<name>source(?:G|Ihis)_tmp\d+)\s*=\s*(?P<rhs>[^;\n]+);$"
)


def _source_temp_rhs_is_liftable(rhs: str) -> bool:
    return not re.search(r"\bsource(?:G|Ihis|GI)_tmp\d+\b", rhs)


def _lift_repeated_ram_code_source_temps(draft: str) -> str:
    """Promote exact RAM/CODE repeated source temps to STATIC storage.

    The optimized-elimination C emitter may discover the same source-level
    subexpression independently in RAM G setup and CODE Ihis setup.  If the
    generated C text is exactly the same, hoist one shared variable so the CODE
    stage can reuse the value computed during RAM initialization.
    """
    if "STATIC:" not in draft or "CODE:" not in draft:
        return draft
    code_pos = draft.find("\nCODE:")
    if code_pos < 0:
        return draft

    line_infos: list[dict] = []
    lines_for_scan = draft.splitlines()
    line_offsets: list[int] = []
    cursor = 0
    for line in lines_for_scan:
        line_offsets.append(cursor)
        cursor += len(line) + 1
    for line_no, line in enumerate(lines_for_scan):
        match = _SOURCE_TEMP_ASSIGN_RE.match(line)
        if not match:
            continue
        rhs = match.group("rhs").strip()
        if not _source_temp_rhs_is_liftable(rhs):
            continue
        section = "RAM" if line_offsets[line_no] < code_pos else "CODE"
        line_infos.append(
            {
                "line_no": line_no,
                "name": match.group("name"),
                "rhs": rhs,
                "section": section,
            }
        )

    by_rhs: dict[str, list[dict]] = {}
    for info in line_infos:
        by_rhs.setdefault(info["rhs"], []).append(info)

    shared_rhs = {
        rhs: infos
        for rhs, infos in by_rhs.items()
        if any(info["section"] == "RAM" for info in infos) and any(info["section"] == "CODE" for info in infos)
    }
    if not shared_rhs:
        return draft

    rhs_to_shared: dict[str, str] = {}
    name_to_shared: dict[str, str] = {}
    keep_line_for_rhs: dict[str, int] = {}
    used_names = set(re.findall(r"\bsourceGI_tmp\d+\b", draft))
    next_index = 0
    for rhs, infos in sorted(shared_rhs.items(), key=lambda item: min(info["line_no"] for info in item[1])):
        while f"sourceGI_tmp{next_index}" in used_names:
            next_index += 1
        shared_name = f"sourceGI_tmp{next_index}"
        used_names.add(shared_name)
        rhs_to_shared[rhs] = shared_name
        ram_lines = [info["line_no"] for info in infos if info["section"] == "RAM"]
        keep_line_for_rhs[rhs] = min(ram_lines)
        for info in infos:
            name_to_shared[info["name"]] = shared_name

    lines = draft.splitlines()
    output: list[str] = []
    declaration_inserted = False
    for line_no, line in enumerate(lines):
        if line == "STATIC:" and not declaration_inserted:
            output.append(line)
            for rhs, shared_name in rhs_to_shared.items():
                output.append(f"    double {shared_name} = 0.0;")
            declaration_inserted = True
            continue

        match = _SOURCE_TEMP_ASSIGN_RE.match(line)
        if match and match.group("name") in name_to_shared:
            rhs = match.group("rhs").strip()
            shared_name = name_to_shared[match.group("name")]
            if line_no == keep_line_for_rhs.get(rhs):
                output.append(f"{match.group('indent')}{shared_name} = {rhs};")
            continue

        updated = line
        for old_name, shared_name in name_to_shared.items():
            updated = re.sub(rf"\b{re.escape(old_name)}\b", shared_name, updated)
        output.append(updated)

    return "\n".join(output)


def _dedupe_same_code_source_temps(draft: str) -> str:
    """Reuse exact sourceG/sourceIhis temps inside a plain CODE block.

    Source-level G and Ihis CSE are intentionally built independently.  When G
    is CODE-owned, both CSE lists live in CODE, so the RAM/CODE hoist above does
    not see duplicates such as the same denominator emitted once as sourceG_tmp0
    and once as sourceIhis_tmp0.  This pass only merges exact text matches in a
    switch-free CODE block; it performs no algebraic equivalence checking and
    never crosses case scopes.
    """
    code_pos = draft.find("\nCODE:")
    if code_pos < 0:
        return draft
    t1_pos = draft.find("\nT1_T2:", code_pos)
    code_end = t1_pos if t1_pos >= 0 else len(draft)
    code_body = draft[code_pos:code_end]
    if re.search(r"\bswitch\s*\(", code_body):
        return draft

    lines = draft.splitlines()
    line_offsets: list[int] = []
    cursor = 0
    for line in lines:
        line_offsets.append(cursor)
        cursor += len(line) + 1

    canonical_by_rhs: dict[str, dict] = {}
    replacements: dict[str, str] = {}
    remove_lines: set[int] = set()
    for line_no, line in enumerate(lines):
        offset = line_offsets[line_no]
        if offset < code_pos or offset >= code_end:
            continue
        match = _SOURCE_TEMP_ASSIGN_RE.match(line)
        if not match:
            continue
        rhs = match.group("rhs").strip()
        if not _source_temp_rhs_is_liftable(rhs):
            continue
        name = match.group("name")
        canonical = canonical_by_rhs.get(rhs)
        if canonical is None:
            canonical_by_rhs[rhs] = {"name": name, "line_no": line_no}
            continue
        replacements[name] = canonical["name"]
        remove_lines.add(line_no)

    if not replacements:
        return draft

    output: list[str] = []
    for line_no, line in enumerate(lines):
        declaration = _SOURCE_TEMP_ASSIGN_RE.match(line)
        if declaration and declaration.group("name") in replacements:
            continue
        if line_no in remove_lines:
            continue
        updated = line
        offset = line_offsets[line_no]
        if code_pos <= offset < code_end:
            for old_name, shared_name in replacements.items():
                updated = re.sub(rf"\b{re.escape(old_name)}\b", shared_name, updated)
        output.append(updated)
    return "\n".join(output)


def _join_c_draft_lines(lines: Sequence[str]) -> str:
    draft = "\n".join(lines)
    draft = _lift_repeated_ram_code_source_temps(draft)
    draft = _dedupe_same_code_source_temps(draft)
    draft = _use_readable_dimension_names(draft)
    draft = _ensure_static_blank_line(draft)
    include_lines: list[str] = []
    if "MATRIX_" in draft and "#include <matrixLIB.h>" not in draft:
        include_lines.append("#include <matrixLIB.h>")
    if (
        ("mat_2x2_sym_inv_code" in draft or "mat_3x3_sym_inv_code" in draft)
        and "#include <builtin_MATH.h>" not in draft
    ):
        include_lines.append("#include <builtin_MATH.h>")
    if include_lines:
        return "\n".join(include_lines) + "\n" + draft
    return draft


def _use_readable_dimension_names(draft: str) -> str:
    enum_pattern = re.compile(r"enum \{ NR = (?P<nr>\d+), NK = (?P<nk>\d+) \};")
    match = enum_pattern.search(draft)
    if match:
        body_without_enum = draft[: match.start()] + draft[match.end() :]
        has_dimension_usage = re.search(r"\b(?:NR|NK)\b", body_without_enum) is not None
        if not has_dimension_usage:
            draft = body_without_enum.lstrip("\n")
        else:
            replacement = (
                f"enum {{ RETAINED_NODES = {match.group('nr')}, INTERNAL_NODES = {match.group('nk')} }};\n"
                "/* Dimension names:\n"
                " * RETAINED_NODES is the number of external nodes kept in the reduced network.\n"
                " * INTERNAL_NODES is the number of eliminated internal nodes used by Schur/Vk recovery.\n"
                " */"
            )
            draft = draft[: match.start()] + replacement + draft[match.end() :]
    draft = re.sub(r"\bNR\b", "RETAINED_NODES", draft)
    draft = re.sub(r"\bNK\b", "INTERNAL_NODES", draft)
    return draft


def _ensure_static_blank_line(draft: str) -> str:
    return re.sub(r"(?m)^STATIC:\n(?!\n)", "STATIC:\n\n", draft)


def c_draft_for_structured_formula(
    structured: dict,
    node_display_names: dict[str, str] | None = None,
    rtds_stage_plan: dict | None = None,
) -> str:
    block_type = structured.get("block_type")
    blocks = structured.get("blocks", {})
    if rtds_stage_plan and rtds_stage_plan.get("code_blocks"):
        code_blocks = rtds_stage_plan["code_blocks"]
        blocks = {
            **blocks,
            "G_rr": code_blocks.get("G_rr", blocks.get("G_rr", [])),
            "G_ri": code_blocks.get("G_ri", blocks.get("G_ri", [])),
            "G_ir": code_blocks.get("G_ir", blocks.get("G_ir", [])),
            "G_ii": code_blocks.get("G_ii", blocks.get("G_ii", [])),
        }
    Grr = sp.Matrix(blocks.get("G_rr", []))
    Grk = sp.Matrix(blocks.get("G_ri", []))
    Gkr = sp.Matrix(blocks.get("G_ir", []))
    Gkk = sp.Matrix(blocks.get("G_ii", []))
    Ihisr = sp.Matrix(blocks.get("Ihis_r", []))
    Ihisk = sp.Matrix(blocks.get("Ihis_i", []))
    nr = Grr.rows
    nk = Gkk.rows
    external_nodes = list(structured.get("external_nodes", []))
    effective_internal_nodes = list(structured.get("effective_internal_nodes", []))
    node_display_names = {str(key): str(value) for key, value in (node_display_names or {}).items()}
    reduction_tail = [] if rtds_stage_plan else _c_emit_rtds_reduction_tail(
        nr,
        nk,
        external_nodes,
        effective_internal_nodes,
        node_display_names,
    )

    lines = [
        "/* RTDS-style C draft for structured node elimination.",
        "   RAM math reference may use matrix_Add/Sub/Mul on raw arrays.",
        "   Runtime sections use MATRIX_ matrixDim/register/condition plus set_CODE,",
        "   matrix_mult_CODE, matrix_matXvec_CODE, matrix_add_CODE/subtract_CODE,",
        "   matrix_scalarMult_CODE, MATH_matx_invert,",
        "   mat_2x2_sym_inv_code, mat_3x3_sym_inv_code. See LOCAL_math_builtin_functions.md. */",
        f"enum {{ NR = {nr}, NK = {nk} }};",
        "",
        *_c_emit_rtds_stage_sections(rtds_stage_plan, node_display_names),
    ]
    if rtds_stage_plan:
        return _join_c_draft_lines(lines)

    lines.extend([
        "/* Input blocks: I = G * V + Ihis, partitioned as r = retained, k = eliminated. */",
        _c_matrix_literal(Grr, "Grr"),
        _c_matrix_literal(Grk, "Grk"),
        _c_matrix_literal(Gkr, "Gkr"),
        _c_matrix_literal(Gkk, "Gkk"),
        _c_vector_literal(Ihisr, "Ihisr"),
        _c_vector_literal(Ihisk, "Ihisk"),
        "",
    ])

    if block_type == "pure_diagonal":
        lines.extend([
            "/* Gkk is diagonal here. Build inv_D directly; do not call a matrix inverse. */",
            _c_zero_matrix("D", nk, nk),
            _c_zero_matrix("inv_D", nk, nk),
            _c_zero_matrix("W", nk, nk),
            "matrix_Copy(NK, NK, D, Gkk);",
        ])
        for index in range(nk):
            lines.append(f"inv_D[{index}][{index}] = 1.0 / D[{index}][{index}];")
        lines.append("matrix_Copy(NK, NK, W, inv_D);")
        lines.extend(reduction_tail)
        return _join_c_draft_lines(lines)

    if block_type == "diagonal_plus_coupled":
        details = structured.get("details", {})
        D = sp.Matrix(details.get("D", []))
        U = sp.Matrix(details.get("U", []))
        S = sp.Matrix(details.get("S", []))
        kd = D.rows
        ks = S.rows
        lines.extend([
            f"enum {{ KD = {kd}, KS = {ks} }};",
            "",
            "/* Gkk = [[D, U], [U^T, S]]. D is diagonal; build inv_D directly. */",
            _c_matrix_literal(D, "D"),
            _c_matrix_literal(U, "U"),
            _c_matrix_literal(S, "S"),
            _c_zero_matrix("inv_D", kd, kd),
        ])
        for index in range(kd):
            lines.append(f"inv_D[{index}][{index}] = 1.0 / D[{index}][{index}];")
        lines.extend([
            "",
            "/* Build M = S - U^T * inv_D * U using RTDS matrix helpers. */",
            _c_zero_matrix("U_T", ks, kd),
            *_c_manual_transpose_assignments("U", "U_T", kd, ks),
            _c_zero_matrix("tmp_UT_invD", ks, kd),
            _c_zero_matrix("tmp_UT_invD_U", ks, ks),
            _c_zero_matrix("M", ks, ks),
            "matrix_Mul(KS, KD, KD, tmp_UT_invD, U_T, inv_D);",
            "matrix_Mul(KS, KD, KS, tmp_UT_invD_U, tmp_UT_invD, U);",
            "matrix_Sub(KS, KS, M, S, tmp_UT_invD_U);",
            "",
            "/* Invert M. Use reciprocal for 1x1, symmetric fast inverse for 2x2/3x3. */",
            _c_zero_matrix("M_inv", ks, ks),
            *_c_sym_inverse_call("M", "M_inv", ks),
            "",
            "/* Build W = inverse(Gkk) from D, U, S block inverse terms. */",
            _c_zero_matrix("tmp_Dinv_U", kd, ks),
            _c_zero_matrix("tmp_Dinv_U_Minv", kd, ks),
            _c_zero_matrix("tmp_Dinv_U_Minv_UT", kd, kd),
            _c_zero_matrix("tmp_Dinv_U_Minv_UT_Dinv", kd, kd),
            _c_zero_matrix("W_DD", kd, kd),
            _c_zero_matrix("W_DS", kd, ks),
            _c_zero_matrix("tmp_Minv_UT", ks, kd),
            _c_zero_matrix("W_SD", ks, kd),
            _c_zero_matrix("W_SS", ks, ks),
            _c_zero_matrix("W", nk, nk),
            "matrix_Mul(KD, KD, KS, tmp_Dinv_U, inv_D, U);",
            "matrix_Mul(KD, KS, KS, tmp_Dinv_U_Minv, tmp_Dinv_U, M_inv);",
            "matrix_Mul(KD, KS, KD, tmp_Dinv_U_Minv_UT, tmp_Dinv_U_Minv, U_T);",
            "matrix_Mul(KD, KD, KD, tmp_Dinv_U_Minv_UT_Dinv, tmp_Dinv_U_Minv_UT, inv_D);",
            "matrix_Add(KD, KD, W_DD, inv_D, tmp_Dinv_U_Minv_UT_Dinv);",
            "matrix_Scale(KD, KS, -1.0, W_DS, tmp_Dinv_U_Minv);",
            "matrix_Mul(KS, KS, KD, tmp_Minv_UT, M_inv, U_T);",
            "matrix_Mul(KS, KD, KD, W_SD, tmp_Minv_UT, inv_D);",
            "matrix_Scale(KS, KD, -1.0, W_SD, W_SD);",
            "matrix_Copy(KS, KS, W_SS, M_inv);",
            *_c_copy_subblock("W", "W_DD", 0, 0, kd, kd),
            *_c_copy_subblock("W", "W_DS", 0, kd, kd, ks),
            *_c_copy_subblock("W", "W_SD", kd, 0, ks, kd),
            *_c_copy_subblock("W", "W_SS", kd, kd, ks, ks),
        ])
        lines.extend(reduction_tail)
        return _join_c_draft_lines(lines)

    lines = [
        *lines,
        "/* General dense Gkk. Prefer the structured block modes above when possible. */",
        _c_zero_matrix("W", nk, nk),
        *(
            [f"/* WARNING: Gkk is {nk}x{nk}; this uses the general inverse routine. */"]
            if nk > 3
            else []
        ),
        f"MATH_matx_invert(NK, &(Gkk[0][0]), NK, &(W[0][0]), NK);",
        *reduction_tail,
    ]
    return _join_c_draft_lines(lines)
