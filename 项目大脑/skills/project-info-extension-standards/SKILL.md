---
name: project-info-extension-standards
description: 需要建立或校验项目大脑用户私有 skills、mcps、hooks 目录，判断 skill/MCP/hook 默认私有落点，或由 leader 执行公共化时使用。
---

# project-info-extension-standards

本 skill 定义项目大脑扩展目录标准，并提供脚本创建缺失目录。项目大脑不设置 `公共/` 物理目录；项目共享扩展放在 `项目大脑/` 下一层，但普通创建默认放当前 author 私有区。

## 目录标准

| 类型 | 项目共享路径 | 用户私有路径 |
| --- | --- | --- |
| skills | `项目大脑/skills/` | `项目大脑/用户/{author}/skills/` |
| mcps | `项目大脑/mcps/` | `项目大脑/用户/{author}/mcps/` |
| hooks | `项目大脑/hooks/` | `项目大脑/用户/{author}/hooks/` |

`项目大脑/skills/` 同时放冷启动基础设施和已公共化的团队共享 skills。公共 skill 分类入口见 `项目大脑/skills/index.md`；新增公共 skill 时同步对应 `skills/索引/{分类}.md` 和 `主流程.md` §3（若需条件加载）。

## 放置规则

| 场景 | 放置位置 |
| --- | --- |
| 用户要求创建新的 skill / MCP / hook / 辅助落盘 | 默认放当前 author 私有区 |
| 某人临时实验、个人偏好、未验证流程 | 当前 author 私有区 |
| 程序员做给非代码成员复用的能力 | 先放作者私有区验证，再由 leader 征求意见后提升到项目共享区 |
| 多人会复用，且接口/行为已经稳定 | 先在私人区留下来源和验证，再由 `tech-lead` 走 leader 公共化流程提升到项目共享区 |
| 冷启动必须存在的最小基础设施 | 仅由 `tech-lead` 收口到 `项目大脑/skills/` |
| 当前 author 私有测试技能和验收基础设施 | 放在 `项目大脑/用户/{author}/skills/`；稳定后再由 leader 提升到 `项目大脑/skills/` |

共享 skill 不复制到个人区。个人需要使用共享 skill 时，在个人记录中引用共享路径；共享 skill 后续升级时，引用方自然使用新版本。

普通 author 不直接写 `项目大脑/skills/`、`项目大脑/mcps/`、`项目大脑/hooks/`。只有 `tech-lead` 或用户明确授权的 leader 公共化流程可以把私人能力提升到私有区外；这不是一般创建流程。

## 命名规则

- 目录名使用小写英文、数字和连字符。
- 公共内容示例使用 `{author}` 占位符，不写死真实账号。
- 每个 skill 目录必须包含 `SKILL.md`。
- 可执行脚本放 `scripts/`；详细规则放 `references/`；示例放 `examples/`。
- 不为扩展子目录创建 `index.md`；例外：`项目大脑/skills/index.md` 与 `skills/索引/` 是公共 skill 分类入口；用户区唯一目录索引是 `项目大脑/用户/{author}/index.md`。

## 创建目录

```powershell
python 项目大脑/skills/project-info-extension-standards/scripts/ensure_extension_dirs.py `
  --author "{author}"
```

未传 `--author` 时，脚本读取根目录 `workstate.json.author`。默认只创建当前 author 私有扩展目录。

leader 公共化时才显式创建或修复公共扩展目录：

```powershell
python 项目大脑/skills/project-info-extension-standards/scripts/ensure_extension_dirs.py `
  --author "tech-lead" `
  --scope public
```
