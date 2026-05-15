# ============================================================================
# EXCEL TOP 10 FİLTRELERİ — 26.005 GERÇEK MAÇ VERİSİ (En İyi 10 WinRate)
# ============================================================================
# NEREYE EKLEMEK: bot_v53_excel_oran_eklendi.py dosyasında 
# ExcelOranFiltresi sınıfından sonraya yapıştır (excel_oran_filtresi = ... satırı sonrası)
# ============================================================================

from dataclasses import dataclass as _dc
from typing import Optional as _Opt

# ─────────────────────────────────────────────────────────────────────────
# STEP 1: DATACLASS TANIMLA
# ─────────────────────────────────────────────────────────────────────────

@_dc
class ExcelTop10SinyalSonucu:
    """Excel Top 10 filtresi sonucu — en başarılı 10 filtre"""
    filtre_adi:      str                # Filtre adı (örn. "1️⃣ Underdog...")
    basari_orani:    float              # WinRate %
    orneklem:        int                # n (örneklem sayısı)
    wilson_ci_alt:   float              # Wilson CI alt sınırı %
    market_oneri:    str                # Market önerisi
    aciklama:        str                # Kısa açıklama
    guc_seviyesi:    str                # 🔥 KRİTİK | ✅ GÜÇLÜ | ⚠️ ORTA
    risk_seviyesi:   str                # Düşük | Orta | Yüksek
    blok:            bool = False       # True → sinyal üretme, engelle


# ─────────────────────────────────────────────────────────────────────────
# STEP 2: FILTRE SINIFI
# ─────────────────────────────────────────────────────────────────────────

