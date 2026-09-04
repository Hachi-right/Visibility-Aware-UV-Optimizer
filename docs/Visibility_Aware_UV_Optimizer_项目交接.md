# Visibility Aware UV Optimizer 项目交接

最后更新：2026-09-04

## 1. 项目定位

Visibility Aware UV Optimizer 是面向 Blender 硬表面游戏资产的 UV 插件，核心目标是：

- 根据可见性和硬表面结构生成或整理 UV；
- 在不跨越 Artist Seam、材质边界、受保护 Sharp、非流形边等硬约束的前提下减少无意义碎岛；
- 让重复件、镜像件和连续机械结构保持可审计的纹理方向与邻近排布；
- 对 Unique / Bake UV 执行严格质量门禁，失败时恢复操作前状态，而不是保存有重叠、翻面或退化的结果。

支持 Blender `3.3` 至 `5.2`，主要回归环境为 Blender `3.3.5` 和 `5.2.0 LTS`。当前仍是受控试验插件，不应直接用于无人值守批处理全部生产资产。

## 2. 版本与状态口径

| 状态 | 版本/位置 | 说明 |
| --- | --- | --- |
| 上一个远端基线 | `fed877f`，插件 `0.5.9` | 修复了无效 Refine Layout 源 UV 自动重建问题。 |
| 本次任务分支交付 | 插件 `0.5.10` | 包含源码、回归测试、CHANGELOG、README、哈希和两个 ZIP；已修复硬表面超预算分支访问未初始化 `repair_faces`，双版本定向回归和 strict smoke 已通过。 |
| 完整发布验证基线 | `0.5.8` | 已记录 Blender 3.3.5/5.2 的完整 16 项矩阵、解压 ZIP strict smoke 和真实武器场景验证。 |
| 49785 实测 | 未通过 | `0.5.10` 已越过旧的碎片预算/未初始化变量错误，但仍在 17 面图表的 bounded repair 失败。 |

`0.5.10` ZIP 不能仅凭合成回归或哈希匹配就视为 49785 兼容版本。双版本 Unique 修复回归和 strict smoke 已通过，但当前任务收口前仍须使 49785 报告满足 `status=success`、`overall_valid=true`。

## 3. 仓库与入口

本机仓库：

```text
C:\Users\SEASUN\Documents\ChatGPT\绑骨\sandbox\gitlab_uv_publish
```

Git 信息：

```text
branch: uv展开
origin: https://gitlab2.seasungame.com/AIGC/MB-AIGC.git
github: https://github.com/Hachi-right/Visibility-Aware-UV-Optimizer.git
```

目录职责：

| 路径 | 职责 |
| --- | --- |
| `addons/visibility_uv_optimizer/` | 插件可编辑源码。 |
| `tests/` | Blender 内运行的回归与 smoke 脚本。 |
| `release/` | 可安装 ZIP、校验值和已保存的验证报告。 |
| `docs/` | 用户说明、设计说明、验证记录和本交接文档。 |
| `项目大脑/用户/chenrun/tasks/2026-08-31-vuv-uv-direction-consistency/` | 当前方向一致性与 49785 兼容任务的决策、过程、指标和现场交接。 |

## 4. 代码架构

| 文件 | 主要职责 |
| --- | --- |
| `__init__.py` | `bl_info`、Blender 版本声明、类注册与注销。 |
| `operators.py` | UI Operator；`VUV_OT_OptimizeUV` 调用优化主链。 |
| `properties.py` | `VUVSettings`、模式、UV 合同、硬表面和参考 UV 参数。 |
| `ui.py` | `3D Viewport > Sidebar > UV Optimizer` 面板。 |
| `uv_optimize.py` | `optimize_active_object` 主入口；模式解析、事务快照、修复、打包和最终 Unique 审计。 |
| `hard_surface.py` | 面板/倒角/圆柱等硬表面分类、边约束和有符号几何方向对齐。 |
| `small_island_cleanup.py` | 受拓扑与质量门禁约束的小岛缝合。 |
| `uv_group_layout.py` | 重复/镜像/旋转结构识别，owner 归属，方向合同和确定性结构排布。 |
| `visibility.py`、`camera_utils.py`、`probe_analysis.py` | 可见性采样、相机和探针分析。 |
| `interactive_export.py`、`mapping_viewer.html` | 独立交互式 3D/UV 映射查看器导出。 |

优化主链可以概括为：

```text
解析 UV Usage / Initial Mode
  -> 快照 UV、Seam、选择和属性状态
  -> 建立硬表面边界与参考边偏置
  -> 初始展开或 Refine Layout 源检查
  -> 局部坏图表修复与小岛清理
  -> 方向对齐、全局 Pack、结构分组排布
  -> Unique 最终门禁
  -> 通过则提交；异常或门禁失败则完整回滚
```

## 5. 核心质量合同

Unique / Bake 结果必须同时满足：

