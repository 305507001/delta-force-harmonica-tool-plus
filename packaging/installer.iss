; 口琴工坊 安装包脚本（Inno Setup 6）
; 由 packaging\build_windows.py 调用，版本号和文件位置从命令行传进来：
;   ISCC /DAppVersion=2.1.0 /DSourceDir=<PyInstaller 输出的文件夹> /DOutputDir=<输出目录> installer.iss
; 本文件必须存成「带 BOM 的 UTF-8」，否则 Inno Setup 会按系统代码页读，中文变乱码。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist\口琴工坊"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define AppName "口琴工坊"
#define AppExe "口琴工坊.exe"

[Setup]
; AppId 用来识别"同一个程序"，升级安装会覆盖旧版本。以后发新版本不要改它。
AppId={{6F2C8E3A-4B7D-4E1F-9A5C-2D8B7E6F1A30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}（个人修改版）
AppPublisher=口琴工坊 个人修改版（非原作者发布）
AppComments=三角洲行动 · 口琴自动演奏 / 跟弹提示。仅供个人学习、技术研究与娱乐使用，严禁在任何在线对局中使用。
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 先给用户看许可协议（必须同意才能装），再看免责声明
LicenseFile={#SourceDir}\许可协议.txt
InfoBeforeFile={#SourceDir}\先看我.txt
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-v{#AppVersion}-安装包
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 程序本身每次启动都要管理员权限（游戏收模拟按键需要），装到 Program Files 也一样
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}

[Languages]
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "chs"; MessagesFile: "ChineseSimplified.isl"
#else
Name: "en"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName} 功能展示（浏览器打开）"; Filename: "{app}\功能展示.html"
Name: "{autoprograms}\{#AppName} 先看我"; Filename: "{app}\先看我.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; 程序要求管理员权限，必须用 shellexec 启动（否则会报"请求的操作需要提升"）
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent shellexec
