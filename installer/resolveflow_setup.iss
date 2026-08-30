; Inno Setup Script cho ResolveFlow Assistant v4.1
; Tự động đóng gói thư mục dist/ResolveFlow-Assistant thành bộ cài đặt Windows chuyên nghiệp

#define MyAppName "ResolveFlow Assistant"
#define MyAppVersion "4.1.0"
#define MyAppPublisher "NguyenVanTruong06"
#define MyAppURL "https://github.com/NguyenVanTruong06/ResolveFlow-Assistant"
#define MyAppExeName "ResolveFlow-Assistant.exe"

[Setup]
AppId={{E584A48A-9A47-498A-BF3B-98A8F9329C1F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\ResolveFlow-Assistant
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\installer_output
OutputBaseFilename=ResolveFlow_Assistant_v4.1_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\ResolveFlow-Assistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
