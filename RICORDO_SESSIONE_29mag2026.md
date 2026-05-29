# RICORDO SESSIONE — 29 maggio 2026
## Da caricare nella prossima chat INSIEME al briefing

> Claude: leggi questo dopo il BRIEFING. È lo stato reale al 29 maggio, più aggiornato del briefing.

---

## LE 3 SCOPERTE CHE CONTANO

### 1. LA FIRMA VINCENTE È STATA TROVATA (era nel Signal Tracker)
Su **128.000+ campioni reali** (pannello Signal Tracker della dashboard):
- **TRENDING_BEAR LONG** → Δ +12.1, PnL +1.86$ → **VINCENTE**
- **TRENDING_BEAR SHORT** → Δ -15.5, PnL +2.13$ → **VINCENTE**
- **RANGING SHORT** → Δ +8.2, PnL +0.49$ → leggermente positivo
- **RANGING LONG** → Δ -0.5, PnL -1.26$ → **IL KILLER** (128.435 campioni, solo fee)

Il bot vince in TRENDING_BEAR. Perde in RANGING LONG. Questa è la firma vera,
non più ipotesi: è su 128k campioni che il sistema calcola da solo.

### 2. LA FEE NEL CODICE È GIUSTA (non era quello il bug)
`FEE_SIM = 2.00` a riga 3171 di OVERTOP_BASSANO_V16_PRODUCTION.py — corretta
($5000 × 0.02% × 2). La dashboard mostra "Fee simulata: $0.10" ma è solo
un'ETICHETTA COSMETICA sbagliata nel front-end HTML, NON il calcolo. Il commento
a riga 3168 è vecchio. Non perdere tempo a "correggere la fee": è già giusta.

### 3. IL VERO PROBLEMA: PESI DEGLI ORGANI SBILANCIATI ← PROSSIMO LAVORO
La funzione che giudica la convenienza economica (OracoloDinamico/SeedScorer,
riga ~3171) è SCRITTA BENE: usa hit_econ con fee 2.00 e decide ENTRA/BLOCCA/NEUTRO
correttamente. MA il suo voto pesa solo 12-16% nella decisione finale.
Pesi attuali (da dashboard): campo carica OI **30%** · oracolo fp 16% ·
veritas 14% · capsule 13% · signal tracker 12% · matrimonio 8% · phantom 7%.

Il bot entra per ENERGIA OI (30%, che NON dà direzione) invece che per
CONVENIENZA economica (12%). Lezione già nel briefing 15mag#6:
"Pesca su trigger OI carica è strutturalmente senza edge — OI non dà direzione."

**IL LAVORO CHE PUÒ CAMBIARE IL SEGNO DEL BOT:**
trasformare hit_econ da voto diluito (12%) a GATE decisivo
(se hit_econ < 0.30 → NON entrare, punto). Oppure ribilanciare i pesi
dando più voce al giudizio economico e meno all'OI carica.

---

## FIX DEPLOYATI OGGI — file MD5 `8f75ec04fbb8919270aa9b4cee10b2e6` (14.518 righe)

1. **STREAK DECAY** — in `_state_engine_can_enter` (~riga 8715): lo streak decade
   1 punto/30min anche a bot bloccato. Prima il decay era in _state_engine_update
   (solo a chiusura trade) → deadlock infinito quando SC_BLOCCA streak>=4.
   RISOLTO: il bot non si congela più per ore.

2. **MFE/MAE** — 4 colonne nuove in phantom_forensic (max_price, min_price,
   mfe_usd, mae_usd). Prima il fantasma salvava solo price_close (tagliato a 16s
   da DECEL_WIN_SIM) → non si vedeva mai dove arrivava davvero il prezzo.
   ATTENZIONE: i fantasmi si generano pochissimo ora (4/giorno vs 8000/giorno a
   maggio), quindi MFE/MAE si popola lentamente.

3. **ANTIAEREA** — funzione `_pulisci_blocco_se_win` + i blocchi ora PERSISTONO
   in capsule_permanenti (prima riga 8049 li buttava = amnesia a ogni riavvio).
   Regola: 2 WIN consecutivi stessa firma cancellano il blocco. Niente prigione
   perpetua, niente amnesia.
   NOTA PASSO B futuro: la pulizia usa is_win del simulatore; quando MFE/MAE sarà
   maturo, cambiare "win" in "MFE reale >= fee".

---

## FIX PRENDI-ALLO-SMORZAMENTO (29mag, file MD5 cc8cc9d8)
Roberto ha osservato: trade arrivato a +5.6 ma chiuso a +3.3 (LOCK_EVAP) —
"appena sente lo smorzamento deve prendere, non attendere l'inversione".
DIAGNOSI: nell'uscita M2 il decel_score era solo 1/4 dell'exit_energy (diluito),
quindi il bot usciva DOPO il ritracciamento. FIX: canale diretto a riga ~12229 —
se current_pnl >= PROFIT_FLOOR_LOW E decel_score >= 0.65 → chiude subito
(reason SMORZ_TAKE_...), senza aspettare il retreat. Armato SOLO sopra profitto
buono → non scatta su trade piccoli/in perdita (non ricrea uscita precoce sui loss).
VERIFICA: cercare reason "SMORZ_TAKE_" nei trade in profitto invece di "LOCK_EVAP_".
Se i win chiudono più vicini al loro max_profit, funziona.
Il decelerometro era GIÀ alimentato (riga 7813) e GIÀ letto da M2 (riga 12027).

