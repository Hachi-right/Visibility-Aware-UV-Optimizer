# 迭代 VUV 硬表面 UV 方向一致性

开始日期: 2026-08-31

## 用户原始需求

继续修改 Visibility Aware UV Optimizer：棋盘格检查发现 UV 展开后方向不一致，学习可靠方法并继续迭代。

## 目标

为硬表面 UV 岛建立稳定的模型空间正方向合同，消除重复件、镜像件和普通岛的 90 度侧转与 180 度倒置，并通过 Blender 3.3/5.2 回归和场景验证。

## 当前状态

- 2026-09-01 根据用户对真实棋盘格与 UV 布局的复核重新打开任务：旧结果虽然
  通过方向数值门禁，但仍存在结构拆碎、同构件分散、排布歪斜和部分方向漂移。
- 当前验收升级为三重合同：源结构连续分区不可被布局阶段改变；同构/重复结构
  必须共享有符号方向；结构组内部采用确定性邻近行列/rack，且全程保持正 winding。
- Body 暴露弱轴组在布局前后换轴；Gun Head 的 42 面连续岛可保留，但 float32
  写回会令两个近共线三角翻面。正在以真实场景作为阻断样本修复。
- 旧 0.5.6 ZIP 和 v24/v26 场景均不再视为本轮最终交付；当前已完成源码增强、安装包重建、双版本 smoke 和 GitLab 推送。

## 团队经验引用

- [x] 已读 `项目大脑/团队经验/INDEX.md`；当前为模板空库，无可点读条目。
- 相关条文 ID：暂无
- 与本任务关联：本轮以官方 Blender UV Geometry 对齐语义和插件实测证据为依据。

## 个人经验引用

- [x] 当前 author 尚无 `经验/INDEX.md` 与 entries。
- 相关条文 ID：暂无
- 与本任务关联：暂无。

## 团队公理引用

- [x] 已读 `项目大脑/公理/index.md`「任务开始路由」；当前为模板空库。
- 推荐公理 ID：暂无
- 与本任务关联：暂无。

## 查询规划卡

- 候选入口：`uv_group_layout.py` 的岛分析、方向策略、刚性布局与打包；`uv_optimize.py` 的 Unique 原生 Pack 调用。
- 关键词/别名：`orientation`、`principal_axis`、`direction_vector`、`quarter_turn`、`pack_islands`、`rotate_method`。
- 反向追踪：从最终 UV 写入与审计向前追踪所有允许 90/180 度旋转的阶段。
- 排除项：不改硬表面切缝分类和 Unique 拓扑修复，除非方向验证证明它们直接破坏正向合同。
- 待验证问题：模型空间正轴如何映射到 UV +V；轴退化如何回退；镜像/重复件如何共享符号；Pack 后是否仍保向。

## 实现摘要

- `hard_surface.py` 以三角扇构造 UV Jacobian，按 3D 三角面积累计 `dP/du` 与
  `dP/dv`，把选定正轴映射到 UV `+V`，保留完整 360 度符号。
- 支持 `OBJECT` 与 `WORLD`；`AUTO` 固定按 `+Z -> +X -> +Y`，仅在相对导数
  信号低于 `1e-4` 时回退。Jacobian、3D 面积与轴信号均使用相对退化阈值。
- 方向锁开启时，原生 Pack、修复 AABB Pack 和结构 Pack 均禁用旋转。局部修复、
  逐面投影和尺度处理后重新对齐。
- Adaptive 候选回放后重新分析 float32 UV，严格比较全部岛 face-set 并绑定候选
  原选轴；无效结果或异常恢复源 UV。
- 关闭关联岛分组时，主 Pack 前记录全部岛的 `face-set -> (axis, space)` 合同；
  修复拆岛后清空并重建，最终对象态按 fixed axis 重算。显式轴丢失、`AUTO`
  换轴或岛身份变化均拒绝并触发整事务回滚。
- `AUTO` 要求全部合法岛可解析；手动轴允许法线与目标轴平行的盖面先天
  unresolved，但已解析岛不得在回放后失去该轴。

## 外部依据

- Blender Geometry Align：
  <https://github.com/blender/blender/blob/main/scripts/startup/bl_operators/uvcalc_transform.py>
- Blender UV Pack 旋转策略：
  <https://github.com/blender/blender/blob/main/source/blender/geometry/intern/uv_pack.cc>
