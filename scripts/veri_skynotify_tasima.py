#!/usr/bin/env python3
"""veri dalının skymail → skynotify kimlik taşıması (ADR 0091; tek seferlik). Çağıran: scripts/veri-skynotify-tasima.sh.

Bu dosya yalnız JSON'u dönüştürür ve karar verir; git ve gh işlerini kabuk betiği yapar. Koşu (depo kökünden):
  python3 -I -B scripts/veri_skynotify_tasima.py --klon DİZİN --kok DEPO_KÖKÜ --isaret "COMMIT KONUSU" [--geri] [--yaz]

DİZİN veri dalının bir klonudur (durum.json, sayfa.json, geçmişiyle). Taşınanlar: durum.json'da son/gecisler/ilk/ms
anahtarları, hizmetler listesindeki kayıt (kimlik, ad, /api/<ad>/ yolu), olaylardaki hizmet kimliği ve adı, sayfa özeti;
sayfa.json'da aynıları. Kesinti geçmişi olduğu gibi korunur. Olay başlıkları ve adımlardaki serbest metin geçmiştir,
yeniden yazılmaz. olaylar.xml'e dokunulmaz: izle.py her yayında issue'lardan yeniden üretir.

Haller (son satır):
  TASINACAK  HEAD'de eski kimlik var, yenisi yok: dönüştürülür.
  KAYIP      HEAD'de yalnız yeni kimlik var ve geçmişte bu taşımadan önce eski kimlikli bir commit var: izle.py taşımadan
             önce yeni hizmet listesiyle koşmuş, eski seriyi atmış. Eski seri o commit'ten alınıp yeniyle birleştirilir.
  TASINMIS   yapılacak bir şey yok (bu yöndeki taşıma commit'i geçmişte, ya da eski kimlik hiç yok).
  DUR        iki kimlik birden var ya da dosyalar okunamıyor.
Çıkış: 0 değişiklik var (--yaz ile yazıldı) · 3 yapılacak bir şey yok · 1 DUR · 2 kullanım.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ALANLAR = ("son", "gecisler", "ilk", "ms")
ADLAR = {"skymail": "SkyMail", "skynotify": "SkyNotify"}
ESKI_AD_RE = re.compile(r"sky[-_ .]?mail", re.I)


class Dur(Exception):
    pass


def yonler(geri):
    """(eski kimlik, yeni kimlik)."""
    return ("skynotify", "skymail") if geri else ("skymail", "skynotify")


def _anahtar_degistir(d, eski, yeni):
    """Sözlükte eski anahtarı yerinde (sırası korunarak) yeniye çevirir."""
    if not isinstance(d, dict) or eski not in d:
        return d
    if yeni in d:
        raise Dur(f"'{eski}' ve '{yeni}' aynı anda var")
    return {(yeni if k == eski else k): v for k, v in d.items()}


def _hizmet(h, eski, yeni):
    if not isinstance(h, dict) or h.get("kimlik") != eski:
        return h
    h = dict(h, kimlik=yeni)
    if h.get("ad") == ADLAR[eski]:
        h["ad"] = ADLAR[yeni]
    if isinstance(h.get("adres"), str):
        h["adres"] = h["adres"].replace(f"/api/{eski}/", f"/api/{yeni}/")
    return h


def _olay(o, eski, yeni):
    if not isinstance(o, dict):
        return o
    o = dict(o)
    if isinstance(o.get("hizmetler"), list):
        o["hizmetler"] = [yeni if k == eski else k for k in o["hizmetler"]]
    if isinstance(o.get("hizmet_adlari"), list):
        o["hizmet_adlari"] = [ADLAR[yeni] if a == ADLAR[eski] else a for a in o["hizmet_adlari"]]
    return o


def sayfa_tasi(sayfa, eski, yeni):
    s = dict(sayfa)
    hiz = [h for h in s.get("hizmetler") or [] if isinstance(h, dict)]
    if any(h.get("kimlik") == eski for h in hiz) and any(h.get("kimlik") == yeni for h in hiz):
        raise Dur(f"sayfada '{eski}' ve '{yeni}' aynı anda var")
    s["hizmetler"] = [_hizmet(h, eski, yeni) for h in s.get("hizmetler") or []]
    s["olaylar"] = [_olay(o, eski, yeni) for o in s.get("olaylar") or []]
    return s


def durum_tasi(durum, eski, yeni):
    d = dict(durum)
    for alan in ALANLAR:
        d[alan] = _anahtar_degistir(d.get(alan) or {}, eski, yeni)
    hiz = d.get("hizmetler") or []
    if any(h.get("kimlik") == eski for h in hiz) and any(h.get("kimlik") == yeni for h in hiz):
        raise Dur(f"hizmet listesinde '{eski}' ve '{yeni}' aynı anda var")
    d["hizmetler"] = [_hizmet(h, eski, yeni) for h in hiz]
    d["olaylar"] = [_olay(o, eski, yeni) for o in d.get("olaylar") or []]
    if isinstance(d.get("sayfa"), dict):
        d["sayfa"] = sayfa_tasi(d["sayfa"], eski, yeni)
    return d


def kimlik_var(durum, k):
    return any(k in (durum.get(a) or {}) for a in ALANLAR) or any(
        h.get("kimlik") == k for h in durum.get("hizmetler") or [] if isinstance(h, dict))


def hal(durum, eski, yeni):
    e, y = kimlik_var(durum, eski), kimlik_var(durum, yeni)
    if e and y:
        return "cakisma"
    return "tasinacak" if e else ("tasinmis" if y else "yok")


def _gecis_birlestir(*seriler):
    """Geçiş listeleri ([t, ok|kesinti]) zamana göre; art arda aynı durum tek kayıt."""
    hepsi = sorted((list(x) for s in seriler for x in s or []), key=lambda x: x[0])
    cikti = []
    for x in hepsi:
        if not cikti or cikti[-1][1] != x[1]:
            cikti.append(x)
    return cikti


def birlestir(eski_tasinmis, simdi, kim):
    """KAYIP hali: eski commit'in (taşınmış) serisi + izle.py'nin sonradan sıfırdan başlattığı seri."""
    d = dict(simdi)
    for alan in ALANLAR:
        d[alan] = dict(d.get(alan) or {})
    e = {alan: (eski_tasinmis.get(alan) or {}).get(kim) for alan in ALANLAR}
    if e["ilk"] is not None:
        d["ilk"][kim] = min(e["ilk"], d["ilk"].get(kim, e["ilk"]))
    d["gecisler"][kim] = _gecis_birlestir(e["gecisler"], d["gecisler"].get(kim))
    ms = {g: list(v) for g, v in (e["ms"] or {}).items()}
    for g, v in (d["ms"].get(kim) or {}).items():
        o = ms.get(g) or [0, 0]
        ms[g] = [o[0] + v[0], o[1] + v[1]]
    d["ms"][kim] = dict(sorted(ms.items()))
    if kim not in d["son"] and e["son"] is not None:
        d["son"][kim] = e["son"]
    return d


def _git(klon, *a):
    return subprocess.run(["git", "-C", klon, *a], check=True, capture_output=True, text=True).stdout


def gecmis_tara(klon, eski, isaret):
    """HEAD'den geriye: önce bu yöndeki taşıma commit'i gelirse (None, None); önce eski kimlikli durum.json gelirse
    (commit, durum). Hiçbiri yoksa (None, None)."""
    for satir in _git(klon, "log", "--format=%H%x09%s", "HEAD").splitlines():
        c, _, konu = satir.partition("\t")
        if konu == isaret:
            return None, None
        try:
            d = json.loads(_git(klon, "show", f"{c}:durum.json"))
        except (subprocess.CalledProcessError, ValueError):
            continue
        if isinstance(d, dict) and kimlik_var(d, eski):
            return c, d
    return None, None


def json_yaz(yol, veri):
    """izle.py'nin biçimi: ensure_ascii=False, ayraç (",", ":"), sonda satır sonu yok."""
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, separators=(",", ":"))


