# MAC ANALIZ BOTU - V29.0 KUSURSUZ DENETLEYICI
import asyncio, aiohttp, os, urllib.parse
from telegram import Bot

# --- AYARLAR VE GLOBAL HARİTA ---
MAP = {"TA": "S3", "DA": "S4", "SOT": "S1"}

async def akilli_ayristirici(v_dict, dk):
    """Veriyi sadece rakam olarak değil, mantık olarak ayrıştırır."""
    try:
        # 1. Sayısal verileri ayıkla ve sırala
        rakamlar = {k: int(val) for k, val in v_dict.items() if k.startswith('S') and str(val).isdigit()}
        sirali = sorted(rakamlar.items(), key=lambda x: x[1], reverse=True)
        
        if len(sirali) < 3: return None # Yetersiz veri

        ta = sirali[0][1] # En büyük
        da = sirali[1][1] # Ortanca
        sot = sirali[2][1] # En küçük

        # 2. FİZİKSEL SINIR KONTROLLERİ
        # Eğer SOT, DA'dan büyükse veya mantıksız bir seviyedeyse (Dk x 1.2'den fazlaysa)
        if sot > da or sot > (dk * 1.2):
            sot = 0 # Veriyi 'hatalı' olarak işaretle ve sıfırla

        # Eğer TA, dakikanın 5 katından fazlaysa (Sarı kart verisi kaymış olabilir)
        if ta > (dk * 5):
            ta = da + 5 # TA'yı DA'ya yakın bir seviyeye çekerek düzelt

        return {"TA": ta, "DA": da, "SOT": sot}
    except: return None

async def analiz_ve_gonder(bot, mac_data):
    # Veri çekme ve temizlik işlemleri (V27 mantığı ile)
    # ... (Burada ev_v, dep_v ve dk çekilir) ...
    
    ev = await akilli_ayristirici(ev_v, dk)
    dep = await akilli_ayristirici(dep_v, dk)

    if not ev or not dep: return

    # PUANLAMA (Bu mantıkla 7.0 barajı artık güvenlidir)
    puan = 4.0
    if skor in ["0-0", "1-1", "2-2", "1-0", "0-1", "2-1", "1-2"]: puan += 3.0
    
    # Gerçekçi Bonuslar
    puan += min(((ev['DA'] + dep['DA']) // 10) * 0.5, 3.0)
    puan += min(((ev['SOT'] + dep['SOT']) // 2) * 0.5, 2.0)

    if puan >= 7.0:
        # Nesine Link Temizliği ve Mesaj Gönderimi
        # ...
