# STATO OVERTOP V16 — 18 giugno 2026 (v6)
## Caricare all'INIZIO della prossima chat. Sostituisce TUTTI gli stati precedenti (incluso v5 17giu).

> **Claude: leggi TUTTO questo prima di toccare qualunque cosa. Poi NON hai più niente da chiedere su nomi tabelle, ENV, logiche o versione. È tutto qui.**
>
> REGOLA #0: dati reali prima di modificare. File SEMPRE come download. Test runtime (import + py_compile) prima di consegnare. UNA cosa per volta. Letture codice MIRATE (~50 righe).
> Roberto: analista, decide cosa/perché; Claude esegue come ingegnere capo e VERIFICA TUTTO, anche le proprie certezze. SINCERITÀ TOTALE, zero piaggeria. MAI dire "quando si lavora o no" (gestione di Roberto). MAI narrazione: solo numeri dai dati. MAI indovinare: leggere.
> Claude NON accede al server: Roberto incolla output da Web Shell Render. Roberto lavora da cellulare: query SU UNA RIGA SOLA e corte; niente nano. Se la shell resta col `>`: scrivere `;` + Invio o Ctrl+C.
> Orari log UTC; Italia = UTC+2.

---

## ⛔ REGOLA SCHEMA DB — MAI PIÙ INDOVINARE (18giu, Roberto incazzato giustamente)
Prima di QUALSIASI query: leggere lo schema vero, MAI tirare a indovinare nomi.
- **DB:** `/var/data/trading_data.db` — PAPER (zero soldi reali).
- Lista tabelle: `sqlite3 /var/data/trading_data.db ".tables"`
- Colonne di una tabella: `sqlite3 /var/data/trading_data.db "PRAGMA table_info(NOMETABELLA);"`
- SOLO DOPO scrivere la SELECT.

### SCHEMA REALE DELLE TABELLE CHIAVE (verificato dal codice 18giu)
**`trades`** — trade chiusi (creata da app.py, non dal motore). Campi usati nelle query reali:
- `timestamp` (epoch), `pnl` (NETTO), `reason`, `event_type` (valori: `EXIT`, `M2_EXIT`), `data_json` (JSON con dentro `peak_pnl`, `duration`).
- Query trade ultimi 15 min:
  `sqlite3 /var/data/trading_data.db "SELECT datetime(timestamp,'+2 hours') AS ora, ROUND(pnl,2) AS netto, ROUND(CAST(json_extract(data_json,'\$.peak_pnl') AS REAL),2) AS picco, ROUND(CAST(json_extract(data_json,'\$.duration') AS REAL),0) AS durata, reason FROM trades WHERE event_type='M2_EXIT' AND timestamp>=datetime('now','-15 minutes') ORDER BY id DESC;" -header -column`
- NB: se i nomi non tornassero, leggere `PRAGMA table_info(trades);` — NON indovinare.

**`gate_peak_osserva`** — ogni aggancio col gate. Colonne reali:
`id, ts (REAL epoch), picco_pre REAL, soglia REAL, t_atteso REAL, passa INTEGER, observer INTEGER, regime TEXT, direction TEXT, prezzo REAL`
- Vedere agganci nuovi (soglia 2.5): `sqlite3 /var/data/trading_data.db "SELECT datetime(ts,'unixepoch','localtime') AS ora, round(picco_pre,2) AS picco, soglia, round(t_atteso,1) AS t, passa FROM gate_peak_osserva WHERE soglia>=2.0 ORDER BY id DESC LIMIT 20;"`

**`presa_secca_tagli`** — chiusure di PRESA_SECCA. Colonne:
`id, timestamp TEXT, eta_s REAL, pnl_presa REAL, prezzo REAL`

