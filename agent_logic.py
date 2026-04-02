import requests
import wmi
import pythoncom
import time
import socket
import urllib3
import json
import pyodbc
import os
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait, FIRST_COMPLETED
from requests.auth import HTTPBasicAuth
import security

# ---------------------------------------------------------------------------
# RUTAS Y CONSTANTES
# ---------------------------------------------------------------------------
DATA_DIR = security.get_app_data_path()
SQL_CHECKPOINT_FILE = os.path.join(DATA_DIR, ".sql_checkpoint")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# QUERY SQL (sin cambios de lógica, solo se mantiene)
# ---------------------------------------------------------------------------
SQL_QUERY = """
DECLARE @StartDate DATETIME = '{start_date_sql}';
DECLARE @EndDate DATETIME = '{end_date_sql}';

SELECT 
    (
        SELECT 
            ISNULL(e.[Description], 'Unknown') AS equipo, 
            ISNULL(e.[AETitle], '') AS aet, 
            ISNULL(e.[DICOMModalityCode], '') AS mod,
            SUM(CASE WHEN t.[CreatedOn] >= @StartDate AND t.[CreatedOn] < @EndDate THEN 1 ELSE 0 END) AS totales,
            SUM(CASE WHEN t.[PlanningDate] >= @StartDate AND t.[PlanningDate] < @EndDate AND t.[IsPlanned] = 1 THEN 1 ELSE 0 END) AS citados,
            SUM(CASE WHEN t.[AdmissionDate] >= @StartDate AND t.[AdmissionDate] < @EndDate AND t.[IsAdmitted] = 1 THEN 1 ELSE 0 END) AS admitidos,
            SUM(CASE WHEN t.[ExecutionDate] >= @StartDate AND t.[ExecutionDate] < @EndDate AND t.[IsExecuted] = 1 THEN 1 ELSE 0 END) AS ejecutados,
            SUM(
                CASE 
                    WHEN t.[ExecutionDate] >= @StartDate AND t.[ExecutionDate] < @EndDate AND t.[ImageAvailability] = 1 THEN 1
                    WHEN t.[ExecutionDate] IS NULL AND t.[AdmissionDate] >= @StartDate AND t.[AdmissionDate] < @EndDate AND t.[ImageAvailability] = 1 THEN 1
                    ELSE 0 
                END
            ) AS con_imagen,
            SUM(CASE WHEN t.[ReportDate] >= @StartDate AND t.[ReportDate] < @EndDate AND t.[IsReported] = 1 THEN 1 ELSE 0 END) AS borradores,
            SUM(CASE WHEN t.[ApprovalDate] >= @StartDate AND t.[ApprovalDate] < @EndDate AND t.[IsApproved] = 1 THEN 1 ELSE 0 END) AS definitivos,
            SUM(
                CASE 
                    WHEN COALESCE(t.[ExecutionDate], t.[AdmissionDate], t.[CreatedOn]) >= @StartDate 
                     AND COALESCE(t.[ExecutionDate], t.[AdmissionDate], t.[CreatedOn]) < @EndDate 
                     AND t.[IsSuspended] = 1 THEN 1 
                    ELSE 0 
                END
            ) AS suspendidos
        FROM [ExtensaRadio].[ExtRadio].[tbExamination] t WITH(NOLOCK)
        INNER JOIN [ExtensaRadio].[ExtRadio].[lsEquipment] e WITH(NOLOCK) ON t.[IdEquipment] = e.[Guid]
        WHERE 
            (t.[CreatedOn] >= @StartDate AND t.[CreatedOn] < @EndDate)
            OR (t.[PlanningDate] >= @StartDate AND t.[PlanningDate] < @EndDate)
            OR (t.[AdmissionDate] >= @StartDate AND t.[AdmissionDate] < @EndDate)
            OR (t.[ExecutionDate] >= @StartDate AND t.[ExecutionDate] < @EndDate)
            OR (t.[ReportDate] >= @StartDate AND t.[ReportDate] < @EndDate)
            OR (t.[ApprovalDate] >= @StartDate AND t.[ApprovalDate] < @EndDate)
            OR (t.[ModifiedOn] >= @StartDate AND t.[ModifiedOn] < @EndDate)
        GROUP BY e.[Description], e.[AETitle], e.[DICOMModalityCode]
        ORDER BY e.[Description]
        FOR JSON PATH
    ) AS [application_metrics.ris],
    (
        SELECT 
            ISNULL([AET], '') AS aet, 
            ISNULL([MOD_IN_STUDY], '') AS mod, 
            COUNT(DISTINCT [STUDY_KEY]) AS almacenados
        FROM [ExtensaPACS].[ExtPacs].[DICOMSTUDIES] WITH(NOLOCK)
        WHERE [LASTUPDATE_DT] >= @StartDate AND [LASTUPDATE_DT] < @EndDate AND ([DELETED] IS NULL OR [DELETED] = '0')
        GROUP BY [AET], [MOD_IN_STUDY]
        ORDER BY almacenados DESC, [AET]
        FOR JSON PATH
    ) AS [application_metrics.pacs],
    (
        SELECT 
            ISNULL(r.[Description], 'Unknown') AS rol, 
            COUNT(DISTINCT a.[User_GUID]) AS usuarios_unicos, 
            COUNT(a.[GUID]) AS inicios_sesion
        FROM [SL_UserAndConfig].[ExtConfig].[UserAuditHistory] a WITH(NOLOCK)
        INNER JOIN [ExtensaRadio].[ExtRadio].[tbUser] u WITH(NOLOCK) ON a.[User_GUID] = u.[Guid]
        INNER JOIN [ExtensaRadio].[ExtRadio].[lsRole] r WITH(NOLOCK) ON u.[IdRole] = r.[Guid]
        WHERE a.[CreatedOn] >= @StartDate AND a.[CreatedOn] < @EndDate AND a.[AuditText] = 'User Logon OK'
        GROUP BY r.[Description]
        ORDER BY usuarios_unicos DESC
        FOR JSON PATH
    ) AS [application_metrics.users]
FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;
"""

