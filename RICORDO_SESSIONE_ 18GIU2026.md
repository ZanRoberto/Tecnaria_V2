# APERTURA OVERTOP V16 — LEGGERE PRIMA DI TUTTO
## Roberto Zan · Tecnaria_V2 · ultima revisione 22 giu 2026

> Claude: questo file esiste per UNA ragione: **non far ricominciare la sessione da zero.**
> I 5 controlli qui sotto vanno fatti PRIMA di parlare di mine, cancelli, soglie o ENV.
> Se salti i controlli e parti a teorizzare, stai sprecando il tempo di Roberto. Non farlo.

---

## ⛔ I 5 CONTROLLI D'APERTURA (in ordine, sempre, prima di qualsiasi proposta)

Roberto incolla l'output (Claude NON accede al server). Una riga per volta, corte (cellulare).

**1. QUALE FILE GIRA DAVVERO** — è la radice di metà dei loop.
```
md5sum OVERTOP_BASSANO*PRODUCTION.py; echo ---; grep -iE "OVERTOP_BASSANO.*PRODUCTION" app.py | grep -i import
```
→ Nella cartella ci sono PIÙ file (`V16`, `(1)_V16`, `(V15)`). Il `(1)` è un download doppione.
→ Il bot esegue SOLO quello che `app.py` importa. Se modifichi un file diverso da quello importato, **lavori su un cadavere mentre il bot ne esegue un altro.** È successo. `/diagnostic` si dichiarava V15 per questo.
→ **REGOLA: si modifica SOLO il file che app.py importa. Verificare md5 PRIMA e DOPO ogni deploy.**

**2. DA QUANTO È SU IL PROCESSO** — giudicare solo i trade nati dopo l'avvio.
```
ps -o lstart=,etime= -p 1
```

**3. STATO VIVO (non lo status, i contatori veri)**
```
sqlite3 /var/data/trading_data.db "SELECT (SELECT COUNT(*) FROM trades) trades, (SELECT COUNT(*) FROM phantom_forensic) tagli, (SELECT MAX(ts_entry) FROM phantom_forensic) ult_taglio, (SELECT COUNT(*) FROM curva_nascita) curve, (SELECT MAX(trade_ts) FROM curva_nascita) ult_curva;"
```
→ Se `ult_curva` è vecchia mentre `ult_taglio` è di adesso → **il film non si registra** (vedi nodo aperto sotto).

**4. ENV REALI DEL CANCELLO** — mai citarli a memoria, leggerli.
```
env | grep -iE "CANCELLO|MOSSE|GRASSO|PICCO|GATE|SOGLIA|SCORE|VERITAS|CAPSULE" | sort
```

**5. SCHEMA PRIMA DI OGNI SELECT** — mai indovinare nomi tabelle/colonne.
```
sqlite3 /var/data/trading_data.db ".tables"
sqlite3 /var/data/trading_data.db "PRAGMA table_info(NOMETABELLA);"
```

---

## 🎯 IL PARADIGMA D'INGRESSO (deciso da Roberto, NON rinegoziabile ogni volta)

**NON c'è una finestra temporale. Non sono 10s, non sono 6s, non sono 15s. È un'OSSERVAZIONE.**

- L'aggancio è osservazione a **costo zero** (no esposizione, no fee). Si guarda il candidato muoversi.
- **Appena il candidato si comporta da maschio — cresce, fa il colpo, tiene il grasso — ENTRA.** Non si aspetta un timer.
- Si entra **mentre sale**, PRIMA che sia grasso da iniziare la discesa. Al picco si è già in ritardo.
- **Firma ambigua all'ingresso** (verificato su 675 trade): a 2 colpi NON sai con certezza se è maschio o trans/femmina. Si accetta il rischio: se sale e tiene il grasso minimo, ENTRA, **qualunque etichetta abbia.** Il trailing in uscita fa il resto — mangia il grasso e molla quando scende.
- Quindi: **si prende rischio su tutti — maschi, trans, femmine.** Il filtro non è perfetto e non deve esserlo. Meglio entrare e lasciar decidere all'uscita, che tagliare a monte e perdere i maschi.

**Cosa Claude NON deve più fare:** proporre di distinguere maschio/femmina all'ingresso; proporre finestre temporali fisse; proporre di tarare un gate in più; rimangiarsi questo paradigma a ogni chat.

---

## 🔴 IL NODO APERTO AL 22 GIU (da chiudere, non da riscoprire)

**Contraddizione codice vs dati nel FILTRO M/F (`MINA_CANCELLO_SALITA`, riga ~12588 del file V16):**

- Il codice dice: se il **picco** (`_grasso`) ha toccato `CANCELLO_GRASSO_MIN` (1.50) → `_ha_grasso_vivo=True` → **NON taglia, ENTRA.**
- I dati dicono: candidati con picco **+4.22 / +3.84 / +3.57** vengono **TAGLIATI** con `MINA_CANCELLO_SALITA`.
- Inoltre: **137 tagliati a picco ≥1.5, di cui 68 (49,6%) sarebbero stati WIN** — maschi veri buttati.

**Codice e dati si contraddicono → il file che gira NON è il file che leggiamo** (vedi Controllo 1).
**O** il film (`curva_nascita`) è fermo dal 19 giu perché `_save_curva_nascita` (chiamata unica riga ~14996) è agganciata all'EXIT di un trade entrato: se nessun trade entra, niente curva → si decide alla cieca.

**PROSSIMO PASSO CONCRETO (uno, mirato):**
1. Controllo 1 → stabilire il file vivo.
2. Sul file vivo, confermare che la mina taglia sul **grasso corrente** invece che sul **picco** (è il bug descritto nel commento del codice come "già corretto" — ma forse corretto solo nel file morto).
3. Spostare il salvataggio `curva_nascita` a MONTE del cancello (registrare OGNI candidato osservato, entri o no) → così il film dei maschi tagliati esiste.
4. UN fix per volta, md5 prima/dopo, test import, deploy, verifica che MASCHI ENTRATI sulla diretta smetta di stare a 0.

