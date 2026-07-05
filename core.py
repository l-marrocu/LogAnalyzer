"""
LogAnalyzer - core.py
======================
Logica condivisa: lettura della configurazione tipi di log, scansione
della cartella, parsing delle righe, analisi statistica e generazione
del report in PDF.

Tutto gira in locale: nessuna API esterna, nessuna connessione di rete.
"""

from __future__ import annotations

import re
import json
from io import BytesIO
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from collections import Counter

import matplotlib
matplotlib.use("Agg")  # backend non interattivo: necessario fuori dal main thread
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from fpdf import FPDF


# ---------------------------------------------------------------------------
# COSTANTI
# ---------------------------------------------------------------------------
REPORTS_DIR_NAME = "Reports"
LOG_TYPES_FILE = "log_types.json"
ESTENSIONI_SUPPORTATE = {".log", ".txt", ".csv"}


# ---------------------------------------------------------------------------
# STRUTTURE DATI
# ---------------------------------------------------------------------------
@dataclass
class TipoLog:
    """Rappresenta un tipo di log letto da log_types.json."""
    id: str
    nome: str
    descrizione: str
    pattern: re.Pattern
    formato_timestamp: str | None
    livelli_errore: list[str]
    livelli_warning: list[str]
    livelli_info: list[str]
    encoding: str


@dataclass
class RigaLog:
    """Una singola riga parsata correttamente."""
    numero_riga: int
    timestamp_raw: str
    timestamp: datetime | None
    livello: str
    messaggio: str
    categoria: str  # "errore", "warning", "info", "altro"


@dataclass
class RisultatoFile:
    """Risultato dell'analisi di un singolo file di log."""
    nome: str
    righe_totali: int = 0
    righe_parsate: int = 0
    errori: list[RigaLog] = field(default_factory=list)
    warning: list[RigaLog] = field(default_factory=list)
    info: list[RigaLog] = field(default_factory=list)
    non_parsabili: int = 0
    incluso: bool = True
    motivo_esclusione: str = ""

    @property
    def n_errori(self) -> int:
        return len(self.errori)

    @property
    def n_warning(self) -> int:
        return len(self.warning)

    @property
    def n_info(self) -> int:
        return len(self.info)


@dataclass
class FileTrovato:
    """File individuato durante la scansione, per la tabella della GUI."""
    nome: str
    dimensione_kb: float
    incluso: bool
    motivo_esclusione: str = ""


class AnalisiError(Exception):
    """Errore gestito: messaggio già pronto per essere mostrato all'utente."""
    pass


# ---------------------------------------------------------------------------
# CONFIGURAZIONE — lettura di log_types.json
# ---------------------------------------------------------------------------
def carica_tipi_log(cartella_progetto: Path) -> tuple[list[TipoLog], int]:
    """
    Legge log_types.json e ritorna la lista dei tipi configurati e il
    limite di dimensione massima in MB.
    Solleva AnalisiError se il file manca o è malformato.
    """
    percorso = cartella_progetto / LOG_TYPES_FILE

    if not percorso.exists():
        raise AnalisiError(
            f"File di configurazione '{LOG_TYPES_FILE}' non trovato.\n"
            "Assicurati che sia nella stessa cartella di gui_app.py."
        )

    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AnalisiError(
            f"Il file '{LOG_TYPES_FILE}' non è un JSON valido: {e}\n"
            "Controlla che non ci siano errori di sintassi."
        ) from e

    dimensione_max_mb = dati.get("dimensione_massima_mb", 10)
    tipi = []

    for t in dati.get("tipi", []):
        try:
            tipi.append(TipoLog(
                id=t["id"],
                nome=t["nome"],
                descrizione=t.get("descrizione", ""),
                pattern=re.compile(t["pattern"], re.IGNORECASE),
                formato_timestamp=t.get("formato_timestamp"),
                livelli_errore=[l.upper() for l in t.get("livelli_errore", [])],
                livelli_warning=[l.upper() for l in t.get("livelli_warning", [])],
                livelli_info=[l.upper() for l in t.get("livelli_info", [])],
                encoding=t.get("encoding", "utf-8"),
            ))
        except (KeyError, re.error) as e:
            raise AnalisiError(
                f"Errore nel tipo di log '{t.get('id', '?')}': {e}\n"
                f"Controlla la configurazione in '{LOG_TYPES_FILE}'."
            ) from e

    if not tipi:
        raise AnalisiError(f"Nessun tipo di log trovato in '{LOG_TYPES_FILE}'.")

    return tipi, dimensione_max_mb


