import requests
import wmi
import pythoncom
import time
import socket
import urllib3
import ssl
import json
import pyodbc
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from requests.auth import HTTPBasicAuth
import security

DATA_DIR = security.get_app_data_path()
SQL_CHECKPOINT_FILE = os.path.join(DATA_DIR, ".sql_checkpoint")

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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_DISK_CACHE = {}

def get_last_checkpoint():
    if os.path.exists(SQL_CHECKPOINT_FILE):
        try:
            with open(SQL_CHECKPOINT_FILE, 'r') as f:
                return datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M:%S")
        except: pass
    return None

def save_checkpoint(dt):
    try:
        with open(SQL_CHECKPOINT_FILE, 'w') as f:
            f.write(dt.strftime("%Y-%m-%d %H:%M:%S"))
    except: pass

def reset_checkpoint():
    if os.path.exists(SQL_CHECKPOINT_FILE):
        try: os.remove(SQL_CHECKPOINT_FILE)
        except: pass

def extraer_metricas_sql(sql_config, log_func=None):
    if not sql_config or not sql_config.get("host"): return None
        
    conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={sql_config['host']};DATABASE={sql_config['db']};UID={sql_config['user']};PWD={sql_config['pass']}"
    conn_str_fallback = f"DRIVER={{SQL Server}};SERVER={sql_config['host']};DATABASE={sql_config['db']};UID={sql_config['user']};PWD={sql_config['pass']}"
    
    conn = None
    try:
        try:
            conn = pyodbc.connect(conn_str, timeout=10)
        except pyodbc.Error:
            conn = pyodbc.connect(conn_str_fallback, timeout=10)

        cursor = conn.cursor()
        
        # Matemáticas
        executions_per_day = int(sql_config.get("executions_per_day", 3))
        if executions_per_day <= 0: executions_per_day = 3
        interval_hours = 24.0 / executions_per_day
        
        ahora = datetime.now()
        ultimo_checkpoint = get_last_checkpoint()
        
        # Lógica de Inicio (Backfill Histórico)
        if not ultimo_checkpoint:
            historical_start = sql_config.get("historical_start_date")
            if historical_start:
                try:
                    # Empieza a las 00:00 del día configurado
                    target_start_time = datetime.strptime(historical_start, "%Y-%m-%d")
                    if log_func: log_func(f"🕰️ INICIANDO BACKFILL HISTÓRICO desde {historical_start}")
                except Exception:
                    target_start_time = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                # Si no hay fecha, empieza hoy a las 00:00
                target_start_time = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            target_start_time = ultimo_checkpoint
            
        target_end_time = target_start_time + timedelta(hours=interval_hours)
        
        # Si el bloque apunta al futuro (aún no se completó en la vida real), esperamos.
        if target_end_time > ahora:
            return None
            
        start_date_sql = target_start_time.strftime("%Y-%m-%dT%H:%M:%S")
        end_date_sql = target_end_time.strftime("%Y-%m-%dT%H:%M:%S")
        
        # Solo imprimimos log especial si estamos muy atrasados
        if (ahora - target_end_time).total_seconds() > 86400:
            if log_func: log_func(f"⏳ Backfill: Recuperando bloque [{start_date_sql} >> {end_date_sql}]...")
        else:
            if log_func: log_func(f"⚙️ SQL: Extrayendo bloque regular [{start_date_sql} >> {end_date_sql}]...")

        query_dinamica = SQL_QUERY.replace("{start_date_sql}", start_date_sql).replace("{end_date_sql}", end_date_sql)
        
        cursor.execute(query_dinamica)
        row = cursor.fetchone()
        if not row: return None
            
        json_fragments = []
        while row:
            json_fragments.append(row[0])
            row = cursor.fetchone()
            
        data_json = json.loads("".join(json_fragments))
        
        # SQL devuelve los datos encapsulados dentro de "application_metrics".
        # Inyectamos los metadatos DENTRO de ese mismo nivel para que viajen al servidor.
        if "application_metrics" not in data_json:
            data_json["application_metrics"] = {}
            
        data_json["application_metrics"]["extraction_interval_hours"] = interval_hours
        data_json["application_metrics"]["start_time_extraction"] = start_date_sql
        data_json["application_metrics"]["end_time_extraction"] = end_date_sql
        
        save_checkpoint(target_end_time)
        return data_json
    except Exception as e:
        if log_func: log_func(f"❌ Error conectando a SQL Server: {e}")
        return None
    finally:
        if conn: conn.close()

# ... (El resto de las funciones safe_int, wmi, proxmox, etc. se mantienen igual) ...

def safe_int(value):
    try: return int(value) if value is not None else 0
    except: return 0

