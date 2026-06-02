# STATO OVERTOP V16 — 2 giugno 2026 (fine sessione)

> Claude: leggi questo PRIMA di toccare codice. È lo stato reale e VERIFICATO sui dati al 2 giugno sera.
> REGOLA #0: leggere i dati reali (DB/log/endpoint) prima di proporre. File SEMPRE come download, mai da incollare.
> Roberto NON programma: decide cosa/perché, Claude esegue e spiega in italiano (comportamento, mai sintassi).
> Sincerità totale, zero piacioneria. "non devi mai dire quando si lavora e quando no — è gestione mia."
> Verificare md5sum sul container dopo ogni deploy. Il verde Render NON è prova.

---

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║   👁️  LA RISURREZIONE DELL'OCCHIO  (2giu2026)                          ║
║                                                                        ║
║   Il canvas (l'occhio che registra il "prima del seme") era MUTO dal   ║
║   31 maggio. Caccia a 4 anelli, ognuno corretto e ancora cieco, fino   ║
║   alla radice VERA:                                                     ║
║                                                                        ║
║   1. sensori null → popolati alla nascita del verbale. Inutile da solo.║
║   2. hook annidato in `if capsule_manager` → tirato fuori. Inutile.    ║
║   3. hook DOPO i veti (FP_TOSSICO faceva return prima) → spostato      ║
║      PRIMA dei veti. Phantom=3828, canvas=0: ancora cieco.             ║
║   4. RADICE: il bot faceva `CapsulaCanvas(bot_ref=self, ...)` ma        ║
║      l'__init__ reale era `def __init__(self, db_path)` — NON accetta  ║
║      bot_ref → TypeError silenzioso → self.canvas=None → MAI scriveva. ║
║   5. RADICE VERA: tolto bot_ref, ma `from capsula_canvas import         ║
║      CapsulaCanvas` falliva: la classe NON ESISTEVA. Il file era stato ║
║      sovrascritto con una COPIA DEL BOT (28 classi). La capsula che    ║
║      Roberto aveva concepito era stata SCHIACCIATA. "Era un virus,     ║
║      messa com'era."                                                   ║
║                                                                        ║
║   CURA: ricostruito capsula_canvas.py da ZERO, pulito, minimale (154   ║
║   righe). Classe CapsulaCanvas con observe_entry/observe_exit/status,  ║
║   firme allineate ESATTAMENTE a come il bot chiama. Testato end-to-end:║
║   scrive righe vere coi sensori PIENI (oi_carica 0.31, non null).      ║
║                                                                        ║
║   LEZIONE: non fidarsi delle luci della dashboard. Tutto era verde     ║
║   mentre un punto vitale era morto in silenzio. Andare al dato grezzo. ║
║   "Una cosa che non gira e tutto è compromesso" — i 3 fix prima della  ║
║   radice giravano a vuoto su un oggetto che non esisteva.              ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
```

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║   🔬  IL GRAAL — IL "PRIMA DEL SEME"  (intuizione Roberto, 15 mesi)    ║
║                                                                        ║
║   "Prima del trade, dove paghi la fee per vedere, esiste già una       ║
║    traccia che distingue il maschio (win) dalla femmina (loss).        ║
║    Il gioco è tutto lì: NON entrare nelle femmine + entrare sui maschi."║
║                                                                        ║
║   CONCETTO MASCHIO/FEMMINA (già sulla pietra, vedi tavola KILLER E40   ║
║   del 31mag): non sono win/loss casuali — sono DUE NATURE diverse dalla║
║   nascita. La femmina cola e muore (loss da evitare). Il maschio ha    ║
║   spinta che cresce (win da lasciar passare). È FISICA, non poesia.    ║
║                                                                        ║
║   INTUIZIONE 2giu (la chiave nuova): il bot misura QUANTA energia c'è  ║
║   alla nascita (livello, soglia SEED) ma forse NON se quell'energia è  ║
║   VIVA (sale) o MORTA (un picco che sta già colando). Energia morta    ║
║   comprata sul picco = femmina garantita.                              ║
║                                                                        ║
║   PRECEDENTE NEI DATI (trovato nel codice, SeedScorer righe ~2511):    ║
║   range_pos_win ~0.18 (WIN nasce col prezzo IN BASSO nel range)        ║
║   range_pos_loss 0.42-0.65 (LOSS nasce col prezzo IN ALTO = sul picco).║
║   MA il SeedScorer fa l'OPPOSTO: dà peso 0.25 a range_pos premiando il ║
║   prezzo IN ALTO. Possibile controsenso architetturale → DA VERIFICARE.║
║                                                                        ║
║   AVVERTENZA DI ROBERTO (acuta): la traccia potrebbe partire PRIMA del ║
║   fotogramma singolo (l'istante della valutazione). Se l'occhio singolo║
║   non distingue → non vuol dire che non c'è, può partire minuti prima. ║
║                                                                        ║
║   CAUTELA (Claude): pochi dati + desiderio forte = rischio di vedere   ║
║   il Graal dove non c'è. Guardare a mente fredda, "come se non lo      ║
║   desiderassimo". Mai concludere su 2-3 trade.                         ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## IL FILE CHE GIRA / DEPLOYATO (2giu sera)

- **BOT: `80f340d472979cd176fdeb6bcf6ee00c`** — OVERTOP_BASSANO_V16_PRODUCTION.py, ~15.035 righe. DEPLOYATO.
- **OCCHIO: `ab1d0981a73e2ad7ae76a73f109c46b8`** — capsula_canvas.py, ~160 righe. DEPLOYATO accanto al bot.
- AL DEPLOY: `md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py ~/project/src/capsula_canvas.py`
  → `80f340d4...` e `ab1d0981...`
- Nome file invariato. Procfile `web: python app.py`. Live tecnaria-v2.onrender.com. DB `/var/data/trading_data.db`. PAPER.

### CATENA MD5 DEL 2 GIUGNO (tutti testati AST + py_compile + IMPORT RUNTIME)
Bot: `1f5af1af` (1giu, base) → `02298376` (+FRENO_COLATA) → `9df639da` (+sensori nascita) →
`e1bb8f17` (hook fuori da if capsule) → `68e71ed7` (hook prima dei veti) → `326bae25` (tolto bot_ref) →
`d5d53af5` (+spia diagnostica freno) → **`80f340d4` (+range_pos/drift_slope/seed_score nel verbale per l'occhio)** ← ULTIMO.
Occhio: **`ab1d0981`** (canvas ricostruito + 3 campi nuovi). [storico stessa giornata: `4239ee76` era il canvas senza i 3 campi]

### LE MODIFICHE DEL 2 GIUGNO (tutte dentro 80f340d4 + ab1d0981)
1. **FRENO_COLATA** (taglia femmina conclamata in colata): `if pnl<-3.5 E peak<1.5 E durata>35s` → chiude prima
   dell'HARD_STOP. FUORI dal cancello energia. Env: FRENO_COLATA_OFF/PNL=3.5/PEAK=1.5, KILLER_TEMPO=35.
2. **SPIA DIAGNOSTICA FRENO**: quando pnl<-3.5 ma il freno NON taglia, scrive nel log M2 `FRENO_SPIA: ...`
   col motivo (peak troppo alto? durata?). NON cambia comportamento. Serve perché il freno NON castra i loss
   al minimo (vedi PROBLEMA APERTO sotto) e non si capisce perché dalla lettura statica.
3. **OCCHIO RICOSTRUITO** (capsula_canvas.py): observe_entry scrive ENTRY_VALUTAZIONE con sensori pieni.
4. **PRIMA DEL SEME — vita dell'energia**: il verbale (e quindi l'occhio) ora registra ANCHE `range_pos`,
   `drift_slope`, `seed_score` — calcolati dal SeedScorer e anticipati PRIMA dei due hook canvas (P1 riga ~11146
   e P2 riga ~10375), perché il seed si calcolava 96 righe DOPO l'hook anticipato (sarebbero stati null).

---

## STATO DELL'OCCHIO (verificato 2giu sera)
- VIVO: scrive ENTRY_VALUTAZIONE (9495+ righe dopo le 15:34). I sensori del campo sono PIENI (oi_carica ecc.).
- `score/soglia/fp_wr` restano null nell'entry perché l'hook è ANTICIPATO (prima che il bot calcoli la decisione).
  Va bene: per il "prima del seme" servono i sensori del CAMPO (carica, breath, momentum, ORA ANCHE range_pos
  e drift_slope), che sono pieni.
- **ZERO righe EXIT** nel canvas (observe_exit non scrive / non chiamata con successo). MA NON SERVE: l'esito
  (maschio/femmina) lo conosciamo GIÀ dalla tabella `trades`. Roberto: "l'exit non c'entra un cazzo, a quel
  punto siamo già entrati". Il metodo è: prendere i trade veri (esito noto da `trades`) e cucirli all'impronta
  ENTRY del canvas via timestamp (ABS(t.timestamp - c.ts) < 90-120s). L'esito è ETICHETTA per imparare, non per decidere.

## VERIFICA DA FARE AL PROSSIMO GIRO (i 3 campi nuovi si scrivono?)
```
sqlite3 /var/data/trading_data.db "SELECT datetime(ts,'unixepoch'), json_extract(sensori_json,'\$.range_pos'),
json_extract(sensori_json,'\$.drift_slope'), json_extract(sensori_json,'\$.seed_score')
FROM canvas_snapshots WHERE evento='ENTRY_VALUTAZIONE' ORDER BY ts DESC LIMIT 3;"
```
Se range_pos e drift_slope mostrano NUMERI (non null) → l'elemento oggettivo viene registrato. Da lì ogni
femmina che nasce porta con sé la prova: era energia viva (slope>0) o morta (slope<0, prezzo sul picco)?

## LA QUERY DEL GRAAL (quando ci sono femmine vive col "prima")
Cuce trade veri (esito) con impronta-prima (canvas) via timestamp. Confronta maschi (pnl>0) e femmine (pnl<0):
```
sqlite3 /var/data/trading_data.db "SELECT datetime(t.timestamp), t.direction, ROUND(t.pnl,2) esito,
json_extract(c.sensori_json,'\$.oi_carica') oi_long, json_extract(c.sensori_json,'\$.oi_carica_short') oi_short,
json_extract(c.sensori_json,'\$.range_pos') range_pos, json_extract(c.sensori_json,'\$.drift_slope') slope,
json_extract(c.sensori_json,'\$.breath_energia') breath
FROM trades t JOIN canvas_snapshots c ON ABS(strftime('%s',t.timestamp)-c.ts)<90
WHERE c.evento='ENTRY_VALUTAZIONE' AND t.timestamp>'2026-06-02 15:34' GROUP BY t.timestamp ORDER BY t.timestamp DESC;"
```
DOMANDA: le femmine nascono con range_pos ALTO + drift_slope NEGATIVO (energia morta, sul picco)? I maschi con
slope POSITIVO (energia viva)? Se sì → traccia trovata, e si ribilanciano i pesi del SeedScorer SUL DATO.

---

## PROBLEMA APERTO #1 — I LOSS NON CASTRATI AL MINIMO
- Roberto: "i loss non vengono castrati al minimo". CONFERMATO sui dati.
- Trade 14:26 oggi = HARD_STOP **−9.01** col bot completo. Curva di nascita (curva_nascita): femmina peak 0.0
  (conclamata da subito), colata LENTA per ~10 MINUTI fino a −9, con un rimbalzo a metà. Il FRENO_COLATA avrebbe
  dovuto tagliarla a ~2.7min/−3.6 (tutte le condizioni vere: peak 0.0<1.5, durata>35s, pnl<-3.5) ma NON è scattato.
  Anche il 14:10 FRENO è scattato troppo tardi (−5.51).
- env FRENO vuoto sul container = usa DEFAULT (freno ARMATO coi valori giusti: 3.5/1.5/35). Quindi NON è disarmato.
- Codice del freno (riga ~12250 di 80f340d4) è CORRETTO. HARD_STOP (12222) e freno (12250) entrambi raggiungibili,
  ordine giusto. Eppure il freno non scatta. SOSPETTI non risolti: `self._trade_peak_pnl` non resettato all'APERTURA
  (reset solo in _close_shadow_trade righe ~13566/13569, non all'apertura) → potrebbe leggere peak sporco di un
  trade precedente (es. maschio peak 5) e credere sia maschio; oppure P1 (EXPLOSIVE) salta il flusso del freno.
- LA SPIA (in 80f340d4) confesserà sul prossimo loss-colata: al prossimo pnl<-3.5 senza taglio, log M2 scrive
  `FRENO_SPIA: pnl... oltre soglia ma NON taglio → peak=X>=1.5(NON-femmina?)` oppure `dur<35`. LEGGERE QUELLA RIGA.
  PENDING. Poi sistemare SUL DATO (probabile fix: resettare _trade_peak_pnl all'apertura del trade).

## PROBLEMA / NON-PROBLEMA — LO SHORT SPENTO (chiuso bene, NON riaprire)
- BTC è sceso ~5000$ il 2giu e ZERO short, tutti LONG. Roberto all'inizio: "dovevamo esserci". POI ha chiarito:
  **gli short erano spenti perché PERDEVANO SOLO** ("mai visti win, 1-2 per videata il resto solo loss").
- Query: ultimo SHORT_SHADOW reale = 15 maggio, tutti usciti per ZONA_MORTA a −2/−3 (duravano 2-4s e morivano).
- DECISIONE CONDIVISA: spegnerli fu GIUSTO. NON è un buco, è una protezione. NON riaprire a intuito.
- DOMANDA APERTA (per quando ci saranno dati): gli short perdevano per colpa LORO o per il MARE LATERALE morto?
  Se è il mare, in un TREND ribassista VERO potrebbero smettere di perdere. Roberto nota che "ricominciano ad
  apparire in questi giorni" (ora che il mare scende davvero). SE un giorno: solo in TRENDING_BEAR confermato,
  in paper, per misurare se in QUEL mare smettono di perdere. NON riaccendere perché "BTC è sceso". PENDING/rimandato.

---

## METODO — REGOLE SACRE (Roberto)
- Una cosa per volta, testata, prima della successiva. MAI bundle.
- File SEMPRE come download. Roberto NON incolla mai (deploy falliti = file sporcati da shell).
- Test SEMPRE: ast.parse + py_compile + IMPORT RUNTIME reale (non solo compile).
- Verificare md5sum sul container dopo OGNI deploy. Il verde Render NON è prova.
- Mai soglie inventate: tutto dai dati. Mai dedurre da dati mancanti (se il dato manca, si DICE).
- Verificare la CATENA INTERA end-to-end, non il singolo pezzo. "Una cosa che non gira e tutto è compromesso."
- NON fidarsi delle luci della dashboard — andare al dato grezzo (lezione del virus canvas).
- Leggere TUTTO l'output, non un frammento (Claude ha sbagliato leggendo 2 trade su quelli mostrati).
- Mai chiamare "monetine/briciole" i profitti piccoli (sono lingotti per il mare arido).
- Guardare i dati anche quando si desidera conferma (rischio Graal-dove-non-c'è).

## ERRORI DI METODO DEL 2 GIUGNO DA NON RIPETERE
- Ho spostato l'hook canvas 3 volte (sensori, fuori-da-if, prima-dei-veti) PRIMA di verificare che il canvas
  ESISTESSE come oggetto. Erano fix su `self.canvas=None`. Lezione: verificare che l'OGGETTO nasca prima di
  riparare le CHIAMATE all'oggetto. (Verifica diretta in shell: `python3 -c "from capsula_canvas import CapsulaCanvas; ..."`.)
- Ho letto 2 trade su quelli mostrati e concluso "sono tutti uguali". Roberto: "ne sono nati di più, te ne sei
  dimenticata". Leggere TUTTO. Poi ho capito che erano davvero pochi (occhio giovane di 3h) → conclusione onesta.
- Stavo per mandare a caccia dell'hook EXIT del canvas — inutile (l'esito è in `trades`). Roberto mi ha corretto.
- Ho rischiato di far credere che "vinciamo" (3 win recenti). VERITÀ: l'occhio si è acceso quando il mare si era
  calmato → ha catturato per caso 3 win. NON vinciamo. Le femmine grosse di oggi erano PRE-15:34 (occhio cieco).

## STATO DATI / TIMING CRITICO
- L'occhio è VIVO dalle ~15:34 del 2giu. TUTTO ciò che è successo prima è cieco (femmine grosse incluse: −9, −5.51).
- Servono FEMMINE NUOVE nate DOPO le 15:34 (meglio dopo il deploy 80f340d4) per avere il loro "prima" CON range_pos
  e drift_slope. Finora (2giu sera) l'occhio ha catturato pochi trade, quasi tutti win → manca la femmina viva.
- PROSSIMO GIRO: lasciar accumulare. Poi query del Graal sopra. CAUTELA: pochi dati, mente fredda.

## PROSSIMI PASSI (in ordine)
1. Verificare che i 3 campi nuovi (range_pos, drift_slope, seed_score) si SCRIVANO nel canvas (query sopra).
2. Accumulare trade (maschi E femmine) col "prima" completo. Poi query del Graal: il prima distingue M da F?
   In particolare: femmine = range_pos alto + slope negativo (picco morto)? Maschi = slope positivo (viva)?
3. SPIA FRENO: al prossimo loss-colata leggere log M2 riga FRENO_SPIA → perché il freno non castra. Fix sul dato
   (probabile: reset _trade_peak_pnl all'apertura del trade).
4. SE la traccia del "prima del seme" è confermata sui dati → ribilanciare i pesi del SeedScorer (oggi range_pos
   pesa 0.25 premiando il picco; slope solo 0.10). MAI a intuito, sul dato, e di quanto lo dicono i dati.
5. LASCIAR GIRARE STABILE tra un'analisi e l'altra. Niente deploy a raffica (ogni riavvio azzera il warmup/dati).

## DETTAGLI TECNICI UTILI
- FEE: TRADE_SIZE_USD=1000, LEVERAGE=5, FEE=$2.00/trade. Mai citare numeri diversi senza leggerli dal codice.
- SeedScorer (classe ~riga 2286): soglia SEED_ENTRY_THRESHOLD=0.45 (riga 75). 7 feature, pesi: range_pos 0.25,
  comp 0.15, drift_persist 0.20, flip 0.15, vol 0.10, slope 0.10, dur 0.05. range_pos = prezzo nel range 20-tick.
  drift_slope = dm5-dm15 (>0 accelera). Chiamato nel flusso entry a ~riga 10459.
- canvas_snapshots: colonne id, ts, evento, trade_id, fingerprint, sensori_json, capsule_voto, sc_decision,
  outcome, pnl_netto, durata_s, reason, note. fingerprint = momentum|volatility|trend|regime.
- Hook canvas: P2 ~riga 10375, P1 ~riga 11146. Entrambi ANTICIPATI (prima dei veti). Entrambi ora calcolano
  seed prima di observe_entry per avere range_pos/drift_slope nel verbale.
- File doppione nel repo: `OVERTOP_BASSANO(1)_V16_PRODUCTION.py` — copia vagante, IGNORARE/eventualmente rimuovere.

## NOTA UMANA
Roberto: occhio fisico fortissimo. Il 2giu ha guidato ogni passo: "magari vedi già qualcosa" (ha smascherato il
canvas muto), "era un virus messa com'era" (la capsula schiacciata), "una cosa che non gira e tutto è compromesso",
"il gioco è tutto lì" (vedere prima del trade se M o F), "ti manca un elemento oggettivo fondamentale, altrimenti
domani sei allo stesso punto" (ha imposto di aggiungere range_pos/drift_slope PRIMA della notte). Pensa per RECUPERO
non aggiunta (background compro oro): trova l'informazione sprecata che il sistema produce e non registra. NESSUN
soldo vero finché non dimostrato (PAPER SEMPRE). Emozionato dopo 15 mesi sul Graal → Claude deve tenere la lucidità
fredda, mai dargli ragione per piacere, sempre sui dati.
