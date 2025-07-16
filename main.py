# main.py
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

def split_testo_per_blocchi(testo, max_length=2000):
    parole = testo.split()
    blocchi = []
    blocco_corrente = []
    lunghezza_corrente = 0
    for parola in parole:
        lunghezza_corrente += len(parola) + 1
        if lunghezza_corrente > max_length:
            blocchi.append(" ".join(blocco_corrente))
            blocco_corrente = [parola]
            lunghezza_corrente = len(parola) + 1
        else:
            blocco_corrente.append(parola)
    if blocco_corrente:
        blocchi.append(" ".join(blocco_corrente))
    return blocchi

@app.route("/", methods=["GET", "POST"])
def home():
    risposta = ""
    if request.method == "POST":
        prompt = request.form["prompt"]
        lingua = rileva_lingua_sicura(prompt)
        contenuto = estrai_testo_vocami()
        if not contenuto.strip():
            contenuto = "⚠️ Nessun contenuto tecnico disponibile al momento."

        print(f"[DEBUG] Prompt: {prompt}")
        print(f"[DEBUG] Contenuto estratto ({len(contenuto)} caratteri)")

        contenuto_tradotto = contenuto
        try:
            if lingua != "it":
                response = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": f"Traduci questo testo in {lingua} mantenendo tono tecnico:"},
                        {"role": "user", "content": contenuto}
                    ],
                    temperature=0.3
                )
                contenuto_tradotto = response.choices[0].message["content"]
        except Exception as e:
            print(f"[ERRORE TRADUZIONE] {e}")

        blocchi = split_testo_per_blocchi(contenuto_tradotto)
        print(f"[DEBUG] Numero blocchi da inviare a GPT: {len(blocchi)}")

        risposte_blocchi = []
        try:
            for i, blocco in enumerate(blocchi):
                print(f"[DEBUG] Invio blocco {i+1} / {len(blocchi)}")
                completamento = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "Rispondi solo usando le informazioni tecniche fornite da Tecnaria S.p.A."},
                        {"role": "user", "content": f"{blocco}\n\nDomanda: {prompt}"}
                    ],
                    temperature=0.5
                )
                risposte_blocchi.append(completamento.choices[0].message["content"])
        except Exception as e:
            print(f"[ERRORE GPT] {e}")
            risposte_blocchi.append("⚠️ Errore durante la generazione della risposta.")

        risposta = "\n\n".join(risposte_blocchi)

        if "chiodatrice" in prompt.lower() and "tecnaria.com/wp-content/uploads" in contenuto:
            risposta += "\n\n🖼️ Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg"

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
    contenuto = estrai_testo_vocami()
    if not contenuto.strip():
        contenuto = "⚠️ Nessun contenuto tecnico disponibile al momento."

    print(f"[API DEBUG] Prompt: {prompt}")
    print(f"[API DEBUG] Contenuto estratto ({len(contenuto)} caratteri)")

    contenuto_tradotto = contenuto
    try:
        if lingua != "it":
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": f"Traduci questo testo in {lingua} mantenendo tono tecnico:"},
                    {"role": "user", "content": contenuto}
                ],
                temperature=0.3
            )
            contenuto_tradotto = response.choices[0].message["content"]
    except Exception as e:
        print(f"[ERRORE TRADUZIONE] {e}")

    blocchi = split_testo_per_blocchi(contenuto_tradotto)
    print(f"[API DEBUG] Numero blocchi: {len(blocchi)}")

    risposte_blocchi = []
    try:
        for i, blocco in enumerate(blocchi):
            print(f"[API DEBUG] Invio blocco {i+1} / {len(blocchi)}")
            completamento = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "Rispondi solo usando le informazioni tecniche fornite da Tecnaria S.p.A."},
                    {"role": "user", "content": f"{blocco}\n\nDomanda: {prompt}"}
                ],
                temperature=0.5
            )
            risposte_blocchi.append(completamento.choices[0].message["content"])
    except Exception as e:
        print(f"[GPT API ERROR] {e}")
        risposte_blocchi.append("⚠️ Errore durante la generazione della risposta.")

    risposta = "\n\n".join(risposte_blocchi)

    if "chiodatrice" in prompt.lower() and "tecnaria.com/wp-content/uploads" in contenuto:
        risposta += "\n\n🖼️ Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg"

    return jsonify({"response": risposta})

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
