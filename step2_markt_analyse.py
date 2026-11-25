from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
import time
from pathlib import Path
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from google import genai
from google.genai import types


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

_MAKRO_CACHE = {"zeit": 0.0, "daten": None}

STANDARD_WATCHLIST = [
    "SPY",
    "QQQ",
    "^GDAXI",
    "GC=F",
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "NVDA",
    "TSLA",
    "AAPL",
    "DELL",
]

WATCHLIST_INFOS = {
    "SPY": ("S&P 500 (SPY)", "SPX500"),
    "QQQ": ("Nasdaq 100 (QQQ)", "NAS100"),
    "^GDAXI": ("DAX 40", "DE40"),
    "GC=F": ("Gold", "XAUUSD"),
    "BTC-USD": ("Bitcoin", "BTCUSD"),
    "ETH-USD": ("Ethereum", "ETHUSD"),
    "SOL-USD": ("Solana", "SOLUSD"),
    "NVDA": ("Nvidia", "NVDA"),
    "TSLA": ("Tesla", "TSLA"),
    "AAPL": ("Apple", "AAPL"),
    "DELL": ("Dell", "DELL"),
}

NEWS_SUCHBEGRIFFE = {
    "SPY": "S&P 500 stock market",
    "QQQ": "Nasdaq 100 tech stocks",
    "^GDAXI": "DAX Boerse Frankfurt",
    "GC=F": "Gold price XAU",
    "BTC-USD": "Bitcoin crypto",
    "ETH-USD": "Ethereum crypto",
    "SOL-USD": "Solana crypto",
    "NVDA": "Nvidia stock",
    "TSLA": "Tesla stock",
    "AAPL": "Apple stock",
    "DELL": "Dell stock",
}

SYMBOL_ALIASE = {
    "sp500": ("SPY", "S&P 500 ETF (mit echtem Volumen)"),
    "s&p500": ("SPY", "S&P 500 ETF (mit echtem Volumen)"),
    "us500": ("SPY", "S&P 500 ETF (mit echtem Volumen)"),
    "spx": ("SPY", "S&P 500 ETF (mit echtem Volumen)"),
    "es": ("ES=F", "S&P 500 Futures"),
    "nasdaq": ("QQQ", "Nasdaq 100 ETF (mit echtem Volumen)"),
    "nasdaq100": ("QQQ", "Nasdaq 100 ETF (mit echtem Volumen)"),
    "us100": ("QQQ", "Nasdaq 100 ETF (mit echtem Volumen)"),
    "ndx": ("QQQ", "Nasdaq 100 ETF (mit echtem Volumen)"),
    "dax": ("^GDAXI", "DAX 40 Index"),
    "de40": ("^GDAXI", "DAX 40 Index"),
    "ger40": ("^GDAXI", "DAX 40 Index"),
    "gold": ("GC=F", "Gold Futures"),
    "xauusd": ("GC=F", "Gold Futures"),
    "silber": ("SI=F", "Silber Futures"),
    "silver": ("SI=F", "Silber Futures"),
    "öl": ("CL=F", "WTI Rohöl Futures"),
    "oel": ("CL=F", "WTI Rohöl Futures"),
    "oil": ("CL=F", "WTI Rohöl Futures"),
    "dollar": ("DX-Y.NYB", "US-Dollar-Index (DXY)"),
    "dxy": ("DX-Y.NYB", "US-Dollar-Index (DXY)"),
    "btc": ("BTC-USD", "Bitcoin"),
    "bitcoin": ("BTC-USD", "Bitcoin"),
    "eth": ("ETH-USD", "Ethereum"),
    "ethereum": ("ETH-USD", "Ethereum"),
    "sol": ("SOL-USD", "Solana"),
    "solana": ("SOL-USD", "Solana"),
    "xrp": ("XRP-USD", "Ripple (XRP)"),
    "doge": ("DOGE-USD", "Dogecoin"),
    "nvidia": ("NVDA", "Nvidia Aktie"),
    "apple": ("AAPL", "Apple Aktie"),
    "tesla": ("TSLA", "Tesla Aktie"),
    "microsoft": ("MSFT", "Microsoft Aktie"),
    "amazon": ("AMZN", "Amazon Aktie"),
    "meta": ("META", "Meta Aktie"),
    "google": ("GOOGL", "Alphabet/Google Aktie"),
    "dell": ("DELL", "Dell Technologies Aktie"),
}


