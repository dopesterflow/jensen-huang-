import csv
from datetime import datetime
import json
from pathlib import Path
import threading

import yfinance as yf

from step2_markt_analyse import lade_config
from step3_monitor_auge import _frage_ki_mit_fallback


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
KONTO_DATEI = LOG_DIR / "paper_konto.json"
TRADES_CSV = LOG_DIR / "trades.csv"

CSV_FELDER = [
    "datum",
    "symbol",
    "chart_tag",
    "richtung",
    "timeframe",
    "einstieg",
    "ausstieg",
    "stop_loss",
    "take_profit",
    "groesse_usd",
    "gebuehr_usd",
    "netto_pnl_usd",
    "netto_pnl_prozent",
    "abschluss_grund",
    "haltedauer_min",
    "war_warnung",
    "warnung_grund",
    "ki_notiz",
]

_KONTO_LOCK = threading.Lock()


def _initiales_konto(config):
    startkapital = float(config.get("risiko", {}).get("startkapital_paper_usd", 1000.0))
    return {
        "startkapital_usd": startkapital,
        "aktuelles_kapital_usd": startkapital,
        "offener_trade": None,
        "abgeschlossene_trades": [],
    }


def lade_konto(config=None):
    if config is None:
        config = lade_config()
    LOG_DIR.mkdir(exist_ok=True)
    with _KONTO_LOCK:
        if not KONTO_DATEI.exists():
            konto = _initiales_konto(config)
            KONTO_DATEI.write_text(json.dumps(konto, indent=2, ensure_ascii=False), encoding="utf-8")
            return konto
        try:
            konto = json.loads(KONTO_DATEI.read_text(encoding="utf-8"))
            if "aktuelles_kapital_usd" not in konto:
                konto = _initiales_konto(config)
            return konto
        except Exception:
            return _initiales_konto(config)


def speichere_konto(konto):
    LOG_DIR.mkdir(exist_ok=True)
    with _KONTO_LOCK:
        KONTO_DATEI.write_text(json.dumps(konto, indent=2, ensure_ascii=False), encoding="utf-8")


def reset_paper_konto(config=None):
    if config is None:
        config = lade_config()
    konto = _initiales_konto(config)
    speichere_konto(konto)
    return konto


def berechne_tages_stats(konto):
    heute_str = datetime.now().strftime("%Y-%m-%d")
    tages_pnl = 0.0
    letzter_verlust_zeit = None

    for t in konto.get("abgeschlossene_trades", []):
        ende_zeit = str(t.get("ende_zeit", ""))
        pnl = float(t.get("netto_pnl_usd", 0.0))
        if ende_zeit.startswith(heute_str):
            tages_pnl += pnl
        if pnl < 0 and ende_zeit:
            try:
                dt = datetime.fromisoformat(ende_zeit)
                if letzter_verlust_zeit is None or dt > letzter_verlust_zeit:
                    letzter_verlust_zeit = dt
            except Exception:
                pass

    start_ref = float(konto.get("startkapital_usd", 1000.0)) or 1000.0
    tages_prozent = (tages_pnl / start_ref) * 100.0
    return round(tages_pnl, 2), round(tages_prozent, 2), letzter_verlust_zeit


