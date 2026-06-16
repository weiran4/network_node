# Branch Builder / 节点网络电路绘图与消元工具

English | [中文说明](README.zh-CN.md)

Branch Builder is a local browser-based tool for building and analyzing network-node circuit models. It lets you draw branches, transformers, custom black boxes, and packaged Y-box components, then generate full and reduced nodal equations.

Branch Builder 是一个本地运行的浏览器工具，用于绘制和分析网络节点电路模型。你可以搭建支路、变压器、自定义黑盒和打包后的 YBox，并输出完整节点方程和节点消去后的等效方程。

## Install and Run / 安装和运行

**Recommended for first-time users:**

1. Download the project package or ZIP file.
2. Extract the whole folder.
3. Double-click `start.bat`.
4. Keep the local server window open while using the app.

`start.bat` will:

- find Python on your computer;
- install `sympy` automatically if it is missing;
- start the local server;
- open `http://127.0.0.1:4177/` in your browser.

**首次使用推荐方式：**

1. 下载项目安装包或 ZIP 压缩包。
2. 解压整个文件夹。
3. 双击运行 `start.bat`。
4. 使用过程中保持本地服务器窗口不要关闭。

`start.bat` 会自动：

- 查找电脑上的 Python；
- 如果缺少 `sympy`，自动安装；
- 启动本地服务；
- 在浏览器中打开 `http://127.0.0.1:4177/`。

If Windows blocks the script, right-click `start.bat`, choose **Properties**, unblock it if needed, then run it again.

如果 Windows 阻止运行脚本，请右键 `start.bat`，打开“属性”，按需解除阻止后再运行。

## Features / 功能

![Branch Builder circuit and matrix equation preview](branch-builder-matrix-equation.png)

- Draw and edit circuit branches on a canvas.
- Build two-node branches, single-phase transformers, custom N-node black boxes, and Y-box packages.
- Generate full nodal equations in the form `I = G V + Ihis`.
- Reduce internal nodes with Schur complement logic.
- Use the Optimized Elimination / C Export tab for large symbolic systems: it keeps the eliminated block as structured `Gkk` formulas, detects diagonal/coupled sub-blocks, and exports RTDS-oriented C-style matrix steps without solve/LU/Cholesky calls.
- Review long formula outputs with the engineering-style results panel, including compact branch-current cards and a VS Code-like minimap for large matrix pages.
- Display internal-node voltage recovery formulas.
- Define and validate branch-current observers for black-box components.
- Define per-component switch cases for `G` and `Ihis`, then double-click a component on the canvas to switch cases.
- Highlight a selected branch in formulas, the canvas, or both. Reduced formulas use hidden provenance tags so same-name symbols from different branches do not cross-highlight.
- Switch internal cases inside packaged Y-box components and recompute the packaged `G`, `Ihis`, and observer formulas.
- Generate a Python Draft with full and reduced matrices, internal-node recovery expressions, and reusable symbolic setup.
- Import and export circuit JSON files.
- Save exported circuits into the local `exports/` folder.
- Switch between Chinese and English UI text.

### Voltage Source Approximation / 电压源近似

`VoltageSourceSeriesR` represents an explicitly supplied voltage source `Vs` with a series conductance `G`. Because the project uses the unified nodal form `I = G * V + Ihis` and does not use MNA, this element is stamped as a Norton equivalent:

```text
i(p -> n) = G * (V_p - V_n - Vs)
```

This produces a symmetric conductance stamp and history-current terms `Ihis[p] += -G*Vs`, `Ihis[n] += G*Vs`, fully compatible with internal-node elimination and black-box reduction.

Use a larger `G` to approximate an ideal voltage source more closely, but avoid making it too large because the matrix can become ill-conditioned. `G` must be positive; zero or negative series conductance is rejected.

中文功能概览：

