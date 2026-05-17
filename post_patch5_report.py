#!/usr/bin/env python3
"""
post_patch5_report.py — Giudice diagnostico post-PATCH 5

Scopo: rispondere a UNA domanda precisa:
  Il bot perde perché entra male, oppure entra abbastanza bene
  ma prende movimenti troppo piccoli?

NON è una dashboard. È un GIUDICE.
Solo lettura DB. Nessuna modifica trading. Nessuna soglia nuova.

Categorie classificate:
  WIN_NET     — trade che ha pagato davvero (netto > 0)
  LOSS_FEE    — perdita vicina al solo costo fee (entry in punto morto)
  LOSS_REAL   — perdita direzionale vera (entry sbagliata contro movimento)
  SUBFEE_HOLD — trade quasi giusto ma non maturo (lordo positivo sotto fee)

Soglie:
  Fee round-trip = $2.00 (FEE_TRADE)
  LOSS_FEE: lordo tra -$0.50 e +$0.50 (punto morto)
  LOSS_REAL: lordo < -$0.50 (movimento contrario)
  Sopra-3-lordo: lordo >= +$3.00 (spazio per pagare il mestiere)
  Morti-vicino-zero: lordo tra -$0.50 e +$0.50

Uso (su Render Shell):
  python3 post_patch5_report.py
  
  oppure con path custom:
  python3 post_patch5_report.py /var/data/trading_data.db
"""
import sqlite3
import json
import sys
import os
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================
DEFAULT_DB = "/var/data/trading_data.db"
FEE = 2.00                      # fee round-trip in $
LORDO_SOGLIA_REAL_LOSS = -0.50  # sotto questo lordo = LOSS_REAL
LORDO_SOGLIA_ZERO_HI = 0.50     # sopra/sotto questo = "vicino a zero"
LORDO_SOGLIA_SOPRA_3 = 3.00     # sopra questo = trade economicamente buono
LIMIT_TRADES = 200              # max trade da analizzare (window)


def classify(pnl_netto, pnl_lordo, reason):
    """Classifica il trade in 4 categorie diagnostiche."""
    if pnl_netto is None:
        return 'UNKNOWN'
    
    r = (reason or '').upper()
    
    # WIN_NET: vittoria netta vera (label o pnl netto > 0)
    if 'WIN_NET' in r or pnl_netto > 0:
        return 'WIN_NET'
    
    # SUBFEE_HOLD: log di pausa, non chiude (non dovrebbe entrare nei trade DB)
    if 'SUBFEE' in r:
        return 'SUBFEE_HOLD'
    
    # Adesso classifichiamo le LOSS in due tipi
    if pnl_lordo is None:
        # Fallback: stima lordo da netto + fee
        pnl_lordo = pnl_netto + FEE
    
    # LOSS_FEE: lordo vicino a zero (entry in punto morto, perde solo per la fee)
    if pnl_lordo >= LORDO_SOGLIA_REAL_LOSS:
        return 'LOSS_FEE'
    
    # LOSS_REAL: lordo significativamente negativo (entry contro movimento)
    return 'LOSS_REAL'


def is_post_patch5(reason):
    """Trade è post-PATCH 5 se ha label nuove di PATCH 3+."""
    if not reason:
        return False
    r = reason.upper()
    return ('WIN_NET' in r) or ('SUBFEE' in r) or ('LOSS_-' in r) or ('LOSS_+' in r)


