# 🦊 V16_INDEX.md — MAPPA OPERATIVA OVERTOP BASSANO V16

**Owner:** Roberto Zandonella (Tecnaria, Bassano del Grappa)
**Repo:** `ZanRoberto/Tecnaria_V2` (branch `main`)
**Live:** https://tecnaria-v2.onrender.com
**Modalità:** PAPER trading BTC/USDC (Binance WS) — NO ordini reali
**File principale:** `/opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py` (~9991 righe post-patch 10mag)
**Wrapper Flask:** `/opt/render/project/src/app.py` (Mission Control V6.0)
**DB:** `/var/data/trading_data.db` (SQLite)

---

## ⚡ COME USARE QUESTO INDEX

**OGNI sessione Claude inizia così, in ordine, sempre:**

1. Roberto carica QUESTO FILE (`V16_INDEX.md`)
2. Roberto carica il bridge della sessione precedente (`BRIDGE_<data>.md`)
3. Roberto incolla screenshot della dashboard live (`https://tecnaria-v2.onrender.com/`)
4. Roberto scrive UNA frase: cosa vuole fare in questa sessione
5. Claude conferma di aver letto e propone un piano

**Se Claude salta uno di questi passi → STOP, ricomincia.**

---

## 🎯 PATTI OPERATIVI NON NEGOZIABILI

1. **Roberto sa più di Claude su questo sistema.** Sempre.
2. **Niente manopole arbitrarie.** Tutto deve emergere dai dati del sistema.
3. **Una modifica alla volta.** Mai impacchettare 3 fix in un deploy.
4. **Prima diagnosi dai log/dashboard reali, poi proposta.** Mai patch al buio.
5. **Quando sbaglia, Claude lo dice subito.** Roberto rispetta l'errore onesto.
6. **Italiano diretto.** Roberto in MAIUSCOLO è enfatico, non arrabbiato.
7. **Volpe non mucca.** Adattivo, leggero, profondo. Mai forza bruta.
8. **Sessioni 1-2 ore max.** Oltre, Claude degrada.
9. **UN obiettivo per sessione.** Finito quello, si chiude.
10. **Screenshot dashboard prima di patchare.** Sempre.

### Trigger speciali che Roberto può scrivere:
- `"volpe"` → protocollo rigore: zero modifiche senza bug provato dai log
- `"claudelove"` → analisi 4 livelli (Architettura → Coerenza Fisica → Opportunità → Bug)
- `"V16 STATO"` → riprendi dal bridge corrente

---

## 🏗️ ARCHITETTURA — MAPPA CLASSI E LORO POSIZIONE

Tutte le righe sono indicative del file post-patch 10mag (9991 righe totali).

### CLASSI PRINCIPALI

| Riga | Classe | Cosa fa |
|---|---|---|
| 117 | `StabilityTelemetry` | Telemetria stabilità (⚠️ rotta in heartbeat, vedi note) |
| 404 | `CapsuleRuntime` | Runtime capsule attive |
| 501 | `ConfigHotReloader` | Reload a caldo configurazione capsule |
| 527 | `IntelligenzaAutonoma` | Capsule manager principale (file JSON + DB) |
| 1286 | `CapsuleIntelligente` | "Sistema immunitario predittivo" — legge precursori |
| 1773 | `LogAnalyzer` | Analisi log per pattern |
| 1801 | `AIExplainer` | Spiega trade (AI narratives) |
| 1851 | `SeedScorer` | Score sequenziale 7 feature, traiettoria ultimi tick |
| 1969 | `OracoloDinamico` | Memoria fingerprint con WR pesato (decay 0.95) |
| 2524 | `PreTradeSignalTracker` | Traccia ogni segnale ≥ soglia (anche bloccati) |
| 2794-2829 | `Capsule1-5` | 5 tipi capsule statiche (Coerenza, Trappola, Protezione, Opportunità, Tattica) |
| 2842 | `MatrimonioIntelligente` | 7 matrimoni (mom×vol×trend → name+wr+duration) |
| 2899 | `MemoriaMatrimoni` | Divorzi e separazioni |
| 2961 | `ContestoAnalyzer` | Classifica contesto live |
| 3069 | `PersistenzaStato` | Save/load brain (oracolo, memoria, calibratore, signal_tracker) |
| 3457 | `RegimeDetector` | RANGING / TRENDING / EXPLOSIVE con confidence |
| 3593 | `MomentumDecelerometer` | DECEL detection |
| 3655 | `PositionSizer` | Sizing dinamico |
| 3721 | `AutoCalibratore` | Calibrazione soglie da log reali |
| 3906 | `CampoGravitazionale` | Engine principale entry/score (M2) |
| 4713 | `VeritatisTracker` | "Chi aveva ragione?" — 60s after verification |
| 4971 | `SuperCervello` | Voto multi-organo (campo, oracolo, signal, matrimonio, phantom) |
| **5132** | **`OvertopBassanoV16Production`** | **CLASSE PRINCIPALE BOT** |

