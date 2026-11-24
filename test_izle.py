#!/usr/bin/env python3
"""izle.py birim testleri (ağ yok). Koşu: python3 -I -B test_izle.py  (çıkış 0 = geçti)."""
import json
import os
import sys
import time
import unittest
import xml.dom.minidom

KOK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KOK)
import izle  # noqa: E402

SA = 3600
GUN = 86400
YAPI = json.load(open(os.path.join(KOK, "izleme.json"), encoding="utf-8"))
HIZ = izle.hizmetler_dogrula(izle.hizmetler_ayristir(open(os.path.join(KOK, "hizmetler.yml"), encoding="utf-8").read()))
T = 2_000_000_000  # 2033-05-18 06:33 İstanbul


def anahtarlar(alarmlar):
    return sorted(a["anahtar"] for a in alarmlar)


class Hizmetler(unittest.TestCase):
    def test_depodaki_dosya_gecerli(self):
        kimlikler = [h["kimlik"] for h in HIZ]
        self.assertEqual(kimlikler, ["site", "etkinlik", "kisa", "giris", "hesap", "panel", "skymail", "forms", "api"])
        giris = next(h for h in HIZ if h["kimlik"] == "giris")
        self.assertEqual(giris["metin"], '"issuer":"https://e.yildizskylab.com/realms/e-skylab"')
        self.assertTrue(all(h["adres"].startswith("https://") for h in HIZ))

    def test_varsayilanlar_ve_sira(self):
        h = izle.hizmetler_dogrula(izle.hizmetler_ayristir(
            "hizmetler:\n  - kimlik: b\n    ad: B\n    adres: https://b.ornek/\n    sira: 5\n"
            "  - kimlik: a  # yorum\n    ad: 'A: iki nokta'\n    adres: https://a.ornek/x\n    gorunur: hayır\n    beklenen: 204\n"))
        self.assertEqual([x["kimlik"] for x in h], ["b", "a"])
        a = h[1]
        self.assertEqual((a["ad"], a["gorunur"], a["beklenen"], a["yavas_ms"], a["grup"]), ("A: iki nokta", False, [204], 3000, "Hizmetler"))

    def hata(self, metin, parca):
        with self.assertRaises(izle.YapilandirmaHatasi) as c:
            izle.hizmetler_dogrula(izle.hizmetler_ayristir(metin))
        self.assertIn(parca, str(c.exception))

    def test_hatalar_satir_numarasiyla(self):
        self.hata("hizmetler:\n  - kimlik: a\n    ad: A\n    adrse: https://x/\n", "satır 4: bilinmeyen alan 'adrse'")
        self.hata("hizmetler:\n  - kimlik: a\n     ad: A\n", "satır 3: girinti")
        self.hata("hizmetler:\n  - kimlik: a\n    ad: A\n    adres: http://x/\n", "https://")
        self.hata("hizmetler:\n  - kimlik: a\n    ad: A\n    adres: https://x/\n  - kimlik: a\n    ad: B\n    adres: https://y/\n",
                  "iki kez")
        self.hata("hizmetler:\n  - kimlik: Buyuk\n    ad: A\n    adres: https://x/\n", "kimlik")
        self.hata("hizmetler:\n  - kimlik: a\n    ad: A\n    adres: https://x/\n    beklenen: [200, x]\n", "beklenen")
        self.hata("hizmetler:\n  - kimlik: a\n    ad: Bir: iki\n    adres: https://x/\n", "tek tırnağa al")
        self.hata("baska:\n", "hizmetler")
        self.hata("hizmetler:\n", "hiç hizmet")
        self.hata("hizmetler:\n\t- kimlik: a\n", "sekme")

    def test_bozuk_dosyada_son_gecerli_liste(self):
        yol = os.path.join(KOK, "__test_bozuk.yml")
        try:
            open(yol, "w").write("hizmetler:\n  - kimlik: a\n    ad: A\n    adrs: x\n")
            h, hata = izle.hizmetleri_yukle(yol, {"hizmetler": HIZ})
            self.assertEqual(h, HIZ)
            self.assertIn("satır 4", hata)
            h, hata = izle.hizmetleri_yukle(yol, None)
            self.assertEqual(h, [])
        finally:
            os.remove(yol)


