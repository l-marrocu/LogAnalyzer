"""
LogAnalyzer - gui_app.py
=========================
Interfaccia grafica moderna (CustomTkinter) per LogAnalyzer.
La logica di parsing, analisi e generazione PDF è in core.py.

THREADING: l'analisi dei file può richiedere diversi secondi su log
grandi. Viene eseguita in un thread separato per mantenere la finestra
reattiva. Gli aggiornamenti della GUI dal thread in background avvengono
sempre tramite self.after(), che è il metodo thread-safe di tkinter.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog

from core import (
    carica_tipi_log,
    scansiona_cartella,
    analizza_cartella,
    genera_pdf,
    genera_txt_fallback,
    AnalisiError,
    FileTrovato,
    TipoLog,
)


class LogAnalyzerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("LogAnalyzer")
        self.geometry("750x650")
        self.minsize(660, 560)

        # Percorso della cartella che contiene gui_app.py e log_types.json
        self.cartella_progetto = Path(__file__).resolve().parent

        # Stato interno
        self.cartella_selezionata: Path | None = None
        self.file_trovati: list[FileTrovato] = []
        self.tipi_log: list[TipoLog] = []
        self.percorso_ultimo_report: Path | None = None

        # Carica i tipi di log da log_types.json all'avvio
        self._carica_configurazione()

        self._costruisci_interfaccia()

    # -----------------------------------------------------------------
    # Caricamento configurazione
    # -----------------------------------------------------------------
    def _carica_configurazione(self):
        """
        Carica i tipi di log da log_types.json. Se il file manca o è
        malformato, mostra un errore e chiude il programma.
        """
        try:
            self.tipi_log, self.dimensione_max_mb = carica_tipi_log(
                self.cartella_progetto
            )
        except AnalisiError as e:
            # Non possiamo usare messagebox qui perché la finestra non
            # è ancora stata costruita: usiamo un dialog tkinter base.
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Errore di configurazione", str(e))
            root.destroy()
            sys.exit(1)

    # -----------------------------------------------------------------
    # Costruzione dell'interfaccia
    # -----------------------------------------------------------------
    def _costruisci_interfaccia(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)  # tabella si espande

        # --- Intestazione ---
        intestazione = ctk.CTkFrame(self, fg_color="transparent")
        intestazione.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        ctk.CTkLabel(
            intestazione, text="LogAnalyzer",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            intestazione,
            text="Analizza file di log locali e genera un report PDF",
            text_color="gray60",
        ).pack(anchor="w")

        # --- Riga tipo di log + selezione cartella ---
        riga_controlli = ctk.CTkFrame(self, fg_color="transparent")
        riga_controlli.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        riga_controlli.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(riga_controlli, text="Tipo di log:").grid(
            row=0, column=0, padx=(0, 8), pady=(0, 6), sticky="w"
        )
        self.dropdown_tipo = ctk.CTkOptionMenu(
            riga_controlli,
            values=[t.nome for t in self.tipi_log],
            width=260,
            command=self._on_tipo_cambiato,
        )
        self.dropdown_tipo.grid(row=0, column=1, sticky="w", pady=(0, 6))
        # Mostriamo la descrizione del tipo selezionato sotto il dropdown
        self.label_descrizione = ctk.CTkLabel(
            riga_controlli,
            text=self.tipi_log[0].descrizione if self.tipi_log else "",
            text_color="gray60",
            font=ctk.CTkFont(size=11),
            wraplength=500,
            justify="left",
        )
        self.label_descrizione.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ctk.CTkLabel(riga_controlli, text="Cartella:").grid(
            row=2, column=0, padx=(0, 8), sticky="w"
        )
        self.campo_cartella = ctk.CTkEntry(
            riga_controlli,
            placeholder_text="Nessuna cartella selezionata",
        )
        self.campo_cartella.grid(row=2, column=1, sticky="ew", padx=(0, 8))
        self.campo_cartella.configure(state="disabled")

        ctk.CTkButton(
            riga_controlli, text="Sfoglia...", width=90,
            command=self._on_sfoglia_cliccato,
        ).grid(row=2, column=2)

        # --- Tabella file trovati ---
        ctk.CTkLabel(
            self, text="File trovati", text_color="gray60", anchor="w",
        ).grid(row=2, column=0, sticky="nw", padx=20, pady=(10, 0))

        self.frame_tabella = ctk.CTkScrollableFrame(self)
        self.frame_tabella.grid(row=3, column=0, sticky="nsew", padx=20, pady=(4, 10))
        self.frame_tabella.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # --- Area log ---
        ctk.CTkLabel(
            self, text="Log", text_color="gray60", anchor="w",
        ).grid(row=4, column=0, sticky="nw", padx=20)

        self.area_log = ctk.CTkTextbox(self, height=90, state="disabled")
        self.area_log.grid(row=5, column=0, sticky="ew", padx=20, pady=(4, 10))

        # --- Riga azione ---
        riga_azione = ctk.CTkFrame(self, fg_color="transparent")
        riga_azione.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 20))
        riga_azione.grid_columnconfigure(1, weight=1)

        self.bottone_analizza = ctk.CTkButton(
            riga_azione, text="Analizza", state="disabled",
            command=self._on_analizza_cliccato,
        )
        self.bottone_analizza.grid(row=0, column=0, padx=(0, 12))

        self.barra_progresso = ctk.CTkProgressBar(riga_azione, mode="indeterminate")
        self.barra_progresso.grid(row=0, column=1, sticky="ew")
        self.barra_progresso.grid_remove()

        self.bottone_apri_report = ctk.CTkButton(
            riga_azione, text="Apri report", width=110,
            command=self._on_apri_report_cliccato,
        )
        self.bottone_apri_report.grid(row=0, column=2, padx=(12, 0))
        self.bottone_apri_report.grid_remove()

    # -----------------------------------------------------------------
    # Utilità
    # -----------------------------------------------------------------
    def _log(self, messaggio: str):
        """Aggiunge una riga al log — da chiamare solo nel thread principale."""
        self.area_log.configure(state="normal")
        self.area_log.insert("end", messaggio + "\n")
        self.area_log.see("end")
        self.area_log.configure(state="disabled")

    def _tipo_selezionato(self) -> TipoLog:
        """Ritorna il TipoLog corrispondente alla scelta nel dropdown."""
        nome_selezionato = self.dropdown_tipo.get()
        return next(t for t in self.tipi_log if t.nome == nome_selezionato)

    # -----------------------------------------------------------------
    # Gestione eventi UI
    # -----------------------------------------------------------------
    def _on_tipo_cambiato(self, valore: str):
        """Aggiorna la descrizione sotto il dropdown quando cambia il tipo."""
        tipo = next((t for t in self.tipi_log if t.nome == valore), None)
        if tipo:
            self.label_descrizione.configure(text=tipo.descrizione)

        # Se una cartella è già selezionata, aggiorniamo la tabella
        # con il nuovo tipo scelto (non cambia cosa viene analizzato,
        # ma è utile come feedback visivo).
        if self.cartella_selezionata:
            self._aggiorna_tabella()

    def _on_sfoglia_cliccato(self):
        cartella = filedialog.askdirectory(
            title="Seleziona la cartella con i file di log"
        )
        if not cartella:
            return

        self.cartella_selezionata = Path(cartella)
        self.campo_cartella.configure(state="normal")
        self.campo_cartella.delete(0, "end")
        self.campo_cartella.insert(0, str(self.cartella_selezionata))
        self.campo_cartella.configure(state="disabled")

        self._log(f"Cartella selezionata: {self.cartella_selezionata}")
        self._aggiorna_tabella()

    def _aggiorna_tabella(self):
        """Scansiona la cartella e aggiorna la tabella file."""
        for widget in self.frame_tabella.winfo_children():
            widget.destroy()

        self.file_trovati = scansiona_cartella(
            self.cartella_selezionata, self.dimensione_max_mb
        )

        # Intestazione tabella
        intestazioni = ["Nome file", "KB", "Stato", "Motivo esclusione"]
        for i, testo in enumerate(intestazioni):
            ctk.CTkLabel(
                self.frame_tabella, text=testo,
                text_color="gray60", font=ctk.CTkFont(size=11),
            ).grid(row=0, column=i, sticky="w", padx=6, pady=(0, 4))

        if not self.file_trovati:
            ctk.CTkLabel(
                self.frame_tabella,
                text="Nessun file trovato in questa cartella.",
                text_color="gray60",
            ).grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=8)
        else:
            for riga, ft in enumerate(self.file_trovati, start=1):
                colore = "#97c459" if ft.incluso else "#e24b4a"
                stato = "incluso" if ft.incluso else "escluso"

                ctk.CTkLabel(self.frame_tabella, text=ft.nome, anchor="w").grid(
                    row=riga, column=0, sticky="w", padx=6, pady=2)
                ctk.CTkLabel(self.frame_tabella, text=f"{ft.dimensione_kb:.0f}", anchor="w").grid(
                    row=riga, column=1, sticky="w", padx=6, pady=2)
                ctk.CTkLabel(self.frame_tabella, text=stato,
                             text_color=colore, anchor="w").grid(
                    row=riga, column=2, sticky="w", padx=6, pady=2)
                ctk.CTkLabel(self.frame_tabella,
                             text=ft.motivo_esclusione, text_color="gray60", anchor="w").grid(
                    row=riga, column=3, sticky="w", padx=6, pady=2)

        inclusi = [f for f in self.file_trovati if f.incluso]
        self._log(
            f"Trovati {len(self.file_trovati)} file, "
            f"{len(inclusi)} verranno analizzati."
        )

        self.bottone_analizza.configure(
            state="normal" if inclusi else "disabled"
        )
        if not inclusi:
            self._log("Nessun file supportato da analizzare in questa cartella.")

    # -----------------------------------------------------------------
    # Analisi (thread separato)
    # -----------------------------------------------------------------
    def _on_analizza_cliccato(self):
        self.bottone_analizza.configure(state="disabled")
        self.bottone_apri_report.grid_remove()
        self.barra_progresso.grid()
        self.barra_progresso.start()
        self._log("Analisi in corso...")

        thread = threading.Thread(
            target=self._analisi_in_background, daemon=True
        )
        thread.start()

    def _analisi_in_background(self):
        """Eseguita nel thread in background. Non tocca mai i widget direttamente."""
        try:
            tipo = self._tipo_selezionato()

            risultati = analizza_cartella(
                self.cartella_selezionata,
                self.file_trovati,
                tipo,
                callback_progresso=lambda msg: self.after(0, self._log, msg),
            )

            try:
                percorso = genera_pdf(
                    risultati, tipo,
                    self.cartella_selezionata,
                    self.cartella_progetto,
                )
            except Exception:
                # Fallback TXT se il PDF non riesce
                percorso = genera_txt_fallback(
                    risultati, tipo,
                    self.cartella_selezionata,
                    self.cartella_progetto,
                )
                self.after(
                    0, self._log,
                    "[AVVISO] PDF non generato, salvato report TXT di fallback."
                )

            self.after(0, self._on_analisi_completata, percorso)

        except AnalisiError as e:
            self.after(0, self._on_errore_analisi, str(e))
        except Exception as e:
            self.after(0, self._on_errore_analisi, f"Errore imprevisto: {e}")

    def _on_analisi_completata(self, percorso_report: Path):
        """Eseguita nel thread principale dopo il completamento."""
        self.barra_progresso.stop()
        self.barra_progresso.grid_remove()
        self.bottone_analizza.configure(state="normal")

        self.percorso_ultimo_report = percorso_report
        self.bottone_apri_report.grid()

        self._log(f"Report salvato in: {percorso_report}")

    def _on_errore_analisi(self, messaggio: str):
        """Eseguita nel thread principale in caso di errore."""
        self.barra_progresso.stop()
        self.barra_progresso.grid_remove()
        self.bottone_analizza.configure(state="normal")
        self._log(f"[ERRORE] {messaggio}")

    # -----------------------------------------------------------------
    # Apertura report
    # -----------------------------------------------------------------
    def _on_apri_report_cliccato(self):
        if self.percorso_ultimo_report is None:
            return
        try:
            os.startfile(self.percorso_ultimo_report)
        except AttributeError:
            import subprocess
            cmd = "xdg-open" if sys.platform.startswith("linux") else "open"
            subprocess.run([cmd, str(self.percorso_ultimo_report)])


if __name__ == "__main__":
    app = LogAnalyzerApp()
    app.mainloop()