### V16 ENGINES (estensioni da V15)

| Riga | Componente | Cosa fa |
|---|---|---|
| 5444 | `_comparto` (CompartoEngine) | 5 preset mercato (DIFENSIVO/NEUTRO/ATTACCO/TRENDING_BULL/TRENDING_BEAR) |
| 5445 | `_nerv` (NervosismoEngine) | Gomme: SLICK / INTER / RAIN basato su volatilità |
| 5446 | `_breath` (BreathEngine) | Fase respiro mercato: INALAZIONE / PICCO / ESALAZIONE / NEUTRO |
| 5210 | `telemetry` (StabilityTelemetry) | ⚠️ Rotto in heartbeat (vedi sezione bug) |
| 5231 | `ci` (CapsuleIntelligente) | Sistema predittivo che legge precursori |

---

## 🛠️ FUNZIONI OPERATIVE PRINCIPALI

### Entry (Motore 2 Shadow)
| Riga | Funzione | Cosa fa |
|---|---|---|
| 7436 | `_evaluate_shadow_entry` | Valuta possibile entry — contiene i 7 GATE (vedi sotto) |
| 7919 | `_evaluate_shadow_exit` | Valuta exit (DECEL, SMORZ, HARD_STOP, ecc.) |
| 8341 | `_close_shadow_trade` | Chiude shadow trade |

### I 7 GATE DI BLOCCO (in ordine di esecuzione)
Tutti dentro `_evaluate_shadow_entry` (riga 7436+):

| # | Gate | Riga | Trigger blocco | Phantom registrato? |
|---|---|---|---|---|
| 1 | SEED_INSUFFICIENTE | 7461 | seed score < soglia minima | ❌ no |
| 2 | VERITAS_GATE | 7479 | contesto WR<20% E pnl_avg<-1.0 (n≥10) | ✅ post-patch 10mag |
| 3 | LEARNED_GATE | 7510 | capsule manager `valuta()` ritorna blocca | ✅ post-patch 10mag |
| 4 | PERCORSO1_CM | ~7560 | capsule manager percorso 1 | ✅ |
| 5 | PERCORSO1_VETO | ~7615 | veto generico percorso 1 | ✅ |
| 6 | RESULT_VETO | ~7669 | veto su result | ✅ |
| 7 | SCORE_INSUFFICIENTE | ~7689 | score < soglia | ✅ |
| 8 | SC_BLOCCA | ~7772 | SuperCervello dice no | ✅ |

**TOTALE chiamate a `_record_phantom`: 7** (post-patch 10mag, era 5 in V15 + V16-pre-patch).

### Phantom Tracker
| Riga | Funzione | Cosa fa |
|---|---|---|
| 8876 | `_record_phantom` | Apre fantasma virtuale al blocco |
| 8919 | `_update_phantoms` | Aggiorna phantom ad ogni tick |
| 9013 | `_close_phantom` | Chiude phantom, calcola pnl_netto, aggrega `_phantom_stats` |
| 9112 | `_get_phantom_summary` | Ritorna dict per dashboard (total, per_livello, verdetto) |

### Heartbeat
| Riga | Funzione | Cosa fa |
|---|---|---|
| 9404 | `_update_heartbeat` | **PATCH 10mag**: ora usa `_hb_set(key, fn)` per scritture singole protette. Una chiave rotta non distrugge le altre. Errori loggati come `[HB_KEY:nome]`. |

### Main Loop
| Riga | Funzione | Cosa fa |
|---|---|---|
| 9943 | `run` | Loop principale del bot |

---

## 🌐 ENDPOINT FLASK (app.py)

L'app.py contiene:
- Route HTTP che leggono `heartbeat_data` (dict condiviso col thread bot)
- Dashboard HTML interattiva (riga 3779)

### Endpoint principali
| URL | Cosa fa |
|---|---|
| `/` | Dashboard HTML completa (la mostra Roberto a Claude via screenshot) |
| `/trading/status` | JSON: `{heartbeat: {...}, metrics: {...}, suggestions: [...], trades: [...]}` |
| `/diagnostic` | Diagnostica HTML (⚠️ ha riferimenti hardcoded a V15, cosmetici) |
| `/health` | Health check |
| `/supervisor` | DeepSeek supervisor |
| `/oracle` | Oracolo AI |
| `/partite` | Libro delle partite (EVITA vs MIGLIORA) |

