async def akilli_denetim_ve_puanla(ev_v, dep_v, dk, skor):
    # 1. Ham verileri sözlükten al
    ta = int(ev_v.get(MAP["TA"], 0)) + int(dep_v.get(MAP["DA"], 0))
    da = int(ev_v.get(MAP["DA"], 0)) + int(dep_v.get(MAP["DA"], 0))
    sot = int(ev_v.get(MAP["SOT"], 0)) + int(dep_v.get(MAP["SOT"], 0))

    # 2. FİZİKSEL SINIR KONTROLLERİ (Hata Önleyici)
    # Kural A: 45 dakikada 47 şut olamaz. (Dk x 0.8 sınırı)
    if sot > (dk * 0.8):
        sot = 0 # Veri hatalı, puanı şişirmesini engelle.

    # Kural B: DA her zaman TA'dan küçük veya eşit olmalıdır.
    if da > ta:
        ta = da + 10 # Etiket kaymasını mantıksal olarak düzelt.

    # 3. PUANLAMA MOTORU (Zırhlı)
    puan = 4.0
    if skor in ["0-0", "1-1", "2-2", "1-0", "0-1"]: puan += 3.0
    
    # Bonusları gerçekçi sınırlara hapset (Max +3 puan DA'dan, +2 puan SOT'tan)
    da_bonusu = min((da // 10) * 0.5, 3.0)
    sot_bonusu = min((sot // 2) * 0.5, 2.0)
    puan += (da_bonusu + sot_bonusu)

    return round(puan, 1), da, sot, ta