class Nabiz(unittest.TestCase):
    def test_taze_basari_alarm_yok(self):
        d = {"isler": {"skylab-db-backup": {"ok": T - 2 * SA, "kayit": T - 30 * SA}}}
        a, s, _ = izle.nabiz_degerlendir(d, YAPI, T)
        self.assertEqual(a, [])

    def test_geciken_gunluk_is(self):
        a, _, _ = izle.nabiz_degerlendir({"isler": {"skylab-db-backup": {"ok": T - 29 * SA}}}, YAPI, T)
        self.assertEqual(anahtarlar(a), ["nabiz-gec:skylab-db-backup"])

    def test_gunluk_hata_hemen_alarm(self):
        d = {"isler": {"skylab-db-backup": {"ok": T - 25 * SA, "hata": T - 60, "hata_ilk": T - 60}}}
        self.assertEqual(anahtarlar(izle.nabiz_degerlendir(d, YAPI, T)[0]), ["nabiz-hata:skylab-db-backup"])

    def test_saatlik_tek_hata_beklenir(self):
        d = {"isler": {"skylab-erasure-alarm": {"ok": T - 70 * 60, "hata": T - 600, "hata_ilk": T - 600}}}
        a, s, _ = izle.nabiz_degerlendir(d, YAPI, T)
        self.assertEqual(a, [])
        self.assertIn("bekleniyor", s[0][3])

    def test_saatlik_hata_suruyor(self):
        d = {"isler": {"skylab-erasure-alarm": {"ok": T - 3 * SA, "hata": T - 300, "hata_ilk": T - 2 * SA}}}
        self.assertEqual(anahtarlar(izle.nabiz_degerlendir(d, YAPI, T)[0]), ["nabiz-hata:skylab-erasure-alarm"])

    def test_ayni_saniye_son_alani_belirler(self):
        d = {"isler": {"skylab-db-backup": {"ok": T - 60, "hata": T - 60, "hata_ilk": T - 60, "son": "hata"}}}
        self.assertEqual(anahtarlar(izle.nabiz_degerlendir(d, YAPI, T)[0]), ["nabiz-hata:skylab-db-backup"])
        d["isler"]["skylab-db-backup"]["son"] = "ok"
        self.assertEqual(izle.nabiz_degerlendir(d, YAPI, T)[0], [])

    def test_kayitli_ama_hic_basari_yok(self):
        d = {"isler": {"openbao-rotate": {"kayit": T - 30 * SA}, "skylab-db-backup": {"kayit": T - SA}}}
        self.assertEqual(anahtarlar(izle.nabiz_degerlendir(d, YAPI, T)[0]), ["nabiz-gec:openbao-rotate"])

    def test_bagli_olmayan_alarm_uretmez(self):
        a, _, bagli_degil = izle.nabiz_degerlendir({"isler": {}}, YAPI, T)
        self.assertEqual(a, [])
        self.assertIn("skylab-db-backup", bagli_degil)


class Karar(unittest.TestCase):
    def test_tablo(self):
        t = 10 * GUN
        self.assertEqual(izle.karar(False, set(), {"a"}, 0, t), "ac")
        self.assertEqual(izle.karar(False, set(), set(), 0, t), "yok")
        self.assertEqual(izle.karar(True, {"a"}, set(), t - 60, t), "kapat")
        self.assertEqual(izle.karar(True, {"a"}, {"a", "b"}, t - 60, t), "yorum-yeni")
        self.assertEqual(izle.karar(True, {"a"}, {"a"}, t - 60, t), "yok")
        self.assertEqual(izle.karar(True, {"a"}, {"a"}, t - GUN, t), "hatirlat")
        self.assertEqual(izle.karar(True, {"a", "b"}, {"a"}, t - 60, t), "guncelle")

    def test_anahtar_isaret_gidis_donus(self):
        al = [{"anahtar": "http:api", "metin": "x"}, {"anahtar": "nabiz-gec:openbao-rotate", "metin": "y"}]
        g = izle.alarm_govdesi(al, T, "https://ornek", ["@yustyy"])
        self.assertEqual(izle.anahtarlar_govdeden(g), {"http:api", "nabiz-gec:openbao-rotate"})
        self.assertIn("@yustyy", g)