**NB:** `/heartbeat` NON ESISTE come URL — `heartbeat` è solo il dict interno esposto dentro `/trading/status`.

---

## 📊 LA DASHBOARD HTML — COMPONENTI VIVI

Roberto vede sotto gli occhi questi componenti (Claude SOLO via screenshot):

### Header
- Status (RUNNING/STOPPED), modalità (PAPER/LIVE), prezzo BTC live
- WR, capitale, trade M2, soglia attiva, regime, state

### Sezioni
1. **MOTORE 2 — CAMPO GRAVITAZIONALE**: WR, PnL, RSI, MACD, soglia base, drift
2. **Live Log M2**: ultimi blocchi/eventi
3. **ORACOLO DINAMICO**: tabella fingerprint con WR%, campioni, pnl_avg, ★ contesti ricorrenti
4. **INTELLIGENZA AUTONOMA — Capsule Vive**: STATIC/AUTO/LEARNED conteggi
5. **LATENCY TRACKER**: slippage decisione→esecuzione (per VPS Frankfurt decision)
6. **NARRATORE AI**: dialogo con DeepSeek (richiede API key)
7. **V16 — ASSETTO MERCATO**: COMPARTO + GOMME + BREATH FASE
8. **PHANTOM SUPERVISOR — Autocorrezione**: 🔥 il sistema **legge il phantom e si autocorregge** irrigidendo soglie
9. **PHANTOM PER COMPONENTE**: WR per gate
10. **PHANTOM**: Bilancio totale (bloccati, protetti, mancati, salvati$, persi$)
11. **IL GENERALE — AI BRIDGE**: bridge predittivo (CARICA, soglia 0.65)
12. **GRAFICO LIVE BTC/USDC**: 180 tick
13. **SUPERCERVELLO**: mercato vs predizione, scostamento, calibrazione magnitudine
14. **VERITAS**: chi aveva ragione (oracolo vs SC vs phantom)
15. **MOTORE PREVISIONALE — Signal Tracker**: contesto × N × HIT 60s × Δ avg × PnL sim
16. **ECONOMIC EDGE**: hit_economica % che copre fee reali
17. **DECISIONI BOT — Live Log**: stream eventi
18. **LOG M2**: log Motore 2
19. **ULTIMI TRADE**: tabella trade chiusi
20. **ANALISI TRADE**: AI explainer

---

## 🗄️ DATABASE (`/var/data/trading_data.db`)

### Tabelle principali

| Tabella | Schema | Note |
|---|---|---|
| `trades` | id, timestamp, event_type, asset, price, size, pnl, direction, reason, data_json | Storico trade reali |
| `bot_state` | key, value (BLOB JSON) | runtime_state, oracolo, signal_tracker, memoria, calibra_params |
| `capsule` | id, livello, contesto, action, scade_ts, hits, last_hit_ts, stato_vita, ... | STATIC + LEARNED + AUTO |
| `veritas_stats` | key, n, hit, pnl_sum | FUOCO\|*, CARICA\|* |
| `veritas_closed` | (vari) | Segnali chiusi 60s after |
| `capsule_log` | (vari) | Log azioni capsule |
| `capsule_permanenti` | (vari) | Sospensione fantasma (vuota — vedi bug) |

### Capsule attualmente in DB (al 10mag pomeriggio)
- 8 STATIC (permanenti)
- 6 LEARNED (scadenza estesa 9 giugno)
- 2 AUTO (scadenza estesa 9 giugno)
- **Hits massimi:** LEARNED_MAT_TOSSICO_WEAK_NEUTRAL = 377

---

## 💰 PARAMETRI ECONOMICI (riga 5151+)

```
TRADE_SIZE_USD = $1000   (margine)
LEVERAGE       = 5
EXPOSURE       = $5000
FEE_PCT        = 0.0002  (0.02% maker)
FEE_TRADE      = $2.00   (fissi)
STOP_LIVE      = $7      (stop lordo)
MIN_PNL_EDGE   = $2.50
PROFIT_LOCK    = 40%
```

### PnL formula (5x leverage):
```
delta = (price_exit - price_entry) per LONG
btc_qty = EXPOSURE / price_entry
pnl_lordo = delta * btc_qty
pnl_netto = pnl_lordo - FEE_TRADE  (al close, una sola volta)
```

