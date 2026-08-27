# Visibility Aware UV Optimizer 0.5.4 硬表面使用说明

## 1. 定位与兼容性

0.5.4 面向游戏武器、机械道具和硬表面模型的保守自动 UV 工作流，同时保留
0.5.3 的 Legacy Smart 模式和原有可见性分析能力。

- 支持 Blender 3.3 至 5.2。
- 优化只处理活动 Mesh 对象。
- 正式资产必须先保存副本或纳入版本控制。

## 2. 安装

1. 下载 `release/Visibility_Aware_UV_Optimizer_0.5.4_HardSurface.zip`。
2. 打开 `Edit > Preferences > Add-ons > Install`。
3. 选择 ZIP 并启用 `Visibility Aware UV Optimizer`。
4. 在 3D Viewport 按 `N`，打开 `UV Optimizer` 标签页。

## 3. UV 用途合同

`UV Usage=Auto` 会根据下划线分词后的对象名判断用途：

| 合同 | 识别名称 | Auto 行为 |
| --- | --- | --- |
| Unique / Bake | 默认 | 重建并打包到 0-1，执行严格门禁 |
| Trim Sheet | `TrimSheet`、`Trim_Sheet` | 保持原布局 |
| Info Atlas | `InfoAtlas`、`Info_Atlas` | 保持原布局 |
| LED / VFX | `LED`、`LED1`、`VFX`、`VFX1` 等 token | 保持原布局 |

自动保护只有在 `Initial UV Mode=Auto` 时生效。显式选择 `Hard Surface`、
`Marked Seams` 或 `Legacy Smart` 会请求重建；`Refine Layout` 虽不做初始投影，
仍会重新缩放和 Pack。它们都可能破坏非 Unique 的堆叠、方向和越 tile 布局。

## 4. 初始模式

| 模式 | 用途 |
| --- | --- |
| Auto | 先解析 UV 合同；非 Unique 自动保护；Unique 按 Seam 情况选择硬表面流程 |
| Hard Surface | 按平面、倒角带、圆柱和通用区域生成结构边界后展开 |
| Marked Seams | 优先沿人工 Seam 展开 |
| Refine Layout（0.5.5） | 保留现有有效图表边界，跳过初始全局展开，只整理碎岛和结构排布 |
| Preserve Layout | 保持 active UV 坐标和 Seam 不变 |
| Legacy Smart | 保留旧版 Smart UV 行为，便于回归和特殊资产兜底 |

## 5. 推荐起始参数

| 设置 | 建议值 |
| --- | ---: |
| Auto Hard Edge Angle | 70° |
| Panel Flatness | 5° |
| Initial Angle | 70° |
| Merge Search Angle | 130° |
| P95 Stretch | 1.50 |
| Max Stretch | 3.0 |
| Merge Tests | 500（首次真实资产可先用 40） |
| Small Island Faces | 12 |
| Small Island Area | 0.004 |
| Developable Angle | 55° |
| Band Merge Bonus | 0.40 |
| Island Margin | 0.002 |

推荐同时开启：

- `Preserve Existing Seams`
- `Respect Material Borders`
- `Align Long Islands`

导入生产资产时默认关闭 `Cut Sharp Edges`。许多硬表面模型的 Sharp 数据远多于
合理 UV 切线，全部转换会造成严重碎岛。

## 6. 推荐工作流

1. 应用或核对 Scale。
2. 清理零面积面、重复点、重复表面、非流形边、错误法线和无效 N-gon。
3. 在纹理必须断开的隐蔽处、结构转折处和人工指定位置标记 Seam。
4. 从零展开时设置 `Initial UV Mode=Auto`；已有可用大岛、只需整理碎岛和重复
   结构时设置 `Initial UV Mode=Refine Layout`。确认运行摘要中的 UV Usage 为
   `UNIQUE`，并使用 `Profile=Weapon / Prop`。