class Http(unittest.TestCase):
    H = {"kimlik": "giris", "ad": "Giriş", "adres": "u", "beklenen": [200], "metin": '"issuer":"x"', "yavas_ms": 1000}

    def sahte(self, yanitlar):
        it = iter(yanitlar)
        return lambda url, zaman_asimi=None: next(it)

    def test_204_tamam(self):
        h = dict(self.H, beklenen=[204], metin="")
        r = izle.denetle_hizmet(h, get=self.sahte([(204, b"", {}, "")]), uyu=lambda s: None)
        self.assertEqual(r["d"], "ok")

    def test_metin_tutmuyor(self):
        r = izle.denetle_hizmet(self.H, get=self.sahte([(200, b'{"issuer":"y"}', {}, "")] * 3), uyu=lambda s: None)
        self.assertEqual(r["d"], "kesinti")
        self.assertIn("beklenen metin", r["neden"])

    def test_yavas(self):
        def get(url, zaman_asimi=None):
            time.sleep(0.02)
            return 200, b'{"issuer":"x"}', {}, ""
        r = izle.denetle_hizmet(dict(self.H, yavas_ms=1), get=get, uyu=lambda s: None)
        self.assertEqual(r["d"], "yavas")

    def test_ikinci_denemede_duzelir(self):
        r = izle.denetle_hizmet(dict(self.H, metin=""), get=self.sahte([(None, b"", {}, "TimeoutError"), (200, b"", {}, "")]),
                                uyu=lambda s: None)
        self.assertEqual(r["d"], "ok")

    def test_cloudflare_engeli_adlandirilir(self):
        r = izle.denetle_hizmet(self.H, get=self.sahte([(403, b"", {"cf-mitigated": "challenge"}, "")] * 3), uyu=lambda s: None)
        self.assertIn("Cloudflare engeli", r["neden"])


class SahteGH:
    def __init__(self, acik=None):
        self.acilan, self.yorumlar, self.duzenlenen, self._acik = [], [], [], acik

    def acik_alarm(self):
        return self._acik

    def son_anma(self, issue):
        return 0

    def ac(self, baslik, govde):
        self.acilan.append((baslik, govde))
        return {"number": 1}

    def yorum(self, no, govde):
        self.yorumlar.append((no, govde))

    def duzenle(self, no, **alan):
        self.duzenlenen.append((no, alan))


class Olcekli(unittest.TestCase):
    """Zamanları 1/100'e küçültür: 240 sn bütçe → 2,4 sn; 5 dk → 3 sn."""

    def setUp(self):
        self.eski = dict(izle.ZAMAN)
        for k in ("http_zaman_asimi", "http_ara", "tls_zaman_asimi", "butce"):
            izle.ZAMAN[k] = self.eski[k] / 100.0

    def tearDown(self):
        izle.ZAMAN.clear()
        izle.ZAMAN.update(self.eski)

    @staticmethod
    def kara_delik(url, zaman_asimi=None):  # sunucu tümden düşük: her bağlantı zaman aşımına gider
        time.sleep(zaman_asimi)
        return None, b"", {}, "TimeoutError: timed out"

    @staticmethod
    def tls_kara_delik(host, port=443, zaman_asimi=None):
        time.sleep(zaman_asimi)
        raise TimeoutError("timed out")

    def test_sunucu_dustu_5_dk_icinde_alarm_issuesu(self):
        t0 = time.monotonic()
        al, son, _, _, _, _ = izle.denetle(HIZ, YAPI, YAPI["nabiz_url"], get=self.kara_delik, tls=self.tls_kara_delik,
                                            uyu=time.sleep, simdi=T)
        gh = SahteGH()
        k = izle.uyar(gh, al, T, "https://ornek", ["@yustyy"])
        self.assertEqual(k, "ac")
        self.assertEqual(len(gh.acilan), 1)
        self.assertLess(time.monotonic() - t0, 3.0, "ölçekli 5 dk aşıldı")
        beklenen = {f"http:{h['kimlik']}" for h in HIZ} | {"nabiz-deposu"}
        self.assertTrue(beklenen <= {a["anahtar"] for a in al})
        self.assertIn("@yustyy", gh.acilan[0][1])

    def test_butce_yeniden_denemeyi_keser(self):
        t0 = time.monotonic()
        r = izle.denetle_hizmet(HIZ[0], get=self.kara_delik, uyu=time.sleep, bitis=time.monotonic() + 0.25)
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(r["d"], "kesinti")
        self.assertIn("süre sınırı", r["neden"])

    def test_paralel_toplam_tek_denetim_kadar(self):
        t0 = time.monotonic()
        izle.denetle(HIZ, YAPI, YAPI["nabiz_url"], get=self.kara_delik, tls=self.tls_kara_delik, uyu=time.sleep)
        self.assertLess(time.monotonic() - t0, 2.0)  # sıralı olsaydı 10 uç × 0,9 sn ≈ 9 sn

    def test_bakimdaki_hizmet_alarm_vermez(self):
        al, _, _, _, _, _ = izle.denetle(HIZ, YAPI, YAPI["nabiz_url"], get=self.kara_delik, tls=self.tls_kara_delik,
                                          uyu=time.sleep, bakimda={"skymail"})
        self.assertNotIn("http:skymail", {a["anahtar"] for a in al})
        self.assertIn("http:api", {a["anahtar"] for a in al})


