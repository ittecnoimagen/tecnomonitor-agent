import sys
import os

# --- FIX CRÍTICO PARA MODO --noconsole ---
# Redirigimos la salida estándar a un agujero negro para evitar crash instantáneo
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

# --- CANDADO ANTI-CLONES ---
def obtener_candado():
    try:
        global candado_socket
        candado_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        candado_socket.bind(('127.0.0.1', 64999))
        return True
    except socket.error:
        return False

# --- CONFIGURACIÓN DE RUTAS ---
if getattr(sys, 'frozen', False): 
    os.chdir(os.path.dirname(sys.executable))
else: 
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = security.get_app_data_path()
CONFIG_FILE = os.path.join(DATA_DIR, "monitor_config.json")
LOG_FILE = os.path.join(DATA_DIR, "activity.log")

logging.basicConfig(
    filename=LOG_FILE, 
    level=logging.INFO, 
    format='[%(asctime)s] %(message)s', 
    datefmt='%H:%M:%S', 
    filemode='a',
    encoding='utf-8'  
)

def log_wrapper(msg):
    try:
        logging.info(msg)
        if not getattr(sys, 'frozen', False): pass
    except: pass

# --- FUNCIÓN DE CONFIGURACIÓN ---
def cargar_config_segura():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: 
                data = json.load(f)
            
            if data.get("auth_token"): data["auth_token"] = security.desencriptar(data["auth_token"])
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
            return data
        except Exception as e: 
            log_wrapper(f"⚠️ Error al procesar JSON: {e}")
    return None

# --- EJECUCIÓN PRINCIPAL ---
if __name__ == "__main__":
    # Cambiamos los print() por log_wrapper()
    log_wrapper("🚀 INICIANDO SCRIPT MODO DEBUG v4.0 (Bloques Fijos)")

    if not obtener_candado():
        log_wrapper("🚨 ¡ALTO AHÍ! EL PUERTO 64999 ESTÁ OCUPADO. Hay una versión vieja corriendo.")
        sys.exit(0)

    log_wrapper("--- CANDADO OK: SERVICIO INICIADO (v4.0 + Bloques SQL) ---")
    
    while True:
        try:
            cfg = cargar_config_segura()
            if cfg:
                if cfg.get("enabled_sql") and cfg.get("sql"):
                    try:
                        sql_data = agent_logic.extraer_metricas_sql(cfg["sql"], log_func=log_wrapper)
                        if sql_data:
                            cfg["_sql_data_payload"] = sql_data 
                            
                    except AttributeError:
                        log_wrapper("⚠️ Falla estructural: agent_logic.extraer_metricas_sql no fue encontrada.")
                    except Exception as e:
                        log_wrapper(f"❌ Error crítico en ejecución SQL: {e}")
                else:
                    log_wrapper("ℹ️ Monitoreo SQL Server está desactivado en la configuración.")

                res = ejecutar_ciclo_agente(cfg, log_callback=log_wrapper)
                
                if isinstance(res, dict):
                    ts = res.get('timestamp', datetime.now().strftime("%H:%M:%S"))
                    if res.get("status") != "OK":
                        log_wrapper(f"❌ FALLO EL ENVÍO AL SERVIDOR: {res.get('error', 'Error desconocido')}")
                    else:
                        log_wrapper(f"✅ CICLO Y ENVÍO OK - {ts}")
                
                minutos = float(cfg.get("interval_minutes", 5))
                log_wrapper(f"💤 Durmiendo {minutos} minutos...\n")
                time.sleep(minutos * 60)
            else: 
                log_wrapper("⚠️ No se encontró monitor_config.json. Reintento en 60s...")
                time.sleep(60)
        except Exception as e:
            log_wrapper(f"💥 CRASH GLOBAL DE BUCLE: {e}")
            time.sleep(60)