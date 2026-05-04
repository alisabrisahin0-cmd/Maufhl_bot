# MAC ANALIZ BOTU - DURDURULDU
# Bu script calistirildiginda hicbir islem yapmaz ve BetsAPI'ye istek atmaz.

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Bot kullanıcı isteği üzerine durduruldu. Yeni komut bekleniyor.")
    # Dongu yok, veri cekme yok.
