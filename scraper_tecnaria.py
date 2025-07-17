import requests
from bs4 import BeautifulSoup
import urllib.parse

def scrape_tecnaria_results(query):
    try:
        # Esegue ricerca su Google per "site:tecnaria.com + query"
        search_url = f"https://www.google.com/search?q=site:tecnaria.com+{urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(search_url, headers=headers, timeout=10)
        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        # Trova il primo risultato utile su tecnaria.com
        link_tag = soup.find("a", href=True)
        tecnaria_link = None
        for tag in soup.find_all("a", href=True):
            href = tag['href']
            if "tecnaria.com" in href:
                tecnaria_link = href
                break

        if not tecnaria_link:
            return ""

        # Accede al contenuto della pagina trovata
        page = requests.get(tecnaria_link, headers=headers, timeout=10)
        if page.status_code != 200:
            return ""

        soup_page = BeautifulSoup(page.text, "html.parser")
        paragraphs = soup_page.find_all("p")
        text = "\n".join(p.get_text() for p in paragraphs)

        return text.strip()

    except Exception as e:
        print(f"[SCRAPER ERROR] {e}")
        return ""
