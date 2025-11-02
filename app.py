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

MAX_CONCURRENT_REQUESTS = 20
REQUEST_TIMEOUT = 12
CRAWL_DEPTH = 1 
CONTACT_KEYWORDS = [
    'contact', 'about', 'contact-us', 'support', 'get-in-touch', 'reach-us', 'team', 'kontakt', 'contato', 'contatti', 
    'contacto', 'kontak', 'hubungi', 'liên hệ', '연락처', 'お問い合わせ'
]
SKIP_PATH_KEYWORDS = ['blog', 'post', 'article', 'news', 'tag', 'category', 'product', 'shop']
DEFAULT_MAX_URLS_PER_DOMAIN = 30 
MAX_QUEUE_SIZE_PER_DOMAIN = 30
BATCH_SIZE = 20

# Added headers to make requests look like they're coming from a real browser
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.google.com/',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# --- File and Session State Management ---
RESULTS_DIR = "temp_results"
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

def save_results_to_file(results, failed, timeout, file_id):
    # Changed to store email with source URL
    data = {"results": list(results.items()), "failed_urls": failed, "timeout_urls": timeout}
    with open(os.path.join(RESULTS_DIR, f"{file_id}.json"), "w") as f:
        json.dump(data, f)

def load_results_from_file(file_id):
    try:
        with open(os.path.join(RESULTS_DIR, f"{file_id}.json"), "r") as f:
            data = json.load(f)
        # Convert list of tuples back to dict
        results_dict = dict(data.get("results", []))
        return results_dict, data.get("failed_urls", []), data.get("timeout_urls", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, [], []

def clear_results_file(file_id):
    try:
        os.remove(os.path.join(RESULTS_DIR, f"{file_id}.json"))
    except FileNotFoundError:
        pass

# --- Session State Initialization ---
def initialize_session_state():
    defaults = {
        'is_running': False, 'stop_extraction': False, 'extraction_complete': False, 'result_file_id': None,
        'urls_to_visit': set(), 'visited_urls': set(), 'all_emails': {},  # Changed from set to dict to store email-source mapping
        'failed_urls': [], 'timeout_urls': [], 'domain_visit_counts': {}, 'processed_count': 0, 'total_urls_found': 0,
        'debug_mode': False
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

initialize_session_state()

# --- On App Start: Check for existing results ---
if st.session_state.result_file_id and not st.session_state.is_running and not st.session_state.extraction_complete:
    loaded_results, loaded_failed, loaded_timeout = load_results_from_file(st.session_state.result_file_id)
    if loaded_results or loaded_failed or loaded_timeout:
        st.session_state.all_emails = loaded_results
        st.session_state.failed_urls = loaded_failed
        st.session_state.timeout_urls = loaded_timeout
        st.session_state.extraction_complete = True
    else:
        st.session_state.result_file_id = None

# --- Helper Functions ---
def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def normalize_url(url):
    """Removes the fragment (#) from a URL to prevent infinite loops on the same page."""
    return url.split('#')[0]

# --- UPDATED and MORE ROBUST resolve_url function ---
async def resolve_url(session, url, max_redirects=5):
    current_url = url
    for _ in range(max_redirects):
        try:
            # Using GET is generally more reliable for following redirects than HEAD
            async with session.get(current_url, timeout=REQUEST_TIMEOUT, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    redirect_url = response.headers.get('Location')
                    if redirect_url:
                        # Handle relative redirects
                        current_url = urljoin(current_url, redirect_url)
                    else:
                        # No Location header, stop redirecting
                        break
                else:
                    # No redirect status code, we've reached the final destination
                    return current_url
        except Exception:
            # If any request fails, return the last known URL
            return current_url
    return current_url # Return after max_redirects

def clean_email(email):
    """Clean up email by removing extra characters before or after the email address."""
    # Find the email pattern in the string
    match = re.search(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', email)
    if match:
        return match.group(0)
    return email

async def scrape_and_extract_emails(session, url, depth, smart_crawl, ignore_query_params):
    found_emails = set()
    priority_links = set()
    regular_links = set()
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            if response.status == 200:
                content = await response.text()
                soup = BeautifulSoup(content, 'html.parser')
                
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href']
                    if href.startswith('mailto:'):
                        email = href.replace('mailto:', '').split('?')[0].strip()
                        email = clean_email(email)
                        if email:
                            found_emails.add(email)
                
                for a_tag in soup.find_all('a'):
                    link_text = a_tag.get_text()
                    emails_in_text = re.findall(r'\b[a-zA-Z][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', link_text)
                    for email in emails_in_text:
                        email = clean_email(email)
                        if email:
                            found_emails.add(email)

                page_text = soup.get_text() + " ".join([tag.string for tag in soup.find_all('script') if tag.string])
                emails_in_text = re.findall(r'\b[a-zA-Z][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', page_text)
                for email in emails_in_text:
                    email = clean_email(email)
                    if email:
                        found_emails.add(email)

                for form in soup.find_all('form'):
                    action = form.get('action', '')
                    emails_in_action = re.findall(r'\b[a-zA-Z][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', action)
                    for email in emails_in_action:
                        email = clean_email(email)
                        if email:
                            found_emails.add(email)
                    for input_tag in form.find_all('input', type='hidden'):
                        value = input_tag.get('value', '')
                        emails_in_value = re.findall(r'\b[a-zA-Z][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', value)
                        for email in emails_in_value:
                            email = clean_email(email)
                            if email:
                                found_emails.add(email)

                if depth > 0:
                    base_domain = urlparse(url).netloc
                    for a_tag in soup.find_all('a', href=True):
                        link = urljoin(url, a_tag['href'])
                        normalized_link = normalize_url(link)
                        parsed_link = urlparse(normalized_link)

                        if ignore_query_params and parsed_link.query:
                            continue
                        
                        if (parsed_link.netloc != base_domain or
                            not parsed_link.scheme in ['http', 'https'] or
                            re.search(r'\.(pdf|jpg|png|zip|doc|xls|css|js|xml)$', normalized_link, re.IGNORECASE) or
                            normalized_link.startswith('tel:') or normalized_link.startswith('javascript:') or normalized_link == '#' or
                            any(keyword in parsed_link.path.lower() for keyword in SKIP_PATH_KEYWORDS) or
                            re.search(r'/page/\d+$', parsed_link.path.lower())):
                            continue
                        
                        if smart_crawl:
                            link_text = a_tag.get_text().lower()
                            link_path = parsed_link.path.lower()
                            if any(keyword in link_text or keyword in link_path for keyword in CONTACT_KEYWORDS):
                                priority_links.add(normalized_link)
                            else:
                                regular_links.add(normalized_link)
                        else:
                            regular_links.add(normalized_link)
    except asyncio.TimeoutError:
        return list(found_emails), list(priority_links), list(regular_links), "timeout"
    except Exception as e:
        return list(found_emails), list(priority_links), list(regular_links), f"error: {str(e)}"
    return list(found_emails), list(priority_links), list(regular_links), "success"

async def process_url_wrapper(session, url, depth, smart_crawl, ignore_query_params):
    emails, priority_links, regular_links, status = await scrape_and_extract_emails(session, url, depth, smart_crawl, ignore_query_params)
    return url, emails, priority_links, regular_links, status

def run_async_batch(batch, depth, smart_crawl, ignore_query_params):
    async def _run():
        # Added headers to make requests look like they're coming from a real browser
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            tasks = [process_url_wrapper(session, url, depth, smart_crawl, ignore_query_params) for url in batch]
            return await asyncio.gather(*tasks)
    return asyncio.run(_run())

# --- Streamlit App UI ---
st.set_page_config(page_title="Advanced Email Extractor", layout="wide")
st.title("Advanced Email Extractor")
st.markdown("")

main_container = st.container()

with main_container:
    if not st.session_state.is_running:
        exclude_urls_input = st.text_area("URLs to Exclude (one per line)", height=100, key="exclude_urls", help="Paste any URLs you want the app to completely ignore.")
        url_input = st.text_area("Enter URLs to Process (one per line)", height=200, key="url_input")
        
        with st.expander("⚙️ Advanced Settings (Optional)"):
            st.session_state.debug_mode = st.checkbox("Enable Debug Mode", value=False, help="Process one URL at a time and show detailed logs.")
            st.session_state.max_concurrent = st.slider("Max Concurrent Requests", 10, 100, MAX_CONCURRENT_REQUESTS)
            st.session_state.request_timeout = st.slider("Request Timeout (seconds)", 5, 30, REQUEST_TIMEOUT)
            st.session_state.crawl_depth = st.slider("Crawling Depth", 0, 2, CRAWL_DEPTH, help="0 = Fastest (only given URLs). 1 = Slower but more thorough.")
            st.session_state.max_urls_per_domain = st.slider("Max URLs per Domain", 10, 100, DEFAULT_MAX_URLS_PER_DOMAIN, help="Stops crawling a domain after this many pages.")
            st.session_state.smart_crawl = st.checkbox("Enable Smart Crawl (Highly Recommended)", value=True)
            st.session_state.ignore_query_params = st.checkbox("Ignore URLs with Query Parameters", value=True)
            st.info(f"The app now extracts emails from forms and link text, ensuring maximum accuracy.")

        if st.button("🔎 Start Extraction", type="primary"):
            urls = [url.strip() for url in url_input.split('\n') if url.strip()]
            exclude_urls = set(url.strip() for url in exclude_urls_input.split('\n') if url.strip())
            final_urls = [url for url in urls if url not in exclude_urls]

            if final_urls:
                initialize_session_state()
                st.session_state.urls_to_visit = set(normalize_url(url) for url in final_urls)
                st.session_state.total_urls_found = len(st.session_state.urls_to_visit)
                st.session_state.is_running = True
                st.session_state.result_file_id = str(time.time())
                st.rerun()
            else:
                st.warning("Please enter at least one valid URL.")

    if st.session_state.is_running:
        st.subheader("Processing...")
        
        if st.session_state.total_urls_found > 0:
            progress_value = st.session_state.processed_count / st.session_state.total_urls_found
        else:
            progress_value = 0.0
        
        progress = min(1.0, max(0.0, progress_value))
        progress_bar = st.progress(progress)
        
        status_placeholder = st.empty()
        status_placeholder.markdown(
            f"<div style='background-color:#f0f2f6;padding:10px;border-radius:5px;'>"
            f"<b>Status:</b> Processed: {st.session_state.processed_count} | Found: {len(st.session_state.all_emails)} | Queue: {len(st.session_state.urls_to_visit)}</div>",
            unsafe_allow_html=True
        )

        if st.session_state.debug_mode:
            st.write("--- Debug Info ---")
            st.write(f"Processed Count: {st.session_state.processed_count}")
            st.write(f"Total URLs Found: {st.session_state.total_urls_found}")
            st.write(f"Raw Progress Value: {progress_value}")
            st.write(f"Clamped Progress Value: {progress}")
            st.write("-------------------")

        if st.button("⏹️ Stop Extraction"):
            st.session_state.stop_extraction = True

        if st.session_state.stop_extraction or not st.session_state.urls_to_visit:
            st.session_state.is_running = False
            st.session_state.extraction_complete = True
            save_results_to_file(st.session_state.all_emails, st.session_state.failed_urls, st.session_state.timeout_urls, st.session_state.result_file_id)
            st.rerun()
        else:
            current_batch_size = 1 if st.session_state.debug_mode else BATCH_SIZE
            current_batch = list(st.session_state.urls_to_visit)[:current_batch_size]
            st.session_state.urls_to_visit.difference_update(current_batch)
            
            should_skip_batch = False
            max_limit = st.session_state.get('max_urls_per_domain', DEFAULT_MAX_URLS_PER_DOMAIN)
            
            for url in current_batch:
                domain = urlparse(url).netloc
                if st.session_state.domain_visit_counts.get(domain, 0) >= max_limit:
                    should_skip_batch = True
                    break
            
            if should_skip_batch:
                if st.session_state.debug_mode:
                    st.warning(f"🛑 SKIPPING BATCH: A domain in this batch has reached its visit limit of {max_limit}.")
                st.session_state.processed_count += len(current_batch)
                st.session_state.total_urls_found = len(st.session_state.visited_urls) + len(st.session_state.urls_to_visit)
                time.sleep(0.1)
                st.rerun()

            if st.session_state.debug_mode:
                st.write(f"🔍 Processing batch of {len(current_batch)} URL(s):")
                st.code("\n".join(current_batch))

            resolved_urls = []
            async def resolve_batch():
                # Added headers to make requests look like they're coming from a real browser
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    tasks = [resolve_url(session, normalize_url(url)) for url in current_batch]
                    return await asyncio.gather(*tasks)
            resolved_urls = asyncio.run(resolve_batch())

            batch_results = run_async_batch(resolved_urls, CRAWL_DEPTH, st.session_state.get('smart_crawl', True), st.session_state.get('ignore_query_params', True))
            
            for url, emails, priority_links, regular_links, status in batch_results:
                st.session_state.visited_urls.add(url)
                
                # Store emails with their source URL
                for email in emails:
                    if email not in st.session_state.all_emails:
                        st.session_state.all_emails[email] = url
                
                if st.session_state.debug_mode:
                    st.write(f"**URL:** `{url}`")
                    st.write(f"**Status:** {status}")
                    st.write(f"**Emails Found:** {len(emails)}")
                    if emails:
                        st.code("\n".join(emails))
                    st.divider()

                if "timeout" in status:
                    st.session_state.timeout_urls.append(url)
                elif "error" in status:
                    st.session_state.failed_urls.append(url)
                
                if CRAWL_DEPTH > 0:
                    all_new_links = set(priority_links) | set(regular_links)
                    for link in all_new_links:
                        if link not in st.session_state.visited_urls and link not in st.session_state.urls_to_visit:
                            domain = urlparse(link).netloc
                            queue_count_for_domain = sum(1 for u in st.session_state.urls_to_visit if urlparse(u).netloc == domain)
                            
                            if queue_count_for_domain < MAX_QUEUE_SIZE_PER_DOMAIN:
                                st.session_state.urls_to_visit.add(link)
                            elif st.session_state.debug_mode:
                                st.warning(f"🛑 SKIPPED: Link `{link}` not added to queue. Queue for domain `{domain}` is full.")

            for url in resolved_urls:
                domain = urlparse(url).netloc
                st.session_state.domain_visit_counts[domain] = st.session_state.domain_visit_counts.get(domain, 0) + 1

            st.session_state.processed_count += len(current_batch)
            st.session_state.total_urls_found = len(st.session_state.visited_urls) + len(st.session_state.urls_to_visit)
            
            time.sleep(0.1)
            st.rerun()

    if st.session_state.extraction_complete:
        st.success("Extraction finished. Here are your results.")
        st.balloons()
        
        # Display email count prominently
        email_count = len(st.session_state.all_emails)
        st.markdown(f"<p style='font-size: 20px; font-weight: bold; color: green;'>Total Emails Found: {email_count}</p>", unsafe_allow_html=True)
        
        if st.session_state.all_emails:
            st.subheader("📋 All Emails (Copy)")
            emails_string = "\n".join(sorted(list(st.session_state.all_emails.keys())))
            st.text_area("All unique emails found:", value=emails_string, height=200)
            st.subheader("💾 Download as CSV")
            
            # Create DataFrame with email and source URL
            data = []
            for email, source in st.session_state.all_emails.items():
                data.append({"Email": email, "Source URL": source})
            
            df = pd.DataFrame(data)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(label="Download emails.csv", data=csv, file_name='extracted_emails.csv', mime='text/csv')
        else:
            st.info("No emails were found.")

        if st.session_state.failed_urls or st.session_state.timeout_urls:
            st.subheader("🔍 Analysis of Failed URLs")
            col1, col2 = st.columns(2)
            with col1:
                if st.session_state.timeout_urls: st.warning(f"**{len(st.session_state.timeout_urls)} URLs Timed Out:**"); st.text("\n".join(st.session_state.timeout_urls))
            with col2:
                if st.session_state.failed_urls: st.error(f"**{len(st.session_state.failed_urls)} URLs Failed:**"); st.text("\n".join(st.session_state.failed_urls))
            st.info("💡 You can copy these URLs and exclude them from your next run to save time.")
        
        if st.button("🗑️ Clear Results & Start New Search"):
            clear_results_file(st.session_state.result_file_id)
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            initialize_session_state()
            st.rerun()
