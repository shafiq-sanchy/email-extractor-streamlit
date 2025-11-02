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

# --- Constants ---
MAX_CONCURRENT_REQUESTS = 20
REQUEST_TIMEOUT = 15 # Increased timeout slightly
CRAWL_DEPTH = 1 
CONTACT_KEYWORDS = [
    'contact', 'about', 'contact-us', 'support', 'get-in-touch', 'reach-us', 'team', 'kontakt', 'contato', 'contatti', 
    'contacto', 'kontak', 'hubungi', 'liên hệ', '연락처', 'お問い合わせ'
]
SKIP_PATH_KEYWORDS = ['blog', 'post', 'article', 'news', 'tag', 'category', 'product', 'shop', 'wp-json', 'feed']
DEFAULT_MAX_URLS_PER_DOMAIN = 30 
MAX_QUEUE_SIZE_PER_DOMAIN = 30
BATCH_SIZE = 20

# --- User-Agent Header to prevent blocking ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- File and Session State Management ---
RESULTS_DIR = "temp_results"
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

def save_results_to_file(results, failed, timeout, file_id):
    # results is a dict {email: source_url}
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
        'is_running': False, 'stop_extraction': False, 'extraction_complete': False, 'result_file
