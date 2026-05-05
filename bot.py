# MAC ANALIZ BOTU - V15.0 FİNAL (NOKTA ATIŞI)
# Yenilik: BetsAPI'nin küçük harf 'id' güncellemesi koda tanıtıldı. Sistem tam aktif.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import urllib.parse

try:
    from google import genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
key_index = 0

def safe_int(val):
    try:
        if not val: return 0
        s_val = str(val).replace(',', '.').split('.')[0]
        return int(''.join(filter(str.isdigit, s_val)) or 0)
    except: return 0

async def get_ai_commentary(ev, dep, dk, skor, sot, da_ev, da_dep, lig):
    global key_index
    if not HAS_GENAI: return "⚠️ AI Yüklü Değil."
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        key_index += 1
        client = genai.Client(api_key=current_key)
        prompt = f"Analiz: {ev} {skor} {dep} | Dk: {dk}. Taktiksel yorum yap."
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except: return "AI analiz edemedi."

async def analiz_et(mac_detay):
    ev_adi = "Ev"; dep_adi = "Dep"; dk = 0; skor = "0-0"; ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0; lig = "Lig"

    veri_listesi = []
    if isinstance(mac_detay, list):
        for x in mac_detay:
            if isinstance(x, dict): veri_listesi.append(x)
            elif isinstance(x, list):
                for y in x:
                    if isinstance(y, dict): veri_listesi.append(y)
    elif isinstance(mac_detay, dict):
        veri_listesi.append(mac_detay)

    for item in veri_listesi:
        t = item.get('type')
        if t == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0] if len(names) > 0 else "Ev"
            dep_adi = names[1] if len(names) > 1 else "Dep"
            dk = safe_int(item.get('TM', 0))
            skor = item.get('SS', '0-0')
            lig = item.get('CT', 'Lig')
        elif t == 'TE':
            if item.get('ID') == '1':
                ev_sot = safe_int(item.get('S1', 0)); ev_da = safe_int(item.get('S4', 0))
            elif item.get('ID') == '2':
                dep_sot = safe_int(item.get('S1', 0)); dep_da = safe_int(item.get('S4', 0))

    if not (20 <= dk <= 85): return None

    ev_gol = safe_int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = safe_int(skor.split('-')[1]) if '-' in skor else 0
    
    puan = 4.0 
    onayli_skorlar = [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (0,0)]
    if (ev_gol, dep_gol) in onayli_skorlar: puan += 3.0
    if abs(ev_gol - dep_gol) >= 3: return None

    if puan >= 4.0:
        ai_yorum = await get_ai_commentary(ev_adi, dep_adi, dk, skor, ev_sot+dep_sot, ev_da, dep_da, lig)
        nesine_link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
        return (f"💎 SİNYAL (Puan: {puan})\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ Dakika: {dk}\n"
                f"--------------------\n🤖 AI: {ai_yorum}\n\n📊 DA: {ev_da}-{dep_da} | SOT: {ev_sot+dep_sot}\n"
                f"🔗 [Nesine'de Ara]({nesine_link})")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🚀 V15.0 FİNAL AKTİF: ID Hatası çözüldü, sistem filtrelerinize uygun maçları tarıyor.")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    data = await r.json()
                    res = data.get('results', [])
                
                if res and isinstance(res, list) and len(res) > 0 and isinstance(res[0], list):
                    res = res[0]

                for m in res:
                    if not isinstance(m, dict): continue
                    
                    # 💡 İŞTE BÜTÜN SORUNU ÇÖZEN O SATIR: Artık küçük harfli 'id' yi de tanıyor!
                    m_id = m.get('id') or m.get('FI') or m.get('ID') 
                    
                    if m_id is None or str(m_id).lower() == "none":
                        continue
                    
                    m_id_str = str(m_id)
                    if m_id_str in bildirim_gonderilen: continue
                    
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id_str}&stats=1") as er:
                        e_data = await er.json()
                        if e_data.get('success') == 1 and e_data.get('results'):
                            msg = await analiz_et(e_data['results'])
                            if msg:
                                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                                bildirim_gonderilen[m_id_str] = True
            except Exception as e: 
                logger.error(f"Döngü Hatası: {e}")
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
