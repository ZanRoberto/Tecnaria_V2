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
- GUARDIANO 2 (riga ~3811, boot) → DA FARE. Se campo_carica<0.30 lo resetta a
  0.30. Rimuovere il check "if <0.30", tenere solo "if >0.45".
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
