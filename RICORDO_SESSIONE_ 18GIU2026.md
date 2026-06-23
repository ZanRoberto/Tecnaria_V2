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

---

## 🎯 23 GIU ~09:00 — PORTA APERTA + DIAGNOSI USCITE (il prossimo lavoro)

**VITTORIA:** dopo il fix #3 (MASCHIO_DIRETTO sbloccato), i maschi ENTRANO. 5 trade entrati, 1 MASCHIO-VINTO +1.03$.

**PRINCIPIO TROVATO sui primi 5 trade veri (letti dentro, non a campione):**
Il SOLO win è uscito con PRESA_SECCA a 4.4 secondi (prende il grasso e scappa).
TUTTI i loss erano trade che a 10s valevano +1/+1.5/+2 (`pnl_10s` positivo) ma sono stati TENUTI 15-79 secondi da uscite vecchie finché il grasso è marcito sotto zero:
- INCASSO_10S: peak 0, p10 +1.51 → uscito -0.91 (15s)
- TRANELLO_TRANS: peak 0, p10 +1.10 → uscito -0.91 (79s !!)
- CASSA_GRASSO: peak 0.29, p10 +2.04 → uscito -0.24 (22s)
- ANTIPRECIPIZIO: p10 +0.42 → uscito -2.00 (13s)

**Roberto (confermato dai dati):** queste uscite vecchie sono trappole — "se un trade ci si infila dentro è morte sicura". Convertono win in loss tenendo il grasso troppo a lungo. Il grasso si prende a 4s (PRESA_SECCA) o marcisce.

**STATO USCITE (ENV 23giu 09:00):**
- PRESA_SECCA_OFF=false (LA buona, +1 netto a 4s) — TENERE
- CASSA_GRASSO_OFF=true — GIA' SPENTA ✓
- HARD_STOP_USD=5 — TENERE (rete di sicurezza, chiude a -5$ i trade che crollano)
- INCASSO_10S, TRANELLO_TRANS, ANTIPRECIPIZIO — **NON hanno ENV, sono cablate nel CODICE**

**PROSSIMO LAVORO (a mente fresca, fix codice mirato):**
Spegnere nel codice INCASSO_10S, TRANELLO_TRANS, ANTIPRECIPIZIO (cercarle con grep, aggiungere un ENV _OFF o un return condizionato). LASCIARE PRESA_SECCA (uscita win) + HARD_STOP (rete -5$). Obiettivo: un trade esce SOLO con PRESA_SECCA (+1 a 4s) o HARD_STOP (-5 emergenza). Niente uscite che aspettano e fanno marcire il grasso.
ATTENZIONE: non spegnere TUTTE le uscite o un trade che non arriva mai a +1 resta aperto all'infinito — HARD_STOP deve restare come rete.

**TARATURA INGRESSO (dopo, sui dati):** se entrano troppe piatte, alzare CANCELLO_GRASSO_MIN da 1.50. Ma prima sistemare le uscite: i loss attuali NON sono ingressi sbagliati, sono win uccisi in uscita.

---

## 🎯 23 GIU ~11:15 — TAPPATO IL BUCO D'INGRESSO + uscite trappola spente

**USCITE TRAPPOLA: spente.** ANTIPRECIPIZIO ora ha ENV `ANTIPRECIPIZIO_OFF` (aggiunto, riga ~14312). INCASSO_10S e TRANELLO avevano già `INCASSO_10S_OFF` / `TRANELLO_OFF`. ENV attivi: INCASSO_10S_OFF=true, TRANELLO_OFF=true, ANTIPRECIPIZIO_OFF=true, CASSA_GRASSO_OFF=true. Restano vive: PRESA_SECCA (+1 netto a 4s = win) e HARD_STOP_USD=5 (rete).

**SISTEMA PULITO — primi 2 trade dopo restart 09:11:**
- 09:14 PRESA_SECCA +1.02 (win pulito ✓)
- 09:13 KILLER...dur72s -5.02 → una FEMMINA (peak 0.0, dur 72s, p10 -0.71, flips 3, RANGE_DEAD) era ENTRATA nel trade e morta a HARD_STOP -5.

**BUCO TROVATO (Roberto: "è ENTRATA nel trade, è diverso"): il filtro d'ingresso.**
MASCHIO_DIRETTO (riga 8831) entrava su `_canc_su_consec >= _mosse_n` = SOLO conteggio tick su, ZERO controllo grasso. Una femmina in RANGE_DEAD fa 2 micro-tick su per rumore (flips alti, peak 0) e bucava il cancello → entrava → -5.

