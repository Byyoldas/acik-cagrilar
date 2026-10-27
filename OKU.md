# Açık çağrılar

Günlük çalışan derleyici + yayın. Sayfa: GitHub Pages (`site/index.html`), veri: `cagrilar.json` (her gün yenilenir).

## Günlük işleyiş
`.github/workflows/gunluk.yml` her gün 08:00'de (İstanbul) `gunluk_cagrilar.py` betiğini çalıştırır,
`cagrilar.json` ve `cagrilar.csv` dosyalarını üretir ve siteyi yeniler. Elle çalıştırmak için:
Actions → "Açık çağrılar (günlük)" → Run workflow.

## Manuel çağrı ekleme
1. `site/giris-formu.html` (yayınlanan adreste `/giris-formu.html`) formunu açın, mevcut `manuel.json`'ı seçin,
   çağrıyı ekleyin, `manuel.json` dosyasını indirin.
2. Depoda `manuel.json` dosyasını açın → kalem simgesi yerine "Upload files" ile üzerine yazın (veya düzenleyip commit edin).
3. `manuel.json` değiştiği anda akış kendiliğinden çalışır ve siteyi günceller.

## E-posta (şu an pasif)
Settings → Secrets and variables → Actions içine `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO`
(virgülle ayrılmış) ekleyince yeni/tarihi değişen çağrılar için e-posta başlar. İsteğe bağlı: `SMTP_PORT` (587).

## Kontrol
- Çalışma günlüğünde hata satırları `HATA:` ile başlar. Hata veren kaynak atlanır, diğerleri yayınlanır.
- Tarihi metinden çıkarılamayan duyurular sitede görünmez; `yeni.json` içindeki `kontrol_gerekli` listesindedir
  (yerel çalıştırmada `cikti/yeni.json`).
