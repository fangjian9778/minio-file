# MinIO 文件服务 - Windows 打包说明

## 环境要求

| 项目 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 7/8/10/11 | 32 位系统 |
| Python | **3.8 (32位)** | Python 3.9+ 不支持 Windows 7 |
| 打包工具 | PyInstaller | 自动安装 |

## 下载 Python 3.8 32位

1. 访问 https://www.python.org/ftp/python/3.8.19/python-3.8.19.exe
2. 安装时勾选 **"Add Python to PATH"**
3. 验证: `python --version` 显示 `3.8.x`
4. 验证位数: `python -c "import struct; print(struct.calcsize('P') * 8, 'bit')"` 显示 `32 bit`

## 打包步骤

### 方式一: 使用批处理脚本 (推荐)

```bash
# 1. 将项目文件夹复制到 Windows 机器
# 2. 双击运行 build_win32.bat
# 3. 等待打包完成
```

### 方式二: 手动打包

```bash
# 1. 安装依赖
pip install -r requirements.txt
pip install pyinstaller

# 2. 执行打包
pyinstaller --clean minio_server.spec

# 3. 产物在 dist/minio_file_service/ 目录
```

## 打包产物

```
dist/minio_file_service/
├── minio_file_service.exe    ← 主程序
├── pythonXX.dll              ← Python 运行时
├── _xxxx.cp38-win32.pyd      ← C 扩展库
├── templates/                ← HTML 模板
├── lib/                      ← 依赖库
└── ...                       ← 其他资源文件
```

## 部署使用

### 方式一: 直接运行

```bash
# 将整个 minio_file_service 文件夹复制到目标机器
# 双击运行 minio_file_service.exe
```

### 方式二: 单文件模式 (可选)

修改 `minio_server.spec` 中的 `EXE` 部分:

```python
exe = EXE(
    ...
    console=False,  # 隐藏控制台窗口
    ...
)

coll = COLLECT(
    ...
    # 改为单文件模式需要修改为 BUNDLE
)
```

或者使用命令打包:

```bash
pyinstaller --onefile --windowed minio_server.py
```

## 运行配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| PORT | 5050 | 服务端口 |
| HOST | 0.0.0.0 | 监听地址 |
| DEBUG | false | 调试模式 |

### Windows 环境变量设置

```bash
# 命令行临时设置
set PORT=8080
minio_file_service.exe

# 永久设置 (系统属性 → 环境变量)
```

## 防火墙配置

```bash
# 允许端口通过防火墙 (管理员权限)
netsh advfirewall firewall add rule name="MinIO File Service" dir=in action=allow protocol=TCP localport=5050
```

## 常见问题

### Q1: Python 3.9 打包的 exe 在 Windows 7 上无法运行

**原因**: Python 3.9+ 使用更新版 Visual C++ Runtime, Windows 7 默认不包含

**解决**: 使用 Python 3.8 打包

### Q2: 缺少 VCRUNTIME140.dll

**解决**: 安装 Visual C++ Redistributable

https://aka.ms/vs/17/release/vc_redist.x86.exe

### Q3: minio SDK 导入失败

**原因**: minio 依赖 cryptography, 需要 OpenSSL

**解决**: 确保打包时包含所有依赖:

```bash
pip install minio cryptography certifi
pyinstaller --clean minio_server.spec
```

### Q4: exe 文件过大 (20MB+)

**原因**: PyInstaller 打包了 Python 运行时

**优化**: 使用 upx 压缩 (默认已启用)

### Q5: 杀毒软件误报

**解决**: 将 exe 添加到白名单, 或使用代码签名证书

## 访问地址

| 场景 | 地址 |
|------|------|
| 本机访问 | http://localhost:5050/minio |
| 局域网访问 | http://192.168.x.x:5050/minio |
| 远程访问 | http://服务器IP:5050/minio |

## 技术支持

- Python 下载: https://www.python.org/downloads/
- PyInstaller 文档: https://pyinstaller.org/
- MinIO SDK: https://min.io/docs/minio/linux/developers/python/API.html