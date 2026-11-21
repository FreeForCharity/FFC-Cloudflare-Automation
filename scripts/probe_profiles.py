#!/usr/bin/env python3
"""Research probe: which public nonprofit profile pages show 501(c)(3) status, mission,
website and social links for a charity, signed out? Read-only, stdlib only."""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
EINS = {"462471893": "Free For Charity", "991579845": "Restorative Expressive Arts Corp", "530196605": "American Red Cross"}
SOCIAL = re.compile(r"https?://(?:www\.)?(facebook|instagram|linkedin|twitter|x|youtube|tiktok)\.com/[^\s\"'<>]+", re.I)
OUT = []


def p(s=""):
    OUT.append(s)
    print(s, flush=True)


def fetch(url, timeout=40, accept="text/html,application/json;q=0.9,*/*;q=0.8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2_500_000), r.geturl()
    except urllib.error.HTTPError as e:
        try:
            b = e.read(3000)
        except Exception:
            b = b""
        return e.code, b, url
    except Exception as e:  # noqa: BLE001
        return None, str(e).encode(), url


def meta(txt, name):
    m = re.search(r'<meta[^>]+(?:name|property)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(name), txt, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']%s["\']' % re.escape(name), txt, re.I)
    return html.unescape(m.group(1))[:220] if m else ""


def analyse(label, url, org_name):
    s, b, final = fetch(url)
    txt = b.decode("utf-8", "replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", txt, re.S | re.I)
    title = html.unescape(title.group(1).strip())[:100] if title else ""
    host = urllib.parse.urlparse(final).netloc
    socials = sorted(set(m.group(0).rstrip("\\/.,);") for m in SOCIAL.finditer(txt)))
    socials = [x for x in socials if not re.search(r"(sharer|share\?|intent/tweet|/charitynavigator|/candid|/every|/endaoment|/give\.org|/bbb|/greatnonprofits|/propublica|/idealist|/pledge|/guidestar|/gofundme|/paypal|/benevity)", x, re.I)]
    ldjson = re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', txt, re.S | re.I)
    ld_bits = []
    for blob in ldjson[:3]:
        try:
            d = json.loads(blob)
            ld_bits.append({k: (str(v)[:120]) for k, v in (d if isinstance(d, dict) else {}).items() if k in ("@type", "name", "url", "sameAs", "description", "taxID", "nonprofitStatus")})
        except Exception:
            pass
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", txt, flags=re.S | re.I)
    plain = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", plain)))
    has501 = bool(re.search(r"501\s*\(c\)\s*\(3\)", txt))
    mission = ""
    m = re.search(r"(Mission|Our mission|mission statement)[^A-Za-z]{0,20}(.{40,240})", plain)
    if m:
        mission = m.group(2)[:200]
    sign_in = bool(re.search(r"(sign in|log in|create (a )?(free )?account) to (view|see|unlock)", plain, re.I))
    name_hit = org_name.split()[0].lower() in plain.lower() or org_name.split()[0].lower() in txt.lower()
    p(f"- **{label}** HTTP {s} final={final[:120]}")
    p(f"    title={title!r} name_on_page={name_hit} 501c3_text={has501} sign_in_wall={sign_in} plain_len={len(plain)}")
    p(f"    og:description={meta(txt, 'og:description') or meta(txt, 'description')!r}")
    if mission:
        p(f"    mission_snippet={mission!r}")
    if socials:
        p(f"    socials={socials[:8]}")
    if ld_bits:
        p(f"    ld+json={ld_bits}")
    # outbound non-host links that look like the charity's own site
    ext = sorted(set(re.findall(r'href=["\'](https?://[^"\']+)', txt)))
    ext = [e for e in ext if host.split(".")[-2] not in e and not SOCIAL.match(e) and not re.search(r"(google|gstatic|cloudflare|apple\.com|cdn|fonts|jsdelivr|schema\.org|w3\.org|amazon|cloudinary|irs\.gov|candid\.org|guidestar|propublica|charitynavigator|bbb\.org|give\.org|youtube|vimeo|hubspot|typekit|stripe|paypal|wordpress|elementor|onetrust|doubleclick|segment)", e, re.I)]
    if ext:
        p(f"    other_outbound_links={ext[:10]}")
    return s, txt


for ein, name in EINS.items():
    d = f"{ein[:2]}-{ein[2:]}"
    p(f"\n## {ein} {name}")
    # APIs that are free/no-key
    s, b, _ = fetch(f"https://partners.every.org/v0.2/nonprofit/{ein}", accept="application/json")
    if s == 200:
        o = json.loads(b)["data"]["nonprofit"]
        p(f"- **Every.org API** websiteUrl={o.get('websiteUrl')!r} description={str(o.get('description'))[:160]!r} descriptionLong={str(o.get('descriptionLong'))[:160]!r} hasAdmin={o.get('hasAdmin')} profileUrl={o.get('profileUrl')}")
        slug_every = o.get("profileUrl")
    else:
        p(f"- **Every.org API** HTTP {s}")
        slug_every = None
    for api in (f"https://api.endaoment.org/v1/orgs/ein/{ein}", f"https://api.endaoment.org/v2/orgs/{ein}", f"https://api.endaoment.org/v1/orgs/{ein}"):
        s, b, _ = fetch(api, accept="application/json")
        if s == 200:
            try:
                o = json.loads(b)
                if isinstance(o, list):
                    o = o[0] if o else {}
                keep = {k: o.get(k) for k in ("name", "ein", "website", "description", "isCompliant", "nonComplianceReasons", "claimed", "twitterUrl", "facebookUrl", "instagramUrl", "linkedinUrl", "id", "nteeCode")}
                p(f"- **Endaoment API** {api}: {json.dumps(keep)[:700]}")
            except Exception as e:  # noqa: BLE001
                p(f"- **Endaoment API** {api}: 200 but parse failed {e}")
            break
        p(f"- **Endaoment API** {api}: HTTP {s} {b[:120]!r}")
    pages = [
        ("Charity Navigator", f"https://www.charitynavigator.org/ein/{ein}"),
        ("Every.org page", slug_every or f"https://www.every.org/ein/{ein}"),
        ("Endaoment page", f"https://app.endaoment.org/orgs/{d}"),
        ("GuideStar (Candid)", f"https://www.guidestar.org/profile/{d}"),
        ("Give.org search", f"https://give.org/search?term={urllib.parse.quote(name)}"),
        ("GreatNonprofits search", f"https://greatnonprofits.org/search?keyword={urllib.parse.quote(name)}"),
        ("Pledge", f"https://www.pledge.to/organizations/{ein}"),
        ("Idealist search", f"https://www.idealist.org/en/organizations?q={urllib.parse.quote(name)}"),
        ("GoFundMe charity search", f"https://www.gofundme.com/charity/search?q={urllib.parse.quote(name)}"),
        ("PayPal Giving Fund search", f"https://www.paypal.com/fundraiser/hub/charities?q={urllib.parse.quote(name)}"),
        ("Instrumentl 990", f"https://www.instrumentl.com/990-report/search?q={ein}"),
        ("TaxExemptWorld", f"https://www.taxexemptworld.com/search.asp?type=e&criteria={ein}"),
        ("GiveRadar lookup", f"https://giveradar.com/nonprofit/{ein}"),
        ("CharityNavigator legacy", f"https://www.charitynavigator.org/index.cfm?bay=search.profile&ein={ein}"),
    ]
    for label, url in pages:
        analyse(label, url, name)
        time.sleep(0.5)

with open("probe-profiles.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(OUT) + "\n")