class BotSuzgeci(unittest.TestCase):
    def gh(self, issues, comments):
        g = izle.GitHub("skylab-kulubu/dis-izleme", "x")
        g.istek = lambda yontem, yol, veri=None: comments if "/comments" in yol else issues
        return g

    def test_baskasinin_alarm_issuesu_sayilmaz(self):
        sahte = {"number": 5, "title": izle.BASLIK_ALARM, "user": {"login": "saldirgan"}, "body": "<!-- dis-izleme-anahtarlar: x -->"}
        self.assertIsNone(self.gh([sahte], []).acik_alarm())
        bot = dict(sahte, number=6, user={"login": izle.BOT})
        self.assertEqual(self.gh([sahte, bot], []).acik_alarm()["number"], 6)

    def test_baskasinin_anma_yorumu_hatirlatmayi_ertelemez(self):
        issue = {"number": 6, "created_at": "2026-01-01T00:00:00Z"}
        yorumlar = [{"user": {"login": "saldirgan"}, "created_at": "2026-06-01T00:00:00Z", "body": izle.IM_ANMA},
                    {"user": {"login": izle.BOT}, "created_at": "2026-02-01T00:00:00Z", "body": izle.IM_ANMA}]
        self.assertEqual(self.gh([], yorumlar).son_anma(issue), izle.iso_epoch("2026-02-01T00:00:00Z"))


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class Olaylar(unittest.TestCase):
    def issue(self, **k):
        d = {"number": 12, "title": "Girişte kesinti", "state": "open", "created_at": iso(T - 2 * SA), "closed_at": None,
             "user": {"login": "yustyy"}, "author_association": "MEMBER", "labels": [{"name": "olay"}],
             "body": "Hizmetler: Giriş (e-skylab), API\n\nGiriş sayfası açılmıyor."}
        d.update(k)
        return d

    def yorum(self, govde, t, login="yustyy", iliski="MEMBER"):
        return {"user": {"login": login}, "author_association": iliski, "created_at": iso(t), "body": govde}

    def test_olay_ve_adimlar(self):
        y = [self.yorum("Sebep bulundu: Veritabanı bağlantıları doldu.", T - SA),
             self.yorum("Çözüldü: Düzeldi <script>x</script>", T - 600),
             self.yorum("Çözüldü: SAHTE", T - 300, login="yabanci", iliski="NONE")]
        o = izle.olay_ayristir(self.issue(state="closed", closed_at=iso(T - 600)), y, HIZ, T, set())
        self.assertEqual(o["durum"], "cozuldu")
        self.assertEqual(o["hizmetler"], ["giris", "api"])
        self.assertEqual([a["asama"] for a in o["adimlar"]], ["Çözüldü", "Sebep bulundu", "İnceleniyor"])
        self.assertNotIn("SAHTE", json.dumps(o, ensure_ascii=False))
        self.assertEqual(o["adimlar"][-1]["m"], "Giriş sayfası açılmıyor.")
        self.assertEqual(o["son"], T - 600)

    def test_dis_kisinin_issuesu_sayilmaz(self):
        self.assertIsNone(izle.olay_ayristir(self.issue(user={"login": "yabanci"}, author_association="NONE"), [], HIZ, T, set()))
        self.assertIsNotNone(izle.olay_ayristir(self.issue(author_association="NONE"), [], HIZ, T, {"yustyy"}))
        self.assertIsNone(izle.olay_ayristir(self.issue(user={"login": izle.BOT}, author_association="NONE"), [], HIZ, T, set()))

    def test_bakim_durumlari(self):
        def bakim(bas, son, **k):
            b = f"Hizmetler: SkyMail\nBaşlangıç: {bas}\nBitiş: {son}\nPosta sunucusu güncellenecek."
            return self.issue(labels=[{"name": "bakim"}], body=b, title="SkyMail güncellemesi", **k)
        simdi = izle._ist_zaman("2026-11-24 17:00")
        o = izle.olay_ayristir(bakim("2026-11-24 16:00", "2026-11-24 18:00"), [], HIZ, simdi, set())
        self.assertEqual((o["tur"], o["durum"], o["hizmetler"]), ("bakim", "suruyor", ["skymail"]))
        self.assertEqual(izle.bakimdakiler([o], simdi), {"skymail"})
        o = izle.olay_ayristir(bakim("2026-11-25 16:00", "2026-11-25 18:00"), [], HIZ, simdi, set())
        self.assertEqual(o["durum"], "planlandi")
        self.assertEqual(o["adimlar"][-1]["asama"], "Planlandı")
        o = izle.olay_ayristir(bakim("2026-11-24 10:00", "2026-11-24 12:00"), [], HIZ, simdi, set())
        self.assertEqual(o["durum"], "tamamlandi")

    def test_sablon_govdesi(self):
        sablon = open(os.path.join(KOK, ".github", "ISSUE_TEMPLATE", "bakim.md"), encoding="utf-8").read().split("---", 2)[2]
        meta, aciklama = izle.govde_ayristir(sablon)
        self.assertEqual((meta["hizmetler"], meta["baslangic"], meta["bitis"]), ("SkyMail", "2026-12-01 16:00", "2026-12-01 18:00"))
        self.assertNotIn("Saatler", aciklama)
        self.assertTrue(aciklama.startswith("Ne yapılacak"))
        meta, aciklama = izle.govde_ayristir(open(os.path.join(KOK, ".github", "ISSUE_TEMPLATE", "olay.md"), encoding="utf-8").read().split("---", 2)[2])
        self.assertEqual(meta["hizmetler"], "Giriş (e-skylab)")
        self.assertNotIn("Adımlar", aciklama)

    def test_rss_gecerli_ve_kacisli(self):
        o = izle.olay_ayristir(self.issue(title="A & <b>B</b>"), [self.yorum("İzleniyor: <x> & y", T - 60)], HIZ, T, set())
        x = izle.rss([o], T)
        dom = xml.dom.minidom.parseString(x.encode())
        self.assertEqual(len(dom.getElementsByTagName("item")), 1)
        self.assertIn("&lt;b&gt;", x)
        self.assertIn("https://durum.yildizskylab.com/#olay-12", x)


