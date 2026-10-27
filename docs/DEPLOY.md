# Deploying (free)

## Render (recommended)

Render's free plan runs Docker web services, with 750 instance hours a month, which is enough for one app running all
month ([Render free tier](https://render.com/docs/free)).

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com), sign up with GitHub, and choose **New → Blueprint**. Pick the repo. It reads
   `render.yaml`.
3. When asked, paste **TAVILY_API_KEY**, **GROQ_API_KEY** and **GEMINI_API_KEY**. They're stored as secrets and
   never go in the repo. Optionally set `SOURCE_URL` to the GitHub repo URL so the footer links to the code.
4. Click **Apply**. The first build takes about 3–5 minutes.
5. Open `https://<your-service>.onrender.com/healthz`. It should show `"engine": "open"`. If it shows `"demo"`, a
   key's name is misspelled or missing.

Every push to `main` redeploys automatically.

### The 30-second rule and cold starts

Free Render services sleep after 15 minutes without traffic, and the next visit takes about a minute to wake up.
To keep a reviewer from hitting that:
- **Keep it awake:** create a free monitor (for example [UptimeRobot](https://uptimerobot.com)) that requests
  `/healthz` every 5 minutes. That uses about 744 of the 750 free hours a month, so it only works for one
  always-on service.
- **Example report:** `app/demo/demo_report.json` is baked into the image, so the homepage's "Example report"
  link is instant. To refresh it:
  ```bash
  uv run --env-file .env python scripts/build_demo.py freecodecamp.org
  git add app/demo/demo_report.json && git commit -m "Refresh example report" && git push
  ```

### Data

SQLite lives in `/tmp` (`DB_PATH`), so saved reports and the answer cache reset when the service restarts or
redeploys. The example report always survives. For permanent history, use a paid disk.

## Hugging Face Spaces (paid now)

Docker Spaces need a PRO account ($9/month) for personal users ([Spaces overview](https://huggingface.co/docs/hub/en/spaces-overview));
free accounts can only create Static Spaces, which can't run this server. With PRO: create a **Docker** Space, push
this repo to it, add the three keys as **secrets**, and set `app_port: 8000` in a YAML header at the top of the
Space's README.

## Staying inside the free tiers

| Service | Free limit (check your dashboard; these change) | What CiteCheck uses |
|---|---|---|
| Tavily | 1,000 searches a month | 1 search per question per run |
| Groq | Per model: about 8,000 tokens a minute and a daily token cap | About 3,000 tokens per answer; calls rotate between `GROQ_MODELS` |
| Gemini (free) | Per-minute and daily request caps | Memory check (5 calls per report) |

CiteCheck's own guards:

| Setting | Default | Effect |
|---|---|---|
| `DAILY_CALL_BUDGET` | 30 (open) / 450 (gemini) | Engine calls per UTC day across all visitors. 30 a day keeps Tavily under ~1,000 a month. After that, answers say "daily limit used up". |
| `RUNS_PER_QUESTION` | 1 (open) / 3 (gemini) | A 10-question report = 10 searches on the open engine |
| `MEMORY_QUESTIONS` | 5 | Memory-check calls per report (0 turns it off) |
| `REPORTS_PER_HOUR` | 5 | Per visitor IP |
| `CHECKS_PER_HOUR` | 30 | Quick checks + suggestions per visitor IP |

Repeat questions on the same day come from the cache and cost nothing.

## Security notes

- **No internal fetches (SSRF):** every outgoing request, including each redirect hop, is checked. Hosts that
  resolve to private, loopback, link-local or reserved addresses (such as `169.254.169.254`, the cloud metadata
  service) are refused (`app/fetch.py::check_public`).
- **Polite fetching:** pages on *other* sites are fetched only if their robots.txt allows `CiteCheckBot`. The site
  being audited is fetched because its owner asked for the audit.
- **Keys** come only from environment variables or secrets. `.env` is git-ignored.
