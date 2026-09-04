# Changelog

本项目遵循语义化版本号。Blender 插件的 `bl_info.version` 与发布包版本保持一致；
实验性验证结果不会单独伪装成稳定版本。

## 0.5.10 - 2026-09-04

### 修复

- 修复硬表面 bounded repair 超出碎片预算后误入通用 Smart Project 回退路径，
  导致读取未初始化 `repair_faces` 并中止优化的问题。
- 硬表面结果继续交给最终 Unique overlap、winding、退化和 0-1 门禁判定；通用
  Smart Project 的超预算恢复与 bounded fallback 行为保持不变。

### 验证

- Unique 修复回归新增硬表面超预算注入，确保该路径不再依赖通用修复的局部状态。

## 0.5.8 - 2026-09-03

### 改进

- 小岛链预算改为只计算新增碎片，不再把大型机械主体计入面数上限；首次合法缝合不受链直径限制，后续扩张仍受吸收次数、碎片面数和模型空间直径约束。
- `Refine Layout` 新增严格平面 U/V frame 修复，以 `0.1°` 法线散度和点到平面误差 / 包围盒对角 `1e-5` 判定真正平面，避免把翘曲 N-gon 当作平面重投影。
- 平面修复保持 face-set、UV 中心、面积和正 winding，并逐岛检查内部重叠、退化和拉伸；不满足门禁的候选保持原状。
- source-cell 增加不旋转的可变尺寸 shelf 候选；候选必须达到规则网格的统一缩放下限，避免用 texel density 换表面规整。
- Adaptive 回放绑定冻结的 face-set/axis 合同，阻止重投影后 `AUTO` 静默换轴；最终方向审计覆盖有符号 U/V 双轴、90°、180° 和负 parity。
- 对 exact signature 且有可靠 intrinsic landmark 的 `MIRROR / ROTATIONAL / REPEATED` 组增加 repeat-local signed semantic frame。局部帧优先于全局 Object/World，写回只做刚性旋转、不做反射；条件不足的 35 组使用全局 fallback。PCA 与轮廓 heading 仅保留为 presentation metric。

### 验证

- Blender `3.3.5` 与 `5.2.0 LTS` 的定向回归、完整 16 项矩阵和解压 ZIP strict smoke 均通过。
- 拼接武器 Blender 5.2 实测：Body `680 -> 508`、Gun Head `203 -> 152`、Bullet `4 -> 4`；覆盖率分别为 `15.5157301% / 23.1494795% / 51.6135375%`。三个对象 overlap、degenerate、negative 与 planar-frame failure 均为 0，保存重开审计通过。
- 59 个 repeat 关系组中 24 组、48 个成员满足 local eligibility；局部残差 P95 `0.0012766°`、最大 `0.0016252°`，signed checker error groups 为 0。raw global audit 的 46 个 misaligned 与 6 个 quarter-turn 是被局部语义帧覆盖的成员，不是有效错误。
- Body 的 235 个单面 UV 岛中，110 个本身就是拓扑断开的单面 Mesh 组件，不能通过 UV 缝合伪造成连续岛。更激进的碎岛放宽候选因残留 `1` 或 `11` 个 folded/degenerate chart 被严格门禁拒绝并回退。

## 0.5.7 - 2026-09-02

### 改进

- 新增长矩形竖向优先：明显长方形默认将主对称轴吸附到 UV `V` 方向；近似正方形不强制旋转。
- 保留 Geometry、方向重复件和连续结构的刚性方向合同；只有无符号方向锁的图表参与竖向展示偏好。
- 顶层自由矩形块在排布时同步锁定竖向，避免 Pack 阶段把已经校正的长条重新转回横向。
- 可自由旋转的小批量矩形增加确定性候选顺序搜索，在不增加最长边预算的前提下优先降低空白条带、提高 0-1 UV 图利用率。
- 在分析快照中记录 `long_rectangle_presentation` 与 `long_rectangle_min_aspect`，便于复现布局决策。

### 验证

- Blender `3.3.5` 与 `5.2.0 LTS`：7 个回归脚本、长矩形方向/排布新增回归和严格 smoke 全部通过。
- 严格 smoke 状态：两版均为 `VUV_055_SMOKE_OK`；Unique 质量门禁、回滚和导入链合同保持通过。

## 0.5.6 - 2026-09-01

### Added

- 增加有符号的 Geometry 方向合同：选择的模型正轴映射到 UV `+V`，可区分 90 度侧转和 180 度倒置。
- `Texture Up Axis=Auto` 支持确定性的 `+Z -> +X -> +Y` 候选解析，并拒绝低投影、双峰或方向不连贯的候选。
- 支持 `Object` 和 `World` 两种方向空间；结构、owner-child、重复件可共享方向轴。
- 方向锁覆盖原生 Pack、结构 Pack、局部修复和 float32 回放，最终阶段重新审计原选轴。
- 0.5.5 的拓扑安全小岛清理、owner cohort、确定性结构排布、Unique 严格门禁和失败事务回滚继续保留。

### Validation

- Blender `3.3.5` 与 `5.2.0 LTS`：方向几何、方向管线、结构布局、局部修复和兼容 smoke 均通过。
- 拼接武器 v31 实测：Bullet `4 -> 4`，Body `680 -> 508`，Gun Head `203 -> 160`；887/887 个输出岛方向可解析，0 侧转、0 倒置、0 超容差。
- 发布 ZIP 为唯一顶层 `visibility_uv_optimizer/`，15 个白名单文件，带 SHA-256 校验记录。

### Known limitations

- 仍是受控试验版，不能宣称对所有复杂 Unique 生产资产都能一次成功。
- 复杂资产可能因源几何退化、关系邻近度或覆盖率门禁被安全拒绝并完整回滚。
- 插件只处理活动 Mesh 对象；不自动综合动画姿态，也不替代多对象批处理器。
- Trim Sheet、Info Atlas、LED/VFX 等非 Unique 布局必须使用保留合同，不能套用 Unique 重叠标准。

## 0.5.6-post1 - unreleased

当前分支包含以下待发布增强，代码已加入回归测试，但尚未改变插件主版本号：

- 增加 AUTO 轴的直角偏好和法线域共同轴一致性选项，且受稳定性损失与置信度门槛约束。
- 为 source-preserving 布局增加有界 source-cell/rack 排布，减少相关碎片被无关区域隔开的情况。
- 增加共同轴直角偏好、边界条件和回退行为的回归覆盖。

这些增强需要重新生成安装包，并在 Blender 3.3.5/5.2.0 重新执行完整验收后，才可并入下一个正式版本。

## 0.5.5 - 2026-08-28

- 增加 `Refine Layout`，在保留有效大岛的前提下清理小岛和重复结构。
- 增加 owner-child / repeat cohort 方向与邻近约束、拓扑安全小岛缝合和局部残留图表修复。
- 建立 Unique 严格质量门禁：有限坐标、0-1 范围、正 winding、零退化、零正面积重叠和事务回滚。

## 0.5.4 - 2026-08-27

- 增加硬表面合同分流、材质/Artist Seam 保护、圆柱和导轨边界规则及 Blender 3.3/5.2 smoke。
- 发布首个硬表面试验安装包，并明确真实资产失败时必须回滚的安全边界。

## 0.5.2 - 2026-08-25

- 建立可见性分析、相机/球面采样、面优先级覆盖和独立 Mapping Viewer 导出能力。
- 保留 Legacy Smart 模式，作为历史行为和兼容回归基线。
