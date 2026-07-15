# RTDS Matrix Lifecycle Guidelines

This note records the matrix API rules that Branch Builder code generation must
follow when emitting RTDS/CBuilder RAM and CODE sections. It is based on the
RTDS local guide notes for `matrixLIB`, math builtins, and RAM matrix API
comparison.

## Core Rules

1. RAM-side `MATRIX_` use is gated by `matrixDim`, not by `matrix_register`.
   Once `matrixDim(&A, rows, cols)` has succeeded, RAM/RAM_PASS code may use
   `set`, `get`, `matrix_mult`, `matrix_add`, `matrix_subtract`,
   `matrix_scalarMult`, `matrix_invert`, and related RAM-side `MATRIX_`
   helpers.

2. CODE-side `MATRIX_` use is gated by the full registration/conditioning chain:
   RAM must call `matrix_register(&A)`, and the first CODE pass must call
   `initializeMatricesForCode()` plus `conditionMatrixForCODE(&A)` before any
   `set_CODE`, `get_CODE`, `matrix_mult_CODE`, `matrix_matXvec_CODE`,
   `matrix_add_CODE`, `matrix_subtract_CODE`, or `matrix_scalarMult_CODE`.

3. Matrix placement is data-dependency first. `matrix_register` and
   `conditionMatrixForCODE` restore CODE-side matrix pointers; they are not a
   reason to move fixed matrix math into CODE. If a matrix value depends only
   on parameters, topology, fixed case data, or other values already known in
   RAM, compute it in RAM, then register it if CODE later needs to read it. If
   a matrix value depends on per-step quantities or runtime-changing inputs,
   compute it in CODE with the `_CODE` helper family.

4. Do not call `matrixDim` again on the same `MATRIX_` after
   `matrix_register`. Resizing can reallocate `A.p` and leave the registered
   pointer stale.

5. Keep RAM and CODE helper families separate:
   - RAM: `set`, `get`, `matrix_mult`, `matrix_add`, `matrix_subtract`,
     `matrix_scalarMult`, `matrix_invert`.
   - CODE: `set_CODE`, `get_CODE`, `matrix_mult_CODE`,
     `matrix_matXvec_CODE`, `matrix_add_CODE`, `matrix_subtract_CODE`,
     `matrix_scalarMult_CODE`.

6. Keep raw-array and `MATRIX_` APIs separate:
   - `double A[r][c]` uses raw-array helpers such as `matrix_Add`,
     `matrix_Sub`, `matrix_Mul`, `matrix_Scale`, and `matrix_Copy`.
   - `MATRIX_ A` uses struct helpers such as `matrix_add`,
     `matrix_subtract`, `matrix_mult`, `matrix_scalarMult`,
     `matrix_copy`, `matrix_transpose`, and `matrix_invert`.
   - Never pass a `double[][]` object to `matrix_mult`, and never pass a
     `MATRIX_` object to `matrix_Mul`.

## Recommended Section Order

For a matrix used only in RAM:

```c
STATIC:

    MATRIX_ A = {0};
    MATRIX_ B = {0};
    MATRIX_ C = {0};

RAM_PASS1:

    err += matrixDim(&A, rows, cols);
    err += matrixDim(&B, cols, outCols);
    err += matrixDim(&C, rows, outCols);
    if (err > 0) {
        reportError_RW(...);
    }

    set(&A, ...);
    set(&B, ...);
    matrix_mult(&C, &A, &B);

    /* No matrix_register is needed when C is RAM-only. */
```

For a matrix used in both RAM and CODE:

```c
STATIC:

    MATRIX_ A = {0};
    MATRIX_ B = {0};
    MATRIX_ C = {0};
    int rtds_matrix_code_ready = 0;

RAM_PASS1:

    err += matrixDim(&A, rows, cols);
    err += matrixDim(&B, cols, outCols);
    err += matrixDim(&C, rows, outCols);
    if (err > 0) {
        reportError_RW(...);
    }

    /* RAM-side precompute is allowed here, before registration. */
    set(&A, ...);
    set(&B, ...);
    matrix_mult(&C, &A, &B);

    /* Register only after all sizing is final. */
    matrix_register(&A);
    matrix_register(&B);
    matrix_register(&C);

CODE:
BEGIN_T0:

    if (!rtds_matrix_code_ready) {
        initializeMatricesForCode();
        conditionMatrixForCODE(&A);
        conditionMatrixForCODE(&B);
        conditionMatrixForCODE(&C);
        rtds_matrix_code_ready = 1;
    }

    /*
     * A, B, and C values were already computed in RAM.
     * CODE may now read C with get_CODE or use it in per-step CODE math.
     * Do not recompute fixed RAM-known products here.
     */
    y = get_CODE(&C, row, col);
```

For a matrix whose values are genuinely CODE-owned:

