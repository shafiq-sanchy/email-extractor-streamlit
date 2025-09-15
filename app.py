# app.py
import re
import time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import streamlit as st

EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

HEADERS = {"User-Agent": "EmailExtractor/1.0"}

st.set_page_config(page_title="Email Extractor", layout="wide")
st.title("📧 Multi-Site Email Extractor with Crawler")

urls_input = st.text_area(
    "Enter website URLs (one per line)",
    "https://example.com\nhttps://www.python.org"
)

crawl_depth = st.slider("Crawl depth (0 = only homepage)", 0, 3, 1)
max_pages = st.number_input("Max pages per site", min_value=1, max_value=200, value=30)
delay = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=5.0, value=0.5, step=0.1)

if st.button("Extract Emails"):
    websites = [u.strip() for u in urls_input.splitlines() if u.strip()]
    all_results = {}

    for url in websites:
        st.subheader(f"🔍 Scanning {url}")
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

            # find emails
            for m in set(EMAIL_REGEX.findall(html)):
                found_emails.add(m.lower())

            # crawl deeper
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
