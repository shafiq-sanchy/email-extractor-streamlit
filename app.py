# app.py
"""
Email Extractor - Debug & Fixed Version

Fixed Issues:
- BeautifulSoup parser changed to 'lxml' for better compatibility
- More aggressive email extraction
- Better crawling of contact/about pages
- Debug mode to see what's happening
- Fallback methods for email extraction
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
# More permissive email regex
EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', re.I)

EXCLUDED_KEYWORDS = ["example@", "test@", "sample@", "admin@example", "user@example", "name@example"]
EXCLUDED_DOMAINS_SUBSTR = [
    "example.com", "example.org", "domain.com", "yourdomain", "yoursite", "mysite", "sentry.io", "wixpress", "amazonaws"
]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".pdf", ".js", ".css")

# Performance settings
MAX_CRAWL_WORKERS = 15
BATCH_SIZE = 50
REQUEST_TIMEOUT = 10
MAX_RETRIES = 2

# Browser-like headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none"
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=MAX_RETRIES, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
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

def looks_like_garbage(email: str) -> bool:
    """Minimal filtering - only obvious garbage"""
    if not email or " " in email or len(email) < 6:
        return True
    
    e = email.strip().lower()
    
    # Basic structure check
    if "@" not in e or e.count("@") != 1:
        return True
    
    try:
        local, domain = e.split("@", 1)
    except ValueError:
        return True
    
    # Must have at least one dot in domain
    if "." not in domain:
        return True
    
    # Check for file extensions in domain
    domain_lower = domain.lower()
    if any(domain_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    
    # Check excluded domains
    for sub in EXCLUDED_DOMAINS_SUBSTR:
        if sub in domain_lower:
            return True
    
    # Check excluded keywords
    for kw in EXCLUDED_KEYWORDS:
        if kw in e:
            return True
    
    return False

def extract_emails_from_text(text: str) -> set:
    """Extract all emails from raw text using regex"""
    if not text:
        return set()
    
    found = set()
    matches = EMAIL_REGEX.findall(text)
    
    for match in matches:
        email = match.strip().lower()
        if email and not looks_like_garbage(email):
            found.add(email)
    
    return found

def extract_emails_from_html(html: str, url: str = "") -> dict:
    """
    Extract emails using multiple methods
    Returns dict with method names and emails found
    """
    results = {
        'text_regex': set(),
        'mailto_links': set(),
        'visible_text': set(),
        'meta_tags': set(),
        'all_attributes': set()
    }
    
    if not html:
        return results
    
    # Method 1: Direct regex on HTML source
    results['text_regex'] = extract_emails_from_text(html)
    
    try:
        # Try lxml parser first (faster and better), fallback to html.parser
        try:
            soup = BeautifulSoup(html, "lxml")
        except:
            soup = BeautifulSoup(html, "html.parser")
        
        # Method 2: Mailto links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "mailto:" in href.lower():
                email = href.lower().replace("mailto:", "").split("?")[0].strip()
                if email and not looks_like_garbage(email):
                    results['mailto_links'].add(email)
        
        # Method 3: All visible text
        visible_text = soup.get_text(separator=" ", strip=True)
        results['visible_text'] = extract_emails_from_text(visible_text)
        
        # Method 4: Meta tags
        for meta in soup.find_all("meta"):
            content = meta.get("content", "")
            if content:
                results['meta_tags'].update(extract_emails_from_text(content))
        
        # Method 5: All tag attributes (href, value, placeholder, data-*, etc)
        for tag in soup.find_all(True):
            for attr_name, attr_value in tag.attrs.items():
                if isinstance(attr_value, str):
                    results['all_attributes'].update(extract_emails_from_text(attr_value))
                elif isinstance(attr_value, list):
                    for val in attr_value:
                        if isinstance(val, str):
                            results['all_attributes'].update(extract_emails_from_text(val))
    
    except Exception as e:
        # If parsing fails, at least we have regex results
        pass
    
    return results

def should_crawl_url(url: str, base_url: str) -> bool:
    """Determine if URL should be crawled - prioritize contact/about pages"""
    url_lower = url.lower()
    
    # Priority pages that likely have emails
    priority_keywords = ['contact', 'about', 'team', 'people', 'staff', 'reach', 'touch']
    
    for keyword in priority_keywords:
        if keyword in url_lower:
            return True
    
    return True

# -----------------------
# Crawling Function
# -----------------------
def crawl_site(url: str, crawl_depth: int = 2, max_pages: int = 50, delay: float = 0.15) -> dict:
    """Crawl website and extract emails"""
    result = {
        'url': url,
        'status': 'error',
        'emails': set(),
        'pages_crawled': 0,
        'pages_found': [],
        'error_message': None,
        'extraction_methods': {}
    }
    
    try:
        parsed = urlparse(url)
        base_domain = parsed.netloc
        
        if not base_domain:
            result['error_message'] = "Invalid URL"
            return result
        
        # Prioritize certain pages
        to_visit = [(url, 0)]
        seen = set([url])
        
        # Try to add common contact pages
        potential_pages = [
            url.rstrip('/') + '/contact',
            url.rstrip('/') + '/contact-us',
            url.rstrip('/') + '/about',
            url.rstrip('/') + '/about-us',
            url.rstrip('/') + '/team',
            url.rstrip('/') + '/reach-us'
        ]
        
        for page in potential_pages:
            if page not in seen:
                to_visit.append((page, 0))
                seen.add(page)
        
        found_emails = set()
        pages_crawled = 0
        pages_list = []
        
        while to_visit and pages_crawled < max_pages:
            current, depth = to_visit.pop(0)
            
            try:
                r = session.get(current, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=False, allow_redirects=True)
                
                if r.status_code != 200:
                    continue
                
                html = r.text
                pages_crawled += 1
                pages_list.append(current)
                
                # Extract emails using all methods
                extraction = extract_emails_from_html(html, current)
                
                # Combine all methods
                page_emails = set()
                for method, emails in extraction.items():
                    page_emails.update(emails)
                    if emails:
                        if method not in result['extraction_methods']:
                            result['extraction_methods'][method] = 0
                        result['extraction_methods'][method] += len(emails)
                
                found_emails.update(page_emails)
                
                # Continue crawling if within depth
                if depth < crawl_depth:
                    try:
                        soup = BeautifulSoup(html, "lxml") if "lxml" else BeautifulSoup(html, "html.parser")
                        
                        for a in soup.find_all("a", href=True):
                            href = a["href"].strip()
                            
                            if href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                                continue
                            
                            try:
                                joined = urljoin(current, href)
                                p = urlparse(joined)
                                
                                if p.scheme not in ("http", "https"):
                                    continue
                                if p.netloc != base_domain:
                                    continue
                                
                                # Remove query params and fragments for uniqueness
                                norm = f"{p.scheme}://{p.netloc}{p.path}".rstrip('/')
                                
                                if norm and norm not in seen and len(seen) < max_pages * 2:
                                    seen.add(norm)
                                    # Prioritize contact pages
                                    if should_crawl_url(norm, url):
                                        to_visit.insert(0, (norm, depth + 1))
                                    else:
                                        to_visit.append((norm, depth + 1))
                            except:
                                continue
                    except:
                        pass
                
                if delay > 0:
                    time.sleep(delay)
                    
            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException:
                continue
            except Exception:
                continue
        
        result['status'] = 'success' if found_emails else 'no_emails'
        result['emails'] = found_emails
        result['pages_crawled'] = pages_crawled
        result['pages_found'] = pages_list[:10]  # Keep first 10 for display
        
        if not found_emails:
            result['error_message'] = f"No emails found (crawled {pages_crawled} pages)"
            
    except Exception as e:
        result['status'] = 'error'
        result['error_message'] = str(e)[:150]
    
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
if 'debug_mode' not in st.session_state:
    st.session_state.debug_mode = False

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Email Extractor Pro", layout="wide")

st.markdown("""
<div style="margin-bottom:12px;">
  <h1 style="color:#1F2328;">📧 Email Extractor Pro (Fixed)</h1>
  <p style="color:#333; font-size:14px;">Fixed email extraction with better parsing and multiple detection methods.</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=350, placeholder="https://example.com\nhttps://another-site.com")
    with col2:
        crawl_depth = st.slider("Crawl depth", 0, 3, 2, help="Higher = more pages crawled")
        max_pages = st.number_input("Max pages per site", 5, 200, 50)
        delay = st.number_input("Delay (seconds)", 0.0, 2.0, 0.15, 0.05)
        debug_mode = st.checkbox("Debug mode", value=False, help="Show detailed extraction info")
        
        st.markdown("### ⚙️ Settings")
        st.markdown(f"- Workers: {MAX_CRAWL_WORKERS}")
        st.markdown(f"- Batch: {BATCH_SIZE}")
        st.markdown(f"- Timeout: {REQUEST_TIMEOUT}s")

