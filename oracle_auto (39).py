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
        f"PATTERN WR<55%:\n{json.dumps(ctx['bad_patterns'][:5], indent=2)}"
    )
    return _deepseek(system, user, max_tokens=500)


def _call_l2(ctx: dict, trigger: str, l1: str) -> str:
    system = (
        "Sei il Superrisponditore L2 di OVERTOP BASSANO V15.\n\n"
        "IL PERNO: ogni trade perso contiene le informazioni per non perderlo piu.\n"
        "Esistono DUE sole verita possibili:\n\n"
        "EVITA: il contesto non aveva edge. Nessuna gestione poteva salvarlo.\n"
        "  Criteri: nessun WIN_+N, pnl_lordo < $2, WR contesto < 10%\n"
        "  Azione: blocca_entry — non entrare mai piu in quel contesto\n\n"
        "MIGLIORA: il mercato si era mosso a favore ma abbiamo gestito male.\n"
        "  Criteri: WIN_+N nel motivo con PnL negativo, OPPURE pnl_lordo > fee ma netto negativo\n"
        "  Azione: boost_soglia negativo — entra ma con soglia corretta\n\n"
        "La classificazione EVITA/MIGLIORA e gia nel contesto — usala come base.\n\n"
        "LEGGI FISICHE:\n"
        "- FEE = $2 fissi. BREAKEVEN ~$30 movimento BTC.\n"
        "- WIN_+N con PnL negativo = movimento c era ma gestione sbagliata = MIGLIORA\n"
        "- Nessun WIN_+N, PnL sempre -$2 = nessun edge = EVITA\n\n"
        "FORMATO TRIGGER (obbligatorio):\n"
        "[{\"param\": \"momentum\", \"op\": \"==\", \"value\": \"DEBOLE\"}, ...]\n"
        "CAMPI: momentum(FORTE/MEDIO/DEBOLE) volatility(ALTA/MEDIA/BASSA) trend(UP/SIDEWAYS/DOWN)\n"
        "OPERATORI: == != < > <= >=\n\n"
        "FORMATO AZIONE:\n"
        "EVITA:   {\"type\": \"blocca_entry\", \"params\": {\"reason\": \"spiegazione\"}}\n"
        "MIGLIORA: {\"type\": \"boost_soglia\", \"params\": {\"delta\": -8}}\n\n"
        "PATTERN TOSSICI (WR reale dai trade):\n"
        "- DEBOLE|ALTA|SIDEWAYS: WR 0.8% su 126 trade reali -> blocca_entry priority 10\n"
        "- MEDIO|ALTA|SIDEWAYS: WR 5.9% su 51 trade reali -> blocca_entry priority 9\n\n"
        "PATTERN VINCENTI (MAI bloccare):\n"
        "- FORTE|BASSA|UP: WR 78% -> se bloccato: boost_soglia delta -8\n"
        "- FORTE|MEDIA|UP: WR 68%\n"
        "- MEDIO|BASSA|UP: WR 65%\n\n"
        "FORMATO RISPOSTA:\n"
        "CLASSIFICAZIONE: [EVITA o MIGLIORA]\n"
        "LETTURA PARTITA: [cosa dice la partita reale in 2 righe]\n"
        "CAPSULE: [perche questa azione]\n"
        "<SUPERCAPSULE>\n"
        "{\n"
        "  \"id\": \"SC_[NOME_BREVE]\",\n"
        "  \"asset\": \"BTCUSDC\",\n"
        "  \"livello\": \"AUTO\",\n"
        "  \"tipo\": \"PARAMETRO\",\n"
        "  \"trigger\": [...],\n"
        "  \"azione\": {\"type\": \"blocca_entry|boost_soglia\", \"params\": {...}},\n"
        "  \"priority\": 10,\n"
        "  \"durata_ore\": 0,\n"
        "  \"analisi_causale\": \"cita la partita reale max 60 parole\"\n"
        "}\n"
        "</SUPERCAPSULE>\n\n"
        "REGOLE ASSOLUTE:\n"
        "- MAI blocca_entry senza trend nel trigger\n"
        "- MAI bloccare FORTE|BASSA|UP o FORTE|MEDIA|UP\n"
        "- durata_ore = 0 = permanente\n"
        "- delta boost_soglia: tra -20 e +20"
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


def _apply_capsule(capsule: dict, trigger: str) -> bool:
    try:
        db_path  = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
        _raw_id = capsule.get("id", f"SC_{int(time.time())}")
        import re as _re
        cap_id = _re.sub(r'_\d{9,10}$', '', _raw_id)
        if not cap_id:
            cap_id = _raw_id

        # ── ID FISSI — normalizza ID varianti allo stesso pattern ──────
        # Impedisce duplicati da nomi diversi per lo stesso contesto
        _ID_FISSI = {
            # DEBOLE|ALTA|SIDEWAYS — pattern più tossico WR 0.8%
            ("DEBOLE", "ALTA",  "SIDEWAYS", "blocca_entry"): "SC_BLOCCA_DEBOLE_ALTA_SIDEWAYS",
            ("DEBOLE", "ALTA",  "SIDEWAYS", "boost_soglia"): "SC_BOOST_DEBOLE_ALTA_SIDEWAYS",
            # MEDIO|ALTA|SIDEWAYS — WR 5.9%
            ("MEDIO",  "ALTA",  "SIDEWAYS", "blocca_entry"): "SC_BLOCCA_MEDIO_ALTA_SIDEWAYS",
            ("MEDIO",  "ALTA",  "SIDEWAYS", "boost_soglia"): "SC_BOOST_MEDIO_ALTA_SIDEWAYS",
            # DEBOLE|BASSA|SIDEWAYS — WR 10%
            ("DEBOLE", "BASSA", "SIDEWAYS", "blocca_entry"): "SC_BLOCCA_DEBOLE_BASSA_SIDEWAYS",
            # FORTE|ALTA|SIDEWAYS
            ("FORTE",  "ALTA",  "SIDEWAYS", "blocca_entry"): "SC_BLOCCA_FORTE_ALTA_SIDEWAYS",
            ("FORTE",  "ALTA",  "SIDEWAYS", "boost_soglia"): "SC_BOOST_FORTE_ALTA_SIDEWAYS",
        }
        # Estrai momentum/volatility/trend dai trigger per cercare ID fisso
        _trigger_raw = capsule.get("trigger", [])
        _trigger_norm_preview = _normalizza_trigger(_trigger_raw)
        _tv = {t.get("param"): t.get("value") for t in _trigger_norm_preview}
        _az_preview = _normalizza_azione(capsule.get("azione", {}))
        _az_type = _az_preview.get("type", "")
        _fixed_key = (_tv.get("momentum",""), _tv.get("volatility",""), _tv.get("trend",""), _az_type)
        if _fixed_key[0] and _fixed_key[1] and _fixed_key[2] and _az_type:
            _fixed_id = _ID_FISSI.get(_fixed_key)
            if _fixed_id:
                cap_id = _fixed_id
                _p(f"ID FISSO applicato: {_raw_id} → {cap_id}")
        asset    = capsule.get("asset",    "BTCUSDC")
        livello  = capsule.get("livello",  "AUTO")
        tipo     = capsule.get("tipo",     "PARAMETRO")
        priority = capsule.get("priority", 7)
        durata_h = capsule.get("durata_ore", 0)
        analisi  = capsule.get("analisi_causale", trigger)[:200]

        # Normalizza sempre il formato — anche se DeepSeek usa vecchio formato
        trigger_norm = _normalizza_trigger(capsule.get("trigger", []))
        azione_norm  = _normalizza_azione(capsule.get("azione", {}))

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
                _p(f"Capsule {cap_id} riabilitata")
                return True
            else:
                _p(f"Capsule {cap_id} gia attiva — skip")
                conn.close()
                return False

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

        _p(f"Capsule {cap_id} inserita — trigger={json.dumps(trigger_norm)[:80]}")

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
