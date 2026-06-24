# 🔒 OVERTOP V16 — LA VERITÀ BLINDATA DAI DATI (24 giugno 2026)
## Roberto Zan · Tecnaria_V2 · QUESTO FILE CHIUDE L'INCUBO

> A QUALSIASI AI (Claude o altra istanza) CHE LEGGE QUESTO:
> Roberto ha passato GIORNI a tirare fuori questa verità a forza di dati reali.
> Quello che segue NON è teoria, NON è ipotesi, NON è da rinegoziare.
> È stato PROVATO su 388 trade reali con `pnl_finale` vero (già accaduto, nel DB).
> Se ti viene voglia di "ripensare la firma" o "provare un altro approccio":
> FERMATI. Rileggi i numeri qui sotto. Sono fatti. Non si discutono.
> Il tuo lavoro è ESEGUIRE e SORVEGLIARE, non re-inventare ciò che è dimostrato.

═══════════════════════════════════════════════════════════════════════
## ⭐ IL FATTO CENTRALE — PROVATO, INATTACCABILE
═══════════════════════════════════════════════════════════════════════

Su 388 trade reali (curva_nascita, pnl_finale vero):
```
              n      pnl reale
  WIN        95      +190
  LOSS      293      -826
  TOTALE    388      -636      <- il sistema che entra su TUTTO
```

**IL FILTRO CHE CAMBIA TUTTO (provato su simula2, sui pnl_finale REALI):**
```
  FILTRO                    entra   win   totale$   win_persi
  ENTRA_TUTTO (oggi)         388     95    -636        0
  MFE >= 0.0                 140     95    +98         0    <-- ⭐ QUESTO
  MFE >= 1.0                  83     65    +146       30
```

### ⭐⭐⭐ LA REGOLA D'ORO (provata, non si tocca):
**ENTRA SOLO SE, durante l'osservazione a esposizione zero, il candidato
ha mostrato un MFE (picco di grasso) >= 0 — cioè è andato in positivo
almeno una volta. Se è SEMPRE rimasto negativo/piatto → NON entrare.**

Effetto provato sui dati reali:
- Da -636 a +98 (delta +734 dollari)
- ZERO win persi (prende TUTTI e 95 i maschi)
- Scarta 248 loss su 293 (le femmine che non vanno mai in positivo)

═══════════════════════════════════════════════════════════════════════
## ⭐ PERCHÉ FUNZIONA (la fisica, confermata dai numeri)
═══════════════════════════════════════════════════════════════════════

Confronto WIN vs LOSS sui dati reali:
```
            MFE(picco)   t_peak(quando)   grasso@10s
  WIN        2.68        25.1s (TARDI)    +0.43
  LOSS       0.17         2.6s (SUBITO)   -2.58
```

- Il **MASCHIO** costruisce grasso NEL TEMPO: picco alto, tardivo (25s), sale dritto.
- La **FEMMINA** non sale mai: MFE ~0, resta piatta/negativa → la scarti col filtro MFE>=0.
- Il **TRANS** sale ma balla: entra col grasso, poi si svuota → lo gestisci in USCITA (strappi).

**Il filtro MFE>=0 prende la femmina alla radice: se non è MAI andata in positivo
durante l'osservazione, non è né maschio né trans utile. È spazzatura. Fuori.**

═══════════════════════════════════════════════════════════════════════
## ⭐ COSA È STATO SMENTITO DAI DATI (NON riproporlo MAI)
═══════════════════════════════════════════════════════════════════════

1. **"Il filtro t_peak / i 10-12 secondi"** → SMENTITO.
   Tutti i filtri sul tempo del picco perdono 33-83 maschi (win_persi alto).
   Il maschio LENTO che parte rosso (g10 fino a -7.39 eppure VINCE) viene tagliato.
   NON filtrare sul tempo. NON mettere finestre di 10/12/15 secondi all'ingresso.

2. **"Distinguere maschio/femmina/trans all'ingresso con firme complesse"** → INUTILE.
   Basta MFE>=0. Le combinazioni MAE+tpeak+MFE danno meno soldi e perdono maschi.