⚠️ **DUBBIO APERTO PHANTOM (10mag):** dentro `_close_phantom` (riga 9013) le fee sembrano sottratte due volte (`total_fees = $4` dentro pnl, poi `_ph_fee_final = $2` ancora). **Da verificare in mercato vivo (EXPLOSIVE/TRENDING) — in RANGING piatto causa WR=0% sistematico, ma l'oracolo conferma che quei contesti perdono davvero, quindi il phantom dice il vero, solo gonfiato.** Esisteva anche in V15.

---

## 🔥 STATO POST-PATCH 10MAG (BASELINE ATTUALE)

### Cosa è stato fixato il 10 maggio 2026:
1. ✅ Schema capsule: aggiunti `last_hit_ts REAL`, `stato_vita TEXT` (mattina)
2. ✅ Capsule LEARNED+AUTO: scadenze estese al 9 giugno 2026
3. ✅ Narratore: vergine al boot (storico DeepSeek non ricaricato)
4. ✅ Heartbeat resiliente: scritture singole `_hb_set(key, fn)` invece di `update({...})` monolitico
5. ✅ Phantom esposto su 7 gate (era 5 — mancavano VERITAS_GATE e LEARNED_GATE)

### Cosa funziona alla grande:
- Phantom Supervisor autocorregge VERITAS_GATE (irrigidisce soglie automaticamente)
- 128 phantom registrati in pochi minuti dopo deploy
- $503 "salvati" virtualmente (gonfiato da doppia-fee, ma direzione corretta)
- LEARNED_GATE blocca massicciamente RANGE_DEAD in mercato piatto

### Cosa è rotto / da osservare:
- ⚠️ `telemetry` (StabilityTelemetry) crasha sempre nell'heartbeat — chiave isolata, non blocca nulla
- 🤔 Doppia-fee phantom — esisteva anche in V15, da decidere dopo mercato vivo
- 🟢 VETO_TOSSICO appare — gate originale, blocca correttamente

### Cosa NON fare adesso:
- ❌ NON cancellare capsule LEARNED (16631+ trade di apprendimento dietro)
- ❌ NON modificare runtime_state direttamente
- ❌ NON implementare DORMIENZA prima di altro
- ❌ NON rinominare il file principale (app.py importa quel nome esatto)
- ❌ NON fidarsi dei commenti del codice senza verifica grep

---

## 🦊 GLOSSARIO TERMINI ROBERTO

Termini specifici di questo progetto, da NON confondere con concetti generici:

| Termine | Significato in V16 |
|---|---|
| **Matrimonio** | Combinazione (mom × vol × trend) classificata in 7 nomi (es. WEAK_NEUTRAL, RANGE_DEAD, FORTE|BASSA|UP) |
| **Divorzio** | Cancellazione permanente di un matrimonio dopo statistiche pessime |
| **Fingerprint** | (mom × vol × trend × direction) — granularità più fine del matrimonio |
| **Capsula** | Regola di trading appresa: STATIC (codice), LEARNED (DeepSeek da loss), AUTO (auto-generata) |
| **EVITA vs MIGLIORA** | Classificazione di una loss: bloccare contesto per sempre, o aggiustare entry/exit/threshold |
| **SMORZ** | Smorzamento: chiusura per energia che muore (momentum debole) |
| **DECEL** | Deceleration: chiusura per perdita di velocità prezzo |
| **HARD_STOP** | Stop loss lordo $7 |
| **Carica** | Energia accumulata pre-breakout (0.0-1.0) |
| **FUOCO** | Stato carica > 0.80 |
| **PB / Pre-Breakout** | Compressione + segnali → esplosione imminente |
| **PERCORSO1 / PERCORSO2** | Due percorsi decisionali nel SeedScorer |
| **Veritas** | Tracker che 60s dopo verifica chi aveva ragione (oracolo vs SC vs phantom) |
| **SuperCervello (SC)** | Voto multi-organo finale (campo + oracolo + signal + matrimonio + phantom) |
| **Bridge predittivo** | "IL GENERALE" — analisi cross-organo ogni 5s, soglia carica 0.65 |
| **Phantom Supervisor** | 🔥 Sistema che legge il phantom e autocorregge le soglie dei gate |
| **Volpe non mucca** | Filosofia: adattivo > forza bruta, intelligenza > parametri fissi |
| **CONTESTO_INVALICABILE** | Blocco assoluto su DEBOLE/MEDIO/FORTE in ALTA|SIDEWAYS |
| **EXPLOSIVE_OVERRIDE** | Cattura finestra 60-90s di breakout vero |
| **Comparto** | Preset mercato (DIFENSIVO/NEUTRO/ATTACCO/TRENDING_BULL/BEAR) |
| **Gomme** | Tipo di assetto: SLICK (asciutto/calmo) / INTER (medio) / RAIN (volatile) |
| **Breath fase** | Respiro mercato: INALAZIONE / PICCO / ESALAZIONE / NEUTRO |

