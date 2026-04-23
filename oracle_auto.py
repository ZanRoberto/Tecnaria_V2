"""
ORACLE AUTO — Background Worker Event-Driven
Versione: V15 Fix Definitivo | Aprile 2026

FILO LOGICO COMPLETO:
  heartbeat_data['oracle_trigger']  →  loop ogni 30s lo rileva
  → _build_context()                →  tutto: phantom, signal_tracker, narratore, zavorra
  → _call_l1()                      →  DeepSeek Risponditore: causa + semaforo SAFE/VALUTA/RISCHIO
  → _call_l2()        (se VALUTA/RISCHIO)  →  DeepSeek Superrisponditore: diagnosi + SuperCapsule JSON
  → _apply_capsule()                →  INSERT DB SQLite — attiva al prossimo tick, zero deploy
  → heartbeat_data['oracle_log']    →  dashboard mostra dialogo L1/L2 in tempo reale

API COMPATIBILE CON app.py:
  start_background(heartbeat_data, bot_instance)  — funzione modulo
  set_mode(mode)                                  — funzione modulo
  run_oracle()                                    — funzione modulo (run manuale)
  _bot_ref                                        — variabile modulo (settata da bot_thread_launcher)

REGOLA: print(flush=True) — logging.getLogger() non visibile su Render stdout
"""

import threading
import time
import json
import os
import sqlite3
import requests
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
# STDOUT — unico modo visibile su Render
# ═══════════════════════════════════════════════════════════════

def _p(msg: str):
    ts = datetime.utcnow().strftime('%H:%M:%S')
    print(f"[{ts}] [ORACLE_AUTO] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════
# STATO MODULO — variabili accessibili da app.py
# ═══════════════════════════════════════════════════════════════

_bot_ref        = None
_heartbeat      = None
_mode           = "MANUAL"
_running        = False
_last_trigger   = ""
_last_ts        = 0
_analysis_log   = []
_lock           = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# DEEPSEEK
# ═══════════════════════════════════════════════════════════════

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL     = "https://api.deepseek.com/v1/chat/completions"


def _deepseek(system: str, user: str, max_tokens: int = 600) -> str:
    if not DEEPSEEK_API_KEY:
        return "[ERRORE] DEEPSEEK_API_KEY mancante"
    try:
        r = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            },
            timeout=25,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[ERRORE_DEEPSEEK] {e}"


# ═══════════════════════════════════════════════════════════════
# API PUBBLICA — chiamata da app.py
# ═══════════════════════════════════════════════════════════════

def start_background(heartbeat_data=None, bot_instance=None):
    """Avvia il background worker. Chiamato da app.py all'avvio."""
    global _heartbeat, _bot_ref, _running

    if heartbeat_data:
        _heartbeat = heartbeat_data
    if bot_instance:
        _bot_ref = bot_instance

    _p(f"start_background: hb={_heartbeat is not None} bot={_bot_ref is not None}")

    if _running:
        _p("warning: background worker gia in esecuzione")
        return

    _running = True
    t = threading.Thread(target=_loop, daemon=True, name="OracleAutoLoop")
    t.start()
    _p("loop avviato")
    _p(f"tick mode=AUTO hb={_heartbeat is not None} trigger=nessuno")
    _p(f"Background worker avviato — modalita={_mode}")
    _p("thread avviato")


def set_mode(mode: str):
    """Cambia modalita AUTO/MANUAL. Chiamato da app.py."""
    global _mode
    mode = mode.upper()
    if mode not in ("AUTO", "MANUAL"):
        return
    old = _mode
    _mode = mode
    _p(f"Modalita: {old} -> {mode}")
    if _heartbeat is not None:
        _heartbeat["oracle_mode"] = mode


def run_oracle():
    """Run manuale — chiamato da /oracle/run_now."""
    _p("Run manuale forzato")
    hb = _get_hb()
    trigger = (hb or {}).get("oracle_trigger", "") or "MANUAL_RUN"
    return _pipeline(trigger)


# ═══════════════════════════════════════════════════════════════
# LOOP BACKGROUND
# ═══════════════════════════════════════════════════════════════

def _loop():
    global _running
    while _running:
        try:
            global _heartbeat, _bot_ref
            if _bot_ref and hasattr(_bot_ref, 'heartbeat_data'):
                _heartbeat = _bot_ref.heartbeat_data

            hb      = _get_hb()
            trigger = (hb or {}).get("oracle_trigger", "")
            mode    = _mode

            _p(f"tick mode={mode} hb={_heartbeat is not None} trigger={trigger or 'nessuno'}")

            if mode == "AUTO" and trigger:
                _p(f"Trigger rilevato: {trigger}")
                _pipeline(trigger)

        except Exception as e:
            _p(f"Errore loop: {e}")

        time.sleep(30)


