#!/usr/bin/env python3
"""
KI-Journalismus Digest
Täglich um 8 Uhr: Inoreader + Gmail (IMAP) → Claude → E-Mail (SMTP)
"""

import os
import re
import imaplib
import email as email_lib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
import smtplib
import datetime
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import anthropic

# ── Konfiguration ────────────────────────────────────────────────────────────

INOREADER_URL = "https://www.inoreader.com/stream/user/1004617329/tag/KI/view/html?cs=m"
RECIPIENT     = "aiformedia@br.de"
CLAUDE_MODEL  = "claude-sonnet-4-6"

# Feste Quellenliste: RSS-Feeds und die AI-for-Newsroom-Übersichtsseite (kein RSS verfügbar)
# Quellen ohne eigenen KI-Fokus (Nieman Lab, Reuters Institute) bekommen einen
# Keyword-Filter, damit nicht die komplette allgemeine Redaktionsberichterstattung
# der Sites im Digest landet. Poynter ist hinter Cloudflare-Bot-Schutz und hat
# keinen erreichbaren aktuellen RSS-Feed — daher nicht eingebunden.
AI_KEYWORD_PATTERN = re.compile(
    r"\b(AI|A\.I\.|artificial intelligence|chatgpt|genai|generative ai|"
    r"large language model|LLM|machine learning|chatbot|algorithmic)\b",
    re.IGNORECASE,
)

RSS_SOURCES = [
    {"name": "The AI Journalist (Steady)", "url": "https://steady.page/de/aijournalist/rss"},
    {"name": "News Product Alliance Blog", "url": "https://newsproduct.org/blog?format=rss"},
    {"name": "Lars Adrian Giske (Substack)", "url": "https://larsadriangiske.substack.com/feed"},
    {"name": "JournalismAI Blog", "url": "https://www.journalismai.info/blog?format=rss"},
    {"name": "RJI (Reynolds Journalism Institute)", "url": "https://rjionline.org/feed/"},
    {"name": "Nieman Lab", "url": "https://www.niemanlab.org/feed/", "keyword_filter": True},
]
AIFORNEWSROOM_URL = "https://aifornewsroom.in/"

# Credentials aus .env laden (Fallback für lokale Ausführung)
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if v and k not in os.environ:
                os.environ[k] = v

GMAIL_ADDRESS    = os.environ.get("GMAIL_ADDRESS", "osbernd@gmail.com")
GMAIL_APP_PW     = os.environ.get("GMAIL_APP_PASSWORD", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Inoreader ────────────────────────────────────────────────────────────────

def _fetch_full_text(url, snippet, max_chars=3000):
    try:
        import trafilatura
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=15)
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False)
        if text and len(text) > len(snippet) + 100:
            return text[:max_chars]
    except Exception:
        pass
    return snippet


