#!/usr/bin/env python3
"""veri-skynotify-tasima birim ve uçtan uca testleri (ağ yok; sahte origin = geçici bare depo, sahte gh).
Koşu (depo kökünden): python3 -I -B scripts/test_veri_skynotify_tasima.py  (çıkış 0 = geçti)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

BURASI = os.path.dirname(os.path.abspath(__file__))
KOK = os.path.dirname(BURASI)
sys.path.insert(0, KOK)
sys.path.insert(0, BURASI)
import izle  # noqa: E402
import veri_skynotify_tasima as vt  # noqa: E402

BETIK = os.path.join(BURASI, "veri-skynotify-tasima.sh")
YML_YENI = open(os.path.join(KOK, "hizmetler.yml"), encoding="utf-8").read()
YML_ESKI = (YML_YENI.replace("kimlik: skynotify", "kimlik: skymail").replace("ad: SkyNotify", "ad: SkyMail")
            .replace("/api/skynotify/", "/api/skymail/"))
HIZ_YENI = izle.hizmetler_dogrula(izle.hizmetler_ayristir(YML_YENI))
HIZ_ESKI = izle.hizmetler_dogrula(izle.hizmetler_ayristir(YML_ESKI))
T = 2_000_000_000
SA = 3600


def sonuc(hiz, d="ok", ms=300, kesik=()):
    return {h["kimlik"]: {"d": "kesinti" if h["kimlik"] in kesik else d,
                          "ms": None if h["kimlik"] in kesik else ms, "neden": ""} for h in hiz}


def eski_durum():
    """Eski listeyle üç günlük geçmiş: skymail dün 2 saat kesik, bugün bakımda bir olay."""
    bakim = {"no": 3, "tur": "bakim", "durum": "tamamlandi", "hizmetler": ["skymail", "api"],
             "hizmet_adlari": ["SkyMail", "API"], "bas": T - 30 * SA, "son": T - 29 * SA, "guncelleme": T - 29 * SA,
             "baslik": "SkyMail bakımı", "adimlar": [{"asama": "Planlandı", "t": T - 31 * SA, "m": "SkyMail güncellenir"}]}
    d, _, _ = izle.durum_guncelle(None, HIZ_ESKI, sonuc(HIZ_ESKI), [], T - 72 * SA, "")
    d, _, _ = izle.durum_guncelle(d, HIZ_ESKI, sonuc(HIZ_ESKI, kesik={"skymail"}), [bakim], T - 26 * SA, "")
    d, _, _ = izle.durum_guncelle(d, HIZ_ESKI, sonuc(HIZ_ESKI), [bakim], T - 24 * SA, "")
    d, _, _ = izle.durum_guncelle(d, HIZ_ESKI, sonuc(HIZ_ESKI, ms=500), [bakim], T - SA, "")
    return d


class Donusum(unittest.TestCase):
    def test_ileri_seriyi_korur_ve_listeyi_main_ile_esitler(self):
        d = eski_durum()
        y = vt.durum_tasi(d, "skymail", "skynotify")
        for alan in vt.ALANLAR:
            self.assertNotIn("skymail", y[alan])
            self.assertEqual(y[alan]["skynotify"], d[alan]["skymail"])
            self.assertEqual(list(y[alan]), [k if k != "skymail" else "skynotify" for k in d[alan]])  # sıra aynı
        self.assertEqual(y["hizmetler"], HIZ_YENI)  # izle.py "hizmet listesi değişti" demez
        o = y["olaylar"][0]
        self.assertEqual((o["hizmetler"], o["hizmet_adlari"]), (["skynotify", "api"], ["SkyNotify", "API"]))
        self.assertEqual((o["baslik"], o["adimlar"][0]["m"]), ("SkyMail bakımı", "SkyMail güncellenir"))  # geçmiş metin
        sn = next(h for h in y["sayfa"]["hizmetler"] if h["kimlik"] == "skynotify")
        sm = next(h for h in d["sayfa"]["hizmetler"] if h["kimlik"] == "skymail")
        self.assertEqual((sn["ad"], sn["gunler"], sn["yuzde"]), ("SkyNotify", sm["gunler"], sm["yuzde"]))
        self.assertLess(sn["yuzde"], 100)  # dünkü kesinti hâlâ sayılıyor
        self.assertEqual(vt.sayfa_tasi(d["sayfa"], "skymail", "skynotify"), y["sayfa"])

    def test_tasinmis_durumla_izle_ayni_seriyi_surdurur(self):
        y = vt.durum_tasi(eski_durum(), "skymail", "skynotify")
        z, yayin, neden = izle.durum_guncelle(y, HIZ_YENI, sonuc(HIZ_YENI), y["olaylar"], T, "")
        self.assertNotIn("hizmet listesi değişti", neden)
        self.assertEqual(z["ilk"]["skynotify"], y["ilk"]["skynotify"])
        self.assertEqual(z["gecisler"]["skynotify"][:len(y["gecisler"]["skynotify"])], y["gecisler"]["skynotify"])

    def test_geri_tam_tersidir(self):
        d = eski_durum()
        self.assertEqual(vt.durum_tasi(vt.durum_tasi(d, "skymail", "skynotify"), "skynotify", "skymail"), d)

    def test_haller_ve_cakisma(self):
        d = eski_durum()
        self.assertEqual(vt.hal(d, "skymail", "skynotify"), "tasinacak")
        y = vt.durum_tasi(d, "skymail", "skynotify")
        self.assertEqual(vt.hal(y, "skymail", "skynotify"), "tasinmis")
        ikisi = dict(d, son=dict(d["son"], skynotify={"d": "ok", "ms": 1, "t": T}))
        self.assertEqual(vt.hal(ikisi, "skymail", "skynotify"), "cakisma")
        with self.assertRaises(vt.Dur):
            vt.durum_tasi(ikisi, "skymail", "skynotify")

    def test_kayip_birlestirme(self):
        d = eski_durum()
        # izle.py taşımadan önce yeni listeyle koştu: skymail serisi atıldı, skynotify sıfırdan başladı
        z, _, _ = izle.durum_guncelle(d, HIZ_YENI, sonuc(HIZ_YENI, kesik={"skynotify"}), d["olaylar"], T, "")
        self.assertNotIn("skymail", z["gecisler"])
        self.assertEqual(z["ilk"]["skynotify"], T)
        b = vt.birlestir(vt.durum_tasi(d, "skymail", "skynotify"), z, "skynotify")
        self.assertEqual(b["ilk"]["skynotify"], d["ilk"]["skymail"])
        self.assertEqual(b["gecisler"]["skynotify"], d["gecisler"]["skymail"] + [[T, "kesinti"]])
        self.assertEqual(b["son"]["skynotify"], z["son"]["skynotify"])
        gun = izle.gun_anahtari(T - SA)
        self.assertEqual(b["ms"]["skynotify"][gun], d["ms"]["skymail"][gun])

    def test_gecis_birlestir_art_arda_ayni_durumu_teke_indirir(self):
        self.assertEqual(vt._gecis_birlestir([[1, "ok"], [5, "kesinti"]], [[9, "kesinti"], [12, "ok"]]),
                         [[1, "ok"], [5, "kesinti"], [12, "ok"]])


SAHTE_GH = r"""#!/bin/bash
# sahte gh: iş akışı durumu ve koşu sayısı dosyalarda; çağrılar günlüğe.
echo "$*" >>"$SAHTE_GH_DIR/cagri"
case "$1 $2" in
  "workflow view") cat "$SAHTE_GH_DIR/durum" ;;
  "workflow disable") echo disabled_manually >"$SAHTE_GH_DIR/durum" ;;
  "workflow enable") echo active >"$SAHTE_GH_DIR/durum" ;;
  "run list") n=$(cat "$SAHTE_GH_DIR/kosu"); echo "$n"; [ "$n" -gt 0 ] && echo $((n - 1)) >"$SAHTE_GH_DIR/kosu"; exit 0 ;;
  *) echo "sahte gh: bilinmeyen $*" >&2; exit 1 ;;