def uebersetze_symbol(eingabe):
    sauber = eingabe.strip().lower().replace(" ", "")
    if sauber in SYMBOL_ALIASE:
        ticker, beschreibung = SYMBOL_ALIASE[sauber]
        print(f"[INFO] '{eingabe}' automatisch übersetzt -> '{ticker}' ({beschreibung})")
        return ticker
    return eingabe.strip().upper()


def lade_config():
    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def berechne_indikatoren(df):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).fillna(100.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

    vol_avg20 = volume.iloc[-22:-2].mean() if len(volume) >= 22 else volume.mean()
    letztes_vol = volume.iloc[-2] if len(volume) >= 2 else volume.iloc[-1]
    rvol = (letztes_vol / vol_avg20) if vol_avg20 and vol_avg20 > 0 else 0.0

    aktueller_preis = float(close.iloc[-1])
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])

    if aktueller_preis > e20 > e50:
        trend = "BULLISH (Aufwärtstrend)"
    elif aktueller_preis < e20 < e50:
        trend = "BEARISH (Abwärtstrend)"
    else:
        trend = "NEUTRAL / GEMISCHT"

    return {
        "preis": round(aktueller_preis, 4),
        "ema20": round(e20, 4),
        "ema50": round(e50, 4),
        "trend": trend,
        "rsi": round(float(rsi.iloc[-1]), 1),
        "atr": round(float(atr.iloc[-1]), 4),
        "rvol": round(float(rvol), 2),
    }


def _lade_history(symbol, period, interval):
    return yf.Ticker(symbol).history(period=period, interval=interval)


def hole_multi_timeframe_daten(symbol):
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_5m = pool.submit(_lade_history, symbol, "5d", "5m")
        f_1h = pool.submit(_lade_history, symbol, "60d", "1h")
        f_1d = pool.submit(_lade_history, symbol, "2y", "1d")

        df_5m = f_5m.result()
        df_1h = f_1h.result()
        df_1d = f_1d.result()

    if df_5m.empty or df_1h.empty or df_1d.empty:
        return None

    df_4h = (
        df_1h.resample("4h")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )

    df_1wk = (
        df_1d.resample("W")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )

    pdh = round(float(df_1d["High"].iloc[-2]), 4) if len(df_1d) >= 2 else None
    pdl = round(float(df_1d["Low"].iloc[-2]), 4) if len(df_1d) >= 2 else None
    pwh = round(float(df_1wk["High"].iloc[-2]), 4) if len(df_1wk) >= 2 else None
    pwl = round(float(df_1wk["Low"].iloc[-2]), 4) if len(df_1wk) >= 2 else None

    df_52w = df_1wk.tail(52)
    hoch_52w = round(float(df_52w["High"].max()), 4) if not df_52w.empty else None
    tief_52w = round(float(df_52w["Low"].min()), 4) if not df_52w.empty else None
    daily_ema200 = round(float(df_1d["Close"].ewm(span=200, adjust=False).mean().iloc[-1]), 4)

    vortag_close = float(df_1d["Close"].iloc[-2]) if len(df_1d) >= 2 else float(df_1d["Open"].iloc[-1])
    aktuell = float(df_5m["Close"].iloc[-1])
    tages_change_prozent = round(((aktuell - vortag_close) / vortag_close) * 100, 2) if vortag_close > 0 else 0.0

    return {
        "symbol": symbol.upper(),
        "aktueller_preis": round(aktuell, 4),
        "tages_change_prozent": tages_change_prozent,
        "daily_last_close": round(float(df_1d["Close"].iloc[-1]), 4),
        "pdh": pdh,
        "pdl": pdl,
        "pwh": pwh,
        "pwl": pwl,
        "hoch_52w": hoch_52w,
        "tief_52w": tief_52w,
        "daily_ema200": daily_ema200,
        "5m": berechne_indikatoren(df_5m),
        "1h": berechne_indikatoren(df_1h),
        "4h": berechne_indikatoren(df_4h),
        "1d": berechne_indikatoren(df_1d),
        "1wk": berechne_indikatoren(df_1wk) if not df_1wk.empty else None,
    }


