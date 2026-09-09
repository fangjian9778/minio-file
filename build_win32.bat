@echo off
chcp 65001 >nul
echo ========================================
echo MinIO 文件服务打包脚本 (Windows 32位)
echo ========================================
echo.

REM 检查 Python 版本
python --version 2>nul | findstr "3.8" >nul
if %errorlevel% neq 0 (
    echo [错误] 请使用 Python 3.8 (32位) 进行打包
    echo        Python 3.9+ 不支持 Windows 7
    echo.
    python --version
    pause
    exit /b 1
)

REM 检查 Python 位数
python -c "import struct; print(struct.calcsize('P') * 8)" | findstr "32" >nul
if %errorlevel% neq 0 (
    echo [错误] 请使用 32位 Python 进行打包
    echo        当前检测到的是 64位 Python
    echo.
    python -c "import struct; print(struct.calcsize('P') * 8, 'bit')"
    pause
    exit /b 1
)

echo [信息] Python 环境检查通过
echo.

REM 安装依赖
echo [步骤 1/4] 安装 Python 依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo [完成] 依赖安装成功
echo.

REM 安装 PyInstaller
echo [步骤 2/4] 安装 PyInstaller...
pip install pyinstaller
if %errorlevel% neq 0 (
    echo [错误] PyInstaller 安装失败
    pause
    exit /b 1
)
echo [完成] PyInstaller 安装成功
echo.

REM 执行打包
echo [步骤 3/4] 开始打包 exe...
echo.
pyinstaller --clean minio_server.spec
if %errorlevel% neq 0 (
    echo [错误] 打包失败
    pause
    exit /b 1
)
echo [完成] 打包成功
echo.

REM 检查输出
if exist "dist\minio_file_service\minio_file_service.exe" (
    echo [步骤 4/4] 打包产物检查...
    echo.
    echo ========================================
    echo 打包成功!
    echo ========================================
    echo.
    echo 可执行文件位置:
    echo   dist\minio_file_service\minio_file_service.exe
    echo.
    echo 使用方法:
    echo   1. 将整个 dist\minio_file_service 文件夹复制到目标机器
    echo   2. 双击运行 minio_file_service.exe
    echo   3. 浏览器访问 http://localhost:5050/minio
    echo.
    echo ========================================
    dir "dist\minio_file_service"
    echo.
) else (
    echo [错误] 打包产物未找到
)

pause