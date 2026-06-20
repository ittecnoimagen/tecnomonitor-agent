import requests
import urllib3

# Desactivamos advertencias SSL.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def recolectar_mirth(mirth_configs, log_func=None):
    """
    Extrae la telemetría de canales HL7 desde la API REST de Mirth Connect.
    Estrictamente formateado para cumplir con el Schema v4.1.
    """
    resultados = {}
    meta_status = "ok"
    errores_globales = 0

    if not mirth_configs:
        return resultados, "disabled", 0

    for m_cfg in mirth_configs:
        alias = m_cfg.get("alias", "Mirth_Desconocido")
        url   = m_cfg.get("url", "").rstrip('/')
        user  = m_cfg.get("user", "")
        pwd   = m_cfg.get("pass", "")
        
        canales_data = []
        try:
            s = requests.Session()
            # Encabezados requeridos por Mirth para la API REST
            s.headers.update({
                'X-Requested-With': 'OpenAPI', 
                'Accept': 'application/json'
            })
            
            # 1. Login
            login_req = s.post(f"{url}/api/users/_login", data={'username': user, 'password': pwd}, verify=False, timeout=10)
            login_req.raise_for_status()
            
            # 2. Obtener Estados (Robustez ante XML-to-JSON)
            r_stat = s.get(f"{url}/api/channels/statuses", verify=False, timeout=15)
            r_stat.raise_for_status()
            data = r_stat.json()
            
            dash_status = data.get('list', {}).get('dashboardStatus', [])
            if isinstance(dash_status, dict): 
                dash_status = [dash_status]
            
            for status in dash_status:
                name  = status.get('name', 'Unknown')
                state = status.get('state', 'UNKNOWN')
                
                stats_entries = status.get('statistics', {}).get('entry', [])
                if isinstance(stats_entries, dict): 
                    stats_entries = [stats_entries]
                
                queued = 0
                errors_count = 0
                
                for entry in stats_entries:
                    st_type = entry.get('com.mirth.connect.donkey.model.message.Status')
                    st_val  = int(entry.get('long', 0))
                    
                    if st_type == 'QUEUED':
                        queued = st_val
                    elif st_type == 'ERROR':
                        errors_count = st_val
                
                # Payload ESTRICTO v4.1 (Eliminamos received y sent)
                canales_data.append({
                    "channel": name,
                    "status": state,
                    "queued": queued,
                    "last_error": f"Errores acumulados: {errors_count}" if errors_count > 0 else ""
                })
            
            # 3. Logout
            s.post(f"{url}/api/users/_logout", verify=False, timeout=5)
            resultados[alias] = canales_data
            
        except Exception as e:
            errores_globales += 1
            meta_status = "partial"
            if log_func: 
                log_func(f"⚠️ Error Mirth ({alias}): {str(e)}")
            
            resultados[alias] = [{
                "channel": "SYSTEM_ERROR", 
                "status": "ERROR", 
                "queued": 0, 
                "last_error": str(e)[:100]
            }]
    
    if errores_globales == len(mirth_configs) and len(mirth_configs) > 0:
        meta_status = "error"
        
    return resultados, meta_status, errores_globales