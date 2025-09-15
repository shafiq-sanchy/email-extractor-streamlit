# app.py
import re
import time
import csv
import io
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import streamlit as st

EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)
HEADERS = {"User-Agent": "EmailExtractor/1.0"}

st.set_page_config(page_title="Email Extractor", layout="wide")
st.title("📧 Multi-Site Email Extractor with Crawler")

urls_input = st.text_area(
    "Enter website URLs (one per line)",
    "https://example.com\nhttps://www.python.org"
)

crawl_depth = st.slider("Crawl depth (0 = only homepage)", 0, 3, 1)
