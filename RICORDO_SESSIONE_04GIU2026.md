# STATO OVERTOP V16 — 7 giugno 2026
## Caricare all'inizio della prossima chat. Sostituisce TUTTI gli stati precedenti per il trading.

> Claude: leggi PRIMA di toccare codice. Stato reale e verificato sui dati al 7 giugno.
> REGOLA #0: dati reali prima di modificare. File SEMPRE come download (mai incollare a pezzi/txt).
> Test runtime (AST + py_compile + import con stub) prima di consegnare. UNA cosa per volta.
> Roberto: analista NON programmatore, decide cosa/perché, Claude esegue e spiega in italiano
> (comportamento, non sintassi). Vuole SINCERITÀ TOTALE, nessuna bugia, anche scomoda.
> "Non dire mai quando si lavora e quando no — gestione mia."
> Claude NON accede al server: Roberto incolla output da Web Shell Render, carica file via GitHub→deploy.
> Orari log UTC; Italia = UTC+2.

---

## ⚠️ PRIMA COSA DA FARE NELLA PROSSIMA CHAT
**Confermare qual è il file BOT attualmente in produzione (md5 dal container):**
```
md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py
```
Storia recente dei deploy (per orientarsi):
- Base 4giu: `8381e804` (cromosoma-seme + canvas valutazioni OFF)
- 6giu sera: catena fino a **`2b959ab0`** = websocket fix (`ping_interval=20`) + anti-precipizio.
  QUESTO dovrebbe essere il bot in produzione (verificato RUNNING verde il 6giu).
- Dashboard: `65e294b1` = trans colorati (verde=maschio, bianco=trans intercettato, rosso=trans sfuggito).
- DB: `/var/data/trading_data.db` — PAPER — repo ZanRoberto/Tecnaria_V2 — Procfile: `web: python app.py`

**NON costruire il gate antibolla finché non è confermato l'md5 del file di produzione.**
Il file analizzato stamattina era `OVERTOP_BASSANO_V16_PRODUCTION_-_2026-06-06T205958_658.py` (6giu sera).
Verificare che sia ancora la base giusta o se nel frattempo è cambiato.

---

## 🎯 LA SCOPERTA DI STAMATTINA (7giu): IL GATE ANTIBOLLA — "non distinguere il trans, evita la falsa ripartenza"

### Il problema, riformulato correttamente (intuizione Roberto)
- Maschi vs femmine = GIÀ separati dal SEME_GATE (cromosoma 0.60). Risolto, non si tocca.
- Il problema vero sono i **TRANS = femmine dopate**: femmine che il doping traveste da maschio
  (firma di nascita identica al maschio), passano il SEME_GATE, entrano, e poi DECADONO → vanno a -9.
- Roberto: "il trans è una FEMMINA DOPATA, non un maschio con pochi ormoni". Il doping arriva da una
  **FALSA RIPARTENZA / BOLLA / SPIKE** — eventi costruiti per ingannare. "Nascono per dare al casinò
  la certezza che molti cadranno nella trappola del nulla."