# ═══════════════════════════════════════════════════════════════
# PIPELINE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

def _pipeline(trigger: str) -> dict:
    global _last_trigger, _last_ts

    t0 = time.time()
    _p(f"Pipeline avviata — trigger={trigger}")
    _last_trigger = trigger

    ctx = _build_context(trigger)

    _p("L1 Risponditore in chiamata...")
    l1 = _call_l1(ctx, trigger)
    _p(f"L1 completato — {len(l1)} chars — inizio: {l1[:150].strip()}")

    sem = _semaforo(l1)
    _p(f"Semaforo L1: {sem}")

    l2           = ""
    supercapsule = None

    if sem in ("VALUTA", "RISCHIO"):
        _p(f"L2 Superrisponditore in chiamata (semaforo={sem})...")
        l2 = _call_l2(ctx, trigger, l1)
        _p(f"L2 completato — {len(l2)} chars")

        supercapsule = _extract_capsule(l2)
        if supercapsule:
            _p(f"SuperCapsule estratta: {supercapsule.get('id','?')}")
            ok = _apply_capsule(supercapsule, trigger)
            _p(f"SuperCapsule {'inserita nel DB — attiva al prossimo tick' if ok else 'FALLITA'}")
    else:
        _p("Semaforo SAFE — nessuna azione necessaria")

    elapsed = round(time.time() - t0, 1)

    entry = {
        "ts":           datetime.utcnow().isoformat(),
        "trigger":      trigger,
        "semaforo":     sem,
        "l1":           l1,
        "l2":           l2,
        "supercapsule": supercapsule,
        "elapsed_s":    elapsed,
    }

    with _lock:
        _analysis_log.append(entry)
        if len(_analysis_log) > 20:
            _analysis_log[:] = _analysis_log[-20:]

    hb = _get_hb()
    if hb is not None:
        hb["oracle_log"]           = _analysis_log[-5:]
        hb["oracle_last_analysis"] = entry
        if hb.get("oracle_trigger") == trigger:
            hb["oracle_trigger"] = ""
            _p(f"Trigger consumato: {trigger}")

    _last_ts = time.time()
    _p(f"Pipeline completata in {elapsed}s — semaforo={sem}")
    return entry


# ═══════════════════════════════════════════════════════════════
# CONTESTO — include Phantom USDC completo per L2
# ═══════════════════════════════════════════════════════════════

