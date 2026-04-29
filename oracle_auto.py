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

    sem = _semaforo(l1, trigger)
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
    system = (
        "Sei il Risponditore L1 di OVERTOP BASSANO V15 — sistema AI trading BTC/USDC.\n\n"
        "LEGGI FISICHE:\n"
        "- PnL in USDC: pnl_netto = (delta x 5000/entry_price) - 2.00\n"
        "- FEE FISSA $2. BREAKEVEN ~$30 movimento BTC.\n"
        "- PROFIT_LOCK WIN_+1/WIN_+2 = perdita netta certa (lordo < fee).\n\n"
        "PATTERN TOSSICI:\n"
        "- DEBOLE|ALTA|SIDEWAYS: WR 11% pnl -$3.29 — il piu tossico\n"
        "- MEDIO|ALTA|SIDEWAYS: WR 28% pnl -$2.79\n"
        "- RANGING + volatilita ALTA: edge fisico negativo\n"
        "- FUOCO bypass in RANGING con loss_streak>=2: entry cieche\n\n"
        "PATTERN VINCENTI:\n"
        "- FORTE|BASSA|UP: WR 78% — non bloccare mai\n"
        "- FORTE|MEDIA|UP: WR 68%\n"
        "- MEDIO|BASSA|UP: WR 65%\n\n"
        "PHANTOM:\n"
        "- pnl_missed = soldi lasciati (blocchi eccessivi) — priorita assoluta se > $20\n"
        "- pnl_saved = perdite evitate (blocchi corretti)\n"
        "- BLOCCO_ECCESSIVO = wr_bloccati > 15%\n\n"
        "SEMAFORO:\n"
        "SAFE = blocchi corretti, phantom zero missed, nessuna anomalia\n"
        "VALUTA = score sotto soglia entra, PROFIT_LOCK perde, pattern tossico non bloccato\n"
        "RISCHIO = loss_streak>=3, FUOCO bypass in RANGING, DEBOLE|ALTA|SIDEWAYS non bloccato\n\n"
        "FORMATO (max 200 parole):\n"
        "Causa: [1 frase]\n"
        "Phantom: [protezione/zavorra/mancato]\n"
        "Pattern: [fingerprint problema]\n"
        "Per L2: [cosa correggere]\n"
        "SEMAFORO: [SAFE|VALUTA|RISCHIO]"
    )

    user = (
        f"TRIGGER: {trigger}\n\n"
        f"STATO BOT:\n{json.dumps(ctx['stato_bot'], indent=2)}\n\n"
        f"PHANTOM (USDC):\n{json.dumps(ctx['phantom'], indent=2)}\n\n"
        f"ULTIMI TRADE:\n{json.dumps(ctx['storia_trade'][-5:], indent=2)}\n\n"
        f"PERDITE RECENTI:\n{json.dumps(ctx['loss_history'], indent=2)}\n\n"
        f"PATTERN WR<55%:\n{json.dumps(ctx['bad_patterns'][:5], indent=2)}"
    )

    return _deepseek(system, user, max_tokens=500)


