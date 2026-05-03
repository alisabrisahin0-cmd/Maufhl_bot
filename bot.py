def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    ah_deger = mac.get('ah_deger', 0.0)
    
    puan = 0.0
    detay = []
    stratejiler = []
    
    # 1. ÜSTEL SOĞUMA (Exponential Decay) KONTROLÜ
    decay_carpan, decay_mesaj = ustel_zaman_asimi(dakika, son_gol)
    if decay_carpan == 0.0: return 0, [decay_mesaj], "BLOCKED", False
    
    # 2. SKOR KORUMA (Death Zone) KONTROLÜ
    dz_aktif, dz_mesaj = death_zone_kontrol(ah_deger, ev_gol, dep_gol)
    if dz_aktif: return 0, [dz_mesaj], "DEATH_ZONE", False

    # 3. KAYAN PENCERE (ROLLING WINDOW) HESAPLAMASI VE İLK TARAMA KORUMASI
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # HARD-LOCK KAPI KONTROLÜ (İvme yoksa acımadan kes)
    if not ilk_tarama and delta_atak < 8 and delta_sut < 1 and dakika > 20:
        return 0, ["HARD LOCK: Son periyotta yeterli ivme yok."], "REJECTED", False

    if ilk_tarama:
        detay.append("🔍 KAPI: İlk ölçüm alınıyor (HFT Referans)")
        puan += 2.0 
    else:
        detay.append(f"✅ KAPI GEÇİLDİ: Son periyot ivmesi (Atak: +{delta_atak}, Şut: +{delta_sut})")
        puan += 4.0 

    # Şut Şiddeti
    sut_puani = suanki_sut * 0.5
    puan += sut_puani
    detay.append(f"🎯 Şut Şiddeti: {suanki_sut} isabetli şut (+{sut_puani} Puan)")
    
    if delta_atak >= 15:
        puan += 2.0
        detay.append(f"🌪️ Ani Baskı İvmesi! (+2.0 Puan)")
        stratejiler.append("YUKSEK_IVME")

    # ==================================================
    # 📊 V3.0 EXCEL KANITLI FİLTRELER (YENİ EKLENEN BÖLÜM)
    # ==================================================
    
    # KURAL 1: ALTIN ERKEN AÇILIŞ (%88 Başarı Oranı)
    # Eski 65-75 zayıf penceresi yerine 5-30 dk arası maksimum verim
    if 5 <= dakika <= 30 and (ev_gol + dep_gol) <= 1:
        puan += 3.5
        detay.append("⚡ Altın Erken Açılış (0-30' ve Maks 1 Gol) +3.5")
        stratejiler.append("ERKEN_ACILIS")
        
    # KURAL 2: GEÇ DAKİKA DOMİNASYONU
    # Sadece ağır favori olan maçlarda ikinci yarıya izin ver
    elif 60 <= dakika <= 75 and ah_deger <= -1.0:
        puan += 1.5
        detay.append("🔥 Agresif Baskı (Geç Dakika Dominasyonu) +1.5")
        stratejiler.append("POWER_WINDOW")

    # KURAL 3: AH TUZAK KONTROLÜ (%57 Başarı Oranı Veren Tuzaklar)
    if dakika >= 60 and ah_deger in [-0.50, -0.25, 0.25, 0.50]:
        puan -= 3.0
        detay.append("⚠️ AH TRAP: İkinci yarı dar handikap tuzağı! (-3.0 Puan)")

    # KURAL 4: BÜYÜK HANDİKAP (DOMİNASYON) ÖDÜLÜ (%94 Başarı)
    if ah_deger <= -1.25 or ah_deger >= 1.25:
        puan += 2.0
        detay.append(f"💎 Dominasyon Baremi ({ah_deger} AH) +2.0 Puan")
        
    # ==================================================

    artefakt_puan, art_mesaj = premium_artefakt_kontrol(mac)
    if artefakt_puan > 0:
        puan += artefakt_puan; detay.append(art_mesaj)

    LIG_KATSAYISI = {'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15, 'Champions League': 1.1}
    lig_katsayisi = next((katsayi for lig_adi, katsayi in LIG_KATSAYISI.items() if lig_adi.lower() in mac.get('lig', '').lower()), 1.0)
    
    puan = round((puan * lig_katsayisi) * decay_carpan, 1)
    if decay_carpan < 1.0: detay.append(decay_mesaj)
        
    strateji_adi = stratejiler[0] if stratejiler else "MOMENTUM_TAKIBI"
    return round(puan, 1), detay, strateji_adi, True
