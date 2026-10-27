"""ARDEB / UIDB / Bilim-Toplum / BİDEB çağrılarının bilgi sayfası ve PDF'lerinden
bütçe ve destek süresi bilgisini otomatik çıkarır (en iyi çaba; 'otomatik' etiketiyle gösterilir).
Önbellek: butce-onbellek.json (URL başına, 7 günde bir yenilenir)."""
import io, json, re, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "butce-onbellek.json"
KAYNAKLAR = ("ardeb", "uidb", "bt", "bideb")
UA = {"User-Agent": "Mozilla/5.0 (compatible; AcikCagrilarBot/1.0)"}
YENILE = timedelta(days=7)
PARA = re.compile(r"(\d[\d.,]*\s*(?:milyon|bin)?\s*(?:TL|₺|TRY|Euro|EUR|€|USD|\$|Avro))|((?:TL|₺|€)\s*\d[\d.,]*)", re.I)
KONU = re.compile(r"bütçe|destek tutar|destek limit|azami|en fazla|üst limit|toplam|proje başına|burs miktar|aylık|ödenek|budget|funding|up to|maximum", re.I)
SURE_CUMLE = re.compile(r"proje süre|destek süre|süre üst|süre sınır|azami süre|duration|project length", re.I)
SURE_SAYI = re.compile(r"(\d{1,2})\s*(ay|yıl|months?|years?)(?![\p{L}])" if False else r"(\d{1,2})\s*(ay|yıl|months?|years?)(?:dır|dir|lık|lik|ı|a)?(?![a-zçğıöşüA-ZÇĞİÖŞÜ])", re.I)
GURULTU = re.compile(r"seyahat|kilometre|yolluk|yol,? konaklama|konaklama|yemek|kongre|konferans|toplantı|gündelik|harcırah|katılım ücreti|ödeme yapılmaktadır|brüt", re.I)
GUCLU = re.compile(r"proje bütçesi|toplam bütçe|bütçe üst|destek üst limit|bütçe sınır|destek tutar|burs miktar|toplam destek|call budget|total budget|indicative budget|bütçeye sahip", re.I)
VER = 3

def indir(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read(15_000_000), r.headers.get("Content-Type", "")

def metin(url):
    veri, tip = indir(url)
    if veri[:4] == b"%PDF" or url.lower().split("?")[0].endswith(".pdf"):
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(veri))
        return "\n".join((p.extract_text() or "") for p in rd.pages[:40]), "pdf"
    h = veri.decode("utf-8", "replace")
    h = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    import html
    return html.unescape(h), "html"

def cumleler(t):
    t = re.sub(r"[ \t\r\f\v]+", " ", t.replace("\xa0", " "))
    t = re.sub(r"\n(?=[a-zçğıöşü])", " ", t)           # PDF satır kırıklarını birleştir
    return [c.strip() for c in re.split(r"(?<=[.;:!?])\s+|\n{2,}|\n(?=[•\-–●])", t) if 25 <= len(c.strip()) <= 400]

def cikar(t):
    cs = cumleler(t)
    bt = [c for c in cs if PARA.search(c) and KONU.search(c)]
    puan = lambda c: (0 if GUCLU.search(c) else 1 if re.search(r"bütçe|tutar|azami|en fazla|limit|budget|maximum", c, re.I) else 2) + (3 if GURULTU.search(c) and not GUCLU.search(c) else 0)
    bt.sort(key=puan)
    gor, secilen = set(), []
    for c in bt:
        k = c[:60]
        if k in gor or puan(c) >= 3 and secilen: continue
        gor.add(k); secilen.append(c[:300])
        if len(secilen) == 2: break
    if secilen and puan(secilen[0]) >= 3: secilen = []        # yalnızca gürültülü (yol/konaklama vb.) cümle varsa yazma
    sure = ""
    for c in cs:
        if SURE_CUMLE.search(c):
            m = SURE_SAYI.search(c)
            if m:
                b = m.group(2).lower(); b = {"ay": "ay", "yıl": "yıl"}.get(b, b)
                sure = f"{m.group(1)} {b} (otomatik)"; break
    return " | ".join(secilen), sure

def calistir(rows, manuel_enrich, log=print):
    onb = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    now = datetime.now()
    urller = {}
    for r in rows:
        if r[0] in KAYNAKLAR and r[8] and r[8].startswith("http"): urller.setdefault(r[8], r[0])
    for u in urller:
        o = onb.get(u)
        if o and o.get("v") == VER and now - datetime.fromisoformat(o["t"]) < YENILE: continue
        try:
            t, tur = metin(u); b, s = cikar(t)
            ham = [re.sub(r"\s+", " ", t[max(0, m.start()-60):m.end()+260]) for m in re.finditer(r"(?i)proje başına|yürütücü kuruluş başına|per project|per coordinator", t)][:6]
            onb[u] = {"ham": ham, "v": VER, "t": now.isoformat(timespec="seconds"), "tur": tur, "butce": b, "sure": s}
        except Exception as e:
            onb[u] = {"v": VER, "t": now.isoformat(timespec="seconds"), "hata": str(e)[:120], "butce": "", "sure": ""}
            log(f"butce: {u} -> {e}")
    CACHE.write_text(json.dumps({u: onb[u] for u in urller}, ensure_ascii=False, indent=1), "utf-8")

    yeni, bulunan = [], 0
    for r in rows:
        o = onb.get(r[8]) if r[0] in KAYNAKLAR else None
        if not o or not (o["butce"] or o["sure"]): continue
        man = next((e for e in manuel_enrich if e.get("m") and e["m"] in r[2]), None)
        if man:                                    # elle girilen öncelikli; eksikleri doldur
            if not man.get("butce") or man["butce"].startswith(("Belirtilmemiş", "Resmi sayfada")): man["butce"] = o["butce"] or man.get("butce", "")
            if not man.get("sure") and o["sure"]: man["sure"] = o["sure"]
            continue
        bulunan += 1
        yeni.append({"m": r[2], "butce": o["butce"] or "Sayfada otomatik olarak bulunamadı", **({"sure": o["sure"]} if o["sure"] else {}),
                     "durum": "otomatik", "kaynak": r[8]})
    log(f"butce: {len(urller)} sayfa, {bulunan} çağrıya otomatik bilgi eklendi")
    return yeni
