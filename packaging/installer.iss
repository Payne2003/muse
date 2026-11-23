; Inno Setup – build: iscc /DAppVersion=1.0.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8C3B5E2A-6F1D-4B7A-9E0C-5D2F1A7B3C91}
AppName=Muse Mailbox
AppVersion={#AppVersion}
AppPublisher=Muse
DefaultDirName={localappdata}\Programs\MuseMailbox
DefaultGroupName=Muse Mailbox
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MuseMailbox-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\MuseMailbox.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên Desktop"; GroupDescription: "Biểu tượng:"

[Files]
Source: "..\dist\MuseMailbox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Muse Mailbox"; Filename: "{app}\MuseMailbox.exe"
Name: "{autodesktop}\Muse Mailbox"; Filename: "{app}\MuseMailbox.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MuseMailbox.exe"; Description: "Mở Muse Mailbox"; Flags: nowait postinstall skipifsilent
; Cập nhật tự động chạy bộ cài ở chế độ /SILENT -> mở lại ứng dụng sau khi cài xong
Filename: "{app}\MuseMailbox.exe"; Flags: nowait skipifnotsilent
