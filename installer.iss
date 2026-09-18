; ============================================================================
; Inno Setup 脚本 - MINIO 文件管理 安装包
; 目标: Windows 7+ 32位/64位
; 打包流程: PyInstaller 生成 dist\minio_file_manager\ 文件夹后, 由本脚本打包为 setup.exe
; 编译命令(Windows): "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; ============================================================================

#define MyAppName "MINIO 文件管理"
#define MyAppVersion "1.0.0"
#define MyAppExeName "minio_file_manager.exe"
#define MyAppPublisher "MinIO File Manager"
#define MyAppURL "https://github.com/fangjian9778/minio-file"
#define MyAppDescription "MINIO 对象存储文件管理工具 - 支持文件上传下载、桶管理"

[Setup]
; 应用唯一标识 (卸载时用于识别, 请勿随意修改)
AppId={{8E2A9B1C-4F3D-4A2B-9C7E-1D5F6A8B9C0D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppComments={#MyAppDescription}
; 默认安装目录: 32位系统装到 Program Files, 64位系统装到 Program Files (x86)
DefaultDirName={autopf}\MINIOFileManager
; 默认开始菜单组
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 输出配置
OutputDir=installer
OutputBaseFilename=MINIO文件管理_Setup_{#MyAppVersion}
; 压缩设置
Compression=lzma2
SolidCompression=yes
; 安装界面风格 (现代向导)
WizardStyle=modern
; 需要管理员权限安装 (写入 Program Files)
PrivilegesRequired=admin
; 安装包图标
SetupIconFile=assets\app.ico
; 支持 Windows 7+
MinVersion=6.1
; 强制使用中文界面
ShowLanguageDialog=no
UsePreviousAppDir=no
; 卸载时删除程序目录
Uninstallable=yes
; 控制面板显示的卸载程序图标
UninstallDisplayIcon={app}\{#MyAppExeName}
; 应用版权信息
LicenseFile=
InfoBeforeFile=
InfoAfterFile=

[Languages]
; 使用 Inno Setup 内置的简体中文语言文件
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Messages]
; 覆盖部分关键提示为更贴合产品的中文描述
chinesesimplified.WelcomeLabel2=此向导将引导您在计算机上安装 [name/ver]。%n%n建议在安装前关闭所有其他应用程序, 以确保安装过程顺利完成。
chinesesimplified.SelectDirLabel3=安装程序将把 [name] 安装到以下文件夹。%n%n推荐使用默认安装路径。如需更改, 请点击"浏览"。

[Files]
; 打包 PyInstaller 输出的整个程序文件夹 (递归包含 templates 等依赖)
Source: "dist\minio_file_manager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 显式打包应用图标, 确保桌面/开始菜单快捷方式使用统一图标
Source: "assets\app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 开始菜单快捷方式 (显式指定 exe 图标, 保证与安装包一致)
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
; 安装完成后可选启动
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时删除整个程序目录 (含运行生成的配置文件)
Type: filesandordirs; Name: "{app}"
