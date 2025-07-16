from flask import Flask, request, jsonify, render_template
from bridge_scraper import estrai_testo_vocami
import openai
import os
from langdetect import detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

DetectorFactory.seed = 42
app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

def rileva_lingua_sicura(testo):
    try:
        return detect(testo)
    except LangDetectException:
        return "it"

def prepara_contenuto():
    testo = estrai_testo_vocami()
    if not testo.strip():
        return "⚠️ Nessun contenuto tecnico disponibile al momento."
    return testo[:3000] + "..."

@app.route("/", methods=["GET", "POST"])
def home():
    risposta = ""
    if request.method == "POST":
        prompt = request.form["prompt"]
        lingua = rileva_lingua_sicura(prompt)
        contenuto = prepara_contenuto()
        print(f"[MAIN] Prompt ricevuto: {prompt}")
        print(f"[MAIN] Contenuto caricato: {len(contenuto)} caratteri")

        try:
            completamento = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "Rispondi solo usando le informazioni tecniche fornite da Tecnaria S.p.A."},
                    {"role": "user", "content": f"{contenuto}\n\nDomanda: {prompt}"}
                ],
                temperature=0.5
            )
            risposta = completamento.choices[0].message["content"]

            if "chiodatrice" in prompt.lower():
                risposta += "\n\n🖼️ Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg"
        except Exception as e:
            print(f"[GPT ERROR] {e}")
            risposta = "⚠️ Errore durante la generazione della risposta."

        return render_template("chat.html", messages=[
            {"role": "user", "text": prompt},
            {"role": "bot", "text": risposta}
        ])
    return render_template("chat.html", messages=[])

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    prompt = data.get("message", "")
    lingua = rileva_lingua_sicura(prompt)
    contenuto = prepara_contenuto()
    print(f"[API] Prompt ricevuto: {prompt}")
    print(f"[API] Contenuto caricato: {len(contenuto)} caratteri")

    try:
        completamento = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Rispondi solo usando le informazioni tecniche fornite da Tecnaria S.p.A."},
                {"role": "user", "content": f"{contenuto}\n\nDomanda: {prompt}"}
            ],
            temperature=0.5
        )
        risposta = completamento.choices[0].message["content"]

        if "chiodatrice" in prompt.lower():
            risposta += "\n\n🖼️ Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg"
        return jsonify({"response": risposta})
    except Exception as e:
        print(f"[GPT API ERROR] {e}")
        return jsonify({"response": "⚠️ Errore durante la generazione della risposta."})

@app.route("/audio", methods=["POST"])
def audio():
    try:
        import pyttsx3
        from flask import send_file

        text = request.get_json().get("text", "")
        engine = pyttsx3.init()
        engine.setProperty("rate", 150)
        audio_file = "output.mp3"
        engine.save_to_file(text, audio_file)
        engine.runAndWait()
        return send_file(audio_file, mimetype="audio/mpeg")
    except Exception as e:
        print(f"[ERRORE AUDIO] {e}")
        return jsonify({"error": "Errore nella sintesi vocale."}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
