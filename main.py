from flask import Flask, request, jsonify, render_template
import openai
import os
from scraper_tecnaria import scrape_tecnaria_results

app = Flask(__name__)

# Carica API key da variabile ambiente
openai.api_key = os.getenv("OPENAI_API_KEY")

# Risposte fallback predefinite
FALLBACK_RISPOSTE = {
    "chiodatrice": """
Tecnaria consiglia l’uso della chiodatrice a gas Spit Pulsa 560 (P560) per l’installazione dei suoi connettori strutturali, in particolare per i sistemi CTF e DIAPASON.

📌 Applicazioni principali:
- Fissaggio rapido dei connettori CTF su lamiere grecate.
- Bloccaggio dei connettori DIAPASON su supporti metallici.

⚙️ Caratteristiche tecniche:
- Alimentazione: a gas
- Potenza: elevata, per lamiere da cantiere
- Compatibilità: chiodi da 22 a 27 mm
- Cadenza: fino a 2 chiodi/sec
- Autonomia: oltre 1.000 fissaggi per bomboletta

📦 Dotazione standard:
- Pistone di ricambio, guida punte, anello ammortizzatore
- Valigetta rigida con manuale

💡 Vantaggi:
- Installazione ultra-rapida
- Guide dedicate = minimo errore
- Adatta a soluzioni antisismiche
- Disponibile per acquisto o noleggio

📸 Immagine: https://tecnaria.com/wp-content/uploads/2020/07/chiodatrice_p560_connettori_ctf_tecnaria.jpg

📞 Contatti: sito ufficiale Tecnaria o ufficio tecnico-commerciale.
"""
}

# Home route
@app.route("/")
def index():
    return render_template("chat.html")

# Route per domanda/risposta
@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()
        context = scrape_tecnaria_results(user_prompt)

        # Fallback se scraping fallisce o è vuoto
        if not context.strip():
            for keyword in FALLBACK_RISPOSTE:
                if keyword in user_prompt.lower():
                    context = FALLBACK_RISPOSTE[keyword]
                    break

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata. Riprova con una domanda diversa."}), 400

        # Prompt combinato
        full_prompt = f"Contesto tecnico:\n{context}\n\nDomanda:\n{user_prompt}\n\nRisposta tecnica:"

        # Chiamata a OpenAI
        completion = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Sei un esperto tecnico dei prodotti Tecnaria. Rispondi con precisione e chiarezza."},
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.3
        )

        answer = completion.choices[0].message.content
        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": f"Errore durante la generazione della risposta: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