def pruefe_risiko_warnungen(config, konto, richtung="", scan_kontext=None):
    """
    Prüft Cooldown, Tagesverlust und Coach-Einschätzung.
    Im PAPER-Modus: Warnungen ohne Zwangssperre!
    Im LIVE/STRENG-Modus: Harte Sperre bei Cooldown oder Tagesverlust.
    """
    risiko = config.get("risiko", {})
    trading_mode = str(config.get("trading_mode", "PAPER")).upper()
    max_tagesverlust_pct = float(risiko.get("max_tagesverlust_prozent", 3.0))
    cooldown_min = int(risiko.get("cooldown_nach_verlust_minuten", 30))

    tages_pnl, tages_pct, letzter_verlust = berechne_tages_stats(konto)
    warnungen = []
    hart_gesperrt = False

    if letzter_verlust is not None and cooldown_min > 0:
        vergangen_min = (datetime.now() - letzter_verlust).total_seconds() / 60.0
        if vergangen_min < cooldown_min:
            rest = max(1, int(round(cooldown_min - vergangen_min)))
            warnungen.append(
                f"⏳ COOLDOWN-CHECK: Letzter Verlust vor {int(vergangen_min)} Min "
                f"(Sperre noch {rest} Min). Kein Rache-Trade?"
            )
            if trading_mode == "LIVE":
                hart_gesperrt = True

    if tages_pct <= -abs(max_tagesverlust_pct):
        warnungen.append(
            f"🛑 TAGESLIMIT ERREICHT: Heute bei {tages_pnl:+.2f}$ ({tages_pct:+.2f}% | "
            f"Limit: -{max_tagesverlust_pct:.1f}%). Im Echtgeld wäre jetzt Feierabend!"
        )
        if trading_mode == "LIVE":
            hart_gesperrt = True

    if scan_kontext:
        coach = str(scan_kontext.get("coach_entscheidung", "")).upper()
        note_str = str(scan_kontext.get("setup_note", ""))
        richtung_u = richtung.upper()

        if "TEE TRINKEN" in coach or "ABWARTEN" in coach or "FINGER WEG" in coach:
            warnungen.append(f"☕ COACH-WARNUNG: Aktuelle Empfehlung war '{scan_kontext.get('coach_entscheidung')}'.")
        elif richtung_u == "LONG" and "SHORT" in coach:
            warnungen.append("🔄 GEGEN-COACH: Du gehst LONG, obwohl der Coach SHORT gesehen hat!")
        elif richtung_u == "SHORT" and "LONG" in coach:
            warnungen.append("🔄 GEGEN-COACH: Du gehst SHORT, obwohl der Coach LONG gesehen hat!")

        try:
            note_zahl = float(note_str.split("/")[0].strip())
            if note_zahl < 6.0:
                warnungen.append(f"📉 SCHWACHE NOTE ({note_str}): Setup unter 6/10 – nur kleines Übungs-Risiko sinnvoll.")
        except Exception:
            pass

    return {
        "hart_gesperrt": hart_gesperrt,
        "warnungen": warnungen,
        "war_warnung": len(warnungen) > 0,
        "warnung_kurz": " | ".join(w.split(":")[0] for w in warnungen),
    }


def hole_live_chart_preis(symbol, skala_faktor=1.0):
    """Holt extrem schnell den letzten 1m/5m Kurs von Yahoo und rechnet in die Chart-Skala um."""
    if not symbol:
        return None
    try:
        t = yf.Ticker(symbol)
        df = t.history(period="1d", interval="1m")
        if df.empty:
            df = t.history(period="5d", interval="5m")
        if df.empty:
            return None
        roh_preis = float(df["Close"].iloc[-1])
        return round(roh_preis * float(skala_faktor or 1.0), 4)
    except Exception:
        return None


def berechne_live_pnl(trade, aktueller_preis, fee_prozent=0.1):
    if not trade or not aktueller_preis or float(trade["einstieg"]) <= 0:
        return 0.0, 0.0, 0.0
    einstieg = float(trade["einstieg"])
    groesse = float(trade["groesse_usd"])
    richtung = str(trade["richtung"]).upper()
    preis = float(aktueller_preis)

    if richtung == "LONG":
        move_pct = (preis - einstieg) / einstieg
    else:
        move_pct = (einstieg - preis) / einstieg

    brutto_usd = groesse * move_pct
    gebuehr_usd = groesse * (2.0 * (float(fee_prozent) / 100.0))
    netto_usd = brutto_usd - gebuehr_usd
    netto_pct = (netto_usd / groesse) * 100.0 if groesse > 0 else 0.0
    return round(netto_usd, 2), round(netto_pct, 2), round(gebuehr_usd, 2)


def eroeffne_paper_trade(
    config,
    symbol,
    chart_tag,
    richtung,
    timeframe,
    einstieg,
    stop_loss,
    take_profit,
    groesse_usd,
    skala_faktor=1.0,
    war_warnung=False,
    warnung_grund="",
):
    konto = lade_konto(config)
    if konto.get("offener_trade"):
        return False, "Es läuft bereits ein aktiver Paper-Trade.", konto

    trade = {
        "id": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "start_zeit": datetime.now().isoformat(timespec="seconds"),
        "symbol": str(symbol).upper(),
        "chart_tag": str(chart_tag or symbol).upper(),
        "richtung": str(richtung).upper(),
        "timeframe": str(timeframe or "5M").upper(),
        "einstieg": round(float(einstieg), 4),
        "stop_loss": round(float(stop_loss), 4),
        "take_profit": round(float(take_profit), 4),
        "groesse_usd": round(float(groesse_usd), 2),
        "skala_faktor": float(skala_faktor or 1.0),
        "war_warnung": bool(war_warnung),
        "warnung_grund": str(warnung_grund or ""),
        "letzter_preis": round(float(einstieg), 4),
    }

    konto["offener_trade"] = trade
    speichere_konto(konto)
    return True, "Paper-Trade eröffnet!", konto


