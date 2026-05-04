"""
ORACLE AUTO — Background Worker Event-Driven
Versione: V15 Fix Definitivo | Maggio 2026

FILO LOGICO COMPLETO:
  heartbeat_data['oracle_trigger']  →  loop ogni 30s lo rileva
  → _build_context()                →  tutto: phantom, signal_tracker, narratore, zavorra
  → _call_l1()                      →  DeepSeek Risponditore: causa + semaforo SAFE/VALUTA/RISCHIO
  → _call_l2()        (se VALUTA/RISCHIO)  →  DeepSeek Superrisponditore: diagnosi + SuperCapsule JSON
  → _apply_capsule()                →  INSERT DB SQLite — attiva al prossimo tick, zero deploy
  → heartbeat_data['oracle_log']    →  dashboard mostra dialogo L1/L2 in tempo reale

FORMATO CAPSULE CORRETTO (CapsuleManager V29):
  trigger: [{"param": "momentum", "op": "==", "value": "DEBOLE"}]
  azione:  {"type": "blocca_entry", "params": {"reason": "motivo"}}
  azione:  {"type": "boost_soglia", "params": {"delta": 10}}
  MAI usare "campo"/"valore"/"tipo" — il CapsuleManager non li legge.
"""

import threading
import time
import json
import os
import sqlite3
import requests
from datetime import datetime

def _p(msg: str):
    ts = datetime.utcnow().strftime('%H:%M:%S')
    print(f"[{ts}] [ORACLE_AUTO] {msg}", flush=True)

_bot_ref        = None
_heartbeat      = None
_mode           = "MANUAL"
_running        = False
_last_trigger   = ""
_last_ts        = 0
_analysis_log   = []
_lock           = threading.Lock()

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


def start_background(heartbeat_data=None, bot_instance=None):
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
    _p(f"Background worker avviato — modalita={_mode}")


def set_mode(mode: str):
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
    _p("Run manuale forzato")
    hb = _get_hb()
    trigger = (hb or {}).get("oracle_trigger", "") or "MANUAL_RUN"
    return _pipeline(trigger)


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


def _pipeline(trigger: str) -> dict:
    global _last_trigger, _last_ts
    t0 = time.time()
    _p(f"Pipeline avviata — trigger={trigger}")
    _last_trigger = trigger
    ctx = _build_context(trigger)
    _p("L1 Risponditore in chiamata...")
    l1 = _call_l1(ctx, trigger)
    _p(f"L1 completato — {len(l1)} chars")
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
            _p(f"SuperCapsule {'inserita nel DB' if ok else 'FALLITA'}")
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