# ---------------------------------------------------------------------------
# SCANSIONE CARTELLA
# ---------------------------------------------------------------------------
def scansiona_cartella(cartella: Path, dimensione_max_mb: int) -> list[FileTrovato]:
    """
    Scansiona i file di primo livello nella cartella e ritorna la lista
    di tutti i file trovati (inclusi e non), per la tabella della GUI.
    """
    file_trovati: list[FileTrovato] = []
    dimensione_max_bytes = dimensione_max_mb * 1024 * 1024

    for elemento in sorted(cartella.iterdir()):
        if elemento.is_dir():
            continue

        # File nascosti e di sistema: ignorati silenziosamente.
        if elemento.name.startswith(".") or elemento.name.lower() == "desktop.ini":
            continue

        estensione = elemento.suffix.lower()
        dimensione_bytes = elemento.stat().st_size
        dimensione_kb = dimensione_bytes / 1024

        if estensione not in ESTENSIONI_SUPPORTATE:
            file_trovati.append(FileTrovato(
                nome=elemento.name,
                dimensione_kb=dimensione_kb,
                incluso=False,
                motivo_esclusione="tipo file non supportato",
            ))
            continue

        if dimensione_bytes > dimensione_max_bytes:
            file_trovati.append(FileTrovato(
                nome=elemento.name,
                dimensione_kb=dimensione_kb,
                incluso=False,
                motivo_esclusione=f"supera il limite di {dimensione_max_mb} MB",
            ))
            continue

        file_trovati.append(FileTrovato(
            nome=elemento.name,
            dimensione_kb=dimensione_kb,
            incluso=True,
        ))

    return file_trovati


# ---------------------------------------------------------------------------
# PARSING E ANALISI
# ---------------------------------------------------------------------------
def _parse_timestamp(timestamp_raw: str, formato: str | None) -> datetime | None:
    """
    Prova a convertire il timestamp grezzo in un oggetto datetime.
    Ritorna None se il formato non è specificato o se la conversione fallisce.
    """
    if not formato or not timestamp_raw:
        return None
    # Puliamo la virgola nei millisecondi (Python si aspetta il punto)
    timestamp_pulito = timestamp_raw.replace(",", ".").split(" +")[0].split(" -")[0]
    for fmt in [formato, formato.replace("%f", "").rstrip()]:
        try:
            return datetime.strptime(timestamp_pulito.strip(), fmt)
        except ValueError:
            continue
    return None