def fetch_inoreader(max_chars=40000):
    resp = requests.get(INOREADER_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    wrappers = soup.find_all("div", class_="article_magazine_content_wraper")
    for wrapper in wrappers:
        a_tag = wrapper.find("a", class_="article_magazine_title_link")
        if not a_tag:
            continue
        title  = a_tag.get_text(strip=True)
        url    = a_tag.get("href", "")
        source = ""
        feed_link = wrapper.find("a", class_="feed_link")
        if feed_link:
            source = feed_link.get_text(strip=True)
        snippet = ""
        content_div = wrapper.find("div", class_="article_magazine_content")
        if content_div:
            snippet = content_div.get_text(strip=True)
        age = ""
        date_div = wrapper.find("div", class_="article_date_short")
        if date_div:
            age = date_div.get_text(strip=True)
        full_text = _fetch_full_text(url, snippet)
        parts = [f"TITEL: {title}", f"URL: {url}", f"QUELLE: {source}"]
        if age:
            parts.append(f"ALTER: {age}")
        parts.append(f"INHALT: {full_text}")
        articles.append("\n".join(parts))

    content = "\n---\n".join(articles)
    return content[:max_chars]

# ── Feste Quellenliste (RSS + AI for Newsroom) ────────────────────────────────
# Diese Quellen posten unregelmäßig (teils nur alle paar Wochen), daher kein
# harter Datumsfilter — stattdessen wird das Datum als ALTER mitgegeben und
# Claude entscheidet anhand der Aktualität, ob ein Eintrag noch relevant ist.

def _parse_pubdate(pub_date_text):
    try:
        return parsedate_to_datetime(pub_date_text)
    except Exception:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def fetch_rss(url, source_name, max_items=3, max_chars=2500, keyword_filter=False):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        # Manche Feeds haben führenden Whitespace vor der XML-Deklaration
        root = ET.fromstring(resp.content.lstrip())

        candidates = []
        for item in root.findall(".//item"):
            pub_date = item.findtext("pubDate", "")
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            desc  = (item.findtext("description") or "").strip()
            desc  = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
            if keyword_filter and not AI_KEYWORD_PATTERN.search(f"{title} {desc}"):
                continue
            candidates.append((_parse_pubdate(pub_date), title, link, desc, pub_date))

        # Nicht jeder Feed liefert Items chronologisch sortiert
        candidates.sort(key=lambda c: c[0], reverse=True)

        parts = []
        for _, title, link, desc, pub_date in candidates[:max_items]:
            full_text = _fetch_full_text(link, desc, max_chars) if link else desc
            entry = [f"TITEL: {title}", f"URL: {link}", f"QUELLE: {source_name}"]
            if pub_date:
                entry.append(f"ALTER: {pub_date}")
            entry.append(f"INHALT: {full_text}")
            parts.append("\n".join(entry))
        return "\n---\n".join(parts)
    except Exception as e:
        print(f"  Fehler bei RSS-Quelle {source_name}: {e}", flush=True)
        return ""


def fetch_aifornewsroom(max_items=6, max_chars=2500):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp = requests.get(AIFORNEWSROOM_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        parts = []
        for art in soup.find_all("article"):
            heading = art.find(["h3", "h4"])
            if not heading:
                continue
            link_tag = heading.find("a", href=lambda h: h and h.startswith("http"))
            if not link_tag:
                continue
            title = link_tag.get_text(strip=True)
            href  = link_tag["href"]
            meta_span = art.find("span")
            meta_text = meta_span.get_text(strip=True) if meta_span else ""
            snippet = art.get_text(" ", strip=True)[:500]
            full_text = _fetch_full_text(href, snippet, max_chars)
            entry = [f"TITEL: {title}", f"URL: {href}", "QUELLE: AI for Newsroom"]
            if meta_text:
                entry.append(f"ALTER: {meta_text}")
            entry.append(f"INHALT: {full_text}")
            parts.append("\n".join(entry))
            if len(parts) >= max_items:
                break
        return "\n---\n".join(parts)
    except Exception as e:
        print(f"  Fehler bei AI for Newsroom: {e}", flush=True)
        return ""


def fetch_additional_sources():
    blocks = [fetch_aifornewsroom()]
    for src in RSS_SOURCES:
        blocks.append(fetch_rss(src["url"], src["name"], keyword_filter=src.get("keyword_filter", False)))
    return "\n---\n".join(b for b in blocks if b)

# ── Gmail (IMAP) ─────────────────────────────────────────────────────────────

def decode_str(value):
    parts = decode_header(value or "")
    result = ""
    for part, charset in parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="replace")
        else:
            result += part
    return result


def fetch_newsletters(max_results=20):
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PW)
    mail.select('"AI News"')

    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
    status, messages = mail.search(None, f'SINCE "{yesterday}"')

    items = []
    msg_ids = messages[0].split() if messages[0] else []

    for num in msg_ids[-max_results:]:
        try:
            status, data = mail.fetch(num, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])

            subject = decode_str(msg.get("Subject", ""))
            sender  = decode_str(msg.get("From", ""))

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        try:
                            body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        except Exception:
                            pass
                        break
            else:
                try:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    pass

            items.append(
                f"VON: {sender}\n"
                f"BETREFF: {subject}\n"
                f"INHALT: {body[:3000]}"
            )
        except Exception as e:
            print(f"  Fehler bei Nachricht {num}: {e}", flush=True)
            continue

    mail.close()
    mail.logout()
    return "\n---\n".join(items)

# ── Claude: Digest verfassen ─────────────────────────────────────────────────

_GUIDELINES_FILE = Path(__file__).parent / "editorial_guidelines.md"
_EDITORIAL_GUIDELINES = _GUIDELINES_FILE.read_text() if _GUIDELINES_FILE.exists() else ""

SYSTEM_PROMPT = """Du bist Redaktionsassistent für Bernd Oswald, Projektmanager KI im Journalismus.
Deine Aufgabe: aus Rohmaterial einen präzisen deutschen E-Mail-Digest erstellen.

Stilregeln:
- Präzises Deutsch, keine Füllwörter
- Maximal 4 Sätze pro Eintrag
- Bei KI-Anwendungen im Journalismus: das WIE erklären (Technik, Umsetzung). Ebenso das WAS und das WARUM (warum wurde KI XY gewählt, um dieses Problem zu lösen)
- Bei KI-News: WER, WAS, WIE beantworten
- Jeder Eintrag endet mit → <a href="[URL]">[Quelle]</a>
- Abschnitte ohne relevante Inhalte komplett weglassen
- Maximal 3 Einträge pro Abschnitt

{guidelines}""".format(guidelines=_EDITORIAL_GUIDELINES)


