"""
Email Extractor (Ultra Optimized - Enhanced Detection)

Features:
- Parallel crawling of unlimited websites
- Enhanced email extraction with multiple methods
- Better URL resolution (handles t.co, bit.ly, etc.)
- JavaScript content detection
- Strong garbage filtering
- Real-time progress tracking
"""

import re
import io
import csv
import time
from urllib.parse import urljoin, urlparse, unquote

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
# Multiple regex patterns for better email detection
EMAIL_PATTERNS = [
    # Standard email pattern
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    # Email with spaces (sometimes in HTML)
    re.compile(r'\b[A-Za-z0-9._%+-]+\s*@\s*[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    # Email in text format
    re.compile(r'\b[A-Za-z0-9._%+-]+\s*\[\s*at\s*\]\s*[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', re.I),
    # Email with [dot]
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\s*\[\s*dot\s*\]\s*[A-Z|a-z]{2,}\b', re.I),
]

# Exclude patterns (reduced to avoid over-filtering)
EXCLUDED_KEYWORDS = [
    "example.com", "example.org", "yourname", "yourdomain", 
    "yoursite", "mysite", "test@", "email@example"
]

EXCLUDED_DOMAINS_SUBSTR = [
    "sentry.io", "wixpress.com", "amazonaws.com", 
    "localhost", "127.0.0.1", "0.0.0.0"
]

SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", 
    ".webp", ".ico", ".bmp", ".pdf", ".css", ".js"
)

# Optimized settings
MAX_CRAWL_WORKERS = 20
BATCH_SIZE = 50
REQUEST_TIMEOUT = 15  # Increased for better success rate
MAX_REDIRECTS = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Optimized session
session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504, 429]
)
adapter = HTTPAdapter(
    max_retries=retries,
    pool_connections=100,
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
    # Remove any whitespace
    url = url.replace(" ", "")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url: str, max_redirects: int = MAX_REDIRECTS) -> str:
    """Resolve shortened URLs (t.co, bit.ly, etc.) to final destination"""
    try:
        # Follow redirects to get final URL
        response = session.head(
            url, 
            allow_redirects=True, 
            headers=HEADERS, 
            timeout=REQUEST_TIMEOUT,
            verify=False
        )
        final_url = response.url
        
        # If head didn't work, try GET
        if not final_url or final_url == url:
            response = session.get(
                url,
                allow_redirects=True,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                verify=False,
                stream=True
            )
            final_url = response.url
        
        return final_url if final_url else url
    except Exception as e:
        # If resolution fails, return original URL
        return url

def normalize_email(email: str) -> str:
    """Normalize email address"""
    email = email.strip().lower()
    # Remove common obfuscations
    email = re.sub(r'\s+', '', email)  # Remove spaces
    email = re.sub(r'\[at\]', '@', email, flags=re.I)
    email = re.sub(r'\[dot\]', '.', email, flags=re.I)
    email = re.sub(r'\(at\)', '@', email, flags=re.I)
    email = re.sub(r'\(dot\)', '.', email, flags=re.I)
    return email

def is_valid_email(email: str) -> bool:
    """Basic email validation"""
    if not email or len(email) < 5:
        return False
    if email.count('@') != 1:
        return False
    
    try:
        local, domain = email.split('@')
        if not local or not domain:
            return False
        if '.' not in domain:
            return False
        if len(local) > 64 or len(domain) > 255:
            return False
        return True
    except:
        return False

def looks_like_garbage(email: str) -> bool:
    """Enhanced garbage detection - less aggressive"""
    if not email or " " in email:
        return True
    
    e = email.strip().lower()
    
    # Basic validation
    if not is_valid_email(e):
        return True
    
    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True
    
    # Check if domain ends with image extension
    for ext in SKIP_EXTENSIONS:
        if domain.endswith(ext.lstrip(".")):
            return True
    
    # Check for very long hex strings (likely system IDs)
    if len(local) > 20 and re.match(r'^[0-9a-f]+$', local, re.I):
        return True
    
    # Check excluded domains
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain:
            return True
    
    # Check excluded keywords (only exact matches now)
    for kw in EXCLUDED_KEYWORDS:
        if kw == e or e.startswith(kw):
            return True
    
    return False

