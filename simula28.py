#!/usr/bin/env python3
"""
SIMULA28 — DISTRIBUZIONE TEMPORALE DEI 20 TRADE FILTRATI

Domanda: i 20 trade con (accel>20 AND mom>0 AND t5m>200) sono
distribuiti su tutto il mese o concentrati in 2-3 giorni fortunati?

Se sono in 2-3 giorni → segnale fragile (regime specifico).
Se sono sparsi → segnale più robusto.
"""
import sqlite3, json, os, datetime, urllib.request, urllib.parse, time

DB         = "/var/data/trading_data.db"
CACHE_FILE = "/tmp/campo_cache.json"
SYM        = "BTCUSDC"

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
    highs=[float(k[2]) for k in klines];  lows=[float(k[3]) for k in klines]
    vols=[float(k[5]) for k in klines]
    mom_now=closes[-1]-closes[-2]; mom_prev=closes[-3]-closes[-4] if len(closes)>=5 else 0
    return {"trend_5m":closes[-1]-closes[0],"mom_last":mom_now,"trend_accel":mom_now-mom_prev,
            "bull_count":sum(1 for c,o in zip(closes,opens) if c>o)}

cache_map={}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE) as f:
        for item in json.load(f): cache_map[str(item["ts"])]=item["metrics"]

def fetch_klines(ts_epoch, n=6):
    ts_ms=int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    params=urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}",timeout=8) as r:
            return json.loads(r.read())
    except Exception: return None

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
def ts_to_dt(ts):
    s=int(ts) if int(ts)<1e12 else int(ts)//1000
    return datetime.datetime.utcfromtimestamp(s)

filtrati=[r for r in records if filtro(r)]
tutti=records

print(f"Trade totali: {len(tutti)}")
print(f"Trade filtrati (accel>20 AND mom>0 AND t5m>200): {len(filtrati)}")
print()

# distribuzione giornaliera
from collections import Counter, defaultdict
giorni_filtrati=Counter(ts_to_dt(r["ts"]).strftime("%Y-%m-%d") for r in filtrati)
giorni_tutti   =Counter(ts_to_dt(r["ts"]).strftime("%Y-%m-%d") for r in tutti)

tutti_giorni=sorted(set(list(giorni_filtrati.keys())+list(giorni_tutti.keys())))

print("="*72)
print("DISTRIBUZIONE GIORNALIERA")
print("="*72)
print(f"  {'DATA':<12}  {'TOT':>5}  {'FILT':>5}  {'%':>5}  {'netto_filt':>11}  BARRA")
print(f"  {'-'*60}")

giorni_con_filtrati=0
netti_per_giorno=defaultdict(float)
for r in filtrati:
    d=ts_to_dt(r["ts"]).strftime("%Y-%m-%d")
    netti_per_giorno[d]+=r["pnl_fin"]

for d in tutti_giorni:
    n_tot=giorni_tutti.get(d,0)
    n_fil=giorni_filtrati.get(d,0)
    pct=100*n_fil/n_tot if n_tot else 0
    netto_d=netti_per_giorno.get(d,0)
    barra="█"*n_fil
    if n_fil>0: giorni_con_filtrati+=1
    flag=" ←" if n_fil>=3 else ""
    print(f"  {d:<12}  {n_tot:>5}  {n_fil:>5}  {pct:>4.0f}%  {netto_d:>+11.2f}  {barra}{flag}")

print()
print(f"  Giorni con almeno 1 trade filtrato: {giorni_con_filtrati} / {len(tutti_giorni)}")
print()

# concentrazione: quanti giorni coprono il 80% dei trade filtrati?
giorni_ord=sorted(giorni_filtrati.items(), key=lambda x:x[1], reverse=True)
cumulato=0; n80=0
for d,n in giorni_ord:
    cumulato+=n; n80+=1
    if cumulato>=0.8*len(filtrati): break

print(f"  80% dei trade filtrati è in {n80} giorni (su {giorni_con_filtrati} totali)")
print()

# distribuzione settimanale
print("─"*72)
print("DISTRIBUZIONE SETTIMANALE (aggrega per ISO-week)")
print("─"*72)
settimane_f=Counter(ts_to_dt(r["ts"]).strftime("%Y-W%W") for r in filtrati)
settimane_t=Counter(ts_to_dt(r["ts"]).strftime("%Y-W%W") for r in tutti)
for s in sorted(set(list(settimane_f.keys())+list(settimane_t.keys()))):
    nf=settimane_f.get(s,0); nt=settimane_t.get(s,0)
    bar="█"*nf
    print(f"  {s}  tot={nt:>3}  filt={nf:>2}  {bar}")
print()

# verdetto concentrazione
print("="*72)
print("VERDETTO — IL SEGNALE È CONCENTRATO O DISTRIBUITO?")
print("="*72)
print()
n_giorni_tot=len(tutti_giorni)
if giorni_con_filtrati>=n_giorni_tot*0.4:
    print(f"  ✓ DISTRIBUITO: {giorni_con_filtrati}/{n_giorni_tot} giorni hanno almeno 1 trade filtrato.")
    print(f"  Il segnale non dipende da 2-3 giorni fortunati.")
elif giorni_con_filtrati>=n_giorni_tot*0.2:
    print(f"  ~ PARZIALE: {giorni_con_filtrati}/{n_giorni_tot} giorni hanno trade filtrati.")
    print(f"  Attenzione: circa il {100-100*giorni_con_filtrati/n_giorni_tot:.0f}% dei giorni non genera segnali.")
else:
    print(f"  ✗ CONCENTRATO: solo {giorni_con_filtrati}/{n_giorni_tot} giorni con trade filtrati.")
    print(f"  Il segnale potrebbe dipendere da regime specifico di mercato.")
    print(f"  Verifica: quei giorni avevano BTC particolarmente volatile/trending?")
print()
if n80==1:
    print(f"  ⚠️  ATTENZIONE: l'80% dei trade filtrati è in UN SOLO GIORNO → molto fragile.")
elif n80<=3:
    print(f"  ⚠️  ATTENZIONE: l'80% dei trade filtrati è in soli {n80} giorni → fragile.")
else:
    print(f"  ✓ L'80% dei trade filtrati è distribuito su {n80} giorni → più robusto.")
print()