- 在画布上绘制、拖拽和编辑电路元件。
- 支持二节点支路、单相变压器、自定义 N 节点黑盒和 YBox 打包元件。
- 生成统一形式的完整节点方程：`I = G V + Ihis`。
- 使用 Schur complement 对内部节点进行消去。
- 新增“优化消元 / C导出”标签页：面向大型符号系统，使用结构化 `Gkk` 块公式展示消元过程，自动识别对角/耦合子块，并导出不含 solve/LU/Cholesky 的 C 风格步骤。
- 显示内部节点电压恢复公式。
- 为黑盒元件定义和校验支路观测电流。
- 为元件定义多个 `G`/`Ihis` 开关工况，并可在画布中双击元件切换。
- 支持支路公式高亮和画布高亮；消去公式使用隐藏来源标签，避免同名 symbol 在不同支路之间串色。
- 打包后的 YBox 可以切换内部支路工况，并重新计算打包后的 `G`、`Ihis` 和观测公式。
- Python 草稿可输出完整矩阵、消去矩阵、内部节点恢复表达式和可复用的符号设置。
- 导入和导出电路 JSON 文件。
- 将导出的电路保存到本地 `exports/` 文件夹。
- 支持中英文界面切换。

### 电压源近似

`VoltageSourceSeriesR` 表示外部显式输入的电压源 `Vs` 串联导纳 `G`。由于本项目统一使用 `I = G * V + Ihis`，且不使用 MNA，该元件会转换为 Norton 等效：

```text
i(p -> n) = G * (V_p - V_n - Vs)
```

它会生成对称的导纳矩阵写入项，并写入历史电流项：`Ihis[p] += -G*Vs`，`Ihis[n] += G*Vs`，因此可以直接参与内部节点消去和黑盒约简。

`G` 越大越接近理想电压源，但过大会导致矩阵病态。`G` 必须为正数，0 或负数会报错。

## Recent Updates / 最近更新

- Refined the lower results panel with denser branch-current cards, clearer formula alignment, engineering-style matrix cards, and a minimap navigator for long node-equation, reduced-equation, C export, JSON, and Python draft outputs.
- Improved the Optimized Elimination / C Export tab for RTDS-style C drafting. It now tracks final-expression RAM/CODE dependencies, splits constant and runtime conductance stamps, keeps `Ihisred` and internal-node voltage recovery in CODE/T1_T2-style matrix flows, and emits compact `Grr/Grk/Gkr/W` aliases instead of fully expanded scalar Schur formulas.
- Dynamic C export stamps only the RAM-side nonzero node subset through `g_mat_nods/g_mat_over/setupGMatrix`, so sparse constant stamps use compact local indices. Repeated block expressions are reused through neutral shared aliases such as `Gkr_shared_1`, with comments listing every represented matrix position.
- The one-click Windows launcher `start.bat` is now the recommended way to run the project. It finds Python, checks/installs `sympy`, starts `local_server.py`, and opens `http://127.0.0.1:4177/`.
- Canvas tabs can be reordered by dragging. Circuit exports now use `version: 3` and preserve more project state, including all canvases, node styling, switch cases, packaged-box settings, UI options, and cached derivation results.
- Switch cases are stored with each component. For ordinary branches, edit case-specific `G` and `Ihis`; for matrix components, edit case-specific local `G` and `Ihis` matrices. Double-click the component to cycle cases.
- Packaged Y-boxes do not use an outer switch case. Instead, their editor lists internal branches that have multiple cases; changing an internal case recomputes the packaged box through the local SymPy backend.
- Formula highlighting is provenance-aware. The app sends hidden tagged expressions to the backend and strips the tags before display. When formula highlighting is enabled in reduced equations, some merged symbolic terms may appear more expanded so that the highlight remains trustworthy.
- The reduced view shows a warning when provenance-based highlighting is active.
- Small-window layout has been improved so the side panel and formula output no longer overlap when the browser window is narrow.

中文最近更新：

