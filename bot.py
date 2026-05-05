# MAC ANALIZ BOTU - V21.0 FİNAL KONTROLLÜ SİSTEM
# Özellik: Dinamik etiket eşleme (S3, S4 vb.) + Gemini 2.0 Analiz + Puanlama

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
# Gemini Key'lerinizi Railway Değişkenlerinde kontrol edin
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]

CURRENT_MAP = {}
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
    if not HAS_GENAI: return "⚠️ google-genai kütüphanesi eksik."
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        if not current_key: return "⚠️ API Key bulunamadı."
        key_index += 1
        client = genai.Client(api_key=current_key)
        prompt = (f"Maç: {ev} {skor} {dep} | Dakika: {dk}. Lig: {lig}. "
                  f"Hücum Verileri: Tehlikeli Atak {da_ev}-{da_dep}, İsabetli Şut: {sot}. "
                  f"Bu verilere göre maçın gidişatını 1 cümleyle taktiksel yorumla.")
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except Exception as e: return f"Analiz yapılamadı: {str(e)[:50]}"

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
                    sorted_keys = sorted(raw_v, key=raw_v.get, reverse=True)
                    CURRENT_MAP = {
                        "TOTAL_ATTACK": sorted_keys[0] if len(sorted_keys) > 0 else "S8",
                        "DANGEROUS_ATTACK": sorted_keys[1] if len(sorted_keys) > 1 else "S4",
                        "SOT": "S1", "CORNER": "S2", "POSSESSION": "S7"
                    }
                    return True
        return False
    except: return False

async def analiz_ve_puanla(mac_detay):
    # Dinamik haritadan verileri çekiyoruz
    ev_adi = "Ev"; dep_adi = "Dep"; dk = 0; skor = "0-0"; lig = "Lig"
    ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0; ev_ta = 0; dep_ta = 0

    stats = esnek_liste_duzelt(mac_detay)
    for item in stats:
        if item.get('type') == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0] if len(names) > 0 else "Ev"
            dep_adi = names[1] if len(names) > 1 else "Dep"
            dk = int(str(item.get('TM', 0)) or 0)
            skor = item.get('SS', '0-0')
            lig = item.get('CT', 'Lig')
        elif item.get('type') == 'TE':
            val_sot = int(str(item.get(CURRENT_MAP.get("SOT", "S1"), 0)) or 0)
            val_da = int(str(item.get(CURRENT_MAP.get("DANGEROUS_ATTACK", "S4"), 0)) or 0)
            val_ta = int(str(item.get(CURRENT_MAP.get("TOTAL_ATTACK", "S8"), 0)) or 0)
            if str(item.get('ID')) == '1':
                ev_sot = val_sot; ev_da = val_da; ev_ta = val_ta
            else:
                dep_sot = val_sot; dep_da = val_da; dep_ta = val_ta

    # 🎯 PUANLAMA MOTORU (FİLTRELER)
    if not (20 <= dk <= 85): return None
    
    # 1. Puanlama Kuralı: Gol farkı 3 ve üzeriyse ilgilenme
    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
    if abs(ev_gol - dep_gol) >= 3: return None

    puan = 4.0
    # Skor Bonusu
    onayli_skorlar = [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (0,0)]
    if (ev_gol, dep_gol) in onayli_skorlar: puan += 3.0
    
    # İstatistik Bonusu (Eski sisteminize göre ayarlanabilir)
    if (ev_da + dep_da) > 60: puan += 1.0 # Maç çok hareketliyse +1

    if puan >= 7.0:
        ai_yorum = await get_ai_commentary(ev_adi, dep_adi, dk, skor, ev_sot+dep_sot, ev_da, dep_da, lig)
        nesine_link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
        return (f"💎 **SİNYAL (Puan: {puan})**\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ Dakika: {dk}\n"
                f"--------------------\n🤖 AI: {ai_yorum}\n\n"
                f"📊 DA: {ev_da}-{dep_da} | SOT: {ev_sot+dep_sot} | TA: {ev_ta}-{dep_ta}\n"
                f"🔗 [Nesine'de Ara]({nesine_link})")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    async with aiohttp.ClientSession() as session:
        if await mantiksal_dogrulama(session):
            await bot.send_message(chat_id=CHAT_ID, text="✅ **Sistem Doğrulandı:** Veri haritası güncellendi ve analiz motoru başlatıldı.")
            while True:
                try:
                    async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                        data = await r.json()
                        res = esnek_liste_duzelt(data.get('results', []))
                    
                    for m in res:
                        m_id = m.get('id') or m.get('FI')
                        if not m_id or str(m_id) in bildirim_gonderilen: continue
                        
                        async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                            e_data = await er.json()
                            msg = await analiz_ve_puanla(e_data.get('results', []))
                            if msg:
                                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                                bildirim_gonderilen[str(m_id)] = True
                except: pass
                await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
