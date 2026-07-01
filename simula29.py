#!/usr/bin/env python3
"""
SIMULA29 — ANALISI GIORNO FORTE: 4 GIUGNO + ROBUSTEZZA LEAVE-ONE-OUT

Domande:
1. Cosa aveva di speciale il 4 giugno? BTC si muoveva forte quel giorno?
2. Quanti giorni "forti" ci sono stati nel mese?
3. Il netto SENZA il giorno migliore regge positivo?
4. Leave-one-out: netto con ogni giorno rimosso uno alla volta.

Lancio: python3 simula29.py
"""
import sqlite3, json, os, datetime, time, urllib.request, urllib.parse

DB         = "/var/data/trading_data.db"
CACHE_FILE = "/tmp/campo_cache.json"
SYM        = "BTCUSDC"

# ── carica DB ─────────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
ts_col = next((c for c in ["trade_ts","ts","timestamp","created_at","open_time"]
               if c in cols), None)
rows = con.execute(
    f"SELECT {ts_col}, peak_nascita, pnl_finale, curva_json "
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL ORDER BY {ts_col} ASC"
).fetchall()
con.close()

def klines_to_metrics(klines):
    if not klines or len(klines)<4: return None
    closes=[float(k[4]) for k in klines]; opens=[float(k[1]) for k in klines]
    vols=[float(k[5]) for k in klines]
    mom_now=closes[-1]-closes[-2]; mom_prev=closes[-3]-closes[-4] if len(closes)>=5 else 0
    return {"trend_5m":closes[-1]-closes[0],"mom_last":mom_now,"trend_accel":mom_now-mom_prev,
            "bull_count":sum(1 for c,o in zip(closes,opens) if c>o)}

def fetch_klines(ts_epoch, n=6):
    ts_ms=int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    params=urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}",timeout=8) as r:
            return json.loads(r.read())
    except Exception: return None

cache_map={}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE) as f:
        for item in json.load(f): cache_map[str(item["ts"])]=item["metrics"]

records=[]; to_save=[]
for ts,peak,pnlf,cj in rows:
    ts_str=str(ts)
    if ts_str in cache_map: m=cache_map[ts_str]
    else:
        kl=fetch_klines(ts); m=klines_to_metrics(kl) if kl else None
        cache_map[ts_str]=m; to_save.append({"ts":ts,"metrics":m}); time.sleep(0.05)
    if m is None: continue
    rec=dict(m); rec.update({"ts":ts,"peak":float(peak or 0),"pnl_fin":float(pnlf)})
    records.append(rec)

if to_save:
    existing=[]
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f: existing=json.load(f)
    with open(CACHE_FILE,"w") as f: json.dump(existing+to_save,f)

def filtro(r): return r["trend_accel"]>20 and r["mom_last"]>0 and r["trend_5m"]>200
def ts_dt(ts):
    s=int(ts) if int(ts)<1e12 else int(ts)//1000
    return datetime.datetime.utcfromtimestamp(s)

filtrati=[r for r in records if filtro(r)]
N_f=len(filtrati)
netto_tot=sum(r["pnl_fin"] for r in filtrati)

print(f"Trade filtrati: {N_f}  netto totale: ${netto_tot:+.2f}")
print()

# netto per giorno sui filtrati
from collections import defaultdict
giorni_netto=defaultdict(float)
giorni_n    =defaultdict(int)
for r in filtrati:
    d=ts_dt(r["ts"]).strftime("%Y-%m-%d")
    giorni_netto[d]+=r["pnl_fin"]
    giorni_n[d]+=1

print("="*64)
print("NETTO PER GIORNO (trade filtrati)")
print("="*64)
print(f"  {'DATA':<12}  {'N':>3}  {'netto':>8}  {'avg':>7}  {'cum':>8}")
print(f"  {'-'*52}")
cum=0
for d in sorted(giorni_netto.keys()):
    cum+=giorni_netto[d]
    print(f"  {d:<12}  {giorni_n[d]:>3}  {giorni_netto[d]:>+8.2f}  "
          f"{giorni_netto[d]/giorni_n[d]:>+7.2f}  {cum:>+8.2f}")
print()

# ── LEAVE-ONE-OUT ─────────────────────────────────────────────────────────────
print("="*64)
print("LEAVE-ONE-OUT — netto senza ogni giorno")
print("="*64)
print(f"  {'ESCLUDO':12}  {'N_rim':>6}  {'netto_rim':>10}  STATUS")
print(f"  {'-'*44}")
for d_escluso in sorted(giorni_netto.keys()):
    rim=[r for r in filtrati if ts_dt(r["ts"]).strftime("%Y-%m-%d")!=d_escluso]
    nr=sum(r["pnl_fin"] for r in rim)
    status="✓ POSITIVO" if nr>0 else ("~ quasi" if nr>=-5 else "✗ negativo")
    print(f"  {d_escluso:<12}  {len(rim):>6}  {nr:>+10.2f}  {status}")
print()

# ── ANALISI 4 GIUGNO: BTC quel giorno ────────────────────────────────────────
print("="*64)
print("ANALISI 4 GIUGNO — BTC si muoveva forte?")
print("="*64)
print()

# prendo i timestamp dei trade filtrati del 4 giugno
giugno4=[r for r in filtrati if ts_dt(r["ts"]).strftime("%Y-%m-%d")=="2026-06-04"]
if not giugno4:
    # prova anno diverso
    for d in sorted(giorni_netto.keys()):
        if "06-04" in d or d.endswith("-06-04"):
            giugno4=[r for r in filtrati if ts_dt(r["ts"]).strftime("%Y-%m-%d")==d]
            break

