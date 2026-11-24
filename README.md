# SKY LAB dış izleme ve durum sayfası

Sunucunun dışından bakan küçük bir izleyici ve herkese açık durum sayfası: **https://status.yildizskylab.com**.
Sunucu, ağı ya da posta yolu tümden düşse de haber verir; sayfa sunuculardan ayrı yerde (Cloudflare) barınır.

## Hizmet nasıl eklenir (3 adım)

1. GitHub'da bu depoda `hizmetler.yml` dosyasını aç, kalem simgesine bas.
2. Listenin sonuna var olan bir hizmeti kopyalayıp değiştir: `kimlik` (küçük harf, tire), `ad`, `grup`, `sira`, `adres`
   (girişsiz, herkese açık bir https adresi), gerekirse `beklenen`, `metin`, `yavas_ms`. Sayfada görünmesin istersen
   `gorunur: hayır`. Alanların hepsi dosyanın başında anlatılıyor.
3. "Commit changes" ile kaydet. En geç 15 dakika sonra hem denetim hem durum sayfası yeni listeyle çalışır.

Dosyada bir yazım hatası olursa hiçbir şey bozulmaz: izleyici son geçerli listeyle devam eder ve `[ALARM]` issue'su hatalı
satırı söyler. Hizmeti kaldırmak için bloğunu silmek yeter (geçmişi de silinir). `kimlik`'i değiştirirsen çubuk sıfırdan
başlar; `ad`'ı değiştirmek geçmişi korur.

## Olay ve planlı bakım duyurmak

- **Olay:** Issues → New issue → "Olay". İlk satır `Hizmetler: Giriş (e-skylab)`, altında kısa açıklama. Adımları yorumla
  ekle (`Sebep bulundu: …`, `İzleniyor: …`, `Çözüldü: …`); issue'yu kapatınca olay "Çözüldü" olur.
- **Planlı bakım:** "Planlı bakım" şablonu; `Hizmetler:`, `Başlangıç: 2026-12-01 16:00`, `Bitiş: 2026-12-01 18:00`
  (İstanbul saati). Bakım süresince hizmet "Bakımda" görünür, alarm vermez ve çalışma yüzdesine sayılmaz.
- Yalnız depo üyelerinin (ve `UYARI_KISILERI`'ndeki kişilerin) açtığı issue'lar ve yazdığı yorumlar sayfaya girer;
  dışarıdan birinin açtığı issue görünmez. Olaylar ve bakımlar RSS'te: https://status.yildizskylab.com/olaylar.xml

## Ne yapar

`.github/workflows/izle.yml` 15 dakikada bir koşar (`izle.py`, yalnız Python standart kitaplığı):

1. **Hizmetler:** `hizmetler.yml`'deki adresler. Her biri en çok 3 deneme; beklenen durum kodu, istenirse yanıtta bir metin,
   yanıt süresi (`yavas_ms`'den uzunsa "Yavaş"). Ayrıca TLS sertifikasının kalan günü (14 günün altı alarm).
2. **Nabız:** sunucudaki zamanlanmış işler (gece yedeği, OpenBao snapshot'ı, sır rotasyonu, saklama/silme alarmları…)
   her koşudan sonra `https://status.yildizskylab.com/heartbeat`'e "ok" ya da "hata" bildirir; sınırları `izleme.json`'da. Bunlar
   sayfada görünmez.
3. **Uyarı:** sorun varsa `[ALARM] SKY LAB dış izleme` issue'su açılır ve `UYARI_KISILERI` anılır; GitHub bunu e-postayla
   iletir. Sorun sürerse 24 saatte bir hatırlatılır, düzelince kendiliğinden kapanır. Denetimler paralel koşar ve toplam 4
   dakikayla sınırlıdır: sunucu tümden düşse de issue birkaç dakikada açılır. Yalnız github-actions[bot]'un açtığı
   issue ve yorumlar sayılır.
4. **Sayfa verisi:** son 90 gün (`durum.json`), sayfanın okuduğu özet (`sayfa.json`) ve RSS (`olaylar.xml`) `veri` dalına
   yazılır: bir hizmetin durumu değişince, olay ya da hizmet listesi değişince hemen; değişiklik yoksa saatte bir.
   `veri` dalı var ama okunamazsa (ağ, yetki, bozuk `durum.json`) denetim ve uyarı yine yapılır; geçmiş silinmesin diye
   o koşuda `veri` dalına ve sayfaya yazılmaz, iş kırmızı biter (GitHub'ın başarısız iş e-postası).

Sunucudaki ters bekçi (`skylab-dis-izleme-bekci`, günde iki kez) bu deponun son başarılı koşusunu GitHub'ın herkese açık
API'sinden okur; 6 saatten eskiyse sunucunun kendi posta yolundan /ADMIN'e yazar. Böylece izleyici de izlenir.

## Kişisel veri, sır, yetki

- Depoda sır yok. Nabız deposu ve sayfa tek bir Cloudflare Worker'da (`worker/worker.js`, KV + statik dosyalar); istek
  IP'si ve gövde saklanmaz. Nabız yazmak için gereken belirteç sunucuda üretilir ve sunucudan çıkmaz; Worker'da yalnız
  SHA-256 özeti durur. `sayfa.json`'da denetim adresleri ve iç hata nedenleri yok.
- İşler: `izle` yalnız okur ve issue yazar (`contents: read`, `issues: write`); `yayin` yalnız `veri` dalına git ile yazar,
  `canlilik` 40 günde bir boş commit atar (`contents: write` yalnız bu ikisinde, ikisinde de betik koşmaz).
- Sayfanın yazı tipleri Worker'da barınır (@fontsource 5.3.0, OFL-1.1); sayfa dışarıya istek atmaz.

## Bilinçli sapmalar

- GitHub Actions zamanlaması yoğunlukta gecikebilir, kuyruktaki koşu düşebilir; 60 gün etkinliksiz açık depoda zamanlama
  kapanır (GitHub belgesi). Hedef RTO 24 saat olduğu için 15 dakikalık bir gecikme önemsiz; 60 gün kuralına karşı
  `canlilik` işi, ikisine karşı sunucudaki ters bekçi.
- Tek yoklama noktası (GitHub'ın ağı). Google ve Microsoft birkaç bölgeden yoklamayı önerir; her denetim 3 deneme yapar ve
  hedef 24 saatlik RTO olduğu için tek nokta yeterli sayıldı. Türkiye içinden görülen kesintiler yakalanmaz.
- Sayfa verisi her koşuda değil, durum değişince ve saatte bir yazılır (Upptime da yalnız değişimde yazar): yılda ~10 bin
  yerine ~35 bin commit olmasın. Kesinti başlangıcı ve bitişi hemen yazıldığı için kesinti dakikaları kaybolmaz; günlük
  ortalama yanıt süresi saatlik örneklerden hesaplanır.
- Sayfa Cloudflare Pages yerine aynı Worker'ın statik dosyalarıyla yayımlanır: Pages'e alan adı bağlamak panel ya da ayrı
  bir API anahtarı ister, Worker alan adlarını wrangler kendisi kurar (Cloudflare yeni projeler için de Workers'ı öneriyor).