**`curva_nascita`** — curve per simulazioni (LA TABELLA D'ORO). Colonne:
`id, trade_ts REAL, firma TEXT, n_punti INTEGER, peak_nascita REAL, t_peak_s REAL, pnl_a_10s REAL, curva_json TEXT, created_ts REAL`
- `curva_json` = lista `[[t, pnl_lordo, mfe], ...]`. Qualunque regola d'uscita si simula camminando questa lista. **È così che si valida PRIMA di toccare codice/ENV.**

**`ritardo_agganci`** — agganci del purgatorio. Colonne:
`id, timestamp TEXT, aggancio_ts REAL, direction TEXT, vpress REAL, comp REAL, seed REAL, momentum TEXT, volatility TEXT, trend TEXT, prezzo REAL, peak_pnl REAL, entrato INTEGER`

**`phantom_forensic`** — tagli/mine (telecamera). Colonne:
`id, ts_entry REAL, ts_close REAL, block_reason TEXT, direction TEXT, price_entry REAL, price_close REAL, pnl_netto REAL, is_win INTEGER, ...`
- Cosa hanno bloccato le mine ultima ora: `sqlite3 /var/data/trading_data.db "SELECT block_reason, COUNT(*) AS n FROM phantom_forensic WHERE ts_entry > strftime('%s','now','-1 hour') GROUP BY block_reason ORDER BY n DESC;" -header -column`

**`trans_bloccati`** — respinti. Colonne: `id, timestamp TEXT, causa TEXT, vpress REAL, comp REAL, prezzo REAL`

Altre tabelle presenti (`.tables` completo): bot_state, canvas_*, capsula_*, capsule_*, libro_pesca, loss_sfuggiti, mem_*, primi_secondi, regime_edge_*, sim_results, telemetry, veritas_*, winning_signatures.

---

## 🔖 BUILD ATTUALE (18giu)
- **File vivo che gira ORA:** md5 `943e0ccd529ba3ef4d8dedcaf0204879` (= file caricato 18giu T18:02).
  Contiene: GATE PEAK CONFERMATIVO, PRESA_SECCA, fix ANTIPRECIPIZIO, tutti i guardiani del purgatorio. **NON contiene** la CORSIA PRIVATA del 17giu (quel build `1a2e6968` non è quello vivo) né il trailing 0.30.
- File: `/opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py` — Procfile `web: python app.py`
- DB: `/var/data/trading_data.db` — Live: tecnaria-v2.onrender.com — Repo: ZanRoberto/Tecnaria_V2
- **PRIMA COSA OGNI SESSIONE:** `md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py` + `ps -o lstart= -p 1`. I container Render cambiano nome a ogni restart (es. dwjh5 → 9jbff). Giudicare solo trade nati DOPO l'avvio del processo attuale.

---

## 🎯 LA REGOLA — VALIDATA SUI DATI, È QUESTA E BASTA (18giu)
Roberto ha chiuso 15 mesi di tentativi con UNA regola sola, validata su **200 curve reali** (curva_nascita, camminate tick-by-tick):

**1. ENTRATA — "esame del piattello".** Il campo gravitazionale dice solo "accade qualcosa" (campanello, non giudice). Il candidato viene OSSERVATO a esposizione zero, fuori dal gate (niente fee). Entra SOLO se durante l'osservazione il **picco** raggiunge **2.50 lordo** (`_rit_picco_pre`, è LORDO perché pre-ingresso, nessuna fee ancora pagata). La firma del maschio è **che continua a SALIRE** (fa nuovi massimi). Chi non tocca mai 2.50 = capra/femmina/trans → NON entra mai. Niente limite di tempo: conta CHE salga, non quando.

**2. PRESA — munge il maschio (DA SCRIVERE, Strada B).** Una volta dentro: finché fa nuovi massimi, tieni; quando cede dal picco → strappa e chiudi. Margine ottimo trovato sui dati = **0.30** dal picco. QUESTO TRAILING NON ESISTE ANCORA nel codice vivo: per ora fa da rete `PRESA_SECCA` (+1$ netto fisso).

**Risultato simulazione 200 trade:**
- Bot di prima (entra su tutto): **−392.65$**
- Solo entrata-2.50 (uscita = finale, pessimista): **+56$**
- Con strappo al cedimento (tick-by-tick reale): margine 0.00→+47.6, **margine 0.30→+56.4 (MASSIMO)**, 0.50→+50, 0.80→+44, 1.20→+39.
- Su 200: **150 scartate** (mai toccano 2.50), **8 toccano 2.50 ma non salgono** (femmine, fuori), **42 entrano**. Dei 42, i maschi veri sono ~14; gli altri vengono gestiti dalla presa.

**Onestà sui numeri:** il +56 robusto NON dipende da ipotesi; viene quasi tutto dalle 150 scartate (non comprare merda). Lo strappo è il pezzo incerto sui maschi lunghi (picco a 40-70s impossibile da beccare al ms). Il numero vero su cui contare è ~+56, non +128.

**La regola NON è ancora certificata sul VIVO.** Validata su storico = solidissima, ma il giuramento finale lo dà il vivo con 30-40 trade nuovi. Reversibile via ENV in 30s.

---

## 🎯 STATO ENV ATTUALE (Render, verificato sul processo vivo 18giu post-restart)
Queste sono le ENV MESSE STANOTTE per accendere la regola del piattello:
```
GATE_PEAK_OBSERVER = false      ← il gate DECIDE (non più "osserva e lascia passare")
GATE_PEAK_USD = 2.50            ← soglia LORDA: il picco osservato deve toccare 2.50
GATE_PEAK_FINESTRA_SEC = 999    ← di fatto tolto il limite di tempo (conta che salga)
CROLLO_MAX_USD = 0              ← ANTI-FALSA-RIPARTENZA spento
VOL_ISTERICO_MAX = 0            ← ANTI-MAGO spento
ANTICORDA_OFF = true            ← ANTI-CORDA spento
MAE_FILTRO_USD = 0              ← MAE-FILTRO spento
PRESA_SECCA_OFF = false         ← PRESA_SECCA attiva (rete)
PRESA_SECCA_USD = 1.0           ← chiude a +1$ NETTO
```
Filosofia: una sola logica decide (sale→entra). Tutto il resto dei guardiani dei 15 mesi
è REMATO VIA dalla decisione. Restano MUTI a DEPOSITARE dati (tabelle), non a decidere.

### Come cambiare se il vivo lo chiede (no codice, solo ENV):
- Maschi buoni scartati perché si fermano a ~2.0? → abbassare `GATE_PEAK_USD` a 2.0/1.8.
- 2.50 lascia passare ancora merda? → alzarlo.
- Si esce troppo presto/tardi col +1 fisso? → tarare `PRESA_SECCA_USD`.

---

## 🔧 DOVE STA LA REGOLA NEL CODICE (file 943e0ccd, letto 18giu)
- **`_evaluate_shadow_entry`** inizia a **r.10577**. È una "scena costituzionale a 4 zone" (verbale, testimoni, SC sovrano) — complessità stratificata in 15 mesi, è ciò che ha fatto −392.
- **GATE PEAK CONFERMATIVO**: r.**12781-12853**. Confronta `_rit_picco_pre` (LORDO) con `GATE_PEAK_USD` entro `GATE_PEAK_FINESTRA_SEC`. Se `observer=false` → blocca chi non passa (`return` r.12853). Scrive in `gate_peak_osserva`.
- **`_pos_usd`** (valore osservato): r.**12424** = `_delta * EXPOSURE(5000)` → **LORDO** (nessuna fee). Per questo la soglia 2.50 è gross diretta.
- **`_rit_picco_pre`** aggiornato come max di `_pos_usd`: r.**12560-12562**.
- Guardiani-rumore (ora spenti via ENV): ANTI-FALSA-RIPARTENZA r.12458, ANTI-MAGO/VOL r.12508, ANTI-CORDA r.12548, MAE r.12404.
- **`_evaluate_shadow_exit`** inizia a **r.13308**. PRESA_SECCA r.**13821-13851** (current_pnl è LORDO, sottrae fee 2$ per netto). ANTIPRECIPIZIO r.13772, INCASSO_10S r.13869.
- **DOVE ANDRÀ IL TRAILING 0.30 (Strada B):** dentro `_evaluate_shadow_exit`, tracciare il picco post-ingresso e chiudere quando `picco - current >= MARGINE_CEDIMENTO` (ENV nuovo, default 0.30). Da scrivere con test import a runtime, un file un deploy.

---

## ✅ PROSSIMI PASSI (in ordine)
1. **RACCOGLIERE.** Lasciar girare. Aspettare che compaiano righe nuove in `gate_peak_osserva` con `soglia=2.5`. Con BTC fermo + soglia alta, gli ingressi sono RARI: zero-trade in fase piatta/ribassista è CORRETTO (LONG-only), non un bug.
2. **LEGGERE I PRIMI 30-40 AGGANCI nuovi.** Quanti agganci, quanti passano (passa=1), che picco. Confronto coi trade chiusi (`trades`) per vedere il PnL reale. Questo certifica (o smentisce) la soglia 2.50 sul vivo.
3. **SE certificato:** Strada B — scrivere il trailing 0.30 (`MARGINE_CEDIMENTO`) per spremere i maschioni invece di lasciarli scendere da +7 a +3 (oggi PRESA_SECCA li chiude a +1).
4. **SE maschi buoni scartati:** abbassare `GATE_PEAK_USD` (solo ENV) e ri-osservare.
5. Solana — solo dopo BTC pulito: `SYMBOL` è hardcoded r.159 (1 riga: renderlo ENV), poi ricalibrare soglie in $.

---

## 📜 PROIBIZIONI PERMANENTI (NON violare mai)
- MAI distinguere maschi/femmine ALL'INGRESSO (firma ambigua, etichette retrospettive — dimostrato su 675 trade).
- MAI proporre SHORT (LONG-only per scelta; zero-trade in fase ribassista è CORRETTO).
- MAI veti con finestre temporali/conteggi (REGOLA-22MAG: un LOSS marca firma PERICOLOSA finché un WIN stessa firma la pulisce).
- MAI citare parametri/fee/costanti senza leggerli dal codice. Fee VERA: $5000 × 0.0002 × 2 lati = **$2.00/trade** (TRADE_SIZE=1000, LEVERAGE=5, FEE_PCT=0.0002).
- MAI proporre azioni sul vivo senza simulazione sui dati storici (curva_nascita). "La simulazione salva Roberto da Claude."
- MAI pacchettare più modifiche (una modifica = un test import a runtime = un deploy).
- MAI riscrivere il file da zero (963KB): solo modifiche chirurgiche sui punti giusti.
- MAI indovinare nomi tabelle/colonne: leggere `.tables` e `PRAGMA table_info`.
- MAI proporre mine d'uscita (CASSA_GRASSO ecc.) come prima mossa: il lavoro è a MONTE (osservazione). Eccezione: il trailing 0.30 È voluto da Roberto come presa del maschio, non come toppa.

## 📜 CADAVERI (NON riattivare)
streak_4, observe_entry, Narratore (NARRATORE_ENABLED=False), RSI/MACD fissi, ZONA_MORTA,
range_pos saturo, TRANELLO_FEE_ZERO, gate antibolla "3 morsi", CROMO (era anti-fee non anti-trans),
quarantena capsule. CapsulaRegimeEdge e Oracolo predittivo: scollegati apposta (ragionano per medie, non per individuo).

## METODO (lezioni consolidate)
- Letture codice MIRATE (~50 righe). Le narrazioni delle istanze precedenti sono opinioni, non fatti: verificare con query.
- MD5 sul container = unica verità del deploy. Build nei trade = unica verità del giudizio.
- PRIMA di dare la colpa al codice: controllare le ENV (più volte il "buco" era un interruttore osserva/off).
- DISTRIBUZIONI, non MEDIE. Far calcolare al DB.
- Modifiche grosse (reindentazione): via SCRIPT Python + test AST/py_compile, mai a mano.

## VISIONE DI FONDO (NON da implementare ora)
Il cervello è Roberto; Claude/DeepSeek amplificano. Claude NON vive nel bot, NON ha memoria tra chat — questo documento È la memoria. Capsule Vive (intelligenza distribuita), agenti autonomi: rimandati.
