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

SRC_ADI = {"ardeb": "ARDEB", "uidb": "UIDB", "bt": "Bilim ve Toplum", "bideb": "BİDEB", "istka": "İSTKA", "tuseb": "TÜSEB", "duyuru": "Duyuru", "eu": "Ufuk Avrupa", "erasmus": "Erasmus+"}
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

def get(url, deneme=3):
    import time
    for i in range(deneme):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == deneme - 1: raise
            time.sleep(5 * (i + 1))

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

ANAHTAR = re.compile(r"son\s*ba[şs]vuru|son\s*tarih|kapan[ıi][şs]|biti[şs]\s*tarih|ba[şs]vurular.{0,30}(?:kadar|sona)", re.I)      # tarih ifadenin ardından gelir
GERI = re.compile(r"tarihine\s*kadar|tarihleri\s*aras[ıi]nda|tarihinde\s*sona|sona\s*erecek", re.I)                                 # tarih ifadenin önünde yer alır
SAAT2 = re.compile(r"saat\s*(\d{1,2})[.:](\d{2})|(?<![\d.])(\d{1,2}):(\d{2})", re.I)

def _iso_tarihler(win):
    out = []
    for d in TARIH.finditer(win):
        if d.group(1): dd, mo, y = int(d.group(1)), AYLAR[d.group(2).lower()], int(d.group(3))
        else: dd, mo, y = int(d.group(4)), int(d.group(5)), int(d.group(6))
        if not (1 <= dd <= 31 and 1 <= mo <= 12): continue
        iso = f"{y}-{mo:02d}-{dd:02d}"
        nxt = TARIH.search(win, d.end())
        tail = win[d.end(): (nxt.start() if nxt else d.end() + 30)][:30]
        sm = SAAT2.search(tail)
        if sm:
            h, mi = (sm.group(1), sm.group(2)) if sm.group(1) else (sm.group(3), sm.group(4))
            if int(h) < 24 and int(mi) < 60: iso += f"T{int(h):02d}:{mi}"
        out.append(iso)
    return out

def tarihler(text):
    """Anahtar ifadelerin (son başvuru, kapanış, '... tarihine kadar') çevresindeki tarihleri ISO olarak döndürür."""
    flat = re.sub(r"\s+", " ", text)
    out = []
    for m in ANAHTAR.finditer(flat):
        out += _iso_tarihler(flat[m.start(): m.end() + 140])
    for m in GERI.finditer(flat):
        t = _iso_tarihler(flat[max(0, m.start() - 70): m.end() + 25])
        if t: out.append(t[-1])                                    # aralıkta ("01 Eylül – 13 Kasım") bitiş tarihi
    seen = []
    for t in out:
        if t not in seen: seen.append(t)
    return seen

def tarih_sec(adaylar):
    """Bir sonraki (bugünden sonraki en yakın) tarihi seç; hiçbiri makul değilse ilkini döndür (elle kontrol için)."""
    uygun = sorted(t for t in adaylar if now_iso() <= t[:10] <= max_iso())
    return uygun[0] if uygun else (adaylar[0] if adaylar else "")

def tarih_cikar(text):
    return tarih_sec(tarihler(text))

class PdfLinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); h = a.get("href") or ""
        if tag == "a" and ".pdf" in h.lower() and h not in self.links: self.links.append(h)

