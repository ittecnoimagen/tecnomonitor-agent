import requests
import urllib3

# Desactivamos advertencias SSL.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class MirthCollector:
    def __init__(self, name, base_url, username, password):
        self.name = name
        self.base_url = base_url
        self.session = requests.Session()
        self.username = username
        self.password = password
        
        self.session.headers.update({
            'X-Requested-With': 'OpenAPI', 
            'Accept': 'application/json'
        })

    def collect(self):
        """Intenta hacer login, extraer los datos y devolverlos formateados"""
        if not self._login():
            return self.name, [{"channel": "SYSTEM_ERROR", "status": "ERROR", "queued": 0, "last_error": "Fallo de autenticación o timeout"}]
            
        data = self._get_channel_statuses()
        self._logout()
        return self.name, data

    def _login(self):
        url = f"{self.base_url}/api/users/_login"
        payload = {'username': self.username, 'password': self.password}
        try:
            response = self.session.post(url, data=payload, verify=False, timeout=10)
            response.raise_for_status()
            return True
        except:
            return False

    def _get_channel_statuses(self):
        url = f"{self.base_url}/api/channels/statuses"
        canales_monitoreados = []
        
        try:
            response = self.session.get(url, verify=False, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            dashboard_status = data.get('list', {}).get('dashboardStatus', [])
            if isinstance(dashboard_status, dict):
                dashboard_status = [dashboard_status]

            for status in dashboard_status:
                name = status.get('name', 'Desconocido')
                state = status.get('state', 'UNKNOWN')
                
                # Extraemos las estadísticas
                stats_entries = status.get('statistics', {}).get('entry', [])
                if isinstance(stats_entries, dict):
                    stats_entries = [stats_entries]

                queued = 0
                errors = 0

                for entry in stats_entries:
                    stat_type = entry.get('com.mirth.connect.donkey.model.message.Status')
                    stat_value = int(entry.get('long', 0))
                    
                    if stat_type == 'QUEUED':
                        queued = stat_value
                    elif stat_type == 'ERROR':
                        errors = stat_value

                # Construimos el objeto exacto que pide el schema v4.1
                canal_data = {
                    "channel": name,
                    "status": state,
                    "queued": queued,
                    "last_error": f"Errores acumulados: {errors}" if errors > 0 else ""
                }
                canales_monitoreados.append(canal_data)

            return canales_monitoreados

        except Exception as e:
            return [{"channel": "API_ERROR", "status": "ERROR", "queued": 0, "last_error": str(e)[:100]}]

    def _logout(self):
        url = f"{self.base_url}/api/users/_logout"
        try:
            self.session.post(url, verify=False, timeout=5)
        except:
            pass

def run_mirth_collection(config_instances):
    """Función principal para exportar a main_agent.py"""
    mirth_data = {}
    if not config_instances:
        return mirth_data
        
    for instance in config_instances:
        collector = MirthCollector(
            name=instance.get("name", "Default"),
            base_url=instance.get("url"),
            username=instance.get("username"),
            password=instance.get("password")
        )
        name, data = collector.collect()
        mirth_data[name] = data
        
    return mirth_data