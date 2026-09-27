import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import feedparser
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

try:
    from google import genai
except ImportError:
    genai = None

app = Flask(__name__, template_folder='templates')
CORS(app)

# ==========================================
# MEMORY CACHE (15 MIN TTL)
# ==========================================
LEADS_CACHE = {}
CACHE_TIMEOUT = 900

def get_from_cache(skill, country, niche):
    cache_key = f"{skill.lower().strip()}_{country.lower().strip()}_{niche.lower().strip()}"
    if cache_key in LEADS_CACHE:
        cached_data, timestamp = LEADS_CACHE[cache_key]
        if time.time() - timestamp < CACHE_TIMEOUT:
            return cached_data
        else:
            del LEADS_CACHE[cache_key]
    return None

def save_to_cache(skill, country, niche, data):
    cache_key = f"{skill.lower().strip()}_{country.lower().strip()}_{niche.lower().strip()}"
    LEADS_CACHE[cache_key] = (data, time.time())

def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', str(raw_html)).strip()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = None
if GEMINI_API_KEY and genai:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Gemini init error:", e)

WEB_HEADERS = {
    'User-Agent': 'ClientHunterPro/2.5 (Mozilla/5.0 Windows NT 10.0; Win64; x64)'
}

# ==========================================
# MAJOR CITIES MAPPING FOR MAXIMUM LOCAL MAPS LEADS
# ==========================================
CITIES_MAP = {
    "Pakistan": ["Lahore", "Karachi", "Islamabad", "Rawalpindi", "Faisalabad", "Multan", "Peshawar", "Sialkot", "Gujranwala"],
    "United States": ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Miami", "Dallas", "Atlanta"],
    "United Kingdom": ["London", "Birmingham", "Manchester", "Leeds", "Glasgow", "Liverpool"],
    "United Arab Emirates": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman"],
    "Canada": ["Toronto", "Vancouver", "Montreal", "Calgary"],
    "Australia": ["Sydney", "Melbourne", "Brisbane", "Perth"]
}

# ==========================================
# PORTFOLIO & DEMO MATCHING
# ==========================================
DEMO_PORTFOLIOS = {
    "web": "https://demo-web.agency-preview.com",
    "clinic": "https://demo-medical.agency-preview.com",
    "real estate": "https://demo-realty.agency-preview.com",
    "ecommerce": "https://demo-store.agency-preview.com",
    "law": "https://demo-lawfirm.agency-preview.com",
    "seo": "https://demo-seo-report.agency-preview.com",
    "design": "https://demo-branding.agency-preview.com",
    "smm": "https://demo-social-kit.agency-preview.com",
    "default": "https://myportfolio.com"
}

def get_matching_demo(skill):
    s = skill.lower()
    for key, url in DEMO_PORTFOLIOS.items():
        if key in s:
            return url
    return DEMO_PORTFOLIOS["default"]

