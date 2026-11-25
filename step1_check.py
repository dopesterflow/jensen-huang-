import csv
import json
from pathlib import Path
from datetime import datetime

from google import genai
from google.genai import types


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
TRADES_CSV = LOG_DIR / "trades.csv"


def lade_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def bereite_tagebuch_vor():
    LOG_DIR.mkdir(exist_ok=True)

    if not TRADES_CSV.exists():
        kopfzeile = [
            "Zeitpunkt",
            "Modus",
            "Markt",
            "Richtung",
            "Einstiegspreis",
            "Stop_Loss",
            "Take_Profit",
            "Positionsgroesse_USD",
            "Ergebnis_USD",
            "Begruendung"
        ]
        with open(TRADES_CSV, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(kopfzeile)
        print(f"[OK] Neues Trade-Tagebuch erstellt: {TRADES_CSV}")
    else:
        print(f"[OK] Trade-Tagebuch bereits vorhanden: {TRADES_CSV}")


def teste_gemini_mit_fallback(api_key, modelle, erfahrungs_modus):
    """Probiert die Modelle der Reihe nach durch, falls eines überlastet ist (503/429)."""
    if not api_key or "HIER_DEINEN" in api_key:
        print("[WARNUNG] Bitte zuerst deinen echten Gemini API-Key in config.json eintragen!")
        return

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta"),
    )

    prompt = (
        f"Du bist mein Trading-Co-Pilot im Modus '{erfahrungs_modus}'. "
        "Antworte auf Deutsch in maximal zwei Sätzen: Bestätige, dass du bereit bist, "
        "und erinnere kurz daran, warum Stop-Loss wichtig ist."
    )

    for modell in modelle:
        print(f"[INFO] Versuche Gemini-Modell '{modell}' (v1beta)...")
        try:
            antwort = client.models.generate_content(
                model=modell,
                contents=prompt,
            )
            print(f"\n--- ANTWORT VON GEMINI ({modell}) ---")
            print(antwort.text.strip())
            print("------------------------------------------\n")
            return

        except Exception as fehler:
            print(f"[FALLBACK] '{modell}' aktuell nicht verfügbar ({type(fehler).__name__}). Wechsle zum nächsten...")

    print("[FEHLER] Aktuell ist keines der konfigurierten Gemini-Modelle erreichbar.")


def main():
    print("========================================")
    print("  KI-TRADING-BOT: SCHRITT 1 SYSTEMCHECK ")
    print("========================================")
    print(f"Startzeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Projektordner: {BASE_DIR}\n")

    config = lade_config()
    risiko = config["risiko"]
    erfahrungs_modus = config.get("erfahrungs_modus", "FORTGESCHRITTEN")

    print(f"[OK] Handels-Modus: {config['trading_mode']} (Sicherheits-Modus aktiv)")
    print(f"[OK] Co-Pilot-Modus: {erfahrungs_modus}")
    print(f"[OK] Paper-Startkapital: {risiko['startkapital_paper_usd']} USD")
    print(f"[OK] Max. Risiko pro Trade: {risiko['max_risiko_pro_trade_prozent']} %")
    print(f"[OK] Max. Tagesverlust (Kill-Switch): {risiko['max_tagesverlust_prozent']} %\n")

    bereite_tagebuch_vor()

    modelle = config.get("gemini_models", ["gemini-3.8-flash"])
    teste_gemini_mit_fallback(
        api_key=config.get("gemini_api_key", ""),
        modelle=modelle,
        erfahrungs_modus=erfahrungs_modus,
    )

    print("[FERTIG] Schritt 1 abgeschlossen!")


if __name__ == "__main__":
    main()
    