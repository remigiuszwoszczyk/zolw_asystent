import os
import json
import requests
import pandas as pd
from datetime import datetime

# =====================================================================
# --- KONFIGURACJA UŻYTKOWNIKA (WPISZ DANE PONIŻEJ) ---
# =====================================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKER = "BTC-USD"
KRAKEN_SYMBOL = "XXBTZUSD"  # Standardowy symbol BTC/USD na Krakenie
STAN_FILE = "stan_pozycji_zolwia.json"

# TAJNY LINK Z HEALTHCHECKS.IO (jeśli nie używasz, zostaw jak jest)
HEALTHCHECK_URL = "https://hc-ping.com/c361b307-0d83-4c2a-9272-8721d580228c"
# =====================================================================

# Proxy wyłączone (niepotrzebne na PythonAnywhere)
PROXIES = {}

def DOMYSLNY_STAN():
    return {
        "status": "BRAK_POZYCJI",
        "kierunek": None,
        "transze": [],
        "stop_loss": 0.0,
        "data_wejscia": None,
        "ostatni_raport_dobowy": ""
    }

def wyslij_telegram(wiadomosc):
    """Wysyła sformatowaną wiadomość na Telegram."""
    if "WPISZ" in TELEGRAM_TOKEN or "WPISZ" in TELEGRAM_CHAT_ID:
        print("⚠️ Błąd: Uzupełnij TELEGRAM_TOKEN oraz TELEGRAM_CHAT_ID w kodzie!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": wiadomosc,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"⚠️ Błąd wysyłania Telegrama: {e}")
        return False

def ping_healthcheck():
    """Wysyła sygnał 'żyję' do zewnętrznego strażnika."""
    if HEALTHCHECK_URL and "c361b307" not in HEALTHCHECK_URL:
        try:
            requests.get(HEALTHCHECK_URL, timeout=10)
        except Exception as e:
            print(f"Błąd Healthchecks: {e}")

def wczytaj_stan():
    """Wczytuje stan bota z pliku JSON."""
    if os.path.exists(STAN_FILE):
        with open(STAN_FILE, 'r') as f:
            try:
                return json.load(f)
            except Exception:
                print("Błąd odczytu pliku stanu. Tworzę nowy domyślny stan.")
                return DOMYSLNY_STAN()
    return DOMYSLNY_STAN()

def zapisz_stan(stan):
    """Zapisuje aktualny stan bota do pliku JSON."""
    try:
        with open(STAN_FILE, 'w') as f:
            json.dump(stan, f, indent=4)
    except Exception as e:
        print(f"❌ Krytyczny błąd zapisu pliku stanu: {e}")

