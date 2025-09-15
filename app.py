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
