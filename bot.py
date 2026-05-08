import requests
import os
import json

# Token'ı environment'tan al
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

print("=" * 70)
print("🔍 403 FORBIDDEN ERROR DIAGNOSTIC SCRIPT")
print("=" * 70)
print(f"Token: {BETSAPI_TOKEN[:10]}...{BETSAPI_TOKEN[-5:]}" if BETSAPI_TOKEN else "❌ Token bulunamadı!")
print("=" * 70)

# Test edilecek endpoint'ler
endpoints = [
    # Mevcut kullanılan (Bet365 API)
    {
        "name": "Bet365 Inplay (Mevcut)",
        "url": f"https://api.b365api.com/v1/bet365/inplay?token={BETSAPI_TOKEN}",
        "description": "Bot'ta kullanılan endpoint"
    },
    {
        "name": "Bet365 Event (Mevcut)",
        "url": f"https://api.b365api.com/v1/bet365/event?token={BETSAPI_TOKEN}&FI=12345",
        "description": "Bot'ta kullanılan event endpoint"
    },
    
    # Soccer API alternatifleri (api.betsapi.com)
    {
        "name": "Soccer Inplay v1",
        "url": f"https://api.betsapi.com/v1/events/inplay?sport_id=1&token={BETSAPI_TOKEN}",
        "description": "Soccer API - Canlı maçlar (v1)"
    },
    {
        "name": "Soccer Inplay v2",
        "url": f"https://api.betsapi.com/v2/events/inplay?sport_id=1&token={BETSAPI_TOKEN}",
        "description": "Soccer API - Canlı maçlar (v2)"
    },
    {
        "name": "Soccer Inplay v3",
        "url": f"https://api.betsapi.com/v3/events/inplay?sport_id=1&token={BETSAPI_TOKEN}",
        "description": "Soccer API - Canlı maçlar (v3)"
    },
    
    # Event detay endpoint'leri
    {
        "name": "Soccer Event v1",
        "url": f"https://api.betsapi.com/v1/event/view?token={BETSAPI_TOKEN}&event_id=12345",
        "description": "Soccer API - Event detay (v1)"
    },
    {
        "name": "Soccer Event v2",
        "url": f"https://api.betsapi.com/v2/event/view?token={BETSAPI_TOKEN}&event_id=12345",
        "description": "Soccer API - Event detay (v2)"
    },
    {
        "name": "Soccer Event v3",
        "url": f"https://api.betsapi.com/v3/event/view?token={BETSAPI_TOKEN}&event_id=12345",
        "description": "Soccer API - Event detay (v3)"
    },
    
    # Alternatif endpoint'ler
    {
        "name": "Soccer Upcoming",
        "url": f"https://api.betsapi.com/v1/events/upcoming?sport_id=1&token={BETSAPI_TOKEN}",
        "description": "Soccer API - Yaklaşan maçlar"
    },
    {
        "name": "Soccer Ended",
        "url": f"https://api.betsapi.com/v1/events/ended?sport_id=1&token={BETSAPI_TOKEN}",
        "description": "Soccer API - Biten maçlar"
    }
]

print("\n🧪 ENDPOINT TESTLERİ BAŞLIYOR...\n")

results = []

