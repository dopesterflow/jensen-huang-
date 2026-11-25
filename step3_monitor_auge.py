from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from datetime import datetime
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import mss
import pygetwindow as gw
import requests
import yfinance as yf
from PIL import Image
from google import genai
from google.genai import types

try:
    import ollama
    OLLAMA_VERFUEGBAR = True
except ImportError:
    OLLAMA_VERFUEGBAR = False

from step2_markt_analyse import (
    lade_config,
    uebersetze_symbol,
    hole_multi_timeframe_daten,
    hole_makro_kompass,
    hole_ticker_news_und_earnings,
    berechne_risiko_plan,
)


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LETZTES_BILD = LOG_DIR / "letzter_chart.jpg"
LETZTES_BILD_MARKIERT = LOG_DIR / "letzter_chart_markiert.jpg"

_SKALEN_CACHE = {}
_MODELL_SPERRE_BIS = {}
_VERALTETE_MODELLE = {"gemini-2.5-flash", "gemini-2.5-pro", "gemini-4-hydrogen"}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_EXTENDED_FRAME_BOUNDS = 9

try:
    user32.SetProcessDPIAware()
except Exception:
    pass

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL


def _baue_gemini_client(api_key):
    http_kwargs = {"api_version": "v1beta", "timeout": 12_000}
    if hasattr(types, "HttpRetryOptions"):
        http_kwargs["retry_options"] = types.HttpRetryOptions(attempts=1)
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(**http_kwargs))


class GeminiChatWrapper:
    """Einheitliche Chat-Hülle für Gemini."""
    def __init__(self, chat, modell):
        self.chat = chat
        self.name = modell

    def send_message(self, text):
        return self.chat.send_message(text).text.strip()


class LokalerOllamaChat:
    """Einheitliche Chat-Hülle für deine lokale RTX 3080 über Ollama (100% VRAM-optimiert)."""
    def __init__(self, modell, bild_pfad=None):
        self.modell = modell
        self.name = f"LOKAL RTX 3080 ({modell})"
        self.bild_pfad = str(bild_pfad) if bild_pfad else None
        self.messages = []
        try:
            self.client = ollama.Client(timeout=60)
        except TypeError:
            self.client = ollama.Client()

    def _rufe_ollama_auf(self, erwarte_json=False):
        t0 = time.time()
        kwargs = {
            "model": self.modell,
            "messages": self.messages,
            "think": False,
            "keep_alive": "15m",
            "options": {"num_ctx": 4096, "num_predict": 1000, "temperature": 0.2},
        }
        if erwarte_json:
            kwargs["format"] = "json"
        try:
            res = self.client.chat(**kwargs)
        except TypeError:
            kwargs.pop("think", None)
            try:
                res = self.client.chat(**kwargs)
            except TypeError:
                kwargs.pop("keep_alive", None)
                kwargs.pop("format", None)
                res = self.client.chat(**kwargs)
        print(f"[OLLAMA] Antwort von '{self.modell}' in {round(time.time() - t0, 1)}s.")
        return res.message.content.strip()

    def sende_erstes_bild(self, prompt, erwarte_json=True):
        msg = {"role": "user", "content": prompt}
        if self.bild_pfad:
            msg["images"] = [self.bild_pfad]
        self.messages.append(msg)
        text = self._rufe_ollama_auf(erwarte_json=erwarte_json)
        self.messages.append({"role": "assistant", "content": text})
        return text

    def send_message(self, text):
        self.messages.append({"role": "user", "content": text})
        antwort_text = self._rufe_ollama_auf(erwarte_json=False)
        self.messages.append({"role": "assistant", "content": antwort_text})
        return antwort_text


def hole_prozessname(hwnd):
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_proc:
        return ""
    try:
        puffer = ctypes.create_unicode_buffer(512)
        groesse = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h_proc, 0, puffer, ctypes.byref(groesse)):
            return Path(puffer.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(h_proc)


def hole_echtes_fenster_rechteck(fenster):
    """Gibt das sichtbare Fenster-Rechteck ohne unsichtbaren Windows-10-Schattenrand zurück."""
    hwnd = getattr(fenster, "_hWnd", None)
    if hwnd:
        try:
            rect = wintypes.RECT()
            res = dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS), ctypes.byref(rect), ctypes.sizeof(rect))
            if res == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:
            pass
    return int(fenster.left), int(fenster.top), int(fenster.left + fenster.width), int(fenster.top + fenster.height)


