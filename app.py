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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Optional DNS resolver
try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

# ---------------------
# Config & constants
# ---------------------
# stricter regex (letters/numbers, dots, hyphen, TLD at least 2 letters)
EMAIL_REGEX = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', re.I)
# skip emails that end with typical file extensions or look like images
SKIP_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico')
# excluded keywords
EXCLUDED_KEYWORDS = ["support@", "press@", "privacy@"]

HEADERS = {"User-Agent": "EmailExtractor/1.0 (+https://example.com)"}
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# concurrency (tune this: higher = faster but more load)
MAX_CRAWL_WORKERS = 20
MAX_VERIFY_WORKERS = 20

# requests session with retries (faster & more robust)
session = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500,502,503,504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)

# ---------------------
# Helper utilities
# ---------------------
def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url

def resolve_url(url):
    """Resolve short URLs -> final URL; ignore SSL verification issues."""
    try:
        r = session.head(url, allow_redirects=True, headers=HEADERS, timeout=8, verify=False)
        final = r.url or url
        # quick GET try to warm up
        try:
            session.get(final, headers=HEADERS, timeout=8, verify=False)
        except:
            pass
        return final
    except Exception:
        return url

def looks_like_valid_email(e: str) -> bool:
    """Extra filtering to drop junk like image@2x.png or emails with excluded keywords."""
    if not e or " " in e:
        return False
    el = e.lower().strip()
    # exclude junk extensions in the domain or local-part ending with image suffix
    if any(el.endswith(ext) for ext in SKIP_EXTENSIONS):
        return False
    # exclude typical tokens
    if any(k in el for k in EXCLUDED_KEYWORDS):
        return False
    # minimal regex match
    return EMAIL_REGEX.fullmatch(e) is not None

def extract_emails_from_html(html: str) -> set:
    found = set()
    if not html:
        return found
    # regex over raw html/text
    for m in set(EMAIL_REGEX.findall(html)):
        candidate = m.lower()
        if looks_like_valid_email(candidate):
            found.add(candidate)
    # parse mailto: links (more reliable)
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a['href']
            if href.lower().startswith("mailto:"):
                email = href.split("mailto:", 1)[1].split('?')[0].strip().lower()
                if looks_like_valid_email(email):
                    found.add(email)
            # some sites put emails in data attributes or text nodes; check common attributes
            # (we don't exhaustively parse JS-rendered content)
    except Exception:
        pass
    return found

# ---------------------
# Verification helpers (two modes)
# ---------------------
def verify_mx(domain: str) -> bool:
    """Return True if MX record exists for domain (fast)."""
    if not DNS_AVAILABLE:
        return False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False

def verify_smtp_rcpt(email: str) -> bool:
    """Attempt RCPT TO on domain MX hosts. Slow and unreliable; use only when requested."""
    if not DNS_AVAILABLE:
        return False
    domain = email.split("@", 1)[1]
    try:
        mx_records = dns.resolver.resolve(domain, "MX", lifetime=5)
        mx_hosts = [str(r.exchange).rstrip('.') for r in mx_records]
    except Exception:
        return False

    for mx in mx_hosts:
        try:
            server = smtplib.SMTP(timeout=6)
            server.connect(mx)
            server.helo()
            server.mail("verify@" + "example.com")
            code, resp = server.rcpt(email)
            server.quit()
            # 250 = accepted, 550 = mailbox not found; treat 250 as success
            if isinstance(code, int) and code == 250:
                return True
        except Exception:
            continue
    return False

def verify_email_worker(email: str, mode: str):
    """mode: 'none', 'mx', 'smtp'"""
    if mode == "none":
        return email, "Skipped"
    domain = email.split("@", 1)[1]
    if mode == "mx":
        ok = verify_mx(domain)
        return email, ("Valid" if ok else "Invalid")
    if mode == "smtp":
        # first mx quick check
        if not verify_mx(domain):
            return email, "Invalid"
        # then RCPT attempt (slow)
        ok = verify_smtp_rcpt(email)
        return email, ("Valid" if ok else "Unknown")
    return email, "Skipped"

# ---------------------
# Crawl single site (used in ThreadPool)
# ---------------------
def crawl_site(url, crawl_depth=1, max_pages=30, delay=0.2):
    parsed = urlparse(url)
    base_domain = parsed.netloc
    to_visit = [(url, 0)]
    seen = set([url])
    found_emails = set()
    pages = 0

    while to_visit and pages < max_pages:
        current, depth = to_visit.pop(0)
        pages += 1
        try:
            r = session.get(current, headers=HEADERS, timeout=10, verify=False)
            html = r.text
        except Exception:
            continue

        found_emails.update(extract_emails_from_html(html))

        if depth < crawl_depth:
            try:
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a['href'].strip()
                    joined = urljoin(current, href)
                    p = urlparse(joined)
                    if p.scheme not in ("http", "https"):
                        continue
                    if p.netloc != base_domain:
                        continue
                    norm = p._replace(fragment="").geturl()
                    if norm not in seen:
                        seen.add(norm)
                        to_visit.append((norm, depth + 1))
            except Exception:
                pass

        time.sleep(delay)

    return url, found_emails

# ---------------------
# Streamlit UI
# ---------------------
st.set_page_config(page_title="Email Extractor", layout="wide")

st.markdown("""
<div style="margin-bottom:20px;">
<h1 style="color:#1F2328;">📧 Email Extractor</h1>
<p style="color:#333; font-size:14px;">Paste website URLs (one per line). Fast parallel crawling and optional verification (MX or MX+RCPT).</p>
</div>
""", unsafe_allow_html=True)

