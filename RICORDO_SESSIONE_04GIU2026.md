# STATO OVERTOP V16 — 4 giugno 2026
## Caricare all'inizio della prossima chat. Sostituisce TUTTI gli stati precedenti per il trading.

> Claude: leggi PRIMA di toccare codice. Stato reale e verificato sui dati al 4 giugno.
> REGOLA #0: dati reali prima di modificare. File SEMPRE come download (mai incollare a pezzi/txt).
> Test runtime (AST + py_compile + import con stub) prima di consegnare. UNA cosa per volta.
> Roberto: analista NON programmatore, decide cosa/perché, Claude esegue e spiega in italiano
> (comportamento, non sintassi). Vuole SINCERITÀ TOTALE, nessuna bugia, anche scomoda.
> "Non dire mai quando si lavora e quando no — gestione mia."

---

## ⚠️ FILE FINALI DA AVERE TUTTI E TRE DEPLOYATI INSIEME (4giu)
1. **Bot: `OVERTOP_BASSANO_V16_PRODUCTION.py` → MD5 `8381e804...`**
   (= 8902f8b7 + valutazioni canvas DISATTIVATE). riga1 = `#!/usr/bin/env python3`
2. **Capsula: `capsula_canvas.py` → MD5 `573aa3df...`** (WAL + accetta nascita)
3. **App/dashboard: `app.py` → MD5 `41d43a5b...`** (pannello SEME GATE live)

DB: `/var/data/trading_data.db` — PAPER — repo ZanRoberto/Tecnaria_V2 — Procfile: `web: python app.py`

### CATENA DEPLOY DELLA SESSIONE (per capire la storia)
- 80f340d4 (base 2giu) → a08157f9 (+SEME_GATE 0.60) → 8902f8b7 (+streak_4 OFF) →
  **8381e804 (+valutazioni canvas OFF) ← QUESTO È IL BOT FINALE**
- capsula: ab1d0981 (base) → c24e2c90 (+nascita) → **573aa3df (+WAL, FINALE)**
- app.py: e57a6729 (base) → **41d43a5b (+pannello SEME GATE, FINALE)**

### STATO DEPLOY AL MOMENTO DEL CAMBIO CHAT
- Sul container girava ancora il BOT `8902f8b7` (valutazioni ANCORA attive).
- VERIFICARE SUBITO: `md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py` deve dare `8381e804`.
  Se dà `8902f8b7` → il bot finale NON è ancora deployato, deployarlo.
- Capsula 573aa3df: confermata deployata e funzionante (ha scritto la prima EXIT col seme).
- app.py 41d43a5b: confermato deployato (pannello visibile).

---

## LA SCOPERTA: IL CROMOSOMA-SEME (3giu, confermato dal vivo 4giu)
Intuizione di Roberto, confermata sui dati: ogni trade nasce "femmina" (debole, in perdita); se ha
forza DIVENTA maschio e vince. Il "sesso" è già nel SEME, PRIMA del trade.
DIMOSTRATO su 44 trade (1-2giu):
- Maschi (WIN): seme medio 0.62, n=20, +42.02$
- Femmine (LOSS): seme medio 0.49, n=24, -105.39$
- Netto -63$. IL BOT SA GUADAGNARE (+42 maschi); le femmine cancellano tutto (-105).
- "seme" = (seed_traj[0]+seed_traj[4])/2, da campo._seed_history[-5:]. In `trades` sta in data_json.seed_traj.

SIMULAZIONE per soglia: 0.50→-2.44 | 0.55→+0.23 | **0.60→+6.94 (PICCO, 15/15 maschi tenuti)** | 0.65→+0.50.
→ SEME_GATE a 0.60 (parametrico via attributo SEME_GATE_SOGLIA). In _open_shadow_position ~riga 11700.

### ⚠️ IL CROMOSOMA NON È PERFETTO — VERITÀ DA NON NASCONDERE A ROBERTO
Filtro PROBABILISTICO ~80-90%, NON 100%. Prove:
- 1 femmina con seme 0.85 morta lo stesso (44 trade).
- 4giu trade 09:35: seme medio 0.70 (alto, da maschio) → HA PERSO (-2.62, femmina). Il gate 0.60
  l'avrebbe fatto passare. È il caso-limite reale.
- Roberto tende a volerlo "perfetto / nulla più passa come femmina". NON assecondare: dire la verità.
  Il valore vero è: ribalta da -63 a positivo, NON azzera le femmine.
Questi casi-limite (femmina con seme alto) sono ORO: servono a trovare il SECONDO elemento (vedi sotto).

---

## 🐛 BUG GROSSO RISOLTO OGGI (4giu): "database is locked"
SINTOMO: i trade nuovi NON comparivano nel pannello SEME GATE. Roberto ha notato a occhio nudo che
un secondo maschio (11:21) non veniva contato.
DIAGNOSI (verificata): observe_exit falliva con `sqlite3.OperationalError: database is locked`,
ingoiato da un except cieco. CAUSA: il canvas scriveva ~60.000 ENTRY_VALUTAZIONE/giorno (una per tick)
→ DB in contesa continua → le EXIT (i dati VERI) fallivano a intermittenza.
Le EXIT erano ferme al 20 maggio per questo; le ENTRY_VALUTAZIONE invece scrivevano (erano loro a lockare).

CURA A (tampone): capsula `573aa3df` — _conn ora con `PRAGMA journal_mode=WAL` + `busy_timeout=30000`.
CURA B (vera): bot `8381e804` — DISATTIVATE le 2 chiamate `self.canvas.observe_entry` (righe 10400 e
11170). Il canvas ora scrive SOLO i trade veri (observe_exit), NON le valutazioni-rumore.
_canvas_tid resta generato per agganciare nascita→esito nelle EXIT.
NOTA: con valutazioni OFF si perde la tabella delle 60k valutazioni (serviva per studiare la
distribuzione del seed_score, GIÀ FATTO: sano, picco 0.40-0.45). Da ora servono solo i trade veri.

