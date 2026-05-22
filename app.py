{
  "_descrizione": "Capsula MUSCOLO antiaerea v2 — REGOLA-22-MAGGIO-ROBERTO. Riscritta dopo l'osservazione di Roberto: 'se aspetti 3 LOSS in finestra di 15 min sfuggira sempre'. Niente finestre. UN solo LOSS con firma X marca la firma PERICOLOSA. La firma resta pericolosa finche un WIN successivo con stessa firma la pulisce.",

  "id": "muscolo_antiaerea_pattern_persistente_v1",
  "ruolo": "BLOCCA_FIRMA_PERICOLOSA_DOPO_PRIMO_LOSS",
  "tipo": "MUSCOLO",
  "stato": "FUNZIONA",

  "presidia_contesto": {
    "regime": "RANGING",
    "direzione_target": "LONG",
    "descrizione": "Presidia il contesto principale del bot (99.7% dei trade). Marca firme pericolose dopo 1 LOSS, blocca i successivi trade con stessa firma."
  },

  "funzione_validazione": {
    "descrizione": "Io funziono se i trade che ho bloccato sarebbero stati cumulativamente perdenti. Ho impedito perdite reali, non opportunita vincenti.",
    "criterio": {
      "tipo_misura": "antiaerea_temporale",
      "logica": "1_LOSS_BASTA_NO_FINESTRA",
      "firma_definizione": "(regime, direction, momentum, volatility, trend, matrimonio)",
      "regola_attivazione": "Se ultimo trade con firma X chiude LOSS → firma X marcata PERICOLOSA",
      "regola_pulizia": "Se un trade con firma X chiude WIN → firma X torna NEUTRA",
      "pattern": {
        "regime": "RANGING",
        "direction": "LONG"
      },
      "min_attivazioni": 5,
      "soglia_pnl_funziona": "pnl_bloccato_totale < 0"
    }
  },

  "logica": {
    "azione_quando_attiva_e_funziona": {
      "descrizione": "Quando V16 chiede /per_contesto con firma completa, se la firma e PERICOLOSA rispondo BLOCCA. Niente attese, niente conteggi, niente finestre. UN LOSS basta.",
      "frequenza_check": "ad ogni call /per_contesto da V16"
    },
    "azione_quando_NON_funziona": {
      "descrizione": "Segnalo NON_FUNZIONA. AI deve decidere se firma troppo grossolana o troppo fine.",
      "trigger_propagazione": "AI analizza loss_sfuggiti e attivazioni: se molti WIN bloccati → firma sbagliata. Se quasi nessuno → tutto bene."
    },
    "non_modifica_capsule_pari_grado": "Questa capsula convive con altre capsule MUSCOLO."
  },

  "metadata": {
    "creata_da": "Claude (sotto direttiva di Roberto)",
    "data_concettuale": "22 maggio 2026 — versione v2 niente finestre",
    "frase_di_nascita_di_Roberto": "Se aspetti 3 trade in finestra di 15 minuti sfuggira sempre. UN LOSS basta.",
    "dato_di_partenza_v2": "22mag: 3 LOSS sfuggiti distribuiti su 43 min (11:08, 11:14, 11:51). Finestra 15min non li intercetta. Con regola 1-LOSS-basta: primo LOSS marca firma, secondo bloccato, terzo bloccato.",
    "ATTENZIONE_HOOK_BOT": "Per attivare il BLOCCO MUSCOLO serve un secondo hook V16 prima dell entry."
  },

  "memoria_eventi": {
    "_descrizione": "Eventi di attivazione: quando la capsula blocca un trade per firma pericolosa.",
    "voci": []
  },

  "scritta_da": "Claude",
  "versione_capsula": "v1.1-no-finestre"
}
