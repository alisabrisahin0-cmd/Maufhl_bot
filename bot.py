from dataclasses import dataclass
from typing import Optional

# ============================================================================
# EXCEL TOP 10 FİLTRELER — Güncellenmiş / En yüksek başarı
# ============================================================================

@dataclass
class ExcelTop10SinyalSonucu:
    filtre_adi: str
    basari_orani: float
    orneklem: int
    ci_low: float
    market_oneri: str
    aciklama: str
    guc_seviyesi: str
    risk: str
    blok: bool = False

class ExcelTop10Filtresi:
    """
    Excel'den çıkarılan en yüksek başarı filtreleri
    Mevcut Gemini/Claude filtrelerini bozmadan çalışır
    """

    @staticmethod
    def kontrol_et(dakika: int, ah_degeri: float, ev_gol: int, dep_gol: int) -> Optional[ExcelTop10SinyalSonucu]:
        fark = ev_gol - dep_gol
        toplam_gol = ev_gol + dep_gol
        ah_abs = abs(ah_degeri)

        # 1️⃣ Underdog Erken Deplasman Önde
        if ah_degeri >= 1.0 and dakika <= 30 and fark < 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Underdog Erken Deplasman Önde",
                basari_orani=97.6,
                orneklem=459,
                ci_low=95.76,
                market_oneri="Çifte Şans X2",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | Deplasman önde → value bet",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük"
            )

        # 2️⃣ Ezici Favori 1X Banko
        if ah_degeri <= -2.0 and dakika <= 30:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Ezici Favori 1X Banko",
                basari_orani=97.14,
                orneklem=699,
                ci_low=95.62,
                market_oneri="Çifte Şans 1X",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} → heavy favori baskısı",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük"
            )

        # 3️⃣ Çok Güçlü Favori Erken 0-0
        if ah_degeri <= -1.25 and dakika <= 30 and fark == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Çok Güçlü Favori Erken 0-0",
                basari_orani=97.42,
                orneklem=466,
                ci_low=95.55,
                market_oneri="Gol Olacak / Sıradaki Gol",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | Skor:0-0 → baskı artacak",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük-Orta"
            )

        # 4️⃣ Ağır Favori Erken 0 Gol
        if ah_degeri <= -0.75 and dakika <= 30 and toplam_gol == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Ağır Favori Erken 0 Gol",
                basari_orani=96.50,
                orneklem=657,
                ci_low=94.80,
                market_oneri="Gol Olacak / MS 2.5 ÜST",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | Gol yok → tempo artacak",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük"
            )

        # 5️⃣ Ağır Favori Erken Eşit
        if ah_degeri <= -1.0 and dakika <= 30 and fark == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Ağır Favori Erken Eşit",
                basari_orani=96.34,
                orneklem=711,
                ci_low=94.70,
                market_oneri="Gol Olacak / Sıradaki Gol",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | Skor eşit → baskı artacak",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük"
            )

        # 6️⃣ Aşırı Favori Ev Gol
        if ah_degeri <= -2.5:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Aşırı Favori Ev Gol",
                basari_orani=95.12,
                orneklem=512,
                ci_low=92.89,
                market_oneri="Ev Gol Atacak",
                aciklama=f"AH:{ah_degeri:+.2f} → çok güçlü favori, ev golü bekleniyor",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük-Orta"
            )

        # 7️⃣ Favori Erken Eşit → Ev Gol
        if ah_degeri <= -1.5 and dakika <= 30 and fark == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Favori Erken Eşit → Ev Gol",
                basari_orani=95.21,
                orneklem=438,
                ci_low=92.78,
                market_oneri="Ev Gol Atacak",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | skor eşit → ev golü penceresi",
                guc_seviyesi="🔥 KRİTİK",
                risk="Orta"
            )

        # 8️⃣ Hafif Favori Erken Geride
        if -1.0 < ah_degeri <= -0.5 and dakika <= 30 and fark < 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Hafif Favori Erken Geride",
                basari_orani=94.68,
                orneklem=620,
                ci_low=92.62,
                market_oneri="Ev Gol Atacak / MS 0.5 ÜST",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | ev geride → geri dönüş penceresi",
                guc_seviyesi="🔥 KRİTİK",
                risk="Düşük-Orta"
            )

        # 9️⃣ Underdog Eşit Erken
        if ah_degeri >= 1.0 and dakika <= 30 and fark == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="Underdog Eşit Erken",
                basari_orani=89.1,
                orneklem=632,
                ci_low=86.0,
                market_oneri="MS 0.5 ÜST / KG Var",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | skor eşit → underdog tempo artışı",
                guc_seviyesi="🔥 KRİTİK",
                risk="Orta"
            )

        # 10️⃣ 2.Yarı Spesifik — Favori + İY Gol
        if ah_degeri <= -1.0 and dakika >= 46 and dakika <= 65 and ev_gol > 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi="2.Yarı Favori + İY Gol",
                basari_orani=86.8,
                orneklem=4190,
                ci_low=85.0,
                market_oneri="Sıradaki Gol / 2Y 0.5 ÜST",
                aciklama=f"AH:{ah_degeri:+.2f} | DK:{dakika} | 2.yarı penceresi açık",
                guc_seviyesi="✅ GÜÇLÜ",
                risk="Düşük"
            )

        # Hiçbir filtre eşleşmezse None
        return None

    @staticmethod
    def mesaj_olustur(ev_adi: str, dep_adi: str, skor: str, dk: int, league: str, sonuc: ExcelTop10SinyalSonucu) -> str:
        blok_uyari = "\n⛔ *Bu sinyal bloklanmıştır — işlem yapma.*" if sonuc.blok else ""
        return (
            f"\n{'═'*30}\n"
            f"📈 *EXCEL TOP 10 FİLTRE: {sonuc.guc_seviyesi}*\n"
            f"⚽ {ev_adi} {skor} {dep_adi}\n"
            f"🏆 {league}\n"
            f"{'─'*28}\n"
            f"🔬 *{sonuc.filtre_adi}*\n"
            f"{sonuc.aciklama}\n"
            f"{'─'*28}\n"
            f"💡 *Market:* {sonuc.market_oneri}\n"
            f"📊 _Başarı: %{sonuc.basari_orani:.1f} | n={sonuc.orneklem}_\n"
            f"{'─'*28}{blok_uyari}"
        )

# ============================================================================
# Kullanım Örneği:
# ============================================================================
# sonuc = ExcelTop10Filtresi.kontrol_et(dakika=12, ah_degeri=-1.3, ev_gol=0, dep_gol=0)
# if sonuc:
#     mesaj = ExcelTop10Filtresi.mesaj_olustur("EvTakim", "DepTakim", "0-0", 12, "Süper Lig", sonuc)
#     print(mesaj)
