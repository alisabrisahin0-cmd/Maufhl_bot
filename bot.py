#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odds Kodlarını Decode Et
1_1, 1_2 gibi kodların ne anlama geldiğini göster
"""

import os
import requests
import json
from datetime import datetime

# Token'ı environment variable'dan al
BETSAPI_TOKEN = os.getenv('BETSAPI_TOKEN', '')

if not BETSAPI_TOKEN:
    print("❌ HATA: BETSAPI_TOKEN environment variable tanımlı değil!")
    exit(1)

print("=" * 80)
print("🔍 ODDS KODLARINI DECODE ET")
print("=" * 80)
print()

# Inplay maçlarını çek
inplay_url = f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"

try:
    response = requests.get(inplay_url, timeout=10)
    data = response.json()
    matches = data.get('results', [])
    
    if not matches:
        print("❌ Canlı maç bulunamadı")
        exit(1)
    
    first_match = matches[0]
    match_id = first_match.get('id')
    home_team = first_match.get('home', {}).get('name', 'N/A')
    away_team = first_match.get('away', {}).get('name', 'N/A')
    
    print(f"🏆 Maç: {home_team} vs {away_team}")
    print(f"ID: {match_id}")
    print()
    
    # Event odds çek
    odds_url = f"https://api.betsapi.com/v1/event/odds?token={BETSAPI_TOKEN}&event_id={match_id}"
    odds_response = requests.get(odds_url, timeout=10)
    odds_data = odds_response.json()
    
    results = odds_data.get('results', {})
    
    print("=" * 80)
    print("📊 ODDS KODLARI VE İÇERİKLERİ")
    print("=" * 80)
    print()
    
    # Her kodu detaylı göster
    for code in sorted(results.keys()):
        odds_list = results.get(code, [])
        
        print(f"🎲 Kod: {code}")
        print(f"   Tip: {type(odds_list)}")
        
        if isinstance(odds_list, list):
            print(f"   Uzunluk: {len(odds_list)}")
            
            if odds_list:
                # İlk 3 item'ı göster
                for i, item in enumerate(odds_list[:3], 1):
                    print(f"   Item #{i}:")
                    if isinstance(item, dict):
                        # Önemli alanları göster
                        for key in ['name', 'header', 'handicap', 'home_od', 'away_od', 'draw_od', 'over_od', 'under_od']:
                            if key in item:
                                print(f"      • {key}: {item[key]}")
                    else:
                        print(f"      {item}")
                    print()
                
                if len(odds_list) > 3:
                    print(f"   ... ve {len(odds_list) - 3} item daha")
        else:
            print(f"   Değer: {odds_list}")
        
        print()
    
    # Tam response'u kaydet
    with open('odds_decoded.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print("=" * 80)
    print("✅ Tam odds verisi 'odds_decoded.json' dosyasına kaydedildi")
    print("=" * 80)
    print()
    
    # Özet
    print("📋 ÖZET:")
    print()
    
    # Handicap kontrolü
    handicap_found = False
    for code, odds_list in results.items():
        if isinstance(odds_list, list):
            for item in odds_list:
                if isinstance(item, dict) and 'handicap' in item:
                    handicap_found = True
                    print(f"✅ Handicap bulundu kod {code}'da:")
                    print(f"   Örnek: {item}")
                    break
            if handicap_found:
                break
    
    if not handicap_found:
        print("❌ Hiçbir kodda 'handicap' field'ı bulunamadı")
    
    print()
    
    # Over/Under kontrolü
    ou_found = False
    for code, odds_list in results.items():
        if isinstance(odds_list, list):
            for item in odds_list:
                if isinstance(item, dict) and ('over_od' in item or 'under_od' in item):
                    ou_found = True
                    print(f"✅ Over/Under bulundu kod {code}'da:")
                    print(f"   Örnek: {item}")
                    break
            if ou_found:
                break
    
    if not ou_found:
        print("❌ Over/Under bulunamadı")
    
except Exception as e:
    print(f"❌ Hata: {e}")
    import traceback
    traceback.print_exc()
