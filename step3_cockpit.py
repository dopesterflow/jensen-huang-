from datetime import datetime
import json
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import traceback

try:
    import winsound
    WINSOUND_OK = True
except ImportError:
    WINSOUND_OK = False

import mss
from PIL import Image, ImageTk

from step2_markt_analyse import CONFIG_FILE, lade_config, scanne_watchlist, uebersetze_symbol
from step3_monitor_auge import (
    LETZTES_BILD,
    LETZTES_BILD_MARKIERT,
    finde_tradingview_fenster,
    pruefe_privatsphaere_sichtbar,
    knipse_tradingview_fenster,
    extrahiere_chart_tag,
    extrahiere_symbol_aus_titel,
    hole_alle_marktdaten_schnell,
    skaliere_wert,
    starte_cockpit_analyse,
    starte_weltlage_analyse,
)
from step4_paper_trader import (
    berechne_live_pnl,
    berechne_tages_stats,
    erstelle_ki_trade_review,
    hole_live_chart_preis,
    lade_konto,
    pruefe_auto_sl_tp,
    schliesse_paper_trade,
    speichere_konto,
)
from step5_radar_ui import (
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    ACCENT_YELLOW,
    BG_CARD,
    BG_DARK,
    BG_INPUT,
    BORDER,
    TEXT_MAIN,
    TEXT_MUTED,
    bereinige_markdown,
    oeffne_journal_fenster,
    oeffne_order_ticket_fenster,
    oeffne_schliessen_fenster,
    zeichne_markierungen_auf_bild,
    zeige_glossar_fenster,
    zeige_markt_radar_fenster,
)
from step6_voice import SprachAssistent, erkenne_sprach_befehl


def ermittle_markt_session():
    jetzt = datetime.now()
    if jetzt.weekday() >= 5:
        return "⚪ Wochenende (Aktien zu, Krypto 24/7)", TEXT_MUTED

    minuten = jetzt.hour * 60 + jetzt.minute
    if 9 * 60 <= minuten < 15 * 60 + 30:
        return "🟡 EU-Handel / US-Vorbörse", ACCENT_YELLOW
    if 15 * 60 + 30 <= minuten < 17 * 60 + 30:
        return "🟢 US-OPEN (Heißes Volumen)", ACCENT_GREEN
    if 17 * 60 + 30 <= minuten < 20 * 60:
        return "🟡 US-Mittagsflaute (Vorsicht Fallen)", ACCENT_YELLOW
    if 20 * 60 <= minuten < 22 * 60:
        return "🟢 US-Schlussstunde (Power Hour)", ACCENT_GREEN
    return "🔴 Nachtmarkt (Dünn, Krypto 24/7)", ACCENT_RED


