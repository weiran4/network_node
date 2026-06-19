# Branch Builder

English | [Chinese README](README.zh-CN.md)

Branch Builder is a local browser-based tool for building and analyzing network-node circuit models. It lets you draw branches, transformers, custom black boxes, and packaged Y-box components, then generate full nodal equations, reduced equations, internal-node voltage recovery formulas, branch-current observer formulas, and RTDS-oriented C drafts.

## Install And Run

Recommended for first-time users:

1. Download the project package or ZIP file.
2. Extract the whole folder.
3. Double-click `start.bat`.
4. Keep the local server window open while using the app.

`start.bat` will:

- find Python on your computer;
- install `sympy` automatically if it is missing;
- start the local server;
- open `http://127.0.0.1:4177/` in your browser.

If Windows blocks the script, right-click `start.bat`, choose **Properties**, unblock it if needed, then run it again.

## Features

![Branch Builder circuit and matrix equation preview](branch-builder-matrix-equation.png)

- Draw, move, resize, rotate, connect, and edit circuit components on a grid canvas.
- Build two-node branches, current sources, voltage-source-with-series-conductance elements, single-phase transformers, custom N-node black boxes, and packaged Y-box components.
- Generate full nodal equations in the form `I = G V + Ihis`.
- Reduce internal nodes with Schur complement logic.
- Use the **Optimized Elimination / C Export** tab for large symbolic systems. It keeps the eliminated block as structured `Gkk` formulas, detects diagonal/coupled sub-blocks, and exports RTDS-oriented C-style matrix steps without solve/LU/Cholesky calls.
- Use the **Multi-Case C Export** tab to generate one case-agnostic alias-template C draft for multiple component cases without enumerating full final `Gred` formulas per combination.
- Review long formula outputs with compact cards, engineering-style matrix sections, and a minimap navigator for large matrix pages.
- Display internal-node voltage recovery formulas.
- Define and validate branch-current observers for black-box components.
- Define per-component switch cases for `G` and `Ihis`, then double-click a component on the canvas to switch the active case.
- Highlight a selected branch in formulas, the canvas, or both. Reduced formulas use hidden provenance tags so same-name symbols from different branches do not cross-highlight.
- Switch internal cases inside packaged Y-box components and recompute the packaged `G`, `Ihis`, and observer formulas.
- Generate a Python draft with full and reduced matrices, internal-node recovery expressions, and reusable symbolic setup.
- Import and export circuit JSON files.
- Save and reload cached reduced-equation, optimized-C, and multi-case-C results with circuit JSON files.
- Switch between Chinese and English UI text.

## Voltage Source Approximation

`VoltageSourceSeriesR` represents an explicitly supplied voltage source `Vs` with a series conductance `G`. Because the project uses the unified nodal form `I = G * V + Ihis` and does not use MNA, this element is stamped as a Norton equivalent:

```text
i(p -> n) = G * (V_p - V_n - Vs)
```

This produces a symmetric conductance stamp and history-current terms `Ihis[p] += -G*Vs`, `Ihis[n] += G*Vs`, fully compatible with internal-node elimination and black-box reduction.

Use a larger `G` to approximate an ideal voltage source more closely, but avoid making it too large because the matrix can become ill-conditioned. `G` must be positive; zero or negative series conductance is rejected.

## Recent Updates

- Optimized C export now uses source-level direct/core splitting. Direct-retained elements are stamped separately and added to the final reduced equation after the core Schur path; internal-node recovery uses only the core matrices.
- Final reduced dependency analysis can be borrowed from the ordinary reduced-equation path, but only for RAM/CODE placement. It is not used to reconstruct Schur/core expressions from huge final formulas.
- RTDS C drafts include `matrixLIB.h` whenever `MATRIX_` helpers are used and include `builtin_MATH.h` whenever `mat_2x2_sym_inv_code` or `mat_3x3_sym_inv_code` is emitted.
- Small symmetric runtime inverses are optimized. A symmetric 1x1/2x2/3x3 `Gkk` or coupled-block `M` uses direct reciprocal, `mat_2x2_sym_inv_code`, or `mat_3x3_sym_inv_code`; larger or non-symmetric matrices use `MATH_matx_invert`.
- Multi-case export uses effective aliases such as `cr_R1_G_eff` and `cr_R1_Ihis_eff`. Each alias receives the current case's full source value, never a base-plus-delta expression, and the normal structured Schur/W/Gred/Ihisred/Vk flow is generated once.
- Multi-case owner promotion is conservative: if any case for an alias depends on CODE or per-step history, the whole alias is promoted to the highest required lifecycle and a warning is shown. `case_id` is assumed fixed before simulation and must not change at runtime.
- Conditional final `GValue` output is supported for mixed RAM/CODE final entries. Matching conditions are grouped into one switch so repeated per-varG switch blocks are avoided.
- Circuit JSON exports preserve more project state, including canvases, node styling, switch cases, packaged-box settings, UI options, and cached derivation results.
- Formula stamping and rendering use SymPy expressions for negation. Composite expressions such as `X + Y + Z` are negated as `-(X + Y + Z)` or `-X - Y - Z`, never by string prefixing.
- The lower results panel and side panel have been refined for narrow windows, long matrix formulas, and precise wire/node selection near component boxes.