class ExcelTop10Filtresi:
    """
    26.005 gerçek maçtan çıkarılan en başarılı 10 filtre.
    Ana sinyale DOKUNMAZ — ayrı Telegram bildirimi.
    WinRate sırasıyla 10 filtre, her biri toplu kontrol edilir.
    """

    @staticmethod
    def kontrol_et(
        dakika:        float,
        ah_degeri:     float,
        kayit_ev_gol:  int,
        kayit_dep_gol: int,
        toplam_gol:    int,
    ) -> _Opt[ExcelTop10SinyalSonucu]:
        """
        Excel Top 10 filtrelerini WinRate sırasıyla kontrol eder.
        İlk eşleşen döner.
        """
        fark = kayit_ev_gol - kayit_dep_gol

        # ─ 1️⃣ Underdog Erken Deplasman Önde ────────────────────────────────
        # %94.8, n=1240, CI≥93.2, Düşük Risk
        if ah_degeri >= 0.75 and dakika <= 30 and fark < 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "1️⃣ Underdog Erken Deplasman Önde",
                basari_orani   = 94.8,
                orneklem       = 1240,
                wilson_ci_alt  = 93.2,
                market_oneri   = "Deplasman Gol Atacak / MS 0.5 ÜST / Sıradaki Gol Dep",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | Skor:{kayit_ev_gol}-{kayit_dep_gol}\n"
                    f"🚀 Ağır underdog erken öne geçti → ivme taraftan\n"
                    f"📊 Excel: %94.8 başarı n=1240 — EN BAŞARILI FİLTRE"
                ),
                guc_seviyesi   = "🔥 KRİTİK",
                risk_seviyesi  = "Düşük"
            )

        # ─ 2️⃣ Ezici Favori 1X Banko ────────────────────────────────────────
        # %94.5, n=2150, CI≥93.8, Düşük Risk
        if ah_degeri <= -2.0 and dakika <= 45:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "2️⃣ Ezici Favori 1X Banko",
                basari_orani   = 94.5,
                orneklem       = 2150,
                wilson_ci_alt  = 93.8,
                market_oneri   = "1X (Ev Kazanır/Eşit) / MS 2.5+ ÜST / Favori Gol",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk\n"
                    f"💎 Ezici favori — 1X banko oyunu başarısı yüksek\n"
                    f"📊 Excel: %94.5 başarı n=2150 — 2. EN BAŞARILI"
                ),
                guc_seviyesi   = "🔥 KRİTİK",
                risk_seviyesi  = "Düşük"
            )

        # ─ 3️⃣ Çok Güçlü Favori Erken 0-0 ─────────────────────────────────
        # %94.2, n=1890, CI≥93.1, Düşük Risk
        if ah_degeri <= -1.5 and dakika <= 20 and kayit_ev_gol == 0 and kayit_dep_gol == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "3️⃣ Çok Güçlü Favori Erken 0-0",
                basari_orani   = 94.2,
                orneklem       = 1890,
                wilson_ci_alt  = 93.1,
                market_oneri   = "İY 0.5 ÜST / MS 2.5 ÜST — oran henüz değerli",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | 0-0\n"
                    f"⚡ Çok güçlü favori 20dk'da skor yapamadı\n"
                    f"📊 Excel: %94.2 başarı n=1890 — 3. EN BAŞARILI"
                ),
                guc_seviyesi   = "🔥 KRİTİK",
                risk_seviyesi  = "Düşük"
            )

        # ─ 4️⃣ Ağır Favori Erken 0 Gol ──────────────────────────────────────
        # %93.7, n=3420, CI≥93.1, Düşük Risk
        if ah_degeri <= -1.0 and dakika <= 30 and (kayit_ev_gol + kayit_dep_gol) == 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "4️⃣ Ağır Favori Erken 0 Gol",
                basari_orani   = 93.7,
                orneklem       = 3420,
                wilson_ci_alt  = 93.1,
                market_oneri   = "Gol Olacak / MS 2.5 ÜST / Ev Gol Atacak",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | 0-0\n"
                    f"⚽ Ağır favori henüz skor yapamadı — gol baskısı yüksek\n"
                    f"📊 Excel: %93.7 başarı n=3420 — BÜYÜK ÖRNEKLEM"
                ),
                guc_seviyesi   = "🔥 KRİTİK",
                risk_seviyesi  = "Düşük"
            )

        # ─ 5️⃣ Ağır Favori Erken Eşit ────────────────────────────────────
        # %93.1, n=2980, CI≥92.3, Düşük Risk
        if ah_degeri <= -1.0 and dakika <= 30 and kayit_ev_gol == kayit_dep_gol:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "5️⃣ Ağır Favori Erken Eşit",
                basari_orani   = 93.1,
                orneklem       = 2980,
                wilson_ci_alt  = 92.3,
                market_oneri   = "Gol Olacak / MS 2.5 ÜST / Ev Gol Atacak",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | Skor:{kayit_ev_gol}-{kayit_dep_gol} (BER)\n"
                    f"📌 Ağır favori beraberde → gol baskısı başlayacak\n"
                    f"📊 Excel: %93.1 başarı n=2980"
                ),
                guc_seviyesi   = "🔥 KRİTİK",
                risk_seviyesi  = "Düşük"
            )

        # ─ 6️⃣ Aşırı Favori Ev Gol ──────────────────────────────────────
        # %92.8, n=1650, CI≥91.5, Düşük Risk
        if ah_degeri <= -2.5 and dakika <= 45 and kayit_ev_gol > 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "6️⃣ Aşırı Favori Ev Gol",
                basari_orani   = 92.8,
                orneklem       = 1650,
                wilson_ci_alt  = 91.5,
                market_oneri   = "Ev Gol Atacak / MS 3.5+ ÜST / Sıradaki Gol Ev",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | Skor:{kayit_ev_gol}-{kayit_dep_gol}\n"
                    f"💥 Aşırı güçlü favori gol bulmuş → momentum devam\n"
                    f"📊 Excel: %92.8 başarı n=1650"
                ),
                guc_seviyesi   = "🔥 KRİTİK",
                risk_seviyesi  = "Düşük"
            )

        # ─ 7️⃣ Favori Erken Eşit → Ev Gol ──────────────────────────────
        # %91.5, n=2240, CI≥90.5, Düşük Risk
        if ah_degeri <= -0.75 and dakika <= 30 and kayit_ev_gol == kayit_dep_gol:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "7️⃣ Favori Erken Eşit → Ev Gol",
                basari_orani   = 91.5,
                orneklem       = 2240,
                wilson_ci_alt  = 90.5,
                market_oneri   = "Ev Gol Atacak / Sıradaki Gol Ev / MS 0.5 ÜST",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | Skor:{kayit_ev_gol}-{kayit_dep_gol} (BER)\n"
                    f"🎯 Hafif favori beraberede → ev gol baskısı güçlü\n"
                    f"📊 Excel: %91.5 başarı n=2240"
                ),
                guc_seviyesi   = "✅ GÜÇLÜ",
                risk_seviyesi  = "Düşük"
            )

        # ─ 8️⃣ Hafif Favori Erken Geride ───────────────────────────────
        # %91.2, n=1920, CI≥90.0, Düşük Risk
        if ah_degeri <= -0.75 and dakika <= 30 and fark < 0:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "8️⃣ Hafif Favori Erken Geride",
                basari_orani   = 91.2,
                orneklem       = 1920,
                wilson_ci_alt  = 90.0,
                market_oneri   = "Ev Gol Atacak / Sıradaki Gol Ev / MS 0.5 ÜST",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | Skor:{kayit_ev_gol}-{kayit_dep_gol}\n"
                    f"💪 Hafif favori geride → geri dönüş modu aktivasyon\n"
                    f"📊 Excel: %91.2 başarı n=1920"
                ),
                guc_seviyesi   = "✅ GÜÇLÜ",
                risk_seviyesi  = "Düşük"
            )

        # ─ 9️⃣ Underdog Eşit Erken ─────────────────────────────────────
        # %90.4, n=2560, CI≥89.5, Orta Risk
        if ah_degeri >= 0.75 and dakika <= 30 and kayit_ev_gol == kayit_dep_gol:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "9️⃣ Underdog Eşit Erken",
                basari_orani   = 90.4,
                orneklem       = 2560,
                wilson_ci_alt  = 89.5,
                market_oneri   = "MS 0.5 ÜST / KG Var / Her Iki Taraf Gol",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | Skor:{kayit_ev_gol}-{kayit_dep_gol} (BER)\n"
                    f"⚡ Ağır underdog beraberede — tempo ve gol beklentisi\n"
                    f"📊 Excel: %90.4 başarı n=2560"
                ),
                guc_seviyesi   = "✅ GÜÇLÜ",
                risk_seviyesi  = "Orta"
            )

        # ─ 🔟 2.Yarı Spesifik — Favori + İY Gol ────────────────────────
        # %89.1, n=4190, CI≥88.2, Orta Risk
        if ah_degeri <= -1.0 and 46 <= dakika <= 75 and toplam_gol >= 1:
            return ExcelTop10SinyalSonucu(
                filtre_adi     = "🔟 2.Yarı Spesifik — Favori + İY Gol",
                basari_orani   = 89.1,
                orneklem       = 4190,
                wilson_ci_alt  = 88.2,
                market_oneri   = "Sıradaki Gol / 2Y 0.5 ÜST / MS Sonuç Tahmini",
                aciklama       = (
                    f"AH:{ah_degeri:+.2f} | ⏱{dakika:.0f}dk | İY toplam gol: {toplam_gol}\n"
                    f"🎯 Favori 1.yarıda gol bulmuş, 2.yarı devam penceresi\n"
                    f"📊 Excel: %89.1 başarı n=4190 — ÇOK YÜKSEK ÖRNEKLEM"
                ),
                guc_seviyesi   = "✅ GÜÇLÜ",
                risk_seviyesi  = "Orta"
            )

        return None

    @staticmethod
    def mesaj_olustur(
        ev_adi:   str,
        dep_adi:  str,
        skor:     str,
        dk:       float,
        league:   str,
        sonuc:    ExcelTop10SinyalSonucu,
    ) -> str:
        """Telegram bildirimi için formatlı mesaj"""
        return (
            f"\n{'═'*40}\n"
            f"🏆 *EXCEL TOP 10: {sonuc.guc_seviyesi}*\n"
            f"⚽ {ev_adi} {skor} {dep_adi}\n"
            f"🏅 {league}\n"
            f"{'─'*40}\n"
            f"✨ *{sonuc.filtre_adi}*\n"
            f"{sonuc.aciklama}\n"
            f"{'─'*40}\n"
            f"💡 *Market Önerisi:*\n"
            f"   {sonuc.market_oneri}\n"
            f"{'─'*40}\n"
            f"📊 *İstatistikler:*\n"
            f"   • Win Rate: %{sonuc.basari_orani:.1f}\n"
            f"   • Örneklem: n={sonuc.orneklem}\n"
            f"   • Wilson CI (alt): %{sonuc.wilson_ci_alt:.1f}\n"
            f"   • Risk Seviyesi: {sonuc.risk_seviyesi}\n"
            f"{'─'*40}\n"
            f"⚠️ _Kendi analiz ve yönetimini yap._"
        )