### PROBLEMA CONTATORE / PANNELLO — DECISIONE PRESA MA NON ANCORA IMPLEMENTATA
Il pannello SEME GATE legge da `canvas_snapshots` (che PERDE EXIT per il lock). Roberto giustamente
non si fida di un contatore che perde pezzi ("perdo la bussola, la verità").
DECISIONE CONCORDATA (DA FARE PROSSIMA CHAT): rifare la rotta `/seme_gate` in app.py perché legga dalla
tabella **`trades`** (event_type='M2_EXIT', che NON perde MAI nessun trade) e calcoli il seme dal
**data_json.seed_traj** (VERIFICATO presente in ogni trade: es. `[0.7217,...,0.6692]`).
Così il contatore diventa COMPLETO (nessun trade perso) e mostra anche i casi-limite (femmine con seme
alto) invece di nasconderli. Questo RENDE IRRILEVANTE il lock per il pannello.
Formula seme nel pannello: (seed_traj[0]+seed_traj[4])/2 ; MASCHIO se pnl>0, FEMMINA se pnl<=0 ;
verdetto gate: PASSA se seme>=0.60, BLOCCA se <0.60. Evidenziare gli ERRORI (seme alto+perso / basso+vinto).

---

## STATO MERCATO (4giu): BTC crollato a ~63k (da 73k del 30mag). Bot prevalentemente LONG → in
TRENDING_BEAR prende mazzate LONG. SHORT via drift agisce solo in RANGING (0 win su 11, -26.40$ →
NON estendere SHORT senza prove). Col crollo nascono molte femmine (semi bassi) → il gate ne blocca tante.

## VERIFICA DA FARE SUI DATI NUOVI (post deploy 8381e804, dati "sobri" = stessa versione)
ATTENZIONE: i trade misti pre/post-deploy NON sono affidabili (versioni diverse). Contare SOLO i trade
dopo il deploy del bot finale. Query verità (tutti i trade veri col seme, dalla tabella che non perde):
```
sqlite3 /var/data/trading_data.db "SELECT datetime(timestamp), ROUND(pnl,2), json_extract(data_json,'\$.seed_traj'), CASE WHEN pnl>0 THEN 'M' ELSE 'F' END FROM trades WHERE event_type='M2_EXIT' AND timestamp > 'ORA_DEPLOY_8381e804' ORDER BY id DESC;"
```
Domanda che decide la strategia: il seme separa al ~95% (→ CACCIA AL CROMOSOMA: butta gli arzigogoli,
entry solo-seme) o all'~80% (→ resta un freno tra i tanti)? Su 44 trade non si distingue. Serve misurare
sui trade nuovi sobri.

## PROSSIMO CANTIERE (DOPO che il contatore-da-trades è fatto e ci sono dati sobri)
1. **Contatore da `trades`** (vedi sopra) — PRIMA COSA da fare prossima chat.
2. **Cromosoma-direzione / SECONDO elemento** (intuizione Roberto): il seme alto+perso (09:35) si spiega
   con un secondo elemento che "mette sotto verifica" il primo nei casi-limite. seed_dir NON è (già
   smentito: M -0.0095 vs F -0.0139, sovrapposti). Candidato: drift_slope, o range_pos RIPARATO.
   METODO: NON indovinarlo. Isolare le femmine-con-seme-alto nei dati nuovi e vedere cos'hanno in comune.
3. **range_pos è ROTTO** (saturo 0/1, riga 2326: usa prices[-1] come estremo della sua stessa finestra).
   Ripararlo (range ampio 50-100 tick) SPOSTA la taratura del seed (peso 0.25 riga 2378) → ri-misurare
   gate. Farlo solo se serve il cromosoma-direzione.

## CADAVERI / DISATTIVATI (NON riattivare)
- streak_4 (blocco dopo 4 loss): DISATTIVATO (soglia 4→999, riga ~5984). Era zavorra: bloccava la
  raccolta dei loss che servono a misurare il cromosoma. Meccanismo intatto, reversibile.
- observe_entry/valutazioni canvas: DISATTIVATE (righe 10400, 11170) — lockavano il DB.
- RSI fisso 50, MACD fisso 0 (disarmati 23mag). ZONA_MORTA (morta 29mag). range_pos saturo.

## VISIONE DI FONDO (discussa, NON da implementare ora)
- Il "cervello" è Roberto (le intuizioni vengono da lui). Claude/DeepSeek sono strumenti che amplificano,
  NON sostituiscono. Claude NON vive nel bot, NON ha memoria tra chat, NON è il Canvas.
- Idea futura: DeepSeek "fabbricante di capsule" (vede distribuzioni, genera regole che girano nel bot a
  costo zero, con quarantena + Roberto garante finale). NON Claude/DeepSeek nel loop a ogni tick
  (latenza+costo+inaffidabilità sul singolo dato).
- Agenti autonomi / `/goal`: rimandati — pericolosi su sistema non ancora funzionante e che Roberto non
  legge a livello codice. Prima far funzionare il bot col cervello di Roberto, poi automatizzare.

## METODO CHE FUNZIONA
Verifica sui dati prima di costruire. Stanotte+oggi: cromosoma-seme CONFERMATO; range_pos, seed_dir,
"dopo-win", "SHORT in trend", "Claude=Canvas" tutti SMENTITI/frenati. Roberto emotivamente investito,
a volte vuole conclusioni perfette: dargli la verità con onestà, anche scomoda, è ciò che chiede.
Confidenzialità assoluta sul knowhow trading.