```c
STATIC:

    MATRIX_ A = {0};
    MATRIX_ B = {0};
    MATRIX_ C = {0};
    int rtds_matrix_code_ready = 0;

RAM_PASS1:

    err += matrixDim(&A, rows, cols);
    err += matrixDim(&B, cols, outCols);
    err += matrixDim(&C, rows, outCols);
    if (err > 0) {
        reportError_RW(...);
    }

    matrix_register(&A);
    matrix_register(&B);
    matrix_register(&C);

CODE:
BEGIN_T0:

    if (!rtds_matrix_code_ready) {
        initializeMatricesForCode();
        conditionMatrixForCODE(&A);
        conditionMatrixForCODE(&B);
        conditionMatrixForCODE(&C);
        rtds_matrix_code_ready = 1;
    }

    /* Only per-step or runtime-changing values belong here. */
    set_CODE(&A, ...runtime_value...);
    set_CODE(&B, ...runtime_value...);
    matrix_mult_CODE(&C, &A, &B);
```

## Codegen Implications

1. Use this placement rule before choosing RAM or CODE:

| Matrix input source | Preferred placement |
|---|---|
| Fixed parameters, topology, fixed case data, or RAM-known initial values | Compute in RAM; register afterward only if CODE needs the matrix pointer. |
| All cases are predefined constants | Compute each needed case in RAM. |
| `active_profile` only selects one of several predefined cases | Precompute RAM-side case results or RAM-side case-specific matrices; CODE may select the prepared result, but should not recompute the fixed matrices. |
| Runtime state creates new matrix values rather than selecting predefined values | Compute the affected matrices in CODE with `_CODE` helpers. |
| CODE inputs, switch/fault/controller outputs, current network voltages/currents, time-step state, history current, dynamic equivalent updates | Compute in CODE with `_CODE` helpers. |
| Case count or matrix dimensions are known only at runtime | Prefer redesigning to RAM-known fixed/super dimensions; otherwise treat as a CODE-side special case with explicit guards. |

2. A CODE ready block should normally contain only:

```c
if (!rtds_matrix_code_ready) {
    initializeMatricesForCode();
    conditionMatrixForCODE(&A);
    conditionMatrixForCODE(&B);
    conditionMatrixForCODE(&C);
    rtds_matrix_code_ready = 1;
}
```

Do not place fixed `Gred = f(parameters, case)`, fixed inverses, fixed Schur
products, or fixed matrix products in this block just because CODE later reads
the matrix. Such math belongs in RAM whenever its inputs are RAM-known. A
CODE-once precompute is only a documented fallback when RAM cannot know the
inputs or dimensions.

3. RAM fixed `Gred` stamps may be computed with `MATRIX_` RAM helpers before
   `setupGMatrix`, as long as all participating matrices have already been
   dimensioned with `matrixDim`.

4. If RAM needs to compute:

```text
Gred = Grr - Grk * W * Gkr
```

then `matrixDim` for `Grr`, `Grk`, `Gkk` or `W`, `Gkr`, `Gred`, and scratch
matrices must be emitted before the `g_mat_over` write. `matrix_register` can
remain near the end of RAM setup.

5. For matrix-mode codegen, avoid expanding large RAM-owned Schur expressions
   into long scalar temporaries when the equivalent RAM `MATRIX_` sequence is
   available. Prefer:

```c
matrix_invert(&W_ram, &Gkk_ram);
matrix_mult(&tmp_Grk_W_ram, &Grk_ram, &W_ram);
matrix_mult(&tmp_Grk_W_Gkr_ram, &tmp_Grk_W_ram, &Gkr_ram);
matrix_subtract(&Gred_ram, &Grr_ram, &tmp_Grk_W_Gkr_ram);
g_mat_over[row][col] = get(&Gred_ram, row, col);
```

over source-level CSE such as `sourceG_tmp0`, `sourceG_tmp1`, etc., when the
selected mode is matrix-oriented and the matrices are RAM-owned.

6. In dummy/multi-case codegen where final retained node counts differ by case,
   `setupGMatrix` must still use the active final retained dimension for the
   current case. Matrix dimensions may use the shared/super dimensions only for
   internal computation; `g_mat_nods`, `g_mat_over`, and `setupGMatrix(dim)`
   must reflect the active case's solver-visible external ports.

7. Do not reuse one `MATRIX_` object with different dimensions across cases
   after registration. If case dimensions vary, either dimension to a safe
   shared maximum before registration and guard active rows/columns, or keep
   case-specific RAM-only matrices unregistered.

8. For diagonal `Gkk` scalar optimizations, scalar paths are still valid when
   selected deliberately. The matrix-mode rule is not "never scalar"; it is
   "do not force large RAM-owned Schur formulas into scalar-expanded code when
   RAM `MATRIX_` computation is the selected and lifecycle-safe path."

## Review Checklist

- Every `MATRIX_` used in RAM has a preceding successful `matrixDim`.
- Every `MATRIX_` used in CODE is registered in RAM and conditioned in CODE.
- No `matrixDim` occurs after `matrix_register` for the same matrix.
- RAM helpers and CODE helpers are not mixed in the same phase.
- Raw-array helpers and `MATRIX_` helpers are not mixed for the same object.
- Fixed RAM-known matrix inverses, Schur products, and `Gred` products are not
  emitted in CODE or in the CODE ready block merely because CODE later reads
  their results.
- CODE ready blocks only initialize/condition matrix pointers, except for a
  documented CODE-owned or runtime-dependent fallback.
- `g_mat_over` and `setupGMatrix` use the active solver-visible port layout,
  not a stale shared dimension.
- Large RAM-owned matrix products in matrix mode are emitted as matrix
  operations, not as long scalar CSE expressions.
