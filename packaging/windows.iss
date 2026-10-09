; Current-user installer: no administrator prompt, with Start menu and uninstall entries.
#ifndef AppVersion
  #define AppVersion "0.8.0"
#endif
[Setup]
AppId={{8BB075E7-971E-4381-988B-F9866FB7D520}
AppName=ThumbTalk
AppVersion={#AppVersion}
AppPublisher=ThumbTalk contributors
AppPublisherURL=https://github.com/AlexanderCGKarlsson/thumbtalk
DefaultDirName={localappdata}\ThumbTalk
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\release
OutputBaseFilename=thumbtalk-windows-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\thumbtalk.ico
UninstallDisplayIcon={app}\app\thumbtalk.exe
CloseApplications=yes

[Files]
Source: "..\dist\thumbtalk\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ThumbTalk"; Filename: "{app}\app\thumbtalk.exe"; Parameters: "--setup"; WorkingDir: "{app}\app"

; The app creates this value only after the user opts in. Keep it on updates.
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "ThumbTalk"; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\app\thumbtalk.exe"; Parameters: "--setup"; Description: "Open ThumbTalk"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Started: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    SaveStringToFile(ExpandConstant('{app}\.thumbtalk-install'), '', False);
    Started := Exec(ExpandConstant('{app}\app\thumbtalk.exe'), '--update-addons',
      ExpandConstant('{app}\app'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if (not Started) or (ResultCode <> 0) then
    begin
      Log('ThumbTalk installed; automatic addon update needs attention.');
      if not WizardSilent then
        MsgBox('ThumbTalk is installed. Some addons need an update from Setup > WoW. Restart WoW after updating.', mbInformation, MB_OK);
    end;
  end;
end;
