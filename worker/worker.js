// SKY LAB dış izleme: durum sayfası + nabız deposu (tek Cloudflare Worker "skylab-durum", KV + statik dosyalar).
//
// Asıl ad status.yildizskylab.com (Yusuf 2026-11-24: ürün adresleri İngilizce; durum. ve nabiz. kalkar, yönlendirme yok):
//   /…                          durum sayfası. Statik dosyalar (sayfa/, yazı tipleri) ASSETS'ten; /sayfa.json ve /olaylar.xml
//                               depodaki "veri" dalından (GitHub Actions her durum değişiminde ve saatte bir yazar) 60 sn önbellekle.
//   POST /heartbeat/<iş>/<olay>  olay: ok | hata | kayit | sil      Authorization: Bearer <belirteç>   → 204 | 401 | 404
//   GET  /heartbeat              {"surum":1,"simdi":<epoch>,"isler":{"<iş>":{"ok":…,"hata":…,"hata_ilk":…,"kayit":…,"son":"ok"|"hata"}}}
// Geçiş (yalnız ESKI_ADLAR değişkeninde adı geçen ad yanıt verir; wizard'ın --eski-sil kipi değişkeni boşaltır ve iki adın
// rotasını kaldırır, en erken status. yayınından 24 saat sonra):
//   durum.yildizskylab.com   aynı durum sayfası (yönlendirme değil)
//   nabiz.yildizskylab.com   eski nabız uçları: POST /nabiz/<iş>/<olay>, GET /durum (aynı JSON), GET / düz metin
// Başka her ad 404.
//
// Sunucudaki systemd işleri her başarılı ya da başarısız koşudan sonra buraya tek bir POST atar. Bu Worker yalnız
// "hangi iş, en son ne zaman" bilgisini saklar ve herkese açık /heartbeat'te yayımlar. Değerlendirme ve uyarı burada
// yapılmaz: GitHub Actions (izle.py) /heartbeat'i okur, gecikeni ya da düşeni bulur, GitHub issue'su ile haber verir.
//
// Belirteç sunucuda üretilir ve sunucudan çıkmaz; burada yalnız SHA-256 özeti durur (NABIZ_OZET, sır değil).
// Kişisel veri yok: istek IP'si, gövde, başlık saklanmaz. Gövde hiç okunmaz.
// KV ücretsiz katmanı günde 1000 yazma: "ok" olayı, aynı iş için son "ok"tan 50 dakika geçmeden yeniden yazılmaz
// (5 ve 15 dakikalık işler saatte bire iner). "hata", "kayit", "sil" her zaman yazılır.
// Özet JSON'u KV listelemesi (ücretsizde günde 1000) caches.default ile 120 sn önbelleklenir; "kayit" ve "sil" o veri merkezindeki
// iki kopyayı da (status./heartbeat, nabiz./durum) siler (wizard --dogrula hemen görsün). Başka veri merkezinde en çok 120 sn eski.

const IS_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
const SAYFA_HOST = "status.yildizskylab.com";
const SINYAL_YOLU = "heartbeat";
const ESKI_SAYFA_HOST = "durum.yildizskylab.com";
const ESKI_NABIZ_HOST = "nabiz.yildizskylab.com";
const OZET_YENI = `https://${SAYFA_HOST}/${SINYAL_YOLU}`;
const OZET_ESKI = `https://${ESKI_NABIZ_HOST}/durum`;
// eskiAcik: geçiş adı ESKI_ADLAR'da (boşlukla ayrılmış) mı. Değişken boşsa eski adlar 404 (rota kalksa da kalmasa da).
const eskiAcik = (env, host) => String(env.ESKI_ADLAR || "").split(/\s+/).includes(host);
const VERI_DOSYALARI = {
  "/sayfa.json": "application/json; charset=utf-8",
  "/olaylar.xml": "application/rss+xml; charset=utf-8",
};
const GUVENLIK = {
  "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; " +
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "strict-transport-security": "max-age=31536000",
};
const OLAYLAR = new Set(["ok", "hata", "kayit", "sil"]);
const OK_SEYREK_SN = 3000;
const DURUM_ONBELLEK_SN = 120;
const onbellek = () => (typeof caches !== "undefined" ? caches.default : null);