def _call_l2(ctx: dict, trigger: str, l1: str) -> str:
    system = """Sei il Superrisponditore L2 di OVERTOP BASSANO V15 — il chirurgo del sistema.
Hai piena conoscenza dell'architettura. Agisci con precisione assoluta.

═══ LEGGI FISICHE (NON VIOLARE MAI) ═══
- PnL in USDC: pnl_netto = (delta × 5000/entry_price) - 2.00
- FEE = $2 fissi. BREAKEVEN = ~$30 movimento BTC. Sotto questo: perdita certa.
- EXPOSURE = $5000. BTC_QTY = 5000/entry_price.
- PROFIT_LOCK su WIN_+1 (+$1 lordo) = -$1 netto. È una perdita. La soglia minima di uscita deve essere WIN_+3 ($3 lordo = $1 netto).

═══ COME FUNZIONA IL CAPSULE_MANAGER ═══
Il CapsuleManager legge ogni tick il contesto: {momentum, volatility, trend, regime, direction, ...}
e confronta con i trigger della capsule. Se match → esegue l'azione.

AZIONI DISPONIBILI (usa ESATTAMENTE questi valori nel campo azione.tipo):
- "BLOCCA_ENTRY" → blocca entry in quel contesto (come VETO)
- "ALZA_SOGLIA"  → aggiunge N punti alla soglia di entrata (es. valore: 10 = soglia+10)
- "ABBASSA_SOGLIA" → sottrae N punti alla soglia di entrata (es. valore: 5 = soglia-5)
- "ALZA_SOGLIA_USCITA" → imposta il PnL lordo MINIMO per chiudere in profitto
  valore: numero float (es. 2.5 = non chiudere mai sotto $2.50 lordo)
  REGOLA FEE: fee fissa = $2.00. Breakeven = $2.00. Profitto minimo = $2.50 lordo ($0.50 netto).
  Usa quando vedi PROFIT_LOCK_WIN_+1 o WIN_+2 che chiudono in perdita netta.
  Esempio: valore=2.5 significa "non uscire mai con profitto lordo < $2.50"

CAMPI TRIGGER DISPONIBILI (usa ESATTAMENTE questi nomi):
- "momentum"   → "FORTE" | "MEDIO" | "DEBOLE"
- "volatility" → "ALTA" | "MEDIA" | "BASSA"
- "trend"      → "UP" | "SIDEWAYS" | "DOWN"
- "regime"     → "RANGING" | "TRENDING_BULL" | "TRENDING_BEAR" | "EXPLOSIVE"
- "direction"  → "LONG" | "SHORT"

OPERATORI: "==" | "!=" | "<" | ">" | "<=" | ">="

═══ PATTERN TOSSICI — BLOCCA SEMPRE ═══
- DEBOLE|ALTA|SIDEWAYS: WR 11% — BLOCCA_ENTRY, priority 10
- MEDIO|ALTA|SIDEWAYS: WR 28% — BLOCCA_ENTRY, priority 9
- FORTE|ALTA|SIDEWAYS: WR 35% — ALZA_SOGLIA +15, priority 8
- RANGING + loss_streak >= 2: ALZA_SOGLIA +10

═══ PATTERN VINCENTI — NON BLOCCARE MAI ═══
- FORTE|BASSA|UP: WR 78% → se bloccato = ABBASSA_SOGLIA -8
- FORTE|MEDIA|UP: WR 68% → se bloccato = ABBASSA_SOGLIA -5
- MEDIO|BASSA|UP: WR 65% → se bloccato = ABBASSA_SOGLIA -5

═══ DECISIONE CAPSULE ═══
1. Se phantom mostra MANCATO con pnl_missed > $10 su pattern vincente → ABBASSA_SOGLIA
2. Se loss_streak >= 2 in contesto tossico → BLOCCA_ENTRY o ALZA_SOGLIA
3. Se PROFIT_LOCK perde (WIN_+1 o WIN_+2) → non serve capsule qui, il problema è nel bot
4. Una sola capsule — quella più urgente. Chirurgica. Non generica.

FORMATO RISPOSTA:
[diagnosi in 3 righe max]
[perché questa capsule specifica]
<SUPERCAPSULE>
{
  "id": "SC_[NOME_BREVE]_[UNIX_TIMESTAMP]",
  "asset": "BTCUSDC",
  "livello": "AUTO",
  "tipo": "PARAMETRO",
  "trigger": [
    {"campo": "momentum",   "op": "==", "valore": "VALORE"},
    {"campo": "volatility", "op": "==", "valore": "VALORE"},
    {"campo": "trend",      "op": "==", "valore": "VALORE"}
  ],
  "azione": {
    "tipo": "BLOCCA_ENTRY|ALZA_SOGLIA|ABBASSA_SOGLIA",
    "valore": NUMERO,
    "motivo": "max 40 parole"
  },
  "priority": 9,
  "durata_ore": 0,
  "analisi_causale": "max 60 parole"
}
</SUPERCAPSULE>

REGOLA CRITICA SUL CAMPO "valore":
- Per ALZA_SOGLIA: valore = numero intero (es. 10, 15, 20) — mai stringhe
- Per ABBASSA_SOGLIA: valore = numero intero (es. 5, 8, 10) — mai stringhe  
- Per BLOCCA_ENTRY: valore = "BLOCCA" — stringa fissa
- MAX valore per ALZA_SOGLIA = 20 (oltre blocchi tutto)
- MAX valore per ABBASSA_SOGLIA = 10 (oltre apre troppo)
- Se vuoi un cooldown → usa ALZA_SOGLIA con valore 15 (stesso effetto pratico)

REGOLA FONDAMENTALE SULLA DURATA:
durata_ore = 0 significa PERMANENTE — la capsule vive finché il Phantom non dimostra che il pattern è cambiato.
NON usare 24 o 48. Usa 0 per pattern tossici confermati (WR < 30%).
La capsule muore solo quando il WR reale supera 50% su 20+ trade — non per un timer."""

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

