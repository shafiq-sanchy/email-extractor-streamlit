# app.py
import re
import time
import io
import csv
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd

# ---------------------
# Config & constants
# ---------------------
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)
HEADERS = {"User-Agent": "EmailExtractor/1.0"}

# ---------------------
# Streamlit UI
# ---------------------
st.set_page_config(page_title="Email Extractor", layout="wide")
st.title("📧 Multi-Site Email Extractor with Crawler")

urls_input = st.text_area(
    "Enter website URLs (one per line)",
    
)

crawl_depth = st.slider("Crawl depth (0 = only homepage)", 0, 3, 1)
max_pages = st.number_input("Max pages per site", min_value=1, max_value=200, value=30)
delay = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=5.0, value=0.5, step=0.1)

# ---------------------
# Extract emails
# ---------------------
if st.button("Extract Emails"):
   
    

# Resolve shortened URLs automatically
def resolve_url(url):
    try:
        resp = requests.head(url, allow_redirects=True, headers=HEADERS, timeout=10)
        return resp.url  # final destination URL
    except Exception as e:
        st.warning(f"⚠ Could not resolve {url}: {e}")
        return url






    
    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        all_results = {}

        for url in websites:
            st.subheader(f"🔍 Scanning {url}")
            if not url.lower().startswith(("http://", "https://")):
                st.warning(f"Skipping invalid URL (must start with http:// or https://): {url}")
                all_results[url] = set()
                continue

            parsed_root = urlparse(url)
            base_domain = parsed_root.netloc

            to_visit = [(url, 0)]
            seen = set([url])
            found_emails = set()
            pages_processed = 0

            progress = st.progress(0)
            status = st.empty()

            while to_visit and pages_processed < max_pages:
                current_url, cur_depth = to_visit.pop(0)
                pages_processed += 1
                status.text(f"({pages_processed}) Visiting: {current_url}")

                try:
                    resp = requests.get(current_url, headers=HEADERS, timeout=10)
                    html = resp.text
                except Exception as e:
                    st.write(f"❌ Failed to fetch {current_url}: {e}")
                    continue

                # Extract emails
                for m in set(EMAIL_REGEX.findall(html)):
                    found_emails.add(m.lower())

                # Crawl internal links
                if cur_depth < crawl_depth:
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a['href'].strip()
                        joined = urljoin(current_url, href)
                        p = urlparse(joined)

                        if p.scheme not in ("http", "https"):
                            continue
                        if p.netloc != base_domain:
                            continue

                        norm = p._replace(fragment="").geturl()
                        if norm not in seen:
                            seen.add(norm)
                            to_visit.append((norm, cur_depth + 1))

                progress.progress(min(1.0, pages_processed / float(max_pages)))
                time.sleep(delay)

            progress.empty()
            status.empty()

            if found_emails:
                st.success(f"✅ Found {len(found_emails)} emails on {url}")
            else:
                st.warning(f"No emails found on {url}")

            all_results[url] = found_emails

        # ---------------------
        # Display emails per site
        # ---------------------
        st.subheader("📋 Extracted Emails per Website")
        for site, emails in all_results.items():
            if emails:
                st.markdown(f"**{site}**")
                df = pd.DataFrame(sorted(emails), columns=["Email"])
                st.dataframe(df, height=min(300, 30 * len(emails)))
            else:
                st.markdown(f"**{site}** → No emails found.")

        # ---------------------
        # CSV download across all sites
        # ---------------------
        if any(len(es) for es in all_results.values()):
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email"])
            for site, emails in all_results.items():
                for e in sorted(emails):
                    writer.writerow([site, e])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")

            st.download_button(
                label="📥 Download all emails (CSV)",
                data=csv_bytes,
                file_name="emails.csv",
                mime="text/csv"
            )
