# C Export Optimization Notes

This note tracks C draft optimization ideas for both single-case optimized
elimination and multi-case C export. It is intentionally implementation-facing:
items here may be partially implemented, planned, or reserved for later review.

## Implemented Or Partially Implemented

### Diagonal `Gkk` Scalar Path

When `Gkk` is proven diagonal for a case, avoid a general matrix inverse:

```text
W[k,k] = 1 / Gkk[k,k]
tmp_Grk_W[row,k] = Grk[row,k] / Gkk[k,k]
Gred[row,col] = Grr[row,col] - sum_k tmp_Grk_W[row,k] * Gkr[k,col]
Ihisred[row] = Ihisr[row] - sum_k tmp_Grk_W[row,k] * Ihisk[k]
Vk[k] = -(sum_col tmp_Grk_W[col,k] * Vr[col] + Ihisk[k] / Gkk[k,k])
```

Non-diagonal cases should keep the matrix-DAG path.

### Symmetry / Transpose Reuse

For symmetric networks:

```text
Gkr = Grk^T
W = W^T
```

After computing:

```text
A = Grk * W
```

the recovery term:

```text
W * Gkr
```

can use:

```text
W * Gkr = A^T
```

This avoids a second matrix multiplication in the recovery path.

### Whole-Entry Reuse

Detect whole-entry relationships such as:

```text
Gred[i,j] = Gred[p,q]
Gred[i,j] = -Gred[p,q]
```

without using expensive `sp.cancel`, `sp.simplify`, or `sp.factor` on large
expanded expressions. Current use is mainly for reducing repeated `varG`
reads/assignments. Future use may reduce scalar entry computation.

### Source-Level Direct/Core Split

Split stamps before Schur elimination:

```text
Gtotal = Gcore + Gdirect
Ihistotal = Ihiscore + Ihisdirect
```

- Retained-only elements go to `Gdirect/Ihisdirect`.
- Elements touching internal nodes go to `Gcore/Ihiscore`.
- Schur reduction and internal voltage recovery use only core blocks.
- Direct entries are added at final assembly and placed in RAM or CODE based on
  their dependency.

Do not compute Schur/core by subtracting direct terms from an expanded final
`Gred`.

### Multi-Case Alias Template

For multi-case export, avoid generating a full final formula per case
combination. Instead, create case-resolved aliases:

```text
cr_R1_G_eff
cr_R1_Ihis_eff
...
```

Each alias receives the full value for the active case. The downstream
reduction then runs once on the alias-template network.

No base-plus-delta compensation is used by default.

### Runtime-Mutable Alias Safety

Runtime-mutable case groups are resolved in CODE, but they still follow the
same full-value rule. When a source entry has common additive terms across all
runtime cases, keep those common terms as the residual and put only the
case-varying part into the `multcase_*` alias:

```text
case 0: A*Z + Y + Gc_0
case 1: A*Z + Y + Gc_1

residual: A*Z + Y
alias:    Gc_0 / Gc_1
final:    A*Z + Y + multcase_*
```

Do not create complement expressions such as:

```text
Gc_0 + Gc_1 - multcase_*
```

Those are another form of base/delta compensation and can make no-internal
multi-case direct stamps mathematically hard to audit.

### Dummy / N-Dummy Finalization

Dummy constructs are used to keep multi-case topology compatible. They should
not increase the final C draft unnecessarily:

- Dummy-only retained nodes can be finalized out before C generation.
- N-Dummy nodes that exist only to match eliminated internal nodes should be
  treated as forced-elimination bookkeeping.
- After finalization, reduce matrix dimensions where safe.

## Planned Optimizations

### Upper-Triangle Only For Diagonal Scalar `Gred`

When the reduced conductance matrix is symmetric, diagonal scalar generation can
compute only the upper triangle:

```c
for (int row = 0; row < nr_active; ++row) {
    for (int col = row; col < nr_active; ++col) {
        ...
    }
}
```

This is safe if later code only reads upper-triangular reduced entries for
`varG` stamping. If a later matrix operation reads full `Gred_code`, either keep
the full write or mirror each entry into the lower triangle.

### Use `Grk/Gkr` Sparsity In Diagonal Scalar Path

The diagonal scalar path currently resembles:

```text
for row in retained:
  for col in retained:
    for k in internal:
```

This assumes every `Grk[row,k]` and `Gkr[k,col]` may be nonzero. Electrical
networks are often sparse. For each internal node `k`, precompute retained
neighbors:

```text
neighbors[k] = retained nodes connected through Grk[:,k] or Gkr[k,:]
```

Then accumulate only those pairs:

```text
for k:
  inv = 1 / Gkk[k,k]
  for row in neighbors[k]:
    for col in neighbors[k]:
      Gred[row,col] -= Grk[row,k] * inv * Gkr[k,col]
```

Expected cost changes from:

```text
NR * NR * NK
```

to roughly:

```text
sum_k degree(k)^2
```

This can be combined with upper-triangle-only generation by emitting only
`row <= col` pairs.

### Direct Scalar Stamp For `NK = 0`

If there are no internal nodes, no Schur complement is needed. Dynamic G entries
can often be assigned directly:

```c
varG_A_B = expression;
```

instead of:

```c
set_CODE(&G_code, ...);
varG_A_B = get_CODE(&G_code, ...);
```

### Avoid Registering RAM-Only Matrices

Matrices used only in `RAM_PASS1` do not need CODE-side `MATRIX_` allocation,
`matrix_register`, or `conditionMatrixForCODE`.

Keep CODE-side matrix objects only when they are actually read or written in
runtime sections.

### Diagonal Plus Small Coupled Block

For `Gkk` that is mostly diagonal with a small coupled subblock, use the block
inverse structure rather than a full inverse. If the coupled block is size 2 or
3, prefer the RTDS symmetric fast inverse helpers.

Future refinement: allow more of the diagonal portion to remain scalar and only
use matrix operations for the small coupled block.

## Safety Rules

- Do not use large expanded final `Gred` expressions to infer core/direct or
  Schur structure.
- Do not rely on `sp.cancel`, `sp.simplify`, or `sp.factor` for large dense
  matrices during C export.
- Multi-case `case_id` is treated as init-time fixed unless a case group is
  explicitly marked runtime-mutable; runtime-mutable groups may only change
  expressions, not topology or dimensions.
- When uncertain, fall back to the existing matrix-DAG path.
