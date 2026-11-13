; Inno Setup script for the CatFlap Windows installer.
; Built by the release workflow:  ISCC /DMyAppVersion=1.3 packaging\windows\catflap.iss
; Installs per user (no admin needed), so updates can run silently.

#ifndef MyAppVersion
  #define MyAppVersion "0.0"
#endif

[Setup]
AppId={{6E3A1C52-8B7F-4C1E-9A8E-CA7F1A90C0DE}
AppName=CatFlap
AppVersion={#MyAppVersion}
AppPublisher=VPN CatFlap contributors
AppPublisherURL=https://github.com/samschultzponsys/vpn-catflap
DefaultDirName={localappdata}\Programs\CatFlap
DefaultGroupName=CatFlap
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist-win
OutputBaseFilename=catflap-{#MyAppVersion}-setup
SetupIconFile=catflap.ico
UninstallDisplayIcon={app}\CatFlap.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start CatFlap when I sign in"; Flags: unchecked

[Files]
Source: "..\..\dist\CatFlap\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\..\NOTICE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\CatFlap"; Filename: "{app}\CatFlap.exe"
Name: "{autodesktop}\CatFlap"; Filename: "{app}\CatFlap.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "catflap"; ValueData: """{app}\CatFlap.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
; Also runs after a silent update, so CatFlap reopens as the new version.
Filename: "{app}\CatFlap.exe"; Description: "Start CatFlap"; Flags: nowait postinstall

[UninstallRun]
Filename: "taskkill"; Parameters: "/IM CatFlap.exe /F"; Flags: runhidden; RunOnceId: "StopCatFlap"

[Code]
function WireGuardInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\WireGuard\wireguard.exe')) or
            FileExists(ExpandConstant('{commonpf}\WireGuard\wireguard.exe'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and (not WizardSilent()) and (not WireGuardInstalled()) then
    if MsgBox('CatFlap uses WireGuard for Windows for the actual VPN connection, and it isn''t ' +
              'installed yet. Open the WireGuard download page now?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://www.wireguard.com/install/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
