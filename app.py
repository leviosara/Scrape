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
    # Use a set to avoid processing the same sitemap twice
    processed_sitemaps = set()

    while sitemaps:
        sm = sitemaps.pop(0)
        if sm in processed_sitemaps:
            continue
        processed_sitemaps.add(sm)

        try:
            r = requests.get(sm, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            # Check if it is a Sitemap Index (contains links to other sitemaps)
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    # CORRECTED LINE: Extract loc text properly
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc:
                            sitemaps.append(loc)
            
            # Check if it is a URL Set (contains actual page links)
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    # CORRECTED LINE: Extract loc text properly
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc:
                            found_urls.append(loc)
                            
        except Exception as e:
            # Ignore errors for individual sitemaps
            continue

    return found_urls
