import streamlit as st
import asyncio
import aiohttp
import re
import pandas as pd
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import time
import json
import os

# --- Configuration ---
MAX_CONCURRENT_REQUESTS = 50
REQUEST_TIMEOUT = 10
CRAWL_DEPTH = 1
CONTACT_KEYWORDS = ['contact', 'about', 'support', 'get-in-touch', 'reach-us', 'team']
MAX_INTERNAL_LINKS_PER_DOMAIN = 5

# --- File and Session State Management ---
RESULTS_DIR = "temp_results"
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

def save_results_to_file(results, failed, timeout, file_id):
    """Saves the results to a JSON file."""
    data = {
        "results": results,
        "failed_urls": failed,
        "timeout_urls": timeout
    }
    with open(os.path.join(RESULTS_DIR, f"{file_id}.json"), "w") as f:
        json.dump(data, f)

def load_results_from_file(file_id):
    """Loads results from a JSON file."""
    try:
        with open(os.path.join(RESULTS_DIR, f"{file_id}.json"), "r") as f:
            data = json.load(f)
        return data.get("results", []), data.get("failed_urls", []), data.get("timeout_urls", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return [], [], []

def clear_results_file(file_id):
    """Deletes the results file."""
    try:
        os.remove(os.path.join(RESULTS_DIR, f"{file_id}.json"))
    except FileNotFoundError:
        pass

# --- Session State Initialization ---
def initialize_session_state():
    defaults = {
        'is_running': False,
        'stop_extraction': False,
        'results': [],
        'failed_urls': [],
        'timeout_urls': [],
        'extraction_complete': False,
        'result_file_id': None
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

initialize_session_state()

# --- On App Start: Check for existing results ---
if st.session_state.result_file_id and not st.session_state.is_running:
    # If a file ID exists but the app isn't running, try to load results
    loaded_results, loaded_failed, loaded_timeout = load_results_from_file(st.session_state.result_file_id)
    if loaded_results or loaded_failed or loaded_timeout:
        st.session_state.results = loaded_results
        st.session_state.failed_urls = loaded_failed
        st.session_state.timeout_urls = loaded_timeout
        st.session_state.extraction_complete = True
    else:
        # File doesn't exist or is empty, reset state
        st.session_state.result_file_id = None

# --- Helper Functions (No changes here) ---
def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

async def resolve_url(session, url):
    try:
        async with session.head(url, allow_redirects=True, timeout=REQUEST_TIMEOUT) as response:
            return str(response.url)
    except Exception:
        try:
            async with session.get(url, allow_redirects=True, timeout=REQUEST_TIMEOUT) as response:
                return str(response.url)
        except Exception:
            return url

async def scrape_and_extract_emails(session, url, depth, smart_crawl):
    found_emails = set()
    priority_links = set()
    regular_links = set()
    
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            if response.status == 200:
                content = await response.text()
                soup = BeautifulSoup(content, 'lxml')
                
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href']
                    if href.startswith('mailto:'):
                        email = href.replace('mailto:', '').split('?')[0]
                        found_emails.add(email)
                
                page_text = soup.get_text() + " ".join([tag.string for tag in soup.find_all('script') if tag.string])
                emails_in_text = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', page_text)
                found_emails.update(emails_in_text)

                if depth > 0:
                    base_domain = urlparse(url).netloc
                    for a_tag in soup.find_all('a', href=True):
                        link = urljoin(url, a_tag['href'])
                        parsed_link = urlparse(link)

                        if (parsed_link.netloc != base_domain or
                            not parsed_link.scheme in ['http', 'https'] or
                            re.search(r'\.(pdf|jpg|png|zip|doc|xls|css|js)$', link, re.IGNORECASE) or
                            link.startswith('tel:') or link.startswith('javascript:') or link == '#'):
                            continue
                        
                        if smart_crawl:
                            link_text = a_tag.get_text().lower()
                            link_path = parsed_link.path.lower()
                            if any(keyword in link_text or keyword in link_path for keyword in CONTACT_KEYWORDS):
                                priority_links.add(link)
                            else:
                                regular_links.add(link)
                        else:
                            regular_links.add(link)

    except asyncio.TimeoutError:
        return list(found_emails), list(priority_links), list(regular_links), "timeout"
    except Exception:
        return list(found_emails), list(priority_links), list(regular_links), "error"
        
    return list(found_emails), list(priority_links), list(regular_links), "success"

async def process_url_wrapper(session, url, depth, smart_crawl):
    emails, priority_links, regular_links, status = await scrape_and_extract_emails(session, url, depth, smart_crawl)
    return url, emails, priority_links, regular_links, status

# --- Main Processing Function (Modified to save to file) ---
async def main_extraction_process(initial_urls, smart_crawl_enabled, file_id):
    if not initial_urls:
        return [], [], []

    all_emails = set()
    visited_urls = set()
    urls_to_visit = {url.strip() for url in initial_urls if is_valid_url(url.strip())}
    
    failed_urls = []
    timeout_urls = []
    domain_link_counts = {}

    progress_bar = st.progress(0)
    status_placeholder = st.empty()
    total_urls_to_process = len(urls_to_visit)

    async with aiohttp.ClientSession() as session:
        while urls_to_visit and not st.session_state.stop_extraction:
            current_batch = list(urls_to_visit)
            urls_to_visit.clear()

            resolved_tasks = [resolve_url(session, url) for url in current_batch]
            resolved_urls = await asyncio.gather(*resolved_tasks)
            
            tasks = []
            for url in resolved_urls:
                if url and url not in visited_urls:
                    visited_urls.add(url)
                    task = asyncio.create_task(process_url_wrapper(session, url, CRAWL_DEPTH, smart_crawl_enabled))
                    tasks.append(task)

            if not tasks:
                continue

            results = await asyncio.gather(*tasks)
            
            for url, emails, priority_links, regular_links, status in results:
                all_emails.update(emails)
                
                if status == "timeout":
                    timeout_urls.append(url)
                elif status == "error":
                    failed_urls.append(url)

                if CRAWL_DEPTH > 0:
                    urls_to_visit.update(priority_links - visited_urls)
                    
                    if smart_crawl_enabled:
                        base_domain = urlparse(url).netloc
                        if base_domain not in domain_link_counts:
                            domain_link_counts[base_domain] = 0
                        
                        allowed_links = []
                        for link in regular_links:
                            if domain_link_counts[base_domain] < MAX_INTERNAL_LINKS_PER_DOMAIN:
                                if link not in visited_urls:
                                    allowed_links.append(link)
                                    domain_link_counts[base_domain] += 1
                            else:
                                break
                        urls_to_visit.update(allowed_links)
                    else:
                        urls_to_visit.update(regular_links - visited_urls)

            total_urls_to_process = len(visited_urls) + len(urls_to_visit)
            progress = len(visited_urls) / total_urls_to_process if total_urls_to_process > 0 else 1.0
            progress_bar.progress(progress)
            status_placeholder.markdown(
                f"<div style='background-color:#f0f2f6;padding:10px;border-radius:5px;'><b>Status:</b> Processed: {len(visited_urls)} | Found: {len(all_emails)} | Queue: {len(urls_to_visit)}</div>", unsafe_allow_html=True
            )

    progress_bar.progress(1.0)
    if st.session_state.stop_extraction:
        status_placeholder.markdown("<div style='background-color:#fff3cd;color:#856404;padding:10px;border-radius:5px;'><b>⚠️ Extraction Stopped.</b></div>", unsafe_allow_html=True)
    else:
        status_placeholder.markdown("<div style='background-color:#d4edda;color:#155724;padding:10px;border-radius:5px;'><b>✅ Extraction Complete!</b></div>", unsafe_allow_html=True)
        # --- Save results to file upon completion ---
        final_emails = list(all_emails)
        save_results_to_file(final_emails, failed_urls, timeout_urls, file_id)

    return list(all_emails), failed_urls, timeout_urls

# --- Streamlit App UI ---
st.set_page_config(page_title="Advanced Email Extractor", layout="wide")

st.title("🚀 Advanced Email Extractor")
st.markdown("This tool extracts email addresses and saves results, so you can refresh the tab without losing data.")

# --- Control Buttons ---
col1, col2, col3 = st.columns([1, 1, 1])

with col1:
    if not st.session_state.is_running:
        if st.button("🔎 Start Extraction", type="primary"):
            st.session_state.is_running = True
            st.session_state.stop_extraction = False
            st.session_state.extraction_complete = False
            st.session_state.result_file_id = str(time.time()) # Create a unique ID for this run
            st.rerun()

with col2:
    if st.session_state.is_running:
        if st.button("⏹️ Stop Extraction"):
            st.session_state.stop_extraction = True

with col3:
    if st.session_state.extraction_complete:
        if st.button("🗑️ Clear Results"):
            clear_results_file(st.session_state.result_file_id)
            for key in st.session_state.keys():
                del st.session_state[key]
            initialize_session_state()
            st.rerun()

# --- Main Logic ---
if st.session_state.is_running:
    url_input = st.text_area("Enter URLs (one per line)", height=200, placeholder="https://example.com\n...")
    
    with st.expander("⚙️ Advanced Settings (Locked during execution)"):
        st.info("Settings are locked. Please stop and restart to change them.")
        st.write(f"Max Concurrent Requests: {st.session_state.get('max_concurrent', MAX_CONCURRENT_REQUESTS)}")
        st.write(f"Request Timeout: {st.session_state.get('request_timeout', REQUEST_TIMEOUT)}s")
        st.write(f"Crawling Depth: {st.session_state.get('crawl_depth', CRAWL_DEPTH)}")
        st.write(f"Smart Crawl: {'Enabled' if st.session_state.get('smart_crawl', True) else 'Disabled'}")

    initial_urls = [url.strip() for url in url_input.split('\n') if url.strip()]
    
    if initial_urls:
        MAX_CONCURRENT_REQUESTS = st.session_state.get('max_concurrent', MAX_CONCURRENT_REQUESTS)
        REQUEST_TIMEOUT = st.session_state.get('request_timeout', REQUEST_TIMEOUT)
        CRAWL_DEPTH = st.session_state.get('crawl_depth', CRAWL_DEPTH)
        SMART_CRAWL_ENABLED = st.session_state.get('smart_crawl', True)
        
        start_time = time.time()
        final_emails, failed, timeout = asyncio.run(main_extraction_process(initial_urls, SMART_CRAWL_ENABLED, st.session_state.result_file_id))
        end_time = time.time()

        st.session_state.results = final_emails
        st.session_state.failed_urls = failed
        st.session_state.timeout_urls = timeout
        st.session_state.is_running = False
        st.session_state.extraction_complete = True
        st.rerun()

elif st.session_state.extraction_complete:
    st.success("Extraction finished. Here are your results.")
    st.balloons()
    
    if st.session_state.results:
        st.subheader("📋 All Emails (Copy)")
        emails_string = "\n".join(sorted(list(st.session_state.results)))
        st.text_area("All unique emails found:", value=emails_string, height=200)
        
        st.subheader("💾 Download as CSV")
        df = pd.DataFrame(st.session_state.results, columns=["Email"])
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download emails.csv",
            data=csv,
            file_name='extracted_emails.csv',
            mime='text/csv',
        )
    else:
        st.info("No emails were found.")

    if st.session_state.failed_urls or st.session_state.timeout_urls:
        st.subheader("🔍 Analysis of Failed URLs")
        col1, col2 = st.columns(2)
        with col1:
            if st.session_state.timeout_urls:
                st.warning(f"**{len(st.session_state.timeout_urls)} URLs Timed Out:**")
                st.text("\n".join(st.session_state.timeout_urls))
        with col2:
            if st.session_state.failed_urls:
                st.error(f"**{len(st.session_state.failed_urls)} URLs Failed:**")
                st.text("\n".join(st.session_state.failed_urls))
        st.info("💡 You can copy these URLs and exclude them from your next run to save time.")

else:
    url_input = st.text_area("Enter URLs (one per line)", height=200, placeholder="https://example.com\n...")
    
    with st.expander("⚙️ Advanced Settings (Optional)"):
        st.session_state.max_concurrent = st.slider("Max Concurrent Requests", 10, 100, MAX_CONCURRENT_REQUESTS)
        st.session_state.request_timeout = st.slider("Request Timeout (seconds)", 5, 30, REQUEST_TIMEOUT)
        st.session_state.crawl_depth = st.slider("Crawling Depth", 0, 2, CRAWL_DEPTH)
        st.session_state.smart_crawl = st.checkbox("Enable Smart Crawl", value=True)
        
        st.info("Results are saved automatically. You can safely refresh the tab.")
