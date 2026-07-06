# Branch Builder 中文说明

[English README](README.md) | 中文

Branch Builder 是一个本地运行的浏览器工具，用于搭建和分析网络节点电路模型。它支持绘制支路、变压器、自定义黑盒和 YBox 打包元件，并生成完整节点方程、节点消去后的等效方程、内部节点电压恢复公式和支路观测电流表达式。

## 安装和运行

首次使用推荐方式：

1. 下载项目安装包或 ZIP 压缩包。
2. 解压整个文件夹。
3. 双击运行 `start.bat`。
4. 使用过程中保持本地服务器窗口不要关闭。

`start.bat` 会自动：

- 查找电脑上的 Python；
- 如果缺少 `sympy`，自动安装；
- 启动本地服务；
- 在浏览器中打开 `http://127.0.0.1:4177/`。

如果 Windows 阻止运行脚本，请右键 `start.bat`，打开“属性”，按需解除阻止后再运行。

## 主要功能

![Branch Builder 电路与矩阵方程预览](branch-builder-matrix-equation.png)

- 在画布中绘制、拖拽、缩放、旋转和编辑电路元件。
- 支持二节点支路、单相变压器、自定义 N 节点黑盒和 YBox。
- 输出统一形式的节点方程：`I = G V + Ihis`。
- 使用 Schur complement 对内部节点进行消去。
- 新增“优化消元 / C导出”标签页：面向大型符号系统，使用结构化 `Gkk` 块公式展示消元过程，自动识别对角/耦合子块，并导出不含 solve/LU/Cholesky 的 C 风格步骤。
- 显示内部节点电压恢复公式。
- 为黑盒元件定义支路观测电流，并进行一致性校验。
- 为每个元件定义多个 `G`/`Ihis` 开关工况，并在右侧元件编辑器中选择当前工况。
- 支持支路公式高亮和画布高亮；消去公式使用隐藏来源标签，避免同名 symbol 在不同支路之间串色。
- 打包后的 YBox 可以切换内部支路工况，并重新计算打包后的 `G`、`Ihis` 和观测公式。
- Python 草稿可输出完整矩阵、消去矩阵、内部节点恢复表达式和可复用的符号设置。
- 导入和导出电路 JSON 文件。
- 将电路保存到项目目录下的 `exports/` 文件夹。
- 支持中英文界面切换。

## 电压源近似

`VoltageSourceSeriesR` 表示外部显式输入的电压源 `Vs` 串联导纳 `G`。由于本项目统一使用 `I = G * V + Ihis`，且不使用 MNA，该元件会转换为 Norton 等效：

```text
i(p -> n) = G * (V_p - V_n - Vs)
```

它会生成对称的导纳矩阵写入项，并写入历史电流项：`Ihis[p] += -G*Vs`，`Ihis[n] += G*Vs`，因此可以直接参与内部节点消去和黑盒约简。

`G` 越大越接近理想电压源，但过大会导致矩阵病态。`G` 必须为正数，0 或负数会报错。

## 最近更新

- 改进“优化消元 / C导出”结果标签页。它不改变现有完整矩阵和消去矩阵页面；保留用户定义的节点名，显示真实 `G` 和 `Ihis` 的 r/k 分块预览，并按最终表达式做 RAM/CODE 依赖切分：常量 `Gred` 可在 RAM 侧写入导纳，动态 `Gred` 和 `Ihisred` 使用 CODE 矩阵流程，内部节点电压恢复放在 T1_T2。
- RTDS C 草稿会为 `Grr/Grk/Gkr/W` 生成可复用别名，避免展开巨大 Schur 标量公式；相同表达式使用 `Gkr_shared_1` 这类中性共享别名，并在注释中列出对应矩阵位置。RAM 侧 `g_mat_nods/g_mat_over/setupGMatrix` 只注册实际有非零常量导纳写入的节点子集，并使用压缩后的局部索引。
- 画布标签支持拖拽排序。电路导出升级为 `version: 3`，会保存更完整的工程状态，包括所有画布、节点样式、开关工况、打包黑盒设置、界面选项和已缓存的推导结果。
- 开关工况保存在每个元件上。普通支路可编辑每个工况的 `G` 和 `Ihis`；变压器和自定义黑盒等矩阵元件可编辑每个工况的局部 `G` 矩阵和 `Ihis` 向量。
- 当前工况通过右侧元件编辑器选择，节点方程、消去版本和 Python 草稿都会使用当前工况。
- 打包后的 YBox 不再使用外层 switch case。它的编辑器会列出内部具有多个工况的支路；切换内部工况后，通过本地 SymPy 后端重新计算打包黑盒的 `G`、`Ihis` 和观测公式。
- 多 Case C 导出默认假定 `case_id` 在仿真开始前固定。只有显式勾选“case id 可在 CODE 阶段改变”的工况组，才会生成独立的 CODE 阶段 selector；这个 runtime selector 只用于给 `cr_*_eff` 赋完整值，不会进入 `createGValue` 条件，也不会展开多套最终 `Gred`。
- Pack 多 Case 导出支持外部端口一致、但内部消去节点不同的工况。缺失的内部节点会作为后端专用的 Gkk placeholder 对齐，真实节点恢复按 case 分支生成，C 草稿中的别名和恢复变量使用用户节点名，例如 `inner_left`，不会暴露后端内部 id。
- 公式高亮带有来源追踪。前端会把隐藏标签表达式发送给后端，显示时再去掉标签。这样同名 symbol 来自不同支路时不会互相串色。
- 开启消去公式高亮时，部分合并项可能比普通显示更展开；界面会在“消去版本”中显示提示说明。
- 小窗口布局已优化，浏览器窗口较窄或缩放较大时，右侧面板和公式输出不会互相覆盖。