def pruefe_auto_sl_tp(trade, aktueller_preis):
    if not trade or aktueller_preis is None:
        return None
    p = float(aktueller_preis)
    sl = float(trade["stop_loss"])
    tp = float(trade["take_profit"])
    richtung = str(trade["richtung"]).upper()

    if richtung == "LONG":
        if sl > 0 and p <= sl:
            return "STOP-LOSS", sl
        if tp > 0 and p >= tp:
            return "TAKE-PROFIT", tp
    else:
        if sl > 0 and p >= sl:
            return "STOP-LOSS", sl
        if tp > 0 and p <= tp:
            return "TAKE-PROFIT", tp
    return None


def _schreibe_trade_in_csv(eintrag):
    LOG_DIR.mkdir(exist_ok=True)
    schreibe_header = True

    if TRADES_CSV.exists():
        zeilen = TRADES_CSV.read_text(encoding="utf-8-sig").splitlines()
        if len(zeilen) > 1:
            schreibe_header = False

    mode = "w" if schreibe_header else "a"
    with open(TRADES_CSV, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FELDER, delimiter=";", extrasaction="ignore")
        if schreibe_header:
            writer.writeheader()
        writer.writerow(eintrag)


def schliesse_paper_trade(config, ausstieg_preis, grund="MANUELL", ki_notiz=""):
    konto = lade_konto(config)
    trade = konto.get("offener_trade")
    if not trade:
        return None, konto

    fee_pct = float(config.get("risiko", {}).get("geschaetzte_gebuehr_prozent", 0.1))
    netto_usd, netto_pct, gebuehr_usd = berechne_live_pnl(trade, ausstieg_preis, fee_prozent=fee_pct)

    try:
        start_dt = datetime.fromisoformat(trade["start_zeit"])
        dauer_min = round((datetime.now() - start_dt).total_seconds() / 60.0, 1)
    except Exception:
        dauer_min = 0.0

    abgeschlossen = {
        "datum": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ende_zeit": datetime.now().isoformat(timespec="seconds"),
        "symbol": trade["symbol"],
        "chart_tag": trade["chart_tag"],
        "richtung": trade["richtung"],
        "timeframe": trade["timeframe"],
        "einstieg": trade["einstieg"],
        "ausstieg": round(float(ausstieg_preis), 4),
        "stop_loss": trade["stop_loss"],
        "take_profit": trade["take_profit"],
        "groesse_usd": trade["groesse_usd"],
        "gebuehr_usd": gebuehr_usd,
        "netto_pnl_usd": netto_usd,
        "netto_pnl_prozent": netto_pct,
        "abschluss_grund": grund,
        "haltedauer_min": dauer_min,
        "war_warnung": "JA" if trade.get("war_warnung") else "NEIN",
        "warnung_grund": trade.get("warnung_grund", ""),
        "ki_notiz": ki_notiz,
    }

    konto["aktuelles_kapital_usd"] = round(float(konto["aktuelles_kapital_usd"]) + netto_usd, 2)
    konto["offener_trade"] = None
    konto.setdefault("abgeschlossene_trades", []).append(abgeschlossen)
    speichere_konto(konto)
    _schreibe_trade_in_csv(abgeschlossen)
    return abgeschlossen, konto


def aktualisiere_letzte_ki_notiz(ki_notiz):
    konto = lade_konto()
    trades = konto.get("abgeschlossene_trades", [])
    if not trades:
        return
    trades[-1]["ki_notiz"] = ki_notiz
    speichere_konto(konto)


