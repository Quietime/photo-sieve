#ifndef AppVersion
  #define AppVersion "4.0.0"
#endif
[Setup]
AppId={{7089E849-DA51-4DE0-B98A-D67311733429}
AppName=照片快筛
AppVersion={#AppVersion}
AppPublisher=Quietime
AppPublisherURL=https://github.com/Quietime/photo-sieve
DefaultDirName={localappdata}\Programs\PhotoSieve
DefaultGroupName=照片快筛
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release
OutputBaseFilename=PhotoSieve-Setup-Windows-x64
SetupIconFile=assets\photosieve.ico
UninstallDisplayIcon={app}\PhotoSieve.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Files]
Source: "dist\install-payload\PhotoSieve\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\照片快筛"; Filename: "{app}\PhotoSieve.exe"; IconFilename: "{app}\PhotoSieve.exe"; IconIndex: 0; AppUserModelID: "Quietime.PhotoSieve"
Name: "{autodesktop}\照片快筛"; Filename: "{app}\PhotoSieve.exe"; IconFilename: "{app}\PhotoSieve.exe"; IconIndex: 0; AppUserModelID: "Quietime.PhotoSieve"
[Run]
Filename: "{app}\PhotoSieve.exe"; Description: "启动照片快筛"; Flags: nowait postinstall skipifsilent

; User workspaces are never listed as installed files: updates/uninstall preserve them.
[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
var Probe: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    if not ForceDirectories(WizardDirValue) then begin
      MsgBox('请选择当前用户可以写入的安装目录。', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    Probe := AddBackslash(WizardDirValue) + '.photosieve-write-check';
    if not SaveStringToFile(Probe, 'write test', False) then begin
      MsgBox('应用需要在安装目录中保存工作文件夹和安装更新。请选择可以写入的目录。', mbError, MB_OK);
      Result := False;
    end else DeleteFile(Probe);
  end;
end;