class TradingCockpitApp:
    def __init__(self, root):
        self.root = root
        self.root.title("KI Trading-Cockpit (Monitor 2)")
        self.root.configure(bg=BG_DARK)

        self.config = lade_config()
        self.konto = lade_konto(self.config)
        self.aktueller_chat = None
        self.aktuelles_pil_bild = None
        self.tk_bild = None
        self.aktion_laeuft = False
        self.radar_laeuft = False
        self.preis_check_laeuft = False
        self.resize_job = None
        self.letzter_scan_kontext = None
        self.preis_alarm = None

        self.zoom_faktor = 1.0
        self.center_ix = 0.0
        self.center_iy = 0.0
        self.drag_x = 0
        self.drag_y = 0

        self.voice = SprachAssistent(status_cb=self._setze_status)

        self._platziere_auf_monitor_2()
        self._baue_gui()
        self._aktualisiere_konto_ui()
        self._aktualisiere_session_uhr()
        self._auto_trade_loop()

        self.root.bind("<F5>", lambda e: self.starte_scan())
        self.root.bind_all("<KeyPress-F8>", self._voice_press)
        self.root.bind_all("<KeyRelease-F8>", self._voice_release)

        if LETZTES_BILD_MARKIERT.exists():
            self._setze_neues_chart_bild(Image.open(LETZTES_BILD_MARKIERT))
        elif LETZTES_BILD.exists():
            self._setze_neues_chart_bild(Image.open(LETZTES_BILD))

        self.root.after(1200, self.voice.lade_modell_im_hintergrund)

    def _platziere_auf_monitor_2(self):
        try:
            with mss.MSS() as sct:
                monitore = sct.monitors
                ziel = monitore[2] if len(monitore) >= 3 else monitore[1]
                w = min(1500, int(ziel["width"] * 0.96))
                h = min(910, int(ziel["height"] * 0.92))
                x = ziel["left"] + max(10, (ziel["width"] - w) // 2)
                y = ziel["top"] + max(10, (ziel["height"] - h) // 2)
                self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            self.root.geometry("1440x880")

    def _baue_gui(self):
        topbar = tk.Frame(self.root, bg="#141b24", pady=7, padx=8, highlightthickness=1, highlightbackground=BORDER)
        topbar.pack(fill="x", side="top")

        tk.Label(topbar, text="🤖", font=("Segoe UI", 12, "bold"), fg=TEXT_MAIN, bg="#141b24").pack(side="left", padx=(0, 6))

        self.btn_scan = tk.Button(topbar, text="📸 Scan (F5)", command=self.starte_scan, font=("Segoe UI", 9, "bold"), bg="#00c853", fg="#09120c", activebackground="#00e676", relief="flat", padx=8, pady=5, cursor="hand2")
        self.btn_scan.pack(side="left", padx=(0, 4))

        self.btn_welt = tk.Button(topbar, text="🌍 Welt", command=self.starte_weltlage, font=("Segoe UI", 9, "bold"), bg=ACCENT_BLUE, fg="#07131a", activebackground="#4fc3f7", relief="flat", padx=8, pady=5, cursor="hand2")
        self.btn_welt.pack(side="left", padx=(0, 4))

        self.btn_radar = tk.Button(topbar, text="🔍 Radar", command=self.starte_markt_radar, font=("Segoe UI", 9, "bold"), bg="#ffb300", fg="#140f02", activebackground="#ffca28", relief="flat", padx=8, pady=5, cursor="hand2")
        self.btn_radar.pack(side="left", padx=(0, 4))

        tk.Button(topbar, text="📊 Journal", command=lambda: oeffne_journal_fenster(self), font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=8, pady=5, cursor="hand2").pack(side="left", padx=(0, 4))
        tk.Button(topbar, text="❓ Glossar", command=lambda: zeige_glossar_fenster(self.root), font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=7, pady=5, cursor="hand2").pack(side="left", padx=(0, 6))

        tk.Label(topbar, text="KI:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg="#141b24").pack(side="left")
        self.ki_quelle_var = tk.StringVar(value="Auto (Cloud -> 3080)")
        ttk.Combobox(topbar, textvariable=self.ki_quelle_var, values=["Auto (Cloud -> 3080)", "⚡ Nur RTX 3080", "☁️ Nur Cloud"], state="readonly", width=16).pack(side="left", padx=(2, 5))

        tk.Label(topbar, text="TF:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg="#141b24").pack(side="left")
        self.tf_var = tk.StringVar(value="5M")
        ttk.Combobox(topbar, textvariable=self.tf_var, values=["5M", "1H", "4H", "1D"], state="readonly", width=4).pack(side="left", padx=(2, 5))

        tk.Label(topbar, text="Modus:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg="#141b24").pack(side="left")
        self.modus_var = tk.StringVar(value=self.config.get("erfahrungs_modus", "ANFAENGER").upper())
        modus_box = ttk.Combobox(topbar, textvariable=self.modus_var, values=["ANFAENGER", "FORTGESCHRITTEN", "PROFI"], state="readonly", width=12)
        modus_box.pack(side="left", padx=(2, 5))
        modus_box.bind("<<ComboboxSelected>>", self._modus_geaendert)

        self.btn_regeln = tk.Button(topbar, text="", command=self._toggle_regeln_modus, font=("Segoe UI", 9, "bold"), relief="flat", padx=7, pady=5, cursor="hand2")
        self.btn_regeln.pack(side="left", padx=(0, 4))
        self._aktualisiere_regeln_button()

        self.topmost_var = tk.BooleanVar(value=False)
        tk.Checkbutton(topbar, text="📌", variable=self.topmost_var, command=self._toggle_topmost, bg="#141b24", fg=TEXT_MAIN, selectcolor=BG_INPUT, activebackground="#141b24", activeforeground=TEXT_MAIN, font=("Segoe UI", 10)).pack(side="left")

        self.status_label = tk.Label(topbar, text="Bereit!", font=("Segoe UI", 9, "bold"), fg=ACCENT_BLUE, bg="#141b24")
        self.status_label.pack(side="right")

        subbar = tk.Frame(self.root, bg="#111720", pady=6, padx=12, highlightthickness=1, highlightbackground=BORDER)
        subbar.pack(fill="x", side="top")

        self.lbl_session = tk.Label(subbar, text="⏱️ Session...", font=("Segoe UI", 10, "bold"), fg=ACCENT_GREEN, bg="#111720")
        self.lbl_session.pack(side="left")

        self.lbl_konto = tk.Label(subbar, text="💰 Paper-Konto: $1000.00", font=("Segoe UI", 10, "bold"), fg=ACCENT_YELLOW, bg="#111720")
        self.lbl_konto.pack(side="left", padx=(16, 16))

        tk.Label(subbar, text="🔔 Wecker:", font=("Segoe UI", 9, "bold"), fg=ACCENT_YELLOW, bg="#111720").pack(side="left")
        self.alarm_entry = tk.Entry(subbar, font=("Segoe UI", 10), bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", width=9)
        self.alarm_entry.pack(side="left", padx=(4, 4), ipady=2)
        self.btn_alarm = tk.Button(subbar, text="Setzen", command=self._toggle_preis_alarm, font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=8, pady=2, cursor="hand2")
        self.btn_alarm.pack(side="left", padx=(0, 12))

        tk.Label(subbar, text="Direktfrage:", font=("Segoe UI", 9), fg=TEXT_MUTED, bg="#111720").pack(side="left", padx=(4, 4))
        self.extra_frage_entry = tk.Entry(subbar, font=("Segoe UI", 10), bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", width=24)
        self.extra_frage_entry.pack(side="left", ipady=2)

        paned = tk.PanedWindow(self.root, orient="horizontal", bg=BG_DARK, sashwidth=6, sashrelief="flat")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        self.links_wrap = tk.Frame(paned, bg=BG_DARK)
        paned.add(self.links_wrap, minsize=430, width=560)

        self.canvas_links = tk.Canvas(self.links_wrap, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.links_wrap, orient="vertical", command=self.canvas_links.yview)
        self.karten_frame = tk.Frame(self.canvas_links, bg=BG_DARK)
        self.karten_frame.bind("<Configure>", lambda e: self.canvas_links.configure(scrollregion=self.canvas_links.bbox("all")))
        self.canvas_window = self.canvas_links.create_window((0, 0), window=self.karten_frame, anchor="nw")
        self.canvas_links.bind("<Configure>", lambda e: self.canvas_links.itemconfig(self.canvas_window, width=e.width))
        self.canvas_links.configure(yscrollcommand=scrollbar.set)
        self.canvas_links.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.root.bind_all("<MouseWheel>", self._mausrad_links_scroll)
        self.root.bind_all("<Control-MouseWheel>", self._ctrl_mausrad_zoom)

        self.aktiver_trade_card = tk.Frame(self.karten_frame, bg="#122636", padx=14, pady=10, highlightthickness=2, highlightbackground=ACCENT_BLUE)
        self.lbl_trade_kopf = tk.Label(self.aktiver_trade_card, text="💼 AKTIVER PAPER-TRADE", font=("Segoe UI", 12, "bold"), fg=ACCENT_BLUE, bg="#122636", anchor="w")
        self.lbl_trade_kopf.pack(fill="x")
        self.lbl_trade_info = tk.Label(self.aktiver_trade_card, text="", font=("Segoe UI", 10), fg=TEXT_MAIN, bg="#122636", justify="left", anchor="w")
        self.lbl_trade_info.pack(fill="x", pady=(4, 6))

        trade_btn_row = tk.Frame(self.aktiver_trade_card, bg="#122636")
        trade_btn_row.pack(fill="x")
        tk.Button(trade_btn_row, text="🔄 Live-Kurs prüfen", command=self._manuell_kurs_pruefen, font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=10, pady=4, cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(trade_btn_row, text="💰 Trade jetzt schließen", command=lambda: oeffne_schliessen_fenster(self), font=("Segoe UI", 9, "bold"), bg=ACCENT_YELLOW, fg="#111111", relief="flat", padx=12, pady=4, cursor="hand2").pack(side="right")

        self.ampel_card = tk.Frame(self.karten_frame, bg="#2b2612", padx=14, pady=12, highlightthickness=2, highlightbackground=ACCENT_YELLOW)
        self.ampel_card.pack(fill="x", pady=(0, 10))
        self.lbl_ampel_titel = tk.Label(self.ampel_card, text="🟡 BEREIT FÜR RADAR, SCAN ODER WELTLAGE", font=("Segoe UI", 14, "bold"), fg=ACCENT_YELLOW, bg="#2b2612", anchor="w")
        self.lbl_ampel_titel.pack(fill="x")
        self.lbl_trend_einigkeit = tk.Label(self.ampel_card, text="", font=("Segoe UI", 10, "bold"), fg=ACCENT_BLUE, bg="#2b2612", wraplength=500, justify="left", anchor="w")
        self.lbl_trend_einigkeit.pack(fill="x", pady=(4, 0))
        self.lbl_ampel_fazit = tk.Label(self.ampel_card, text="Tipp: Klicke auf '🔍 Radar' für die heißesten Märkte, oder drücke F5 für deinen TradingView-Scan!", font=("Segoe UI", 11), fg=TEXT_MAIN, bg="#2b2612", wraplength=500, justify="left", anchor="w")
        self.lbl_ampel_fazit.pack(fill="x", pady=(6, 0))
        self.lbl_setup_score = tk.Label(self.ampel_card, text="", font=("Segoe UI", 10, "bold"), fg=TEXT_MAIN, bg="#2b2612", wraplength=500, justify="left", anchor="w")
        self.lbl_setup_score.pack(fill="x", pady=(8, 0))

        self.card_was, self.lbl_was_titel, self.lbl_was = self._erstelle_karte("1. WAS PASSIERT GERADE?", "Noch kein Scan gestartet.")
        self.card_wo, self.lbl_wo_titel, self.lbl_wo = self._erstelle_karte("2. WO MUSST DU IM BILD HINSCHAUEN?", "Hier erscheinen gleich die Erklärungen zu [1], [2], [3] im Bild.")
        self.card_plan, self.lbl_plan_titel, self.lbl_plan = self._erstelle_karte("3. COACH-PLAN & PAPER-TRADING", "Long-Trigger, Short-Trigger, Einstieg, SL & TP erscheinen nach dem Scan hier.")

        self.paper_btn_row = tk.Frame(self.card_plan, bg=BG_CARD)
        self.paper_btn_row.pack(fill="x", pady=(10, 2))
        self.btn_long = tk.Button(self.paper_btn_row, text="🟢 Paper LONG setzen", command=lambda: oeffne_order_ticket_fenster(self, "LONG"), font=("Segoe UI", 10, "bold"), bg="#0e5b34", fg="#ffffff", activebackground="#00c853", relief="flat", padx=14, pady=6, cursor="hand2")
        self.btn_long.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.btn_short = tk.Button(self.paper_btn_row, text="🔴 Paper SHORT setzen", command=lambda: oeffne_order_ticket_fenster(self, "SHORT"), font=("Segoe UI", 10, "bold"), bg="#6e1a22", fg="#ffffff", activebackground="#ff5252", relief="flat", padx=14, pady=6, cursor="hand2")
        self.btn_short.pack(side="right", expand=True, fill="x", padx=(6, 0))

        self.card_mathe, self.lbl_mathe_titel, self.lbl_mathe = self._erstelle_karte("4. ZEITEBENEN, NEWS & MAKRO-KOMPASS", "1W / 1D / 4H / 1H / 5M + News + Earnings.")

        rechts_wrap = tk.Frame(paned, bg=BG_DARK)
        paned.add(rechts_wrap, minsize=460)

        chart_header = tk.Frame(rechts_wrap, bg=BG_CARD, padx=10, pady=6, highlightthickness=1, highlightbackground=BORDER)
        chart_header.pack(fill="x")
        self.lbl_chart_info = tk.Label(chart_header, text="📷 Chart (Strg+Mausrad = Zoom, Ziehen = Bewegen)", font=("Segoe UI", 10, "bold"), fg=TEXT_MAIN, bg=BG_CARD)
        self.lbl_chart_info.pack(side="left")
        tk.Button(chart_header, text="🔍 Groß", command=self._oeffne_grossbild, font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=9, pady=3, cursor="hand2").pack(side="right", padx=(4, 0))
        tk.Button(chart_header, text="🔄 100%", command=self._reset_zoom, font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=9, pady=3, cursor="hand2").pack(side="right", padx=(6, 0))
        self.lbl_zoom = tk.Label(chart_header, text="Zoom: 100%", font=("Segoe UI", 9, "bold"), fg=ACCENT_YELLOW, bg=BG_CARD)
        self.lbl_zoom.pack(side="right", padx=(0, 8))

        self.chart_box = tk.Frame(rechts_wrap, bg="#090c10", height=420, highlightthickness=1, highlightbackground=BORDER)
        self.chart_box.pack(fill="both", expand=True, pady=(0, 8))
        self.chart_box.pack_propagate(False)

        self.chart_canvas = tk.Canvas(self.chart_box, bg="#090c10", highlightthickness=0)
        self.chart_canvas.pack(fill="both", expand=True)
        self.chart_canvas.create_text(300, 180, text="Hier erscheint dein TradingView-Screenshot\n(Strg + Mausrad zum Reinzoomen, Ziehen zum Verschieben)", fill=TEXT_MUTED, font=("Segoe UI", 11), justify="center", tags="placeholder")
        self.chart_canvas.bind("<Configure>", self._bei_chart_resize)
        self.chart_canvas.bind("<ButtonPress-1>", self._start_pan)
        self.chart_canvas.bind("<B1-Motion>", self._bewege_pan)
        self.chart_canvas.bind("<Double-Button-1>", lambda e: self._reset_zoom())

        chat_card = tk.Frame(rechts_wrap, bg=BG_CARD, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER)
        chat_card.pack(fill="x", side="bottom")

        chip_bar = tk.Frame(chat_card, bg=BG_CARD)
        chip_bar.pack(fill="x", pady=(0, 6))
        tk.Label(chip_bar, text="Rücksprache:", font=("Segoe UI", 9, "bold"), fg=TEXT_MAIN, bg=BG_CARD).pack(side="left", padx=(0, 4))
        for chip_text, frage in [
            ("❓ Warum?", "Erkläre mir kurz und einfach, warum deine Einschätzung so ausfällt."),
            ("🛡️ Stop-Loss?", "Wo genau soll ich im Bild den Stop-Loss hinlegen und warum?"),
            ("📊 Makro?", "Welche heutigen News, Earnings oder Makro-Faktoren sind am wichtigsten?"),
        ]:
            tk.Button(chip_bar, text=chip_text, command=lambda f=frage: self._sende_chat_nachricht(f), font=("Segoe UI", 9), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=7, pady=3, cursor="hand2").pack(side="left", padx=2)

        self.tts_var = tk.BooleanVar(value=False)
        tk.Checkbutton(chip_bar, text="🔊 Vorlesen", variable=self.tts_var, command=self._toggle_tts, bg=BG_CARD, fg=ACCENT_YELLOW, selectcolor=BG_INPUT, activebackground=BG_CARD, activeforeground=ACCENT_YELLOW, font=("Segoe UI", 9, "bold")).pack(side="right")

        self.chat_text = tk.Text(chat_card, height=8, bg=BG_INPUT, fg=TEXT_MAIN, font=("Segoe UI", 10), wrap="word", relief="flat", padx=8, pady=6, state="disabled")
        self.chat_text.pack(fill="x", pady=(0, 6))

        eingabe_row = tk.Frame(chat_card, bg=BG_CARD)
        eingabe_row.pack(fill="x")
        self.btn_voice = tk.Button(eingabe_row, text="🎙️ Halten: Sprechen (F8)", font=("Segoe UI", 10, "bold"), bg="#2b3a4d", fg=TEXT_MAIN, activebackground=ACCENT_RED, relief="flat", padx=12, pady=6, cursor="hand2")
        self.btn_voice.pack(side="left", padx=(0, 8))
        self.btn_voice.bind("<ButtonPress-1>", self._voice_press)
        self.btn_voice.bind("<ButtonRelease-1>", self._voice_release)

        self.chat_entry = tk.Entry(eingabe_row, font=("Segoe UI", 11), bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat")
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.chat_entry.bind("<Return>", lambda e: self._sende_chat_aus_feld())

        self.btn_senden = tk.Button(eingabe_row, text="Senden", command=self._sende_chat_aus_feld, font=("Segoe UI", 10, "bold"), bg=ACCENT_BLUE, fg="#07131a", relief="flat", padx=16, pady=6, cursor="hand2")
        self.btn_senden.pack(side="right")

    def starte_markt_radar(self):
        if self.radar_laeuft:
            return
        self.radar_laeuft = True
        self.btn_radar.config(state="disabled", text="⏳ Radar...")
        self.config = lade_config()
        threading.Thread(target=self._radar_worker, daemon=True).start()

    def _radar_worker(self):
        t0 = time.time()
        try:
            ergebnisse, fehler = scanne_watchlist(self.config, status_cb=self._setze_status)
            dauer = round(time.time() - t0, 1)
            self.root.after(0, lambda: zeige_markt_radar_fenster(self, ergebnisse, fehler, dauer))
        except Exception as e:
            self._setze_status(f"❌ Radar-Fehler: {e}")
        finally:
            self.radar_laeuft = False
            self.root.after(0, lambda: self.btn_radar.config(state="normal", text="🔍 Radar"))

    def _toggle_tts(self):
        self.voice.vorlesen_aktiv = self.tts_var.get()
        if self.voice.vorlesen_aktiv:
            self._setze_status("🔊 Sprachausgabe AN.")
            self.voice.sage("Sprachausgabe aktiviert.")
        else:
            self._setze_status("🔇 Sprachausgabe AUS.")

    def _voice_press(self, event=None):
        if self.voice.aufnahme_laeuft:
            return
        ok, err = self.voice.starte_aufnahme()
        if ok:
            self.btn_voice.config(bg=ACCENT_RED, fg="#ffffff", text="🔴 Hört zu... (Loslassen)")
            self._setze_status("🎙️ Aufnahme läuft – sprich jetzt...")
        else:
            self._chat_hinzufuegen("Voice-Hinweis", err)

    def _voice_release(self, event=None):
        if not self.voice.aufnahme_laeuft:
            return
        self.btn_voice.config(bg="#2b3a4d", fg=TEXT_MAIN, text="⏳ Verstehe...")
        self._setze_status("🧠 Erkenne Sprache...")
        threading.Thread(target=self._voice_transcribe_worker, daemon=True).start()

    def _voice_transcribe_worker(self):
        text = self.voice.stoppe_und_transkribiere()
        self.root.after(0, lambda: self.btn_voice.config(bg="#2b3a4d", fg=TEXT_MAIN, text="🎙️ Halten: Sprechen (F8)"))
        if not text:
            self._setze_status("⚠️ Nichts verstanden – Taste beim Reden gedrückt halten.")
            return
        befehl = erkenne_sprach_befehl(text)
        if befehl == "SCAN":
            self._chat_hinzufuegen("Sprach-Befehl", f"„{text}“ -> Starte Live-Scan!")
            self.root.after(0, self.starte_scan)
        elif befehl == "WELT":
            self._chat_hinzufuegen("Sprach-Befehl", f"„{text}“ -> Lade Weltlage & News!")
            self.root.after(0, self.starte_weltlage)
        elif befehl == "CLOSE_TRADE":
            self._chat_hinzufuegen("Sprach-Befehl", f"„{text}“ -> Öffne Schließen-Dialog!")
            self.root.after(0, lambda: oeffne_schliessen_fenster(self))
        else:
            self.root.after(0, lambda: self._sende_chat_nachricht(text))

    def _aktualisiere_regeln_button(self):
        mode = str(self.config.get("trading_mode", "PAPER")).upper()
        if mode == "LIVE":
            self.btn_regeln.config(text="🔒 STRENG", bg="#6e1a22", fg="#ffffff")
        else:
            self.btn_regeln.config(text="🛡️ PAPER", bg="#1f3b2c", fg=ACCENT_GREEN)

    def _toggle_regeln_modus(self):
        try:
            cfg = lade_config()
            alt = str(cfg.get("trading_mode", "PAPER")).upper()
            neu = "LIVE" if alt == "PAPER" else "PAPER"
            cfg["trading_mode"] = neu
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            self.config = cfg
            self._aktualisiere_regeln_button()
            if neu == "LIVE":
                self._setze_status("🔒 Strenge Regeln AN (30m Cooldown & -3% Tageslimit sperren hart!).")
            else:
                self._setze_status("🛡️ Paper-Modus AN (nur Trainer-Warnungen, keine Sperre!).")
        except Exception as e:
            self._setze_status(f"⚠️ Konnte Modus nicht speichern: {e}")

    def _toggle_preis_alarm(self):
        if self.preis_alarm:
            self.preis_alarm = None
            self.btn_alarm.config(text="Setzen", bg="#253244", fg=TEXT_MAIN)
            self._setze_status("🔕 Preis-Wecker ausgeschaltet.")
            return
        if not self.letzter_scan_kontext:
            messagebox.showinfo("Erst scannen", "Bitte erst einmal F5 (Live-Scan) drücken, damit der Wecker deinen Markt kennt!")
            return
        try:
            ziel = float(self.alarm_entry.get().replace(",", ".").strip())
            if ziel <= 0:
                raise ValueError()
        except Exception:
            messagebox.showwarning("Ungültiger Preis", "Bitte eine gültige Ziel-Zahl ins Wecker-Feld tippen (z. B. 7270).")
            return
        k = self.letzter_scan_kontext
        richtung = "UEBER" if ziel >= float(k["preis"]) else "UNTER"
        self.preis_alarm = {"symbol": k["symbol"], "chart_tag": k["chart_tag"], "skala_faktor": k["skala_faktor"], "ziel": ziel, "richtung": richtung}
        pfeil = "≥" if richtung == "UEBER" else "≤"
        self.btn_alarm.config(text="🔕 Aus", bg="#6e4b12", fg="#ffffff")
        self._setze_status(f"🔔 Wecker aktiv: {k['chart_tag']} {pfeil} {ziel}")

    def _aktualisiere_session_uhr(self):
        text, farbe = ermittle_markt_session()
        self.lbl_session.config(text=f"⏱️ {datetime.now().strftime('%H:%M')} Uhr  |  {text}", fg=farbe)
        self.root.after(30000, self._aktualisiere_session_uhr)

    def _hole_ki_quelle(self):
        auswahl = self.ki_quelle_var.get()
        return "LOKAL" if "3080" in auswahl else ("CLOUD" if "Cloud" in auswahl else "AUTO")

    def _erstelle_karte(self, titel, start_text):
        card = tk.Frame(self.karten_frame, bg=BG_CARD, padx=14, pady=10, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 8))
        lbl_titel = tk.Label(card, text=titel, font=("Segoe UI", 10, "bold"), fg=ACCENT_BLUE, bg=BG_CARD, anchor="w")
        lbl_titel.pack(fill="x")
        lbl_body = tk.Label(card, text=start_text, font=("Segoe UI", 11), fg=TEXT_MAIN, bg=BG_CARD, justify="left", anchor="w", wraplength=500)
        lbl_body.pack(fill="x", pady=(5, 0))
        return card, lbl_titel, lbl_body

    def _ist_maus_ueber(self, widget_ziel, event):
        w = self.root.winfo_containing(event.x_root, event.y_root)
        while w is not None:
            if w == widget_ziel:
                return True
            w = w.master
        return False

    def _mausrad_links_scroll(self, event):
        if event.state & 0x0004:
            return
        if self._ist_maus_ueber(self.links_wrap, event):
            try:
                self.canvas_links.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

    def _aktualisiere_konto_ui(self):
        self.konto = lade_konto(self.config)
        kapital = float(self.konto.get("aktuelles_kapital_usd", 1000.0))
        tages_pnl, tages_pct, _ = berechne_tages_stats(self.konto)
        farbe_pnl = ACCENT_GREEN if tages_pnl >= 0 else ACCENT_RED
        self.lbl_konto.config(text=f"💰 Paper-Konto: ${kapital:.2f}  |  Heute: {tages_pnl:+.2f}$ ({tages_pct:+.2f}%)", fg=farbe_pnl if tages_pnl != 0 else ACCENT_YELLOW)

        trade = self.konto.get("offener_trade")
        if trade:
            if not self.aktiver_trade_card.winfo_ismapped():
                self.aktiver_trade_card.pack(fill="x", pady=(0, 10), before=self.ampel_card)
            live_p = float(trade.get("letzter_preis") or trade["einstieg"])
            fee_pct = float(self.config.get("risiko", {}).get("geschaetzte_gebuehr_prozent", 0.1))
            netto_usd, netto_pct, _ = berechne_live_pnl(trade, live_p, fee_prozent=fee_pct)
            icon = "🟢" if trade["richtung"] == "LONG" else "🔴"
            pnl_icon = "📈" if netto_usd >= 0 else "📉"
            self.lbl_trade_kopf.config(text=f"💼 OFFEN: {icon} {trade['richtung']} {trade['chart_tag']} ({trade['timeframe']})", fg=ACCENT_GREEN if netto_usd >= 0 else ACCENT_RED)
            self.lbl_trade_info.config(text=f"Einstieg: {trade['einstieg']}  |  Aktuell: {live_p}  |  Größe: ${trade['groesse_usd']:.2f}\nStop-Loss: {trade['stop_loss']}  |  Take-Profit: {trade['take_profit']}\n{pnl_icon} Live-Ergebnis (nach Gebühr): {netto_usd:+.2f}$ ({netto_pct:+.2f}%)")
        else:
            if self.aktiver_trade_card.winfo_ismapped():
                self.aktiver_trade_card.pack_forget()

    def _auto_trade_loop(self):
        if (self.konto.get("offener_trade") or self.preis_alarm) and not self.aktion_laeuft and not self.preis_check_laeuft:
            threading.Thread(target=self._pruefe_live_trade_worker, daemon=True).start()
        self.root.after(15000, self._auto_trade_loop)

    def _manuell_kurs_pruefen(self):
        if self.preis_check_laeuft:
            return
        self._setze_status("🔄 Prüfe Live-Kurs...")
        threading.Thread(target=self._pruefe_live_trade_worker, args=(True,), daemon=True).start()

    def _pruefe_live_trade_worker(self, manuell=False):
        self.preis_check_laeuft = True
        try:
            konto = lade_konto(self.config)
            trade = konto.get("offener_trade")
            if trade:
                live_p = hole_live_chart_preis(trade["symbol"], trade.get("skala_faktor", 1.0))
                if live_p is not None:
                    trade["letzter_preis"] = live_p
                    speichere_konto(konto)
                    treffer = pruefe_auto_sl_tp(trade, live_p)
                    if treffer:
                        self.root.after(0, lambda g=treffer[0], p=treffer[1]: self._beende_trade_und_review(p, g))
                    else:
                        self.root.after(0, self._aktualisiere_konto_ui)
                        if manuell:
                            self._setze_status(f"✅ Kurs aktualisiert: {live_p}")
                elif manuell:
                    self._setze_status("⚠️ Konnte gerade keinen Live-Kurs abrufen.")

            if self.preis_alarm:
                al = self.preis_alarm
                p_alarm = hole_live_chart_preis(al["symbol"], al["skala_faktor"])
                if p_alarm is not None and ((al["richtung"] == "UEBER" and p_alarm >= al["ziel"]) or (al["richtung"] == "UNTER" and p_alarm <= al["ziel"])):
                    self.preis_alarm = None
                    self.root.after(0, lambda p=p_alarm, a=al: self._alarm_ausgeloest(a, p))
        finally:
            self.preis_check_laeuft = False

    def _alarm_ausgeloest(self, al, preis):
        self.btn_alarm.config(text="Setzen", bg="#253244", fg=TEXT_MAIN)
        if WINSOUND_OK:
            try:
                winsound.Beep(880, 260)
                winsound.Beep(1175, 320)
            except Exception:
                pass
        self._chat_hinzufuegen("Wecker", f"🔔 PREIS-ALARM! {al['chart_tag']} hat deine Marke {al['ziel']} erreicht (aktuell {preis}). Starte frischen Scan!")
        self.voice.sage(f"Achtung! Preis-Alarm für {al['chart_tag']} bei {al['ziel']} erreicht.")
        self.starte_scan()

    def _beende_trade_und_review(self, exit_preis, grund):
        abgeschlossen, _ = schliesse_paper_trade(self.config, exit_preis, grund=grund)
        self._aktualisiere_konto_ui()
        if not abgeschlossen:
            return
        emoji = "🎉" if abgeschlossen["netto_pnl_usd"] >= 0 else "🛑"
        self._chat_hinzufuegen("Paper-Konto", f"{emoji} Trade geschlossen ({grund}): {abgeschlossen['richtung']} {abgeschlossen['chart_tag']} | Einstieg {abgeschlossen['einstieg']} -> Ausstieg {abgeschlossen['ausstieg']} | Netto: {abgeschlossen['netto_pnl_usd']:+.2f}$ ({abgeschlossen['netto_pnl_prozent']:+.2f}%).")
        self.voice.sage(f"Trade geschlossen per {grund}. Netto {abgeschlossen['netto_pnl_usd']:+.2f} Dollar.")
        self._setze_status("🤖 Erstelle KI-Trade-Review...")
        threading.Thread(target=self._review_worker, args=(abgeschlossen, self._hole_ki_quelle()), daemon=True).start()

    def _review_worker(self, abgeschlossen, ki_quelle):
        try:
            modell_name, review = erstelle_ki_trade_review(self.config, abgeschlossen, ki_quelle=ki_quelle)
            self._chat_hinzufuegen(f"Coach-Review ({modell_name or 'KI'})", review)
            self.voice.sage(review)
            self._setze_status("✅ Trade geloggt & bewertet!")
        except Exception as e:
            self._setze_status(f"⚠️ Review-Fehler: {e}")

    def _setze_neues_chart_bild(self, pil_bild):
        self.aktuelles_pil_bild = pil_bild
        self.zoom_faktor = 1.0
        self.center_ix = pil_bild.width / 2.0
        self.center_iy = pil_bild.height / 2.0
        self.root.after(10, self._rendere_chart_bild)

    def _reset_zoom(self):
        if not self.aktuelles_pil_bild:
            return
        self.zoom_faktor = 1.0
        self.center_ix = self.aktuelles_pil_bild.width / 2.0
        self.center_iy = self.aktuelles_pil_bild.height / 2.0
        self._rendere_chart_bild()

    def _ctrl_mausrad_zoom(self, event):
        if not self.aktuelles_pil_bild or not self._ist_maus_ueber(self.chart_canvas, event):
            return
        cw, ch = max(1, self.chart_canvas.winfo_width()), max(1, self.chart_canvas.winfo_height())
        iw, ih = self.aktuelles_pil_bild.size
        fit_scale = min(cw / iw, ch / ih)
        alter_scale = fit_scale * self.zoom_faktor
        mx, my = event.x_root - self.chart_canvas.winfo_rootx(), event.y_root - self.chart_canvas.winfo_rooty()
        maus_ix = self.center_ix + (mx - cw / 2.0) / alter_scale
        maus_iy = self.center_iy + (my - ch / 2.0) / alter_scale
        neuer_zoom = max(1.0, min(5.0, self.zoom_faktor * (1.2 if event.delta > 0 else 1.0 / 1.2)))
        self.zoom_faktor = 1.0 if abs(neuer_zoom - 1.0) < 0.05 else neuer_zoom
        neuer_scale = fit_scale * self.zoom_faktor
        if self.zoom_faktor == 1.0:
            self.center_ix, self.center_iy = iw / 2.0, ih / 2.0
        else:
            self.center_ix = maus_ix - (mx - cw / 2.0) / neuer_scale
            self.center_iy = maus_iy - (my - ch / 2.0) / neuer_scale
        self._clamp_pan()
        self._rendere_chart_bild()

    def _start_pan(self, event):
        self.drag_x, self.drag_y = event.x, event.y

    def _bewege_pan(self, event):
        if not self.aktuelles_pil_bild or self.zoom_faktor <= 1.0:
            return
        cw, ch = max(1, self.chart_canvas.winfo_width()), max(1, self.chart_canvas.winfo_height())
        iw, ih = self.aktuelles_pil_bild.size
        total_scale = min(cw / iw, ch / ih) * self.zoom_faktor
        self.center_ix -= (event.x - self.drag_x) / total_scale
        self.center_iy -= (event.y - self.drag_y) / total_scale
        self.drag_x, self.drag_y = event.x, event.y
        self._clamp_pan()
        self._rendere_chart_bild()

    def _clamp_pan(self):
        if not self.aktuelles_pil_bild:
            return
        cw, ch = max(1, self.chart_canvas.winfo_width()), max(1, self.chart_canvas.winfo_height())
        iw, ih = self.aktuelles_pil_bild.size
        total_scale = min(cw / iw, ch / ih) * self.zoom_faktor
        hx, hy = (cw / 2.0) / total_scale, (ch / 2.0) / total_scale
        self.center_ix = iw / 2.0 if hx >= iw / 2.0 else max(hx, min(iw - hx, self.center_ix))
        self.center_iy = ih / 2.0 if hy >= ih / 2.0 else max(hy, min(ih - hy, self.center_iy))

    def _bei_chart_resize(self, event):
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(80, self._rendere_chart_bild)

    def _rendere_chart_bild(self):
        if not self.aktuelles_pil_bild:
            return
        cw, ch = max(100, self.chart_canvas.winfo_width()), max(100, self.chart_canvas.winfo_height())
        iw, ih = self.aktuelles_pil_bild.size
        self._clamp_pan()
        total_scale = min(cw / iw, ch / ih) * self.zoom_faktor
        x1, y1 = self.center_ix - (cw / 2.0) / total_scale, self.center_iy - (ch / 2.0) / total_scale
        x2, y2 = self.center_ix + (cw / 2.0) / total_scale, self.center_iy + (ch / 2.0) / total_scale
        cx1, cy1, cx2, cy2 = max(0, int(x1)), max(0, int(y1)), min(iw, int(x2) + 1), min(ih, int(y2) + 1)
        if cx2 <= cx1 or cy2 <= cy1:
            return
        ausschnitt = self.aktuelles_pil_bild.crop((cx1, cy1, cx2, cy2))
        skaliert = ausschnitt.resize((max(1, int((cx2 - cx1) * total_scale)), max(1, int((cy2 - cy1) * total_scale))), Image.Resampling.BILINEAR if self.zoom_faktor > 1.0 else Image.Resampling.LANCZOS)
        self.tk_bild = ImageTk.PhotoImage(skaliert)
        self.chart_canvas.delete("all")
        self.chart_canvas.create_image(int((cx1 - x1) * total_scale), int((cy1 - y1) * total_scale), anchor="nw", image=self.tk_bild)
        self.lbl_zoom.config(text=f"Zoom: {int(round(self.zoom_faktor * 100))}%")
        self.chart_canvas.config(cursor="fleur" if self.zoom_faktor > 1.0 else "")

    def _oeffne_grossbild(self):
        if not self.aktuelles_pil_bild:
            return
        pop = tk.Toplevel(self.root)
        pop.title("Markierter Chart – Originalgröße")
        pop.configure(bg="#090c10")
        kopie = self.aktuelles_pil_bild.copy()
        kopie.thumbnail((1280, 960), Image.Resampling.LANCZOS)
        tk_big = ImageTk.PhotoImage(kopie)
        lbl = tk.Label(pop, image=tk_big, bg="#090c10")
        lbl.image = tk_big
        lbl.pack(padx=8, pady=8)

    def _toggle_topmost(self):
        self.root.attributes("-topmost", self.topmost_var.get())

    def _modus_geaendert(self, event=None):
        neuer_modus = self.modus_var.get()
        try:
            cfg = lade_config()
            cfg["erfahrungs_modus"] = neuer_modus
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            self.config = cfg
            self._setze_status(f"✅ Modus '{neuer_modus}' gespeichert.")
        except Exception as e:
            self._setze_status(f"⚠️ Konnte Modus nicht speichern: {e}")

    def _setze_status(self, text):
        self.root.after(0, lambda: self.status_label.config(text=text))

    def _sperre_buttons(self, sperren, aktiv_text=""):
        def _ui():
            if sperren:
                self.btn_scan.config(state="disabled", text="⏳..." if aktiv_text != "scan" else "⏳ Scanne...")
                self.btn_welt.config(state="disabled", text="⏳..." if aktiv_text != "welt" else "⏳ Lade...")
            else:
                self.btn_scan.config(state="normal", text="📸 Scan (F5)")
                self.btn_welt.config(state="normal", text="🌍 Welt")
        self.root.after(0, _ui)

    def _chat_hinzufuegen(self, absender, text):
        sauber = bereinige_markdown(text)
        def _update():
            self.chat_text.config(state="normal")
            self.chat_text.insert("end", f"{absender}: {sauber}\n\n")
            self.chat_text.see("end")
            self.chat_text.config(state="disabled")
        self.root.after(0, _update)

    def _zeige_hinweis_ampel(self, titel, text, farbe="GELB", trend_text="", score_zeilen=None):
        def _ui():
            bg, fg = {"GRUEN": ("#0e3b25", ACCENT_GREEN), "ROT": ("#3e1418", ACCENT_RED)}.get(farbe, ("#2b2612", ACCENT_YELLOW))
            self.ampel_card.config(bg=bg, highlightbackground=fg)
            self.lbl_ampel_titel.config(text=titel, bg=bg, fg=fg)
            self.lbl_trend_einigkeit.config(text=trend_text, bg=bg)
            self.lbl_ampel_fazit.config(text=text, bg=bg)
            self.lbl_setup_score.config(text="\n".join(bereinige_markdown(s) for s in (score_zeilen or [])), bg=bg)
        self.root.after(0, _ui)

    def starte_scan(self):
        if self.aktion_laeuft:
            return
        self.aktion_laeuft = True
        self._sperre_buttons(True, "scan")
        extra_frage = self.extra_frage_entry.get().strip()
        ki_quelle = self._hole_ki_quelle()
        tf = self.tf_var.get().lower()
        self.config = lade_config()
        konto_jetzt = lade_konto(self.config)
        cfg_scan = json.loads(json.dumps(self.config))
        cfg_scan["risiko"]["startkapital_paper_usd"] = konto_jetzt["aktuelles_kapital_usd"]
        threading.Thread(target=self._scan_worker, args=(cfg_scan, extra_frage, ki_quelle, tf), daemon=True).start()

    def _scan_worker(self, cfg_scan, extra_frage, ki_quelle, tf):
        t0 = time.time()
        try:
            self._setze_status("🔍 Suche TradingView auf Monitor 1...")
            fenster, _ = finde_tradingview_fenster()
            if not fenster:
                self._zeige_hinweis_ampel("🔴 TRADINGVIEW NICHT GEFUNDEN", "Öffne die TradingView Desktop App sichtbar auf Monitor 1 und klicke erneut auf Live-Scan.", "ROT")
                self._setze_status("❌ Kein TradingView-Fenster gefunden.")
                return
            ist_frei, sichtbarer_titel = pruefe_privatsphaere_sichtbar(fenster)
            if not ist_frei:
                self._zeige_hinweis_ampel("🔒 PRIVATSPHÄRE-STOPP", f"TradingView wird teilweise verdeckt von: '{sichtbarer_titel}'. Bitte hole TradingView komplett nach vorn!", "ROT")
                self._setze_status(f"🔒 Verdeckt durch: {sichtbarer_titel}")
                return
            self._setze_status(f"📸 Knipse '{fenster.title}'...")
            bild_bytes = knipse_tradingview_fenster(fenster)
            chart_tag = extrahiere_chart_tag(fenster.title)
            symbol = extrahiere_symbol_aus_titel(fenster.title)
            if symbol:
                symbol = uebersetze_symbol(symbol)
                self._setze_status(f"⚡ Lade Zahlen, News & Earnings ({tf.upper()}) für {symbol}...")
            daten, risiko_plan, makros, skala_info = hole_alle_marktdaten_schnell(chart_tag, symbol, cfg_scan, timeframe=tf)
            chat, parsed, roh = starte_cockpit_analyse(cfg_scan, bild_bytes, daten, risiko_plan, makros, skala_info, extra_frage=extra_frage, status_cb=self._setze_status, ki_quelle=ki_quelle)
            self.aktueller_chat = chat
            markierungen = parsed.get("markierungen", []) if parsed else []
            pil_markiert = zeichne_markierungen_auf_bild(LETZTES_BILD, LETZTES_BILD_MARKIERT, markierungen)
            dauer = round(time.time() - t0, 1)
            if daten and risiko_plan:
                f = skala_info.get("faktor", 1.0)
                self.letzter_scan_kontext = {
                    "symbol": daten["symbol"], "chart_tag": skala_info.get("chart_tag", daten["symbol"]), "timeframe": tf.upper(), "skala_faktor": f,
                    "preis": skaliere_wert(daten["aktueller_preis"], f), "long_sl": skaliere_wert(risiko_plan["long_sl_vorschlag"], f),
                    "long_tp": skaliere_wert(risiko_plan["long_tp_vorschlag_2r"], f), "short_sl": skaliere_wert(risiko_plan["short_sl_vorschlag"], f),
                    "short_tp": skaliere_wert(risiko_plan["short_tp_vorschlag_2r"], f), "groesse_usd": risiko_plan["positionsgroesse_usd"],
                    "coach_entscheidung": parsed.get("coach_entscheidung", "") if parsed else "", "setup_note": parsed.get("setup_note", "") if parsed else "",
                }
            self.root.after(0, lambda: self._aktualisiere_scan_ui(fenster.title, daten, makros, skala_info, chat, parsed, roh, pil_markiert, dauer, tf))
        except Exception as e:
            self._setze_status(f"❌ Fehler beim Scan: {e}")
        finally:
            self.aktion_laeuft = False
            self._sperre_buttons(False)

    def _aktualisiere_scan_ui(self, fenster_titel, daten, makros, skala_info, chat, parsed, roh, pil_markiert, dauer, tf):
        self._setze_neues_chart_bild(pil_markiert)
        modell_name = chat.name if chat else "Keine KI"
        self.lbl_chart_info.config(text=f"📷 {fenster_titel}  |  KI: {modell_name}")
        self._setze_status(f"✅ Fertig in {dauer}s ({modell_name})")
        self.lbl_was_titel.config(text="1. WAS PASSIERT GERADE?")
        self.lbl_wo_titel.config(text="2. WO MUSST DU IM BILD HINSCHAUEN?")
        self.lbl_plan_titel.config(text=f"3. COACH-PLAN & PAPER-TRADING ({tf.upper()})")
        self.lbl_mathe_titel.config(text="4. ZEITEBENEN, NEWS & MAKRO-KOMPASS")

        kontext = (daten or {}).get("ticker_kontext") or {}
        earnings_warn = kontext.get("earnings_hinweis") if kontext.get("earnings_dringend") else ""

        if parsed:
            ampel = str(parsed.get("ampel", "GELB")).upper()
            coach = bereinige_markdown(parsed.get("coach_entscheidung") or parsed.get("ampel_titel") or "ANALYSE FERTIG")
            note = bereinige_markdown(str(parsed.get("setup_note", "")))
            kopf_titel = f"{coach}  |  Note: {note}" if note else coach
            fazit = bereinige_markdown(parsed.get("ein_satz_fazit", ""))
            if earnings_warn:
                fazit = f"{earnings_warn}\n{fazit}"
            self._zeige_hinweis_ampel(kopf_titel, fazit, ampel, bereinige_markdown(parsed.get("trend_einigkeit", "")), parsed.get("setup_check", []))
            self.voice.sage(f"{coach}. Note {note}. {fazit}")
            self.lbl_was.config(text="\n".join(f"• {bereinige_markdown(p)}" for p in parsed.get("was_passiert", [])) or "-")
            self.lbl_wo.config(text="\n".join(f"• {bereinige_markdown(p)}" for p in parsed.get("wo_schauen", [])) or "Siehe Markierungen [1], [2], [3] unten am Chart.")
            plan = parsed.get("plan", {})
            plan_zeilen = [f"Empfehlung:     {bereinige_markdown(str(plan.get('richtung', '-')))}"]
            if plan.get("long_trigger"):
                plan_zeilen.append(f"🟢 Long-Trigger:  {bereinige_markdown(str(plan.get('long_trigger')))}")
            if plan.get("short_trigger"):
                plan_zeilen.append(f"🔴 Short-Trigger: {bereinige_markdown(str(plan.get('short_trigger')))}")
            plan_zeilen.extend([f"Einstieg:       {bereinige_markdown(str(plan.get('einstieg', '-')))}", f"Stop-Loss:      {bereinige_markdown(str(plan.get('stop_loss', '-')))}", f"Ziel (TP):      {bereinige_markdown(str(plan.get('ziel', '-')))}", f"Risiko:         {bereinige_markdown(str(plan.get('risiko_info', '-')))}"])
            self.lbl_plan.config(text="\n".join(plan_zeilen))
        else:
            self._zeige_hinweis_ampel("🟡 ANALYSE (KLARTEXT)", earnings_warn or "Struktur-Formatierung übersprungen – Klartext unten.", "GELB")
            self.lbl_was.config(text=bereinige_markdown(roh))
            self.lbl_wo.config(text="-")
            self.lbl_plan.config(text="-")

        if daten:
            f, tag = skala_info.get("faktor", 1.0), skala_info.get("chart_tag", daten["symbol"])
            zeilen = []
            if skala_info.get("aktiv"):
                zeilen.append(f"Chart-Skala ({tag}): ~{skaliere_wert(daten['aktueller_preis'], f)}  |  ETF ({daten['symbol']}): {daten['aktueller_preis']} ({daten['tages_change_prozent']:+.2f}%)")
                zeilen.append(f"Vortag PDH/PDL: {skaliere_wert(daten['pdh'], f)} / {skaliere_wert(daten['pdl'], f)}  |  Daily EMA200: {skaliere_wert(daten['daily_ema200'], f)}")
            else:
                zeilen.append(f"{daten['symbol']}: {daten['aktueller_preis']} ({daten['tages_change_prozent']:+.2f}%)")
                zeilen.append(f"PDH/PDL: {daten['pdh']} / {daten['pdl']}  |  Daily EMA200: {daten['daily_ema200']}")
            if kontext.get("earnings_hinweis"):
                zeilen.append(kontext["earnings_hinweis"])
            zeilen.append("")
            for t in ["1wk", "1d", "4h", "1h", "5m"]:
                d = daten.get(t)
                if d:
                    icon = "🟢" if "BULLISH" in d["trend"] else ("🔴" if "BEARISH" in d["trend"] else "🟡")
                    zeilen.append(f"{icon} {t.upper():>3}: RSI {d['rsi']:>4} | RVOL {d['rvol']:>4}x | {d['trend'].split()[0]}")
            if makros:
                zeilen.append("")
                zeilen.append("Makro: " + " | ".join(f"{k.split()[0]}: {v}" for k, v in makros.items()))
            if kontext.get("news"):
                zeilen.append("")
                zeilen.append("Aktuelle Schlagzeilen:")
                for n in kontext["news"][:3]:
                    zeilen.append(f"• {n}")
            self.lbl_mathe.config(text="\n".join(zeilen))
        else:
            self.lbl_mathe.config(text="Nur Screenshot analysiert (kein passender Yahoo-Ticker).")

        self.chat_text.config(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.config(state="disabled")
        self._chat_hinzufuegen("Co-Pilot", f"Scan in {dauer}s fertig ({modell_name}). Halte F8 (oder Button), um per Sprache zu fragen!")

    def starte_weltlage(self):
        if self.aktion_laeuft:
            return
        self.aktion_laeuft = True
        self._sperre_buttons(True, "welt")
        self.config = lade_config()
        threading.Thread(target=self._weltlage_worker, args=(self._hole_ki_quelle(),), daemon=True).start()

    def _weltlage_worker(self, ki_quelle):
        t0 = time.time()
        try:
            chat, parsed, roh, welt = starte_weltlage_analyse(self.config, status_cb=self._setze_status, ki_quelle=ki_quelle)
            self.aktueller_chat = chat
            dauer = round(time.time() - t0, 1)
            self.root.after(0, lambda: self._aktualisiere_weltlage_ui(chat, parsed, roh, welt, dauer))
        except Exception as e:
            self._setze_status(f"❌ Fehler beim News-Radar: {e}")
        finally:
            self.aktion_laeuft = False
            self._sperre_buttons(False)

    def _aktualisiere_weltlage_ui(self, chat, parsed, roh, welt, dauer):
        modell_name = chat.name if chat else "Keine KI"
        self._setze_status(f"🌍 Weltlage in {dauer}s ({modell_name})")
        self.lbl_was_titel.config(text="🌍 WAS BEWEGT HEUTE DIE WELT?")
        self.lbl_wo_titel.config(text="📊 AUSWIRKUNG AUF DEINE MÄRKTE")
        self.lbl_plan_titel.config(text="🎯 TAGES-FAHRPLAN & RISIKEN")
        self.lbl_mathe_titel.config(text="📰 LIVE-PULS & ECHTE SCHLAGZEILEN (24H)")

        if parsed:
            ampel = str(parsed.get("ampel", "GELB")).upper()
            coach = bereinige_markdown(parsed.get("coach_entscheidung") or parsed.get("ampel_titel") or "WELTLAGE")
            note = bereinige_markdown(str(parsed.get("setup_note", "")))
            kopf_titel = f"{coach}  |  Stimmung: {note}" if note else coach
            fazit = bereinige_markdown(parsed.get("ein_satz_fazit", ""))
            self._zeige_hinweis_ampel(kopf_titel, fazit, ampel, bereinige_markdown(parsed.get("trend_einigkeit", "")), parsed.get("setup_check", []))
            self.voice.sage(f"{coach}. {fazit}")
            self.lbl_was.config(text="\n".join(f"• {bereinige_markdown(p)}" for p in parsed.get("was_passiert", [])) or "-")
            self.lbl_wo.config(text="\n".join(f"• {bereinige_markdown(p)}" for p in parsed.get("wo_schauen", [])) or "-")
            plan = parsed.get("plan", {})
            plan_zeilen = [f"Stimmung:     {bereinige_markdown(str(plan.get('richtung', '-')))}"]
            if plan.get("long_trigger"):
                plan_zeilen.append(f"🟢 Rückenwind: {bereinige_markdown(str(plan.get('long_trigger')))}")
            if plan.get("short_trigger"):
                plan_zeilen.append(f"🔴 Gegenwind:  {bereinige_markdown(str(plan.get('short_trigger')))}")
            plan_zeilen.extend([f"Im Blick:     {bereinige_markdown(str(plan.get('einstieg', '-')))}", f"Störfeuer:    {bereinige_markdown(str(plan.get('stop_loss', '-')))}", f"Profiteur:    {bereinige_markdown(str(plan.get('ziel', '-')))}", f"Hinweis:      {bereinige_markdown(str(plan.get('risiko_info', '-')))}"])
            self.lbl_plan.config(text="\n".join(plan_zeilen))
        else:
            self._zeige_hinweis_ampel("🟡 WELTLAGE (KLARTEXT)", "Rohbericht unten.", "GELB")
            self.lbl_was.config(text=bereinige_markdown(roh))
            self.lbl_wo.config(text="-")
            self.lbl_plan.config(text="-")

        puls, schlagzeilen = welt.get("puls", {}), welt.get("schlagzeilen", [])
        zeilen = []
        if puls:
            zeilen.append(" | ".join(f"{k.split('(')[0].strip()}: {v['preis']} ({v['change']:+.2f}%)" for k, v in puls.items()))
            zeilen.append("")
        zeilen.extend(f"• {z}" for z in schlagzeilen[:10]) if schlagzeilen else zeilen.append("Keine Schlagzeilen geladen.")
        self.lbl_mathe.config(text="\n".join(zeilen))

        self.chat_text.config(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.config(state="disabled")
        self._chat_hinzufuegen("Welt-Radar", f"Briefing in {dauer}s fertig ({modell_name}). Frag mich gern per Chat oder Taste F8!")

    def _sende_chat_aus_feld(self):
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.chat_entry.delete(0, "end")
        self._sende_chat_nachricht(text)

    def _sende_chat_nachricht(self, text):
        if not self.aktueller_chat:
            self._chat_hinzufuegen("Hinweis", "Bitte erst oben auf '📸 Live-Scan' oder '🌍 Weltlage' klicken!")
            return
        self._chat_hinzufuegen("Du", text)
        self.btn_senden.config(state="disabled")
        threading.Thread(target=self._chat_worker, args=(text,), daemon=True).start()

    def _chat_worker(self, text):
        try:
            prompt = f"{text}\n\n(Antworte kurz, verständlich, auf Deutsch und ohne Markdown-Sternchen wie ** oder ###.)"
            antwort = self.aktueller_chat.send_message(prompt)
            self._chat_hinzufuegen(f"Co-Pilot ({self.aktueller_chat.name})", antwort)
            self.voice.sage(antwort)
        except Exception as e:
            self._chat_hinzufuegen("Fehler", f"Rückfrage fehlgeschlagen: {str(e).splitlines()[0][:100]}")
        finally:
            self.root.after(0, lambda: self.btn_senden.config(state="normal"))


def main():
    print("[COCKPIT] Starte Oberflaeche...")
    root = tk.Tk()
    app = TradingCockpitApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Fehler! Druecke Enter zum Schliessen...")
        