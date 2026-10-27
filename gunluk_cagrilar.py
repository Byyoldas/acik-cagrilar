#!/usr/bin/env python3
"""Günlük açık çağrı derleyicisi (yalnızca Python 3.9+ standart kütüphane).

Kaynaklar : ARDEB / UIDB / Bilim-Toplum PBS, BİDEB, TÜBİTAK duyuruları, İSTKA, F&T Portalı (Ufuk Avrupa)
Girdiler  : manuel.json (elle girilen/denetlenen satırlar), cagri-detaylari.json (zenginleştirme)
Çıktılar  : cikti/cagrilar.json  (siteye), cikti/cagrilar.csv, cikti/yeni.json, durum.json (önceki gün)
E-posta   : SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO ortam değişkenleri doluysa yeni çağrılar gönderilir.
Çalıştırma: python3 gunluk_cagrilar.py [--sessiz-mail] [--duyuru-sayfa 3]
"""
import argparse, csv, json, os, re, smtplib, ssl, sys, urllib.request
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import fetch_eu_calls as eu

IST = ZoneInfo("Europe/Istanbul")
HERE = Path(__file__).parent
OUT = HERE / "cikti"
UA = {"User-Agent": "Mozilla/5.0 (compatible; acik-cagrilar-derleyici/1.0)"}
BIDEB_CLOSE_TIME = os.environ.get("BIDEB_CLOSE_TIME", "")   # örn. "17:30"; boşsa yalnızca tarih

PBS = {
    "ardeb": "https://ardeb-pbs.tubitak.gov.tr",
    "uidb": "https://uidb-pbs.tubitak.gov.tr",
    "bt": "https://bilimtoplum-pbs.tubitak.gov.tr",
}
BIDEB_URL = "https://ebideb.tubitak.gov.tr/basvuruacikprogramlar.htm"
DUYURU_URL = "https://tubitak.gov.tr/tr/duyuru"
ISTKA_URL = "https://www.istka.org.tr/acik-destek-programlari"
YENI_KELIMELER = re.compile(r"açıldı|başvuruya açıl|başvurular(ı)? başladı|çağrısı yayımlandı|çağrı(sı)? yayında", re.I)
ELE_KELIMELER = re.compile(r"sonuç|ertelen|iptal|uzatıl|duyurusu$", re.I)

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")

# ---------------------------------------------------------------- PBS
class PbsParser(HTMLParser):
    def __init__(self): super().__init__(); self.rows = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and "data-cagri-adi" in a: self.rows.append(a)

def iso_pbs(s):
    s = (s or "").strip()
    if not s or s == "-": return ""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})(?: (\d{2}):(\d{2}))?", s)
    if not m: return ""
    d, mo, y, hh, mm = m.groups()
    return f"{y}-{mo}-{d}" + (f"T{hh}:{mm}" if hh else "")

def fetch_pbs(src):
    p = PbsParser(); p.feed(get(PBS[src]))
    rows = []
    for a in p.rows:
        dash = lambda v: "" if (v or "-").strip() == "-" else v.strip()
        rows.append({"key": f"{src}:{a.get('data-program-cagri-id')}",
            "row": [src, a.get("data-program-adi", ""), a.get("data-cagri-adi", ""), dash(a.get("data-cagri-kategori")),
                    dash(a.get("data-cagri-kodu")), iso_pbs(a.get("data-baslangic")), iso_pbs(a.get("data-bitis")),
                    1 if a.get("data-ikinci-asama") == "true" else 0, dash(a.get("data-bilgi-notu-url")),
                    int(a["data-program-id"]) if a.get("data-program-id", "").isdigit() else None, "", ""]})
    return rows

# ---------------------------------------------------------------- BİDEB
class TableParser(HTMLParser):
    def __init__(self): super().__init__(); self.rows, self._r, self._c, self._link = [], None, None, ""
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr": self._r = []
        elif tag == "td" and self._r is not None: self._c = ""; self._link = ""
        elif tag == "a" and self._c is not None and a.get("href") and not self._link: self._link = a["href"]
    def handle_data(self, d):
        if self._c is not None: self._c += d
    def handle_endtag(self, tag):
        if tag == "td" and self._r is not None and self._c is not None:
            self._r.append((re.sub(r"\s+", " ", self._c).strip(), self._link)); self._c = None
        elif tag == "tr" and self._r:
            self.rows.append(self._r); self._r = None

