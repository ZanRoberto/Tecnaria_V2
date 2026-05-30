render@srv-d1qhf8je5dus73e8vfv0-6f579fb7cc-w8thp:~/project/src$ sed -n '9505,9560p' ~/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py
        # (Altrimenti l'IF sopra ha bloccato il flip, ma _veritas/_drift non sono attivi)
        elif self._regime_current == "RANGING" and campo._direction == "LONG" and campo._direction_bearish_streak >= 3 and cooldown_ok and _tsunami_short_ok and not _veritas_short_ok and not _drift_short_ok:
            campo._direction = "SHORT"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            self._log_m2("🔄", f"FLIP → SHORT via TSUNAMI 2min "
                              f"(drift={drift:+.3f}% bearish={bearish_energy})")
        
        # RSI OVERRIDE: ipervenduto/ipercomprato sovrasta tutto
        # RSI < 30 = mercato caduto troppo → LONG obbligatorio
        # RSI > 75 = mercato salito troppo → SHORT permesso solo se già SHORT
        _rsi_now = campo._last_rsi if hasattr(campo, '_last_rsi') else 50.0
        if _rsi_now < 30 and campo._direction == "SHORT" and cooldown_ok:
            campo._direction = "LONG"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            self._log_m2("🔄", f"RSI OVERRIDE → LONG (RSI={_rsi_now:.0f} ipervenduto)")
        elif _rsi_now < 30 and campo._direction == "LONG":
            # Già LONG e ipervenduto — blocca flip SHORT
            # ECCEZIONE: OI SHORT FUOCO molto forte (>=0.90) + drift negativo + RANGING
            # = il mercato sta dichiarando la direzione nonostante RSI basso
            _oi_short_forte = (getattr(self, '_oi_carica_short', 0) >= 0.90 and
                               getattr(self, '_oi_stato_short', '') == "FUOCO")
            if _oi_short_forte and drift < -0.003 and self._regime_current == "RANGING":
                campo._direction = "SHORT"
                campo._direction_last_change = now
                campo._direction_bearish_streak = 0
                self._log_m2("🔄", f"FLIP → SHORT in RANGING (OI_SHORT_FORTE={getattr(self,'_oi_carica_short',0):.2f} drift={drift:+.3f}% RSI={_rsi_now:.0f})")
            else:
                campo._direction_bearish_streak = 0

        # In NON-RANGING: flip normale LONG → SHORT
        elif campo._direction == "LONG" and campo._direction_bearish_streak >= 3 and cooldown_ok:
            # Non flippare SHORT se RSI ipervenduto
            if _rsi_now < 35:
                self._log_m2("🔇", f"SHORT BLOCCATO da RSI={_rsi_now:.0f} ipervenduto")
                campo._direction_bearish_streak = 0
            else:
                campo._direction = "SHORT"
                campo._direction_last_change = now
                campo._direction_bearish_streak = 0
        # SHORT → LONG: energia bearish scesa sotto 2 + cooldown
        elif campo._direction == "SHORT" and bearish_energy < 2 and cooldown_ok:
            campo._direction = "LONG"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
        
        # SHORT in RANGING: mantenuto se il drift è negativo
        # Non forzare LONG quando il mercato scende
        
        if campo._direction != old_direction:
            self._log_m2("🔄", f"DIREZIONE → {campo._direction} (drift={drift:+.3f}% macd_hist={macd_hist:+.2f} trend={trend})")
            self.telemetry.log_direction_flip(
                old_direction, campo._direction,
                regime=self._regime_current, direction=campo._direction,
                open_position=self._shadow is not None,
render@srv-d1qhf8je5dus73e8vfv0-6f579fb7cc-w8thp:~/project/src$ 
