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

        output = []
        for elem in soup.find_all(["p", "li", "a", "span"]):
            if elem.name == "a" and elem.has_attr("href"):
                text = elem.get_text(strip=True)
                link = elem["href"]
                if re.search(r"\.(jpg|jpeg|png|gif)$", link, re.IGNORECASE):
                    output.append(f"Immagine: {link}")
                elif text:
                    output.append(f"{text}: {link}")
            else:
                text = elem.get_text(strip=True)
                if text:
                    output.append(text)

        testo = " ".join(output)
        testo_pulito = re.sub(r"\s+", " ", testo)
        return testo_pulito

    except Exception as e:
        return ""