def analizza_file(
    percorso_file: Path,
    tipo_log: TipoLog,
    callback_progresso: callable | None = None,
) -> RisultatoFile:
    """
    Legge e analizza un singolo file di log con il tipo specificato.
    callback_progresso, se fornita, viene chiamata con un messaggio
    stringa per aggiornare il log della GUI.
    """
    risultato = RisultatoFile(nome=percorso_file.name)

    # Lettura robusta con fallback encoding.
    testo = None
    for encoding in (tipo_log.encoding, "utf-8", "cp1252", "latin-1"):
        try:
            testo = percorso_file.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if testo is None:
        risultato.incluso = False
        risultato.motivo_esclusione = "encoding non riconosciuto"
        return risultato

    righe = testo.splitlines()
    risultato.righe_totali = len(righe)
    righe_senza_match = 0

    for numero, riga in enumerate(righe, start=1):
        riga = riga.strip()
        if not riga:
            continue

        match = tipo_log.pattern.search(riga)
        if not match:
            righe_senza_match += 1
            continue

        gruppi = match.groupdict()
        timestamp_raw = gruppi.get("timestamp", "") or ""
        livello_raw = (gruppi.get("livello", "") or "").upper().strip()
        messaggio = (gruppi.get("messaggio", "") or riga).strip()

        timestamp = _parse_timestamp(timestamp_raw, tipo_log.formato_timestamp)

        if livello_raw in tipo_log.livelli_errore:
            categoria = "errore"
        elif livello_raw in tipo_log.livelli_warning:
            categoria = "warning"
        elif livello_raw in tipo_log.livelli_info:
            categoria = "info"
        else:
            categoria = "altro"

        riga_log = RigaLog(
            numero_riga=numero,
            timestamp_raw=timestamp_raw,
            timestamp=timestamp,
            livello=livello_raw or "N/D",
            messaggio=messaggio,
            categoria=categoria,
        )

        risultato.righe_parsate += 1

        if categoria == "errore":
            risultato.errori.append(riga_log)
        elif categoria == "warning":
            risultato.warning.append(riga_log)
        else:
            risultato.info.append(riga_log)

    risultato.non_parsabili = righe_senza_match

    # Avvisiamo se il pattern non ha matchato quasi nulla.
    if risultato.righe_totali > 0:
        percentuale_match = risultato.righe_parsate / risultato.righe_totali
        if percentuale_match < 0.1 and callback_progresso:
            callback_progresso(
                f"[AVVISO] '{percorso_file.name}': solo "
                f"{risultato.righe_parsate}/{risultato.righe_totali} righe "
                f"riconosciute. Prova il tipo 'Log Generico (Fallback)'."
            )

    return risultato


def analizza_cartella(
    cartella: Path,
    file_inclusi: list[FileTrovato],
    tipo_log: TipoLog,
    callback_progresso: callable | None = None,
) -> list[RisultatoFile]:
    """Analizza tutti i file inclusi e ritorna la lista dei risultati."""
    risultati = []
    for ft in file_inclusi:
        if not ft.incluso:
            continue
        percorso = cartella / ft.nome
        if callback_progresso:
            callback_progresso(f"Analisi di '{ft.nome}'...")
        risultato = analizza_file(percorso, tipo_log, callback_progresso)
        risultati.append(risultato)
    return risultati


# ---------------------------------------------------------------------------
# STATISTICHE AGGREGATE
# ---------------------------------------------------------------------------
def calcola_top_errori(risultati: list[RisultatoFile], top_n: int = 10) -> list[tuple[str, int]]:
    """Ritorna i top_n messaggi di errore più frequenti (testo, conteggio)."""
    contatore: Counter = Counter()
    for r in risultati:
        for e in r.errori:
            # Tronchiamo il messaggio a 120 caratteri per la deduplicazione,
            # così piccole variazioni (es. numeri di riga) non creano voci separate.
            chiave = e.messaggio[:120]
            contatore[chiave] += 1
    return contatore.most_common(top_n)


def costruisci_timeline(risultati: list[RisultatoFile]) -> tuple[list[datetime], list[int], list[int]]:
    """
    Raggruppa errori e warning per ora e ritorna tre liste parallele:
    (timestamps, conteggio_errori, conteggio_warning).
    Ritorna liste vuote se nessuna riga ha timestamp parsabile.
    """
    bucket_errori: Counter = Counter()
    bucket_warning: Counter = Counter()

    for r in risultati:
        for e in r.errori:
            if e.timestamp:
                bucket_errori[e.timestamp.replace(minute=0, second=0, microsecond=0)] += 1
        for w in r.warning:
            if w.timestamp:
                bucket_warning[w.timestamp.replace(minute=0, second=0, microsecond=0)] += 1

    if not bucket_errori and not bucket_warning:
        return [], [], []

    tutti_i_bucket = sorted(set(bucket_errori) | set(bucket_warning))
    errori = [bucket_errori.get(t, 0) for t in tutti_i_bucket]
    warning = [bucket_warning.get(t, 0) for t in tutti_i_bucket]

    return tutti_i_bucket, errori, warning