def _classifica_partita(trade: dict) -> dict:
    """
    Legge una singola partita e determina se era EVITA o MIGLIORA.

    EVITA  — il trade non aveva edge. Nessuna gestione poteva salvarlo.
             Criteri: WR contesto < 10% E pnl_lordo < fee ($2) E nessun WIN_+X significativo.

    MIGLIORA — il mercato si era mosso a favore ma abbiamo gestito male.
             Criteri: WIN_+X nel motivo con PnL negativo (lordo < fee ma movimento c'era)
                      OPPURE pnl_lordo > 0 ma netto negativo per fee.
                      OPPURE score molto alto ma uscita prematura.
    """
    reason  = trade.get("reason", "")
    pnl     = trade.get("pnl", 0.0)
    score   = trade.get("score", 0)
    soglia  = trade.get("soglia", 50)
    ctx     = trade.get("ctx", "")
    FEE     = 2.00

    # Estrai WIN_+N dal motivo
    import re as _re2
    win_match = _re2.search(r'WIN_\+(\d+)', reason)
    win_ticks = int(win_match.group(1)) if win_match else 0

    # Stima pnl_lordo dal pnl netto
    pnl_lordo = pnl + FEE  # approssimazione: netto = lordo - fee

    # Classifica
    if win_ticks >= 10 and pnl < 0:
        # Il mercato si era mosso a favore (WIN_+N) ma siamo usciti in perdita
        # Era un nostro errore di uscita o di timing
        tipo = "MIGLIORA"
        motivo = f"WIN_+{win_ticks} tick ma PnL {pnl:.2f} — errore di uscita o soglia"
        azione_suggerita = "Aggiusta soglia di uscita o abbassa soglia di entry per catturare il movimento"
    elif pnl_lordo > FEE and pnl < 0:
        # Il movimento c'era (lordo > fee) ma il netto è negativo
        tipo = "MIGLIORA"
        motivo = f"Lordo ${pnl_lordo:.2f} > fee ${FEE} ma netto {pnl:.2f} — gestione migliorabile"
        azione_suggerita = "Ottimizza timing di entrata o uscita"
    elif score > 60 and soglia > score * 0.8 and pnl < 0:
        # Score alto ma soglia troppo alta — abbiamo quasi catturato il trade
        tipo = "MIGLIORA"
        motivo = f"Score {score} alto ma soglia {soglia} troppo alta — perso per eccesso di cautela"
        azione_suggerita = "Abbassa soglia per questo contesto specifico"
    else:
        # Trade davvero perdente — nessun segnale di movimento a favore
        tipo = "EVITA"
        motivo = f"Nessun segnale di movimento a favore — contesto {ctx} senza edge"
        azione_suggerita = "Blocca entry in questo contesto"

    return {
        "tipo":             tipo,
        "motivo":           motivo,
        "azione_suggerita": azione_suggerita,
        "win_ticks":        win_ticks,
        "pnl_lordo":        round(pnl_lordo, 2),
        "pnl_netto":        round(pnl, 2),
        "score":            score,
        "soglia":           soglia,
        "reason":           reason,
        "ctx":              ctx,
    }


def _build_context(trigger: str) -> dict:
    hb = _get_hb() or {}

    # ── PARTITA REALE — cuore del nuovo sistema ──────────────────────────
    # Non leggiamo lo stato astratto — leggiamo la partita che ha causato il trigger
    storia = hb.get("narratore_trade_storia", [])[-20:]
    stats  = hb.get("narratore_trade_stats", {})

    # Estrai l'ultima partita persa (quella che ha generato il trigger)
    ultima_perdita = None
    classificazione = None
    for t in reversed(storia):
        if not t.get("is_win", True):
            ultima_perdita = t
            classificazione = _classifica_partita(t)
            break

    # Ultime 5 perdite per vedere il pattern
    perdite_recenti = [_classifica_partita(t) for t in storia if not t.get("is_win", True)][-5:]

    # Conta: quante EVITA vs MIGLIORA nelle ultime perdite
    n_evita   = sum(1 for p in perdite_recenti if p["tipo"] == "EVITA")
    n_migliora = sum(1 for p in perdite_recenti if p["tipo"] == "MIGLIORA")

    # ── PHANTOM — misura dei blocchi ─────────────────────────────────────
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
            vrd = "BLOCCO_CORRETTO" if win == 0 else ("BLOCCO_ECCESSIVO" if wr_blk > 15 else "ZONA_GRIGIA")
            verdetti.append({"livello": livello, "blocked": blk, "would_win": win,
                             "would_lose": los, "wr_bloccati_pct": wr_blk,
                             "pnl_missed_usdc": mis, "pnl_saved_usdc": sav, "verdetto": vrd})
    phantom_ctx = {
        "bilancio_usdc":        round(ph.get("bilancio",    0), 2),
        "protezione":           ph.get("protezione", 0),
        "zavorra":              ph.get("zavorra",    0),
        "pnl_missed_usdc":      round(ph.get("pnl_missed", 0), 2),
        "pnl_saved_usdc":       round(ph.get("pnl_saved",  0), 2),
        "verdetti_per_livello": verdetti,
    }

    # ── STATO BOT — contesto generale ────────────────────────────────────
    stato = {
        "regime":           hb.get("regime",        "N/A"),
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
        "trigger":           trigger,
        "stato_bot":         stato,
        # ── PARTITA REALE ──────────────────────────────────────────────
        "ultima_perdita":    ultima_perdita,
        "classificazione":   classificazione,   # EVITA o MIGLIORA con motivazione
        "perdite_recenti":   perdite_recenti,   # ultime 5 classificate
        "n_evita":           n_evita,
        "n_migliora":        n_migliora,
        # ── CONTESTO ──────────────────────────────────────────────────
        "storia_trade":      storia[-5:],
        "trade_stats":       stats,
        "phantom":           phantom_ctx,
        "loss_history":      hb.get("oracle_loss_history", [])[-5:],
        "win_history":       hb.get("oracle_win_history",  [])[-3:],
        "ts":                datetime.utcnow().isoformat(),
    }


