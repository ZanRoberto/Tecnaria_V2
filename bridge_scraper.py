import requests
from bs4 import BeautifulSoup
import re

def estrai_testo_vocami():
    url = "https://www.vocami.it/mirami/?qr=63AE29D71905EF509A564E58E576D123"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        testo = soup.get_text(separator=" ", strip=True)
        testo_pulito = re.sub(r"\s+", " ", testo)
        return testo_pulito
    except Exception as e:
        return ""