class Gecmis(unittest.TestCase):
    def sonuc(self, d, ms=200):
        return {h["kimlik"]: {"kimlik": h["kimlik"], "ad": h["ad"], "d": d, "ms": None if d == "kesinti" else ms, "neden": ""}
                for h in HIZ}

    def test_ilk_kosu_yayinlanir_ve_veriyok_gunleri(self):
        d, yay, neden = izle.durum_guncelle(None, HIZ, self.sonuc("ok"), [], T, "")
        self.assertTrue(yay)
        self.assertIn("ilk koşu", neden)
        s = d["sayfa"]["hizmetler"][0]
        self.assertEqual(len(s["gunler"]), 90)
        self.assertEqual(s["gunler"][0]["d"], "veriyok")
        self.assertEqual(s["gunler"][-1]["d"], "ok")

    def test_degisiklik_yoksa_saatte_bir(self):
        d, _, _ = izle.durum_guncelle(None, HIZ, self.sonuc("ok"), [], T, "")
        _, yay, _ = izle.durum_guncelle(d, HIZ, self.sonuc("ok"), [], T + 900, "")
        self.assertFalse(yay)
        _, yay, neden = izle.durum_guncelle(d, HIZ, self.sonuc("ok"), [], T + 55 * 60, "")
        self.assertTrue(yay)
        self.assertIn("saatlik", neden)

    def test_kesinti_dakikasi_ve_yuzde(self):
        d0, _, _ = izle.durum_guncelle(None, HIZ, self.sonuc("ok"), [], T - 2 * GUN, "")
        d1, yay, neden = izle.durum_guncelle(d0, HIZ, self.sonuc("kesinti"), [], T - 3 * SA, "")
        self.assertTrue(yay)
        self.assertIn("api: ok → kesinti", neden)
        d2, _, _ = izle.durum_guncelle(d1, HIZ, self.sonuc("ok"), [], T - 3 * SA + 47 * 60, "")
        api = next(h for h in d2["sayfa"]["hizmetler"] if h["kimlik"] == "api")
        bugun = [g for g in api["gunler"] if g["k"]]
        self.assertEqual(sum(g["k"] for g in bugun), 47)
        self.assertTrue(all(g["d"] == "kesinti" for g in bugun))
        self.assertLess(api["yuzde"], 100)

    def test_bakim_suresi_kesinti_sayilmaz(self):
        bakim = {"no": 3, "tur": "bakim", "durum": "suruyor", "hizmetler": ["skymail"], "hizmet_adlari": ["SkyMail"],
                 "bas": T - 2 * SA, "son": T + SA, "guncelleme": T - 2 * SA, "baslik": "b", "adimlar": []}
        d0, _, _ = izle.durum_guncelle(None, HIZ, self.sonuc("ok"), [], T - 3 * SA, "")
        d1, _, _ = izle.durum_guncelle(d0, HIZ, self.sonuc("kesinti"), [bakim], T - SA, "")
        sm = next(h for h in d1["sayfa"]["hizmetler"] if h["kimlik"] == "skymail")
        self.assertEqual(sm["simdi"], "bakim")
        self.assertEqual(sm["gunler"][-1]["k"], 0)
        api = next(h for h in d1["sayfa"]["hizmetler"] if h["kimlik"] == "api")
        self.assertEqual(api["simdi"], "kesinti")

    def test_gizli_hizmet_sayfada_yok_ve_adres_yok(self):
        hiz = [dict(h, gorunur=(h["kimlik"] != "panel")) for h in HIZ]
        d, _, _ = izle.durum_guncelle(None, hiz, self.sonuc("ok"), [], T, "")
        kim = [h["kimlik"] for h in d["sayfa"]["hizmetler"]]
        self.assertNotIn("panel", kim)
        self.assertNotIn("adres", json.dumps(d["sayfa"]))

    def test_silinen_hizmetin_gecmisi_atilir(self):
        d, _, _ = izle.durum_guncelle(None, HIZ, self.sonuc("ok"), [], T, "")
        d2, yay, neden = izle.durum_guncelle(d, HIZ[:-1], self.sonuc("ok"), [], T + 60, "")
        self.assertNotIn("api", d2["son"])
        self.assertIn("hizmet listesi değişti", neden)

    def test_yavas_gun(self):
        d, _, _ = izle.durum_guncelle(None, HIZ, self.sonuc("yavas", ms=5000), [], T, "")
        api = next(h for h in d["sayfa"]["hizmetler"] if h["kimlik"] == "api")
        self.assertEqual((api["simdi"], api["gunler"][-1]["d"]), ("yavas", "yavas"))