def _build_context(trigger: str) -> dict:
    hb = _get_hb() or {}

    storia = hb.get("narratore_trade_storia", [])[-10:]
    stats  = hb.get("narratore_trade_stats", {})

    # Signal tracker
    st     = hb.get("signal_tracker", {})
    st_top = st.get("top", []) if isinstance(st, dict) else []
    bad_patterns = [x for x in st_top if x.get("wr", 1) < 0.55][:8]

    # Phantom — verdetti per livello di blocco in USDC
    ph         = hb.get("phantom", {})
    ph_livelli = ph.get("per_livello", {})
    verdetti   = []
    for livello, dati in ph_livelli.items():
        blk = dati.get("blocked", 0)
        win = dati.get("would_win", 0)
        los = dati.get("would_lose", 0)
        mis = round(dati.get("pnl_missed", 0), 2)
        sav = round(dati.get("pnl_saved",  0), 2)
        if blk > 0:
            wr_blk = round(win / blk * 100, 1)
            if win == 0:
                vrd = "BLOCCO_CORRETTO"
            elif wr_blk > 15:
                vrd = "BLOCCO_ECCESSIVO"
            else:
                vrd = "ZONA_GRIGIA"
            verdetti.append({
                "livello":          livello,
                "blocked":          blk,
                "would_win":        win,
                "would_lose":       los,
                "wr_bloccati_pct":  wr_blk,
                "pnl_missed_usdc":  mis,
                "pnl_saved_usdc":   sav,
                "verdetto":         vrd,
            })

    phantom_ctx = {
        "bilancio_usdc":        round(ph.get("bilancio",    0), 2),
        "protezione":           ph.get("protezione", 0),
        "zavorra":              ph.get("zavorra",    0),
        "pnl_missed_usdc":      round(ph.get("pnl_missed", 0), 2),
        "pnl_saved_usdc":       round(ph.get("pnl_saved",  0), 2),
        "verdetti_per_livello": verdetti,
    }

    stato = {
        "regime":           hb.get("regime",        "N/A"),
        "comparto":         hb.get("comparto",       "N/A"),
        "gomme":            hb.get("gomme",          "N/A"),
        "breath":           hb.get("breath_fase",    "N/A"),
        "oi_stato":         hb.get("oi_stato",       "N/A"),
        "oi_carica":        round(hb.get("oi_carica", 0), 3),
        "soglia_base":      hb.get("m2_soglia_base", 55),
        "last_score":       hb.get("m2_last_score",  0),
        "total_trades":     hb.get("m2_trades",      0),
        "wr_pct":           round(hb.get("m2_wins", 0) / max(hb.get("m2_trades", 1), 1) * 100, 1),
        "session_pnl_usdc": round(hb.get("m2_pnl", 0), 2),
        "loss_streak":      hb.get("m2_loss_streak", 0),
        "capital_usdc":     round(hb.get("capital",  10000), 2),
    }

    return {
        "trigger":      trigger,
        "stato_bot":    stato,
        "storia_trade": storia,
        "trade_stats":  stats,
        "bad_patterns": bad_patterns,
        "phantom":      phantom_ctx,
        "loss_history": hb.get("oracle_loss_history", [])[-5:],
        "win_history":  hb.get("oracle_win_history",  [])[-3:],
        "ts":           datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# L1 — RISPONDITORE
# ═══════════════════════════════════════════════════════════════

def _call_l1(ctx: dict, trigger: str) -> str:
    system = """Sei il Risponditore L1 di OVERTOP BASSANO — sistema trading BTC/USDC paper trade.
TUTTI i PnL sono in USDC. Mai BTC. Formula: pnl = delta × (5000/entry_price) - 2.00

COMPITO: diagnosi rapida del contesto.

PHANTOM:
- pnl_missed_usdc = soldi USDC mancati perché il sistema bloccava trade vincenti
- verdetto BLOCCO_ECCESSIVO = sistema troppo difensivo in quel contesto
- Questo e' denaro lasciato sul tavolo — e' la priorita'

FORMATO (max 150 parole):
[causa principale]
[osservazioni phantom + WR]
[cosa deve fare L2]
SEMAFORO: [SAFE|VALUTA|RISCHIO]"""

    user = f"""TRIGGER: {trigger}

STATO:
{json.dumps(ctx['stato_bot'], indent=2)}

PHANTOM (USDC):
{json.dumps(ctx['phantom'], indent=2)}

ULTIMI TRADE:
{json.dumps(ctx['storia_trade'][-5:], indent=2)}

PATTERN WR<55%:
{json.dumps(ctx['bad_patterns'][:5], indent=2)}"""

    return _deepseek(system, user, max_tokens=400)


# ═══════════════════════════════════════════════════════════════
# L2 — SUPERRISPONDITORE
# ═══════════════════════════════════════════════════════════════

def _call_l2(ctx: dict, trigger: str, l1: str) -> str:
    system = """Sei il Superrisponditore L2 di OVERTOP BASSANO.
TUTTI i PnL in USDC. Formula: pnl = delta × (5000/entry_price) - 2.00

COMPITO:
1. Causa radicale (phantom BLOCCO_ECCESSIVO = soldi lasciati, non trade evitati)
2. UNA correzione chirurgica
3. UNA SuperCapsule JSON tra <SUPERCAPSULE></SUPERCAPSULE>

TIPI:
- VETO: blocca entry in contesto perdente
- BOOST: abbassa soglia in contesto bloccato ma vincente
- PARAMETRO: modifica parametro numerico

FORMATO JSON:
<SUPERCAPSULE>
{
  "id": "SC_NOME_UNIX",
  "asset": "BTCUSDC",
  "livello": "AUTO",
  "tipo": "VETO|BOOST|PARAMETRO",
  "trigger": [
    {"campo": "momentum",   "op": "==", "valore": "FORTE|MEDIO|DEBOLE"},
    {"campo": "volatility", "op": "==", "valore": "ALTA|MEDIA|BASSA"},
    {"campo": "trend",      "op": "==", "valore": "UP|SIDEWAYS|DOWN"}
  ],
  "azione": {
    "tipo": "BLOCCA_ENTRY|ABBASSA_SOGLIA|ALZA_SOGLIA",
    "valore": "numero o stringa",
    "motivo": "max 50 parole italiano"
  },
  "priority": 8,
  "durata_ore": 24,
  "analisi_causale": "max 80 parole"
}
</SUPERCAPSULE>

REGOLA: se phantom BLOCCO_ECCESSIVO con pnl_missed > $20 -> BOOST, non VETO.
Il sistema deve prendere soldi, non solo evitare perdite."""

    user = f"""TRIGGER: {trigger}

DIAGNOSI L1:
{l1}

STATO:
{json.dumps(ctx['stato_bot'], indent=2)}

PHANTOM COMPLETO (USDC):
{json.dumps(ctx['phantom'], indent=2)}

PERDITE:
{json.dumps(ctx['loss_history'], indent=2)}

VINCITE:
{json.dumps(ctx['win_history'], indent=2)}

DOMANDA: cosa blocca i trade vincenti? Genera la capsule che sblocca quei soldi."""

    return _deepseek(system, user, max_tokens=900)


# ═══════════════════════════════════════════════════════════════
# ESTRAI SEMAFORO
# ═══════════════════════════════════════════════════════════════

def _semaforo(text: str) -> str:
    t = (text or "").upper()
    if "SEMAFORO: RISCHIO" in t or "SEMAFORO:RISCHIO" in t:
        return "RISCHIO"
    if "SEMAFORO: VALUTA" in t or "SEMAFORO:VALUTA" in t:
        return "VALUTA"
    if "SEMAFORO: SAFE" in t or "SEMAFORO:SAFE" in t:
        return "SAFE"
    return "VALUTA"


# ═══════════════════════════════════════════════════════════════
# ESTRAI SUPERCAPSULE
# ═══════════════════════════════════════════════════════════════

def _extract_capsule(text: str):
    if not text:
        return None
    try:
        s = text.find("<SUPERCAPSULE>")
        e = text.find("</SUPERCAPSULE>")
        if s == -1 or e == -1:
            _p("Nessuna SuperCapsule nel testo L2")
            return None
        raw = text[s + len("<SUPERCAPSULE>"):e].strip()
        cap = json.loads(raw)
        _p(f"SuperCapsule estratta: {cap.get('id','?')} tipo={cap.get('tipo','?')}")
        return cap
    except Exception as ex:
        _p(f"Parse SuperCapsule fallito: {ex}")
        return None


# ═══════════════════════════════════════════════════════════════
# APPLICA SUPERCAPSULE AL DB — zero deploy
# ═══════════════════════════════════════════════════════════════

def _apply_capsule(capsule: dict, trigger: str) -> bool:
    try:
        db_path  = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
        cap_id   = capsule.get("id", f"SC_{int(time.time())}")
        asset    = capsule.get("asset",    "BTCUSDC")
        livello  = capsule.get("livello",  "AUTO")
        tipo     = capsule.get("tipo",     "VETO")
        priority = capsule.get("priority", 7)
        durata_h = capsule.get("durata_ore", 24)
        analisi  = capsule.get("analisi_causale", trigger)[:200]

        trigger_json = json.dumps(capsule.get("trigger", []))
        azione_json  = json.dumps(capsule.get("azione",  {}))
        scade_ts     = time.time() + durata_h * 3600

        conn = sqlite3.connect(db_path, timeout=10)
        c    = conn.cursor()

        if c.execute("SELECT id FROM capsule WHERE id=?", (cap_id,)).fetchone():
            _p(f"Capsule {cap_id} gia presente — skip")
            conn.close()
            return False

        c.execute("""
            INSERT INTO capsule
              (id, asset, livello, tipo, trigger_json, azione_json,
               priority, enabled, samples, wr, pnl_avg,
               created_ts, scade_ts, hits, hits_saved, last_hit_ts, note)
            VALUES (?,?,?,?,?,?,?,1,0,0.0,0.0,?,?,0,0,0,?)
        """, (
            cap_id, asset, livello, tipo,
            trigger_json, azione_json,
            priority,
            time.time(), scade_ts,
            f"OracleAuto|trigger={trigger}|{analisi}"
        ))
        conn.commit()
        conn.close()

        _p(f"Capsule {cap_id} inserita — attiva al prossimo tick — zero deploy")

        hb = _get_hb()
        if hb is not None:
            sc_log = hb.get("supercapsule_log", [])
            sc_log.append({
                "ts":      datetime.utcnow().isoformat(),
                "id":      cap_id,
                "tipo":    tipo,
                "trigger": trigger,
                "analisi": analisi[:80],
            })
            hb["supercapsule_log"] = sc_log[-10:]

        return True

    except Exception as ex:
        _p(f"Apply capsule errore: {ex}")
        return False


# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════

def _get_hb():
    global _heartbeat, _bot_ref
    if _bot_ref and hasattr(_bot_ref, "heartbeat_data"):
        return _bot_ref.heartbeat_data
    return _heartbeat


def get_log() -> list:
    with _lock:
        return list(_analysis_log)


def get_status() -> dict:
    return {
        "mode":         _mode,
        "running":      _running,
        "last_trigger": _last_trigger,
        "last_ts":      _last_ts,
        "n_analyses":   len(_analysis_log),
    }
