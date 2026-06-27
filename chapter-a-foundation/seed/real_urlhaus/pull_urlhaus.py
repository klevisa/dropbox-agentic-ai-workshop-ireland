#!/usr/bin/env python3
"""
ONE-TIME frozen snapshot pull of REAL threat indicators from the abuse.ch URLhaus API.

URLhaus is a public defensive-security feed of malware-distribution URLs operated by
abuse.ch. This script pulls a bounded sample of recent indicators for a threat-intel
DEMO and writes them to local CSV/JSONL files. It is a one-time pull; the output is a
frozen snapshot committed alongside the demo so we never need to re-pull.

The current (2024+) URLhaus API requires an Auth-Key header on every request.

Endpoints used (all under https://urlhaus-api.abuse.ch/):
  GET  /v1/urls/recent/        -> recent malicious URLs (url/host/threat/tags/...)
  GET  /v1/payloads/recent/    -> recent file payloads (md5/sha256/file_type/signature)
  POST /v1/url/  (url=...)      -> per-URL detail incl. associated payloads (enrichment)
"""
import csv
import json
import os
import time
import urllib.parse
import urllib.request

API = "https://urlhaus-api.abuse.ch"
AUTH_KEY = "4ce9f3f9df092a88b0f42708c7b6fb28"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Bound the sample so we don't store the full dump.
MAX_URLS = 1000
MAX_PAYLOADS = 1000
ENRICH_N = 150  # number of URLs to enrich with per-URL payload detail


def _req(path, post_data=None):
    url = API + path
    data = None
    if post_data is not None:
        data = urllib.parse.urlencode(post_data).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Auth-Key", AUTH_KEY)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode())


def pull_recent_urls():
    d = _req("/v1/urls/recent/")
    assert d.get("query_status") == "ok", d
    return d.get("urls", [])[:MAX_URLS]


def pull_recent_payloads():
    d = _req("/v1/payloads/recent/")
    assert d.get("query_status") == "ok", d
    return d.get("payloads", [])[:MAX_PAYLOADS]


def enrich_urls(urls):
    """Look up per-URL detail for a subset to capture associated payloads.

    Returns mapping: url -> list of payload dicts {md5,sha256,file_type,signature}.
    """
    mapping = {}
    for u in urls[:ENRICH_N]:
        try:
            d = _req("/v1/url/", {"url": u["url"]})
        except Exception:
            continue
        if d.get("query_status") != "ok":
            continue
        pls = d.get("payloads") or []
        if pls:
            mapping[u["url"]] = [
                {
                    "md5_hash": p.get("response_md5"),
                    "sha256_hash": p.get("response_sha256"),
                    "file_type": p.get("file_type"),
                    "signature": p.get("signature"),
                }
                for p in pls
            ]
        time.sleep(0.05)
    return mapping