# ---------------------------------------------------------------------------
# GRAFICO TIMELINE (matplotlib → BytesIO → PDF)
# ---------------------------------------------------------------------------
def genera_grafico_timeline(
    timestamps: list[datetime],
    errori: list[int],
    warning: list[int],
) -> BytesIO | None:
    """
    Genera un grafico a barre della timeline e lo ritorna come BytesIO (PNG).
    Ritorna None se non ci sono dati con timestamp.
    """
    if not timestamps:
        return None

    fig, ax = plt.subplots(figsize=(10, 3.5))
    fig.patch.set_facecolor("white")

    x = range(len(timestamps))
    larghezza = 0.4

    ax.bar([i - larghezza / 2 for i in x], errori,
           width=larghezza, color="#e24b4a", label="Errori", alpha=0.85)
    ax.bar([i + larghezza / 2 for i in x], warning,
           width=larghezza, color="#f0a500", label="Warning", alpha=0.85)

    ax.set_xticks(list(x))
    etichette = [t.strftime("%d/%m %H:00") for t in timestamps]
    ax.set_xticklabels(etichette, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("Numero di eventi")
    ax.set_title("Timeline Errori e Warning per Ora")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    plt.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)

    return buffer


# ---------------------------------------------------------------------------
# GENERAZIONE PDF
# ---------------------------------------------------------------------------
def genera_pdf(
    risultati: list[RisultatoFile],
    tipo_log: TipoLog,
    cartella_analizzata: Path,
    cartella_progetto: Path,
) -> Path:
    """
    Genera il report PDF completo e lo salva in Reports/ con timestamp.
    Ritorna il percorso del file creato.
    """
    cartella_reports = cartella_progetto / REPORTS_DIR_NAME
    cartella_reports.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    percorso_pdf = cartella_reports / f"report_{timestamp}.pdf"

    # --- Statistiche aggregate ---
    totale_righe = sum(r.righe_totali for r in risultati)
    totale_errori = sum(r.n_errori for r in risultati)
    totale_warning = sum(r.n_warning for r in risultati)
    totale_info = sum(r.n_info for r in risultati)
    totale_non_parsabili = sum(r.non_parsabili for r in risultati)

    top_errori = calcola_top_errori(risultati)
    timestamps, conteggio_errori, conteggio_warning = costruisci_timeline(risultati)
    grafico_buffer = genera_grafico_timeline(timestamps, conteggio_errori, conteggio_warning)

    # --- Costruzione PDF ---
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Titolo
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "LogAnalyzer - Report di Analisi", ln=True, align="C")
    pdf.ln(3)

    # Metadati
    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Cartella analizzata: {cartella_analizzata}", ln=True)
    pdf.cell(0, 6, f"Tipo di log: {tipo_log.nome}", ln=True)
    pdf.cell(0, 6, f"Data e ora analisi: {datetime.now().strftime('%d/%m/%Y %H:%M')}", ln=True)
    pdf.cell(0, 6, f"File analizzati: {len(risultati)}", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    # --- Sezione 1: Riepilogo ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "1. Riepilogo", ln=True)
    pdf.ln(2)

    # Intestazione tabella riepilogo
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(240, 240, 240)
    larghezze = [55, 22, 22, 22, 22, 27]
    intestazioni = ["File", "Righe", "Errori", "Warning", "Info", "Non parsabili"]
    for i, (h, w) in enumerate(zip(intestazioni, larghezze)):
        pdf.cell(w, 7, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", size=8)
    for r in risultati:
        pdf.cell(larghezze[0], 6, r.nome[:35], border=1)
        pdf.cell(larghezze[1], 6, str(r.righe_totali), border=1, align="C")
        pdf.cell(larghezze[2], 6, str(r.n_errori), border=1, align="C")
        pdf.cell(larghezze[3], 6, str(r.n_warning), border=1, align="C")
        pdf.cell(larghezze[4], 6, str(r.n_info), border=1, align="C")
        pdf.cell(larghezze[5], 6, str(r.non_parsabili), border=1, align="C")
        pdf.ln()

    # Riga totali
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(larghezze[0], 6, "TOTALE", border=1, fill=True)
    for val, w in zip([totale_righe, totale_errori, totale_warning,
                        totale_info, totale_non_parsabili], larghezze[1:]):
        pdf.cell(w, 6, str(val), border=1, fill=True, align="C")
    pdf.ln(8)

    # --- Sezione 2: Errori più frequenti ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "2. Errori più frequenti (Top 10)", ln=True)
    pdf.ln(2)

    if not top_errori:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 7, "Nessun errore rilevato nei file analizzati.", ln=True)
    else:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(15, 7, "#", border=1, fill=True, align="C")
        pdf.cell(155, 7, "Messaggio di errore", border=1, fill=True)
        pdf.cell(20, 7, "Occorrenze", border=1, fill=True, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", size=8)
        for i, (msg, count) in enumerate(top_errori, start=1):
            pdf.cell(15, 6, str(i), border=1, align="C")
            # Tronchiamo il messaggio se troppo lungo per la cella
            testo = msg if len(msg) <= 90 else msg[:87] + "..."
            pdf.cell(155, 6, testo, border=1)
            pdf.cell(20, 6, str(count), border=1, align="C")
            pdf.ln()

    pdf.ln(8)

    # --- Sezione 3: Timeline ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "3. Timeline Errori e Warning", ln=True)
    pdf.ln(2)

    if grafico_buffer is not None:
        pdf.image(grafico_buffer, w=pdf.epw)
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(0, 7,
            "Grafico non disponibile: nessun timestamp parsabile trovato "
            "nei file analizzati. Per abilitare la timeline, usa un tipo di "
            "log con formato timestamp configurato.")
    pdf.ln(8)

    # --- Sezione 4: Dettaglio errori ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "4. Dettaglio Errori", ln=True)
    pdf.ln(2)

    tutti_gli_errori = []
    for r in risultati:
        for e in r.errori:
            tutti_gli_errori.append((r.nome, e))

    if not tutti_gli_errori:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 7, "Nessun errore rilevato.", ln=True)
    else:
        for nome_file, errore in tutti_gli_errori[:200]:  # max 200 per non creare PDF enormi
            pdf.set_font("Helvetica", "B", 8)
            ts = errore.timestamp_raw[:25] if errore.timestamp_raw else "N/D"
            intestazione = f"[{nome_file}] [{ts}] [{errore.livello}]"
            pdf.set_text_color(180, 0, 0)
            pdf.cell(0, 5, intestazione, ln=True)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", size=8)
            testo_msg = errore.messaggio if len(errore.messaggio) <= 200 else errore.messaggio[:197] + "..."
            pdf.multi_cell(0, 5, testo_msg)
            pdf.ln(1)

        if len(tutti_gli_errori) > 200:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 7,
                f"[Mostrati i primi 200 errori su {len(tutti_gli_errori)} totali.]",
                ln=True)

    pdf.output(str(percorso_pdf))
    return percorso_pdf


