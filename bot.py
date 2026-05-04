# MAC ANALIZ BOTU - V12.9-DİYAGNOZ
# Yenilik: Her 10 dk'da bir durum raporu ve zırhlı ID tespiti.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import urllib.parse
from google import genai

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
mac_atak_gecmisi = {}
key_index = 0

def safe_int(val):
    try:
        if not val: return 0
        s_val = str(val).replace(',', '.').split('.')[0]
        return int(''.join(filter(str.isdigit, s_val)) or 0)
    except: return 0

async def get_ai_commentary(ev, dep, dk, skor, sot, da_ev, da_dep, lig):
    global key_index
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        key_index += 1
        if not current_key: return "⚠️ AI Key Eksik."
        client = genai.Client(api_key=current_key)
        prompt = (f"Futbol Analisti: {ev} {skor} {dep} | Dakika: {dk} | Lig: {lig}\n"
                  f"İst: SOT: {sot}, DA: {da_ev}-{da_dep}\n"
                  f"İstatistik tekrarı yapmadan 2 kısa cümlelik taktiksel risk analizi yap.")
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except: return "AI şu an analiz yapamıyor."

async def analiz_et(mac_detay, m_id):
    ev_adi = "Bilinmeyen"; dep_adi = "Bilinmeyen"; dk = 0; skor = "0-0"; ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0; lig = "Lig"

    for item in mac_detay:
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

    toplam_da = ev_da + dep_da
    toplam_sot = ev_sot + dep_sot
    ev_gol = safe_int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = safe_int(skor.split('-')[1]) if '-' in skor else 0

    puan = 0.0
    if 20 <= dk <= 85: puan += 4.0
    else: return None

    onayli_skorlar = [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (0,0)]
    if (ev_gol, dep_gol) in onayli_skorlar: puan += 3.0
    
    if abs(ev_gol - dep_gol) >= 3 or (ev_gol + dep_gol) >= 6: return None

    onceki_atak = mac_atak_gecmisi.get(m_id, toplam_da)
    delta_atak = toplam_da - onceki_atak
    mac_atak_gecmisi[m_id] = toplam_da
    if delta_atak >= 5: puan += 2.0

    if puan >= 4.0:
        ai_yorum = await get_ai_commentary(ev_adi, dep_adi, dk, skor, toplam_sot, ev_da, dep_da, lig)
        nesine_link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
        return {
            "mesaj": (f"💎 SİNYAL (Puan: {puan})\n⚽ {ev_adi} {skor} {dep_adi}\n"
                      f"⏱ Dakika: {dk}\n--------------------\n"
                      f"🤖 AI: _{ai_yorum}_\n\n📊 DA: {ev_da}-{dep_da} | SOT: {toplam_sot}\n"
                      f"🔗 [Nesine Link]({nesine_link})")
        }
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🛰️ V12.9 DİYAGNOZ MODU AKTİF")
    async with aiohttp.ClientSession() as session:
        sayac = 0
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    list_data = await r.json()
                    results = list_data.get('results', [])
                    if results and isinstance(results[0], list): results = results[0]
                
                # Her 5 döngüde bir (yaklaşık 10 dk) durum raporu at
                sayac += 1
                if sayac % 5 == 0:
                    await bot.send_message(chat_id=CHAT_ID, text=f"📊 SİSTEM RAPORU: Şu an {len(results)} maç taranıyor. Aktif filtreler devrede.")

                for m in results:
                    # Zırhlı ID Tespiti
                    if isinstance(m, dict):
                        m_id = str(m.get('FI') or m.get('ID') or m.get('id', ''))
                    else:
                        m_id = str(m)
                    
                    if not m_id or m_id in bildirim_gonderilen: continue
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        e_data = await er.json()
                        if e_data.get('success') == 1:
                            sonuc = await analiz_et(e_data['results'][0], m_id)
                            if sonuc:
                                await bot.send_message(chat_id=CHAT_ID, text=sonuc['mesaj'], parse_mode="Markdown")
                                bildirim_gonderilen[m_id] = True
            except Exception as e: logger.error(f"Hata: {e}")
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
