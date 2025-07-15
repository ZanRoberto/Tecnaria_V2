import os
import requests
from flask import Flask, render_template, request, jsonify, send_file
import openai
from bs4 import BeautifulSoup
import re

openai.api_key = os.getenv("OPENAI_API_KEY")
app = Flask(__name__)

BASE_SYSTEM_PROMPT = (
    "Agisci come assistente esperto della società TECNARIA S.p.A., con sede unica in Viale Pecori Giraldi 55, 36061 Bassano del Grappa (VI), Italia. "
    "Concentrati esclusivamente su questa azienda e sui suoi prodotti e servizi. "
    "Se l'utente menziona altre aziende omonime, ignorale. "
    "Puoi fornire qualsiasi informazione utile su prodotti, usi, caratteristiche tecniche e dettagli pratici, "
    "anche se non presente nei cataloghi, purché rilevante per Tecnaria S.p.A. "
)

def scraper_esteso():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    urls = [
        "https://www.tecnaria.com/it/prodotti/",
        "https://www.tecnaria.com/it/faq/",
        "https://www.tecnaria.com/it/documentazione/",
        "https://www.tecnaria.com/it/connettori-ctf/",
        "https://www.tecnaria.com/it/listino-prezzi/"
    ]

    risultati = []

    for url in urls:
        try:
            response = session.get(url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Errore nella richiesta {url}: {e}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")

        if "prodotti" in url:
            items = soup.select('div.prodotto, div.item, .product-info')
            for item in items:
                titolo = item.find("h3")
                descrizione = item.find("p")
                prezzo = re.search(r"€(\d+[\.,]?\d*)", descrizione.text if descrizione else "")
                if titolo and descrizione:
                    risultato = f"Prodotto: {titolo.text.strip()} – {descrizione.text.strip()}"
                    if prezzo:
                        risultato += f" – Prezzo: €{prezzo.group(1)}"
                    else:
                        risultato += " – ⚠️ Prezzo non disponibile online."
                    risultati.append(risultato)

        elif "faq" in url:
            faq_items = soup.select('div.faq-item, .question')
            for faq in faq_items:
                domanda = faq.find("h4")
                risposta = faq.find("p")
                if domanda and risposta:
                    risultati.append(f"Domanda: {domanda.text.strip()} – Risposta: {risposta.text.strip()}")

        elif "documentazione" in url:
            docs = soup.select('a[href$=".pdf"]')
            for doc in docs:
                risultati.append(f"PDF: {doc['href']} – {doc.text.strip()}")

        elif "listino-prezzi" in url:
            prezzo_items = soup.select('div.price, span.price')
            for price in prezzo_items:
                risultati.append(f"Prezzo trovato: {price.text.strip()}")

    if not risultati:
        return "⚠️ Nessun dato trovato durante lo scraping."

    return "\n".join(risultati)

def get_contenuto_tecnaria():
    try:
        return scraper_esteso()
    except Exception as e:
        return f"⚠️ Errore nello scraping esteso: {e}"

@app.route("/")
def home():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    user_message = request.json.get("message", "").strip()
    contenuto_scraping = get_contenuto_tecnaria()

    if "⚠️ Prezzo non disponibile" in contenuto_scraping:
        contenuto_scraping += "\n\nNota: I prezzi dei prodotti Tecnaria variano in base al modello e alla quantità ordinata. Per ottenere un preventivo, contatta direttamente l’ufficio commerciale."

    prompt_dinamico = BASE_SYSTEM_PROMPT + "\n\nContenuti tecnici estratti dal sito Tecnaria:\n" + contenuto_scraping

    print("\n" + "="*40)
    print("🟠 PROMPT INVIATO A GPT-4:")
    print(prompt_dinamico)
    print("="*40 + "\n")

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt_dinamico},
                {"role": "user", "content": user_message}
            ]
        )
        risposta = response.choices[0].message.content.strip()
    except Exception as e:
        risposta = f"⚠️ Errore nella risposta: {e}"

    return jsonify({"response": risposta})

@app.route("/audio", methods=["POST"])
def audio():
    data = request.json
    testo = data.get("text", "")
    if not testo:
        return jsonify({"error": "Nessun testo fornito"}), 400

    nome_file = "output.mp3"
    try:
        response = openai.audio.speech.create(
            model="tts-1",
            voice="nova",
            input=testo
        )
        with open(nome_file, "wb") as f:
            f.write(response.read())
        return send_file(nome_file, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
