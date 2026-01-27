"""
Email Extractor (Ultra Optimized)

Features:
- Parallel crawling of unlimited websites
- Fast email extraction (text + mailto)
- Strong garbage filtering
- Real-time progress tracking
- URL counter
- Optimized for large-scale processing
- Memory efficient batching
"""

import re
import io
import csv
import time
from urllib.parse import urljoin, urlparse
from collections import defaultdict

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

# Exclude patterns and domains
EXCLUDED_KEYWORDS = [
    "support@", "account", "filter", "team", "hr", "enquiries", "press@", 
    "job", "career", "sales", "inquiry", "yourname", "john", "example", 
    "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"
]

EXCLUDED_DOMAINS_SUBSTR = [
    "sentry", "wixpress", "latofonts", "address", "yourdomain", 
    "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", 
    "amazonaws", "localhost", "invalid", "example", "website"
]

SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", 
    ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf"
)

# Optimized concurrency settings
MAX_CRAWL_WORKERS = 20  # Increased for faster processing
BATCH_SIZE = 50  # Process URLs in batches to avoid memory issues
REQUEST_TIMEOUT = 8  # Reduced timeout for faster failures

HEADERS = {"User-Agent": "EmailExtractor/2.0 (+https://example.com)"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Optimized session with connection pooling
session = requests.Session()
retries = Retry(
    total=2, 
    backoff_factor=0.1,  # Faster retry
    status_forcelist=[500, 502, 503, 504]
)
adapter = HTTPAdapter(
    max_retries=retries, 
    pool_connections=100,  # Increased pool
    pool_maxsize=100
)
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

def quick_url_check(url: str) -> str:
    """Quick URL validation without full resolution - much faster"""
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return url
        return url
    except Exception:
        return url

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)

def looks_like_garbage(email: str) -> bool:
    """Fast garbage detection"""
    if not email or " " in email:
        return True
    
    e = email.strip().lower()
    
    if EMAIL_REGEX.fullmatch(e) is None:
        return True
    
    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True
    
    # Quick checks first (most common)
    if any(domain.endswith(ext.lstrip(".")) for ext in SKIP_EXTENSIONS):
        return True
    
    if HEX_GARBAGE_RE.fullmatch(local):
        return True
    
    # Check excluded substrings
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain:
            return True
    
    # Check keywords
    for kw in EXCLUDED_KEYWORDS:
        if kw in e:
            return True
    
    return False

def extract_emails_from_html(html: str) -> set:
    """Fast email extraction from HTML"""
    found = set()
    if not html:
        return found
    
    # Regex extraction (faster)
    for m in EMAIL_REGEX.findall(html):
        found.add(m.lower())
    
    # Parse mailto links
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
# Optimized Crawling
# -----------------------
def crawl_site(url: str, crawl_depth: int, max_pages: int, delay: float) -> tuple:
    """Optimized crawling with better error handling"""
    try:
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
                r = session.get(
                    current, 
                    headers=HEADERS, 
                    timeout=REQUEST_TIMEOUT, 
                    verify=False,
                    allow_redirects=True
                )
                html = r.text
            except Exception:
                continue
            
            # Extract emails
            found.update(extract_emails_from_html(html))
            
            # Crawl deeper if needed
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
                        if norm not in seen and len(to_visit) < max_pages * 2:
                            seen.add(norm)
                            to_visit.append((norm, depth + 1))
                except Exception:
                    pass
            
            # Small delay to avoid overwhelming servers
            if delay > 0:
                time.sleep(delay)
        
        return url, found, None
    
    except Exception as e:
        return url, set(), str(e)

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor - Optimized", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor (Optimized)</h1>
  <p style="color:#333; font-size:14px;">Fast bulk email extraction from unlimited websites. Optimized for large-scale processing.</p>
