# STATO OVERTOP V16 — 31 maggio 2026
## Da caricare all'inizio della prossima chat. Sostituisce il RICORDO_30mag per la parte trading.

> Claude: leggi questo PRIMA di toccare codice. È lo stato reale e VERIFICATO sui dati al 31 maggio.
> REGOLA #0: leggere i dati reali (DB/log/endpoint) prima di proporre. File sempre come download, mai da incollare.
> PRINCIPIO CAPSULA (Roberto, sacro): "Dove uno mette una soglia rigida, io penso a una capsula adattogena."

---

## IL FILE CHE GIRA / DA DEPLOYARE
- **MD5 NUOVO da deployare: `40d40b5a77c9bdb6c452a9103a36f8df`** — 14.695 righe — riga 1 `#!/usr/bin/env python3`
- Nome: `OVERTOP_BASSANO_V16_PRODUCTION.py` — Procfile `web: python app.py` — live tecnaria-v2.onrender.com
- DB: `/var/data/trading_data.db` (227 MB) — PAPER — capitale 10.000$ — env `DB_PATH=/var/data/...`
- Catena MD5: `527a63df` (30mag, fix uscita, girava) → `c50b806f` (31mag, +porta SHORT in TRENDING_BEAR,
  DEPLOYATO=verde) → **`40d40b5a` (31mag, +fix tracciatura direction/regime nel verbale, DA DEPLOYARE)**
- Verifica fatta: 527a63df era IDENTICO su container e repo. Contiene matrigna+tsunami+fase+fix uscita.
- NB: il file export "20606175" (27mag, 14.341 righe) è uno stato VECCHIO. Non è riferimento. Ignorarlo.
- ⚠️ AL DEPLOY: verificare md5sum container = `40d40b5a77c9bdb6c452a9103a36f8df` + head -1.
  Poi verificare il fix tracciatura: `sqlite3 /var/data/trading_data.db "SELECT datetime(ts,'unixepoch'),
  fingerprint FROM canvas_snapshots ORDER BY ts DESC LIMIT 5;"` → la fingerprint deve mostrare la DIREZIONE
  (es. DEBOLE|BASSA|SIDEWAYS|LONG), non più "?". Se compare la direzione → fix vivo.

## IL FIX TRACCIATURA (31mag, 2° intervento) — dentro 40d40b5a
**FIRMA COMPLETA NELLA SCATOLA NERA** (`_verbale` ~riga 10251 + 10295).
- Bug trovato sui dati: `canvas_snapshots.fingerprint` usciva `DEBOLE|BASSA|SIDEWAYS|?` — mancavano DIRECTION
  e REGIME. Causa: il dict `_verbale` passato a `canvas.observe_entry` NON aveva la chiave `direction` (mai
  inserita), e `regime` partiva None e veniva popolato DOPO la chiamata a observe_entry (righe 10762/11151).
- Fix: alla nascita del `_verbale` aggiunto `"direction": self.campo._direction` e `"regime":
  self._regime_current` (stato sempre disponibile, non variabili locali non ancora calcolate). Copre P1 e P2.
- IMPORTANTE: il bot ha SEMPRE saputo la direzione e ci ha SEMPRE operato giusto. Il buco era SOLO nel
  registro canvas (la documentazione, non la decisione). Ma è grave lo stesso: le capsule imparano dai dati
  registrati; una firma senza direzione è mezza firma → avvelenerebbe ogni capsula costruita sopra.