def write_urls_csv(urls, url_payloads):
    path = os.path.join(OUT_DIR, "urlhaus_urls.csv")
    cols = [
        "id", "url", "host", "url_status", "threat", "tags",
        "date_added", "reporter", "larted", "urlhaus_reference",
        "linked_md5", "linked_sha256",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for u in urls:
            tags = u.get("tags") or []
            pls = url_payloads.get(u["url"], [])
            w.writerow({
                "id": u.get("id"),
                "url": u.get("url"),
                "host": u.get("host"),
                "url_status": u.get("url_status"),
                "threat": u.get("threat"),
                "tags": "|".join(tags) if isinstance(tags, list) else tags,
                "date_added": u.get("date_added"),
                "reporter": u.get("reporter"),
                "larted": u.get("larted"),
                "urlhaus_reference": u.get("urlhaus_reference"),
                "linked_md5": "|".join(p["md5_hash"] for p in pls if p.get("md5_hash")),
                "linked_sha256": "|".join(p["sha256_hash"] for p in pls if p.get("sha256_hash")),
            })
    return path, len(urls)


def write_payloads_csv(payloads):
    path = os.path.join(OUT_DIR, "urlhaus_payloads.csv")
    cols = [
        "md5_hash", "sha256_hash", "file_type", "file_size",
        "signature", "firstseen", "imphash", "ssdeep", "virustotal",
        "urlhaus_download",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in payloads:
            w.writerow({c: p.get(c) for c in cols})
    return path, len(payloads)


def write_indicators_jsonl(urls, payloads, url_payloads):
    """Flatten everything to one indicator-per-line, typed.

    indicator_type in {url, host, md5, sha256}.
    """
    path = os.path.join(OUT_DIR, "urlhaus_real.jsonl")
    counts = {"url": 0, "host": 0, "md5": 0, "sha256": 0}
    seen_hosts = set()
    seen_md5 = set()
    seen_sha256 = set()
    with open(path, "w") as f:
        for u in urls:
            tags = u.get("tags") or []
            tags = tags if isinstance(tags, list) else [tags]
            f.write(json.dumps({
                "indicator_type": "url",
                "indicator": u.get("url"),
                "host": u.get("host"),
                "url_status": u.get("url_status"),
                "threat": u.get("threat"),
                "tags": tags,
                "date_added": u.get("date_added"),
                "reporter": u.get("reporter"),
                "source_ref": u.get("urlhaus_reference"),
            }) + "\n")
            counts["url"] += 1

            h = u.get("host")
            if h and h not in seen_hosts:
                seen_hosts.add(h)
                f.write(json.dumps({
                    "indicator_type": "host",
                    "indicator": h,
                    "threat": u.get("threat"),
                    "tags": tags,
                    "date_added": u.get("date_added"),
                    "reporter": u.get("reporter"),
                }) + "\n")
                counts["host"] += 1

        # payloads -> md5 + sha256 indicators
        for p in payloads:
            md5 = p.get("md5_hash")
            sha = p.get("sha256_hash")
            base = {
                "file_type": p.get("file_type"),
                "file_size": p.get("file_size"),
                "signature": p.get("signature"),
                "date_added": p.get("firstseen"),
            }
            if md5 and md5 not in seen_md5:
                seen_md5.add(md5)
                f.write(json.dumps({"indicator_type": "md5", "indicator": md5, **base}) + "\n")
                counts["md5"] += 1
            if sha and sha not in seen_sha256:
                seen_sha256.add(sha)
                f.write(json.dumps({"indicator_type": "sha256", "indicator": sha, **base}) + "\n")
                counts["sha256"] += 1

        # hashes discovered via URL enrichment (linked to a URL)
        for url, pls in url_payloads.items():
            for p in pls:
                md5 = p.get("md5_hash")
                sha = p.get("sha256_hash")
                base = {"file_type": p.get("file_type"), "signature": p.get("signature"),
                        "linked_url": url}
                if md5 and md5 not in seen_md5:
                    seen_md5.add(md5)
                    f.write(json.dumps({"indicator_type": "md5", "indicator": md5, **base}) + "\n")
                    counts["md5"] += 1
                if sha and sha not in seen_sha256:
                    seen_sha256.add(sha)
                    f.write(json.dumps({"indicator_type": "sha256", "indicator": sha, **base}) + "\n")
                    counts["sha256"] += 1
    return path, counts


def main():
    print("Pulling recent URLs...")
    urls = pull_recent_urls()
    print(f"  got {len(urls)} urls")

    print("Pulling recent payloads...")
    payloads = pull_recent_payloads()
    print(f"  got {len(payloads)} payloads")

    print(f"Enriching first {ENRICH_N} URLs with payload detail...")
    url_payloads = enrich_urls(urls)
    print(f"  {len(url_payloads)} urls had linked payloads")

    p1, n1 = write_urls_csv(urls, url_payloads)
    p2, n2 = write_payloads_csv(payloads)
    p3, counts = write_indicators_jsonl(urls, payloads, url_payloads)
    print(f"Wrote {p1} ({n1} rows)")
    print(f"Wrote {p2} ({n2} rows)")
    print(f"Wrote {p3} ({sum(counts.values())} indicators)")
    print("Indicator counts:", json.dumps(counts))

    with open(os.path.join(OUT_DIR, "_counts.json"), "w") as f:
        json.dump({"urls_csv_rows": n1, "payloads_csv_rows": n2,
                   "indicator_counts": counts}, f, indent=2)


if __name__ == "__main__":
    main()
