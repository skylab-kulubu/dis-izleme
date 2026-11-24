#!/usr/bin/env python3
"""SKY LAB dış izleme: sunucu dışından HTTP denetimi, nabız değerlendirmesi, GitHub issue ile uyarı, durum sayfası verisi.

GitHub Actions'ta 15 dakikada bir koşar (.github/workflows/izle.yml). Yalnız Python standart kitaplığı.

Girdiler
  hizmetler.yml   denetlenen ve durum sayfasında gösterilen hizmetler (TEK YER; Yusuf GitHub'da düzenler). Bozuksa son geçerli
                  liste (önceki durum.json'daki) kullanılır ve alarm issue'su hatanın satırını söyler.
  izleme.json     sunucudaki zamanlanmış işlerin nabız sınırları (sayfada görünmez), nabız deposu adresi
                  (https://status.yildizskylab.com/heartbeat), sertifika eşiği.
  ONCEKI_DURUM    önceki durum.json (veri dalından; yoksa ilk koşu).
  GECMIS_OKUNAMADI=1  veri dalı var ama okunamadı (veri-oku.sh). Denetim ve alarm yine yapılır; geçmiş ve sayfa verisi
                  yazılmaz (yayinla=hayir), çıkış 1. ONCEKI_DURUM var ama bozuksa da aynısı.
Çıktılar (CIKTI_DIR)
  durum.json      son 90 günün geçmişi (bir sonraki koşunun girdisi); sayfa.json (sayfanın okuduğu özet: yalnız görünür
                  hizmetler, adres ve iç neden yok); olaylar.xml (RSS). GITHUB_OUTPUT: yayinla, canlilik.

Bütün denetimler (HTTP, TLS, nabız deposu) PARALEL koşar ve toplam süreleri ZAMAN["butce"] (240 sn) ile sınırlıdır: sunucu
tümden düşüp her bağlantı zaman aşımına gitse de değerlendirme birkaç dakikada biter ve issue açılır (işin kendi sınırı 20 dk).

Uyarı: "[ALARM] SKY LAB dış izleme" issue'su, UYARI_KISILERI anılır (GitHub e-postası). Sürerse 24 saatte bir hatırlatma,
düzelince kapanış. Yalnız github-actions[bot]'un açtığı issue'lar ve yazdığı yorumlar sayılır.
Olaylar ve planlı bakım: "olay" / "bakim" etiketli issue'lar; yalnız depo üyelerinin (OWNER/MEMBER/COLLABORATOR ya da
UYARI_KISILERI'ndeki kişiler) açtıkları; adımlar yine yalnız onların yorumlarıyla. Dış kişilerin issue ve yorumları sayılmaz.

Çıkış: 0 değerlendirme yapıldı (alarm olsa da); 1 GitHub'a yazılamadı, geçmiş okunamadı ya da betik hatası (iş kırmızı
olur ve GitHub, zamanlamayı en son değiştiren kişiye ayrıca e-posta atar: ikinci kanal).
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import format_datetime
from xml.sax.saxutils import escape as xml_escape

UA = "skylab-dis-izleme/1 (+https://github.com/skylab-kulubu/dis-izleme)"
BASLIK_ALARM = "[ALARM] SKY LAB dış izleme"
BASLIK_DENEME = "[DENEME] SKY LAB dış izleme: uyarı yolu denemesi"
IM_ANAHTAR = "dis-izleme-anahtarlar"
IM_ANMA = "<!-- dis-izleme-anma -->"
HATIRLAT_SN = 24 * 3600
CANLILIK_GUN = 40
BOT = "github-actions[bot]"
YETKILI_ILISKI = {"OWNER", "MEMBER", "COLLABORATOR"}
GUN_SAYISI = 90
YAYIN_EN_GEC_SN = 55 * 60
SAYFA_URL = "https://status.yildizskylab.com"
KISI_RE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9._-]{1,100})?$")
# Zamanlar (sn). Testler küçültür; oranlar aynı kalır.
ZAMAN = {"http_zaman_asimi": 20, "http_ara": 15, "http_deneme": 3, "tls_zaman_asimi": 15, "butce": 240}

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Europe/Istanbul")
except Exception:  # pragma: no cover
    IST = dt.timezone(dt.timedelta(hours=3))


def saat(epoch):
    if not epoch:
        return "—"
    return dt.datetime.fromtimestamp(int(epoch), IST).strftime("%Y-%m-%d %H:%M")


def sure(sn):
    sn = int(max(0, sn))
    if sn < 3600:
        return f"{sn // 60} dk"
    if sn < 172800:
        return f"{sn // 3600} sa {sn % 3600 // 60} dk"
    return f"{sn // 86400} gün {sn % 86400 // 3600} sa"


def gun_anahtari(epoch):
    return dt.datetime.fromtimestamp(int(epoch), IST).strftime("%Y-%m-%d")


def gun_baslangici(gun):
    """'YYYY-MM-DD' (İstanbul) → epoch."""
    y, a, g = (int(x) for x in gun.split("-"))
    return int(dt.datetime(y, a, g, tzinfo=IST).timestamp())


# ── hizmetler.yml: YAML'ın küçük bir alt kümesi (stdlib'de YAML yok; biçim dosyanın başında anlatılır) ─────────────────

class YapilandirmaHatasi(ValueError):
    pass


ALANLAR = {"kimlik", "ad", "grup", "sira", "gorunur", "aciklama", "adres", "beklenen", "metin", "yavas_ms"}


def _yorum_at(satir):
    """Tırnak dışındaki ' #' ile başlayan yorumu atar."""
    tek = cift = False
    for i, c in enumerate(satir):
        if c == "'" and not cift:
            tek = not tek
        elif c == '"' and not tek:
            cift = not cift
        elif c == "#" and not tek and not cift and (i == 0 or satir[i - 1] in " \t"):
            return satir[:i].rstrip()
    return satir.rstrip()


def _deger(ham, no):
    ham = ham.strip()
    if ham == "":
        return ""
    if ham[0] == "'":
        if len(ham) < 2 or ham[-1] != "'":
            raise YapilandirmaHatasi(f"satır {no}: tek tırnak kapanmamış")
        return ham[1:-1].replace("''", "'")
    if ham[0] == '"':
        if len(ham) < 2 or ham[-1] != '"':
            raise YapilandirmaHatasi(f"satır {no}: çift tırnak kapanmamış")
        try:
            return json.loads(ham)
        except ValueError:
            raise YapilandirmaHatasi(f"satır {no}: çift tırnaklı metin okunamadı (tek tırnak kullan)")
    if ham[0] == "[":
        if ham[-1] != "]":
            raise YapilandirmaHatasi(f"satır {no}: köşeli parantez kapanmamış")
        ic = ham[1:-1].strip()
        return [_deger(x, no) for x in ic.split(",")] if ic else []
    k = ham.lower()
    if k in ("evet", "true", "yes", "açık"):
        return True
    if k in ("hayır", "hayir", "false", "no", "kapalı"):
        return False
    if re.fullmatch(r"-?[0-9]+", ham):
        return int(ham)
    if ": " in ham or ham.endswith(":"):
        raise YapilandirmaHatasi(f"satır {no}: değerde ':' var, değeri tek tırnağa al")
    return ham


def hizmetler_ayristir(metin):
    """hizmetler.yml → [{alan: değer}]. Desteklenen: 'hizmetler:' altında '- anahtar: değer' öğeleri, yorumlar, tırnaklı metin,
    tamsayı, evet/hayır, [1, 2] listesi. Başka her şey satır numarasıyla hata."""
    liste, simdiki, girinti, kok = [], None, None, False
    for no, satir in enumerate(metin.splitlines(), 1):
        if "\t" in satir[: len(satir) - len(satir.lstrip())]:
            raise YapilandirmaHatasi(f"satır {no}: girintide sekme var, boşluk kullan")
        s = _yorum_at(satir)
        if not s.strip():
            continue
        g = len(s) - len(s.lstrip(" "))
        s = s.strip()
        if g == 0:
            if s != "hizmetler:" or kok:
                raise YapilandirmaHatasi(f"satır {no}: en üstte yalnız 'hizmetler:' olur")
            kok = True
            continue
        if not kok:
            raise YapilandirmaHatasi(f"satır {no}: önce 'hizmetler:' satırı gelmeli")
        if s.startswith("- "):
            simdiki = {}
            liste.append(simdiki)
            girinti = g + 2
            s = s[2:].strip()
        elif simdiki is None or g != girinti:
            raise YapilandirmaHatasi(f"satır {no}: girinti beklenen yerde değil (alanlar '- ' işaretinden 2 boşluk içeride)")
        m = re.fullmatch(r"([a-z_]+):(?:\s+(.*))?", s)
        if not m:
            raise YapilandirmaHatasi(f"satır {no}: 'alan: değer' biçiminde değil")
        alan = m.group(1)
        if alan not in ALANLAR:
            raise YapilandirmaHatasi(f"satır {no}: bilinmeyen alan '{alan}' (yazım hatası mı?)")
        if alan in simdiki:
            raise YapilandirmaHatasi(f"satır {no}: '{alan}' iki kez yazılmış")
        simdiki[alan] = _deger(m.group(2) or "", no)
        simdiki.setdefault("_satir", no)
    if not kok:
        raise YapilandirmaHatasi("'hizmetler:' satırı yok")
    return liste


def hizmetler_dogrula(liste):
    """Alanları denetler, varsayılanları koyar, sıraya dizer. Hata → YapilandirmaHatasi."""
    if not liste:
        raise YapilandirmaHatasi("listede hiç hizmet yok")
    if len(liste) > 40:
        raise YapilandirmaHatasi("en çok 40 hizmet")
    cikti, gorulen = [], set()
    for h in liste:
        no = h.get("_satir", "?")
        kim = h.get("kimlik")
        if not isinstance(kim, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", kim):
            raise YapilandirmaHatasi(f"satır {no}: kimlik küçük harf, rakam ve tireden oluşmalı (en çok 31)")
        if kim in gorulen:
            raise YapilandirmaHatasi(f"satır {no}: kimlik '{kim}' iki kez var")
        gorulen.add(kim)
        ad = h.get("ad")
        if not isinstance(ad, str) or not ad.strip() or len(ad) > 60:
            raise YapilandirmaHatasi(f"satır {no} ({kim}): ad zorunlu, en çok 60 karakter")
        adres = h.get("adres")
        if not isinstance(adres, str) or not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(/[^\s]*)?", adres):
            raise YapilandirmaHatasi(f"satır {no} ({kim}): adres https:// ile başlayan bir adres olmalı")
        bek = h.get("beklenen", [200])
        if isinstance(bek, int) and not isinstance(bek, bool):
            bek = [bek]
        if not isinstance(bek, list) or not bek or not all(
                isinstance(x, int) and not isinstance(x, bool) and 100 <= x <= 599 for x in bek):
            raise YapilandirmaHatasi(f"satır {no} ({kim}): beklenen [200] gibi durum kodları listesi olmalı")
        yavas = h.get("yavas_ms", 3000)
        if not isinstance(yavas, int) or isinstance(yavas, bool) or not 100 <= yavas <= 60000:
            raise YapilandirmaHatasi(f"satır {no} ({kim}): yavas_ms 100 ile 60000 arasında bir sayı olmalı")
        sira = h.get("sira", 100)
        if not isinstance(sira, int) or isinstance(sira, bool):
            raise YapilandirmaHatasi(f"satır {no} ({kim}): sira bir sayı olmalı")
        gor = h.get("gorunur", True)
        if not isinstance(gor, bool):
            raise YapilandirmaHatasi(f"satır {no} ({kim}): gorunur evet ya da hayır olmalı")
        for alan, en in (("grup", 40), ("aciklama", 60), ("metin", 200)):
            v = h.get(alan)
            if v is not None and (not isinstance(v, str) or len(v) > en):
                raise YapilandirmaHatasi(f"satır {no} ({kim}): {alan} en çok {en} karakterlik metin olmalı")
        cikti.append({"kimlik": kim, "ad": ad.strip(), "grup": (h.get("grup") or "Hizmetler").strip(), "sira": sira,
                      "gorunur": gor, "aciklama": (h.get("aciklama") or "").strip(), "adres": adres, "beklenen": bek,
                      "metin": h.get("metin") or "", "yavas_ms": yavas})
    cikti.sort(key=lambda x: (x["sira"], x["kimlik"]))
    return cikti


def hizmetleri_yukle(yol, onceki):
    """(hizmetler, hata_metni). Dosya bozuksa önceki durum.json'daki son geçerli liste."""
    try:
        with open(yol, encoding="utf-8") as f:
            return hizmetler_dogrula(hizmetler_ayristir(f.read())), ""
    except (OSError, UnicodeDecodeError, YapilandirmaHatasi) as e:
        hata = str(e) if isinstance(e, YapilandirmaHatasi) else f"okunamadı ({type(e).__name__})"
        return list((onceki or {}).get("hizmetler") or []), hata


