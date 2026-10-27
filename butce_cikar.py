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
VER = 6

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

def _temiz(x):
    x = re.sub(r"[•❖▪●o]\s+(?=[A-ZÇĞİÖŞÜ])|\s+", " ", x)
    return re.sub(r" {2,}", " ", x).strip(" ;,.")

def cikar_uidb(t):
    """UIDB: yalnızca 'Proje başına' ve 'Yürütücü kuruluş başına' üst limitleri."""
    d = re.sub(r"\s+", " ", t.replace("\xa0", " "))
    pb = re.search(r"proje başına[^.\d]{0,90}?(\d{2,3}(?:\.\d{3})+|\d{4,})\s*(Avro|€|EUR|TL|₺)", d, re.I)
    yb = re.search(r"yürütücü kuruluş başına\s*;?\s*(.{20,420})", d, re.I)
    out = []
    if pb:
        ek = " (Kurum Hissesi ve PTİ hariç)" if re.search(r"Kurum Hissesi ve Proje Teşvik İkramiyesi \(PTİ\) hariç", d[max(0, pb.start()-120):pb.start()+120]) else ""
        out.append(f"Proje başına: {pb.group(1)} {pb.group(2)}{ek}")
    if yb:
        seg = re.split(r"aşamaz|TÜBİTAK.tan talep|\d? ?Bu süreç|Proje süresi|Ayrıca|\. ", yb.group(1))[0]
        seg = _temiz(seg)
        if re.search(r"\d", seg): out.append("Yürütücü kuruluş başına: " + seg[:300])
    return " · ".join(out)

def cikar_ikili(t):
    """İkili işbirliği: Türk tarafı (TÜBİTAK) ve ortak ülke tarafı üst limitleri."""
    d = re.sub(r"\s+", " ", t.replace("\xa0", " "))
    tl = re.search(r"(?:Turkish\s*(?:partners|team)[^.]{0,140}?can\s*re\s*ceive|TÜBİTAK funds?)\s+up to\s+([\d.]{5,})\s*(Turkish\s*Li\s*ra\s*s?|TL|₺)(?:[^.]{0,12}?per (project|year))?", d, re.I) \
        or re.search(r"(?:bütçe|destek) üst limiti[^.\d]{0,120}?([\d.]{5,})\s*(TL|₺)", d, re.I) \
        or re.search(r"(?:bütçe|destek) üst limiti\s*:?\s*([\d.]{5,})\s*(TL|₺)", d, re.I)
    out = []
    if tl: out.append(f"Türk tarafı (TÜBİTAK): {tl.group(1)} TL" + (" / proje" if not tl.lastindex or tl.lastindex < 3 or (tl.group(3) or "project").lower() == "project" else " / yıl") + (" (Kurum Hissesi ve PTİ hariç)" if re.search(r"excl\. overhead|PTİ.{0,20}hariç|PTİ hariç", d[max(0, tl.start()-20):tl.end()+90]) else ""))
    ULKE = {"German": "Alman", "Uzbek": "Özbek", "Jordanian": "Ürdün", "Bulgarian": "Bulgar", "Polish": "Polonyalı", "Chinese": "Çinli", "Korean": "Koreli", "Japanese": "Japon", "American": "ABD"}
    for m in re.finditer(r"([A-Z][a-z]+) (?:partners?|team)[^.]{0,100}?up to\s+([\d.]{4,})\s*([A-Za-z]{2,12}(?: [A-Za-z]{3,8})?(?: \([A-Z]{3}\))?)\s*/?\s*per (project|year)", d):
        if m.group(1) in ("Turkish", "Each", "The"): continue
        out.append(f"{ULKE.get(m.group(1), m.group(1))} tarafı: {m.group(2)} {m.group(3)} / {'proje' if m.group(4) == 'project' else 'yıl'}")
        break
    return " · ".join(out)

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
            u_b = (cikar_uidb(t) or cikar_ikili(t)) if urller.get(u) == "uidb" else ""
            onb[u] = {"uidb": u_b, "v": VER, "t": now.isoformat(timespec="seconds"), "tur": tur, "butce": b, "sure": s}
        except Exception as e:
            onb[u] = {"v": VER, "t": now.isoformat(timespec="seconds"), "hata": str(e)[:120], "butce": "", "sure": ""}
            log(f"butce: {u} -> {e}")
    CACHE.write_text(json.dumps({u: onb[u] for u in urller}, ensure_ascii=False, indent=1), "utf-8")

    yeni, bulunan = [], 0
    for r in rows:
        o = onb.get(r[8]) if r[0] in KAYNAKLAR else None
        if o and r[0] == "uidb":                       # UIDB: yalnızca proje / yürütücü başına limitler
            o = {"butce": o.get("uidb", ""), "sure": ""}
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
