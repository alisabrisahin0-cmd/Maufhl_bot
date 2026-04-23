import asyncio
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
import logging
import os
import asyncpg
from datetime import datetime, timedelta

# =============================================
# AYARLAR (GitHub/Render Secret Files kısmından alınır)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
# Dikkat: Değişken adını panelde X_AUTH_TOKEN olarak ayarladık
API_KEY = os.getenv("X_AUTH_TOKEN", "") 
DATABASE_URL = os.getenv("DATABASE_URL", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "7"))
KONTROL_SURESI = 120 # 2 dakikada bir kontrol
# =============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None

async def db_baglant():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS sinyaller (
                id SERIAL PRIMARY KEY,
                mac_id TEXT,
                ev TEXT,
                dep TEXT,
                lig TEXT,
                dakika INTEGER,
                ev_gol INTEGER,
                dep_gol INTEGER,
                puan INTEGER,
                tahmin TEXT,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        logger.info("✅ Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB hatası: {e}")

async def sinyal_kaydet(mac, puan, tahmin):
    try:
        await db_pool.execute("""
            INSERT INTO sinyaller 
            (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, tahmin)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
            mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, tahmin)
    except Exception as e:
        logger.error(f"Kayıt hatası: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        await db_pool.execute("""
            UPDATE sinyaller 
            SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
            WHERE mac_id=$4 AND sonuc='BEKLIYOR'
        """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Güncelleme hatası: {e}")

async def macları_cek():
    # Football-Data.org Yeni URL
    url = "https://api.football-data.org/v4/matches"
    headers = { "X-Auth-Token": API_KEY }
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    matches = data.get('matches', [])
                    logger.info(f"{len(matches)} maç taranıyor...")
                    
                    for event in matches:
                        # Sadece canlı maçları filtrele
                        if event.get('status') != 'IN_PLAY':
                            continue
                            
                        try:
                            ev = event.get('homeTeam', {}).get('name', '?')
                            dep = event.get('awayTeam', {}).get('name', '?')
                            lig = event.get('competition', {}).get('name', '?')
                            mac_id = str(event.get('id', ''))
                            dakika = 45 # Ücretsiz planda dakika kısıtlı olabilir
                            
                            score = event.get('score', {}).get('fullTime', {})
                            ev_gol = int(score.get('home', 0) or 0)
                            dep_gol = int(score.get('away', 0) or 0)
                            
                            maclar.append({
                                'id': mac_id, 'ev': ev, 'dep': dep, 'lig': lig,
                                'dakika': dakika, 'ev_gol': ev_gol, 'dep_gol': dep_gol,
                                'ev_corner': 0, 'dep_corner': 0, 'son_gol': 0
                            })
                        except:
                            continue
                elif resp.status == 429:
                    logger.warning("⚠️ Hız sınırı uyarısı (Rate Limit).")
    except Exception as e:
        logger.error(f"API hatası: {e}")
    return maclar

def sinyal_hesapla(mac):
    puan = 0
    aktif = []
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    
    if ev_gol > 0 and dep_gol > 0:
        puan += 2
        aktif.append("✅ KG VAR")
    if abs(ev_gol - dep_gol) >= 2:
        puan += 2
        aktif.append("🔥 Büyük Fark")
    if (ev_gol + dep_gol) >= 3:
        puan += 2
        aktif.append("⚽ 3+ Gol")
    
    return puan, aktif

def tavsiye_uret(mac):
    if mac['ev_gol'] > mac['dep_gol']:
        return "EV GOL ATACAK (S)"
    return "GOL OLACAK (S)"

async def bildirim_gonder(bot, mac, puan, sinyaller, tahmin):
    karar = "🔥 KESİN GİR" if puan >= 5 else "✅ GİREBİLİRSİN"
    bar = "█" * puan + "░" * (10 - puan)
    mesaj = f"""🚀 *{mac['ev']} {mac['ev_gol']}–{mac['dep_gol']} {mac['dep']}*
🏆 {mac['lig']}

📊 *Sinyal Gücü: {puan}/10*
`{bar}`

*Aktif Analiz:*
{chr(10).join(sinyaller)}

━━━━━━━━━━━━
{karar}
💡 *{tahmin}*
━━━━━━━━━━━━"""
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode=ParseMode.MARKDOWN)
        await sinyal_kaydet(mac, puan, tahmin)
    except Exception as e:
        logger.error(f"Mesaj hatası: {e}")

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="🤖 *BOT BAŞLATILDI*\n\n✅ Football-Data.org Aktif\n📡 2 Dakikada Bir Kontrol\n📈 Sonuç Takibi Devrede",
            parse_mode=ParseMode.MARKDOWN
        )
    except: pass

    while True:
        try:
            maclar = await macları_cek()
            for mac in maclar:
                puan, sinyaller = sinyal_hesapla(mac)
                mac_id = mac['id']
                
                if puan >= MIN_PUAN and mac_id not in bildirim_gonderilen:
                    tahmin = tavsiye_uret(mac)
                    await bildirim_gonder(bot, mac, puan, sinyaller, tahmin)
                    bildirim_gonderilen[mac_id] = {'puan': puan}
                    
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
        
        await asyncio.sleep(KONTROL_SURESI)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
