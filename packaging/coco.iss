; Inno Setup 6 script for Coco-Setup-<version>.exe. Built by tools_dev\build_exe.py --installer,
; which passes AppVersion, SourceDir (dist\Coco), OutDir (dist) and IconFile.
;
; Per-user install (no admin): %LOCALAPPDATA%\Programs\Coco. Her data (settings, chats,
; memories, models) lives in %LOCALAPPDATA%\Coco and is kept on uninstall unless you say so.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Coco"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6F1C2B9E-3C4D-4E0A-9C7B-C0C0CA7D0001}
AppName=Coco
AppVersion={#AppVersion}
AppVerName=Coco {#AppVersion}
AppPublisher=Coco desktop cat
DefaultDirName={autopf}\Coco
DefaultGroupName=Coco
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutDir}
OutputBaseFilename=Coco-Setup-{#AppVersion}
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\Coco.exe
UninstallDisplayName=Coco (desktop cat)
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
DiskSpanning=no
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start Coco with Windows"; GroupDescription: "When Windows starts:"
Name: "desktopicon"; Description: "Make a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; an update replaces the whole build (old DLLs never linger)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Coco"; Filename: "{app}\Coco.exe"; Comment: "Coco, the desktop cat"
Name: "{group}\Coco's dashboard"; Filename: "http://127.0.0.1:8765"; Comment: "Her dashboard (when Coco is on)"
Name: "{group}\Uninstall Coco"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Coco"; Filename: "{app}\Coco.exe"; Tasks: desktopicon

[Registry]
; the same entry her tray's "Start with Windows" writes (coco/core/launch.py)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Coco desktop cat"; ValueData: """{app}\Coco.exe"""; Tasks: startup
; (uninstall removes it in [Code] only while it still starts this Coco.exe)

[Run]
Filename: "{app}\Coco.exe"; Description: "Start Coco now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM Coco.exe /F"; Flags: runhidden; RunOnceId: "StopCoco"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data, Cmd: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { "Start with Windows" may have been turned on from her tray: remove it too, but only
      if it starts THIS Coco.exe (never a Coco run from source) }
    if RegQueryStringValue(HKCU, RunKey, 'Coco desktop cat', Cmd) then
      if Pos(Lowercase(ExpandConstant('{app}\Coco.exe')), Lowercase(Cmd)) > 0 then
        RegDeleteValue(HKCU, RunKey, 'Coco desktop cat');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\Coco');
    if DirExists(Data) and not UninstallSilent then
      { (no line may start with a # here: the preprocessor would read it as a directive) }
      if MsgBox('Also delete your Coco data?' + #13#10#13#10 +
                'Your chats, memories, notes, settings, PIN and her downloaded models are in:' + #13#10 +
                Data + #13#10#13#10 + 'Keep them (No) if you might install Coco again.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