## 仓库整理原则

公开 GitHub 仓库只应包含有用且非私人的项目内容：源码、测试、公开说明文档，以及 `exports/` 下整理过的测试/示例电路。

本地 AI 备忘录、个人工作笔记、临时截图、日志、根目录临时导出的 JSON、私人推导草稿都应只保留在本地，不提交到公开仓库。`.gitignore` 已包含这些本地文件的忽略规则。

## 环境要求

- Python 3.10 或更新版本。
- Chrome、Edge、Firefox 等现代浏览器。
- 第一次自动安装 `sympy` 时需要网络；如果提前准备了离线 `wheels/` 文件夹，则可以离线运行。

`local_server.py` 会在启动时自动检查 `sympy` 是否已经安装。如果缺少 `sympy`，它会先尝试使用项目目录下的 `wheels/` 离线包安装；如果没有 `wheels/` 文件夹，则通过 pip 在线安装。

## 快速启动

Windows 推荐直接双击：

```text
start.bat
```

也可以在项目文件夹中打开终端：

```powershell
cd path\to\network_node
```

启动本地服务：

```powershell
python local_server.py
```

然后在浏览器中打开：

```text
http://127.0.0.1:4177/
```

使用过程中请保持终端窗口不要关闭。

## 在另一台电脑上运行

请把整个项目文件夹复制到另一台电脑，不要只复制 `index.html`。这个项目的后端计算、保存、读取和黑盒校验都依赖本地服务。

复制完成后，在另一台电脑上直接双击：

```text
start.bat
```

或者进入项目目录：

```powershell
cd path\to\network_node
```

启动：

```powershell
python local_server.py
```

打开：

```text
http://127.0.0.1:4177/
```

如果那台电脑可以联网，服务会自动下载并安装 `sympy`。

## 离线演示准备

如果演示电脑没有网络，请先在有网络的电脑上运行：

```powershell
python -m pip download sympy -d wheels
```

然后把生成的 `wheels/` 文件夹和整个项目一起复制到演示电脑。

在演示电脑上直接运行：

```powershell
python local_server.py
```

服务会自动从 `wheels/` 文件夹安装 `sympy`。

也可以手动安装：

```powershell
python -m pip install --no-index --find-links wheels sympy
```

## 可选 Node.js 启动方式

如果电脑安装了 Node.js，也可以运行：

```powershell
node server.js
```

不过第一次使用时更推荐 Python 服务，因为 `local_server.py` 可以自动检查并安装 Python 后端计算需要的 `sympy`。

## 项目结构

- `index.html`：主浏览器界面。
- `local_server.py`：推荐使用的本地服务。
- `server.js`：可选 Node.js 本地服务。
- `reduce_api.py`：节点方程消元 API。
- `optimized_elimination_api.py`：优化消元 / C 导出 API。
- `elimination.py`：符号节点消去逻辑。
- `observers.py`：支路观测电流消去逻辑。
- `blackbox_validation_api.py`：黑盒观测电流校验 API。
- `nodal_tool/optimized_elimination.py`：结构化 `Gkk` 分块分析，以及不含 solve/LU/Cholesky 的 C 草稿生成。
- `nodal_tool/blackbox_validation.py`：黑盒观测电流一致性检查。
- `exports/`：保存的电路 JSON 文件。
- `tests/`：回归测试。

## 可选开发测试

普通用户只需要运行 `start.bat`，不需要运行这些测试。只有修改源码或检查开发版本时才需要。

运行 Python 测试：

```powershell
python -m unittest discover tests -v
```

运行前端公式格式化测试：

```powershell
node tests\frontend_math_formatter_cases.mjs
```

## 演示注意事项

- 先启动服务，再打开网页。
- 请使用 `http://127.0.0.1:4177/`，不要直接双击打开 `index.html`。
- 如果需要保存导出的电路，请确保项目文件夹可写。
- 如果 `4177` 端口被占用，可以换一个端口：

```powershell
$env:PORT=4180
python local_server.py
```

然后打开：

```text
http://127.0.0.1:4180/
```
