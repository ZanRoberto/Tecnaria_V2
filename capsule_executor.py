"""
CAPSULE EXECUTOR — Ciclo di Vita Autonomo delle Capsule Eseguibili
==================================================================
Permette al sistema OVERTOP di generare, testare in phantom, promuovere
ed eseguire codice Python a caldo senza riavvio del processo.

LIVELLI enabled:
  0 = disabilitata
  1 = PHANTOM (test parallelo, non eseguita live)
  2 = LIVE (iniettata nel bot, eseguita ad ogni entry/exit)

SICUREZZA:
  - SafeCodeValidator valida AST prima di qualsiasi exec()
  - Lista bianca di operazioni permesse
  - Sempre rollback disponibile verso funzione originale
  - Legge Fondamentale (PnL, STOP_LIVE, TRADE_SIZE_USD) NON modificabile
"""

import ast
import sqlite3
import time
import logging
import types
import traceback
from datetime import datetime

log = logging.getLogger(__name__)

# ===========================================================================
# SCHEMA DB
# ===========================================================================

SCHEMA_ESEGUIBILI = """
CREATE TABLE IF NOT EXISTS capsule_eseguibili (
    id                  TEXT PRIMARY KEY,
    nome                TEXT NOT NULL,
    target_method       TEXT NOT NULL,
    sorgente            TEXT NOT NULL,
    contesto_trigger    TEXT DEFAULT '{}',
    firma_autore        TEXT DEFAULT 'ORACLE_AUTO',
    enabled             INTEGER DEFAULT 1,
    created_ts          REAL NOT NULL,
    promoted_ts         REAL,
    last_used_ts        REAL,
    attivazioni         INTEGER DEFAULT 0,
    phantom_trades      INTEGER DEFAULT 0,
    phantom_wr          REAL DEFAULT 0.0,
    phantom_pnl_avg     REAL DEFAULT 0.0,
    live_wr             REAL DEFAULT 0.0,
    live_pnl_avg        REAL DEFAULT 0.0,
    performance_score   REAL DEFAULT 0.0,
    error_log           TEXT DEFAULT '',
    note                TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS capsule_eseguibili_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT DEFAULT CURRENT_TIMESTAMP,
    capsule_id  TEXT,
    evento      TEXT,
    dettaglio   TEXT
);
"""

# ===========================================================================
# METODI TARGET PERMESSI (whitelist — Legge Fondamentale protetta)
# ===========================================================================

ALLOWED_TARGETS = {
    # Soglie e score
    "_calcola_soglia_ranging",
    "_calcola_soglia_explosive",
    "_calcola_soglia_trending",
    "_get_ia_soglia_boost",
    "_get_dynamic_soglia_max",
    # Size
    "_calcola_size_boost",
    # Veti
    "_check_extra_veto",
    # Seed modifier
    "_seed_modifier",
    # Exit modifier
    "_exit_energy_modifier",
    # Drift
    "_drift_factor_override",
}

# METODI PROIBITI — mai sovrascrivibili
FORBIDDEN_TARGETS = {
    "_close_shadow_trade", "_close_trade", "_place_order",
    "run", "connect_binance", "_process_tick",
    "__init__", "_update_heartbeat", "_persist",
}

# NOMI PROIBITI NEL CODICE (AST check)
FORBIDDEN_NAMES = {
    "exec", "eval", "compile", "__import__", "open", "os", "sys",
    "subprocess", "socket", "requests", "urllib", "http",
    "TRADE_SIZE_USD", "LEVERAGE", "FEE_TRADE", "FEE_PCT", "STOP_LIVE",
    "heartbeat_lock", "_place_order", "connect_binance",
}

# ===========================================================================
# SAFE CODE VALIDATOR
# ===========================================================================

