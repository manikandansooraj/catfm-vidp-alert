import os
import re
import requests
import json
import smtplib
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from io import BytesIO
import fitz  # PyMuPDF

# ------- CONFIGURATION --------
LIST_URL = "https://www.atfmaai.aero/portal/en/news/atfm-measures"
SEEN_FILE = "seen.json"
USER_AGENT = "VIDP-ATFM-Watcher/1.0"

# Email config via environment variables
EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

PDF_DOWNLOAD_TIMEOUT = 30  # seconds

# Regexes
PATTERN_CALLSIGN = re.compile(r"\b([A-Z]{2,3}\d{1,4}[A-Z]?)\b")
PATTERN_VIDP = re.compile(r"\bVIDP\b", re.IGNORECASE)

# -------- Helpers --------
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen)), f, indent=2)

def send_email(subject, body):
    if not EMAIL_USERNAME or not EMAIL_PASSWORD or not EMAIL_TO:
        print("Missing email credentials — cannot send.")
        return False
    msg = MIMEText(body, "plain")
    msg["From"] = EMAIL_USERNAME
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587, timeout=60)
        s.starttls()
        s.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        s.send_message(msg)
        s.quit()
        print("Email sent:", subject)
        return True
    except Exception as e:
        print("Error sending email:", e)
        return False

def find_pdf_links():
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(LIST_URL, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Check if link is PDF
        if href.lower().endswith(".pdf"):
            if href.startswith("/"):
                href = "https://www.atfmaai.aero" + href
            links.append(href)
    # dedupe in order
    seen = set()
    ordered = []
    for l in links:
        if l not in seen:
            seen.add(l)
            ordered.append(l)
    return ordered

def extract_callsigns_from_pdf_bytes(pdf_bytes):
    callsigns = set()
    with fitz.open(stream=BytesIO(pdf_bytes), filetype="pdf") as doc:
        for page in doc:
            text = page.get_text("text")
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if PATTERN_VIDP.search(line):
                    # build small context window
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    window = " ".join(lines[start:end])
                    for m in PATTERN_CALLSIGN.findall(window):
                        callsigns.add(m)
    return list(callsigns)

def process_new_pdfs():
    seen = load_seen()
    new_seen = set(seen)
    alerts = []

    pdfs = find_pdf_links()
    print("Found PDF links:", len(pdfs))
    for url in pdfs:
        if url in seen:
            continue
        print("Downloading PDF:", url)
        try:
            r = requests.get(url, timeout=PDF_DOWNLOAD_TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            print("Failed download:", e)
            continue

        calls = extract_callsigns_from_pdf_bytes(r.content)
        if calls:
            alerts.append((url, calls))
            print("VIDP callsigns found:", calls)
        else:
            print("No VIDP in this PDF.")

        new_seen.add(url)

    save_seen(new_seen)
    return alerts

def main():
    alerts = process_new_pdfs()
    if not alerts:
        print("No new VIDP entries found.")
        return

    body_lines = []
    for url, calls in alerts:
        body_lines.append(f"PDF: {url}")
        body_lines.append("Callsigns found:")
        for c in calls:
            body_lines.append(" - " + c)
        body_lines.append("")  # blank line

    subject = f"VIDP ATFM Alert — {len(alerts)} file(s) with matches"
    body = "\n".join(body_lines)
    send_email(subject, body)

if __name__ == "__main__":
    main()