with st.container():
    col1, col2 = st.columns([3, 1])
    with col1:
        urls_input = st.text_area("Enter website URLs (one per line)", height=250)
    with col2:
        crawl_depth = st.slider("Crawl depth (0=homepage)", 0, 1, 1)
        max_pages = st.number_input("Max pages per site", 1, 200, 30)
        delay = st.number_input("Delay between requests (seconds)", 0.0, 5.0, 0.2, 0.1)
        verify_mode = st.selectbox("Verify emails", options=["None", "MX only (fast)", "MX+RCPT (slow)"], index=0)
        st.markdown("<small style='color:#666'>MX+RCPT will be much slower and may be blocked by servers.</small>", unsafe_allow_html=True)

st.markdown("---")

if st.button("🚀 Extract Emails"):
    # build normalized, resolved site list
    websites = []
    for u in urls_input.splitlines():
        n = normalize_url(u)
        if n:
            websites.append(resolve_url(n))

    if not websites:
        st.warning("Please enter at least one URL.")
    else:
        st.info(f"⏳ Starting extraction from {len(websites)} website(s)...")
        all_results = {}
        total_emails = 0

        # parallel crawling
        with ThreadPoolExecutor(max_workers=MAX_CRAWL_WORKERS) as executor:
            futures = {executor.submit(crawl_site, url, crawl_depth, max_pages, delay): url for url in websites}
            for fut in as_completed(futures):
                url, emails = fut.result()
                # filter out excluded and junk once
                cleaned = {e for e in emails if looks_like_valid_email(e)}
                all_results[url] = cleaned
                total_emails += len(cleaned)

        st.subheader("📋 Extracted Emails per Website (raw, pre-verified)")
        # show results and build a flat set of unique emails
        unique_emails = set()
        for site, emails in all_results.items():
            if emails:
                st.markdown(f"**{site}**")
                unique_emails.update(emails)
                df_view = pd.DataFrame(sorted(emails), columns=["Email"])
                st.dataframe(df_view, height=min(350, 28 * len(emails)))
            else:
                st.markdown(f"**{site}** → No emails found.")

        # Verification step (parallel) if requested
        verify_choice = verify_mode  # "None" / "MX only (fast)" / "MX+RCPT (slow)"
        mode = "none"
        if verify_choice == "MX only (fast)":
            mode = "mx"
        elif verify_choice == "MX+RCPT (slow)":
            mode = "smtp"

        verified_map = {}  # email -> status ("Valid"/"Invalid"/"Unknown"/"Skipped")
        if mode != "none" and unique_emails:
            st.info("🔎 Verifying emails (this may take some time)...")
            # verify in parallel per unique email
            with ThreadPoolExecutor(max_workers=MAX_VERIFY_WORKERS) as vexec:
                v_futures = {vexec.submit(verify_email_worker, e, mode): e for e in unique_emails}
                for vf in as_completed(v_futures):
                    email, status = vf.result()
                    verified_map[email] = status
        else:
            # all skipped
            for e in unique_emails:
                verified_map[e] = "Skipped"

        # Build final per-site data with verification status cached
        st.subheader("📊 Final Results (with verification)")
        total_verified_count = 0
        for site, emails in all_results.items():
            if emails:
                rows = []
                for e in sorted(emails):
                    status = verified_map.get(e, "Skipped")
                    rows.append({"Email": e, "Verified": status})
                    if status == "Valid":
                        total_verified_count += 1
                df_final = pd.DataFrame(rows)
                st.markdown(f"**{site}**")
                st.dataframe(df_final, height=min(400, 30 * len(rows)))
            else:
                st.markdown(f"**{site}** → No emails found.")

        # CSV export (prepared once)
        if unique_emails:
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["website", "email", "verified"])
            for site, emails in all_results.items():
                for e in sorted(emails):
                    writer.writerow([site, e, verified_map.get(e, "Skipped")])
            csv_bytes = csv_buffer.getvalue().encode("utf-8")

            # spacing so UI doesn't overlap
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.download_button("📥 Download all emails (CSV)", data=csv_bytes, file_name="emails.csv", mime="text/csv")

        # completion notification
        st.balloons()
        st.success(f"🎉 Extraction completed! Total unique emails found: {len(unique_emails)} — Valid: {sum(1 for v in verified_map.values() if v == 'Valid')}")
        st.info("💡 Done by Shafiq Sanchy")

        # browser notification + sound
        js_code = f"""
        <script>
        function notifyMe() {{
            if (!("Notification" in window)) {{
                alert("Extraction done! Total emails: {len(unique_emails)}");
                return;
            }}
            if (Notification.permission !== "granted") Notification.requestPermission();
            if (Notification.permission === "granted") {{
                new Notification("Email Extractor", {{
                    body: "Done! {len(unique_emails)} unique emails, Valid: {sum(1 for v in verified_map.values() if v == 'Valid')}",
                    icon: "https://cdn-icons-png.flaticon.com/512/561/561127.png"
                }});
            }}
            var audio = new Audio("https://www.soundjay.com/buttons/sounds/beep-07.mp3");
            audio.play();
        }}
        notifyMe();
        </script>
        """
        import streamlit.components.v1 as components
        components.html(js_code, height=0, width=0)

# footer
st.markdown("""
<div style="padding:10px; border-top:0px solid #ccc; margin-top:50px; text-align:center; font-size:14px; color:#555;">
© Shafiq Sanchy 2025
</div>
""", unsafe_allow_html=True)