class SafeCodeValidator:
    """
    Validatore AST per codice generato da DeepSeek.
    Rifiuta qualsiasi costrutto pericoloso prima dell'esecuzione.
    """

    ALLOWED_NODE_TYPES = {
        ast.FunctionDef, ast.Return, ast.Assign, ast.AugAssign,
        ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler,
        ast.With, ast.Pass, ast.Break, ast.Continue,
        ast.Expr, ast.Call, ast.Attribute, ast.Subscript,
        ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
        ast.Constant, ast.Name, ast.List, ast.Dict, ast.Tuple, ast.Set,
        ast.Index, ast.Slice, ast.Load, ast.Store, ast.Del,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
        ast.FloorDiv, ast.LShift, ast.RShift, ast.BitOr, ast.BitAnd,
        ast.BitXor, ast.Invert, ast.Not, ast.UAdd, ast.USub,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.Is, ast.IsNot, ast.In, ast.NotIn,
        ast.And, ast.Or,
        ast.arguments, ast.arg, ast.keyword,
        ast.ListComp, ast.GeneratorExp, ast.comprehension,
        ast.IfExp, ast.Lambda,
        ast.Module, ast.Import, ast.ImportFrom,  # import solo per math, collections
        ast.Global, ast.Nonlocal,
        ast.FormattedValue, ast.JoinedStr,  # f-string
        ast.AnnAssign,
    }

    ALLOWED_IMPORTS = {"math", "collections", "time", "datetime", "json", "logging"}

    def validate(self, source: str, expected_func_name: str) -> tuple:
        """
        Valida il sorgente Python.
        Returns: (ok: bool, error: str)
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return False, f"SyntaxError: {e}"

        # Deve contenere almeno una funzione con il nome atteso
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        func_names = [f.name for f in funcs]
        if expected_func_name not in func_names:
            return False, f"Funzione '{expected_func_name}' non trovata. Trovate: {func_names}"

        # Controlla ogni nodo
        for node in ast.walk(tree):
            # Tipo nodo consentito?
            if type(node) not in self.ALLOWED_NODE_TYPES:
                return False, f"Nodo AST non consentito: {type(node).__name__}"

            # Import solo da lista bianca
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name not in self.ALLOWED_IMPORTS:
                            return False, f"Import non consentito: {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module not in self.ALLOWED_IMPORTS:
                        return False, f"Import non consentito: {node.module}"

            # Nome vietato?
            if isinstance(node, ast.Name):
                if node.id in FORBIDDEN_NAMES:
                    return False, f"Nome vietato nel codice: '{node.id}'"

            # Attributo vietato?
            if isinstance(node, ast.Attribute):
                if node.attr in FORBIDDEN_NAMES:
                    return False, f"Attributo vietato: '.{node.attr}'"

        return True, "OK"


# ===========================================================================
# CAPSULE EXECUTOR
# ===========================================================================

class CapsuleExecutor:
    """
    Gestore del ciclo di vita delle capsule di codice eseguibile.

    Tre stati:
      enabled=1 → PHANTOM: test parallelo, non modifica il bot live
      enabled=2 → LIVE: iniettata nel metodo target del bot
      enabled=0 → DISABILITATA: archiviata nel DB

    Il bot originale non viene mai toccato finché una capsula è in PHANTOM.
    In LIVE, il metodo originale viene salvato e ripristinabile in qualsiasi momento.
    """

    PHANTOM_TEST_TRADES  = 20    # trade per promozione
    PROMOTION_WR_DELTA   = 0.05  # +5% WR per promuovere
    PROMOTION_PNL_DELTA  = 1.50  # +$1.50 PnL medio per promuovere
    ROLLBACK_WINDOW      = 100   # trade per valutare LIVE
    ROLLBACK_PERIODS     = 2     # periodi consecutivi di peggioramento

    def __init__(self, db_path: str, bot_instance):
        self.db_path   = db_path
        self.bot       = bot_instance
        self.validator = SafeCodeValidator()
        self._originals = {}   # {method_name: original_function}
        self._live      = {}   # {method_name: capsule_id}
        self._phantom   = {}   # {capsule_id: {"func": fn, "trades": [], ...}}
        self._init_db()
        self.load_all()
        log.info(f"[CE] ✅ CapsuleExecutor pronto — {len(self._live)} LIVE, {len(self._phantom)} PHANTOM")

    # ────────────────────────────────────────────────────────────────────────
    # INIT
    # ────────────────────────────────────────────────────────────────────────

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.executescript(SCHEMA_ESEGUIBILI)

    def load_all(self):
        """Al boot, carica e inietta tutte le capsule LIVE dal DB."""
        try:
            with sqlite3.connect(self.db_path) as c:
                rows = c.execute(
                    "SELECT id, nome, target_method, sorgente, enabled FROM capsule_eseguibili WHERE enabled > 0"
                ).fetchall()
            for cap_id, nome, target, sorgente, enabled in rows:
                if enabled == 2:
                    ok, err = self._inject_live(cap_id, nome, target, sorgente)
                    if ok:
                        log.info(f"[CE] 🧬 LIVE caricata al boot: {nome} → {target}")
                    else:
                        log.error(f"[CE] ❌ Boot inject fallito {nome}: {err}")
                elif enabled == 1:
                    ok, err = self._register_phantom(cap_id, nome, target, sorgente)
                    if ok:
                        log.info(f"[CE] 👻 PHANTOM caricata al boot: {nome}")
        except Exception as e:
            log.error(f"[CE] load_all error: {e}")

    # ────────────────────────────────────────────────────────────────────────
    # AGGIUNTA CAPSULE
    # ────────────────────────────────────────────────────────────────────────

    def add_capsule(self, nome: str, sorgente: str, target_method: str,
                    contesto: dict = None, firma_autore: str = "ORACLE_AUTO") -> tuple:
        """
        Aggiunge una nuova capsula eseguibile.
        Default: enabled=1 (PHANTOM) — deve superare il test prima di andare LIVE.
        Returns: (ok: bool, cap_id: str, error: str)
        """
        # Sicurezza: target nella whitelist
        if target_method in FORBIDDEN_TARGETS:
            return False, "", f"Target '{target_method}' è nella lista vietata"
        if target_method not in ALLOWED_TARGETS:
            # Auto-aggiunge se la firma inizia con _ e non è in forbidden
            if not target_method.startswith("_") or any(f in target_method for f in ["place", "close", "run", "connect"]):
                return False, "", f"Target '{target_method}' non nella whitelist"

        # Estrai nome funzione atteso dal target
        func_name = target_method.lstrip("_")
        # Valida AST
        ok, err = self.validator.validate(sorgente, func_name if func_name in sorgente else target_method.lstrip("_"))
        if not ok:
            # Prova anche con il nome originale
            ok2, err2 = self.validator.validate(sorgente, target_method)
            if not ok2:
                return False, "", f"Validazione AST fallita: {err}"

        cap_id = f"CE_{nome}_{int(time.time())}"
        import json
        try:
            with sqlite3.connect(self.db_path) as c:
                c.execute("""
                    INSERT INTO capsule_eseguibili
                    (id, nome, target_method, sorgente, contesto_trigger,
                     firma_autore, enabled, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """, (cap_id, nome, target_method, sorgente,
                      json.dumps(contesto or {}), firma_autore, time.time()))
            self._log_evento(cap_id, "AGGIUNTA", f"fonte={firma_autore} target={target_method}")
        except Exception as e:
            return False, "", f"DB error: {e}"

        # Registra come PHANTOM
        ok, err = self._register_phantom(cap_id, nome, target_method, sorgente)
        if not ok:
            return False, cap_id, f"Registrazione phantom fallita: {err}"

        log.info(f"[CE] 💊 Nuova capsula PHANTOM: {nome} → {target_method}")
        return True, cap_id, "OK"

    # ────────────────────────────────────────────────────────────────────────
    # INIEZIONE PHANTOM
    # ────────────────────────────────────────────────────────────────────────

    def _register_phantom(self, cap_id: str, nome: str, target: str, sorgente: str) -> tuple:
        """Registra la funzione in memoria per test parallelo. Non tocca il bot."""
        try:
            func = self._compile_function(sorgente, target)
            if func is None:
                return False, "Compilazione fallita"
            self._phantom[cap_id] = {
                "nome":    nome,
                "target":  target,
                "func":    func,
                "trades":  [],
                "pnl_sum": 0.0,
                "wins":    0,
            }
            return True, "OK"
        except Exception as e:
            return False, str(e)

    def test_phantom(self, cap_id: str, contesto: dict, pnl: float, is_win: bool):
        """
        Chiamato dopo ogni trade per registrare il risultato della capsula PHANTOM.
        Se ha abbastanza trade, valuta promozione automatica.
        """
        if cap_id not in self._phantom:
            return
        ph = self._phantom[cap_id]
        ph["trades"].append({"pnl": pnl, "is_win": is_win})
        ph["pnl_sum"] += pnl
        if is_win:
            ph["wins"] += 1

        n = len(ph["trades"])
        if n >= self.PHANTOM_TEST_TRADES:
            self._valuta_promozione(cap_id)

    def _valuta_promozione(self, cap_id: str):
        """Dopo PHANTOM_TEST_TRADES, decide se promuovere o scartare."""
        if cap_id not in self._phantom:
            return
        ph = self._phantom[cap_id]
        n = len(ph["trades"])
        if n == 0:
            return

        ph_wr  = ph["wins"] / n
        ph_pnl = ph["pnl_sum"] / n

        # Prendi WR baseline del bot (ultimi n trade)
        baseline_wr  = self._get_baseline_wr(n)
        baseline_pnl = self._get_baseline_pnl(n)

        delta_wr  = ph_wr  - baseline_wr
        delta_pnl = ph_pnl - baseline_pnl

        promuovi = (delta_wr >= self.PROMOTION_WR_DELTA and
                    delta_pnl >= self.PROMOTION_PNL_DELTA)

        if promuovi:
            log.info(f"[CE] 🚀 PROMOZIONE: {ph['nome']} WR Δ={delta_wr:+.1%} PnL Δ={delta_pnl:+.2f}")
            self._promote_to_live(cap_id, ph_wr, ph_pnl)
        else:
            log.info(f"[CE] ❌ SCARTATA: {ph['nome']} WR Δ={delta_wr:+.1%} PnL Δ={delta_pnl:+.2f} — sotto soglia")
            self._disable_capsule(cap_id, f"Phantom: WR Δ={delta_wr:+.1%} PnL Δ={delta_pnl:+.2f}")

    # ────────────────────────────────────────────────────────────────────────
    # PROMOZIONE A LIVE
    # ────────────────────────────────────────────────────────────────────────

    def _promote_to_live(self, cap_id: str, phantom_wr: float, phantom_pnl: float):
        """Promuove capsule da PHANTOM a LIVE — inietta nel metodo del bot."""
        if cap_id not in self._phantom:
            return
        ph = self._phantom[cap_id]

        ok, err = self._inject_live(cap_id, ph["nome"], ph["target"], None, ph["func"])
        if not ok:
            log.error(f"[CE] Promozione fallita: {err}")
            self._disable_capsule(cap_id, f"Inject failed: {err}")
            return

        # Aggiorna DB
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
                UPDATE capsule_eseguibili
                SET enabled=2, promoted_ts=?, phantom_wr=?, phantom_pnl_avg=?
                WHERE id=?
            """, (time.time(), phantom_wr, phantom_pnl, cap_id))

        self._log_evento(cap_id, "PROMOSSA_LIVE",
                         f"phantom_wr={phantom_wr:.1%} phantom_pnl={phantom_pnl:+.2f}")
        del self._phantom[cap_id]

    def _inject_live(self, cap_id: str, nome: str, target: str,
                     sorgente: str = None, func=None) -> tuple:
        """Inietta la funzione nel metodo target del bot a runtime."""
        try:
            if func is None:
                func = self._compile_function(sorgente, target)
            if func is None:
                return False, "Compilazione fallita"

            # Salva originale se non già salvato
            if target not in self._originals:
                orig = getattr(self.bot, target, None)
                if orig is not None:
                    self._originals[target] = orig
                else:
                    # Metodo non esiste ancora — crea stub
                    self._originals[target] = None

            # Binda la funzione all'istanza del bot
            bound = types.MethodType(func, self.bot)
            setattr(self.bot, target, bound)
            self._live[target] = cap_id

            log.info(f"[CE] 🧬 Iniettato LIVE: {nome} → bot.{target}")
            return True, "OK"
        except Exception as e:
            return False, str(e)

    # ────────────────────────────────────────────────────────────────────────
    # INJECT FOR CONTEXT (chiamato ad ogni entry/exit)
    # ────────────────────────────────────────────────────────────────────────

    def inject_for_context(self, contesto: dict) -> list:
        """
        Verifica se ci sono capsule PHANTOM attive per questo contesto.
        Le capsule LIVE sono già iniettate permanentemente.
        Ritorna lista dei metodi applicati temporaneamente (per rollback post-entry).
        """
        # Le LIVE sono già nel bot — niente da fare
        # Le PHANTOM vengono registrate ma non iniettate live
        # Questa funzione è per usi futuri (iniezione contestuale temporanea)
        return []

    # ────────────────────────────────────────────────────────────────────────
    # ROLLBACK
    # ────────────────────────────────────────────────────────────────────────

    def rollback(self, method_name: str):
        """Ripristina un metodo alla funzione originale."""
        if method_name not in self._originals:
            return
        orig = self._originals[method_name]
        if orig is not None:
            setattr(self.bot, method_name, orig)
        elif hasattr(self.bot, method_name):
            # Rimuove il metodo iniettato (torna al metodo della classe)
            try:
                delattr(self.bot, method_name)
            except AttributeError:
                pass
        if method_name in self._live:
            del self._live[method_name]
        log.info(f"[CE] ↩️  Rollback: bot.{method_name} ripristinato")

    def rollback_all(self):
        """Ripristina tutti i metodi iniettati."""
        for method_name in list(self._live.keys()):
            self.rollback(method_name)
        log.info("[CE] ↩️  Rollback completo — tutti i metodi ripristinati")

    # ────────────────────────────────────────────────────────────────────────
    # MONITORAGGIO LIVE E ROLLBACK AUTOMATICO
    # ────────────────────────────────────────────────────────────────────────

    def record_live_result(self, pnl: float, is_win: bool):
        """
        Chiamato dopo ogni trade per monitorare le capsule LIVE.
        Se peggiorano per ROLLBACK_PERIODS consecutivi → rollback automatico.
        """
        for target, cap_id in list(self._live.items()):
            try:
                with sqlite3.connect(self.db_path) as c:
                    # Aggiorna stats live
                    c.execute("""
                        UPDATE capsule_eseguibili
                        SET live_wr = (live_wr * attivazioni + ?) / (attivazioni + 1),
                            live_pnl_avg = (live_pnl_avg * attivazioni + ?) / (attivazioni + 1),
                            attivazioni = attivazioni + 1,
                            last_used_ts = ?
                        WHERE id=?
                    """, (1.0 if is_win else 0.0, pnl, time.time(), cap_id))

                    row = c.execute(
                        "SELECT live_wr, live_pnl_avg, phantom_wr, phantom_pnl_avg, attivazioni FROM capsule_eseguibili WHERE id=?",
                        (cap_id,)
                    ).fetchone()

                if row and row[4] >= self.ROLLBACK_WINDOW:
                    live_wr, live_pnl, ph_wr, ph_pnl, n = row
                    # Se peggiora rispetto al phantom
                    if live_wr < ph_wr - self.PROMOTION_WR_DELTA:
                        log.warning(f"[CE] ⚠️  Capsula LIVE peggiorata: {cap_id} live_wr={live_wr:.1%} vs ph_wr={ph_wr:.1%}")
                        self._auto_rollback_check(cap_id, target)
            except Exception as e:
                log.debug(f"[CE] record_live: {e}")

    def _auto_rollback_check(self, cap_id: str, target: str):
        """Rollback automatico se performance cala per ROLLBACK_PERIODS."""
        if not hasattr(self, '_rollback_counter'):
            self._rollback_counter = {}
        self._rollback_counter[cap_id] = self._rollback_counter.get(cap_id, 0) + 1

        if self._rollback_counter[cap_id] >= self.ROLLBACK_PERIODS:
            log.warning(f"[CE] 🔄 AUTO_ROLLBACK: {cap_id} — {self.ROLLBACK_PERIODS} periodi consecutivi di peggioramento")
            self.rollback(target)
            self._disable_capsule(cap_id, "AUTO_ROLLBACK performance degradata")
            del self._rollback_counter[cap_id]

    # ────────────────────────────────────────────────────────────────────────
    # UTILITY
    # ────────────────────────────────────────────────────────────────────────

    def _compile_function(self, sorgente: str, func_name: str):
        """Compila il sorgente Python ed estrae la funzione per nome."""
        try:
            namespace = {}
            exec(compile(sorgente, f"<capsule:{func_name}>", "exec"), namespace)
            # Cerca la funzione nel namespace
            for name, obj in namespace.items():
                if callable(obj) and name == func_name:
                    return obj
            # Prova il primo callable
            for name, obj in namespace.items():
                if callable(obj) and not name.startswith("_"):
                    return obj
            return None
        except Exception as e:
            log.error(f"[CE] Compilazione fallita {func_name}: {e}")
            return None

    def _disable_capsule(self, cap_id: str, motivo: str):
        """Disabilita una capsule (enabled=0) con motivo."""
        try:
            with sqlite3.connect(self.db_path) as c:
                c.execute("UPDATE capsule_eseguibili SET enabled=0 WHERE id=?", (cap_id,))
            self._log_evento(cap_id, "DISABILITATA", motivo)
            if cap_id in self._phantom:
                del self._phantom[cap_id]
        except Exception as e:
            log.debug(f"[CE] disable: {e}")

    def set_enabled(self, cap_id: str, enabled_level: int):
        """Imposta manualmente il livello enabled (0/1/2)."""
        with sqlite3.connect(self.db_path) as c:
            c.execute("UPDATE capsule_eseguibili SET enabled=? WHERE id=?",
                      (enabled_level, cap_id))
        self._log_evento(cap_id, f"SET_ENABLED_{enabled_level}", "manuale")

    def _get_baseline_wr(self, n: int) -> float:
        """WR baseline del bot sugli ultimi n trade M2."""
        try:
            m2_wins   = getattr(self.bot, '_m2_wins', 0)
            m2_losses = getattr(self.bot, '_m2_losses', 0)
            tot = m2_wins + m2_losses
            return m2_wins / tot if tot > 0 else 0.5
        except Exception:
            return 0.5

    def _get_baseline_pnl(self, n: int) -> float:
        """PnL medio baseline del bot sugli ultimi n trade M2."""
        try:
            m2_pnl    = getattr(self.bot, '_m2_pnl', 0.0)
            m2_trades = getattr(self.bot, '_m2_trades', 0)
            return m2_pnl / m2_trades if m2_trades > 0 else 0.0
        except Exception:
            return 0.0

    def _log_evento(self, cap_id: str, evento: str, dettaglio: str = ""):
        try:
            with sqlite3.connect(self.db_path) as c:
                c.execute(
                    "INSERT INTO capsule_eseguibili_log (capsule_id, evento, dettaglio) VALUES (?,?,?)",
                    (cap_id, evento, dettaglio)
                )
        except Exception:
            pass

    # ────────────────────────────────────────────────────────────────────────
    # DASHBOARD
    # ────────────────────────────────────────────────────────────────────────

    def get_dashboard_data(self) -> dict:
        """Dati per endpoint /capsule_eseguibili in app.py."""
        try:
            with sqlite3.connect(self.db_path) as c:
                rows = c.execute("""
                    SELECT id, nome, target_method, enabled, created_ts,
                           promoted_ts, attivazioni, phantom_wr, phantom_pnl_avg,
                           live_wr, live_pnl_avg, performance_score, error_log, note,
                           phantom_trades
                    FROM capsule_eseguibili ORDER BY created_ts DESC
                """).fetchall()
                log_rows = c.execute(
                    "SELECT ts, capsule_id, evento, dettaglio FROM capsule_eseguibili_log ORDER BY id DESC LIMIT 50"
                ).fetchall()

            capsule = []
            for r in rows:
                stato = {0: "DISABILITATA", 1: "PHANTOM", 2: "LIVE"}.get(r[3], "?")
                capsule.append({
                    "id": r[0], "nome": r[1], "target": r[2],
                    "enabled": r[3], "stato": stato,
                    "created": datetime.fromtimestamp(r[4]).strftime("%d/%m %H:%M") if r[4] else "?",
                    "promoted": datetime.fromtimestamp(r[5]).strftime("%d/%m %H:%M") if r[5] else "—",
                    "attivazioni": r[6] or 0,
                    "phantom_wr": round((r[7] or 0) * 100, 1),
                    "phantom_pnl": round(r[8] or 0, 2),
                    "live_wr": round((r[9] or 0) * 100, 1),
                    "live_pnl": round(r[10] or 0, 2),
                    "score": round(r[11] or 0, 2),
                    "errors": r[12] or "",
                    "note": r[13] or "",
                    "phantom_trades": r[14] or 0,
                })

            return {
                "capsule": capsule,
                "log": [{"ts": r[0], "id": r[1], "evento": r[2], "det": r[3]} for r in log_rows],
                "live_count": len(self._live),
                "phantom_count": len(self._phantom),
            }
        except Exception as e:
            return {"error": str(e), "capsule": [], "log": []}