def finde_tradingview_fenster():
    kandidaten, diagnose = [], []
    for w in gw.getAllWindows():
        hwnd = getattr(w, "_hWnd", None)
        if not hwnd or not user32.IsWindowVisible(hwnd) or w.isMinimized or w.width < 400 or w.height < 300:
            continue
        titel = (w.title or "").strip()
        prozess = hole_prozessname(hwnd)
        if titel:
            diagnose.append((titel, prozess))
        if "tradingview" in titel.lower() or "tradingview" in prozess:
            kandidaten.append(w)
    if not kandidaten:
        return None, diagnose
    kandidaten.sort(key=lambda w: w.width * w.height, reverse=True)
    return kandidaten[0], diagnose


def pruefe_privatsphaere_sichtbar(fenster):
    """5-Punkte-Privatsphäre-Raster: Schützt auch vor halb überlappenden Fenstern im Split-Screen."""
    left, top, right, bottom = hole_echtes_fenster_rechteck(fenster)
    breite, hoehe = right - left, bottom - top
    tv_hwnd = getattr(fenster, "_hWnd", None)
    for fx, fy in [(0.50, 0.50), (0.15, 0.15), (0.85, 0.15), (0.15, 0.85), (0.85, 0.85)]:
        pt = wintypes.POINT(int(left + breite * fx), int(top + hoehe * fy))
        root_hwnd = user32.GetAncestor(user32.WindowFromPoint(pt), 3)
        laenge = user32.GetWindowTextLengthW(root_hwnd)
        puffer = ctypes.create_unicode_buffer(laenge + 1)
        user32.GetWindowTextW(root_hwnd, puffer, laenge + 1)
        sichtbarer_titel = puffer.value
        sichtbarer_prozess = hole_prozessname(root_hwnd)
        if (tv_hwnd and root_hwnd == tv_hwnd) or "tradingview" in sichtbarer_titel.lower() or "tradingview" in sichtbarer_prozess:
            continue
        return False, f"{sichtbarer_titel or 'Unbekanntes Fenster'} ({sichtbarer_prozess or '?'})"
    return True, fenster.title


def knipse_tradingview_fenster(fenster):
    LOG_DIR.mkdir(exist_ok=True)
    f_left, f_top, f_right, f_bottom = hole_echtes_fenster_rechteck(fenster)
    center_x, center_y = int((f_left + f_right) / 2), int((f_top + f_bottom) / 2)
    with mss.MSS() as sct:
        ziel_monitor = sct.monitors[1]
        for mon in sct.monitors[1:]:
            if mon["left"] <= center_x < mon["left"] + mon["width"] and mon["top"] <= center_y < mon["top"] + mon["height"]:
                ziel_monitor = mon
                break
        left, top = max(f_left, ziel_monitor["left"]), max(f_top, ziel_monitor["top"])
        right, bottom = min(f_right, ziel_monitor["left"] + ziel_monitor["width"]), min(f_bottom, ziel_monitor["top"] + ziel_monitor["height"])
        roh_bild = sct.grab({"left": int(left), "top": int(top), "width": int(max(1, right - left)), "height": int(max(1, bottom - top))})
    img = Image.frombytes("RGB", roh_bild.size, roh_bild.bgra, "raw", "BGRX")
    if img.width > 1280:
        img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    img.save(LETZTES_BILD, format="JPEG", quality=85)
    puffer = io.BytesIO()
    img.save(puffer, format="JPEG", quality=85)
    return puffer.getvalue()


def extrahiere_chart_tag(titel):
    sauber = titel.replace("\u2014 TradingView", "").replace("- TradingView", "").strip()
    if not sauber:
        return None
    erstes_wort = re.split(r"[\s\u00b7|/]+", sauber)[0].upper()
    return None if (not erstes_wort or erstes_wort == "TRADINGVIEW") else erstes_wort


