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

# Funzione di scraping avanzato integrato nel main
def scraper_esteso():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    # URL principali da cui partire
    urls = [
        "https://www.tecnaria.com/it/prodotti/",
        "https://www.tecnaria.com/it/faq/",
        "https://www.tecnaria.com/it/documentazione/",
        "https://www.tecnaria.com/it/connettori-ctf/"
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

        # Estrazione di titoli e descrizioni da prodotti e FAQ
        if "prodotti" in url:
            print(f"Scraping della pagina {url}")
            items = soup.select('div.prodotto, div.item, .product-info')  # Selettore variabile
            for item in items:
                titolo = item.find("h3")
                descrizione = item.find("p")
                if titolo and descrizione:
                    risultati.append(f"Prodotto: {titolo.text.strip()} – {descrizione.text.strip()}")

        elif "faq" in url:
            print(f"Scraping della FAQ {url}")
            faq_items = soup.select('div.faq-item, .question')
            for faq in faq_items:
                domanda = faq.find("h4")
                risposta = faq.find("p")
                if domanda and risposta:
                    risultati.append(f"Domanda: {domanda.text.strip()} – Risposta: {risposta.text.strip()}")

        elif "documentazione" in url:
            print(f"Scraping della documentazione {url}")
            docs = soup.select('a[href$=".pdf"]')  # Link ai PDF
            for doc in docs:
                risultati.append(f"PDF: {doc['href']} – {doc.text.strip()}")

    if not risultati:
        return "⚠️ Nessun dato trovato durante lo scraping."

    return "

".join(risultati)

# Funzione di scraping integrato nel main.py
def get_contenuto_tecnaria():
    try:
        return scraper_esteso()  # Usando la funzione di scraping avanzato
    except Exception as e:
        return f"⚠️ Errore nello scraping esteso: {e}"

@app.route("/")
def home():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    user_message = request.json.get("message", "").strip()
    contenuto_scraping = get_contenuto_tecnaria()

    prompt_dinamico = BASE_SYSTEM_PROMPT + "

Contenuti tecnici estratti dal sito Tecnaria:
" + contenuto_scraping

    print("
" + "="*40)
    print("🟠 PROMPT INVIATO A GPT-4:")
    print(prompt_dinamico)
    print("="*40 + "
")

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
