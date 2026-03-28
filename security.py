from cryptography.fernet import Fernet
import os

# --- CAMBIO IMPORTANTE: RUTAS A PROGRAMDATA ---
def get_app_data_path():
    r"""Retorna la ruta segura C:\ProgramData\TecnoMonitor"""
    path = os.path.join(os.environ['PROGRAMDATA'], 'TecnoMonitor')
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except: pass # Si ya existe o error
    return path

DATA_DIR = get_app_data_path()
KEY_FILE = os.path.join(DATA_DIR, "secret.key")

def cargar_o_generar_clave():
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as key_file:
            key_file.write(key)
    else:
        with open(KEY_FILE, "rb") as key_file:
            key = key_file.read()
    return key

cipher_suite = Fernet(cargar_o_generar_clave())

def encriptar(texto):
    if not texto: return ""
    try:
        return cipher_suite.encrypt(texto.encode()).decode()
    except Exception:
        return texto

def desencriptar(texto_encriptado):
    if not texto_encriptado: return ""
    try:
        return cipher_suite.decrypt(texto_encriptado.encode()).decode()
    except Exception:
        return texto_encriptado