# ==========================================
# FAST MINI-AUDIT & SCRAPER ENGINE
# ==========================================
def deep_audit_and_scrape(website_url):
    audit_data = {
        "emails": [],
        "phones": [],
        "audit_notes": [],
        "has_ssl": True,
        "is_responsive": True,
        "has_seo_tags": True
    }
    if not website_url or not website_url.startswith("http"):
        return audit_data

    try:
        if website_url.startswith("http://"):
            audit_data["has_ssl"] = False
            audit_data["audit_notes"].append("❌ Missing SSL Certificate (HTTP)")

        res = requests.get(website_url, headers=WEB_HEADERS, timeout=2.5)
        if res.status_code == 200:
            html = res.text

            # Extract Emails
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
            audit_data["emails"] = list(set([e for e in emails if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.svg', '.gif'))]))[:3]

            # Extract WhatsApp/Phones
            phones = re.findall(r'(\+?\d{1,4}[-.\s]?\(?\d{1,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4})', html)
            audit_data["phones"] = list(set([p for p in phones if len(p) >= 10]))[:2]

            # Check Mobile Viewport Tag
            if "viewport" not in html.lower():
                audit_data["is_responsive"] = False
                audit_data["audit_notes"].append("📱 Mobile Viewport Missing")

            # Check Meta Description
            if 'name="description"' not in html.lower() and "name='description'" not in html.lower():
                audit_data["has_seo_tags"] = False
                audit_data["audit_notes"].append("🔍 Meta Description Missing")

    except Exception:
        pass

    return audit_data

# ==========================================
# HOT LEAD EVALUATOR
# ==========================================
def evaluate_client_hot_lead(item_data, user_skill):
    skill = user_skill.lower().strip()
    extratags = item_data.get("extratags", {}) or {}
    b_name = item_data.get("display_name", "").lower()
    
    website = extratags.get("website") or extratags.get("contact:website", "")
    has_website = bool(website)
    has_email = "email" in extratags or "contact:email" in extratags
    
    is_new_launch = any(term in b_name for term in ["new", "express", "grand", "launch", "studio", "center", "prime"])

    audit_info = deep_audit_and_scrape(website) if has_website else {}
    audit_notes = audit_info.get("audit_notes", [])

    if any(k in skill for k in ["web", "dev", "wordpress", "frontend", "backend", "shopify", "website"]):
        if not has_website:
            return True, "🔥 HOT (NO WEBSITE)", "⚠️ Business lacks a website. Ideal candidate for complete web development setup.", audit_info
        elif audit_notes:
            return True, "🔥 HOT (AUDIT ISSUES)", f"⚠️ Audit: {', '.join(audit_notes[:2])}", audit_info
        elif is_new_launch:
            return True, "🔥 HOT (NEW LAUNCH)", "⚠️ Newly opened business! Urgent requirement for web presence.", audit_info

    elif any(k in skill for k in ["seo", "marketing", "digital marketing", "ads", "sem"]):
        if not has_website or not audit_info.get("has_seo_tags", True):
            return True, "🔥 HOT (ZERO SEO)", "⚠️ Minimal or missing SEO setup. Pitch local Google ranking services.", audit_info

    if not has_website or not has_email:
        return True, "🔥 HOT CLIENT", f"⚠️ High-value target client for {user_skill} services.", audit_info

    return False, "Local Business Client", "Verified local business listing.", audit_info

# ==========================================
# ENGINE 1: 50+ GOOGLE MAPS LEADS (CITY-LEVEL TARGETING)
# ==========================================
def fetch_maximum_maps_clients(skill, country, niche):
    leads = []
    country_str = country.strip().title() if country else "Pakistan"
    seen_titles = set()

    niche_query = f"{niche} " if niche and niche != "All Niches" else ""
    cities = CITIES_MAP.get(country_str, [country_str, "Capital City", "Central District"])

    search_queries = []
    for city in cities:
        search_queries.append(f"{niche_query}{skill} in {city} {country_str}")
        search_queries.append(f"{niche_query}agency in {city} {country_str}")
        search_queries.append(f"{niche_query}business center in {city} {country_str}")

    def query_nominatim_deep(q_term):
        results = []
        try:
            nom_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q_term)}&format=json&addressdetails=1&extratags=1&limit=25"
            res = requests.get(nom_url, headers=WEB_HEADERS, timeout=5)
            if res.status_code == 200:
                data = res.json()
                for item in data:
                    display_name = item.get("display_name", "")
                    b_name = display_name.split(",")[0].strip()
                    
                    if b_name.lower() in seen_titles or len(b_name) < 3:
                        continue
                    seen_titles.add(b_name.lower())

                    is_hot, badge_label, desc_text, audit_info = evaluate_client_hot_lead(item, skill)

                    lat, lon = item.get("lat"), item.get("lon")
                    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else f"https://www.google.com/maps/search/{urllib.parse.quote(b_name + ' ' + country_str)}"

                    extratags = item.get("extratags", {}) or {}
                    website = extratags.get("website") or extratags.get("contact:website", "")
                    direct_email = extratags.get("email") or extratags.get("contact:email", "")
                    if not direct_email and audit_info.get("emails"):
                        direct_email = audit_info["emails"][0]

                    results.append({
                        "platform": "Google Maps",
                        "title": b_name,
                        "description": desc_text,
                        "website": website,
                        "email": direct_email or "Not Available",
                        "phones": audit_info.get("phones", []),
                        "audit_notes": audit_info.get("audit_notes", []),
                        "action_link": maps_link,
                        "is_hot": is_hot,
                        "badge": badge_label,
                        "lead_type": "maps"
                    })
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(query_nominatim_deep, q) for q in search_queries]
        for future in as_completed(futures):
            leads.extend(future.result())

    return leads