---

## 📌 DATI DURI GIÀ DIMOSTRATI (non rifare le query, sono questi)
- 137 tagli a picco ≥1.5 da `MINA_CANCELLO_SALITA`; 68 WIN / 69 LOSS (monetina → la mina non separa).
- Su `phantom_forensic` (la foto) maschi e capre hanno medie identiche: durata ~10.7s, MAE ~0.3. **La foto non separa.**
- `curva_nascita` (il film, tick per tick `[t,pnl,mfe]`) è l'unico posto dove si separano — ma è ferma al 19 giu.
- ENV verificati 22 giu: `CANCELLO_MOSSE=2`, `CANCELLO_GRASSO_MIN=1.50`, `GATE_PEAK_OFF=true` (gate vecchio, spento).

---

## 🧭 REGOLE DI METODO (perché i loop nascono quando si rompono)
- **Dati reali prima di modificare.** Mai patch al buio, mai ENV cambiato senza prova.
- **md5 prima e dopo ogni deploy.** È il controllo che chiude il loop del "file sbagliato".
- **Un fix = un file = un test (import, non solo py_compile) = un deploy verificato.** Mai pacchettare.
- **Schema DB sempre letto, mai indovinato.**
- **Onestà nuda.** Se codice e dati si contraddicono, dirlo subito, non costruirci sopra una teoria.
- **Claude NON accede al server.** Roberto incolla. Query corte (cellulare).
- **Cadaveri da non riattivare:** streak_4, Narratore, RSI/MACD, ZONA_MORTA, CROMO, gate "3 morsi", SHORT (LONG-only).

---

## ▶️ COME SI APRE LA PROSSIMA CHAT
Roberto allega questo file e scrive: **"Apertura OVERTOP. Fai i 5 controlli."**
Claude esegue i 5 controlli (chiede gli output), poi — e solo dopo — riparte dal NODO APERTO.
Niente teoria prima dei 5 controlli. Niente riscoperta di ciò che è già scritto qui.

---

## 🔥 SESSIONE 22-23 GIUGNO — CATENA FIX (caricata su Render)

Trovati e corretti 4 bug in catena che tenevano MASCHI ENTRATI = 0:

1. **`_grasso` cieco** (riga 12532): leggeva `_canc_max_usd`, variabile MAI scritta da nessuno → grasso sempre 0 → tutto "piatto" → tagliato. FIX: fallback su `_rit_picco_pre` (osservatore vivo, in dollari giusti).

2. **75% / `_si_sgonfia` all'ingresso** (riga 12576): tagliava i maschi al primo respiro (scendi sotto il 75% del picco = buttato). Roberto: "il maschio non si sgonfia, e se cala un po' è il momento di PRENDERLO non di buttarlo". FIX: ingresso ora guarda `_grasso_ora >= CANCELLO_GRASSO_MIN` (grasso ADESSO in mano), non il picco né lo sgonfiamento.

3. **`MASCHIO_DIRETTO` tappato** (riga 8843) — IL NODO VERO: `_gia_aperto = bool(_phantoms_open)` → bastava 1 sola posizione aperta per bloccare MASCHIO_DIRETTO PER SEMPRE. Il motore loggava "ENTRA SUBITO" fino a 32 mosse ma non apriva mai. FIX: `len(_phantoms_open) >= 5` (allineato al resto del codice che ragiona su max 5 posizioni). `_close_phantom` (16134) fa il pop correttamente, timeout 60s.

4. **PRESA_SECCA spenta** (ENV `PRESA_SECCA_OFF=true`): l'UNICO schema vincente (18/18 win, +20$) era disattivato. Riacceso `PRESA_SECCA_OFF=false`. `PRESA_SECCA_USD=1.0` = +1$ NETTO in tasca (fee già dentro), va bene così.

**AUDIT vecchio mondo (754 trade chiusi):** P1/P2/score aprono su grasso 0 (esempio: score 71, peak_pnl 0.0 → loss). Uscite dominanti = ANTIPRECIPIZIO (-298$, 0 win), ZONA_MORTA (~-380$, 0 win), HARD_STOP (-99$). Totale vecchio mondo ≈ -1000$. Unica cosa positiva = PRESA_SECCA. CONCLUSIONE: P1/P2/score come giudici d'ingresso vanno spenti (confermato dai dati, non a intuizione). DA FARE: spegnere uno alla volta, con prova, NON in blocco.

**ENV stato 23giu mattina:** MASCHIO_ENTRA_DIRETTO=true, CANCELLO_MOSSE=2, CANCELLO_SALITA_OFF=false, PRESA_SECCA_OFF=false (riacceso), PRESA_SECCA_USD=1.0, CANCELLO_GRASSO_MIN=1.50, GATE_PEAK_OFF=true.

**PARADIGMA INGRESSO (Roberto, definitivo):** niente finestre temporali, niente distinguere M/F all'ingresso. Il candidato è osservato tick per tick (MASCHIO_DIRETTO conta le mosse su). Sale N mosse → ENTRA mentre sale (non al picco) → PRESA_SECCA prende +1 netto → esce. Femmina = piatta/storna subito, si smaschera da sola. Rischio preso su tutti.

**PROSSIMA VERIFICA:** dopo deploy del file con fix #3, controllare MASCHI ENTRATI sulla diretta. Se si stacca da 0 = porta aperta. Se resta 0 = c'è un 5° blocco a valle di _open_shadow_position, cercarlo lì.