def safe_float(value, decimals=2):
    try: return round(float(value), decimals) if value is not None else 0.0
    except: return 0.0

def verificar_puerto(ip, puerto, timeout=2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, int(puerto)))
        sock.close()
        return result == 0
    except: return False

def parse_wmi_date(wmi_date):
    try: return datetime.strptime(wmi_date.split('.')[0], "%Y%m%d%H%M%S")
    except: return datetime.now()

def test_connection_proxmox(config):
    host = config.get("host") or config.get("ip")
    if not host or not verificar_puerto(host, 8006): return {"success": False, "msg": "Puerto 8006 cerrado"}
    try:
        auth = {"username": config['user'], "password": config['pass']}
        r = requests.post(f"https://{host}:8006/api2/json/access/ticket", data=auth, verify=False, timeout=5)
        return {"success": True, "msg": "Proxmox OK"} if r.status_code == 200 else {"success": False, "msg": "Credenciales inválidas"}
    except Exception as e: return {"success": False, "msg": str(e)}

def test_connection_idrac(config):
    try:
        auth = HTTPBasicAuth(config.get('user'), config.get('pass'))
        r = requests.get(f"https://{config['ip']}/redfish/v1/Systems/System.Embedded.1", auth=auth, verify=False, timeout=5)
        return {"success": True, "msg": "iDRAC OK"} if r.status_code == 200 else {"success": False, "msg": "Fallo iDRAC"}
    except Exception as e: return {"success": False, "msg": str(e)}

def test_connection_vm_wmi(vm_info):
    ip = vm_info.get("ip")
    if not verificar_puerto(ip, 135): return {"success": False, "msg": "Puerto 135 cerrado"}
    try:
        pythoncom.CoInitialize()
        c = wmi.WMI(ip, 
                    user=vm_info.get("user"), 
                    password=vm_info.get("pass"),
                    impersonation_level="Impersonate", 
                    authentication_level="Pktprivacy")
        
        hostname = c.Win32_ComputerSystem()[0].Name
        return {"success": True, "msg": f"WMI OK: {hostname}", "hostname": hostname}
    except Exception as e: 
        return {"success": False, "msg": str(e)}
    finally: 
        pythoncom.CoUninitialize()

def test_connection_vmware(config):
    return {"success": False, "msg": "Módulo VMware no configurado localmente"}

def obtener_physical_layer(config):
    host = config.get("host") or config.get("ip")
    res = {"host_info": {"hostname": host, "type": "proxmox", "model": "Unknown", "uptime_seconds": 0}, "telemetry": {}, "sensors": {}}
    try:
        auth = {"username": config['user'], "password": config['pass']}
        r_auth = requests.post(f"https://{host}:8006/api2/json/access/ticket", data=auth, verify=False, timeout=5)
        if r_auth.status_code == 200:
            tk = r_auth.json()['data']
            r_node = requests.get(f"https://{host}:8006/api2/json/nodes/{config.get('node', 'pve')}/status", cookies={"PVEAuthCookie": tk['ticket']}, headers={"CSRFPreventionToken": tk['CSRFPreventionToken']}, verify=False, timeout=5)
            raw = r_node.json()['data']
            res["host_info"].update({"model": raw.get("cpuinfo", {}).get("model"), "uptime_seconds": safe_int(raw.get("uptime"))})
            u_mem, t_mem = safe_int(raw.get("memory",{}).get("used")), safe_int(raw.get("memory",{}).get("total"))
            res["telemetry"] = {"cpu": {"usage_percent": safe_float(raw.get("cpu", 0)*100)}, "ram": {"total_gb": round(t_mem/1073741824, 2), "used_gb": round(u_mem/1073741824, 2), "usage_percent": safe_float(u_mem/t_mem*100 if t_mem > 0 else 0)}}
    except: pass
    return res

def obtener_sensors_idrac(config):
    sensors = {"status": "OK", "temperatures": [], "fans": [], "power": {"watts_current": 0, "supplies": []}}
    try:
        auth = HTTPBasicAuth(config.get('user'), config.get('pass'))
        base = f"https://{config['ip']}/redfish/v1/Chassis/System.Embedded.1"
        r_th = requests.get(f"{base}/Thermal", auth=auth, verify=False, timeout=5)
        if r_th.status_code == 200:
            d = r_th.json()
            for t in d.get("Temperatures", []): sensors["temperatures"].append({"name": t.get("Name"), "value": t.get("ReadingCelsius"), "unit": "C", "status": "OK"})
            for f in d.get("Fans", []): sensors["fans"].append({"name": f.get("FanName") or f.get("Name"), "value": f.get("Reading"), "unit": "RPM", "status": "OK"})
        r_pw = requests.get(f"{base}/Power", auth=auth, verify=False, timeout=5)
        if r_pw.status_code == 200:
            d = r_pw.json()
            sensors["power"]["watts_current"] = safe_int(d.get("PowerControl",[{}])[0].get("PowerConsumedWatts"))
            for ps in d.get("PowerSupplies", []): sensors["power"]["supplies"].append({"name": ps.get("Name"), "watts": safe_float(ps.get("LastPowerOutputWatts")), "status": "OK"})
    except: pass
    return sensors

