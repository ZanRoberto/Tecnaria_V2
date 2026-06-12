# STATO OVERTOP V16 — 12 giugno 2026
## Caricare all'inizio della prossima chat. Sostituisce TUTTI gli stati precedenti per il trading.

> Claude: leggi PRIMA di toccare codice. REGOLA #0: dati reali prima di modificare.
> File SEMPRE come download (mai incollare a pezzi/txt). Test runtime (AST + py_compile +
> import con stub) prima di consegnare. UNA cosa per volta.
> Roberto: analista NON programmatore, decide cosa/perché, Claude esegue e spiega in italiano
> (comportamento, non sintassi). Vuole SINCERITÀ TOTALE, nessuna bugia, anche scomoda.
> "Non dire mai quando si lavora e quando no — gestione mia."
> Claude NON accede al server: Roberto incolla output da Web Shell Render, carica file via GitHub→deploy.
> Orari log UTC; Italia = UTC+2. Confidenzialità assoluta sul knowhow trading.

---

## ⚠️ PRIMA COSA DA FARE NELLA PROSSIMA CHAT — DISCREPANZA MD5 DA CHIARIRE
```
md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py
```
- Produzione DICHIARATA (12giu): **`836f35da8eb4`**
- File allegato alla chat del 12giu (OVERTOP_..._2026-06-12T093312_261.py):
  md5 = **`47ec6d5a52c8`** → **NON COINCIDE** con la produzione dichiarata.