---

## 🎯 PROSSIMI PASSI DOCUMENTATI

In ordine di priorità (decisi nelle sessioni passate):

1. **Osservare phantom in mercato vivo** (EXPLOSIVE/TRENDING) — verificare doppia-fee
2. **Capire perché bot entra su contesti tossici ★** — DEBOLE|ALTA|SIDEWAYS ★32 con WR=3% reale
3. **Post-Trade Energy Tracker** — dopo ogni win, registrare energia residua per pre-seed prossimo trade
4. **Fix telemetry** — quando si vorrà (non urgente, isolata grazie a heartbeat resiliente)
5. **DORMIENZA capsule** — capsule non cancellate ma indicizzate, risvegliate al riconoscere pattern

---

## 🛠️ COMANDI OPERATIVI PRONTI

### Verifica patch attive
```bash
grep -c "self._record_phantom(" /opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py
# Atteso: 7 (post-patch 10mag)
```

### Phantom check completo
```bash
curl -s https://tecnaria-v2.onrender.com/trading/status | python3 -c "
import sys, json
from datetime import datetime
d = json.load(sys.stdin)
hb = d.get('heartbeat', {})
m2_trades = hb.get('m2_trades', 0)
m2_wins = hb.get('m2_wins', 0)
m2_losses = hb.get('m2_losses', 0)
m2_pnl = hb.get('m2_pnl', 0)
regime = hb.get('regime', '?')
ph = hb.get('phantom', {})
total_blocked = ph.get('total', 0)
per_livello = ph.get('per_livello', {})
totale_eventi = m2_trades + total_blocked
tasso_op = (m2_trades/totale_eventi*100) if totale_eventi else 0
tasso_bk = (total_blocked/totale_eventi*100) if totale_eventi else 0
print(f'PHANTOM CHECK {datetime.now().strftime(\"%H:%M:%S\")}')
print(f'Regime: {regime} | Trades: {m2_trades} (W{m2_wins}/L{m2_losses} \${m2_pnl:+.2f}) | Bloccate: {total_blocked}')
print(f'Operatività: {tasso_op:.0f}% | Strangolamento: {tasso_bk:.0f}%')
for k, v in sorted(per_livello.items(), key=lambda x: -x[1].get('blocked', 0)):
    b = v.get('blocked', 0); ww = v.get('would_win', 0); wl = v.get('would_lose', 0)
    net = v.get('pnl_saved', 0) - v.get('pnl_missed', 0)
    chiusi = ww + wl
    wr = (ww/chiusi*100) if chiusi else 0
    print(f'  {k[:30]:30s} blk:{b:4d} chiusi:{chiusi:4d} WR:{wr:3.0f}% net:\${net:+.1f}')
"
```

### Stato bot rapido
```bash
curl -s https://tecnaria-v2.onrender.com/trading/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
hb = d.get('heartbeat', {})
print(f\"Regime: {hb.get('regime','?')} | State: {hb.get('m2_state','?')} | Trades: {hb.get('m2_trades',0)} | Score: {hb.get('m2_last_score',0)}/{hb.get('m2_last_soglia',0)}\")
"
```

### Query DB rapide
```bash
# Ultimo trade
sqlite3 /var/data/trading_data.db "SELECT timestamp, direction, price, pnl, reason FROM trades ORDER BY id DESC LIMIT 1;"

# Capsule attive
sqlite3 /var/data/trading_data.db "SELECT livello, COUNT(*), SUM(hits) FROM capsule WHERE enabled=1 GROUP BY livello;"

# Trade ultime 24h
sqlite3 /var/data/trading_data.db "SELECT COUNT(*), SUM(pnl) FROM trades WHERE timestamp > datetime('now','-24 hours');"
```

---

## 📁 ALTRI FILE NEL REPO

- `app.py` — Mission Control (Flask + dashboard HTML)
- `capsule_manager.py` — gestione capsule
- `comparto_engine.py`, `nervosismo_engine.py`, `breath_engine.py` — engines V16
- `oracle_auto.py` — DeepSeek L1+L2 (legge narratore_trade_storia)
- `supervisor_new.py` — DeepSeek cross-asset

---

## 🔚 FINE INDEX

**Aggiornato il:** 10 maggio 2026, sera
**Ultima patch:** 10mag — heartbeat resiliente + phantom su VERITAS_GATE/LEARNED_GATE
**Prossima azione attesa:** osservare phantom in mercato vivo (regime EXPLOSIVE)

🦊 *Volpe non mucca.*