function yanit(govde, durum, tur) {
  return new Response(govde, {
    status: durum,
    headers: { "content-type": tur || "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}

async function sha256hex(metin) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(metin));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function esit(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let f = 0;
  for (let i = 0; i < a.length; i++) f |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return f === 0;
}

async function yetkili(istek, env) {
  const ozet = String(env.NABIZ_OZET || "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(ozet)) return false;
  const b = istek.headers.get("authorization") || "";
  const m = /^Bearer ([A-Za-z0-9_-]{32,128})$/.exec(b);
  if (!m) return false;
  return esit(await sha256hex(m[1]), ozet);
}

// Bir işin kaydını değiştirir; KV'de değer boş, bilgi metadata'da (list tek çağrıda hepsini verir).
export function yeniKayit(eski, olay, simdi) {
  const k = { ...(eski || {}) };
  const hataSonda = k.son ? k.son === "hata" : (k.hata || 0) > (k.ok || 0);
  if (olay === "ok") {
    if (!hataSonda && k.ok && simdi - k.ok < OK_SEYREK_SN) return null; // yazma yok
    k.ok = simdi;
    k.son = "ok";
    delete k.hata_ilk;
  } else if (olay === "hata") {
    if (!hataSonda || !k.hata_ilk) k.hata_ilk = simdi;
    k.hata = simdi;
    k.son = "hata";
  } else if (olay === "kayit") {
    k.kayit = simdi;
  }
  return k;
}

async function nabiz(istek, env, is, olay) {
  if (!(await yetkili(istek, env))) return yanit("yetkisiz\n", 401);
  const anahtar = "is:" + is;
  const c = onbellek();
  if (c && (olay === "kayit" || olay === "sil")) for (const a of [OZET_YENI, OZET_ESKI]) await c.delete(new Request(a));
  if (olay === "sil") {
    await env.NABIZ.delete(anahtar);
    return new Response(null, { status: 204 });
  }
  const simdi = Math.floor(Date.now() / 1000);
  const { metadata } = await env.NABIZ.getWithMetadata(anahtar);
  const k = yeniKayit(metadata, olay, simdi);
  if (k) await env.NABIZ.put(anahtar, "", { metadata: k });
  return new Response(null, { status: 204 });
}

async function durum(istek, env, ctx, anahtarUrl) {
  const c = onbellek();
  const anahtar = new Request(anahtarUrl);
  if (c) {
    const eski = await c.match(anahtar);
    if (eski) return eski;
  }
  const isler = {};
  let cursor;
  do {
    const r = await env.NABIZ.list({ prefix: "is:", cursor });
    for (const a of r.keys) isler[a.name.slice(3)] = a.metadata || {};
    cursor = r.list_complete ? undefined : r.cursor;
  } while (cursor);
  const govde = JSON.stringify({ surum: 1, simdi: Math.floor(Date.now() / 1000), isler }) + "\n";
  const r = new Response(govde, {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": `public, max-age=${DURUM_ONBELLEK_SN}` },
  });
  if (c) {
    const p = c.put(anahtar, r.clone());
    if (ctx && ctx.waitUntil) ctx.waitUntil(p); else await p;
  }
  return r;
}

// status. (ve geçişte durum.) adındaki her yanıt: güvenlik başlıkları. Yalnız HTML yanıtında cache-control'a no-transform (Cloudflare e-posta
// gizleme vb. HTML'i değiştirmesin; wizard --sayfa yayındaki index.html'i hub kopyasıyla bayt bayt karşılaştırır). JSON, XML,
// JS ve düz metinde yok: no-transform Cloudflare'in sıkıştırmasını da kapatır.
function guvenli(r) {
  const y = new Response(r.body, r);
  for (const [k, v] of Object.entries(GUVENLIK)) y.headers.set(k, v);
  const cc = y.headers.get("cache-control");
  const html = /^text\/html\b/i.test(y.headers.get("content-type") || "");
  if (html && !/\bno-transform\b/.test(cc || "")) y.headers.set("cache-control", cc ? cc + ", no-transform" : "no-transform");
  return y;
}

async function veri(yol, env) {
  const kok = String(env.VERI_KOK || "").replace(/\/$/, "");
  let r;
  try {
    r = await fetch(kok + yol, { cf: { cacheTtl: 60, cacheEverything: true } });
  } catch (e) {
    r = null;
  }
  if (!r || !r.ok) {
    return guvenli(yanit(JSON.stringify({ hata: "veri henüz yok ya da okunamadı" }) + "\n", 503, "application/json; charset=utf-8"));
  }
  return guvenli(new Response(r.body, {
    status: 200,
    headers: { "content-type": VERI_DOSYALARI[yol], "cache-control": "public, max-age=60" },
  }));
}

async function durumSayfasi(istek, env, u) {
  if (istek.method !== "GET" && istek.method !== "HEAD") return guvenli(yanit("yok\n", 405));
  if (VERI_DOSYALARI[u.pathname]) return veri(u.pathname, env);
  if (!env.ASSETS) return guvenli(yanit("sayfa dosyaları yok\n", 503));
  return guvenli(await env.ASSETS.fetch(istek));
}

// sinyal: status./heartbeat… (yeni nabız uçları; aynı belirteç, aynı KV).
async function sinyal(istek, env, ctx, p) {
  const m = istek.method;
  if (p.length === 1 && (m === "GET" || m === "HEAD")) return guvenli(await durum(istek, env, ctx, OZET_YENI));
  if (p.length === 3 && m === "POST" && IS_RE.test(p[1]) && OLAYLAR.has(p[2])) return guvenli(await nabiz(istek, env, p[1], p[2]));
  return guvenli(yanit("yok\n", 404));
}

// eskiNabiz: nabiz. adının eski uçları (geçiş; ESKI_ADLAR'da adı varken).
function eskiNabiz(istek, env, ctx, p) {
  if (istek.method === "GET" && p.length === 0) return yanit("SKY LAB dış izleme nabız deposu (eski ad; yeni: " + OZET_YENI + ")\n", 200);
  if (istek.method === "GET" && p.length === 1 && p[0] === "durum") return durum(istek, env, ctx, OZET_ESKI);
  if (istek.method === "POST" && p.length === 3 && p[0] === "nabiz" && IS_RE.test(p[1]) && OLAYLAR.has(p[2])) {
    return nabiz(istek, env, p[1], p[2]);
  }
  return yanit("yok\n", 404);
}

export default {
  async fetch(istek, env, ctx) {
    const u = new URL(istek.url);
    const p = u.pathname.split("/").filter(Boolean);
    if (u.hostname === SAYFA_HOST) {
      if (p[0] === SINYAL_YOLU) return sinyal(istek, env, ctx, p);
      return durumSayfasi(istek, env, u);
    }
    if (u.hostname === ESKI_SAYFA_HOST && eskiAcik(env, ESKI_SAYFA_HOST)) return durumSayfasi(istek, env, u);
    if (u.hostname === ESKI_NABIZ_HOST && eskiAcik(env, ESKI_NABIZ_HOST)) return eskiNabiz(istek, env, ctx, p);
    return yanit("yok\n", 404);
  },
};