- 改进“优化消元 / C导出”结果标签页。它不改变现有完整矩阵和消去矩阵页面；保留用户定义的节点名，显示真实 `G` 和 `Ihis` 的 r/k 分块预览，并按最终表达式做 RAM/CODE 依赖切分：常量 `Gred` 可在 RAM 侧写入导纳，动态 `Gred` 和 `Ihisred` 使用 CODE 矩阵流程，内部节点电压恢复放在 T1_T2。
- RTDS C 草稿会为 `Grr/Grk/Gkr/W` 生成可复用别名，避免展开巨大 Schur 标量公式；相同表达式使用 `Gkr_shared_1` 这类中性共享别名，并在注释中列出对应矩阵位置。RAM 侧 `g_mat_nods/g_mat_over/setupGMatrix` 只注册实际有非零常量导纳写入的节点子集，并使用压缩后的局部索引。
- 画布标签支持拖拽排序。电路导出升级为 `version: 3`，会保存更完整的工程状态，包括所有画布、节点样式、开关工况、打包黑盒设置、界面选项和已缓存的推导结果。
- 开关工况保存在每个元件上。普通支路可编辑每个工况的 `G` 和 `Ihis`；矩阵元件可编辑每个工况的局部 `G` 矩阵和 `Ihis` 向量。画布中双击元件可切换工况。
- 打包后的 YBox 不使用外层 switch case。它会在编辑器中列出内部具有多个工况的支路；切换内部工况后，通过本地 SymPy 后端重新计算打包黑盒。
- 公式高亮带有来源追踪。前端会把隐藏标签表达式发送给后端，显示时再去掉标签。开启消去公式高亮时，部分合并项可能比普通显示更展开，以保证高亮来源可信。
- 消去版本在启用来源高亮时会显示提示说明。
- 小窗口布局已优化，浏览器窗口较窄时右侧面板和公式输出不会互相覆盖。

## Repository Hygiene / 仓库整理原则

The public GitHub repository should contain only useful, non-private project material: source code, tests, public documentation, and curated circuit examples under `exports/`.

Local AI notes, personal working notes, temporary screenshots, logs, ad-hoc root-level JSON exports, and private derivation scratch files should stay local and must not be committed. The `.gitignore` file includes patterns for these local-only files.

公开 GitHub 仓库只应包含有用且非私人的项目内容：源码、测试、公开说明文档，以及 `exports/` 下整理过的测试/示例电路。

本地 AI 备忘录、个人工作笔记、临时截图、日志、根目录临时导出的 JSON、私人推导草稿都应只保留在本地，不提交到公开仓库。`.gitignore` 已包含这些本地文件的忽略规则。

## Requirements / 环境要求

- Python 3.10 or newer.
- A modern browser such as Chrome, Edge, or Firefox.
- Internet access the first time the server needs to install `sympy`, unless you prepare an offline `wheels/` folder.

需要：

- Python 3.10 或更新版本。
- Chrome、Edge、Firefox 等现代浏览器。
- 第一次自动安装 `sympy` 时需要网络；如果提前准备了离线 `wheels/` 文件夹，则可以离线运行。

`local_server.py` automatically checks whether `sympy` is installed. If it is missing, the server will try to install it with pip before starting. If a local `wheels/` folder exists, the server will try that folder first.

`local_server.py` 会在启动时自动检查是否安装了 `sympy`。如果没有安装，它会先尝试使用项目目录下的 `wheels/` 离线包；如果没有离线包，则使用 pip 在线安装。

## Quick Start / 快速启动

For Windows, use the one-click launcher:

Windows 推荐使用一键启动：

```text
start.bat
```

Alternatively, open a terminal in this project folder and run:

也可以在项目文件夹中打开终端，运行：

```powershell
python local_server.py
```

Then open this URL in your browser:

然后在浏览器中打开：

```text
http://127.0.0.1:4177/
```

Keep the server window open while using the app.

使用过程中请保持服务器窗口不要关闭。

## Running on Another Computer / 在另一台电脑运行

Copy the whole project folder to the other computer. Do not copy only `index.html`, because the app needs the local server for symbolic reduction, validation, saving, and loading.

请把整个项目文件夹复制到另一台电脑，不要只复制 `index.html`。因为符号消元、黑盒校验、保存和读取都需要本地服务。

