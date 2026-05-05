# MAC ANALIZ BOTU - V20.0 MANTIKSAL MUHAFIZ
# Yenilik: Verileri sadece çekmez, mantıksal büyüklüklerine göre etiketleri kendisi atar.

import asyncio
import aiohttp
from telegram import Bot
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# Dinamik Harita - Bot bunu her açılışta güncelleyecek
CURRENT_MAP = {}

def esnek_liste_duzelt(veri):
    duz_liste = []
    if isinstance(veri, list):
        for eleman in veri: duz_liste.extend(esnek_liste_duzelt(eleman))
    elif isinstance(veri, dict): duz_liste.append(veri)
    return duz_liste

async def mantiksal_dogrulama(session, bot):
    """Verilerin büyüklük sıralamasına göre etiketleri otomatik atar."""
    global CURRENT_MAP
    try:
        async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
            data = await r.json()
            res = esnek_liste_duzelt(data.get('results', []))
        
        if not res: return False

        m_id = res[0].get('id') or res[0].get('FI')
        async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
            e_data = await er.json()
            stats = esnek_liste_duzelt(e_data.get('results', []))
            
            for item in stats:
                if item.get('type') == 'TE' and item.get('ID') == '1': # Ev sahibi verileri üzerinden analiz
                    
                    # Tüm S değerlerini sayıya çevirip bir sözlüğe alalım
                    raw_values = {k: int(str(v) or 0) for k, v in item.items() if k.startswith('S') and str(v).isdigit()}
                    
                    # MANTIKSAL EŞLEŞTİRME
                    # 1. En büyük değer her zaman Toplam Ataktır (Genelde S8 veya S3)
                    sorted_keys = sorted(raw_values, key=raw_values.get, reverse=True)
                    
                    # Örnek sıralama: S3(98), S4(30), S1(4), S2(7)
                    CURRENT_MAP["TOTAL_ATTACK"] = sorted_keys[0] # En büyük
                    CURRENT_MAP["DANGEROUS_ATTACK"] = sorted_keys[1] # İkinci büyük
                    
                    # Topla oynama % tespiti (Toplamı 100'e yakın olanı bulalım)
                    for k, v in raw_values.items():
                        if 30 <= v <= 70: # Topla oynama genelde bu aralıktadır
                            CURRENT_MAP["POSSESSION"] = k
                            break

                    report = (f"🛡️ **MANTIKSAL DENETİM RAPORU**\n\n"
                              f"Bot verileri analiz etti ve şu eşleşmeleri yaptı:\n"
                              f"- ⚔️ **Toplam Atak:** {CURRENT_MAP.get('TOTAL_ATTACK')} (Değer: {raw_values.get(sorted_keys[0])})\n"
                              f"- 🔥 **Tehlikeli Atak:** {CURRENT_MAP.get('DANGEROUS_ATTACK')} (Değer: {raw_values.get(sorted_keys[1])})\n"
                              f"- 📈 **Topla Oynama:** {CURRENT_MAP.get('POSSESSION')}\n\n"
                              f"✅ Etiket kaymaları otomatik olarak bypass edildi.")
                    
                    await bot.send_message(chat_id=CHAT_ID, text=report, parse_mode="Markdown")
                    return True
        return False
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Mantıksal Denetim Hatası: {e}")
        return False

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    async with aiohttp.ClientSession() as session:
        # BOT BAŞLARKEN AKLINI KULLANARAK KONTROL EDİYOR
        if await mantiksal_dogrulama(session, bot):
            while True:
                # Analiz kısmında artık sabit S3 değil, CURRENT_MAP["TOTAL_ATTACK"] kullanılacak
                await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
