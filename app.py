import streamlit as st
import requests
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import pandas as pd
import dateparser
import re
from datetime import datetime, timedelta
import feedparser

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url.rstrip('/')

def make_naive(dt):
    if dt is None: return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def find_date_in_url(url):
    # Matches YYYY/MM/DD or YYYY-MM-DD
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

# --- SCANNING STRATEGIES (NO SLOW DOWNLOADS) ---

def scan_rss_feeds(base_url, log_box):
    log_box.text("⏳ Step 1: Checking RSS Feeds...")
    found = {}
    paths = [f"{base_url}/feed/", f"{base_url}/rss/", f"{base_url}/en/feed/", f"{base_url}/uk/feed/"]
    
    for path in paths:
        try:
            r = requests.get(path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                if feed.entries:
                    for entry in feed.entries:
                        link = entry.get('link')
                        published = entry.get('published_parsed') or entry.get('updated_parsed')
                        if link and published:
                            dt = datetime(*published[:6])
                            found[link] = dt
                    if found:
                        log_box.text(f"✅ RSS: Found {len(found)} articles.")
                        return found
        except: continue
    return found

def scan_sitemaps_fast(base_url, log_box):
    log_box.text("⏳ Step 2: Scanning Sitemaps (Fast Mode)...")
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    
    # We prioritize specific sitemaps that usually have dates
    priority_paths = [
        "/post-sitemap.xml", "/news-sitemap.xml", "/sitemap-news.xml",
        "/sitemap.xml", "/sitemap_index.xml"
    ]
    
    found = {}
    
    for path in priority_paths:
        try:
            r = requests.get(domain + path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            # If it's an index, we just grab the first few links to other sitemaps
            if 'sitemapindex' in str(root.tag).lower():
                # Quick check: does the index list other sitemaps?
                # We will just check the FIRST 3 child sitemaps found in the index
                count = 0
                for child in root:
                    if count >= 3: break # Strict limit for speed
                    loc = [c.text for c in child if 'loc'