if giugno4:
    print(f"  Trade filtrati in quel giorno: {len(giugno4)}")
    print(f"  Netto: ${sum(r['pnl_fin'] for r in giugno4):+.2f}")
    print(f"  accel medio: {sum(r['trend_accel'] for r in giugno4)/len(giugno4):+.1f}")
    print(f"  t5m medio:   {sum(r['trend_5m'] for r in giugno4)/len(giugno4):+.1f}")
    print()
    # fetch kline orarie di quel giorno per vedere il range BTC
    ts_giorno = giugno4[0]["ts"]
    ts_ms = int(ts_giorno)*1000 if int(ts_giorno)<1e12 else int(ts_giorno)
    # kline orarie: 24 ore di quel giorno
    try:
        params=urllib.parse.urlencode({"symbol":SYM,"interval":"1h",
                                       "endTime":ts_ms+24*3600*1000,"limit":24})
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}",timeout=8) as r:
            kh=json.loads(r.read())
        if kh:
            highs=[float(k[2]) for k in kh]; lows=[float(k[3]) for k in kh]
            closes=[float(k[4]) for k in kh]; opens=[float(k[1]) for k in kh]
            vols=[float(k[5]) for k in kh]
            rng=max(highs)-min(lows)
            mov=closes[-1]-opens[0]
            print(f"  BTC quel giorno: open={opens[0]:.0f}$  close={closes[-1]:.0f}$")
            print(f"  Movimento netto: {mov:+.0f}$  Range: {rng:.0f}$")
            print(f"  Volume totale:   {sum(vols):.0f} BTC")
    except Exception as e:
        print(f"  (fetch kline giornaliere: {e})")
    print()

# ── QUANTI GIORNI FORTI? ──────────────────────────────────────────────────────
print("="*64)
print("GIORNI FORTI NEL MESE (range BTC > soglia)")
print("="*64)
print()

# per ogni giorno del dataset, fetch range giornaliero
print("  Fetching range giornaliero BTC per ogni giorno...")
giorni_unici=sorted(set(ts_dt(r["ts"]).strftime("%Y-%m-%d") for r in records))
giorni_range={}
for d in giorni_unici:
    try:
        # prendo timestamp fine giorno
        dt=datetime.datetime.strptime(d,"%Y-%m-%d")
        ts_end=int((dt+datetime.timedelta(days=1)).timestamp())*1000
        params=urllib.parse.urlencode({"symbol":SYM,"interval":"1d",
                                       "endTime":ts_end,"limit":1})
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}",timeout=5) as r:
            kd=json.loads(r.read())
        if kd:
            h=float(kd[0][2]); l=float(kd[0][3])
            giorni_range[d]=h-l
        time.sleep(0.1)
    except Exception:
        giorni_range[d]=None

SOGLIA_FORTE=1500   # $1500 range = giorno forte
print(f"  {'DATA':<12}  {'RANGE_BTC':>10}  {'FORTE?':>8}  {'N_filt':>7}  {'netto_filt':>11}")
print(f"  {'-'*58}")
n_forti=0
for d in giorni_unici:
    rng=giorni_range.get(d)
    nf=giorni_netto.get(d,0)
    nn=giorni_n.get(d,0)
    forte="✓ FORTE" if rng and rng>=SOGLIA_FORTE else "  piano"
    if rng and rng>=SOGLIA_FORTE: n_forti+=1
    rng_str=f"{rng:.0f}$" if rng else "N/A"
    print(f"  {d:<12}  {rng_str:>10}  {forte:>8}  {nn:>7}  {nf:>+11.2f}")

print()
print(f"  Giorni FORTI (range>{SOGLIA_FORTE}$): {n_forti} / {len(giorni_unici)}")
print()

# ── VERDETTO ──────────────────────────────────────────────────────────────────
print("="*64)
print("VERDETTO — IL SEGNALE REGGE O È UN GIORNO FORTUNATO?")
print("="*64)
print()
# conta leave-one-out positivi
loo_positivi=0
for d_escluso in giorni_netto:
    rim=sum(r["pnl_fin"] for r in filtrati if ts_dt(r["ts"]).strftime("%Y-%m-%d")!=d_escluso)
    if rim>0: loo_positivi+=1

tot_giorni=len(giorni_netto)
print(f"  Netto con tutti i giorni: ${netto_tot:+.2f}")
print(f"  Leave-one-out positivi: {loo_positivi}/{tot_giorni} giorni")
print()
if loo_positivi==tot_giorni:
    print("  ✓ ROBUSTO: positivo anche rimuovendo qualsiasi singolo giorno.")
elif loo_positivi>=tot_giorni*0.7:
    print(f"  ~ PARZIALMENTE ROBUSTO: positivo in {loo_positivi}/{tot_giorni} casi.")
    print("  Il segnale dipende parzialmente da 1-2 giorni forti.")
    print("  Il paper trading su 2-3 mesi lo chiarirà.")
else:
    print(f"  ✗ FRAGILE: positivo solo in {loo_positivi}/{tot_giorni} casi leave-one-out.")
    print("  Il segnale dipende da pochi giorni eccezionali.")
print()
print("  IMPLICAZIONE PER IL PAPER TRADING:")
print(f"  Il filtro cattura ~{N_f/len(giorni_unici):.1f} trade/giorno.")
print(f"  Con 2-3 mesi di paper (60-90 giorni) ci aspettiamo")
print(f"  ~{int(N_f/len(giorni_unici)*60)}-{int(N_f/len(giorni_unici)*90)} trade filtrati.")
print(f"  Se il netto paper è > 0 dopo 50+ trade → go-live.")
print()