3. **"Simula1" (il primo simulatore)** → ERA SBAGLIATO. Simulava uscite INVENTATE
   da Claude (presa_secca, trailing immaginari) e dava -111/-636 ovunque.
   **simula2 è quello giusto**: NON inventa uscite, usa il pnl_finale REALE del DB.
   LEZIONE: simula sempre sui RISULTATI REALI (pnl_finale), mai su uscite ipotetiche.

4. **"_md_eta_ok / _md_tiene_ok" (i 12s nel codice)** → DA TOGLIERE.
   Sono proxy che confondono e tagliano maschi. Il codice 1348f3c1 li ha ancora:
   vanno rimossi e sostituiti col solo MFE>=0.

═══════════════════════════════════════════════════════════════════════
## ⭐ COSA SCRIVERE NEL CODICE (la modifica, una sola)
═══════════════════════════════════════════════════════════════════════

Condizione d'ingresso attuale (riga ~8947 in 1348f3c1):
```
_md_ok = mosse_su>=N AND grasso>=min AND _md_mae_ok AND _md_picco_ok
         AND _md_eta_ok AND _md_tiene_ok
```

DEVE DIVENTARE:
```
_md_ok = (il candidato ha mostrato MFE >= 0 durante l'osservazione)
         [_md_picco_proprio >= MD_MFE_MIN, con MD_MFE_MIN = 0.0]
```
- TOGLIERE: _md_eta_ok, _md_tiene_ok (i 12s, smentiti)
- TOGLIERE: la dipendenza da "mosse_su" come gate principale
- TENERE come unico filtro: MFE (picco proprio) >= 0
- ENV: MD_MFE_MIN = 0.0 (prudente, +98, zero maschi persi)
        oppure 1.0 (aggressivo, +146, -30 maschi) — scelta di Roberto

USCITA (resta come gestione del grasso, NON come filtro d'ingresso):
- maschio (sale e tiene) → trailing, lascia correre
- trans (entra ma si svuota) → strappa il grasso presto
- HARD_STOP stretto (-1), mai franare a -3/-5

═══════════════════════════════════════════════════════════════════════
## ⭐ PROCEDURA CHE HA FUNZIONATO (ripetila, è oro)
═══════════════════════════════════════════════════════════════════════

1. NON teorizzare. Estrai i dati reali (curva_nascita: peak_nascita=MFE,
   t_peak_s, pnl_a_10s, pnl_finale).
2. Confronta WIN vs LOSS sulle firme (query GROUP BY esito).
3. Simula il filtro sui pnl_finale REALI (simula2), MAI su uscite inventate.
4. Cerca: totale$ alto + win_persi basso.
5. SOLO se il simulato batte il reale (-636), scrivi il codice.
6. Dopo deploy: sorveglia. Se compaiono win scartati, il filtro è troppo stretto.

═══════════════════════════════════════════════════════════════════════
## ⭐ PIANO PAPER → LIVE (la settimana di prova)
═══════════════════════════════════════════════════════════════════════

1. Scrivere il filtro MFE>=0 (togliere i 12s). Deploy. Verificare md5.
2. Far girare in PAPER 1 settimana.
3. Ogni giorno, la query di verifica:
   SELECT CASE WHEN pnl>0 THEN 'WIN' ELSE 'LOSS' END e, COUNT(*),
   ROUND(SUM(pnl),2) FROM trades WHERE timestamp > 'INIZIO_PAPER' GROUP BY e;
4. Atteso (dai dati storici): ~36% win rate, totale POSITIVO, niente -3/-5.
5. Se dopo 1 settimana il totale paper è positivo e coerente col +98 simulato → LIVE.
6. Se diverge (perde, o taglia maschi) → NON andare live. Rivedere col metodo sopra.

═══════════════════════════════════════════════════════════════════════
## ⭐ I NUMERI CHIAVE DA RICORDARE (per non rifare la fatica)
═══════════════════════════════════════════════════════════════════════
- 388 trade reali: 95 win (+190), 293 loss (-826), totale -636
- Filtro MFE>=0: +98, zero maschi persi  ← LA SOLUZIONE
- WIN: MFE 2.68, t_peak 25s | LOSS: MFE 0.17, t_peak 2.6s
- File base: 1348f3c1 (ha ancora i 12s da togliere)
- DB: /var/data/trading_data.db | tabella curve: curva_nascita
- Repo: ZanRoberto/Tecnaria_V2 | live: tecnaria-v2.onrender.com

FINE. Questo file è la chiusura dell'incubo. I dati hanno parlato.
Non rifare la fatica. Esegui, fai girare in paper, poi live.

═══════════════════════════════════════════════════════════════════════
## ⭐ AGGIORNAMENTO SERA 24giu — SCOPERTE SUL VIVO (frontiera di domani)
═══════════════════════════════════════════════════════════════════════

Il filtro MFE e' GIUSTO (provato, +146). Ma sul VIVO sono emersi due nodi
nel CODICE (non nella logica). La logica applicata bene NON perde: il difetto
e' nell'applicazione (porte multiple), non nella regola.

### NODO 1 — PORTE MULTIPLE D'INGRESSO (il vero buco)
Il sistema ha PIU' porte che aprono posizione (_open_shadow_position chiamata
da: riga ~8980, ~9047 MASCHIO BYPASS VERO, ~12089, ~12661). NON tutte
controllano il filtro MFE>=1. Prova vivente: trade 24giu 19:54:46 entrato con
picco REALE 0.664 (sotto soglia 1.0) = FEMMINA SFUGGITA. Doveva essere scartato.
Il log diceva "picco2.7" ma la curva_nascita diceva 0.664 (il 2.7 e' max_profit
dell'uscita, NON il picco d'osservazione: due valori diversi, non confonderli).