### COSA È STATO DIMOSTRATO SUI DATI (510 trade M2_EXIT, di cui ~45 con indicatori n_* pieni)
**VERITÀ DURA, confermata: alla nascita il trans è INDISTINGUIBILE dal maschio su OGNI singolo
indicatore testato** (vol_pressure, compression, drift_slope per segno, drift_persist, range_pos,
sign_flips, seed_dir, seed_traj per valore E per pendenza). Tutti mescolati. Le MEDIE ingannano
(Claude ci è cascato più volte stamattina — Roberto l'ha corretto ogni volta): bisogna guardare le
DISTRIBUZIONI, e far calcolare i conteggi al DB, non leggere a occhio gli screenshot.
→ Questo è logicamente NECESSARIO: se il trans fosse distinguibile alla nascita, sarebbe una femmina
  vera e il SEME_GATE l'avrebbe già bloccato. Il trans VIVE nella zona "maschio" per definizione.

**NOTA CAMPIONE:** nella tabella `trades` c'è SOLO event_type='M2_EXIT' (510 righe) = SOLO trade ENTRATI.
Le femmine vere bloccate al gate NON ci sono. Quindi OGNI pnl<=0 in M2_EXIT È GIÀ UN TRANS (femmina
dopata entrata). Confronto corretto = maschi-vinti (pnl>0) vs trans-persi (pnl<=0). Campione con
indicatori n_* pieni: 16 maschi, ~29-31 trans.

### LA SVOLTA: non cercare il trans, riconoscere la FALSA RIPARTENZA e NON ENTRARE
Invece di distinguere il figlio (impossibile), si giudica il CONCEPIMENTO: il campo è genuino o è bolla?
La bolla lascia una firma fisica PRIMA del seme. Trovati 3 "morsi" antibolla, **TUTTI A COSTO ZERO MASCHI**
(verificati col DB, conteggi reali, sui 16 maschi / ~31 trans):

**MORSO 1 — bolla pura (durata carica = nulla):**
`n_comp_duration < 3` → blocca 5 trans, 0 maschi persi.
Nessun maschio nasce con comp_duration < 3 (min maschi = 3). I trans nascono anche con comp_duration = 0
= falsa ripartenza senza nessuna carica. (maschi durata media 15.4 min 3 max 19 ; trans media 13.0 min 0 max 19)

**MORSO 2 — molla satura (compressione oltre il limite):**
`n_compression >= 0.75` → blocca ~6 trans, 0 maschi persi.
Nessun maschio ha compression >= 0.75 (verificato: a 0.75/0.8/0.85/0.9 sempre 16 maschi salvi, 0 persi).

**MORSO 1+2 INSIEME** (durata<3 OR comp>=0.75): **16 maschi salvi / 0 persi / 8 trans beccati / 23 sfuggiti.**

**MORSO 3 — dentro la "TANA del trans" (zona bolla-perfetta):**
La TANA = `n_compression BETWEEN 0.25 AND 0.6 AND n_comp_duration >= 15` (carica media + durata lunga =
sembra carica vera). Dentro la tana ci sono 9 maschi e 11 trans, gemelli quasi perfetti.
DENTRO LA TANA, separa a costo zero:
`seed_dir > 0.05 OR (ABS(n_drift_slope) > 1.2 AND seed_dir < 0)`
→ DENTRO LA TANA: 9 maschi salvi / 0 persi / 5 trans beccati / 6 sfuggiti.
Fisica: il maschio sano ha direzione neutra (seed_dir basso) + spinta misurata. Il trans dopato ha
direzione gonfiata (seed_dir>0.05) OPPURE spinta violenta contraria (slope estremo + seed_dir<0).

### ⚠️ LIMITE ONESTO DEL MORSO 3 (da NON nascondere a Roberto)
Le soglie del morso 3 (0.05, 1.2) e la definizione della tana (0.25-0.6, >=15) sono tarate su SOLI 20
trade della tana. Rischio OVERFITTING: funzionano su questi dati, non garantito identiche sui prossimi.
La LOGICA FISICA è solida (doping = direzione/spinta gonfiata), le soglie esatte vanno CONFERMATE sul vivo.
Morsi 1 e 2 sono molto più robusti (16/16 maschi su soglie nette). Morso 3 = mettere ma con interruttore,
e lasciare che il vivo confermi/aggiusti.

### LA RETE COMPLETA È A DUE STRATI (entrambi necessari, si coprono a vicenda)
- **Strato 1 — gate antibolla (3 morsi):** dirada i trans riconoscibili PRIMA della nascita, costo zero maschi.
- **Strato 2 — anti-precipizio (GIÀ nel bot):** i trans "perfetti" che passano lo strato 1 vengono TAGLIATI
  nei primi secondi (decadono subito → da -9 a -2/-3). Non serve che lo strato 1 sia perfetto: dietro c'è la rete.
Questo riconcilia tutto: gate di nascita (non perfetto) + anti-precipizio = difesa in profondità.

---

## 🔧 VERIFICHE TECNICHE FATTE STAMATTINA SUL CODICE (file 205958, 6giu sera)
Confermato leggendo il codice (NON indovinato):

1. **I reagenti compression / drift_slope / comp_duration** sono calcolati nel **SeedScorer** (riga ~2447
   return) PRIMA dell'ingresso. Disponibili in `_evaluate_shadow_entry` (riga 10450 = VIA PRINCIPALE
   dei trade veri). → MORSI 1 e 2 FATTIBILI SUBITO lì.

