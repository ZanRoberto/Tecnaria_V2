# STATO OVERTOP V16 — 1 giugno 2026

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║   ⚖️  LA TAVOLA DI MOSÈ — IL KILLER  (31mag2026, scoperto da Roberto)  ║
║                                                                        ║
║   "TUTTI NASCONO FEMMINA. IL MASCHIO SI MANIFESTA ENTRO UN TEMPO       ║
║    PRECISO, PERCHÉ HA GLI ELEMENTI PIÙ GRANDI."                        ║
║                                                                        ║
║   ┌──────────────────────────────────────────────────────────────┐   ║
║   │  ENERGIA (E) ≥ 40  →  88% WIN   (71 win / 10 loss)  = MASCHIO  │   ║
║   │  ENERGIA (E) <  40 →  28% WIN   (34 win / 89 loss)  = FEMMINA  │   ║
║   └──────────────────────────────────────────────────────────────┘   ║
║                                                                        ║
║   DIMOSTRATO SU 204 TRADE REALI. Frontiera NETTA a 40 (non graduale:  ║
║   E30-40 e E<30 hanno lo STESSO 28% → o sei sopra 40 o sei femmina).   ║
║                                                                        ║
║   IL KILLER: dai al trade il tempo di manifestarsi (≈35s, i win vivono ║
║   ~100s, i loss ~35s). Se resta sotto E40 ED è in perdita → FEMMINA    ║
║   non manifestata → MOLLALA SUBITO, non bruciare la fee tenendola.     ║
║                                                                        ║
║   Questo CORREGGE il "timeframe lungo" (30mag) che teneva aperte le    ║
║   femmine sperando respirassero — ed era ciò che trasformava i -2$     ║
║   in -6$ (peggiorava i loss). I dati hanno dato ragione a Roberto.     ║
║                                                                        ║
║   Codice: dentro MD5 551ac1a3 — cerca "KILLER_E40" (4 occorrenze).     ║
║   Interruttori: KILLER_E40_OFF | KILLER_E_SOGLIA=40 | KILLER_TEMPO=35  ║
║   Log quando agisce: ⚰️ KILLER_E40 nel log M2.                         ║
║                                                                        ║
║   DA RAFFINARE (microscopio): il "tempo preciso" esatto (a che SECONDO ║
║   il maschio raggiunge E40) lo darà la curva_nascita quando il bot     ║
║   aprirà trade. Il 35s è la migliore stima dai dati di durata.         ║
║                                                                        ║
║   NON RIMETTERE IN DISCUSSIONE QUESTA FRONTIERA SENZA RIMISURARLA SUI  ║
║   DATI. È SCRITTA SULLA PIETRA. 88% vs 28% su 204 trade reali.         ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
```

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║   💰 LA SECONDA TAVOLA — I LINGOTTI DEBOLE (1giu2026, da Roberto)      ║
║                                                                        ║
║   "OTTIMI IN DIFESA, MA NON PROFITTEVOLI IN ATTACCO. SE NON GUARDI    ║
║    BENE SEMBRANO TUTTI UGUALI E BUTTI TUTTO DAL LAVANDINO."           ║
║                                                                        ║
║   Roberto ha letto il monitor ("$24.525 salvati / $1.493 mancati")    ║
║   e ha fiutato che i "mancati" erano oro buttato, non difesa giusta.  ║
║   I dati gli hanno dato ragione, OLTRE le aspettative.                ║
║                                                                        ║
║   SCOPERTA (phantom_forensic, is_win=1, momentum=DEBOLE):             ║
║   ┌──────────────────────────────────────────────────────────────┐   ║
║   │  mfe 2-3$:  344 trade   (media 2.44)                          │   ║
║   │  mfe 3-4$:  176 trade   (media 3.51)                          │   ║
║   │  mfe 4-6$:  219 trade   (media 4.73)                          │   ║
║   │  mfe >6$:   482 trade   (media 10.36)  ← LINGOTTI, non monetine│   ║
║   └──────────────────────────────────────────────────────────────┘   ║
║                                                                        ║
║   FRONTIERA NETTA (come il 40): mfe_min maschi DEBOLE = 2.01.         ║
║   NESSUN maschio DEBOLE fa picco sotto 2$. Sotto 2 = femmina piatta.  ║
║   (femmine bloccate: mfe medio 0.31 — non si muovono mai).            ║
║                                                                        ║
║   IL BUG: il floor di armamento SMORZ_TAKE per DEBOLE era 3.00$ →     ║
║   tagliava fuori i 344 maschi della fascia 2-3 (non armavano MAI la   ║
║   presa sul picco) e ritardava su tutti gli altri. Il grasso          ║
║   evaporava: maschi da mfe 4-10$ incassati a ~2$ perché si usciva     ║
║   DOPO che la forza era decaduta (LOCK_EVAP / EXIT_E tardivi).        ║
║                                                                        ║
║   LA CURA (1giu): floor DEBOLE 3.00 → 2.20 (appena sopra la frontiera ║
║   2.01). Ora SMORZ_TAKE si arma appena il maschio si manifesta e      ║
║   prende sul rallentamento mentre il prezzo è ALTO. Le femmine        ║
║   (<2) restano fuori → protezione anti-uscita-precoce mantenuta.      ║
║                                                                        ║
║   Codice: dentro MD5 1f5af1af — env FLOOR_LOW_DEBOLE=2.20.            ║
║   SMORZ_TAKE scatta solo CON decelerometro → non taglia il maschio    ║
║   che ancora corre, solo quello che già rallenta.                     ║
║                                                                        ║
║   ⚠️ DA CONSOLIDARE: i 4.820$ di spreco sono dati VECCHI (bot di      ║
║   settimane fa). La frontiera 2.01 è solida. Ma che il bot di OGGI    ║
║   raccolga i lingotti vicino al picco VA VISTO sui trade NUOVI col    ║
║   microscopio esteso acceso. È miniera POTENZIALE finché non si vede  ║
║   il primo lingotto cadere nel sacco. MEDIO/FORTE non misurati: floor ║
║   NON toccato lì (solo DEBOLE ha la frontiera dimostrata).            ║
║                                                                        ║
║   "STAVO PER CAMBIARE VESTITO CON LE STESSE MUTANDE SPORCHE" — lo      ║
║   sprecone trovato PRIMA di cambiare mercato. Pulite le mutande,      ║
║   poi si cambia vestito. Non si porta lo spreco su un mare ricco.     ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
```

