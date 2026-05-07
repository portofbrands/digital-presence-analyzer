"""Digital Presence Analyzer — MVP

Kör:
    pip install -r requirements.txt
    streamlit run app.py
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote_plus

import requests
import streamlit as st
from bs4 import BeautifulSoup


def _load_api_key() -> str | None:
    try:
        if "pagespeed_api_key" in st.secrets:
            return st.secrets["pagespeed_api_key"]
    except Exception:
        pass
    return os.environ.get("PAGESPEED_API_KEY")

UA = "Mozilla/5.0 (compatible; DigitalPresenceAnalyzer/1.0)"
TIMEOUT = 20


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@st.cache_data(ttl=600, show_spinner=False)
def get_pagespeed(url: str, api_key: str | None, strategy: str = "mobile") -> dict:
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "accessibility", "best-practices", "seo"],
    }
    if api_key:
        params["key"] = api_key
    r = requests.get(endpoint, params=params, timeout=90)
    if r.status_code == 429:
        raise RuntimeError("RATE_LIMITED")
    r.raise_for_status()
    return r.json()


def parse_pagespeed(data: dict) -> dict:
    lh = data.get("lighthouseResult", {})
    cats = lh.get("categories", {})
    audits = lh.get("audits", {})

    scores = {
        name: int((cats.get(name, {}).get("score") or 0) * 100)
        for name in ("performance", "accessibility", "best-practices", "seo")
    }

    metric_keys = {
        "first-contentful-paint": "First Contentful Paint",
        "largest-contentful-paint": "Largest Contentful Paint",
        "total-blocking-time": "Total Blocking Time",
        "cumulative-layout-shift": "Cumulative Layout Shift",
        "speed-index": "Speed Index",
        "interactive": "Time to Interactive",
    }
    metrics = []
    for key, label in metric_keys.items():
        a = audits.get(key) or {}
        if a.get("displayValue") is not None:
            metrics.append({
                "id": key, "label": label,
                "value": a.get("displayValue", "—"),
                "score": a.get("score"),
            })

    screenshot = None
    fs = (audits.get("final-screenshot") or {}).get("details") or {}
    if fs.get("data"):
        screenshot = fs["data"]

    opportunities = []
    for audit in audits.values():
        details = audit.get("details", {}) or {}
        savings = details.get("overallSavingsMs", 0) or 0
        if details.get("type") == "opportunity" and savings > 100:
            opportunities.append(
                {"title": audit.get("title"), "savings_ms": int(savings),
                 "description": audit.get("description", "")}
            )
    opportunities.sort(key=lambda x: -x["savings_ms"])

    failed_audits = []
    for key, audit in audits.items():
        score = audit.get("score")
        if score is not None and score < 0.9 and audit.get("title"):
            if details := audit.get("details", {}):
                if details.get("type") in ("opportunity", "diagnostic"):
                    continue
            failed_audits.append({"id": key, "title": audit.get("title")})

    return {"scores": scores, "metrics": metrics, "screenshot": screenshot,
            "opportunities": opportunities[:8],
            "failed_audits": failed_audits[:10]}


def _score_color(score: float | int | None, thresholds=(0.5, 0.9)) -> str:
    if score is None:
        return "#9aa0a6"
    s = score / 100 if score > 1 else score
    if s >= thresholds[1]:
        return "#0cce6b"
    if s >= thresholds[0]:
        return "#ffa400"
    return "#ff4e42"


def _gauge_svg(score: int, label: str, size: int = 160) -> str:
    color = _score_color(score)
    radius = size / 2 - 8
    circumference = 2 * 3.14159 * radius
    pct = max(0, min(score, 100)) / 100
    dash = circumference * pct
    gap = circumference - dash
    half = size / 2
    text_y = half + size / 12
    return (
        f'<div style="display:inline-block;text-align:center;margin:0 16px;">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{half}" cy="{half}" r="{radius}" fill="none" '
        f'stroke="{color}22" stroke-width="8"/>'
        f'<circle cx="{half}" cy="{half}" r="{radius}" fill="none" '
        f'stroke="{color}" stroke-width="8" '
        f'stroke-dasharray="{dash} {gap}" stroke-linecap="round" '
        f'transform="rotate(-90 {half} {half})"/>'
        f'<text x="{half}" y="{text_y}" text-anchor="middle" '
        f'font-size="{size/3}" font-weight="700" fill="{color}" '
        f'font-family="Playfair Display, Georgia, serif">{score}</text>'
        f'</svg>'
        f'<div style="font-size:15px;margin-top:-2px;color:#1b2632;font-weight:600;">{label}</div>'
        f'</div>'
    )


def _metric_card(label: str, value: str, score: float | None) -> str:
    color = _score_color(score)
    return (
        f'<div style="border-left:4px solid {color};padding:14px 20px;'
        f'margin:10px 0;background:#ffffff;border-radius:8px;'
        f'box-shadow:0 1px 3px rgba(27,38,50,0.04);">'
        f'<div style="font-size:13px;color:#5a6470;text-transform:uppercase;'
        f'letter-spacing:0.8px;font-weight:600;">{label}</div>'
        f'<div style="font-size:28px;color:{color};font-weight:700;'
        f'margin-top:4px;font-family:Playfair Display,Georgia,serif;">{value}</div>'
        f'</div>'
    )


def _hero_html(domain: str, overall: int) -> str:
    color = _score_color(overall)
    grade = "Utmärkt" if overall >= 90 else "Bra" if overall >= 70 else \
            "OK" if overall >= 50 else "Behöver förbättring"
    return (
        f'<div style="background:#ffffff;'
        f'border:1px solid rgba(27,38,50,0.1);border-left:6px solid {color};'
        f'border-radius:12px;padding:36px 40px;margin:16px 0 32px;'
        f'box-shadow:0 2px 8px rgba(27,38,50,0.04);">'
        f'<div style="display:flex;align-items:center;gap:32px;flex-wrap:wrap;">'
        f'<div style="font-size:84px;font-weight:800;color:{color};'
        f'line-height:1;font-family:Playfair Display,Georgia,serif;">{overall}</div>'
        f'<div>'
        f'<div style="font-size:13px;color:#5a6470;text-transform:uppercase;'
        f'letter-spacing:1.5px;font-weight:600;">Totalpoäng — {domain}</div>'
        f'<div style="font-size:26px;font-weight:700;color:#1b2632;'
        f'margin-top:6px;font-family:Playfair Display,Georgia,serif;">{grade}</div>'
        f'<div style="font-size:15px;color:#5a6470;margin-top:8px;">'
        f'Baserat på prestanda, SEO och digital närvaro</div>'
        f'</div></div></div>'
    )


def _section_card(title: str, body: str) -> str:
    return (
        f'<div style="border:1px solid rgba(127,127,127,0.2);border-radius:10px;'
        f'padding:18px 22px;margin:8px 0;">'
        f'<div style="font-size:14px;color:#888;text-transform:uppercase;'
        f'letter-spacing:0.8px;margin-bottom:8px;">{title}</div>'
        f'{body}</div>'
    )


def scrape_site(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.text.strip() if title_tag else None

    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = desc_tag.get("content", "").strip() if desc_tag else None

    og_tags = {t.get("property"): t.get("content")
               for t in soup.find_all("meta") if t.get("property", "").startswith("og:")}

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = soup.find_all("h2")
    images = soup.find_all("img")
    images_no_alt = [img for img in images if not img.get("alt")]

    issues = []
    if not title:
        issues.append("❌ Saknar <title>-tagg")
    elif len(title) > 60:
        issues.append(f"⚠️ Title är {len(title)} tecken (rekommenderat ≤60)")
    elif len(title) < 20:
        issues.append(f"⚠️ Title är kort ({len(title)} tecken)")

    if not description:
        issues.append("❌ Saknar meta description")
    elif len(description) > 160:
        issues.append(f"⚠️ Meta description är {len(description)} tecken (≤160)")
    elif len(description) < 50:
        issues.append(f"⚠️ Meta description är kort ({len(description)} tecken)")

    if not h1s:
        issues.append("❌ Saknar H1-rubrik")
    elif len(h1s) > 1:
        issues.append(f"⚠️ {len(h1s)} H1-taggar (bör vara exakt 1)")

    if images and len(images_no_alt) / len(images) > 0.2:
        issues.append(f"⚠️ {len(images_no_alt)}/{len(images)} bilder saknar alt-text")

    if not og_tags.get("og:image"):
        issues.append("⚠️ Saknar Open Graph-bild (og:image) — sämre vid delning")

    if not url.startswith("https://"):
        issues.append("❌ Använder inte HTTPS")

    if not soup.find("link", attrs={"rel": "canonical"}):
        issues.append("⚠️ Saknar canonical-tag")

    if not soup.find("html").get("lang") if soup.find("html") else True:
        issues.append("⚠️ Saknar lang-attribut på <html>")

    socials = find_social_links(r.text)

    return {
        "title": title, "title_len": len(title) if title else 0,
        "description": description,
        "description_len": len(description) if description else 0,
        "h1_count": len(h1s), "h1_first": h1s[0] if h1s else None,
        "h2_count": len(h2s),
        "images_total": len(images), "images_no_alt": len(images_no_alt),
        "og_tags": len(og_tags),
        "issues": issues, "socials": socials,
        "status_code": r.status_code,
    }


def find_social_links(html: str) -> dict[str, str]:
    patterns = {
        "Facebook": r"https?://(?:www\.)?facebook\.com/[\w.\-/]+",
        "Instagram": r"https?://(?:www\.)?instagram\.com/[\w.\-]+",
        "LinkedIn": r"https?://(?:www\.)?linkedin\.com/(?:company|in|school)/[\w.\-]+",
        "Twitter/X": r"https?://(?:www\.)?(?:twitter|x)\.com/[\w.\-]+",
        "YouTube": r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|user/|@)[\w.\-]+",
        "TikTok": r"https?://(?:www\.)?tiktok\.com/@[\w.\-]+",
    }
    found = {}
    for name, pat in patterns.items():
        m = re.search(pat, html)
        if m:
            link = m.group(0).rstrip("/\"'")
            if "/sharer" not in link and "/share" not in link and "/intent" not in link:
                found[name] = link
    return found


def check_robots_sitemap(base_url: str) -> dict:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    out = {}
    for path in ("/robots.txt", "/sitemap.xml"):
        try:
            r = requests.get(root + path, headers={"User-Agent": UA}, timeout=10)
            out[path] = r.status_code == 200
        except Exception:
            out[path] = False
    return out


# ─── Tech-stack detection ────────────────────────────────────────────────────

TECH_RULES: list[tuple[str, str, str, str]] = [
    # (name, category, pattern_type, pattern)
    # E-commerce
    ("Shopify", "E-handel", "html", r"cdn\.shopify\.com|Shopify\.theme"),
    ("Shopify", "E-handel", "header", r"x-shopify"),
    ("WooCommerce", "E-handel", "html", r"woocommerce|wc-block"),
    ("Magento", "E-handel", "html", r"Mage\.Cookies|/skin/frontend/"),
    ("BigCommerce", "E-handel", "html", r"bigcommerce\.com|cdn11\.bigcommerce"),
    ("Squarespace Commerce", "E-handel", "html", r"squarespace-cdn\.com.*commerce"),
    ("Centra", "E-handel", "html", r"centra\.com|centraapi"),

    # CMS
    ("WordPress", "CMS", "html", r"/wp-content/|/wp-includes/|wp-json"),
    ("Drupal", "CMS", "html", r"Drupal\.settings|/sites/default/files/"),
    ("Webflow", "CMS", "html", r"webflow\.com|wf-loaded"),
    ("Wix", "CMS", "html", r"static\.wixstatic|wix-code"),
    ("Squarespace", "CMS", "html", r"squarespace\.com|static\.squarespace"),
    ("Sanity", "CMS", "html", r"cdn\.sanity\.io"),
    ("Contentful", "CMS", "html", r"cdn\.contentful\.com|images\.ctfassets"),

    # Frontend frameworks
    ("React", "Frontend", "html", r"__react|react-dom|_reactRoot"),
    ("Next.js", "Frontend", "html", r"/_next/|__NEXT_DATA__"),
    ("Vue.js", "Frontend", "html", r"vue\.js|__vue__|data-v-"),
    ("Nuxt", "Frontend", "html", r"__NUXT__|/_nuxt/"),
    ("Angular", "Frontend", "html", r"ng-version|angular\.js"),
    ("Svelte", "Frontend", "html", r"svelte-|/_app/immutable/"),
    ("Gatsby", "Frontend", "html", r"___gatsby|/page-data/"),

    # Analytics
    ("Google Analytics 4", "Analytics", "html",
     r"googletagmanager\.com/gtag/js\?id=G-|gtag\('config',\s*'G-"),
    ("Google Analytics (UA)", "Analytics", "html", r"google-analytics\.com/(ga|analytics)\.js|UA-\d{4,}"),
    ("Google Tag Manager", "Analytics", "html", r"googletagmanager\.com/gtm\.js"),
    ("Plausible", "Analytics", "html", r"plausible\.io/js"),
    ("Fathom", "Analytics", "html", r"cdn\.usefathom\.com"),
    ("Matomo", "Analytics", "html", r"matomo\.js|piwik\.js"),
    ("Hotjar", "Analytics", "html", r"static\.hotjar\.com|hjid:\s*\d"),
    ("Microsoft Clarity", "Analytics", "html", r"clarity\.ms/tag"),
    ("Mixpanel", "Analytics", "html", r"cdn\.mxpnl\.com"),

    # Marketing & email
    ("Klaviyo", "E-postmarknadsföring", "html", r"klaviyo\.com|static\.klaviyo"),
    ("Mailchimp", "E-postmarknadsföring", "html", r"chimpstatic\.com|list-manage\.com"),
    ("HubSpot", "Marketing", "html", r"js\.hs-scripts\.com|hubspot"),
    ("ActiveCampaign", "E-postmarknadsföring", "html", r"trackcmp\.net"),
    ("Drip", "E-postmarknadsföring", "html", r"getdrip\.com"),

    # Ads / pixels
    ("Facebook Pixel", "Annonspixlar", "html", r"connect\.facebook\.net.*fbevents\.js|fbq\("),
    ("TikTok Pixel", "Annonspixlar", "html", r"analytics\.tiktok\.com|ttq\.load"),
    ("Pinterest Tag", "Annonspixlar", "html", r"s\.pinimg\.com/ct/core"),
    ("LinkedIn Insight", "Annonspixlar", "html", r"snap\.licdn\.com/li\.lms"),
    ("Snap Pixel", "Annonspixlar", "html", r"sc-static\.net/scevent"),
    ("Google Ads Conversion", "Annonspixlar", "html", r"googleadservices\.com/pagead/conversion"),

    # CDN & hosting
    ("Cloudflare", "CDN/Hosting", "header", r"cf-ray|cloudflare"),
    ("Vercel", "CDN/Hosting", "header", r"x-vercel"),
    ("Netlify", "CDN/Hosting", "header", r"x-nf-request-id|netlify"),
    ("AWS CloudFront", "CDN/Hosting", "header", r"x-amz-cf-id"),
    ("Fastly", "CDN/Hosting", "header", r"x-served-by.*fastly|fastly-"),

    # CSS / UI
    ("Tailwind CSS", "Frontend", "html", r"tailwindcss|--tw-"),
    ("Bootstrap", "Frontend", "html", r"bootstrap(\.min)?\.css|bootstrap@"),

    # Chat / support
    ("Intercom", "Kundtjänst", "html", r"widget\.intercom\.io"),
    ("Drift", "Kundtjänst", "html", r"drift\.com|js\.driftt\.com"),
    ("Zendesk Chat", "Kundtjänst", "html", r"zdassets\.com|zopim"),
    ("Crisp", "Kundtjänst", "html", r"client\.crisp\.chat"),
    ("Tawk.to", "Kundtjänst", "html", r"embed\.tawk\.to"),

    # Payment
    ("Klarna", "Betalning", "html", r"klarna\.com|cdn\.klarna"),
    ("Stripe", "Betalning", "html", r"js\.stripe\.com"),
    ("PayPal", "Betalning", "html", r"www\.paypal\.com/sdk"),

    # Cookie / consent
    ("CookieBot", "Consent", "html", r"consent\.cookiebot\.com"),
    ("OneTrust", "Consent", "html", r"cdn\.cookielaw\.org|onetrust"),
    ("Cookie Information", "Consent", "html", r"policy\.app\.cookieinformation"),
]


def detect_technologies(url: str) -> dict:
    """Detect tech stack from HTML, headers and cookies."""
    found: dict[str, dict] = {}
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        html = r.text.lower()
        headers_blob = "\n".join(f"{k}: {v}" for k, v in r.headers.items()).lower()
        for name, category, ptype, pattern in TECH_RULES:
            blob = html if ptype == "html" else headers_blob
            if re.search(pattern.lower(), blob):
                if name not in found:
                    found[name] = {"category": category}
        # Generator meta tag (often reveals CMS/framework)
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            gen = soup.find("meta", attrs={"name": "generator"})
            if gen and gen.get("content"):
                content = gen["content"]
                if not any(k in content for k in found.keys()):
                    found[content[:60]] = {"category": "Generator"}
        except Exception:
            pass
    except Exception as e:
        return {"error": str(e), "techs": {}}

    by_category: dict[str, list[str]] = {}
    for name, meta in found.items():
        by_category.setdefault(meta["category"], []).append(name)
    return {"techs": found, "by_category": by_category}


# ─── Site crawler ────────────────────────────────────────────────────────────

SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
                   ".pdf", ".zip", ".mp4", ".mp3", ".css", ".js",
                   ".ico", ".woff", ".woff2", ".ttf")


def fetch_sitemap_urls(base_url: str, max_urls: int = 50) -> list[str]:
    """Hämta URLer från sitemap.xml — följer sitemap index och gzip-varianter."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc.replace("www.", "")
    urls: list[str] = []
    seen_sitemaps: set[str] = set()
    queue = [
        root + "/sitemap.xml",
        root + "/sitemap_index.xml",
        root + "/wp-sitemap.xml",
    ]
    while queue and len(urls) < max_urls:
        sm_url = queue.pop(0)
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)
        try:
            r = requests.get(sm_url, headers={"User-Agent": UA}, timeout=12)
            if r.status_code != 200 or "<urlset" not in r.text and "<sitemapindex" not in r.text:
                continue
            soup = BeautifulSoup(r.text, "xml")
            for sm in soup.find_all("sitemap"):
                loc = sm.find("loc")
                if loc and loc.text.strip() not in seen_sitemaps:
                    queue.append(loc.text.strip())
            for u in soup.find_all("url"):
                loc = u.find("loc")
                if not loc:
                    continue
                page_url = loc.text.strip()
                if domain not in page_url:
                    continue
                if page_url.lower().endswith(SKIP_EXTENSIONS):
                    continue
                urls.append(page_url)
                if len(urls) >= max_urls:
                    break
        except Exception:
            continue
    seen, dedup = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup[:max_urls]