- UV 坐标有限且全部位于 `0-1`；
- 源三角形和 UV 三角形均非退化；
- 所有三角形保持一致的正 winding；
- 岛内和岛间没有正面积重叠；
- 真实 UV 不连续边与 Seam 状态一致；
- 开启方向锁时，已解析的模型正轴仍映射到 UV `+V`；
- 优化失败时，UV 层、坐标、active/render 身份、pin/选择、Seam、隐藏状态和分析属性恢复到操作前状态。

不得通过单纯提高碎片预算、关闭最终 overlap 检查或逐面投影全部多面图表来绕过门禁。

## 6. UVMap2 / UVMap3 参考边设计

插件设置：

- `reference_uv_layers`：逗号分隔参考层，例如 `UVMap2,UVMap3`；
- `reference_uv_boundary_mode`：`UNION` 或 `INTERSECTION`；
- `reference_uv_boundary_bias`：参考边在候选分割评分中的软偏置，当前默认 `0.35`。

参考层只提供软边界证据，不会直接成为锁定 Seam。几何、材质、Artist Seam、Sharp 和非流形约束仍优先。49785 的基准统计为：

| 参考方式 | 不连续边/岛 |
| --- | ---: |
| `UVMap2` | 5035 条边，261 岛 |
| `UVMap3` | 4756 条边，284 岛 |
| 交集 | 4239 条公共边，222 岛 |
| 并集 | 5552 条边，329 岛 |

`UVMap2` 和 `UVMap3` 自身都不是合法 Unique 源：二者存在 overlap，且分别含退化或负 winding 三角形。因此只能学习其分割意图，不能复制后直接宣称成功。

## 7. 安装与日常使用

1. 在 `Edit > Preferences > Add-ons > Install` 选择 `release/` 下目标 ZIP。
2. 启用 `Visibility Aware UV Optimizer`。
3. 复制生产资产或另存版本，激活目标 Mesh。
4. 默认使用 `Initial UV Mode=Auto`、`UV Usage=Auto`、`Profile=Weapon / Prop`。
5. 已有大岛需要保留边界并整理时使用 `Refine Layout`；源 UV 不合法时插件会切换到 Hard Surface 重建。
6. 只有 Operator 完成且最终门禁通过的结果才可交付。

## 8. 验证命令

PowerShell 示例，先进入仓库根目录。

定向 Unique 修复回归：

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python 'tests\test_unique_uv_repair_fallback.py'
& 'C:\Program Files\Blender Foundation\Blender 3.3\blender.exe' --background --factory-startup --python 'tests\test_unique_uv_repair_fallback.py'
```

通过标记为 `VUV_UNIQUE_LOCAL_REPAIR_OK`。完整发布还需运行 `tests/` 下方向、结构布局、frame、axis、face direction 和 smoke 脚本；不能以单个测试代替双版本发布矩阵。

文档和补丁基础检查：

```powershell
git diff --check
Get-FileHash -Algorithm SHA256 -LiteralPath 'release\Visibility_Aware_UV_Optimizer_0.5.10_HardSurface.zip'
```

`release/SHA256SUMS.txt` 是包校验入口。ZIP 应只有一个顶层目录 `visibility_uv_optimizer/`，不得包含缓存、临时文件或重复 entry。当前仓库没有独立发布构建脚本，重建时要沿用既有 15 文件白名单和确定性 entry 时间戳，并逐文件比对源码哈希。

## 9. 发布流程

1. 确认 `bl_info.version`、README、CHANGELOG、ZIP 文件名和哈希版本一致。
2. 在 Blender 3.3.5 与 5.2.0 跑相关定向回归和完整 smoke。
3. 从 ZIP 解压目录再跑 strict smoke，验证真实安装链。
4. 检查 ZIP 顶层、白名单、重复 entry、逐文件哈希和 `SHA256SUMS.txt`。
5. 审阅 `git diff`，确认没有 `.blend`、临时报告、缓存或无关用户改动。
6. 只有用户明确要求时才执行 `git commit`；只有用户明确要求推送时才执行 `git push`。

## 10. 当前已知限制

- 49785 仍会报 `Unique UV bounded repair could not repair chart with 17 faces`，尚无成功 UV 结果。
- 零面积面、重复表面、严重非流形或数值极不稳定的 N-gon 可能被严格拒绝。
- 拓扑断开的单面组件不能为了减少岛数而伪造 UV 焊接。
- 方向一致性必须用带字母、编号或箭头的棋盘格验收；纯双色棋盘格无法证明 180 度正反。
- `addons/visibility_uv_optimizer/README.md` 仍以 `0.5.8` 为标题，正式发布新版本时要同步更新。

当前 49785 现场、复现命令和下一步见[任务交接说明](../项目大脑/用户/chenrun/tasks/2026-08-31-vuv-uv-direction-consistency/交接说明.md)。
