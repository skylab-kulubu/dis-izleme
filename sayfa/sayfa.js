/* SKY LAB Durum sayfası. Veri: /sayfa.json (izle.py üretir; hizmetler.yml'deki görünür hizmetler, son 90 gün, olaylar).
 * Saf işlevler (HTML üretimi, tarih, ipucu konumu) DOM'suz; Node'da sınanır (test/sayfa-test.mjs). Bütün metinler kaçışlanır:
 * hizmet adları ve olay metinleri depodan (issue'lardan) gelir, sayfaya HTML olarak girmez. */
(function (kok) {
  'use strict';

  var GUN_SAYISI = 90;
  var AYLAR = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
  var AY_KISA = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
  var GUNLER = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'];
  var DURUM = {
    ok:      { ad: 'Çalışıyor', sinif: 'ok',      simge: 's-ok' },
    yavas:   { ad: 'Yavaş',     sinif: 'yavas',   simge: 's-yavas' },
    kesinti: { ad: 'Kesinti',   sinif: 'kesinti', simge: 's-kesinti' },
    bakim:   { ad: 'Bakımda',   sinif: 'bakim-r', simge: 's-bakim' },
    veriyok: { ad: 'Veri yok',  sinif: 'veriyok', simge: 's-veriyok' }
  };
  var OLAY_DURUM = {
    suruyor:    { ad: 'Sürüyor',    sinif: 'kesinti', simge: 's-kesinti' },
    cozuldu:    { ad: 'Çözüldü',    sinif: 'ok',      simge: 's-ok' },
    planlandi:  { ad: 'Planlandı',  sinif: 'bakim-r', simge: 's-bakim' },
    tamamlandi: { ad: 'Tamamlandı', sinif: 'ok',      simge: 's-ok' }
  };

  function kac(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function paragraf(s) { return kac(s).replace(/\r?\n/g, '<br>'); }
  // Türkiye 2016'dan beri yıl boyu UTC+3: İstanbul saati = UTC + 3 sa.
  function ist(t) { return new Date((t + 3 * 3600) * 1000); }
  function iki(n) { return (n < 10 ? '0' : '') + n; }
  function saatDk(t) { var d = ist(t); return iki(d.getUTCHours()) + ':' + iki(d.getUTCMinutes()); }
  function kisaGun(t) { var d = ist(t); return d.getUTCDate() + ' ' + AY_KISA[d.getUTCMonth()]; }
  function uzunTarih(gun) {
    var d = new Date(gun + 'T12:00:00Z');
    return d.getUTCDate() + ' ' + AYLAR[d.getUTCMonth()] + ' ' + d.getUTCFullYear() + ', ' + GUNLER[d.getUTCDay()];
  }
  function isoIst(t) { var d = ist(t); return d.toISOString().slice(0, 16) + '+03:00'; }
  function yuzde(y) { return y == null ? '—' : '%' + (y >= 99.995 ? '100' : y.toFixed(2).replace('.', ',')); }
  // Dar ekranda çubuk son 30 günü gösterir; yüzde de o 30 günün (izle.py'deki 90 günlük gibi günlük yüzdelerin ortalaması).
  function ortalama(gunler) {
    var y = gunler.filter(function (g) { return g.y != null; });
    return y.length ? Math.round(1000 * y.reduce(function (t, g) { return t + g.y; }, 0) / y.length) / 1000 : null;
  }
  function simge(id) { return '<svg aria-hidden="true"><use href="#' + id + '"/></svg>'; }
  function birlestir(l) { return l.length < 2 ? l.join('') : l.slice(0, -1).join(', ') + ' ve ' + l[l.length - 1]; }

  function hizmetHtml(h) {
    var D = DURUM[h.simdi] || DURUM.veriyok;
    var sorunlu = { kesinti: [], yavas: [], bakim: [] };
    h.gunler.forEach(function (g) { if (sorunlu[g.d]) sorunlu[g.d].push(g.g); });
    function adlar(l) { return l.map(function (g) { var d = new Date(g + 'T12:00:00Z'); return d.getUTCDate() + ' ' + AY_KISA[d.getUTCMonth()]; }).join(', '); }
    var p = [];
    if (sorunlu.kesinti.length) p.push('Kesinti: ' + adlar(sorunlu.kesinti));
    if (sorunlu.yavas.length) p.push('Yavaş: ' + adlar(sorunlu.yavas));
    if (sorunlu.bakim.length) p.push('Bakım: ' + adlar(sorunlu.bakim));
    var id = kac(h.kimlik);
    return '<li class="hizmet" data-kimlik="' + id + '">' +
      '<div class="hizmet-bas">' +
        '<h3 id="ad-' + id + '">' + kac(h.ad) + (h.aciklama ? '<small>' + kac(h.aciklama) + '</small>' : '') + '</h3>' +
        '<span class="etiket ' + D.sinif + '">' + simge(D.simge) + D.ad + '</span>' +
      '</div>' +
      '<div class="cubuk" id="cubuk-' + id + '" tabindex="0" role="group" ' +
        'aria-label="' + kac(h.ad) + ', son 90 gün. Sol/sağ ok: gün" aria-describedby="oz-' + id + '">' +
        h.gunler.map(function (g, i) { return '<span class="gun" data-d="' + kac(g.d) + '" data-i="' + i + '"></span>'; }).join('') +
      '</div>' +
      '<div class="cubuk-alt"><span><span class="kisa90">90 gün önce</span><span class="kisa30">30 gün önce</span></span>' +
        '<span class="yuzde"><span class="kisa90">' + yuzde(h.yuzde) + '</span><span class="kisa30">' + yuzde(ortalama(h.gunler.slice(-30))) +
        '</span> çalışma süresi</span><span>Bugün</span></div>' +
      '<p class="gizli" id="oz-' + id + '">' + (p.length ? p.join('. ') : 'Sorun yok') + '</p>' +
    '</li>';
  }

  // Gruplar en küçük sıralarına göre, içleri sıraya göre (izle.py zaten sıralı verir).
  function gruplar(hizmetler) {
    var sira = [], gr = {};
    hizmetler.forEach(function (h) {
      if (!gr[h.grup]) { gr[h.grup] = []; sira.push(h.grup); }
      gr[h.grup].push(h);
    });
    return sira.map(function (g) { return { ad: g, hizmetler: gr[g] }; });
  }

  function listeHtml(hizmetler) {
    if (!hizmetler.length) return '<p class="bekle">Hizmet yok</p>';
    var g = gruplar(hizmetler);
    return g.map(function (x) {
      return '<div class="grup">' + (g.length > 1 ? '<h3 class="grup-ad">' + kac(x.ad) + '</h3>' : '') +
        '<ul class="hizmetler">' + x.hizmetler.map(hizmetHtml).join('') + '</ul></div>';
    }).join('');
  }

  function genelDurum(hizmetler, olaylar) {
    function adlar(d) { return hizmetler.filter(function (h) { return h.simdi === d; }).map(function (h) { return h.ad; }); }
    var k = adlar('kesinti'), y = adlar('yavas'), b = adlar('bakim');
    var suren = olaylar.filter(function (o) { return o.tur === 'olay' && o.durum === 'suruyor'; });
    var c = [], ust = 'ok';
    if (k.length) { c.push(birlestir(k) + ' çalışmıyor'); ust = 'kesinti'; }
    else if (suren.length) { c.push('Süren olay: ' + suren[0].baslik); ust = 'kesinti'; }
    if (y.length) { c.push(birlestir(y) + ' yavaş'); if (ust === 'ok') ust = 'yavas'; }
    if (b.length) { c.push(birlestir(b) + ' bakımda'); if (ust === 'ok') ust = 'bakim'; }
    if (!c.length) c.push(hizmetler.length ? 'Tüm sistemler çalışıyor' : 'Veri yok');
    return { durum: ust, metin: c.join(' · ') };
  }

  function olayHtml(o) {
    var D = OLAY_DURUM[o.durum] || OLAY_DURUM.cozuldu;
    if (o.tur === 'bakim' && o.durum === 'suruyor') D = { ad: 'Sürüyor', sinif: 'bakim-r', simge: 's-bakim' };
    var zaman = '<time datetime="' + isoIst(o.bas) + '">' + kisaGun(o.bas) + ' ' + saatDk(o.bas) + '</time>';
    if (o.son) {
      var ayniGun = kisaGun(o.son) === kisaGun(o.bas);
      zaman += ' – <time datetime="' + isoIst(o.son) + '">' + (ayniGun ? '' : kisaGun(o.son) + ' ') + saatDk(o.son) + '</time>';
    }
    zaman += ' (İstanbul)';
    if (o.son && o.tur === 'olay') zaman += ' · ' + Math.max(1, Math.round((o.son - o.bas) / 60)) + ' dakika';
    return '<article class="kart olay" id="olay-' + (+o.no) + '">' +
      '<div class="bakim-bas"><h3>' + kac(o.baslik) + '</h3>' +
      '<span class="etiket ' + D.sinif + '">' + simge(D.simge) + D.ad + '</span></div>' +
      '<p class="meta">' + zaman + '</p>' +
      (o.hizmet_adlari && o.hizmet_adlari.length ? '<p class="etkilenen">Etkilenen: ' + kac(o.hizmet_adlari.join(', ')) + '</p>' : '') +
      '<ol class="cizelge">' + o.adimlar.map(function (a) {
        return '<li><span class="asama">' + kac(a.asama) + '</span><time datetime="' + isoIst(a.t) + '">' + kisaGun(a.t) + ' ' + saatDk(a.t) +
          '</time>' + (a.m ? '<p>' + paragraf(a.m) + '</p>' : '') + '</li>';
      }).join('') + '</ol></article>';
  }

  // Son 90 günün ayları (yeniden eskiye); her ayda o ay başlamış olay ve biten bakımlar.
  function gecmisHtml(olaylar, simdi) {
    var gecmis = olaylar.filter(function (o) { return o.durum === 'cozuldu' || o.durum === 'tamamlandi'; });
    var d = ist(simdi), y = d.getUTCFullYear(), a = d.getUTCMonth();
    var bas = ist(simdi - (GUN_SAYISI - 1) * 86400);
    var out = '';
    while (y > bas.getUTCFullYear() || (y === bas.getUTCFullYear() && a >= bas.getUTCMonth())) {
      var bu = gecmis.filter(function (o) { var b = ist(o.bas); return b.getUTCFullYear() === y && b.getUTCMonth() === a; });
      out += '<div class="ay"><h3 class="ay-ad">' + AYLAR[a] + ' ' + y + '</h3>' +
        (bu.length ? bu.map(olayHtml).join('') : '<p class="bos">Olay yok</p>') + '</div>';
      a -= 1; if (a < 0) { a = 11; y -= 1; }
    }
    return out;
  }

  // İpucu konumu: kutunun üstü; satır başlığıyla (ad + durum etiketi) çakışıyorsa ya da ekranın üstüne taşıyorsa altı.
  // r: kutu, bas: satır başlığı, iw/ih: ipucu ölçüsü, vw: görünür genişlik, sx/sy: kaydırma. Hepsi viewport koordinatı.
  function ipucuKonum(r, bas, iw, ih, vw, sx, sy) {
    var x = r.left + r.width / 2 - iw / 2;
    x = Math.max(8, Math.min(x, vw - iw - 8));
    var ust = r.top - ih - 8;
    var cakisir = bas && x < bas.right && x + iw > bas.left && ust < bas.bottom && ust + ih > bas.top;
    var alta = ust < 0 || cakisir;
    var y = alta ? r.bottom + 8 : ust;
    return { x: x + sx, y: y + sy, alta: !!alta };
  }

  function ipucuMetni(h, g) {
    var D = DURUM[g.d] || DURUM.veriyok;
    return {
      html: '<div class="t">' + uzunTarih(g.g) + '</div>' +
        '<div class="e">' + simge(D.simge) + D.ad + (g.y != null ? ' · ' + yuzde(g.y) : '') + '</div>' +
        (g.n ? '<div class="n">' + kac(g.n) + '</div>' : ''),
      duyuru: h.ad + ', ' + uzunTarih(g.g) + ': ' + D.ad + (g.y != null ? ', ' + yuzde(g.y) : '') + (g.n ? '. ' + g.n : ''),
      sinif: D.sinif
    };
  }

  var api = { kac: kac, hizmetHtml: hizmetHtml, listeHtml: listeHtml, gruplar: gruplar, genelDurum: genelDurum, olayHtml: olayHtml,
              gecmisHtml: gecmisHtml, ortalama: ortalama, ipucuKonum: ipucuKonum, ipucuMetni: ipucuMetni, yuzde: yuzde, DURUM: DURUM };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  if (typeof document === 'undefined') return;

  /* ---------- Tema ---------- */
  var belge = document.documentElement;
  var temaBtn = document.getElementById('tema');
  function sistemKoyu() { return !kok.matchMedia || !kok.matchMedia('(prefers-color-scheme: light)').matches; }
  function etkinTema() { return belge.getAttribute('data-theme') || (sistemKoyu() ? 'dark' : 'light'); }
  function temaDugmesi() {
    var t = etkinTema();
    temaBtn.setAttribute('aria-label', t === 'dark' ? 'Açık tema' : 'Koyu tema');
  }
  try { var kayitli = localStorage.getItem('durum-tema'); if (kayitli === 'light' || kayitli === 'dark') belge.setAttribute('data-theme', kayitli); } catch (e) {}
  var adresTema = new URLSearchParams(location.search).get('tema');
  if (adresTema === 'light' || adresTema === 'dark') belge.setAttribute('data-theme', adresTema);
  temaDugmesi();
  temaBtn.addEventListener('click', function () {
    var yeni = etkinTema() === 'dark' ? 'light' : 'dark';
    belge.setAttribute('data-theme', yeni);
    try { localStorage.setItem('durum-tema', yeni); } catch (e) {}
    temaDugmesi();
  });

  /* ---------- Veri ---------- */
  var ipucu = document.getElementById('ipucu');
  var duyuru = document.getElementById('duyuru');
  var HIZMETLER = [];

  function goster(veri) {
    var simdi = Math.floor(Date.now() / 1000);
    HIZMETLER = veri.hizmetler || [];
    var olaylar = veri.olaylar || [];
    var g = genelDurum(HIZMETLER, olaylar);
    var genel = document.getElementById('genel');
    genel.className = 'genel ' + DURUM[g.durum].sinif;
    document.getElementById('genel-baslik').textContent = g.metin;
    document.getElementById('genel-simge').innerHTML = '<use href="#' + DURUM[g.durum].simge + '"/>';
    var eski = document.getElementById('eski');
    if (simdi - veri.guncelleme > 2 * 3600) {
      eski.hidden = false;
      eski.textContent = 'Son güncelleme ' + Math.round((simdi - veri.guncelleme) / 3600) + ' saat önce';
    }
    var suren = olaylar.filter(function (o) { return o.tur === 'olay' && o.durum === 'suruyor'; });
    document.getElementById('suren-bolum').hidden = !suren.length;
    document.getElementById('suren').innerHTML = suren.map(olayHtml).join('');
    var bakim = olaylar.filter(function (o) { return o.tur === 'bakim' && (o.durum === 'planlandi' || o.durum === 'suruyor'); });
    document.getElementById('bakim-bolum').hidden = !bakim.length;
    document.getElementById('bakimlar').innerHTML = bakim.map(olayHtml).join('');
    document.getElementById('liste').innerHTML = listeHtml(HIZMETLER);
    document.getElementById('gecmis').innerHTML = gecmisHtml(olaylar, simdi);
    baglaCubuklar();
    if (location.hash) { var hedef = document.getElementById(location.hash.slice(1)); if (hedef) hedef.scrollIntoView(); }
  }

  function hata(metin) {
    document.getElementById('genel-baslik').textContent = metin;
    document.getElementById('liste').innerHTML = '<p class="bekle">' + kac(metin) + '</p>';
  }

  /* ---------- İpucu: fare, dokunma ve klavye ---------- */
  function ipucuGoster(cubuk, i) {
    var li = cubuk.closest('.hizmet');
    var h = HIZMETLER.filter(function (x) { return x.kimlik === li.getAttribute('data-kimlik'); })[0];
    var g = h.gunler[i];
    var m = ipucuMetni(h, g);
    ipucu.className = 'ipucu ' + m.sinif;
    ipucu.innerHTML = m.html;
    ipucu.hidden = false;
    cubuk.querySelectorAll('.gun.secili').forEach(function (s) { s.classList.remove('secili'); });
    var kutu = cubuk.children[i];
    kutu.classList.add('secili');
    var k = ipucuKonum(kutu.getBoundingClientRect(), li.querySelector('.hizmet-bas').getBoundingClientRect(),
      ipucu.offsetWidth, ipucu.offsetHeight, document.documentElement.clientWidth, kok.scrollX, kok.scrollY);
    ipucu.style.left = k.x + 'px';
    ipucu.style.top = k.y + 'px';
    cubuk.dataset.secili = i;
    return m.duyuru;
  }
  function ipucuGizle(cubuk) {
    ipucu.hidden = true;
    if (cubuk) cubuk.querySelectorAll('.gun.secili').forEach(function (s) { s.classList.remove('secili'); });
  }
  function gorunenIlk(cubuk) {
    for (var i = 0; i < cubuk.children.length; i++) if (cubuk.children[i].offsetParent !== null) return i;
    return 0;
  }
  function baglaCubuklar() {
    document.querySelectorAll('.cubuk').forEach(function (cubuk) {
      var son = cubuk.children.length - 1;
      cubuk.addEventListener('pointerover', function (e) { if (e.target.classList.contains('gun')) ipucuGoster(cubuk, +e.target.dataset.i); });
      cubuk.addEventListener('pointerdown', function (e) { if (e.target.classList.contains('gun')) ipucuGoster(cubuk, +e.target.dataset.i); });
      cubuk.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse' && document.activeElement !== cubuk) ipucuGizle(cubuk); });
      cubuk.addEventListener('focus', function () { duyuru.textContent = ipucuGoster(cubuk, cubuk.dataset.secili ? +cubuk.dataset.secili : son); });
      cubuk.addEventListener('blur', function () { ipucuGizle(cubuk); });
      cubuk.addEventListener('keydown', function (e) {
        var i = cubuk.dataset.secili ? +cubuk.dataset.secili : son;
        var ilk = gorunenIlk(cubuk);
        if (e.key === 'ArrowLeft') i = Math.max(ilk, i - 1);
        else if (e.key === 'ArrowRight') i = Math.min(son, i + 1);
        else if (e.key === 'Home') i = ilk;
        else if (e.key === 'End') i = son;
        else if (e.key === 'Escape') { ipucuGizle(cubuk); return; }
        else return;
        e.preventDefault();
        duyuru.textContent = ipucuGoster(cubuk, i);
      });
    });
  }
  document.addEventListener('pointerdown', function (e) { if (!e.target.closest('.cubuk')) ipucuGizle(); });
  kok.addEventListener('resize', function () { ipucuGizle(); });

  fetch('/sayfa.json', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(goster)
    .catch(function () { hata('Durum alınamadı'); });
})(typeof window !== 'undefined' ? window : this);
