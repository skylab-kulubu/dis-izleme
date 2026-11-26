#!/bin/bash
# veri-skynotify-tasima.sh: SkyMail → SkyNotify (ADR 0091) için "veri" dalının tek seferlik kimlik taşıması. durum.json ve
# sayfa.json'daki skymail kimliği skynotify olur; kesinti geçmişi ve günlük yanıt süreleri korunur (yoksa izle.py yeni
# kimliği sıfırdan başlatır, eskisini siler). Dönüşüm scripts/veri_skynotify_tasima.py'de; bu betik git ve gh işini yapar.
#
# Depo kökünden (taşıma penceresinin P8 adımında, hizmetler.yml'i skynotify yapan PR main'e birleştikten HEMEN sonra):
#   bash scripts/veri-skynotify-tasima.sh --kuru           salt okur: veri dalının farkını basar, yazmaz
#   bash scripts/veri-skynotify-tasima.sh --uygula         taşır; son satır "VERI: TAMAM"
#   bash scripts/veri-skynotify-tasima.sh --geri [--uygula]  geri alma (skynotify → skymail); --uygula'sız kuru koşu.
#                                                          Önce main'deki hizmetler.yml skymail'e dönmüş olmalı.
# --uygula sırası: izle iş akışı kapatılır (gh workflow disable; zaten kapalıysa dokunulmaz) → koşmakta olan izle koşuları
# biter (en çok 15 dk) → veri dalı çekilir, dönüştürülür, tek commit ile itilir (force yok) → yeniden okunup doğrulanır →
# iş akışı (bu betik kapattıysa) yeniden açılır. Böylece eski listeyle hesaplanmış bir koşu taşınmış veriyi ezemez. Taşımadan
# önce yeni listeyle bir koşu araya girip eski seriyi attıysa (KAYIP), eski seri veri dalı geçmişinden alınıp birleştirilir.
# Yeniden koşmak güvenli: taşınmışsa "yapılacak bir şey yok" der ve yine "VERI: TAMAM" ile biter.
#
# Gerekenler: git, python3, gh (yalnız --uygula; `gh auth status` geçerli, depoda Actions yazma yetkisi), veri dalına push
# yetkisi. Ortam (yalnız prova): DIS_IZLEME_GIT_URL (veri dalının adresi; varsayılan origin), DIS_IZLEME_DEPO (sahip/ad;
# varsayılan origin'den), DIS_IZLEME_BEKLE_SN (en çok bekleme, 900), DIS_IZLEME_ARALIK_SN (yoklama aralığı, 20).
# Çıkış: 0 tamam · 1 DUR (neden son satırdan önce) · 2 kullanım.
set -uo pipefail

D=$(cd "$(dirname "$0")" && pwd)
KOK=$(cd "$D/.." && pwd)
PY=$D/veri_skynotify_tasima.py
IS_AKISI=izle.yml
ISARET_ILERI='veri: skymail → skynotify kimlik taşıması (ADR 0091)'
ISARET_GERI='veri: skynotify → skymail kimlik geri taşıması (ADR 0091)'

KIP=''; GERI=''
for a in "$@"; do
  case $a in
    --kuru) KIP=${KIP:-kuru} ;;
    --uygula) KIP=uygula ;;
    --geri) GERI=1 ;;
    -h | --help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "kullanım: $0 --kuru | --uygula | --geri [--uygula]" >&2; exit 2 ;;
  esac
done
[[ -n $KIP || -n $GERI ]] || { echo "kullanım: $0 --kuru | --uygula | --geri [--uygula]" >&2; exit 2; }
KIP=${KIP:-kuru}
if [[ -n $GERI ]]; then ISARET=$ISARET_GERI; HEDEF=skymail; YON='skynotify → skymail'; PYGERI=--geri
else ISARET=$ISARET_ILERI; HEDEF=skynotify; YON='skymail → skynotify'; PYGERI=''; fi

