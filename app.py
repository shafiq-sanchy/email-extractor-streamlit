import streamlit as st
import asyncio
import aiohttp
import re
import pandas as pd
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import time

# --- Configuration ---
# একসাথে কতগুলো URL প্রসেস হবে, সার্ভারে চাপ কমাতে এটি ব্যবহার করুন
MAX_CONCURRENT_REQUESTS = 20 
# প্রতিটি রিকোয়েস্টের জন্য সর্বোচ্চ সময় (সেকেন্ডে)
REQUEST_TIMEOUT = 15 
# কতটা গভীর পর্যন্ত ক্রল করবে (0 = শুধু দেওয়া URL, 1 = একটা লেভেল ভেতরে)
CRAWL_DEPTH = 1 

# --- Helper Functions ---

def is_valid_url(url):
    """Check if the URL is valid and has a scheme."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

async def resolve_url(session, url):
    """Resolves a shortened URL to its final destination."""
    try:
        # Use a HEAD request first as it's faster and doesn't download the body
        async with session.head(url, allow_redirects=True, timeout=REQUEST_TIMEOUT) as response:
            return str(response.url)
    except Exception:
        # If HEAD fails, try a GET request
        try:
            async with session.get(url, allow_redirects=True, timeout=REQUEST_TIMEOUT) as response:
                return str(response.url)
        except Exception:
            return url # Return original URL if resolution fails

async def scrape_and_extract_emails(session, url, depth):
    """Scrapes a single URL, extracts emails, and finds internal links for crawling."""
    found_emails = set()
    internal_links = set()
    
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            if response.status == 200:
                content = await response.text()
                soup = BeautifulSoup(content, 'html.parser')
                
                # 1. Extract from mailto links
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href']
                    if href.startswith('mailto:'):
                        email = href.replace('mailto:', '').split('?')[0] # Remove parameters
                        found_emails.add(email)
                
                # 2. Extract from plain text and script tags
                # We include script tags to catch emails hidden in JavaScript variables
                page_text = soup.get_text() + " ".join([tag.string for tag in soup.find_all('script') if tag.string])
                emails_in_text = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', page_text)
                found_emails.update(emails_in_text)

                # 3. Find internal links for crawling if depth is not reached
                if depth > 0:
                    base_domain = urlparse(url).netloc
                    for a_tag in soup.find_all('a', href=True):
                        link = urljoin(url, a_tag['href'])
                        parsed_link = urlparse(link)
                        # Check if the link is on the same domain and is a valid http/https link
                        if parsed_link.netloc == base_domain and parsed_link.scheme in ['http', 'https']:
                            internal_links.add(link)

    except asyncio.TimeoutError:
        st.warning(f"Timeout for URL: {url}")
    except Exception as e:
        st.error(f"Could not process {url}. Error: {e}")
        
    return list(found_emails), list(internal_links)

# --- Main Processing Function ---

async def main_extraction_process(initial_urls):
    """Orchestrates the entire extraction process."""
    if not initial_urls:
        return {}

    # Use a semaphore to limit the number of concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    
    # To keep track of all unique emails and visited URLs
    all_emails = set()
    visited_urls = set()
    # Queue to manage URLs to visit
    urls_to_visit = {url.strip() for url in initial_urls if is_valid_url(url.strip())}
    
    # Create placeholders for UI updates
    progress_bar = st.progress(0)
    status_text = st.empty()
    log_text = st.empty()

    total_urls_to_process = len(urls_to_visit)
    processed_count = 0

    # Use a session for connection pooling
    async with aiohttp.ClientSession() as session:
        while urls_to_visit:
            current_batch = list(urls_to_visit)
            urls_to_visit.clear()

            # Resolve all URLs in the current batch first
            resolved_tasks = [resolve_url(session, url) for url in current_batch]
            resolved_urls = await asyncio.gather(*resolved_tasks)
            
            # Filter out already visited or invalid URLs
            tasks = []
            for url in resolved_urls:
                if url and url not in visited_urls:
                    visited_urls.add(url)
                    # Create a bounded task for each URL
                    task = asyncio.create_task(scrape_and_extract_emails(session, url, CRAWL_DEPTH))
                    tasks.append(task)

            if not tasks:
                continue

            # Run all scraping tasks concurrently
            results = await asyncio.gather(*tasks)
            
            # Process results
            new_internal_links = set()
            for emails, links in results:
                all_emails.update(emails)
                new_internal_links.update(links)
                processed_count += 1
            
            # Add new internal links to the queue for the next level of crawling
            if CRAWL_DEPTH > 0:
                urls_to_visit.update(new_internal_links - visited_urls)
                total_urls_to_process = len(visited_urls) + len(urls_to_visit)

            # Update UI
            progress = len(visited_urls) / total_urls_to_process if total_urls_to_process > 0 else 1.0
            progress_bar.progress(progress)
            status_text.text(f"Processed: {len(visited_urls)} URLs. Found: {len(all_emails)} emails. Queue: {len(urls_to_visit)} URLs remaining.")
            log_text.text(f"Last processed batch: {current_batch[:5]}...") # Show a snippet of the last batch

    # Final UI update
    progress_bar.progress(1.0)
    status_text.text("✅ Extraction Complete!")
    log_text.empty()

    return list(all_emails)

# --- Streamlit App UI ---

st.set_page_config(page_title="Advanced Email Extractor", layout="wide")

st.title("🚀 Advanced Email Extractor")
st.markdown("""
This tool extracts email addresses from a list of websites. It can handle a large number of URLs, resolve shortened links, and perform deep crawling for maximum results.
""")

with st.expander("⚙️ Advanced Settings (Optional)"):
    st.session_state.max_concurrent = st.slider("Max Concurrent Requests", 10, 50, MAX_CONCURRENT_REQUESTS)
    st.session_state.request_timeout = st.slider("Request Timeout (seconds)", 5, 30, REQUEST_TIMEOUT)
    st.session_state.crawl_depth = st.slider("Crawling Depth", 0, 2, CRAWL_DEPTH)
    st.info("""
    - **Max Concurrent Requests:** How many websites to check at the same time. Higher is faster but may get you blocked.
    - **Request Timeout:** How long to wait for a website to respond.
    - **Crawling Depth:** 0 = only the given URLs. 1 = also checks links found on those pages.
    """)

url_input = st.text_area("Enter URLs (one per line)", height=200, placeholder="https://example.com\nhttps://another-site.com\nhttps://t.co/shortlink")

if st.button("🔎 Start Extraction", type="primary"):
    if not url_input.strip():
        st.warning("Please enter at least one URL.")
    else:
        initial_urls = [url.strip() for url in url_input.split('\n') if url.strip()]
        
        # Run the async process
        with st.spinner("Initializing... This may take a while for many URLs. The app will be unresponsive during processing."):
            # Update global config from session state
            MAX_CONCURRENT_REQUESTS = st.session_state.max_concurrent
            REQUEST_TIMEOUT = st.session_state.request_timeout
            CRAWL_DEPTH = st.session_state.crawl_depth
            
            start_time = time.time()
            final_emails = asyncio.run(main_extraction_process(initial_urls))
            end_time = time.time()

        if final_emails:
            st.success(f"Found {len(final_emails)} unique emails in {end_time - start_time:.2f} seconds.")
            
            # --- Display Results ---
            
            # 1. Copy to Clipboard Option
            st.subheader("📋 All Emails (Copy)")
            emails_string = "\n".join(sorted(list(final_emails)))
            st.text_area("All unique emails found:", value=emails_string, height=200)
            
            # 2. CSV Download Option
            st.subheader("💾 Download as CSV")
            df = pd.DataFrame(final_emails, columns=["Email"])
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download emails.csv",
                data=csv,
                file_name='extracted_emails.csv',
                mime='text/csv',
            )
        else:
            st.info("No emails were found.")
