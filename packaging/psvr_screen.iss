; PSVR Screen installer (Inno Setup 6). Build the app first (psvr_screen.spec), then:
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\psvr_screen.iss
; Result: dist\PSVR-Screen-Setup-<version>.exe
; The user picks what to install from a numbered list (see before_install.txt). Optional parts are downloaded
; from their official sources during the install; Steam / SteamVR / iVRy pages are only opened.

#define AppVersion "0.9.0"
#define AppExe "PSVR Screen.exe"

[Setup]
AppId={{6F3B2C8A-91D4-4E7B-A5C2-2D8E4F1B7A90}
AppName=PSVR Screen
AppVersion={#AppVersion}
AppPublisher=ALG4RVIO
AppPublisherURL=https://github.com/bife34/psvr-screen
DefaultDirName={autopf}\PSVR Screen
DefaultGroupName=PSVR Screen
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
InfoBeforeFile=before_install.txt
SetupIconFile=psvr_screen.ico
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=PSVR-Screen-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Types]
Name: "recommended"; Description: "Recommended (PSVR Screen + its monitors)"
Name: "everything"; Description: "Everything, also for VR games and the DS4 in PC games"
Name: "custom"; Description: "Choose"; Flags: iscustom

[Components]
Name: "app"; Description: "1. PSVR Screen - the app (needed)"; Types: recommended everything custom; Flags: fixed
Name: "vdd"; Description: "2. Virtual Display Driver - the VR monitors and 4K screen (needed)"; Types: recommended everything custom; Flags: fixed
Name: "ds4"; Description: "3. DS4Windows + ViGEmBus - DS4 in non-Steam PC games (optional, not needed in VR)"; Types: everything
Name: "steam"; Description: "4. Steam + SteamVR - for VR games (optional, free)"; Types: everything
Name: "ivry"; Description: "5. iVRy - PSVR in SteamVR, for VR games (optional, paid: opens its Steam page)"; Types: everything

[Tasks]
Name: "desktopicon"; Description: "A PSVR Screen icon on the desktop"

[Files]
Source: "..\dist\PSVR Screen\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Components: app
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Components: app
Source: "..\LICENSE"; DestDir: "{app}"; Components: app
Source: "third_party\vdd\*"; DestDir: "{app}\drivers\vdd"; Flags: ignoreversion; Components: vdd
Source: "install_vdd.ps1"; DestDir: "{app}\drivers"; Components: vdd
Source: "uninstall_vdd.ps1"; DestDir: "{app}\drivers"; Components: vdd

[Icons]
Name: "{group}\PSVR Screen"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\PSVR Screen"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{group}\DS4Windows"; Filename: "{app}\DS4Windows\DS4Windows.exe"; Components: ds4
Name: "{group}\Uninstall PSVR Screen"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\drivers\install_vdd.ps1"""; StatusMsg: "Installing the Virtual Display Driver (the VR monitors)..."; Flags: runhidden waituntilterminated; Components: vdd
Filename: "{tmp}\ViGEmBus_setup.exe"; StatusMsg: "Installing ViGEmBus (follow its window)..."; Flags: waituntilterminated skipifdoesntexist; Components: ds4
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Expand-Archive -Path '{tmp}\DS4Windows.zip' -DestinationPath '{app}' -Force"""; StatusMsg: "Unpacking DS4Windows..."; Flags: runhidden waituntilterminated skipifdoesntexist; Components: ds4
Filename: "{tmp}\SteamSetup.exe"; StatusMsg: "Installing Steam (follow its window)..."; Flags: waituntilterminated skipifdoesntexist; Components: steam
Filename: "steam://install/250820"; Description: "Install SteamVR in Steam now"; Flags: shellexec postinstall nowait; Components: steam
Filename: "https://store.steampowered.com/app/992490/"; Description: "Open iVRy's Steam page (PSVR in SteamVR)"; Flags: shellexec postinstall nowait; Components: ivry
Filename: "{app}\{#AppExe}"; Description: "Start PSVR Screen"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\drivers\uninstall_vdd.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveVDD"; Components: vdd

[Code]
var
  Downloads: TDownloadWizardPage;

function SteamInstalled: Boolean;
begin
  Result := RegKeyExists(HKCU, 'Software\Valve\Steam') and
            FileExists(ExpandConstant('{commonpf32}\Steam\steam.exe'));
end;

procedure InitializeWizard;
begin
  Downloads := CreateDownloadPage('Downloading', 'Getting the optional parts from their official sources...', nil);
end;

{ Before installing: download what was ticked (each from its official source). A failed download is skipped. }
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Count: Integer;
begin
  Result := True;
  Count := 0;
  if CurPageID <> wpReady then
    Exit;
  Downloads.Clear;
  if WizardIsComponentSelected('ds4') then
  begin
    Downloads.Add('https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe', 'ViGEmBus_setup.exe', '');
    Downloads.Add('https://github.com/schmaldeo/DS4Windows/releases/download/v3.9.9/DS4Windows_3.9.9_x64.zip', 'DS4Windows.zip', '');
    Count := Count + 2;
  end;
  if WizardIsComponentSelected('steam') and not SteamInstalled then
  begin
    Downloads.Add('https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe', 'SteamSetup.exe', '');
    Count := Count + 1;
  end;
  if Count = 0 then
    Exit;
  Downloads.Show;
  try
    try
      Downloads.Download;
    except
      SuppressibleMsgBox('Some optional parts could not be downloaded: ' + GetExceptionMessage + #13#10 +
        'PSVR Screen will be installed anyway; you can get them later from their websites.', mbInformation, MB_OK, IDOK);
    end;
  finally
    Downloads.Hide;
  end;
end;