## Repository Hygiene

The public GitHub repository should contain only useful, non-private project material: source code, tests, public documentation, and curated circuit examples under `exports/`.

Local AI notes, personal working notes, temporary screenshots, logs, ad-hoc root-level JSON exports, and private derivation scratch files should stay local unless they are intentionally promoted into public documentation or curated test fixtures.

## Requirements

- Python 3.10 or newer.
- A modern browser such as Chrome, Edge, or Firefox.
- Internet access the first time the server needs to install `sympy`, unless you prepare an offline `wheels/` folder.

`local_server.py` automatically checks whether `sympy` is installed. If it is missing, the server will try to install it with pip before starting. If a local `wheels/` folder exists, the server will try that folder first.

## Quick Start

For Windows, use the one-click launcher:

```text
start.bat
```

Alternatively, open a terminal in this project folder and run:

```powershell
python local_server.py
```

Then open this URL in your browser:

```text
http://127.0.0.1:4177/
```

Keep the server window open while using the app.

## Running On Another Computer

Copy the whole project folder to the other computer. Do not copy only `index.html`, because the app needs the local server for symbolic reduction, validation, saving, and loading.

On the other computer:

```text
Double-click start.bat
```

Or use a terminal:

```powershell
cd path\to\network_node
python local_server.py
```

Then open:

```text
http://127.0.0.1:4177/
```

If the computer has internet access, `local_server.py` will automatically install `sympy` if needed.

## Offline Setup

If the demonstration computer does not have internet access, prepare the dependency on a computer that does:

```powershell
python -m pip download sympy -d wheels
```

Copy the generated `wheels/` folder together with the project folder to the offline computer. Then run:

```text
start.bat
```

The server will detect the local `wheels/` folder and install `sympy` from it.

You can also install manually:

```powershell
python -m pip install --no-index --find-links wheels sympy
```

## Optional Node Server

The project also provides an optional Node.js local server:

```powershell
node server.js
```

For first-time use, the Python server is recommended because it can automatically check and install the Python dependency required by the symbolic backend.

## Project Structure

- `start.bat` - Windows one-click launcher that starts the Python local server and opens the app.
- `index.html` - main browser interface.
- `local_server.py` - recommended local server.
- `server.js` - optional Node.js local server.
- `reduce_api.py` - backend API wrapper for ordinary equation reduction.
- `optimized_elimination_api.py` - API wrapper for optimized elimination, C export, and multi-case C export.
- `elimination.py` - symbolic node-elimination logic.
- `observers.py` - branch-current observer reduction logic.
- `blackbox_validation_api.py` - API wrapper for black-box observer validation.
- `nodal_tool/blackbox_validation.py` - black-box observer consistency checks.
- `nodal_tool/optimized_elimination.py` - structured `Gkk` block analysis and C draft generation helpers.
- `exports/` - saved circuit JSON files and curated test fixtures.
- `tests/` - regression tests.

## Optional Developer Tests

Ordinary users only need `start.bat`. These commands are mainly for source changes and development checks.

Run Python tests:

```powershell
python -m pytest -q
```

Run frontend formula-formatting tests:

```powershell
node tests\frontend_math_formatter_cases.mjs
```

## Demonstration Notes

- Start the local server before opening the web page.
- Use `http://127.0.0.1:4177/`; do not open `index.html` directly with `file://`.
- If you need to save exported circuits, make sure the project folder is writable.
- If port `4177` is occupied, choose another port:

```powershell
python local_server.py 4188
```

Then open:

```text
http://127.0.0.1:4188/
```