def hole_ticker_news_und_earnings(symbol):
    """Holt aktuelle 24h-News + prüft nahende Quartalszahlen (Earnings) für den gescannten Markt."""
    sym = (symbol or "").upper().strip()
    ergebnis = {"earnings_hinweis": "", "earnings_dringend": False, "news": []}
    if not sym:
        return ergebnis

    ist_aktie = (
        not sym.endswith("-USD")
        and "=F" not in sym
        and not sym.startswith("^")
        and sym not in {"SPY", "QQQ", "DX-Y.NYB"}
    )

    def _pruefe_earnings():
        if not ist_aktie:
            return "", False
        try:
            cal = yf.Ticker(sym).calendar
            kandidat = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if isinstance(ed, (list, tuple)) and ed:
                    kandidat = ed[0]
                elif ed is not None:
                    kandidat = ed
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    kandidat = cal.loc["Earnings Date"].iloc[0]
                elif "Earnings Date" in cal.columns:
                    kandidat = cal["Earnings Date"].iloc[0]

            if kandidat is not None:
                if isinstance(kandidat, datetime):
                    e_datum = kandidat.date()
                elif isinstance(kandidat, date):
                    e_datum = kandidat
                else:
                    e_datum = pd.to_datetime(str(kandidat)).date()

                tage = (e_datum - date.today()).days
                if tage == 0:
                    return (
                        f"🚨 EARNINGS HEUTE ({e_datum.strftime('%d.%m.%Y')})! Extreme Gap-/Sprung-Gefahr nach Börsenschluss oder vorbörslich – Daytrades vor den Zahlen schließen!",
                        True,
                    )
                if tage == 1:
                    return (
                        f"⚠️ EARNINGS MORGEN ({e_datum.strftime('%d.%m.%Y')})! Positionen nicht blind über Nacht halten.",
                        True,
                    )
                if 2 <= tage <= 5:
                    return f"📅 Earnings in {tage} Tagen ({e_datum.strftime('%d.%m.%Y')}).", False
        except Exception:
            pass
        return "", False

    def _lade_yf_news():
        treffer = []
        try:
            items = yf.Ticker(sym).news or []
            for it in items[:5]:
                content = it.get("content") if isinstance(it.get("content"), dict) else it
                titel = (content.get("title") or "").strip()
                provider = ""
                if isinstance(content.get("provider"), dict):
                    provider = content["provider"].get("displayName", "")
                if titel:
                    treffer.append(f"[{provider or 'Yahoo'}] {titel}")
        except Exception:
            pass
        return treffer

    def _lade_google_ticker_rss():
        treffer = []
        try:
            suchtext = NEWS_SUCHBEGRIFFE.get(sym, f"{sym} stock")
            url = f"https://news.google.com/rss/search?q={quote_plus(suchtext)}+when:1d&hl=en-US&gl=US&ceid=US:en"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:5]:
                    titel = (item.findtext("title") or "").strip()
                    if titel:
                        treffer.append(f"[News 24h] {titel}")
        except Exception:
            pass
        return treffer

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_earn = pool.submit(_pruefe_earnings)
        f_yf = pool.submit(_lade_yf_news)
        f_rss = pool.submit(_lade_google_ticker_rss)

        e_text, e_dringend = f_earn.result()
        roh_news = f_yf.result() + f_rss.result()

    gesehen = set()
    sauber_news = []
    for z in roh_news:
        kern = z.split("]")[-1].strip().lower()[:55]
        if kern not in gesehen:
            gesehen.add(kern)
            sauber_news.append(z)

    ergebnis["earnings_hinweis"] = e_text
    ergebnis["earnings_dringend"] = e_dringend
    ergebnis["news"] = sauber_news[:6]
    return ergebnis


