# STATO OVERTOP V16 — 14 giugno 2026 (v4)
## Caricare all'inizio della prossima chat. Sostituisce TUTTI gli stati precedenti.

> Claude: leggi PRIMA di toccare codice. REGOLA #0: dati reali prima di modificare.
> File SEMPRE come download. Test runtime (AST + py_compile + import con stub) prima
> di consegnare. UNA cosa per volta. **Letture codice MIRATE** (range stretto, ~50 righe
> max). Letture larghe del file da 16k righe saturano la chat in poche ore.
> Roberto: analista NON programmatore, decide cosa/perché, Claude esegue come ingegnere capo
> e VERIFICA TUTTO, anche le proprie certezze (spesso contengono errori).
> Vuole SINCERITÀ TOTALE, nessuna piaggeria, anche scomoda. Mai dire "quando si lavora
> o no" — gestione di Roberto.
> Claude NON accede al server: Roberto incolla output da Web Shell Render.
> Claude può fetchare endpoint pubblici (vedi REGOLA #0 originale).
> Orari log UTC; Italia = UTC+2. Confidenzialità assoluta sul knowhow trading.

---

## 🔖 BUILD ATTUALE IN PRODUZIONE (verificato 14giu 09:39 sul container)
- **`305252c3332e`** = file con TUTTE le mine + telecamere + GATE PEAK + SINAPSI + CASSA_GRASSO
- File: `/opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py`
- Caricato 13giu alle 13:35 ora server (probabilmente da DeepSeek o altra istanza Claude
  in chat separata; ha aggiunto 3 sistemi nuovi sopra la base "telecamere" di Claude 12giu)
- DB: `/var/data/trading_data.db` — PAPER — Procfile: `web: python app.py`
- Live: tecnaria-v2.onrender.com
- Repo: ZanRoberto/Tecnaria_V2

### Storico build recenti (per orientarsi)
- `47ec6d5a52c8` (9-12giu) = solo le 7 mine, senza telecamere — solo CROMO registrava
- `918edffdeab7` (12giu pomeriggio, Claude) = aggiunte 9 telecamere MINA_, mai messo in prod
- `305252c3332e` (13giu 13:35, attuale) = `918edffdeab7` + GATE PEAK + SINAPSI + CASSA_GRASSO

### Marchio build nei trade
BUILD_MD5 = primi 12 char dell'md5 del file (r.44-53, salvato r.14363 e 14471).
Query per i soli trade del codice attuale:
```sql
WHERE json_extract(data_json,'$.build')='305252c3332e'
```
A ogni nuovo deploy: rifare `md5sum` sul container e aggiornare questo valore.

---

## 🎯 STATO OPERATIVO (14giu) — 8 MINE D'INGRESSO + USCITA

### Le 8 mine d'ingresso (in ordine di applicazione)
1. **MAE** (📉, ~r.12307) — scarta chi scende dall'aggancio
2. **ANTI-FALSA-RIPARTENZA** (💀, ~r.12362) — crolla sotto -2$ nei secondi gratis
3. **RI-AGGANCIO SOSPETTO** (🎭, ~r.12253) — flag che marchia crollo entro 20s
4. **CONTROSCATTO** (🦊) — falsa partenza sgonfiata dal picco
5. **ANTI-ISTERIA** (🌀, ~r.12396) — vol_pressure alto + momentum debole
6. **STRATEGIA 4+2** (⏳, ~r.12547) — incerti a 4s ricevono +2s. Roberto: FONDAMENTALE
7. **TRANS PIATTI** (🟰) — chi non sale mai
8. **GATE PEAK CONFERMATIVO** (✅, ~r.12750, NUOVO 13giu) — entra solo se picco ≥+1$ entro 15s.
   "I maschi partono rossi e risalgono TARDI (t_peak medio WIN 33s vs LOSS 5s)".
   Su 154 trade simulati: da -247$ a +60$, WR 30%→62%.
   **DEFAULT: GATE_PEAK_OBSERVER=true (solo logga, NON blocca).**
   Mettere `GATE_PEAK_OBSERVER=false` quando confermato dai dati.

### Telecamere attive (9 etichette MINA_ in `phantom_forensic`)
- `MINA_MAE` 📉, `MINA_ANTIFALSA_RIPARTENZA` 💀, `MINA_RIAGGANCIO_EREDITATO` 🎭
- `MINA_ANTIISTERIA` 🌀, `MINA_CONTROSCATTO` 🦊, `MINA_TRANS_PIATTI` 🟰
- `MINA_RIPIEGAMENTO` 🔄, `MINA_ANTICORDA_CROLLO` 🪢, `MINA_ANTICORDA_INCERTO` 🪢
- (`MINA_GATE_PEAK` appare solo se OBSERVER=false)

Distinzione RI-AGGANCIO: nel branch ANTIFALSA_RIPARTENZA controllo `_crollo_min == -999.0`.
Se sì → `MINA_RIAGGANCIO_EREDITATO`; altrimenti → `MINA_ANTIFALSA_RIPARTENZA`.

### Sistemi nuovi del 13giu (NON cambiano il trading, sono telescopi)
- **SINAPSI LOOKUP** (~r.12005, +87 righe). Prima dell'ingresso interroga `phantom_forensic`
  cercando casi con stessa firma (regime+direction+momentum+vol+trend) negli ultimi N giorni.
  Se ≥20 casi simili, logga WR storico, picco e crollo medio. SHADOW PURO: logga, non decide.
  ENV: `SINAPSI_LOOKUP_ENABLED=false` (default). Tabella: `sinapsi_observations`.
- **CASSA_GRASSO** (~r.13598, +77 righe). MINA USCITA: se peak≥1.5$ E età≥10s E ceduto 0.5$
  E ancora_verde → chiude subito. Per catturare i "furbi" (190 LOSS che toccano grasso a
  20s+ e poi mollano). **DEFAULT: CASSA_GRASSO_OFF=true (spenta).** Tabella: `cassa_grasso_tagli`.
  **NOTA:** Roberto ha esplicitato CASSA_GRASSO_OFF=true in ENV Render il 14giu — comportamento
  invariato, solo documentazione esplicita. Rischio se attivata: può tagliare maschi a +1$
  che sarebbero saliti a +6/+11. La regola del 13giu *"DIVIETO USCITA prima di lavorare a
  monte"* resta valida — CASSA_GRASSO esiste ma resta dormiente per ora.

### Uscita attuale (invariato dal v3)
- **SALVA_VERDE anticipato** (🎯, ~r.13305). ATTENZIONE ZONA MORTA verificata 12giu:
  con `if _cur_sv >= _vm_sv and (_max_sv - _cur_sv) >= _tg_sv` (cur sopra 2.5$ E ceduto 0.5),
  i picchi tra 2.5 e 3.0 NON scattano MAI matematicamente. Fix concettuale (NON deployato):
  condizione sul PICCO invece dell'attuale. Aspettare dati prima di toccare.
- **ANTI-PRECIPIZIO** (✂️, ~r.13397): dopo 8s, perdita E mai peak verde → taglio.
  Roberto vorrebbe spegnerlo, decisione rimandata ai dati delle telecamere.

### ENV CHIAVE (Render, 14giu)
```
VERDE_MIN_USD=2.5, TRAIL_GIU_USD=0.5, RITARDO_INGRESSO_SEC=4,
RITARDO_EXTRA_SEC=2, ANTICORDA_ZONA=1.0, CROLLO_MAX_USD=2.0,
RIAGGANCIO_MEMORIA_SEC=20, VOL_ISTERICO_MAX=1.0,
HARD_STOP_USD=5, RIPIEG_PRE_USD=0, MERCATO_MORTO_OFF=true,
CASSA_GRASSO_OFF=true (esplicito 14giu),
GATE_PEAK_OBSERVER=true (default), GATE_PEAK_USD=1.0, GATE_PEAK_FINESTRA_SEC=15,
SINAPSI_LOOKUP_ENABLED=false (default)
```

### MULTI-COPPIA (pronto, NON attivo) — Solana DORMIENTE, focus BTC.

---

## 🔭 SCOPERTE 12-14 GIUGNO (numeri sui dati, NON narrazione)

### CROMO_TOTALE processato — è un filtro anti-FEE, non anti-CROLLO
Query 12giu su `phantom_forensic` (2.638 record CROMO):
- bloccati: 2.638
- avrebbero vinto (mfe≥2$): 203 (7.7%)
- **sarebbero crollati (mae≤-2$): 0** — ZERO su 2.638
- picco medio bloccato: 0.72$ — crollo medio 0.52$
- range medio mfe-mae: **0.196$** (venti centesimi!)
- pnl_netto medio: -1.896$ (include le fee ≈ 2$)

**Verdetto:** narrazione "CROMO salva dalle femmine che crollano a -9" è FALSA.
È un filtro anti-noise su mercato piatto. Risparmia 2.638×~2$ = ~5.000$ di fee evitate
su trade morti. Costo opportunità: 203 maschi veri lasciati fuori.

### Le 7 mine giravano CIECHE prima del 12giu
Verificato sul codice 12giu: 6 mine su 7 facevano `return` senza chiamare `_record_phantom`.
Solo CROMO scriveva. Bug `trans_bloccati not writing` del 7giu = diagnosi sbagliata
(INSERT corretto, ma blocco mai eseguito). Vera causa: telecamere mai installate.
Risolto 12giu con patch telecamere + GATE PEAK + SINAPSI il 13giu.

### Tribunale parziale (14giu 09:39, prime ore di telecamera vivente)
Dati limitati ma indicativi:
- `MINA_ANTIISTERIA` = **112 blocchi (84% del totale!)** — di gran lunga la più aggressiva
- `MINA_TRANS_PIATTI` = 15
- `MINA_MAE` = 5
- `MINA_CONTROSCATTO` = 1
- MINA_ANTIFALSA_RIPARTENZA, RIAGGANCIO_EREDITATO, RIPIEGAMENTO, ANTICORDA_×2 = ZERO scatti
- Da processare con `WHERE block_reason LIKE 'MINA_%' AND mfe_usd IS NOT NULL`
  appena passano 24-48h di mercato vivo.

### SEME_GATE — narrazione "80-90% precisione" smentita su 570 trade live
Dalla dashboard "SEME GATE VERITÀ DA TRADES" (12giu e 14giu):
- 14giu: 134 maschi (+279.2$) / 440 femmine (-1229.68$) / **406 ERRORI: ♀alto 398, ♂basso 8**
- 12giu: 133 maschi / 437 femmine / 403 errori (395 ♀alto, 8 ♂basso)
- Trans bloccati cumulativi: 15.392 (era 12.936 il 12giu) → +2.500 in 48h
- **Sostanzialmente identico in 48h** (mercato fermo: solo +1 maschio, +3 femmine entrati)

**Realtà su 574 trade entrati:** 398/440 = 90% delle femmine sono ibridi che il gate
non distingue. La narrazione 3-4giu basata su 44 trade era ottimistica.
**Il SEME_GATE è un anti-NOISE (blocca 15.392 piatti), non un anti-TRANS.**
Da rivedere come funzione, NON come "rete antitrans risolta".

### Tesi Roberto del 12giu — DA VERIFICARE su `primi_secondi` (25.614 record vivi)
**"Le mine d'ingresso fanno preselezione. Chi passa NON è morto. Molti a 10s hanno grasso
piccolo. Ma il sistema d'uscita (SALVA_VERDE pavimento 2.5$, MIN_HOLD 10s, ANTI-PRECIPIZIO)
non riconosce il lavoro delle mine: tratta i passati come potenziali maschioni che devono
salire molto, e così lascia sfumare grassi reali."**

Conseguenza ipotizzata da verificare sui dati: abbassare il pavimento SALVA_VERDE
(incassare grasso piccolo > 0 invece di lasciar tornare a -2 netti) sarebbe meglio
matematicamente, perché la fee è già spesa e non torna indietro.

Query da costruire dopo aver letto `.schema primi_secondi`.

### Silenzio di 9-21 ore (13-14giu) — diagnosi
Bot vivo, mercato piatto + ANTIISTERIA aggressiva = 0 entrati su 294 agganci.
NON è WebSocket congelato (gli scansamenti aumentano: 36→294 in 21h).
ANTIISTERIA fa 84% dei blocchi → forte sospetto di zavorra, ma verdetto vero solo
da query con mfe/mae.

---

## 📊 QUERY PRONTE PER LA PROSSIMA SESSIONE

### Processo del tribunale per mina (dopo 24-48h di vita)
```sql
SELECT block_reason, COUNT(*) AS bloccati,
       SUM(CASE WHEN mfe_usd>=2.0 THEN 1 ELSE 0 END) AS avrebbero_vinto,
       SUM(CASE WHEN mae_usd<=-2.0 THEN 1 ELSE 0 END) AS sarebbero_crollati,
       ROUND(AVG(mfe_usd),2) AS picco_medio,
       ROUND(AVG(mae_usd),2) AS crollo_medio
FROM phantom_forensic
WHERE block_reason LIKE 'MINA_%' AND mfe_usd IS NOT NULL
GROUP BY block_reason ORDER BY bloccati DESC;
```

### Verifica vita bot
```bash
sqlite3 /var/data/trading_data.db "SELECT datetime(MAX(ts),'unixepoch','+2 hours') AS ultimo_prezzo FROM primi_secondi;"
```

### Verdetto BTC sulla build attuale
```sql
SELECT COUNT(*), ROUND(SUM(pnl),2), ROUND(AVG(pnl),2)
FROM trades WHERE json_extract(data_json,'$.build')='305252c3332e'
AND reason NOT LIKE '%ENTRY%';
```

### Verifica GATE PEAK in observer mode (legge logs)
Quando GATE_PEAK_OBSERVER=true, logga "GATE PEAK [OSSERVA]: avrebbe SCARTATO...".
Per misurare i suoi verdetti, andrà costruita query su log o aggiunto un counter dedicato.

---

## 📜 STORICO INVARIATO

### CROMOSOMA-SEME / SEME_GATE 0.60 (3-4giu, ATTIVO)
seme = (seed_traj[0]+seed_traj[4])/2 da campo._seed_history[-5:].
SEME_GATE=0.60 in _open_shadow_position (parametrico SEME_GATE_SOGLIA).
**ATTENZIONE:** narrazione "filtro probabilistico 80-90%" SMENTITA da 574 trade live.
Realtà: distingue piatti da non-piatti, non distingue maschi da trans.

### INFRASTRUTTURA (6-7giu, valido)
- DB bonificato (442MB→3.5MB, autopulitore 10min, AUTOPULITORE_OFF interruttore)
- Websocket fix (ping_interval=20, ping_timeout=10) — ha tenuto, nessuna ricaduta
- Veritas riacceso (peso 16%). Fee: $2/trade. BTC ~60k.

### CADAVERI (NON riattivare)
streak_4, observe_entry, Narratore, RSI/MACD fissi, ZONA_MORTA, range_pos saturo (ROTTO 0/1),
TRANELLO_FEE_ZERO, gate antibolla "3 morsi" (7giu, superato).

### METODO (lezioni consolidate 12-14giu)
- **Letture codice MIRATE** (range stretto, ~50 righe). Letture larghe saturano la chat in
  poche ore. Lezione dolorosa del 12giu.
- **Le narrazioni delle istanze precedenti sono opinioni fossilizzate, NON fatti.**
  Una frase tipo "il gate funziona" senza numeri non vale niente. Verificare con query.
  Esempi: "CROMO blocca femmine che crollerebbero" (FALSO sui dati), "SEME_GATE 80-90%
  precisione" (FALSO sui dati).
- **Una mina muta è ingiudicabile.** Sempre accendere telecamera prima di decidere.
- **MD5 sul container = unica verità di un deploy.** Marchio `build` nei trade = unica
  verità del giudizio. **Verificare il file in produzione PRIMA di consegnare patch**:
  potrebbero esserci modifiche di altre istanze (come è successo 13giu con GATE PEAK).
- DISTRIBUZIONI, non MEDIE. Far calcolare al DB, non a occhio.

### VISIONE DI FONDO (discussa, NON da implementare)
Il cervello è Roberto; Claude/DeepSeek amplificano, non sostituiscono. Claude NON vive
nel bot, NON ha memoria tra chat. Idea futura: DeepSeek "fabbricante di capsule".
Agenti autonomi / `/goal`: rimandati.

### BUG NOTI ANCORA APERTI
- `trans_bloccati` ancora vuota (diagnosi 7giu sbagliata, vera causa: blocco mai eseguito).
  Potrebbe non più servire dato che `phantom_forensic` registra tutto via telecamere.
- Documento `/mnt/user-data/outputs/AUDIT_SINAPSI_13GIU2026.md` citato nel codice (zona
  SINAPSI LOOKUP) — esiste su qualche istanza Claude del 13giu, non accessibile da qui.
