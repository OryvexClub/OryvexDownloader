[Setup]
AppName=Oryvex Media Downloader
AppVersion=1.0.0
DefaultDirName={autopf}\OryvexDownloader
DefaultGroupName=OryvexDownloader
OutputDir=dist
OutputBaseFilename=OryvexDownloader-Setup
Compression=lzma
SolidCompression=yes
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\OryvexDownloader.exe

[Files]
Source: "dist\OryvexDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Oryvex Downloader"; Filename: "{app}\OryvexDownloader.exe"; IconFilename: "{app}\OryvexDownloader.exe"
Name: "{commondesktop}\Oryvex Downloader"; Filename: "{app}\OryvexDownloader.exe"; IconFilename: "{app}\OryvexDownloader.exe"

[Run]
Filename: "{app}\OryvexDownloader.exe"; Description: "Launch application"; Flags: nowait postinstall skipifsilent