esac
"""


class UctanUca(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="veri-tasima-test.")
        self.git = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test", "GIT_CONFIG_GLOBAL": "/dev/null"}
        self.bare = os.path.join(self.t, "origin.git")
        self.sh("git", "init", "-q", "--bare", self.bare)
        self.url = "file://" + self.bare
        self.main_yaz(YML_YENI)
        self.veri_yaz(eski_durum(), "veri: 2026-11-25T10:00Z")
        gd = os.path.join(self.t, "gh")
        os.makedirs(os.path.join(gd, "bin"))
        with open(os.path.join(gd, "bin", "gh"), "w") as f:
            f.write(SAHTE_GH)
        os.chmod(os.path.join(gd, "bin", "gh"), stat.S_IRWXU)
        for ad, deger in (("durum", "active"), ("kosu", "2"), ("cagri", "")):
            with open(os.path.join(gd, ad), "w") as f:
                f.write(deger + ("\n" if deger else ""))
        self.gd = gd

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def sh(self, *a, cwd=None):
        return subprocess.run(a, cwd=cwd, env=self.git, check=True, capture_output=True, text=True).stdout

    def dal_yaz(self, dal, dosyalar, mesaj):
        w = tempfile.mkdtemp(dir=self.t)
        self.sh("git", "init", "-q", w)
        if self.sh("git", "ls-remote", self.url, f"refs/heads/{dal}"):
            self.sh("git", "fetch", "-q", self.url, dal, cwd=w)
            self.sh("git", "checkout", "-q", "-B", dal, "FETCH_HEAD", cwd=w)
        else:
            self.sh("git", "checkout", "-q", "--orphan", dal, cwd=w)
        for ad, icerik in dosyalar.items():
            with open(os.path.join(w, ad), "w", encoding="utf-8") as f:
                f.write(icerik)
        self.sh("git", "add", "-A", cwd=w)
        self.sh("git", "commit", "-q", "--allow-empty", "-m", mesaj, cwd=w)
        self.sh("git", "push", "-q", self.url, f"HEAD:refs/heads/{dal}", cwd=w)

    def main_yaz(self, yml):
        self.dal_yaz("main", {"hizmetler.yml": yml}, "main")

    def veri_yaz(self, durum, mesaj):
        j = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
        self.dal_yaz("veri", {"durum.json": j(durum), "sayfa.json": j(durum["sayfa"]), "olaylar.xml": "<rss/>",
                              "README.md": "veri\n"}, mesaj)

    def veri_oku(self):
        w = tempfile.mkdtemp(dir=self.t)
        self.sh("git", "clone", "-q", "--branch", "veri", self.url, w)
        d = json.load(open(os.path.join(w, "durum.json"), encoding="utf-8"))
        s = json.load(open(os.path.join(w, "sayfa.json"), encoding="utf-8"))
        konu = self.sh("git", "log", "-1", "--format=%s", cwd=w).strip()
        n = int(self.sh("git", "rev-list", "--count", "HEAD", cwd=w))
        return d, s, konu, n

    def kos(self, *a, bekle=30):
        env = {**self.git, "PATH": os.path.join(self.gd, "bin") + os.pathsep + os.environ["PATH"],
               "SAHTE_GH_DIR": self.gd, "DIS_IZLEME_GIT_URL": self.url, "DIS_IZLEME_DEPO": "ornek/dis-izleme",
               "DIS_IZLEME_ARALIK_SN": "0", "DIS_IZLEME_BEKLE_SN": str(bekle), "TMPDIR": self.t}
        p = subprocess.run(["/bin/bash", BETIK, *a], cwd=KOK, env=env, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def cagrilar(self):
        return open(os.path.join(self.gd, "cagri")).read()

    def test_kuru_yazmaz_uygula_tasir_ikincisi_bir_sey_yapmaz(self):
        d0, _, _, n0 = self.veri_oku()
        rc, out = self.kos("--kuru")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.rstrip().endswith("VERI: KURU (TASINACAK; --uygula taşır)"), out)
        self.assertIn("fark durum.json:", out)
        self.assertEqual(self.cagrilar(), "")  # kuru gh'ye dokunmaz
        self.assertEqual(self.veri_oku()[3], n0)

        rc, out = self.kos("--uygula")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.rstrip().splitlines()[-1], "VERI: TAMAM", out)
        d, s, konu, n = self.veri_oku()
        self.assertEqual((konu, n), (vt_isaret(False), n0 + 1))
        self.assertEqual(d, vt.durum_tasi(d0, "skymail", "skynotify"))
        self.assertEqual(s, d["sayfa"])
        c = self.cagrilar()
        self.assertLess(c.index("workflow disable"), c.index("run list"))
        self.assertTrue(c.rstrip().splitlines()[-1].startswith("workflow enable"), c)
        self.assertEqual(open(os.path.join(self.gd, "durum")).read().strip(), "active")

        rc, out = self.kos("--uygula")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.rstrip().splitlines()[-1], "VERI: TAMAM", out)
        self.assertIn("yapılacak bir şey yok", out)
        self.assertEqual(self.veri_oku()[3], n0 + 1)

    def test_izle_commitleri_tasinmis_sayilir(self):
        self.assertEqual(self.kos("--uygula")[0], 0)
        d, _, _, n = self.veri_oku()
        z, _, _ = izle.durum_guncelle(d, HIZ_YENI, sonuc(HIZ_YENI), d["olaylar"], T, "")
        self.veri_yaz(z, "veri: 2026-11-25T12:00Z")
        rc, out = self.kos("--uygula")
        self.assertEqual(rc, 0, out)
        self.assertIn("yapılacak bir şey yok", out)
        self.assertEqual(self.veri_oku()[3], n + 1)

    def test_main_birlesmeden_uygulamaz_ve_is_akisina_dokunmaz(self):
        self.main_yaz(YML_ESKI)
        rc, out = self.kos("--uygula")
        self.assertEqual(rc, 1, out)
        self.assertIn("önce o PR birleşmeli", out)
        self.assertEqual(self.cagrilar(), "")
        rc, out = self.kos("--kuru")
        self.assertEqual(rc, 0, out)
        self.assertIn("UYARI: main", out)

    def test_kayip_gecmis_birlestirilir(self):
        d0 = eski_durum()
        z, _, _ = izle.durum_guncelle(d0, HIZ_YENI, sonuc(HIZ_YENI), d0["olaylar"], T, "")
        self.veri_yaz(z, "veri: 2026-11-25T11:00Z")
        rc, out = self.kos("--uygula")
        self.assertEqual(rc, 0, out)
        self.assertIn("KAYIP", out)
        d, s, konu, _ = self.veri_oku()
        self.assertEqual(konu, vt_isaret(False))
        self.assertEqual(d["ilk"]["skynotify"], d0["ilk"]["skymail"])
        self.assertEqual(d["gecisler"]["skynotify"], d0["gecisler"]["skymail"])  # [T, ok] öncekiyle aynı durum
        self.assertEqual(s, d["sayfa"])

    def test_kosu_bitmezse_yazmaz_ve_is_akisini_acar(self):
        with open(os.path.join(self.gd, "kosu"), "w") as f:
            f.write("999999\n")
        _, _, _, n0 = self.veri_oku()
        rc, out = self.kos("--uygula", bekle=1)
        self.assertIn("bitmedi", out)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.veri_oku()[3], n0)
        self.assertEqual(open(os.path.join(self.gd, "durum")).read().strip(), "active")

    def test_geri_alma(self):
        self.assertEqual(self.kos("--uygula")[0], 0)
        rc, out = self.kos("--geri", "--uygula")
        self.assertEqual(rc, 1, out)  # main hâlâ skynotify: geri alınmaz
        self.main_yaz(YML_ESKI)
        rc, out = self.kos("--geri")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERI: KURU (TASINACAK", out)
        rc, out = self.kos("--geri", "--uygula")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.rstrip().splitlines()[-1], "VERI: TAMAM", out)
        d, _, konu, _ = self.veri_oku()
        self.assertEqual(konu, vt_isaret(True))
        self.assertEqual(d, eski_durum())


def vt_isaret(geri):
    satir = next(s for s in open(BETIK, encoding="utf-8") if s.startswith("ISARET_GERI=" if geri else "ISARET_ILERI="))
    return satir.split("=", 1)[1].strip().strip("'")


if __name__ == "__main__":
    unittest.main()
