# Visibility Aware UV Optimizer 0.5.2 验证记录

验证日期：`2026-08-25`

## 发布信息

- 插件版本：`0.5.2`
- 安装包：`release/Visibility_Aware_UV_Optimizer_0.5.2.zip`
- 安装包大小：`47544` 字节
- SHA-256：
  `87717D9780C1206B21F08426AA61DE4078FAEBB7DEFAEE67BB21EC6A0C6286DD`

## 验证环境

- Windows
- Blender `3.3.5`
- 插件最低版本声明：Blender `3.3`

Blender 5.1 用户插件目录已同步，并完成源码文件哈希一致性检查；本次环境没有
Blender 5.1 可执行程序，因此没有执行 5.1 运行时回归。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| Python 静态编译 | 通过 |
| 完整 Blender 回归 | `ALL_TESTS_OK` |
| 红面原点测试 | `HIDDEN_UV_ORIGIN_TEST_OK` |
| 方向探针纯 Python 测试 | `PROBE_EVIDENCE_TEST_OK` |
| 方向探针 Blender 测试 | `PROBE_BLENDER_TEST_OK` |
| Blender 安装副本测试 | `INSTALLED_ADDON_TEST_OK` |
| 3.3 安装目录与源码哈希 | 12 个文件，0 个差异 |
| 5.1 安装目录与源码哈希 | 12 个文件，0 个差异 |

## 红面规则实测

测试模型中一个红色四边面包含 4 个 UV 环。优化后实测：

```text
(0.0, 0.0)
(0.0, 0.0)
(0.0, 0.0)
(0.0, 0.0)
```

同次测试中，可见 UV 保持有效面积，跨度约为：

```text
0.6668 x 0.9995
```

说明红面收拢不会再通过预留角落二次缩放绿色和黄色 UV。

## 安装包结构

ZIP 顶层为 `visibility_uv_optimizer/`，包含 12 个发布文件：

```text
__init__.py
camera_utils.py
interactive_export.py
mapping_viewer.html
operators.py
overlay.py
probe_analysis.py
properties.py
README.md
ui.py
uv_optimize.py
visibility.py
```

该结构可直接通过 Blender 的 `Install` 功能安装。
