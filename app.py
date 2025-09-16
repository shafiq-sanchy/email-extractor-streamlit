# app.py
import re
import io
import csv
import time
import smtplib
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import streamlit as st
import pandas as pd
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

# Optional MX & SMTP verification
try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)
HEADERS = {"User-Agent": "EmailExtractor/1.0"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MAX_WORKERS = 10

# ---------------------
# Helper functions
# ---------------------
def normalize_url(url):
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url):
    try:
        resp = requests.head(url, allow_redirects=True, headers=HEADERS, timeout=10, verify=False)
        return resp.url
    except:
        return url

def is_email_valid(email):
    if not DNS_AVAILABLE:
        return "Skipped"
    try:
        domain = email.split("@")[1]
        mx_records = dns.resolver.resolve(domain, 'MX')
        mx_hosts = [r.exchange.to_text() for r in mx_records]

        # Simple SMTP VRFY check (some servers block this)
        for mx in mx_hosts:
            try:
                server = smtplib.SMTP(timeout=5)
                server.connect(mx)
                server.helo()
                code, _ = server.mail("test@" + domain)
                server.quit()
                if code == 250:
                    return True
            except:
                continue
        return True if mx_hosts else False
    except:
        return False

def extract_emails_from_html(html):
    found_emails = set()
    for m in set(EMAIL_REGEX.findall(html)):
        found_emails.add(m.lower())
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a['href'].startswith("mailto:"):
            email = a['href'][7:].split('?')[0]
            found_emails.add(email.lower())
    return found_emails

def crawl_site(url, crawl_depth=1, max_pages=30, delay=0.5):
    parsed_root = urlparse(url)
    base_domain = parsed_root.netloc
    to_visit = [(url, 0)]
    seen = set([url])
    found_emails = set()
    pages_processed = 0

    while to_visit and pages_processed < max_pages:
        current_url, cur_depth = to_visit.pop(0)
        pages_processed += 1
        try:
            resp = requests.get(current_url, headers=HEADERS, timeout=10, verify=False)
            html = resp.text
        except:
            continue

        found_emails.update(extract_emails_from_html(html))

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
        time.sleep(delay)

    return url, found_emails

# ---------------------
# Streamlit UI
# ---------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

# Header
st.markdown("""
<div style="margin-bottom:20px;">
<h1 st
