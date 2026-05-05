import sys
import os

# --- FIX CRÍTICO PARA MODO --noconsole ---
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
# -----------------------------------------

import time
import json
import logging
import socket
import security
from datetime import datetime
import agent_logic
from agent_logic import ejecutar_ciclo_agente

# ---------------------------------------------------------------------------
# CANDADO ANTI-CLONES (un solo proceso activo)
# ---------------------------------------------------------------------------
_candado_socket = None

def obtener_candado():
    global _candado_socket
    try:
        _candado_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _candado_socket.bind(('127.0.0.1', 64999))
        return True
    except socket.error:
        return False

# ---------------------------------------------------------------------------
# RUTAS
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR    = security.get_app_data_path()
CONFIG_FILE = os.path.join(DATA_DIR, "monitor_config.json")
LOG_FILE    = os.path.join(DATA_DIR, "activity.log")

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S',
    filemode='a',
    encoding='utf-8',
)

def log(msg: str):
    """Wrapper de logging: nunca falla, nunca silencia la causa real."""
    try:
        logging.info(msg)
    except Exception as log_err:
        # Último recurso: si el logger falla, intentamos escribir directo al archivo
        try:
            with open(LOG_FILE, 'a', encoding='utf-8', errors='replace') as f:
                f.write(f"[LOG_ERROR] {log_err} | Mensaje original: {msg}\n")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# CARGA DE CONFIGURACIÓN
# ---------------------------------------------------------------------------
def cargar_config_segura():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Desencriptar credenciales
        if data.get("auth_token"):
            data["auth_token"] = security.desencriptar(data["auth_token"])

        if isinstance(data.get("proxmox"), dict) and data["proxmox"].get("pass"):
            data["proxmox"]["pass"] = security.desencriptar(data["proxmox"]["pass"])

        if isinstance(data.get("idrac"), dict) and data["idrac"].get("pass"):
            data["idrac"]["pass"] = security.desencriptar(data["idrac"]["pass"])

        if isinstance(data.get("sql"), dict) and data["sql"].get("pass"):
            data["sql"]["pass"] = security.desencriptar(data["sql"]["pass"])

        if isinstance(data.get("vms"), list):
            for vm in data["vms"]:
                if isinstance(vm, dict) and vm.get("pass"):
                    vm["pass"] = security.desencriptar(vm["pass"])

        # --- AÑADE ESTO: Desencriptar Mirth Connect ---
        if isinstance(data.get("mirth_servers"), list):
            for m in data["mirth_servers"]:
                if isinstance(m, dict) and m.get("pass"):
                    m["pass"] = security.desencriptar(m["pass"])
        # ----------------------------------------------

        return data

    except json.JSONDecodeError as e:
        log(f"❌ monitor_config.json malformado: {e}")
    except Exception as e:
        log(f"⚠️ Error al cargar configuración: {e}")

    return None

# ---------------------------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log("🚀 TecnoMonitor Service v4.1 — Iniciando")

    if not obtener_candado():
        log("🚨 Puerto 64999 ocupado — ya hay una instancia corriendo. Saliendo.")
        sys.exit(0)

    log("🔒 Candado adquirido. Servicio activo.")

    while True:
        try:
            cfg = cargar_config_segura()

            if not cfg:
                log("⚠️ Configuración no disponible. Reintentando en 60s...")
                time.sleep(60)
                continue

            # --- Módulo SQL ---
            if cfg.get("enabled_sql") and cfg.get("sql"):
                try:
                    sql_data = agent_logic.extraer_metricas_sql(cfg["sql"], log_func=log)
                    if sql_data:
                        cfg["_sql_data_payload"] = sql_data
                    else:
                        log("ℹ️ SQL: bloque futuro o sin datos nuevos, se omite en este ciclo.")
                except Exception as e:
                    log(f"❌ Error en módulo SQL: {e}")
            else:
                log("ℹ️ Módulo SQL desactivado.")

            # --- Ciclo principal ---
            res = ejecutar_ciclo_agente(cfg, log_callback=log)

            if isinstance(res, dict):
                if res.get("status") == "OK":
                    log(f"✅ Ciclo completado y enviado OK — {res.get('timestamp', '')}")
                else:
                    log(f"❌ Fallo en el envío: {res.get('error', 'Error desconocido')}")
            else:
                log(f"⚠️ Respuesta inesperada del ciclo: {res}")

            minutos = float(cfg.get("interval_minutes", 5))
            log(f"💤 Durmiendo {minutos:.1f} minutos...\n")
            time.sleep(minutos * 60)

        except Exception as e:
            log(f"💥 Error global del bucle: {e}")
            time.sleep(60)