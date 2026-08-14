# KI-Journalismus Digest

An automated daily email digest that monitors AI news relevant to journalism, curates it with Claude, and delivers it every morning at 8:45 am (CEST).

## Problem

Following AI developments in journalism requires monitoring dozens of sources daily — RSS feeds, newsletters, LinkedIn digests. Manually scanning them is time-consuming and easy to skip. This project automates the full pipeline: fetch → curate → summarize → send.

## Architecture

```
[GitHub Actions — 6:45 UTC / 8:45 CEST]
        ↓
[digest.py]
  1. Fetch Inoreader "KI" folder (public HTML) → extract full article text via trafilatura
  2. Connect to Gmail via IMAP → read emails from "AI News" label (last 24h)
  3. Fetch a fixed list of AI-in-journalism blogs/newsletters (RSS where available,
     HTML scraping as fallback) → see "Additional Fixed Sources" below
  4. Send all sources to Claude API → structured HTML digest
  5. Send digest email via Gmail SMTP
```

## Repository Contents

| File | Purpose |
|------|---------|
| `digest.py` | Main script — fetches sources, calls Claude, sends email |
| `requirements.txt` | Python dependencies |
| `editorial_guidelines.md` | Defines what belongs in each digest section (editable without touching Python) |
| `.github/workflows/digest.yml` | GitHub Actions cron workflow |

## Setup

### 1. Fork or clone this repository

```bash
git clone https://github.com/nepomunich/ki-digest.git
cd ki-digest
```

### 2. Configure GitHub Secrets

In your repository go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|--------|-------|
| `GMAIL_ADDRESS` | Your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 16-character Gmail App Password (requires 2FA — generate at myaccount.google.com/apppasswords) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (console.anthropic.com) |

> **Important:** Copy the API key directly from the Anthropic console into the GitHub Secret field. Do not relay it through any chat interface — character encoding issues will cause authentication failures.

### 3. Configure Inoreader

The script reads your Inoreader public HTML stream. Update `INOREADER_URL` in `digest.py` with your own stream URL:

```
https://www.inoreader.com/stream/user/YOUR_USER_ID/tag/YOUR_TAG/view/html?cs=m
```

Find your user ID and tag name in your Inoreader account URL.

### 4. Configure Gmail label

Create a Gmail label called **"AI News"** and apply it to the newsletters you want included. The script reads only this label — no other inbox mail is touched.

### 4b. Additional Fixed Sources (optional)

`digest.py` also pulls from a fixed list of AI-in-journalism blogs/newsletters, defined in `RSS_SOURCES` and `AIFORNEWSROOM_URL`:

| Source | Method | Notes |
|--------|--------|-------|
| AI for Newsroom (aifornewsroom.in) | HTML scrape | No RSS feed available |
| The AI Journalist (Steady) | RSS | `steady.page/de/aijournalist/rss` — the plain publication URL is behind Cloudflare bot protection |
| News Product Alliance Blog | RSS | Squarespace `?format=rss` |
| Lars Adrian Giske (Substack) | RSS | Standard `/feed` |
| JournalismAI (Polis/LSE) | RSS | Squarespace `?format=rss` |
| RJI (Reynolds Journalism Institute) | RSS | `rjionline.org/feed/` |
| Nieman Lab | RSS + keyword filter | General feed filtered to AI-related keywords (their own AI tag feed is too sparse) |

Poynter was evaluated but dropped — its feed is behind Cloudflare bot protection and no working alternative RSS was found. These sources post irregularly, so entries older than ~2 weeks are filtered out (either in Python for date-sortable feeds, or left to Claude's judgment via the `ALTER` field for feeds without reliable dates). To add or remove a source, edit `RSS_SOURCES` in `digest.py` — no other changes needed for standard RSS feeds.

### 5. Set delivery recipient

In `digest.py`, update:
```python
RECIPIENT = "your@email.com"
```

### 6. Adjust delivery time

In `.github/workflows/digest.yml`, the cron runs at `45 6 * * *` (6:45 UTC = 8:45 CEST in summer). For winter (CET), change to `45 7 * * *`.

## Usage

### Automatic (GitHub Actions)

The workflow triggers automatically at 6:45 UTC every day. No Mac or local machine required. Monitor runs at: `https://github.com/YOUR_USERNAME/ki-digest/actions`

### Manual trigger

In GitHub: **Actions → KI-Journalismus Digest → Run workflow**

### Local test run

```bash
pip install -r requirements.txt

# Set credentials
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export ANTHROPIC_API_KEY="sk-ant-..."

python digest.py
```

Or create a `.env` file (never commit this):
```
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
ANTHROPIC_API_KEY=sk-ant-...
```

## Expected Output

A German HTML email with up to five sections (sections with no qualifying content are omitted):

- **KI im Journalismus** — AI use cases inside newsrooms and editorial workflows only (see `editorial_guidelines.md` for exact criteria)
- **KI-Nachrichten USA** — major AI news from the US: new models, regulation, infrastructure
- **KI-Nachrichten Europa** — major AI news from Europe
- **Konferenzen & Veranstaltungen** — AI-in-journalism conference announcements
- **Empfehlung** — one blog, newsletter, or account worth following

Each entry: bold title, max 4 sentences with specific details (model names, numbers, workflows), source link. Max 3 entries per section.

See `sample_output.html` for a real example.

## Editorial Guidelines

The file `editorial_guidelines.md` controls what Claude considers relevant for the "KI im Journalismus" section. You can update it without touching Python:

- Entries that qualify: newsrooms using AI in editorial workflows (reporting, research, automation, publishing)
- Entries that do not qualify: tech companies using AI for their own products, corporate intelligence products, general AI announcements without direct newsroom context

## Limitations

- **JavaScript-rendered pages**: Sites like OpenAI.com require a headless browser — trafilatura cannot extract their content. The script falls back to the Inoreader teaser snippet for these articles.
- **Paywalled articles**: Full text is not accessible. Same fallback applies.
- **Timing window**: The digest captures newsletters received in the 24 hours before the run. Newsletters arriving after 8:45 am appear in the next day's digest.
- **Inoreader**: Uses the public HTML stream URL — no API authentication required, but limited to what Inoreader displays in its magazine view (typically the 20 most recent items).
- **GitHub Actions schedule**: GitHub does not guarantee exact cron timing — delivery may be a few minutes late during high-load periods.

## Dependencies

- `requests` — HTTP fetching
- `beautifulsoup4` — Inoreader HTML parsing
- `trafilatura` + `lxml_html_clean` — full article text extraction
- `anthropic` — Claude API client
- Python standard library: `imaplib`, `smtplib`, `email`
