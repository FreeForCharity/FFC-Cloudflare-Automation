#!/usr/bin/env python3
"""Compare what two sets of AUTHORITATIVE nameservers answer for one domain.

Read-only. Sends ordinary DNS queries (plus one AXFR attempt, which servers
normally refuse) straight to each nameserver, with recursion off, so the answer
is the zone's own content and not anyone's cache.

Usage:
  python3 scripts/dns-zone-compare.py --domain example.org \
      --old ns1.bluehost.com,ns2.bluehost.com \
      --new dane.ns.cloudflare.com,zainab.ns.cloudflare.com \
      [--extra-names a,b,c] [--json out.json]

Names checked: a built-in list of common hosting/mail/verification labels,
anything --extra-names adds, and every name under the domain found in
certificate transparency logs (crt.sh, then certspotter as a fallback).
"""

import argparse
import json
import sys
import urllib.request

import dns.exception
import dns.message
import dns.name
import dns.query
import dns.rdatatype
import dns.resolver
import dns.flags
import dns.rcode
import dns.zone

TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV", "SOA"]

COMMON = [
    "@", "www", "mail", "webmail", "smtp", "imap", "pop", "pop3", "ftp", "sftp",
    "cpanel", "whm", "webdisk", "cpcalendars", "cpcontacts", "autodiscover",
    "autoconfig", "mx", "email", "admin", "blog", "shop", "store", "staging",
    "dev", "test", "beta", "portal", "learn", "lms", "courses", "classroom",
    "annex", "online", "school", "publications", "radio", "events", "donate",
    "calendar", "docs", "drive", "sites", "start", "groups", "m", "api", "cdn",
    "img", "images", "static", "vpn", "remote", "ns1", "ns2", "old", "new",
    "members", "login", "secure", "support", "help", "forum", "news", "podcast",
    "stream", "live", "video", "media", "library", "store", "go", "link",
    "_dmarc", "_domainkey", "google._domainkey", "default._domainkey",
    "selector1._domainkey", "selector2._domainkey", "k1._domainkey",
    "s1._domainkey", "s2._domainkey", "mail._domainkey", "dkim._domainkey",
    "bh._domainkey", "_acme-challenge", "_github-pages-challenge-freeforcharity",
    "_globalsign-domain-verification", "_mta-sts", "mta-sts", "_smtp._tls",
    "_autodiscover._tcp", "_caldav._tcp", "_caldavs._tcp", "_carddav._tcp",
    "_carddavs._tcp", "_imap._tcp", "_imaps._tcp", "_pop3._tcp", "_pop3s._tcp",
    "_submission._tcp", "_sip._tcp", "_sip._tls", "_sipfederationtls._tcp",
    "lyncdiscover", "sip", "enterpriseenrollment", "enterpriseregistration",
    "_amazonses", "_spf", "zzz-nonexistent-wildcard-probe",
]


def ct_names(domain):
    found = set()
    sources = [
        f"https://crt.sh/?q=%25.{domain}&output=json",
        f"https://api.certspotter.com/v1/issuances?domain={domain}"
        "&include_subdomains=true&expand=dns_names",
    ]
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dns-zone-compare"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
        except Exception as e:  # noqa: BLE001 -- report and fall through
            print(f"CT source failed: {url}: {e}", file=sys.stderr)
            continue
        for item in data:
            vals = item.get("name_value", "").split("\n") if "name_value" in item else item.get("dns_names", [])
            for v in vals:
                v = v.strip().lower().rstrip(".")
                if v.startswith("*."):
                    v = v[2:]
                if v == domain:
                    found.add("@")
                elif v.endswith("." + domain):
                    found.add(v[: -len(domain) - 1])
        print(f"CT source ok: {url.split('?')[0]} -> {len(found)} names so far", file=sys.stderr)
    return found


def ns_ips(hosts):
    out = {}
    res = dns.resolver.Resolver()
    for h in hosts:
        try:
            out[h] = [a.to_text() for a in res.resolve(h, "A")]
        except Exception as e:  # noqa: BLE001
            out[h] = []
            print(f"cannot resolve nameserver {h}: {e}", file=sys.stderr)
    return out


def ask(server_ip, fqdn, rtype):
    q = dns.message.make_query(fqdn, rtype)
    q.flags &= ~dns.flags.RD
    try:
        r = dns.query.udp(q, server_ip, timeout=5)
        if r.flags & dns.flags.TC:
            r = dns.query.tcp(q, server_ip, timeout=8)
    except dns.exception.Timeout:
        return {"status": "TIMEOUT", "records": []}
    except Exception as e:  # noqa: BLE001
        return {"status": f"ERROR {e}", "records": []}
    recs = []
    want = dns.rdatatype.from_text(rtype)
    for rrset in r.answer:
        if rrset.rdtype == want or (rrset.rdtype == dns.rdatatype.CNAME):
            for rd in rrset:
                txt = rd.to_text()
                if rrset.rdtype == dns.rdatatype.SOA:
                    # serials and timers always differ between providers; keep mname/rname
                    txt = " ".join(txt.split()[:2])
                recs.append(f"{dns.rdatatype.to_text(rrset.rdtype)} {rrset.ttl} {txt}")
    status = dns.rcode.to_text(r.rcode())
    if status == "NOERROR" and not recs:
        status = "NODATA"
    if not (r.flags & dns.flags.AA) and status in ("NOERROR", "NODATA"):
        status += " (not authoritative)"
    return {"status": status, "records": sorted(set(recs))}