def obtener_vm_data(args):
    vm_info, log_func = args
    ip = vm_info.get("ip")
    tipo_maquina = vm_info.get("type", "vm") 
    nombre_manual = vm_info.get("nombre", "").strip()
    
    vm_obj = {
        "id": nombre_manual if nombre_manual else ip, 
        "type": tipo_maquina, 
        "state": "Offline", 
        "telemetry": {}, 
        "storage": [], 
        "application_layer": {"services": []}
    }
    
    if not verificar_puerto(ip, 135): 
        return vm_obj
        
    try:
        pythoncom.CoInitialize()
        c = wmi.WMI(ip, 
                    user=vm_info.get("user"), 
                    password=vm_info.get("pass"),
                    impersonation_level="Impersonate", 
                    authentication_level="Pktprivacy")
        
        hostname_real = c.Win32_ComputerSystem()[0].Name
        vm_obj["id"] = nombre_manual if nombre_manual else (hostname_real if hostname_real else ip)
        vm_obj["state"] = "Online"
        
        os_sys = c.Win32_OperatingSystem()[0]
        t_ram = safe_int(os_sys.TotalVisibleMemorySize)
        u_ram = t_ram - safe_int(os_sys.FreePhysicalMemory)
        
        vm_obj["telemetry"] = {
            "cpu": {"usage_percent": safe_float(sum([safe_int(x.LoadPercentage) for x in c.Win32_Processor()])/max(1, len(c.Win32_Processor())))}, 
            "ram": {"total_gb": round(t_ram/1048576, 2), "used_gb": round(u_ram/1048576, 2), "usage_percent": round((u_ram/t_ram)*100, 2)}, 
            "uptime_seconds": int((datetime.now()-parse_wmi_date(os_sys.LastBootUpTime)).total_seconds())
        }
        
        perf_map_temp = {}
        perf_map_promedio = {}
        
        try:
            muestras = 6      
            espera_seg = 10   
            for i in range(muestras):
                for p in c.Win32_PerfFormattedData_PerfDisk_LogicalDisk():
                    letra = p.Name
                    if letra not in perf_map_temp: perf_map_temp[letra] = []
                    latencia_ms = safe_float(p.AvgDisksecPerTransfer) * 1000.0
                    perf_map_temp[letra].append(latencia_ms)
                if i < muestras - 1: time.sleep(espera_seg)
            
            for letra, valores in perf_map_temp.items():
                valores_reales = [v for v in valores if v > 0]
                if len(valores_reales) > 0: perf_map_promedio[letra] = sum(valores_reales) / len(valores_reales)
                else: perf_map_promedio[letra] = 0.0
        except Exception: pass

        for d in c.Win32_LogicalDisk(DriveType=3):
            letra = d.DeviceID
            latencia = perf_map_promedio.get(letra, 0.0)
            
            estado_perf = "OK"
            if latencia > 50.0: estado_perf = "Critical"
            elif latencia > 20.0: estado_perf = "Warning"

            vm_obj["storage"].append({
                "mount_point": letra, 
                "total_gb": round(safe_int(d.Size)/1073741824, 2), 
                "free_gb": round(safe_int(d.FreeSpace)/1073741824, 2), 
                "usage_percent": round(((safe_int(d.Size)-safe_int(d.FreeSpace))/max(1,safe_int(d.Size)))*100, 1), 
                "performance": {"latency_ms": round(latencia, 2), "status": estado_perf}
            })

        proc_map = {safe_int(p.IDProcess): p for p in c.Win32_PerfFormattedData_PerfProc_Process()}
        servicios = [s.strip() for s in vm_info.get("servicios", "").split(",") if s.strip()]
        
        for s in c.Win32_Service():
            if s.Name in servicios:
                v = {"pid": safe_int(s.ProcessId), "health": "OK", "cpu_percent": 0.0, "ram_mb": 0.0, "threads": 0, "handles": 0}
                if v["pid"] in proc_map:
                    p = proc_map[v["pid"]]
                    v.update({
                        "cpu_percent": safe_float(p.PercentProcessorTime), 
                        "ram_mb": round(safe_int(p.WorkingSet)/1048576, 1), 
                        "threads": safe_int(p.ThreadCount), 
                        "handles": safe_int(p.HandleCount)
                    })
                vm_obj["application_layer"]["services"].append({
                    "name": s.Name, "display_name": s.DisplayName, "state": s.State, "vital_signs": v
                })
    except Exception as e: 
        if log_func: log_func(f"⚠️ Error en WMI ({tipo_maquina.upper()}) {ip}: {str(e)}")
    finally: 
        pythoncom.CoUninitialize()
        
    return vm_obj