def _call_l1(ctx: dict, trigger: str) -> str:
    system = (
        "Sei il Risponditore L1 di OVERTOP BASSANO V15 — sistema AI trading BTC/USDC.\n\n"
        "LEGGI FISICHE:\n"
        "- PnL in USDC: pnl_netto = (delta x 5000/entry_price) - 2.00\n"
        "- FEE FISSA $2. BREAKEVEN ~$30 movimento BTC.\n"
        "- PROFIT_LOCK WIN_+1/WIN_+2 = perdita netta certa (lordo < fee).\n\n"
        "PATTERN TOSSICI:\n"
        "- DEBOLE|ALTA|SIDEWAYS: WR 11% — il piu tossico\n"
        "- MEDIO|ALTA|SIDEWAYS: WR 28%\n"
        "- RANGING + volatilita ALTA: edge fisico negativo\n\n"
        "PATTERN VINCENTI:\n"
        "- FORTE|BASSA|UP: WR 78% — non bloccare mai\n"
        "- FORTE|MEDIA|UP: WR 68%\n"
        "- MEDIO|BASSA|UP: WR 65%\n\n"
        "SEMAFORO:\n"
        "SAFE = blocchi corretti, nessuna anomalia\n"
        "VALUTA = pattern tossico non bloccato, PROFIT_LOCK perde\n"
        "RISCHIO = loss_streak>=3, DEBOLE|ALTA|SIDEWAYS non bloccato\n\n"
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
        f"CLASSIFICAZIONE PERDITA: {json.dumps(ctx.get('classificazione', {}), indent=2)}\n\n"
        f"ULTIME 5 PERDITE:\n{json.dumps(ctx.get('perdite_recenti', []), indent=2)}"
    )
    return _deepseek(system, user, max_tokens=500)