# ==========================================
# ENGINE 2: 50+ WEB, SOCIAL & FREELANCE REQUESTS
# ==========================================
def fetch_secondary_client_requests(skill, country):
    leads = []
    seen_titles = set()

    client_search_targets = [
        (f'site:facebook.com "{skill}" ("looking for freelancer" OR "need agency" OR "hiring")', "Facebook Client Request"),
        (f'site:upwork.com/jobs "{skill}"', "Upwork Project"),
        (f'site:linkedin.com/posts "{skill}" ("looking for agency" OR "hiring freelancer")', "LinkedIn Lead"),
        (f'site:reddit.com/r/forhire "{skill}" ("hiring" OR "looking for")', "Reddit Job Post"),
        (f'site:twitter.com "{skill}" ("looking for developer" OR "hiring designer")', "X (Twitter) Feed"),
        (f'"{skill}" client job requirement {country}', "Global Job Board")
    ]

    def execute_client_rss(query, platform_name):
        results = []
        try:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
            res = requests.get(url, headers=WEB_HEADERS, timeout=5)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                # Fetches up to 12 items per source
                for entry in feed.entries[:12]:
                    title = clean_html(entry.title)
                    summary = clean_html(getattr(entry, 'summary', ''))

                    if title.lower() in seen_titles:
                        continue
                    seen_titles.add(title.lower())

                    results.append({
                        "platform": platform_name,
                        "title": title,
                        "description": summary[:220] + "..." if len(summary) > 220 else summary,
                        "website": "",
                        "email": "Direct Link",
                        "phones": [],
                        "audit_notes": [],
                        "action_link": entry.link,
                        "is_hot": False,
                        "badge": platform_name,
                        "lead_type": "other"
                    })
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(execute_client_rss, t[0], t[1]) for t in client_search_targets]
        for future in as_completed(futures):
            leads.extend(future.result())

    return leads

# ==========================================
# MAIN API ROUTE
# ==========================================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/leads', methods=['GET'])
def get_leads():
    skill = request.args.get('skill', 'Web Development').strip()
    country = request.args.get('country', 'Pakistan').strip()
    niche = request.args.get('niche', 'All Niches').strip()

    cached = get_from_cache(skill, country, niche)
    if cached:
        cached["is_cached"] = True
        return jsonify(cached)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_maps = executor.submit(fetch_maximum_maps_clients, skill, country, niche)
        f_other = executor.submit(fetch_secondary_client_requests, skill, country)

        maps_leads = f_maps.result()
        other_leads = f_other.result()

    hot_clients = [item for item in maps_leads if item.get('is_hot')]
    normal_maps = [item for item in maps_leads if not item.get('is_hot')]
    final_clients = hot_clients + normal_maps + other_leads

    response_payload = {
        "status": "success",
        "is_cached": False,
        "total_found": len(final_clients),
        "demo_portfolio": get_matching_demo(skill),
        "leads": final_clients
    }

    save_to_cache(skill, country, niche, response_payload)
    return jsonify(response_payload)

# ==========================================
# AI PITCH & 3-STEP SEQUENCES
# ==========================================
@app.route('/api/ai_pitch', methods=['POST'])
def generate_ai_pitch():
    data = request.json or {}
    lead_title = data.get("lead_title", "")
    lead_desc = data.get("lead_desc", "")
    audit_notes = data.get("audit_notes", [])
    step = data.get("step", "day1")
    user_portfolio = data.get("portfolio", "https://myportfolio.com")

    audit_str = f" Audit Issues Found: {', '.join(audit_notes)}." if audit_notes else ""

    prompts = {
        "day1": f"Write a short, high-converting initial pitch for business '{lead_title}'. Context: {lead_desc}.{audit_str} Include Demo: {user_portfolio}",
        "day3": f"Write a gentle 2-sentence follow-up message for business '{lead_title}' checking if they reviewed the previous message regarding their web/digital setup. Demo: {user_portfolio}",
        "day7": f"Write a value-add final follow-up offer for '{lead_title}' offering a free 15-min consultation or live mockup preview. Demo: {user_portfolio}"
    }

    prompt = prompts.get(step, prompts["day1"])

    if not ai_client:
        fallback = f"Hello {lead_title}!\n\nI noticed some digital improvements for your business. Check our live sample: {user_portfolio}\nLet's connect!"
        return jsonify({"status": "success", "pitch": fallback, "mode": "template"})

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        pitch_text = response.text if response and hasattr(response, 'text') else "Proposal generation issue."
        return jsonify({"status": "success", "pitch": pitch_text, "mode": "ai"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
    
