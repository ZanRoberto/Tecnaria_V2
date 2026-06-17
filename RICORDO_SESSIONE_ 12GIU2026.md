# STATO OVERTOP V16 — 17 giugno 2026 (v5)
## Caricare all'inizio della prossima chat. Sostituisce TUTTI gli stati precedenti.

> Claude: leggi PRIMA di toccare codice. REGOLA #0: dati reali prima di modificare.
> File SEMPRE come download. Test runtime (AST + py_compile) prima di consegnare.
> UNA cosa per volta. **Letture codice MIRATE** (range stretto, ~50 righe max).
> Roberto: analista NON programmatore, decide cosa/perché, Claude esegue come ingegnere
> capo e VERIFICA TUTTO, anche le proprie certezze (spesso contengono errori).
> Vuole SINCERITÀ TOTALE, nessuna piaggeria. Mai dire "quando si lavora o no" — gestione
> di Roberto. Mai narrazione: solo numeri dai dati.
> Claude NON accede al server: Roberto incolla output da Web Shell Render.
> Orari log UTC; Italia = UTC+2. Confidenzialità assoluta sul knowhow trading.
> Roberto lavora da cellulare: query SU UNA RIGA SOLA e corte; niente nano (trappola).
> Se la shell resta col `>` (parentesi aperta): scrivere `;` + Invio o Ctrl+C.

---

## 🔖 BUILD ATTUALE — DA VERIFICARE A INIZIO SESSIONE
- **Ultimo file consegnato 17giu: md5 `1a2e69687571d54cd11ef6541eec5c15`**
  (contiene la CORSIA PRIVATA DEL MASCHIO — vedi sotto)
- File precedente della giornata: `303b00823faa...` (girava in produzione fino al
  pomeriggio 17giu, conteneva PRESA_SECCA + fix ANTIPRECIPIZIO)
- File: `/opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py`
- DB: `/var/data/trading_data.db` — PAPER — Procfile: `web: python app.py`
- Live: tecnaria-v2.onrender.com — Repo: ZanRoberto/Tecnaria_V2
- **PRIMA COSA DELLA SESSIONE:** `md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py`
  e `ps -o lstart= -p 1` (ora avvio processo). I container Render cambiano spesso
  (oggi: nxv4x → bcflq → lrsrv → 9975c...): verificare sempre cosa gira DAVVERO.
- **Trappola dimostrata oggi:** la dashboard mostra trade VECCHI nello storico. Giudicare
  solo i trade nati DOPO l'avvio del processo attuale (confronto orari). Più volte oggi
  un trade "colpevole" si è rivelato essere di un container precedente.

---

## 🎯 LA SCOPERTA CHE CAMBIA TUTTO (consolidata 12-17giu)

**Maschi, femmine, dopate e trans sono INDISTINGUIBILI all'ingresso e nei primi secondi.**
Dimostrato sui dati: seme alto ce l'hanno sia maschi che capre (anzi le capre a volte di più).
Si separano SOLO DOPO, nei secondi successivi: il maschio sale e TIENE, la capra crolla
o resta piatta. Quindi:
- **Inutile cercare di distinguerli all'ingresso.** Vietato perderci tempo.
- **Il vero lavoro è il RITARDO/OSSERVAZIONE:** tenere il segnale FUORI dal trade
  (zero esposizione, zero fee) abbastanza da farlo manifestare, e far entrare solo chi
  si dichiara maschio.
- **Le mine d'uscita (ANTIPRECIPIZIO, TRANELLO, CASSA_GRASSO) sono ammissione di
  fallimento:** se una capra arriva all'uscita, vuol dire che è ENTRATA e non doveva.
  Il lavoro è tutto a MONTE, nell'osservazione.

**Principio di Roberto (17giu), tradotto in architettura:**
> "Il maschio deve avere il suo occhio, la sua telecamera privata, e scatta nel momento
> in cui ha le carte in regola. Gli altri vanno in purgatorio, ma non lui. Altrimenti è
> tutto morto, non passa più nulla."

Cioè: NON un'attesa fissa per tutti (uccide i maschi veloci che a 5s sono già pronti e
muoiono se aspetti). DUE CORSIE: il maschio entra SUBITO appena è pronto; gli incerti
restano nel purgatorio del ritardo; le capre non entrano mai.