def _call_l2(ctx: dict, trigger: str, l1: str) -> str:
    # Legge capsule AUTO esistenti dal DB per passarle al prompt — previene duplicati
    _existing_ids = []
    try:
        import sqlite3 as _sq, os as _os
        _db = _os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
        _conn = _sq.connect(_db, timeout=5)
        _rows = _conn.execute(
            "SELECT id FROM capsule WHERE livello='AUTO' AND enabled=1 LIMIT 20"
        ).fetchall()
        _conn.close()
        _existing_ids = [r[0] for r in _rows]
    except Exception:
        pass
    _existing_str = ", ".join(_existing_ids) if _existing_ids else "nessuna"

    system = (
        "Sei il Superrisponditore L2 di OVERTOP BASSANO V15.\n\n"
        "COMPITO UNICO: leggere UNA partita persa e produrre UNA sola SuperCapsule chirurgica.\n\n"
        "REGOLA FONDAMENTALE — UNA CAPSULE SOLA:\n"
        "Produci SEMPRE e SOLO una capsule. Mai due. Mai varianti. Una.\n"
        "Se il contesto e gia coperto da capsule esistenti nel DB, rispondi con boost_soglia delta=5\n"
        "invece di blocca_entry — non duplicare blocchi gia esistenti.\n\n"
        "CAPSULE GIA ESISTENTI NEL DB (non duplicare questi pattern):\n"
        f"{_existing_str}\\n\\n"
        "DUE SOLE VERITA:\n"
        "EVITA: nessun WIN_+N nei motivi di uscita, pnl_lordo < $2, WR < 10%\n"
        "  → blocca_entry con trigger SPECIFICI: momentum + volatility + trend obbligatori\n"
        "MIGLIORA: WIN_+N presente ma PnL negativo, oppure pnl_lordo > $2 ma netto negativo\n"
        "  → boost_soglia con delta tra -15 e +15\n\n"
        "LEGGI FISICHE:\n"
        "- FEE = $2 fissi. BREAKEVEN = $30 movimento BTC minimo.\n"
        "- WIN_+1 o WIN_+2 = lordo < $2 = perdita netta certa = EVITA, non MIGLIORA\n"
        "- WIN_+10 o piu con PnL negativo = MIGLIORA\n\n"
        "TRIGGER — REGOLE FERRO:\n"
        "1. blocca_entry DEVE avere SEMPRE: momentum + volatility + trend\n"
        "2. trigger MINIMO: 3 parametri. trigger MASSIMO: 4 parametri\n"
        "3. NON aggiungere regime se momentum+volatility+trend gia specificano il contesto\n"
        "4. CAMPI VALIDI SOLI: momentum, volatility, trend, direction, oi_carica, oi_stato, loss_consecutivi\n"
        "5. VIETATO: score, soglia, pnl, seed, fingerprint_wr, regime come unico trigger\n\n"
        "PATTERN TOSSICI CERTIFICATI (usa questi trigger esatti):\n"
        "- DEBOLE|ALTA|SIDEWAYS: [{momentum==DEBOLE},{volatility==ALTA},{trend==SIDEWAYS}]\n"
        "- MEDIO|ALTA|SIDEWAYS: [{momentum==MEDIO},{volatility==ALTA},{trend==SIDEWAYS}]\n"
        "- FORTE|ALTA|DOWN: [{momentum==FORTE},{volatility==ALTA},{trend==DOWN}]\n\n"
        "PATTERN VINCENTI — MAI bloccare:\n"
        "- FORTE|BASSA|UP, FORTE|MEDIA|UP, MEDIO|BASSA|UP\n\n"
        "ID CAPSULE — REGOLA:\n"
        "Usa formato: SC_[MOMENTUM]_[VOLATILITY]_[TREND]\n"
        "Esempio: SC_DEBOLE_ALTA_SIDEWAYS\n"
        "Questo previene duplicati — stesso contesto = stesso ID = update, non insert.\n\n"
        "FORMATO RISPOSTA (rispetta esattamente):\n"
        "CLASSIFICAZIONE: [EVITA o MIGLIORA]\n"
        "LETTURA: [1 frase sulla partita reale]\n"
        "<SUPERCAPSULE>\n"
        "{\n"
        "  \"id\": \"SC_[MOMENTUM]_[VOLATILITY]_[TREND]\",\n"
        "  \"asset\": \"BTCUSDC\",\n"
        "  \"livello\": \"AUTO\",\n"
        "  \"tipo\": \"PARAMETRO\",\n"
        "  \"trigger\": [{\"param\":\"momentum\",\"op\":\"==\",\"value\":\"DEBOLE\"},{\"param\":\"volatility\",\"op\":\"==\",\"value\":\"ALTA\"},{\"param\":\"trend\",\"op\":\"==\",\"value\":\"SIDEWAYS\"}],\n"
        "  \"azione\": {\"type\": \"blocca_entry\", \"params\": {\"reason\": \"max 80 caratteri\"}},\n"
        "  \"priority\": 8,\n"
        "  \"durata_ore\": 0,\n"
        "  \"analisi_causale\": \"cita la partita reale max 60 parole\"\n"
        "}\n"
        "</SUPERCAPSULE>"
    )

    # Costruisce il messaggio centrato sulla PARTITA REALE
    classificazione = ctx.get("classificazione") or {}
    ultima_perdita  = ctx.get("ultima_perdita")  or {}
    perdite_recenti = ctx.get("perdite_recenti", [])
    n_evita    = ctx.get("n_evita", 0)
    n_migliora = ctx.get("n_migliora", 0)

    user = (
        f"TRIGGER: {trigger}\n\n"
        f"DIAGNOSI L1:\n{l1}\n\n"
        f"=== PARTITA REALE (quella che ha generato il trigger) ===\n"
        f"Contesto: {ultima_perdita.get('ctx', '?')}\n"
        f"Score: {ultima_perdita.get('score', 0)} / Soglia: {ultima_perdita.get('soglia', 0)}\n"
        f"PnL netto: ${ultima_perdita.get('pnl', 0):.2f}\n"
        f"Motivo uscita: {ultima_perdita.get('reason', '?')}\n\n"
        f"=== CLASSIFICAZIONE AUTOMATICA ===\n"
        f"Tipo: {classificazione.get('tipo', '?')}\n"
        f"Motivazione: {classificazione.get('motivo', '?')}\n"
        f"PnL lordo stimato: ${classificazione.get('pnl_lordo', 0):.2f}\n"
        f"WIN ticks: {classificazione.get('win_ticks', 0)}\n"
        f"Azione suggerita: {classificazione.get('azione_suggerita', '?')}\n\n"
        f"=== PATTERN ULTIME {len(perdite_recenti)} PERDITE ===\n"
        f"EVITA: {n_evita} | MIGLIORA: {n_migliora}\n"
        + "\n".join([
            f"- {p.get('ctx','?')} [{p.get('tipo','?')}] PnL lordo ${p.get('pnl_lordo',0):.2f} WIN+{p.get('win_ticks',0)}"
            for p in perdite_recenti
        ]) + "\n\n"
        f"=== STATO BOT ===\n"
        f"{json.dumps(ctx['stato_bot'], indent=2)}\n\n"
        f"=== PHANTOM ===\n"
        f"{json.dumps(ctx['phantom'], indent=2)}"
    )

    return _deepseek(system, user, max_tokens=900)


