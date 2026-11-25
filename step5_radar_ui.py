import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageFont

from step4_paper_trader import (
    berechne_journal_statistik,
    eroeffne_paper_trade,
    erstelle_tages_fazit,
    lade_konto,
    pruefe_risiko_warnungen,
    reset_paper_konto,
)


BG_DARK = "#0f1419"
BG_CARD = "#18202b"
BG_INPUT = "#11171f"
BORDER = "#2a3646"
TEXT_MAIN = "#eef2f6"
TEXT_MUTED = "#9aa8b8"
ACCENT_BLUE = "#29b6f6"
ACCENT_GREEN = "#00e676"
ACCENT_YELLOW = "#ffca28"
ACCENT_RED = "#ff5252"

FARBEN_PIL = {
    "rot": (255, 75, 75),
    "gruen": (0, 230, 118),
    "gelb": (255, 210, 40),
    "blau": (41, 182, 246),
}

GLOSSAR_TEXT = """
📘 TRADING-SPICKZETTEL FÜR EINSTEIGER

• LONG vs. SHORT:
  LONG = Du setzt darauf, dass der Kurs steigt.
  SHORT = Du setzt darauf, dass der Kurs fällt.

• STOP-LOSS (SL) & TAKE-PROFIT (TP):
  SL = Dein automatischer Notausgang, damit ein Verlust nie wehtut (z. B. max. 1 % vom Konto).
  TP = Dein geplantes Gewinn-Ziel. Bei CRV 2:1 gewinnst du doppelt so viel wie du riskierst.

• RVOL (Relatives Volumen):
  Zeigt, wie viel Geld gerade im Markt steckt im Vergleich zum Durchschnitt.
  RVOL < 0.8 = Markt schläft (oft Fehlausbrüche).
  RVOL > 1.5 = Echte Power / großes Interesse!

• RSI (14):
  Misst auf einer Skala 0–100, ob ein Kurs kurzfristig heiß gelaufen ist.
  Über 70 = stark gestiegen (überkauft). Unter 30 = stark gefallen (überverkauft).

• EMA 20 / 50 / 200 (Gleitende Durchschnitte):
  Wie Leitplanken im Chart. Kurs über EMA = eher Aufwärtstrend, darunter = eher Abwärtstrend.
  Die 200er-EMA auf dem Tageschart ist die wichtigste Langfrist-Linie der großen Banken.

• PDH / PDL & PWH / PWL:
  PDH / PDL = Hoch und Tief von gestern (Previous Day High/Low).
  PWH / PWL = Hoch und Tief der letzten Woche. Dort liegen oft viele Orders!

• DXY & VIX (Makro-Kompass):
  DXY = Stärke des US-Dollars. Steigt der Dollar stark, haben Aktien & Krypto oft Gegenwind.
  VIX = Angst-Index an der US-Börse. Unter 20 = ruhig. Über 25 = Unruhe / starke Schwankungen.
"""


def bereinige_markdown(text):
    if not text:
        return ""
    sauber = text.replace("**", "").replace("__", "").replace("`", "")
    sauber = re.sub(r"^#{1,6}\s*", "", sauber, flags=re.MULTILINE)
    sauber = re.sub(r"^\s*[\*\-]\s+", "• ", sauber, flags=re.MULTILINE)
    return sauber.strip()


def lade_schrift(groesse=15):
    for pfad in ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "arial.ttf"]:
        try:
            return ImageFont.truetype(pfad, groesse)
        except Exception:
            continue
    return ImageFont.load_default()


