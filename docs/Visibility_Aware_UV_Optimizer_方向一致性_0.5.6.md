# Visibility Aware UV Optimizer 0.5.6 方向一致性

## 目标

硬表面 UV 的“横平竖直”只解决无向直线，无法区分 180 度正反。0.5.6 增加
有符号方向合同：选定的模型正轴必须映射到 UV `+V`，从而让带字母、编号或箭头的
方向棋盘格保持一致。

## 使用方式

在 `Hard Surface` 设置中保持以下选项开启：

- `Lock Texture Direction / 锁定纹理方向`：开启方向合同。
- `Direction Space / 方向空间`：单个零件通常选 `Object`；多个已正确摆放的零件
  需要共同“向上”时选 `World`。
- `Texture Up Axis / 纹理向上轴`：默认 `Auto`。自动顺序固定为
  `+Z -> +X -> +Y`，每个被选中的正轴都映射到 UV `+V`。

`Auto` 先按 `+Z -> +X -> +Y` 建立候选，但不会盲目采用一个仅在数值上存在的
弱候选。投影低于 `1e-4`、正反双峰、有效面占比不足或局部方向不连贯的候选会被
排除。关联结构需要共同方向时，以组内最弱成员的稳定性和置信度选择共同轴；
`1e-4` 对称平局带内才按 `Z/X/Y` 顺序决胜，防止 float32 微扰导致重开后换轴。

默认 `Auto` 是全岛严格合同：每个通过 Unique 几何门禁的岛都必须解析出稳定轴，
否则候选无效并恢复源 UV。手动选择 `X/Y/Z` 时，只约束该轴在岛切平面上有分量的
区域；法线与该轴平行的盖面没有几何正反定义，可保持 unresolved。若某个岛在候选
阶段已经解析出轴，但最终回放后丢失该轴，即使使用手动模式也会失败回滚。

## 工作原理

实现与 Blender `Align Rotation > Geometry` 一致：把每个面拆成三角扇，求 UV
Jacobian 的逆，按三角形 3D 面积累计 `dP/du` 与 `dP/dv`，再用所选正轴在两个
导数上的有符号分量计算 `atan2`。该角度覆盖完整 360 度，因此 0 度与 180 度
不会被当成同一方向。Jacobian 与 3D 面积退化均按边长乘积的相对阈值判断，所以
将一个合法 UV 岛统一缩小到很小不会改变它的选轴和方向。

方向对齐后，所有 Pack 阶段都关闭旋转。局部 UV 修复或逐面投影可能重新引入
任意方向，所以修复后会再次执行方向对齐；Adaptive 候选写回 float32 UV 后还会
重新分析、绑定候选阶段的原选轴、审计和评分，不能复用写回前的旧报告；该阶段
返回无效结果或抛出异常都会恢复源 UV。`关联岛分组` 只控制结构排布；即使关闭，
方向锁定仍会在 Pack 前记录每个岛的完整 face-set、选轴和方向空间，最终只读门禁
严格匹配全部岛并按原选轴重算。已解析轴丢失、`Auto` 偷换到另一轴或岛身份变化
都会使整次事务回滚。

结构方向按照固定层级解析：`STRUCTURE_AFFINITY` 先锁定真实拓扑关系，随后
`OWNER_ATTACHMENT` 尽量统一主体与附属小岛，再锁定左右/旋转/重复件 cohort，
最后执行一次 owner 调和。拓扑和重复件属于硬合同；owner 关系属于软合同，不能
用多数小岛覆盖硬锁。若同一 owner 下的面没有共同切向模型轴，就确定性拆成多个
方向 cohort，而不是牺牲低拉伸或重复件一致性强制转正。

排布阶段把每个结构/重复组件作为刚性矩形块。无旋转 MaxRects-BSSF 只允许平移
整个块，必须保持尺寸、间距、分区和零重叠，并且最长边不得超过 shelf 基线；
任一条件失败立即使用 shelf。这样紧凑排布不会再次破坏 `+V` 方向合同。

## 拼接武器 v31 实测

场景：`拼接武器_VUV_结构连续方向统一_v31.blend`，目标图层：
`VUV_StructuredDirection_v31`。

| 对象 | 岛数变化 | 可解析 | 侧转/倒置 | 最大残差 | 多边形覆盖率 | owner 同轴率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bullet | 4 -> 4 | 4 | 0 / 0 | 0.000031 度 | 52.56% | 无附属岛 |
| Body | 680 -> 508 | 508 | 0 / 0 | 0.006014 度 | 19.45% | 70.35% |
| Gun Head | 203 -> 160 | 160 | 0 / 0 | 0.002244 度 | 34.88% | 94.87% |

Body 安全合并 `172` 个碎岛，Gun Head 合并 `43` 个；重复件组方向通过率 `100%`。
完整性审计同时确认三个对象的 Mesh 拓扑、连接关系、Seam 和源图层
`VUV_Grouped_Final` 均未变化；目标图层全部位于 0-1，正 winding、零退化、
零正面积重叠，保存后 face-set 分区完全一致，并已设为 active/render UV。

## 验证范围

- Blender 3.3.5 与 5.2.0 LTS：Geometry 方向、完整优化管线、分组布局、局部修复
  和严格 0.5.5 兼容 smoke 均通过。
- 覆盖 180 度倒置、10 度斜面、近法线退化回退、World 空间且关闭分组、修复后
  重定向、极小 UV 岛缩放不变性、Auto 全岛解析门禁、候选选轴保持、Adaptive
  最终回放异常回滚、关闭分组时手动轴丢失与 Auto 换轴回滚，以及 float32 共边误报。
- 方向棋盘格必须包含字母、编号或箭头。纯双色方格具有 180 度对称性，不能证明
  正反一致。

## 发布物

- 安装包：`release/Visibility_Aware_UV_Optimizer_0.5.6_HardSurface.zip`
- 大小：`220220` 字节
- SHA-256：`7022CAC248B4EBFA74FE726A89EA1B3462D9630D9D828859F0C124B3202E65B6`
- 内容：唯一顶层 `visibility_uv_optimizer/`，精确 15 个白名单文件，逐文件与
  `addons/visibility_uv_optimizer/` 的 SHA-256 一致，无缓存文件。

## 依据

- Blender `Align Rotation` Geometry 实现：
  <https://github.com/blender/blender/blob/main/scripts/startup/bl_operators/uvcalc_transform.py>
- Blender UV Pack 实现与旋转策略：
  <https://github.com/blender/blender/blob/main/source/blender/geometry/intern/uv_pack.cc>
- LSCM 原论文说明参数化具有平移、旋转和缩放自由度：
  <https://www.cs.jhu.edu/~misha/ReadingSeminar/Papers/Levy02.pdf>