def extrahiere_symbol_aus_titel(titel):
    erstes_wort = extrahiere_chart_tag(titel)
    if not erstes_wort:
        return None
    if erstes_wort.endswith("USDT") and len(erstes_wort) > 4:
        return erstes_wort[:-4] + "-USD"
    if erstes_wort in {"XAUUSD", "GOLD"}:
        return "GC=F"
    if erstes_wort in {"US500", "SPX", "SP500", "SPX500"}:
        return "SPY"
    if erstes_wort in {"NAS100", "US100", "NDX", "USTEC"}:
        return "QQQ"
    if erstes_wort in {"DE40", "GER40", "DAX"}:
        return "^GDAXI"
    if erstes_wort.endswith("USD") and len(erstes_wort) > 3:
        return erstes_wort[:-3] + "-USD"
    return erstes_wort


def hole_skalen_info(chart_tag, symbol, daten):
    """Übersetzt ETF-Preise (z. B. SPY 722) exakt in deine Chart-Skala (z. B. SPX500 7257)."""
    if not daten:
        return {"chart_tag": chart_tag or "-", "faktor": 1.0, "aktiv": False}
    tag = (chart_tag or "").upper()
    referenz = "^GSPC" if (tag in {"US500", "SPX", "SP500", "SPX500"} and symbol == "SPY") else ("^NDX" if (tag in {"NAS100", "US100", "NDX", "USTEC"} and symbol == "QQQ") else None)
    if not referenz:
        return {"chart_tag": tag or symbol, "faktor": 1.0, "aktiv": False}
    cache_key = (tag, symbol)
    eintrag = _SKALEN_CACHE.get(cache_key)
    if eintrag and (time.time() - eintrag["zeit"] < 300):
        return dict(eintrag["info"])
    try:
        ref_df = yf.Ticker(referenz).history(period="5d", interval="1d")
        etf_close = daten.get("daily_last_close")
        if not etf_close:
            etf_df = yf.Ticker(symbol).history(period="5d", interval="1d")
            etf_close = float(etf_df["Close"].iloc[-1]) if not etf_df.empty else 0.0
        if not ref_df.empty and etf_close and etf_close > 0:
            info = {"chart_tag": tag, "referenz": referenz, "faktor": round(float(ref_df["Close"].iloc[-1]) / float(etf_close), 4), "aktiv": True}
            _SKALEN_CACHE[cache_key] = {"zeit": time.time(), "info": info}
            return dict(info)
    except Exception:
        pass
    return {"chart_tag": tag or symbol, "faktor": 1.0, "aktiv": False}


def hole_alle_marktdaten_schnell(chart_tag, symbol, config, timeframe="5m"):
    """Lädt Kerzen, Makro-Kompass, Chart-Skala & Live-News/Earnings gleichzeitig (parallel)."""
    if not symbol:
        return None, None, None, {"chart_tag": chart_tag or "-", "faktor": 1.0, "aktiv": False}
    tag = (chart_tag or "").upper()
    referenz = "^GSPC" if (tag in {"US500", "SPX", "SP500", "SPX500"} and symbol == "SPY") else ("^NDX" if (tag in {"NAS100", "US100", "NDX", "USTEC"} and symbol == "QQQ") else None)

    def _lade_ref_close():
        if not referenz:
            return None
        eintrag = _SKALEN_CACHE.get((tag, symbol))
        if eintrag and (time.time() - eintrag["zeit"] < 300):
            return eintrag["info"]
        try:
            df = yf.Ticker(referenz).history(period="5d", interval="1d")
            if not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_daten = pool.submit(hole_multi_timeframe_daten, symbol)
        f_makros = pool.submit(hole_makro_kompass)
        f_ref = pool.submit(_lade_ref_close)
        f_kontext = pool.submit(hole_ticker_news_und_earnings, symbol)
        daten, makros, ref_wert, ticker_kontext = f_daten.result(), f_makros.result(), f_ref.result(), f_kontext.result()

    if not daten:
        return None, None, None, {"chart_tag": chart_tag or "-", "faktor": 1.0, "aktiv": False}

    daten["ticker_kontext"] = ticker_kontext
    risiko_plan = berechne_risiko_plan(daten, config, timeframe=timeframe)

    if isinstance(ref_wert, dict):
        skala_info = dict(ref_wert)
    elif isinstance(ref_wert, float) and daten.get("daily_last_close"):
        skala_info = {"chart_tag": tag, "referenz": referenz, "faktor": round(ref_wert / float(daten["daily_last_close"]), 4), "aktiv": True}
        _SKALEN_CACHE[(tag, symbol)] = {"zeit": time.time(), "info": skala_info}
    else:
        skala_info = {"chart_tag": tag or symbol, "faktor": 1.0, "aktiv": False}
    return daten, risiko_plan, makros, skala_info