def bewerte_markt_kandidat(daten):
    """Bewertet einen Markt rein mathematisch (0-100) nach Trend, Volumen, Bewegung & FOMO-Schutz."""
    sym = daten["symbol"]
    preis = float(daten["aktueller_preis"])
    tages_pct = float(daten.get("tages_change_prozent", 0.0))

    tfs = [daten.get("1d"), daten.get("4h"), daten.get("1h"), daten.get("5m")]
    bull_count = sum(1 for d in tfs if d and "BULLISH" in d["trend"])
    bear_count = sum(1 for d in tfs if d and "BEARISH" in d["trend"])

    if bull_count > bear_count and bull_count >= 2:
        richtung = "LONG"
        einigkeit = bull_count
        trend_label = f"{bull_count}/4 BULLISH"
    elif bear_count > bull_count and bear_count >= 2:
        richtung = "SHORT"
        einigkeit = bear_count
        trend_label = f"{bear_count}/4 BEARISH"
    else:
        richtung = "GEMISCHT"
        einigkeit = max(bull_count, bear_count)
        trend_label = f"{bull_count}B/{bear_count}S GEMISCHT"

    if einigkeit == 4:
        trend_pts = 35
    elif einigkeit == 3:
        trend_pts = 25
    elif einigkeit == 2:
        trend_pts = 12
    else:
        trend_pts = 4

    d5m = daten.get("5m") or {}
    d1h = daten.get("1h") or {}
    rvol_5m = float(d5m.get("rvol", 0.0))
    rvol_1h = float(d1h.get("rvol", 0.0))
    rvol_max = max(rvol_5m, rvol_1h)
    hat_volumen = rvol_max > 0.0

    if not hat_volumen:
        vol_pts = 12
    elif rvol_max >= 1.8:
        vol_pts = 30
    elif rvol_max >= 1.3:
        vol_pts = 24
    elif rvol_max >= 0.9:
        vol_pts = 16
    elif rvol_max >= 0.6:
        vol_pts = 8
    else:
        vol_pts = 2

    atr_1h = float(d1h.get("atr", 0.0))
    atr_1h_pct = round((atr_1h / preis) * 100.0, 2) if preis > 0 else 0.0
    atr_pts = min(12, int(round(atr_1h_pct * 15)))
    move_pts = min(8, int(round(abs(tages_pct) * 3)))
    bewegung_pts = atr_pts + move_pts

    rsi_1h = float(d1h.get("rsi", 50.0))
    rsi_5m = float(d5m.get("rsi", 50.0))
    fomo_warnung = ""

    if richtung == "LONG" and (rsi_1h > 72 or rsi_5m > 75):
        rsi_pts = 3
        fomo_warnung = "⚠️ Heißgelaufen (RSI hoch) – Rücksetzer abwarten"
    elif richtung == "SHORT" and (rsi_1h < 28 or rsi_5m < 25):
        rsi_pts = 3
        fomo_warnung = "⚠️ Stark abverkauft (RSI tief) – nicht blind hinterher"
    elif 35 <= rsi_1h <= 65:
        rsi_pts = 15
    else:
        rsi_pts = 10

    score = max(0, min(100, trend_pts + vol_pts + bewegung_pts + rsi_pts))

    if fomo_warnung:
        hinweis = fomo_warnung
    elif abs(tages_pct) >= 7.0:
        hinweis = f"⚠️ Heute schon {tages_pct:+.1f}% gelaufen – im F5-Scan auf News/Earnings & Rücksetzer prüfen!"
    elif score >= 72:
        hinweis = f"🔥 Sauberer {richtung}-Trend + starke Bewegung"
    elif hat_volumen and rvol_max < 0.7:
        hinweis = "😴 Wenig Volumen – Vorsicht vor Fehlausbrüchen"
    elif einigkeit <= 2:
        hinweis = "☕ Zeitebenen uneinig – lieber abwarten"
    else:
        hinweis = "👀 Solide Bewegung – in TradingView prüfen"

    ampel = "GRUEN" if score >= 70 else ("GELB" if score >= 48 else "ROT")
    name, tv_tipp = WATCHLIST_INFOS.get(sym, (sym, sym))

    return {
        "symbol": sym,
        "name": name,
        "tv_tipp": tv_tipp,
        "preis": round(preis, 2) if preis >= 10 else round(preis, 4),
        "tages_pct": tages_pct,
        "richtung": richtung,
        "trend_label": trend_label,
        "rvol_5m": rvol_5m,
        "rvol_1h": rvol_1h,
        "rvol_max": rvol_max,
        "hat_volumen": hat_volumen,
        "atr_1h_pct": atr_1h_pct,
        "rsi_1h": rsi_1h,
        "score": score,
        "ampel": ampel,
        "hinweis": hinweis,
    }


