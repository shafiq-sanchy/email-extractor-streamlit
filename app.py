# app.py
"""
Email Extractor (optimized)

Features:
- Parallel crawling of multiple websites
- Robust email extraction (text + mailto)
- Strong garbage filtering (image-file like, hashed sentry IDs, known noisy domains)
- Two verification modes: MX-only (fast) and SMTP RCPT (slower; may be blocked)
- Caching for verification results
- Safe UI heights and spacing to avoid overlap
- Optional: add 'dnspython' to requirements for verification
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

# Optional DNS resolver (dnspython)
try:
    import dns.resolver
    DNS_AVAILABLE = True
except Exception:
    DNS_AVAILABLE = False

# -----------------------
# Configuration
# -----------------------
# stricter regex: ensures at least 2-letter TLD, avoid catching weird partial strings
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', re.I)

# Exclude patterns and domains (customize as needed)
EXCLUDED_KEYWORDS = ["support@", "press@", "privacy@", "no-reply@", "noreply@", "unsubscribe@"]
EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "sentry-next", "amazonaws", "localhost", "invalid", "example", "2x.png"
]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".pdf")

# Concurrency & requests session
MAX_CRAWL_WORKERS = 12
MAX_VERIFY_WORKERS = 10

HEADERS = {"User-Agent": "EmailExtractor/1.0 (+https://example.com)"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Utility / helper funcs
# -----------------------
def normalize_url(url: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url: str) -> str:
    """Resolve shortened URL to final; ignore SSL verification issues"""
    try:
        resp = session.head(url, allow_redirects=True, headers=HEADERS, timeout=8, verify=False)
        final = resp.url or url
        # warm GET (non-fatal)
        try:
            session.get(final, headers=HEADERS, timeout=6, verify=False)
        except Exception:
            pass
        return final
    except Exception:
        return url

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)  # local parts that are long hex strings

def looks_like_garbage(email: str) -> bool:
    """Return True if email looks like machine-generated or obviously invalid for our use."""
    if not email or " " in email:
        return True
    e = email.strip().lower()

    # quick structure check
    if EMAIL_REGEX.fullmatch(e) is None:
        return True

    # local and domain parts
    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True

    # skip if domain ends with a file extension (common scraped image filenames)
    if any(domain.endswith(ext.lstrip(".")) or domain.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    # also check if domain string itself contains file extensions sequence (rare)
    if any(domain.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True

    # skip if local part is long hex (system IDs)
    if HEX_GARBAGE_RE.fullmatch(local):
        return True

    # skip if domain contains known noisy substrings
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain:
            return True

    # skip specific excluded keywords anywhere
    for kw in EXCLUDED_KEYWORDS:
        if kw in e:
            return True

    # skip if domain includes numeric TLD only (extremely rare) or other oddities - leave default
    return False

def extract_emails_from_html(html: str) -> set:
    """Extract emails using regex and mailto links; returns set of lowercased emails (raw)."""
    found = set()
    if not html:
        return found
    # regex on page content (faster)
    for m in set(EMAIL_REGEX.findall(html)):
        found.add(m.lower())
    # parse mailto links
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split("?")[0].strip().lower()
                if email:
                    found.add(email)
    except Exception:
        pass
    return found

# -----------------------
# Verification utilities
# -----------------------
@lru_cache(maxsize=4096)
def verify_mx_only(domain: str) -> bool:
    """Return True if domain has MX records. Requires dnspython (dns.resolver)."""
    if not DNS_AVAILABLE:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False

@lru_cache(maxsize=4096)
def verify_smtp_rcpt_cached(email: str) -> str:
    """Attempt SMTP RCPT TO on domain MX hosts.
    Returns 'Valid', 'Invalid', or 'Unknown'.
    This is slow and may be blocked by MTAs; use sparingly.
    """
    if not DNS_AVAILABLE:
        return "DNS missing"
    try:
        domain = email.split("@", 1)[1]
    except Exception:
        return "Invalid"

    try:
        mx_records = dns.resolver.resolve(domain, "MX", lifetime=5)
        mx_hosts = [str(r.exchange).rstrip(".") for r in mx_records]
    except Exception:
        return "Invalid"

    for mx in mx_hosts:
        try:
            server = smtplib.SMTP(timeout=6)
            server.connect(mx)
            server.helo()  # EHLO/HELO
            server.mail("verify@" + "example.com")
            code, _ = server.rcpt(email)
            server.quit()
            # 250 accepted; some servers return 251 etc.
            if isinstance(code, int) and 200 <= code < 300:
                return "Valid"
            # 550 typically means mailbox not found
            if isinstance(code, int) and 500 <= code < 600:
                return "Invalid"
        except Exception:
            continue
    return "Unknown"

def verify_email(email: str, mode: str = "none") -> str:
    """
    mode: 'none' | 'mx' | 'smtp'
    returns: "Skipped" | "Valid" | "Invalid" | "Unknown" | "DNS missing"
    """
    if mode == "none":
        return "Skipped"
    # basic structure first
    if EMAIL_REGEX.fullmatch(email) is None:
        return "Invalid"
    try:
        domain = email.split("@", 1)[1]
    except Exception:
        return "Invalid"

    if mode == "mx":
        ok = verify_mx_only(domain)
        return "Valid" if ok else "Invalid"
    if mode == "smtp":
        # check MX first
        if not verify_mx_only(domain):
            return "Invalid"
        return verify_smtp_rcpt_cached(email)
    return "Skipped"

# -----------------------
# Crawling
# -----------------------
def crawl_site(url: str, crawl_depth: int = 1, max_pages: int = 30, delay: float = 0.2) -> tuple:
    """Return (url, set_of_raw_emails)"""
    parsed = urlparse(url)
    base_domain = parsed.netloc
    to_visit = [(url, 0)]
    seen = set([url])
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
                    href = a["href"].strip()
                    joined = urljoin(current, href)
                    p = urlparse(joined)
                    if p.scheme not in ("http", "https"):
                        continue
                    if p.netloc != base_domain:
                        continue
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

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor</h1>
  <p style="color:#333; font-size:14px;">Paste website URLs (one per line). Use MX-only verification for bulk. SMTP RCPT is slower and may be blocked.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=220)
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 1, 1)
        max_pages = st.number_input("Max pages per site", 1, 200, 30)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)
        verify_choice = st.selectbox(
            "Verify emails",
            options=["None", "MX only (fast)", "MX+RCPT (slow)"],
            index=0
        )
        st.markdown("<small style='color:#666'>MX+RCPT may be slow and sometimes blocked by mail servers.</small>", unsafe_allow_html=True)

st.markdown("---")

if st.button("🚀 Extract Emails"):
    # normalize and resolve
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(resolve_url(n))

    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        st.info(f"⏳ Starting extraction from {len(websites)} website(s)...")
        all_results = {}
        unique_emails = set()

        # crawl in parallel
        with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
            futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in websites}
            for fut in as_completed(futures):
                url, raw_emails = fut.result()
                # filter garbage & excluded keywords now
                cleaned = {e for e in raw_emails if not looks_like_garbage(e)}
                # also filter EXCLUDED_KEYWORDS explicitly
                cleaned = {e for e in cleaned if not any(k in e for k in EXCLUDED_KEYWORDS)}
                all_results[url] = {
                    "raw": sorted(raw_emails),
                    "clean": sorted(cleaned)
                }
                unique_emails.update(cleaned)

        # show raw + cleaned per site with safe heights to avoid overlap
        st.subheader("📋 Extracted Emails per Website")
        for site, data in all_results.items():
            st.markdown(f"### 🌐 {site}")
            raw = data["raw"]
            clean = data["clean"]

            # Raw (if any)
            st.markdown("**Raw Emails Found:**")
            if raw:
                df_raw = pd.DataFrame({"Email": raw})
                # safe height calculation
                rows = max(1, len(df_raw))
                height = max(180, min(500, 32 * rows))
                st.dataframe(df_raw, height=height)
            else:
                st.markdown("→ No raw emails found.")

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

            # Clean (if any)
            st.markdown("**Filtered Emails (cleaned):**")
            if clean:
                # prepare df with verification status placeholder
                df_clean = pd.DataFrame({"Email": clean})
                rows = max(1, len(df_clean))
                height = max(180, min(600, 32 * rows))
                st.dataframe(df_clean, height=height)
            else:
                st.markdown("→ No filtered emails found.")

            st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # Verification step (parallel) if requested
        mode = "none"
        if verify_choice == "MX only (fast)":
            mode = "mx"
        elif verify_choice == "MX+RCPT (slow)":
            mode = "smtp"

        verified_map = {}
        if mode != "none" and unique_emails:
            st.info("🔎 Verifying emails (parallel). This may take time depending on mode.")
            # parallel verification with caching
            with ThreadPoolExecutor(max_workers=MAX_VERIFY_WORKERS) as vexec:
                futures = {}
                for e in unique_emails:
                    if mode == "mx":
                        # call verify_mx_only via wrapper
                        futures[vexec.submit(lambda em: (em, "Valid" if verify_mx_only(em.split('@',1)[1]) else "Invalid"), e)] = e
                    else:
                        futures[vexec.submit(verify_smtp_rcpt_cached, e)] = e
                # collect results
                for fut in as_completed(futures):
                    # smtp path returns status string; mx path we submitted lambda returning tuple? handle both
                    res = fut.result()
                    if isinstance(res, tuple) and len(res) == 2:
                        email, status = res
                        verified_map[email] = status
                    elif isinstance(res, str):
                        # res corresponds to email passed via futures mapping key
                        # need to find which email; but we used email as argument, so fut.result() returns string status
                        # get email from futures dict
                        email = futures[fut]
                        # if status from verify_smtp_rcpt_cached
                        verified_map[email] = res
                    else:
                        # fallback
                        email = futures[fut]
                        verified_map[email] = "Unknown"
        else:
            # mark all as skipped
            for e in unique_emails:
                verified_map[e] = "Skipped" if mode == "none" else ("Invalid" if not DNS_AVAILABLE else "Unknown")

        # Final presentation per site with verification status
        st.subheader("✅ Final Results (with verification status)")
        valid_count = 0
        for site, data in all_results.items():
            clean = data["clean"]
            if not clean:
                st.markdown(f"**{site}** → No filtered emails")
                continue
            rows = []
            for e in sorted(clean):
                status = verified_map.get(e, "Skipped")
                rows.append({"Email": e, "Verified": status})
                if status == "Valid":
                    valid_count += 1
            df_final = pd.DataFrame(rows)
            st.markdown(f"**{site}**")
            height = max(180, min(700, 32 * max(1, len(rows))))
            st.dataframe(df_final, height=height)
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # CSV download (prepared once)
        if unique_emails:
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email", "verified"])
            for site, data in all_results.items():
                for e in data["clean"]:
                    writer.writerow([site, e, verified_map.get(e, "Skipped")])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")
            st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
            st.download_button("📥 Download all emails (CSV)", data=csv_bytes, file_name="emails.csv", mime="text/csv")

        # Finish notification & branding
        st.balloons()
        st.success(f"🎉 Extraction completed! Unique cleaned emails: {len(unique_emails)} — Valid: {sum(1 for v in verified_map.values() if v == 'Valid')}")
        st.info("💡 Done by Shafiq Sanchy")

        # browser notification + sound
        js_code = f"""
        <script>
        function notifyMe() {{
            if (!("Notification" in window)) {{
                alert("Extraction done! Total emails: {len(unique_emails)}");
                return;
            }}
            if (Notification.permission !== "granted") Notification.requestPermission();
            if (Notification.permission === "granted") {{
                new Notification("Email Extractor", {{
                    body: "Done! {len(unique_emails)} unique cleaned emails — Valid: {sum(1 for v in verified_map.values() if v == 'Valid')}",
                    icon: "https://cdn-icons-png.flaticon.com/512/561/561127.png"
                }});
            }}
            var audio = new Audio("https://www.soundjay.com/buttons/sounds/beep-07.mp3");
            audio.play();
        }}
        notifyMe();
        </script>
        """
        import streamlit.components.v1 as components
        components.html(js_code, height=0, width=0)

# footer
st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