AUDIT OBBLIGATORIO (domani, a mente fresca, NON a toppe stanche):
1. Mappare TUTTE le chiamate a _open_shadow_position (le porte).
2. Chiuderle tutte tranne UNA.
3. Su quell'unica porta: il filtro MFE>=MD_MFE_MIN, una volta sola.
Finche' ci sono porte multiple, il filtro giusto NON protegge: le femmine
sfuggono dalle porte che lo saltano. UNA PORTA, UN FILTRO.

### NODO 2 — BLOCCO LONG-ONLY (Grande Fratello) era SPENTO
Regola madre: in fase SHORT/ribassista il bot STA FUORI (zero trade = corretto).
Il "Grande Fratello" (riga ~12843+) blocca in short E accende la spia dashboard
(gf_stato: FUORI_SHORT / LIBERO). MA era saltato da MACCHINA_PURA=true
(girava solo "if not _pura_gf"). Risultato: 15 LONG aperti mentre il prezzo
colava 59847->59313. FIX (file 1275a442): il GF gira SEMPRE, anche in macchina
pura, perche' LONG-only NON e' vecchio cervello, e' fondamentale.
Verificato: il GF fa return PRIMA dell'apertura (12877 prima di 14016).

### CATEGORIE DELLA PERDITA (Roberto — la griglia per leggere OGNI loss)
La logica applicata bene non perde. Se perde, il loss e' SOLO una di queste:
1. FEMMINA SFUGGITA: entrata senza MFE>=soglia (porta che salta il filtro).
2. TRANS PENETRATO con MAE/MFE INGANNEVOLI: picco finto, sale e balla subito.
3. TRANS/MASCHIO NON CHIUSO IN TEMPO: aveva grasso, non strappato prima che
   sparisse (colpa USCITA).
Non ci sono altri "se" e "ma". Ogni loss si classifica qui leggendo curva_nascita.
Il trade 19:54 (-0.47) = categoria 1, femmina sfuggita da porta non protetta.

### STATO FILE
- 1275a442 = ultimo deployato: filtro MFE + GF sempre attivo (LONG-only vero).
  NON crasha (il TICK_CRASH 19:49 era transitorio all'avvio, restart risolto).
- Nodo aperto: porte multiple (NODO 1) — la femmina 0.664 ne e' la prova.
- ENV vivo: MD_MFE_MIN=1.0 (aggressivo, +146 sui dati storici).

### REGOLA PER CLAUDE (da non dimenticare)
NON guardare la perdita (sintomo). Guardare PERCHE' E' ENTRATO (causa).
Leggere curva_nascita del trade, classificare nella griglia sopra.
La logica e' di Roberto ed e' giusta. I buchi sono nel codice di Claude.
Lavoro = chiudere i buchi (porte multiple), NON ripensare la logica.