def scanne_watchlist(config=None, status_cb=None):
    """Scannt alle Watchlist-Märkte parallel und gibt sortierte Kandidaten zurück."""
    if config is None:
        config = lade_config()

    roh_liste = config.get("watchlist") or STANDARD_WATCHLIST
    symbole = [uebersetze_symbol(str(s)) for s in roh_liste if str(s).strip()]

    ergebnisse = []
    fehler = []

    def _scan_einen(sym):
        try:
            daten = hole_multi_timeframe_daten(sym)
            if not daten:
                return None, sym
            return bewerte_markt_kandidat(daten), None
        except Exception:
            return None, sym

    if status_cb:
        status_cb(f"🔍 Scanne {len(symbole)} Märkte parallel...")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for bewertung, fehl_sym in pool.map(_scan_einen, symbole):
            if bewertung:
                ergebnisse.append(bewertung)
            elif fehl_sym:
                fehler.append(fehl_sym)

    ergebnisse.sort(
        key=lambda x: (x["score"], x["rvol_max"], abs(x["tages_pct"])),
        reverse=True,
    )
    return ergebnisse, fehler


def _hole_einzel_makro(name, sym):
    try:
        df = yf.Ticker(sym).history(period="5d", interval="1d")
        if len(df) >= 2:
            alt = float(df["Close"].iloc[-2])
            neu = float(df["Close"].iloc[-1])
            diff = round(((neu - alt) / alt) * 100, 2)
            return name, f"{round(neu, 2)} ({diff:+.2f}%)"
    except Exception:
        pass
    return name, "Keine Daten"


def hole_makro_kompass(cache_sekunden=180):
    jetzt = time.time()
    if _MAKRO_CACHE["daten"] and (jetzt - _MAKRO_CACHE["zeit"] < cache_sekunden):
        return dict(_MAKRO_CACHE["daten"])

    kompass_symbole = {
        "S&P 500 (SPY)": "SPY",
        "Bitcoin (BTC)": "BTC-USD",
        "US-Dollar (DXY)": "DX-Y.NYB",
    }

    ergebnis = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_hole_einzel_makro, name, sym) for name, sym in kompass_symbole.items()]
        for f in futures:
            name, wert = f.result()
            ergebnis[name] = wert

    _MAKRO_CACHE["zeit"] = jetzt
    _MAKRO_CACHE["daten"] = dict(ergebnis)
    return ergebnis


def berechne_risiko_plan(daten, config, timeframe="5m"):
    risiko = config["risiko"]
    kapital = float(risiko["startkapital_paper_usd"])
    max_risiko_prozent = float(risiko["max_risiko_pro_trade_prozent"])
    gebuehr_prozent = float(risiko["geschaetzte_gebuehr_prozent"])

    tf = timeframe.lower() if timeframe else "5m"
    if tf not in daten or not daten.get(tf):
        tf = "5m"

    max_verlust_usd = kapital * (max_risiko_prozent / 100.0)
    preis = daten["aktueller_preis"]
    atr_tf = daten[tf]["atr"]

    sl_abstand = max(atr_tf * 1.5, preis * 0.003)
    sl_prozent = sl_abstand / preis

    gesamtkosten_faktor = sl_prozent + (2 * (gebuehr_prozent / 100.0))
    mathematische_groesse_usd = max_verlust_usd / gesamtkosten_faktor

    empfohlene_groesse_usd = min(mathematische_groesse_usd, kapital)
    hebel_hinweis = ""
    if mathematische_groesse_usd > kapital:
        hebel_hinweis = f" (Mathematisch wären {round(mathematische_groesse_usd, 2)} $ möglich, aber ohne Hebel gedeckelt auf {kapital} $)"

    return {
        "timeframe": tf.upper(),
        "kapital_usd": kapital,
        "max_verlust_usd": round(max_verlust_usd, 2),
        "sl_abstand_preis": round(sl_abstand, 4),
        "sl_abstand_prozent": round(sl_prozent * 100, 2),
        "long_sl_vorschlag": round(preis - sl_abstand, 4),
        "long_tp_vorschlag_2r": round(preis + (sl_abstand * 2), 4),
        "short_sl_vorschlag": round(preis + sl_abstand, 4),
        "short_tp_vorschlag_2r": round(preis - (sl_abstand * 2), 4),
        "positionsgroesse_usd": round(empfohlene_groesse_usd, 2),
        "hebel_hinweis": hebel_hinweis,
    }