# ═══════════════════════════════════════════════════════════════
# NORMALIZZA FORMATO — converte vecchio formato in nuovo
# ═══════════════════════════════════════════════════════════════

def _normalizza_trigger(trigger_list: list) -> list:
    """Converte trigger con campo/valore nel formato corretto param/value."""
    normalizzati = []
    for t in trigger_list:
        if "campo" in t or "valore" in t:
            normalizzati.append({
                "param": t.get("campo", t.get("param", "")),
                "op":    t.get("op", "=="),
                "value": t.get("valore", t.get("value", "")),
            })
        else:
            normalizzati.append(t)
    return normalizzati


def _normalizza_azione(azione: dict) -> dict:
    """Converte azione con tipo/valore nel formato corretto type/params."""
    if not azione:
        return {"type": "blocca_entry", "params": {"reason": "Oracle Auto"}}
    tipo = azione.get("tipo") or azione.get("type", "")
    tipo_map = {
        "BLOCCA_ENTRY":   "blocca_entry",
        "ALZA_SOGLIA":    "boost_soglia",
        "ABBASSA_SOGLIA": "boost_soglia",
        "BLOCCA_CONTESTO": "blocca_entry",
    }
    type_new = tipo_map.get(tipo, tipo.lower() if tipo else "blocca_entry")
    if type_new == "boost_soglia":
        valore = azione.get("valore", azione.get("params", {}).get("delta", 10))
        if tipo == "ABBASSA_SOGLIA":
            delta = -abs(float(valore))
        else:
            delta = abs(float(valore))
        return {"type": "boost_soglia", "params": {"delta": delta}}
    elif type_new == "blocca_entry":
        motivo = azione.get("motivo", azione.get("params", {}).get("reason", "Oracle Auto"))
        return {"type": "blocca_entry", "params": {"reason": str(motivo)[:100]}}
    else:
        if "type" in azione:
            return azione
        return {"type": type_new, "params": azione.get("params", {})}


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
        _p(f"Semaforo override SAFE->VALUTA")
        return "VALUTA"
    return sem


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
        _p(f"SuperCapsule estratta: {cap.get('id','?')}")
        return cap
    except Exception as ex:
        _p(f"Parse SuperCapsule fallito: {ex}")
        return None