def _crawl_one(url: str, timeout: int = 10) -> dict:
    try:
        t0 = time.time()
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout,
                         allow_redirects=True)
        elapsed_ms = int((time.time() - t0) * 1000)
        ctype = r.headers.get("content-type", "")
        size_kb = len(r.content) // 1024
        title, description, h1_count = None, None, 0
        if "text/html" in ctype:
            soup = BeautifulSoup(r.text, "html.parser")
            t = soup.find("title")
            title = t.get_text(strip=True) if t else None
            d = soup.find("meta", attrs={"name": "description"})
            description = d.get("content", "").strip() if d else None
            h1_count = len(soup.find_all("h1"))
        return {
            "url": url, "status": r.status_code, "elapsed_ms": elapsed_ms,
            "size_kb": size_kb, "title": title,
            "description": description, "h1_count": h1_count,
            "final_url": r.url,
        }
    except requests.exceptions.Timeout:
        return {"url": url, "status": 0, "error": "timeout"}
    except Exception as e:
        return {"url": url, "status": 0, "error": str(e)[:60]}


def crawl_pages(urls: list[str], concurrency: int = 8) -> list[dict]:
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_crawl_one, u): u for u in urls}
        for f in as_completed(futures):
            results.append(f.result())
    return results


def analyze_crawl(results: list[dict]) -> dict:
    successful = [r for r in results if 200 <= r.get("status", 0) < 400]
    broken = [r for r in results if r.get("status", 0) == 0
              or r.get("status", 0) >= 400]
    redirects = [r for r in results
                 if 300 <= r.get("status", 0) < 400
                 or (r.get("final_url") and r.get("final_url") != r.get("url"))]

    no_desc = [r for r in successful if not r.get("description")]
    no_h1 = [r for r in successful if r.get("h1_count", 0) == 0]
    multi_h1 = [r for r in successful if r.get("h1_count", 0) > 1]

    title_groups: dict[str, list[str]] = {}
    for r in successful:
        if r.get("title"):
            title_groups.setdefault(r["title"], []).append(r["url"])
    dup_titles = [{"title": t, "urls": us}
                  for t, us in title_groups.items() if len(us) > 1]

    avg_ms = (int(sum(r["elapsed_ms"] for r in successful) / len(successful))
              if successful else 0)
    slow = sorted(
        [r for r in successful if r.get("elapsed_ms")],
        key=lambda x: -x["elapsed_ms"],
    )[:5]
    largest = sorted(
        [r for r in successful if r.get("size_kb")],
        key=lambda x: -x["size_kb"],
    )[:5]

    return {
        "total": len(results),
        "successful": len(successful),
        "broken": broken,
        "redirects": redirects,
        "no_description": no_desc,
        "no_h1": no_h1,
        "multi_h1": multi_h1,
        "duplicate_titles": dup_titles,
        "avg_ms": avg_ms,
        "slow": slow,
        "largest": largest,
    }


