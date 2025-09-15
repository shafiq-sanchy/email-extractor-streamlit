# app.py
import re
import io
import csv
import time
import smtplib
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

# Optional MX & SMTP verification
try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)
HEADERS = {"User-Agent": "EmailExtractor/1.0"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MAX_WORKERS = 12

# keywords to exclude trap/garbage addresses
EXCLUDE_KEYWORDS = ["sentry", "support", "press", "privacy", "noreply", "no-reply"]

# ---------------------
# Helper functions
# ---------------------
def normalize_url(url):
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url):
    try:
        resp = requests.head(url, allow_redirects=True, headers=HEADERS, timeout=10, verify=False)
        return resp.url
    except:
        return url

def is_email_valid(email):
    """Very basic MX check (optional)."""
    if not DNS_AVAILABLE:
        return "Skipped"
    try:
        domain = email.split("@")[1]
        mx_records = dns.resolver.resolve(domain, 'MX')
        return True if mx_records else False
    except:
        return False

def extract_emails_from_html(html):
    found_emails = set()
    for m in set(EMAIL_REGEX.findall(html)):
        found_emails.add(m.lower())
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a['href'].startswith("mailto:"):
            email = a['href'][7:].split('?')[0]
            found_emails.add(email.lower())
    return found_emails

def filter_emails(emails):
    """Remove garbage/trap keywords."""
    return {e for e in emails if not any(x in e for x in EXCLUDE_KEYWORDS)}

def crawl_site(url, crawl_depth=1, max_pages=30, delay=0.3):
    parsed_root = urlparse(url)
    base_domain = parsed_root.netloc
    to_visit = [(url, 0)]
    seen = set([url])
    found_emails = set()
    pages_processed = 0

    while to_visit and pages_processed < max_pages:
        current_url, cur_depth = to_visit.pop(0)
        pages_processed += 1
        try:
            resp = requests.get(current_url, headers=HEADERS, timeout=10, verify=False)
            html = resp.text
        except:
            continue

        found_emails.update(extract_emails_from_html(html))

        if cur_depth < crawl_depth:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a['href'].strip()
                joined = urljoin(current_url, href)
                p = urlparse(joined)
                if p.scheme not in ("http", "https"):
                    continue
                if p.netloc != base_domain:
                    continue
                norm = p._replace(fragment="").geturl()
                if norm not in seen:
                    seen.add(norm)
                    to_visit.append((norm, cur_depth + 1))
        time.sleep(delay)

    return url, found_emails

# ---------------------
# Streamlit UI
# ---------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

# Header
st.markdown("""
<div style="margin-bottom:20px;">
<h1 style="color:#1F2328;">📧 Email Extractor</h1>
<p style="color:#333; font-size:16px;">Extract emails from websites fast. Supports multiple URLs, crawl depth, and MX validation.</p>
</div>
""", unsafe_allow_html=True)

# Input
with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area(
            "Enter website URLs (one per line)",
            height=300
        )
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 1, 1)
        max_pages = st.number_input("Max pages per site", 1, 200, 30)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)
        verify_emails = st.checkbox("✅ Verify emails (MX check)", value=False)

st.markdown("---")

# Extract Emails
if st.button("🚀 Extract Emails"):
    websites = []
    for u in urls_input.splitlines():
        norm_url = normalize_url(u)
        if norm_url:
            final_url = resolve_url(norm_url)
            websites.append(final_url)

    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        st.info(f"⏳ Starting extraction from {len(websites)} website(s)...")
        all_results = {}
        total_emails_found = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_url = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in websites}
            for future in as_completed(future_to_url):
                url, emails = future.result()
                raw_emails = emails
                filtered_emails = filter_emails(emails)

                if verify_emails:
                    verified_emails = {e for e in filtered_emails if is_email_valid(e)}
                else:
                    verified_emails = filtered_emails

                all_results[url] = (raw_emails, verified_emails)
                total_emails_found += len(verified_emails)

        # Display results
        st.subheader("📋 Extracted Emails per Website")
        for site, (raw, filtered) in all_results.items():
            st.markdown(f"### 🌐 {site}")
            if raw:
                st.markdown("**Raw Emails Found:**")
                st.dataframe(pd.DataFrame({"Email": sorted(raw)}), height=min(200, 25*len(raw)))

                st.markdown("<br>", unsafe_allow_html=True)

                st.markdown("**Filtered Emails (cleaned & verified):**")
                df = pd.DataFrame({
                    "Email": sorted(filtered),
                    "Verified": [is_email_valid(e) if verify_emails else "Skipped" for e in sorted(filtered)]
                })
                st.dataframe(df, height=min(300, 25*len(filtered)))
            else:
                st.markdown("→ No emails found.")

            st.markdown("<br><br>", unsafe_allow_html=True)

        # Notification
        st.success(f"🎉 Extraction completed! Total emails found: {total_emails_found}")
        st.balloons()

        # CSV download
        if any(len(f[1]) for f in all_results.values()):
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email", "verified"])
            for site, (_, emails) in all_results.items():
                for e in sorted(emails):
                    writer.writerow([site, e, is_email_valid(e) if verify_emails else "Skipped"])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")

            st.download_button("📥 Download all emails (CSV)", csv_bytes, "emails.csv", "text/csv")

        # Browser notification + sound
        js_code = f"""
        <script>
        function notifyMe() {{
            if (!("Notification" in window)) {{
                alert("Extraction done! Total emails: {total_emails_found}");
                return;
            }}
            if (Notification.permission !== "granted")
                Notification.requestPermission();
            if (Notification.permission === "granted") {{
                var notification = new Notification("Email Extractor", {{
                    body: "Extraction completed! Total emails: {total_emails_found}\\nBy Shafiq Sanchy",
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

# Footer
st.markdown("""
<div style="padding:15px; margin-top:50px; text-align:center; font-size:14px; color:#555; border-top:1px solid #eee;">
This app is designed and developed by <b>Shafiq Sanchy</b>. © 2025
</div>
""", unsafe_allow_html=True)
