# Visibility Aware UV Optimizer 0.5.8 结构连续与方向验证

> 状态：受控试验版的最终验证记录。Blender 3.3.5 / 5.2.0 LTS 定向回归、完整 16 项发布矩阵和解压 ZIP strict smoke 均已通过；本文不代表全场景生产成熟度承诺。

## 本轮目标

0.5.8 针对硬表面模型的三个问题继续收敛：同一机械结构被切成过多小岛、重复或对称结构的纹理正反不一致、方向约束后排布利用率不足。实现必须保持 Unique UV 的有限坐标、0-1、正 winding、零退化和零正面积重叠门禁，不能为减少岛数而跨越 Seam、材质、硬切线或不连通拓扑。

## 方向合同

### Repeat-local signed semantic frame

局部有符号语义帧只对以下关系类型生效：

- `MIRROR`
- `ROTATIONAL`
- `REPEATED`

候选组还必须同时满足 exact signature，并为每个参与成员找到可靠的 intrinsic landmark。满足条件后，repeat-local frame 优先于全局 Object/World frame；UV 写回只允许整岛刚性旋转，不允许反射、逐面扭曲或负统一缩放。因此左右镜像件可以取得一致的纹理语义方向，但不会把其中一侧 UV 镜像，也不会改变正 winding。

exact signature 或 intrinsic landmark 的证据不足时，不猜测局部正反，直接使用已有的 global Object/World fallback。全局合同仍负责普通岛和所有不满足局部条件的关系组。

PCA 主轴与轮廓 heading 只作为 presentation metric，用于描述岛看起来是否横向、竖向或规整；它们是无符号或易受近对称轮廓影响的统计量，不能单独定义 repeat-local signed semantic frame，也不能证明 180 度正反一致。

### 审计口径

方向报告必须区分：

1. local effective audit：对满足条件的 repeat-local 成员按局部语义帧验收；
2. raw global audit：保留全局 Object/World 轴下的诊断值，供解释 fallback 和局部覆盖关系。

本场景 raw global audit 出现 46 个 misaligned 和 6 个 quarter-turn。这些成员具有通过门禁的 repeat-local override，因此不是最终有效方向错误。最终 signed checker error groups 为 0。

## 结构连续策略

- 小岛链预算只统计新增吸收的碎片，不把 100 面以上的主体面板计入 `small_chain_max_faces`。
- 只有共享真实 Mesh 拓扑边的 UV 岛才可能缝合；拓扑断开的螺栓、端盖和单面组件只能相邻、同向排布，不能伪造成一个连续 UV 岛。
- 链扩展仍受 Seam、材质、forced cut、翻转、拉伸、内部重叠、累计吸收次数和模型空间直径约束。
- 严格平面且完整 U/V frame 失败的岛可尝试受控重参数化；候选必须保持 face-set、中心、面积和正 winding，并再次通过重叠与拉伸检查。
- source-cell 在保留方向与源结构关系的前提下比较规则网格和不旋转的可变尺寸 shelf，禁止用 90 度旋转换覆盖率。

Body 输出仍有 235 个单面 UV 岛，其中 110 个是拓扑上独立的单面 Mesh 组件。这 110 个岛不是小岛清理遗漏：没有共享拓扑边时，安全规则不允许将它们焊成连续岛。

更激进的碎岛参数曾作为候选测试。候选分别留下 1 个或 11 个 folded/degenerate chart，Unique 严格门禁拒绝这些结果并回退到当前保守参数；没有为了更少岛数放松最终质量合同。

## Blender 5.2 拼接武器实测

测试使用真实 Operator，在源 `VUV_Grouped_Final` 的副本层上生成 0.5.8 结果，并在保存后重新打开文件复审。

| 对象 | UV 岛 | Polygon coverage | Overlap | Degenerate | Negative | Planar frame failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Body | `680 -> 508` | `15.5157301%` | 0 | 0 | 0 | 0 |
| Gun Head | `203 -> 152` | `23.1494795%` | 0 | 0 | 0 | 0 |
| Bullet | `4 -> 4` | `51.6135375%` | 0 | 0 | 0 | 0 |

Repeat-local 审计结果：

- 总关系组：59；
- local eligible：24 组、48 个成员；
- global fallback：35 组；
- local residual P95：`0.0012766°`；
- local residual max：`0.0016252°`；
- signed checker error groups：0。

三个对象保存后重开均保持 0 overlap、0 degenerate、0 negative 和 0 planar-frame failure，reopen audit 通过。

## 双版本状态

Blender 3.3.5 与 5.2.0 LTS 的定向回归已通过，覆盖 repeat-local 方向、全局 fallback、刚性旋转、禁止反射和方向审计口径。完整 16 项发布矩阵和解压 ZIP strict smoke 也在两个版本上通过。

## Blender 官方依据

- [Align Rotation - Geometry](https://docs.blender.org/manual/en/5.2/modeling/meshes/uv/editing.html#align-rotation)：Blender 的 Geometry 方法说明了基于几何轴的 UV 岛方向对齐；插件在此基础上增加 repeat-local 的适用门禁和有符号审计。
- [Pack Islands](https://docs.blender.org/manual/en/5.2/modeling/meshes/uv/editing.html#pack-islands)：官方 Pack 可缩放、平移并按设置旋转 UV 岛；本插件在方向锁定路径禁用 Pack 旋转，以免覆盖已验收的方向帧。
- [Seams](https://docs.blender.org/manual/en/5.2/modeling/meshes/uv/unwrapping/seams.html)：Seam 用于限制并引导展开；本插件将 Artist Seam 作为不得跨越的结构边界。

这些官方功能是算法语义和操作边界的依据，不等于 Blender 官方为本插件的自动分组策略或生产成熟度背书。

## 已知边界

- 本轮真实数据来自一份拼接武器场景，不能外推为所有硬表面资产均会一次成功。
- 拓扑断开的相似小面只能分组排布，不能在 Unique UV 中凭空间邻近自动焊接。
- repeat-local 只覆盖 exact signature + reliable intrinsic landmark；近似拓扑、地标歧义或普通岛必须走 global fallback。
- 严格门禁可能拒绝并回滚激进候选；这是当前试验版的安全行为，不代表成功展开。
- 当前通过结论只覆盖已定义的 16 项发布矩阵与实测场景，不外推为所有复杂资产的成功率保证。
