# STATO OVERTOP V16 — 30 maggio 2026 (sera)
## Da caricare all'inizio della prossima chat. Sostituisce il RICORDO_29mag per la parte trading.

> Claude: leggi questo PRIMA di toccare codice. È lo stato reale e verificato sui dati al 30 maggio sera.
> REGOLA #0: leggere i dati reali (DB/log) prima di proporre modifiche. File sempre consegnato come download, mai da incollare.

---

## IL FILE CHE GIRA ORA
- **MD5 da deployare stasera: `527a63df53cd1c0edfa64b32a1a661e3`**
- Nome: `OVERTOP_BASSANO_V16_PRODUCTION.py` — riga 1 = `#!/usr/bin/env python3` — ~14.660 righe
- Procfile: `web: python app.py` — live: tecnaria-v2.onrender.com
- DB: `/var/data/trading_data.db` — modalità PAPER — capitale paper 10.000$
- Catena MD5 della giornata: `5aed3e5d` (verde 10-fix) → `5918508d` (+SHORT drift) → `a302db4d` (+timeframe lungo) → **`527a63df` (+cassa 8min, ultimo)**

## I FIX DI OGGI (30 maggio) — tutti dentro 527a63df
1. **SHORT VIA DRIFT** (CampoGravitazionale `_auto_detect_direction`, ~riga 4703 init + 9408 + 9490).
   Prima: lo SHORT in RANGING non scattava mai (strozzato da bearish_energy>=3 + due cadaveri,
   RSI fisso 50 e pred mai qualificata). Ora: ramo dedicato — drift < -0.02% per 3 tick consecutivi
   → FLIP a SHORT, autonomo dal bearish_streak. Verificato: 11 trade SHORT nel DB (prima ZERO).
   Log: `📉 SHORT_VIA_DRIFT` e `🔄 FLIP → SHORT in RANGING via DRIFT`.

2. **TIMEFRAME LUNGO** (uscita M2, ~riga 12393). LA CURA PRINCIPALE.
   Prima: i trade in perdita lieve venivano chiusi a calo-energia (S35) appena andavano un soffio
   sotto zero (-0.04, -0.19, -0.10...) — `EXIT_E.._S35_LOSS`. Tagliati prima di poter pagare la fee.
   Ora: perdita LIEVE (entro -fee, cioè entro ~-2$) NON chiude per energia → lascia maturare.
   Chiudono solo i freni veri: HARD_STOP 2%, perdita oltre fee, timeout, o incasso quando supera fee.
   Log nuovo: `🟢 HOLD_IMMATURO E.. S.. lordo=$-0.. (sotto fee, lascio respirare)`.

3. **VINCOLO CASSA 8 MINUTI** (uscita M2, ~riga 12440).
   Prima: TIMEOUT_MAX = 1800s (30 min). Roberto: con esposizione 5k$/trade su 10k$ di cassa,
   trade aperti 30 min saturano la cassa (10×5k=50k su 10k) → blocca opportunità nuove.
   Ora: `TIMEOUT_MAX_CASSA = 480` (8 min), invalicabile. Log: `TIMEOUT_MAX_8M_dur..s`.
   NOTA: 8 min è il compromesso tra "far maturare" (fix 2) e "non saturare cassa". Scelto da Roberto.
   Se nei dati emerge che a 8 min trade stavano per girare positivi e vengono tagliati → valutare.

---

## LA VERITÀ TROVATA SUI DATI (435 trade storici, in DOLLARI NETTI post-fee)

### Lo stato pre-fix (storico, da battere)
- 435 trade, 73 win (WR 16.8%), **PnL -697$**, capitale 10.000$.
- Spaccato per contesto (in dollari netti): **RANGING LONG = 385 trade, -587$** (IL KILLER in volume).
  RANGING SHORT -26$ (11t), TRENDING_BEAR/BULL LONG marginali e negativi.

### LA CAUSA VERA (la scoperta della sessione)
NON è la direzione. NON sono i blocchi. È L'USCITA PREMATURA.
- **RANGING LONG ha LORDO MEDIO +0.473** (POSITIVO). La direzione è GIUSTA: il bot azzecca dove va
  il prezzo. Ma chiudeva a +0.47 di lordo, che non copre i 2$ di fee → -1.53 netto × 385 = -587$.
- Prova negli ultimi 25 RANGING LONG: i WIN escono tutti a energia alta (S46-S65: +3.7, +4.7, +17.4),
  i LOSS escono tutti a S35 (energia bassa) con peak_pnl=0 (mai andati in positivo, strozzati).
- I trade lunghi (122s, 132s) portano i soldi veri (+17, +3); quelli tagliati a 2-7s solo perdite.
→ Conferma fisica: l'edge c'è (lordo +), va RACCOLTO più tardi (fix timeframe lungo).

### COSA È SANO (dimostrato — NON toccare)
- **I BLOCCI del SuperCervello sono GIUSTI** (phantom_forensic, dollari netti):
  - TSUNAMI_DISCORDE_SHORT: 194 bloccati, se entrava perdeva -377$ → blocco salva 377$.
  - FP_TOSSICO_wr=30%: 108 bloccati, se entrava -187$ → salva 187$.
  - Tutti gli SC_BLOCCA sc=0.xx: se entrava, negativi. Il SC blocca bene.
