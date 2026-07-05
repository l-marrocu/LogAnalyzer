# LogAnalyzer

Strumento locale per Windows che analizza file di log (`.log`, `.txt`, `.csv`) e genera automaticamente un report PDF strutturato con tabelle di riepilogo, errori più frequenti e grafico timeline.

Funziona completamente offline: nessuna API esterna, nessuna connessione internet richiesta.

## Cosa fa

1. All'avvio apre una finestra dove scegli il **tipo di log** (Python, Apache, generico, ecc.) e la **cartella** da analizzare.
2. Mostra una tabella con tutti i file trovati (inclusi ed esclusi, con il motivo).
3. Analizza i file inclusi: conta errori, warning e info, raggruppa gli errori più frequenti, costruisce una timeline.
4. Genera un report PDF in `Reports/report_AAAAMMGG_HHMM.pdf` con:
   - Tabella di riepilogo per file
   - Top 10 errori più frequenti
   - Grafico timeline errori/warning per ora
   - Dettaglio completo di tutti gli errori trovati

## Aggiungere un nuovo tipo di log

I tipi di log supportati sono definiti nel file `log_types.json` — **non serve toccare il codice Python**. Per aggiungere un nuovo tipo:

1. Apri `log_types.json` con un editor di testo
2. Aggiungi un nuovo oggetto alla lista `tipi`, seguendo la stessa struttura degli altri
3. I campi obbligatori sono: `id`, `nome`, `pattern` (regex con named groups `timestamp`, `livello`, `messaggio`), `livelli_errore`, `livelli_warning`, `livelli_info`
4. Riavvia il programma — il nuovo tipo comparirà automaticamente nel dropdown

Esempio minimo di tipo personalizzato:
```json
{
  "id": "mio_app",
  "nome": "La Mia Applicazione",
  "descrizione": "Log personalizzato della mia app",
  "pattern": "(?P<timestamp>\\d{4}-\\d{2}-\\d{2}) (?P<livello>INFO|ERROR) (?P<messaggio>.+)",
  "formato_timestamp": "%Y-%m-%d",
  "livelli_errore": ["ERROR"],
  "livelli_warning": [],
  "livelli_info": ["INFO"],
  "encoding": "utf-8"
}
```

## Requisiti

- Windows
- Python 3.11 o superiore

## Installazione

```bash
# 1. Crea l'ambiente virtuale
python -m venv .venv

# 2. Attivalo
.venv\Scripts\activate

# 3. Installa le dipendenze
pip install -r requirements.txt
```

## Avvio

```bash
python gui_app.py
```

## Verifica rapida (smoke test)

1. Crea una cartella `test_logs/` con dentro un file `prova.log` contenente:
   ```
   2026-06-28 10:00:01,123 [INFO] Applicazione avviata
   2026-06-28 10:01:00,456 [ERROR] Errore di connessione al database
   2026-06-28 10:02:00,789 [WARNING] Memoria al 90%
   ```
2. Lancia `python gui_app.py`
3. Seleziona tipo **"Python Application Log"** e la cartella `test_logs/`
4. Il file dovrebbe comparire nella tabella come "incluso"
5. Clicca **Analizza** — dopo qualche secondo apparirà il bottone **Apri report**
6. Il PDF dovrebbe mostrare 1 errore, 1 warning, 1 info e un grafico timeline

Se il tipo Python non riconosce il tuo formato, prova con **"Log Generico (Fallback)"**.

## Licenza

Distribuito con licenza MIT — vedi il file [LICENSE](LICENSE).