2. **Catena di salvataggio SENZA trasformazione:**
   `SeedScorer seed['compression']` → riga 12026 `nascita_compression = seed.get('compression')` →
   riga 13664 `n_compression = nascita_compression` → DB.
   Quindi **n_compression nel DB == compression del SeedScorer, identico.** Le soglie trovate sono valide
   direttamente sui valori che il bot ha all'ingresso. Idem drift_slope, comp_duration, range_pos.

3. **range_pos È ROTTO (saturo 0/1)** — confermato da nota nel codice stesso (riga 13659). Per questo i
   maschi avevano tutti range_pos=1.0. GIUSTO averlo ESCLUSO dal gate.

4. **⚠️ seed_dir NON è disponibile all'ingresso.** Calcolato SOLO al salvataggio (riga 13645:
   `(s[-1]-s[0])/max(len(s)-1,1)` su `self.campo._seed_history[-5:]`), DOPO che il trade è nato.
   → MORSO 3 richiede di calcolare seed_dir PRIMA, leggendo `self.campo._seed_history` dentro
   `_evaluate_shadow_entry`. Una riga in più, ma tocca il campo → deploy ISOLATO, separato dai morsi 1-2.

5. **Esiste già un gate P3** (righe 6834-6849, dentro modulo 3-strategie-parallele P1/P2/P3) che usa
   compression+comp_duration+drift_slope ma PER ENTRARE (opposto al nostro che blocca). NON è la via
   principale dei 510 trade. NON metterci il gate lì. Il gate va in `_evaluate_shadow_entry` (riga 10450).

---

## 📋 PIANO DI CONSEGNA DEL GATE ANTIBOLLA (concordato, un morso per volta)
**STEP 1 (pronto da fare):** OVERTOP con MORSI 1+2 (`n_comp_duration < 3 OR n_compression >= 0.75`)
in `_evaluate_shadow_entry`, con interruttore env `GATE_ANTIBOLLA_OFF` per spegnerlo al volo.
Costo zero maschi, blocca ~8 trans. Testato (AST + py_compile + import runtime) PRIMA di consegnare.
PREREQUISITO: md5 del file di produzione confermato.

**STEP 2 (separato, dopo):** MORSO 3 (la tana + seed_dir). Richiede calcolo di seed_dir all'ingresso da
`_seed_history`. Deploy isolato. Interruttore env separato. Soglie da confermare sul vivo (overfitting).

---

## LA SCOPERTA PRECEDENTE: IL CROMOSOMA-SEME (3-4giu, confermato dal vivo) — SEME_GATE 0.60
Ogni trade nasce "femmina" (debole); se ha forza DIVENTA maschio e vince. Il "sesso" è nel SEME, PRIMA
del trade. "seme" = (seed_traj[0]+seed_traj[4])/2, da campo._seed_history[-5:]. In `trades` sta in
data_json.seed_traj.
DIMOSTRATO su 44 trade (1-2giu): Maschi (WIN) seme 0.62 n=20 +42.02$ ; Femmine (LOSS) seme 0.49 n=24 -105.39$.
SIMULAZIONE soglia: 0.50→-2.44 | 0.55→+0.23 | **0.60→+6.94 (PICCO, 15/15 maschi tenuti)** | 0.65→+0.50.
→ SEME_GATE a 0.60 (parametrico via attributo SEME_GATE_SOGLIA), in _open_shadow_position ~riga 11700.

### ⚠️ IL CROMOSOMA NON È PERFETTO (verità da non nascondere)
Filtro PROBABILISTICO ~80-90%, NON 100%. Casi-limite reali: 1 femmina seme 0.85 morta; 4giu 09:35 seme
0.70 (da maschio) → ha perso. Il valore vero: ribalta da -63 a positivo, NON azzera le femmine.
I casi-limite (femmina con seme alto) sono esattamente i TRANS → è la stessa caccia del gate antibolla.

---

