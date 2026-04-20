#!/usr/bin/env python3
"""
INJECT_BRAIN — Iniezione dati storici nell'Oracolo
====================================================
╔══════════════════════════════════════════════════════════════════════╗
║  LEGGE FONDAMENTALE — TUTTO IN USDC                                  ║
║  exposure=$5000 | fee=$2.00 | breakeven delta=+$30 BTC              ║
║  pnl_win/loss = delta BTC → convertiti in USDC netti                ║
║  Per essere profittevoli i delta WIN devono essere >> $30            ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sqlite3, json, random, argparse
random.seed(42)

EXPOSURE = 5000.0
BTC_REF  = 75000.0
BTC_QTY  = EXPOSURE / BTC_REF   # 0.06667
FEE      = 2.0

def to_usdc(delta_btc):
    return round(delta_btc * BTC_QTY - FEE, 4)

# ============================================================================
# DELTA BTC calibrati per essere profittevoli con futures leva 5x
# WIN deve essere abbastanza grande da coprire fee + perdite medie
# ============================================================================
FINGERPRINT_DATA = {
    # ── TRENDING UP — movimenti ampi, edge reale ─────────────────────────
    "LONG|FORTE|BASSA|UP":        {"wr":0.78,"pnl_win":55.0, "pnl_loss":-25.0,"dur_win":45,"dur_loss":18,"n":120},
    "LONG|FORTE|MEDIA|UP":        {"wr":0.68,"pnl_win":70.0, "pnl_loss":-30.0,"dur_win":38,"dur_loss":20,"n":90},
    "LONG|FORTE|ALTA|UP":         {"wr":0.55,"pnl_win":100.0,"pnl_loss":-45.0,"dur_win":28,"dur_loss":12,"n":60},
    "LONG|MEDIO|BASSA|UP":        {"wr":0.65,"pnl_win":65.0, "pnl_loss":-28.0,"dur_win":35,"dur_loss":22,"n":75},
    "LONG|MEDIO|MEDIA|UP":        {"wr":0.58,"pnl_win":75.0, "pnl_loss":-35.0,"dur_win":30,"dur_loss":18,"n":55},
    "LONG|DEBOLE|BASSA|UP":       {"wr":0.55,"pnl_win":80.0, "pnl_loss":-40.0,"dur_win":28,"dur_loss":20,"n":40},
    "LONG|MEDIO|ALTA|UP":         {"wr":0.50,"pnl_win":120.0,"pnl_loss":-60.0,"dur_win":25,"dur_loss":12,"n":35},

    # ── RANGING SIDEWAYS con bassa volatilità — unici con edge ───────────
    "LONG|FORTE|BASSA|SIDEWAYS":  {"wr":0.65,"pnl_win":55.0, "pnl_loss":-25.0,"dur_win":60,"dur_loss":35,"n":40},
    "LONG|FORTE|MEDIA|SIDEWAYS":  {"wr":0.60,"pnl_win":65.0, "pnl_loss":-30.0,"dur_win":55,"dur_loss":38,"n":50},

    # ── RANGING ad alta volatilità — TOSSICI, perde sempre ───────────────
    "LONG|FORTE|ALTA|SIDEWAYS":   {"wr":0.35,"pnl_win":40.0, "pnl_loss":-32.0,"dur_win":66,"dur_loss":43,"n":150},
    "LONG|MEDIO|BASSA|SIDEWAYS":  {"wr":0.50,"pnl_win":45.0, "pnl_loss":-28.0,"dur_win":50,"dur_loss":38,"n":30},
    "LONG|MEDIO|MEDIA|SIDEWAYS":  {"wr":0.45,"pnl_win":45.0, "pnl_loss":-30.0,"dur_win":55,"dur_loss":40,"n":45},
    "LONG|MEDIO|ALTA|SIDEWAYS":   {"wr":0.28,"pnl_win":35.0, "pnl_loss":-30.0,"dur_win":70,"dur_loss":45,"n":150},
    "LONG|DEBOLE|BASSA|SIDEWAYS": {"wr":0.55,"pnl_win":50.0, "pnl_loss":-28.0,"dur_win":40,"dur_loss":30,"n":25},
    "LONG|DEBOLE|MEDIA|SIDEWAYS": {"wr":0.35,"pnl_win":42.0, "pnl_loss":-28.0,"dur_win":35,"dur_loss":28,"n":35},
    "LONG|DEBOLE|ALTA|SIDEWAYS":  {"wr":0.12,"pnl_win":35.0, "pnl_loss":-28.0,"dur_win":20,"dur_loss":18,"n":120},

    # ── DOWN ─────────────────────────────────────────────────────────────
    "LONG|FORTE|BASSA|DOWN":      {"wr":0.60,"pnl_win":60.0, "pnl_loss":-32.0,"dur_win":20,"dur_loss":12,"n":30},
    "LONG|MEDIO|BASSA|DOWN":      {"wr":0.45,"pnl_win":55.0, "pnl_loss":-30.0,"dur_win":15,"dur_loss":10,"n":25},
    "LONG|FORTE|MEDIA|DOWN":      {"wr":0.50,"pnl_win":60.0, "pnl_loss":-32.0,"dur_win":18,"dur_loss":12,"n":20},

    # ── SHORT RANGING TOSSICI ─────────────────────────────────────────────
    "SHORT|MEDIO|ALTA|SIDEWAYS":  {"wr":0.08,"pnl_win":40.0, "pnl_loss":-28.0,"dur_win":15,"dur_loss":18,"n":100},
    "SHORT|FORTE|ALTA|SIDEWAYS":  {"wr":0.12,"pnl_win":42.0, "pnl_loss":-30.0,"dur_win":18,"dur_loss":20,"n":100},
    "SHORT|DEBOLE|ALTA|SIDEWAYS": {"wr":0.10,"pnl_win":38.0, "pnl_loss":-27.0,"dur_win":12,"dur_loss":15,"n":80},
    "SHORT|MEDIO|MEDIA|SIDEWAYS": {"wr":0.20,"pnl_win":45.0, "pnl_loss":-28.0,"dur_win":20,"dur_loss":22,"n":60},
    "SHORT|FORTE|MEDIA|SIDEWAYS": {"wr":0.25,"pnl_win":50.0, "pnl_loss":-30.0,"dur_win":22,"dur_loss":25,"n":50},

    # ── SHORT TRENDING DOWN ───────────────────────────────────────────────
    "SHORT|FORTE|ALTA|DOWN":      {"wr":0.55,"pnl_win":90.0, "pnl_loss":-42.0,"dur_win":25,"dur_loss":12,"n":40},
    "SHORT|MEDIO|ALTA|DOWN":      {"wr":0.48,"pnl_win":75.0, "pnl_loss":-38.0,"dur_win":20,"dur_loss":10,"n":35},
    "SHORT|DEBOLE|ALTA|DOWN":     {"wr":0.40,"pnl_win":65.0, "pnl_loss":-40.0,"dur_win":15,"dur_loss":8, "n":25},
}

MATRIMONI_DATA = {
    "STRONG_BULL":    {"wr":0.78,"n":120,"trust":85},
    "STRONG_MED":     {"wr":0.68,"n":90, "trust":75},
    "MEDIUM_BULL":    {"wr":0.65,"n":75, "trust":70},
    "CAUTIOUS":       {"wr":0.58,"n":55, "trust":60},
    "RANGE_VOL_F":    {"wr":0.52,"n":85, "trust":55},
    "RANGE_MED_F":    {"wr":0.60,"n":50, "trust":60},
    "RANGE_VOL_M":    {"wr":0.43,"n":70, "trust":45},
    "RANGE_NEUTRAL":  {"wr":0.45,"n":45, "trust":45},
    "RANGE_VOL_W":    {"wr":0.19,"n":60, "trust":20},
    "WEAK_NEUTRAL":   {"wr":0.35,"n":35, "trust":35},
    "STRONG_VOLATILE":{"wr":0.55,"n":45, "trust":55},
    "PANIC":          {"wr":0.15,"n":30, "trust":10},
    "TRAP":           {"wr":0.05,"n":25, "trust":5},
}


def build_oracolo_entry(fp, data):
    n=data["n"]; wr=data["wr"]
    pw=to_usdc(data["pnl_win"]); pl=to_usdc(data["pnl_loss"])
    peso=n*0.25; samples=peso; wins=peso*wr
    pnl_sum=wins*pw+(samples-wins)*pl
    dw=data["dur_win"]; dl=data["dur_loss"]
    durs_w=[max(5,random.gauss(dw,dw*0.25)) for _ in range(min(20,int(peso*wr)))]
    durs_l=[max(3,random.gauss(dl,dl*0.25)) for _ in range(min(20,int(peso*(1-wr))))]
    if "UP"   in fp: dfw=[random.gauss(0.08,0.04) for _ in range(min(15,len(durs_w)))]; dfl=[random.gauss(-0.02,0.05) for _ in range(min(15,len(durs_l)))]
    elif "DOWN" in fp: dfw=[random.gauss(-0.06,0.04) for _ in range(min(15,len(durs_w)))]; dfl=[random.gauss(0.02,0.05) for _ in range(min(15,len(durs_l)))]
    else: dfw=[random.gauss(0.02,0.05) for _ in range(min(15,len(durs_w)))]; dfl=[random.gauss(-0.03,0.05) for _ in range(min(15,len(durs_l)))]
    np_=min(10,int(peso*0.5))
    return {"wins":round(wins,2),"samples":round(samples,2),"pnl_sum":round(pnl_sum,4),
            "real_samples":0,"durations_win":durs_w,"durations_loss":durs_l,
            "rsi_win":[random.gauss(52,8) for _ in range(min(15,len(durs_w)))],
            "rsi_loss":[random.gauss(58,10) for _ in range(min(15,len(durs_l)))],
            "drift_win":dfw,"drift_loss":dfl,
            "range_pos_win":[random.uniform(0.3,0.8) for _ in range(len(durs_w))],
            "range_pos_loss":[random.uniform(0.2,0.7) for _ in range(len(durs_l))],
            "post_continued":[random.random()<max(0.1,0.6-wr*0.5) for _ in range(np_)],
            "post_delta":[random.gauss(2.0,3.0) for _ in range(np_)]}


def build_memoria(data):
    t={};w={};l={};h={};s={};b={}
    for mat,d in data.items():
        p=d["n"]*0.3; wi=int(p*d["wr"]); lo=int(p*(1-d["wr"]))
        t[mat]=d["trust"]; w[mat]=wi; l[mat]=lo
        h[mat]=[round(d["wr"]+random.gauss(0,0.05),3) for _ in range(min(10,wi+lo))]
        s[mat]=0; b[mat]=0
    return {"trust":t,"separazione":s,"blacklist":b,"divorzio":[],"wins":w,"losses":l,"wr_history":h}


def inject(db_path, dry_run=False):
    print(f"\n{'='*60}")
    print(f"  INJECT_BRAIN USDC | exposure=${EXPOSURE} fee=${FEE} be=+${FEE/BTC_QTY:.0f}BTC")
    print(f"  {'DRY RUN' if dry_run else 'LIVE — scrittura DB'}")
    print(f"{'='*60}\n")

    oracolo={}; prof=0
    for fp,d in FINGERPRINT_DATA.items():
        e=build_oracolo_entry(fp,d)
        oracolo[fp]=e
        avg=e["pnl_sum"]/e["samples"] if e["samples"]>0 else 0
        wr=e["wins"]/e["samples"] if e["samples"]>0 else 0
        ok="✅" if avg>0 else "❌"
        if avg>0: prof+=1
        print(f"  {ok} {fp:<38} WR={wr:.0%} pnl_avg={avg:+.2f}$")

    print(f"\n  Profittevoli: {prof}/{len(oracolo)}")
    mem=build_memoria(MATRIMONI_DATA)

    if dry_run:
        print("\n  [DRY RUN] Nessuna scrittura."); return

    try:
        conn=sqlite3.connect(db_path)
        rows=dict(conn.execute("SELECT key,value FROM bot_state").fetchall())
        if 'oracolo' in rows:
            ex=json.loads(rows['oracolo'])
            mg=dict(oracolo)
            for fp,re in ex.items():
                if re.get('real_samples',0)>0:
                    if fp in mg:
                        mg[fp]['wins']+=re.get('wins',0); mg[fp]['samples']+=re.get('samples',0)
                        mg[fp]['pnl_sum']+=re.get('pnl_sum',0); mg[fp]['real_samples']=re.get('real_samples',0)
                    else: mg[fp]=re
            oracolo=mg
            print(f"\n  Merge con {len(ex)} fingerprint esistenti")
        conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('oracolo',?)",(json.dumps(oracolo),))
        if 'memoria' in rows:
            em=json.loads(rows['memoria'])
            rd=em.get('divorzio',[])
            if rd: mem['divorzio']=rd
        conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('memoria',?)",(json.dumps(mem),))
        conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('brain_injected','1')")
        conn.commit(); conn.close()
        print(f"\n  ✅ Brain USDC iniettato — {len(oracolo)} fingerprint")
    except Exception as e:
        print(f"\n  ❌ {e}"); import traceback; traceback.print_exc()


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="trading_data.db")
    p.add_argument("--dry-run",action="store_true")
    a=p.parse_args()
    inject(a.db,a.dry_run)
