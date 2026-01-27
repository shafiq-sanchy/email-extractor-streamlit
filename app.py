"""
Email Extractor (optimized)

Features:
- Parallel crawling of multiple websites
- Robust email extraction (text + mailto)
- Strong garbage filtering (image-file like, hashed sentry IDs, known noisy domains)
- Progress bar and real-time activity display
- Stop, Pause, Resume functionality
- Safe UI heights and spacing to avoid overlap
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
import threading

# -----------------------
# Configuration
# -----------------------
# stricter regex: ensures at least 2-letter TLD, avoid catching weird partial strings
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', re.I)

# Exclude patterns and domains (customize as needed)
EXCLUDED_KEYWORDS = ["support@", "account", "filter", "team", "hr", "enquiries", "press@", "job", "career", "sales", "inquiry", "yourname", "john", "example", "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"]
EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", "amazonaws", "localhost", "invalid", "example", "website", "2x.png"
]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf")

# Concurrency & requests session
MAX_CRAWL_WORKERS = 12

HEADERS = {"User-Agent": "EmailExtractor/1.0 (+https://example.com)"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Control flags for stop/pause/resume
# -----------------------
class CrawlerControl:
    def __init__(self):
        self.stopped = False
        self.paused = False
        self.lock = threading.Lock()
    
    def stop(self):
        with self.lock:
            self.stopped = True
    
    def pause(self):
        with self.lock:
            self.paused = True
    
    def resume(self):
        with self.lock:
            self.paused = False
    
    def is_stopped(self):
        with self.lock:
            return self.stopped
    
    def is_paused(self):
        with self.lock:
            return self.paused
    
    def should_continue(self):
        with self.lock:
            return not self.stopped
    
    def wait_if_paused(self):
        while True:
            with self.lock:
                if not self.paused or self.stopped:
                    break
            time.sleep(0.1)

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
# Crawling
# -----------------------
def crawl_site(url: str, crawl_depth: int = 1, max_pages: int = 30, delay: float = 0.2, control: CrawlerControl = None) -> tuple:
    """Return (url, set_of_raw_emails)"""
    if control and control.is_stopped():
        return url, set()
    
    parsed = urlparse(url)
    base_domain = parsed.netloc
    to_visit = [(url, 0)]
    seen = set([url])
    found = set()
    pages = 0

    while to_visit and pages < max_pages:
        if control:
            control.wait_if_paused()
            if control.is_stopped():
                break
        
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

# Initialize session state for control
if 'control' not in st.session_state:
    st.session_state.control = None
if 'extraction_running' not in st.session_state:
    st.session_state.extraction_running = False

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor</h1>
  <p style="color:#333; font-size:14px;">Paste website URLs (one per line). Fast extraction with real-time progress tracking.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=300)
        # Count URLs
        url_lines = [line.strip() for line in urls_input.splitlines() if line.strip()]
        url_count = len([normalize_url(line) for line in url_lines if normalize_url(line)])
        if url_count > 0:
            st.markdown(f"<p style='color:#0066cc; font-size:13px; margin-top:-10px;'>📊 {url_count} URL(s) added</p>", unsafe_allow_html=True)
    
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 3, 2)
        max_pages = st.number_input("Max pages per site", 1, 500, 50)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)
        batch_size = st.number_input("Batch size (process in chunks)", 5, 100, 20)

st.markdown("---")

# Control buttons with better state management
if 'is_paused' not in st.session_state:
    st.session_state.is_paused = False

col_btn1, col_btn2, col_btn3, col_btn4, col_btn5 = st.columns([1.2, 1, 1, 1, 2.8])

with col_btn1:
    start_button = st.button("🚀 Start Extraction", use_container_width=True, 
                            disabled=st.session_state.extraction_running,
                            type="primary")

with col_btn2:
    pause_button = st.button("⏸️ Pause", use_container_width=True, 
                            disabled=not st.session_state.extraction_running or st.session_state.is_paused)
    if pause_button and st.session_state.control:
        st.session_state.control.pause()
        st.session_state.is_paused = True
        st.toast("⏸️ Paused", icon="⏸️")

with col_btn3:
    resume_button = st.button("▶️ Resume", use_container_width=True, 
                             disabled=not st.session_state.extraction_running or not st.session_state.is_paused)
    if resume_button and st.session_state.control:
        st.session_state.control.resume()
        st.session_state.is_paused = False
        st.toast("▶️ Resumed", icon="▶️")

with col_btn4:
    stop_button = st.button("⏹️ Stop", use_container_width=True, 
                           disabled=not st.session_state.extraction_running)
    if stop_button and st.session_state.control:
        st.session_state.control.stop()
        st.session_state.extraction_running = False
        st.session_state.is_paused = False
        st.toast("⏹️ Stopped", icon="⏹️")

st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

if start_button:
    # normalize and resolve
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(resolve_url(n))

    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        st.session_state.extraction_running = True
        st.session_state.control = CrawlerControl()
        st.session_state.is_paused = False
        control = st.session_state.control
        
        # Create placeholders for dynamic updates
        main_container = st.container()
        
        with main_container:
            col_status1, col_status2 = st.columns([2, 1])
            with col_status1:
                st.info(f"⏳ Processing {len(websites)} website(s) in batches of {batch_size}...")
            with col_status2:
                # Compact summary on the side
                summary_placeholder = st.empty()
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Collapsible activity log
            with st.expander("📊 Detailed Activity & Progress", expanded=False):
                activity_log = st.empty()
                site_stats = st.empty()
        
        all_results = {}
        unique_emails = set()
        completed = 0
        activity_messages = []
        
        # Process in batches to avoid streamlit timeout
        total_batches = (len(websites) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            if control.is_stopped():
                break
            
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(websites))
            batch_websites = websites[start_idx:end_idx]
            
            # crawl batch in parallel
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay, control): url for url in batch_websites}
                
                for fut in as_completed(futures):
                    if control.is_stopped():
                        for f in futures:
                            f.cancel()
                        break
                    
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
                        
                        # Update compact summary
                        summary_placeholder.markdown(f"""
                        <div style='background:#f0f8ff; padding:8px; border-radius:5px; font-size:12px;'>
                        <b>📊 Quick Stats</b><br>
                        ✅ Processed: {completed}/{len(websites)}<br>
                        📧 Unique Emails: {len(unique_emails)}<br>
                        📦 Batch: {batch_num + 1}/{total_batches}
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Update status
                        status_text.markdown(f"**Processing:** {completed}/{len(websites)} websites | **Found:** {len(unique_emails)} unique emails | **Batch:** {batch_num + 1}/{total_batches}")
                        
                        # Update activity log in dropdown
                        activity_messages.append(f"✅ {url[:50]}... → {len(cleaned)} emails")
                        if len(activity_messages) > 15:
                            activity_messages.pop(0)
                        activity_log.markdown("**Recent Activity:**\n" + "\n".join([f"- {msg}" for msg in activity_messages[-15:]]))
                        
                        # Site stats in dropdown
                        site_stats_text = "**Website Email Counts:**\n"
                        for site, data in list(all_results.items())[-10:]:
                            site_stats_text += f"- {site[:40]}... → {len(data['clean'])} emails\n"
                        site_stats.markdown(site_stats_text)
                        
                    except Exception as e:
                        activity_messages.append(f"❌ {url[:50]}... → Error")
                        if len(activity_messages) > 15:
                            activity_messages.pop(0)
                        activity_log.markdown("**Recent Activity:**\n" + "\n".join([f"- {msg}" for msg in activity_messages[-15:]]))
            
            # Small delay between batches
            if not control.is_stopped() and batch_num < total_batches - 1:
                time.sleep(0.5)

        st.session_state.extraction_running = False
        st.session_state.is_paused = False
        
        if control.is_stopped():
            st.warning("⏹️ Extraction was stopped by user")
        else:
            progress_bar.progress(1.0)
            st.success(f"✅ Extraction completed!")
        
        st.markdown("---")
        
        # Direct email display for easy copying
        st.subheader("📧 All Extracted Emails")
        
        if unique_emails:
            col_display, col_download = st.columns([3, 1])
            
            with col_display:
                # Create copyable text area with all emails
                emails_text = "\n".join(sorted(unique_emails))
                st.text_area("Copy all emails from here:", emails_text, height=300, 
                           help="Select all (Ctrl+A / Cmd+A) and copy (Ctrl+C / Cmd+C)")
            
            with col_download:
                st.markdown("<div style='height:42px'></div>", unsafe_allow_html=True)
                # CSV download
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(["website", "email"])
                for site, data in all_results.items():
                    for e in data["clean"]:
                        writer.writerow([site, e])
                csv_bytes = csv_buffer.getvalue().encode("utf-8")
                st.download_button("📥 Download CSV", data=csv_bytes, file_name="emails.csv", 
                                 mime="text/csv", use_container_width=True)
                
                # Text file download
                txt_bytes = emails_text.encode("utf-8")
                st.download_button("📄 Download TXT", data=txt_bytes, file_name="emails.txt", 
                                 mime="text/plain", use_container_width=True)
                
                # Show compact stats
                st.markdown(f"""
                <div style='background:#e8f5e9; padding:10px; border-radius:5px; margin-top:12px;'>
                <b>📊 Final Results</b><br>
                <small>
                • Sites: {len(all_results)}<br>
                • Raw: {sum(len(data['raw']) for data in all_results.values())}<br>
                • Clean: {len(unique_emails)}
                </small>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No emails found from the provided websites.")
        
        # Detailed results in collapsible section
        with st.expander("🔍 Detailed Results by Website", expanded=False):
            for site, data in all_results.items():
                st.markdown(f"**🌐 {site}**")
                raw = data["raw"]
                clean = data["clean"]
                
                col_raw, col_clean = st.columns(2)
                
                with col_raw:
                    st.markdown(f"*Raw: {len(raw)} emails*")
                    if raw:
                        st.text("\n".join(raw[:20]))
                        if len(raw) > 20:
                            st.markdown(f"*...and {len(raw) - 20} more*")
                
                with col_clean:
                    st.markdown(f"*Clean: {len(clean)} emails*")
                    if clean:
                        st.text("\n".join(clean[:20]))
                        if len(clean) > 20:
                            st.markdown(f"*...and {len(clean) - 20} more*")
                
                st.markdown("---")

        # Finish notification & branding
        if not control.is_stopped():
            st.balloons()
            st.success(f"🎉 Extraction completed! Unique cleaned emails: {len(unique_emails)}")
        
        st.info("💡 Done by Shafiq Sanchy")

        # browser notification + sound
        if not control.is_stopped():
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