def ad_library_links(domain: str, company: str, country: str = "SE") -> dict:
    return {
        "Meta Ad Library": (
            "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
            f"&country={country}&q={quote_plus(company)}&search_type=keyword_unordered"
        ),
        "Google Ads Transparency": (
            f"https://adstransparency.google.com/?region={country}"
            f"&domain={quote_plus(domain)}"
        ),
        "TikTok Creative Center": (
            f"https://library.tiktok.com/ads?region={country}&adv_name={quote_plus(company)}"
        ),
    }


def score_overall(seo_issues: int, perf_score: int, social_count: int) -> int:
    seo = max(0, 100 - seo_issues * 10)
    social = min(100, social_count * 25)
    return int((seo + perf_score + social) / 3)


# ─── Ad scraping (Playwright) ────────────────────────────────────────────────

UA_DESKTOP = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

COOKIE_SELECTORS = [
    'button[data-cookiebanner="accept_only_essential_button"]',
    'button[title*="Endast nödvändiga"]',
    'button:has-text("Endast nödvändiga cookies")',
    'button:has-text("Decline optional cookies")',
    'button:has-text("Allow all cookies")',
    'button:has-text("Accept all")',
    'button:has-text("Acceptera alla")',
    'button:has-text("Avvisa alla")',
    'button[aria-label*="cookie" i]',
]