def pdf_tarih(html, base):
    """Sayfa metninde tarih yoksa sayfadaki PDF'lere (en çok 3) bak."""
    try:
        import butce_cikar
    except Exception:
        return ""
    lp = PdfLinkParser(); lp.feed(html)
    for h in lp.links[:3]:
        try:
            t, _ = butce_cikar.metin(urljoin(base, h))
            c = tarih_sec(tarihler(t))
            if c and now_iso() <= c[:10] <= max_iso(): return c
        except Exception:
            continue
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
            html = get(it["url"]); tp = TextParser(); tp.feed(html); text = "\n".join(tp.t)
        except Exception as e:
            kontrol.append({**it, "neden": f"sayfa okunamadı: {e}"}); continue
        end = tarih_cikar(text); kaynak = "duyuru metninden"
        if not end or not (now_iso() <= end[:10] <= max_iso()):
            pe = pdf_tarih(html, it["url"])
            if pe: end, kaynak = pe, "duyurudaki PDF'ten"
        if end and not (now_iso() <= end[:10] <= max_iso()):
            kontrol.append({**it, "neden": f"çıkarılan tarih makul değil ({end}); elle kontrol edin"}); continue
        if not end:
            kontrol.append({**it, "neden": "son başvuru tarihi metinden çıkarılamadı"}); continue
        adaylar.append({"key": "duyuru:" + it["url"],
            "row": ["duyuru", "TÜBİTAK Duyurusu", it["title"], "Duyuru", "", "", end, 0, it["url"], None,
                    f"Tarih {kaynak} otomatik çıkarıldı; doğrulayınız.", it["date"]]})
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
    # Kaynak alınamazsa son başarılı sonuç kullanılır (sayfa bir anda boşalmasın)
    son_dosya = HERE / "son-iyi.json"
    son = json.loads(son_dosya.read_text("utf-8")) if son_dosya.exists() else {}
    basarili = {}
    def al(ad, fn):
        try:
            lst = fn(); ekle(lst, ad); basarili[ad] = lst
            if not lst and son.get(ad): raise ValueError("kaynak boş döndü")
        except Exception as e:
            hatalar.append(f"{ad}: {e} -> önceki başarılı sonuç kullanıldı ({len(son.get(ad, []))} kayıt)")
            for k in son.get(ad, []): kayitlar[k["key"]] = k["row"]
    for src in PBS: al(src, lambda src=src: fetch_pbs(src))
    for ad, fn in (("bideb", fetch_bideb), ("istka", fetch_istka)): al(ad, fn)

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

    for ad, lst in basarili.items():
        if lst: son[ad] = lst
    son_dosya.write_text(json.dumps(son, ensure_ascii=False), "utf-8")
    (HERE / "uyarilar.json").write_text(json.dumps({"zaman": now.isoformat(timespec="minutes"), "hatalar": hatalar, "kontrol_gerekli": kontrol,
        "kaynak_sayilari": {k: sum(1 for r in rows if r[0] == k) for k in sorted({r[0] for r in rows})}}, ensure_ascii=False, indent=1), "utf-8")
    # Günlük durum günlüğü: aynı güne ait çalışmalar tek kayıtta birleşir -> DURUM.md
    try:
        gj_dosya = HERE / "durum-gunlugu.json"
        gj = json.loads(gj_dosya.read_text("utf-8")) if gj_dosya.exists() else {}
        gun = now.strftime("%Y-%m-%d")
        g = gj.get(gun, {"yeni": [], "degisen": [], "calisma": 0})
        satir = lambda r: f"[{r[2]}]({r[8]})" if r[8] else r[2]
        for r in yeni:
            t = f"**{SRC_ADI.get(r[0], r[0])}** — {satir(r)} (son tarih: {(r[6] or 'sürekli açık').replace('T', ' ')})"
            if t not in g["yeni"]: g["yeni"].append(t)
        for r, o in degisen:
            t = f"**{SRC_ADI.get(r[0], r[0])}** — {satir(r)}: {(o or '—').replace('T', ' ')} → {(r[6] or '—').replace('T', ' ')}"
            if t not in g["degisen"]: g["degisen"].append(t)
        g.update({"saat": now.strftime("%H:%M"), "toplam": len(rows), "calisma": g.get("calisma", 0) + 1,
                  "kaynaklar": {k: sum(1 for r in rows if r[0] == k) for k in sorted({r[0] for r in rows})},
                  "kontrol": len(kontrol), "hatalar": hatalar})
        gj[gun] = g
        for eski in sorted(gj)[:-180]: del gj[eski]          # son 180 gün
        gj_dosya.write_text(json.dumps(gj, ensure_ascii=False, indent=1), "utf-8")
        md = ["# Günlük Durum Güncellemeleri", "",
              "Bu dosya her çalışmada otomatik güncellenir (en yeni gün en üstte). Aynı güne ait birden fazla çalışma tek kayıtta birleşir.", ""]
        for gun_k in sorted(gj, reverse=True):
            x = gj[gun_k]
            md += [f"## {gun_k[8:10]}.{gun_k[5:7]}.{gun_k[:4]}", "",
                   f"- Son çalışma: **{x['saat']}** (İstanbul) · o gün {x['calisma']} çalışma",
                   f"- Açık çağrı: **{x['toplam']}** — " + " · ".join(f"{SRC_ADI.get(k, k)} {v}" for k, v in x["kaynaklar"].items())]
            md.append("- Yeni çağrılar: " + ("" if x["yeni"] else "yok"))
            md += [f"  - {t}" for t in x["yeni"]]
            md.append("- Son tarihi değişenler: " + ("" if x["degisen"] else "yok"))
            md += [f"  - {t}" for t in x["degisen"]]
            if x.get("kontrol"): md.append(f"- Elle kontrol bekleyen duyuru adayı: {x['kontrol']}")
            md.append("- Uyarılar: " + ("yok" if not x["hatalar"] else ""))
            md += [f"  - {h}" for h in x["hatalar"]]
            md.append("")
        (HERE / "DURUM.md").write_text("\n".join(md), "utf-8")
    except Exception as e:
        print("durum günlüğü yazılamadı:", e, file=sys.stderr)
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
