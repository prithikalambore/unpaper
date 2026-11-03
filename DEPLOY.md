# Deploying Unpaper

This runs the whole app on one Linux server with Docker. Caddy handles HTTPS automatically
(free Let's Encrypt certificates). Expect about 15 minutes once you have a server and a domain.

## What you need

- **A server** with Docker installed: Ubuntu 24.04 with **2 vCPUs, 4 GB RAM, 20 GB disk** is
  plenty for testing with real users. Any VPS provider works (Hetzner, DigitalOcean, Linode,
  AWS Lightsail and so on). Install Docker with the official guide:
  <https://docs.docker.com/engine/install/ubuntu/>.
- **A domain or subdomain**, for example `pdf.yourdomain.com`.
- Ports **80 and 443** open to the internet (plus 22 for SSH).

## Steps

1. **Point the domain at the server.** At your DNS provider, add an `A` record
   (and an `AAAA` record if the server has IPv6): `pdf.yourdomain.com → <server IP>`.
   Wait until `ping pdf.yourdomain.com` shows the server's IP.

2. **Open the firewall** (on Ubuntu with ufw):
   ```bash
   sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp && sudo ufw enable
   ```
   Also allow 80 and 443 in your provider's cloud firewall, if it has one.

3. **Copy the project to the server**, either `git clone <your repo>` or:
   ```bash
   rsync -av --exclude node_modules --exclude .venv --exclude .next ~/Projects/pdf-to-word/ user@server:~/pdf-to-word/
   ```

4. **Configure it:**
   ```bash
   cd ~/pdf-to-word
   cp .env.example .env
   nano .env        # set DOMAIN=pdf.yourdomain.com and ACME_EMAIL=you@yourdomain.com
   ```

5. **Start it:**
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```
   The first build takes a few minutes. Caddy fetches the HTTPS certificate on the first request.

6. **Open `https://pdf.yourdomain.com`** and convert a test file. That URL is the one to share.

## Seeing whether people use it

```bash
docker compose -f docker-compose.prod.yml exec backend python -m app.stats             # last 30 days
docker compose -f docker-compose.prod.yml exec backend python -m app.stats --days 7
```

This shows daily unique visitors, uploads, successful and failed conversions, pages, which mode
people chose, error types, how often the rate limit kicked in, "did it work for you?" answers and
the comments people wrote. No cookies, IP addresses, file names or document contents are stored.
The privacy page at `/privacy` tells users exactly this.

Back up the stats file:

```bash
docker compose -f docker-compose.prod.yml cp backend:/data/stats.db ./stats-backup.db
```

## Day-to-day

| Task | Command |
| --- | --- |
| Live logs | `docker compose -f docker-compose.prod.yml logs -f` |
| Status | `docker compose -f docker-compose.prod.yml ps` |
| Deploy an update | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| Restart | `docker compose -f docker-compose.prod.yml restart` |
| Stop | `docker compose -f docker-compose.prod.yml down` (stats and certificates are kept in volumes) |

For uptime alerts, point any free uptime monitor at `https://pdf.yourdomain.com/api/health`.

## Built-in protection for a free, login-free tool

| Protection | Default | Setting in `.env` |
| --- | --- | --- |
| Conversions per visitor per hour / day | 20 / 60 | `RATE_LIMIT_PER_HOUR`, `RATE_LIMIT_PER_DAY` |
| Conversions running at once per visitor | 1 | `MAX_ACTIVE_JOBS_PER_CLIENT` |
| Conversions running at once overall (rest queue) | 2 | `MAX_CONCURRENT_JOBS` |
| Queue length (further uploads get "server busy") | 20 | `MAX_QUEUED_JOBS` |
| Max file size (app / proxy hard cap) | 25 MB / 26 MB | `MAX_UPLOAD_MB`, `MAX_REQUEST_MB` |
| Max pages | 300 | `MAX_PAGES` |
| Time limit per conversion | 180 s | `CONVERSION_TIMEOUT_SECONDS` |
| Backend CPU / RAM | 2 CPUs / 2 GB | `BACKEND_CPUS`, `BACKEND_MEMORY` |

A "visitor" is an IP address (IPv6 is grouped per /64). Caddy overwrites any client-supplied
`X-Forwarded-For` header, so the limits can't be bypassed by spoofing it. People behind one shared
IP, such as an office or university network, share one allowance. Raise the limits if that becomes
a problem.

Other protections in place:
- HTTPS only, with HTTP redirected to HTTPS.
- HSTS, a Content-Security-Policy, and no-framing and no-sniffing headers.
- Containers run read-only as non-root users, with no-new-privileges set.
- Uploads and results live in RAM-backed `tmpfs` and are deleted after download or within 10 minutes.
- Docker log rotation is set to 10 MB × 3 files per container.
- The backend is never exposed directly. Only Caddy listens on the internet.

## Capacity

A typical text PDF takes under 0.1 s per page (a 163-page document took about 11 s in testing).
Scanned pages that need OCR take a few seconds each. With the defaults, two conversions run at once and others wait in a queue that the UI shows
as "Waiting for a free converter". For more throughput on a bigger server, raise
`MAX_CONCURRENT_JOBS`, `BACKEND_CPUS` and `BACKEND_MEMORY` together. Allow roughly 1 CPU and 1 GB
of RAM per concurrent conversion.

## Before you share the link

- Open a few converted files in **Microsoft Word** and **Apple Pages** on your own machines. The
  output has been checked in LibreOffice and macOS Quick Look but not in those two apps.
- Search engines are asked not to index the site (`robots: noindex` in
  `frontend/src/app/layout.tsx`), which suits a test with people you share the link with. Remove
  it if you want the tool to be found through search.
- The tool doesn't need cookies or tracking, so no cookie banner is required. The `/privacy` page
  describes the data handling. If you operate it as a business in the EU or UK, add your name and
  contact details to that page.