def _semaforo(text: str, trigger: str = "") -> str:
    t = (text or "").upper()
    trig = (trigger or "").upper()
    if "SEMAFORO: RISCHIO" in t or "SEMAFORO:RISCHIO" in t:
        sem = "RISCHIO"
    elif "SEMAFORO: VALUTA" in t or "SEMAFORO:VALUTA" in t:
        sem = "VALUTA"
    elif "SEMAFORO: SAFE" in t or "SEMAFORO:SAFE" in t:
        sem = "SAFE"
    else:
        sem = "VALUTA"
    if sem == "SAFE" and ("LOSS_PATTERN" in trig or "LOSS_STREAK" in trig):
        _p(f"Semaforo override SAFE->VALUTA — perdita richiede L2")
        return "VALUTA"
    return sem


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
        # Normalizza ID: rimuovi timestamp finale per ottenere ID stabile
        # SC_BLOCCA_RANGING_1777449089 → SC_BLOCCA_RANGING
        # Così lo stesso pattern genera sempre lo stesso ID — no duplicati
        _raw_id = capsule.get("id", f"SC_{int(time.time())}")
        import re as _re
        cap_id = _re.sub(r'_\d{9,10}$', '', _raw_id)  # rimuove _UNIX_TS finale
        if not cap_id:
            cap_id = _raw_id
        asset    = capsule.get("asset",    "BTCUSDC")
        livello  = capsule.get("livello",  "AUTO")
        tipo     = capsule.get("tipo",     "VETO")
        priority = capsule.get("priority", 7)
        durata_h = capsule.get("durata_ore", 0)
        analisi  = capsule.get("analisi_causale", trigger)[:200]

        # Sanitizza azione: valore deve essere numerico per ALZA/ABBASSA_SOGLIA
        azione = capsule.get("azione", {})
        if azione.get("tipo") in ("ALZA_SOGLIA", "ABBASSA_SOGLIA"):
            raw_val = azione.get("valore", 10)
            if isinstance(raw_val, str):
                # Estrai numero dalla stringa es. "cooldown_seconds=300" → 10 (default sicuro)
                import re as _re
                nums = _re.findall(r'\d+\.?\d*', str(raw_val))
                # Usa valore conservativo: max 20 punti soglia
                azione["valore"] = min(float(nums[0]), 20.0) if nums else 10.0
                _p(f"Sanitizzato valore stringa → {azione['valore']}")
            azione["valore"] = float(azione.get("valore", 10))
        capsule["azione"] = azione

        trigger_json = json.dumps(capsule.get("trigger", []))
        azione_json  = json.dumps(capsule.get("azione",  {}))
        # durata_ore=0 → PERMANENTE (1 anno). durata_ore>0 → scade dopo N ore
        if durata_h == 0:
            scade_ts = time.time() + (365 * 24 * 3600)
            _p(f"Capsule PERMANENTE — scade 1 anno")
        else:
            scade_ts = time.time() + durata_h * 3600

        conn = sqlite3.connect(db_path, timeout=10)
        c    = conn.cursor()

        _existing = c.execute(
            "SELECT id, enabled FROM capsule WHERE id=?", (cap_id,)
        ).fetchone()
        if _existing:
            if _existing[1] == 0:
                # Era disabilitata (Phantom l'aveva rimossa) — riabilita con nuovi dati
                c.execute("UPDATE capsule SET enabled=1, created_ts=? WHERE id=?",
                          (time.time(), cap_id))
                conn.commit()
                conn.close()
                _p(f"Capsule {cap_id} riabilitata — nuovo trigger Oracle")
                return True
            else:
                _p(f"Capsule {cap_id} gia attiva — skip (ID stabile)")
                conn.close()
                return False

        # Verifica colonne disponibili nel DB (versioni diverse possono mancare di colonne)
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(capsule)").fetchall()]
        except:
            cols = []
        
        if 'last_hit_ts' in cols:
            c.execute("""
                INSERT OR IGNORE INTO capsule
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
        else:
            c.execute("""
                INSERT OR IGNORE INTO capsule
                  (id, asset, livello, tipo, trigger_json, azione_json,
                   priority, enabled, samples, wr, pnl_avg,
                   created_ts, scade_ts, hits, note)
                VALUES (?,?,?,?,?,?,?,1,0,0.0,0.0,?,?,0,?)
            """, (
                cap_id, asset, livello, tipo,
                trigger_json, azione_json,
                priority,
                time.time(), scade_ts,
                f"OracleAuto|trigger={trigger}|{analisi}"
            ))
        conn.commit()
        conn.close()

        _p(f"Capsule {cap_id} inserita — PERMANENTE (Phantom decide la morte) — zero deploy")

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
