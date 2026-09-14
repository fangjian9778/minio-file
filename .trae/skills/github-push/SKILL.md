---
name: "github-push"
description: "将本地代码提交并推送到 GitHub 的完整流程，含常规 add/commit/push、Token 认证错误分析、网络慢自动重试。当用户要求推送代码、提交到 GitHub、触发 Actions 构建时调用。"
---

# GitHub 推送（github-push）

将本地代码变更提交并推送到 GitHub 远程仓库的标准化流程。适用于用户说"推送一下"、"提交并推送"、"触发重新构建"等场景。

## 使用流程

### 1. 检查仓库状态（并行执行）

```bash
git status                      # 查看未跟踪/已修改文件
git log --oneline -5            # 查看最近提交风格，保持信息风格一致
git remote -v                   # 确认远程仓库存在
```

### 2. 暂存文件

- 优先按文件名逐个暂存，避免 `git add -A` / `git add .` 误带入敏感文件（`.env`、`*.json` 凭据等）
- 若项目已有 `.gitignore`，先确认敏感文件已被忽略

```bash
git add <file1> <file2>
```

### 3. 提交

- 提交信息聚焦"为什么改"而非"改了什么"，1-2 句
- 不要 amend 已有提交，始终新建提交
- 使用 heredoc 保证格式：

```bash
git commit -m "$(cat <<'EOF'
提交信息
EOF
)"
```

### 4. 推送

```bash
git push
```

若首次推送或未设置上游：

```bash
git push -u origin <branch>
```

## Token 认证错误处理

推送时若提示认证失败，按顺序排查：

| 错误特征 | 原因 | 处理 |
|---------|------|------|
| `Authentication failed` / 403 | 密码或 Token 错误 | 让用户重新生成 PAT 并重试 |
| `Bad credentials` (401) | Token 失效/过期 | 重新生成 Token |
| 推送报错但提交成功 | 需要 workflow 权限 | 生成 Token 时必须勾选 **workflow** scope，否则 Actions 文件无法推送 |
| `SSL certificate problem` | 证书校验失败 | 配置 `git config --global http.sslVerify false` 后重试 |

提示：Token 推送用法 `https://<token>@github.com/<user>/<repo>.git` 或按 git 提示输入用户名 + PAT（作为密码）。

## 网络慢自动重试

- 推送因网络超时/缓慢失败时，直接重试同一条 push 命令
- 重试前不要做额外改动，保持幂等
- 若连续重试仍失败，检查网络连通性，或改用 git 代理设置

## 完成后

- 确认推送成功后，提示用户到 GitHub Actions 页面查看构建进度
- 如需下载产物，告知产物在 Actions 运行页面的 Artifacts 中