# ---------------------------------------------------------------------------
# HELPERS GENERALES
# ---------------------------------------------------------------------------
def safe_int(value):
    try:
        return int(value) if value is not None else 0
    except Exception:
        return 0


def safe_float(value, decimals=2):
    try:
        return round(float(value), decimals) if value is not None else 0.0
    except Exception:
        return 0.0


def verificar_puerto(ip, puerto, timeout=2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, int(puerto)))
        sock.close()
        return result == 0
    except Exception:
        return False


def parse_wmi_date(wmi_date):
    try:
        return datetime.strptime(wmi_date.split('.')[0], "%Y%m%d%H%M%S")
    except Exception:
        return datetime.now()


# ---------------------------------------------------------------------------
# CHECKPOINT SQL — escritura atómica, guardado solo al confirmar envío exitoso
# ---------------------------------------------------------------------------
def get_last_checkpoint():
    if os.path.exists(SQL_CHECKPOINT_FILE):
        try:
            with open(SQL_CHECKPOINT_FILE, 'r') as f:
                return datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return None


def save_checkpoint(dt):
    """Escritura atómica: escribe en .tmp y renombra. Nunca deja el archivo a medias."""
    try:
        tmp = SQL_CHECKPOINT_FILE + ".tmp"
        with open(tmp, 'w') as f:
            f.write(dt.strftime("%Y-%m-%d %H:%M:%S"))
        os.replace(tmp, SQL_CHECKPOINT_FILE)
    except Exception:
        pass


def reset_checkpoint():
    for path in [SQL_CHECKPOINT_FILE, SQL_CHECKPOINT_FILE + ".tmp"]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# MÉTRICAS SQL