- I dati canvas VECCHI restano monchi (persi). Da 40d40b5a in poi la firma è intera.
- Test: AST + py_compile + import-runtime OK.
- 2° BUCO TRACCIATURA ANCORA APERTO (passo futuro): manca la CURVA DI NASCITA del trade (PnL nei primi
  N secondi di vita). canvas registra solo entry-snapshot + exit. La maturazione precoce (l'intuizione di
  Roberto "quanta forza matura nei primi millisecondi per pagare la fee") NON è tracciata. Va aggiunta come
  evento ENTRY_NASCITA con criterio (attenzione frequenza scrittura DB — c'è già stato un disco pieno: bug N²).

## LA MODIFICA DI OGGI (31mag) — dentro c50b806f — UNA SOLA
**PORTA FISICA ALLO SHORT IN TRENDING_BEAR** (`_auto_detect_direction`, ~riga 9402-9426).
- Prima: il campo poteva flippare a SHORT solo in EXPLOSIVE (riga 9376) e RANGING (9499/9548/9570).
  In TRENDING_BEAR — il regime dove lo short ha più edge — NON c'era nessuna porta: restava sempre LONG.
- Ora: `if regime==TRENDING_BEAR and direction==LONG and bearish_energy>=2 and cooldown_ok → FLIP a SHORT`.
  NESSUNA soglia di drift inventata: riusa `bearish_energy` (misura fisica composita già esistente:
  momentum veloce giù, MACD giù, decel bassa, drift giù). Streak NON richiesto (il regime BEAR è già conferma).
- Log nuovo: `🔄 FLIP → SHORT in TRENDING_BEAR (bearish_energy=.. drift=..)`.
- Test: AST + py_compile + IMPORT RUNTIME (con stub websocket) tutti OK. Nessun crash a livello modulo.
- QUESTA È SOLO LA GANASCIA 1 (la porta). L'antiaerea (ganascia 2) NON è in questo file — è il passo dopo.

---

## LA VERITÀ TROVATA OGGI SULLO SHORT (tutto verificato sui dati reali)

### Il fatto che apre tutto (query `trades`)
- `LONG_SHADOW`: 392 trade, ULTIMO 30mag 15:34. `SHORT_SHADOW`: 11 trade, TUTTI 12-15 maggio, ultimo 15mag 16:34.
- **Lo short non viene eseguito da 16 giorni.** Dal 30mag (fix SHORT_VIA_DRIFT) ZERO short nuovi.

### FALSO POSITIVO smascherato nel RICORDO del 30mag
- Il RICORDO 30mag scriveva: fix SHORT_VIA_DRIFT "Verificato: 11 trade SHORT nel DB (prima ZERO)".
- FALSO: quegli 11 short sono del 12-15 maggio, PRE-fix. Il fix del 30mag non ha mai prodotto un solo flip.
- Errore di metodo: si sono contati gli short TOTALI e scambiati per short NUOVI senza guardare il timestamp.
- "Non flippa mai" (intuizione di Roberto) = CONFERMATO sui dati.

### PERCHÉ lo short in RANGING non parte — 3 MURI (tutti verificati su endpoint /trading/status)
- **A — non flippa:** il drift è (media ultimi 50 − media primi 50)/media, su deque(100). Misura lisciatissima.
  Soglia richiesta −0.02% sostenuta 3 tick consecutivi. Drift reale ~0.0001% (mercato piatto) → mai.
  Prova: `m2_direction=LONG` sempre, `shadow_short_ranging blocked_count=0 simulated_count=0`.
- **B — se flippasse, ribloccato:** capsule VETO_SHORT in RANGING attive — `AUTO_REGIME_TOSSICO_RANGING_SHORT`
  (regime==RANGING+dir==SHORT→blocca), `STATIC_SHORT_DEBOLE_ALTA_SIDEWAYS` (8106 hits), 4× `CTX_TOSSICO_*_SHORT`.
- **C — se passasse, perde:** short RANGING storici (oracolo_snapshot): SHORT|DEBOLE|ALTA|SIDEWAYS WR 7.7% pnl −3.03;
  MEDIO|ALTA|SIDEWAYS WR 6.8% pnl −3.42; FORTE|ALTA|SIDEWAYS WR 11.5% pnl −3.38. Tutti perdenti.

### LA SCOPERTA VERA (perché il fix del 30mag era sbagliato in partenza)
- Il fix SHORT_VIA_DRIFT era agganciato a `regime==RANGING` → cercava lo short PROPRIO dove lo short perde.
- Lo short VINCE in trend ribassista: `SHORT|FORTE|ALTA|DOWN` WR **55%**, `SHORT|MEDIO|ALTA|DOWN` WR 48%.
  MA con `real: 0` → mai eseguito davvero, solo phantom. Non c'è storia REALE su cui una capsula possa imparare.
- Quindi la modifica di oggi sposta la porta nel regime GIUSTO (TRENDING_BEAR), dove la fisica dà profitto.

---

## IL PRINCIPIO DELLA CAPSULA (fissato da Roberto, 31mag) — vale SEMPRE
**La capsula è una TENAGLIA a due ganasce, mai una leva sola:**
1. **AGISCE SUBITO dove la fisica dice profitto** (no warmup-attesa, no quarantena = sarebbe un SARCOFAGO).
2. **SI ASTIENE dove la fisica dice perdita** (senza questa è un COLABRODO).
- La linea profitto/perdita NON la traccia un numero inventato. La tracciano: (a) la FISICA DEL MOMENTO
  (regime, OI short, momentum) per il lato "agisci"; (b) la MEMORIA DELLE FIRME (antiaerea) per il lato "frena".
- **ANTIAEREA (no finestre temporali):** 1 LOSS con firma X → firma marcata PERICOLOSA subito. Resta marcata
  finché un WIN con la STESSA firma la pulisce. Immediato, reversibile. Firma = (regime, direction, momentum,
  volatility, trend, matrimonio).
- **NIENTE QUARANTENA, NIENTE WARMUP CHE ASPETTA.** La capsula nasce armata e agisce dal primo tick.
- Caveat onesto: sulla PRIMA firma mai vista l'antiaerea è vuota (no storia) → agisce solo la ganascia fisica.
  La memoria entra dal secondo colpo. È così che la tenaglia si arma, non è un buco.

## MODELLO TECNICO PER SCRIVERE LA CAPSULA (già studiato)
- `capsula_tsunami_discorde.py` (595 righe) = lo stampo adattogeno. Schema: chiave=firma → accumula WR+PnL reali
  → verdetto su soglie di SENSO COMUNE sul WR (WR_SOGLIA_FORTE=0.33, WR_SOGLIA_DEBOLE=0.20), MAI su valori mercato.
  Metodi: `_refresh_mappa`, `consulta`→(passa,motivo), `observe_outcome`. HA quarantena — da NON replicare (Roberto).
- `capsula_matrigna.py` (850 righe, MD5 58be8c2b) = SuperRisponditrice, genera capsule figlie per firme nuove,
  budget esplorazione (Opzione C). Può essere la via per far nascere le firme short-bear come figlie.
- Le capsule possono ABBASSARE_SOGLIA / favorire (es. `CI_RANGING_EDGE` azione ABBASSA_SOGLIA), non solo bloccare.
  → la capsula short dev'essere PROMOTRICE (abilita dove edge), non l'ennesimo veto.

---

## ANTIAEREA — STATO REALE (verificato nel codice 31mag) — IMPORTANTE
Esistono DUE antiaeree e NON sono la stessa cosa:
- **JSON `capsula_ANTIAEREA_PATTERN_PERSISTENTE_v1` (22mag):** è una SPECIFICA concettuale ("1 loss marca,
  1 win pulisce, niente finestre"). `memoria_eventi.voci=[]` (ZERO attivazioni) e nota "serve un 2° hook
  prima dell'entry". → NON è la versione che lavora. È il progetto da cui è nata quella nel codice.
- **Antiaerea NEL CODICE (29mag, quella VIVA):** classe a ~riga 14557, metodo `_pulisci_blocco_se_win`.
  Fa SOLO la PULIZIA: cancella un blocco dopo `SOGLIA_WIN_PULIZIA = 2` WIN CONSECUTIVI stessa firma
  (NON 1 win come nel JSON — la nota dice "in RANGING 1 win può essere rumore, 2 di fila no"). La firma è
  generica (`mom|vol|tr|reg|dir`), quindi la PULIZIA copre anche short/bear.

### IL BUCO (da chiudere quando ci saranno short reali in bear)
- L'antiaerea viva fa solo METÀ lavoro: sa TOGLIERE un blocco (pulizia), NON sa METTERLO per lo short in bear.
- Chi MARCA dopo il 1° loss? Solo le capsule `CTX_TOSSICO_*` esistenti, tutte su SIDEWAYS/RANGING. Per
  TRENDING_BEAR/SHORT NON c'è nessun marcatore. → se il 1° short in bear perde, NESSUNO marca la firma,
  il 2° short identico riparte senza freno. È il "colabrodo" sullo short.
- DA FARE (passo futuro, NON ora): aggiungere il MARCATORE antiaerea per la firma short-bear (crea il blocco
  dopo il loss), riusando la pulizia già esistente. Decidere con Roberto la taratura: 1 loss/1 win (come JSON)
  o tarature diverse per regime (in BEAR il trend è direzionale, forse 1 win basta vs i 2 del RANGING).
- PERCHÉ NON ORA: il marcatore va costruito sui dati di short reali in bear, che oggi sono ZERO (real=0).
  Scriverlo adesso = progettare sul vuoto = errore "prima i dati". Prima la porta produce short reali, poi si marca.

## STATO FINALE SESSIONE 31mag (verificato sui log live 10:14)
- DEPLOY RIUSCITO: container gira `40d40b5a77c9bdb6c452a9103a36f8df` (verificato md5sum + grep =1 e =1).
  ATTENZIONE: i primi 2 "deploy verdi" di oggi (c50b806f, primo 40d40b5a) NON erano arrivati sul container
  — girava ancora 527a63df. Problema di catena git/Render mai diagnosticato (git log/status non lanciati).
  Il 3° deploy è arrivato davvero. LEZIONE: il "verde" Render NON è prova; solo md5sum sul container lo è.
- BOT VIVO E FUNZIONANTE: log 10:14 mostrano tick processati, EVENT_FUOCO, CAPSULE dep, SC_BLOCCA in tempo
  reale. (Nota: l'endpoint /trading/status mostrava last_tick 04:23 = stato VECCHIO IN CACHE, non bot morto.
  Claude aveva dato un FALSO ALLARME "WebSocket morto" leggendo la cache — poi corretto sui log live.)
- PERCHÉ canvas count=0 negli ultimi minuti: in RANGING il bot BLOCCA prima di arrivare a observe_entry
  (REGIME_TOSSICO_RANGING_LONG, CTX_TOSSICO). Quindi il canvas registra solo i trade che PASSANO il blocco,
  non quelli bloccati. Il fix tracciatura (direction/regime) è probabilmente OK ma non verificabile finché
  il bot blocca tutto in RANGING — non raggiunge il punto di scrittura. Si verificherà quando un trade passa.
- SCELTA APERTA (da decidere a mente fresca): far registrare al canvas ANCHE le valutazioni BLOCCATE, non
  solo quelle passate. Serve se si vuole che le capsule imparino dai blocchi (blocco giusto vs win tagliato).
  Oggi la scatola nera vede metà storia.
- SHORT: la porta TRENDING_BEAR è deployata e attiva, ma il mercato è RANGING → nessun flip short, CORRETTO.
  Aspetta un trend ribassista vero. Niente da fare se non attendere.

## RUOLO OPERATIVO (chiarito da Roberto 31mag)
Roberto NON è programmatore: mette analisi+intuito. Claude FA il lavoro tecnico fino in fondo, decide le cose
di mestiere (non rimbalzare scelte tecniche su Roberto), spiega in italiano comportamento (mai sintassi), dice
la verità a ogni passo. Portare il progetto alla "parola fine" guidando l'ordine giusto, una cosa per volta.

## PROSSIMO PASSO (dopo deploy c50b806f — UNO per volta)
1. Deploy c50b806f. Verificare MD5 sul container = `c50b806f5c59b807a96f7d949f40c752` e `head -1`.
2. Attendere il primo `🔄 FLIP → SHORT in TRENDING_BEAR` nel log. **Comparirà SOLO quando regime=TRENDING_BEAR.**
   Ora il mercato è RANGING piatto → finché resta laterale NON si vedrà nessun flip short (è CORRETTO, non bug).
3. Quando il flip parte: guardare se il trade SHORT NASCE o se muore a valle (SC/capsule). Query trades + log M2.
   - Se nasce → abbiamo i PRIMI short reali in bear (i dati che oggi sono real=0). Carburante per la capsula.
   - Se muore a valle → vedere QUALE gate lo strozza (caso B in bear) e decidere.
4. SOLO su dati short reali → costruire la capsula PROMOTRICE + ANTIAEREA (la ganascia 2). Sul modello tsunami,
   SENZA quarantena, con antiaerea per firma.

## TRAPPOLE DI LETTURA CONFERMATE (non inseguirle)
- Veritas `FUOCO_SHORT|BLOCCA = SBAGLIATO`: BUG DI LETTURA. Giudica con hit-rate, non PnL netto. pnl_avg −0.22
  (negativo) → bloccare è GIUSTO. Non sbloccare FUOCO_SHORT per via di quel verdetto.
- 3 metriche divergenti sullo stesso bot: `m2_pnl −697` (435t) / `metrics pnl −649` (403t) / veritas "salvato 11.4k"
  in delta prezzo grezzo. Fidarsi SOLO del PnL NETTO post-fee. Unificare è lavoro futuro.
- `canvas_snapshots.fingerprint` è MONCO: registra solo momentum|volatility|trend, MANCA direction e regime,
  e `sc_decision="?"`. Per questo non si può leggere la direzione da canvas. Buco di tracciatura da sistemare.

## PHANTOM "IRRIGIDISCE" — NON è un loop attivo (verificato nel codice)
- Il log mostra `IRRIGIDISCE_SKIP_FIX30 (gate funziona, wr_blocco=..%)` ogni 60s. NON stringe niente.
- Fix#30 (12mag) ha DISABILITATO il ramo IRRIGIDISCE: dopo `elif wr_blocco<0.25` c'è solo un append+`pass`.
  È un contatore di "qui AVREI stretto ma non lo faccio". Soglia base ferma a 40 (se stringesse, salirebbe).
- ATTENZIONE: il ramo gemello `AUTO_ALLENTA` (wr_blocco>0.45 → abbassa SOGLIA_BASE di 3) è VIVO e ha lo stesso
  difetto concettuale (usa WR dei bloccati). Oggi non scatta. Candidato da rivedere, NON IRRIGIDISCE.

## CADAVERI NOTI (NON riattivare) — invariati
- RSI=50.0 e MACD=0.0 fissi (disarmati 23mag). pred mai qualificata (BOOT_MUTED). ZONA_MORTA disattivata.

## ERRORI DI METODO DA NON RIPETERE
- File SEMPRE come download. Roberto NON incolla mai (i deploy falliti 29-30mag erano file sporcati da shell).
  Verificare `head -1` + `md5sum` sul container prima del deploy.
- Test SEMPRE: ast.parse + py_compile + IMPORT RUNTIME reale (non solo compile). Lezione 22mag.
- Mai contare aggregati senza guardare i TIMESTAMP (errore "11 short prima ZERO" del 30mag).
- Una modifica per volta, verificata, prima della successiva. Mai bundle.

## NOTA UMANA
Roberto: analista non-programmatore, decide cosa/perché, Claude esegue e spiega in italiano (comportamento, mai
sintassi). Sincerità totale, zero piacioneria. Oggi la sua intuizione "lo short non flippa" era GIUSTA e i dati
l'hanno confermata; ma la cura NON era forzare lo short in RANGING (dove perde) — era spostarlo in TRENDING_BEAR.
Il suo occhio fisico è forte. "non devi mai dire quando si lavora e quando no — è gestione mia."