def _dismiss_cookies(page) -> None:
    for sel in COOKIE_SELECTORS:
        try:
            page.locator(sel).first.click(timeout=1500)
            return
        except Exception:
            continue


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


def scrape_meta_ads(query: str, country: str = "SE", max_ads: int = 6,
                    timeout_ms: int = 30000) -> dict:
    from playwright.sync_api import sync_playwright

    url = (
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        f"&country={country}&q={quote_plus(query)}&search_type=keyword_unordered"
    )
    result: dict = {"url": url, "count": None, "ads": [],
                    "screenshot": None, "error": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 1800},
                user_agent=UA_DESKTOP, locale="sv-SE",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            _dismiss_cookies(page)
            page.wait_for_timeout(4500)

            try:
                lib_id_loc = page.locator(
                    'text=/(Library ID|Bibliotek-id|ID:?\\s*\\d{10,})/i')
                count = lib_id_loc.count()
                result["count"] = count
                for i in range(min(count, max_ads)):
                    try:
                        card = lib_id_loc.nth(i).locator(
                            'xpath=ancestor::div[5]')
                        text = (card.inner_text(timeout=1500) or "")[:500]
                        img_src = None
                        try:
                            img_src = card.locator('img').first.get_attribute(
                                'src', timeout=800)
                        except Exception:
                            pass
                        result["ads"].append({"text": text, "image": img_src})
                    except Exception:
                        continue
            except Exception:
                pass

            try:
                page.evaluate("window.scrollTo(0, 400)")
                page.wait_for_timeout(800)
            except Exception:
                pass
            result["screenshot"] = page.screenshot(full_page=False)
            browser.close()
    except Exception as e:
        result["error"] = str(e)
    return result