# ---------------------------------------------------------------------------
def extraer_metricas_sql(sql_config, log_func=None):
    if not sql_config or not sql_config.get("host"):
        return None

    conn_str          = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={sql_config['host']};DATABASE={sql_config['db']};UID={sql_config['user']};PWD={sql_config['pass']}"
    conn_str_fallback = f"DRIVER={{SQL Server}};SERVER={sql_config['host']};DATABASE={sql_config['db']};UID={sql_config['user']};PWD={sql_config['pass']}"

    conn = None
    try:
        try:
            conn = pyodbc.connect(conn_str, timeout=10)
        except pyodbc.Error:
            conn = pyodbc.connect(conn_str_fallback, timeout=10)

        cursor = conn.cursor()

        executions_per_day = int(sql_config.get("executions_per_day", 3))
        if executions_per_day <= 0:
            executions_per_day = 3
        interval_hours = 24.0 / executions_per_day

        ahora             = datetime.now()
        ultimo_checkpoint = get_last_checkpoint()

        if not ultimo_checkpoint:
            historical_start = sql_config.get("historical_start_date")
            if historical_start:
                try:
                    target_start_time = datetime.strptime(historical_start, "%Y-%m-%d")
                    if log_func:
                        log_func(f"🕰️ INICIANDO BACKFILL HISTÓRICO desde {historical_start}")
                except Exception:
                    target_start_time = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                target_start_time = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            target_start_time = ultimo_checkpoint

        target_end_time = target_start_time + timedelta(hours=interval_hours)

        if target_end_time > ahora:
            return None

        start_date_sql = target_start_time.strftime("%Y-%m-%dT%H:%M:%S")
        end_date_sql   = target_end_time.strftime("%Y-%m-%dT%H:%M:%S")

        if (ahora - target_end_time).total_seconds() > 86400:
            if log_func:
                log_func(f"⏳ Backfill: Recuperando bloque [{start_date_sql} >> {end_date_sql}]...")
        else:
            if log_func:
                log_func(f"⚙️ SQL: Extrayendo bloque regular [{start_date_sql} >> {end_date_sql}]...")

        query_dinamica = SQL_QUERY.replace("{start_date_sql}", start_date_sql).replace("{end_date_sql}", end_date_sql)
        cursor.execute(query_dinamica)
        row = cursor.fetchone()
        if not row:
            return None

        json_fragments = []
        while row:
            json_fragments.append(row[0])
            row = cursor.fetchone()

        data_json = json.loads("".join(json_fragments))

        if "application_metrics" not in data_json:
            data_json["application_metrics"] = {}

        data_json["application_metrics"]["extraction_interval_hours"] = interval_hours
        data_json["application_metrics"]["start_time_extraction"]     = start_date_sql
        data_json["application_metrics"]["end_time_extraction"]       = end_date_sql

        # IMPORTANTE: el checkpoint se guarda solo cuando el POST confirme éxito.
        # Devolvemos también el target_end_time para que ejecutar_ciclo_agente pueda guardarlo.
        data_json["_checkpoint_to_save"] = target_end_time
        return data_json

    except Exception as e:
        if log_func:
            log_func(f"❌ Error conectando a SQL Server: {e}")
        return None
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# PROXMOX
# ---------------------------------------------------------------------------
def test_connection_proxmox(config):
    host = config.get("host") or config.get("ip")
    if not host or not verificar_puerto(host, 8006):
        return {"success": False, "msg": "Puerto 8006 cerrado o no alcanzable"}
    try:
        auth = {"username": config['user'], "password": config['pass']}
        r = requests.post(
            f"https://{host}:8006/api2/json/access/ticket",
            data=auth, verify=False, timeout=5
        )
        if r.status_code == 200:
            return {"success": True, "msg": "Proxmox OK"}
        return {"success": False, "msg": f"Credenciales inválidas (HTTP {r.status_code})"}
    except Exception as e:
        return {"success": False, "msg": str(e)}