> Claude: leggi questo PRIMA di toccare codice. È lo stato reale e VERIFICATO sui dati al 31 maggio.
> REGOLA #0: leggere i dati reali (DB/log/endpoint) prima di proporre. File sempre come download, mai da incollare.
> PRINCIPIO CAPSULA (Roberto, sacro): "Dove uno mette una soglia rigida, io penso a una capsula adattogena."

---

## IL FILE CHE GIRA / DA DEPLOYARE
- **MD5 ULTIMISSIMO (1giu, microscopio esteso + floor DEBOLE 2.20): `1f5af1af4c87e7f5c818800221af916b`** — 14.920 righe. ← DEPLOYARE QUESTO.
- Catena 1giu: `551ac1a3` (KILLER E40, 31mag notte) → `a0f0806a` (microscopio VISIBILITA' ESTESA opzione B) →
  **`1f5af1af` (floor DEBOLE 3.00→2.20, presa lingotti)** ← ULTIMO.
- AL DEPLOY: `md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py` deve dare `1f5af1af4c87e7f5c818800221af916b`.
- Env regolabili senza redeploy: `FLOOR_LOW_DEBOLE=2.20` | `MICRO_PASSO_S=3` | `MICRO_MAX_PUNTI=200` |
  `KILLER_E40_OFF` | `KILLER_E_SOGLIA=40` | `KILLER_TEMPO=35` | `CAPSULA_REGIME_EDGE_L4=true` (per armare la capsula).
- File compagno: `capsula_regime_edge.py` (MD5 968b7960) deve stare nel repo accanto al bot.

### LE 3 MODIFICHE DI OGGI (1giu) — tutte dentro 1f5af1af, tutte testate (AST+py_compile+IMPORT RUNTIME)
1. **MICROSCOPIO ESTESO (opzione B):** prima filmava solo 0-10.5s poi STOP (buco cieco fino all'uscita →
   causava diagnosi alla cieca). Ora: 0-10.5s ogni tick (nascita densa) + ogni 3s fino alla chiusura
   (evoluzione). Flush UNICO alla chiusura (in `_close_shadow_trade`, ~riga 12700 — verificato: 1 sola
   chiamata, niente doppio salvataggio). Tetto duro `MICRO_MAX_PUNTI=200` → niente disco pieno (lezione N²).
   DB curva_nascita: aggiunta colonna `pnl_finale`; `pnl_a_10s` resta il punto più vicino a 10s (confrontabile).
2. **FLOOR DEBOLE 3.00 → 2.20:** vedi 2ª Tavola sopra. Recupera i 344 maschi fascia 2-3$. Solo DEBOLE.
3. (Il KILLER E40 di 551ac1a3 è INTATTO — non toccato. Verificato grep=1.)

### COSA E' PRONTO / COSA E' VISIONE (chiarito 1giu)
- PRONTO E GIRA: killer E40, microscopio esteso, fix tracciatura direction/regime, floor DEBOLE 2.20.
- PRONTO MA OSSERVATRICE (non armata): capsula regime_edge (L3, serve CAPSULA_REGIME_EDGE_L4=true per armarla).
- SOLO VISIONE (zero codice): mercato nervoso/altcoin, metro del vento, capsule-coppie 3 cerchi, cassa circolante.

## (STORICO 31mag) IL FILE CHE GIRAVA
- MD5 col KILLER E40: `551ac1a30642eb645d6215c44b9a21de` — 14.863 righe.
- Catena 31mag completa: `527a63df` (fix uscita 30mag) → `c50b806f` (porta SHORT bear) →
  `40d40b5a` (fix tracciatura direction/regime) → `587070ea` (microscopio curva_nascita) →
  `289f104c` (capsula regime_edge agganciata, 4 hook) → **`551ac1a3` (KILLER E40)** ← DEPLOYARE QUESTO.
- File compagno: `capsula_regime_edge.py` (MD5 968b7960) deve stare nel repo accanto al bot.
- AL DEPLOY verificare md5sum container = `551ac1a30642eb645d6215c44b9a21de`. Log killer: ⚰️ KILLER_E40.
- (storico) MD5 precedente: `40d40b5a77c9bdb6c452a9103a36f8df` — 14.695 righe — riga 1 `#!/usr/bin/env python3`
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

## ═══ VISIONE STRATEGICA — NOTTE 31mag→1giu + analisi 1giu (NON perdere) ═══

### LA CATENA DEL RAGIONAMENTO (ogni passo nasce dal precedente, sui dati)
1. Il bot perde → NON è la direzione → è il CAMPO (SIDEWAYS). 450+ trade SIDEWAYS = -822$.
2. SIDEWAYS = merda MA i win nascono lì → il filtro non è il campo, è DURATA/ENERGIA (il killer E40).
3. E40 separa 88%/28% (1ª Tavola). Microscopio (9 nascite): maschio si manifesta entro ~8s (peak>1.5),
   femmina resta a peak 0.0. CONFERMA VISIVA della regola di Roberto.
4. MA a 8s nel piatto è sempre fermo → vero mostro = SCALPING NEL MARE MORTO (460 biglietti×2$=920$ fee
   per 62 win = 1 win ogni 7.4 biglietti). Lo scalping nel piatto non paga.
5. BTC = PACHIDERMA: enorme, liquidissimo, solido per INVESTIRE ma senza onde brevi → MARE SBAGLIATO
   per SCALPING. È stato la PALESTRA GIUSTA (l'aridità ha costretto a costruire intelligenza vera).
6. Serve barca da CORSA su mare con VENTO, non transatlantico in bonaccia. ("Luna Rossa a remi non va.")

### ANALISI DIFESA/ATTACCO (1giu, sui dati phantom_forensic)
- Difesa ECCELLENTE, NON troppo zelante: 38.048 loss bloccati (-80.491$ risparmiati) vs 1.300 win
  bloccati (+2.035$). Rapporto 29:1. Allentare la difesa = riaprire spazzatura, pessimo affare.
- I guardiani che contano: `SC_BLOCCA_FP_TOSSICO_wr=18%` (20.638 blocchi, 805 maschi, blocca contesti
  al 18% WR — giusto) e `TSUNAMI_NO_ENTRY` (17.179 blocchi, 372 maschi). Bloccano su SCORE/CONTESTO,
  dove maschi e femmine sono GEMELLI (score 28.2 vs 27.7, identici). Per questo bloccano in blocco.
- MA nell'mfe sono OPPOSTI: maschi mfe 4.24, femmine 0.31. Il maschio si tradisce nel MOVIMENTO,
  non nel contesto. Il guardiano è cieco proprio sulla dimensione dove la differenza esiste.
- LEZIONE: il guardiano grezzo NON era "presuntuoso" — era GIOVANE (difesa d'emergenza giusta nel suo
  momento). La mossa non è demolirlo (riapre 80k di loss), è DARGLI OCCHI MIGLIORI: distinguere maschio
  da femmina guardando la curva di nascita (movimento primi secondi), non solo lo score.
- ATTENZIONE mfe è dato "del futuro" (lo sai solo a trade finito) → NON usabile come filtro d'ingresso.
  MA dice DOVE guardare: il movimento dei primi secondi (= curva di nascita del microscopio), che invece
  è leggibile in tempo reale. Il microscopio esteso serve a raccogliere quella prova sui trade nuovi.

### ARCHITETTURA OBIETTIVO — "CAPSULE-COPPIE" a 3 cerchi (VISIONE, zero codice)
- 3 coppie TITOLARI che tradano. Se girano bene RESTANO. 1 in PANCHINA = "osservata speciale" (la più
  nervosa, monitorata da vicino, pronta a subentrare, resta prediletta anche dopo uscita). Le OSSERVABILI
  = resto del mercato, scansionato da lontano. L'OSSERVATORE decide chi entra/esce, NON Roberto.
- ROTAZIONE: UNA coppia alla volta. La più fiacca esce → panchina → entra un'altra. Mai stravolgere tutto.
- È la MATRIGNA applicata alle coppie (nascita/CONGELATA/morte). Osservatore a 2 velocità: veloce
  (titolari+quarta→rotazione), lenta (osservabili→promozione a osservata speciale).
- DNA PER COPPIA: ogni capsula-coppia NASCE OSSERVATRICE, impara le soglie della SUA coppia dai dati di
  QUELLA coppia, si arma solo quando le conosce. MAI numeri "in prestito" (il 40 è di BTC, su ETH è altro).

### REGOLE CASSA (ferree, Roberto 1giu) — sistema CHIUSO
- TETTO MASSIMO ESPOSTO: 1500$ INVALICABILE. Max 2 posizioni aperte PER COPPIA (3×2×250$=1500$).
- CAPITALE CIRCOLANTE non additivo: "si vende si compra", MAI "si compra si compra" (= cassa esaurita).
  Coppia che esce → VENDE → reinserisce il residuo sulla nuova coppia. Stesso 1500 che gira tra gli slot.
- A TETTO PIENO SI ASPETTA. Mai aggiungere capitale per inseguire un'occasione. (scelta Roberto: opzione A)
- DA CHIARIRE: 250$ è SIZE o ESPOSIZIONE (cambia il calcolo fee — la fee si paga sulla SIZE).

### REGOLA DI SICUREZZA ASSOLUTA (sopra tutto)
**PAPER SEMPRE finché il sistema non è rodato sui dati. "I soldi si usano con auto rodata, non con
prototipo." L'apprendimento del DNA di ogni coppia avviene GRATIS in paper — sbagliare lì non costa nulla.**

### ROTTA A TAPPE (NON tutto insieme)
- Tappa 0 (FATTA 1giu): smettere di buttare i lingotti DEBOLE su BTC (floor 2.20) + microscopio esteso.
- Tappa 0-bis (ADESSO): deploy 1f5af1af, microscopio acceso, GUARDARE se i lingotti arrivano davvero
  (SMORZ_TAKE scatta di più? maschi DEBOLE escono vicino al mfe?). Consolidare la miniera GUARDANDOLA.
- Tappa 1: portare il bot su UNA coppia nervosa (ETH/SOL, liquide; NO micro-cap illiquide). Paper.
  RIMISURARE la frontiera lì (energia, floor, killer — tutti numeri di BTC, vanno ributtati e rimisurati).
- Tappa 2: METRO DEL VENTO (oscilloscopio a scala AMPIA, NON 8s — a scala stretta tutto sembra fermo).
  Solo osservatore prima, non comanda.
- Tappa 3: DIRETTORE D'ORCHESTRA (le capsule-coppie a 3 cerchi). Solo dopo che 1 e 2 hanno dato prova.
- REGOLA D'ORO trasversale: il COMPORTAMENTO si adatta, il RIGHELLO (regola di osservazione) resta FISSO.
  (Verificato 1giu: dopo 4-6 win il righello del bot NON si è mosso — niente "testa montata". Telemetry pulita.)

## RUOLO OPERATIVO (chiarito da Roberto 31mag)
Roberto NON è programmatore: mette analisi+intuito. Claude FA il lavoro tecnico fino in fondo, decide le cose
di mestiere (non rimbalzare scelte tecniche su Roberto), spiega in italiano comportamento (mai sintassi), dice
la verità a ogni passo. Portare il progetto alla "parola fine" guidando l'ordine giusto, una cosa per volta.

## SCAVO MEDIO/FORTE (1giu, dopo i DEBOLE) — il prossimo lingotto
Stesso filone della 2ª Tavola, cercato negli stessi dati phantom_forensic (is_win=1):
- **MEDIO:** mfe_min **2.04** (frontiera quasi identica ai DEBOLE 2.01), 93 trade. Fasce: sotto3$=30,
  3-4=18, 4-5=11, 5-7=4, >7$=30 (media 12.72 → lingotti grossi). Floor MEDIO attuale = **3.50** → TAGLIA
  i maschi sotto 3$ (30) e parte dei 3-4. STESSO SPRECO DEI DEBOLE, su numeri più piccoli (93 vs 1221).
  → CURA PROPOSTA: floor MEDIO LOW 3.50 → **2.50** (più alto del DEBOLE 2.20 perché meno dati = più margine).
- **FORTE:** SOLO **3 trade** (mfe_min 2.5). DATO INSUFFICIENTE → NON TOCCARE. Tararlo = inventare. CONGELATO
  finché non accumula abbastanza maschi FORTE per una frontiera vera.

### DECISIONE CAPOPROGETTO (1giu): NON fare il MEDIO adesso
- Il floor DEBOLE 2.20 è appena deployato e NON ancora verificato sui trade nuovi (zero trade dal deploy).
- Mettere ORA anche il MEDIO = due modifiche non verificate insieme → se qualcosa va storto, diagnosi
  impossibile (quale delle due?). Viola "una cosa per volta, verificata prima della successiva. Mai bundle."
- SEQUENZA: 1) verificare DEBOLE 2.20 sui trade nuovi (i 3 segnali sotto). 2) SE confermato → applicare la
  STESSA cura al MEDIO (floor 2.50), modifica già validata su categoria gemella, rischio basso. 3) FORTE: dopo,
  quando ci sono dati.