def canonical_capsule_id(asset: str, classification: str, direction: str,
                          momentum: str, volatility: str, trend: str,
                          action_type: str, regime: str = "", matrimonio: str = "") -> str:
    """
    Produce ID canonico deterministico dalla forma del pattern.
    Stesso pattern = stesso ID sempre. Previene duplicati da nomi DeepSeek liberi.
    
    Formato: SC_{CLASSIFICATION}_{ASSET}_{DIRECTION}_{MOMENTUM}_{VOLATILITY}_{TREND}
    Esempio: SC_EVITA_BTCUSDC_LONG_DEBOLE_ALTA_SIDEWAYS
    """
    parts = ["SC"]
    # Classificazione
    cl = (classification or "").upper()
    if cl in ("EVITA", "MIGLIORA"):
        parts.append(cl)
    # Asset (solo BTC o SOL)
    a = (asset or "BTCUSDC").upper().replace("USDC", "").replace("USDT", "")
    parts.append(a if a else "BTC")
    # Direction
    d = (direction or "").upper()
    if d in ("LONG", "SHORT"):
        parts.append(d)
    # Momentum
    m = (momentum or "").upper()
    if m in ("FORTE", "MEDIO", "DEBOLE"):
        parts.append(m)
    # Volatility
    v = (volatility or "").upper()
    if v in ("ALTA", "MEDIA", "BASSA"):
        parts.append(v)
    # Trend
    t = (trend or "").upper()
    if t in ("UP", "SIDEWAYS", "DOWN"):
        parts.append(t)
    return "_".join(parts)


