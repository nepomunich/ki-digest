#!/usr/bin/env python3
"""
KI-Journalismus Digest
Täglich um 8 Uhr: Inoreader + Gmail (IMAP) → Claude → E-Mail (SMTP)
"""

import os
import imaplib
import email as email_lib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import anthropic

# ── Konfiguration ────────────────────────────────────────────────────────────

INOREADER_URL = "https://www.inoreader.com/stream/user/1004617329/tag/KI/view/html?cs=m"
RECIPIENT     = "post@berndoswald.de"
CLAUDE_MODEL  = "claude-sonnet-4-6"

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
- Bei KI-Anwendungen im Journalismus: das WIE erklären (Technik, Umsetzung)
- Bei KI-News: WER, WAS, WIE beantworten
- Jeder Eintrag endet mit → <a href="[URL]">[Quelle]</a>
- Abschnitte ohne relevante Inhalte komplett weglassen
- Maximal 3 Einträge pro Abschnitt

{guidelines}""".format(guidelines=_EDITORIAL_GUIDELINES)


def compose_digest(inoreader_text, newsletter_text):
    today = datetime.datetime.now().strftime("%-d. %B %Y")

    user_prompt = f"""Erstelle den KI-Journalismus Digest für {today}.

=== INOREADER KI-ORDNER (letzte 24h) ===
{inoreader_text}

=== GMAIL NEWSLETTER (letzte 24h) ===
{newsletter_text if newsletter_text else "(Keine Newsletter gefunden)"}

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
        f'Quellen: Inoreader KI-Ordner · Gmail-Newsletter · '
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

    print("Erstelle Digest mit Claude …", flush=True)
    html_body = compose_digest(inoreader_text, newsletter_text)

    print("Sende E-Mail …", flush=True)
    send_email(subject, html_body)
    print("Fertig!", flush=True)


if __name__ == "__main__":
    main()
