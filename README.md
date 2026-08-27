# Visibility Aware UV Optimizer

面向 Blender 硬表面游戏资产的可见性分析与保守自动 UV 插件。

当前试验版本：`0.5.5`

## 下载与安装

- [Visibility_Aware_UV_Optimizer_0.5.5_HardSurface.zip](release/Visibility_Aware_UV_Optimizer_0.5.5_HardSurface.zip)

在 Blender 中打开 `Edit > Preferences > Add-ons > Install`，选择 ZIP 并启用
`Visibility Aware UV Optimizer`。面板位于：

`3D Viewport > Sidebar(N) > UV Optimizer`

## 0.5.5 核心变化

- 新增 `Refine Layout / 优化现有布局`：适合已经有可用大岛、但小面零碎且重复
  结构排布分散的 Unique UV。它沿现有 UV 推导图表边界，跳过初始全局
  `Unwrap`、`Smart UV Project` 和常规大岛合并，不会重新切碎有效大岛；随后只
  尝试安全的小岛缝合，再做全局 Pack、结构分组排布和最终 Unique 门禁。
  这里保留的是图表边界，不是锁定原坐标；岛的尺度、方向和位置仍会变化，坏图表
  也可能触发限定范围的局部修复。
- 同构、左右镜像、绕轴旋转和重复机械结构按 owner cohort 解析共同 UV 方向。
  每个主体 owner 与归属于它的小碎片组成不可拆分的原子 cell；同一 cohort 的
  owner cells 必须同向且紧邻排布。这一步只做刚性旋转和平移，不反射、镜像、
  堆叠或焊接 UV。即使同一 RepeatGroup 跨越多个 LayoutGroup，相关布局块也会
  先组成亲和组件，避免被无关 UV 块隔开。
- 零碎小面优先依据共享 Mesh 拓扑归属主体 owner；没有可用拓扑关系时，再按
  模型空间距离选择，材质一致性只作为第三优先级证据。附属碎片随主体 cell 一起
  打包，避免脱离模型附近结构散落到 UV 视图其他区域。
- 结构排布必须同时满足 cohort 紧邻、配置间距和零正面积重叠；确定性布局与有限
  回溯仍无法满足时，本次优化硬失败并恢复操作前状态，不会提交分散或重叠的折中结果。
- 小岛只有在两个 UV 岛共享真实 Mesh 拓扑边时才允许尝试缝合。Artist Seam、
  材质边界、受保护 Sharp、锁定结构切线和非流形边不会被跨越；候选还必须通过
  disk 拓扑、拉伸、winding、退化和正面积重叠门禁。
- 模型空间邻近但没有共享拓扑边的小岛只进入邻近分组，Pack 后仍是彼此分离、
  保留间距的独立 UV 岛。
- 最终门禁若仍发现折叠或退化图表，只对这些残留图表建立面邻接生成树切线并
  局部重展开；必要时逐面投影兜底，不会全局重新切碎无关图表。
- 对源几何近共线的 N-gon 耳三角，仅在数值不稳定时做最小正 winding 修正，
  并且只有重新通过局部门禁才保留结果；真实零面积源几何仍会被拒绝。
- `Stitch Small Islands / 缝合小岛` 与
  `Group Related Islands / 关联岛分组` 默认开启，可分别关闭。
- 初始模式包括 `Auto / Hard Surface / Marked Seams / Refine Layout /
  Preserve Layout / Legacy Smart`。
- 自动区分 Unique、Trim Sheet、Info Atlas、LED/VFX 四类 UV 合同。
- Artist Seam、材质边界、圆柱端盖和稳定纵缝作为硬表面结构边界。
- Sharp 默认只作为几何证据，不会全部强制切开，避免硬表面资产碎岛。
- 规则圆柱稳定为一个侧壁岛和两个端盖岛；长导轨优先保持连续岛。
- Unique 结果必须通过 0-1、有限坐标、正 winding、零退化、零正面积重叠和接缝一致性门禁。
- 未解决的展开、局部修复或结构排布失败会取消整次操作，并恢复全部 UV 层和
  坐标、active/render 身份、pin/选择、Seam、隐藏状态和分析属性。
- 修复 Blender 5.2 Pack 前 UV 未全选的问题，同时兼容 Blender 3.3。

## 0.5.5 默认参数

| 设置 | 默认值 |
| --- | ---: |
| 安全小岛缝合 | 开启 |
| 关联结构分组 | 开启 |
| 小岛最大面数 | `12` |
| Mesh 面积比 | `0.001` |
| UV 面积比 | `0.001` |
| 面积判断 | Mesh 与 UV 必须同时满足（`AND`） |
| 最小共享边比例 | `0.25` |
| 模型空间聚类半径 | 包围盒对角线的 `0.025` |
| 小岛缝合 P95 / Max 拉伸 | `1.35 / 2.0` |
| UV 岛间距 | `0.002` |

## 推荐硬表面流程

1. 复制生产资产或保存新版本。
2. 应用/确认 Scale，清理零面积面、重复点、非流形和无效 N-gon。
3. 在必须断开连续纹理的位置标记 Artist Seam。
4. 从零展开时设置 `Initial UV Mode=Auto`；已有可用大岛、只需整理碎岛和结构
   排布时设置 `Initial UV Mode=Refine Layout`。两种流程均建议使用
   `UV Usage=Auto`（默认命中 Unique）和 `Profile=Weapon / Prop`。
5. 保持 `Preserve Existing Seams`、`Respect Material Borders`、`Align Long Islands` 开启。
6. 导入资产默认关闭 `Cut Sharp Edges`，除非 Sharp 本来就是人工 UV 切线。
7. 在活动 Mesh 对象上运行优化；只有通过最终门禁的 Unique 结果才会提交。

详细资料：

- [0.5.4 使用说明与 0.5.5 增量](docs/Visibility_Aware_UV_Optimizer_硬表面使用说明_0.5.4.md)
- [0.5.5 验证记录](docs/Visibility_Aware_UV_Optimizer_验证记录_0.5.5.md)
- [0.5.4 验证记录](docs/Visibility_Aware_UV_Optimizer_验证记录_0.5.4.md)
- [0.5.2 历史教程](docs/Visibility_Aware_UV_Optimizer_使用教程.md)

## 兼容性与验证

- 支持范围：Blender `3.3` 至 `5.2`
- 发布回归版本：Blender `3.3.5`、Blender `5.2.0 LTS`
- 0.5.5 的结构分组只使用两版共有的刚性 UV 变换和确定性矩形排布；不依赖
  Blender 5.x 专属的镜像或重叠 Pack 行为。
- 0.5.4 Blender 5.2 ChopSword 历史实测：220 岛、4,246 个正 winding
  三角形、0 overlap、0 退化，全部位于 0-1。

当前版本仍是受控试验版。真实 Unique 抽样中另外四个复杂资产因折叠或退化图表被拒绝并完整回滚，因此不能描述为全场景生产批处理已成熟。

## 仓库结构

```text
addons/visibility_uv_optimizer/        插件源码
release/                               Blender 安装包、smoke 结果与校验值
docs/                                  中文说明和验证记录
```

## 安装包校验

发布包的当前 SHA-256 记录在 [release/SHA256SUMS.txt](release/SHA256SUMS.txt)。