def obtener_physical_layer(config):
    host = config.get("host") or config.get("ip")
    res = {
        "host_info":  {"hostname": host, "type": "proxmox", "model": "Unknown", "uptime_seconds": 0},
        "telemetry":  {},
        "sensors":    {},
    }
    try:
        auth    = {"username": config['user'], "password": config['pass']}
        r_auth  = requests.post(
            f"https://{host}:8006/api2/json/access/ticket",
            data=auth, verify=False, timeout=5
        )
        if r_auth.status_code == 200:
            tk     = r_auth.json()['data']
            r_node = requests.get(
                f"https://{host}:8006/api2/json/nodes/{config.get('node', 'pve')}/status",
                cookies={"PVEAuthCookie": tk['ticket']},
                headers={"CSRFPreventionToken": tk['CSRFPreventionToken']},
                verify=False, timeout=5
            )
            raw = r_node.json()['data']
            res["host_info"].update({
                "model":          raw.get("cpuinfo", {}).get("model"),
                "uptime_seconds": safe_int(raw.get("uptime")),
            })
            u_mem = safe_int(raw.get("memory", {}).get("used"))
            t_mem = safe_int(raw.get("memory", {}).get("total"))
            res["telemetry"] = {
                "cpu": {"usage_percent": safe_float(raw.get("cpu", 0) * 100)},
                "ram": {
                    "total_gb":     round(t_mem / 1073741824, 2),
                    "used_gb":      round(u_mem / 1073741824, 2),
                    "usage_percent": safe_float(u_mem / t_mem * 100 if t_mem > 0 else 0),
                },
            }
    except Exception as e:
        res["host_info"]["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# VMWARE — implementación real con pyVmomi
# ---------------------------------------------------------------------------
def test_connection_vmware(config):
    """
    Prueba conexión a vCenter o ESXi directo.
    Requiere: pip install pyVmomi
    """
    host = config.get("host") or config.get("ip")
    if not host:
        return {"success": False, "msg": "IP / Hostname no configurado"}

    if not verificar_puerto(host, 443):
        return {"success": False, "msg": "Puerto 443 cerrado o no alcanzable"}

    try:
        from pyVim.connect import SmartConnect, Disconnect
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode    = ssl.CERT_NONE

        si = SmartConnect(
            host=host,
            user=config.get("user", ""),
            pwd=config.get("pass", ""),
            sslContext=context,
            connectionPoolTimeout=10,
        )
        about = si.content.about
        version_info = f"{about.fullName} (API {about.apiVersion})"
        Disconnect(si)
        return {"success": True, "msg": f"VMware OK — {version_info}"}

    except ImportError:
        return {
            "success": False,
            "msg": "Módulo pyVmomi no instalado. Ejecutar: pip install pyVmomi",
        }
    except Exception as e:
        return {"success": False, "msg": f"Error VMware: {str(e)}"}


def obtener_vmware_layer(config, log_func=None):
    """
    Recolecta métricas del host ESXi / vCenter usando pyVmomi.
    Devuelve la misma estructura que obtener_physical_layer() para Proxmox,
    más la lista de VMs con telemetría básica.
    """
    host = config.get("host") or config.get("ip")
    res = {
        "host_info": {"hostname": host, "type": "vmware", "model": "Unknown", "uptime_seconds": 0},
        "telemetry": {},
        "sensors":   {},
        "vms":       [],
    }

    try:
        from pyVim.connect  import SmartConnect, Disconnect
        from pyVmomi        import vim
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode    = ssl.CERT_NONE

        si = SmartConnect(
            host=host,
            user=config.get("user", ""),
            pwd=config.get("pass", ""),
            sslContext=context,
            connectionPoolTimeout=10,
        )

        content = si.content

        # --- Info del host ---
        container  = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
        host_list  = container.view
        container.Destroy()

        if host_list:
            hs = host_list[0]
            summary = hs.summary
            hw      = summary.hardware
            rt      = summary.runtime
            qs      = summary.quickStats

            uptime = 0
            if rt.bootTime:
                uptime = int((datetime.utcnow() - rt.bootTime.replace(tzinfo=None)).total_seconds())

            total_ram_bytes = safe_int(hw.memorySize) if hw else 0
            used_ram_bytes  = safe_int(qs.overallMemoryUsage) * 1048576 if qs else 0

            res["host_info"].update({
                "model":          hw.cpuModel if hw else "Unknown",
                "uptime_seconds": uptime,
                "vendor":         hw.vendor  if hw else "Unknown",
            })
            res["telemetry"] = {
                "cpu": {
                    "usage_percent": safe_float(qs.overallCpuUsage / max(1, hw.numCpuCores * (hw.cpuMhz or 1)) * 100 if (qs and hw) else 0),
                },
                "ram": {
                    "total_gb":      round(total_ram_bytes / 1073741824, 2),
                    "used_gb":       round(used_ram_bytes  / 1073741824, 2),
                    "usage_percent": safe_float(used_ram_bytes / total_ram_bytes * 100 if total_ram_bytes > 0 else 0),
                },
            }

        # --- Lista de VMs ---
        vm_container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        vms          = vm_container.view
        vm_container.Destroy()

        for vm in vms:
            try:
                cfg_vm  = vm.config
                summary = vm.summary
                qs      = summary.quickStats
                state   = "Online" if summary.runtime.powerState == "poweredOn" else "Offline"

                total_ram_mb = safe_int(cfg_vm.hardware.memoryMB) if cfg_vm else 0
                used_ram_mb  = safe_int(qs.guestMemoryUsage)      if qs      else 0

                res["vms"].append({
                    "id":    cfg_vm.name if cfg_vm else vm.name,
                    "type":  "vm",
                    "state": state,
                    "telemetry": {
                        "cpu": {"usage_percent": safe_float(qs.overallCpuUsage if qs else 0)},
                        "ram": {
                            "total_gb":      round(total_ram_mb / 1024, 2),
                            "used_gb":       round(used_ram_mb  / 1024, 2),
                            "usage_percent": safe_float(used_ram_mb / total_ram_mb * 100 if total_ram_mb > 0 else 0),
                        },
                    },
                    "storage":           [],
                    "application_layer": {"services": []},
                })
            except Exception as vm_err:
                if log_func:
                    log_func(f"⚠️ VMware: error en VM {getattr(vm, 'name', '?')}: {vm_err}")

        Disconnect(si)

    except ImportError:
        msg = "pyVmomi no instalado. Ejecutar: pip install pyVmomi"
        if log_func:
            log_func(f"❌ VMware: {msg}")
        res["host_info"]["error"] = msg

    except Exception as e:
        if log_func:
            log_func(f"❌ Error recolectando VMware: {e}")
        res["host_info"]["error"] = str(e)

    return res


# ---------------------------------------------------------------------------
# iDRAC — Sensores y Storage
# ---------------------------------------------------------------------------
def test_connection_idrac(config):
    try:
        auth = HTTPBasicAuth(config.get('user'), config.get('pass'))
        r    = requests.get(
            f"https://{config['ip']}/redfish/v1/Systems/System.Embedded.1",
            auth=auth, verify=False, timeout=5
        )
        return {"success": True, "msg": "iDRAC OK"} if r.status_code == 200 else {"success": False, "msg": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "msg": str(e)}


def obtener_sensors_idrac(config):
    sensors = {
        "status":       "OK",
        "temperatures": [],
        "fans":         [],
        "power":        {"watts_current": 0, "supplies": []},
    }
    try:
        auth  = HTTPBasicAuth(config.get('user'), config.get('pass'))
        base  = f"https://{config['ip']}/redfish/v1/Chassis/System.Embedded.1"
        r_th  = requests.get(f"{base}/Thermal", auth=auth, verify=False, timeout=5)
        if r_th.status_code == 200:
            d = r_th.json()
            for t in d.get("Temperatures", []):
                sensors["temperatures"].append({"name": t.get("Name"), "value": t.get("ReadingCelsius"), "unit": "C", "status": "OK"})
            for f in d.get("Fans", []):
                sensors["fans"].append({"name": f.get("FanName") or f.get("Name"), "value": f.get("Reading"), "unit": "RPM", "status": "OK"})
        r_pw = requests.get(f"{base}/Power", auth=auth, verify=False, timeout=5)
        if r_pw.status_code == 200:
            d = r_pw.json()
            sensors["power"]["watts_current"] = safe_int(d.get("PowerControl", [{}])[0].get("PowerConsumedWatts"))
            for ps in d.get("PowerSupplies", []):
                sensors["power"]["supplies"].append({"name": ps.get("Name"), "watts": safe_float(ps.get("LastPowerOutputWatts")), "status": "OK"})
    except Exception as e:
        sensors["status"] = f"error: {str(e)}"
    return sensors


def obtener_storage_fisico_v3(config, log_func=None):
    """
    Recorre la API Redfish de iDRAC de forma secuencial con timeout global de 60s.
    Agrega collection_complete para que el servidor sepa si los datos son parciales.
    """
    if log_func:
        log_func(f"📦 Recolectando Storage iDRAC (RAID): {config.get('ip')}")

    storage_data = {
        "controllers":      [],
        "logical_volumes":  [],
        "physical_drives":  [],
        "collection_complete": False,
    }

    deadline = time.time() + 60          # timeout global de 60 segundos

    try:
        auth     = HTTPBasicAuth(config.get('user'), config.get('pass'))
        base_url = f"https://{config['ip']}/redfish/v1/Systems/System.Embedded.1/Storage"

        r_store = requests.get(base_url, auth=auth, verify=False, timeout=8)
        if r_store.status_code != 200:
            return storage_data

        members = r_store.json().get("Members", [])
        for m in members:
            if time.time() > deadline:
                if log_func:
                    log_func("⚠️ iDRAC Storage: timeout global alcanzado, datos parciales")
                return storage_data

            r_ctrl = requests.get(f"https://{config['ip']}{m['@odata.id']}", auth=auth, verify=False, timeout=5)
            if r_ctrl.status_code != 200:
                continue

            c         = r_ctrl.json()
            ctrl_name = c.get("Id", "Unknown Controller")
            status    = c.get("Status", {}).get("Health", "Unknown")
            storage_data["controllers"].append({
                "name":   ctrl_name,
                "status": status,
                "model":  c.get("Summary", {}).get("Model", "Dell PERC"),
            })

            # Volúmenes lógicos
            v_disks_url = f"https://{config['ip']}{m['@odata.id']}/Volumes"
            r_vd = requests.get(v_disks_url, auth=auth, verify=False, timeout=5)
            if r_vd.status_code == 200:
                for v in r_vd.json().get("Members", []):
                    if time.time() > deadline:
                        return storage_data
                    r_v_info = requests.get(f"https://{config['ip']}{v['@odata.id']}", auth=auth, verify=False, timeout=5)
                    if r_v_info.status_code == 200:
                        vd = r_v_info.json()
                        storage_data["logical_volumes"].append({
                            "name":       vd.get("Name"),
                            "raid_level": vd.get("VolumeType"),
                            "size_gb":    round(safe_int(vd.get("CapacityBytes")) / 1073741824, 2),
                            "status":     vd.get("Status", {}).get("Health", "Unknown"),
                        })

            # Discos físicos
            for d in c.get("Drives", []):
                if time.time() > deadline:
                    return storage_data
                r_d_info = requests.get(f"https://{config['ip']}{d['@odata.id']}", auth=auth, verify=False, timeout=5)
                if r_d_info.status_code == 200:
                    drive = r_d_info.json()
                    storage_data["physical_drives"].append({
                        "slot":       drive.get("Id"),
                        "model":      drive.get("Model"),
                        "size_gb":    round(safe_int(drive.get("CapacityBytes")) / 1073741824, 2),
                        "media_type": drive.get("MediaType"),
                        "status":     drive.get("Status", {}).get("Health", "Unknown"),
                    })

        storage_data["collection_complete"] = True

    except Exception as e:
        if log_func:
            log_func(f"❌ Error RAID iDRAC: {e}")
        storage_data["error"] = str(e)

    return storage_data


# ---------------------------------------------------------------------------
# WMI — VMs y Workstations
# Corrección clave: timeout por VM via thread, muestras de disco reducidas,
# state_reason para distinguir offline real de error de conexión.
# ---------------------------------------------------------------------------
def test_connection_vm_wmi(vm_info):
    ip = vm_info.get("ip")
    if not verificar_puerto(ip, 135):
        return {"success": False, "msg": "Puerto 135 cerrado"}
    try:
        pythoncom.CoInitialize()
        c = wmi.WMI(
            ip,
            user=vm_info.get("user"),
            password=vm_info.get("pass"),
            impersonation_level="Impersonate",
            authentication_level="Pktprivacy",
        )
        hostname = c.Win32_ComputerSystem()[0].Name
        return {"success": True, "msg": f"WMI OK: {hostname}", "hostname": hostname}
    except Exception as e:
        return {"success": False, "msg": str(e)}
    finally:
        pythoncom.CoUninitialize()


def _recolectar_wmi_interno(vm_info, log_func):
    """
    Ejecutado en un thread separado con timeout controlado desde obtener_vm_data().
    Retorna el objeto vm_obj completo.
    """
    ip             = vm_info.get("ip")
    tipo_maquina   = vm_info.get("type", "vm")
    nombre_manual  = vm_info.get("nombre", "").strip()

    vm_obj = {
        "id":                nombre_manual if nombre_manual else ip,
        "type":              tipo_maquina,
        "state":             "Offline",
        "state_reason":      "unknown",
        "telemetry":         {},
        "storage":           [],
        "application_layer": {"services": []},
    }

    if not verificar_puerto(ip, 135):
        vm_obj["state_reason"] = "port_closed"
        return vm_obj

    try:
        pythoncom.CoInitialize()
        c = wmi.WMI(
            ip,
            user=vm_info.get("user"),
            password=vm_info.get("pass"),
            impersonation_level="Impersonate",
            authentication_level="Pktprivacy",
        )

        hostname_real    = c.Win32_ComputerSystem()[0].Name
        vm_obj["id"]     = nombre_manual if nombre_manual else (hostname_real or ip)
        vm_obj["state"]  = "Online"
        vm_obj["state_reason"] = "ok"

        os_sys = c.Win32_OperatingSystem()[0]
        t_ram  = safe_int(os_sys.TotalVisibleMemorySize)
        u_ram  = t_ram - safe_int(os_sys.FreePhysicalMemory)

        vm_obj["telemetry"] = {
            "cpu": {
                "usage_percent": safe_float(
                    sum(safe_int(x.LoadPercentage) for x in c.Win32_Processor()) /
                    max(1, len(c.Win32_Processor()))
                )
            },
            "ram": {
                "total_gb":      round(t_ram / 1048576, 2),
                "used_gb":       round(u_ram / 1048576, 2),
                "usage_percent": round((u_ram / t_ram) * 100, 2) if t_ram > 0 else 0,
            },
            "uptime_seconds": int((datetime.now() - parse_wmi_date(os_sys.LastBootUpTime)).total_seconds()),
        }

        # --- Latencia de disco: 3 muestras con 5s de espera (antes eran 6x10s) ---
        perf_map_promedio = {}
        try:
            muestras    = 3
            espera_seg  = 5
            perf_map_temp = {}
            for i in range(muestras):
                for p in c.Win32_PerfFormattedData_PerfDisk_LogicalDisk():
                    letra = p.Name
                    if letra not in perf_map_temp:
                        perf_map_temp[letra] = []
                    latencia_ms = safe_float(p.AvgDisksecPerTransfer) * 1000.0
                    perf_map_temp[letra].append(latencia_ms)
                if i < muestras - 1:
                    time.sleep(espera_seg)

            for letra, valores in perf_map_temp.items():
                reales = [v for v in valores if v > 0]
                perf_map_promedio[letra] = sum(reales) / len(reales) if reales else 0.0
        except Exception as disk_err:
            if log_func:
                log_func(f"⚠️ WMI disco ({ip}): {disk_err}")

        for d in c.Win32_LogicalDisk(DriveType=3):
            letra     = d.DeviceID
            latencia  = perf_map_promedio.get(letra, 0.0)
            if latencia > 50.0:
                estado_perf = "Critical"
            elif latencia > 20.0:
                estado_perf = "Warning"
            else:
                estado_perf = "OK"

            vm_obj["storage"].append({
                "mount_point":   letra,
                "total_gb":      round(safe_int(d.Size)      / 1073741824, 2),
                "free_gb":       round(safe_int(d.FreeSpace) / 1073741824, 2),
                "usage_percent": round(((safe_int(d.Size) - safe_int(d.FreeSpace)) / max(1, safe_int(d.Size))) * 100, 1),
                "performance":   {"latency_ms": round(latencia, 2), "status": estado_perf},
            })

        # --- Servicios ---
        proc_map = {safe_int(p.IDProcess): p for p in c.Win32_PerfFormattedData_PerfProc_Process()}
        servicios_cfg = vm_info.get("servicios", "")
        if isinstance(servicios_cfg, list):
            servicios = [s.strip() for s in servicios_cfg if s.strip()]
        else:
            servicios = [s.strip() for s in servicios_cfg.split(",") if s.strip()]

        for s in c.Win32_Service():
            if s.Name in servicios:
                v = {"pid": safe_int(s.ProcessId), "health": "OK", "cpu_percent": 0.0, "ram_mb": 0.0, "threads": 0, "handles": 0}
                if v["pid"] in proc_map:
                    p = proc_map[v["pid"]]
                    v.update({
                        "cpu_percent": safe_float(p.PercentProcessorTime),
                        "ram_mb":      round(safe_int(p.WorkingSet) / 1048576, 1),
                        "threads":     safe_int(p.ThreadCount),
                        "handles":     safe_int(p.HandleCount),
                    })
                vm_obj["application_layer"]["services"].append({
                    "name": s.Name, "display_name": s.DisplayName, "state": s.State, "vital_signs": v,
                })

    except Exception as e:
        vm_obj["state"]        = "Offline"
        vm_obj["state_reason"] = "wmi_error"
        vm_obj["wmi_error"]    = str(e)
        if log_func:
            log_func(f"⚠️ Error WMI ({tipo_maquina.upper()}) {ip}: {e}")
    finally:
        pythoncom.CoUninitialize()

    return vm_obj


def obtener_vm_data(args):
    """
    Wrapper con timeout global de 90s por VM para evitar threads colgados.
    """
    vm_info, log_func = args
    ip            = vm_info.get("ip", "?")
    nombre_manual = vm_info.get("nombre", "").strip()

    resultado_holder = [None]

    def _worker():
        resultado_holder[0] = _recolectar_wmi_interno(vm_info, log_func)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=90)

    if resultado_holder[0] is None:
        if log_func:
            log_func(f"⏱️ Timeout WMI ({ip}): no respondió en 90s")
        return {
            "id":                nombre_manual if nombre_manual else ip,
            "type":              vm_info.get("type", "vm"),
            "state":             "Offline",
            "state_reason":      "wmi_timeout",
            "telemetry":         {},
            "storage":           [],
            "application_layer": {"services": []},
        }

    return resultado_holder[0]


