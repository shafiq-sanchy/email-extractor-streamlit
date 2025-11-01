# app.py
"""
Email Extractor - Optimized for Speed & Large Scale

Features:
- Ultra-fast parallel crawling (1000+ URLs supported)
- Deep crawling (hidden pages, forms, mailto links)
- Smart error handling (individual failures won't stop the process)
- Real browser User-Agent to avoid blocking
- Session state for result persistence
- Easy copy and CSV download
- No verification (for maximum speed)
"""

import re
import io
import csv
import time
from functools import lru_cache
from urllib.parse import urljoin, urlparse, parse_qs

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
EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", "amazonaws", "localhost", "invalid", "example", "website", "2x.png"
]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf")

# Performance optimization for large scale
MAX_CRAWL_WORKERS = 20      # Increased for faster processing
BATCH_SIZE = 50             # Process 50 URLs at a time
REQUEST_TIMEOUT = 8         # Reduced timeout for faster failure detection
MAX_RETRIES = 1             # Minimal retries for speed

# Realistic browser headers to avoid blocking
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Session with retry strategy
session = requests.Session()
retries = Retry(total=MAX_RETRIES, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Utility Functions
# -----------------------
def normalize_url(url: str) -> str | None:
    """Normalize and validate URL"""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)

def looks_like_garbage(email: str) -> bool:
    """Filter out invalid/garbage emails"""
    if not email or " " in email:
        return True
    e = email.strip().lower()

    if EMAIL_REGEX.fullmatch(e) is None:
        return True

    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True

    # Skip file extensions
    if any(domain.endswith(ext.lstrip(".")) or domain.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True

    # Skip hex garbage (system IDs)
    if HEX_GARBAGE_RE.fullmatch(local):
        return True

    # Skip noisy domains
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain:
            return True

    # Skip excluded keywords
    for kw in EXCLUDED_KEYWORDS:
        if kw in e:
            return True

    return False

def extract_emails_from_html(html: str, url: str = "") -> set:
    """
    Deep email extraction from HTML:
    - Text content (regex)
    - Mailto links
    - Form action attributes
    - JavaScript variables
    - Hidden input values
    - Meta tags
    """
    found = set()
    if not html:
        return found
    
    # 1. Regex extraction from text
    for m in EMAIL_REGEX.findall(html):
        found.add(m.lower())
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # 2. Mailto links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split("?")[0].strip().lower()
                if email:
                    found.add(email)
        
        # 3. Form actions (some sites hide emails in form actions)
        for form in soup.find_all("form", action=True):
            action = form["action"]
            emails_in_action = EMAIL_REGEX.findall(action)
            for e in emails_in_action:
                found.add(e.lower())
        
        # 4. Input fields with email type or value
        for inp in soup.find_all("input"):
            # Check value attribute
            if inp.get("value"):
                emails_in_value = EMAIL_REGEX.findall(inp["value"])
                for e in emails_in_value:
                    found.add(e.lower())
            # Check placeholder
            if inp.get("placeholder"):
                emails_in_placeholder = EMAIL_REGEX.findall(inp["placeholder"])
                for e in emails_in_placeholder:
                    found.add(e.lower())
        
        # 5. Meta tags (some sites put contact info in meta)
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            emails_in_meta = EMAIL_REGEX.findall(content)
            for e in emails_in_meta:
                found.add(e.lower())
        
        # 6. Script tags (emails in JavaScript)
        for script in soup.find_all("script"):
            if script.string:
                emails_in_js = EMAIL_REGEX.findall(script.string)
                for e in emails_in_js:
                    found.add(e.lower())
        
        # 7. Comments (sometimes emails are in HTML comments)
        for comment in soup.find_all(string=lambda text: isinstance(text, str)):
            emails_in_comment = EMAIL_REGEX.findall(str(comment))
            for e in emails_in_comment:
                found.add(e.lower())
                
    except Exception as e:
        # Log but don't fail
        pass
    
    return found

# -----------------------
# Crawling with Deep Search
# -----------------------
def crawl_site(url: str, crawl_depth: int = 2, max_pages: int = 50, delay: float = 0.1) -> dict:
    """
    Crawl website and extract emails with error handling
    Returns: {
        'url': original_url,
        'status': 'success' or 'error',
        'emails': set of emails,
        'pages_crawled': count,
        'error_message': if error occurred
    }
    """
    result = {
        'url': url,
        'status': 'error',
        'emails': set(),
        'pages_crawled': 0,
        'error_message': None
    }
    
    try:
        parsed = urlparse(url)
        base_domain = parsed.netloc
        
        if not base_domain:
            result['error_message'] = "Invalid URL format"
            return result
        
        to_visit = [(url, 0)]
        seen = set([url])
        found = set()
        pages = 0

        while to_visit and pages < max_pages:
            current, depth = to_visit.pop(0)
            pages += 1
            
            try:
                r = session.get(current, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=False)
                html = r.text
                
                # Extract emails from this page
                found.update(extract_emails_from_html(html, current))
                
                # Only continue crawling if within depth limit
                if depth < crawl_depth:
                    try:
                        soup = BeautifulSoup(html, "html.parser")
                        
                        # Find all links
                        for a in soup.find_all("a", href=True):
                            href = a["href"].strip()
                            
                            # Skip javascript, mailto, tel, etc
                            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                                continue
                            
                            joined = urljoin(current, href)
                            p = urlparse(joined)
                            
                            # Only same domain
                            if p.scheme not in ("http", "https"):
                                continue
                            if p.netloc != base_domain:
                                continue
                            
                            # Normalize (remove fragments)
                            norm = p._replace(fragment="").geturl()
                            
                            if norm not in seen:
                                seen.add(norm)
                                to_visit.append((norm, depth + 1))
                    except Exception:
                        pass
                
                # Small delay to be polite
                if delay > 0:
                    time.sleep(delay)
                    
            except requests.exceptions.Timeout:
                result['error_message'] = f"Timeout on page {pages}"
                continue
            except requests.exceptions.ConnectionError:
                result['error_message'] = f"Connection error on page {pages}"
                continue
            except Exception as e:
                result['error_message'] = f"Error on page {pages}: {str(e)[:50]}"
                continue
        
        result['status'] = 'success' if found else 'no_emails'
        result['emails'] = found
        result['pages_crawled'] = pages
        
        if not found and not result['error_message']:
            result['error_message'] = "No emails found"
            
    except Exception as e:
        result['status'] = 'error'
        result['error_message'] = str(e)[:100]
    
    return result

# -----------------------
# Session State
# -----------------------
if 'results_ready' not in st.session_state:
    st.session_state.results_ready = False
if 'all_results' not in st.session_state:
    st.session_state.all_results = {}
if 'unique_emails' not in st.session_state:
    st.session_state.unique_emails = set()
if 'total_pages_crawled' not in st.session_state:
    st.session_state.total_pages_crawled = 0
if 'failed_urls' not in st.session_state:
    st.session_state.failed_urls = []

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor Pro", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor Pro</h1>
  <p style="color:#333; font-size:14px;">Ultra-fast email extraction from unlimited websites. Deep crawling with smart error handling.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line) - Supports 1000+ URLs", height=350)
    with col2:
        crawl_depth = st.slider("Crawl depth", 0, 3, 2, help="0=Homepage only, 1=Homepage + direct links, 2-3=Deeper pages")
        max_pages = st.number_input("Max pages per site", 1, 200, 50, help="More pages = more emails but slower")
        delay = st.number_input("Delay (seconds)", 0.0, 2.0, 0.1, 0.05, help="Delay between requests (0.1 is good)")
        
        st.markdown("### ⚡ Performance")
        st.markdown(f"- **Workers**: {MAX_CRAWL_WORKERS}")
        st.markdown(f"- **Batch Size**: {BATCH_SIZE}")
        st.markdown(f"- **Timeout**: {REQUEST_TIMEOUT}s")

