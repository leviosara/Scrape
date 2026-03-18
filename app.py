import streamlit as st
import requests
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
import re
from datetime import datetime, timedelta
import feedparser

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
MAX_SLOW_CHECKS = 50  # Limit deep content scans
MAX_SITEMAP_FILES = 5 # Stop after scanning 5 sitemap files (speed up)
MAX_URLS = 1000       # Stop after finding 1000 links
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
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

def get_date_from_html(html):
    if not html: return None
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.date: return dateparser.parse(metadata.date)
    except: pass
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match: return dateparser.parse(match.group(1))
    return None

# --- SCANNING STRATEGIES ---

def scan_rss_feeds(base_url, log_box):
    log_box.text("⏳ Step 1: Checking RSS Feeds (Fastest)...")
    found = {}
    paths = [f"{base_url}/feed/", f"{base_url}/rss/", f"{base_url}/en/feed/"]
    
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
                        log_box.text(f
