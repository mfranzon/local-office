; Inno Setup script for the Windows installer (run via packaging/build_windows.ps1, which passes AppVersion).
; Installs per user, without administrator rights. Data (models, database) stays in %LOCALAPPDATA%\Local Office
; and is kept on uninstall, like the Mac and Linux apps.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{34A0D261-CC82-4366-B566-A56DDF712431}
AppName=Local Office
AppVersion={#AppVersion}
AppPublisher=Local Office
AppPublisherURL=https://github.com/mfranzon/local-office
DefaultDirName={autopf}\Local Office
DefaultGroupName=Local Office
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=LocalOffice-Setup
SetupIconFile=..\build\LocalOffice.ico
UninstallDisplayIcon={app}\Local Office.exe
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Local Office\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Local Office"; Filename: "{app}\Local Office.exe"
Name: "{autodesktop}\Local Office"; Filename: "{app}\Local Office.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Local Office.exe"; Description: "{cm:LaunchProgram,Local Office}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; model servers still running would keep their files locked
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM ""Local Office.exe"""; Flags: runhidden; RunOnceId: "StopApp"