5. 首次真实资产把 `Merge Tests` 降至 40，确认合同和质量门禁行为。
6. 运行 `Optimize Active Object UV`。
7. 非 Unique 应看到 `mode PRESERVE_LAYOUT`，并确认 UV、Seam、层身份没有变化。
8. Unique 成功后检查 0-1、overlap、winding、退化、岛数、拉伸、长条方向和纹理密度。
9. L/R、LOD0/LOD1、active/render 不同的对象分别验收。
10. 质量稳定后再逐步提高 `Merge Tests`。

## 7. 硬表面边界规则

- Mesh 边界和非流形边属于结构切线。
- 保留的 Artist Seam 是锁定切线，不会被合并。
- 启用 `Respect Material Borders` 时，材质边界是切线。
- 圆柱端盖边界和一条稳定纵向开口是切线；规则封闭圆柱预期为一个侧壁岛和两个端盖岛。
- Sharp 只有在启用 `Cut Sharp Edges` 后才会强制切开。
- 几何硬角、凹边、倒角、可见性和可展带影响候选优先级，但不能绕过拓扑、拉伸、winding 和 overlap 检查。
- `Panel Flatness` 决定生长区域是否归为平面面板。
- `Align Long Islands` 会把通过检查的长岛对齐到水平或垂直方向。

## 8. Unique 最终质量门禁

Unique / Bake 结果必须同时满足：

- 所有 UV 坐标有限且位于 0-1；
- 源三角形和 UV 三角形都不退化；
- 所有三角形保持一致的正 winding；
- 岛内和岛间都没有正面积 overlap；
- 每条真实 UV 不连续边都有对应 Seam。

插件在 Pack 前显式全选 UV，并使用 active UDIM。普通 Pack 仍产生重叠时会尝试
AABB 安全打包。最终仍失败则取消操作并恢复原状态。

`Reject UV Overlap` 只控制候选合并；即使关闭，Unique 最终门禁仍然要求零重叠。

## 9. UV 层与事务回滚

重建模式只改运行前的 active UV。其他 UV 层以及 active/render 身份保持不变。
失败时会恢复：

- 所有 UV 层、坐标、pin 和 UV 选择；
- active/render UV 身份；
- Seam、Mesh 选择和隐藏状态；
- 运行前的 `vuv_detail_score` 属性。

`Collapse Hidden Overrides` 默认关闭。Unique 合同不要开启，否则被收拢的纹理面会
故意产生退化 UV。

## 10. 可见性与 Mapping Viewer

插件仍支持六相机、已选相机、Hybrid、球面采样和 Exterior Flood 可见性分析，
并允许把面覆盖为 Important、Hidden 或 Auto。

`Interactive Mapping > Export Viewer` 可导出独立 HTML，在 UV 与 3D 表面之间定位，
无需外部网络服务。

## 11. 已知限制

- 当前不是生产批处理器，只处理活动 Mesh。
- 严重源退化、重复表面、非流形、错误法线和复杂凹 N-gon 可能被安全拒绝。
- 自动命名识别依赖标准 token，特殊命名应显式指定 UV Usage。
- 当前真实 Unique 抽样为 1/5 通过；拒绝表示原状态已恢复，不代表展开成功。
- 自动结果仍需美术复核，不能未经检查直接覆盖生产 UV。

## 12. 0.5.5 增量说明（0.5.4 历史内容保持不变）

本节只记录 0.5.5 在结构排布、零碎岛整理和失败修复上的增量。上文的 0.5.4
参数、工作流和真实资产通过比例均保留为历史记录，不代表新增的 0.5.5 实测结论。

### 12.1 Refine Layout：优化已有 Unique UV

`Initial UV Mode=Refine Layout` 用于已有 UV 的二次整理。适用前提是 active UV
已经有合理的大岛，只是重复结构排布分散，或附近残留较多零碎小岛。它不是
`Preserve Layout`，运行前仍应保存副本。

该模式会先从 active UV 推导现有图表边界和对应 Seam，然后读取硬表面结构约束。
它不会执行初始全局 `Unwrap` 或 `Smart UV Project`，也跳过常规的图表生长合并，
因此有效大岛不会被全局重新切开。后续处理顺序为：

