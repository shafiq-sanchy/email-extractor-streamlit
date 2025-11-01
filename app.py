# app.py
"""
Email Extractor - Final Optimized Version

Features:
- URL shortener resolver (t.co, bit.ly, etc.)
- Deep crawling with multiple extraction methods
- Fast parallel processing (1000+ URLs)
- Smart filtering with original exclusion rules
- Session state for result persistence
- Debug mode for troubleshooting
- No verification for maximum speed
"""

import re
import io
import csv
import time
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

# -----------------------
# Configuration (from original)
# -----------------------
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', re.I)

# Original exclusion rules
EXCLUDED_KEYWORDS = [
    "support@", "account", "filter", "team", "hr", "enquiries", "press@", 
    "job", "career", "sales", "inquiry", "yourname", "john", "example", 
    "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"
]

EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", 
    "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", 
    "amazonaws", "localhost", "invalid", "example", "website", "2x.png"
]

SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", 
    ".domain", "example", ".webp", ".ico", ".bmp", ".pdf"
)

# Performance settings
MAX_CRAWL_WORKERS = 15
BATCH_SIZE = 50
REQUEST_TIMEOUT = 10
MAX_RETRIES = 2

# Realistic browser headers
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

# Session setup
session = requests.Session()
retries = Retry(total=MAX_RETRIES, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Utility Functions (Original + Improved)
# -----------------------
def normalize_url(url: str) -> str | None:
    """Normalize URL"""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url: str) -> str:
    """
    Resolve shortened URL to final destination (ORIGINAL FUNCTION)
    Handles: t.co, bit.ly, tinyurl, etc.
    """
    try:
        resp = session.head(url, allow_redirects=True, headers=HEADERS, timeout=8, verify=False)
        final = resp.url or url
        # Warm GET request (non-fatal)
        try:
            session.get(final, headers=HEADERS, timeout=6, verify=False)
        except Exception:
            pass
        return final
    except Exception:
        return url

# Original garbage detection
HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)