# ---------------------------------------------------------------------------
# FALLBACK TXT (se la generazione PDF fallisce)
# ---------------------------------------------------------------------------
def genera_txt_fallback(
    risultati: list[RisultatoFile],
    tipo_log: TipoLog,
    cartella_analizzata: Path,
    cartella_progetto: Path,
) -> Path:
    """
    Genera un report testuale minimale come fallback se la creazione
    del PDF fallisce per qualsiasi motivo.
    """
    cartella_reports = cartella_progetto / REPORTS_DIR_NAME
    cartella_reports.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    percorso_txt = cartella_reports / f"report_{timestamp}_fallback.txt"

    righe = [
        "LogAnalyzer — Report di Analisi (fallback TXT)",
        "=" * 60,
        f"Cartella: {cartella_analizzata}",
        f"Tipo log: {tipo_log.nome}",
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
    ]

    for r in risultati:
        righe.append(f"File: {r.nome}")
        righe.append(f"  Righe totali: {r.righe_totali}")
        righe.append(f"  Errori: {r.n_errori}")
        righe.append(f"  Warning: {r.n_warning}")
        righe.append(f"  Info: {r.n_info}")
        righe.append(f"  Non parsabili: {r.non_parsabili}")
        righe.append("")

    percorso_txt.write_text("\n".join(righe), encoding="utf-8")
    return percorso_txt