def _apply_capsule(capsule: dict, trigger: str) -> bool:
    try:
        db_path  = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
        _raw_id = capsule.get("id", f"SC_{int(time.time())}")
        import re as _re

        # ── ID CANONICO — deriva dalla forma del pattern, non dal nome DeepSeek ──
        # Normalizza prima per estrarre i valori dal trigger
        _trigger_raw = capsule.get("trigger", [])
        _trigger_norm_preview = _normalizza_trigger(_trigger_raw)
        _tv = {t.get("param"): t.get("value") for t in _trigger_norm_preview}
        _az_preview = _normalizza_azione(capsule.get("azione", {}))
        _az_type = _az_preview.get("type", "")

        # Estrai classificazione dal testo della capsule
        _classif = "EVITA" if _az_type == "blocca_entry" else "MIGLIORA"

        # Calcola ID canonico se abbiamo momentum+volatility+trend
        _mom = _tv.get("momentum", "")
        _vol = _tv.get("volatility", "")
        _trd = _tv.get("trend", "")
        _dir = _tv.get("direction", "")
        _asset = capsule.get("asset", "BTCUSDC")

        if _mom and _vol and _trd:
            cap_id = canonical_capsule_id(
                asset=_asset,
                classification=_classif,
                direction=_dir,
                momentum=_mom,
                volatility=_vol,
                trend=_trd,
                action_type=_az_type,
            )
            _p(f"[CAPSULE_MEMORY] ID CANONICO: {_raw_id} → {cap_id}")
        else:
            # Fallback: usa ID DeepSeek pulito da timestamp
            cap_id = _re.sub(r'_\d{9,10}$', '', _raw_id)
            if not cap_id:
                cap_id = _raw_id
            _p(f"[CAPSULE_MEMORY] ID FALLBACK (trigger incompleto): {cap_id}")
        asset    = capsule.get("asset",    "BTCUSDC")
        livello  = capsule.get("livello",  "AUTO")
        tipo     = capsule.get("tipo",     "PARAMETRO")
        priority = capsule.get("priority", 7)
        durata_h = capsule.get("durata_ore", 0)
        analisi  = capsule.get("analisi_causale", trigger)[:200]

        # Normalizza sempre il formato — anche se DeepSeek usa vecchio formato
        trigger_norm = _normalizza_trigger(capsule.get("trigger", []))
        azione_norm  = _normalizza_azione(capsule.get("azione", {}))

        # ── EVITA / MIGLIORA VINCOLANTE ────────────────────────────────
        # Se classificazione è MIGLIORA, blocca_entry è vietata — corregge in boost_soglia
        # Se classificazione è EVITA, boost_entry è vietata — corregge in blocca_entry
        _classif_final = "EVITA" if azione_norm.get("type") == "blocca_entry" else "MIGLIORA"
        _analisi_text = (capsule.get("analisi_causale", "") or "").upper()
        if "MIGLIORA" in _analisi_text and azione_norm.get("type") == "blocca_entry":
            _p(f"[CAPSULE_MEMORY] MIGLIORA→blocca_entry CORRETTO in boost_soglia: {cap_id}")
            azione_norm = {"type": "boost_soglia", "params": {"delta": -8, "reason": "MIGLIORA_corretto_da_blocca"}}
        elif "EVITA" in _analisi_text and azione_norm.get("type") == "boost_entry":
            _p(f"[CAPSULE_MEMORY] EVITA→boost_entry CORRETTO in blocca_entry: {cap_id}")
            azione_norm = {"type": "blocca_entry", "params": {"reason": "EVITA_corretto_da_boost"}}

        # ── VALIDAZIONE CAMPI TRIGGER — lista bianca definitiva ────────────
        # Solo questi campi sono letti dal CapsuleManager. Tutto il resto viene rifiutato.
        _CAMPI_VALIDI = {
            "momentum", "volatility", "trend", "regime", "direction",
            "oi_carica", "oi_stato", "loss_consecutivi", "matrimonio",
            "breath", "comparto"
        }
        # FIX P1: rimuove campi non validi invece di rifiutare tutta la capsule
        _trigger_filtrato = []
        for _t in trigger_norm:
            _param = _t.get("param", "")
            if _param and _param not in _CAMPI_VALIDI:
                _p(f"CAMPO RIMOSSO: '{_param}' non letto da CM — capsule salvata senza quel trigger")
            else:
                _trigger_filtrato.append(_t)
        trigger_norm = _trigger_filtrato
        if azione_norm.get("type") == "blocca_entry" and \
           not any(t.get("param") == "trend" for t in trigger_norm):
            trigger_norm.append({"param": "trend", "op": "==", "value": "SIDEWAYS"})  # FIX: inject trend mancante
            _p(f"TREND INIETTATO: {cap_id} blocca_entry senza trend — aggiunto SIDEWAYS automaticamente")
        # ── VALIDAZIONE TREND OBBLIGATORIO ─────────────────────────────────
        # MAI blocca_entry senza trend nel trigger — blocca UP vincenti
        # MAI boost_soglia con delta alto senza trend — alza soglia anche su UP
        _az_type = azione_norm.get("type")
        _has_trend = any(t.get("param") == "trend" for t in trigger_norm)
        if _az_type == "blocca_entry" and not _has_trend:
            trigger_norm.append({"param": "trend", "op": "==", "value": "SIDEWAYS"})  # FIX: inject trend
            _p(f"TREND INIETTATO (validazione): {cap_id} — aggiunto SIDEWAYS")
        if _az_type == "boost_soglia" and not _has_trend:
            _delta = abs(azione_norm.get("params", {}).get("delta", 0))
            if _delta >= 10:
                _p(f"RIFIUTATA: capsule {cap_id} boost_soglia delta={_delta} senza trend — pericolosa")
                return False

        # ── VALIDAZIONE WHITELIST — rifiuta capsule che bloccano pattern vincenti ──
        _WHITELIST = [
            {"momentum": "FORTE", "volatility": "BASSA", "trend": "UP"},
            {"momentum": "FORTE", "volatility": "MEDIA", "trend": "UP"},
            {"momentum": "MEDIO", "volatility": "BASSA", "trend": "UP"},
        ]
        if azione_norm.get("type") == "blocca_entry":
            tvals = {t.get("param"): t.get("value") for t in trigger_norm}
            for pattern in _WHITELIST:
                if all(tvals.get(k) == v for k, v in pattern.items() if k in tvals):
                    if sum(1 for k in pattern if k in tvals) >= 2:
                        _p(f"CAPSULE RIFIUTATA — blocca pattern vincente {pattern}: {cap_id}")
                        return False

        # Sanitizza delta boost_soglia
        if azione_norm.get("type") == "boost_soglia":
            delta = azione_norm.get("params", {}).get("delta", 10)
            if isinstance(delta, str):
                nums = _re.findall(r'-?\d+\.?\d*', str(delta))
                delta = float(nums[0]) if nums else 10.0
            azione_norm["params"]["delta"] = max(-20.0, min(20.0, float(delta)))

        trigger_json = json.dumps(trigger_norm)
        azione_json  = json.dumps(azione_norm)

        if durata_h == 0:
            scade_ts = time.time() + (365 * 24 * 3600)
        else:
            scade_ts = time.time() + durata_h * 3600

        conn = sqlite3.connect(db_path, timeout=10)
        c    = conn.cursor()

        _existing = c.execute("SELECT id, enabled FROM capsule WHERE id=?", (cap_id,)).fetchone()
        if _existing:
            if _existing[1] == 0:
                c.execute("UPDATE capsule SET enabled=1, created_ts=? WHERE id=?", (time.time(), cap_id))
                conn.commit()
                conn.close()
                _p(f"[CAPSULE_MEMORY] UPDATE id={cap_id} (riabilitata)")
                return True
            else:
                _p(f"[CAPSULE_MEMORY] DUPLICATE_SKIP id={cap_id} — gia attiva nel DB")
                conn.close()
                return False

        # ── CHECK TRIGGER DUPLICATO — il pattern esiste già con nome diverso ──
        # Normalizza trigger a chiave unica e cerca nel DB
        import json as _jj
        _trigger_key = _jj.dumps(
            sorted([(t.get("param",""), t.get("op",""), str(t.get("value",""))) 
                    for t in trigger_norm]),
            sort_keys=True
        )
        _azione_type = azione_norm.get("type", "")
        _dup = c.execute(
            "SELECT id FROM capsule WHERE enabled=1 AND livello='AUTO' "
            "AND azione_json LIKE ? LIMIT 1",
            (f'%{_azione_type}%',)
        ).fetchall()
        for _row in _dup:
            try:
                _existing_trig = c.execute(
                    "SELECT trigger_json FROM capsule WHERE id=?", (_row[0],)
                ).fetchone()
                if _existing_trig:
                    _ex_tr = _jj.loads(_existing_trig[0] or '[]')
                    _ex_key = _jj.dumps(
                        sorted([(t.get("param",""), t.get("op",""), str(t.get("value","")))
                                for t in _ex_tr]),
                        sort_keys=True
                    )
                    if _ex_key == _trigger_key:
                        _p(f"TRIGGER DUPLICATO: {cap_id} ha stesso trigger di {_row[0]} — skip")
                        conn.close()
                        return False
            except Exception:
                pass

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
            """, (cap_id, asset, livello, tipo, trigger_json, azione_json,
                  priority, time.time(), scade_ts,
                  f"OracleAuto|trigger={trigger}|{analisi}"))
        else:
            c.execute("""
                INSERT OR IGNORE INTO capsule
                  (id, asset, livello, tipo, trigger_json, azione_json,
                   priority, enabled, samples, wr, pnl_avg,
                   created_ts, scade_ts, hits, note)
                VALUES (?,?,?,?,?,?,?,1,0,0.0,0.0,?,?,0,?)
            """, (cap_id, asset, livello, tipo, trigger_json, azione_json,
                  priority, time.time(), scade_ts,
                  f"OracleAuto|trigger={trigger}|{analisi}"))
        conn.commit()
        conn.close()

        _p(f"[CAPSULE_MEMORY] INSERT id={cap_id} trigger={json.dumps(trigger_norm)[:80]}")

        hb = _get_hb()
        if hb is not None:
            sc_log = hb.get("supercapsule_log", [])
            sc_log.append({"ts": datetime.utcnow().isoformat(), "id": cap_id,
                           "tipo": tipo, "trigger": trigger, "analisi": analisi[:80]})
            hb["supercapsule_log"] = sc_log[-10:]

        return True

    except Exception as ex:
        _p(f"Apply capsule errore: {ex}")
        return False


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