T=''; ACILACAK=''
temizle() {
  if [[ -n $ACILACAK ]]; then
    if gh workflow enable "$IS_AKISI" -R "$DEPO" >/dev/null 2>&1; then echo "izle iş akışı yeniden açıldı"
    else echo "UYARI: izle iş akışı yeniden AÇILAMADI; elle aç: gh workflow enable $IS_AKISI -R $DEPO"; fi
    ACILACAK=''
  fi
  [[ -z $T ]] || rm -rf "$T"
}
trap temizle EXIT
dur() { echo "DUR: $*"; exit 1; }

command -v git >/dev/null || dur "git yok."
command -v python3 >/dev/null || dur "python3 yok."
[[ -f $PY && -f $KOK/izle.py ]] || dur "betik depo kökünde değil ($KOK)."
URL=${DIS_IZLEME_GIT_URL:-}
if [[ -z $URL ]]; then URL=$(git -C "$KOK" remote get-url origin 2>/dev/null) || dur "origin adresi okunamadı."; fi
DEPO=${DIS_IZLEME_DEPO:-}
if [[ -z $DEPO ]]; then
  DEPO=$(printf '%s\n' "$URL" | sed -E -n 's#^(https://[^/]*github\.com/|git@github\.com:)([^/]+/[^/]+)$#\2#p')
  DEPO=${DEPO%.git}
fi
echo "veri dalı taşıması ($YON), kip: $KIP, depo: ${DEPO:-?}"

# main'deki hizmet listesi hedef kimliği taşımıyorsa bu yön için erken: ilk izle koşusu taşınan seriyi yine atar.
MAIN_HIZ=$(git ls-remote "$URL" refs/heads/main 2>/dev/null | cut -f1)
[[ -n $MAIN_HIZ ]] || dur "main dalı okunamadı ($URL)."
T=$(mktemp -d "${TMPDIR:-/tmp}/veri-skynotify.XXXXXX") || dur "geçici dizin açılamadı."
git -C "$T" init -q m && git -C "$T/m" fetch -q --depth=1 "$URL" main 2>/dev/null || dur "main çekilemedi."
if git -C "$T/m" show FETCH_HEAD:hizmetler.yml 2>/dev/null | grep -qE "^[[:space:]]*-[[:space:]]*kimlik:[[:space:]]*$HEDEF[[:space:]]*$"; then
  echo "main ${MAIN_HIZ:0:12}: hizmetler.yml '$HEDEF' kimliğini taşıyor"
elif [[ $KIP == uygula ]]; then
  dur "main ${MAIN_HIZ:0:12}'de hizmetler.yml '$HEDEF' kimliğini taşımıyor: önce o PR birleşmeli (ileri: bilet 08 PR'ı; geri: onun geri alınışı)."
else
  echo "UYARI: main ${MAIN_HIZ:0:12}'de hizmetler.yml henüz '$HEDEF' kimliğini taşımıyor (--uygula bunu bekler)."
fi

if [[ $KIP == uygula ]]; then
  command -v gh >/dev/null || dur "gh yok."
  [[ -n $DEPO ]] || dur "depo adı origin'den çıkarılamadı ($URL); DIS_IZLEME_DEPO=sahip/ad ver."
  DURUM=$(gh workflow view "$IS_AKISI" -R "$DEPO" --json state -q .state 2>/dev/null) || dur "gh: $IS_AKISI okunamadı (gh auth status?)."
  if [[ $DURUM == active ]]; then
    gh workflow disable "$IS_AKISI" -R "$DEPO" >/dev/null 2>&1 || dur "gh: $IS_AKISI kapatılamadı."
    ACILACAK=1
    echo "izle iş akışı kapatıldı (taşıma sürerken yeni koşu başlamasın; sonunda açılır)"
  else
    echo "izle iş akışı zaten kapalı ($DURUM); bu betik açmaz"
  fi
  BEKLE=${DIS_IZLEME_BEKLE_SN:-900}; ARALIK=${DIS_IZLEME_ARALIK_SN:-20}; bas=$(date +%s)
  while :; do
    n=$(gh run list -R "$DEPO" -w "$IS_AKISI" -L 30 --json status -q '[.[] | select(.status != "completed")] | length' 2>/dev/null) \
      || dur "gh: izle koşuları okunamadı."
    [[ $n == 0 ]] && break
    (( $(date +%s) - bas < BEKLE )) || dur "$n izle koşusu ${BEKLE} sn'de bitmedi; veri dalına yazılmadı (iş akışı yeniden açılıyor)."
    echo "  $n izle koşusu bitmeyi bekliyor…"
    sleep "$ARALIK"
  done
  echo "koşmakta olan izle koşusu yok"