def zeichne_markierungen_auf_bild(quell_pfad, ziel_pfad, markierungen):
    basis = Image.open(quell_pfad).convert("RGBA")
    breite, hoehe = basis.size

    if not markierungen:
        rgb = basis.convert("RGB")
        rgb.save(ziel_pfad, format="JPEG", quality=88)
        return rgb

    overlay = Image.new("RGBA", basis.size, (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    draw_main = ImageDraw.Draw(basis)
    font_num = lade_schrift(14)
    font_leg = lade_schrift(15)
    legende_eintraege = []

    for idx, m in enumerate(markierungen[:5], start=1):
        box = m.get("box_2d")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            y1, x1, y2, x2 = [float(v) for v in box]
        except Exception:
            continue

        if max(abs(y1), abs(x1), abs(y2), abs(x2)) <= 1.0:
            y1, x1, y2, x2 = y1 * 1000, x1 * 1000, y2 * 1000, x2 * 1000

        ymin, ymax = sorted([max(0, min(1000, y1)), max(0, min(1000, y2))])
        xmin, xmax = sorted([max(0, min(1000, x1)), max(0, min(1000, x2))])

        px1, py1 = int((xmin / 1000.0) * breite), int((ymin / 1000.0) * hoehe)
        px2, py2 = int((xmax / 1000.0) * breite), int((ymax / 1000.0) * hoehe)

        if px2 - px1 < 32:
            mx = (px1 + px2) // 2
            px1, px2 = max(4, mx - 20), min(breite - 4, mx + 20)
        if py2 - py1 < 32:
            my = (py1 + py2) // 2
            py1, py2 = max(4, my - 20), min(hoehe - 4, my + 20)

        farbe_key = str(m.get("farbe", "gelb")).lower().strip()
        rgb = FARBEN_PIL.get(farbe_key, FARBEN_PIL["gelb"])
        typ = str(m.get("typ", "kreis")).lower().strip()
        roh_label = str(m.get("label", f"Zone {idx}")).strip()
        sauber_label = re.sub(r"^\d+[\)\.\:]\s*", "", roh_label)

        if typ == "kreis":
            draw_ov.ellipse([px1, py1, px2, py2], fill=(rgb[0], rgb[1], rgb[2], 18))
            draw_main.ellipse([px1 - 1, py1 - 1, px2 + 1, py2 + 1], outline=(0, 0, 0, 210), width=5)
            draw_main.ellipse([px1, py1, px2, py2], outline=rgb + (245,), width=3)
        else:
            draw_ov.rectangle([px1, py1, px2, py2], fill=(rgb[0], rgb[1], rgb[2], 18))
            draw_main.rectangle([px1 - 1, py1 - 1, px2 + 1, py2 + 1], outline=(0, 0, 0, 210), width=5)
            draw_main.rectangle([px1, py1, px2, py2], outline=rgb + (245,), width=3)

        num_str = str(idx)
        badge_w, badge_h = 22, 22
        bx = max(4, min(breite - badge_w - 4, px1 - 4))
        by = max(4, min(hoehe - badge_h - 4, py1 - 10))
        draw_main.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=6, fill=rgb + (235,), outline=(10, 14, 18, 255), width=2)
        nb = draw_main.textbbox((0, 0), num_str, font=font_num)
        draw_main.text((bx + (badge_w - (nb[2] - nb[0])) // 2, by + (badge_h - (nb[3] - nb[1])) // 2 - 2), num_str, font=font_num, fill=(10, 14, 18, 255))
        legende_eintraege.append((num_str, rgb, sauber_label))

    komposit = Image.alpha_composite(basis, overlay).convert("RGB")
    if not legende_eintraege:
        komposit.save(ziel_pfad, format="JPEG", quality=90)
        return komposit

    dummy_draw = ImageDraw.Draw(komposit)
    zeilen = [[]]
    cur_x = 12
    for num_str, rgb, label in legende_eintraege:
        tb = dummy_draw.textbbox((0, 0), f" {num_str}  {label}", font=font_leg)
        pill_w = (tb[2] - tb[0]) + 24
        if cur_x + pill_w > breite - 12 and zeilen[-1]:
            zeilen.append([])
            cur_x = 12
        zeilen[-1].append((num_str, rgb, label, pill_w))
        cur_x += pill_w + 10

    leiste_h = len(zeilen) * 34 + 12
    gesamt = Image.new("RGB", (breite, hoehe + leiste_h), (20, 27, 36))
    gesamt.paste(komposit, (0, 0))
    draw_g = ImageDraw.Draw(gesamt)
    draw_g.line([(0, hoehe), (breite, hoehe)], fill=(42, 54, 70), width=2)

    ly = hoehe + 8
    for zeile in zeilen:
        lx = 12
        for num_str, rgb, label, pill_w in zeile:
            draw_g.rounded_rectangle([lx, ly, lx + pill_w, ly + 26], radius=6, fill=(26, 35, 48), outline=rgb, width=2)
            draw_g.rounded_rectangle([lx, ly, lx + 24, ly + 26], radius=6, fill=rgb)
            draw_g.text((lx + 7, ly + 4), num_str, font=font_num, fill=(10, 14, 18))
            draw_g.text((lx + 30, ly + 4), label, font=font_leg, fill=(238, 242, 246))
            lx += pill_w + 10
        ly += 34

    gesamt.save(ziel_pfad, format="JPEG", quality=90)
    return gesamt


def zeige_glossar_fenster(parent):
    pop = tk.Toplevel(parent)
    pop.title("📘 Trading-Spickzettel für Einsteiger")
    pop.configure(bg=BG_DARK)
    pop.geometry("680x560")
    txt = tk.Text(pop, bg=BG_CARD, fg=TEXT_MAIN, font=("Segoe UI", 11), wrap="word", relief="flat", padx=16, pady=14)
    txt.pack(fill="both", expand=True, padx=12, pady=12)
    txt.insert("end", GLOSSAR_TEXT.strip())
    txt.config(state="disabled")


def zeige_markt_radar_fenster(app, ergebnisse, fehler, dauer):
    app._setze_status(f"🔍 Radar fertig in {dauer}s ({len(ergebnisse)} Märkte).")
    pop = tk.Toplevel(app.root)
    pop.title("🔍 Markt-Radar – Wo ist gerade saubere Bewegung?")
    pop.configure(bg=BG_DARK)
    pop.geometry("1040x640")

    wrap = tk.Frame(pop, bg=BG_DARK, padx=16, pady=14)
    wrap.pack(fill="both", expand=True)

    kopf_row = tk.Frame(wrap, bg=BG_DARK)
    kopf_row.pack(fill="x", pady=(0, 10))
    tk.Label(kopf_row, text=f"🔍 WATCHLIST-RADAR ({len(ergebnisse)} Märkte in {dauer}s)", font=("Segoe UI", 14, "bold"), fg=TEXT_MAIN, bg=BG_DARK).pack(side="left")
    tk.Button(kopf_row, text="🔄 Neu scannen", command=lambda: (pop.destroy(), app.starte_markt_radar()), font=("Segoe UI", 9, "bold"), bg="#ffb300", fg="#140f02", relief="flat", padx=12, pady=4, cursor="hand2").pack(side="right")

    if ergebnisse:
        top = ergebnisse[0]
        if top["score"] >= 68:
            banner_bg, banner_fg = "#0e3b25", ACCENT_GREEN
            banner_titel = f"🥇 HEISSESTER MARKT GERADE: {top['name']} ({top['richtung']} | Score {top['score']}/100)"
            app.voice.sage(f"Top-Markt im Radar: {top['name']}, Richtung {top['richtung']}, Score {top['score']} von 100.")
        else:
            banner_bg, banner_fg = "#2b2612", ACCENT_YELLOW
            banner_titel = f"☕ BESTER KANDIDAT NUR {top['score']}/100: {top['name']} – aktuell eher ruhig oder gemischt"
            app.voice.sage(f"Aktuell kein starkes Top-Setup. Bester Kandidat ist {top['name']} mit {top['score']} Punkten.")

        banner = tk.Frame(wrap, bg=banner_bg, padx=14, pady=10, highlightthickness=2, highlightbackground=banner_fg)
        banner.pack(fill="x", pady=(0, 10))
        tk.Label(banner, text=banner_titel, font=("Segoe UI", 12, "bold"), fg=banner_fg, bg=banner_bg, anchor="w").pack(fill="x")
        vol_str = f"RVOL 5M {top['rvol_5m']:.2f}x / 1H {top['rvol_1h']:.2f}x" if top["hat_volumen"] else "Index ohne Intraday-Volumen"
        tk.Label(
            banner,
            text=(
                f"Trend: {top['trend_label']}  |  Heute: {top['tages_pct']:+.2f}%  |  1H-ATR: {top['atr_1h_pct']:.2f}%  |  "
                f"{vol_str}  |  RSI(1H): {top['rsi_1h']}\n"
                f"👉 {top['hinweis']}  (Für Detail-Check: '{top['tv_tipp']}' in TradingView öffnen & F5 drücken!)"
            ),
            font=("Segoe UI", 10),
            fg=TEXT_MAIN,
            bg=banner_bg,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
    else:
        tk.Label(wrap, text="Konnte leider keine Marktdaten laden.", fg=ACCENT_RED, bg=BG_DARK, font=("Segoe UI", 11)).pack(anchor="w")

    liste_wrap = tk.Frame(wrap, bg=BG_DARK)
    liste_wrap.pack(fill="both", expand=True)
    canvas = tk.Canvas(liste_wrap, bg=BG_DARK, highlightthickness=0)
    sb = ttk.Scrollbar(liste_wrap, orient="vertical", command=canvas.yview)
    rows_frame = tk.Frame(canvas, bg=BG_DARK)
    rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win_id = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    pop.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    def _kopiere_tv(tv_ticker):
        app.root.clipboard_clear()
        app.root.clipboard_append(tv_ticker)
        app._setze_status(f"📋 '{tv_ticker}' kopiert -> in TradingView einfügen & F5 drücken!")

    for idx, item in enumerate(ergebnisse, start=1):
        ampel_farbe = {"GRUEN": ACCENT_GREEN, "GELB": ACCENT_YELLOW, "ROT": ACCENT_RED}.get(item["ampel"], TEXT_MUTED)
        card = tk.Frame(rows_frame, bg=BG_CARD, padx=12, pady=8, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=4)
        top_r = tk.Frame(card, bg=BG_CARD)
        top_r.pack(fill="x")
        richt_icon = "🟢" if item["richtung"] == "LONG" else ("🔴" if item["richtung"] == "SHORT" else "🟡")
        tk.Label(top_r, text=f"#{idx}  {item['name']}   |   {richt_icon} {item['richtung']}   |   Score: {item['score']}/100", font=("Segoe UI", 11, "bold"), fg=ampel_farbe, bg=BG_CARD).pack(side="left")
        tk.Button(top_r, text=f"📋 TV: {item['tv_tipp']}", command=lambda t=item["tv_tipp"]: _kopiere_tv(t), font=("Segoe UI", 9, "bold"), bg="#253244", fg=TEXT_MAIN, relief="flat", padx=8, pady=2, cursor="hand2").pack(side="right")
        vol_info = f"RVOL 5M: {item['rvol_5m']:.2f}x | 1H: {item['rvol_1h']:.2f}x" if item["hat_volumen"] else "RVOL: k.A. (Index)"
        tk.Label(card, text=f"Preis: {item['preis']} ({item['tages_pct']:+.2f}%)  |  1H-Schwankung (ATR): {item['atr_1h_pct']:.2f}%  |  {vol_info}  |  Trend: {item['trend_label']}  |  RSI(1H): {item['rsi_1h']}", font=("Segoe UI", 9), fg=TEXT_MAIN, bg=BG_CARD, anchor="w").pack(fill="x", pady=(3, 2))
        tk.Label(card, text=item["hinweis"], font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_CARD, anchor="w").pack(fill="x")

    if fehler:
        tk.Label(wrap, text=f"Hinweis – ohne Daten übersprungen: {', '.join(fehler)}", font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_DARK).pack(anchor="w", pady=(6, 0))


def oeffne_order_ticket_fenster(app, richtung):
    konto = lade_konto(app.config)
    if konto.get("offener_trade"):
        messagebox.showinfo("Trade läuft bereits", "Du hast schon einen offenen Paper-Trade.\nSchließe ihn zuerst, bevor du den nächsten startest!")
        return
    if not app.letzter_scan_kontext:
        messagebox.showinfo("Erst scannen", "Bitte drücke zuerst einmal F5 (Live-Scan), damit Symbol, Preis, SL & TP ausgefüllt werden!")
        return

    k = app.letzter_scan_kontext
    risiko_check = pruefe_risiko_warnungen(app.config, konto, richtung=richtung, scan_kontext=k)
    if risiko_check["hart_gesperrt"]:
        messagebox.showerror(
            "STRENGE REGELN: GESPERRT",
            "Im STRENGEN Modus (Live-Regeln) greift jetzt das Sicherheits-Limit:\n\n"
            + "\n".join(risiko_check["warnungen"])
            + "\n\n(Zum freien Üben kannst du oben auf '🛡️ Regeln: PAPER' umschalten.)",
        )
        return

    pop = tk.Toplevel(app.root)
    pop.title(f"Paper {richtung} eröffnen – {k['chart_tag']}")
    pop.configure(bg=BG_DARK)
    pop.transient(app.root)
    pop.grab_set()
    wrap = tk.Frame(pop, bg=BG_DARK, padx=18, pady=14)
    wrap.pack(fill="both", expand=True)

    farbe_kopf = ACCENT_GREEN if richtung == "LONG" else ACCENT_RED
    tk.Label(wrap, text=f"{'🟢' if richtung == 'LONG' else '🔴'} PAPER {richtung}: {k['chart_tag']} ({k['timeframe']})", font=("Segoe UI", 14, "bold"), fg=farbe_kopf, bg=BG_DARK).pack(anchor="w", pady=(0, 8))

    if risiko_check["warnungen"]:
        warn_box = tk.Frame(wrap, bg="#3b260b", padx=10, pady=8, highlightthickness=1, highlightbackground=ACCENT_YELLOW)
        warn_box.pack(fill="x", pady=(0, 10))
        tk.Label(warn_box, text="⚠️ TRAINER-WARNUNG (Im Paper-Modus keine Sperre!):", font=("Segoe UI", 10, "bold"), fg=ACCENT_YELLOW, bg="#3b260b", anchor="w").pack(fill="x")
        tk.Label(warn_box, text="\n".join(risiko_check["warnungen"]), font=("Segoe UI", 10), fg=TEXT_MAIN, bg="#3b260b", justify="left", wraplength=420, anchor="w").pack(fill="x", pady=(4, 0))

    default_sl = k["long_sl"] if richtung == "LONG" else k["short_sl"]
    default_tp = k["long_tp"] if richtung == "LONG" else k["short_tp"]
    felder = {}
    for label_txt, init_val in [
        ("Einstiegspreis (Chart-Skala):", str(k["preis"])),
        ("Stop-Loss (SL):", str(default_sl)),
        ("Take-Profit (2:1 Ziel):", str(default_tp)),
        ("Positionsgröße (USD):", str(k["groesse_usd"])),
    ]:
        row = tk.Frame(wrap, bg=BG_DARK)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label_txt, font=("Segoe UI", 10), fg=TEXT_MAIN, bg=BG_DARK, width=26, anchor="w").pack(side="left")
        ent = tk.Entry(row, font=("Segoe UI", 11), bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", width=16)
        ent.insert(0, init_val)
        ent.pack(side="right", ipady=3)
        felder[label_txt] = ent

    def _abschicken():
        try:
            p_in = float(felder["Einstiegspreis (Chart-Skala):"].get().replace(",", "."))
            sl_in = float(felder["Stop-Loss (SL):"].get().replace(",", "."))
            tp_in = float(felder["Take-Profit (2:1 Ziel):"].get().replace(",", "."))
            gr_in = float(felder["Positionsgröße (USD):"].get().replace(",", "."))
            if p_in <= 0 or gr_in <= 0:
                raise ValueError("Preis und Größe müssen positiv sein.")
        except Exception as ex:
            messagebox.showerror("Eingabe prüfen", f"Bitte gültige Zahlen eingeben ({ex}).", parent=pop)
            return

        ok, msg, _ = eroeffne_paper_trade(
            app.config, symbol=k["symbol"], chart_tag=k["chart_tag"], richtung=richtung, timeframe=k["timeframe"],
            einstieg=p_in, stop_loss=sl_in, take_profit=tp_in, groesse_usd=gr_in, skala_faktor=k["skala_faktor"],
            war_warnung=risiko_check["war_warnung"], warnung_grund=risiko_check["warnung_kurz"],
        )
        pop.destroy()
        if ok:
            app._aktualisiere_konto_ui()
            app._chat_hinzufuegen("Paper-Konto", f"🚀 Paper {richtung} eröffnet: {k['chart_tag']} @ {p_in} | SL {sl_in} | TP {tp_in} | Größe ${gr_in:.2f}")
        else:
            messagebox.showwarning("Hinweis", msg)

    btn_row = tk.Frame(wrap, bg=BG_DARK)
    btn_row.pack(fill="x", pady=(14, 0))
    tk.Button(btn_row, text="Abbrechen", command=pop.destroy, bg="#253244", fg=TEXT_MAIN, relief="flat", padx=14, pady=6).pack(side="left")
    tk.Button(btn_row, text=f"✅ Paper {richtung} jetzt starten", command=_abschicken, font=("Segoe UI", 10, "bold"), bg="#00c853" if richtung == "LONG" else "#ff5252", fg="#09120c" if richtung == "LONG" else "#ffffff", relief="flat", padx=16, pady=6, cursor="hand2").pack(side="right")


def oeffne_schliessen_fenster(app):
    konto = lade_konto(app.config)
    trade = konto.get("offener_trade")
    if not trade:
        messagebox.showinfo("Kein Trade", "Es läuft aktuell kein offener Paper-Trade.")
        return

    pop = tk.Toplevel(app.root)
    pop.title("Paper-Trade schließen")
    pop.configure(bg=BG_DARK)
    pop.transient(app.root)
    pop.grab_set()
    wrap = tk.Frame(pop, bg=BG_DARK, padx=18, pady=14)
    wrap.pack(fill="both", expand=True)

    tk.Label(wrap, text=f"💰 {trade['richtung']} {trade['chart_tag']} schließen", font=("Segoe UI", 13, "bold"), fg=ACCENT_YELLOW, bg=BG_DARK).pack(anchor="w", pady=(0, 8))
    tk.Label(wrap, text="Ausstiegspreis (vorausgefüllt mit letztem Live-Kurs, bei Bedarf anpassen):", font=("Segoe UI", 10), fg=TEXT_MAIN, bg=BG_DARK).pack(anchor="w")
    ent_preis = tk.Entry(wrap, font=("Segoe UI", 12), bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat")
    ent_preis.insert(0, str(trade.get("letzter_preis") or trade["einstieg"]))
    ent_preis.pack(fill="x", pady=(6, 12), ipady=4)

    def _confirm_close():
        try:
            p_exit = float(ent_preis.get().replace(",", "."))
            if p_exit <= 0:
                raise ValueError()
        except Exception:
            messagebox.showerror("Fehler", "Bitte einen gültigen Ausstiegspreis eingeben.", parent=pop)
            return
        pop.destroy()
        app._beende_trade_und_review(p_exit, "MANUELL")

    btn_row = tk.Frame(wrap, bg=BG_DARK)
    btn_row.pack(fill="x")
    tk.Button(btn_row, text="Abbrechen", command=pop.destroy, bg="#253244", fg=TEXT_MAIN, relief="flat", padx=12, pady=6).pack(side="left")
    tk.Button(btn_row, text="✅ Jetzt schließen & KI-Review holen", command=_confirm_close, font=("Segoe UI", 10, "bold"), bg=ACCENT_YELLOW, fg="#111111", relief="flat", padx=14, pady=6, cursor="hand2").pack(side="right")


def oeffne_journal_fenster(app):
    konto = lade_konto(app.config)
    stats = berechne_journal_statistik(konto)
    pop = tk.Toplevel(app.root)
    pop.title("📊 Paper-Trading Journal & Tages-Fazit")
    pop.configure(bg=BG_DARK)
    pop.geometry("850x560")
    wrap = tk.Frame(pop, bg=BG_DARK, padx=16, pady=14)
    wrap.pack(fill="both", expand=True)

    tk.Label(wrap, text="📊 DEIN PAPER-TRADING JOURNAL", font=("Segoe UI", 15, "bold"), fg=TEXT_MAIN, bg=BG_DARK).pack(anchor="w")
    stat_box = tk.Frame(wrap, bg=BG_CARD, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER)
    stat_box.pack(fill="x", pady=(10, 12))
    stat_text = (
        f"Aktuelles Konto: ${konto['aktuelles_kapital_usd']:.2f} (Start: ${konto['startkapital_usd']:.2f})   |   "
        f"Trades: {stats['anzahl']} (Gewinner: {stats['gewinner']} | Verlierer: {stats['verlierer']} | Winrate: {stats['winrate']}%)\n"
        f"Gesamt-PnL (netto): {stats['pnl_gesamt']:+.2f}$   |   "
        f"✅ Saubere Trades ({stats['anzahl_sauber']}): {stats['pnl_sauber']:+.2f}$   |   "
        f"⚠️ Trotz Warnung ({stats['anzahl_warnung']}): {stats['pnl_warnung']:+.2f}$"
    )
    tk.Label(stat_box, text=stat_text, font=("Segoe UI", 10, "bold"), fg=ACCENT_BLUE, bg=BG_CARD, justify="left").pack(anchor="w")

    txt = tk.Text(wrap, bg=BG_INPUT, fg=TEXT_MAIN, font=("Consolas", 10), relief="flat", padx=8, pady=8)
    txt.pack(fill="both", expand=True)
    trades = list(reversed(konto.get("abgeschlossene_trades", [])))
    if not trades:
        txt.insert("end", "Noch keine abgeschlossenen Paper-Trades.\nStarte einen Live-Scan (F5) und klicke auf 'Paper LONG' oder 'Paper SHORT'!")
    else:
        for t in trades:
            warn_tag = "⚠️ WARNUNG" if t.get("war_warnung") == "JA" else "✅ SAUBER"
            txt.insert("end", f"{t['datum']} | {t['richtung']:<5} {t['chart_tag']:<7} ({t['timeframe']}) | In {t['einstieg']} -> Out {t['ausstieg']} ({t['abschluss_grund']}) | Netto: {float(t['netto_pnl_usd']):+6.2f}$ ({float(t['netto_pnl_prozent']):+5.2f}%) | {warn_tag}\n")
            if t.get("ki_notiz"):
                txt.insert("end", f"   Coach: {t['ki_notiz']}\n")
            txt.insert("end", "-" * 95 + "\n")
    txt.config(state="disabled")

    btn_bar = tk.Frame(wrap, bg=BG_DARK)
    btn_bar.pack(fill="x", pady=(10, 0))
    btn_fazit = tk.Button(btn_bar, text="🌙 KI-Tages-Fazit abrufen", font=("Segoe UI", 10, "bold"), bg=ACCENT_BLUE, fg="#07131a", relief="flat", padx=14, pady=6, cursor="hand2")
    btn_fazit.pack(side="left")

    def _hole_fazit():
        btn_fazit.config(state="disabled", text="⏳ Erstelle Fazit...")
        ki_q = app._hole_ki_quelle()
        def _worker():
            name, bericht = erstelle_tages_fazit(app.config, konto, ki_quelle=ki_q)
            def _show():
                btn_fazit.config(state="normal", text="🌙 KI-Tages-Fazit abrufen")
                app._chat_hinzufuegen(f"Feierabend-Coach ({name or 'KI'})", bericht)
                app.voice.sage(bericht)
                messagebox.showinfo("🌙 Dein KI-Tages-Fazit", bereinige_markdown(bericht), parent=pop)
            app.root.after(0, _show)
        threading.Thread(target=_worker, daemon=True).start()

    btn_fazit.config(command=_hole_fazit)

    def _reset():
        if messagebox.askyesno("Konto zurücksetzen", "Paper-Konto wirklich wieder auf Startkapital zurücksetzen?", parent=pop):
            reset_paper_konto(app.config)
            app._aktualisiere_konto_ui()
            pop.destroy()

    tk.Button(btn_bar, text="🔄 Konto auf Startkapital zurücksetzen", command=_reset, bg="#3e1418", fg=TEXT_MAIN, relief="flat", padx=12, pady=6).pack(side="right")
    