st.markdown("---")

col1, col2 = st.columns([1, 5])
with col1:
    extract_btn = st.button("🚀 Extract", use_container_width=True)
with col2:
    if st.session_state.results_ready:
        if st.button("🔄 Clear", use_container_width=True):
            st.session_state.results_ready = False
            st.session_state.all_results = {}
            st.session_state.unique_emails = set()
            st.rerun()

if extract_btn:
    st.session_state.results_ready = False
    st.session_state.all_results = {}
    st.session_state.unique_emails = set()
    st.session_state.debug_mode = debug_mode
    
    websites = [normalize_url(line) for line in urls_input.splitlines() if normalize_url(line)]
    
    if not websites:
        st.warning("⚠️ Please enter at least one URL")
    else:
        total = len(websites)
        st.info(f"🎯 Processing {total} websites...")
        
        progress = st.progress(0)
        status = st.empty()
        
        metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
        
        all_results = {}
        unique_emails = set()
        completed = 0
        start = time.time()
        
        # Process in batches
        for batch_start in range(0, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            batch = websites[batch_start:batch_end]
            
            status.markdown(f"⚙️ Batch {batch_start//BATCH_SIZE + 1}/{(total+BATCH_SIZE-1)//BATCH_SIZE}")
            
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in batch}
                
                for fut in as_completed(futures):
                    url = futures[fut]
                    try:
                        result = fut.result()
                        
                        all_results[url] = {
                            "status": result['status'],
                            "emails": sorted(result['emails']),
                            "pages": result['pages_crawled'],
                            "pages_list": result['pages_found'],
                            "error": result.get('error_message'),
                            "methods": result.get('extraction_methods', {})
                        }
                        
                        unique_emails.update(result['emails'])
                        
                    except Exception as e:
                        all_results[url] = {
                            "status": "error",
                            "emails": [],
                            "pages": 0,
                            "pages_list": [],
                            "error": str(e)[:100],
                            "methods": {}
                        }
                    
                    completed += 1
                    progress.progress(completed / total)
                    
                    metrics_col1.metric("✅ Done", f"{completed}/{total}")
                    metrics_col2.metric("📧 Emails", len(unique_emails))
                    metrics_col3.metric("⚡ Speed", f"{completed/(time.time()-start):.1f}/s")
        
        progress.progress(1.0)
        elapsed = time.time() - start
        
        st.session_state.all_results = all_results
        st.session_state.unique_emails = unique_emails
        st.session_state.results_ready = True
        
        st.balloons()
        st.success(f"✅ Done in {elapsed:.1f}s! Found {len(unique_emails)} emails")

