# Route 53 Configuration — Wave 0 (hidden-view.com)

DNS for the closed beta: point the apex `hidden-view.com` (and `www`) at the instance's Elastic IP so
Caddy can obtain a Let's Encrypt certificate and users can reach the app. The hosted zone for
`hidden-view.com` already exists in this AWS account; this doc adds two A records and verifies them.

> DEPLOYMENT-ONLY. No application change. Caddy serves the apex and redirects `www→apex`, so both hosts
> resolve to the same IP.

## Records to create

| Name | Type | Value | TTL | Purpose |
|---|---|---|---|---|
| `hidden-view.com` | **A** | `<Elastic IP>` | 300 | apex → the instance (the app) |
| `www.hidden-view.com` | **A** | `<Elastic IP>` | 300 | www → the instance (Caddy 308-redirects to the apex) |

- **Apex as a plain A record to the EIP** is the simplest correct choice. (Route 53 "alias" targets are
  for AWS resources like ALB/CloudFront; a single EC2 instance uses a normal A record to its Elastic IP.)
- **No AAAA** — we run IPv4-only for the beta (the EIP is IPv4). Add AAAA later only if you enable IPv6.
- **Low TTL (300s)** keeps cutovers fast during setup; you can raise it once stable.

## Why the Elastic IP matters
The A records point at a **fixed** Elastic IP (allocated + associated in the deployment guide §2.3). A
stop/start of the instance keeps the EIP, so DNS and the Google OAuth callback never need to change. Do
not point DNS at the instance's default public IP — that changes on stop/start.

## Create the records

**Console:** Route 53 → Hosted zones → `hidden-view.com` → **Create record** → Simple routing →
Record name empty (apex), type **A**, value = the EIP → Create. Repeat with record name `www`.

**CLI** (replace `<ZONE_ID>` and `<EIP>`):
```bash
cat > /tmp/rr.json <<'JSON'
{ "Comment": "Wave 0 apex + www",
  "Changes": [
    {"Action":"UPSERT","ResourceRecordSet":{"Name":"hidden-view.com","Type":"A","TTL":300,"ResourceRecords":[{"Value":"<EIP>"}]}},
    {"Action":"UPSERT","ResourceRecordSet":{"Name":"www.hidden-view.com","Type":"A","TTL":300,"ResourceRecords":[{"Value":"<EIP>"}]}}
  ] }
JSON
aws route53 change-resource-record-sets --hosted-zone-id <ZONE_ID> --change-batch file:///tmp/rr.json
```
Find the zone id: `aws route53 list-hosted-zones-by-name --dns-name hidden-view.com`.

## Verify BEFORE first deploy (critical for HTTPS)

Caddy completes the Let's Encrypt **HTTP-01 challenge** on port 80, which requires the name to already
resolve to this host. Confirm resolution first:
```bash
dig +short hidden-view.com        # → the EIP
dig +short www.hidden-view.com    # → the EIP
```
Both must return the EIP before running `deploy/ops/deploy.sh`. If they don't, wait for propagation
(usually minutes at TTL 300; up to the old TTL if you changed an existing record).

## After deploy
```bash
curl -I https://hidden-view.com        # valid TLS, HTTP/2 200 or an auth redirect
curl -I http://hidden-view.com         # 308 → https://hidden-view.com
curl -I https://www.hidden-view.com    # 308 → https://hidden-view.com
```
`deploy/ops/smoke-test.sh` checks the HTTPS reachability + TLS validity + HTTP→HTTPS redirect automatically.

## Registrar note
If `hidden-view.com`'s registrar is **not** Route 53, either (a) delegate the domain to this Route 53
hosted zone by setting the registrar's nameservers to the zone's NS records, or (b) create the same two A
records at the registrar's DNS instead. The app is indifferent to which DNS host is authoritative — only
that the two names resolve to the EIP.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Caddy stuck "obtaining certificate" | Name doesn't resolve to this host yet, or port 80 blocked | `dig +short hidden-view.com`; open SG 80; wait for propagation |
| `dig` returns an old IP | Cached at the previous TTL | Wait out the old TTL; verify the record value in the console |
| `www` errors | Missing www A record | Add `www.hidden-view.com` A → EIP |
| Cert issued but site unreachable | SG not allowing 443, or Caddy down | Open SG 443; `deploy/ops/smoke-test.sh`; `docker compose … logs caddy` |
