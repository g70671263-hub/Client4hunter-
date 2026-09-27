import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
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
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

def fetch_google_maps_leads(skill, country):
    leads = []
    country_str = country.strip().title() if country else "Pakistan"
    
    # 1. Nominatim OpenStreetMap API
    try:
        nom_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(skill + ' in ' + country_str)}&format=json&addressdetails=1&limit=10"
        res = requests.get(nom_url, headers=WEB_HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json()
            for item in data:
                display_name = item.get("display_name", "Local Business")
                name_parts = display_name.split(",")
                b_name = name_parts[0].strip()
                address = ", ".join(name_parts[1:4]).strip()
                lat = item.get("lat")
                lon = item.get("lon")
                maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat and lon else f"https://www.google.com/maps/search/{urllib.parse.quote(b_name + ' ' + country_str)}"
                
                leads.append({
                    "platform": "Google Maps / Local Business",
                    "title": f"{b_name} ({country_str})",
                    "description": f"Verified local business located at: {address}. Potential client for {skill} services.",
                    "contact_info": "Google Maps Listing",
                    "action_link": maps_link,
                    "time_ago": "Active Entity",
                    "lead_type": "maps"
                })
    except Exception as e:
        print("Nominatim error:", e)

    # 2. Google RSS Search Fallback for Local Businesses
    try:
        rss_query = f'"{skill}" ("business" OR "agency" OR "company" OR "store") "{country_str}"'
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(rss_query)}&hl=en-US&gl=US&ceid=US:en"
        res = requests.get(rss_url, headers=WEB_HEADERS, timeout=5)
        if res.status_code == 200:
            feed = feedparser.parse(res.content)
            for entry in feed.entries[:8]:
                leads.append({
                    "platform": "Google Business Search",
                    "title": clean_html(entry.title),
                    "description": clean_html(getattr(entry, 'summary', ''))[:200] + "...",
                    "contact_info": "View Business Page",
                    "action_link": entry.link,
                    "time_ago": "Recent Listing",
                    "lead_type": "maps"
                })
    except Exception as e:
        print("RSS Maps error:", e)

    return leads

def fetch_social_leads(skill, country):
    leads = []
    country_str = country.strip().title() if country else "Pakistan"
    
    social_queries = [
        (f'"{skill}" ("hiring" OR "looking for" OR "need developer" OR "client") "{country_str}" site:facebook.com', "Facebook Lead"),
        (f'"{skill}" ("hiring" OR "project" OR "looking for") "{country_str}" site:linkedin.com', "LinkedIn Lead"),
        (f'"{skill}" ("hiring" OR "freelance" OR "job") site:reddit.com', "Reddit Lead")
    ]

    for q, platform_label in social_queries:
        try:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en"
            res = requests.get(url, headers=WEB_HEADERS, timeout=5)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                for entry in feed.entries[:8]:
                    leads.append({
                        "platform": platform_label,
                        "title": clean_html(entry.title),
                        "description": clean_html(getattr(entry, 'summary', ''))[:220] + "...",
                        "contact_info": "Social Direct",
                        "action_link": entry.link,
                        "time_ago": "Recently Posted",
                        "lead_type": "social"
                    })
        except Exception as e:
            print(f"Social RSS error ({platform_label}):", e)

    return leads

def fetch_job_leads(skill):
    leads = []
    job_queries = [
        (f'"{skill}" site:upwork.com/jobs', "Upwork Job"),
        (f'"{skill}" site:remoteok.com', "RemoteOK Job"),
        (f'"{skill}" site:freelancer.com/projects', "Freelancer Project")
    ]

    for q, platform_label in job_queries:
        try:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en"
            res = requests.get(url, headers=WEB_HEADERS, timeout=5)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                for entry in feed.entries[:8]:
                    leads.append({
                        "platform": platform_label,
                        "title": clean_html(entry.title),
                        "description": clean_html(getattr(entry, 'summary', ''))[:200] + "...",
                        "contact_info": "Job Portal Link",
                        "action_link": entry.link,
                        "time_ago": "Posted Recently",
                        "lead_type": "job"
                    })
        except Exception as e:
            print(f"Job RSS error ({platform_label}):", e)

    return leads

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/leads', methods=['GET'])
def get_leads():
    skill = request.args.get('skill', 'Web Development').strip()
    country = request.args.get('country', 'Pakistan').strip()

    all_leads = []
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        f_maps = executor.submit(fetch_google_maps_leads, skill, country)
        f_social = executor.submit(fetch_social_leads, skill, country)
        f_jobs = executor.submit(fetch_job_leads, skill)

        all_leads.extend(f_maps.result())
        all_leads.extend(f_social.result())
        all_leads.extend(f_jobs.result())

    return jsonify({
        "status": "success",
        "count": len(all_leads),
        "leads": all_leads
    })

@app.route('/api/ai_pitch', methods=['POST'])
def generate_ai_pitch():
    data = request.json or {}
    lead_title = data.get("lead_title", "")
    lead_desc = data.get("lead_desc", "")
    user_portfolio = data.get("portfolio", "https://myportfolio.com")

    if not ai_client:
        pitch = f"""Hi there!

I noticed your request regarding "{lead_title}".

I specialize in delivering high-converting, tailored solutions that directly align with your requirements. Here is a link to my past work & portfolio:
{user_portfolio}

Let's schedule a short 5-minute call or conversation to discuss how we can complete this efficiently!

Best regards,"""
        return jsonify({"status": "success", "pitch": pitch, "mode": "template"})

    prompt = f"""You are an expert sales representative and freelancer.
Write a concise, high-converting 3-paragraph outreach pitch/proposal for the following client opportunity.

Lead Title: {lead_title}
Lead Details: {lead_desc}
Freelancer Portfolio: {user_portfolio}

Instructions:
1. Directly address the client's problem or goal.
2. Highlight key relevant skills and value proposition.
3. End with a clear, professional call to action.
Keep it direct and compelling without generic fluff.
"""

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
      