def frage_gemini_analyse(config, daten, risiko_plan, makros):
    api_key = config.get("gemini_api_key", "")
    if not api_key or "HIER_DEINEN" in api_key:
        print("[WARNUNG] Kein Gemini API-Key gefunden.")
        return

    modus = config.get("erfahrungs_modus", "FORTGESCHRITTEN").upper()
    modelle = config.get("gemini_models", ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"])

    if modus == "ANFAENGER":
        stil_anweisung = (
            "Erkläre verständlich für einen Einsteiger, was die Zahlen (5m vs. 1h/4h/1D/1W, RSI, RVOL, "
            "PDH/PDL, PWH/PWL, 200er-EMA) bedeuten. Erkläre Fachbegriffe kurz in Klammern und warne klar vor Risiken."
        )
    elif modus == "PROFI":
        stil_anweisung = (
            "Antworte extrem knapp und präzise in Stichpunkten: 1) Top-Down-Bias (1W/1D -> 4h/1h -> 5m), "
            "2) Key-Levels (PWH/PWL, PDH/PDL, EMA200) & RVOL, 3) Konkretes Szenario (Long/Short/Wait)."
        )
    else:
        stil_anweisung = (
            "Antworte wie ein erfahrener Daytrading-Co-Pilot auf Augenhöhe (Top-Down-Analyse): Prüfe, ob "
            "der 5m-Chart zum 1h/4h/1D/1W-Bild und zum Makro-Kompass passt, weise auf PWH/PWL, PDH/PDL und die "
            "Daily-200-EMA hin und gib eine klare Empfehlung (Long-Idee, Short-Idee oder lieber abwarten)."
        )

    prompt = f"""
Du bist mein Trading-Co-Pilot im Modus '{modus}'.
{stil_anweisung}

WICHTIG: Nutze ausschließlich diese exakten berechneten Daten und erfinde keine Kurse dazu!

--- MAKRO-KOMPASS (Roter Faden) ---
{json.dumps(makros, indent=2, ensure_ascii=False)}

--- ANALYSIERTER MARKT: {daten['symbol']} ---
Aktueller Preis: {daten['aktueller_preis']} (Heute vs. Vortag: {daten['tages_change_prozent']:+.2f}%)
Vortag:   PDH = {daten['pdh']} | PDL = {daten['pdl']}
Vorwoche: PWH = {daten['pwh']} | PWL = {daten['pwl']}
52-Wochen-Spanne: Hoch = {daten['hoch_52w']} | Tief = {daten['tief_52w']}
Daily EMA 200 (Langfrist-Trendlinie): {daten['daily_ema200']}

5m-Chart:  {daten['5m']}
1h-Chart:  {daten['1h']}
4h-Chart:  {daten['4h']}
1D-Chart:  {daten['1d']}
1W-Chart:  {daten['1wk']}

--- RISIKO-RECHNER (Paper-Konto: {risiko_plan['kapital_usd']} USD, max. Risiko: {risiko_plan['max_verlust_usd']} USD) ---
Empfohlener {risiko_plan['timeframe']}-Stop-Abstand (1.5x ATR): {risiko_plan['sl_abstand_preis']} ({risiko_plan['sl_abstand_prozent']}%)
Max. Positionsgröße (ohne Hebel): {risiko_plan['positionsgroesse_usd']} USD
Falls Long: SL {risiko_plan['long_sl_vorschlag']} | 2:1-Ziel {risiko_plan['long_tp_vorschlag_2r']}
Falls Short: SL {risiko_plan['short_sl_vorschlag']} | 2:1-Ziel {risiko_plan['short_tp_vorschlag_2r']}
"""

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta"),
    )

    for modell in modelle:
        try:
            print(f"[INFO] Frage Gemini ({modell}) nach Einschätzung...")
            antwort = client.models.generate_content(model=modell, contents=prompt)
            print(f"\n================ CO-PILOT ({modell} | Modus: {modus}) ================")
            print(antwort.text.strip())
            print("========================================================================\n")
            return
        except Exception as e:
            kurz_fehler = str(e).splitlines()[0][:110]
            print(f"[FALLBACK] '{modell}' übersprungen -> {kurz_fehler}")
            time.sleep(0.3)

    print("[FEHLER] Konnte Gemini aktuell nicht erreichen.")