</div>
""", unsafe_allow_html=True)

# Input section
with st.container():
    col1, col2 = st.columns([3, 1])
    
    with col1:
        urls_input = st.text_area(
            "Enter website URLs (one per line)", 
            height=350,
            help="Paste URLs here. Supports unlimited URLs with batch processing."
        )
        
        # URL Counter
        if urls_input:
            valid_urls = [normalize_url(line) for line in urls_input.splitlines() if normalize_url(line)]
            url_count = len(valid_urls)
            st.markdown(f"<p style='color:#666; font-size:13px; margin-top:-10px;'>📊 Total URLs entered: <strong>{url_count}</strong></p>", unsafe_allow_html=True)
        else:
            st.markdown(f"<p style='color:#999; font-size:13px; margin-top:-10px;'>📊 Total URLs entered: <strong>0</strong></p>", unsafe_allow_html=True)
    
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 2, 0)
        max_pages = st.number_input("Max pages per site", 1, 100, 20)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 2.0, 0.1, 0.1)
        
        st.markdown("<small style='color:#666'>Lower delay = faster extraction</small>", unsafe_allow_html=True)

st.markdown("---")

# Extract button
if st.button("🚀 Extract Emails", type="primary"):
    # Parse and normalize URLs
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(quick_url_check(n))
    
    if not websites:
        st.warning("⚠️ Please enter at least one URL.")
    else:
        # Initialize tracking
        total_urls = len(websites)
        all_results = {}
        unique_emails = set()
        processed_count = 0
        error_count = 0
        
        st.info(f"🔄 Starting extraction from **{total_urls}** website(s)...")
        
        # Progress tracking
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Process in batches for memory efficiency
        batch_num = 0
        total_batches = (total_urls + BATCH_SIZE - 1) // BATCH_SIZE
        
        for i in range(0, total_urls, BATCH_SIZE):
            batch_num += 1
            batch = websites[i:i + BATCH_SIZE]
            batch_size = len(batch)
            
            status_text.markdown(f"**📦 Processing batch {batch_num}/{total_batches}** (URLs {i+1} to {i+batch_size})")
            
            # Parallel crawling with progress updates
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {
                    executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url 
                    for url in batch
                }
                
                for fut in as_completed(futures):
                    url, raw_emails, error = fut.result()
                    processed_count += 1
                    
                    # Update progress
                    progress = processed_count / total_urls
                    progress_bar.progress(progress)
                    
                    if error:
                        error_count += 1
                        status_text.markdown(
                            f"**⏳ Processing:** {processed_count}/{total_urls} | "
                            f"**✅ Success:** {processed_count - error_count} | "
                            f"**❌ Errors:** {error_count} | "
                            f"**📧 Emails found:** {len(unique_emails)}"
                        )
                    else:
                        # Filter garbage emails
                        cleaned = {e for e in raw_emails if not looks_like_garbage(e)}
                        
                        all_results[url] = {
                            "raw": sorted(raw_emails),
                            "clean": sorted(cleaned)
                        }
                        unique_emails.update(cleaned)
                        
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
        
        # Results section
        st.subheader("📋 Extraction Results")
        
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
        
        # Detailed results per website
        if all_results:
            with st.expander("🔍 View Detailed Results per Website", expanded=False):
                for site, data in all_results.items():
                    clean = data["clean"]
                    raw = data["raw"]
                    
                    st.markdown(f"### 🌐 {site}")
                    st.markdown(f"**Raw emails found:** {len(raw)} | **Cleaned emails:** {len(clean)}")
                    
                    if clean:
                        df_clean = pd.DataFrame({"Email": clean})
                        st.dataframe(df_clean, use_container_width=True)
                    else:
                        st.markdown("_No valid emails found after filtering._")
                    
                    st.markdown("<div style='height:15px'></div>", unsafe_allow_html=True)
        
        # All unique emails
        if unique_emails:
            st.markdown("---")
            st.subheader("✅ All Unique Emails (Filtered)")
            
            df_all = pd.DataFrame({"Email": sorted(unique_emails)})
            st.dataframe(df_all, use_container_width=True, height=400)
            
            # CSV download
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email"])
            
            for site, data in all_results.items():
                for e in data["clean"]:
                    writer.writerow([site, e])
            
            csv_bytes = csv_buffer.getvalue().encode("utf-8")
            
            st.download_button(
                label="📥 Download All Emails (CSV)",
                data=csv_bytes,
                file_name=f"extracted_emails_{int(time.time())}.csv",
                mime="text/csv",
                type="primary"
            )
            
            # Success notification
            st.balloons()
            st.success(f"🎉 Extraction completed! Found **{len(unique_emails)}** unique valid emails from **{processed_count}** websites.")
        else:
            st.warning("⚠️ No valid emails found after filtering.")
        
        # Browser notification
        if len(unique_emails) > 0:
            js_code = f"""
            <script>
            function notifyMe() {{
                if ("Notification" in window) {{
                    if (Notification.permission === "granted") {{
                        new Notification("Email Extractor", {{
                            body: "Extraction complete! Found {len(unique_emails)} unique emails from {processed_count} websites.",
                            icon: "https://cdn-icons-png.flaticon.com/512/561/561127.png"
                        }});
                    }} else if (Notification.permission !== "denied") {{
                        Notification.requestPermission().then(function(permission) {{
                            if (permission === "granted") {{
                                new Notification("Email Extractor", {{
                                    body: "Extraction complete! Found {len(unique_emails)} unique emails.",
                                    icon: "https://cdn-icons-png.flaticon.com/512/561/561127.png"
                                }});
                            }}
                        }});
                    }}
                }}
            }}
            notifyMe();
            </script>
            """
            import streamlit.components.v1 as components
            components.html(js_code, height=0, width=0)

# Footer
st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
    ⚡ Optimized Email Extractor v2.0 | © Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