def scrape_google_ads(domain: str, country: str = "SE",
                      timeout_ms: int = 30000) -> dict:
    from playwright.sync_api import sync_playwright

    url = (f"https://adstransparency.google.com/?region={country}"
           f"&domain={quote_plus(domain)}")
    result: dict = {"url": url, "advertisers": [], "ad_count": 0,
                    "screenshot": None, "error": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 1600},
                user_agent=UA_DESKTOP, locale="sv-SE",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            _dismiss_cookies(page)
            page.wait_for_timeout(5000)

            try:
                advertiser_links = page.locator('a[href*="/advertiser/"]').all()
                seen = set()
                for a in advertiser_links[:5]:
                    try:
                        href = a.get_attribute("href", timeout=800)
                        name = (a.inner_text(timeout=800) or "").strip()
                        if href and href not in seen:
                            seen.add(href)
                            full = href if href.startswith("http") else \
                                f"https://adstransparency.google.com{href}"
                            result["advertisers"].append(
                                {"name": name or domain, "url": full})
                    except Exception:
                        continue
            except Exception:
                pass

            if result["advertisers"]:
                try:
                    page.goto(result["advertisers"][0]["url"],
                              wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(4000)
                    creative_count = page.locator(
                        '[class*="creative"], creative-preview, '
                        'a[href*="/advertiser/"][href*="/creative/"]'
                    ).count()
                    result["ad_count"] = creative_count
                except Exception:
                    pass

            result["screenshot"] = page.screenshot(full_page=False)
            browser.close()
    except Exception as e:
        result["error"] = str(e)
    return result


# ─── UI ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Digital Presence Analyzer",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Playfair+Display:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
      html, body, .stApp {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        font-weight: 300;
        font-size: 16px;
      }
      .stApp p, .stApp [data-testid="stMarkdownContainer"] p {
        font-weight: 300;
        line-height: 1.6;
        font-size: 16px;
      }
      .stApp [data-testid="stCaptionContainer"],
      .stApp small { font-size: 14px !important; }
      [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"],
      [data-testid="stVerticalBlock"] > [data-testid="element-container"] {
        margin-bottom: 8px;
      }
      [data-testid="stHorizontalBlock"] { gap: 1.5rem !important; }
      .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
      .stApp [data-testid="stMarkdownContainer"] h1,
      .stApp [data-testid="stMarkdownContainer"] h2,
      .stApp [data-testid="stMarkdownContainer"] h3,
      .stApp .pob-serif {
        font-family: 'Playfair Display', Georgia, 'Times New Roman', serif !important;
        font-weight: 700 !important;
        letter-spacing: 0.5px !important;
        color: #1b2632 !important;
        line-height: 1.1 !important;
      }
      .stApp h1 { font-weight: 800 !important; }
      .stApp .pob-eyebrow {
        font-family: 'Inter', sans-serif !important;
        font-size: 14px;
        font-weight: 600;
        letter-spacing: 2.5px;
        text-transform: uppercase;
        color: #ffb162;
        margin-bottom: 16px;
      }
      .stApp [data-testid="stMetricValue"] {
        font-family: 'Playfair Display', Georgia, serif !important;
        font-weight: 700;
      }
      .block-container { padding-top: 2.5rem; max-width: 1100px; }
      .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background: #ffffff;
        padding: 6px;
        border-radius: 10px;
        border: 1px solid rgba(27,38,50,0.08);
      }
      .stTabs [data-baseweb="tab-list"] button[data-baseweb="tab"] {
        padding: 12px 22px !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 15px !important;
        color: #5a6470 !important;
        background: transparent !important;
        border: none !important;
        transition: all 0.15s ease;
      }
      .stTabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:hover {
        background: rgba(27,38,50,0.04) !important;
        color: #1b2632 !important;
      }
      .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {
        background: #1b2632 !important;
        color: #ffffff !important;
        box-shadow: 0 2px 6px rgba(27,38,50,0.15);
      }
      .stTabs [data-baseweb="tab-highlight"],
      .stTabs [data-baseweb="tab-border"] { display: none !important; }
      .stTabs [data-baseweb="tab-panel"] { padding-top: 32px; }
      .stTabs { margin-top: 24px !important; }
      hr, [data-testid="stDivider"] { margin: 28px 0 !important; }
      .stContainer { padding: 4px 0; }
      [data-testid="stExpander"] { margin: 8px 0; }
      .stButton > button {
        font-family: 'Inter', sans-serif;
        font-weight: 600;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        font-size: 13px;
        border-radius: 4px;
        border: none;
        background: #1b2632;
        color: #ffffff;
        padding: 14px 24px;
        transition: all 0.2s ease;
      }
      .stButton > button:hover {
        background: #ffb162;
        color: #1b2632;
        transform: translateY(-1px);
      }
      [data-testid="stTextInput"] input {
        background: #ffffff;
        border-radius: 4px;
        border: 1px solid rgba(27,38,50,0.15);
        padding: 12px 16px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    "<div class='pob-eyebrow'>WEBBANALYS · GRATIS</div>"
    "<h1 class='pob-serif' style='margin:0 0 16px;font-size:54px;line-height:1.05;'>"
    "Hur presterar<br>din webbsida?"
    "</h1>"
    "<p style='color:#1b2632;font-size:18px;margin-top:0;font-weight:300;"
    "line-height:1.5;max-width:640px;'>"
    "Få en komplett insikt i sidhastighet, SEO, sociala medier och annonser — "
    "på under en minut."
    "</p>",
    unsafe_allow_html=True,
)

SERVER_API_KEY = _load_api_key()
country = "SE"

if not SERVER_API_KEY:
    with st.sidebar:
        api_key_input = st.text_input(
            "Google PageSpeed API-nyckel",
            type="password",
            help="Server-nyckel saknas — ange en egen för att undvika rate limit (429).",
        )
else:
    api_key_input = None

api_key = SERVER_API_KEY or api_key_input

with st.container(border=True):
    url_input = st.text_input(
        "**Företagets webbplats**",
        placeholder="example.com eller https://example.com",
        label_visibility="visible",
    )
    col_strategy, col_btn = st.columns([1, 2])
    with col_strategy:
        strategy = st.segmented_control(
            "Enhet",
            ["mobile", "desktop"],
            default="mobile",
            label_visibility="collapsed",
        )
        if strategy is None:
            strategy = "mobile"
    with col_btn:
        st.write("")
        go = st.button("🚀 Analysera", type="primary", use_container_width=True)

if go and url_input:
    url = normalize_url(url_input)
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    company = domain.split(".")[0]

    st.divider()
    st.subheader(f"Resultat för {domain}")

    seo_data, ps_data, robots = None, None, None

    with st.spinner("Hämtar webbplats..."):
        try:
            seo_data = scrape_site(url)
        except Exception as e:
            st.error(f"Kunde inte hämta webbplatsen: {e}")

    with st.spinner("Kontrollerar robots.txt och sitemap..."):
        robots = check_robots_sitemap(url)

    with st.spinner("Identifierar tekniker..."):
        tech_data = detect_technologies(url)

    with st.spinner(f"Kör Google PageSpeed Insights ({strategy})... kan ta 30–60s"):
        try:
            ps_raw = get_pagespeed(url, api_key, strategy)
            ps_data = parse_pagespeed(ps_raw)
        except RuntimeError as e:
            if str(e) == "RATE_LIMITED":
                st.error("⚠️ **Google PageSpeed har rate-limitat dig (429).**")
                st.markdown(
                    "Utan API-nyckel tillåter Google bara några anrop per minut. "
                    "Lös det på 1 minut:\n\n"
                    "1. Hämta en **gratis** API-nyckel här: "
                    "[developers.google.com/speed/docs/insights/v5/get-started]"
                    "(https://developers.google.com/speed/docs/insights/v5/get-started)\n"
                    "2. Klistra in den i fältet i sidopanelen ⬅️\n"
                    "3. Klicka **Analysera** igen\n\n"
                    "_Eller vänta ~60 sekunder och försök igen._"
                )
            else:
                st.error(f"PageSpeed misslyckades: {e}")
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                st.error("⚠️ **Rate limit (429)** — skaffa gratis API-nyckel: "
                         "https://developers.google.com/speed/docs/insights/v5/get-started")
            else:
                st.error(f"PageSpeed misslyckades: {e}")

    if seo_data and ps_data:
        overall = score_overall(
            len(seo_data["issues"]),
            ps_data["scores"]["performance"],
            len(seo_data["socials"]),
        )
        st.markdown(_hero_html(domain, overall), unsafe_allow_html=True)

        gauges_html = "<div style='text-align:center;padding:8px 0 24px;'>"
        for cat, label in [("performance", "Prestanda"),
                           ("accessibility", "Tillgänglighet"),
                           ("best-practices", "Bästa metoder"),
                           ("seo", "SEO")]:
            gauges_html += _gauge_svg(ps_data["scores"][cat], label, size=110)
        gauges_html += "</div>"
        st.markdown(gauges_html, unsafe_allow_html=True)

    tab1, tab_crawl, tab2, tab3, tab4, tab5 = st.tabs([
        "🔍 SEO & Fel",
        "🕸️ Sajt-crawl",
        "⚡ Sidhastighet",
        "🛠️ Tech-stack",
        "📱 Sociala medier",
        "📢 Annonser",
    ])

    with tab1:
        if seo_data:
            col_a, col_b = st.columns(2)
            with col_a:
                with st.container(border=True):
                    st.markdown("##### 📝 Meta-info")
                    st.markdown(
                        f"**Title** ({seo_data['title_len']} tecken)  \n"
                        f"<span style='color:#888;font-size:13px;'>{seo_data['title'] or '—'}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"**Description** ({seo_data['description_len']} tecken)  \n"
                        f"<span style='color:#888;font-size:13px;'>{seo_data['description'] or '—'}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"**H1**  \n<span style='color:#888;font-size:13px;'>"
                        f"{seo_data['h1_first'] or '—'}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"**Bilder:** {seo_data['images_total']} totalt · "
                        f"{seo_data['images_no_alt']} utan alt-text  \n"
                        f"**Open Graph:** {seo_data['og_tags']} taggar  \n"
                        f"**robots.txt:** {'✅' if robots.get('/robots.txt') else '❌'} · "
                        f"**sitemap.xml:** {'✅' if robots.get('/sitemap.xml') else '❌'}"
                    )

            with col_b:
                with st.container(border=True):
                    st.markdown("##### ⚠️ Hittade problem")
                    if seo_data["issues"]:
                        for issue in seo_data["issues"]:
                            st.markdown(issue)
                    else:
                        st.success("Inga uppenbara SEO-fel hittades. 🎉")

    with tab_crawl:
        st.markdown(
            "<p style='font-size:16px;color:#5a6470;'>Crawla webbplatsen för att hitta "
            "trasiga länkar, missade meta-tags och duplicerat innehåll på alla sidor.</p>",
            unsafe_allow_html=True,
        )
        crawl_key = f"crawl::{domain}"
        max_pages = st.slider("Max antal sidor att crawla", 10, 100, 50, step=10)
        if st.button("🕸️ Starta crawl", key="btn_crawl",
                     use_container_width=True):
            with st.spinner("Hämtar URL-lista från sitemap.xml..."):
                urls = fetch_sitemap_urls(url, max_urls=max_pages)
            if not urls:
                st.warning("Ingen sitemap hittades. Kunde bara crawla startsidan.")
                urls = [url]
            else:
                st.info(f"Hittade {len(urls)} URL:er i sitemap. Crawlar nu...")
            progress = st.progress(0, text="Crawlar sidor...")
            results = []
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(_crawl_one, u): u for u in urls}
                for i, f in enumerate(as_completed(futures), 1):
                    results.append(f.result())
                    progress.progress(i / len(urls),
                                      text=f"Crawlar sidor... ({i}/{len(urls)})")
            progress.empty()
            st.session_state[crawl_key] = analyze_crawl(results)

        crawl_res = st.session_state.get(crawl_key)
        if crawl_res:
            st.divider()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("📄 Sidor", crawl_res["total"])
            c2.metric("✅ OK", crawl_res["successful"])
            c3.metric("❌ Trasiga", len(crawl_res["broken"]))
            c4.metric("⏱️ Snitt-tid", f"{crawl_res['avg_ms']} ms")

            if crawl_res["broken"]:
                with st.container(border=True):
                    st.markdown("##### ❌ Trasiga länkar")
                    for r in crawl_res["broken"]:
                        status = r.get("status") or r.get("error", "?")
                        st.markdown(f"- `{status}` — [{r['url']}]({r['url']})")

            issue_cols = st.columns(2)
            with issue_cols[0]:
                if crawl_res["no_description"]:
                    with st.container(border=True):
                        st.markdown(f"##### 📝 Saknar meta description ({len(crawl_res['no_description'])})")
                        for r in crawl_res["no_description"][:8]:
                            st.markdown(f"- [{r['url']}]({r['url']})")
                        if len(crawl_res["no_description"]) > 8:
                            st.caption(f"+ {len(crawl_res['no_description']) - 8} till")
                if crawl_res["no_h1"]:
                    with st.container(border=True):
                        st.markdown(f"##### 🔠 Saknar H1 ({len(crawl_res['no_h1'])})")
                        for r in crawl_res["no_h1"][:8]:
                            st.markdown(f"- [{r['url']}]({r['url']})")
            with issue_cols[1]:
                if crawl_res["duplicate_titles"]:
                    with st.container(border=True):
                        st.markdown(f"##### 🔁 Duplicerade titles ({len(crawl_res['duplicate_titles'])})")
                        for d in crawl_res["duplicate_titles"][:5]:
                            st.markdown(f"**`{d['title'][:60]}`**")
                            for u in d["urls"][:3]:
                                st.markdown(f"  - [{u}]({u})")
                if crawl_res["multi_h1"]:
                    with st.container(border=True):
                        st.markdown(f"##### ⚠️ Flera H1-taggar ({len(crawl_res['multi_h1'])})")
                        for r in crawl_res["multi_h1"][:5]:
                            st.markdown(f"- [{r['url']}]({r['url']}) ({r['h1_count']} H1)")

            with st.expander(f"🐌 Långsammaste sidor (top {len(crawl_res['slow'])})"):
                for r in crawl_res["slow"]:
                    st.markdown(f"- **{r['elapsed_ms']} ms** — [{r['url']}]({r['url']})")
            with st.expander(f"📦 Tyngsta sidor (top {len(crawl_res['largest'])})"):
                for r in crawl_res["largest"]:
                    st.markdown(f"- **{r['size_kb']} KB** — [{r['url']}]({r['url']})")

    with tab2:
        if ps_data:
            col_score, col_shot = st.columns([1, 1])
            with col_score:
                perf = ps_data["scores"]["performance"]
                big_gauge = _gauge_svg(perf, "Prestanda", size=220)
                st.markdown(
                    f"<div style='text-align:center;padding-top:12px;'>{big_gauge}</div>"
                    "<div style='text-align:center;font-size:13px;color:#888;"
                    "margin-top:8px;'>🔴 0–49 &nbsp;·&nbsp; 🟠 50–89 &nbsp;·&nbsp; 🟢 90–100</div>",
                    unsafe_allow_html=True,
                )
            with col_shot:
                if ps_data.get("screenshot"):
                    st.image(ps_data["screenshot"],
                             caption=f"Skärmdump ({strategy})",
                             use_container_width=True)

            st.markdown("#### 📊 Mätvärden (Core Web Vitals)")
            metrics = ps_data.get("metrics", [])
            if metrics:
                m_col1, m_col2 = st.columns(2)
                for i, m in enumerate(metrics):
                    target = m_col1 if i % 2 == 0 else m_col2
                    with target:
                        st.markdown(
                            _metric_card(m["label"], m["value"], m["score"]),
                            unsafe_allow_html=True,
                        )

            st.markdown("#### 🚀 Förbättringsmöjligheter")
            if ps_data["opportunities"]:
                for opp in ps_data["opportunities"]:
                    with st.expander(f"⏱️ {opp['title']}  —  spara ~{opp['savings_ms']} ms"):
                        st.markdown(opp["description"])
            else:
                st.success("Inga större förbättringsmöjligheter hittades.")

            with st.expander("Visa underkända kontroller"):
                if ps_data["failed_audits"]:
                    for fa in ps_data["failed_audits"]:
                        st.write(f"- {fa['title']}")
                else:
                    st.success("Alla kontroller godkända.")

    with tab3:
        if tech_data and tech_data.get("by_category"):
            cat_emojis = {
                "E-handel": "🛒", "CMS": "📝", "Frontend": "⚛️",
                "Analytics": "📊", "E-postmarknadsföring": "✉️",
                "Marketing": "📣", "Annonspixlar": "🎯",
                "CDN/Hosting": "☁️", "Kundtjänst": "💬",
                "Betalning": "💳", "Consent": "🍪", "Generator": "⚙️",
            }
            total_count = sum(len(v) for v in tech_data["by_category"].values())
            st.markdown(
                f"<p style='font-size:16px;color:#5a6470;'>Hittade "
                f"<strong style='color:#1b2632;'>{total_count} tekniker</strong> "
                f"fördelat på <strong style='color:#1b2632;'>"
                f"{len(tech_data['by_category'])} kategorier</strong>.</p>",
                unsafe_allow_html=True,
            )
            for cat, items in sorted(tech_data["by_category"].items()):
                emoji = cat_emojis.get(cat, "🔧")
                with st.container(border=True):
                    st.markdown(f"##### {emoji} {cat}")
                    badges_html = "<div style='margin-top:8px;'>"
                    for tech in items:
                        badges_html += (
                            f"<span style='display:inline-block;"
                            f"background:#1b2632;color:#fff;padding:6px 14px;"
                            f"border-radius:20px;font-size:14px;font-weight:500;"
                            f"margin:4px 6px 4px 0;'>{tech}</span>"
                        )
                    badges_html += "</div>"
                    st.markdown(badges_html, unsafe_allow_html=True)

            if "Analytics" not in tech_data["by_category"]:
                st.warning("⚠️ Ingen analytics-lösning hittades — sajten mäter inte trafik.")
            if "Annonspixlar" not in tech_data["by_category"]:
                st.info("💡 Inga annonspixlar hittades. Företaget kan inte retargeta besökare.")
        elif tech_data and tech_data.get("error"):
            st.error(f"Kunde inte analysera tekniker: {tech_data['error']}")
        else:
            st.info("Inga kända tekniker identifierades. Detta kan bero på en mycket skräddarsydd lösning.")

    with tab4:
        if seo_data:
            socials = seo_data["socials"]
            icons = {"Facebook": "📘", "Instagram": "📷", "LinkedIn": "💼",
                     "Twitter/X": "🐦", "YouTube": "▶️", "TikTok": "🎵"}
            if socials:
                cols = st.columns(min(len(socials), 3))
                for i, (name, link) in enumerate(socials.items()):
                    with cols[i % len(cols)]:
                        with st.container(border=True):
                            st.markdown(
                                f"### {icons.get(name, '🔗')} {name}\n"
                                f"[Öppna profil →]({link})"
                            )
            else:
                st.warning("Inga sociala medier-länkar hittades på startsidan.")
                st.info("💡 **Potential:** Företaget länkar inte tydligt till sina sociala kanaler. "
                        "Lägg till ikoner i header/footer för att öka synlighet och trovärdighet.")

            expected = {"Facebook", "Instagram", "LinkedIn"}
            missing = expected - set(socials.keys())
            if missing and socials:
                st.info(f"🔍 Saknar länk till: **{', '.join(missing)}**. "
                        "De flesta B2C/B2B-företag bör synas på dessa.")

    with tab5:
        st.write(f"### Annonser för {company} ({domain})")
        links = ad_library_links(domain, company, country)
        scraping_available = _playwright_available()

        meta_key = f"meta_ads::{company}::{country}"
        google_key = f"google_ads::{domain}::{country}"

        col_m, col_g = st.columns(2)
        with col_m:
            st.markdown(f"**Meta (Facebook/Instagram)** — [öppna sökning]({links['Meta Ad Library']})")
            if scraping_available:
                if st.button("🔎 Hämta Meta-annonser", key="btn_meta",
                             use_container_width=True):
                    with st.spinner("Öppnar Meta Ad Library i headless browser (~20s)..."):
                        st.session_state[meta_key] = scrape_meta_ads(
                            company, country=country)
        with col_g:
            st.markdown(f"**Google Ads** — [öppna sökning]({links['Google Ads Transparency']})")
            if scraping_available:
                if st.button("🔎 Hämta Google-annonser", key="btn_google",
                             use_container_width=True):
                    with st.spinner("Öppnar Google Ads Transparency Center (~20s)..."):
                        st.session_state[google_key] = scrape_google_ads(
                            domain, country=country)

        st.markdown(f"**TikTok Creative Center** — [öppna sökning]({links['TikTok Creative Center']})")

        if not scraping_available:
            st.info("ℹ️ Automatisk annonshämtning är inaktiverad i denna deployment. "
                    "Klicka länkarna ovan för att se annonser i respektive bibliotek.")
        st.divider()

        meta_res = st.session_state.get(meta_key)
        if meta_res:
            st.subheader("📘 Meta Ad Library — resultat")
            if meta_res.get("error"):
                st.error(f"Kunde inte läsa Meta: {meta_res['error']}")
            else:
                count = meta_res.get("count")
                if count is None:
                    st.warning("Kunde inte tolka antal — se skärmdump nedan.")
                elif count == 0:
                    st.warning(f"⚠️ Inga aktiva annonser hittades för **{company}** i {country}.")
                    st.info("💡 **Potential:** Företaget kör inga Meta-annonser. "
                            "Konkurrenter som annonserar når målgruppen i Facebook/Instagram-flöden.")
                else:
                    st.success(f"✅ Hittade ~{count} aktiva annons-element på sidan.")
                    if meta_res["ads"]:
                        st.write("**Exempel på annonser:**")
                        for i, ad in enumerate(meta_res["ads"], 1):
                            with st.expander(f"Annons {i}"):
                                if ad.get("image"):
                                    try:
                                        st.image(ad["image"], width=320)
                                    except Exception:
                                        st.caption("(kunde inte ladda bild)")
                                st.text(ad.get("text", "")[:400])
                if meta_res.get("screenshot"):
                    with st.expander("📸 Skärmdump av sökresultatet"):
                        st.image(meta_res["screenshot"])

        google_res = st.session_state.get(google_key)
        if google_res:
            st.subheader("🔵 Google Ads Transparency — resultat")
            if google_res.get("error"):
                st.error(f"Kunde inte läsa Google: {google_res['error']}")
            else:
                advertisers = google_res.get("advertisers", [])
                ad_count = google_res.get("ad_count", 0)
                if not advertisers:
                    st.warning(f"⚠️ Ingen verifierad Google-annonsör hittades för **{domain}**.")
                    st.info("💡 **Potential:** Företaget kör troligen inga Google-annonser "
                            "(eller är inte verifierat i Transparency Center).")
                else:
                    st.success(f"✅ Hittade {len(advertisers)} annonsör(er).")
                    for adv in advertisers:
                        st.markdown(f"- [{adv['name']}]({adv['url']})")
                    if ad_count:
                        st.write(f"**~{ad_count} kreativ hittades** för första annonsören.")
                if google_res.get("screenshot"):
                    with st.expander("📸 Skärmdump av Transparency Center"):
                        st.image(google_res["screenshot"])

        st.caption("⚠️ Scraping är skört — om Meta/Google ändrar sin sida kan resultaten bli ofullständiga. "
                   "Klicka länkarna ovan för att verifiera manuellt.")

    if seo_data:
        st.divider()
        st.subheader("📋 Sammanfattning")
        summary = []
        if ps_data and ps_data["scores"]["performance"] < 50:
            summary.append("⚡ Sidhastigheten är låg — prioritera de förbättringar som listas under 'Sidhastighet'.")
        if len(seo_data["issues"]) > 3:
            summary.append(f"🔍 {len(seo_data['issues'])} SEO-problem hittades — fixa de röda (❌) först.")
        if len(seo_data["socials"]) < 2:
            summary.append("📱 Svag närvaro på sociala medier — bygg upp åtminstone Facebook + LinkedIn.")
        if not robots.get("/sitemap.xml"):
            summary.append("🗺️ Saknar sitemap.xml — försvårar Googles indexering.")

        if summary:
            for s in summary:
                st.write(s)
        else:
            st.success("✅ Webbplatsen ser stark ut på alla områden!")
