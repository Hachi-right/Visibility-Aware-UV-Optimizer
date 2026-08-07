---
name: generate-workstate
description: 需要生成或刷新仓库根目录 workstate.json、绑定当前 author 与当前任务目录，或执行项目大脑冷启动状态校验时使用。
---

# generate-workstate

## 参考文档表

| 主题 | 文件 | 内容 |
|------|------|------|
| workstate 规则 | `references/workstate.md` | 字段、author 来源、task_folder 规则 |
| 路径约束 | `references/path-rules.md` | 本 skill 内路径和任务目录约束 |

## 示例表

| 场景 | 文件 | 内容 |
|------|------|------|
| 冷启动状态 | `examples/cold-start.md` | 首次生成空 `task_folder` 的 workstate |
| 指向已有任务 | `examples/existing-task.md` | 将 workstate 指向已存在任务目录 |

## 脚本表

| 脚本 | 用途 |
|------|------|
| `scripts/generate_workstate.py` | 生成或刷新根目录 `workstate.json` |