---

## 🔧 LAVORO FATTO OGGI (17giu) — in ordine

### 1. PRESA SECCA (mina d'uscita, già in produzione build 303b...)
"Porta a casa il grasso." Ad ogni tick, a qualunque secondo: se il PnL **NETTO**
(lordo meno fee 2$) raggiunge la soglia → chiude e incassa SUBITO. Non importa
maschio/femmina/trans. Non aspetta cedimenti.
- Punto codice: `_evaluate_shadow_exit`, subito dopo calcolo `_eta` (~r.13763)
- `current_pnl` è LORDO (def. r.343 funzione: `_sdelta * (5000.0/price_entry)`).
  La mina sottrae la fee: `_presa_netto = current_pnl - (TRADE_SIZE*LEVERAGE*FEE_PCT*2)`
- ENV: `PRESA_SECCA_USD` (default 1.0 = +1$ netto), `PRESA_SECCA_OFF`
- Tabella: `presa_secca_tagli` (si crea al primo scatto)
- **PRIMO SCATTO CONFERMATO 17giu** sul maschio delle 10:09: +1.02 netto, motivo PRE/PRESA_SECCA.
- **LEZIONE CHIAVE:** il campo `pnl_10s` mostrato in dashboard è LORDO. Un trans con
  "pnl_10s +1.34" era −0.66 NETTO (è morto lì). Da ora ragionare SEMPRE in netto.

### 2. Fix ANTIPRECIPIZIO (già in produzione build 303b...)
**Tolta la condizione `_sta_scendendo`.** Prima ANTIPRECIPIZIO tagliava solo se beccava
il trans nel tick esatto di discesa; i micro-rimbalzi (un centesimo su) lo salvavano e
agonizzava fino a TRANELLO a 47s. CASO DIMOSTRATO: trans RANGE_STRONG picco 0.0,
TRENDING_BEAR, colato a gradini fino a −3.53 in 47s.
- Ora taglia con: età ≥8s + in perdita + mai-verde (`max_profit < ANTIPREC_PEAK_MIN`).
  Non aspetta più il tick perfetto.
- Il maschio lento NON rientra: lui un peak verde lo fa, quindi `_mai_verde` è falso.
- Reversibile: `ANTIPREC_RICHIEDI_DISCESA=true` ripristina il vecchio comportamento.
- Punto: ~r.13716

### 3. CORSIA PRIVATA DEL MASCHIO (NUOVO — file `1a2e6968...`, DA DEPLOYARE)
La telecamera privata del maschio. Durante l'attesa, ad OGNI secondo, se il grasso
**NETTO** (`_pos_usd − fee 2$`) supera la soglia → il trade ENTRA SUBITO, saltando
TUTTO il purgatorio (ritardo, ANTICORDA, GATE PEAK, ecc.).
- Punto codice: dentro `_evaluate_shadow_entry`, blocco ritardo, ~r.12579-12620
- I guardiani del purgatorio (r.12624-12900) sono stati avvolti in
  `if not _maschio_entra_ora:` (reindentazione automatica via script, testata).
  L'apertura del trade (`self._shadow = {`, ~r.12903) è FUORI da quel blocco → il
  maschio confermato ci arriva diretto.
- `_maschio_entra_ora` definito a r.12579 (indent 20), guardia a r.12625 (indent 20):
  stesso livello, nessun rischio NameError.
- Aggiorna `ritardo_agganci SET entrato=1` e stat `maschi_corsia`.
- ENV: `MASCHIO_CORSIA_USD` (default 1.0 = grasso netto per scattare),
  `MASCHIO_CORSIA_OFF` (spegne).
- Log allo scatto: 🐺 "CORSIA MASCHIO: grasso netto +X$ — carte in regola, ENTRA SUBITO".
- **NON ancora deployato né visto vivo.** Prova vera = quando mercato si muove e nasce
  un maschio: cercare il log 🐺 e verificare che entri presto + presa_secca lo spolpa.

---

## 🐛 IL GRANDE BUCO TROVATO OGGI: INTERRUTTORI "OSSERVA" CHE NON BLOCCANO

Pattern ricorrente e velenoso: **guardiani potenti messi in modalità OSSERVA invece che
DECIDE.** Vedono la merda, la loggano, e la lasciano passare lo stesso.