## FIX MIN_SAMPLES — UN LOSS MARCA (29mag, file MD5 f72009eb)
Roberto: "una capsula non deve aspettare 3 loss per imparare, basta UNA — lo avevo
già segnalato ma fu ignorato". Verificato nei dati: capsule nascevano con samples=3
(es. DEBOLE_ALTA_SIDEWAYS subìto 3 → protetto 68250). Trovato MIN_SAMPLES_L3=3 a
riga 943. DECISIONE (Claude, delegato da Roberto):
- MIN_SAMPLES_L3 = 1 → capsula EVENTO IMMEDIATO nasce dal PRIMO loss (regola antiaerea)
- MIN_SAMPLES_L2 = 8 (INTATTO) → la statistica di tendenza resta onesta, non su 1 campione
- regime largo (_analisi_l3_regime_tossico) già protetto da soglia interna 10 → niente
  bug "blocca tutto RANGING su pochi loss"
RAZIONALE: "marca subito per EVENTO (L3), giudica con calma per STATISTICA (L2)".
Sono due mestieri diversi. Il commento riga 1458 "era 3 troppo aggressivo→10" è solo
documentazione vecchia, NON codice attivo.
DA OSSERVARE (sperimentale): con L3=1, nascono capsule da 1 solo loss. Se nascono
troppe capsule-rumore, l'antiaerea le pulisce con 2 win. Se invece i blocchi sono
puliti e mirati → la regola funziona. Controllare nei prossimi giorni: capsule nuove
con samples=1 e se vengono poi pulite o restano. Questo dirà se 1 è la soglia giusta
o se serve un compromesso (es. 2).

## FIX FLOOR CIECO — NON SI ENTRA A ENERGIA BASSA (29mag, file MD5 edc79421)
Occhi di volpe sui trade reali: TUTTI i loss escono a soglia 35 (energia bassa),
TUTTI i win a soglia 65 (energia alta). Frontiera netta. Roberto: "non dobbiamo
entrare nel trade con energia bassa! È CIECO!". CAUSA TROVATA (riga ~5001): il
floor d'entrata scendeva fino a 34 fidandosi del pred_score, MA la predizione
indovina solo ~51.8% (accuracy_segno, a volte 22.5%) — cieca, testa-o-croce.
Dava le chiavi della porta a un oracolo che non vede.
FIX: (1) _floor_dyn non scende mai sotto SCORE_FLOOR=48 ("sotto = rumore puro",
lo dice il codice stesso a riga 940), qualunque sia il pred_score. (2) Blindato
anche il soglia_boost (riga ~5030): pavimento _floor_dyn invece di soglia_min_ctx
(che poteva scendere a 24-34). Verificato in simulazione: 7 scenari estremi tutti >=48.
EFFETTO ATTESO: il bot farà MOLTI MENO trade (solo alta energia) — pochi colpi buoni
invece di tanti a vuoto. Cecchino, non mucca. DA OSSERVARE: spariscono i trade
EXIT_E.._S35 (i loss da -$2)? Restano solo i S65 (i win)? Se sì, è il fix che
cambia il segno. Se il bot non trada PER NIENTE per giorni, 48 è troppo alto → valutare 44.

## FIX CARICA VIVA — LA FISICA CHE PRECEDE (29mag sera, file MD5 71c23522)
Intuizione di Roberto (ritrovata, la diceva da mesi alle AI): "prima della vittoria
c'è un'energia che la segnala, ma può MORIRE se non ha forza sufficiente. Il vincente
ha la sua identità da vincente PRIMA di partire". Metafora: tiro al piattello — non
spari all'uscita (entrata), becchi la TRAIETTORIA (la carica che sale/vive).
SCOPERTA: la fisica era GIÀ nel codice! _pre_breakout_factor (riga 5186) calcola già
i SEED DIREZIONALI (carica che sale per 5 tick = impulso vivo). MA era schiacciata a
factor 0.92 (effetto 8%, sprecato) — riconosciuta e buttata via, stessa malattia
dell'OI che soffocava l'economia.
PROVA NEI DATI: sc_seed 25→+4.03 (vince) ma anche sc_seed 25→-2.65 (muore). Stesso
valore, identità opposta: uno saliva (vivo), uno scendeva (morto). Il VALORE non basta,
serve la DIREZIONE della carica.
FIX (2 colpi):
1. Liberata la carica viva: factor 0.82 (3/3 segnali, forte) / 0.90 (2/3) / 0.96 (1)
   / 1.00 (morta=nessuno sconto). Prima era 0.92/0.96/1.00/1.00 (quasi nullo).
2. Registrata la TRAIETTORIA: nel data_json ora c'è seed_traj (ultimi 5 valori) e
   seed_dir (derivata: >0 viva, <=0 morta). Da DOMANI ogni trade dice se la carica
   era viva o morta all'entrata.