st.markdown("---")

col_btn1, col_btn2 = st.columns([1, 5])
with col_btn1:
    extract_button = st.button("🚀 Extract Emails", use_container_width=True)
with col_btn2:
    if st.session_state.results_ready:
        if st.button("🔄 Clear & Start Fresh", use_container_width=True):
            st.session_state.results_ready = False
            st.session_state.all_results = {}
            st.session_state.unique_emails = set()
            st.session_state.total_pages_crawled = 0
            st.session_state.failed_urls = []
            st.rerun()

if extract_button:
    # Reset
    st.session_state.results_ready = False
    st.session_state.all_results = {}
    st.session_state.unique_emails = set()
    st.session_state.total_pages_crawled = 0
    st.session_state.failed_urls = []
    
    # Parse URLs
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(n)

    if not websites:
        st.warning("⚠️ Please enter at least one URL.")
    else:
        total_sites = len(websites)
        st.success(f"🎯 Processing **{total_sites}** websites...")
        
        # Progress tracking
        progress_bar = st.progress(0)
        status_text = st.empty()
        metrics_col1, metrics_col2, metrics_col3, metrics_col4 = st.columns(4)
        
        all_results = {}
        unique_emails = set()
        total_pages = 0
        failed_urls = []
        
        completed = 0
        start_time = time.time()
        
        # Process in batches
        for batch_start in range(0, total_sites, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_sites)
            batch = websites[batch_start:batch_end]
            batch_num = (batch_start // BATCH_SIZE) + 1
            total_batches = (total_sites + BATCH_SIZE - 1) // BATCH_SIZE
            
            status_text.markdown(f"⚙️ **Batch {batch_num}/{total_batches}** | Processing {len(batch)} sites...")
            
            # Parallel crawling
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in batch}
                
                for fut in as_completed(futures):
                    url = futures[fut]
                    try:
                        result = fut.result()
                        
                        # Filter garbage emails
                        raw_emails = result['emails']
                        cleaned = {e for e in raw_emails if not looks_like_garbage(e)}
                        cleaned = {e for e in cleaned if not any(k in e for k in EXCLUDED_KEYWORDS)}
                        
                        all_results[url] = {
                            "status": result['status'],
                            "raw": sorted(raw_emails),
                            "clean": sorted(cleaned),
                            "pages": result['pages_crawled'],
                            "error": result.get('error_message')
                        }
                        
                        unique_emails.update(cleaned)
                        total_pages += result['pages_crawled']
                        
                        if result['status'] == 'error':
                            failed_urls.append((url, result.get('error_message', 'Unknown error')))
                        
                    except Exception as e:
                        all_results[url] = {
                            "status": "error",
                            "raw": [],
                            "clean": [],
                            "pages": 0,
                            "error": str(e)[:100]
                        }
                        failed_urls.append((url, str(e)[:100]))
                    
                    completed += 1
                    progress = completed / total_sites
                    progress_bar.progress(progress)
                    
                    # Real-time metrics
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = (total_sites - completed) / rate if rate > 0 else 0
                    
                    metrics_col1.metric("✅ Completed", f"{completed}/{total_sites}")
                    metrics_col2.metric("📧 Emails Found", len(unique_emails))
                    metrics_col3.metric("📄 Pages Crawled", total_pages)
                    metrics_col4.metric("⚡ Speed", f"{rate:.1f} sites/sec")
                    
                    status_text.markdown(f"⏱️ **ETA**: ~{int(eta)}s | **Success**: {completed - len(failed_urls)} | **Errors**: {len(failed_urls)}")
        
        # Completion
        progress_bar.progress(1.0)
        elapsed_total = time.time() - start_time
        
        # Save to session
        st.session_state.all_results = all_results
        st.session_state.unique_emails = unique_emails
        st.session_state.total_pages_crawled = total_pages
        st.session_state.failed_urls = failed_urls
        st.session_state.results_ready = True
        
        st.balloons()
        st.success(f"🎉 **Completed in {elapsed_total:.1f}s!** Found **{len(unique_emails)}** unique emails from **{total_pages}** pages!")

