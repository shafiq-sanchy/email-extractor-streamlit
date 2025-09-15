# app.py
import requests
import re
from bs4 import BeautifulSoup
import streamlit as st

EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

st.title("Email Extractor")

url = st.text_input("Enter a website URL", "https://example.com")

if st.button("Extract Emails"):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text()
        emails = set(EMAIL_REGEX.findall(text))

        if emails:
            st.success(f"Found {len(emails)} emails:")
            for email in sorted(emails):
                st.write(email)
        else:
            st.warning("No emails found.")
    except Exception as e:
        st.error(f"Error: {e}")