def looks_like_garbage(email: str) -> bool:
    """Original garbage filter logic"""
    if not email or " " in email:
        return True
    e = email.strip().lower()

    if EMAIL_REGEX.fullmatch(e) is None:
        return True

    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True

    # Skip if domain ends with file extension
    if any(domain.endswith(ext.lstrip(".")) or domain.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True

    # Skip if local part is long hex (system IDs)
    if HEX_GARBAGE_RE.fullmatch(local):
        return True

    # Skip if domain contains known noisy substrings
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain:
            return True

    # Skip specific excluded keywords
    for kw in EXCLUDED_KEYWORDS:
        if kw in e:
            return True

    return False

def extract_emails_from_html(html: str) -> set:
    """
    Enhanced extraction combining original + new methods
    """
    found = set()
    if not html:
        return found
    
    # Method 1: Regex on page content (original method - fastest)
    for m in set(EMAIL_REGEX.findall(html)):
        found.add(m.lower())
    
    try:
        # Try lxml parser first, fallback to html.parser
        try:
            soup = BeautifulSoup(html, "lxml")
        except:
            soup = BeautifulSoup(html, "html.parser")
        
        # Method 2: Mailto links (original)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split("?")[0].strip().lower()
                if email:
                    found.add(email)
        
        # Method 3: Link text (NEW - for Gmail/Outlook links)
        for a in soup.find_all("a"):
            link_text = a.get_text(strip=True)
            if link_text and "@" in link_text:
                link_emails = EMAIL_REGEX.findall(link_text)
                for e in link_emails:
                    found.add(e.lower())
        
        # Method 4: Form actions (original)
        for form in soup.find_all("form", action=True):
            action = form["action"]
            emails_in_action = EMAIL_REGEX.findall(action)
            for e in emails_in_action:
                found.add(e.lower())
        
        # Method 5: Input fields (original)
        for inp in soup.find_all("input"):
            if inp.get("value"):
                emails_in_value = EMAIL_REGEX.findall(inp["value"])
                for e in emails_in_value:
                    found.add(e.lower())
            if inp.get("placeholder"):
                emails_in_placeholder = EMAIL_REGEX.findall(inp["placeholder"])
                for e in emails_in_placeholder:
                    found.add(e.lower())
        
        # Method 6: Meta tags (original)
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            emails_in_meta = EMAIL_REGEX.findall(content)
            for e in emails_in_meta:
                found.add(e.lower())
        
        # Method 7: Script tags (original)
        for script in soup.find_all("script"):
            if script.string:
                emails_in_js = EMAIL_REGEX.findall(script.string)
                for e in emails_in_js:
                    found.add(e.lower())
        
        # Method 8: Comments (original)
        for comment in soup.find_all(string=lambda text: isinstance(text, str)):
            emails_in_comment = EMAIL_REGEX.findall(str(comment))
            for e in emails_in_comment:
                found.add(e.lower())
                
    except Exception:
        pass
    
    return found

# -----------------------
# Crawling Function (Original logic + improvements)
# -----------------------
def crawl_site(url: str, crawl_depth: int = 1, max_pages: int = 30, delay: float = 0.2) -> dict:
    """
    Crawl site with original logic but better error handling
    """
    result = {
        'url': url,
        'status': 'error',
        'raw_emails': set(),
        'clean_emails': set(),
        'pages_crawled': 0,
        'error_message': None
    }
    
    try:
        parsed = urlparse(url)
        base_domain = parsed.netloc
        
        if not base_domain:
            result['error_message'] = "Invalid URL format"
            return result
        
        # Original crawling logic
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
            except Exception as e:
                result['error_message'] = f"Request failed: {str(e)[:50]}"
                continue

            # Extract emails
            found.update(extract_emails_from_html(html))

            # Continue crawling if within depth (original logic)
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
        
        # Filter with original logic
        cleaned = {e for e in found if not looks_like_garbage(e)}
        cleaned = {e for e in cleaned if not any(k in e for k in EXCLUDED_KEYWORDS)}
        
        result['status'] = 'success' if cleaned else 'no_emails'
        result['raw_emails'] = found
        result['clean_emails'] = cleaned
        result['pages_crawled'] = pages
        
        if not cleaned and not result['error_message']:
            result['error_message'] = "No valid emails found"
            
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

# -----------------------
# Streamlit UI (Original style)
# -----------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor</h1>
  <p style="color:#333; font-size:14px;">Paste website URLs (one per line). Supports URL shorteners (t.co, bit.ly, etc.). Optimized for 1000+ URLs.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=350, 
                                   placeholder="https://example.com\nhttps://t.co/xyz123\nhttps://bit.ly/abc456")
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 2, 1)
        max_pages = st.number_input("Max pages per site", 1, 200, 30)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)
        
        st.markdown("### ⚙️ Performance")
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
            st.rerun()

