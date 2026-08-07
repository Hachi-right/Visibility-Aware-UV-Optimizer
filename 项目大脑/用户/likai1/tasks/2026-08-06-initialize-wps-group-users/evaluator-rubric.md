# 验收标准

## 通过条件

- WPS IM 群名唯一命中，成员清单可追溯到 chat_id 和 user_id。
- canonical author 来自与群成员 user_id 对齐的企业通讯录邮箱，不由姓名猜测。
- `workstate.json.author` 为 `likai1`，当前任务指针有效。
- 6 位成员均存在唯一用户目录，初始化 skill 约定的文件和子目录无缺失。
- `likai1` 的身份为 Leader；其余成员未确认职责时保留“待确认”，不得虚构分工。
- 当前任务四件套状态完整，项目启动检查为 0 errors / 0 warnings。
- 相关 Markdown 不含 UTF-8 替换字符或 Git 冲突标记。

## 非阻断基线

- 模板仓库公共健康检查的既有错误不计入本任务失败，但必须在 `process.md` 记录数量和类别。
- 仓库尚无首个 commit 时，明确说明 `git diff --check` 缺少已跟踪基线。