def main(db_path=DEFAULT_DB):
    if not os.path.exists(db_path):
        print(f"ERROR: DB non trovato in {db_path}")
        print(f"Prova: python3 {sys.argv[0]} /path/to/trading_data.db")
        sys.exit(1)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Leggo gli ultimi N trade M2_EXIT
    cur.execute("""
        SELECT id, timestamp, pnl, reason, data_json
        FROM trades
        WHERE event_type='M2_EXIT'
        ORDER BY id DESC
        LIMIT ?
    """, (LIMIT_TRADES,))
    
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        print("Nessun trade M2_EXIT trovato nel DB.")
        return
    
    # Filtro post-PATCH 5
    post = []
    pre = []
    for r in rows:
        try:
            data = json.loads(r['data_json']) if r['data_json'] else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        
        rec = {
            'id': r['id'],
            'ts': r['timestamp'],
            'pnl': float(r['pnl'] or 0),
            'pnl_lordo': data.get('pnl_lordo'),
            'pnl_netto': data.get('pnl_netto'),
            'reason': r['reason'] or '',
            'momentum': data.get('momentum') or '?',
            'volatility': data.get('volatility') or '?',
            'trend': data.get('trend') or '?',
            'direction': data.get('direction') or '?',
            'regime': data.get('regime') or '?',
            'duration': data.get('duration'),
            'soglia': data.get('soglia'),
        }
        # Fallback lordo se non c'è nel DB
        if rec['pnl_lordo'] is None and rec['pnl'] is not None:
            rec['pnl_lordo'] = rec['pnl'] + FEE
        
        if is_post_patch5(rec['reason']):
            post.append(rec)
        else:
            pre.append(rec)
    
    n_pre = len(pre)
    n_post = len(post)
    
    print("=" * 72)
    print(" GIUDICE DIAGNOSTICO POST-PATCH 5")
    print("=" * 72)
    print(f"DB:                       {db_path}")
    print(f"Trade in window:          {len(rows)} (cap a {LIMIT_TRADES})")
    print(f"  Pre-PATCH 3:            {n_pre} (esclusi dal giudizio)")
    print(f"  Post-PATCH 3/4/5:       {n_post}")
    print()
    
    if n_post == 0:
        print("Nessun trade post-PATCH 5. Niente da giudicare.")
        return
    
    # ========================================================
    # CLASSIFICAZIONE 4-WAY
    # ========================================================
    counts = {'WIN_NET': 0, 'LOSS_FEE': 0, 'LOSS_REAL': 0, 'SUBFEE_HOLD': 0, 'UNKNOWN': 0}
    pnls = {'WIN_NET': [], 'LOSS_FEE': [], 'LOSS_REAL': [], 'SUBFEE_HOLD': []}
    lordi = {'WIN_NET': [], 'LOSS_FEE': [], 'LOSS_REAL': [], 'SUBFEE_HOLD': []}
    
    n_sopra_3_lordo = 0
    n_morti_zero = 0
    
    fp_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0, 'lordo_max': -999})
    
    for t in post:
        cat = classify(t['pnl_netto'] or t['pnl'], t['pnl_lordo'], t['reason'])
        counts[cat] += 1
        pnls[cat].append(t['pnl'])
        if t['pnl_lordo'] is not None:
            lordi[cat].append(t['pnl_lordo'])
            
            if t['pnl_lordo'] >= LORDO_SOGLIA_SOPRA_3:
                n_sopra_3_lordo += 1
            if -LORDO_SOGLIA_ZERO_HI <= t['pnl_lordo'] <= LORDO_SOGLIA_ZERO_HI:
                n_morti_zero += 1
        
        # Fingerprint stats
        fp = f"{t['momentum']}|{t['volatility']}|{t['trend']}|{t['direction']}"
        fp_stats[fp]['n'] += 1
        if cat == 'WIN_NET':
            fp_stats[fp]['wins'] += 1
        fp_stats[fp]['pnl'] += t['pnl']
        if t['pnl_lordo'] is not None:
            fp_stats[fp]['lordo_max'] = max(fp_stats[fp]['lordo_max'], t['pnl_lordo'])
    
    # ========================================================
    # SEZIONE 1 — DISTRIBUZIONE 4-WAY
    # ========================================================
    print("=" * 72)
    print(" 1. CLASSIFICAZIONE DIAGNOSTICA")
    print("=" * 72)
    print(f"  WIN_NET:        {counts['WIN_NET']:4d}  ({counts['WIN_NET']*100//n_post:2d}%)  trade che ha pagato davvero")
    print(f"  LOSS_FEE:       {counts['LOSS_FEE']:4d}  ({counts['LOSS_FEE']*100//n_post:2d}%)  perdita = solo costo fee (entry in punto morto)")
    print(f"  LOSS_REAL:      {counts['LOSS_REAL']:4d}  ({counts['LOSS_REAL']*100//n_post:2d}%)  perdita direzionale (entry contro movimento)")
    print(f"  SUBFEE_HOLD:    {counts['SUBFEE_HOLD']:4d}  ({counts['SUBFEE_HOLD']*100//n_post:2d}%)  trade quasi giusto, non maturato (raro nel DB)")
    print()
    print(f"  WR netto:       {counts['WIN_NET']*100/n_post:.1f}%  ({counts['WIN_NET']}/{n_post})")
    print()
    
    # ========================================================
    # SEZIONE 2 — PnL PER CATEGORIA
    # ========================================================
    print("=" * 72)
    print(" 2. PnL NETTO PER CATEGORIA")
    print("=" * 72)
    for cat in ['WIN_NET', 'LOSS_FEE', 'LOSS_REAL', 'SUBFEE_HOLD']:
        if pnls[cat]:
            avg = sum(pnls[cat]) / len(pnls[cat])
            tot = sum(pnls[cat])
            mx = max(pnls[cat])
            mn = min(pnls[cat])
            print(f"  {cat:13s} n={len(pnls[cat]):3d}  avg=${avg:+.2f}  tot=${tot:+.2f}  max=${mx:+.2f}  min=${mn:+.2f}")
        else:
            print(f"  {cat:13s} n=  0")
    print()
    pnl_tot = sum(t['pnl'] for t in post)
    print(f"  TOTALE PnL:    ${pnl_tot:+.2f}  ({pnl_tot/n_post:+.2f}/trade media)")
    print()
    
    # ========================================================
    # SEZIONE 3 — LORDO STIMATO
    # ========================================================
    print("=" * 72)
    print(" 3. SPAZIO ECONOMICO (PnL LORDO)")
    print("=" * 72)
    print(f"  Trade lordo ≥ +$3:        {n_sopra_3_lordo:3d}/{n_post}  ({n_sopra_3_lordo*100//n_post}%)")
    print(f"  Trade morti vicino zero:  {n_morti_zero:3d}/{n_post}  ({n_morti_zero*100//n_post}%)  (lordo tra ±$0.50)")
    print()
    
    # ========================================================
    # SEZIONE 4 — FINGERPRINT (chi vince, chi perde)
    # ========================================================
    print("=" * 72)
    print(" 4. FINGERPRINT — chi vince, chi perde")
    print("=" * 72)
    # Ordina per WR descendente, mostra top 5 + bottom 3
    fp_list = []
    for fp, s in fp_stats.items():
        if s['n'] >= 2:  # almeno 2 trade per essere statisticamente rilevante
            wr = s['wins'] / s['n'] * 100
            fp_list.append((fp, s['n'], wr, s['pnl'], s['lordo_max']))
    
    if not fp_list:
        print(f"  Pochi fingerprint con n≥2. Trade post-PATCH 5 = {n_post}.")
        print(f"  Tutti i fingerprint visti:")
        for fp, s in fp_stats.items():
            print(f"    {fp}  n={s['n']}  pnl=${s['pnl']:+.2f}")
    else:
        fp_list.sort(key=lambda x: (-x[2], -x[3]))  # WR desc, pnl desc
        print(f"  {'Fingerprint':<35} {'n':>3} {'WR':>6} {'PnL':>8} {'lordo_max':>10}")
        print(f"  {'-'*35} {'-'*3} {'-'*6} {'-'*8} {'-'*10}")
        for fp, n, wr, pnl, lmax in fp_list:
            print(f"  {fp:<35} {n:>3} {wr:>5.1f}% ${pnl:>+7.2f} ${lmax:>+9.2f}")
    print()
    
    # ========================================================
    # SEZIONE 5 — SEQUENZA TRADE (degrado o sano?)
    # ========================================================
    print("=" * 72)
    print(" 5. SEQUENZA TRADE (più recente a sinistra)")
    print("=" * 72)
    seq = []
    for t in post[:40]:
        cat = classify(t['pnl_netto'] or t['pnl'], t['pnl_lordo'], t['reason'])
        if cat == 'WIN_NET':
            seq.append('W')
        elif cat == 'LOSS_FEE':
            seq.append('f')  # minuscolo = fee-loss
        elif cat == 'LOSS_REAL':
            seq.append('L')  # maiuscolo = real-loss
        elif cat == 'SUBFEE_HOLD':
            seq.append('S')
        else:
            seq.append('?')
    print(f"  Pattern: {''.join(seq)}")
    print(f"  W=WIN_NET  f=LOSS_FEE(punto morto)  L=LOSS_REAL(contro)  S=SUBFEE")
    print()
    
    # Streak max
    seq_chrono = list(reversed(seq))
    max_w = max_l = max_f = cur_w = cur_l = cur_f = 0
    for c in seq_chrono:
        if c == 'W':
            cur_w += 1; cur_l = cur_f = 0; max_w = max(max_w, cur_w)
        elif c == 'L':
            cur_l += 1; cur_w = cur_f = 0; max_l = max(max_l, cur_l)
        elif c == 'f':
            cur_f += 1; cur_w = cur_l = 0; max_f = max(max_f, cur_f)
        else:
            cur_w = cur_l = cur_f = 0
    print(f"  Max WIN consecutive:        {max_w}")
    print(f"  Max LOSS_REAL consecutive:  {max_l}")
    print(f"  Max LOSS_FEE consecutive:   {max_f}")
    print()
    
    # ========================================================
    # SEZIONE 6 — DETTAGLIO ULTIMI 20 (per occhio)
    # ========================================================
    print("=" * 72)
    print(" 6. DETTAGLIO ULTIMI 20 TRADE")
    print("=" * 72)
    print(f"  {'ts':<20} {'cat':<10} {'pnl_n':>7} {'pnl_l':>7} {'fp':<25} {'sgl':>4}")
    print(f"  {'-'*20} {'-'*10} {'-'*7} {'-'*7} {'-'*25} {'-'*4}")
    for t in post[:20]:
        cat = classify(t['pnl_netto'] or t['pnl'], t['pnl_lordo'], t['reason'])
        ts = str(t['ts'])[:19]
        fp = f"{t['momentum']}|{t['volatility']}|{t['trend']}|{t['direction']}"[:25]
        pnl_n = t['pnl']
        pnl_l = t['pnl_lordo'] if t['pnl_lordo'] is not None else 0
        sgl = t['soglia'] if t['soglia'] is not None else 0
        print(f"  {ts:<20} {cat:<10} ${pnl_n:>+6.2f} ${pnl_l:>+6.2f} {fp:<25} {int(sgl):>4}")
    print()
    
    # ========================================================
    # VERDETTO DIAGNOSTICO (la cosa che ChatGPT vuole)
    # ========================================================
    print("=" * 72)
    print(" VERDETTO DIAGNOSTICO")
    print("=" * 72)
    
    # Regole di ChatGPT, applicate letteralmente
    diagnosi = []
    
    pct_fee_loss = counts['LOSS_FEE'] * 100 / n_post
    pct_real_loss = counts['LOSS_REAL'] * 100 / n_post
    pct_sopra_3 = n_sopra_3_lordo * 100 / n_post
    
    if counts['LOSS_FEE'] > counts['LOSS_REAL'] and counts['LOSS_FEE'] >= n_post * 0.3:
        diagnosi.append(
            f"PROBLEMA ENTRY IN PUNTI MORTI ({pct_fee_loss:.0f}% LOSS_FEE > {pct_real_loss:.0f}% LOSS_REAL)"
        )
        diagnosi.append("  → il bot entra in zone dove il prezzo non si muove.")
        diagnosi.append("  → next: SINAPSI per misurare anticipo verde sulla blu.")
    
    if n_sopra_3_lordo > 0 and counts['WIN_NET'] < n_sopra_3_lordo:
        gap = n_sopra_3_lordo - counts['WIN_NET']
        diagnosi.append(
            f"PROBLEMA EXIT / PROFIT LOCK ({gap} trade hanno superato +$3 lordo ma non sono WIN_NET)"
        )
        diagnosi.append("  → il bot prende il movimento ma esce troppo presto.")
        diagnosi.append("  → ricontrollare PROFIT_FLOOR_LOW/HIGH e LOCK_LOW_RETREAT.")
    
    if n_sopra_3_lordo == 0:
        diagnosi.append(
            f"NESSUN TRADE SUPERA +$3 LORDO ({n_post} trade analizzati)"
        )
        diagnosi.append("  → il bot non sta intercettando movimenti utili.")
        diagnosi.append("  → priorità assoluta: SINAPSI per capire dove sta il movimento.")
    
    # Verifico se torna il pattern WIN→LOSS→LOSS→LOSS
    pattern_degrado = 0
    for i in range(len(seq_chrono) - 3):
        if (seq_chrono[i] == 'W' and 
            seq_chrono[i+1] in ('L', 'f') and 
            seq_chrono[i+2] in ('L', 'f') and 
            seq_chrono[i+3] in ('L', 'f')):
            pattern_degrado += 1
    
    if pattern_degrado >= 2:
        diagnosi.append(
            f"PATTERN DEGRADO PRESENTE ({pattern_degrado} occorrenze WIN→LOSS×3+)"
        )
        diagnosi.append("  → il degrado dopo WIN è tornato. Ricontrollare moduli post-close:")
        diagnosi.append("    _analisi_l3_loss_streak, history_factor, comparto/nerv/breath.")
    elif counts['WIN_NET'] >= 2 and max_w >= 2:
        diagnosi.append(f"PATTERN DEGRADO ASSENTE (max WIN consecutive = {max_w})")
        diagnosi.append("  → PATCH 4/5 stanno tenendo. Pattern primo-paga-poi-degrada interrotto.")
    
    # Fingerprint vincenti
    if fp_list:
        top_fp = [f for f, n, wr, _, _ in fp_list if wr >= 50 and n >= 2]
        if top_fp:
            diagnosi.append(
                f"FINGERPRINT VINCENTI ({len(top_fp)}): {', '.join(top_fp[:3])}"
            )
            diagnosi.append("  → possibile filtro contestuale: entrare SOLO su questi fingerprint.")
            diagnosi.append("  → ma serve più dati prima di decidere (n piccolo).")
    
    if not diagnosi:
        diagnosi.append("Quadro misto — servono più trade post-PATCH 5 per diagnosi netta.")
        diagnosi.append(f"  Attuali: {n_post} trade. Target consigliato: 30+.")
    
    for d in diagnosi:
        print(f"  {d}")
    print()
    
    # ========================================================
    # PROSSIMI PASSI (decisione automatica)
    # ========================================================
    print("=" * 72)
    print(" PROSSIMO PASSO SUGGERITO (decisione automatica)")
    print("=" * 72)
    
    if n_sopra_3_lordo == 0 or pct_fee_loss > 50:
        print("  → SINAPSI logger PRIMA di qualsiasi altra patch.")
        print("    Serve sapere a quale orizzonte la verde anticipa la blu.")
        print("    Senza quel dato, ogni modifica entry è a sentimento.")
    elif n_sopra_3_lordo > 0 and counts['WIN_NET'] < n_sopra_3_lordo // 2:
        print("  → Indagare moduli exit residui (PROFIT_FLOOR, retreat).")
        print("    Il bot intercetta il movimento ma esce male.")
    elif pattern_degrado >= 2:
        print("  → Indagare moduli post-close (l3_loss_streak, history, comparto).")
        print("    Il degrado è tornato dopo PATCH 4/5.")
    else:
        print("  → Lascia girare. Quadro non ancora abbastanza definito.")
    print()


if __name__ == '__main__':
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    main(db)
