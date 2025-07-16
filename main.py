from flask import Flask, request, jsonify, render_template
from bridge_scraper import estrai_testo_vocami
import openai
import os
from langdetect import detect

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

def traduci_testo(testo, lingua_target):
    if lingua_target == "it":
        return testo
    try:
        traduzione = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": f"Traduci questo testo in {lingua_target} mantenendo tono tecnico professionale:"},
                {"role": "user", "content": testo}
            ],
            temperature=0.3
        )
        return traduzione.choices[0].message["content"]
    except:
        return testo  # fallback in italiano se errore

@app.route("/", methods=["GET", "POST"])
def home():
    risposta = ""
    if request.method == "POST":
        prompt = request.form["prompt"]
        lingua = detect(prompt)
        contenuto = estrai_testo_vocami()
        contenuto_tradotto = traduci_testo(contenuto, lingua)

        try:
            completamento = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "Rispondi solo usando le informazioni tecniche fornite da Tecnaria S.p.A."},
                    {"role": "user", "content": f"{contenuto_tradotto}\n\nDomanda: {prompt}"}
                ],
                temperature=0.5
            )
            risposta = completamento.choices[0].message["content"]

            if "chiodatrice" in prompt.lower() and "tecnaria.com/wp-content/uploads" in contenuto:
                risposta += "\n\n🖼️ Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg"

        except:
            risposta = "Si è verificato un errore nel generare la risposta. Riprova."

        return render_template("chat.html", messages=[
            {"role": "user", "text": prompt},
            {"role": "bot", "text": risposta}
        ])
    return render_template("chat.html", messages=[])

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    prompt = data.get("message", "")
    lingua = detect(prompt)
    contenuto = estrai_testo_vocami()
    contenuto_tradotto = traduci_testo(contenuto, lingua)

    try:
        completamento = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Rispondi solo usando le informazioni tecniche fornite da Tecnaria S.p.A."},
                {"role": "user", "content": f"{contenuto_tradotto}\n\nDomanda: {prompt}"}
            ],
            temperature=0.5
        )
        risposta = completamento.choices[0].message["content"]

        if "chiodatrice" in prompt.lower() and "tecnaria.com/wp-content/uploads" in contenuto:
            risposta += "\n\n🖼️ Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg"

        return jsonify({"response": risposta})
    except:
        return jsonify({"response": "Si è verificato un errore nel generare la risposta."})

@app.route("/audio", methods=["POST"])
def audio():
    try:
        import pyttsx3
        from io import BytesIO
        from flask import send_file

        text = request.get_json().get("text", "")
        engine = pyttsx3.init()
        engine.setProperty("rate", 150)
        audio_file = "output.mp3"
        engine.save_to_file(text, audio_file)
        engine.runAndWait()
        return send_file(audio_file, mimetype="audio/mpeg")
    except:
        return jsonify({"error": "Errore nella sintesi vocale."}), 500

if __name__ == "__main__":
    app.run(debug=False, port=10000)
