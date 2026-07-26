[Setup]
AppName=Oryvex Media Downloader
AppVersion=3.9.4
DefaultDirName={autopf}\OryvexDownloader
DefaultGroupName=OryvexDownloader
OutputDir=dist
OutputBaseFilename=OryvexDownloader-Setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "dist\OryvexDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Oryvex Downloader"; Filename: "{app}\OryvexDownloader.exe"
Name: "{commondesktop}\Oryvex Downloader"; Filename: "{app}\OryvexDownloader.exe"

[Run]
Filename: "{app}\OryvexDownloader.exe"; Description: "Launch application"; Flags: nowait postinstall skipifsilent