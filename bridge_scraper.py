import requests
from bs4 import BeautifulSoup
import re

def estrai_testo_vocami():
    url = "https://docs.google.com/document/d/e/2PACX-1vSqy0-FZAqOGvnCFZwwuBfT1cwXFpmSpkWfrRiT8RlbQpdQy-_1hOaqIslih5ULSa0XhVt0V8QeWJDP/pub"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href")
            if href:
                a.insert_after(f" ({href})")
        testo = soup.get_text(separator=" ", strip=True)
        testo_pulito = re.sub(r"\s+", " ", testo)
        print(f"[BRIDGE] Testo estratto ({len(testo_pulito)} caratteri)")
        return testo_pulito
    except Exception as e:
        print(f"[BRIDGE ERROR] {e}")
        return ""