def erstelle_ki_trade_review(config, trade_ergebnis, ki_quelle="AUTO"):
    modus = config.get("erfahrungs_modus", "ANFAENGER").upper()
    prompt = f"""
Du bist mein ehrlicher Trading-Coach im Modus '{modus}'.
Ich habe gerade einen Paper-Trade abgeschlossen. Gib mir ein kurzes, direktes Review in 3-4 klaren Sätzen auf Deutsch (OHNE Markdown-Sternchen wie ** oder ###):
- Chart/Symbol: {trade_ergebnis['chart_tag']} ({trade_ergebnis['symbol']})
- Richtung & Timeframe: {trade_ergebnis['richtung']} auf {trade_ergebnis['timeframe']}
- Einstieg: {trade_ergebnis['einstieg']} -> Ausstieg: {trade_ergebnis['ausstieg']} ({trade_ergebnis['abschluss_grund']})
- Stop-Loss: {trade_ergebnis['stop_loss']} | Ziel: {trade_ergebnis['take_profit']}
- Netto-Ergebnis (inkl. Gebühren): {trade_ergebnis['netto_pnl_usd']:+.2f} USD ({trade_ergebnis['netto_pnl_prozent']:+.2f}%)
- Haltedauer: {trade_ergebnis['haltedauer_min']} Minuten
- Gab es vorab eine Trainer-Warnung (Cooldown/Gegen-Coach)? {trade_ergebnis['war_warnung']} ({trade_ergebnis['warnung_grund'] or 'Keine'})

Bewerte kurz: Was war diszipliniert und was ist die wichtigste Lehre aus diesem Trade?
"""
    chat, roh = _frage_ki_mit_fallback(
        config,
        prompt,
        bild_bytes=None,
        ki_quelle=ki_quelle,
        erwarte_json=False,
    )
    if chat and roh:
        aktualisiere_letzte_ki_notiz(roh[:300])
        return chat.name, roh
    return None, "Kein KI-Review verfügbar."


def erstelle_tages_fazit(config, konto, ki_quelle="AUTO"):
    """Erstellt einen abendlichen Coach-Report zu allen heutigen Trades & der Disziplin."""
    heute_str = datetime.now().strftime("%Y-%m-%d")
    heute_trades = [t for t in konto.get("abgeschlossene_trades", []) if str(t.get("ende_zeit", "")).startswith(heute_str)]
    stats = berechne_journal_statistik(konto)
    tages_pnl, tages_pct, _ = berechne_tages_stats(konto)

    prompt = f"""
Du bist mein Trading-Coach im Modus '{config.get('erfahrungs_modus', 'ANFAENGER')}'.
Gib mir ein ehrliches, motivierendes Feierabend-Zeugnis in 4-5 kurzen Sätzen auf Deutsch (OHNE Markdown-Sternchen ** oder ###):
- Heutiges Datum: {heute_str}
- Heutige abgeschlossene Paper-Trades: {len(heute_trades)}
- Heutiger Netto-PnL: {tages_pnl:+.2f} USD ({tages_pct:+.2f}%)
- Gesamtstatistik bisher: {stats['anzahl']} Trades, Winrate {stats['winrate']}%, Saubere Trades PnL: {stats['pnl_sauber']:+.2f}$, Trotz Warnung PnL: {stats['pnl_warnung']:+.2f}$
- Details der heutigen Trades: {json.dumps(heute_trades[-8:], ensure_ascii=False)}

Hinweis: Falls heute 0 Trades gemacht wurden, lobe ausdrücklich die Geduld und Disziplin (kein FOMO / kein Trade bei schlechten Setups)!
"""
    chat, roh = _frage_ki_mit_fallback(
        config,
        prompt,
        bild_bytes=None,
        ki_quelle=ki_quelle,
        erwarte_json=False,
    )
    if chat and roh:
        return chat.name, roh
    return None, "Kein KI-Fazit verfügbar."


def berechne_journal_statistik(konto):
    trades = konto.get("abgeschlossene_trades", [])
    gesamt = len(trades)
    gewinner = [t for t in trades if float(t.get("netto_pnl_usd", 0)) > 0]
    verlierer = [t for t in trades if float(t.get("netto_pnl_usd", 0)) < 0]

    sauber = [t for t in trades if t.get("war_warnung") != "JA"]
    trotz_warnung = [t for t in trades if t.get("war_warnung") == "JA"]

    pnl_gesamt = round(sum(float(t.get("netto_pnl_usd", 0)) for t in trades), 2)
    pnl_sauber = round(sum(float(t.get("netto_pnl_usd", 0)) for t in sauber), 2)
    pnl_warnung = round(sum(float(t.get("netto_pnl_usd", 0)) for t in trotz_warnung), 2)

    winrate = round((len(gewinner) / gesamt) * 100.0, 1) if gesamt else 0.0

    return {
        "anzahl": gesamt,
        "gewinner": len(gewinner),
        "verlierer": len(verlierer),
        "winrate": winrate,
        "pnl_gesamt": pnl_gesamt,
        "anzahl_sauber": len(sauber),
        "pnl_sauber": pnl_sauber,
        "anzahl_warnung": len(trotz_warnung),
        "pnl_warnung": pnl_warnung,
    }