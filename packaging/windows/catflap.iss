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
const
  WireGuardInstallerUrl = 'https://download.wireguard.com/windows-client/wireguard-installer.exe';

var
  DownloadPage: TDownloadWizardPage;
  NeedWireGuard: Boolean;

function WireGuardInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\WireGuard\wireguard.exe')) or
            FileExists(ExpandConstant('{commonpf}\WireGuard\wireguard.exe'));
end;

procedure InitializeWizard;
begin
  NeedWireGuard := not WireGuardInstalled();
  DownloadPage := CreateDownloadPage('Getting WireGuard',
    'CatFlap uses WireGuard for Windows for the VPN connection. Downloading the official installer...', nil);
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + MemoGroupInfo + NewLine + MemoTasksInfo;
  if NeedWireGuard then
    Result := Result + NewLine + NewLine + 'WireGuard for Windows is not installed yet:' + NewLine +
      Space + 'the official installer will be downloaded from download.wireguard.com and started.' + NewLine +
      Space + 'Windows will ask for permission once (it installs a network driver).';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and NeedWireGuard and (not WizardSilent()) then begin
    DownloadPage.Clear;
    DownloadPage.Add(WireGuardInstallerUrl, 'wireguard-installer.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        SuppressibleMsgBox('Couldn''t download WireGuard (' + GetExceptionMessage + '). CatFlap will ' +
          'still be installed; install WireGuard later from https://www.wireguard.com/install/ ' +
          'or from inside CatFlap.', mbError, MB_OK, IDOK);
        NeedWireGuard := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
  Installer: String;
begin
  if (CurStep = ssPostInstall) and NeedWireGuard and (not WizardSilent()) then begin
    Installer := ExpandConstant('{tmp}\wireguard-installer.exe');
    if FileExists(Installer) then
      { The official installer asks for administrator rights itself (UAC). }
      if not ShellExec('', Installer, '', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ErrorCode) then
        MsgBox('WireGuard''s installer didn''t start (' + SysErrorMessage(ErrorCode) + '). You can ' +
          'install it later from inside CatFlap.', mbError, MB_OK);
  end;
end;
