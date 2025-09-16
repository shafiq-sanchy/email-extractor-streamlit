# app.py (updated with UI reorder & stronger garbage filter)

"""
Email Extractor (optimized)

- Parallel crawling of multiple websites
- Garbage email filtering (image filenames, hashed IDs, traps, noreply, sentry, wix, etc.)
- MX-only or SMTP RCPT verification (optional)
- Caching for verification results
- ✅ UI: completion message + buttons appear before tables
"""

import re
import io
import csv
import time
import smtplib
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Optional DNS resolver
try:
    import dns.resolver
    DNS_AVAILABLE = True
except Exception:
    DNS_AVAILABLE = False

# -----------------------
# Config
# -----------------------
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', re.I)

EXCLUDED_KEYWORDS = [
    "support@", "press@", "job", "career", "enquiry", "sales", "yourname", "john",
    "example", "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"
]
EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "mysite", "yoursite", "yourwebsite", "sentry-next",
    "amazonaws", "localhost", "invalid", "example", "2x.png"
]
SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".pdf"
)

MAX_CRAWL_WORKERS = 12
MAX_VERIFY_WORKERS = 10

HEADERS = {"User-Agent": "EmailExtractor/1.0"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Helpers
# -----------------------
def normalize_url(url: str):
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url: str) -> str:
    try:
        resp = session.head(url, allow_redirects=True, headers=HEADERS, timeout=8, verify=False)
        return resp.url or url
    except Exception:
        return url

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{12,}$', re.I)  # detect long hex IDs

def looks_like_garbage(email: str) -> bool:
    if not email:
        return True
    e = email.strip().lower()

    if EMAIL_REGEX.fullmatch(e) is None:
        return True

    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True

    # skip image-like or doc-like "emails"
    if any(domain.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True

    if HEX_GARBAGE_RE.fullmatch(local):
        return True

    if any(sub in domain for sub in EXCLUDED_DOMAINS_SUBSTR):
        return True

    if any(kw in e for kw in EXCLUDED_KEYWORDS):
        return True

    return False

def extract_emails_from_html(html: str) -> set:
    found = set()
    for m in set(EMAIL_REGEX.findall(html)):
        found.add(m.lower())
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if a["href"].lower().startswith("mailto:"):
                email = a["href"].split("mailto:", 1)[1].split("?")[0].strip().lower()
                if email:
                    found.add(email)
    except Exception:
        pass
    return found

# -----------------------
# Verification
# -----------------------
@lru_cache(maxsize=2048)
def verify_mx_only(domain: str) -> bool:
    if not DNS_AVAILABLE:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False

@lru_cache(maxsize=2048)
def verify_smtp_rcpt_cached(email: str) -> str:
    if not DNS_AVAILABLE:
        return "DNS missing"
    try:
        domain = email.split("@", 1)[1]
        mx_records = dns.resolver.resolve(domain, "MX", lifetime=5)
        mx_hosts = [str(r.exchange).rstrip(".") for r in mx_records]
    except Exception:
        return "Invalid"

    for mx in mx_hosts:
        try:
            server = smtplib.SMTP(timeout=6)
            server.connect(mx)
            server.helo()
            server.mail("check@example.com")
            code, _ = server.rcpt(email)
            server.quit()
            if 200 <= code < 300:
                return "Valid"
            if 500 <= code < 600:
                return "Invalid"
        except Exception:
            continue
    return "Unknown"

# -----------------------
# Crawl
# -----------------------
def crawl_site(url: str, crawl_depth=1, max_pages=30, delay=0.2):
    parsed = urlparse(url)
    base_domain = parsed.netloc
    to_visit = [(url, 0)]
    seen = {url}
    found = set()
    pages = 0

    while to_visit and pages < max_pages:
        current, depth = to_visit.pop(0)
        pages += 1
        try:
            r = session.get(current, headers=HEADERS, timeout=10, verify=False)
            html = r.text
        except Exception:
            continue

        found.update(extract_emails_from_html(html))

        if depth < crawl_depth:
            try:
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    joined = urljoin(current, a["href"].strip())
                    p = urlparse(joined)
                    if p.scheme in ("http", "https") and p.netloc == base_domain:
                        norm = p._replace(fragment="").geturl()
                        if norm not in seen:
                            seen.add(norm)
                            to_visit.append((norm, depth + 1))
            except Exception:
                pass

        time.sleep(delay)

    return url, found

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

st.markdown("## 📧 Email Extractor")
urls_input = st.text_area("Enter website URLs (one per line)", height=300)
crawl_depth = st.slider("Crawl depth", 0, 1, 1)
max_pages = st.number_input("Max pages per site", 1, 200, 30)
delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)
verify_choice = st.selectbox("Verify emails", ["None", "MX only (fast)", "MX+RCPT (slow)"])

if st.button("🚀 Extract Emails"):
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(resolve_url(n))

    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        st.info(f"⏳ Crawling {len(websites)} website(s)...")
        all_results = {}
        unique_emails = set()

        with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
            futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in websites}
            for fut in as_completed(futures):
                url, raw = fut.result()
                clean = {e for e in raw if not looks_like_garbage(e)}
                all_results[url] = {"raw": sorted(raw), "clean": sorted(clean)}
                unique_emails.update(clean)

        # ------------------
        # ✅ SHOW RESULTS HEADER FIRST
        # ------------------
        mode = "none"
        if verify_choice == "MX only (fast)": mode = "mx"
        if verify_choice == "MX+RCPT (slow)": mode = "smtp"

        # Verification
        verified_map = {}
        if mode != "none":
            with ThreadPoolExecutor(max_workers=MAX_VERIFY_WORKERS) as vexec:
                futures = {}
                for e in unique_emails:
                    if mode == "mx":
                        futures[vexec.submit(lambda em: (em, "Valid" if verify_mx_only(em.split('@',1)[1]) else "Invalid"), e)] = e
                    else:
                        futures[vexec.submit(verify_smtp_rcpt_cached, e)] = e
                for fut in as_completed(futures):
                    res = fut.result()
                    if isinstance(res, tuple):
                        email, status = res
                        verified_map[email] = status
                    else:
                        email = futures[fut]
                        verified_map[email] = res
        else:
            verified_map = {e: "Skipped" for e in unique_emails}

        # CSV
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["website", "email", "verified"])
        for site, data in all_results.items():
            for e in data["clean"]:
                writer.writerow([site, e, verified_map.get(e, "Skipped")])
        csv_bytes = csv_buffer.getvalue().encode("utf-8")

        # ✅ Completion + Buttons FIRST
        st.success(f"🎉 Done! {len(unique_emails)} unique emails — {sum(1 for v in verified_map.values() if v == 'Valid')} valid")
        st.download_button("📥 Download Emails (CSV)", csv_bytes, "emails.csv", "text/csv")
        st.markdown("---")

        # Then tables
        st.subheader("📋 Results per Website")
        for site, data in all_results.items():
            st.markdown(f"### 🌐 {site}")
            if data["raw"]:
                st.markdown("**Raw Emails**")
                st.dataframe(pd.DataFrame({"Email": data["raw"]}), height=min(400, 32*len(data["raw"])))
            if data["clean"]:
                st.markdown("**Cleaned Emails (with verification)**")
                rows = [{"Email": e, "Verified": verified_map.get(e, "Skipped")} for e in data["clean"]]
                st.dataframe(pd.DataFrame(rows), height=min(500, 32*len(rows)))
            st.markdown("---")
