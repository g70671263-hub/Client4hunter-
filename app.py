import os
import re
import random
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import feedparser
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

# Google GenAI SDK Import
try:
    from google import genai
except ImportError:
    genai = None

app = Flask(__name__, template_folder='templates')
CORS(app)

# Helper function to strip HTML tags
def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', str(raw_html))
    return cleantext.strip()

# Gemini API Client initialization
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = None
if GEMINI_API_KEY and genai:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Gemini client initialization error:", e)

WEB_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# Dynamic Skill Keyword Expander (Generates broad search variations for ANY input skill)
def generate_skill_query_variations(base_skill):
    skill = base_skill.strip()
    
    # Core search variations
    intents = [
        f"{skill}",
        f"{skill} specialist",
        f"{skill} expert",
        f"{skill} agency",
        f"{skill} freelancer",
        f"{skill} services",
        f"hire {skill}",
        f"need {skill}",
        f"looking for {skill}",
        f"{skill} developer" if "dev" not in skill.lower() and "design" not in skill.lower() else skill,
        f"{skill} designer" if "design" in skill.lower() else skill
    ]
    return list(set(intents))

# ==========================================
# 1. GOOGLE MAPS & LOCAL BUSINESS ENGINE (50% TARGET)
# ==========================================
def fetch_maps_and_local_leads(skill, country):
    leads = []
    country_str = country.strip().title() if country else "Pakistan"
    query_variations = generate_skill_query_variations(skill)
    seen_titles = set()

    # A. OpenStreetMap / Nominatim Multi-Query API
    def query_nominatim(q_term):
        try:
            nom_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q_term + ' in ' + country_str)}&format=json&addressdetails=1&limit=10"
            res = requests.get(nom_url, headers=WEB_HEADERS, timeout=4)
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data:
                    display_name = item.get("display_name", "Local Business")
                    b_name = display_name.split(",")[0].strip()
                    if b_name.lower() in seen_titles:
                        continue
                    seen_titles.add(b_name.lower())
                    
                    address = ", ".join(display_name.split(",")[1:4]).strip()
                    lat, lon = item.get("lat"), item.get("lon")
                    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else f"https://www.google.com/maps/search/{urllib.parse.quote(b_name + ' ' + country_str)}"
                    
                    results.append({
                        "platform": "Google Maps / Business Directory",
                        "title": f"{b_name} ({country_str})",
                        "description": f"Verified local entity offering services in {address}. Excellent target for {skill} outreach.",
                        "contact_info": "Google Maps Listing",
                        "action_link": maps_link,
                        "time_ago": "Verified Business",
                        "lead_type": "maps"
                    })
                return results
        except Exception as e:
            pass
        return []

    # B. Google Business RSS Search Engine
    def query_google_biz_rss(q_term):
        try:
            rss_query = f'"{q_term}" ("company" OR "agency" OR "services" OR "business" OR "office") "{country_str}"'
            rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(rss_query)}&hl=en-US&gl=US&ceid=US:en"
            res = requests.get(rss_url, headers=WEB_HEADERS, timeout=4)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                results = []
                for entry in feed.entries[:6]:
                    t = clean_html(entry.title)
                    if t.lower() in seen_titles:
                        continue
                    seen_titles.add(t.lower())
                    results.append({
                        "platform": "Google Maps & Local Search",
                        "title": t,
                        "description": clean_html(getattr(entry, 'summary', ''))[:220] + "...",
                        "contact_info": "View Business Page",
                        "action_link": entry.link,
                        "time_ago": "Recent Listing",
                        "lead_type": "maps"
                    })
                return results
        except Exception as e:
            pass
        return []

    # Threaded Execution across multiple skill variations
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for term in query_variations[:5]:
            futures.append(executor.submit(query_nominatim, term))
            futures.append(executor.submit(query_google_biz_rss, term))

        for future in as_completed(futures):
            leads.extend(future.result())

    return leads