- Il MEDIO NON scappa: i 93 trade e la frontiera 2.04 restano scritti nel DB. Aspettare non costa nulla.

## PROSSIMO PASSO (1giu — UNO per volta)
1. DEPLOY `1f5af1af`. Verificare `md5sum ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py` = `1f5af1af4c87e7f5c818800221af916b`.
   (Il verde Render NON è prova — il 31mag 2 deploy su 3 non erano arrivati. Solo md5sum sul container lo dimostra.)
2. CONSOLIDARE LA MINIERA guardando i trade NUOVI col microscopio acceso:
   - SMORZ_TAKE scatta di più? (prima 1 su 83 win). Query: trades, reason LIKE 'SMORZ_TAKE%'.
   - I maschi DEBOLE escono vicino al loro mfe invece che a metà? (confronto pnl_a_10s/pnl_finale vs incasso).
   - Curva_nascita ora ha la vita INTERA del trade (non più solo 10s): verificare che cattura oltre i 10s.
   - Se 2.20 risulta troppo alto/basso sui dati nuovi → spostare da env FLOOR_LOW_DEBOLE, NON toccare codice.
3. SE i lingotti arrivano davvero → miniera consolidata. Allora (e solo allora) ha senso Tappa 1 (mare nervoso).
   3-bis. SE il DEBOLE 2.20 è confermato → applicare la STESSA cura al MEDIO (floor 3.50→2.50, frontiera 2.04,
   93 trade). FORTE resta congelato (3 trade). Vedi sezione "SCAVO MEDIO/FORTE" sopra.
