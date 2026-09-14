#!/bin/bash
#
# 快速设置 GitHub 仓库并推送代码以触发 Windows EXE 打包
#
# 使用方法:
#   1. 在 GitHub 创建新仓库 (例如: minio-file-service)
#   2. 运行此脚本
#   3. 按提示输入仓库地址
#   4. 推送完成后，到 GitHub Actions 页面查看打包进度
#   5. 打包完成后下载 dist/minio_file_service/ 目录

set -e

echo "=========================================="
echo "  GitHub Actions Windows EXE 打包助手"
echo "=========================================="
echo ""

# 检查 git 是否安装
if ! command -v git &> /dev/null; then
    echo "[错误] git 未安装，请先安装 git"
    exit 1
fi

# 检查是否已初始化 git 仓库
if [ ! -d ".git" ]; then
    echo "[步骤 1/6] 初始化 git 仓库..."
    git init
    git add .
    git commit -m "Initial commit: MinIO file service for Windows build"
    echo "[完成] Git 仓库初始化成功"
    echo ""
fi

# 检查是否有 remote
if ! git remote | grep -q "origin"; then
    echo "[步骤 2/6] 添加远程仓库..."
    echo "请在 GitHub 创建新仓库，然后输入仓库地址:"
    echo "  例如: https://github.com/yourusername/minio-file-service.git"
    echo ""
    read -p "仓库地址: " REPO_URL

    if [ -z "$REPO_URL" ]; then
        echo "[错误] 仓库地址不能为空"
        exit 1
    fi

    git remote add origin "$REPO_URL"
    echo "[完成] 远程仓库添加成功"
    echo ""
else
    echo "[步骤 2/6] 远程仓库已存在，跳过"
    echo ""
fi

# 创建 .gitignore
if [ ! -f ".gitignore" ]; then
    echo "[步骤 3/6] 创建 .gitignore..."
    cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
*.egg-info/
dist/
build/
*.spec.bak

# Virtual environments
venv/
env/
.venv/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# MinIO config (contains secrets)
.minio_config.json

# PyInstaller
*.spec.orig
EOF

    git add .gitignore
    git commit -m "Add .gitignore"
    echo "[完成] .gitignore 创建成功"
    echo ""
else
    echo "[步骤 3/6] .gitignore 已存在，跳过"
    echo ""
fi

# 添加 GitHub Actions 文件
echo "[步骤 4/6] 添加 GitHub Actions 配置文件..."
git add .github/workflows/build_windows_exe.yml
git add minio_server.spec
git add requirements.txt
git add build_win32.bat
git add BUILD_WINDOWS.md
git commit -m "Add Windows build configuration" --allow-empty
echo "[完成] 配置文件添加成功"
echo ""

# 添加所有源文件
echo "[步骤 5/6] 添加源文件..."
git add minio_server.py
git add templates/
git commit -m "Update source files" --allow-empty
echo "[完成] 源文件添加成功"
echo ""

# 推送到 GitHub
echo "[步骤 6/6] 推送到 GitHub..."
echo "输入你的 GitHub 凭据:"
echo "  - 用户名: 你的 GitHub 用户名"
echo "  - Password: 使用 Personal Access Token (不是密码)"
echo "  - PAT 获取: https://github.com/settings/tokens"
echo ""
read -p "按 Enter 继续推送..."

git push -u origin main 2>/dev/null || git push -u origin master

echo ""
echo "=========================================="
echo "  推送完成!"
echo "=========================================="
echo ""
echo "接下来:"
echo "  1. 打开 GitHub 仓库页面"
echo "  2. 点击 'Actions' 标签"
echo "  3. 查看 'Build Windows 32-bit EXE' 工作流"
echo "  4. 等待打包完成 (约 5-10 分钟)"
echo "  5. 打包完成后下载 artifacts"
echo ""
echo "打包产物下载:"
echo "  GitHub 仓库 -> Actions -> 点击工作流运行 -> 下载 'minio-file-service-win32'"
echo ""
echo "或者使用 GitHub CLI 下载:"
echo "  gh run download <run-id> -n minio-file-service-win32"
echo ""