import sys
import os

# --- FIX CRÍTICO PARA MODO --noconsole ---
# Redirigimos la salida estándar a un agujero negro para que Eel/Bottle no crasheen al intentar imprimir.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
# -----------------------------------------

import eel
import json
import subprocess
import psutil
import traceback
import security
import agent_logic

DATA_DIR = security.get_app_data_path() 
CONFIG_FILE = os.path.join(DATA_DIR, "monitor_config.json")
LOG_FILE = os.path.join(DATA_DIR, "activity.log")

def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)

eel.init(resource_path('web'))

@eel.expose
def cargar_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if "auth_token" in data: data["auth_token"] = security.desencriptar(data["auth_token"])
            if "proxmox" in data: data["proxmox"]["pass"] = security.desencriptar(data["proxmox"]["pass"])
            if "idrac" in data: data["idrac"]["pass"] = security.desencriptar(data["idrac"]["pass"])
            
            if "sql" in data and "pass" in data["sql"]: 
                data["sql"]["pass"] = security.desencriptar(data["sql"]["pass"])

            if "vms" in data:
                for vm in data["vms"]:
                    if "pass" in vm: vm["pass"] = security.desencriptar(vm["pass"])
            return data
        except Exception:
            pass
    return {}

@eel.expose
def guardar_config(config):
    try:
        if config.get("auth_token"): config["auth_token"] = security.encriptar(config["auth_token"])
        if config.get("proxmox"): config["proxmox"]["pass"] = security.encriptar(config["proxmox"]["pass"])
        if config.get("idrac"): config["idrac"]["pass"] = security.encriptar(config["idrac"]["pass"])
        
        if config.get("sql") and config["sql"].get("pass"): 
            config["sql"]["pass"] = security.encriptar(config["sql"]["pass"])

        if config.get("vms"):
            for vm in config["vms"]:
                if vm.get("pass"): vm["pass"] = security.encriptar(vm["pass"])
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        
        toggle_monitoreo(False)
        toggle_monitoreo(True)
        return {"success": True}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@eel.expose
def toggle_monitoreo(activar):
    try:
        if activar:
            res = subprocess.run(["schtasks", "/Run", "/TN", "TecnoMonitor_AutoStart"], capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return {"success": False, "msg": "Requiere privilegios de Administrador para iniciar el servicio."}
        else:
            res = subprocess.run(["taskkill", "/F", "/IM", "TecnoMonitorService.exe", "/T"], capture_output=True, text=True, timeout=5)
            if res.returncode == 5:
                return {"success": False, "msg": "Requiere privilegios de Administrador para detener el servicio."}
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {"success": False, "msg": "El comando tardó demasiado (Timeout)."}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@eel.expose
def limpiar_log():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("--- Log limpiado por el administrador ---\n")
        return True
    except:
        return False

@eel.expose
def check_service_status():
    try:
        for proc in psutil.process_iter(['name', 'cmdline']):
            name = proc.info.get('name')
            cmdline = proc.info.get('cmdline')
            if name == "TecnoMonitorService.exe":
                return True
            if cmdline and any("headless_service.py" in arg for arg in cmdline):
                return True
    except Exception:
        pass
    return False

@eel.expose
def leer_log_delta(posicion_anterior):
    if not os.path.exists(LOG_FILE):
        return {"content": "", "pos": 0}
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)
            tamano_total = f.tell()
            if posicion_anterior > tamano_total:
                posicion_anterior = 0
            f.seek(posicion_anterior)
            contenido = f.read()
            nueva_posicion = f.tell()
            return {"content": contenido, "pos": nueva_posicion}
    except Exception as e:
        return {"content": f"Error log: {str(e)}\n", "pos": posicion_anterior}

@eel.expose
def test_proxmox_gui(data): return agent_logic.test_connection_proxmox(data)

@eel.expose
def test_vmware_gui(data): return agent_logic.test_connection_vmware(data)

@eel.expose
def test_idrac_gui(data): return agent_logic.test_connection_idrac(data)

@eel.expose
def test_vm_gui(data): return agent_logic.test_connection_vm_wmi(data)

@eel.expose
def probar_conexion_central(url):
    import requests
    try:
        r = requests.get(url, timeout=5, verify=False)
        return {"success": True, "code": r.status_code}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@eel.expose
def reset_historial_sql():
    try:
        agent_logic.reset_checkpoint()
        return True
    except:
        return False

if __name__ == '__main__':
    try:
        # Iniciamos la GUI. 
        eel.start('index.html', size=(1100, 900), port=0)
    except Exception as e:
        # Si de verdad hay un error (ej. Chrome o Edge no instalados), ahora sí lo registramos en el log
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"--- CRASH GUI: No se pudo abrir la interfaz ({str(e)}) ---\n")