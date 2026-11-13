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

[Types]
Name: "full"; Description: "Recommended: CatFlap and the WireGuard engine"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "app"; Description: "CatFlap"; Types: full custom; Flags: fixed
Name: "wireguard"; Description: "WireGuard engine (download from wireguard.com, about 3 MB)"; Types: full; ExtraDiskSpaceRequired: 10000000

[Messages]
SelectComponentsLabel2=CatFlap connects using WireGuard, the open-source VPN. Setup can download WireGuard's official, signed engine from download.wireguard.com. It runs inside CatFlap: it doesn't install the WireGuard app, so there's no second window or tray icon.%n%nThe first time you add a site, Windows asks for permission once to put the engine in Program Files\CatFlap and set the site up.%n%nUntick it to skip the download. CatFlap then uses WireGuard for Windows if you install it yourself, or offers to download the engine when you add a site.

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
; Removes CatFlap's tunnels and WireGuard engine (Windows asks for permission once, only if there are any).
Filename: "{app}\catflap-cli.exe"; Parameters: "remove-tunnels"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTunnels"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\wireguard"

[Code]
const
  WireGuardListing = 'https://download.wireguard.com/windows-client/latest.sig';
  WireGuardBase = 'https://download.wireguard.com/windows-client/';

var
  DownloadPage: TDownloadWizardPage;
  MsiName: String;

function EngineInPlace(): Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\CatFlap\WireGuard\wireguard.exe'));
end;

function WantEngine(): Boolean;
begin
  Result := WizardIsComponentSelected('wireguard') and (not EngineInPlace()) and (not WizardSilent());
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Downloading the WireGuard engine',
    'Getting the official, signed download from download.wireguard.com...', nil);
end;

{ The newest MSI for this PC, from WireGuard's signed file list. }
function FindMsiName(): String;
var
  Lines: TArrayOfString;
  I, P: Integer;
  Prefix, Line: String;
begin
  Result := '';
  if IsArm64 then Prefix := 'wireguard-arm64-' else Prefix := 'wireguard-amd64-';
  DownloadTemporaryFile(WireGuardListing, 'latest.sig', '', nil);
  if not LoadStringsFromFile(ExpandConstant('{tmp}\latest.sig'), Lines) then Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    Line := Trim(Lines[I]);
    P := Pos(Prefix, Line);
    if (P > 0) and (Copy(Line, Length(Line) - 3, 4) = '.msi') then begin
      Result := Copy(Line, P, Length(Line) - P + 1);
      Exit;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine + MemoComponentsInfo + NewLine + MemoTasksInfo;
  if WantEngine() then
    Result := Result + NewLine + 'Will be downloaded:' + NewLine +
      Space + 'WireGuard engine from download.wireguard.com (about 3 MB, checked for WireGuard''s signature).' + NewLine +
      Space + 'The WireGuard app itself is not installed.'
  else if not WizardIsComponentSelected('wireguard') then
    Result := Result + NewLine + 'Nothing else will be downloaded or installed.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  MsiName := '';
  if (CurPageID = wpReady) and WantEngine() then begin
    DownloadPage.Clear;
    DownloadPage.Show;
    try
      try
        MsiName := FindMsiName();
        if MsiName = '' then RaiseException('WireGuard''s file list didn''t name a download for this PC');
        DownloadPage.Add(WireGuardBase + MsiName, MsiName, '');
        DownloadPage.Download;
      except
        MsiName := '';
        SuppressibleMsgBox('Couldn''t download the WireGuard engine (' + GetExceptionMessage + '). ' +
          'CatFlap will still be installed and offers to download it when you add a site.',
          mbError, MB_OK, IDOK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir: String;
begin
  if (CurStep = ssPostInstall) and (MsiName <> '') and FileExists(ExpandConstant('{tmp}\') + MsiName) then begin
    { Kept next to CatFlap; the first site setup extracts the engine from it. }
    Dir := ExpandConstant('{app}\wireguard');
    DelTree(Dir, True, True, True);
    ForceDirectories(Dir);
    FileCopy(ExpandConstant('{tmp}\') + MsiName, Dir + '\' + MsiName, False);
  end;
end;