class VeriOku(unittest.TestCase):
    """veri-oku.sh: dal yok → ilk koşu (0); okunamadı → 1 ve dosya yok (izle.yml GECMIS_OKUNAMADI=1 yapar)."""

    def kos(self, lsremote_rc, fetch_rc=0, icerik='{"surum":1}'):
        import shutil
        import subprocess
        import tempfile
        d = tempfile.mkdtemp(prefix="veri-oku-")
        try:
            b = os.path.join(d, "bin")
            os.makedirs(b)
            with open(os.path.join(b, "git"), "w") as f:
                f.write("#!/bin/bash\n"
                        'printf "%s\\n" "$*" >>"$GIT_KAYIT"\n'
                        f'case "$1" in ls-remote) exit {lsremote_rc} ;; fetch) exit {fetch_rc} ;;\n'
                        f"  show) printf '%s' {json.dumps(icerik)} ;; esac\n")
            os.chmod(os.path.join(b, "git"), 0o755)
            os.makedirs(os.path.join(d, "veri"))
            open(os.path.join(d, "veri", "durum.json"), "w").write("eski kalıntı")
            kayit = os.path.join(d, "git.log")
            env = dict(os.environ, PATH=b + os.pathsep + os.environ["PATH"], GIT_KAYIT=kayit)
            r = subprocess.run(["/bin/bash", os.path.join(KOK, "veri-oku.sh")], cwd=d, env=env, capture_output=True, text=True)
            self.git_kayit = open(kayit).read().splitlines()
            yol = os.path.join(d, "veri", "durum.json")
            return r.returncode, (open(yol).read() if os.path.exists(yol) else None), r.stdout
        finally:
            shutil.rmtree(d)

    def test_dal_yok_ilk_kosu(self):
        rc, dosya, out = self.kos(2)
        self.assertEqual((rc, dosya), (0, None))
        self.assertIn("ilk koşu", out)

    def test_ls_remote_kesin_eslesme(self):
        self.kos(2)
        self.assertEqual(self.git_kayit[0], "ls-remote --exit-code origin refs/heads/veri")

    def test_okunamadi_cikis_1_dosya_yok(self):
        for ls_rc, f_rc in ((128, 0), (1, 0), (0, 128)):
            rc, dosya, out = self.kos(ls_rc, f_rc)
            self.assertEqual((rc, dosya), (1, None), (ls_rc, f_rc))

    def test_bozuk_durum_json_cikis_1(self):
        rc, dosya, _ = self.kos(0, 0, "{bozuk")
        self.assertEqual((rc, dosya), (1, None))

    def test_okundu(self):
        rc, dosya, _ = self.kos(0, 0, '{"surum":1}')
        self.assertEqual((rc, json.loads(dosya)), (0, {"surum": 1}))

    def test_workflow_okunamayinca_isaret_koyar(self):
        yml = open(os.path.join(KOK, ".github", "workflows", "izle.yml"), encoding="utf-8").read()
        self.assertIn('bash veri-oku.sh || echo "GECMIS_OKUNAMADI=1" >>"$GITHUB_ENV"', yml)
        self.assertIn("GECMIS_OKUNAMADI: ${{ env.GECMIS_OKUNAMADI }}", yml)


