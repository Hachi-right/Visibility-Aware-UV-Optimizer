# Visibility Aware UV Optimizer

Blender 可见性分析与低碎片 UV 优化插件。

当前发布版本：`0.5.2`

## 下载与安装

直接下载：

- [Visibility_Aware_UV_Optimizer_0.5.2.zip](release/Visibility_Aware_UV_Optimizer_0.5.2.zip)

在 Blender 中打开：

`Edit > Preferences > Add-ons > Install`

选择 ZIP 后启用 `Visibility Aware UV Optimizer`。插件面板位于：

`3D Viewport > Sidebar(N) > UV Optimizer`

完整操作说明：

- [Visibility Aware UV Optimizer 使用教程](docs/Visibility_Aware_UV_Optimizer_使用教程.md)
- [0.5.2 验证记录](docs/Visibility_Aware_UV_Optimizer_验证记录_0.5.2.md)

## 0.5.2 核心规则

- 红色 Hidden 面不再保留独立 UV 面积。
- 所有红面 UV 环统一收拢为一个点。
- 固定坐标为 UV 原点 `(0, 0)`。
- 已删除 `Hidden UV Scale` 和 `Hidden Corner Size` 两个无效调节项。
- 绿色和黄色 UV 不再为红面预留右上角区域。

## 推荐工作流

1. 复制一份待处理模型作为备份。
2. 选择参与遮挡分析的网格对象。
3. 在 `Visibility` 中选择分析方式并执行 `Analyze Visibility`。
4. 用热力图检查绿、黄、红面。
5. 在编辑模式中用 `Important / Auto / Hidden` 修正特殊面。
6. 将需要优化的对象设为活动对象。
7. 执行 `Optimize Active Object UV`。
8. 检查红面 UV 是否全部位于 `(0, 0)`，再检查可见区域接缝和拉伸。

## 仓库结构

```text
addons/visibility_uv_optimizer/        插件源码
release/                               Blender 安装包与校验值
docs/                                  中文教程和验证记录
```

## 兼容性与验证

- 插件最低版本声明：Blender `3.3`
- 完整运行验证：Blender `3.3.5`
- Blender `5.1` 插件目录已同步验证文件一致性，但本次没有可用的 5.1
  可执行程序进行运行时回归

已通过：

- Python 静态编译检查
- 完整 Blender 插件回归
- 红面 UV 原点回归
- 方向探针数据测试
- 方向探针 Blender 测试
- Blender 用户插件目录安装测试

## 安装包校验

```text
SHA-256  87717D9780C1206B21F08426AA61DE4078FAEBB7DEFAEE67BB21EC6A0C6286DD
```

## 注意

优化会重写活动 UV Map 和接缝标记。正式资产建议先复制对象或保存新版本，
并在确认热力图分类后再执行 UV 优化。
