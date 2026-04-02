            elif _score >= 65: _band = "BUONO_65-75"
            elif _score >= 58: _band = "BASE_58-65"
            else:              _band = "DEBOLE_<58"
            _econ_key = f"{self._regime_current}|{self.campo._direction}|{_band}"
            _st = getattr(self.signal_tracker, '_stats', {}).get(_econ_key, {})
            _n         = _st.get('n', 0)
            _pnls      = _st.get('pnl_sim', [])
            _hits      = _st.get('hit_60', [])
            _avg_pnl   = sum(_pnls) / len(_pnls) if _pnls else None
            _pnl_pos   = sum(1 for p in _pnls if p > 0) / len(_pnls) if _pnls else None

            if _avg_pnl is not None and _n >= 100 and _avg_pnl <= -0.05:
                    # Bypass ECON_BLOCK se OracoloInterno in FUOCO con carica alta
                    if self._oi_stato == "FUOCO" and self._oi_carica >= 0.65:
                        self._log_m2("🔥", f"ECON_BLOCK bypassed — FUOCO carica={self._oi_carica:.2f}")
                    else:
                        self._log_m2("💸", f"ECON_BLOCK {_econ_key} avg_pnl={_avg_pnl:.3f} n={_n}")
                        if len(self._phantoms_open) < 5:
                            self._record_phantom(price, f"ECON_BLOCK_{_econ_key}",
                                seed['score'], momentum, volatility, trend)
                        return

            elif (_avg_pnl is None or _avg_pnl < 0 or _n < 20 or
                  (_pnl_pos is not None and _pnl_pos < 0.55)):
                # B: dati insufficienti o edge debole — PILOT (size cappata)
                if _band == "DEBOLE_<58":
                    result['size'] = min(result['size'], 0.10)
                    result['soglia'] = max(result['soglia'], 58)
                elif _band == "BASE_58-65":
                    result['size'] = min(result['size'], 0.15)
                _pilot_reason = (f"n={_n}" if _n < 20 else
                                 f"avg_pnl={_avg_pnl:.3f}" if _avg_pnl is not None and _avg_pnl < 0 else
                                 f"pos={_pnl_pos:.0%}" if _pnl_pos is not None else "no_data")
                self._log_m2("🔬", f"ECON_PILOT {_econ_key} {_pilot_reason} size→{result['size']:.2f}")
            else:
                # C: evidenza positiva — FULL
                self._log_m2("✅", f"ECON_OK {_econ_key} avg_pnl={_avg_pnl:.3f} pos={_pnl_pos:.0%} n={_n}")

            # ===============================================================
            # REGIME-AWARE BEHAVIOR - il laterale è un altro mestiere
            #
            # RANGING: no-trade zone al centro, min hold lungo, più selettivo
render@srv-d1qhf8je5dus73e8vfv0-57d588598b-ckkvj:~/project/src$ cd /opt/render/project/src && python3 -c "
c=open('OVERTOP_BASSANO_V15_PRODUCTION.py').read()              cd /opt/render/project/src && python3 -c "
c=open('OVERTOP_BASSANO_V15_PRODUCTION.py').read()nl < 0 or _n < 20 or\n                  (_pnl_pos is not None and _pnl_pos < 0.55
old=\"            elif (_avg_pnl is None or _avg_pnl < 0 or _n < 20 or\n                  (_pnl_pos is not None and _pnl_pos < 0.55)):\n                # B: dati insufficienti o edge debole — PILOT (size cappata)\n                if _band == \\\"DEBOLE_<58\\\":\n                    result['size'] = min(result['size'], 0.10)\n                    result['soglia'] = max(result['soglia'], 58)\" ew=\"            elif (_avg_pnl is None or _avg_pnl < 0 or _n < 20 or\n                  (_pnl_pos is not None and _pnl_pos < 0.55
new=\"            elif (_avg_pnl is None or _avg_pnl < 0 or _n < 20 or\n                  (_pnl_pos is not None and _pnl_pos < 0.55)):\n                # B: dati insufficienti o edge debole — PILOT (size cappata)\n                if self._oi_stato == 'FUOCO' and self._oi_carica >= 0.65:\n                    pass  # FUOCO bypassa ECON_PILOT\n                elif _band == \\\"DEBOLE_<58\\\":\n                    result['size'] = min(result['size'], 0.10)\n                    result['soglia'] = max(result['soglia'], 58)\" f old in c:
if old in c:ERTOP_BASSANO_V15_PRODUCTION.py','w').write(c.replace(old,new,1))
    open('OVERTOP_BASSANO_V15_PRODUCTION.py','w').write(c.replace(old,new,1))
    print('✅ ECON_PILOT bypass FUOCO applicato')
else:rint('❌ non trovato')
    print('❌ non trovato')
"
✅ ECON_PILOT bypass FUOCO applicato
render@srv-d1qhf8je5dus73e8vfv0-57d588598b-ckkvj:~/project/src$ 
