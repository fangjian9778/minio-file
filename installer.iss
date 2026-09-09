; ============================================================================
; Inno Setup 脚本 - MinIO 文件服务 安装包
; 目标: Windows 7+ 32位/64位
; 打包流程: PyInstaller 生成 dist\minio_file_service\ 文件夹后, 由本脚本打包为 setup.exe
; 编译命令(Windows): "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; ============================================================================

#define MyAppName "MinIO 文件服务"
#define MyAppVersion "1.0.0"
#define MyAppExeName "minio_file_service.exe"
#define MyAppPublisher "MinIO File Service"

[Setup]
; 应用唯一标识 (卸载时用于识别, 请勿随意修改)
AppId={{8E2A9B1C-4F3D-4A2B-9C7E-1D5F6A8B9C0D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 默认安装目录: 32位系统装到 Program Files, 64位系统装到 Program Files (x86)
DefaultDirName={autopf}\MinIOFileService
; 默认开始菜单组
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 输出配置
OutputDir=installer
OutputBaseFilename=MinIO_File_Service_Setup_{#MyAppVersion}
; 压缩设置
Compression=lzma2
SolidCompression=yes
; 安装界面风格 (现代向导)
WizardStyle=modern
; 需要管理员权限安装 (写入 Program Files)
PrivilegesRequired=admin
; 支持 Windows 7+
MinVersion=6.1
; 使用中文界面
ShowLanguageDialog=no
UsePreviousAppDir=no
; 卸载时删除程序目录
Uninstallable=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Files]
; 打包 PyInstaller 输出的整个程序文件夹 (递归包含 templates 等依赖)
Source: "dist\minio_file_service\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单快捷方式
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
; 安装完成后可选启动
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时删除整个程序目录 (含运行生成的配置文件)
Type: filesandordirs; Name: "{app}"