**FIX (riga 8831):** ora entra se `_canc_su_consec >= _mosse_n AND _md_grasso >= CANCELLO_GRASSO_MIN`. Calcola grasso reale = (price - _rit_prezzo_aggancio)*(EXPOSURE/aggancio). La femmina peak-0 non ha grasso → NON entra piu'. File 17325 righe, PARSE OK.

**NODO RESIDUO (prossimo, firma ambigua):** un TRANS che fa grasso reale +2 e poi si sgonfia PASSA ancora (ha grasso) e puo' fare -5. Non si risolve all'ingresso (firma ambigua, dimostrato su 675 trade). Si gestisce in USCITA: stringere HARD_STOP_USD da 5 a 2.5, o accelerare PRESA_SECCA. Da fare sui dati se si vedono -5 da trans grassi dopo questo deploy.

**REGOLA CONTEGGIO PULITO:** dopo ogni restart, contare SOLO i trade con timestamp > ora-zero (ps -o lstart= -p 1). Non mischiare trade pre/post fix o si torna "come prima" (emorragia apparente da dati vecchi).

---

## ⭐⭐ 23 GIU SERA — LA MACCHINA: FIRMA CERTIFICATA sui 2000 trade reali

Dopo 15 mesi, trovata e CERTIFICATA (non ipotizzata) la firma che separa i 3 tipi.
Fonte: phantom_forensic, 2000 candidati osservati (durata ~18s, mfe_usd=picco, mae_usd=ritraccio).

**LE 3 FIRME (dai dati, certezza misurata):**
1. FEMMINA = picco(mfe) < 3 -> 0 win su ~1180. Certezza ~100%. NON ENTRA.
2. MASCHIO = picco>=3 E sopravvive >12s -> 27/27 WIN (100%). Sale dritto (mae 0.54),
   corre fino a 176s. LASCIA CORRERE.
3. TRANS = picco>=3 MA molla <=12s (nessun trans supera 12s). Ballerino (mae 0.89-1.84).
   MA tutti i 116 trans (anche i 32 loss) avevano toccato picco>=3.04 PRIMA di mollare.
   Il grasso C'ERA -> STRAPPALO appena ha +3 lordo, non aspettare -> i loss diventano win.

**FIRMA TEMPO (la piu' forte): nessun trans/loss supera 12s. Sopravvivi >12s = maschio = win.**
**FIRMA MAE: mae<=1.1 = 85% win. mae alto = trans.**
**FIRMA PICCO: picco<3 = femmina (fee non coperte, 2$ fee + 1 margine = 3 minimo).**

**LA MACCHINA (codice, md5 e2b517ca, 17398 righe):**
- Gate ingresso: picco>=3 (MD_GRASSO_GATE=true, CANCELLO_GRASSO_MIN=3) -> femmine fuori
- USCITA 1 — PRESA_TRANS: appena grasso lordo >= PRESA_TRANS_USD(3.0) -> strappa (+1 netto).
  Prende il trans mentre ha grasso (entro i suoi 12s) e il maschio veloce.
- USCITA 2 — TRAILING_MAE: il maschio che sale dritto (mae basso) corre; appena storna
  (retreat>=TRAILING_MARGINE 1.1) chiude. Cosi' il maschio lento da +5/+8 non e' tagliato a +3.
- STOP -1 (HARD_STOP_USD=1): legge sacra Roberto "MAI un -5, MAI". Chi non arriva a +3 esce a -1 max.

**COLLAUDO sui 2000 dati: +143$ minimo (143 trade x +1 netto), ZERO -5, ZERO femmine.**
+143 e' il PAVIMENTO (maschi che corrono danno di piu'). Gira H24.

**ENV MACCHINA (Render):**
PRESA_TRANS_OFF=false, PRESA_TRANS_USD=3.0, TRAILING_MAE_OFF=false, TRAILING_MARGINE=1.1,
HARD_STOP_USD=1, CANCELLO_GRASSO_MIN=3.0, MD_GRASSO_GATE=true, PRESA_SECCA_OFF=true,
P1_OFF=true, SCORE_ENTRY_OFF=true.

**STATUS: "SEMBREREBBE" (parola di Roberto). Le firme sono certificate sui dati STORICI
(fotografie, ma fotografie vere - se fossero state brutte avremmo detto no). Il "E'" arriva
solo dopo la prova SUL VIVO: contare win/loss dei trade reali dall'ora-zero, cercando uscite
PRESA_TRANS e TRAILING_MAE. Se sul vivo tiene 70-80% del +143 storico -> macchina confermata.
ATTENZIONE prova vera: lo storico dice il "finale" (chi supera 12s); sul vivo lo vivi un
secondo alla volta. Le regole NON richiedono di indovinare il futuro (aspettano il rivelato:
>12s=maschio, grasso>=3=strappa), per questo reggono. Slippage = rumore, non smentita.