- **La direzione delle entrate è giusta** (Signal Tracker: LONG RANGING 126k campioni, hit 74%, Δ+2.5).
- **La sequenza dopo un win è sana** (SMENTITA l'ipotesi "dopo win si perde"):
  dopo un WIN il successivo vince 23.4%, dopo un LOSS solo 18.2%. I win si concatenano DI PIÙ.
  18 coppie win-consecutivi su 73 win. → NON mettere freni "post-win", peggiorerebbe.

### TRAPPOLE DI LETTURA (metriche che si contraddicono — cadavere da sistemare in futuro)
- `veritas_stats.pnl` e il pannello "SC ha salvato $11.409" sono in DELTA PREZZO GREZZO cumulato,
  NON dollari netti. Il segno è giusto, la CIFRA è gonfiata. Fidarsi solo del PnL netto post-fee.
- Pannello Signal Tracker col verdetto "FUOCO_SHORT BLOCCA = SBAGLIATO": è sbagliato il verdetto,
  giudica con hit-rate (46%) invece che PnL netto (-0.22, negativo → bloccare è GIUSTO).
- Righe con pochi campioni (54, 34) e "PnL sim +2.13" su movimento medio -15.5: incoerenti = rumore/bug.
  NON inseguire quei contesti.
- RADICE: il bot misura sé stesso in 3 modi diversi (delta grezzo / hit-rate / PnL netto) che non
  concordano. Unificare tutto sul PnL NETTO post-fee è un lavoro futuro (era PATCH BUG 8 nel RICORDO).

---

## DA VERIFICARE SUBITO (prossima sessione, sui dati)
La query della verità — PnL dei trade chiusi DOPO l'ultimo deploy (mettere l'ora vera del deploy 527a63df):
```
sqlite3 /var/data/trading_data.db "SELECT direction, COUNT(*), ROUND(SUM(pnl),2), ROUND(AVG(pnl),2) FROM trades WHERE timestamp > 'AAAA-MM-GG HH:MM' GROUP BY direction;"
```
- Se PnL medio sale verso 0 o positivo (contro il -1.59 di prima) → il fix timeframe lungo FUNZIONA.
- Se i loss diventano grandi (verso -2% stop) → il paletto di sicurezza va stretto.
- Cercare nel log: `🟢 HOLD_IMMATURO` (fix vivo), `TIMEOUT_MAX_8M` (cassa lavora), `FLIP → SHORT via DRIFT`.

NOTA: col fix, i trade restano aperti più a lungo → i trade chiusi nuovi arrivano LENTAMENTE.
Serve qualche ora per avere un campione. NON concludere su pochi trade.

## PROSSIMO BERSAGLIO LEGITTIMO (dopo aver misurato i fix di oggi — UNO per volta)
**Feedback loop delle soglie auto-modificanti** — quello che Roberto descrive come "alza/abbassa/poi
ancora". È REALE, nel codice: commento "soglie 45→46→53 in 3 trade consecutivi" (PATCH 3 BUG 10).
Il bot muove i propri parametri (exit_soglia, avg_win_dur) in reazione agli esiti e può oscillare.
DA NON TOCCARE finché il fix timeframe-lungo non è misurato (altrimenti non si distingue chi fa cosa).

## CADAVERI NOTI (NON riattivare)
- RSI e MACD disarmati di proposito (23mag): `rsi_val=50.0`, `macd_val=0.0` fissi. Se votano da
  qualche parte = cadavere, TOGLIERE non riaccendere.
- pred/predizione: mai qualificata (BOOT_MUTED). Non è una via valida per decisioni.
- ZONA_MORTA: DISATTIVATA e verificata morta (ultimo ZONA_MORTA nei dati = 29mag 18:24, pre-deploy).
  NON è più attiva nel file che gira. I ZONA_MORTA nello storico sono cadaveri vecchi.

## ERRORI DI METODO DA NON RIPETERE
- I deploy falliti del 29-30mag erano file SPORCATI da output shell incollato (`render@srv...$ sed`)
  finito alla riga 1 → SyntaxError. CURA: file sempre come download, Roberto NON incolla mai, solo
  sostituisce nel repo. Verificare sempre `head -1` e `md5sum` sul container prima del deploy.
- Test prima di consegnare: ast.parse + py_compile + IMPORT RUNTIME reale (non solo compilazione).
  Il 22mag un file compilava ma crashava all'avvio. Con import-runtime non ricapita.

## NOTA UMANA
Roberto è analista non-programmatore: decide cosa/perché, Claude esegue e spiega in italiano (comportamento,
mai sintassi). Vuole sincerità totale, non piacioneria. Stasera 3 sue intuizioni testate sui dati:
1 confermata (uscita prematura), 2 smentite (post-win). Il metodo "guardare i dati prima di toccare" lo
ha protetto da 2 errori. Il suo occhio sulla fisica (frontiera energia S35/S65, short bloccato) è forte e
spesso giusto; sulle sequenze statistiche la percezione può ingannare → sempre verificare sui dati.
"non devi mai dire quando si lavora e quando no — è gestione mia."
