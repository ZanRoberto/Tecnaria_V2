#!/usr/bin/env python3
"""
inject_signals.py — Inietta segnali sintetici TRENDING_BULL nel signal tracker.
Basati su 10.058 trade reali V14. Chiamato al boot da app.py.
"""
import sqlite3, json, random, time

def inject(db_path: str):
    random.seed(99)

    TEMPLATE = [
        # regime, dir, mom, vol, trd, score_base, soglia, drift, rsi, hit_rate, avg_delta, n
        ("TRENDING_BULL","LONG","FORTE","BASSA","UP",   72,55,0.08,52,0.78, 45.0,30),
        ("TRENDING_BULL","LONG","FORTE","MEDIA","UP",   68,52,0.06,48,0.68, 32.0,22),
        ("TRENDING_BULL","LONG","MEDIO","BASSA","UP",   65,50,0.05,45,0.65, 28.0,18),
        ("TRENDING_BULL","LONG","MEDIO","MEDIA","UP",   60,48,0.04,42,0.58, 18.0,14),
        ("TRENDING_BULL","LONG","FORTE","ALTA","UP",    63,50,0.07,50,0.55, 22.0,11),
        ("RANGING","LONG","FORTE","ALTA","SIDEWAYS",    52,48,0.02,55,0.35, -8.0,37),
        ("RANGING","LONG","MEDIO","ALTA","SIDEWAYS",    48,48,0.01,50,0.28,-12.0,37),
        ("RANGING","LONG","DEBOLE","ALTA","SIDEWAYS",   44,44,0.00,48,0.12,-15.0,30),
    ]

    segnali = []
    for regime,d,mom,vol,trd,sc_b,sog,dr_b,rsi_b,hr,ad,n in TEMPLATE:
        for _ in range(n):
            sc  = sc_b  + random.gauss(0,3)
            dr  = dr_b  + random.gauss(0,0.01)
            rsi = rsi_b + random.gauss(0,8)
            hit = random.random() < hr
            delta = ad + random.gauss(0,abs(ad)*0.5) if hit else -abs(ad)*0.4+random.gauss(0,5)
            pnl = delta/70000*5000*0.7-2.0
            if sc>=75:   band="FORTE_75+"
            elif sc>=65: band="BUONO_65-75"
            elif sc>=58: band="BASE_58-65"
            else:        band="DEBOLE_<58"
            segnali.append({
                'regime':regime,'direction':d,'score_band':band,
                'drift':round(dr,4),'rsi':round(rsi,1),
                'results':{'delta_60':delta,'hit_60':hit,'pnl_60':pnl}
            })

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute(
        "SELECT key,value FROM bot_state WHERE key='signal_tracker'"
    ).fetchall())

    if 'signal_tracker' in rows:
        existing = json.loads(rows['signal_tracker'])
        stats = existing.get('stats', {})
        existing_closed = existing.get('total_closed', 0)
        # Se già iniettato con segnali TRENDING_BULL → skip
        if 'TRENDING_BULL|LONG|BUONO_65-75' in stats:
            conn.close()
            print(f"[SIGNAL_INJECT] ✅ Già presenti segnali TRENDING_BULL — skip")
            return
    else:
        stats = {}; existing_closed = 0

    injected = 0
    for sig in segnali:
        key = f"{sig['regime']}|{sig['direction']}|{sig['score_band']}"
        if key not in stats:
            stats[key] = {'n':0,'delta_30':[],'delta_60':[],'delta_120':[],
                          'hit_30':[],'hit_60':[],'hit_120':[],'pnl_sim':[]}
        s = stats[key]
        s['n'] += 1
        r = sig['results']
        s['delta_60'].append(r['delta_60'])
        s['hit_60'].append(r['hit_60'])
        s['pnl_sim'].append(r['pnl_60'])
        if len(s['delta_60'])>200: s['delta_60']=s['delta_60'][-200:]
        if len(s['hit_60'])>200:   s['hit_60']=s['hit_60'][-200:]
        if len(s['pnl_sim'])>200:  s['pnl_sim']=s['pnl_sim'][-200:]
        injected += 1

    conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('signal_tracker', ?)",
                 (json.dumps({'stats':stats,'total_closed':existing_closed+injected}),))
    conn.commit(); conn.close()

    print(f"[SIGNAL_INJECT] ✅ {injected} segnali iniettati — "
          f"TRENDING_BULL pronto per predizione")

if __name__ == '__main__':
    import sys
    db = sys.argv[1] if len(sys.argv)>1 else '/home/app/data/trading_data.db'
    inject(db)
