🧠 TECNARIA BOT – DOCUMENTAZIONE RIASSUNTIVA

✅ PROGETTO
Bot basato su GPT-4 (via API OpenAI) che risponde a domande sui prodotti TECNARIA, usando dati reali dal sito via scraping.

✅ COMPONENTI ESSENZIALI
1. main.py (Flask + GPT + TTS "Nova" + Scraping integrato)
2. requirements.txt (librerie necessarie)
3. Procfile (per Render)
4. .env (con OPENAI_API_KEY)

✅ PROMPT GPT
Ogni risposta viene generata con prompt dinamico che include:
- Il ruolo dell’assistente (esperto Tecnaria)
- Il contenuto estratto dal sito (scraping)
- La domanda dell’utente

✅ FUNZIONALITÀ AUDIO
Voce "Nova" di OpenAI abilitata tramite endpoint `/audio`.

✅ DEPLOY
- GitHub o ZIP
- Render.com: deploy automatico con variabili ambiente impostate

✅ COMPORTAMENTO
Il bot risponde con precisione usando GPT-4, alimentato con dati aggiornati del sito Tecnaria.