def compose_digest(inoreader_text, newsletter_text, additional_text):
    today = datetime.datetime.now().strftime("%-d. %B %Y")

    user_prompt = f"""Erstelle den KI-Journalismus Digest für {today}.

=== INOREADER KI-ORDNER (letzte 24h) ===
{inoreader_text}

=== GMAIL NEWSLETTER (letzte 24h) ===
{newsletter_text if newsletter_text else "(Keine Newsletter gefunden)"}

=== WEITERE QUELLEN (AI for Newsroom, The AI Journalist, News Product Alliance Blog, Lars Adrian Giske, JournalismAI, RJI, Nieman Lab) ===
Diese Blogs/Newsletter posten unregelmäßig. Nimm aus dem ALTER-Feld nur Einträge auf, die
höchstens ein bis zwei Wochen alt sind — ältere Einträge ignorieren, auch wenn sie inhaltlich passen.
{additional_text if additional_text else "(Keine neuen Einträge gefunden)"}

Strukturiere den Digest als HTML-E-Mail-Body mit diesen Abschnitten
(nur wenn relevante Inhalte vorhanden):

<h2>KI im Journalismus</h2>
Neue KI-Produkte, Anwendungsfälle oder Projekte speziell in Redaktionen und Medienhäusern.

<h2>KI-Nachrichten USA</h2>
Wichtige KI-Entwicklungen aus den USA: Modelle, Regulierung, Ethik, Infrastruktur.

<h2>KI-Nachrichten Europa</h2>
Wichtige KI-Entwicklungen aus Europa: Modelle, Regulierung, Ethik, Infrastruktur.

<h2>Konferenzen & Veranstaltungen</h2>
Nur wenn konkrete aktuelle Ankündigungen oder Berichte vorhanden.

<h2>Empfehlung</h2>
Ein Website, Blog oder Account, der für KI-Journalismus-Profis interessant ist.

Format je Eintrag:
<p><strong>[Titel]</strong><br>[Text max. 4 Sätze]<br>→ <a href="[URL]">[Quelle]</a></p>

Gib ausschließlich den HTML-Body zurück (keine html/body/head-Tags)."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text

# ── E-Mail senden (SMTP) ─────────────────────────────────────────────────────

def send_email(subject, html_body):
    footer = (
        '<hr><p style="color:#999;font-size:11px;">'
        f'Quellen: Inoreader KI-Ordner · Gmail-Newsletter · AI for Newsroom · '
        f'The AI Journalist · News Product Alliance Blog · Lars Adrian Giske · '
        f'JournalismAI · RJI · Nieman Lab · '
        f'Digest erstellt am {datetime.datetime.now().strftime("%-d. %B %Y, %H:%M Uhr")}'
        '</p>'
    )
    full_html = html_body + footer

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.sendmail(GMAIL_ADDRESS, [RECIPIENT], msg.as_string())
    print(f"✓ Digest gesendet an {RECIPIENT}", flush=True)

# ── Hauptprogramm ────────────────────────────────────────────────────────────

def main():
    if not GMAIL_APP_PW:
        raise SystemExit("FEHLER: GMAIL_APP_PASSWORD nicht gesetzt (.env oder Umgebungsvariable)")
    if not ANTHROPIC_KEY:
        raise SystemExit("FEHLER: ANTHROPIC_API_KEY nicht gesetzt (.env oder Umgebungsvariable)")

    today   = datetime.datetime.now().strftime("%-d. %B %Y")
    subject = f"KI-Journalismus Digest – {today}"

    print("Lade Inoreader-Artikel …", flush=True)
    inoreader_text = fetch_inoreader()
    print(f"  → {inoreader_text.count('TITEL:')} Artikel geladen", flush=True)

    print("Lade Gmail-Newsletter via IMAP …", flush=True)
    newsletter_text = fetch_newsletters()
    print(f"  → {newsletter_text.count('BETREFF:')} Newsletter gefunden", flush=True)

    print("Lade weitere Quellen (AI for Newsroom, Steady, NPA Blog, Substack) …", flush=True)
    additional_text = fetch_additional_sources()
    print(f"  → {additional_text.count('TITEL:')} Artikel geladen", flush=True)

    print("Erstelle Digest mit Claude …", flush=True)
    html_body = compose_digest(inoreader_text, newsletter_text, additional_text)

    print("Sende E-Mail …", flush=True)
    send_email(subject, html_body)
    print("Fertig!", flush=True)


if __name__ == "__main__":
    main()
