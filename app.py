"""
Email Extractor - Fast & Optimized

Features:
- Parallel crawling of multiple websites
- Robust email extraction (text + mailto)
- Strong garbage filtering
- Batch processing for unlimited URLs
- Real-time progress tracking and email display
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
  <p style="color:#333; font-size:14px;">Paste website URLs (one per line). Fast extraction with real-time progress tracking.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=350)
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 3, 2)
        max_pages = st.number_input("Max pages per site", 1, 500, 50)
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
        st.info(f"⏳ Starting extraction from {len(websites)} website(s)...")
        
        # Create placeholders for real-time updates
        progress_bar = st.progress(0)
        status_text = st.empty()
        activity_container = st.container()
        
        # Real-time email display
        st.markdown("### 📧 Extracted Emails (Real-time)")
        realtime_email_display = st.empty()
        
        all_results = {}
        unique_emails = set()
        completed = 0

        # Batch processing
        batch_size = 25
        total_batches = (len(websites) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(websites))
            batch_websites = websites[start_idx:end_idx]
            
            # crawl batch in parallel
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in batch_websites}
                
                for fut in as_completed(futures):
                    url = futures[fut]
                    try:
                        _, raw_emails = fut.result()
                        
                        # filter garbage & excluded keywords
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
                        status_text.markdown(f"**Progress: {completed}/{len(websites)} websites processed | Found: {len(unique_emails)} unique emails**")
                        
                        # Real-time email display
                        if unique_emails:
                            emails_text = "\n".join(sorted(unique_emails))
                            realtime_email_display.text_area(
                                f"✅ Found {len(unique_emails)} unique emails so far:",
                                emails_text,
                                height=300,
                                key=f"realtime_{completed}"
                            )
                        
                    except Exception as e:
                        pass
        
        # Show simple activity summary
        with activity_container:
            with st.expander("📊 Processing Summary", expanded=False):
                st.markdown(f"**Total websites processed:** {completed}/{len(websites)}")
                st.markdown(f"**Total batches:** {total_batches}")
                st.markdown(f"**Unique emails found:** {len(unique_emails)}")
                
                # Show per-site summary
                st.markdown("**Emails per website:**")
                for site, data in all_results.items():
                    st.markdown(f"- {site}: {len(data['clean'])} emails")
        
        progress_bar.progress(1.0)
        st.success(f"✅ Extraction completed!")
        
        st.markdown("---")
        
        # Final results with copy button
        st.subheader("📋 Final Results")
        
        if unique_emails:
            emails_text = "\n".join(sorted(unique_emails))
            
            # Text area with emails
            col_text, col_btn = st.columns([4, 1])
            with col_text:
                st.text_area("All extracted emails:", emails_text, height=400, key="final_emails")
            
            with col_btn:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                # Copy button using JavaScript
                copy_js = f"""
                <button onclick="copyToClipboard()" style="
                    background-color:#2196F3;
                    color:white;
                    padding:10px 20px;
                    border:none;
                    border-radius:5px;
                    cursor:pointer;
                    font-size:14px;
                    font-weight:bold;
                    width:100%;
                ">
                    📋 Copy All
                </button>
                <script>
                function copyToClipboard() {{
                    const text = `{emails_text}`;
                    navigator.clipboard.writeText(text).then(function() {{
                        alert('✅ Copied {len(unique_emails)} emails to clipboard!');
                    }}, function(err) {{
                        alert('❌ Failed to copy');
                    }});
                }}
                </script>
                """
                import streamlit.components.v1 as components
                components.html(copy_js, height=50)
                
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                
                # Download buttons
                # CSV
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(["website", "email"])
                for site, data in all_results.items():
                    for e in data["clean"]:
                        writer.writerow([site, e])
                csv_bytes = csv_buffer.getvalue().encode("utf-8")
                st.download_button("📥 Download CSV", data=csv_bytes, file_name="emails.csv", 
                                 mime="text/csv", use_container_width=True)
                
                # TXT
                txt_bytes = emails_text.encode("utf-8")
                st.download_button("📄 Download TXT", data=txt_bytes, file_name="emails.txt", 
                                 mime="text/plain", use_container_width=True)
            
            # Summary
            st.markdown("---")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Websites Processed", completed)
            with col2:
                st.metric("Total Raw Emails", sum(len(data["raw"]) for data in all_results.values()))
            with col3:
                st.metric("Unique Clean Emails", len(unique_emails))
        
        # Detailed results per website
        st.markdown("---")
        with st.expander("🔍 Detailed Results by Website", expanded=False):
            for site, data in all_results.items():
                st.markdown(f"### 🌐 {site}")
                raw = data["raw"]
                clean = data["clean"]

                col_raw, col_clean = st.columns(2)
                
                with col_raw:
                    st.markdown("**Raw Emails Found:**")
                    if raw:
                        df_raw = pd.DataFrame({"Email": raw})
                        rows = max(1, len(df_raw))
                        height = max(180, min(500, 32 * rows))
                        st.dataframe(df_raw, height=height)
                    else:
                        st.markdown("→ No raw emails found.")

                with col_clean:
                    st.markdown("**Filtered Emails (cleaned):**")
                    if clean:
                        df_clean = pd.DataFrame({"Email": clean})
                        rows = max(1, len(df_clean))
                        height = max(180, min(600, 32 * rows))
                        st.dataframe(df_clean, height=height)
                    else:
                        st.markdown("→ No filtered emails found.")
                
                st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # Finish notification
        st.balloons()
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
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
