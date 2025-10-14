# vidp_alert.py
# Python 3 script. Expects EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_TO in environment.
import os
import re
import requests
import json
import smtplib
import tempfile
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import fitz  # PyMuPDF

# ---- Configuration ----
BASE_URL = "https://www.atfmaai.aero"
LIST_URL = BASE_URL + "/portal/en/news/atfm-measures"
SEEN_FILE = "seen.json"     # persisted in repo by workflow commit
USER_AGENT = "VIDP-ATFM-Watcher/1.0"

EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

# set more aggressive timeout for PDF downloads
REQUEST_TIMEOUT = 30

# callsign regex: 2-4 letters followed by 1-4 digits OR some operator codes, fallback
CALLSIGN_RE = re.compile(r"\b([A-Z]{2,4}\d{1,4})\b")

# ---- Helpers ----
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
        print("Email credentials not set. Skipping email.")
        return
    msg = MIMEText(body, "plain")
    msg["From"] = EMAIL_USERNAME
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    s = smtplib.SMTP("smtp.gmail.com", 587, timeout=60)
    s.starttls()
    s.login(EMAIL_USERNAME, EMAIL_PASSWORD)
    s.send_message(msg)
    s.quit()
    print("Email sent:", subject)

def find_pdf_links():
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(LIST_URL, headers=headers, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".pdf"):
            if href.startswith("/"):
                href = BASE_URL + href
            links.append(href)
    # keep order and unique
    seen = set()
    ordered = []
    for v in links:
        if v not in seen:
            seen.add(v)
            ordered.append(v)
    return ordered

def extract_callsigns_from_pdf_bytes(pdf_bytes):
    callsigns = []
    # create temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        doc = fitz.open(tmp.name)
        for page in doc:
            # get clean text per page
            text = page.get_text("text")
            # split into lines to preserve adjacency
            for line in text.splitlines():
                if "VIDP" in line.upper():
                    # try to find callsign in same line (search left/right)
                    # first search for tokens that match callsign regex
                    tokens = CALLSIGN_RE.findall(line.upper())
                    if tokens:
                        callsigns.extend(tokens)
                    else:
                        # fallback: look at neighboring words: split and find token adjacent to VIDP
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part.upper() == "VIDP":
                                # look left
                                if i-1 >= 0:
                                    m = CALLSIGN_RE.search(parts[i-1].upper())
                                    if m:
                                        callsigns.append(m.group(1))
                                # look right
                                if i+1 < len(parts):
                                    m = CALLSIGN_RE.search(parts[i+1].upper())
                                    if m:
                                        callsigns.append(m.group(1))
        # dedupe preserving order
        seen_cs = []
        for c in callsigns:
            if c not in seen_cs:
                seen_cs.append(c)
        return seen_cs
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass

def process_new_pdfs():
    seen = load_seen()
    new_seen = set(seen)
    found_alerts = []

    pdfs = find_pdf_links()
    print("Found PDF links:", len(pdfs))
    for pdf_url in pdfs:
        if pdf_url in seen:
            continue
        print("Processing new PDF:", pdf_url)
        try:
            r = requests.get(pdf_url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            print("Failed to download PDF:", e)
            continue
        callsigns = extract_callsigns_from_pdf_bytes(r.content)
        if callsigns:
            found_alerts.append((pdf_url, callsigns))
            print("VIDP callsigns found:", callsigns)
        else:
            print("No VIDP mentions in this PDF.")
        new_seen.add(pdf_url)

    save_seen(new_seen)
    return found_alerts

def main():
    alerts = process_new_pdfs()
    if not alerts:
        print("No new VIDP entries found.")
        return
    # compose email body
    lines = []
    for pdf_url, calls in alerts:
        lines.append(f"PDF: {pdf_url}")
        lines.append("Callsigns found:")
        for c in calls:
            lines.append(f" - {c}")
        lines.append("")  # blank line
    body = "\n".join(lines)
    subject = f"VIDP ATFM Alert — {len(alerts)} file(s) with matches"
    send_email(subject, body)

if __name__ == "__main__":
    main()