def extract_emails_from_html(html: str, url: str = "") -> set:
    """Enhanced email extraction with multiple methods"""
    found = set()
    if not html:
        return found
    
    # Method 1: Multiple regex patterns
    for pattern in EMAIL_PATTERNS:
        matches = pattern.findall(html)
        for match in matches:
            normalized = normalize_email(match)
            if is_valid_email(normalized):
                found.add(normalized)
    
    # Method 2: Parse mailto links
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Extract from mailto links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split("?")[0].strip()
                normalized = normalize_email(email)
                if is_valid_email(normalized):
                    found.add(normalized)
        
        # Method 3: Look for emails in text content
        all_text = soup.get_text()
        for pattern in EMAIL_PATTERNS:
            matches = pattern.findall(all_text)
            for match in matches:
                normalized = normalize_email(match)
                if is_valid_email(normalized):
                    found.add(normalized)
        
        # Method 4: Check meta tags
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            if "@" in content:
                for pattern in EMAIL_PATTERNS:
                    matches = pattern.findall(content)
                    for match in matches:
                        normalized = normalize_email(match)
                        if is_valid_email(normalized):
                            found.add(normalized)
        
        # Method 5: Check for obfuscated emails in comments
        comments = soup.find_all(string=lambda text: isinstance(text, str) and '@' in text)
        for comment in comments:
            for pattern in EMAIL_PATTERNS:
                matches = pattern.findall(str(comment))
                for match in matches:
                    normalized = normalize_email(match)
                    if is_valid_email(normalized):
                        found.add(normalized)
    
    except Exception as e:
        pass
    
    return found

