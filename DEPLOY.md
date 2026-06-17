# Deploy — free cloud cron (no laptop required)

A local `crontab` needs your machine on at 8/13/18/22. Here are the no-laptop
options, easiest first.

---

## Option 1 — GitHub Actions (recommended: free, no server) ✅ already wired up

`.github/workflows/monitor.yml` runs the monitor on a schedule on GitHub's
runners, and **commits `seen.json` back to the repo** so dedup state survives
between runs (cloud cron disks are otherwise wiped each run → you'd get spammed
the same deals every time).

### One-time setup

1. **Create a repo and push** (it's not on GitHub yet):
   ```bash
   cd refurbed-monitor
   # git is already initialised with an initial commit (see below)
   gh repo create refurbed-monitor --private --source=. --push
   # …or manually:
   #   create an empty private repo on github.com, then:
   #   git remote add origin git@github.com:<you>/refurbed-monitor.git
   #   git push -u origin main
   ```
   > Use a **private** repo (the schedule still works on private repos; free tier
   > gives 2000 Action-minutes/month — this uses ~600).

2. **Add your email secrets** — repo → **Settings → Secrets and variables →
   Actions → New repository secret**. Add:
   - `SMTP_USER` — your Gmail address
   - `SMTP_PASS` — Gmail **App Password** (16 chars)
   - `ALERT_TO` — where to send alerts
   - `GEMINI_API_KEY` — for AI ranking ([aistudio.google.com/apikey](https://aistudio.google.com/apikey); optional — falls back to deterministic ranking)
   - *(optional)* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

> **Two workflows ship in `.github/workflows/`:** `monitor.yml` (full deep crawl,
> 4×/day) and `monitor-light.yml` (fast cheap-config scan every 20 min to catch
> steals). They share a concurrency group so they never clash over `seen.json`.
> The frequent light scan needs unlimited Action minutes → make the repo
> **public** (`gh repo edit --visibility public`); secrets stay encrypted and
> your email is masked in logs, so nothing sensitive is exposed.

3. **Test it now** — repo → **Actions** tab → "refurbed MacBook monitor" → **Run
   workflow**. First run emails everything found, then commits `seen.json`.
   Subsequent scheduled runs only email new finds.

### Reliable scheduling via cron-job.org
GitHub's own `schedule:` cron is **best-effort and throttled** — `*/20` actually
fires every ~40 min–1h45 and slots get skipped. So scheduling is driven by
**[cron-job.org](https://cron-job.org)** (free) instead, which calls GitHub's
`workflow_dispatch` REST API on time. The workflows therefore only declare
`workflow_dispatch` (no `schedule:`).

**1. Create a fine-grained GitHub token** —
github.com → Settings → Developer settings → **Fine-grained tokens** → Generate:
- Repository access → **Only select repositories** → `refurbed-monitor`
- Permissions → Repository → **Actions: Read and write** (Metadata: Read is auto)
- Copy the `github_pat_…` token.

**2. Add ONE cron-job.org job** (Account → set timezone Europe/Zagreb first).
There is now a single targeted scan (no more light/full split):

| | SCAN |
|---|---|
| URL | `https://api.github.com/repos/ivankikic/refurbed-monitor/actions/workflows/monitor.yml/dispatches` |
| Schedule | every 15 min (`*/15` or "Every 15 minutes") |

In the job's *Advanced → request settings*:
- Method: **POST**
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer github_pat_…`
  - `X-GitHub-Api-Version: 2022-11-28`
- Body: `{"ref":"main"}`

A successful call returns **HTTP 204**; the run then shows in the Actions tab as
a `workflow_dispatch` event. (Verified working.)

### Two caveats (both easy)
- **Delays:** scheduled Actions can start a few minutes late under load. Fine for
  a deal monitor.
- **Auto-disable:** GitHub disables scheduled workflows after **60 days of repo
  inactivity**. The bot's `seen.json` commits may not reset that timer, so if it
  ever goes quiet, just push any commit (or click *Run workflow*) to re-arm it.

### Don't want commit noise in history?
Swap the "Persist dedup state" step for `actions/cache` keyed on a rolling date,
or accept the occasional re-alert if state is lost. Commit-back is the most
reliable, so it's the default.

---

## Option 2 — Oracle Cloud Always-Free VM (free forever, "just like your laptop")

A real always-on Ubuntu box with a real `crontab` and a persistent disk → **zero
code changes**, `seen.json` just works on local disk.

1. Sign up at cloud.oracle.com (needs a card for identity check; the *Always
   Free* Ampere/A1 or micro VM isn't charged). Create an **Always Free** Ubuntu
   VM.
2. SSH in, then:
   ```bash
   sudo apt update && sudo apt install -y python3-venv git
   git clone <your repo> refurbed-monitor && cd refurbed-monitor
   python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
   cp .env.example .env && nano .env       # add SMTP creds
   crontab -e
   # add:  0 8,13,18,22 * * *  /home/ubuntu/refurbed-monitor/run.sh
   ```
   (VM timezone is usually UTC — `sudo timedatectl set-timezone Europe/Zagreb` if
   you want local times.)

Heavier setup than Option 1, but it's the closest thing to "my laptop, always
on" and it's free.

---

## Option 3 — cheap paid, dead simple (~€4–5/mo)

- **Hetzner CX22** (~€4/mo): a tiny VPS, same steps as Option 2.
- **PythonAnywhere "Hacker" ($5/mo):** has a "Scheduled tasks" UI + persistent
  files (so `seen.json` just works). ⚠️ The **free** PythonAnywhere tier can only
  reach whitelisted sites, and refurbed.hr isn't on it — so you need the paid tier
  to scrape it.

---

### Which should you pick?
Start with **Option 1 (GitHub Actions)** — it's free, already configured, and
takes ~10 minutes (push + 3 secrets). Move to Option 2/3 only if you outgrow the
Actions limits or want a general-purpose box.