1. **ANTICORDA aveva DUE interruttori in conflitto:** `ANTICORDA_ZONA=1.2` (acceso) MA
   `ANTICORDA_OFF=true` (che lo spegneva di nascosto). Roberto credeva acceso, era spento.
   **RISOLTO 17giu:** messo `ANTICORDA_OFF=false`. Verificato `env | grep ANTICORDA`.
2. **GATE PEAK** — verificato `GATE_PEAK_OBSERVER=false` (già decide, OK).
3. **CapsulaRegimeEdge** (file `capsula_regime_edge.py`): in L3 OSSERVA di default,
   ritorna None, non blocca. Si arma con `CAPSULA_REGIME_EDGE_L4=true` (NON impostata →
   sta osservando). **DECISIONE ROBERTO: NON armarla.** Ragiona per categorie larghe di
   mercato (SIDEWAYS/UP/DOWN); bloccherebbe anche i maschi nati nei mercati "merda".
   Il ritardo/corsia-maschio è più fine (giudica l'individuo, non la media). Appartiene
   a "prima della scoperta".
4. **L'ORACOLO PREDITTIVO è scollegato:** `predict_from_signals` (~r.3208) calcola la
   tabella ECONOMIC EDGE (verdetto ENTRA/BLOCCA/NEUTRO per ogni mercato) ma **non è MAI
   chiamato dalla logica d'ingresso** — solo per la dashboard. Conoscenza calcolata e
   ignorata. NON collegato per ora (stessa ragione della regime edge: ragiona per medie).

**Insegnamento:** prima di dare la colpa al codice, controllare SEMPRE le ENV con
`env | grep`. Più volte oggi il "buco nel codice" era un interruttore spento/osserva.

---

## 🎯 STATO ENV (Render, 17giu — verificare a inizio sessione)
```
ANTICORDA_ZONA=1.2
ANTICORDA_OFF=false          ← CORRETTO oggi (era true, spegneva il filtro)
RITARDO_INGRESSO_SEC=4        ← ancora 4 (la dashboard mostra "4")
RITARDO_EXTRA_SEC=2
RITARDO_RESET_GAP=3
GATE_PEAK_OBSERVER=false      ← decide (OK)
GATE_PEAK_USD=1.0
GATE_PEAK_FINESTRA_SEC=15
RIPIEG_PRE_USD=0.01           ← praticamente spento (1 centesimo)
SALITA_MIN_USD=1.0            ← aggiunto oggi (guardiano trans piatti)
SALITA_DOPO_SEC=12            ← aggiunto oggi
CAP_MAT_L4_DECIDE=true        ← capsula matrigna (altra cosa)
PRESA_SECCA_USD=1.0 (default se non impostata)
MASCHIO_CORSIA_USD=1.0 (NUOVO — default; impostare quando si deploya 1a2e6968)
```
**NOTA su SALITA vs RITARDO:** oggi scoperto che SALITA_DOPO_SEC=12 NON basta da solo,
perché ANTICORDA/4+2 decide a 4-6s e fa entrare PRIMA che il guardiano SALITA (a 12s)
intervenga. Si pestano i piedi. La corsia-maschio (file nuovo) risolve a monte: il maschio
entra appena pronto, gli incerti restano nel ritardo. Da valutare se serve anche alzare
`RITARDO_INGRESSO_SEC` a 10-12 per dare al purgatorio il tempo di smascherare gli incerti.

---

## 📊 DATI CHIAVE DI OGGI (numeri, non narrazione)

### La merda recente è quasi tutta TRANS_PIATTI (picco 0.0)
Su 43 perdenti recenti: 42 erano TRANS_PIATTI (picco 0.00), 1 TRANS_SCATTO, 0 FEMMINA_DOPATA.
Le dopate "che salgono come maschi e poi crollano" quasi NON esistono nei dati recenti.
Il 98% del problema sono trade piatti che non salgono mai → facili da bloccare con soglia.

### Separazione netta a 10s (dimostrata su trade 14-16giu)
- A 10s i perdenti (42 su 42) erano TUTTI sotto +0.6$ lordo. ZERO trans a +1$ a 10s.
- I maschi (pochi: 2-9 nel campione) erano sopra +1$ a 10s.
- **MA:** campione maschi piccolo. E un maschio delle 13:31 (17giu) aveva pnl_10s +1.96,
  pnl_20s +2.79, picco 0.83 → maschio vero, profittevole.

### Drift NON è il discriminante
Maschi drift medio 0.63, femmine 0.51. Troppo vicini, si sovrappongono. Il drift NON
separa mercato-vero da pattume. Lasciato perdere come filtro.

### t_peak: i maschi GROSSI nascono LENTI
Maschi piccoli/veloci: picco a 4-8s. Maschi grossi: picco a 16-20s (uno visto +4.71 a 16.6s,
+2.22 a un trade bloccato da GATE PEAK perché fuori finestra 15s).
**TENSIONE IRRISOLTA:** aspettare i maschi lenti (>15s) = stare esposti troppo a lungo =
mangiarsi le capre. Roberto: "i grossi sono pochi e rari, non puoi stare col culo fuori
16-20s". Per ora si sacrificano i maschi lenti. Recupero futuro = GATE PEAK ASIMMETRICO
(intercettare il maschio lento DENTRO i 15s da una firma di salita sana), ma è il passo dopo.

### ECONOMIC EDGE (la mappa, calcolata ma non usata per decidere)
Dalla dashboard, mercati per redditività (n = casi):
- ✅ RANGING LONG DEBOLE: hit 55%, +2.76$, n=157.993 (il pane)
- ✅ TRENDING_BEAR LONG DEBOLE: hit 55%, +2.27$, n=2.322
- 🟡 EXPLOSIVE/TRENDING_BULL LONG: ~35%, marginali
- 🔴 RANGING SHORT: 25%, −0.49$, n=11.996
- 🔴 TRENDING_BEAR SHORT: 10%, −2.13$, n=54 (veleno)

---

## 🔭 SCOPERTE STRUTTURALI DI FONDO (consolidate)

### Il problema vero NON è il filtraggio, è il TERRENO + il TEMPO
1. Fee 2$/trade su 60s è altissima rispetto al segnale su BTC fermo. BTC piatto è la
   prova più dura (sfida sui centesimi). Se il bot regge qui, su coppie più volatili
   (Solana) la fee fissa pesa meno → dovrebbe premiare di più. MA: chiudere i buchi
   PRIMA di passare a coppie volatili, sennò i buchi diventano voragini.
2. Tutta la fisica (seme, campo, ritardo, mine) è AGNOSTICA alla coppia. Il PnL usa
   `delta * (5000/price)` → proporzionale, si adatta da solo. Le SOGLIE in $ vanno
   ricalibrate per ogni coppia.

### MULTI-COPPIA / SOLANA (pronto a metà, NON attivo)
- `SYMBOL = "BTCUSDC"` è **hardcoded a r.159** — NON è una ENV. Per Solana serve UNA
  modifica codice: `SYMBOL = os.environ.get("SYMBOL", "BTCUSDC")`. Poi ENV `SYMBOL=SOLUSDC`.
- Calcolo PnL già agnostico. Soglie ($) da ricalibrare osservando SOL 1-2 giorni.
- **DECISIONE:** finire la verifica BTC con corsia-maschio PRIMA di toccare coppia.

---

## 📊 QUERY PRONTE (cellulare: UNA RIGA, corte)

### Verifica build in produzione
```bash
md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py
```

### Ora avvio processo (per distinguere trade vecchi da nuovi)
```bash
ps -o lstart= -p 1
```

### ENV chiave
```bash
env | grep -E "ANTICORDA|RITARDO|GATE_PEAK|SALITA|PRESA|MASCHIO|RIPIEG"
```

### Trade ultimi 15 min (solo roba del processo attuale)
```bash
sqlite3 /var/data/trading_data.db "SELECT datetime(timestamp,'+2 hours') AS ora, ROUND(pnl,2) AS netto, ROUND(CAST(json_extract(data_json,'\$.peak_pnl') AS REAL),2) AS picco, ROUND(CAST(json_extract(data_json,'\$.duration') AS REAL),0) AS durata, reason FROM trades WHERE event_type='M2_EXIT' AND timestamp>=datetime('now','-15 minutes') ORDER BY id DESC;" -header -column
```

### Contenuto grezzo ultimo trade di un tipo (per autopsia)
```bash
sqlite3 /var/data/trading_data.db "SELECT data_json FROM trades WHERE event_type='M2_EXIT' AND reason='ANTIPRECIPIZIO' ORDER BY id DESC LIMIT 1;"
```

### Cosa hanno bloccato le mine, ultima ora
```bash
sqlite3 /var/data/trading_data.db "SELECT block_reason, COUNT(*) AS n FROM phantom_forensic WHERE ts_entry > strftime('%s','now','-1 hour') GROUP BY block_reason ORDER BY n DESC;" -header -column
```

### Prese secche portate a casa
```bash
sqlite3 /var/data/trading_data.db "SELECT * FROM presa_secca_tagli ORDER BY id DESC LIMIT 10;" -header -column
```

### Maschi entrati in corsia privata (dopo deploy 1a2e6968)
Cercare nei log la scritta 🐺 "CORSIA MASCHIO". (Aggiungere counter dedicato se serve.)

---

## ✅ PROSSIMI PASSI (in ordine)
1. **Deployare il file `1a2e6968...`** (corsia maschio) e verificare md5 sul container.
2. **Lasciar scorrere** e vedere col mercato vivo: il maschio entra presto in corsia?
   Le capre restano fuori? La presa secca spolpa? Query: ultimi 15 min + log 🐺.
3. Se le capre incerte (non-piatte, non-maschio-chiaro) entrano ancora dal purgatorio
   e muoiono: valutare alzare `RITARDO_INGRESSO_SEC` a 10-12 SOLO per il purgatorio
   (il maschio già bypassa via corsia privata, quindi non lo penalizza).
4. **DOPO** aver certificato i maschi su BTC: studiare le dopate/femmine col grasso a 10s
   (perché partono sotto e poi trovano grasso). Solo col metro del maschio certificato.
5. GATE PEAK ASIMMETRICO (intercettare maschi lenti dentro i 15s) — passo successivo.
6. Solana — solo dopo che BTC è pulito (1 riga codice su SYMBOL + ricalibro soglie).

---

## 📜 STORICO INVARIATO (dal v4, valido)

### CADAVERI (NON riattivare)
streak_4, observe_entry, Narratore, RSI/MACD fissi, ZONA_MORTA, range_pos saturo,
TRANELLO_FEE_ZERO, gate antibolla "3 morsi", quarantena capsule (rimossa, mai menzionare).

### PROIBIZIONI PERMANENTI
- MAI distinguere maschi/femmine ALL'INGRESSO (firma ambigua, etichette retrospettive).
- MAI proporre SHORT (LONG-only per scelta strutturale; zero-trade in fase ribassista è
  comportamento CORRETTO, non un problema).
- MAI veti con finestre temporali/conteggi (REGOLA-22MAG: un LOSS marca firma PERICOLOSA
  finché un WIN stessa firma la pulisce).
- MAI citare parametri/fee/costanti senza leggerli dal codice.
- MAI proporre azioni sul vivo senza simulazione/verifica sui dati storici.
- MAI pacchettare più modifiche insieme (una modifica = un test = un deploy).

### METODO (lezioni consolidate)
- Letture codice MIRATE (~50 righe). Letture larghe saturano la chat.
- Le narrazioni delle istanze precedenti sono opinioni, NON fatti. Verificare con query.
- Una mina muta è ingiudicabile: accendere telecamera prima di decidere.
- MD5 sul container = unica verità del deploy. Build nei trade = unica verità del giudizio.
- PRIMA di dare la colpa al codice: `env | grep` (oggi 2 buchi su 2 erano ENV osserva/off).
- DISTRIBUZIONI, non MEDIE. Far calcolare al DB, non a occhio.
- Modifiche grosse al file (reindentazione di blocchi lunghi): farle via SCRIPT Python,
  non a mano, e testare AST+py_compile prima di consegnare (fatto oggi per la corsia maschio).

### INFRASTRUTTURA (valido)
DB autopulitore, websocket fix (ping_interval=20/ping_timeout=10), Veritas peso 16%,
fee $2/trade, BTC ~60-66k. Telecamere MINA_ in phantom_forensic.

### VISIONE DI FONDO (NON da implementare)
Il cervello è Roberto; Claude/DeepSeek amplificano. Claude NON vive nel bot, NON ha
memoria tra chat — questo documento È la memoria. Agenti autonomi/`/goal`: rimandati.
