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

def get_contenuto_tecnaria():
    session = requests.Session()
    session.headers.update({"User-Agent":"Mozilla/5.0"})
    pagine = [
        "https://www.tecnaria.com/prodotto/chiodatrice-p560-per-connettori-ctf/",
        "https://www.tecnaria.com/prodotto/chiodatrice-p560-per-connettori-diapason/",
        "https://www.tecnaria.com/en/prodotto/spit-p560-for-the-installation-of-metal-sheet/"
    ]
    risultati = []

    for url in pagine:
        try:
            resp = session.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            risultati.append(f"⚠️ Errore {url}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        titolo = soup.find(["h1", "h2", "h3"])
        text = soup.get_text(separator=" ").replace("\n", " ")

        codice = re.search(r"Cod\.\s*0*(\d+)", text)
        peso = re.search(r"peso\s*(\d+,\d+)\s*Kg", text, flags=re.IGNORECASE)

        risultati.append(
            f"{titolo.text.strip() if titolo else 'Prodotto'} – "
            f"codice {codice.group(1) if codice else '?'} – "
            f"peso {peso.group(1) + ' kg' if peso else '?'} – "
            f"info: {text[:400]}..."
        )

    return "\n\n".join(risultati) if risultati else "⚠️ Nessun contenuto trovato da scraping."

@app.route("/")
def home():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    user_message = request.json.get("message", "").strip()
    contenuto_scraping = get_contenuto_tecnaria()

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