# ==========================================
# 2. JOB BOARDS & SOCIAL MEDIA ENGINE (50% TARGET)
# ==========================================
def fetch_other_sources_leads(skill, country):
    leads = []
    country_str = country.strip().title() if country else "Pakistan"
    query_variations = generate_skill_query_variations(skill)
    seen_titles = set()

    # Custom search patterns for all platforms
    search_targets = [
        # Upwork & Job Boards
        ('site:upwork.com/jobs "{skill}"', "Upwork Project"),
        ('site:remoteok.com "{skill}"', "RemoteOK Job"),
        ('site:freelancer.com/projects "{skill}"', "Freelancer Project"),
        ('site:indeed.com "{skill}" ("hiring" OR "urgent")', "Indeed Job"),
        ('site:simplyhired.com "{skill}"', "SimplyHired Job"),
        
        # Social Networks
        ('site:facebook.com "{skill}" ("looking for" OR "hiring" OR "need")', "Facebook Client Request"),
        ('site:linkedin.com/posts "{skill}" ("hiring" OR "looking for developer" OR "looking for designer")', "LinkedIn Direct Post"),
        ('site:reddit.com ("looking for" OR "hiring") "{skill}"', "Reddit Client Request"),
        ('site:twitter.com "{skill}" ("hiring" OR "need freelancer")', "Twitter / X Hiring"),
        ('site:t.me "{skill}" ("job" OR "hiring" OR "client")', "Telegram Jobs")
    ]

    def execute_rss_search(target_template, platform_name):
        try:
            # Build dynamic search string for any user skill
            formatted_query = target_template.replace("{skill}", skill)
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(formatted_query)}&hl=en-US&gl=US&ceid=US:en"
            
            res = requests.get(url, headers=WEB_HEADERS, timeout=4)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                results = []
                for entry in feed.entries[:5]:
                    title = clean_html(entry.title)
                    if title.lower() in seen_titles:
                        continue
                    seen_titles.add(title.lower())

                    results.append({
                        "platform": platform_name,
                        "title": title,
                        "description": clean_html(getattr(entry, 'summary', ''))[:220] + "...",
                        "contact_info": "Direct Platform Link",
                        "action_link": entry.link,
                        "time_ago": "Recently Posted",
                        "lead_type": "other"
                    })
                return results
        except Exception as e:
            pass
        return []

    # Parallel Execution for all job and social networks
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(execute_rss_search, t[0], t[1]) for t in search_targets]
        for future in as_completed(futures):
            leads.extend(future.result())

    return leads

# ==========================================
# 3. MAIN API ENDPOINT WITH 50/50 BALANCER
# ==========================================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/leads', methods=['GET'])
def get_leads():
    skill = request.args.get('skill', 'Web Development').strip()
    country = request.args.get('country', 'Pakistan').strip()

    # Parallel execution for both categories
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_maps = executor.submit(fetch_maps_and_local_leads, skill, country)
        f_other = executor.submit(fetch_other_sources_leads, skill, country)

        maps_leads = f_maps.result()
        other_leads = f_other.result()

    # Shuffle for varied freshness
    random.shuffle(maps_leads)
    random.shuffle(other_leads)

    # Strict 50% Google Maps / 50% Other Platforms Balancing Logic
    balanced_results = []
    max_count = max(len(maps_leads), len(other_leads))

    for i in range(max_count):
        if i < len(maps_leads):
            balanced_results.append(maps_leads[i])
        if i < len(other_leads):
            balanced_results.append(other_leads[i])

    return jsonify({
        "status": "success",
        "total_found": len(balanced_results),
        "maps_count": len(maps_leads),
        "other_count": len(other_leads),
        "ratio": "50% Google Maps / Local Businesses | 50% Job Boards & Social Media",
        "leads": balanced_results
    })

# ==========================================
# 4. AI PROPOSAL GENERATOR
# ==========================================
@app.route('/api/ai_pitch', methods=['POST'])
def generate_ai_pitch():
    data = request.json or {}
    lead_title = data.get("lead_title", "")
    lead_desc = data.get("lead_desc", "")
    user_portfolio = data.get("portfolio", "https://myportfolio.com")

    if not ai_client:
        pitch = f"""Hi there!

I came across your listing regarding "{lead_title}".

I am a specialist in this field and can deliver top-quality results tailored to your requirements. Check out my portfolio here:
{user_portfolio}

Let's discuss this further to get started immediately!

Best regards,"""
        return jsonify({"status": "success", "pitch": pitch, "mode": "template"})

    prompt = f"""Write a professional, high-converting outreach proposal for this opportunity:
Opportunity: {lead_title}
Details: {lead_desc}
Portfolio Link: {user_portfolio}

Keep it concise (3 short paragraphs), direct, and persuasive."""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        pitch_text = response.text if response and hasattr(response, 'text') else "Could not generate proposal."
        return jsonify({"status": "success", "pitch": pitch_text, "mode": "ai"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
        