- Possibili cause: (a) il file allegato è una versione più nuova non ancora deployata,
  (b) il deploy è cambiato dopo l'annotazione. **Chiarire PRIMA di qualsiasi modifica:**
  il marchio `build` nei trade dice la verità (BUILD_MD5 = primi 12 caratteri dell'md5
  del file stesso, calcolato all'avvio, righe 44-53).
- DB: `/var/data/trading_data.db` — PAPER — repo ZanRoberto/Tecnaria_V2 — Procfile: `web: python app.py`
- Live: tecnaria-v2.onrender.com

---

## 🎯 STATO ATTUALE (12giu): LE 7 MINE D'INGRESSO (tutte attive coi default)
*(Verificate nel codice del file 12giu — righe indicative)*

1. **MAE** (📉, ~r.12307) — scarta chi scende dall'aggancio. "Il maschio non scende, la dopata sì."
2. **ANTI-FALSA-RIPARTENZA** (💀, ~r.12362) — chi crolla sotto -2$ nei secondi gratis, anche se rimbalza. ENV: CROLLO_MAX_USD.
3. **RI-AGGANCIO SOSPETTO** (🎭, ~r.12253) — il mago che si ri-aggancia pulito dopo un crollo (memoria 20s). ENV: RIAGGANCIO_MEMORIA_SEC.
4. **CONTROSCATTO** (🦊) — falsa partenza sgonfiata dal picco.
5. **ANTI-ISTERIA** (🌀, ~r.12396) — vol_pressure alto + momentum DEBOLE. Default codice = 0 (spento), **ACCESA via ENV Render: VOL_ISTERICO_MAX=1.0**.
6. **STRATEGIA 4+2** (⏳, ~r.12547, idea Roberto) — incerti a 4s ricevono +2s; chi corre entra, chi crolla esce. Il tempo è il giudice. ENV: ANTICORDA_ZONA, RITARDO_EXTRA_SEC, ANTICORDA_OFF.
7. **TRANS PIATTI** (🟰) — chi non sale mai.

**USCITA — SALVA_VERDE ANTICIPATO** (🎯, ~r.13305, 11giu): incassa il grasso vicino al picco
in qualsiasi secondo (il MIN_HOLD 10s faceva perdere picchi tipo +2.71→-0.12).
Stessa identità del SALVA_VERDE classico, solo anticipata. Spegnibile TRAIL_OFF.

**ANTI-PRECIPIZIO ancora attivo** (✂️, ~r.13397, da 6giu): dopo 8s, in perdita E mai peak
verde (max_profit<0.5) E in discesa → taglio. ENV: ANTIPREC_OFF / ANTIPREC_MIN_ETA=8 /
ANTIPREC_PEAK_MIN=0.5. Scrive tabella `antiprec_tagli`.

### 🔖 MARCHIO VERSIONE (BUILD_MD5)
Ogni trade salva `build` = primi 12 char dell'md5 del codice che l'ha prodotto (r.44-53, salvato a r.14363 e 14471).
Query per i soli trade del codice attuale:
```sql
WHERE json_extract(data_json,'$.build')='836f35da8eb4'
```
(aggiornare il valore se l'md5 di produzione cambia — vedi discrepanza sopra)

### ENV CHIAVE (Render, 12giu)
```
VERDE_MIN_USD=2.5, TRAIL_GIU_USD=0.5, RITARDO_INGRESSO_SEC=4,
RITARDO_EXTRA_SEC=2 (default), ANTICORDA_ZONA=1.0 (default),
CROLLO_MAX_USD=2.0, RIAGGANCIO_MEMORIA_SEC=20, VOL_ISTERICO_MAX=1.0,
HARD_STOP_USD=5, RIPIEG_PRE_USD=0 (spento; default codice 0.60), MERCATO_MORTO_OFF=true
```

### MULTI-COPPIA (pronto, NON attivo)
- SYMBOL ora da ENV (default BTCUSDC). DB_PATH e NARRATIVES_DB da ENV.
- Bug DB hardcoded (riga ~14910) FIXATO — ogni coppia ha il suo DB.
- Per Solana: stesso file in repo Solana + ENV: SYMBOL=SOLUSDC,
  DB_PATH=/var/data/solana_data.db, NARRATIVES_DB=/var/data/solana_narratives.db,
  + ENV in dollari RI-TARATI per il prezzo di SOL (~150-200$ vs BTC ~63k).
- **DECISIONE: Solana seminata ma DORMIENTE. Focus su BTC.**

### VERDETTO BTC (da fare quando il mercato si rianima)
Servono 30-40 trade marchiati a mercato VIVO:
```sql
SELECT COUNT(*), ROUND(SUM(pnl),2), ROUND(AVG(pnl),2)
FROM trades WHERE json_extract(data_json,'$.build')='836f35da8eb4'
AND reason NOT LIKE '%ENTRY%';
```
**NON giudicare sulla lista storica mista** (cimitero di versioni vecchie).

### PHANTOM (verdetto attuale)
Phantom forensic: le mine bloccano spazzatura (0% WR sui campioni veri, es. 15 bloccati→0%,
44→0%). Gli "ALLENTA" sono tutti 1-2 campioni = rumore.
**NON allentare finché un componente non ha 20+ campioni con WR alto.**

### 🔭 PROSSIMO LAVORO GROSSO (a mente fresca): L'OCCHIO SULLA PROVENIENZA
Nessun componente guarda la traiettoria del prezzo nei ~30s PRIMA dell'aggancio.
`_prices_short` (deque maxlen=50, r.4794) esiste ed è alimentato, ma oggi serve solo
al calcolo compressione (r.5293+) — NON è usato per decidere l'ingresso.
È la "sincronizzazione" che Roberto cerca: da dove VIENE il prezzo quando aggancia.

---

## 📜 STORICO — GATE ANTIBOLLA "3 MORSI" (7giu) — ⚠️ SUPERATO DALLE 7 MINE
**Verificato sul codice 12giu: i morsi 1-2-3 NON sono mai stati deployati.** Il piano del 7giu
(morsi su n_comp_duration/n_compression/seed_dir) è stato sostituito dall'architettura delle
7 mine (9-11giu), che attacca lo stesso nemico (il trans/femmina dopata) con un'altra fisica:
comportamento del prezzo nei secondi gratis invece che firma di nascita.
Resta valido come CONOSCENZA (le distribuzioni sui 510 trade M2_EXIT sono reali):

- Alla nascita il trans è INDISTINGUIBILE dal maschio su ogni indicatore testato
  (logicamente necessario: se fosse distinguibile, il SEME_GATE l'avrebbe già preso).
- Nella tabella `trades` c'è solo M2_EXIT = solo trade ENTRATI → ogni pnl<=0 è un trans.
- Morso 1 (n_comp_duration<3): 5 trans, 0 maschi persi. Morso 2 (n_compression>=0.75):
  ~6 trans, 0 maschi. Insieme: 16/16 maschi salvi, 8 trans beccati.
- Morso 3 ("tana": compression 0.25-0.6 + duration>=15, separatore seed_dir>0.05 OR
  slope estremo): 9/9 maschi salvi ma TARATO SU 20 TRADE → rischio overfitting dichiarato.
- range_pos È ROTTO (saturo 0/1, nota nel codice ~r.13659). seed_dir NON disponibile
  all'ingresso (calcolato solo al salvataggio).
- Questi reagenti (compression, comp_duration, drift_slope) restano disponibili nel SeedScorer
  PRIMA dell'ingresso, identici tra DB e runtime → riutilizzabili per "l'occhio sulla provenienza".

## 📜 STORICO — CROMOSOMA-SEME / SEME_GATE 0.60 (3-4giu, ATTIVO)
Ogni trade nasce "femmina"; se ha forza diventa maschio e vince. Il sesso è nel SEME, prima del trade.
seme = (seed_traj[0]+seed_traj[4])/2 da campo._seed_history[-5:]; in `trades` sta in data_json.seed_traj.
Dimostrato su 44 trade: maschi seme 0.62 (n=20, +42.02$), femmine 0.49 (n=24, -105.39$).
Soglia: 0.50→-2.44 | 0.55→+0.23 | **0.60→+6.94 (picco, 15/15 maschi tenuti)** | 0.65→+0.50.
SEME_GATE=0.60 in _open_shadow_position (parametrico SEME_GATE_SOGLIA).
**Non perfetto:** filtro probabilistico ~80-90%. I casi-limite (femmina con seme alto) = i TRANS
→ è esattamente la caccia delle 7 mine. Maschi vs femmine = risolto, non si tocca.

## 📜 STORICO — INFRASTRUTTURA (6-7giu, tutto ancora valido)
- **DB bonificato:** da 442MB (tabelle log >1.5M righe) a ~3.5MB con DELETE+VACUUM +
  autopulitore ogni 10min (interruttore AUTOPULITORE_OFF). Il lock era il DB gonfio.
- **Websocket fix:** run_forever senza ping_interval = freeze silenzioso. Risolto:
  ping_interval=20, ping_timeout=10 + loop riconnessione _ws_loop.
- **Veritas riacceso** (tolto VERITAS_OFF; peso 16% nei voti SC).
- **Fee corrette:** TRADE_SIZE_USD=1000, LEVERAGE=5, FEE_PCT=0.0002 → $2.00/trade (2 lati).
- **Mercato:** BTC ~60k (era 73k il 30mag). Bot prevalentemente LONG → in TRENDING_BEAR prende
  mazzate. SHORT via drift solo in RANGING (0 win su 11 storici → NON estendere senza prove).
- Dashboard build 65e294b1: trans colorati (verde=maschio, bianco=trans intercettato, rosso=sfuggito).

### BUG NOTI
- **`trans_bloccati` non scrive** (tabella vuota, contatore RAM sale): INSERT fallisce silenziosamente
  ~r.11942, sospetto float(None) da seed.get(). DA RIPARARE. *(Verificare se ancora aperto sul codice 12giu.)*

## 📜 CADAVERI / DISATTIVATI (NON riattivare)
- streak_4 (soglia 4→999). observe_entry/valutazioni canvas (lockavano il DB).
- Narratore (NARRATORE_ENABLED=False). RSI fisso 50, MACD fisso 0 (23mag).
- ZONA_MORTA (29mag). range_pos saturo (ROTTO 0/1).
- TRANELLO_FEE_ZERO: SCARTATO (barare sulle fee gonfia il paper, fallisce in live).
- Gate antibolla "3 morsi" (7giu): mai deployato, superato dalle 7 mine.

## 📜 METODO CHE FUNZIONA (lezioni consolidate)
- Far calcolare i conteggi al DB, non leggere a occhio. DISTRIBUZIONI, non MEDIE.
- Verifica sui dati PRIMA di costruire. Soglie dai conteggi reali, non da intuito.
- Le scoperte vengono da Roberto (femmina dopata, falsa ripartenza, 4+2, "il dopo contiene
  la traccia del prima"); Claude verifica e fa i conti. Dire la verità anche scomoda.
- Mai dichiarare "funziona" senza verifica end-to-end. Mai allentare una mina su 1-2 campioni.
- MD5 sul container = unica verità di un deploy. Marchio `build` nei trade = unica verità del giudizio.

## 📜 VISIONE DI FONDO (discussa, NON da implementare ora)
- Il cervello è Roberto; Claude/DeepSeek amplificano, non sostituiscono. Claude NON vive nel bot.
- Idea futura: DeepSeek "fabbricante di capsule" (genera regole da distribuzioni, quarantena,
  Roberto garante). NON Claude/DeepSeek nel loop a ogni tick.
- Agenti autonomi / `/goal`: rimandati finché il bot non funziona col cervello di Roberto.
