#define MyAppName "TecnoMonitor Agent"
#define MyAppVersion "4.0"
#define MyAppPublisher "TecnoImagen"
#define MyAppExeName "TecnoMonitorConfig.exe"
#define MyServiceExeName "TecnoMonitorService.exe"
#define MyIcon "logo.ico"

[Setup]
; AppId único. NO lo cambies para que las futuras actualizaciones sobreescriban correctamente.
AppId={{A2B5C8D1-9E3F-4A7B-8C2D-1E5F6A9B0C3D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\TecnoMonitorAgent
DisableProgramGroupPage=yes
; Fuerza a Inno Setup a pedir permisos de Administrador al instalar
PrivilegesRequired=admin
OutputDir=.
OutputBaseFilename=Instalar_TecnoMonitor_v4.0_Gold
SetupIconFile={#MyIcon}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
; Toma los ejecutables blindados con UAC que acabamos de compilar
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#MyServiceExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyIcon}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Crea un acceso directo en el escritorio para el Configurador con el escudo de Admin
Name: "{autodesktop}\Configurar TecnoMonitor"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyIcon}"

[Run]
; 1. Limpieza: Elimina la tarea programada vieja si existe para evitar clones
Filename: "schtasks"; Parameters: "/Delete /TN ""TecnoMonitor_AutoStart"" /F"; Flags: runhidden; StatusMsg: "Limpiando servicios anteriores..."

; 2. Crea la tarea programada maestra: Ejecuta el .bat como SYSTEM
Filename: "schtasks"; \
    Parameters: "/Create /F /TN ""TecnoMonitor_AutoStart"" /TR ""'{app}\start_agent.bat'"" /SC ONSTART /RL HIGHEST /RU SYSTEM"; \
    Flags: runhidden; Description: "Instalando servicio de monitoreo en segundo plano..."

; 3. LA CORRECCIÓN CLAVE: shellexec añadido al final para que Windows muestre el UAC sin error 740
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Panel de Configuración"; Flags: nowait postinstall skipifsilent shellexec

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  if CurStep = ssPostInstall then
  begin
    // Genera el archivo BAT de arranque con un delay de 15 segundos
    // Esto asegura que WMI y la red estén listos al encender el servidor
    SetArrayLength(Lines, 5);
    Lines[0] := '@echo off';
    Lines[1] := 'timeout /t 15 /nobreak > nul'; 
    Lines[2] := 'cd /d "' + ExpandConstant('{app}') + '"';
    Lines[3] := 'start "" "{#MyServiceExeName}"';
    SaveStringsToFile(ExpandConstant('{app}\start_agent.bat'), Lines, false);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultCode: Integer;
begin
  // Mata cualquier proceso de versiones anteriores para no bloquear la sobrescritura
  Exec('taskkill', '/F /IM {#MyServiceExeName} /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill', '/F /IM {#MyAppExeName} /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500); // Pausa de seguridad para que Windows libere el archivo
  Result := '';
end;