; Inno Setup script: turns the PyInstaller build (dist\Para) into dist\Para-windows-setup.exe, so people install Para
; with a double-click: no admin rights, a Start menu entry, and an uninstaller in Settings > Apps.
; Build from the repository root:  iscc /DVersion=0.1.0 packaging\windows\para.iss

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
; Never change the AppId: Windows uses it to recognise an update to an installed Para.
AppId={{F229DF43-C893-40D7-AE2C-567D3D5AB973}
AppName=Para
AppVersion={#Version}
AppVerName=Para {#Version}
AppPublisher=Tomas Val
AppPublisherURL=https://pararegs.co.uk
AppSupportURL=https://github.com/valtomas/para-building-regs/issues
AppUpdatesURL=https://github.com/valtomas/para-building-regs/releases
; Installs for this person only, so it needs no administrator. The app's own data (%LOCALAPPDATA%\Para) is a
; different folder, so uninstalling leaves chats, settings and downloaded models alone.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Para
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\Para.exe
UninstallDisplayName=Para
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\..\dist
OutputBaseFilename=Para-windows-setup
; Closes a running Para before replacing its files (an update), and offers to start it again.
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[Files]
Source: "..\..\dist\Para\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update replaces the whole app, so files a newer build no longer ships don't linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\Para"; Filename: "{app}\Para.exe"
Name: "{userdesktop}\Para"; Filename: "{app}\Para.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Para.exe"; Description: "Open Para"; Flags: nowait postinstall skipifsilent
