import re
import io
import csv
import time
import gc
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import streamlit as st
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)

EXCLUDED_KEYWORDS = ["support@", "account", "filter", "team", "hr", "enquiries", "press@", "job", "career", "sales", "inquiry", "yourname", "john", "example", "fraud", "scam", "privacy@", "no-reply@", "noreply@", "unsubscribe@"]
EXCLUDED_DOMAINS_SUBSTR = ["sentry", "wixpress", "sentry.wixpress.com", "latofonts", "address", "yourdomain", "err.abtm.io", "sentry-next", "wix", "mysite", "yoursite", "amazonaws", "localhost", "invalid", "example", "website", "2x.png"]
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", "email.com", "the.benhawy", ".gif", ".svg", ".domain", "example", ".webp", ".ico", ".bmp", ".pdf")

MAX_CRAWL_WORKERS = 20
HEADERS = {"User-Agent": "EmailExtractor/1.0"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
retries = Retry(total=1, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
session.mount("http://", adapter)
session.mount("https://", adapter)

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
    try:
        resp = session.head(url, allow_redirects=True, headers=HEADERS, timeout=5, verify=False)
        return resp.url or url
    except Exception:
        return url

HEX_GARBAGE_RE = re.compile(r'^[0-9a-f]{16,}$', re.I)

def looks_like_garbage(email: str) -> bool:
    if not email or " " in email:
        return True
    e = email.strip().lower()
    if not EMAIL_REGEX.search(e):
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

def crawl_site(url: str, crawl_depth: int = 1, max_pages: int = 30, delay: float = 0.2) -> tuple:
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

st.set_page_config(page_title="Email Extractor", layout="wide")

st.markdown("<h1>Email Extractor</h1>", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([2.5, 1.5])
    with col1:
        urls_input = st.text_area("Enter URLs (one per line)", height=280, placeholder="https://example.com")
        url_lines = [line.strip() for line in urls_input.splitlines() if line.strip()]
        url_count = len([normalize_url(line) for line in url_lines if normalize_url(line)])
        if url_count > 0:
            st.info(f"{url_count} URLs ready")
    
    with col2:
        crawl_depth = st.slider("Crawl depth", 0, 3, 2)
        max_pages = st.number_input("Max pages", 10, 500, 50, step=10)
        delay = st.number_input("Delay", 0.0, 2.0, 0.1, 0.1)
        batch_size = st.number_input("Batch size", 10, 100, 25, step=5)

st.markdown("---")

extract_button = st.button("Extract Emails", type="primary")

if extract_button:
    websites = []
    for line in urls_input.splitlines():
        n = normalize_url(line)
        if n:
            websites.append(resolve_url(n))

    if not websites:
        st.warning("Please enter URLs")
    else:
        st.info(f"Processing {len(websites)} websites...")
        
        progress_bar = st.progress(0)
        status_placeholder = st.empty()
        
        with st.expander("Activity", expanded=False):
            activity_log = st.empty()
        
        all_results = {}
        unique_emails = set()
        completed = 0
        activity_messages = []
        
        total_batches = (len(websites) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(websites))
            batch_websites = websites[start_idx:end_idx]
            
            with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
                futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in batch_websites}
                
                for fut in as_completed(futures):
                    url = futures[fut]
                    try:
                        _, raw_emails = fut.result()
                        
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
                        
                        status_placeholder.markdown(f"Progress: {completed}/{len(websites)} | Found: {len(unique_emails)} emails | Batch: {batch_num + 1}/{total_batches}")
                        
                        activity_messages.append(f"{url[:60]}... → {len(cleaned)} emails")
                        if len(activity_messages) > 20:
                            activity_messages.pop(0)
                        activity_log.text("\n".join(activity_messages[-20:]))
                        
                    except Exception:
                        activity_messages.append(f"{url[:60]}... → Error")
                        if len(activity_messages) > 20:
                            activity_messages.pop(0)
                        activity_log.text("\n".join(activity_messages[-20:]))

        progress_bar.progress(1.0)
        st.success(f"Found {len(unique_emails)} emails from {len(all_results)} websites")
        
        st.markdown("---")
        
        if unique_emails:
            col_main, col_side = st.columns([2.5, 1.5])
            
            with col_main:
                emails_text = "\n".join(sorted(unique_emails))
                st.text_area("All emails:", emails_text, height=350)
            
            with col_side:
                st.markdown(f"**Sites:** {len(all_results)}")
                st.markdown(f"**Raw:** {sum(len(data['raw']) for data in all_results.values())}")
                st.markdown(f"**Clean:** {len(unique_emails)}")
                
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(["website", "email"])
                for site, data in all_results.items():
                    for e in data["clean"]:
                        writer.writerow([site, e])
                csv_bytes = csv_buffer.getvalue().encode("utf-8")
                st.download_button("Download CSV", data=csv_bytes, file_name="emails.csv", mime="text/csv")
                
                txt_bytes = emails_text.encode("utf-8")
                st.download_button("Download TXT", data=txt_bytes, file_name="emails.txt", mime="text/plain")
        else:
            st.info("No emails found")

st.markdown("<div style='text-align:center; padding:20px;'>Shafiq Sanchy</div>", unsafe_allow_html=True)
