#!/bin/bash
# veri-oku.sh: önceki durum.json'u "veri" dalından veri/durum.json'a alır (izle.yml çağırır; cwd = depo kökü).
# Dal YOKSA (ls-remote çıkış 2): ilk koşu, dosya yazılmaz, çıkış 0. Dal var ama OKUNAMADIYSA (ağ, kimlik, bozuk dosya):
# çıkış 1 ve dosya yok. izle.yml bunu GECMIS_OKUNAMADI=1 yapar: izle.py denetimleri ve alarmı yine yapar, ama geçmişe /
# veri dalına yazmaz ve sayfa yayınlanmaz (90 günlük geçmiş boş sanılıp silinmesin); iş sonunda kırmızı biter.
set -uo pipefail
mkdir -p veri
rm -f veri/durum.json
rc=0
git ls-remote --exit-code origin refs/heads/veri >/dev/null 2>&1 || rc=$?
if [[ $rc == 2 ]]; then
  echo "veri dalı yok: ilk koşu (geçmiş boş başlar)"
  exit 0
fi
if [[ $rc != 0 ]]; then
  echo "::error::veri dalı okunamadı (git ls-remote çıkış $rc); geçmiş ve sayfa bu koşuda yazılmayacak"
  exit 1
fi
if ! git fetch -q --depth=1 origin veri 2>/dev/null; then
  echo "::error::veri dalı var ama çekilemedi; geçmiş ve sayfa bu koşuda yazılmayacak"
  exit 1
fi
if ! git show FETCH_HEAD:durum.json >veri/durum.json 2>/dev/null || ! python3 -I -c 'import json,sys; json.load(open(sys.argv[1]))' veri/durum.json 2>/dev/null; then
  rm -f veri/durum.json
  echo "::error::veri dalındaki durum.json okunamadı ya da bozuk; geçmiş ve sayfa bu koşuda yazılmayacak"
  exit 1
fi
echo "önceki durum okundu ($(wc -c <veri/durum.json | tr -d ' ') bayt)"