def strip_ttl(recs):
    return sorted({" ".join([r.split(" ", 2)[0], r.split(" ", 2)[2]]) for r in recs})


def try_axfr(domain, ip):
    try:
        z = dns.zone.from_xfr(dns.query.xfr(ip, domain, timeout=10, lifetime=20))
        return sorted(str(n) for n in z.nodes.keys())
    except Exception as e:  # noqa: BLE001
        return f"refused/failed: {type(e).__name__}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--extra-names", default="")
    ap.add_argument("--json")
    a = ap.parse_args()
    domain = a.domain.lower().rstrip(".")
    old = [h.strip() for h in a.old.split(",") if h.strip()]
    new = [h.strip() for h in a.new.split(",") if h.strip()]

    sources = {}
    for n in COMMON:
        sources.setdefault(n, set()).add("common-list")
    for n in [x.strip() for x in a.extra_names.split(",") if x.strip()]:
        sources.setdefault(n, set()).add("extra")
    for n in ct_names(domain):
        sources.setdefault(n, set()).add("ct-logs")

    old_ips = ns_ips(old)
    new_ips = ns_ips(new)
    print("Old NS:", json.dumps(old_ips))
    print("New NS:", json.dumps(new_ips))

    axfr = {h: try_axfr(domain, ips[0]) for h, ips in {**old_ips, **new_ips}.items() if ips}
    for h, v in axfr.items():
        if isinstance(v, list):
            print(f"AXFR {h}: ALLOWED, {len(v)} names")
            for n in v:
                sources.setdefault(n if n != "@" else "@", set()).add(f"axfr:{h}")
        else:
            print(f"AXFR {h}: {v}")

    results = []
    for name in sorted(sources):
        fqdn = domain if name == "@" else f"{name}.{domain}"
        for t in TYPES:
            if t == "SOA" and name != "@":
                continue
            per = {}
            for h, ips in list(old_ips.items()) + list(new_ips.items()):
                per[h] = ask(ips[0], fqdn, t) if ips else {"status": "NO-IP", "records": []}
            o_sets = {h: tuple(strip_ttl(per[h]["records"])) for h in old}
            n_sets = {h: tuple(strip_ttl(per[h]["records"])) for h in new}
            o_union = sorted({r for s in o_sets.values() for r in s})
            n_union = sorted({r for s in n_sets.values() for r in s})
            if not o_union and not n_union:
                continue
            row = {
                "name": name, "type": t, "found_by": sorted(sources[name]),
                "old": o_union, "new": n_union,
                "old_consistent": len(set(o_sets.values())) == 1,
                "new_consistent": len(set(n_sets.values())) == 1,
                "old_ttls": sorted({r.split(' ')[1] for h in old for r in per[h]['records']}),
                "new_ttls": sorted({r.split(' ')[1] for h in new for r in per[h]['records']}),
                "same": o_union == n_union,
                "raw": per,
            }
            results.append(row)

    print("\n================ DIFFERENCES ================")
    for r in results:
        if r["same"] and r["old_consistent"] and r["new_consistent"]:
            continue
        print(f"\n### {r['name']} {r['type']}   (found by: {', '.join(r['found_by'])})")
        print("  OLD:", r["old"] or "(none)", "ttl", r["old_ttls"], "" if r["old_consistent"] else "  !! old NS disagree")
        print("  NEW:", r["new"] or "(none)", "ttl", r["new_ttls"], "" if r["new_consistent"] else "  !! new NS disagree")
    print("\n================ IDENTICAL ================")
    for r in results:
        if r["same"] and r["old_consistent"] and r["new_consistent"]:
            print(f"  {r['name']} {r['type']}: {r['old']}  ttl old {r['old_ttls']} new {r['new_ttls']}")
    checked = sorted(sources)
    print(f"\nNames checked ({len(checked)}): {', '.join(checked)}")
    print("CT-log names:", ", ".join(sorted(n for n, s in sources.items() if "ct-logs" in s)) or "(none)")
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"domain": domain, "old": old_ips, "new": new_ips, "axfr": axfr,
                       "names": {k: sorted(v) for k, v in sources.items()}, "results": results}, fh, indent=1)


if __name__ == "__main__":
    main()