def json_oku(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise Dur(f"{os.path.basename(yol)} okunamadı ({type(e).__name__})")


def ozet(durum, kim):
    g = (durum.get("gecisler") or {}).get(kim) or []
    kes = sum(1 for x in g if x[1] == "kesinti")
    gun = len((durum.get("ms") or {}).get(kim) or {})
    return f"{len(g)} geçiş ({kes} kesinti başlangıcı), {gun} günlük yanıt süresi"


def fark_satirlari(a, b, ad, en_cok=40):
    import difflib
    sa = json.dumps(a, ensure_ascii=False, indent=1, sort_keys=False).splitlines()
    sb = json.dumps(b, ensure_ascii=False, indent=1, sort_keys=False).splitlines()
    fark = [s for s in difflib.unified_diff(sa, sb, f"{ad} (önce)", f"{ad} (sonra)", n=0, lineterm="")]
    return fark[:en_cok] + ([f"… ({len(fark) - en_cok} satır daha)"] if len(fark) > en_cok else []), len(fark)


def calistir(klon, kok, isaret, geri, yaz, cikis=print):
    eski, yeni = yonler(geri)
    sys.path.insert(0, kok)
    import izle  # noqa: E402  (depo kökünden; sayfa_verisi ve hizmet listesi doğrulaması)

    durum = json_oku(os.path.join(klon, "durum.json"))
    sayfa = json_oku(os.path.join(klon, "sayfa.json"))
    if not isinstance(durum, dict) or not isinstance(sayfa, dict):
        raise Dur("durum.json ya da sayfa.json nesne değil")
    h = hal(durum, eski, yeni)
    cikis(f"yön: {eski} → {yeni}; HEAD hali: {h}")
    if h == "cakisma":
        raise Dur(f"durum.json'da '{eski}' ve '{yeni}' aynı anda var; elle bakılmalı")
    kayip_commit = None
    if h == "tasinacak":
        cikis(f"önce: {eski}: {ozet(durum, eski)}")
        yeni_durum = durum_tasi(durum, eski, yeni)
        yeni_sayfa = sayfa_tasi(sayfa, eski, yeni)
        if sayfa == durum.get("sayfa") and yeni_sayfa != yeni_durum.get("sayfa"):
            raise Dur("sayfa.json ile durum.json'daki sayfa önce aynıydı, sonra ayrıştı")
    else:
        kayip_commit, eski_durum = gecmis_tara(klon, eski, isaret)
        if not kayip_commit:
            cikis(f"sonuç: yapılacak bir şey yok ({'taşınmış' if h == 'tasinmis' else 'iki kimlik de yok'})")
            return 3, "TASINMIS"
        if h != "tasinmis":
            raise Dur(f"HEAD'de iki kimlik de yok ama {kayip_commit[:12]} eski kimlikli; elle bakılmalı")
        cikis(f"KAYIP: izle.py taşımadan önce yeni listeyle koşmuş; eski seri {kayip_commit[:12]}'den alınıyor")
        cikis(f"eski ({kayip_commit[:12]}): {eski}: {ozet(eski_durum, eski)}; HEAD: {yeni}: {ozet(durum, yeni)}")
        yeni_durum = birlestir(durum_tasi(eski_durum, eski, yeni), durum, yeni)
        yeni_durum["sayfa"] = izle.sayfa_verisi(yeni_durum, int(yeni_durum.get("guncelleme") or 0))
        yeni_sayfa = yeni_durum["sayfa"]
    if kimlik_var(yeni_durum, eski) or any(x.get("kimlik") == eski for x in yeni_sayfa.get("hizmetler") or []):
        raise Dur(f"dönüşümden sonra '{eski}' kimliği kaldı")
    cikis(f"sonra: {yeni}: {ozet(yeni_durum, yeni)}")
    try:
        with open(os.path.join(kok, "hizmetler.yml"), encoding="utf-8") as f:
            liste = izle.hizmetler_dogrula(izle.hizmetler_ayristir(f.read()))
        ayni = liste == yeni_durum.get("hizmetler")
        cikis("hizmet listesi depodaki hizmetler.yml ile " + ("aynı (izle.py liste değişikliği görmez)" if ayni
                                                              else "farklı (izle.py ilk koşuda listeyi yeniler)"))
    except Exception as e:  # yalnız bilgi
        cikis(f"hizmetler.yml karşılaştırılamadı ({type(e).__name__})")
    serbest = len(ESKI_AD_RE.findall(json.dumps(yeni_durum, ensure_ascii=False))) if not geri else 0
    if serbest:
        cikis(f"bilgi: olay başlığı/adımlarında {serbest} serbest metin eski adı anıyor (geçmiş; yeniden yazılmaz)")
    for ad, a, b in (("durum.json", durum, yeni_durum), ("sayfa.json", sayfa, yeni_sayfa)):
        satirlar, n = fark_satirlari(a, b, ad)
        cikis(f"fark {ad}: {n} satır")
        for s in satirlar:
            cikis("  " + s)
    if yaz:
        json_yaz(os.path.join(klon, "durum.json"), yeni_durum)
        json_yaz(os.path.join(klon, "sayfa.json"), yeni_sayfa)
        cikis("yazıldı: durum.json, sayfa.json")
    return 0, ("KAYIP" if kayip_commit else "TASINACAK")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--klon", required=True)
    p.add_argument("--kok", required=True)
    p.add_argument("--isaret", required=True)
    p.add_argument("--geri", action="store_true")
    p.add_argument("--yaz", action="store_true")
    a = p.parse_args(argv)
    try:
        rc, durum = calistir(a.klon, a.kok, a.isaret, a.geri, a.yaz)
    except Dur as e:
        print(f"DUR: {e}")
        return 1
    print(f"HAL: {durum}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