for i, endpoint in enumerate(endpoints, 1):
    print(f"[{i}/{len(endpoints)}] Testing: {endpoint['name']}")
    print(f"    URL: {endpoint['url'][:80]}...")
    print(f"    Açıklama: {endpoint['description']}")
    
    try:
        response = requests.get(endpoint['url'], timeout=10)
        status = response.status_code
        
        result = {
            "name": endpoint['name'],
            "status": status,
            "url": endpoint['url']
        }
        
        if status == 200:
            print(f"    ✅ SUCCESS: HTTP {status}")
            try:
                data = response.json()
                if 'results' in data:
                    print(f"    📊 Results count: {len(data.get('results', []))}")
                elif 'success' in data:
                    print(f"    📊 Success: {data.get('success')}")
                result['response'] = data
            except:
                print(f"    ⚠️ Response is not JSON")
                result['response'] = response.text[:200]
        elif status == 401:
            print(f"    ❌ UNAUTHORIZED: HTTP {status} - Token geçersiz")
            result['error'] = "Token geçersiz veya eksik"
        elif status == 403:
            print(f"    ❌ FORBIDDEN: HTTP {status} - Erişim izni yok")
            try:
                error_data = response.json()
                print(f"    📄 Error: {error_data}")
                result['error'] = error_data
            except:
                result['error'] = response.text[:200]
        elif status == 404:
            print(f"    ❌ NOT FOUND: HTTP {status} - Endpoint bulunamadı")
            result['error'] = "Endpoint mevcut değil"
        else:
            print(f"    ⚠️ UNEXPECTED: HTTP {status}")
            result['error'] = response.text[:200]
        
        results.append(result)
        
    except requests.exceptions.Timeout:
        print(f"    ⏱️ TIMEOUT: İstek zaman aşımına uğradı")
        results.append({"name": endpoint['name'], "status": "TIMEOUT", "error": "Request timeout"})
    except Exception as e:
        print(f"    ❌ ERROR: {str(e)}")
        results.append({"name": endpoint['name'], "status": "ERROR", "error": str(e)})
    
    print()

# Özet rapor
print("=" * 70)
print("📊 TEST SONUÇLARI ÖZETİ")
print("=" * 70)

success_count = sum(1 for r in results if r.get('status') == 200)
forbidden_count = sum(1 for r in results if r.get('status') == 403)
unauthorized_count = sum(1 for r in results if r.get('status') == 401)
other_count = len(results) - success_count - forbidden_count - unauthorized_count

print(f"\n✅ Başarılı (200): {success_count}")
print(f"❌ Forbidden (403): {forbidden_count}")
print(f"❌ Unauthorized (401): {unauthorized_count}")
print(f"⚠️ Diğer: {other_count}")

if success_count > 0:
    print("\n🎯 ÇALIŞAN ENDPOINT'LER:")
    for r in results:
        if r.get('status') == 200:
            print(f"  ✅ {r['name']}")
            print(f"     URL: {r['url'][:80]}...")

if forbidden_count > 0:
    print("\n🚫 403 FORBIDDEN ENDPOINT'LER:")
    for r in results:
        if r.get('status') == 403:
            print(f"  ❌ {r['name']}")
            if 'error' in r:
                print(f"     Error: {r['error']}")

# JSON rapor kaydet
with open('403_diagnostic_report.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("\n" + "=" * 70)
print("📄 Detaylı rapor kaydedildi: 403_diagnostic_report.json")
print("=" * 70)

# Tavsiyeler
print("\n💡 TAVSİYELER:")
if success_count > 0:
    print("  ✅ Çalışan endpoint'ler bulundu! Bot kodunu bu endpoint'lerle güncelleyin.")
elif forbidden_count == len(results):
    print("  ⚠️ Tüm endpoint'ler 403 döndürüyor.")
    print("  📌 Olası sebepler:")
    print("     1. Soccer API subscription'ı bu endpoint'leri kapsamıyor")
    print("     2. Token'ın yetki kapsamı (scope) sınırlı")
    print("     3. API plan'ı yükseltilmesi gerekiyor")
elif unauthorized_count > 0:
    print("  ⚠️ Token sorunu var. Token'ı kontrol edin.")
else:
    print("  ⚠️ Hiçbir endpoint çalışmıyor. API dokümantasyonunu kontrol edin.")

print("\n🔗 Faydalı Linkler:")
print("  • BetsAPI Docs: https://betsapi.com/docs/")
print("  • Soccer API Docs: https://betsapi.com/docs/soccer.html")
print("  • Support: https://betsapi.com/contact")
