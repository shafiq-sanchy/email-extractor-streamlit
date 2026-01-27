"""
Email Extractor - ULTIMATE VERSION (Maximum Email Extraction)

CRITICAL FIXES:
1. Restored GLOBAL session (old code advantage)
2. Added advanced email extraction from:
   - JavaScript variables (window.email, etc.)
   - JSON-LD structured data
   - Obfuscated emails (dot, at replacements)
   - Data attributes and hidden fields
   - Contact forms and WordPress plugins
3. Added URL resolution back for redirects
4. Optimized for both speed AND accuracy
5. Deep HTML parsing for embedded content
"""

import re
import io
import csv
import time
import json
import gc
from urllib.parse import urljoin, urlparse
from html import unescape

import requests
from bs4 import BeautifulSoup
import streamlit as st
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------
# Configuration
# -----------------------
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$', re.I)

EXCLUDED_KEYWORDS = ["support@", "account", "filter", "team", "hr", "enquiries", "press@", "job", "career", "sales", "inquiry", "yourname", "john", "example", "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"]
EXCLUDED_DOMAINS_SUBSTR = ["sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", "amazonaws", "localhost", "invalid", "example", "website", "2x.png"]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf")

# Optimized settings
MAX_CRAWL_WORKERS = 15  # Balanced
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# CRITICAL: Global session like old code (connection pooling + cookie persistence)
session = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------
# Utility Functions
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
    """Quick URL resolution - RESTORED from old code"""
    try:
        resp = session.head(url, allow_redirects=True, headers=HEADERS, timeout=4, verify=False)
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

def decode_obfuscated_email(text: str) -> list:
    """Decode obfuscated emails like 'info [at] example [dot] com'"""
    emails = []
    
    # Pattern: word [at] word [dot] word
    pattern1 = r'([a-zA-Z0-9._%+-]+)\s*\[at\]\s*([a-zA-Z0-9.-]+)\s*\[dot\]\s*([a-zA-Z]{2,})'
    for match in re.finditer(pattern1, text, re.I):
        email = f"{match.group(1)}@{match.group(2)}.{match.group(3)}"
        emails.append(email.lower())
    
    # Pattern: word (at) word (dot) word
    pattern2 = r'([a-zA-Z0-9._%+-]+)\s*\(at\)\s*([a-zA-Z0-9.-]+)\s*\(dot\)\s*([a-zA-Z]{2,})'
    for match in re.finditer(pattern2, text, re.I):
        email = f"{match.group(1)}@{match.group(2)}.{match.group(3)}"
        emails.append(email.lower())
    
    # Pattern: word AT word DOT word
    pattern3 = r'([a-zA-Z0-9._%+-]+)\s+AT\s+([a-zA-Z0-9.-]+)\s+DOT\s+([a-zA-Z]{2,})'
    for match in re.finditer(pattern3, text, re.I):
        email = f"{match.group(1)}@{match.group(2)}.{match.group(3)}"
        emails.append(email.lower())
    
    return emails

def extract_emails_from_html(html: str) -> set:
    """ADVANCED email extraction - finds hidden/embedded emails"""
    found = set()
    if not html:
        return found
    
    # Unescape HTML entities first
    try:
        html = unescape(html)
    except:
        pass
    
    # 1. Direct regex extraction from HTML text
    try:
        for email in EMAIL_REGEX.findall(html):
            found.add(email.lower())
    except Exception:
        pass
    
    # 2. Obfuscated emails
    try:
        for email in decode_obfuscated_email(html):
            found.add(email)
    except Exception:
        pass
    
    # 3. Parse with BeautifulSoup
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # 3a. Mailto links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split("?")[0].split("#")[0].strip().lower()
                if email:
                    found.add(email)
        
        # 3b. Data attributes (common in modern websites)
        for attr in ['data-email', 'data-mail', 'data-contact', 'data-user-email', 'data-mailto']:
            for elem in soup.find_all(attrs={attr: True}):
                potential = elem.get(attr, '').strip().lower()
                if potential and EMAIL_REGEX.fullmatch(potential):
                    found.add(potential)
        
        # 3c. Hidden input fields (contact forms)
        for input_field in soup.find_all("input", attrs={"type": ["email", "hidden"]}):
            value = input_field.get("value", "").strip().lower()
            placeholder = input_field.get("placeholder", "").strip().lower()
            if value and EMAIL_REGEX.fullmatch(value):
                found.add(value)
            if placeholder and EMAIL_REGEX.fullmatch(placeholder):
                found.add(placeholder)
        
        # 3d. Meta tags
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            if content:
                for email in EMAIL_REGEX.findall(content):
                    found.add(email.lower())
        
        # 3e. Script tags - check for JavaScript variables
        for script in soup.find_all("script"):
            script_text = script.string
            if script_text:
                # Look for common JS patterns
                # var email = "..."
                # window.email = "..."
                # config.email = "..."
                for email in EMAIL_REGEX.findall(script_text):
                    found.add(email.lower())
                
                # Check for JSON-LD structured data
                if script.get("type") == "application/ld+json":
                    try:
                        data = json.loads(script_text)
                        # Recursively search for email in JSON
                        def find_emails_in_json(obj):
                            if isinstance(obj, dict):
                                for key, value in obj.items():
                                    if isinstance(value, str) and EMAIL_REGEX.fullmatch(value):
                                        found.add(value.lower())
                                    else:
                                        find_emails_in_json(value)
                            elif isinstance(obj, list):
                                for item in obj:
                                    find_emails_in_json(item)
                        
                        find_emails_in_json(data)
                    except:
                        pass
        
        # 3f. Check all text nodes for obfuscated patterns
        for text in soup.stripped_strings:
            for email in decode_obfuscated_email(text):
                found.add(email)
        
        # 3g. WordPress/plugin specific - check for common class names
        for elem in soup.find_all(class_=re.compile(r'(email|contact|mail)', re.I)):
            text = elem.get_text()
            for email in EMAIL_REGEX.findall(text):
                found.add(email.lower())
    
    except Exception:
        pass
    
    return found

