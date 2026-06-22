import sys
import os

# --- FIX CRÍTICO PARA MODO --noconsole ---
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
# -----------------------------------------

import eel
import json
import hashlib
import subprocess
import psutil
import security
import agent_logic

DATA_DIR    = security.get_app_data_path()
CONFIG_FILE = os.path.join(DATA_DIR, "monitor_config.json")
LOG_FILE    = os.path.join(DATA_DIR, "activity.log")

# ---------------------------------------------------------------------------
# Hash de la contraseña de acceso a la GUI.
# Para cambiarla: python -c "import hashlib; print(hashlib.sha256(b'NuevaClave').hexdigest())"
# Clave actual: TM4dm1n
# ---------------------------------------------------------------------------
_ADMIN_HASH = hashlib.sha256(b"TM4dm1n").hexdigest()


def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)


eel.init(resource_path('web'))


# ---------------------------------------------------------------------------
# AUTENTICACIÓN
# ---------------------------------------------------------------------------
@eel.expose
def verificar_clave(clave_ingresada: str) -> bool:
    """El frontend envía la clave; Python compara el hash. Nunca viaja la clave real."""
    return hashlib.sha256(clave_ingresada.encode()).hexdigest() == _ADMIN_HASH


# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------
def _desencriptar_config(data: dict) -> dict:
    """Desencripta todas las credenciales de un dict de configuración."""
    if data.get("auth_token"):
        data["auth_token"] = security.desencriptar(data["auth_token"])

    if isinstance(data.get("proxmox"), dict):
        px = data["proxmox"]
        if px.get("pass"):
            px["pass"] = security.desencriptar(px["pass"])

    if isinstance(data.get("idrac"), dict):
        if data["idrac"].get("pass"):
            data["idrac"]["pass"] = security.desencriptar(data["idrac"]["pass"])

    if isinstance(data.get("sql"), dict) and data["sql"].get("pass"):
        data["sql"]["pass"] = security.desencriptar(data["sql"]["pass"])

    if isinstance(data.get("vms"), list):
        for vm in data["vms"]:
            if isinstance(vm, dict) and vm.get("pass"):
                vm["pass"] = security.desencriptar(vm["pass"])

    # --- NUEVO: Desencriptar Mirth Connect ---
    if isinstance(data.get("mirth_servers"), list):
        for m in data["mirth_servers"]:
            if isinstance(m, dict) and m.get("pass"):
                m["pass"] = security.desencriptar(m["pass"])

    return data


@eel.expose
def cargar_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return _desencriptar_config(data)
    except Exception as e:
        return {"_error": str(e)}


@eel.expose
def guardar_config(config: dict):
    try:
        # Encriptar credenciales antes de persistir
        if config.get("auth_token"):
            config["auth_token"] = security.encriptar(config["auth_token"])

        if isinstance(config.get("proxmox"), dict) and config["proxmox"].get("pass"):
            config["proxmox"]["pass"] = security.encriptar(config["proxmox"]["pass"])

        if isinstance(config.get("idrac"), dict) and config["idrac"].get("pass"):
            config["idrac"]["pass"] = security.encriptar(config["idrac"]["pass"])

        if isinstance(config.get("sql"), dict) and config["sql"].get("pass"):
            config["sql"]["pass"] = security.encriptar(config["sql"]["pass"])

        if isinstance(config.get("vms"), list):
            for vm in config["vms"]:
                if isinstance(vm, dict) and vm.get("pass"):
                    vm["pass"] = security.encriptar(vm["pass"])

        # --- NUEVO: Encriptar Mirth Connect ---
        if isinstance(config.get("mirth_servers"), list):
            for m in config["mirth_servers"]:
                if isinstance(m, dict) and m.get("pass"):
                    m["pass"] = security.encriptar(m["pass"])

        # Escritura atómica del JSON
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)

        # Reiniciar el servicio para aplicar cambios
        toggle_monitoreo(False)
        toggle_monitoreo(True)
        return {"success": True}

    except Exception as e:
        return {"success": False, "msg": str(e)}


# ---------------------------------------------------------------------------
# CONTROL DEL SERVICIO
# ---------------------------------------------------------------------------
@eel.expose
def toggle_monitoreo(activar: bool):
    try:
        if activar:
            res = subprocess.run(
                ["schtasks", "/Run", "/TN", "TecnoMonitor_AutoStart"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode != 0:
                return {"success": False, "msg": "Requiere privilegios de Administrador para iniciar el servicio."}
        else:
            res = subprocess.run(
                ["taskkill", "/F", "/IM", "TecnoMonitorService.exe", "/T"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 5:
                return {"success": False, "msg": "Requiere privilegios de Administrador para detener el servicio."}
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {"success": False, "msg": "Timeout al ejecutar el comando."}
    except Exception as e:
        return {"success": False, "msg": str(e)}


@eel.expose
def check_service_status():
    try:
        for proc in psutil.process_iter(['name', 'cmdline']):
            name    = proc.info.get('name', '')
            cmdline = proc.info.get('cmdline') or []
            if name == "TecnoMonitorService.exe":
                return True
            if any("headless_service.py" in arg for arg in cmdline):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# LOG
# ---------------------------------------------------------------------------
@eel.expose
def limpiar_log():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("--- Log limpiado por el administrador ---\n")
        return True
    except Exception:
        return False


@eel.expose
def leer_log_delta(posicion_anterior: int):
    if not os.path.exists(LOG_FILE):
        return {"content": "", "pos": 0}
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)
            tamano_total = f.tell()
            if posicion_anterior > tamano_total:
                posicion_anterior = 0
            f.seek(posicion_anterior)
            contenido     = f.read()
            nueva_posicion = f.tell()
        return {"content": contenido, "pos": nueva_posicion}
    except Exception as e:
        return {"content": f"[Error leyendo log: {e}]\n", "pos": posicion_anterior}


# ---------------------------------------------------------------------------
# TESTS DE CONEXIÓN
# ---------------------------------------------------------------------------
@eel.expose
def test_proxmox_gui(data):
    return agent_logic.test_connection_proxmox(data)


@eel.expose
def test_vmware_gui(data):
    return agent_logic.test_connection_vmware(data)


@eel.expose
def test_idrac_gui(data):
    return agent_logic.test_connection_idrac(data)


@eel.expose
def test_vm_gui(data):
    return agent_logic.test_connection_vm_wmi(data)


# --- NUEVO: Test Mirth Connect ---
@eel.expose
def test_mirth_gui(data):
    return agent_logic.test_connection_mirth(data)


@eel.expose
def probar_conexion_central(url: str):
    import requests as req
    try:
        r = req.get(url, timeout=5, verify=False)
        return {"success": True, "code": r.status_code}
    except Exception as e:
        return {"success": False, "msg": str(e)}


@eel.expose
def reset_historial_sql():
    try:
        agent_logic.reset_checkpoint()
        return True
    except Exception:
        return False

@eel.expose
def test_ssl_gui(data):
    return agent_logic.test_ssl_gui(data)

# ---------------------------------------------------------------------------
# ARRANQUE
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    try:
        eel.start('index.html', size=(1100, 900), port=0)
    except Exception as e:
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"--- CRASH GUI: {e} ---\n")
        except Exception:
            pass