# -----------------------
# Display Results
# -----------------------
if st.session_state.results_ready:
    all_results = st.session_state.all_results
    unique_emails = st.session_state.unique_emails
    total_pages = st.session_state.total_pages_crawled
    failed_urls = st.session_state.failed_urls
    
    st.markdown("---")
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🌐 Websites Processed", len(all_results))
    col2.metric("📧 Unique Emails", len(unique_emails))
    col3.metric("📄 Total Pages Crawled", total_pages)
    col4.metric("❌ Failed URLs", len(failed_urls))
    
    # Quick Actions
    st.markdown("### 🎯 Quick Actions")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if unique_emails:
            emails_text = "\n".join(sorted(unique_emails))
            st.download_button(
                "📥 Download Emails (TXT)",
                data=emails_text,
                file_name="emails.txt",
                mime="text/plain",
                use_container_width=True
            )
    
    with col2:
        if unique_emails:
            # CSV with website mapping
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email", "status", "pages_crawled"])
            for site, data in all_results.items():
                for e in data["clean"]:
                    writer.writerow([site, e, data["status"], data["pages"]])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")
            
            st.download_button(
                "📥 Download Detailed CSV",
                data=csv_bytes,
                file_name="emails_detailed.csv",
                mime="text/csv",
                use_container_width=True
            )
    
    with col3:
        if unique_emails:
            # Copy to clipboard text
            st.text_area("📋 Copy All Emails", value=emails_text, height=100, key="copy_emails")
    
    # Detailed Results
    st.markdown("---")
    st.subheader("📊 Detailed Results")
    
    # Tabs for different views
    tab1, tab2, tab3 = st.tabs(["✅ All Emails", "🌐 By Website", "❌ Errors"])
    
    with tab1:
        if unique_emails:
            df_all = pd.DataFrame({"Email": sorted(unique_emails)})
            st.dataframe(df_all, height=400, use_container_width=True)
        else:
            st.warning("No emails found.")
    
    with tab2:
        for site, data in all_results.items():
            status_icon = "✅" if data["status"] == "success" else "⚠️" if data["status"] == "no_emails" else "❌"
            with st.expander(f"{status_icon} {site} ({len(data['clean'])} emails, {data['pages']} pages)"):
                if data["clean"]:
                    df_site = pd.DataFrame({"Email": data["clean"]})
                    st.dataframe(df_site, height=min(300, 40 * len(data["clean"])))
                else:
                    st.info(f"No emails found. {data.get('error', '')}")
    
    with tab3:
        if failed_urls:
            st.warning(f"⚠️ {len(failed_urls)} URLs failed to process:")
            df_errors = pd.DataFrame(failed_urls, columns=["URL", "Error"])
            st.dataframe(df_errors, height=400, use_container_width=True)
        else:
            st.success("✅ All URLs processed successfully!")
    
    st.info("💡 Done by Shafiq Sanchy")

# Footer
st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
© Shafiq Sanchy 2025 | Email Extractor Pro v2.0
</div>
""", unsafe_allow_html=True)