# -----------------------
# Crawling Function - RESTORED from old code structure
# -----------------------
def crawl_site(url: str, crawl_depth: int = 2, max_pages: int = 50, delay: float = 0.15) -> tuple:
    """Crawl website and extract emails - Using GLOBAL session like old code"""
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
            # Using GLOBAL session (old code advantage)
            r = session.get(current, headers=HEADERS, timeout=8, verify=False)
            html = r.text
        except Exception:
            continue

        # Extract emails with advanced method
        found.update(extract_emails_from_html(html))

        # Continue crawling if depth allows
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
st.set_page_config(page_title="Email Extractor Pro", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor Pro</h1>
  <p style="color:#333; font-size:14px;">Advanced extraction • Hidden emails • Contact forms • Unlimited URLs</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([2.5, 1.5])
    with col1:
        urls_input = st.text_area(
            "📝 Enter website URLs (one per line)", 
            height=280, 
            placeholder="https://example.com\nhttps://another-site.com\n\n✅ Extracts from contact forms, hidden fields, JS variables"
        )
        # Count URLs
        url_lines = [line.strip() for line in urls_input.splitlines() if line.strip()]
        url_count = len([normalize_url(line) for line in url_lines if normalize_url(line)])
        if url_count > 0:
            color = "#e3f2fd" if url_count <= 100 else "#fff3cd" if url_count <= 500 else "#f8d7da"
            st.markdown(
                f"<div style='background:{color}; padding:8px; border-radius:5px; margin-top:8px;'>"
                f"<b>📊 {url_count} URL(s) ready to process</b></div>", 
                unsafe_allow_html=True
            )
    
    with col2:
        st.markdown("### ⚙️ Settings")
        crawl_depth = st.slider("🔍 Crawl depth", 0, 3, 2, help="0 = homepage only, 3 = deep crawl")
        max_pages = st.number_input("📄 Max pages per site", 10, 200, 50, step=10)
        delay = st.number_input("⏱️ Delay (seconds)", 0.0, 2.0, 0.15, 0.05, help="Delay between requests")
        batch_size = st.number_input("📦 Batch size", 10, 50, 25, step=5, help="Process URLs in batches")

st.markdown("---")

# Extract button
extract_button = st.button("🚀 Extract Emails", use_container_width=False, type="primary")

if extract_button:
    # Normalize and resolve URLs (RESTORED from old code)
    st.info("⏳ Preparing URLs...")
    websites = []
    
    # Quick resolution for small batches, skip for large
    raw_urls = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            raw_urls.append(n)
    
    if len(raw_urls) <= 100:
        # Resolve URLs for better results (old code behavior)
        for url in raw_urls:
            websites.append(resolve_url(url))
    else:
        # Skip resolution for large batches to save time
        websites = raw_urls

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
        failed = 0
        activity_messages = []
        
        # Process in batches
        total_batches = (len(websites) + batch_size - 1) // batch_size
        start_time = time.time()
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(websites))
            batch_websites = websites[start_idx:end_idx]
            
            # Crawl batch in parallel
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {
                    executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url 
                    for url in batch_websites
                }
                
                for fut in as_completed(futures, timeout=600):
                    url = futures[fut]
                    try:
                        _, raw_emails = fut.result(timeout=120)
                        
                        # Filter garbage
                        cleaned = {e for e in raw_emails if not looks_like_garbage(e)}
                        cleaned = {e for e in cleaned if not any(k in e for k in EXCLUDED_KEYWORDS)}
                        
                        # Store results
                        if cleaned:
                            all_results[url] = {
                                "raw": sorted(raw_emails),
                                "clean": sorted(cleaned)
                            }
                        unique_emails.update(cleaned)
                        
                        completed += 1
                        
                        # Update UI every 3 URLs or last URL
                        if completed % 3 == 0 or completed == len(websites):
                            progress = completed / len(websites)
                            progress_bar.progress(min(progress, 1.0))
                            
                            elapsed = time.time() - start_time
                            rate = completed / elapsed if elapsed > 0 else 0
                            eta = (len(websites) - completed) / rate if rate > 0 else 0
                            
                            # Update status
                            status_placeholder.markdown(f"""
                            <div style='background:#f5f5f5; padding:12px; border-radius:8px; border-left:4px solid #2196F3;'>
                            <b>Progress:</b> {completed}/{len(websites)} ({failed} failed) | 
                            <b>Found:</b> {len(unique_emails)} unique emails | 
                            <b>Batch:</b> {batch_num + 1}/{total_batches} | 
                            <b>Speed:</b> {rate:.1f} sites/sec | 
                            <b>ETA:</b> {int(eta)}s
                            </div>
                            """, unsafe_allow_html=True)
                        
                        # Update activity log
                        if len(cleaned) > 0:
                            activity_messages.append(f"✅ {url[:50]}... → {len(cleaned)} emails")
                        if len(activity_messages) > 12:
                            activity_messages.pop(0)
                        activity_log.markdown("\n".join([f"- {msg}" for msg in activity_messages[-12:]]))
                        
                    except TimeoutError:
                        failed += 1
                        completed += 1
                        activity_messages.append(f"⏱️ {url[:50]}... → Timeout")
                        if len(activity_messages) > 12:
                            activity_messages.pop(0)
                        activity_log.markdown("\n".join([f"- {msg}" for msg in activity_messages[-12:]]))
                    except Exception as e:
                        failed += 1
                        completed += 1
                        activity_messages.append(f"❌ {url[:50]}... → Error")
                        if len(activity_messages) > 12:
                            activity_messages.pop(0)
                        activity_log.markdown("\n".join([f"- {msg}" for msg in activity_messages[-12:]]))
            
            # Clean up memory after each batch
            gc.collect()

        progress_bar.progress(1.0)
        total_time = time.time() - start_time
        st.success(f"✅ Extraction completed in {int(total_time)}s! Found {len(unique_emails)} unique emails from {len(all_results)} websites ({failed} failed)")
        
        st.markdown("---")
        
        # Direct email display
        st.subheader("📧 Extracted Emails")
        
        if unique_emails:
            col_main, col_side = st.columns([2.5, 1.5])
            
            with col_main:
                emails_text = "\n".join(sorted(unique_emails))
                st.text_area(
                    "✅ All emails found (copy from here):", 
                    emails_text, 
                    height=350, 
                    help="Select all (Ctrl+A / Cmd+A) and copy (Ctrl+C / Cmd+C)"
                )
            
            with col_side:
                # Stats card
                st.markdown(f"""
                <div style='background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                            padding:20px; border-radius:10px; color:white; margin-bottom:15px;'>
                    <h3 style='margin:0; color:white;'>📊 Results</h3>
                    <hr style='margin:10px 0; border-color:rgba(255,255,255,0.3);'>
                    <p style='margin:5px 0; font-size:16px;'>
                        <b>🌐 Processed:</b> {completed}<br>
                        <b>✅ Success:</b> {len(all_results)}<br>
                        <b>❌ Failed:</b> {failed}<br>
                        <b>📨 Raw:</b> {sum(len(data['raw']) for data in all_results.values())}<br>
                        <b>✨ Clean:</b> {len(unique_emails)}<br>
                        <b>⏱️ Time:</b> {int(total_time)}s
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
                st.download_button(
                    "📥 CSV File", 
                    data=csv_bytes, 
                    file_name="emails.csv", 
                    mime="text/csv", 
                    use_container_width=True
                )
                
                # TXT
                txt_bytes = emails_text.encode("utf-8")
                st.download_button(
                    "📄 TXT File", 
                    data=txt_bytes, 
                    file_name="emails.txt", 
                    mime="text/plain", 
                    use_container_width=True
                )
        else:
            st.info("No emails found from the provided websites.")
        
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        
        # Detailed results
        if len(all_results) <= 100:
            with st.expander("🔍 Detailed Results by Website", expanded=False):
                for site, data in all_results.items():
                    st.markdown(f"**🌐 {site}**")
                    raw = data["raw"]
                    clean = data["clean"]
                    
                    col_raw, col_clean = st.columns(2)
                    
                    with col_raw:
                        st.markdown(f"*Raw: {len(raw)} emails*")
                        if raw:
                            display_raw = raw[:15]
                            st.code("\n".join(display_raw), language=None)
                            if len(raw) > 15:
                                st.markdown(f"*...and {len(raw) - 15} more*")
                    
                    with col_clean:
                        st.markdown(f"*Clean: {len(clean)} emails*")
                        if clean:
                            display_clean = clean[:15]
                            st.code("\n".join(display_clean), language=None)
                            if len(clean) > 15:
                                st.markdown(f"*...and {len(clean) - 15} more*")
                    
                    st.markdown("---")
        else:
            st.info(f"💡 Detailed view disabled for {len(all_results)} websites. Download CSV for full results.")
        
        # Success notification
        st.balloons()

# Footer
st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
📧 Email Extractor Pro • Extracts Hidden Emails • Contact Forms • JS Variables • © Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
