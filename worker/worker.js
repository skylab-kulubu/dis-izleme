// SKY LAB dış izleme: nabız deposu + durum sayfası (tek Cloudflare Worker "skylab-durum", KV + statik dosyalar).
//
// Ana makineye göre:
//   durum.yildizskylab.com   durum sayfası. Statik dosyalar (sayfa/, yazı tipleri) ASSETS'ten; /sayfa.json ve /olaylar.xml
//                            depodaki "veri" dalından (GitHub Actions her durum değişiminde ve saatte bir yazar) 60 sn önbellekle.
//   status.yildizskylab.com  durum.yildizskylab.com'a kalıcı yönlendirme (kayıt varsa; wizard var olan kaydı ezmez).
//   nabiz.yildizskylab.com   aşağıdaki nabız deposu.
//
// Sunucudaki systemd işleri her başarılı ya da başarısız koşudan sonra buraya tek bir POST atar. Bu Worker yalnız
// "hangi iş, en son ne zaman" bilgisini saklar ve herkese açık /durum'da yayımlar. Değerlendirme ve uyarı burada
// yapılmaz: GitHub Actions (izle.py) /durum'u okur, gecikeni ya da düşeni bulur, GitHub issue'su ile haber verir.
//
//   POST /nabiz/<iş>/<olay>   olay: ok | hata | kayit | sil      Authorization: Bearer <belirteç>   → 204 | 401 | 404
//   GET  /durum               {"surum":1,"simdi":<epoch>,"isler":{"<iş>":{"ok":…,"hata":…,"hata_ilk":…,"kayit":…,"son":"ok"|"hata"}}}
//   GET  /                    düz metin, kimlik
//
// Belirteç sunucuda üretilir ve sunucudan çıkmaz; burada yalnız SHA-256 özeti durur (NABIZ_OZET, sır değil).
// Kişisel veri yok: istek IP'si, gövde, başlık saklanmaz. Gövde hiç okunmaz.
// KV ücretsiz katmanı günde 1000 yazma: "ok" olayı, aynı iş için son "ok"tan 50 dakika geçmeden yeniden yazılmaz
// (5 ve 15 dakikalık işler saatte bire iner). "hata", "kayit", "sil" her zaman yazılır.
// /durum KV listelemesi (ücretsizde günde 1000) caches.default ile 120 sn önbelleklenir; "kayit" ve "sil" o veri merkezindeki
// kopyayı siler (wizard --dogrula hemen görsün). Başka veri merkezinde en çok 120 sn eski kalabilir.

const IS_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
const DURUM_HOST = "durum.yildizskylab.com";
const YONLENEN_HOSTLAR = new Set(["status.yildizskylab.com"]);
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
const durumAnahtari = (u) => new Request(u.origin + "/durum");

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
  if (c && (olay === "kayit" || olay === "sil")) await c.delete(durumAnahtari(new URL(istek.url)));
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

async function durum(istek, env, ctx) {
  const c = onbellek();
  const anahtar = durumAnahtari(new URL(istek.url));
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

function guvenli(r) {
  const y = new Response(r.body, r);
  for (const [k, v] of Object.entries(GUVENLIK)) y.headers.set(k, v);
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
  if (istek.method !== "GET" && istek.method !== "HEAD") return yanit("yok\n", 405);
  if (VERI_DOSYALARI[u.pathname]) return veri(u.pathname, env);
  if (!env.ASSETS) return yanit("sayfa dosyaları yok\n", 503);
  return guvenli(await env.ASSETS.fetch(istek));
}

export default {
  async fetch(istek, env, ctx) {
    const u = new URL(istek.url);
    if (YONLENEN_HOSTLAR.has(u.hostname)) return Response.redirect("https://" + DURUM_HOST + u.pathname + u.search, 301);
    if (u.hostname === DURUM_HOST) return durumSayfasi(istek, env, u);
    const p = u.pathname.split("/").filter(Boolean);
    if (istek.method === "GET" && p.length === 0) return yanit("SKY LAB dış izleme nabız deposu\n", 200);
    if (istek.method === "GET" && p.length === 1 && p[0] === "durum") return durum(istek, env, ctx);
    if (istek.method === "POST" && p.length === 3 && p[0] === "nabiz" && IS_RE.test(p[1]) && OLAYLAR.has(p[2])) {
      return nabiz(istek, env, p[1], p[2]);
    }
    return yanit("yok\n", 404);
  },
};