fi

git clone -q --single-branch --branch veri --depth 300 "$URL" "$T/v" 2>/dev/null || dur "veri dalı çekilemedi ($URL)."
ONCE=$(git -C "$T/v" rev-parse HEAD) || dur "veri HEAD okunamadı."
KONU=$(git -C "$T/v" log -1 --format=%s HEAD)
echo "veri dalı: ${ONCE:0:12} ($KONU)"
YAZ=''; [[ $KIP == uygula ]] && YAZ=--yaz
CIKTI=$(python3 -I -B "$PY" --klon "$T/v" --kok "$KOK" --isaret "$ISARET" $PYGERI $YAZ 2>&1); rc=$?
printf '%s\n' "$CIKTI" | sed 's/^/  /'
case $rc in
  0) ;;
  3) temizle
     if [[ $KIP == kuru ]]; then echo "VERI: KURU (yapılacak bir şey yok)"; else echo "VERI: TAMAM"; fi; exit 0 ;;
  *) NEDEN=$(printf '%s\n' "$CIKTI" | grep '^DUR:' | tail -n1); dur "dönüşüm durdu (çıkış $rc): ${NEDEN:-neden yok}" ;;
esac
HAL=$(printf '%s\n' "$CIKTI" | sed -n 's/^HAL: //p' | tail -n1)
if [[ $KIP == kuru ]]; then echo "VERI: KURU ($HAL; --uygula taşır)"; exit 0; fi

git -C "$T/v" add durum.json sayfa.json || dur "git add."
git -C "$T/v" diff --cached --quiet && dur "dönüşüm 'değişiklik var' dedi ama dosyalar aynı."
AD=$(git -C "$T/v" config user.name || true); EP=$(git -C "$T/v" config user.email || true)
[[ -n $AD ]] || AD='SKY LAB dis-izleme'; [[ -n $EP ]] || EP='noreply@yildizskylab.com'
git -C "$T/v" -c user.name="$AD" -c user.email="$EP" commit -q -m "$ISARET" || dur "commit atılamadı."
YENI=$(git -C "$T/v" rev-parse HEAD)
git -C "$T/v" push -q origin "HEAD:refs/heads/veri" 2>&1 | sed 's/^/  /'
UZAK=$(git ls-remote "$URL" refs/heads/veri 2>/dev/null | cut -f1)
[[ $UZAK == "$YENI" ]] || dur "veri dalı itilemedi ya da başkası yazdı (uzak ${UZAK:0:12}, bizim ${YENI:0:12}); iş akışı açılıyor, yeniden koş."
echo "veri dalı: ${ONCE:0:12} → ${YENI:0:12} ($HAL)"

# Doğrulama: taze klonda aynı dönüşüm "yapılacak bir şey yok" demeli.
git clone -q --single-branch --branch veri --depth 300 "$URL" "$T/d" 2>/dev/null || dur "doğrulama klonu çekilemedi."
python3 -I -B "$PY" --klon "$T/d" --kok "$KOK" --isaret "$ISARET" $PYGERI >/dev/null 2>&1; rc=$?
[[ $rc == 3 ]] || dur "doğrulama: taze klonda taşıma bitmemiş görünüyor (çıkış $rc)."
echo "doğrulama: taze klonda '$HEDEF' kimliği, taşınacak bir şey yok"
temizle
echo "VERI: TAMAM"
