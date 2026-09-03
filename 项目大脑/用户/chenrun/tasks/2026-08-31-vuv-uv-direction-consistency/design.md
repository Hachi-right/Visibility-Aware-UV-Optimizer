# 迭代 VUV 硬表面 UV 方向一致性 设计决策

开始日期: 2026-08-31

本文件只记录本任务形成的稳定设计决策。过程、尝试、失败路径和验收流水写入 `process.md`。

## 决策记录

- 方向合同不能以增加 UV 岛数量为代价：布局写回前后必须比较完整 face-set 分区，
  任何意外拆岛或合岛都回滚；连续 welded loops 的修复不得跨 seam 或源岛。
- 结构优先级为“连续结构 -> 同构方向 -> 组内邻近/规整 -> 全局覆盖率”。Pack 只可
  对整岛或已验证兼容的结构组做刚性变换，不允许逐面投影来掩盖多面图表失败。
- 同构/重复件的轴选择按组内最弱成员的稳定度与置信度决定共同轴，避免弱 Z 信号
  抢占稳定 X/Y 轴；无共同轴时拆成最大兼容子组，不清空全部方向合同。
- 用 Blender Geometry Align 的面积加权有符号 UV Jacobian 建立方向合同：选中的
  模型正轴必须映射到 UV `+V`，不以无向 PCA 或最小包围盒代替 180 度正反。
- `Auto` 固定按 `+Z -> +X -> +Y` 解析，不按最大投影动态重排。只有前一轴的
  相对切平面信号低于 `1e-4` 才回退。
- UV Jacobian、3D 面积与轴信号均使用相对阈值，统一缩放合法小岛不得改变选轴。
- 方向锁定时所有原生 Pack、安全 AABB Pack 和结构 Pack 只允许平移与正统一缩放，
  不允许 Pack 再旋转。
- 局部修复和逐面投影后重新执行方向对齐；Adaptive float32 回放重新分析并绑定
  候选原选轴，禁止静默换轴。
- `Auto` 要求所有合法岛可解析。手动 `X/Y/Z` 允许法线与目标轴平行的盖面先天
  unresolved，但候选阶段已解析的岛不得在最终回放后丢失该轴。
- 关闭结构分组只跳过结构排布，不关闭方向门禁。无分组路径在 Pack 前记录全部岛的
  face-set、选轴和空间，最终严格匹配并以 fixed axis 重算。
- face-set 变化、已解析轴失效、方向超出 `3°` 或回放异常均视为事务失败并恢复源 UV。
- 验证 180 度正反必须使用带字母、编号或箭头的方向棋盘格；纯双色棋盘格不足以
  证明方向一致。
- 0.5.8 的 repeat-local signed semantic frame 只对 exact signature 且具有可靠
  intrinsic landmark 的 `MIRROR / ROTATIONAL / REPEATED` 生效，并优先于全局
  Object/World frame。写回只做整岛刚性旋转，不允许反射；条件不足时使用 global
  fallback，不以近似轮廓强猜语义方向。
- PCA 主轴和轮廓 heading 只属于 presentation metric；它们不提供稳定的有符号
  landmark，不能单独参与 180 度方向门禁。报告必须区分 local effective audit 与
  raw global metric，可靠局部覆盖下的全局 misaligned/quarter-turn 不计作有效错误。
- 岛数优化服从 Unique 正确性：激进小岛参数如果残留 folded/degenerate chart，必须
  回退到上一组通过门禁的保守参数；拓扑独立的单面 Mesh 组件不得用 UV 焊接伪造连续性。