class GecmisOkunamadi(unittest.TestCase):
    """Veri dalı okunamadı: main() denetler ve alarm issue'su açar; geçmiş/sayfa yazılmaz, yayinla=hayir, çıkış 1."""

    class GH(SahteGH):
        def olay_issuelari(self, simdi, yazarlar=frozenset()):
            return []

        def son_commit_epoch(self):
            return T

    def kos(self, ek_env, onceki_icerik=None):
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="izle-gecmis-")
        self.addCleanup(shutil.rmtree, d)
        onceki = os.path.join(d, "veri", "durum.json")
        if onceki_icerik is not None:
            os.makedirs(os.path.dirname(onceki))
            open(onceki, "w").write(onceki_icerik)
        cikis_yolu = os.path.join(d, "github_output")
        env = {"GITHUB_REPOSITORY": "skylab-kulubu/dis-izleme", "GITHUB_TOKEN": "x", "ONCEKI_DURUM": onceki,
               "CIKTI_DIR": os.path.join(d, "cikti"), "GITHUB_OUTPUT": cikis_yolu, "UYARI_KISILERI": "@yustyy",
               "GECMIS_OKUNAMADI": "", "DENEME": "", "GITHUB_STEP_SUMMARY": ""}
        env.update(ek_env)
        gh = self.GH()
        alarm = [{"anahtar": "http:api", "metin": "HTTP `API`: 502"}]
        eski = {"env": {k: os.environ.get(k) for k in env}, "GitHub": izle.GitHub, "denetle": izle.denetle}

        def geri():
            for k, v in eski["env"].items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            izle.GitHub, izle.denetle = eski["GitHub"], eski["denetle"]
        self.addCleanup(geri)
        os.environ.update(env)
        izle.GitHub = lambda *a, **k: gh
        izle.denetle = lambda hizmetler, *a, **k: (alarm, {h["kimlik"]: {"d": "ok", "ms": 100, "neden": ""}
                                                           for h in hizmetler}, {}, [], [], "")
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as hata:
            rc = izle.main()
        cikti = open(cikis_yolu).read() if os.path.exists(cikis_yolu) else ""
        return rc, gh, os.path.exists(os.path.join(d, "cikti")), cikti, hata.getvalue()

    def test_isaretle_alarm_acilir_yazilmaz(self):
        rc, gh, cikti_var, cikti, hata = self.kos({"GECMIS_OKUNAMADI": "1"}, '{"surum":1}')
        self.assertEqual(rc, 1)
        self.assertEqual(len(gh.acilan), 1)
        self.assertIn("http:api", gh.acilan[0][1])
        self.assertFalse(cikti_var, "geçmiş/sayfa yazılmamalı")
        self.assertIn("yayinla=hayir", cikti)
        self.assertNotIn("yayinla=evet", cikti)
        self.assertIn("geçmiş silinmesin", hata)

    def test_bozuk_onceki_durum_ayni_davranis(self):
        rc, gh, cikti_var, cikti, _ = self.kos({}, "{yarım")
        self.assertEqual((rc, len(gh.acilan), cikti_var), (1, 1, False))
        self.assertIn("yayinla=hayir", cikti)

    def test_dal_yok_ilk_kosu_yazar(self):
        rc, gh, cikti_var, cikti, _ = self.kos({})
        self.assertEqual((rc, len(gh.acilan), cikti_var), (0, 1, True))
        self.assertIn("yayinla=evet", cikti)


