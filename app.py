"""
Email Extractor (Optimized - Fixed Regex)

Features:
- Parallel crawling of multiple websites
- Robust email extraction (text + mailto)
- Strong garbage filtering
- URL counter and progress tracking
- Optimized for unlimited URLs with batch processing
- NO verification (removed for speed)
"""

import re
import io
import csv
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------
# Configuration
# -----------------------
# FIXED: Changed $ to \b for proper email extraction from HTML
EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', re.I)

# Exclude patterns and domains (from original)
EXCLUDED_KEYWORDS = ["support@", "account", "filter", "team", "hr", "enquiries", "press@", "job", "career", "sales", "inquiry", "yourname", "john", "example", "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"]
EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", "amazonaws", "localhost", "invalid", "example", "website", "2x.png"
]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf")

# Concurrency & batch settings
MAX_CRAWL_WORKERS = 20
BATCH_SIZE = 50

HEADERS = {"User-Agent": "EmailExtractor/1.0 (+https://example.com)"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100)
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

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)

def looks_like_garbage(email: str) -> bool:
    """Return True if email looks like machine-generated or obviously invalid for our use."""
    if not email or " " in email:
        return True
    e = email.strip().lower()

    # quick structure check - FIXED: using search instead of fullmatch
    if not EMAIL_REGEX.search(e):
        return True

    # local and domain parts
    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True

    # skip if domain ends with a file extension
    if any(domain.endswith(ext.lstrip(".")) or domain.endswith(ext) for ext in SKIP_EXTENSIONS):
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

    return False

def extract_emails_from_html(html: str) -> set:
    """Extract emails using regex and mailto links; returns set of lowercased emails."""
    found = set()
    if not html:
        return found
    
    # FIXED: Using findall with proper regex
    for m in EMAIL_REGEX.findall(html):
        found.add(m.lower())
    
    # parse mailto links
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split("?")[0].strip().lower()
                if email and EMAIL_REGEX.search(email):
                    found.add(email)
    except Exception:
        pass
    
    return found

# -----------------------
# Crawling
# -----------------------
def crawl_site(url: str, crawl_depth: int = 1, max_pages: int = 30, delay: float = 0.2) -> tuple:
    """Return (url, set_of_raw_emails, error_message_or_none)"""
    try:
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

        return url, found, None
    except Exception as e:
        return url, set(), str(e)

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor</h1>
  <p style="color:#333; font-size:14px;">Paste website URLs (one per line). Fast extraction without verification.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=350)
        
        # URL Counter
        if urls_input:
            valid_urls = [normalize_url(line) for line in urls_input.splitlines() if normalize_url(line)]
            url_count = len(valid_urls)
            st.markdown(f"<p style='color:#666; font-size:13px; margin-top:-10px;'>📊 Total URLs entered: <strong>{url_count}</strong></p>", unsafe_allow_html=True)
        else:
            st.markdown(f"<p style='color:#999; font-size:13px; margin-top:-10px;'>📊 Total URLs entered: <strong>0</strong></p>", unsafe_allow_html=True)
    
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 2, 1)
        max_pages = st.number_input("Max pages per site", 1, 200, 30)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)

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
        total_urls = len(websites)
        all_results = {}
        unique_emails = set()
        processed_count = 0
        error_count = 0
        
        st.info(f"⏳ Starting extraction from {total_urls} website(s)...")
        
        # Progress tracking
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Batch processing for unlimited URLs
        batch_num = 0
        total_batches = (total_urls + BATCH_SIZE - 1) // BATCH_SIZE
        
        for i in range(0, total_urls, BATCH_SIZE):
            batch_num += 1
            batch = websites[i:i + BATCH_SIZE]
            batch_size = len(batch)
            
            status_text.markdown(f"**📦 Processing batch {batch_num}/{total_batches}** (URLs {i+1} to {i+batch_size})")

            # crawl in parallel
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in batch}
                for fut in as_completed(futures):
                    url, raw_emails, error = fut.result()
                    processed_count += 1
                    
                    # Update progress
                    progress = processed_count / total_urls
                    progress_bar.progress(progress)
                    
                    if error:
                        error_count += 1
                    else:
                        # filter garbage & excluded keywords now
                        cleaned = {e for e in raw_emails if not looks_like_garbage(e)}
                        # also filter EXCLUDED_KEYWORDS explicitly
                        cleaned = {e for e in cleaned if not any(k in e for k in EXCLUDED_KEYWORDS)}
                        all_results[url] = {
                            "raw": sorted(raw_emails),
                            "clean": sorted(cleaned)
                        }
                        unique_emails.update(cleaned)
                    
                    # Update status
                    status_text.markdown(
                        f"**⏳ Processing:** {processed_count}/{total_urls} | "
                        f"**✅ Success:** {processed_count - error_count} | "
                        f"**❌ Errors:** {error_count} | "
                        f"**📧 Emails found:** {len(unique_emails)}"
                    )
        
        # Complete progress
        progress_bar.progress(1.0)
        status_text.success(
            f"✅ **Completed!** Processed {processed_count}/{total_urls} URLs | "
            f"Success: {processed_count - error_count} | Errors: {error_count} | "
            f"Total unique emails: {len(unique_emails)}"
        )
        
        st.markdown("---")
        
        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total URLs Processed", processed_count)
        with col2:
            st.metric("Successful Extractions", processed_count - error_count)
        with col3:
            st.metric("Total Unique Emails", len(unique_emails))
        with col4:
            st.metric("Failed URLs", error_count)
        
        st.markdown("---")
        
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
                rows = max(1, len(df_raw))
                height = max(180, min(500, 32 * rows))
                st.dataframe(df_raw, height=height)
            else:
                st.markdown("→ No raw emails found.")

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

            # Clean (if any)
            st.markdown("**Filtered Emails (cleaned):**")
            if clean:
                df_clean = pd.DataFrame({"Email": clean})
                rows = max(1, len(df_clean))
                height = max(180, min(600, 32 * rows))
                st.dataframe(df_clean, height=height)
            else:
                st.markdown("→ No filtered emails found.")

            st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # CSV download
        if unique_emails:
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email"])
            for site, data in all_results.items():
                for e in data["clean"]:
                    writer.writerow([site, e])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")
            st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
            st.download_button("📥 Download all emails (CSV)", data=csv_bytes, file_name="emails.csv", mime="text/csv")

        # Finish notification & branding
        st.balloons()
        st.success(f"🎉 Extraction completed! Unique cleaned emails: {len(unique_emails)}")
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
                    body: "Done! {len(unique_emails)} unique cleaned emails",
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
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:0px solid #eee;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