def iso_slash(s):
    m = re.match(r"\s*(\d{2})/(\d{2})/(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""

def fetch_bideb():
    p = TableParser(); p.feed(get(BIDEB_URL)); out = []
    for r in p.rows:
        if len(r) < 4: continue
        ad, link = r[1]; link = urljoin(BIDEB_URL, link) if link else ""
        start, end = iso_slash(r[2][0]), iso_slash(r[3][0])
        if not ad or not end: continue
        if BIDEB_CLOSE_TIME: end += "T" + BIDEB_CLOSE_TIME
        sonuc = r[4][0] if len(r) > 4 else ""
        out.append({"key": "bideb:" + ad,
            "row": ["bideb", "BİDEB", ad, "", "", start, end, 0, link, None, ("Sonuç: " + sonuc) if sonuc else "", ""]})
    return out

# ---------------------------------------------------------------- Duyurular
class DuyuruParser(HTMLParser):
    def __init__(self): super().__init__(); self.items, self._time, self._href, self._txt = [], "", None, ""
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "time" and a.get("datetime"): self._time = a["datetime"]
        elif tag == "a" and (a.get("href") or "").startswith("/tr/duyuru/"): self._href, self._txt = a["href"], ""
    def handle_data(self, d):
        if self._href: self._txt += d
    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            t = re.sub(r"\s+", " ", self._txt).strip()
            if t and t.lower() != "devamını oku" and self._time:
                self.items.append({"title": t, "url": "https://tubitak.gov.tr" + self._href, "date": self._time[:10]})
                self._time = ""
            self._href = None

class TextParser(HTMLParser):
    def __init__(self): super().__init__(); self.t, self.skip = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style"): self.skip -= 1
    def handle_data(self, d):
        if not self.skip: self.t.append(d)

AYLAR = {a: i + 1 for i, a in enumerate("ocak şubat mart nisan mayıs haziran temmuz ağustos eylül ekim kasım aralık".split())}
TARIH = re.compile(r"(\d{1,2})\s+(" + "|".join(AYLAR) + r")\s+(\d{4})|(\d{1,2})[./](\d{1,2})[./](\d{4})", re.I)
SAAT = re.compile(r"(?:saat\s*)?(\d{1,2})[.:](\d{2})", re.I)

def tarih_cikar(text):
    """'son başvuru/son tarih' içeren ilk cümleden tarih(+saat) -> ISO ya da ''. Her zaman doğrulama gerektirir."""
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        if not re.search(r"son\s+(başvuru|tarih)|tarihine kadar|kadar", sent, re.I): continue
        m = TARIH.search(sent)
        if not m: continue
        if m.group(1): d, mo, y = int(m.group(1)), AYLAR[m.group(2).lower()], int(m.group(3))
        else: d, mo, y = int(m.group(4)), int(m.group(5)), int(m.group(6))
        iso = f"{y}-{mo:02d}-{d:02d}"
        tail = sent[m.end():m.end() + 40]
        s = SAAT.search(tail)
        if s and int(s.group(1)) < 24: iso += f"T{int(s.group(1)):02d}:{s.group(2)}"
        return iso
    return ""

def now_iso(): return datetime.now(IST).strftime("%Y-%m-%d")
def max_iso():
    d = datetime.now(IST); return d.replace(year=d.year + 1).strftime("%Y-%m-%d")   # en fazla ~1 yıl ilerisi

def fetch_duyuru(pages):
    items, seen = [], set()
    for pg in range(pages):
        p = DuyuruParser(); p.feed(get(DUYURU_URL + (f"?page={pg}" if pg else "")))
        for it in p.items:
            if it["url"] not in seen: seen.add(it["url"]); items.append(it)
    return items

def duyuru_adaylari(items, bilinen_urller, bilinen_kodlar=frozenset()):
    adaylar, kontrol = [], []
    for it in items:
        if it["url"] in bilinen_urller: continue
        if not YENI_KELIMELER.search(it["title"]) or ELE_KELIMELER.search(it["title"]): continue
        kod = re.match(r"\s*(\d{4})\b", it["title"])
        if kod and any(ad.strip().startswith(kod.group(1)) for ad in bilinen_kodlar): continue   # zaten başvuru sistemi/BİDEB listesinde
        try:
            tp = TextParser(); tp.feed(get(it["url"])); text = "\n".join(tp.t)
        except Exception as e:
            kontrol.append({**it, "neden": f"sayfa okunamadı: {e}"}); continue
        end = tarih_cikar(text)
        if end and not (now_iso() <= end[:10] <= max_iso()):
            kontrol.append({**it, "neden": f"çıkarılan tarih makul değil ({end}); elle kontrol edin"}); continue
        if not end:
            kontrol.append({**it, "neden": "son başvuru tarihi metinden çıkarılamadı"}); continue
        adaylar.append({"key": "duyuru:" + it["url"],
            "row": ["duyuru", "TÜBİTAK Duyurusu", it["title"], "Duyuru", "", "", end, 0, it["url"], None,
                    "Tarih duyuru metninden otomatik çıkarıldı; doğrulayınız.", it["date"]]})
    return adaylar, kontrol

# ---------------------------------------------------------------- İSTKA (en iyi çaba; kırılgan)
def fetch_istka():
    tp = TextParser(); tp.feed(get(ISTKA_URL)); text = re.sub(r"\s+", " ", " ".join(tp.t))
    out = []
    for m in re.finditer(r"([^.|]{10,160}?\(İSTKA/\d{4}/[A-Z]+\))", text):
        ad = re.sub(r"^.*\bProgramlar(?:ı)?\s+", "", m.group(1).strip()) if len(m.group(1)) > 40 else m.group(1).strip(); kod = re.search(r"\((İSTKA/[^)]+)\)", ad).group(1)
        seg = text[m.end(): m.end() + 800]
        d = re.search(r"(\d{2})/(\d{2})/(\d{4})[ ,]+(\d{2}):(\d{2})", seg)
        if not d: continue
        out.append({"key": "istka:" + kod,
            "row": ["istka", "İSTKA Mali Destek Programları", ad, "Mali Destek Programı", kod, "",
                    f"{d.group(3)}-{d.group(2)}-{d.group(1)}T{d.group(4)}:{d.group(5)}", 0, ISTKA_URL, None, "", ""]})
    return out

# ---------------------------------------------------------------- Ana akış
def bitis(row):
    e = row[6]
    if not e: return None
    d = datetime.strptime(e[:16] if "T" in e else e + "T23:59", "%Y-%m-%dT%H:%M")
    return d.replace(tzinfo=IST)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessiz-mail", action="store_true", help="e-posta göndermeden çalış")
    ap.add_argument("--duyuru-sayfa", type=int, default=3)
    args = ap.parse_args()
    now = datetime.now(IST); OUT.mkdir(exist_ok=True)
    kayitlar, hatalar = {}, []

    def ekle(lst, kaynak):
        for k in lst: kayitlar[k["key"]] = k["row"]
    manuel_kodlar = set()
    for src in PBS:
        try: ekle(fetch_pbs(src), src)
        except Exception as e: hatalar.append(f"{src}: {e}")
    for ad, fn in (("bideb", fetch_bideb), ("istka", fetch_istka)):
        try: ekle(fn(), ad)
        except Exception as e: hatalar.append(f"{ad}: {e}")

    # EU (Ufuk Avrupa): hazır betiği kullan
    eu_enrich = []
    try:
        tmp = OUT / "_eu.json"; eu.main(str(tmp)); d = json.loads(tmp.read_text("utf-8")); tmp.unlink()
        for r in d["rows"]: kayitlar[r[0] + ":" + r[8]] = r
        for e in d["enrich"]:
            eu_enrich.append({"u": e["kaynak"], "butce": e["butce"], "durum": "dogrulandi", "kaynak": e["kaynak"],
                              **({"sure": e["sure"]} if e.get("sure") else {}), **({"not": e["not"]} if e.get("not") else {})})
    except Exception as e: hatalar.append(f"eu: {e}")

    # Elle girilen / denetlenen satırlar (duyuru, İSTKA vb.)
    manuel = json.loads((HERE / "manuel.json").read_text("utf-8")) if (HERE / "manuel.json").exists() else []
    manuel_kodlar = {r[4] for r in manuel if r[4]}
    for k in [k for k, r in kayitlar.items() if r[0] == "istka" and r[4] in manuel_kodlar]: del kayitlar[k]
    for r in manuel: kayitlar["manuel:" + (r[8] or r[2])] = r
    # Otomatik duyuru adayları (manuel listede olmayanlar)
    kontrol = []
    try:
        adaylar, kontrol = duyuru_adaylari(fetch_duyuru(args.duyuru_sayfa), {r[8] for r in manuel}, {r[2] for r in kayitlar.values() if r[0] in ("ardeb", "uidb", "bt", "bideb")})
        ekle(adaylar, "duyuru")
    except Exception as e: hatalar.append(f"duyuru: {e}")

    # Süresi dolanları ele
    rows = [r for r in kayitlar.values() if not (bitis(r) and bitis(r) < now)]
    rows.sort(key=lambda r: (bitis(r) is None, bitis(r) or now))

    # Fark (önceki çalıştırmaya göre)
    durum_dosya = HERE / "durum.json"
    onceki = json.loads(durum_dosya.read_text("utf-8")) if durum_dosya.exists() else None
    anahtar = lambda r: f"{r[0]}|{r[8] or r[2]}|{r[4]}|{r[2]}"
    yeni, degisen = [], []
    if onceki is not None:
        for r in rows:
            o = onceki.get(anahtar(r))
            if o is None: yeni.append(r)
            elif o != r[6]: degisen.append((r, o))
    durum_dosya.write_text(json.dumps({anahtar(r): r[6] for r in rows}, ensure_ascii=False), "utf-8")

    enrich = json.loads((HERE / "cagri-detaylari.json").read_text("utf-8")) if (HERE / "cagri-detaylari.json").exists() else []
    try:
        import butce_cikar
        oto = butce_cikar.calistir(rows, enrich, log=lambda m: print(m, file=sys.stderr))
    except Exception as e:
        oto = []; hatalar.append(f"butce: {e}")
    cikti = {"generated": now.isoformat(timespec="minutes"), "rows": rows, "enrich": eu_enrich + enrich + oto}
    (OUT / "cagrilar.json").write_text(json.dumps(cikti, ensure_ascii=False, indent=1), "utf-8")
    (OUT / "yeni.json").write_text(json.dumps({"yeni": yeni, "degisen": [{"row": r, "eski_bitis": o} for r, o in degisen],
                                               "kontrol_gerekli": kontrol, "hatalar": hatalar}, ensure_ascii=False, indent=1), "utf-8")
    with open(OUT / "cagrilar.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(["Kaynak", "Program", "Çağrı", "Kategori", "Kod", "Başlangıç", "Bitiş", "Not", "Bağlantı"])
        for r in rows: w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10], r[8]])

    print(f"{len(rows)} açık çağrı | yeni: {len(yeni)} | tarihi değişen: {len(degisen)} | kontrol gerekli: {len(kontrol)} | hata: {len(hatalar)}")
    for h in hatalar: print("HATA:", h, file=sys.stderr)
    if onceki is None: print("İlk çalıştırma: temel durum kaydedildi, e-posta gönderilmedi.")
    elif (yeni or degisen) and not args.sessiz_mail: mail_gonder(yeni, degisen)
    return 1 if hatalar and not rows else 0

def mail_gonder(yeni, degisen):
    env = {k: os.environ.get(k, "") for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_FROM", "MAIL_TO")}
    if not all(env.values()): print("SMTP ayarları eksik: e-posta atlandı."); return
    satir = lambda r: f"- [{r[0].upper()}] {r[2]} — son: {r[6] or 'sürekli'}\n  {r[8]}"
    govde = ""
    if yeni: govde += "YENİ ÇAĞRILAR\n" + "\n".join(satir(r) for r in yeni) + "\n\n"
    if degisen: govde += "SON TARİHİ DEĞİŞENLER\n" + "\n".join(satir(r) + f"\n  (eski: {o})" for r, o in degisen) + "\n"
    m = EmailMessage(); m["Subject"] = f"Açık çağrılar: {len(yeni)} yeni, {len(degisen)} değişen"
    m["From"], m["To"] = env["MAIL_FROM"], [x.strip() for x in env["MAIL_TO"].split(",")]
    m.set_content(govde)
    with smtplib.SMTP(env["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls(context=ssl.create_default_context()); s.login(env["SMTP_USER"], env["SMTP_PASS"]); s.send_message(m)
    print("E-posta gönderildi:", env["MAIL_TO"])

if __name__ == "__main__":
    sys.exit(main())
