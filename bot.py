import asyncio
import aiohttp
import logging

# ---------------------------
# Sinyal geçmişi (duplicate kontrol)
# ---------------------------
class SinyalGecmisi:
    def __init__(self):
        self.gonderilen = {}  # {event_id: [filtre_adlari]}
    
    def zaten_gonderildi_mi(self, event_id: str, filtre_adi: str) -> bool:
        return event_id in self.gonderilen and filtre_adi in self.gonderilen[event_id]
    
    def kaydet(self, event_id: str, filtre_adi: str):
        if event_id not in self.gonderilen:
            self.gonderilen[event_id] = []
        self.gonderilen[event_id].append(filtre_adi)

sinyal_gecmisi = SinyalGecmisi()

# ---------------------------
# BetsAPI fetch + backoff
# ---------------------------
MAX_BACKOFF = 300
BASE_BACKOFF = 60
backoff_counter = 0

async def fetch_inplay_with_odds(session, url):
    """
    /events/inplay + odds (MS, Alt/Üst, AH) verilerini çeker.
    429 geldiğinde exponential backoff uygular.
    """
    global backoff_counter
    try:
        async with session.get(url) as resp:
            if resp.status == 429:
                backoff_counter += 1
                wait_time = min(BASE_BACKOFF * backoff_counter, MAX_BACKOFF)
                logging.warning(f"BetsAPI 429 — rate limit, {wait_time}s bekleniyor")
                return []

            elif resp.status == 200:
                backoff_counter = 0
                data = await resp.json()
                # Her maç için canlı oran verilerini ekle
                results = []
                for mac in data.get("results", []):
                    mac['odds'] = mac.get("odds", {})  # MS, AH, Alt/Üst gibi
                    results.append(mac)
                return results
            else:
                logging.error(f"BetsAPI HTTP {resp.status}")
                return []
    except Exception as e:
        logging.error(f"BetsAPI request hatası: {e}")
        return []

# ---------------------------
# Ana motor loop
# ---------------------------
async def ana_dongu():
    url = "https://api.betsapi.com/events/inplay"
    async with aiohttp.ClientSession() as session:
        loop_sayaci = 0
        while True:
            loop_sayaci += 1
            aktif_maclar = await fetch_inplay_with_odds(session, url)
            if not aktif_maclar:
                logging.info(f"Loop #{loop_sayaci} | inplay matches: 0 (429 veya veri yok)")
                await asyncio.sleep(5)
                continue

            for mac in aktif_maclar:
                event_id = mac.get("event_id")
                dakika = mac.get("minute", 0)
                ah_degeri = mac.get("ah", 0.0)
                ev_gol = mac.get("home_goals", 0)
                dep_gol = mac.get("away_goals", 0)
                ev_corner = mac.get("home_corners", 0)
                dep_corner = mac.get("away_corners", 0)
                toplam_gol = ev_gol + dep_gol
                league = mac.get("league_name", "Unknown")
                ev_adi = mac.get("home_name", "EvTakim")
                dep_adi = mac.get("away_name", "DepTakim")
                odds = mac.get("odds", {})  # MS, AH, Alt/Üst

                # ---------------------------
                # Excel Top10 filtresi
                # ---------------------------
                sonuc_top10 = ExcelTop10Filtresi.kontrol_et(
                    dakika=dakika, ah_degeri=ah_degeri,
                    ev_gol=ev_gol, dep_gol=dep_gol, odds=odds
                )
                if sonuc_top10 and not sinyal_gecmisi.zaten_gonderildi_mi(event_id, sonuc_top10.filtre_adi):
                    mesaj = ExcelTop10Filtresi.mesaj_olustur(
                        ev_adi, dep_adi, f"{ev_gol}-{dep_gol}", dakika, league, sonuc_top10
                    )
                    sinyal_gecmisi.kaydet(event_id, sonuc_top10.filtre_adi)
                    print(mesaj)

                # ---------------------------
                # Claude filtreleri
                # ---------------------------
                sonuc_claude = claude_orijinal_filtresi.kontrol_et(
                    dakika=dakika, ah_degeri=ah_degeri,
                    kayit_ev_gol=ev_gol, kayit_dep_gol=dep_gol,
                    ev_corner=ev_corner, dep_corner=dep_corner,
                    toplam_gol=toplam_gol, odds=odds
                )
                if sonuc_claude and not sinyal_gecmisi.zaten_gonderildi_mi(event_id, sonuc_claude.filtre_adi):
                    mesaj = claude_orijinal_filtresi.mesaj_olustur(
                        ev_adi, dep_adi, f"{ev_gol}-{dep_gol}", dakika, league, sonuc_claude
                    )
                    sinyal_gecmisi.kaydet(event_id, sonuc_claude.filtre_adi)
                    print(mesaj)

                # ---------------------------
                # Gemini filtreleri
                # ---------------------------
                sonuc_gemini = gemini_filtresi.kontrol_et(
                    dakika=dakika, ah_degeri=ah_degeri,
                    ev_gol=ev_gol, dep_gol=dep_gol,
                    ev_corner=ev_corner, dep_corner=dep_corner,
                    toplam_gol=toplam_gol, odds=odds
                )
                if sonuc_gemini and not sinyal_gecmisi.zaten_gonderildi_mi(event_id, sonuc_gemini.filtre_adi):
                    mesaj = gemini_filtresi.mesaj_olustur(
                        ev_adi, dep_adi, f"{ev_gol}-{dep_gol}", dakika, league, sonuc_gemini
                    )
                    sinyal_gecmisi.kaydet(event_id, sonuc_gemini.filtre_adi)
                    print(mesaj)

            await asyncio.sleep(5)

# ---------------------------
# Bot başlatma
# ---------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ana_dongu())