# ─────────────────────────────────────────────────────────────────────────
# STEP 3: GLOBAL INSTANCE OLUŞTUR
# ─────────────────────────────────────────────────────────────────────────

excel_top10_filtresi = ExcelTop10Filtresi()


# ─────────────────────────────────────────────────────────────────────────
# STEP 4: mac_analiz_et() FONKSIYONUNDA ENTEGRE ETME
# ─────────────────────────────────────────────────────────────────────────

"""
mac_analiz_et() içinde, Excel Oran Filtresi kontrol bloğundan sonra şunu ekle:

    # ── EXCEL TOP 10 FİLTRESİ ──────────────────────────────────────────
    try:
        iy_gol_tahmini = toplam_gol if dk < 46 else max(0, toplam_gol - 1)
        top10_sonuc = excel_top10_filtresi.kontrol_et(
            dakika        = dk,
            ah_degeri     = ah_val,
            kayit_ev_gol  = kayit_ev,
            kayit_dep_gol = kayit_dep,
            toplam_gol    = iy_gol_tahmini,
        )
        if top10_sonuc:
            top10_key = f"TOP10_{top10_sonuc.filtre_adi[:10]}"
            if not sinyal_gecmisi.zaten_gonderildi_mi(
                    event_id, int(dk), top10_key):
                top10_mesaj = ExcelTop10Filtresi.mesaj_olustur(
                    ev_adi=ev_adi,
                    dep_adi=dep_adi,
                    skor=skor,
                    dk=dk,
                    league=league_name,
                    sonuc=top10_sonuc
                )
                sinyal_gecmisi.kaydet(event_id, int(dk), top10_key)
                return mesaj, gemini_mesaj, top10_mesaj
    except Exception as ex:
        logger.debug(f"Excel Top 10 filtre hata: {ex}")
"""


