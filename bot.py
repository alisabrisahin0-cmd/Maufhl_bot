# MAC ANALIZ BOTU - V22.0 ŞEFFAF TAKİP
# Özellik: Her 5 dakikada bir 'Hayattayım' raporu atar ve elenen maçların nedenini söyler.

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

# Varsayılan harita (Denetim başarısız olursa diye)
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
    if not HAS_GENAI: return "⚠️ AI Yüklü Değil."
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        key_index += 1
        client = genai.Client(api_key=current_key)
        prompt = f"Maç: {ev} {skor} {dep} | Dk: {dk}. Taktiksel çok kısa yorum yap."
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except: return "Analiz yapılamadı."

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
                    if len(sorted_keys) >= 2:
                        CURRENT_MAP["TOTAL_ATTACK"] = sorted_keys[0]
                        CURRENT_MAP["DANGEROUS_ATTACK"] = sorted_keys[1]
                        return True
        return False
    except: return False

async def analiz_ve_puanla(mac_detay):
    stats = esnek_liste_duzelt(mac_detay)
    ev_adi = "Ev"; dep_adi = "Dep"; dk = 0; skor = "0-0"; lig = "Lig"
    ev_sot = 0; dep_sot = 0; ev_da = 0; dep_da = 0

    for item in stats:
        if item.get('type') == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0] if len(names) > 0 else "Ev"
            dep_adi = names[1] if len(names) > 1 else "Dep"
            dk = int(str(item.get('TM', 0)) or 0)
            skor = item.get('SS', '0-0')
            lig = item.get('CT', 'Lig')
        elif item.get('type') == 'TE':
            v_sot = int(str(item.get(CURRENT_MAP["SOT"], 0)) or 0)
            v_da = int(str(item.get(CURRENT_MAP["DANGEROUS_ATTACK"], 0)) or 0)
            if str(item.get('ID')) == '1': ev_sot = v_sot; ev_da = v_da
            else: dep_sot = v_sot; dep_da = v_da

    # Filtre Kontrolü (Log için)
    if not (15 <= dk <= 88): return "DK_DISI"
    
    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
    if abs(ev_gol - dep_gol) >= 3: return "GOL_FARKI"

    # Puanlama
    puan = 4.0
    # Onaylı skorlar bonusu (+3)
    if (ev_gol, dep_gol) in [(0,0), (1,1), (2,2), (1,0), (0,1), (2,1), (1,2)]:
        puan += 3.0

    if puan >= 4.0:
        ai = await get_ai_commentary(ev_adi, dep_adi, dk, skor, ev_sot+dep_sot, ev_da, dep_da, lig)
        link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
        return (f"💎 **SİNYAL (Puan: {puan})**\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ Dakika: {dk}\n"
                f"--------------------\n🤖 AI: {ai}\n📊 DA: {ev_da}-{dep_da} | SOT: {ev_sot+dep_sot}\n🔗 [Nesine]({link})")
    return "PUAN_YETERSIZ"

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🛰 **V22.0 ŞEFFAF MOD:** Tarama başlıyor. Her 5 dk'da bir durum raporu verilecek.")
    
    async with aiohttp.ClientSession() as session:
        # Denetim yapılamasa bile varsayılanla başla (Kilitlenmeyi önlemek için)
        await mantiksal_dogrulama(session)
        
        sayac = 0
        while True:
            try:
                sayac += 1
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    data = await r.json()
                    res = esnek_liste_duzelt(data.get('results', []))
                
                toplam = len(res)
                elenen_dk = 0; elenen_gol = 0; gonderilen = 0

                for m in res:
                    m_id = m.get('id') or m.get('FI')
                    if not m_id or str(m_id) in bildirim_gonderilen: continue
                    
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        e_data = await er.json()
                        sonuc = await analiz_ve_puanla(e_data.get('results', []))
                        
                        if isinstance(sonuc, str) and "SİNYAL" in sonuc:
                            await bot.send_message(chat_id=CHAT_ID, text=sonuc, parse_mode="Markdown")
                            bildirim_gonderilen[str(m_id)] = True
                            gonderilen += 1
                        elif sonuc == "DK_DISI": elenen_dk += 1
                        elif sonuc == "GOL_FARKI": elenen_gol += 1

                # Her 5 döngüde bir (yaklaşık 5-10 dk) durum raporu at
                if sayac % 5 == 0:
                    await bot.send_message(chat_id=CHAT_ID, text=f"📊 **DURUM RAPORU:**\n- Taranan Maç: {toplam}\n- Dakika Dışı: {elenen_dk}\n- Gol Farkı Fazla: {elenen_gol}\n- Yeni Sinyal: {gonderilen}\n✅ Sistem Sorunsuz Çalışıyor.")

            except Exception as e:
                await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Hata: {str(e)[:100]}")
            
            await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