1. 对运行前已经折叠或退化的图表，只在必要时尝试限定范围的局部修复；
2. 仅跨符合条件的真实共享 Mesh 边尝试小岛缝合；
3. 统一岛尺度和方向，并对全部岛执行全局 Pack；
4. 把重复、左右镜像、绕轴旋转结构统一方向并作为邻近块重排；没有安全缝合边的
   小碎片只按模型空间位置附着到附近主体，仍保持独立和间距；
5. 在结构排布后再次执行完整 Unique 门禁，任何失败都回滚到运行前状态。

“保留现有大岛”只表示保留有效图表的边界与连通关系，不表示 UV 坐标、像素密度
或排布位置不变。安全小岛缝合可能局部更新它所依附的主体图表；全局 Pack 和结构
排布也会改变岛的缩放、90 度方向和位置。若必须逐坐标保持原布局，应改用
`Preserve Layout`，但该模式不会清理碎岛或重新分组。

此模式只建议用于 Unique / Bake。Trim Sheet、Info Atlas、LED/VFX 应保持
`Initial UV Mode=Auto`，由合同自动进入 `PRESERVE_LAYOUT`，不要用 Refine Layout
覆盖刻意堆叠或越 tile 的布局。

### 12.2 对称与重复结构排布

- 同构、左右镜像、绕轴旋转和重复机械结构会获得一致的 UV 方向，并尽量邻近排布。
- 该过程只使用刚性旋转和平移，不镜像 UV 坐标、不堆叠、不制造重叠，也不焊接
  彼此断开的 Mesh 结构。
- “重复关系组”只提供共同方向和排序证据；“实际布局组”才决定哪些岛作为一个
  邻近块排布。重复的小碎片不会成为全局锚点，而是按模型空间附着到附近的有效
  主体，避免把模型上相距很远的相同薄片聚到一起。

使用 0.5.5 时保持 `Group Related Islands / 关联岛分组` 开启。结果仍应人工检查
左右件、环形阵列和重复模组是否同向、相邻且互不重叠。

### 12.3 零碎小岛整理与安全缝合

- 没有真实共享 Mesh 拓扑边的小岛，只按模型空间距离归入附近布局组；Pack 后
  仍是保留间距、互不重叠的独立 UV 岛。“就近合并”在这里表示视觉排布成组，
  不表示焊接拓扑或共享 UV 坐标。
- 只有两个岛共享真实拓扑边时才尝试 `Stitch Small Islands / 缝合小岛`。Artist
  Seam、材质边界、受保护 Sharp、锁定结构切线和非流形边都不能跨越。
- 缝合候选还必须通过连通 disk、有限坐标、拉伸、正 winding、非退化和正面积
  overlap 门禁。候选失败时只恢复该候选，不会强行接受有风险的拼接。

### 12.4 残留坏图表的局部修复

最终检查仍发现折叠或退化图表时，0.5.5 只处理这些残留坏图表：在图表的面邻接
图中保留一棵生成树，把其余成环连接设为局部切线，然后重新展开；局部展开仍无效
时可逐面投影兜底。无关的有效图表不会被全局重新切碎。任何修复都必须重新通过
局部 winding 与退化检查，不能保证每个坏图表都可自动修复。

对 N-gon 三角化产生的近共线耳三角，刚性 Pack 变换可能让极小数值误差表现为
翻面或退化。0.5.5 只对源几何确属近共线、且 UV 也处于数值阈值附近的三角做
有界的正 winding 稳定；完整图表复检不通过就撤销。真实零面积源几何仍会被拒绝。

### 12.5 完整事务回滚

任何未解决的最终门禁、局部修复或结构排布失败都会取消整次优化，并恢复运行前的：

- 所有 UV 层及其坐标、pin 和 UV 选择；
- active/render UV 层身份；
- Seam、Mesh 选择与隐藏状态；
- `vuv_detail_score` 等分析属性。

因此报错表示插件没有提交不满足 Unique 合同的结果，不应解读为自动展开成功或
生产可用性证明。