# -----------------------
# Display Results
# -----------------------
if st.session_state.results_ready:
    all_results = st.session_state.all_results
    unique_emails = st.session_state.unique_emails
    debug_mode = st.session_state.debug_mode
    
    st.markdown("---")
    
    # Summary
    col1, col2, col3 = st.columns(3)
    col1.metric("🌐 Sites", len(all_results))
    col2.metric("📧 Unique Emails", len(unique_emails))
    col3.metric("📄 Total Pages", sum(r['pages'] for r in all_results.values()))
    
    # Download options
    st.markdown("### 📥 Download Results")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if unique_emails:
            txt = "\n".join(sorted(unique_emails))
            st.download_button("📄 TXT File", txt, "emails.txt", "text/plain", use_container_width=True)
    
    with col2:
        if unique_emails:
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(["website", "email", "status", "pages"])
            for site, data in all_results.items():
                for email in data["emails"]:
                    writer.writerow([site, email, data["status"], data["pages"]])
            
            st.download_button("📊 CSV File", csv_buf.getvalue(), "emails.csv", "text/csv", use_container_width=True)
    
    with col3:
        if unique_emails:
            st.text_area("📋 Copy Emails", "\n".join(sorted(unique_emails)), height=100)
    
    # Detailed results
    st.markdown("---")
    st.subheader("📊 Detailed Results")
    
    tab1, tab2, tab3 = st.tabs(["✅ All Emails", "🌐 By Website", "🔍 Debug Info" if debug_mode else "ℹ️ Info"])
    
    with tab1:
        if unique_emails:
            df = pd.DataFrame({"Email": sorted(unique_emails)})
            st.dataframe(df, height=400, use_container_width=True)
        else:
            st.warning("⚠️ No emails found")
    
    with tab2:
        success_count = sum(1 for r in all_results.values() if r['emails'])
        st.info(f"📊 {success_count}/{len(all_results)} sites had emails")
        
        for site, data in all_results.items():
            icon = "✅" if data['emails'] else "⚠️"
            with st.expander(f"{icon} {site} ({len(data['emails'])} emails, {data['pages']} pages)"):
                if data['emails']:
                    st.write("**Emails found:**")
                    for email in data['emails']:
                        st.code(email)
                    
                    if debug_mode and data['methods']:
                        st.write("**Extraction methods:**")
                        for method, count in data['methods'].items():
                            st.write(f"- {method}: {count} emails")
                    
                    if debug_mode and data['pages_list']:
                        st.write("**Pages crawled:**")
                        for page in data['pages_list'][:5]:
                            st.write(f"- {page}")
                else:
                    st.warning(f"No emails found. {data.get('error', '')}")
    
    with tab3:
        if debug_mode:
            st.markdown("### 🔍 Debug Information")
            
            total_methods = {}
            for data in all_results.values():
                for method, count in data.get('methods', {}).items():
                    total_methods[method] = total_methods.get(method, 0) + count
            
            if total_methods:
                st.write("**Email extraction method effectiveness:**")
                for method, count in sorted(total_methods.items(), key=lambda x: x[1], reverse=True):
                    st.write(f"- {method}: {count} emails")
            
            failed = [url for url, data in all_results.items() if not data['emails']]
            if failed:
                st.warning(f"⚠️ {len(failed)} sites with no emails:")
                for url in failed[:10]:
                    st.write(f"- {url}")
        else:
            st.info("💡 Enable 'Debug mode' to see detailed extraction information")
    
    st.markdown("---")
    st.info("💡 Created by Shafiq Sanchy")

st.markdown("""
<div style="padding:12px; margin-top:32px; text-align:center; font-size:13px; color:#555; border-top:1px solid #eee;">
© Shafiq Sanchy 2025 | Email Extractor Pro v2.1 (Fixed)
</div>
""", unsafe_allow_html=True)