def skaliere_wert(wert, faktor):
    return None if wert is None else round(float(wert) * faktor, 2)


def _ermittle_session_kurz():
    jetzt = datetime.now()
    if jetzt.weekday() >= 5:
        return "Wochenende (Aktien zu, Krypto 24/7)"
    m = jetzt.hour * 60 + jetzt.minute
    if 9 * 60 <= m < 15 * 60 + 30:
        return "EU-Handel / US-Vorbörse (US-Aktien öffnen erst 15:30 Uhr!)"
    if 15 * 60 + 30 <= m < 17 * 60 + 30:
        return "US-OPEN (Heißes Volumen, erste 15-30 Min oft wild)"
    if 17 * 60 + 30 <= m < 20 * 60:
        return "US-Mittagsflaute (oft zähe Seitwärtsphasen & Fehlausbrüche!)"
    if 20 * 60 <= m < 22 * 60:
        return "US-Schlussstunde / Power Hour (oft klarer Zug bis 22:00 Uhr)"
    return "Nachtmarkt (Aktien zu / dünn, Krypto 24/7)"


def baue_zahlen_block(daten, risiko_plan, makros, skala_info):
    if not daten:
        return "\n[Hinweis: Für dieses Chart-Symbol lagen keine Yahoo-Zahlen vor - analysiere bitte rein anhand des Screenshots!]\n"

    f, skala_aktiv = skala_info.get("faktor", 1.0), skala_info.get("aktiv", False)
    tag = skala_info.get("chart_tag", daten["symbol"])
    tages_pct = float(daten.get("tages_change_prozent", 0.0))

    if abs(tages_pct) >= 5.0:
        dehn_alarm = f"🚨 STARK ÜBERDEHNT: Kurs heute schon {tages_pct:+.2f}% gelaufen! Nicht blind hinterherspringen (Gegen-Bounce/Rücksetzer-Gefahr)!"
    elif abs(tages_pct) >= 2.5:
        dehn_alarm = f"⚠️ SPÜRBARE TAGESBEWEGUNG ({tages_pct:+.2f}%): Auf sauberen Rücksetzer oder bestätigten Bruch achten."
    else:
        dehn_alarm = f"Normale Tagesbewegung ({tages_pct:+.2f}%)."

    kontext = daten.get("ticker_kontext") or {}
    earnings_txt = kontext.get("earnings_hinweis") or "Keine unmittelbaren Quartalszahlen in den nächsten Tagen erkannt."
    news_liste = kontext.get("news") or []
    news_block = "\n".join(f"  - {n}" for n in news_liste) if news_liste else "  - Keine aktuellen Schlagzeilen gefunden."

    skalen_hinweis = ""
    if skala_aktiv:
        skalen_hinweis = (
            f"\nWICHTIG ZUR PREIS-SKALA: Auf dem TradingView-Chart siehst du '{tag}' (~{skaliere_wert(daten['aktueller_preis'], f)}). "
            f"Nenne im Plan & Text IMMER die umgerechneten '{tag}'-Chart-Preise!\n"
            f"  Aktuell: {skaliere_wert(daten['aktueller_preis'], f)} | PDH: {skaliere_wert(daten['pdh'], f)} | PDL: {skaliere_wert(daten['pdl'], f)} | Daily EMA200: {skaliere_wert(daten['daily_ema200'], f)}\n"
            f"  Long ({tag} {risiko_plan['timeframe']}): SL {skaliere_wert(risiko_plan['long_sl_vorschlag'], f)} | TP {skaliere_wert(risiko_plan['long_tp_vorschlag_2r'], f)}\n"
            f"  Short ({tag} {risiko_plan['timeframe']}): SL {skaliere_wert(risiko_plan['short_sl_vorschlag'], f)} | TP {skaliere_wert(risiko_plan['short_tp_vorschlag_2r'], f)}\n"
        )

    return f"""
--- UHRZEIT / SESSION & FALLEN-CHECK ---
Uhrzeit: {datetime.now().strftime('%H:%M')} Uhr | Session: {_ermittle_session_kurz()}
Überdehnungs-Check: {dehn_alarm}
Earnings-Check: {earnings_txt}

--- AKTUELLE NEWS ZU {daten['symbol']} (WARUM BEWEGT SICH DER KURS?) ---
{news_block}

--- HARTE MATHEMATIK-DATEN ({daten['symbol']}) ---
Preis: {daten['aktueller_preis']} ({tages_pct:+.2f}%) | PDH={daten['pdh']} | PDL={daten['pdl']} | PWH={daten['pwh']} | PWL={daten['pwl']} | Daily EMA200={daten['daily_ema200']}
1W: {daten['1wk']}
1D: {daten['1d']}
4H: {daten['4h']}
1H: {daten['1h']}
5M: {daten['5m']}
{skalen_hinweis}
--- MAKRO-KOMPASS ---
{json.dumps(makros, ensure_ascii=False)}

--- RISIKO-RECHNER ({risiko_plan['timeframe']}-ATR | Max. Risiko: {risiko_plan['max_verlust_usd']} USD | Größe: {risiko_plan['positionsgroesse_usd']} USD) ---
Long-Vorschlag: SL {risiko_plan['long_sl_vorschlag']} | 2:1 TP {risiko_plan['long_tp_vorschlag_2r']}
Short-Vorschlag: SL {risiko_plan['short_sl_vorschlag']} | 2:1 TP {risiko_plan['short_tp_vorschlag_2r']}
"""


