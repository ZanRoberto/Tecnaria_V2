import os
import requests
from flask import Flask, render_template, request, jsonify, send_file
import openai
from bs4 import BeautifulSoup

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
    url = "https://www.tecnaria.com/it/prodotti/"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        return f"⚠️ Errore nello scraping: {e}"

    soup = BeautifulSoup(response.text, "html.parser")
    sezioni = soup.select("div.prodotto")
    risultati = []

    for sezione in sezioni:
        titolo = sezione.find("h3")
        descrizione = sezione.find("p")
        if titolo and descrizione:
            risultati.append(f"{titolo.text.strip()}: {descrizione.text.strip()}")

    if not risultati:
        return "⚠️ Nessun contenuto trovato durante lo scraping."

    return "\n\n".join(risultati)

@app.route("/")
def home():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    user_message = request.json.get("message", "").strip()
    contenuto_scraping = get_contenuto_tecnaria()

    prompt_dinamico = BASE_SYSTEM_PROMPT + "\n\nContenuti tecnici estratti dal sito Tecnaria:\n" + contenuto_scraping

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
