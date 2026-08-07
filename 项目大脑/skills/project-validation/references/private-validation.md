# 私人验收扩展

本文件说明当前 author 如何维护自己的私有测试 skill 和验收基础设施。

## 放置位置

| 类型 | 路径 | 用途 |
| --- | --- | --- |
| 私有测试 skill | `项目大脑/用户/{author}/skills/{skill-name}/` | 当前 author 的个人测试流程、实验性验收方法 |
| 私有脚本 | 私有 skill 的 `scripts/` | 可复用的个人测试自动化脚本 |
| 私有参考 | 私有 skill 的 `references/` | 个人常用测试清单、模型评测方法、会议转写验收规则 |
| 任务证据 | `项目大脑/用户/{author}/tasks/{task-id}/` | 当前任务的日志摘要、截图路径、验收记录 |
| 本机私有原始材料 | `_local/` 或 `raw-local/` | 不入库的敏感或体积大的原始材料 |

## 使用规则

- 私有测试 skill 不复制公共 `project-validation`，只引用它或补充个人差异。
- 稳定、多人可复用的测试方法，由当前 author 在任务 `process.md` 提出，交给 `tech-lead` 收口到公共 skill。
- 私有 skill 可以被当前 author 的 `index.md` 引用；不要在子目录新建额外 `index.md`。
- 程序员做给非代码成员使用的测试/编辑 skill，先放作者私有区验证；确认稳定后提升到 `项目大脑/skills/`。

## 验收接入

任务收尾时按顺序判断：

1. 公共 `project-validation` 是否已覆盖任务风险。
2. 当前 author 私有 `index.md` 是否引用了和本任务相关的私有测试 skill。
3. 私有 skill 是否只补充个人/专项内容，没有和公共规则冲突。
4. 需要公共化时，先在任务 `process.md` 记录建议，不直接改公共入口。
