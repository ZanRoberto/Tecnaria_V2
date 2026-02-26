# mission_control_bridge.py
import requests
from datetime import datetime

class MissionControlBridge:
    def __init__(self, base_url):
        """
        base_url: l'URL del server Mission Control (es. 'https://tuo-bot.onrender.com')
        """
        self.base_url = base_url.rstrip('/')
        self.last_config = None

    def log_event(self, event_type, **kwargs):
        """Invia un evento al Mission Control."""
        payload = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            **kwargs
        }
        try:
            r = requests.post(f"{self.base_url}/trading/log", json=payload, timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[Bridge] Invio log fallito: {e}")
        return None

    def get_config(self):
        """Recupera la configurazione corrente dal Mission Control."""
        try:
            r = requests.get(f"{self.base_url}/trading/config", timeout=3)
            if r.status_code == 200:
                self.last_config = r.json()
            return self.last_config
        except Exception as e:
            print(f"[Bridge] Recupero config fallito: {e}")
            return self.last_config  # ultima nota