On the other computer:

在另一台电脑上：

```text
Double-click start.bat
```

或者使用命令行：

```powershell
cd path\to\network_node
python local_server.py
```

Then open:

然后打开：

```text
http://127.0.0.1:4177/
```

If the computer has internet access, `local_server.py` will automatically install `sympy` if needed.

如果电脑可以联网，`local_server.py` 会在需要时自动安装 `sympy`。

## Offline Setup / 离线准备

If the demonstration computer does not have internet access, prepare the dependency on a computer that does:

如果演示电脑没有网络，请先在有网络的电脑上准备离线依赖包：

```powershell
python -m pip download sympy -d wheels
```

Copy the generated `wheels/` folder together with the project. On the offline computer, simply run:

把生成的 `wheels/` 文件夹和项目一起复制到离线电脑。然后直接运行：

```powershell
python local_server.py
```

The server will detect the local `wheels/` folder and install `sympy` from it automatically.

服务会自动检测本地 `wheels/` 文件夹，并从里面安装 `sympy`。

You can also install it manually:

也可以手动安装：

```powershell
python -m pip install --no-index --find-links wheels sympy
```

## Optional Node Server / 可选 Node 服务

There is also a Node.js server:

项目也提供 Node.js 版本的本地服务：

```powershell
node server.js
```

The Python server is recommended for first-time users because it can automatically check and install the Python dependency used by the calculation backend.

第一次使用时建议运行 Python 服务，因为它可以自动检查并安装后端计算所需的 Python 依赖。

## Project Structure / 项目结构

- `start.bat` - Windows one-click launcher that starts the Python local server and opens the app / Windows 一键启动脚本。
- `index.html` - main browser interface / 主浏览器界面。
- `local_server.py` - recommended local server / 推荐使用的本地服务。
- `server.js` - optional Node.js local server / 可选 Node.js 本地服务。
- `reduce_api.py` - backend API wrapper for equation reduction / 节点方程消元 API。
- `optimized_elimination_api.py` - API wrapper for the Optimized Elimination / C Export tab / 优化消元与 C 导出 API。
- `elimination.py` - symbolic node-elimination logic / 符号节点消去逻辑。
- `observers.py` - branch-current observer reduction logic / 支路观测电流消去逻辑。
- `blackbox_validation_api.py` - API wrapper for black-box observer validation / 黑盒观测电流校验 API。
- `nodal_tool/blackbox_validation.py` - black-box observer consistency checks / 黑盒观测电流一致性检查。
- `nodal_tool/optimized_elimination.py` - structured `Gkk` block analysis and C draft helpers without solve/LU/Cholesky calls / 结构化 `Gkk` 分块分析，以及不含 solve/LU/Cholesky 的 C 草稿生成。
- `exports/` - saved circuit JSON files / 保存的电路 JSON 文件。
- `tests/` - regression tests / 回归测试。

## Optional Developer Tests / 可选开发测试

Regular users do not need to run these tests. They are useful when modifying the source code or checking a development build.

普通用户只需要运行 `start.bat`，不需要运行这些测试。只有修改源码或检查开发版本时才需要。

Run the core Python tests with:

运行 Python 测试：

```powershell
python -m unittest discover tests -v
```

Run the frontend formatter cases with:

运行前端公式格式化测试：

```powershell
node tests\frontend_math_formatter_cases.mjs
```

## Notes for Demonstrations / 演示注意事项

- Start the server before opening the page.
- Use `http://127.0.0.1:4177/`, not a direct `file://` path.
- Keep the project folder writable if you want to save exported circuits.
- If port `4177` is already in use, start with a different port:

中文提醒：

- 先启动服务，再打开网页。
- 请使用 `http://127.0.0.1:4177/`，不要直接双击打开 `index.html`。
- 如果需要保存导出的电路，请确保项目文件夹可写。
- 如果 `4177` 端口被占用，可以换一个端口：

```powershell
$env:PORT=4180
python local_server.py
```

Then open:

然后打开：

```text
http://127.0.0.1:4180/
```