def pobierz_i_oblicz_dane():
    """Pobiera dane z API Krakena i wylicza wskaźniki strategii Żółwia."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={KRAKEN_SYMBOL}&interval=1440"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        result = response.json()
        
        if result.get("error") and len(result["error"]) > 0:
            print(f"Błąd API Kraken: {result['error']}")
            return None
            
    except Exception as e:
        print(f"Błąd pobierania danych z Kraken API: {e}")
        return None

    result_data = result.get("result", {})
    klines = result_data.get(KRAKEN_SYMBOL)
    
    if not klines:
        print(f"Nie znaleziono danych dla klucza {KRAKEN_SYMBOL} w odpowiedzi.")
        return None

    parsed_data = []
    for k in klines:
        parsed_data.append({
            'Date': pd.to_datetime(k[0], unit='s'),
            'Open': float(k[1]),
            'High': float(k[2]),
            'Low': float(k[3]),
            'Close': float(k[4]),
            'Volume': float(k[6])
        })
        
    data = pd.DataFrame(parsed_data)
    data.set_index('Date', inplace=True)
    
    if len(data) < 100:
        print("Za mało danych historycznych do obliczenia EMA 100!")
        return None

    data['High_20'] = data['High'].shift(1).rolling(window=20).max()
    data['Low_20'] = data['Low'].shift(1).rolling(window=20).min()
    data['Low_10'] = data['Low'].shift(1).rolling(window=10).min()
    data['High_10'] = data['High'].shift(1).rolling(window=10).max()
    data['EMA_100'] = data['Close'].ewm(span=100, adjust=False).mean()

    high_low = data['High'] - data['Low']
    high_close = (data['High'] - data['Close'].shift()).abs()
    low_close = (data['Low'] - data['Close'].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    data['ATR_14'] = true_range.rolling(window=14).mean()

    return data.tail(365)

def analizuj_rynek():
    data = pobierz_i_oblicz_dane()
    if data is None:
        return

    ostatnia_swieca = data.iloc[-2]
    cena_dzis = float(data.iloc[-1]['Close'])
    dzisiejsza_data_str = str(data.index[-1].strftime('%Y-%m-%d'))
    
    atr = float(ostatnia_swieca['ATR_14'])
    krok_piramidy = 0.5 * atr
    
    stan = wczytaj_stan()
    wiadomosc = ""
    wymus_wysylke = False

    dzisiejszy_dzien_roku = datetime.now().strftime('%Y-%m-%d')
    if stan.get("ostatni_raport_dobowy") != dzisiejszy_dzien_roku:
        wymus_wysylke = True
        stan["ostatni_raport_dobowy"] = dzisiejszy_dzien_roku

    if stan["status"] == "BRAK_POZYCJI":
        if ostatnia_swieca['High'] > ostatnia_swieca['High_20'] and cena_dzis > ostatnia_swieca['EMA_100']:
            cena_wejscia = cena_dzis
            sl = cena_wejscia - (2 * atr)
            
            stan.update({
                "status": "W_POZYCJI",
                "kierunek": "LONG",
                "transze": [cena_wejscia],
                "stop_loss": sl,
                "data_wejscia": dzisiejsza_data_str
            })
            
            wiadomosc = (f"🟢 *OTWARCIE POZYCJI (LONG - Transza 1/3)*\n"
                         f"Para: `{TICKER}`\n"
                         f"Realna cena wejścia: `{cena_wejscia:,.2f} USD`\n"
                         f"Początkowy Stop Loss: `{sl:,.2f} USD`\n"
                         f"Kolejne dokupienie powyżej: `{cena_wejscia + krok_piramidy:,.2f} USD`")
            wymus_wysylke = True
            
        elif ostatnia_swieca['Low'] < ostatnia_swieca['Low_20'] and cena_dzis < ostatnia_swieca['EMA_100']:
            cena_wejscia = cena_dzis
            sl = cena_wejscia + (2 * atr)
            
            stan.update({
                "status": "W_POZYCJI",
                "kierunek": "SHORT",
                "transze": [cena_wejscia],
                "stop_loss": sl,
                "data_wejscia": dzisiejsza_data_str
            })
            
            wiadomosc = (f"🔴 *OTWARCIE POZYCJI (SHORT - Transza 1/3)*\n"
                         f"Para: `{TICKER}`\n"
                         f"Realna cena wejścia: `{cena_wejscia:,.2f} USD`\n"
                         f"Początkowy Stop Loss: `{sl:,.2f} USD`\n"
                         f"Kolejne dokupienie poniżej: `{cena_wejscia - krok_piramidy:,.2f} USD`")
            wymus_wysylke = True
            
        elif wymus_wysylke:
            wiadomosc = f"💤 *DOBOWY RAPORT ({TICKER}):* Brak pozycji. Czekam na wybicie z kanału 20-dniowego."

    else:
        liczba_transz = len(stan["transze"])
        if liczba_transz == 0:
            stan = DOMYSLNY_STAN()
            zapisz_stan(stan)
            return

        ostatnie_wejscie = stan["transze"][-1]
        pierwsze_wejscie = stan["transze"][0]
        
        if stan["kierunek"] == "LONG":
            low_10 = float(ostatnia_swieca['Low_10'])
            stop_loss_sztywny = stan["stop_loss"]
            trailing_stop = low_10 if low_10 > pierwsze_wejscie else stop_loss_sztywny
            ostateczna_obrona = max(stop_loss_sztywny, trailing_stop)
            
            if cena_dzis < ostateczna_obrona:
                cena_wyjscia = ostateczna_obrona
                wynik_procentowy = sum([(cena_wyjscia - w) / w for w in stan["transze"] if w > 0]) / liczba_transz * 100
                wiadomosc = (f"🚨 *ZAMKNIJ POZYCJĘ LONG dla {TICKER}*\n"
                             f"Cena wyjścia: `{cena_wyjscia:,.2f} USD`\n"
                             f"Średni wynik serii: `{wynik_procentowy:+.2f}%`")
                stan = DOMYSLNY_STAN()
                stan["ostatni_raport_dobowy"] = dzisiejszy_dzien_roku
                wymus_wysylke = True
                
            elif liczba_transz < 3 and cena_dzis >= (ostatnie_wejscie + krok_piramidy):
                nowe_wejscie = cena_dzis
                stan["transze"].append(nowe_wejscie)
                stan["stop_loss"] += krok_piramidy
                wiadomosc = (f"➕ *DOKUPIENIE DO POZYCJI (LONG - Transza {len(stan['transze'])}/3)*\n"
                             f"Cena dokupienia: `{nowe_wejscie:,.2f} USD`")
                wymus_wysylke = True
                
            elif wymus_wysylke:
                zysk_proc = sum([(cena_dzis - w) / w for w in stan["transze"] if w > 0]) / liczba_transz * 100
                wiadomosc = (f"📈 *DOBOWY RAPORT: LONG W TOKU ({TICKER})*\n"
                             f"Aktualny wynik serii: `{zysk_proc:+.2f}%`")

        elif stan["kierunek"] == "SHORT":
            high_10 = float(ostatnia_swieca['High_10'])
            stop_loss_sztywny = stan["stop_loss"]
            trailing_stop = high_10 if high_10 < pierwsze_wejscie else stop_loss_sztywny
            ostateczna_obrona = min(stop_loss_sztywny, trailing_stop)
            
            if cena_dzis > ostateczna_obrona:
                cena_wyjscia = ostateczna_obrona
                wynik_procentowy = sum([(w - cena_wyjscia) / w for w in stan["transze"] if w > 0]) / liczba_transz * 100
                wiadomosc = (f"🚨 *ZAMKNIJ POZYCJĘ SHORT dla {TICKER}*\n"
                             f"Cena wyjścia: `{cena_wyjscia:,.2f} USD`\n"
                             f"Średni wynik serii: `{wynik_procentowy:+.2f}%`")
                stan = DOMYSLNY_STAN()
                stan["ostatni_raport_dobowy"] = dzisiejszy_dzien_roku
                wymus_wysylke = True
                
            elif liczba_transz < 3 and cena_dzis <= (ostatnie_wejscie - krok_piramidy):
                nowe_wejscie = cena_dzis
                stan["transze"].append(nowe_wejscie)
                stan["stop_loss"] -= krok_piramidy
                wiadomosc = (f"➕ *DOKUPIENIE DO POZYCJI (SHORT - Transza {len(stan['transze'])}/3)*\n"
                             f"Cena dokupienia: `{nowe_wejscie:,.2f} USD`")
                wymus_wysylke = True
                
            elif wymus_wysylke:
                zysk_proc = sum([(w - cena_dzis) / w for w in stan["transze"] if w > 0]) / liczba_transz * 100
                wiadomosc = (f"📉 *DOBOWY RAPORT: SHORT W TOKU ({TICKER})*\n"
                             f"Aktualny wynik serii: `{zysk_proc:+.2f}%`")

    zapisz_stan(stan)
    ping_healthcheck()
    if wymus_wysylke and wiadomosc:
        wyslij_telegram(wiadomosc)

if __name__ == "__main__":
    analizuj_rynek()