4. PASSO PARALLELO (quando ci sono dati nuovi): verificare se la curva di nascita distingue maschio da femmina
   GIA' nei primi 3-5 secondi (non solo nell'mfe a posteriori). Se sì → dare "occhi" al guardiano FP_TOSSICO
   perché lasci passare i maschi (lascia nascere il trade qualche secondo prima di bloccare). Recupera l'oro
   bloccato SENZA riaprire la spazzatura. NON ora: serve la prova sui dati freschi.

## (STORICO 31mag) PROSSIMO PASSO SHORT — UNO per volta
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
- (1giu) NON dedurre una traiettoria da due punti: Claude ha detto "il -3.36 a 10s è diventato -5.97 nei 40s
  di attesa" SENZA avere il dato di cosa succedeva in mezzo (microscopio filmava solo 10s). Era inventato.
  Roberto: "ERRORE TUO". → mai colmare un buco di dati con una storia plausibile. Se il dato manca, si dice.
- (1giu) NON sottovalutare quello che Roberto vede: Claude ha detto "monetine" DUE volte, i dati l'hanno
  smentito DUE volte (mfe 4.24, poi fascia >6$ media 10.36 = lingotti). Quando Roberto dice "guarda meglio",
  guardare meglio NEI DATI, non difendere la conclusione comoda.
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
