# lint / 格式检查

本文件用于任务验收的第一层检查。它只说明项目通用入口；具体改动还要结合相关模块文档和代码规范。

> 模板说明：接入真实项目后，把依赖安装命令和审计脚本路径替换为本仓库的实际约定。

## 速查

| 场景 | 命令 | 通过标准 |
| --- | --- | --- |
| Git 空白和冲突标记 | `git diff --check` | 无 trailing whitespace、冲突标记或补丁格式错误 |
| Python 语法 | `python -m py_compile <changed-python-files>` | 修改过的 Python 文件全部可编译 |
| 根工程单测冒烟 | `python -m pytest tests/unit -q` | 相关集合通过；如全量已有历史失败，要记录和本次改动无关的证据 |
| 前端构建 | `Push-Location frontend; npm run build; Pop-Location` | 构建通过，无新增前端构建错误 |
| 代码规范审计 | `python tools/code_style_audit/report_code_style_audit.py --limit-per-category 20` | 改动文件没有新增命名、路径、日志字段、容器后缀等规范问题 |

## 依赖缺失

- Python 依赖缺失时，优先使用项目约定的统一依赖安装命令；若无统一入口，则在根目录和各子模块分别执行 `uv sync` 或等价命令。
- 前端依赖缺失时，在前端工程目录下跑 `npm install`。

## 使用规则

- 文档-only 改动至少跑 `git diff --check`，并检查新增 Markdown 是否无乱码、无错误路径。
- Python 改动至少跑 `py_compile` 和相关单测。
- 前端改动至少跑 `npm run build`，用户可见交互还要做集成模拟或浏览器覆盖。
- 不用全仓库机械修行尾、格式或命名；只处理本次任务涉及文件。
- 如果全量审计有历史债务，验收记录要写“本次改动文件是否新增问题”，不要把历史债务伪装成本次通过。
