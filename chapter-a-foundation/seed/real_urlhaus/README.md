# Real URLhaus Threat-Indicator Snapshot (FROZEN)

This directory holds a **one-time, frozen snapshot** of REAL malware-distribution
indicators pulled from the [abuse.ch URLhaus](https://urlhaus.abuse.ch/) public
defensive-security feed. It is intended for the threat-intel demo as a realistic
counterpart to the synthetic data.

**This is a frozen snapshot — do NOT re-pull.** The files here are the source of truth.

## Pull metadata

- **Pull date:** 2026-06-10 (data timestamps are 2026-06-11 UTC; URLhaus uses UTC)
- **Pull method:** **LOCAL** — direct HTTPS from the laptop. Outbound egress to
  `urlhaus-api.abuse.ch` worked, so the Databricks egress fallback was NOT needed.
- **Auth:** abuse.ch Auth-Key sent as the `Auth-Key` HTTP header on every request
  (required by the current 2024+ URLhaus API).
- **Pull script:** [`pull_urlhaus.py`](./pull_urlhaus.py) (kept for provenance; do not re-run).

## Endpoints used

All under base `https://urlhaus-api.abuse.ch/`:

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/urls/recent/` | GET | Recent malicious URLs (url, host, threat, tags, status, reporter) |
| `/v1/payloads/recent/` | GET | Recent file payloads (md5, sha256, file_type, signature, file_size) |
| `/v1/url/` (`url=<value>`) | POST | Per-URL enrichment to capture payloads linked to a specific URL |

Note: the API rejects GET on POST-only lookup endpoints with
`{"query_status":"http_get_expected"}` and vice-versa. The two `recent/` feeds are GET;
the `/v1/url/` lookup is POST form-encoded.

## Files

| File | Rows | Description |
|---|---|---|
| `urlhaus_urls.csv` | 929 | Recent malicious URLs. Includes `linked_md5`/`linked_sha256` for the 111 URLs (of the first 150) that had associated payloads. |
| `urlhaus_payloads.csv` | 1000 | Recent file payloads (file hashes + metadata). |
| `urlhaus_real.jsonl` | 2773 | All indicators flattened to one-per-line, typed by `indicator_type`. |
| `_counts.json` | — | Machine-readable row/indicator counts. |
| `pull_urlhaus.py` | — | The pull script (provenance only). |

## Row counts by indicator type

From `urlhaus_real.jsonl` (deduplicated within each type):

| indicator_type | count |
|---|---|
| url | 929 |
| host (domain/IP) | 428 |
| md5 | 708 |
| sha256 | 708 |
| **total** | **2773** |

`host` indicators are deduped hosts extracted from the URL feed (mix of domains and bare
IPv4). `md5`/`sha256` come from the recent-payloads feed plus URL enrichment.

## Field schema

### `urlhaus_urls.csv`
`id, url, host, url_status, threat, tags, date_added, reporter, larted, urlhaus_reference, linked_md5, linked_sha256`

- `url_status`: `online` | `offline`
- `threat`: URLhaus threat class (all rows in this snapshot are `malware_download`)
- `tags`: pipe-delimited (`|`) — e.g. `32-bit|elf|mips|Mozi`, `ClearFake`
- `date_added`: e.g. `2026-06-11 04:41:09 UTC`
- `larted`: whether an abuse report was sent (`true`/`false`)
- `linked_md5` / `linked_sha256`: pipe-delimited hashes of payloads served by that URL
  (populated only for enriched rows that had payloads)

### `urlhaus_payloads.csv`
`md5_hash, sha256_hash, file_type, file_size, signature, firstseen, imphash, ssdeep, virustotal, urlhaus_download`

- `file_type`: e.g. `elf`, `html`, `exe`
- `signature`: malware family if known — e.g. `Mirai`, `Mozi`, `Gafgyt`,
  `Ransomware.BlackMatter`, `CoinMiner`, `Formbook`, `Vidar` (often empty/`none`)
- `urlhaus_download`: abuse.ch download link for the sample

### `urlhaus_real.jsonl`
Each line: `{indicator_type, indicator, ...type-specific fields}`
- `indicator_type` ∈ {`url`, `host`, `md5`, `sha256`}
- `url`: `host, url_status, threat, tags, date_added, reporter, source_ref`
- `host`: `threat, tags, date_added, reporter`
- `md5`/`sha256`: `file_type, file_size, signature, date_added` (and `linked_url` when
  discovered via URL enrichment)

## Snapshot characteristics

- threat: 100% `malware_download` (URLhaus only tracks malware-distribution URLs)
- url_status: 361 online / 568 offline
- top payload signatures: Mirai (224), Mozi (132), Gafgyt (32) — IoT botnet families
  dominate the recent feed, plus some ransomware/infostealer/coinminer samples

## Defanging

These are REAL live/recently-live malicious indicators. When displaying them in docs,
slides, or chat, defang them (e.g. `hxxp://`, `evil[.]com`, `1.2.3[.]4`). The stored
files keep raw values so they remain machine-usable for the demo pipeline.