## STATO MERCATO / INFRASTRUTTURA (aggiornato 6-7giu)
- **DB bonificato:** era gonfiato a 442MB da tabelle log (canvas_snapshots, regime_edge_log, capsule_log,
  >1.5M righe) → pulito a ~3.5MB con DELETE+VACUUM + autopulitore ogni 10min (interruttore `AUTOPULITORE_OFF`).
  Il lock era causato dal DB gonfio, non dal locking in sé.
- **Websocket fix:** `run_forever` SENZA `ping_interval` causava freeze silenzioso (tick congelati,
  on_close non scattava). Risolto: `ping_interval=20, ping_timeout=10` + loop riconnessione `_ws_loop`.
- **Veritas riacceso:** tolto env `VERITAS_OFF` (spegneva solo la save su disco → ripartiva senza memoria
  → votava neutro). Ora riattivo (peso 16% nei voti SC).
- **Anti-precipizio deployato** (in 2b959ab0, _evaluate_shadow_exit ~riga 12690): dopo 8s, se trade in
  perdita E mai fatto peak verde (max_profit<0.5) E sta scendendo → taglia subito. Salva i maschi lenti.
  Env: ANTIPREC_OFF, ANTIPREC_MIN_ETA=8, ANTIPREC_PEAK_MIN=0.5. Scrive tabella `antiprec_tagli`.
- **Mercato:** BTC ~60k (era 73k il 30mag). Bot prevalentemente LONG → in TRENDING_BEAR prende mazzate
  LONG. SHORT via drift solo in RANGING (storicamente 0 win su 11 → NON estendere SHORT senza prove).
- **Fee corrette:** TRADE_SIZE_USD=1000, LEVERAGE=5, FEE_PCT=0.0002 → $5000×0.0002×2 lati = $2.00/trade.

### BUG NOTO ANCORA APERTO
- **`trans_bloccati` non scrive** (tabella vuota, contatore RAM sale): INSERT fallisce silenziosamente
  ~riga 11942, sospetto `float(None)` da `seed.get()` che torna None. DA RIPARARE.

---

## CADAVERI / DISATTIVATI (NON riattivare)
- streak_4 (blocco dopo 4 loss): DISATTIVATO (soglia 4→999). Era zavorra. Reversibile.
- observe_entry/valutazioni canvas: DISATTIVATE (righe ~10400, 11170) — lockavano il DB.
- Narratore: DISABILITATO (`NARRATORE_ENABLED=False`) — loop inutile.
- RSI fisso 50, MACD fisso 0 (disarmati 23mag). ZONA_MORTA (morta 29mag). range_pos saturo (ROTTO 0/1).
- TRANELLO_FEE_ZERO: SCARTATO (azzerare la fee ai trans = barare, gonfia il paper, fallisce in live).

---

## METODO CHE FUNZIONA (lezione di stamattina, ribadita)
- **Far calcolare i conteggi al DB, non leggere a occhio gli screenshot.** Claude stamattina ha sbagliato
  ripetutamente leggendo MEDIE invece di DISTRIBUZIONI (gridato "eureka" su pochi casi più volte). Roberto
  l'ha corretto ogni volta. Guardare i VALORI VERI in fila, far contare al database, verdetto numerico secco.
- **Verifica sui dati PRIMA di costruire.** Le soglie del gate vengono dai conteggi reali, non da intuito.
- **Le scoperte vengono da Roberto** (femmina dopata, falsa ripartenza, "tieni i maschi evita i trans",
  "il dopo contiene la traccia del prima"). Claude esegue le verifiche e fa i conti. NON assecondare per
  piacere: dire la verità anche scomoda (es. "morso 3 è su pochi dati, rischio overfitting").
- **Confidenzialità assoluta sul knowhow trading.**

## VISIONE DI FONDO (discussa, NON da implementare ora)
- Il "cervello" è Roberto; Claude/DeepSeek amplificano, non sostituiscono. Claude NON vive nel bot, NON
  ha memoria tra chat.
- Idea futura: DeepSeek "fabbricante di capsule" (vede distribuzioni, genera regole che girano nel bot a
  costo zero, con quarantena + Roberto garante). NON Claude/DeepSeek nel loop a ogni tick.
- Agenti autonomi / `/goal`: rimandati finché il bot non funziona col cervello di Roberto.