# -----------------------
# Optimized Crawling
# -----------------------
def crawl_site(url: str, crawl_depth: int, max_pages: int, delay: float) -> tuple:
    """Enhanced crawling with better email detection"""
    try:
        # First resolve the URL if it's shortened
        resolved_url = resolve_url(url)
        
        parsed = urlparse(resolved_url)
        base_domain = parsed.netloc
        to_visit = [(resolved_url, 0)]
        seen = {resolved_url}
        found = set()
        pages = 0
        
        while to_visit and pages < max_pages:
            current, depth = to_visit.pop(0)
            pages += 1
            
            try:
                # Try to get the page
                r = session.get(
                    current,
                    headers=HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    verify=False,
                    allow_redirects=True
                )
                
                # Check if response is HTML
                content_type = r.headers.get('Content-Type', '')
                if 'text/html' not in content_type.lower():
                    continue
                
                html = r.text
                
                # Extract emails with enhanced method
                emails = extract_emails_from_html(html, current)
                found.update(emails)
                
            except Exception as e:
                continue
            
            # Crawl deeper if needed
            if depth < crawl_depth:
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"].strip()
                        
                        # Skip javascript, mailto, tel links
                        if href.startswith(('javascript:', 'mailto:', 'tel:')):
                            continue
                        
                        joined = urljoin(current, href)
                        p = urlparse(joined)
                        
                        if p.scheme not in ("http", "https"):
                            continue
                        if p.netloc != base_domain:
                            continue
                        
                        # Skip files
                        if any(joined.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
                            continue
                        
                        norm = p._replace(fragment="").geturl()
                        if norm not in seen and len(to_visit) < max_pages * 2:
                            seen.add(norm)
                            to_visit.append((norm, depth + 1))
                except Exception:
                    pass
            
            # Small delay
            if delay > 0:
                time.sleep(delay)
        
        return url, found, None
    
    except Exception as e:
        return url, set(), str(e)

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor - Enhanced", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor (Enhanced Detection)</h1>
  <p style="color:#333; font-size:14px;">Fast bulk email extraction with improved detection. Handles shortened URLs and JavaScript content.</p>
</div>
""", unsafe_allow_html=True)

# Input section
with st.container():
    col1, col2 = st.columns([3, 1])
    
    with col1:
        urls_input = st.text_area(
            "Enter website URLs (one per line)",
            height=350,
            help="Paste URLs here. Supports t.co, bit.ly and other shortened URLs."
        )
        
        # URL Counter
        if urls_input:
            valid_urls = [normalize_url(line) for line in urls_input.splitlines() if normalize_url(line)]
            url_count = len(valid_urls)
            st.markdown(f"<p style='color:#666; font-size:13px; margin-top:-10px;'>📊 Total URLs entered: <strong>{url_count}</strong></p>", unsafe_allow_html=True)
        else:
            st.markdown(f"<p style='color:#999; font-size:13px; margin-top:-10px;'>📊 Total URLs entered: <strong>0</strong></p>", unsafe_allow_html=True)
    
    with col2:
        crawl_depth = st.slider("Crawl depth", 0, 3, 1, help="0=homepage only, 1=homepage+links, 2+=deeper crawl")
        max_pages = st.number_input("Max pages per site", 1, 100, 30)
        delay = st.number_input("Delay (seconds)", 0.0, 2.0, 0.15, 0.05)
        
        st.markdown("<small style='color:#666'>Tip: Increase crawl depth to find more emails</small>", unsafe_allow_html=True)

st.markdown("---")

# Extract button
if st.button("🚀 Extract Emails", type="primary"):
    # Parse and normalize URLs
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(n)
    
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
        
        # Process in batches
        batch_num = 0
        total_batches = (total_urls + BATCH_SIZE - 1) // BATCH_SIZE
        
        for i in range(0, total_urls, BATCH_SIZE):
            batch_num += 1
            batch = websites[i:i + BATCH_SIZE]
            batch_size = len(batch)
            
            status_text.markdown(f"**📦 Processing batch {batch_num}/{total_batches}** (URLs {i+1} to {i+batch_size})")
            
            # Parallel crawling
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
                    else:
                        # Filter garbage emails (less aggressive now)
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
        
        # Complete
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
            st.metric("Total URLs", processed_count)
        with col2:
            st.metric("Successful", processed_count - error_count)
        with col3:
            st.metric("Unique Emails", len(unique_emails))
        with col4:
            success_rate = ((processed_count - error_count) / processed_count * 100) if processed_count > 0 else 0
            st.metric("Success Rate", f"{success_rate:.1f}%")
        
        st.markdown("---")
        
        # Detailed results
        if all_results:
            with st.expander("🔍 View Details per Website", expanded=True):
                for site, data in all_results.items():
                    clean = data["clean"]
                    raw = data["raw"]
                    
                    if clean:
                        st.markdown(f"### ✅ {site}")
                        st.markdown(f"Found **{len(clean)}** valid emails (from {len(raw)} raw)")
                        
                        df_clean = pd.DataFrame({"Email": clean})
                        st.dataframe(df_clean, use_container_width=True)
                    else:
                        st.markdown(f"### ⚠️ {site}")
                        st.markdown(f"No valid emails found (raw found: {len(raw)})")
                    
                    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        
        # All unique emails
        if unique_emails:
            st.markdown("---")
            st.subheader("✅ All Unique Emails")
            
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
                file_name=f"emails_{int(time.time())}.csv",
                mime="text/csv",
                type="primary"
            )
            
            st.balloons()
            st.success(f"🎉 Found **{len(unique_emails)}** unique emails from **{processed_count - error_count}** websites!")
        else:
            st.warning("⚠️ No emails found. Try increasing crawl depth or check if websites have contact pages.")
            st.info("💡 Tip: Some websites hide emails on separate 'Contact' or 'About' pages. Increase crawl depth to 2-3.")
        
        # Notification
        if len(unique_emails) > 0:
            js_code = f"""
            <script>
            if ("Notification" in window && Notification.permission === "granted") {{
                new Notification("Email Extractor", {{
                    body: "Found {len(unique_emails)} emails from {processed_count - error_count} websites!",
                    icon: "https://cdn-icons-png.flaticon.com/512/561/561127.png"
                }});
            }}
            </script>
            """
            import streamlit.components.v1 as components
            components.html(js_code, height=0, width=0)

# Footer
st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
    ⚡ Enhanced Email Extractor v2.1 | © Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
