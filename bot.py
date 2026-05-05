# MAC ANALIZ BOTU - V23.0 ANALİTİK MOTOR
# Yenilik: İstatistiksel puanlama, 60 sn tarama ve detaylı AI hata raporu.

import asyncio
import aiohttp
from telegram import Bot
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

CURRENT_MAP = {"TOTAL_ATTACK": "S3", "DANGEROUS_ATTACK": "S4", "SOT": "S1", "CORNER": "S2", "POSSESSION": "S7"}
bildirim_gonderilen = {}
key_index = 0

def esnek_liste_duzelt(veri):
    duz_liste = []
    if isinstance(veri, list):
        for eleman in veri: duz_liste.extend(esnek_liste_duzelt(eleman))
    elif isinstance(veri, dict): duz_liste.append(veri)
    return duz_liste

async def get_ai_commentary(ev, dep, dk, skor, sot, da_ev, da_dep, lig):
    global key_index
    if not HAS_GENAI: return "⚠️ Google-GenAI kütüphanesi yüklü değil (requirements.txt kontrol)."
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        if not current_key: return "⚠️ Railway Variables: GEMINI_KEY_1 boş!"
        key_index += 1
        client = genai.Client(api_key=current_key)
        prompt = (f"Maç: {ev} {skor} {dep} | Dk: {dk}. DA: {da_ev}-{da_dep}. SOT: {sot}. "
                  f"Bu istatistiklere göre maçın gidişatını 1 cümleyle yorumla.")
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except Exception as e: 
        return f"⚠️ AI Bağlantı Hatası: {str(e)[:100]}"

async def mantiksal_dogrulama(session):
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
                if item.get('type') == 'TE' and str(item.get('ID')) == '1':
                    raw_v = {k: int(str(v) or 0) for k, v in item.items() if k.startswith('S') and str(v).isdigit()}
                    sk = sorted(raw_v, key=raw_v.get, reverse=True)
                    if len(sk) >= 2:
                        CURRENT_MAP["TOTAL_ATTACK"] = sk[0]
                        CURRENT_MAP["DANGEROUS_ATTACK"] = sk[1]
                        return True
        return False
    except: return False

async def analiz_ve_puanla(mac_detay):
    stats = esnek_liste_duzelt(mac_detay)
    ev_adi = "Ev"; dep_adi = "Dep"; dk = 0; skor = "0-0"; lig = "Lig"
    ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0; ev_ta = 0; dep_ta = 0

    for item in stats:
        if item.get('type') == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0]; dep_adi = names[1]
            dk = int(str(item.get('TM', 0)) or 0)
            skor = item.get('SS', '0-0'); lig = item.get('CT', 'Lig')
        elif item.get('type') == 'TE':
            v_sot = int(str(item.get(CURRENT_MAP["SOT"], 0)) or 0)
            v_da = int(str(item.get(CURRENT_MAP["DANGEROUS_ATTACK"], 0)) or 0)
            v_ta = int(str(item.get(CURRENT_MAP["TOTAL_ATTACK"], 0)) or 0)
            if str(item.get('ID')) == '1': ev_sot = v_sot; ev_da = v_da; ev_ta = v_ta
            else: dep_sot = v_sot; dep_da = v_da; dep_ta = v_ta

    if not (20 <= dk <= 85): return None
    
    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
    if abs(ev_gol - dep_gol) >= 3: return None

    # 🚀 GELİŞMİŞ PUANLAMA
    puan = 4.0
    # Skor Bonusu
    if (ev_gol, dep_gol) in [(0,0), (1,1), (2,2), (1,0), (0,1), (2,1), (1,2)]:
        puan += 3.0
    
    # İstatistik Bonusu (Dinamiğe uygun)
    toplam_da = ev_da + dep_da
    toplam_sot = ev_sot + dep_sot
    
    puan += (toplam_da // 10) * 0.5  # Her 10 DA = +0.5 Puan
    puan += (toplam_sot // 2) * 0.5  # Her 2 SOT = +0.5 Puan

    if puan >= 4.0:
        ai = await get_ai_commentary(ev_adi, dep_adi, dk, skor, toplam_sot, ev_da, dep_da, lig)
        link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
        return (f"💎 **SİNYAL (Puan: {puan})**\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ Dakika: {dk}\n"
                f"--------------------\n🤖 AI: {ai}\n"
                f"📊 DA: {ev_da}-{dep_da} | SOT: {toplam_sot} | TA: {ev_ta}-{dep_ta}\n"
                f"🔗 [Nesine]({link})")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    async with aiohttp.ClientSession() as session:
        await mantiksal_dogrulama(session)
        await bot.send_message(chat_id=CHAT_ID, text="🚀 **V23.0 AKTİF:** Puanlama motoru ve AI hata raporlama devrede.")
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    res = esnek_liste_duzelt((await r.json()).get('results', []))
                
                for m in res:
                    m_id = str(m.get('id') or m.get('FI', ''))
                    if not m_id or m_id in bildirim_gonderilen: continue
                    
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        msg = await analiz_ve_puanla((await er.json()).get('results', []))
                        if msg:
                            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                            bildirim_gonderilen[m_id] = True
            except: pass
            await asyncio.sleep(60) # 60 saniye tarama

if __name__ == "__main__":
    asyncio.run(ana_dongu())