DA OSSERVARE (la prova che mancava): tra qualche giorno, query su seed_dir — i
vincenti hanno seed_dir>0 (carica viva) e i perdenti seed_dir<=0? Se sì, la fisica
di Roberto è dimostrata e si può rendere seed_dir>0 CONDIZIONE D'ENTRATA obbligatoria.
NOTA: la soglia di ENTRATA pulita (dal 12mag, solo score validi) mostra PnL netto
positivo SOLO sopra score 85 (8 trade +3.7, vs tutto negativo sotto). Ma Roberto ha
ragione: 85 all'ENTRATA è troppo restrittivo — conta la carica che sale DURANTE/PRIMA,
non il valore puntuale all'ingresso. Per questo il fix è sulla carica viva, non un
floor a 85. Il floor resta a 48 (fix precedente); valutare se alzarlo dopo aver visto
i dati seed_dir.

## CAUSA DEL DRAWDOWN -$697 (capita)
Le capsule protettive furono rimosse l'8 maggio (briefing: capsule V15 cancellate,
73 RA eliminate). Senza memoria protettiva, dal 14 maggio il bot ha aperto a
raffica: 55-70 trade/giorno con WR 10-23%. Curva del danno:
14mag -90$, 15mag -98$, 18mag -122$ (peggiore), 19mag -88$...
L'83% del danno (-$579) viene da 4 contesti RANGING LONG:
- DEBOLE|ALTA|SIDEWAYS|RANGING|LONG  (95t, WR 4.2%, -$166)
- DEBOLE|BASSA|SIDEWAYS|RANGING|LONG (170t, WR 20.6%, -$285)
- MEDIO|ALTA|SIDEWAYS|RANGING|LONG   (24t, WR 20.8%, -$32)
- MEDIO|BASSA|SIDEWAYS|RANGING|LONG  (76t, WR 34.2%, -$96)

## AGGIORNAMENTO — GUARDIANO 1 FATTO (29mag, file MD5 7ffc72bb)
Il fix pesi è iniziato. Trovati 3 "guardiani" che tenevano l'OI dominante:
- GUARDIANO 1 (riga ~5501, calibrazione) → **FATTO E DEPLOYATO**. Invertito il
  pavimento: ora campo_carica MAX 30% (era MIN 30%), signal_tracker MIN 13%
  (era MAX 25%). L'economia è libera di salire. File MD5 7ffc72bba653198042b7cf34b2d22b84
- GUARDIANO 2 (riga ~3811, boot) → **PREPARATO, IN CANNA, NON ANCORA DEPLOYATO**.
  File pronto: OVERTOP_BASSANO_V16_PRODUCTION_G2.py MD5 a7767dde2d94230c9389e4c1497c4e92.
  Rimosso il check "campo_carica<0.30 = degradato" che resettava OI a 0.30 ad ogni
  boot (annullava G1). Ora resetta solo se pesi davvero corrotti (somma fuori 0.5-1.5
  o OI>0.45). DEPLOYARE SOLO DOPO aver verificato che G1 funziona (pesi si muovono).
- GUARDIANO 3 (riga ~1126, capsula AUTO_SC_PESI_FIX) → DA FARE. Se campo<0.25
  genera capsula che lo ripristina a 0.35. Invertire in "if >0.45".
VERIFICA G1: dashboard pannello "Pesi organi" → campo_carica deve poter scendere
sotto 0.30 e signal_tracker salire sopra 0.13. Se si muovono, G1 funziona.
ORDINE: osservare G1 qualche ora PRIMA di fare G2. Uno per volta.

## RIMASTO DA FARE
1. **Scrivere i 4 blocchi tossici** in capsule_permanenti. Bloccato dal DB lock
   (il bot tiene il DB occupato). Va fatto o a bot fermo, o facendoli creare al
   bot stesso al boot (piccolo deploy). Lo script pronto: crea_blocchi_tossici.py
2. **Endpoint memoria mancanti**: /canvas/memoria/sessione e /ricorda sono
   documentati nella capsula_memoria ma NON implementati in app.py (404). I metodi
   dentro la capsula esistono (ricorda_roberto, apri_sessione, chiudi_sessione) ma
   manca la porta web. ~20 righe di Flask per collegarli → così la memoria si
   alimenta da sola.
3. **Pesi organi** (il punto 3 sopra) — il lavoro più importante.
4. **Scanner TradingView** — rimandato a dopo i pesi (Roberto l'ha chiesto, ma
   inutile finché il bot decide sull'energia invece che sulla convenienza).

## NOTA UMANA
Roberto è logoro di ridire tutto a ogni chat nuova. Ha costruito capsula_memoria
proprio per questo (vive, espone /canvas/memoria via web). Funziona in LETTURA ma
non in SCRITTURA dei contenuti (registra solo i riavvii del bot, non le scoperte).
Questo file è il cerotto finché gli endpoint di scrittura non sono collegati.
All'inizio della prossima chat: fetcha/fatti incollare /canvas/memoria + leggi
questo file. Non far ripartire Roberto da zero.