def obtener_storage_fisico_v3(config, log_func):
    if log_func: log_func(f"Recolectando Storage iDRAC (RAID): {config.get('ip')}")
    storage_data = {"controllers": [], "logical_volumes": [], "physical_drives": []}
    
    try:
        auth = HTTPBasicAuth(config.get('user'), config.get('pass'))
        base_url = f"https://{config['ip']}/redfish/v1/Systems/System.Embedded.1/Storage"
        
        r_store = requests.get(base_url, auth=auth, verify=False, timeout=8)
        if r_store.status_code == 200:
            members = r_store.json().get("Members", [])
            for m in members:
                r_ctrl = requests.get(f"https://{config['ip']}{m['@odata.id']}", auth=auth, verify=False, timeout=5)
                if r_ctrl.status_code == 200:
                    c = r_ctrl.json()
                    ctrl_name = c.get("Id", "Unknown Controller")
                    status = c.get("Status", {}).get("Health", "Unknown")
                    storage_data["controllers"].append({"name": ctrl_name, "status": status, "model": c.get("Summary", {}).get("Model", "Dell PERC")})

                    v_disks_url = f"https://{config['ip']}{m['@odata.id']}/Volumes"
                    r_vd = requests.get(v_disks_url, auth=auth, verify=False, timeout=5)
                    if r_vd.status_code == 200:
                        for v in r_vd.json().get("Members", []):
                            r_v_info = requests.get(f"https://{config['ip']}{v['@odata.id']}", auth=auth, verify=False, timeout=5)
                            if r_v_info.status_code == 200:
                                vd = r_v_info.json()
                                storage_data["logical_volumes"].append({
                                    "name": vd.get("Name"), "raid_level": vd.get("VolumeType"),
                                    "size_gb": round(safe_int(vd.get("CapacityBytes")) / 1073741824, 2), "status": vd.get("Status", {}).get("Health", "Unknown")
                                })

                    for d in c.get("Drives", []):
                        r_d_info = requests.get(f"https://{config['ip']}{d['@odata.id']}", auth=auth, verify=False, timeout=5)
                        if r_d_info.status_code == 200:
                            drive = r_d_info.json()
                            storage_data["physical_drives"].append({
                                "slot": drive.get("Id"), "model": drive.get("Model"),
                                "size_gb": round(safe_int(drive.get("CapacityBytes")) / 1073741824, 2),
                                "media_type": drive.get("MediaType"), "status": drive.get("Status", {}).get("Health", "Unknown")
                            })
        return storage_data
    except Exception as e:
        if log_func: log_func(f"Error RAID iDRAC: {e}")
        return storage_data

def ejecutar_ciclo_agente(config, log_callback=None):
    reporte = {
        "envelope": {
            "schema_version": "4.0", 
            "agent_version": "4.0", 
            "hospital_id": config.get("hospital_id", "P03"), 
            "timestamp": datetime.now().isoformat()
        }, 
        "physical_layer": {}, 
        "virtual_layer": []
    }
    
    if config.get("enabled_proxmox"):
        reporte["physical_layer"] = obtener_physical_layer(config["proxmox"])
        if config.get("enabled_idrac"):
            reporte["physical_layer"]["sensors"] = obtener_sensors_idrac(config["idrac"])
            reporte["physical_layer"]["storage_layer"] = obtener_storage_fisico_v3(config["idrac"], log_callback)
            
    if config.get("enabled_vms"):
        with ThreadPoolExecutor(max_workers=5) as ex:
            reporte["virtual_layer"] = list(ex.map(obtener_vm_data, [(vm, log_callback) for vm in config.get("vms", [])]))

    if "_sql_data_payload" in config:
        # Extraemos exclusivamente el bloque interno (que ahora sí tiene las fechas inyectadas)
        reporte["application_metrics"] = config["_sql_data_payload"].get("application_metrics", config["_sql_data_payload"])
    try:
        r = requests.post(config.get("central_url"), json=reporte, timeout=25, verify=False)
        return {"status": "OK", "timestamp": datetime.now().strftime("%H:%M:%S")}
    except Exception as e:
        return {"status": "Error", "error": str(e), "timestamp": datetime.now().strftime("%H:%M:%S")}