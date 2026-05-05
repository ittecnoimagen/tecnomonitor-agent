from cryptography.fernet import Fernet
import os

# ---------------------------------------------------------------------------
# RUTAS A PROGRAMDATA
# ---------------------------------------------------------------------------
def get_app_data_path():
    r"""Retorna la ruta segura C:\ProgramData\TecnoMonitor"""
    path = os.path.join(os.environ.get('PROGRAMDATA', os.path.expanduser('~')), 'TecnoMonitor')
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


DATA_DIR = get_app_data_path()
KEY_FILE = os.path.join(DATA_DIR, "secret.key")


def cargar_o_generar_clave():
    """Carga la clave Fernet o la genera y la persiste de forma atómica."""
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        tmp = KEY_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(key)
        os.replace(tmp, KEY_FILE)          # escritura atómica: nunca deja el archivo a medias
    else:
        with open(KEY_FILE, "rb") as f:
            key = f.read()
    return key


cipher_suite = Fernet(cargar_o_generar_clave())


def encriptar(texto: str) -> str:
    if not texto:
        return ""
    try:
        return cipher_suite.encrypt(texto.encode()).decode()
    except Exception:
        return texto


def desencriptar(texto_encriptado: str) -> str:
    if not texto_encriptado:
        return ""
    try:
        return cipher_suite.decrypt(texto_encriptado.encode()).decode()
    except Exception:
        return texto_encriptado