- LSCM 参数化的旋转自由度：
  <https://www.cs.jhu.edu/~misha/ReadingSeminar/Papers/Levy02.pdf>

## 验收结果

| 检查 | 结果 | 覆盖范围 | 未覆盖风险 |
| --- | --- | --- | --- |
| 修改 Python `py_compile` | 21/21 通过 | 插件与测试全部 Python 文件 | 不代替 Blender 运行时 |
| Blender 3.3.5 / 5.2.0 方向几何 | 两版均 `VUV_DIRECTED_GEOMETRY_OK` | 正反、斜面、回退、极小岛、Object/World | 无 |
| Blender 3.3.5 / 5.2.0 完整方向管线 | 两版均 `VUV_DIRECTION_PIPELINE_OK` | 分组开关、原选轴保持、手动轴丢失与 Auto 换轴回滚 | 无 |
| Blender 3.3.5 / 5.2.0 分组布局 | 两版均 `VUV_GROUP_LAYOUT_REGRESSION_OK` | 重复/镜像/旋转结构、小岛 owner、Adaptive 回放 | 无 |
| Blender 3.3.5 / 5.2.0 Unique 修复 | 两版均 `VUV_UNIQUE_LOCAL_REPAIR_OK` | 修复后重定向、局部回退、事务恢复 | 无 |
| Blender 3.3.5 / 5.2.0 strict smoke | 两版均 `VUV_055_SMOKE_OK` | 0.5.5 继承合同、Operator RNA、失败注入 | 测试标记沿用兼容脚本名称 |
| 新 ZIP 解压后 strict smoke | 两版均 `VUV_055_SMOKE_OK`，版本 `[0,5,6]` | 当前安装包真实导入链 | 无 |
| 拼接武器 `VUV_Directed_v24` | 887/887 upright，0 unresolved/misaligned/opposite/quarter-turn | Body 680、Gun Head 203、Bullet 4 | Blender 5.2 格式场景不能由 3.3 打开 |
| 场景最大残差 | Body `0.004785°`；Gun Head `0.002105°`；Bullet `0.000022°` | `3°` 门槛内的真实资产方向 | 场景原有缺失骨骼父级 warning，不影响 UV |
| 场景完整性 | 拓扑、Seam、源 UV 未变；目标 UV 在 0-1、正 winding、零退化、零重叠 | 三个目标 Mesh | 场景只读打开，未保存 |
| 0.5.6 安装包 | 15/15 白名单文件与源码哈希一致；220220 字节 | 唯一顶层、无缓存、无重复 entry | 无 |
| 0.5.6 ZIP SHA-256 | `7022CAC248B4EBFA74FE726A89EA1B3462D9630D9D828859F0C124B3202E65B6` | 当前重建安装包 | 源码再变更必须重建 |
| `release/SHA256SUMS.txt` | 8/8 匹配 | 历史包、0.5.6 ZIP 与四份历史 smoke | 无 |
| 确定性重复构建 | 重建哈希与正式 ZIP 完全一致 | 固定白名单、顺序和 entry 时间戳 | 无 |
| `git diff --check` | 通过 | 空白、冲突标记和补丁格式 | 无 |
| 发布范围审计 | 源码、文档、安装包和轻量验证结果；实验 sandbox 已忽略 | 无 Blend、图片、场景审计、MCP 脚本或缓存 | GitLab 远端 commit 推送后复核 |
| 项目大脑 healthcheck | `errors=12 warnings=0`，本任务无新增错误 | 当前任务五件套、代码地图和索引 | 12 项均为入口/hook/公共索引覆盖等既有历史债务 |

## 交付与范围

- 插件 ZIP：`release/Visibility_Aware_UV_Optimizer_0.5.6_HardSurface.zip`。
- 方向说明：`docs/Visibility_Aware_UV_Optimizer_方向一致性_0.5.6.md`。
- 实测场景：工作区 `release/VUV_0.5.6_DirectedScene_v24/拼接武器_VUV_方向一致_v24.blend`，
  UV 图层 `VUV_Directed_v24`；场景及 audit 不纳入本仓库待提交范围。
- 不纳入 `.blend/.blend1`、场景 manifest、PNG/SVG、临时审计/MCP 脚本、缓存。
- 本轮已按用户“上传到 GitLab”要求完成提交与推送；目标远程为 `origin`（`gitlab2.seasungame.com/AIGC/MB-AIGC.git`），分支为 `uv展开`。
