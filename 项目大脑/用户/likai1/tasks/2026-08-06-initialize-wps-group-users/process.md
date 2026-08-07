# 按 WPS IM 群初始化项目大脑用户

开始日期: 2026-08-06

## 用户原始需求

按 WPS IM 群“见习机组整备舱”添加用户目录；当前身份为 Leader，canonical author 为 `likai1`。

## 目标

核对 WPS IM 群及成员身份，将 `likai1` 设为 Leader，为每位已确认群成员初始化唯一 canonical 用户目录，并验证项目大脑结构。

## 当前状态

- 已完成：WPS 群与通讯录身份核对、Leader 身份落盘、6 个用户目录初始化及定向验收。
- 未提交、未推送。

## 团队经验引用

- [x] 已读 `项目大脑/团队经验/INDEX.md`。
- 相关条文 ID：暂无（模板空库，entries 数量为 0）。
- 与本任务关联：无可路由条目。

## 个人经验引用

- [x] 检查 `项目大脑/用户/likai1/经验/INDEX.md`。
- 相关条文 ID：暂无；初始化脚本尚未生成该 INDEX。
- 与本任务关联：暂无个人经验条目。

## 团队公理引用

- [x] 已读 `项目大脑/公理/index.md`「任务开始路由」。
- 推荐公理 ID：暂无（模板空库）。
- 与本任务关联：无可路由条目。

## WPS 身份核对

- 会话：`见习机组整备舱`，chat_id=`98794843`，唯一命中且状态为 active。
- 成员共 6 人；以群成员 `user_id` 与企业通讯录结果交叉确认姓名和邮箱。
- canonical author 使用企业邮箱本地部分：`likai1`、`lusitong`、`huangshu`、`lihaixuan`、`chenrun`、`lixiaolong3`。
- 用户明确确认 `likai1` 为 Leader；其余成员具体角色与职责未获确认，初始化为“成员（待确认）/待 Leader 分配”。

## 初始化结果

| author | 成员 | 角色 |
| --- | --- | --- |
| `likai1` | 李凯 | Leader |
| `lusitong` | 卢思彤 | 成员（待确认） |
| `huangshu` | 黄数 | 成员（待确认） |
| `lihaixuan` | 李海旋 | 成员（待确认） |
| `chenrun` | 陈闰 | 成员（待确认） |
| `lixiaolong3` | 李小龙 | 成员（待确认） |

## 验收摘要

- 6/6 用户目录通过定向结构检查；每个目录的 skill 约定文件和子目录均无缺失。
- `workstate.json.author=likai1`，任务指针为本任务目录。
- `project-startup-check --check-only --json`：errors=0，warnings=0，next_action=`read_current_task_then_main_flow`。
- 扫描 113 个相关文件：UTF-8 替换字符 0，Git 冲突标记文件 0。
- `git diff --check`：退出码 0；但仓库尚无首个 commit，全部模板内容仍为 untracked，此检查没有可比较的已跟踪基线。
- `project-info-healthcheck`：errors=12，均为模板公共基线问题，包括缺少 Claude/Cursor/Copilot/Codex hook 入口、AGENTS 导航项、公共 docs/skill/Markdown 覆盖项；本次新增用户区未被报告错误。
- 范围外风险：`用户/{author}/经验/INDEX.md` 与任务启动路由存在模板契约缺口；未修改公共 `init-project-info-user` skill，留待 tech-lead 收口。
