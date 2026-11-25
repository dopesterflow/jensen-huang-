import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import queue
import re
import subprocess
import threading
import time

import numpy as np

try:
    import sounddevice as sd
    SOUNDDEVICE_OK = True
except ImportError:
    SOUNDDEVICE_OK = False

try:
    from faster_whisper import WhisperModel
    WHISPER_OK = True
except ImportError:
    WHISPER_OK = False

try:
    import pythoncom
    import win32com.client
    WIN32_TTS_OK = True
except ImportError:
    WIN32_TTS_OK = False

try:
    import pyttsx3
    PYTTSX3_OK = True
except ImportError:
    PYTTSX3_OK = False


# "base" = extrem schnell (~0.4s auf Ryzen 9). Für noch höhere Genauigkeit auf "small" (~1.5s) ändern.
WHISPER_MODELL = "base"


def bereinige_fuer_sprache(text):
    """Macht Text sauber vorlesbar ohne Emojis oder Sonderzeichen-Salat."""
    if not text:
        return ""
    t = text.replace("->", " zu ").replace("|", ", ").replace("•", " ")
    t = re.sub(r"[*_`#]", "", t)
    t = re.sub(r"[^\wäöüßÄÖÜ0-9.,!?:;%\-\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def erkenne_sprach_befehl(text):
    """Erkennt kurze Steuer-Befehle per Sprache."""
    sauber = re.sub(r"[^\wäöüß\s]", "", (text or "").lower()).strip()
    woerter = sauber.split()
    if len(woerter) <= 7:
        if any(k in sauber for k in ["live scan", "livescan", "mach einen scan", "chart scannen", "scan starten", "neuen scan"]):
            return "SCAN"
        if any(k in sauber for k in ["weltlage", "nachrichten", "aktuelle news", "was geht in der welt", "markt news"]):
            return "WELT"
        if any(k in sauber for k in ["trade schließen", "trade schliessen", "position schließen", "position schliessen", "mach den trade zu"]):
            return "CLOSE_TRADE"
    return "CHAT"


class SprachAssistent:
    """Kapselt blitzschnelle lokale Spracherkennung (CPU int8) und zuverlässiges Windows-TTS."""

    def __init__(self, status_cb=None):
        self.status_cb = status_cb
        self.whisper_model = None
        self._lade_lock = threading.Lock()
        self._frames = []
        self._stream = None
        self.aufnahme_laeuft = False
        self.samplerate = 16000

        self.vorlesen_aktiv = False
        self._tts_queue = queue.Queue()
        threading.Thread(target=self._tts_worker, daemon=True).start()

    def verfuegbar_info(self):
        return WHISPER_OK and SOUNDDEVICE_OK

    def lade_modell_im_hintergrund(self):
        if not WHISPER_OK:
            return
        threading.Thread(target=self._lade_whisper, daemon=True).start()

    def _lade_whisper(self):
        with self._lade_lock:
            if self.whisper_model is not None:
                return self.whisper_model
            try:
                t0 = time.time()
                if self.status_cb:
                    self.status_cb(f"🎙️ Lade Sprachmodell '{WHISPER_MODELL}'...")
                self.whisper_model = WhisperModel(
                    WHISPER_MODELL,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=10,
                )
                dauer = round(time.time() - t0, 1)
                print(f"[VOICE] Whisper '{WHISPER_MODELL}' bereit (geladen in {dauer}s, 10 CPU-Threads).")
                if self.status_cb:
                    self.status_cb("✅ Voice bereit (Taste F8 oder Button halten)!")
            except Exception as e:
                print(f"[VOICE FEHLER] Whisper konnte nicht geladen werden: {e}")
                if self.status_cb:
                    self.status_cb(f"⚠️ Voice-Fehler: {e}")
            return self.whisper_model

    def starte_aufnahme(self):
        if not (WHISPER_OK and SOUNDDEVICE_OK):
            return False, "Bitte erst 'pip install faster-whisper sounddevice' im venv ausführen."
        if self.aufnahme_laeuft:
            return True, ""

        self._frames = []
        try:
            def _callback(indata, frames, time_info, status):
                if self.aufnahme_laeuft:
                    self._frames.append(indata.copy())

            self.aufnahme_laeuft = True
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                callback=_callback,
            )
            self._stream.start()
            return True, ""
        except Exception as e:
            self.aufnahme_laeuft = False
            print(f"[VOICE MIKRO FEHLER] {e}")
            return False, f"Mikrofon-Fehler: {e}"

    def stoppe_und_transkribiere(self):
        if not self.aufnahme_laeuft:
            return ""
        self.aufnahme_laeuft = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
        except Exception:
            pass

        if not self._frames:
            return ""

        audio = np.concatenate(self._frames, axis=0).flatten()
        dauer_sek = len(audio) / float(self.samplerate)
        max_pegel = float(np.max(np.abs(audio))) if len(audio) else 0.0

        if dauer_sek < 0.30:
            print(f"[VOICE] Aufnahme zu kurz ({dauer_sek:.2f}s) – Taste beim Sprechen gedrückt halten.")
            return ""
        if max_pegel < 0.004:
            print(f"[VOICE] Kein Mikrofon-Signal erkannt (Pegel: {max_pegel:.4f}) – Windows-Standardmikro prüfen!")
            return ""

        modell = self._lade_whisper()
        if not modell:
            return ""

        t0 = time.time()
        try:
            segmente, _ = modell.transcribe(
                audio,
                language="de",
                beam_size=1,
                best_of=1,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt="Trading, SPX500, Bitcoin, Long, Short, Stop-Loss, Take-Profit, RVOL, Live-Scan, Weltlage.",
            )
            text = " ".join(s.text.strip() for s in segmente).strip()
            print(
                f"[VOICE] Aufnahme {dauer_sek:.1f}s | Pegel {max_pegel:.3f} | "
                f"Erkannt in {time.time() - t0:.2f}s: '{text}'"
            )
            return text
        except Exception as e:
            print(f"[VOICE FEHLER] Transkription: {e}")
            return ""

    # --- SPRACHAUSGABE (WINDOWS SAPI5 + POWERSHELL FALLBACK) ---
    def sage(self, text):
        if not self.vorlesen_aktiv:
            return
        sauber = bereinige_fuer_sprache(text)
        if not sauber:
            return
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
            except Exception:
                break
        self._tts_queue.put(sauber[:450])

    def _sprich_powershell(self, text):
        sauber_ps = text.replace("'", "''")
        ps_befehl = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 2; $s.Volume = 100; "
            "$de = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'de*' } | Select-Object -First 1; "
            "if ($de) { $s.SelectVoice($de.VoiceInfo.Name) }; "
            f"$s.Speak('{sauber_ps}');"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_befehl],
            creationflags=flags,
            timeout=25,
            check=False,
        )

    def _tts_worker(self):
        sp_voice = None
        if WIN32_TTS_OK:
            try:
                pythoncom.CoInitialize()
                sp_voice = win32com.client.Dispatch("SAPI.SpVoice")
                sp_voice.Volume = 100
                sp_voice.Rate = 2
                gewaehlt = "Standard"
                for v in sp_voice.GetVoices():
                    desc = str(v.GetDescription())
                    low = desc.lower()
                    if any(k in low for k in ["german", "deutsch", "hedda", "katja", "stefan"]):
                        sp_voice.Voice = v
                        gewaehlt = desc
                        break
                print(f"[TTS] Windows SAPI.SpVoice bereit (Stimme: {gewaehlt}).")
            except Exception as e:
                print(f"[TTS HINWEIS] SAPI.SpVoice Fehler ({e}) -> nutze PowerShell-Fallback.")
                sp_voice = None
        else:
            print("[TTS] win32com nicht gefunden -> nutze PowerShell-Sprachausgabe.")

        while True:
            text = self._tts_queue.get()
            if text is None:
                break
            if not self.vorlesen_aktiv:
                continue

            # Falls inzwischen schon ein neuerer Text wartet, nur den neusten sprechen
            while not self._tts_queue.empty():
                try:
                    neuer = self._tts_queue.get_nowait()
                    if neuer is None:
                        return
                    text = neuer
                except Exception:
                    break

            if sp_voice is not None:
                try:
                    # 18 = SVSFPurgeBeforeSpeak (2) | SVSFIsNotXML (16)
                    sp_voice.Speak(text, 18)
                    continue
                except Exception as e:
                    print(f"[TTS FEHLER] SAPI.SpVoice ({e}) -> weiche auf PowerShell aus.")

            try:
                self._sprich_powershell(text)
            except Exception as e:
                print(f"[TTS FEHLER] PowerShell-TTS ({e})")
                if PYTTSX3_OK:
                    try:
                        eng = pyttsx3.init()
                        eng.setProperty("rate", 185)
                        eng.say(text)
                        eng.runAndWait()
                    except Exception as e2:
                        print(f"[TTS FEHLER] pyttsx3 ({e2})")