# STATO OVERTOP V16 — 3 giugno 2026
## Caricare all'inizio della prossima chat. Sostituisce gli stati precedenti per la parte trading.

> Claude: leggi PRIMA di toccare codice. Stato reale e verificato sui dati al 3 giugno.
> REGOLA #0: dati reali prima di modificare. File come download, mai incollare. Test runtime prima di consegnare.

---

## FILE CHE GIRANO (deploy 3giu)
- **Bot: `OVERTOP_BASSANO_V16_PRODUCTION.py` → MD5 `a08157f947d4f31ec76fa9f0466d4beb`**
- **Capsula occhio: `capsula_canvas.py` → MD5 `c24e2c90...`** (va deployata INSIEME al bot)
- riga 1 bot = `#!/usr/bin/env python3` — ~15.080 righe — Procfile: `web: python app.py`
- DB: `/var/data/trading_data.db` — PAPER — repo ZanRoberto/Tecnaria_V2
- Catena: 80f340d4 (base 2giu) → f096cbaa (+canvas che salva nascita) → **a08157f9 (+SEME_GATE)**

## LA SCOPERTA DI STANOTTE — IL CROMOSOMA-SEME (3giu)
Intuizione di Roberto, confermata sui dati: ogni trade nasce "femmina" (in perdita, debole),
poi se ha forza DIVENTA maschio e vince. Il "sesso" è già scritto nel SEME, PRIMA del trade.
DIMOSTRATO su 44 trade reali (1-2giu):
- Maschi (WIN): seme medio 0.62, n=20, +42.02$ (media +2.10)
- Femmine (LOSS): seme medio 0.49, n=24, -105.39$ (media -4.39)
- Netto -63$. IL BOT SA GUADAGNARE (+42 maschi); le femmine cancellano tutto (-105).
- "seme" = media tra primo e ultimo degli ultimi 5 seed_score (= seed_traj[0]+seed_traj[4])/2,
  letti da campo._seed_history[-5:].

### SIMULAZIONE RETROSPETTIVA (44 trade, filtro per soglia seme)
  0.50 → -2.44 | 0.55 → +0.23 | **0.60 → +6.94 (PICCO, 15/15 maschi tenuti)** | 0.65 → +0.50 (perde maschi)
  → soglia 0.60 = ultimo gradino prima di uccidere maschi. Da -63 a +6.94. PARAMETRICA.

### SEME_GATE (implementato in a08157f9)
- In `_open_shadow_position` (~riga 11700), PRIMA di aprire: calcola seme medio da _seed_history[-5:].
  Se < SEME_GATE_SOGLIA (default 0.60, regolabile via attributo) → NON apre. Log: `🚫 SEME_GATE BLOCCO femmina`.
- Filtro PROBABILISTICO non perfetto: 1 femmina con seme 0.85 è morta lo stesso; 2 maschi con seme
  basso hanno vinto. Ma il bilancio in dollari è nettamente a favore.
- VERIFICA SUI DATI NUOVI (post-deploy): PnL deve girare verso positivo (vs -63). Se blocca maschi
  o lascia passare femmine → ritarare la soglia. Il log dei blocchi è il giudice.

## CANVAS RIPARATO (occhio)
- capsula_canvas era stata persa (sovrascritta con copia del bot). RICOSTRUITA: MD5 c24e2c90.
- observe_entry funziona; observe_exit ORA riceve anche `nascita={...}` (i sensori dello shadow) e li
  salva in sensori_json della riga EXIT → ogni EXIT = trade completo (nascita+esito) SENZA aggancio
  temporale fragile. Lo shadow esiste solo per trade VERI (non per le 30k valutazioni-rumore).

## INGANNI SCOPERTI (NON costruirci sopra)
1. **range_pos è ROTTO**: saturo a 0/1 (8354 a 1.0, 7249 a 0.0) + 10287 NULL. Causa (riga 2326):
   `range_pos=(prices[-1]-low20)/(r20+0.01)` con low20/high20 sulla STESSA finestra che contiene
   prices[-1] → quando il prezzo fa un estremo, sbatte a 0 o 1. Misura male. NON usare range_pos
   come discriminatore finché non riparato.
2. **Aggancio canvas per tempo è sbagliato**: 30.173 ENTRY_VALUTAZIONE (ogni tick) vs 16 trade veri.
   NON agganciare nascita-esito per timestamp. Usare lo shadow (vedi canvas riparato sopra).
3. **seed_dir NON separa maschi/femmine** (M -0.0095 vs F -0.0139, min/max sovrapposti). La direzione
   del SEED non è il cromosoma. La direzione del PREZZO nel range sarebbe altro, ma serve range_pos sano.

## PROSSIMO CANTIERE (solo se i dati lo chiedono, DOPO aver verificato il gate)
**Cromosoma-direzione** (intuizione Roberto): un prezzo alto può salire (maschio) o ritornare giù
(femmina) — stessa posizione, sesso opposto. Plausibile ma NON ANCORA MISURABILE: serve riparare
range_pos (range ampio 50-100 tick, non la finestra corta che l'ultimo prezzo ridefinisce) e
calcolarne la derivata pulita. ATTENZIONE: range_pos alimenta il seed (peso 0.25, riga 2378) e P1
(riga 6722) → ripararlo SPOSTA la taratura del SEME_GATE → da ri-misurare la soglia 0.60 sui dati.
Sequenza: 1) deploy gate, 2) verifica gate sui dati, 3) SE serve, ripara range_pos + ritara gate +
cerca cromosoma-direzione.

## CONTESTO MERCATO (3giu)
BTC crollato da ~73k (30mag) a ~66k (2-3giu). Bot ancora prevalentemente LONG → in TRENDING_BEAR
prende mazzate LONG. Lo SHORT via drift (fix 30mag) agisce solo in RANGING, non in TRENDING_BEAR.
Da valutare in futuro: SHORT anche in trend ribassista. Ma PRIMA il gate (le femmine sono il danno
maggiore e più trasversale).

## CADAVERI NOTI (NON riattivare)
- RSI fisso 50, MACD fisso 0 (disarmati 23mag). pred mai qualificata. Se votano = cadaveri, togliere.
- ZONA_MORTA disattivata e verificata morta (ultimo 29mag, pre-deploy).
- range_pos saturo (vedi sopra) — non è un cadavere ma uno strumento difettoso, da riparare se serve.

## METODO CHE HA FUNZIONATO STANOTTE
3 intuizioni di Roberto testate sui dati: cromosoma-seme CONFERMATO (+6.94), "range_pos alto=femmina"
SMENTITO (sensore rotto), "dopo-win si perde" già smentito sessione prec. Il metodo "verifica prima
di costruire" ha evitato 2 filtri sbagliati e trovato 1 vero. Roberto: analista non-programmatore,
decide cosa/perché, Claude esegue e spiega in italiano (comportamento, non sintassi). Vuole sincerità
totale. "non dire mai quando si lavora e quando no — gestione mia."
