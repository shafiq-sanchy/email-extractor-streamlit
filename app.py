"""
Email Extractor - Fast & Optimized

Features:
- Parallel crawling of multiple websites
- Robust email extraction (text + mailto)
- Strong garbage filtering
- Batch processing for unlimited URLs
- Real-time progress tracking
- User-friendly interface
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
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', re.I)

EXCLUDED_KEYWORDS = ["support@", "account", "filter", "team", "hr", "enquiries", "press@", "job", "career", "sales", "inquiry", "yourname", "john", "example", "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"]
EXCLUDED_DOMAINS_SUBSTR = ["sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", "amazonaws", "localhost", "invalid", "example", "website", "2x.png"]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf")

# Optimized for speed
MAX_CRAWL_WORKERS = 20
HEADERS = {"User-Agent": "EmailExtractor/1.0 (+https://example.com)"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=1, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Utility Functions
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
    """Quick URL resolution"""
    try:
        resp = session.head(url, allow_redirects=True, headers=HEADERS, timeout=5, verify=False)
        return resp.url or url
    except Exception:
        return url

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)

def looks_like_garbage(email: str) -> bool:
    """Check if email looks like garbage"""
    if not email or " " in email:
        return True
    e = email.strip().lower()
    if EMAIL_REGEX.fullmatch(e) is None:
        return True
    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True
    if any(domain.endswith(ext.lstrip(".")) or domain.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    if HEX_GARBAGE_RE.fullmatch(local):
        return True
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain:
            return True
    for kw in EXCLUDED_KEYWORDS:
        if kw in e:
            return True
    return False

def extract_emails_from_html(html: str) -> set:
    """Extract emails from HTML"""
    found = set()
    if not html:
        return found
    for m in set(EMAIL_REGEX.findall(html)):
        found.add(m.lower())
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
# Crawling Function
# -----------------------
def crawl_site(url: str, crawl_depth: int = 1, max_pages: int = 30, delay: float = 0.2) -> tuple:
    """Crawl website and extract emails"""
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
            r = session.get(current, headers=HEADERS, timeout=7, verify=False)
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

        if delay > 0:
            time.sleep(delay)

    return url, found

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor</h1>
  <p style="color:#333; font-size:14px;">Fast email extraction with real-time progress tracking</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([2.5, 1.5])
    with col1:
        urls_input = st.text_area("📝 Enter website URLs (one per line)", height=280, 
                                 placeholder="https://example.com\nhttps://another-site.com")
        # Count URLs
        url_lines = [line.strip() for line in urls_input.splitlines() if line.strip()]
        url_count = len([normalize_url(line) for line in url_lines if normalize_url(line)])
        if url_count > 0:
            st.markdown(f"<div style='background:#e3f2fd; padding:8px; border-radius:5px; margin-top:8px;'><b>📊 {url_count} URL(s) ready to process</b></div>", unsafe_allow_html=True)
    
    with col2:
        st.markdown("### ⚙️ Settings")
        crawl_depth = st.slider("🔍 Crawl depth", 0, 3, 2, help="0 = homepage only, 3 = deep crawl")
        max_pages = st.number_input("📄 Max pages per site", 10, 500, 50, step=10)
        delay = st.number_input("⏱️ Delay (seconds)", 0.0, 2.0, 0.1, 0.1, help="Delay between requests")
        batch_size = st.number_input("📦 Batch size", 10, 100, 25, step=5, help="Process URLs in batches")

st.markdown("---")

# Extract button
extract_button = st.button("🚀 Extract Emails", use_container_width=False, type="primary")

if extract_button:
    # Normalize and resolve URLs
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(resolve_url(n))

    if not websites:
        st.warning("⚠️ Please enter at least one URL.")
    else:
        # Create placeholders
        main_container = st.container()
        
        with main_container:
            st.info(f"⏳ Processing {len(websites)} website(s) in batches of {batch_size}...")
            
            progress_bar = st.progress(0)
            status_placeholder = st.empty()
            
            # Collapsible activity log
            with st.expander("📊 Processing Details", expanded=False):
                activity_log = st.empty()
        
        all_results = {}
        unique_emails = set()
        completed = 0
        activity_messages = []
        
        # Process in batches
        total_batches = (len(websites) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(websites))
            batch_websites = websites[start_idx:end_idx]
            
            # Crawl batch in parallel
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in batch_websites}
                
                for fut in as_completed(futures):
                    url = futures[fut]
                    try:
                        _, raw_emails = fut.result()
                        
                        # Filter garbage
                        cleaned = {e for e in raw_emails if not looks_like_garbage(e)}
                        cleaned = {e for e in cleaned if not any(k in e for k in EXCLUDED_KEYWORDS)}
                        
                        all_results[url] = {
                            "raw": sorted(raw_emails),
                            "clean": sorted(cleaned)
                        }
                        unique_emails.update(cleaned)
                        
                        completed += 1
                        progress = completed / len(websites)
                        progress_bar.progress(progress)
                        
                        # Update status
                        status_placeholder.markdown(f"""
                        <div style='background:#f5f5f5; padding:12px; border-radius:8px; border-left:4px solid #2196F3;'>
                        <b>Progress:</b> {completed}/{len(websites)} websites | 
                        <b>Found:</b> {len(unique_emails)} unique emails | 
                        <b>Batch:</b> {batch_num + 1}/{total_batches}
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Update activity log
                        activity_messages.append(f"✅ {url[:60]}... → {len(cleaned)} emails")
                        if len(activity_messages) > 20:
                            activity_messages.pop(0)
                        activity_log.markdown("\n".join([f"- {msg}" for msg in activity_messages[-20:]]))
                        
                    except Exception:
                        activity_messages.append(f"❌ {url[:60]}... → Error")
                        if len(activity_messages) > 20:
                            activity_messages.pop(0)
                        activity_log.markdown("\n".join([f"- {msg}" for msg in activity_messages[-20:]]))

        progress_bar.progress(1.0)
        st.success(f"✅ Extraction completed! Found {len(unique_emails)} unique emails from {len(all_results)} websites")
        
        st.markdown("---")
        
        # Direct email display
        st.subheader("📧 Extracted Emails")
        
        if unique_emails:
            col_main, col_side = st.columns([2.5, 1.5])
            
            with col_main:
                emails_text = "\n".join(sorted(unique_emails))
                st.text_area("✅ All emails found (copy from here):", emails_text, height=350, 
                           help="Select all (Ctrl+A / Cmd+A) and copy (Ctrl+C / Cmd+C)")
            
            with col_side:
                # Stats card
                st.markdown(f"""
                <div style='background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                            padding:20px; border-radius:10px; color:white; margin-bottom:15px;'>
                    <h3 style='margin:0; color:white;'>📊 Results</h3>
                    <hr style='margin:10px 0; border-color:rgba(255,255,255,0.3);'>
                    <p style='margin:5px 0; font-size:16px;'>
                        <b>🌐 Sites:</b> {len(all_results)}<br>
                        <b>📨 Raw:</b> {sum(len(data['raw']) for data in all_results.values())}<br>
                        <b>✨ Clean:</b> {len(unique_emails)}
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                # Download buttons
                st.markdown("### 💾 Download")
                
                # CSV
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(["website", "email"])
                for site, data in all_results.items():
                    for e in data["clean"]:
                        writer.writerow([site, e])
                csv_bytes = csv_buffer.getvalue().encode("utf-8")
                st.download_button("📥 CSV File", data=csv_bytes, file_name="emails.csv", 
                                 mime="text/csv", use_container_width=True)
                
                # TXT
                txt_bytes = emails_text.encode("utf-8")
                st.download_button("📄 TXT File", data=txt_bytes, file_name="emails.txt", 
                                 mime="text/plain", use_container_width=True)
        else:
            st.info("No emails found from the provided websites.")
        
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        
        # Detailed results
        with st.expander("🔍 Detailed Results by Website", expanded=False):
            for site, data in all_results.items():
                st.markdown(f"**🌐 {site}**")
                raw = data["raw"]
                clean = data["clean"]
                
                col_raw, col_clean = st.columns(2)
                
                with col_raw:
                    st.markdown(f"*Raw: {len(raw)} emails*")
                    if raw:
                        display_raw = raw[:25]
                        st.code("\n".join(display_raw), language=None)
                        if len(raw) > 25:
                            st.markdown(f"*...and {len(raw) - 25} more*")
                
                with col_clean:
                    st.markdown(f"*Clean: {len(clean)} emails*")
                    if clean:
                        display_clean = clean[:25]
                        st.code("\n".join(display_clean), language=None)
                        if len(clean) > 25:
                            st.markdown(f"*...and {len(clean) - 25} more*")
                
                st.markdown("---")
        
        # Notifications
        st.balloons()
        st.info("💡 Done by Shafiq Sanchy")

        # Browser notification
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
                    body: "Done! {len(unique_emails)} unique emails found",
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
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
