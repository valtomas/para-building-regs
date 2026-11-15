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
; In the app's look: the tall picture on the first and last pages, the icon in the corner of the others (drawn by
; packaging/make_icon.py; Inno Setup picks the size that suits the screen's scaling). The welcome page is shown, since
; the modern style hides it and the install was otherwise one bare progress bar.
WizardImageFile=wizard-164.bmp,wizard-246.bmp,wizard-328.bmp
WizardSmallImageFile=wizard-small-55.bmp,wizard-small-83.bmp,wizard-small-110.bmp
DisableWelcomePage=no
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\..\dist
OutputBaseFilename=Para-windows-setup
; Closes a running Para before replacing its files. Para's own updater quits before running this.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on this computer.%n%nPara answers questions about the building regulations in England, citing the paragraph each answer comes from. It needs no administrator rights. Your questions stay on your computer or your own network unless you choose an online AI service.
FinishedLabel=Para is installed. You'll find it in the Start menu.

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
; Also runs after a silent install, which is how Para updates itself (para/updater.py), so it opens again.
Filename: "{app}\Para.exe"; Description: "Open Para"; Flags: nowait postinstall