# ── HTTP ve TLS ────────────────────────────────────────────────────────────────────────────────────────────────────────

class SizeYonlendirme(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # yönlendirme izlenmez: beklenen kod 3xx değilse hata sayılır
        return None


ACICI = urllib.request.build_opener(SizeYonlendirme)


def http_get(url, zaman_asimi=20):
    """(durum, gövde_baytları, başlıklar, hata_metni); ağ hatasında durum None."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Cache-Control": "no-cache"})
    try:
        with ACICI.open(req, timeout=zaman_asimi) as r:
            return r.status, r.read(262144), {k.lower(): v for k, v in r.headers.items()}, ""
    except urllib.error.HTTPError as e:
        govde = b""
        try:
            govde = e.read(65536)
        except Exception:
            pass
        return e.code, govde, {k.lower(): v for k, v in (e.headers or {}).items()}, ""
    except Exception as e:  # zaman aşımı, DNS, TLS
        return None, b"", {}, type(e).__name__ + ": " + str(e)[:160]


def kalan(bitis):
    return bitis - time.monotonic()


def denetle_hizmet(h, get=http_get, uyu=time.sleep, bitis=None):
    """Bir hizmetin denetimi → {kimlik, ad, d: ok|yavas|kesinti, ms, neden}. bitis (monotonic) geçince yeniden denemez."""
    deneme, ara = ZAMAN["http_deneme"], ZAMAN["http_ara"]
    if bitis is None:
        bitis = time.monotonic() + ZAMAN["butce"]
    son, yapilan, kesildi = "süre sınırı (hiç denenemedi)", 0, False
    for i in range(deneme):
        k = kalan(bitis)
        if k <= 0:
            kesildi = True
            break
        t0 = time.monotonic()
        yapilan += 1
        kod, govde, bas, hata = get(h["adres"], zaman_asimi=min(ZAMAN["http_zaman_asimi"], k))
        ms = int((time.monotonic() - t0) * 1000)
        if kod is None:
            son = f"bağlanılamadı ({hata})"
        elif kod not in h["beklenen"]:
            son = f"HTTP {kod}"
            if kod == 403 and bas.get("cf-mitigated"):
                son += " (Cloudflare engeli: cf-mitigated)"
        elif h.get("metin") and h["metin"] not in govde.decode("utf-8", "replace"):
            son = f"HTTP {kod} ama beklenen metin yanıtta yok"
        else:
            d = "yavas" if ms > h["yavas_ms"] else "ok"
            return {"kimlik": h["kimlik"], "ad": h["ad"], "d": d, "ms": ms, "neden": f"HTTP {kod}, {ms} ms"}
        if i < deneme - 1:
            if kalan(bitis) <= ara:
                kesildi = True
                break
            uyu(ara)
    ek = f" ({yapilan} deneme" + (", süre sınırı" if kesildi else "") + ")"
    return {"kimlik": h["kimlik"], "ad": h["ad"], "d": "kesinti", "ms": None, "neden": son + ek}


def sertifika_gun(host, port=443, zaman_asimi=15):
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=zaman_asimi) as s:
        with ctx.wrap_socket(s, server_hostname=host) as t:
            bitis = ssl.cert_time_to_seconds(t.getpeercert()["notAfter"])
    return (bitis - time.time()) / 86400


def nabiz_oku_url(url):
    """Nabız özetinin adresi. Yeni taban (…/heartbeat) kendisi okunur; eski taban (https://nabiz.yildizskylab.com) için
    /durum eklenir: depo değişkeni NABIZ_URL ile Worker'ın geçişi hangi sırayla olursa olsun okuma kopmaz (wizard --eski-sil'e
    kadar eski ad da yayında)."""
    url = url.rstrip("/")
    return url if url.endswith("/heartbeat") else url + "/durum"


def nabiz_oku(url, get=http_get, uyu=time.sleep, bitis=None):
    """Nabız deposunun özeti (nabiz_oku_url): (durum_sözlüğü | None, hata_metni). En çok 3 deneme, bütçe içinde."""
    if bitis is None:
        bitis = time.monotonic() + ZAMAN["butce"]
    hata_m = "süre sınırı"
    for i in range(ZAMAN["http_deneme"]):
        k = kalan(bitis)
        if k <= 0:
            break
        kod, govde, _, hata = get(nabiz_oku_url(url), zaman_asimi=min(ZAMAN["http_zaman_asimi"], k))
        if kod == 200:
            try:
                return json.loads(govde.decode("utf-8")), ""
            except Exception:
                hata_m = "JSON bozuk"
        else:
            hata_m = f"HTTP {kod}" if kod else hata
        if i < ZAMAN["http_deneme"] - 1:
            if kalan(bitis) <= ZAMAN["http_ara"]:
                break
            uyu(ZAMAN["http_ara"])
    return None, hata_m


# ── Nabız (sunucudaki zamanlanmış işler; sayfada görünmez) ─────────────────────────────────────────────────────────────

def nabiz_degerlendir(durum, yapi, simdi):
    """durum: Worker'ın /heartbeat JSON'u. Dönüş: (alarmlar[{anahtar, metin}], satırlar[tablo], bağlı olmayanlar[ad])."""
    alarmlar, satirlar = [], []
    isler = durum.get("isler") or {}
    tanimli = yapi.get("nabiz") or {}
    vars_ = yapi.get("varsayilan_nabiz") or {"en_cok_saat": 26, "hata_bekle_dk": 0}
    bagli_degil = sorted(a for a in tanimli if a not in isler)
    for ad in sorted(isler):
        k = isler[ad] or {}
        ayar = tanimli.get(ad, vars_)
        en_cok = float(ayar.get("en_cok_saat", 26)) * 3600
        bekle = float(ayar.get("hata_bekle_dk", 0)) * 60
        ok, hata, ilk, kayit = (int(k.get(x) or 0) for x in ("ok", "hata", "hata_ilk", "kayit"))
        durum_metin = "tamam"
        hata_sonda = k.get("son") == "hata" if k.get("son") else hata > ok
        if hata_sonda:
            baslangic = ilk or hata
            if simdi - baslangic >= bekle:
                durum_metin = f"son koşu BAŞARISIZ ({saat(hata)}; {sure(simdi - baslangic)} süredir)"
                alarmlar.append({"anahtar": f"nabiz-hata:{ad}", "metin": f"`{ad}`: {durum_metin}"})
            else:
                durum_metin = f"son koşu başarısız, {int(bekle // 60)} dk içinde düzelmesi bekleniyor"
        if not durum_metin.startswith("son koşu BAŞARISIZ"):
            if ok:
                yas = simdi - ok
                if yas > en_cok:
                    durum_metin = f"GECİKTİ: son başarı {sure(yas)} önce (sınır {ayar.get('en_cok_saat')} sa)"
                    alarmlar.append({"anahtar": f"nabiz-gec:{ad}", "metin": f"`{ad}`: {durum_metin}"})
            else:
                temel = kayit or hata
                if temel and simdi - temel > en_cok:
                    durum_metin = f"HİÇ BAŞARI YOK: {sure(simdi - temel)} önce bağlandı (sınır {ayar.get('en_cok_saat')} sa)"
                    alarmlar.append({"anahtar": f"nabiz-gec:{ad}", "metin": f"`{ad}`: {durum_metin}"})
                elif durum_metin == "tamam":
                    durum_metin = "bağlandı, ilk başarı bekleniyor"
        if ad not in tanimli:
            durum_metin += " (izleme.json'da yok: varsayılan sınır)"
        satirlar.append((ad, saat(ok), saat(hata) if hata else "—", durum_metin))
    return alarmlar, satirlar, bagli_degil


# ── Olaylar ve planlı bakım (GitHub issue'ları) ───────────────────────────────────────────────────────────────────────

ASAMALAR = ["İnceleniyor", "Sebep bulundu", "İzleniyor", "Çözüldü", "Planlandı", "Başladı", "Tamamlandı", "Güncelleme"]
_META_RE = re.compile(r"^\s*(hizmetler|başlangıç|baslangic|bitiş|bitis)\s*:\s*(.*?)\s*$", re.I)


def _tr_kucuk(s):
    return s.replace("I", "ı").replace("İ", "i").lower().strip()


def _ascii(s):
    return _tr_kucuk(s).translate(str.maketrans("çğıöşü", "cgiosu"))


def _ist_zaman(metin):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", (metin or "").strip())
    if not m:
        return None
    y, a, g, s, d = (int(x) for x in m.groups())
    try:
        return int(dt.datetime(y, a, g, s, d, tzinfo=IST).timestamp())
    except ValueError:
        return None


def iso_epoch(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())


def yetkili_mi(nesne, yazarlar):
    login = ((nesne.get("user") or {}).get("login") or "").lower()
    if not login or login == BOT:
        return False
    return nesne.get("author_association") in YETKILI_ILISKI or login in yazarlar


def govde_ayristir(govde):
    """Issue gövdesi → (meta{hizmetler, baslangic, bitis}, açıklama metni). Meta satırları gövdenin herhangi bir yerinde."""
    meta, kalanlar = {}, []
    govde = re.sub(r"<!--.*?(-->|$)", "", (govde or "").replace("\r", ""), flags=re.S)
    for satir in govde.split("\n"):
        m = _META_RE.match(satir)
        if m:
            meta[_ascii(m.group(1))] = m.group(2)
        else:
            kalanlar.append(satir)
    return meta, "\n".join(kalanlar).strip()


def adim_ayristir(govde):
    """Yorum → (aşama, metin). İlk satır 'Aşama: metin' biçimindeyse ve aşama bilinen biriyse o; değilse Güncelleme."""
    metin = (govde or "").replace("\r", "").strip()
    m = re.match(r"^\**\s*([^:\n*]{2,20}?)\s*\**\s*:\s*(.*)$", metin, re.S)
    if m:
        aday = _ascii(m.group(1))
        for a in ASAMALAR:
            if _ascii(a) == aday:
                return a, m.group(2).strip()
    return "Güncelleme", metin


def olay_ayristir(issue, yorumlar, hizmetler, simdi, yazarlar):
    """Bir olay/bakım issue'su → sayfa kaydı. Yetkisiz yazarın issue'su None; yetkisiz yorumlar atlanır."""
    if not yetkili_mi(issue, yazarlar):
        return None
    etiketler = {(e.get("name") or "").lower() for e in issue.get("labels") or []}
    tur = "bakim" if "bakim" in etiketler else "olay"
    meta, aciklama = govde_ayristir(issue.get("body"))
    adlar = {}
    for h in hizmetler:
        adlar[_ascii(h["ad"])] = h["kimlik"]
        adlar[h["kimlik"]] = h["kimlik"]
    etkilenen, etkilenen_ad = [], []
    for parca in re.split(r"[,;]", meta.get("hizmetler", "")):
        p = parca.strip()
        if not p:
            continue
        kim = adlar.get(_ascii(p))
        if kim and kim not in etkilenen:
            etkilenen.append(kim)
        etkilenen_ad.append(next((h["ad"] for h in hizmetler if h["kimlik"] == kim), p))
    acilis = iso_epoch(issue["created_at"])
    kapanis = iso_epoch(issue["closed_at"]) if issue.get("closed_at") else None
    kapali = issue.get("state") == "closed"
    bas = _ist_zaman(meta.get("baslangic")) or acilis
    if tur == "olay":
        son = kapanis if kapali else None
        durum = "cozuldu" if kapali else "suruyor"
    else:
        son = _ist_zaman(meta.get("bitis"))
        if kapali and kapanis and (son is None or kapanis < son):
            son = kapanis
        if kapali or (son and simdi >= son):
            durum = "tamamlandi"
        elif simdi < bas:
            durum = "planlandi"
        else:
            durum = "suruyor"
    adimlar = [{"asama": "Planlandı" if tur == "bakim" else "İnceleniyor", "t": acilis, "m": aciklama[:2000]}]
    for y in yorumlar or []:
        if not yetkili_mi(y, yazarlar):
            continue
        a, m = adim_ayristir(y.get("body"))
        if m or a != "Güncelleme":
            adimlar.append({"asama": a, "t": iso_epoch(y["created_at"]), "m": m[:2000]})
    adimlar.sort(key=lambda x: x["t"])
    if kapali and kapanis and adimlar[-1]["asama"] not in ("Çözüldü", "Tamamlandı"):
        adimlar.append({"asama": "Tamamlandı" if tur == "bakim" else "Çözüldü", "t": kapanis, "m": ""})
    adimlar.reverse()
    return {"no": issue["number"], "tur": tur, "baslik": (issue.get("title") or "")[:120], "durum": durum,
            "hizmetler": etkilenen, "hizmet_adlari": etkilenen_ad, "bas": bas, "son": son,
            "guncelleme": max(a["t"] for a in adimlar), "adimlar": adimlar}


def bakimdakiler(olaylar, simdi):
    """Şu an planlı bakımda olan hizmet kimlikleri."""
    k = set()
    for o in olaylar:
        if o["tur"] == "bakim" and o["durum"] == "suruyor" and o["bas"] <= simdi:
            k.update(o["hizmetler"])
    return k


def rss(olaylar, simdi):
    """Olaylar ve bakımlar için RSS 2.0 (son 30)."""
    def zaman(t):
        return format_datetime(dt.datetime.fromtimestamp(int(t), dt.timezone.utc))
    durum_ad = {"suruyor": "sürüyor", "cozuldu": "çözüldü", "planlandi": "planlandı", "tamamlandi": "tamamlandı"}
    ogeler = []
    for o in sorted(olaylar, key=lambda x: x["guncelleme"], reverse=True)[:30]:
        tur = "Planlı bakım" if o["tur"] == "bakim" else "Olay"
        son = o["adimlar"][0]
        aciklama = f"{son['asama']}: {son['m']}" if son["m"] else son["asama"]
        if o["hizmet_adlari"]:
            aciklama += " (Etkilenen: " + ", ".join(o["hizmet_adlari"]) + ")"
        ogeler.append(
            "<item>"
            f"<title>{xml_escape(tur + ': ' + o['baslik'] + ' (' + durum_ad.get(o['durum'], o['durum']) + ')')}</title>"
            f"<link>{SAYFA_URL}/#olay-{o['no']}</link>"
            f"<guid isPermaLink=\"false\">skylab-durum-{o['no']}-{o['guncelleme']}</guid>"
            f"<pubDate>{zaman(o['guncelleme'])}</pubDate>"
            f"<description>{xml_escape(aciklama)}</description>"
            "</item>")
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel>'
            "<title>SKY LAB Durum</title>"
            f"<link>{SAYFA_URL}/</link><description>Olaylar ve planlı bakımlar</description>"
            f"<language>tr</language><lastBuildDate>{zaman(simdi)}</lastBuildDate>"
            + "".join(ogeler) + "</channel></rss>\n")


# ── Durum geçmişi (durum.json) ────────────────────────────────────────────────────────────────────────────────────────

def _kesisim(a1, a2, b1, b2):
    return max(0, min(a2, b2) - max(a1, b1))


def gun_ozeti(kim, h, durum, olaylar, simdi):
    """Bir hizmetin son 90 günü (eskiden yeniye): [{g, d, y, k, ms, n}]; d: ok|yavas|kesinti|bakim|veriyok, k: kesinti dk,
    y: çalışma yüzdesi (bakım süresi hesaba katılmaz)."""
    gecis = durum["gecisler"].get(kim) or []
    ilk = durum["ilk"].get(kim) or simdi
    bakimlar = [(o["bas"], o["son"] or simdi) for o in olaylar if o["tur"] == "bakim" and kim in o["hizmetler"]]
    kesintiler = [(t, gecis[i + 1][0] if i + 1 < len(gecis) else simdi) for i, (t, d) in enumerate(gecis) if d == "kesinti"]
    g0 = gun_baslangici(gun_anahtari(simdi))
    gunler = []
    for i in range(GUN_SAYISI - 1, -1, -1):
        bas = g0 - i * 86400  # Türkiye 2016'dan beri yıl boyu UTC+3: gün hep 86400 sn
        gun = gun_anahtari(bas + 3600)
        son = min(bas + 86400, simdi)
        kap = _kesisim(bas, son, ilk, simdi)
        if kap <= 0 and bas <= ilk <= son:
            kap = 1  # izleme bu gün, bu koşuda başladı: ilk örnek gösterilir
        if kap <= 0:
            gunler.append({"g": gun, "d": "veriyok", "y": None, "k": 0, "ms": None, "n": ""})
            continue
        bak = sum(_kesisim(max(bas, ilk), son, b1, b2) for b1, b2 in bakimlar)
        kes = 0
        for k1, k2 in kesintiler:
            ic = _kesisim(bas, son, k1, k2)
            if ic:
                kes += max(0, ic - sum(_kesisim(max(bas, k1), min(son, k2), b1, b2) for b1, b2 in bakimlar))
        ornek = (durum["ms"].get(kim) or {}).get(gun)
        ms = int(ornek[1] / ornek[0]) if ornek and ornek[0] else None
        etkin = max(0, kap - bak)
        y = round(100.0 * (1 - kes / etkin), 3) if etkin > 0 else 100.0
        kdk = int(round(kes / 60))
        if kdk >= 1:
            d, n = "kesinti", f"{kdk} dakika"
        elif bak > 0:
            d, n = "bakim", ""
        elif ms is not None and ms > h["yavas_ms"]:
            d, n = "yavas", f"Ortalama yanıt {ms / 1000:.1f} sn".replace(".", ",")
        else:
            d, n = "ok", (f"Ortalama yanıt {ms} ms" if ms is not None else "")
        gunler.append({"g": gun, "d": d, "y": y, "k": kdk, "ms": ms, "n": n})
    return gunler


def _ozet(olaylar):
    return hashlib.sha256(json.dumps(olaylar, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def durum_guncelle(onceki, hizmetler, sonuclar, olaylar, simdi, yap_hatasi):
    """Önceki durum.json + bu koşu → (yeni durum, yayımlanmalı mı, neden). Yayın: ilk koşu, bir hizmetin durumu değişti,
    hizmet listesi / yapılandırma hatası / olaylar değişti ya da son yayından 55 dk geçti."""
    o = onceki or {}
    yeni = {"surum": 1, "guncelleme": simdi, "hizmetler": hizmetler, "yapilandirma_hatasi": yap_hatasi or "",
            "son": dict(o.get("son") or {}),
            "gecisler": {k: [list(x) for x in v] for k, v in (o.get("gecisler") or {}).items()},
            "ilk": dict(o.get("ilk") or {}), "ms": {k: dict(v) for k, v in (o.get("ms") or {}).items()},
            "olaylar": olaylar}
    neden = []
    if not onceki:
        neden.append("ilk koşu")
    gecerli = {h["kimlik"] for h in hizmetler}
    for alan in ("son", "gecisler", "ilk", "ms"):
        for kim in [k for k in yeni[alan] if k not in gecerli]:
            yeni[alan].pop(kim)
    bakimda = bakimdakiler(olaylar, simdi)
    for h in hizmetler:
        kim = h["kimlik"]
        r = sonuclar.get(kim)
        if not r:
            continue
        d = "bakim" if kim in bakimda else r["d"]  # planlı bakım süresince sayfada "Bakımda"; alarm da verilmez
        yeni["ilk"].setdefault(kim, simdi)
        eski = (o.get("son") or {}).get(kim, {}).get("d")
        if eski != d:
            neden.append(f"{kim}: {eski or 'yeni'} → {d}")
        g = yeni["gecisler"].setdefault(kim, [])
        kesinti_mi = "kesinti" if d == "kesinti" else "ok"
        if not g or g[-1][1] != kesinti_mi:
            g.append([simdi, kesinti_mi])
        yeni["son"][kim] = {"d": d, "ms": r["ms"], "t": simdi}
        if r["ms"] is not None:
            gun = gun_anahtari(simdi)
            o_gun = yeni["ms"].setdefault(kim, {}).get(gun) or [0, 0]
            yeni["ms"][kim][gun] = [o_gun[0] + 1, o_gun[1] + r["ms"]]
    sinir = simdi - GUN_SAYISI * 86400
    for kim, g in yeni["gecisler"].items():
        g.sort()
        once = [x for x in g if x[0] < sinir]
        yeni["gecisler"][kim] = ([once[-1]] if once else []) + [x for x in g if x[0] >= sinir]
    gun_sinir = gun_anahtari(sinir)
    for kim in yeni["ms"]:
        yeni["ms"][kim] = {gn: v for gn, v in yeni["ms"][kim].items() if gn >= gun_sinir}
    if hizmetler != (o.get("hizmetler") or []):
        neden.append("hizmet listesi değişti")
    if (yap_hatasi or "") != (o.get("yapilandirma_hatasi") or ""):
        neden.append("yapılandırma hatası değişti")
    if _ozet(olaylar) != _ozet(o.get("olaylar") or []):
        neden.append("olaylar değişti")
    if simdi - int(o.get("guncelleme") or 0) >= YAYIN_EN_GEC_SN:
        neden.append("saatlik yayın")
    yeni["sayfa"] = sayfa_verisi(yeni, simdi)
    return yeni, bool(neden), ", ".join(neden)


def sayfa_verisi(durum, simdi):
    """Sayfanın okuduğu hazır özet: yalnız görünür hizmetler; denetim adresleri ve iç nedenler yok."""
    hizmetler = []
    for h in durum["hizmetler"]:
        if not h["gorunur"]:
            continue
        son = durum["son"].get(h["kimlik"]) or {}
        gunler = gun_ozeti(h["kimlik"], h, durum, durum["olaylar"], simdi)
        etkin = [g for g in gunler if g["y"] is not None]
        hizmetler.append({"kimlik": h["kimlik"], "ad": h["ad"], "grup": h["grup"], "sira": h["sira"],
                          "aciklama": h["aciklama"], "simdi": son.get("d") or "veriyok", "ms": son.get("ms"),
                          "yuzde": round(sum(g["y"] for g in etkin) / len(etkin), 3) if etkin else None, "gunler": gunler})
    gorunur = {h["kimlik"] for h in durum["hizmetler"] if h["gorunur"]}
    olaylar = []
    for o in durum["olaylar"]:
        if o["tur"] == "olay" and o["bas"] < simdi - GUN_SAYISI * 86400:
            continue
        olaylar.append(dict(o, hizmetler=[k for k in o["hizmetler"] if k in gorunur]))
    return {"guncelleme": simdi, "hizmetler": hizmetler, "olaylar": olaylar}


# ── GitHub ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def anahtarlar_govdeden(govde):
    m = re.search(r"<!-- " + IM_ANAHTAR + r": ([^>]*) -->", govde or "")
    if not m:
        return set()
    return {x for x in m.group(1).split(",") if x}


def karar(acik, eski, yeni, son_anma, simdi):
    """Issue kararı. acik: açık alarm issue'su var mı. Dönüş: ac | yorum-yeni | hatirlat | guncelle | kapat | yok."""
    if yeni and not acik:
        return "ac"
    if not yeni:
        return "kapat" if acik else "yok"
    if yeni - eski:
        return "yorum-yeni"
    if simdi - son_anma >= HATIRLAT_SN:
        return "hatirlat"
    return "guncelle" if yeni != eski else "yok"


class GitHub:
    def __init__(self, depo, token, api="https://api.github.com"):
        self.depo, self.token, self.api = depo, token, api.rstrip("/")

    def istek(self, yontem, yol, veri=None):
        govde = None if veri is None else json.dumps(veri).encode()
        req = urllib.request.Request(self.api + yol, data=govde, method=yontem, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ham = r.read()
        return json.loads(ham) if ham else None

    def acik_alarm(self):
        """Yalnız github-actions[bot]'un açtığı açık "[ALARM]" issue'su (açık depoda başkası aynı başlığı açabilir)."""
        for s in range(1, 4):
            liste = self.istek("GET", f"/repos/{self.depo}/issues?state=open&per_page=100&page={s}"
                                      f"&creator={urllib.parse.quote(BOT)}")
            for i in liste:
                if "pull_request" not in i and i.get("title") == BASLIK_ALARM and (i.get("user") or {}).get("login") == BOT:
                    return i
            if len(liste) < 100:
                break
        return None

    def son_anma(self, issue):
        enson = iso_epoch(issue["created_at"])
        yorumlar = self.istek("GET", f"/repos/{self.depo}/issues/{issue['number']}/comments?per_page=100")
        for y in yorumlar or []:
            if (y.get("user") or {}).get("login") == BOT and IM_ANMA in (y.get("body") or ""):
                enson = max(enson, iso_epoch(y["created_at"]))
        return enson

    def olay_issuelari(self, simdi, yazarlar=frozenset()):
        """'olay' ve 'bakim' etiketli issue'lar (son 90 günde güncellenen) ve yorumları. Yorumlar yalnız yetkili yazarın
        açtığı issue'lar için çekilir (yetkisizinki zaten sayılmaz; dışarıdan açılan issue'lar API kotası harcamasın)."""
        since = dt.datetime.fromtimestamp(simdi - GUN_SAYISI * 86400, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        gorulen, sonuc = set(), []
        for etiket in ("olay", "bakim"):
            liste = self.istek("GET", f"/repos/{self.depo}/issues?labels={etiket}&state=all&since={since}&per_page=100") or []
            for i in liste:
                if "pull_request" in i or i["number"] in gorulen or not yetkili_mi(i, yazarlar):
                    continue
                gorulen.add(i["number"])
                yorumlar = (self.istek("GET", f"/repos/{self.depo}/issues/{i['number']}/comments?per_page=100")
                            if i.get("comments") else [])
                sonuc.append((i, yorumlar or []))
        return sonuc

    def ac(self, baslik, govde):
        return self.istek("POST", f"/repos/{self.depo}/issues", {"title": baslik, "body": govde})

    def yorum(self, no, govde):
        return self.istek("POST", f"/repos/{self.depo}/issues/{no}/comments", {"body": govde})

    def duzenle(self, no, **alan):
        return self.istek("PATCH", f"/repos/{self.depo}/issues/{no}", alan)

    def son_commit_epoch(self):
        c = self.istek("GET", f"/repos/{self.depo}/commits?per_page=1")
        return iso_epoch(c[0]["commit"]["committer"]["date"]) if c else 0


def kisiler(ham):
    ok, yanlis = [], []
    for k in (ham or "").replace(",", " ").split():
        (ok if KISI_RE.match(k) else yanlis).append(k)
    return ok, yanlis


def alarm_govdesi(alarmlar, simdi, run_url, anma):
    satir = [f"Durum {saat(simdi)} (İstanbul) itibarıyla sorunlu olanlar:", ""]
    satir += [f"- {a['metin']}" for a in alarmlar]
    satir += ["", "Ne yapmalı: sunucu ayakta mı (site ve giriş açılıyor mu), sonra ilgili işin günlüğü: "
              "`sudo journalctl -u <iş>.service -n 50` (sunucuda). Ayrıntılı tablo: " + run_url,
              "", "Bu issue her şey düzelince kendiliğinden kapanır. Sorun sürerse 24 saatte bir hatırlatılır."]
    if anma:
        satir += ["", "Bildirim: " + " ".join(anma) + " " + IM_ANMA]
    anahtar = ",".join(sorted(a["anahtar"] for a in alarmlar))
    satir += ["", f"<!-- {IM_ANAHTAR}: {anahtar} -->"]
    return "\n".join(satir)


def uyar(gh, alarmlar, simdi, run_url, anma):
    """Issue kararını uygular; yapılanı döner (ac | yorum-yeni | hatirlat | guncelle | kapat | yok)."""
    acik = gh.acik_alarm()
    eski = anahtarlar_govdeden(acik["body"]) if acik else set()
    yeni = {a["anahtar"] for a in alarmlar}
    son = gh.son_anma(acik) if acik else 0
    k = karar(bool(acik), eski, yeni, son, simdi)
    alarmlar = sorted(alarmlar, key=lambda a: a["anahtar"])
    anma_metni = (" ".join(anma) + " " + IM_ANMA) if anma else IM_ANMA
    if k == "ac":
        i = gh.ac(BASLIK_ALARM, alarm_govdesi(alarmlar, simdi, run_url, anma))
        print(f"alarm issue'su açıldı: #{i['number']}")
    elif k in ("yorum-yeni", "hatirlat"):
        gh.duzenle(acik["number"], body=alarm_govdesi(alarmlar, simdi, run_url, []))
        bas = "Yeni sorun eklendi" if k == "yorum-yeni" else "Sorun 24 saattir sürüyor (hatırlatma)"
        liste = "\n".join(f"- {a['metin']}" for a in alarmlar)
        gh.yorum(acik["number"], f"{bas}, {saat(simdi)}:\n\n{liste}\n\n{anma_metni}")
        print(f"#{acik['number']}: {k}")
    elif k == "guncelle":
        gh.duzenle(acik["number"], body=alarm_govdesi(alarmlar, simdi, run_url, []))
        print(f"#{acik['number']}: güncellendi (bazı sorunlar düzeldi, yeni yok)")
    elif k == "kapat":
        gh.yorum(acik["number"], f"Hepsi düzeldi, {saat(simdi)} (İstanbul). Koşu: {run_url}\n\n{anma_metni}")
        gh.duzenle(acik["number"], state="closed", state_reason="completed")
        print(f"#{acik['number']}: düzeldi, kapatıldı")
    return k


# ── Koşu ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def denetle(hizmetler, yapi, nabiz_url, get=http_get, tls=sertifika_gun, uyu=time.sleep, simdi=None,
            bakimda=frozenset()):
    """Bütün denetimler paralel, toplam ZAMAN["butce"] içinde. Dönüş: (alarmlar, sonuçlar{kimlik: sonuç}, sertifika,
    nabız satırları, bağlı olmayanlar, depo_hata)."""
    bitis = time.monotonic() + ZAMAN["butce"]
    hostlar = sorted({urllib.parse.urlsplit(h["adres"]).hostname for h in hizmetler}
                     | {urllib.parse.urlsplit(nabiz_url).hostname})
    havuz = cf.ThreadPoolExecutor(max_workers=len(hizmetler) + len(hostlar) + 1)
    f_http = [havuz.submit(denetle_hizmet, h, get, uyu, bitis) for h in hizmetler]
    f_tls = {host: havuz.submit(tls, host, 443, max(1, min(ZAMAN["tls_zaman_asimi"], kalan(bitis)))) for host in hostlar}
    f_nabiz = havuz.submit(nabiz_oku, nabiz_url, get, uyu, bitis)
    cf.wait(f_http + list(f_tls.values()) + [f_nabiz], timeout=max(0, kalan(bitis)) + 5)
    havuz.shutdown(wait=False, cancel_futures=True)

    alarmlar, sonuclar = [], {}
    for h, f in zip(hizmetler, f_http):
        r = (f.result() if f.done() and not f.cancelled()
             else {"kimlik": h["kimlik"], "ad": h["ad"], "d": "kesinti", "ms": None, "neden": "süre sınırı (yanıt yok)"})
        sonuclar[h["kimlik"]] = r
        if r["d"] == "kesinti" and h["kimlik"] not in bakimda:
            alarmlar.append({"anahtar": f"http:{h['kimlik']}", "metin": f"HTTP `{h['ad']}`: {r['neden']}"})

    sertifika, sinir = {}, float(yapi.get("sertifika_en_az_gun", 14))
    for host, f in f_tls.items():
        try:
            if not f.done() or f.cancelled():
                raise TimeoutError("süre sınırı")
            g = f.result()
            sertifika[host] = f"{g:.0f}"
            if g < sinir:
                alarmlar.append({"anahtar": f"tls:{host}", "metin": f"`{host}` TLS sertifikası {g:.0f} gün içinde bitiyor"})
        except Exception as e:
            sertifika[host] = "okunamadı (" + type(e).__name__ + ")"

    durum, depo_hata = (f_nabiz.result() if f_nabiz.done() and not f_nabiz.cancelled() else (None, "süre sınırı"))
    nabiz_satir, bagli_degil = [], []
    if durum is None:
        alarmlar.append({"anahtar": "nabiz-deposu", "metin": f"Nabız deposu ({nabiz_oku_url(nabiz_url)}) okunamadı: {depo_hata}"})
    else:
        depo_hata = ""
        n_alarm, nabiz_satir, bagli_degil = nabiz_degerlendir(durum, yapi, int(simdi or time.time()))
        alarmlar += n_alarm
    return alarmlar, sonuclar, sertifika, nabiz_satir, bagli_degil, depo_hata


def ozet_yaz(yol, hizmetler, sonuclar, sertifika, nabiz_satir, bagli_degil, alarmlar, depo_hata, yap_hatasi, yayin):
    if not yol:
        return
    s = ["## SKY LAB dış izleme", "", f"Alarm: **{len(alarmlar)}** · Sayfa yayını: {yayin}", ""]
    if yap_hatasi:
        s += [f"**hizmetler.yml geçersiz, son geçerli liste kullanıldı:** {yap_hatasi}", ""]
    s += ["### Hizmetler", "", "| hizmet | durum | ayrıntı |", "|---|---|---|"]
    for h in hizmetler:
        r = sonuclar.get(h["kimlik"]) or {}
        s.append(f"| {h['ad']}{'' if h['gorunur'] else ' (sayfada gizli)'} | {r.get('d', '—')} | {r.get('neden', '')} |")
    s += ["", "### Sertifikalar", "", "| ana makine | kalan gün |", "|---|---|"]
    s += [f"| {h} | {g} |" for h, g in sorted(sertifika.items())]
    s += ["", "### Nabız", ""]
    if depo_hata:
        s += [f"Nabız deposu okunamadı: {depo_hata}", ""]
    s += ["| iş | son başarı | son hata | durum |", "|---|---|---|---|"]
    s += [f"| {a} | {o} | {h} | {d} |" for a, o, h, d in nabiz_satir]
    if bagli_degil:
        s += ["", "Sunucuda bağlı olmayan (hiç kayıt yok, alarm üretmez): " + ", ".join(bagli_degil)]
    with open(yol, "a", encoding="utf-8") as f:
        f.write("\n".join(s) + "\n")


def cikti(ad, deger):
    yol = os.environ.get("GITHUB_OUTPUT")
    if yol:
        with open(yol, "a", encoding="utf-8") as f:
            f.write(f"{ad}={deger}\n")


def json_oku(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    kok = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(kok, "izleme.json"), encoding="utf-8") as f:
        yapi = json.load(f)
    nabiz_url = (os.environ.get("NABIZ_URL") or yapi["nabiz_url"]).rstrip("/")
    depo = os.environ["GITHUB_REPOSITORY"]
    gh = GitHub(depo, os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{depo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    anma, yanlis_kisi = kisiler(os.environ.get("UYARI_KISILERI"))
    yazarlar = {k[1:].lower() for k in anma if "/" not in k}
    deneme = os.environ.get("DENEME", "").lower() in ("true", "evet", "1")
    onceki_yol = os.environ.get("ONCEKI_DURUM") or os.path.join(kok, "veri", "durum.json")
    gecmis_okunamadi = os.environ.get("GECMIS_OKUNAMADI", "") == "1"
    onceki = None if gecmis_okunamadi else json_oku(onceki_yol)
    if onceki is None and not gecmis_okunamadi and os.path.exists(onceki_yol):
        print(f"önceki durum ({onceki_yol}) var ama okunamadı", file=sys.stderr)
        gecmis_okunamadi = True
    if gecmis_okunamadi:
        print("geçmiş okunamadı: denetim ve alarm yapılıyor; geçmiş silinmesin diye geçmiş ve sayfa yazılmayacak",
              file=sys.stderr)
    cikti_dir = os.environ.get("CIKTI_DIR") or os.path.join(kok, "cikti")

    t0 = time.monotonic()
    simdi = int(time.time())
    hizmetler, yap_hatasi = hizmetleri_yukle(os.path.join(kok, "hizmetler.yml"), onceki)
    olaylar = []
    try:
        for issue, yorumlar in gh.olay_issuelari(simdi, yazarlar):
            o = olay_ayristir(issue, yorumlar, hizmetler, simdi, yazarlar)
            if o:
                olaylar.append(o)
    except Exception as e:
        olaylar = list((onceki or {}).get("olaylar") or [])
        print(f"olay issue'ları okunamadı ({type(e).__name__}); öncekiler korunuyor", file=sys.stderr)
    olaylar.sort(key=lambda x: x["bas"], reverse=True)
    alarmlar, sonuclar, sertifika, nabiz_satir, bagli_degil, depo_hata = denetle(
        hizmetler, yapi, nabiz_url, simdi=simdi, bakimda=bakimdakiler(olaylar, simdi))
    if yap_hatasi:
        alarmlar.append({"anahtar": "yapilandirma:hizmetler",
                         "metin": f"hizmetler.yml geçersiz, son geçerli liste kullanılıyor: {yap_hatasi}"})
    print(f"denetim süresi: {time.monotonic() - t0:.0f} sn")

    if gecmis_okunamadi:
        yayinla, yayin_metni = False, "hayır (geçmiş okunamadı)"
    else:
        yeni, yayinla, neden = durum_guncelle(onceki, hizmetler, sonuclar, olaylar, simdi, yap_hatasi)
        os.makedirs(cikti_dir, exist_ok=True)
        with open(os.path.join(cikti_dir, "durum.json"), "w", encoding="utf-8") as f:
            json.dump(yeni, f, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(cikti_dir, "sayfa.json"), "w", encoding="utf-8") as f:
            json.dump(yeni["sayfa"], f, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(cikti_dir, "olaylar.xml"), "w", encoding="utf-8") as f:
            f.write(rss(olaylar, simdi))
        yayin_metni = "evet (" + neden + ")" if yayinla else "hayır (değişiklik yok)"
    cikti("yayinla", "evet" if yayinla else "hayir")
    print(f"sayfa yayını: {yayin_metni}")

    ozet_yaz(os.environ.get("GITHUB_STEP_SUMMARY"), hizmetler, sonuclar, sertifika, nabiz_satir, bagli_degil, alarmlar,
             depo_hata, yap_hatasi, yayin_metni)
    for h in hizmetler:
        r = sonuclar.get(h["kimlik"]) or {}
        print(f"hizmet {h['kimlik']}: {r.get('d')} {r.get('neden')}")
    for a, o, hh, d in nabiz_satir:
        print(f"nabiz {a}: {d}")
    print(f"alarm sayısı: {len(alarmlar)}")
    if yanlis_kisi:
        print("UYARI_KISILERI'nde tanınmayan: " + " ".join(yanlis_kisi), file=sys.stderr)
    if not anma:
        print("UYARI: UYARI_KISILERI boş; issue açılır ama kimse anılmaz", file=sys.stderr)

    if deneme:
        govde = ("Bu bir DENEMEDİR, gerçek alarm değil. Dış izlemenin uyarı yolu (GitHub issue → e-posta) çalışıyor mu "
                 f"diye açıldı ve hemen kapatılır. Koşu: {run_url}")
        if anma:
            govde += "\n\nBildirim: " + " ".join(anma) + " " + IM_ANMA
        i = gh.ac(BASLIK_DENEME, govde)
        gh.duzenle(i["number"], state="closed", state_reason="completed")
        print(f"deneme issue'su açıldı ve kapatıldı: #{i['number']}")
        cikti("deneme_issue", i["number"])

    uyar(gh, alarmlar, simdi, run_url, anma)

    try:
        if simdi - gh.son_commit_epoch() > CANLILIK_GUN * 86400:
            cikti("canlilik", "evet")
    except Exception as e:
        print(f"son commit okunamadı: {type(e).__name__}", file=sys.stderr)
    if gecmis_okunamadi:
        print("::error::veri dalı okunamadı: denetim ve alarm yapıldı, geçmiş ve sayfa yazılmadı; iş kırmızı bitiyor")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