def parse_ki_json(roh_text):
    if not roh_text:
        return None
    sauber = re.sub(r"^```(?:json)?\s*|\s*```$", "", roh_text.strip(), flags=re.IGNORECASE)
    start, ende = sauber.find("{"), sauber.rfind("}")
    if start == -1 or ende == -1 or ende <= start:
        return None
    try:
        daten = json.loads(sauber[start : ende + 1])
        return daten if isinstance(daten, dict) else None
    except Exception:
        return None


def _aktive_gemini_modelle(config):
    alle = config.get("gemini_models", ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"])
    gefiltert = [m for m in alle if m not in _VERALTETE_MODELLE]
    if "gemini-2.5-flash-lite" not in gefiltert:
        gefiltert.append("gemini-2.5-flash-lite")
    jetzt = time.time()
    verfuegbar = [m for m in gefiltert if _MODELL_SPERRE_BIS.get(m, 0.0) <= jetzt]
    return verfuegbar if verfuegbar else gefiltert


def _frage_ki_mit_fallback(config, prompt, bild_bytes=None, status_cb=None, ki_quelle="AUTO", erwarte_json=True):
    api_key = (config.get("gemini_api_key") or "").strip()
    ollama_modell = config.get("ollama_fallback_model", "qwen3.5:9b")
    quelle = ki_quelle.upper()

    if quelle != "LOKAL" and api_key and "HIER_DEINEN" not in api_key:
        client = _baue_gemini_client(api_key)
        ueberlastet_count = 0
        for modell in _aktive_gemini_modelle(config):
            try:
                print(f"[GEMINI] Frage '{modell}'...")
                if status_cb:
                    status_cb(f"🤖 Frage Cloud-KI ({modell})...")
                chat = client.chats.create(model=modell)
                content = [types.Part.from_bytes(data=bild_bytes, mime_type="image/jpeg"), prompt] if bild_bytes else prompt
                return GeminiChatWrapper(chat, modell), chat.send_message(content).text.strip()
            except Exception as e:
                _MODELL_SPERRE_BIS[modell] = time.time() + 300
                kurz = str(e).splitlines()[0][:100]
                print(f"[GEMINI FALLBACK] '{modell}' -> {kurz}")
                if "503" in kurz or "UNAVAILABLE" in kurz:
                    ueberlastet_count += 1
                    if ueberlastet_count >= 2 and quelle == "AUTO":
                        print("[GEMINI] Cloud überlastet -> springe direkt zur RTX 3080!")
                        break
    elif quelle != "LOKAL":
        print("[INFO] Kein Gemini API-Key in config.json -> nutze direkt RTX 3080.")

    if quelle != "CLOUD" and OLLAMA_VERFUEGBAR and ollama_modell:
        try:
            print(f"[OLLAMA] Starte '{ollama_modell}' auf RTX 3080...")
            if status_cb:
                status_cb(f"🖥️ RTX 3080 ({ollama_modell}) denkt...")
            lokal_chat = LokalerOllamaChat(ollama_modell, LETZTES_BILD if bild_bytes else None)
            return lokal_chat, lokal_chat.sende_erstes_bild(prompt, erwarte_json=erwarte_json)
        except Exception as e:
            print(f"[OLLAMA FEHLER] {e}")
            if status_cb:
                status_cb(f"❌ RTX 3080 Fehler: {e}")
    return None, "Weder Cloud-KI noch lokales Ollama erreichbar."


def starte_cockpit_analyse(config, bild_bytes, daten, risiko_plan, makros, skala_info, extra_frage="", status_cb=None, ki_quelle="AUTO"):
    modus = config.get("erfahrungs_modus", "ANFAENGER").upper()
    stil = (
        "Erkläre ruhig, ehrlich und anfängerfreundlich auf Deutsch: WARUM bewegt sich der Kurs (News/Earnings), ob die Bewegung heute schon zu weit gelaufen ist und welche Falle droht."
        if modus == "ANFAENGER"
        else ("Schreibe ultraknapp: Catalyst/Earnings, Top-Down-Bias, Überdehnung, Key-Levels, Invalidation." if modus == "PROFI" else "Verbinde Catalyst/News, Earnings-Risiko, Chart-Struktur, Session und klare Trigger.")
    )
    zahlen_block = baue_zahlen_block(daten, risiko_plan, makros, skala_info)
    prompt = f"""
Du bist mein ehrlicher Profi-Trading-Lehrer im Modus '{modus}'.
{stil}
WICHTIG:
1) KEINE Markdown-Sternchen (** oder ###). Sauberer Klartext.
2) KEINE leeren Phrasen wie "Das Volumen ist moderat, was auf Unsicherheit hindeutet"! Nutze echte Fakten aus den Daten:
   - Punkt 1 in 'was_passiert': WARUM bewegt sich der Markt heute? (News & Earnings-Check! Bei Earnings heute/morgen explizit vor 22:00 Uhr Gap-Risiko warnen!)
   - Punkt 2 in 'was_passiert': IST DER ZUG SCHON ABGEFAHREN? (Tages-% & RSI bewerten: Bei über ±5% vor blindem Hinterherjagen warnen!)
   - Punkt 3 in 'was_passiert': SESSION & VOLUMEN: Passt die aktuelle Uhrzeit/Session und RVOL?
3) KONSISTENZ: Jeder Punkt in 'wo_schauen' MUSS exakt zu Nummer und Farbe aus 'markierungen' passen (z.B. "[1] Gelber Kreis (Aktuelle Kerze): ...", "[2] Rote Box (Widerstand): ...").
4) BEREICH FÜR 'box_2d': Der Kerzen-Chart liegt links/mittig (x 60..760, y 150..660). KEINE Boxen rechts in die Watchlist (x > 780) oder unten ins Order-Fenster (y > 680)!

{zahlen_block}

{f"Meine direkte Frage: {extra_frage}" if extra_frage else "Gib mir deine ehrliche Lehrer-Einschätzung: Jetzt rein oder abwarten / Finger weg?"}

Antworte AUSSCHLIESSLICH als valides JSON:
{{
  "ampel": "GELB",
  "coach_entscheidung": "☕ ABWARTEN & TEE TRINKEN",
  "setup_note": "5/10",
  "trend_einigkeit": "⚠️ GEGEN DEN HAUPTTREND oder 🔥 MIT DEM HAUPTTREND",
  "ein_satz_fazit": "Klare Lehrer-Ansage in 1-2 Sätzen inkl. wichtigster Gefahr (z.B. Earnings/Überdehnung/Mittagsflaute).",
  "setup_check": [
    "✅/❌ Trend passt übergeordnet",
    "✅/❌ Genug Volumen & Bewegung nicht überdehnt",
    "✅/❌ Platz bis Widerstand/Support",
    "✅/❌ Keine Earnings-/News-Falle & Session passt"
  ],
  "was_passiert": [
    "Grund & News/Earnings heute: ...",
    "Überdehnung & Chart-Lage: ...",
    "Session & Volumen-Realität: ..."
  ],
  "wo_schauen": [
    "[1] Gelber Kreis (Aktuelle Kerze): ...",
    "[2] Rote Box (Widerstand/Support): ..."
  ],
  "plan": {{
    "richtung": "ABWARTEN",
    "long_trigger": "Wann Long gut wird (in Chart-Skala)",
    "short_trigger": "Wann Short gut wird (in Chart-Skala)",
    "einstieg": "Konkreter Einstiegsbereich (in Chart-Skala)",
    "stop_loss": "Konkreter SL-Preis (in Chart-Skala)",
    "ziel": "Konkretes Ziel (in Chart-Skala)",
    "risiko_info": "Max. 10 USD Verlust | ggf. vor 22:00 Uhr Earnings schließen!"
  }},
  "markierungen": [
    {{"label": "Aktuelle Kerze", "typ": "kreis", "farbe": "gelb", "box_2d": [420, 580, 560, 720]}},
    {{"label": "Widerstand", "typ": "box", "farbe": "rot", "box_2d": [280, 420, 350, 730]}}
  ]
}}
Regeln: 'ampel' nur GRUEN/GELB/ROT. 'coach_entscheidung' eine von: "🟢 JETZT LONG-CHANCE", "🔴 JETZT SHORT-CHANCE", "☕ ABWARTEN & TEE TRINKEN", "🚫 FINGER WEG (ZU RISKANT)". 'markierungen': 2-3 Stellen (x max 760, y max 660). 'farbe' nur rot/gruen/gelb/blau. 'typ' nur kreis/box.
"""
    chat, roh = _frage_ki_mit_fallback(config, prompt, bild_bytes=bild_bytes, status_cb=status_cb, ki_quelle=ki_quelle, erwarte_json=True)
    return chat, parse_ki_json(roh), roh


def _hole_puls_einzel(name, sym):
    try:
        df = yf.Ticker(sym).history(period="5d", interval="1d")
        if len(df) >= 2:
            alt, neu = float(df["Close"].iloc[-2]), float(df["Close"].iloc[-1])
            return name, {"preis": round(neu, 2), "change": round(((neu - alt) / alt) * 100, 2)}
    except Exception:
        pass
    return name, None


def _hole_yahoo_news(sym, limit=4):
    treffer = []
    try:
        for it in (yf.Ticker(sym).news or [])[:limit]:
            c = it.get("content") if isinstance(it.get("content"), dict) else it
            titel = (c.get("title") or "").strip()
            datum = (c.get("pubDate") or "")[:16].replace("T", " ")
            provider = c["provider"].get("displayName", "") if isinstance(c.get("provider"), dict) else str(it.get("publisher") or "")
            if titel:
                treffer.append(f"[{sym} | {provider or 'Yahoo'} | {datum}] {titel}")
    except Exception:
        pass
    return treffer


def _hole_google_rss(url, prefix, limit=6):
    treffer = []
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                titel = (item.findtext("title") or "").strip()
                if titel:
                    treffer.append(f"[{prefix} | {(item.findtext('pubDate') or '')[:22]}] {titel}")
    except Exception:
        pass
    return treffer


def hole_weltlage_daten():
    puls_symbole = {"S&P 500 (SPY)": "SPY", "Nasdaq (QQQ)": "QQQ", "Bitcoin (BTC)": "BTC-USD", "Gold": "GC=F", "US-Dollar (DXY)": "DX-Y.NYB", "Angst-Index (VIX)": "^VIX"}
    rss_us = "https://news.google.com/rss/search?q=(stock+market+OR+Federal+Reserve+OR+Trump+OR+inflation+OR+bitcoin)+when:1d&hl=en-US&gl=US&ceid=US:en"
    rss_de = "https://news.google.com/rss/search?q=(Boerse+OR+Aktien+OR+Zinsen+OR+EZB+OR+Bitcoin+OR+Trump)+when:1d&hl=de&gl=DE&ceid=DE:de"
    puls, schlagzeilen, gesehen = {}, [], set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        pf = [pool.submit(_hole_puls_einzel, n, s) for n, s in puls_symbole.items()]
        nf = [pool.submit(_hole_yahoo_news, "SPY", 4), pool.submit(_hole_yahoo_news, "BTC-USD", 3), pool.submit(_hole_yahoo_news, "GC=F", 2), pool.submit(_hole_google_rss, rss_us, "US-News", 6), pool.submit(_hole_google_rss, rss_de, "DE-News", 5)]
        for f in pf:
            n, w = f.result()
            if w:
                puls[n] = w
        for f in nf:
            for z in f.result():
                kern = z.split("]")[-1].strip().lower()[:60]
                if kern not in gesehen:
                    gesehen.add(kern)
                    schlagzeilen.append(z)
    return {"puls": puls, "schlagzeilen": schlagzeilen[:14]}


def starte_weltlage_analyse(config, status_cb=None, ki_quelle="AUTO"):
    if status_cb:
        status_cb("🌍 Lade Live-Schlagzeilen & Marktpuls...")
    welt = hole_weltlage_daten()
    modus = config.get("erfahrungs_modus", "ANFAENGER").upper()
    stil = "Erkläre einfach, ruhig und anfängerfreundlich auf Deutsch." if modus == "ANFAENGER" else "Analysiere knackig wie ein Makro-Stratege auf Deutsch."
    prompt = f"""
Du bist mein Weltlage- & Makro-Radar im Modus '{modus}'. {stil}
Nutze NUR diese echten Daten, erfinde KEINE News! Keine Markdown-Sternchen (** oder ###).
--- MARKT-PULS ---
{json.dumps(welt['puls'], indent=2, ensure_ascii=False)}
--- SCHLAGZEILEN (24H) ---
{chr(10).join(welt['schlagzeilen']) if welt['schlagzeilen'] else 'Keine Schlagzeilen geladen.'}
Antworte AUSSCHLIESSLICH als valides JSON:
{{
  "ampel": "GELB",
  "coach_entscheidung": "🟡 WELTLAGE: VORSICHTIG / GEMISCHT",
  "setup_note": "6/10",
  "trend_einigkeit": "Makro-Stimmung heute im Überblick",
  "ein_satz_fazit": "Kurze Gesamtzusammenfassung in 1-2 Sätzen.",
  "setup_check": ["✅/❌ Aktien (S&P / Nasdaq)", "✅/❌ Krypto (Bitcoin)", "✅/❌ Angst-Index (VIX < 20?)", "✅/❌ US-Dollar (kein Bremsklotz?)"],
  "was_passiert": ["Top-Thema 1", "Top-Thema 2", "Top-Thema 3"],
  "wo_schauen": ["Aktien: Rücken- oder Gegenwind?", "Krypto & Gold/Dollar: Wohin fließt Geld?"],
  "plan": {{"richtung": "RISK-ON / GEMISCHT / RISK-OFF", "long_trigger": "Rückenwind-Märkte", "short_trigger": "Druck-Märkte", "einstieg": "Worauf achten", "stop_loss": "Größtes Störfeuer", "ziel": "Stärkster Sektor", "risiko_info": "VIX & Dollar beachten"}}
}}
"""
    chat, roh = _frage_ki_mit_fallback(config, prompt, bild_bytes=None, status_cb=status_cb, ki_quelle=ki_quelle, erwarte_json=True)
    return chat, parse_ki_json(roh), roh, welt


def main():
    print("Starte fuer das Cockpit bitte: python step3_cockpit.py")


if __name__ == "__main__":
    main()