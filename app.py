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
import dns.resolver
import urllib3

# ---------------------
# Config & constants
# ---------------------
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)
HEADERS = {"User-Agent": "EmailExtractor/1.0"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------
# Helper functions
# ---------------------
def resolve_url(url):
    """Resolve shortened URLs to final destination, ignore SSL errors."""
    try:
        resp = requests.head(url, allow_redirects=True, headers=HEADERS, timeout=10, verify=False)
        final_url = resp.url
        # Test GET request in case HEAD fails
        try:
            requests.get(final_url, headers=HEADERS, timeout=10, verify=False)
        except:
            pass
        return final_url
    except Exception as e:
        st.warning(f"⚠ Could not resolve {url}: {e}")
        return url

def normalize_url(url):
    """Ensure URL starts with http:// or https://"""
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def is_email_valid(email):
    """Check if email domain has MX records."""
    try:
        domain = email.split("@")[1]
        records = dns.resolver.resolve(domain, 'MX')
        return True if records else False
    except:
        return False

# ---------------------
# Streamlit UI
# ---------------------
st.set_page_config(page_title="Email Extractor", layout="wide")
st.title("📧 Email Extractor created by Shafiq Sanchy")

urls_input = st.text_area(
    "Enter website URLs (one per line)",
    "fusiondigital.ie\nexample.com"
)

crawl_depth = st.slider("Crawl depth (0 = only homepage)", 0, 3, 1)
max_pages = st.number_input("Max pages per site", min_value=1, max_value=200, value=30)
delay = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=5.0, value=0.5, step=0.1)
verify_emails = st.checkbox("✅ Verify emails (MX check)")

# ---------------------
# Extract emails
# ---------------------
if st.button("Extract Emails"):
    # Normalize and resolve URLs
    websites = []
    for u in urls_input.splitlines():
        norm_url = normalize_url(u)
        if norm_url:
            final_url = resolve_url(norm_url)
            websites.append(final_url)

    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        all_results = {}
        total_emails_found = 0

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
                    resp = requests.get(current_url, headers=HEADERS, timeout=10, verify=False)
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

            # Verify emails if checkbox selected
            if verify_emails and found_emails:
                verified_emails = {e for e in found_emails if is_email_valid(e)}
            else:
                verified_emails = found_emails

            total_emails_found += len(verified_emails)

            if verified_emails:
                st.success(f"✅ Found {len(verified_emails)} emails on {url}")
            else:
                st.warning(f"No emails found on {url}")

            all_results[url] = verified_emails

        # ---------------------
        # Display emails per site
        # ---------------------
        st.subheader("📋 Extracted Emails per Website")
        for site, emails in all_results.items():
            if emails:
                st.markdown(f"**{site}**")
                df = pd.DataFrame({
                    "Email": sorted(emails),
                    "Verified": [is_email_valid(e) if verify_emails else "Skipped" for e in sorted(emails)]
                })
                st.dataframe(df, height=min(300, 30*len(emails)))
            else:
                st.markdown(f"**{site}** → No emails found.")

        # ---------------------
        # CSV download across all sites
        # ---------------------
        if any(len(es) for es in all_results.values()):
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email", "verified"])
            for site, emails in all_results.items():
                for e in sorted(emails):
                    writer.writerow([site, e, is_email_valid(e) if verify_emails else "Skipped"])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")

            st.download_button(
                label="📥 Download all emails (CSV)",
                data=csv_bytes,
                file_name="emails.csv",
                mime="text/csv"
            )

        # ---------------------
        # Completion notification
        # ---------------------
        st.balloons()
        st.success(f"🎉 Extraction completed! Total emails found: {total_emails_found}")
        st.info("💡 Done by Shafiq Sanchy")
