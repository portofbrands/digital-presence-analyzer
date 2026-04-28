"""Digital Presence Analyzer — MVP

Kör:
    pip install -r requirements.txt
    streamlit run app.py
"""
from __future__ import annotations

import os
import re
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

    tab1, tab2, tab3, tab4 = st.tabs(["🔍 SEO & Fel", "⚡ Sidhastighet", "📱 Sociala medier", "📢 Annonser"])

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

    with tab4:
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