if extract_button:
    # Reset
    st.session_state.results_ready = False
    st.session_state.all_results = {}
    st.session_state.unique_emails = set()
    
    # Normalize and resolve URLs (ORIGINAL LOGIC)
    websites = []
    raw_urls = [line.strip() for line in urls_input.splitlines() if line.strip()]
    
    if not raw_urls:
        st.warning("⚠️ Please enter at least one URL.")
    else:
        # Step 1: Normalize
        st.info("🔗 Step 1: Normalizing URLs...")
        normalized = []
        for line in raw_urls:
            n = normalize_url(line)
            if n:
                normalized.append(n)
        
        # Step 2: Resolve shortened URLs (IMPORTANT!)
        st.info(f"🔗 Step 2: Resolving {len(normalized)} URLs (handling t.co, bit.ly, etc.)...")
        resolve_progress = st.progress(0)
        
        for i, url in enumerate(normalized):
            resolved = resolve_url(url)
            websites.append(resolved)
            resolve_progress.progress((i + 1) / len(normalized))
        
        resolve_progress.empty()
        
        total_sites = len(websites)
        st.success(f"✅ URLs resolved! Starting extraction from {total_sites} websites...")
        
        # Progress tracking
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        metrics_col1, metrics_col2, metrics_col3, metrics_col4 = st.columns(4)
        
        all_results = {}
        unique_emails = set()
        total_pages = 0
        
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
                        
                        all_results[url] = {
                            "status": result['status'],
                            "raw": sorted(result['raw_emails']),
                            "clean": sorted(result['clean_emails']),
                            "pages": result['pages_crawled'],
                            "error": result.get('error_message')
                        }
                        
                        unique_emails.update(result['clean_emails'])
                        total_pages += result['pages_crawled']
                        
                    except Exception as e:
                        all_results[url] = {
                            "status": "error",
                            "raw": [],
                            "clean": [],
                            "pages": 0,
                            "error": str(e)[:100]
                        }
                    
                    completed += 1
                    progress = completed / total_sites
                    progress_bar.progress(progress)
                    
                    # Real-time metrics
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = (total_sites - completed) / rate if rate > 0 else 0
                    
                    metrics_col1.metric("✅ Completed", f"{completed}/{total_sites}")
                    metrics_col2.metric("📧 Emails", len(unique_emails))
                    metrics_col3.metric("📄 Pages", total_pages)
                    metrics_col4.metric("⚡ Speed", f"{rate:.1f}/s")
                    
                    status_text.markdown(f"⏱️ **ETA**: ~{int(eta)}s | **Emails Found**: {len(unique_emails)}")
        
        # Completion
        progress_bar.progress(1.0)
        elapsed_total = time.time() - start_time
        
        # Save to session
        st.session_state.all_results = all_results
        st.session_state.unique_emails = unique_emails
        st.session_state.results_ready = True
        
        st.balloons()
        st.success(f"🎉 **Completed in {elapsed_total:.1f}s!** Found **{len(unique_emails)}** unique emails from **{total_pages}** pages!")

# -----------------------
# Display Results (Original style)
# -----------------------
if st.session_state.results_ready:
    all_results = st.session_state.all_results
    unique_emails = st.session_state.unique_emails
    
    st.markdown("---")
    
    # Summary
    col1, col2, col3 = st.columns(3)
    col1.metric("🌐 Websites", len(all_results))
    col2.metric("📧 Unique Emails", len(unique_emails))
    col3.metric("📄 Total Pages", sum(r['pages'] for r in all_results.values()))
    
    # Quick Actions
    st.markdown("### 🎯 Download Results")
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
            # CSV with original format
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email", "status"])
            for site, data in all_results.items():
                for e in data["clean"]:
                    writer.writerow([site, e, data["status"]])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")
            
            st.download_button(
                "📥 Download CSV",
                data=csv_bytes,
                file_name="emails.csv",
                mime="text/csv",
                use_container_width=True
            )
    
    with col3:
        if unique_emails:
            st.text_area("📋 Copy All Emails", value=emails_text, height=100)
    
    # Detailed Results (Original style)
    st.markdown("---")
    st.subheader("📋 Extracted Emails per Website")
    
    for site, data in all_results.items():
        status_icon = "✅" if data["status"] == "success" else "⚠️" if data["status"] == "no_emails" else "❌"
        with st.expander(f"{status_icon} {site} ({len(data['clean'])} emails, {data['pages']} pages)"):
            
            # Raw emails
            st.markdown("**Raw Emails Found:**")
            if data["raw"]:
                df_raw = pd.DataFrame({"Email": data["raw"]})
                rows = max(1, len(df_raw))
                height = max(180, min(500, 32 * rows))
                st.dataframe(df_raw, height=height)
            else:
                st.markdown("→ No raw emails found.")

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

            # Cleaned emails
            st.markdown("**Filtered Emails (cleaned):**")
            if data["clean"]:
                df_clean = pd.DataFrame({"Email": data["clean"]})
                rows = max(1, len(df_clean))
                height = max(180, min(600, 32 * rows))
                st.dataframe(df_clean, height=height)
            else:
                st.markdown(f"→ No filtered emails. {data.get('error', '')}")

            st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    
    st.info("💡 Done by Shafiq Sanchy")

# Footer (Original)
st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
