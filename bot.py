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
# Benim filtrelerim
# ---------------------------
class BenimStratejilerim:
    STRATEGIES = [
        {
            "filtre_adi": "Underdog Erken Deplasman Önde",
            "basari_orani": 97.6,
            "orneklem": 459,
            "ci_low": 95.76,
            "market_oneri": "Çifte Şans X2",
            "aciklama": "Ağır underdog/deplasman tarafı erken öne geçtiğinde X2 çok güçleniyor.",
            "guc_seviyesi": "✅ GÜÇLÜ",
            "risk": "Düşük",
            "blok": False
        },
        {
            "filtre_adi": "Ezici Favori 1X Banko",
            "basari_orani": 97.14,
            "orneklem": 699,
            "ci_low": 95.62,
            "market_oneri": "Çifte Şans 1X",
            "aciklama": "Çok güçlü favori erken bölümde kolay kolay maçı bırakmıyor.",
            "guc_seviyesi": "✅ GÜÇLÜ",
            "risk": "Düşük",
            "blok": False
        },
        {
            "filtre_adi": "Dengeli Maç — Yüksek Gol Beklentisi",
            "basari_orani": 76.0,
            "orneklem": 229,
            "ci_low": 70.0,
            "market_oneri": "MS 2.5 Üst / KG Var",
            "aciklama": "Dengeli maçlarda her iki takım gol arayışında → KG Var ihtimali güçlü",
            "guc_seviyesi": "⚠️ ORTA",
            "risk": "Orta",
            "blok": False
        }
        # Buraya diğer benim Excel Top10 filtrelerim eklenebilir
    ]

    @staticmethod
    def kontrol_et(mac_data):
        dakika = mac_data.get("minute", 0)
        ah = mac_data.get("ah", 0.0)
        ev_gol = mac_data.get("home_goals", 0)
        dep_gol = mac_data.get("away_goals", 0)
        odds = mac_data.get("odds", {})

        # Sadece basit örnek: AH veya skor kriteri eklenebilir
        results = []
        for s in BenimStratejilerim.STRATEGIES:
            # Örnek tetikleme kuralı: AH pozitif veya negatif durumu
            if "Underdog" in s["filtre_adi"] and ah >= 1.0:
                results.append(s)
            elif "Favori" in s["filtre_adi"] and ah <= -1.0:
                results.append(s)
            elif "Dengeli" in s["filtre_adi"] and abs(ah) < 0.25:
                results.append(s)
        return results

# ---------------------------
# Ana döngü
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
                ev_adi = mac.get("home_name", "EvTakim")
                dep_adi = mac.get("away_name", "DepTakim")
                dakika = mac.get("minute", 0)
                ev_gol = mac.get("home_goals", 0)
                dep_gol = mac.get("away_goals", 0)
                league = mac.get("league_name", "Unknown")
                toplam_gol = ev_gol + dep_gol

                filtreler = BenimStratejilerim.kontrol_et(mac)

                for f in filtreler:
                    if not sinyal_gecmisi.zaten_gonderildi_mi(event_id, f["filtre_adi"]):
                        mesaj = f"📊 STRATEJİ: {f['filtre_adi']}\n⚽ {ev_adi} {ev_gol}-{dep_gol} {dep_adi}\n🏆 {league}\n🎯 Market: {f['market_oneri']}\n📊 Başarı: %{f['basari_orani']}\n📦 Örneklem: n={f['orneklem']}\n⚠️ Risk: {f['risk']}\n📝 {f['aciklama']}"
                        sinyal_gecmisi.kaydet(event_id, f["filtre_adi"])
                        print(mesaj)

            await asyncio.sleep(5)

# ---------------------------
# Başlat
# ---------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ana_dongu())
