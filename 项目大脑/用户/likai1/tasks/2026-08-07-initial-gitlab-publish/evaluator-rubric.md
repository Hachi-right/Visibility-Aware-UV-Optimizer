# GitLab 首次发布验收标准

## 必须通过

- GitLab 项目位于 `AIGC/MB-AIGC`，远端 URL 为 `https://gitlab2.seasungame.com/AIGC/MB-AIGC.git`。
- 本地目标分支为 `main`，只使用普通 push，不使用 force push。
- `workstate.json`、Python 缓存和依赖目录不进入提交。
- 暂存差异通过 `git diff --cached --check`，Python 脚本可编译，推送安全检查器自测通过。
- 推送完成后，本地 `HEAD`、重新 fetch 的 `origin/main` 和 `git ls-remote` 返回的 `main` 哈希一致。
- 工作树除被 `.gitignore` 排除的本机状态和缓存外保持干净。

## 可记录但不阻塞

- `project-info-healthcheck` 的模板既有缺口可记录为未覆盖风险，但不得误报为本次新增问题。
- Git Credential Manager 的 GitLab OAuth 配置提示可记录；只要实际 fetch/push 与哈希回读成功，不阻塞交付。