class OlayKotasi(unittest.TestCase):
    def test_yorumlar_yalniz_yetkili_issuelar_icin(self):
        g = izle.GitHub("skylab-kulubu/dis-izleme", "x")
        cagri = []
        issues = [{"number": 1, "comments": 3, "user": {"login": "yustyy"}, "author_association": "OWNER"},
                  {"number": 2, "comments": 50, "user": {"login": "yabanci"}, "author_association": "NONE"},
                  {"number": 3, "comments": 2, "user": {"login": "gonullu"}, "author_association": "NONE"}]

        def istek(yontem, yol, veri=None):
            cagri.append(yol)
            return [] if "/comments" in yol else (issues if "labels=olay" in yol else [])
        g.istek = istek
        sonuc = g.olay_issuelari(T, {"gonullu"})
        self.assertEqual([i["number"] for i, _ in sonuc], [1, 3])
        self.assertEqual(sorted(y for y in cagri if "/comments" in y),
                         ["/repos/skylab-kulubu/dis-izleme/issues/1/comments?per_page=100",
                          "/repos/skylab-kulubu/dis-izleme/issues/3/comments?per_page=100"])


class Kisiler(unittest.TestCase):
    def test_ayristir(self):
        ok, yanlis = izle.kisiler("@yustyy, @baska-kisi  yustyy @a@b")
        self.assertEqual(ok, ["@yustyy", "@baska-kisi"])
        self.assertEqual(yanlis, ["yustyy", "@a@b"])


class Yapilandirma(unittest.TestCase):
    def test_izleme_json(self):
        self.assertTrue(YAPI["nabiz_url"].startswith("https://"))
        self.assertNotIn("http", YAPI)
        for ad, v in YAPI["nabiz"].items():
            self.assertRegex(ad, r"^[a-z0-9][a-z0-9-]{0,62}$")
            self.assertNotIn("not", v)


if __name__ == "__main__":
    unittest.main(verbosity=1)
