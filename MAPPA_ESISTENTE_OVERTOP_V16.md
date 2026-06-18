# MAPPA DELL'ESISTENTE — OVERTOP V16

**Fonte:** `OVERTOP_BASSANO_V16_PRODUCTION.py` (build vivo `943e0ccd`) + `app.py`.
**Scopo:** fotografare cosa esiste GIÀ — campi, formule, chi decide — così la prossima
istanza NON riparte cieca. Nessuna proposta, nessuna soglia, nessuna patch. Solo: cosa
c'è, dove (riga), e cosa fa davvero.

Tutte le righe si riferiscono al motore vivo, salvo dove indicato `app.py`.

---

## 1. I DUE SISTEMI-SEME (non confonderli — è la causa di metà della confusione)

Esistono **due numeri diversi**, entrambi chiamati informalmente "seme":

| | Sistema A — SeedScorer | Sistema B — seme_entry / seed_history |
|---|---|---|
| Cos'è | score 0..1 a 7 feature | media `(s[0]+s[-1])/2` degli ultimi 5 di `campo._seed_history` |
| Dove | classe `SeedScorer.score()` r.2382-2469 | calcolato a r.12906 |
| Soglia | `SEED_ENTRY_THRESHOLD = 0.45` (r.138) | ~0.60 (SEME_GATE) |
| Lo usa | produce i 7 campi `nascita_*` | il pannello ⚠ (`app.py` r.613-624) |

> **Il "seme 0.717" che vedi nel pannello è il Sistema B, NON lo score dello SeedScorer.**
> Quando si parla di "il seed non separa" (484 ibridi) si parla del Sistema B.

---

## 2. I 7 CAMPI DI NASCITA (`n_*`) — misurati PRIMA del trade

**Catena:** `SeedScorer.score()` (r.2459-2467) → snapshot in `_shadow["nascita_*"]`
all'apertura (r.12922-12929) → salvati come `n_*` nel record alla chiusura (r.14839-14845).

| campo | formula (in parole) | range | calcolo | DECIDE? |
|---|---|---|---|---|
| **vol_pressure** | vol medio 5 tick / vol medio 15 tick. >1 = volume in accumulo | ~0–4 | r.2426 | **SÌ** — CROMO (r.12239) + VOL_ISTERICO (r.12510) |
| **compression** | range 5 / range 10. basso = molla che si carica | 0–~1 | r.2405 | **SÌ** — CROMO (r.12240, `_cp > MAX`) |
| **comp_duration** | n. tick consecutivi col range stretto | 0–20 | r.2440 | **SÌ** — CROMO (r.12241) |
| **range_pos** | dove sta il prezzo nel range di 20 tick | 0–1 | r.2399 | logged. ⚠ **NOTA codice r.14835: "è ROTTO (saturo 0/1)"** |
| **drift_persist** | % tick positivi sugli ultimi 10 | 0–1 | r.2413 | letto in locale r.8101 — gating da confermare |
| **drift_slope** | media drift 5 − media drift 15 (drift che accelera) | piccolo | r.2435 | letto r.8105. ⚠ **NOTA codice r.14836: "lead debole"** |
| **sign_flips** | n. cambi di segno su 20 tick. basso = coerente | 0–20 | r.2418 | entra solo nel total dello SeedScorer (flip_score) |

### Il volume di Roberto = `vol_pressure`, e CHI lo usa: CROMO
`CROMO` (gate r.12234, `if CROMO_GATE_ON`) blocca l'aggancio se:
`vol_pressure < CROMO_VPRESS_MIN` **oppure** `vol_pressure > CROMO_VPRESS_MAX`
**oppure** `compression > CROMO_COMP_MAX` **oppure** `comp_duration` troppo breve.

> **IPOTESI FORTE (da confermare):** i **12358 "vol↓" bloccati** del pannello sono
> CROMO che scarta su `vol_pressure < MIN`. Cioè il bot **scansa già sul volume**.
> Da verificare: (a) `CROMO_GATE_ON` è davvero on sul vivo? (b) il contatore "vol↓"
> del pannello è alimentato da CROMO? — vanno lette le ENV vive e il punto in `app.py`
> che incrementa quel contatore. NON ancora confermato dal codice da solo.

---

## 3. ENTRY vs CLOSE — quali campi sono firma d'ingresso e quali NO

Misurati **all'INGRESSO** → affidabili come firma:
`momentum`, `volatility` (analyzer all'aggancio) · i 7 `nascita_*` (snapshot r.12922) ·
`seme_entry` / `seed` · `entry_price`.

Misurati **alla CHIUSURA** → **NON usare come firma d'ingresso** (circolari):
`regime` (`_regime_current`, scritto r.14800) · `is_win`/`pnl` · `reason` ·
`peak_pnl` · `duration` · `pnl_10s` / `pnl_20s`.

> **Trappola del regime:** un LONG che perde → il prezzo è sceso → alla chiusura il
> regime legge `TRENDING_BEAR`. La perdita ha CAUSATO l'etichetta, non il contrario.
> Raggruppare le perdite per `regime` è circolare. Già caduti in questo errore.

---

## 4. UNITÀ — netto vs lordo (altra fonte di equivoci)

- Colonna **PNL** del pannello = `trades.pnl` = **NETTO** (fee già tolta). Motore r.14789 + r.14803 (`pnl_netto`).
- Colonna **10S** del pannello = `pnl_10s` = **LORDO** (`current_pnl`, fee dentro). Motore r.13853.
- Fee per trade = `TRADE_SIZE × LEVERAGE × FEE_PCT × 2` = **2.00$**.
- Conseguenza: un "+1.29 a 10s" è **−0.71 NETTO**. Il grasso sotto +2 lordo è fee travestita.

---

## 5. NOTE ONESTE GIÀ SCRITTE NEL CODICE (da non riscoprire ogni volta)

- r.14835 — `nascita_range_pos` è **ROTTO** (saturo 0/1).
- r.14836 — `drift_slope` / `seed_dir` sono **lead deboli**.
- r.10058 — bug pre-esistente: SeedScorer ritorna `vol_pressure`, non `vol_accel`.
- r.2386 — "WR 77.8%" è da **SIMULAZIONE** (`cavalca_curva`), NON dal vivo.

---

## 6. INGRESSO — sintesi cancelli (dettaglio nell'audit separato)

Due funzioni in serie: `_evaluate_shadow_entry` (decide se tentare) →
`_open_shadow_position` (ritardo/purgatorio/gate/apertura). **40 `return`** totali,
**35 ENV** distinte. Unico punto di nascita del trade: `self._shadow = {` a r.12903,
preceduto da GATE PEAK (r.12832). Tutte le porte passano di lì.

Cancelli che usano i campi di nascita: **CROMO** (vol_pressure, compression, comp_duration).
Cancello sul picco d'osservazione: **GATE PEAK** (legge `_rit_picco_pre`, soglia
`GATE_PEAK_USD` — misurata in **LORDO**).

---

## 7. COSA NON È ANCORA DOCUMENTATO (onestà — buchi della mappa)

- Se `drift_persist` / `range_pos` / `drift_slope` gating davvero, oltre a essere loggati.
- Il punto esatto in `app.py` che incrementa i contatori "vol↓ / vol↑ / comp" del pannello.
- Quali ENV CROMO sono attive sul vivo (`CROMO_GATE_ON`, `CROMO_VPRESS_MIN/MAX`, `CROMO_COMP_MAX`).
- Il rapporto tra SeedScorer (Sistema A) e seed_history (Sistema B): chi alimenta cosa.

Questi 4 punti sono il prossimo lavoro di documentazione, prima di qualunque modifica.
