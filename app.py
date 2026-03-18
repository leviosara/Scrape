import streamlit as st
import requests
from urllib.parse import urlparse
import trafilatura
import dateparser
import pandas as pd
from datetime import datetime, date, timedelta
import xml.etree.ElementTree as ET
import feedparser

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7

# --- CORE FUNCTIONS ---

def get_sitemap_urls(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml"]
    sitemaps = []
    found_urls = []
    
    # 1. Find sitemap
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
                break
        except: continue

    # Fallback to robots.txt
    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except: pass

    # 2. Parse Sitemap
    for sm in sitemaps:
        try:
            r = requests.get(sm, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    loc = [c.text for c in child if 'loc' in c
