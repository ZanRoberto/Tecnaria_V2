import sqlite3
con = sqlite3.connect('/var/data/trading_data.db')
rows = con.execute("""
SELECT json_extract(data_json,'$.momentum')||'|'||json_extract(data_json,'$.volatility')||'|'||json_extract(data_json,'$.trend') firma,
COUNT(*) n,
SUM(CASE WHEN json_extract(data_json,'$.pnl_netto')>0 THEN 1 ELSE 0 END) win,
ROUND(SUM(json_extract(data_json,'$.pnl_netto')),1) tot
FROM trades WHERE event_type IN ('EXIT','M2_EXIT') AND timestamp >= '2026-06-24'
GROUP BY firma ORDER BY tot ASC
""").fetchall()
print(f"{'FIRMA':<35} {'N':>5} {'WIN':>5} {'TOT':>8} {'WR%':>6}")
print("-"*62)
for firma, n, win, tot in rows:
    wr = round(100*win/n) if n else 0
    print(f"{str(firma):<35} {n:>5} {win:>5} {tot:>+8.1f} {wr:>5}%")