def zeige_dashboard(daten, risiko_plan, makros):
    print("\n============================================================")
    print(f"  MARKT-CHECK: {daten['symbol']}  |  Preis: {daten['aktueller_preis']}  ({daten['tages_change_prozent']:+.2f}%)")
    print("============================================================")
    print("Makro-Kompass:")
    for k, v in makros.items():
        print(f"  - {k}: {v}")

    print(f"\nSchlüssel-Levels:")
    print(f"  - Vortag:   PDH = {daten['pdh']}  |  PDL = {daten['pdl']}")
    print(f"  - Vorwoche: PWH = {daten['pwh']}  |  PWL = {daten['pwl']}")
    print(f"  - 52W High/Low: {daten['hoch_52w']} / {daten['tief_52w']}  |  Daily EMA200: {daten['daily_ema200']}")

    print("\nMulti-Timeframe (Top-Down Mathematik):")
    for tf in ["1wk", "1d", "4h", "1h", "5m"]:
        d = daten.get(tf)
        if d:
            print(f"  [{tf.upper():>3}] {d['trend']:<24} | RSI: {d['rsi']:>5} | RVOL: {d['rvol']:>4}x")

    print(f"\nRisiko-Rechner ({risiko_plan['timeframe']} | mit Gebühren-Puffer):")
    print(f"  - Max. erlaubter Verlust: {risiko_plan['max_verlust_usd']} USD")
    print(f"  - Sinnvoller SL-Abstand: {risiko_plan['sl_abstand_preis']} ({risiko_plan['sl_abstand_prozent']}%)")
    print(f"  - Erlaubte Positionsgröße: {risiko_plan['positionsgroesse_usd']} USD{risiko_plan['hebel_hinweis']}")
    print(f"  - Long-Idee  (2:1 CRV): SL {risiko_plan['long_sl_vorschlag']} -> TP {risiko_plan['long_tp_vorschlag_2r']}")
    print(f"  - Short-Idee (2:1 CRV): SL {risiko_plan['short_sl_vorschlag']} -> TP {risiko_plan['short_tp_vorschlag_2r']}")
    print("============================================================\n")


def main():
    config = lade_config()
    print("============================================================")
    print("  SCHRITT 2: TOP-DOWN-MATHEMATIK (1W BIS 5M) & CO-PILOT")
    print("============================================================")
    print("Du kannst jetzt ganz normal schreiben: sp500, nasdaq, dax, gold, btc, eth, dell, nvidia ...")
    print("Tippe 'q' zum Beenden.\n")

    while True:
        eingabe = input("Welchen Markt soll ich checken? (Enter = btc): ").strip()
        if eingabe.lower() == "q":
            print("Bis gleich, Akh!")
            break
        if not eingabe:
            eingabe = "btc"

        symbol = uebersetze_symbol(eingabe)

        print(f"\n[INFO] Lade echte Kerzendaten (5m bis 1W) für '{symbol}' und Makro-Kompass...")
        daten = hole_multi_timeframe_daten(symbol)
        if not daten:
            print(f"[FEHLER] Konnte für '{symbol}' keine Daten finden. Tipp: Probiere 'sp500', 'btc', 'gold' oder 'dell'.\n")
            continue

        makros = hole_makro_kompass()
        risiko_plan = berechne_risiko_plan(daten, config)

        zeige_dashboard(daten, risiko_plan, makros)
        frage_gemini_analyse(config, daten, risiko_plan, makros)


if __name__ == "__main__":
    main()