# ---------------------------------------------------------------------------
# CICLO PRINCIPAL DEL AGENTE
# ---------------------------------------------------------------------------
def ejecutar_ciclo_agente(config, log_callback=None):
    """
    Recolecta todos los módulos habilitados, construye el envelope y lo envía.
    Incluye collection_meta para que el servidor distinga módulos desactivados
    de módulos con error.
    El checkpoint SQL se guarda SOLO si el POST es exitoso.
    """

    collection_meta = {
        "proxmox": {"enabled": config.get("enabled_proxmox", False), "status": "disabled"},
        "idrac":   {"enabled": config.get("enabled_idrac",   False), "status": "disabled"},
        "wmi":     {"enabled": config.get("enabled_vms",     False), "status": "disabled"},
        "sql":     {"enabled": config.get("enabled_sql",     False), "status": "disabled"},
    }

    reporte = {
        "envelope": {
            "schema_version": "4.1",
            "agent_version":  "4.1",
            "hospital_id":    config.get("hospital_id", "UNKNOWN"),
            "timestamp":      datetime.now().isoformat(),
        },
        "collection_meta":  collection_meta,
        "physical_layer":   {},
        "virtual_layer":    [],
    }

    # --- Capa física: Proxmox o VMware ---
    if config.get("enabled_proxmox"):
        hyper_cfg  = config.get("proxmox", {})
        hyper_type = hyper_cfg.get("type", "proxmox")
        try:
            if hyper_type == "vmware":
                data = obtener_vmware_layer(hyper_cfg, log_func=log_callback)
                reporte["physical_layer"] = data
                # Las VMs de VMware se incorporan al virtual_layer
                for vm_vm in data.pop("vms", []):
                    reporte["virtual_layer"].append(vm_vm)
            else:
                reporte["physical_layer"] = obtener_physical_layer(hyper_cfg)

            collection_meta["proxmox"]["status"] = "ok"
        except Exception as e:
            collection_meta["proxmox"]["status"] = "error"
            collection_meta["proxmox"]["error"]  = str(e)
            if log_callback:
                log_callback(f"❌ Error capa física ({hyper_type}): {e}")

        # iDRAC solo tiene sentido sobre hardware físico (Proxmox/bare-metal)
        if config.get("enabled_idrac") and hyper_type != "vmware":
            try:
                reporte["physical_layer"]["sensors"]       = obtener_sensors_idrac(config["idrac"])
                reporte["physical_layer"]["storage_layer"] = obtener_storage_fisico_v3(config["idrac"], log_callback)
                collection_meta["idrac"]["status"] = "ok"
                if not reporte["physical_layer"]["storage_layer"].get("collection_complete"):
                    collection_meta["idrac"]["status"] = "partial"
            except Exception as e:
                collection_meta["idrac"]["status"] = "error"
                collection_meta["idrac"]["error"]  = str(e)
                if log_callback:
                    log_callback(f"❌ Error iDRAC: {e}")

    # --- Capa virtual: VMs/WS via WMI ---
    if config.get("enabled_vms") and config.get("vms"):
        try:
            with ThreadPoolExecutor(max_workers=5) as ex:
                wmi_results = list(ex.map(
                    obtener_vm_data,
                    [(vm, log_callback) for vm in config.get("vms", [])],
                ))
            reporte["virtual_layer"].extend(wmi_results)
            errores_wmi = sum(1 for v in wmi_results if v.get("state_reason") not in ("ok", "port_closed"))
            collection_meta["wmi"]["status"] = "partial" if errores_wmi else "ok"
            collection_meta["wmi"]["total"]  = len(wmi_results)
            collection_meta["wmi"]["errors"] = errores_wmi
        except Exception as e:
            collection_meta["wmi"]["status"] = "error"
            collection_meta["wmi"]["error"]  = str(e)
            if log_callback:
                log_callback(f"❌ Error capa WMI: {e}")

    # --- Métricas SQL ---
    if "_sql_data_payload" in config:
        payload = config["_sql_data_payload"]
        reporte["application_metrics"] = payload.get("application_metrics", payload)
        collection_meta["sql"]["status"] = "ok"
        collection_meta["sql"]["block_start"] = payload.get("application_metrics", {}).get("start_time_extraction", "")
        collection_meta["sql"]["block_end"]   = payload.get("application_metrics", {}).get("end_time_extraction", "")

    # --- Envío al servidor central ---
    try:
        r = requests.post(
            config.get("central_url"),
            json=reporte,
            timeout=25,
            verify=False,
            headers={"Authorization": f"Bearer {config.get('auth_token', '')}"},
        )
        r.raise_for_status()

        # Checkpoint guardado SOLO aquí, tras confirmación de envío exitoso
        if "_sql_data_payload" in config:
            checkpoint_dt = config["_sql_data_payload"].get("_checkpoint_to_save")
            if checkpoint_dt:
                save_checkpoint(checkpoint_dt)
                if log_callback:
                    log_callback(f"💾 Checkpoint SQL guardado: {checkpoint_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        return {"status": "OK", "timestamp": datetime.now().strftime("%H:%M:%S")}

    except Exception as e:
        return {
            "status":    "Error",
            "error":     str(e),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }