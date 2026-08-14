; Inno Setup script for the Windows installer.
;
; Produces Urjapod-Setup-<version>.exe. The same installer covers both roles:
;
;   Server      — the always-on office PC. Installs a Windows Service that
;                 starts at boot, opens the firewall port, and creates the
;                 first owner account.
;   Workstation — the accountant's PC. Installs only a shortcut that opens a
;                 browser at the server. No database, no data, nothing to edit.
;
; That second mode is the point: if the accountant's machine held the database
; he could open it with any SQLite tool and the audit trail would be worthless.

#define AppName "Urjapod"
#define AppVersion GetEnv("URJAPOD_VERSION")
#if AppVersion == ""
  #define AppVersion "0.2.0"
#endif
#define AppPublisher "Urjapod Energy Private Limited"
#define AppExe "urjapod.exe"
#define DefaultPort "8765"

[Setup]
AppId={{7D2A5F41-6C3E-4B8A-9F1D-URJAPOD00001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputBaseFilename=Urjapod-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The service and the firewall rule both need elevation.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName} {#AppVersion}
DisableDirPage=no
LicenseFile=
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "server";      Description: "Server — this PC stores the books (choose this for ONE machine only)"
Name: "workstation"; Description: "Workstation — connect to a server on another PC"

[Components]
Name: "core";    Description: "Application files"; Types: server workstation; Flags: fixed
Name: "service"; Description: "Run as a Windows Service, starting at boot"; Types: server

[Files]
Source: "..\..\dist\urjapod\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
; NSSM wraps the console application as a proper Windows Service.
Source: "nssm.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: service
; WeasyPrint needs the GTK3 runtime for PDF rendering. Chain-installed below
; if pango is not already present.
Source: "gtk3-runtime-setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Components: service; Check: NeedsGtk

[Tasks]
Name: "firewall"; Description: "Allow other PCs on the office network to reach this server"; Components: service
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "open --server {code:GetServerUrl}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "open --server {code:GetServerUrl}"; Tasks: desktopicon
Name: "{group}\Back up the books"; Filename: "{app}\{#AppExe}"; Parameters: "backup"; Components: service
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
; --- GTK3, needed by WeasyPrint for PDF output ---------------------------
Filename: "{tmp}\gtk3-runtime-setup.exe"; Parameters: "/S"; StatusMsg: "Installing PDF rendering support..."; Flags: waituntilterminated; Components: service; Check: NeedsGtk

; --- service -------------------------------------------------------------
Filename: "{app}\nssm.exe"; Parameters: "install UrjapodServer ""{app}\{#AppExe}"" serve --lan --port {code:GetPort} --no-open"; Flags: runhidden waituntilterminated; Components: service
Filename: "{app}\nssm.exe"; Parameters: "set UrjapodServer DisplayName ""Urjapod Orders & Invoicing"""; Flags: runhidden waituntilterminated; Components: service
Filename: "{app}\nssm.exe"; Parameters: "set UrjapodServer Start SERVICE_AUTO_START"; Flags: runhidden waituntilterminated; Components: service
Filename: "{app}\nssm.exe"; Parameters: "set UrjapodServer AppStdout ""{commonappdata}\Urjapod\server.log"""; Flags: runhidden waituntilterminated; Components: service
Filename: "{app}\nssm.exe"; Parameters: "set UrjapodServer AppStderr ""{commonappdata}\Urjapod\server.log"""; Flags: runhidden waituntilterminated; Components: service
Filename: "{app}\nssm.exe"; Parameters: "start UrjapodServer"; Flags: runhidden waituntilterminated; Components: service

; --- firewall ------------------------------------------------------------
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Urjapod Server"" dir=in action=allow protocol=TCP localport={code:GetPort}"; Flags: runhidden waituntilterminated; Tasks: firewall

; --- first account -------------------------------------------------------
Filename: "{app}\{#AppExe}"; Parameters: "create-owner"; Description: "Create the owner account now"; Flags: postinstall nowait; Components: service
Filename: "{app}\{#AppExe}"; Parameters: "open --server {code:GetServerUrl}"; Description: "Open {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop UrjapodServer"; Flags: runhidden waituntilterminated; RunOnceId: "StopSvc"; Components: service
Filename: "{app}\nssm.exe"; Parameters: "remove UrjapodServer confirm"; Flags: runhidden waituntilterminated; RunOnceId: "DelSvc"; Components: service
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Urjapod Server"""; Flags: runhidden waituntilterminated; RunOnceId: "DelFw"

[UninstallDelete]
; The books are NOT removed with the application. Deleting a company's
; accounting records because someone uninstalled a program would be
; indefensible; they stay in ProgramData until deliberately removed.
Type: files; Name: "{app}\*.log"

[Code]
var
  ServerPage: TInputQueryWizardPage;
  PortPage: TInputQueryWizardPage;

function NeedsGtk: Boolean;
begin
  { Present already if any pango DLL is on the path. }
  Result := not (FileExists(ExpandConstant('{sys}\libpango-1.0-0.dll'))
              or FileExists(ExpandConstant('{pf}\GTK3-Runtime Win64\bin\libpango-1.0-0.dll')));
end;

procedure InitializeWizard;
begin
  ServerPage := CreateInputQueryPage(wpSelectComponents,
    'Server address',
    'Which PC stores the books?',
    'On the accountant''s PC, type the name or IP of the office server, for ' +
    'example http://urjapod-server:8765. Leave the default if this PC is the server.');
  ServerPage.Add('Server URL:', False);
  ServerPage.Values[0] := 'http://localhost:' + '{#DefaultPort}';

  PortPage := CreateInputQueryPage(wpSelectComponents,
    'Network port',
    'Which port should the server listen on?',
    'The default is fine unless something else already uses it.');
  PortPage.Add('Port:', False);
  PortPage.Values[0] := '{#DefaultPort}';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { The server does not need to be told its own address; a workstation does
    not need to choose a port. }
  if PageID = ServerPage.ID then
    Result := WizardIsComponentSelected('service');
  if PageID = PortPage.ID then
    Result := not WizardIsComponentSelected('service');
end;

function GetPort(Param: String): String;
begin
  if WizardIsComponentSelected('service') then
    Result := PortPage.Values[0]
  else
    Result := '{#DefaultPort}';
  if Result = '' then
    Result := '{#DefaultPort}';
end;

function GetServerUrl(Param: String): String;
begin
  if WizardIsComponentSelected('service') then
    Result := 'http://localhost:' + GetPort('')
  else
    Result := ServerPage.Values[0];
end;
