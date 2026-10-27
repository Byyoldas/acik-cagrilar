#!/usr/bin/env python3
"""F&T Portal (Ufuk Avrupa) açık çağrılarını resmi Search API'den çeker.
Çıktı: eu_calls.json  ->  {"generated": ..., "rows": [...], "enrich": [...]}
rows  : acik-cagrilar-demo.html içindeki DATA satır biçimi
enrich: ENRICH katmanı biçimi (bütçe, süre, tüm kesim tarihleri)

Kaynak: https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis
Not: API'deki deadlineDate saatleri Brüksel yerel saati kabul edilip İstanbul saatine çevrilir
     (API '+0000' etiketi taşıyor ama değerler 17:00 gibi yerel saatler). İlk çalıştırmada
     bir çağrıyı portaldaki saatle karşılaştırarak doğrulayın. 00:00 ise yalnızca tarih kabul edilir.
"""
import json, sys, uuid, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text=***&pageSize=100&pageNumber=%d"
QUERY = {"bool": {"must": [
    {"terms": {"type": ["1", "2", "8"]}},            # 1=konu, 8=kademeli fonlama (cascade)
    {"terms": {"status": ["31094502"]}},             # 31094502 = Open
    {"terms": {"frameworkProgramme": ["43108390"]}}  # Horizon Europe
]}}
BRU, IST = ZoneInfo("Europe/Brussels"), ZoneInfo("Europe/Istanbul")

def post(page):
    b = uuid.uuid4().hex
    parts = []
    for name, val in (("query", QUERY), ("languages", ["en"])):
        parts.append('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s.json"\r\n'
                     'Content-Type: application/json\r\n\r\n%s\r\n' % (b, name, name, json.dumps(val)))
    body = ("".join(parts) + "--%s--\r\n" % b).encode()
    req = urllib.request.Request(API % page, data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + b})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def first(m, k, default=""):
    v = m.get(k)
    return (v[0] if isinstance(v, list) and v else v) or default

def parse_dl(s, now):
    """-> (iso_str_istanbul, aware_dt) ; saat 00:00 ise yalnızca tarih."""
    d = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    if d.hour == 0 and d.minute == 0:
        return d.strftime("%Y-%m-%d"), d.replace(hour=23, minute=59, tzinfo=BRU)
    a = d.replace(tzinfo=BRU).astimezone(IST)
    return a.strftime("%Y-%m-%dT%H:%M"), a

def money(n):
    return "{:,.0f}".format(n).replace(",", ".") + " €"

def budget_text(m):
    if first(m, "budget"):                       # cascade çağrıları
        try: return "Toplam " + money(float(first(m, "budget")))
        except ValueError: return ""
    raw = first(m, "budgetOverview")
    if not raw: return ""
    try: bo = json.loads(raw)
    except ValueError: return ""
    total = grants = 0; mx = 0
    for acts in (bo.get("budgetTopicActionMap") or {}).values():
        for a in acts:
            total += sum(float(v) for v in (a.get("budgetYearMap") or {}).values())
            grants += a.get("expectedGrants") or 0
            mx = max(mx, a.get("maxContribution") or 0)
    out = []
    if total: out.append("Toplam " + money(total))
    if mx: out.append("proje başına en fazla " + money(mx))
    if grants: out.append("~%d proje" % grants)
    return "; ".join(out)

def main(out_path="eu_calls.json"):
    now = datetime.now(IST)
    results, page = [], 1
    while True:
        j = post(page)
        results += j.get("results", [])
        if page * 100 >= j.get("totalResults", 0): break
        page += 1
    seen, rows, enrich = set(), [], []
    for x in results:
        m = x["metadata"]
        key = first(m, "ccm2Id") or x["reference"]
        if key in seen: continue
        seen.add(key)
        dls = sorted(parse_dl(s, now) for s in m.get("deadlineDate", []) if s)
        future = [d for d in dls if d[1] > now]
        if not future: continue                  # tüm kesimleri geçmiş -> listeleme
        nxt = future[0][0]
        casc = first(m, "type") == "8"
        model = first(m, "deadlineModel").lower()
        kat = "İki aşamalı" if "two" in model else ("Çok kesimli" if len(dls) > 1 or "multiple" in model else "Tek aşamalı")
        title = first(m, "title"); code = first(m, "identifier")
        url = x["url"]
        if casc and first(m, "callccm2Id"):   # API'nin verdiği adres boş sayfa açar; portalın kendi bağlantısı çağrı numarasını kullanır
            url = ("https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/"
                   "competitive-calls-cs/" + first(m, "callccm2Id"))
        start = first(m, "startDate")[:10]
        rows.append(["eu",
                     "Ufuk Avrupa · " + ("Kademeli fonlama (üçüncü taraf çağrısı)" if casc else "Doğrudan çağrı (AB)"),
                     title, kat, code, start, nxt, 0, url, None,
                     "Gösterilen tarih bir sonraki son tarihtir." if len(dls) > 1 else "", ""])
        e = {"m": title, "kaynak": url, "durum": "dogrulandi"}
        b = budget_text(m)
        e["butce"] = b or "Belirtilmemiş / bulunamadı"
        if first(m, "duration"): e["sure"] = first(m, "duration")
        if len(dls) > 1:
            e["not"] = "Tüm kesimler: " + ", ".join(d[0].replace("T", " ") for d in dls)
        enrich.append(e)
    rows.sort(key=lambda r: r[6])
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated": now.isoformat(timespec="minutes"), "source": "F&T Portal Search API",
                   "rows": rows, "enrich": enrich}, f, ensure_ascii=False, indent=1)
    print("%d ham kayıt -> %d açık çağrı yazıldı: %s" % (len(results), len(rows), out_path))

if __name__ == "__main__":
    main(*sys.argv[1:2])
