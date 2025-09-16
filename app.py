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
MAX_WORKERS = 10

# ---------------------
# Helper functions
# ---------------------
def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url):
    try:
        resp = requests.head(url, allow_redirects=True, headers=HEADERS, timeout=10, verify=False)
        return resp.url or url
    except Exception:
        return url

def is_email_valid(email):
    # returns True/False or "Skipped"
    if not DNS_AVAILABLE:
        return "Skipped"
    try:
        domain = email.split("@", 1)[1]
        mx_records = dns.resolver.resolve(domain, 'MX')
        mx_hosts = [r.exchange.to_text() for r in mx_records]

        # Simple SMTP MAIL/RCPT probing attempt (may be blocked by many servers)
        for mx in mx_hosts:
            try:
                server = smtplib.SMTP(timeout=5)
                server.connect(mx)
                server.helo()
                # attempt MAIL command (some servers accept MAIL from any sender)
                code_mail, _ = server.mail("test@" + domain)
                # attempt RCPT (not always reliable)
                code_rcpt, _ = server.rcpt(email)
                server.quit()
                # treat rcpt 250 as positive
                if isinstance(code_rcpt, int) and 200 <= code_rcpt < 300:
                    return True
                # if MAIL returned a 250 and RCPT didn't explicitly reject, consider True
                if isinstance(code_mail, int) and 200 <= code_mail < 300 and (isinstance(code_rcpt, int) and code_rcpt < 400):
                    return True
            except Exception:
                continue
        # if MX exists but probes failed/blocked, treat as True (domain accepts mail)
        return True if mx_hosts else False
    except Exception:
        return False

def extract_emails_from_html(html):
    found_emails = set()
    if not html:
        return found_emails
    # regex
    for m in set(EMAIL_REGEX.findall(html)):
        candidate = m.lower()
        found_emails.add(candidate)
    # mailto links
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a['href']
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split('?')[0].strip().lower()
                if email:
                    found_emails.add(email)
    except Exception:
        pass
    return found_emails

def crawl_site(url, crawl_depth=1, max_pages=30, delay=0.5):
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
        except Exception:
            continue

        found_emails.update(extract_emails_from_html(html))

        if cur_depth < crawl_depth:
            try:
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
            except Exception:
                pass

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
<p style="color:#333; font-size:16px;">Enter website URLs below to extract emails. Supports multiple websites, and MX verification. </p>
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

        # parallel crawl
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_url = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in websites}
            for future in as_completed(future_to_url):
                try:
                    url, emails = future.result()
                except Exception:
                    continue

                all_results[url] = emails

        # prepare verification cache (compute statuses once per email)
        verified_cache = {}
        if verify_emails:
            # collect all unique emails
            unique_emails = set()
            for emails in all_results.values():
                unique_emails.update(emails)
            # verify each (sequentially to avoid overloading) but keep it simple
            for e in sorted(unique_emails):
                try:
                    res = is_email_valid(e)
                except Exception:
                    res = "Skipped"
                # normalize to string
                if res is True:
                    verified_cache[e] = "Valid"
                elif res is False:
                    verified_cache[e] = "Invalid"
                else:
                    verified_cache[e] = str(res)
        else:
            # mark as Skipped
            for emails in all_results.values():
                for e in emails:
                    verified_cache[e] = "Skipped"

        # compute total found (using verified_cache keys per site)
        total_emails_found = sum(len(v) for v in all_results.values())

        # 🎉 Notification first (moved before tables)
        st.balloons()
        st.success(f"🎉 Extraction completed! Total emails found: {total_emails_found}")

        # prepare CSV
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["website", "email", "verified"])
        for site, emails in all_results.items():
            for e in sorted(emails):
                writer.writerow([site, e, verified_cache.get(e, "Skipped")])
        csv_bytes = csv_buffer.getvalue().encode("utf-8")

        # 📥 CSV download BEFORE tables
        if any(len(es) for es in all_results.values()):
            st.download_button("📥 Download all emails (CSV)", csv_bytes, "emails.csv", "text/csv")

        # Tables after message + download
        st.subheader("📋 Extracted Emails per Website")
        for site, emails in all_results.items():
            if emails:
                st.markdown(f"**{site}**")
                rows = []
                for e in sorted(emails):
                    rows.append({
                        "Email": e,
                        "Verified": verified_cache.get(e, "Skipped")
                    })
                df = pd.DataFrame(rows)
                # safe height to avoid StreamlitInvalidHeightError
                num_rows = max(1, len(df))
                height = max(200, min(600, 30 * num_rows))
                st.dataframe(df, height=height)
            else:
                st.markdown(f"**{site}** → No emails found.")

        # Browser notification + sound (runs after tables but UI still shows above)
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
<div style="padding:10px; border-top:0px solid #ccc; margin-top:50px; text-align:center; font-size:14px; color:#555;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