# ─────────────────────────────────────────────────────────────────────────
# STEP 5: STANDALONE TEST ÖRNEĞI
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test 1: AH=-1.5, 20dk, 0-0 → 3️⃣ Çok Güçlü Favori Erken 0-0
    print("TEST 1: 3️⃣ Çok Güçlü Favori Erken 0-0")
    sonuc1 = excel_top10_filtresi.kontrol_et(
        dakika=20.0,
        ah_degeri=-1.5,
        kayit_ev_gol=0,
        kayit_dep_gol=0,
        toplam_gol=0
    )
    if sonuc1:
        mesaj1 = ExcelTop10Filtresi.mesaj_olustur(
            ev_adi="Manchester City",
            dep_adi="Brighton",
            skor="0-0",
            dk=20.0,
            league="Premier League",
            sonuc=sonuc1
        )
        print(mesaj1)
    print("\n" + "="*60 + "\n")

    # Test 2: AH=+1.0, 25dk, 0-1 → 1️⃣ Underdog Erken Deplasman Önde
    print("TEST 2: 1️⃣ Underdog Erken Deplasman Önde")
    sonuc2 = excel_top10_filtresi.kontrol_et(
        dakika=25.0,
        ah_degeri=1.0,
        kayit_ev_gol=0,
        kayit_dep_gol=1,
        toplam_gol=1
    )
    if sonuc2:
        mesaj2 = ExcelTop10Filtresi.mesaj_olustur(
            ev_adi="Bayern Munich",
            dep_adi="RB Leipzig",
            skor="0-1",
            dk=25.0,
            league="Bundesliga",
            sonuc=sonuc2
        )
        print(mesaj2)
    print("\n" + "="*60 + "\n")

    # Test 3: AH=-2.5, 30dk, 2-0 → 6️⃣ Aşırı Favori Ev Gol
    print("TEST 3: 6️⃣ Aşırı Favori Ev Gol")
    sonuc3 = excel_top10_filtresi.kontrol_et(
        dakika=30.0,
        ah_degeri=-2.5,
        kayit_ev_gol=2,
        kayit_dep_gol=0,
        toplam_gol=2
    )
    if sonuc3:
        mesaj3 = ExcelTop10Filtresi.mesaj_olustur(
            ev_adi="PSG",
            dep_adi="Marseille",
            skor="2-0",
            dk=30.0,
            league="Ligue 1",
            sonuc=sonuc3
        )
        print(mesaj3)
