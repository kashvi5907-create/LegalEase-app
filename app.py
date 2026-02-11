import streamlit as st
import streamlit.components.v1 as components
import fitz  # PyMuPDF
import re
import html
import textwrap
from huggingface_hub import InferenceClient
import os
import time
from dotenv import load_dotenv
import easyocr
from pdf2image import convert_from_bytes
import numpy as np
from PIL import Image
import json
import datetime

# Google Calendar API Imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Load environment variables
load_dotenv()

# --- GOOGLE CALENDAR HELPER ---
SCOPES = ['https://www.googleapis.com/auth/calendar']

def sync_deadlines_to_calendar(deadlines, filename):
    """
    Syncs a list of deadlines to the user's primary Google Calendar.
    deadlines: list of dicts {'obligation': str, 'date': str (YYYY-MM-DD)}
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None # Force re-auth
        
        if not creds:
            if not os.path.exists('credentials.json'):
                return False, "Missing 'credentials.json'. Please add your Google Cloud credentials to the project root to enable Calendar Sync."
            
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                # Run local server for auth - FIXED PORT 8080 for Google Console matching
                creds = flow.run_local_server(port=8080)
            except Exception as e:
                return False, f"Authentication failed: {str(e)}"
        
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)
        
        created_count = 0
        for item in deadlines:
            date_str = item.get('date', 'N/A')
            obligation = item.get('obligation', 'Unknown Obligation')
            
            # Skip invalid dates or "N/A"
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
                continue
                
            event = {
                'summary': f'⚠️ LegalEase Deadline: {obligation}',
                'description': f'Extracted from {filename} by LegalEase AI.',
                'start': {
                    'date': date_str,
                    'timeZone': 'UTC',
                },
                'end': {
                    'date': date_str,
                    'timeZone': 'UTC',
                },
            }
            
            service.events().insert(calendarId='primary', body=event).execute()
            created_count += 1
            
        return True, f"Successfully created {created_count} events in your Google Calendar!"
        
    except Exception as e:
        return False, f"Calendar API Error: {str(e)}"

def extract_deadlines_with_ai(text, client, model):
    """
    Uses the LLM to extract deadlines and obligations from text.
    Returns a list of dicts.
    """
    if not client:
        return []

    # Prompt for structured extraction
    prompt = f"""
    Analyze the following contract text and extract all specific deadlines, notice periods, expiration dates, and payment due dates.
    
    Return the result ONLY as a JSON array of objects. Format:
    [
      {{
        "obligation": "Short description of the task or event",
        "date": "YYYY-MM-DD"
      }}
    ]
    
    Rules:
    1. If a date is absolute (e.g., "January 15, 2024"), convert to YYYY-MM-DD.
    2. If a date is relative (e.g., "30 days after signing"), calculate the estimated date assuming the signing date is TODAY ({datetime.date.today().isoformat()}).
    3. If no specific date can be determined, DO NOT include it in the list.
    4. Return ONLY the JSON array. No markdown, no explanations.
    
    Contract Text (Snippet):
    {text[:8000]}
    """
    
    try:
        messages = [{"role": "user", "content": prompt}]
        response = client.chat_completion(
            model=model,
            messages=messages,
            max_tokens=1500,
            temperature=0.1 # Low temperature for consistent formatting
        )
        content = response.choices[0].message.content.strip()
        
        # Clean up potential markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        data = json.loads(content)
        return data
    except Exception as e:
        print(f"Extraction Error: {e}")
        # Return a dummy error item or empty list
        return []

def scan_for_red_flags(full_text):
    keywords = ['Termination', 'Fees', 'Personal Data', 'Automatic Renewal']
    found_red_flags = {} # Dictionary to store keyword -> list of snippets
    
    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), full_text, re.IGNORECASE):
            start_idx = match.start()
            end_idx = match.end()
            context_start = max(0, start_idx - 200)
            context_end = min(len(full_text), end_idx + 200)
            snippet = full_text[context_start:context_end].strip()
            if context_start > 0: snippet = "..." + snippet
            if context_end < len(full_text): snippet = snippet + "..."
            
            if keyword not in found_red_flags: found_red_flags[keyword] = []
            if snippet not in found_red_flags[keyword]: found_red_flags[keyword].append(snippet)

    # --- CALCULATE RISK SCORE ---
    risk_score = 1
    if found_red_flags:
        risk_score += len(found_red_flags) * 2
    risk_score = min(10, risk_score)
    
    return found_red_flags, risk_score

def render_landing_page():
    # Dark Neon Wave Theme & Landing Page CSS
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
        
        /* Global Reset & Dark Neon Background */
        .stApp {
            background-color: #050511 !important;
            background-image: 
                radial-gradient(circle at 85% 20%, rgba(236, 72, 153, 0.15) 0%, transparent 40%),
                radial-gradient(circle at 10% 60%, rgba(99, 102, 241, 0.15) 0%, transparent 40%),
                linear-gradient(135deg, #0f0c29 0%, #1a1b4b 50%, #0f0c29 100%) !important;
            background-attachment: fixed !important;
            color: #ffffff !important;
        }
        
        /* Typography Override */
        h1, h2, h3, h4, p, a {
            font-family: 'Inter', sans-serif !important;
            color: #ffffff;
        }
        
        /* Hide Streamlit Chrome */
        header[data-testid="stHeader"] { background: transparent !important; }
        footer { display: none !important; }
        #MainMenu { display: none !important; }
        [data-testid="stToolbar"] { display: none !important; }
        .stAppDeployButton { display: none !important; }
        [data-testid="stDecoration"] { display: none !important; }
        [data-testid="stStatusWidget"] { display: none !important; }
        
        /* Force Sidebar HIDDEN - LANDING PAGE */
        section[data-testid="stSidebar"] {
            display: none !important;
            visibility: hidden !important;
        }
        [data-testid="stSidebarCollapsedControl"] {
            display: none !important;
            visibility: hidden !important;
        }
        section[data-testid="stSidebar"] > div > div > button { 
             /* Hide Close Button Only */
             display: none !important; 
        }

        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 5rem !important;
            max-width: 1200px !important;
        }
        
        /* Navbar */
        .navbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1.5rem 0;
            margin-bottom: 4rem;
            background: transparent !important;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .logo-box {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .logo-icon {
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, #ec4899 0%, #8b5cf6 100%);
            border-radius: 6px;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
        }
        .logo-text {
            font-size: 1.25rem;
            font-weight: 700;
            letter-spacing: -0.5px;
            color: #ffffff !important;
        }
        .nav-items {
            display: flex;
            gap: 32px;
        }
        .nav-item {
            color: rgba(255, 255, 255, 0.7) !important;
            font-weight: 500;
            font-size: 0.95rem;
            cursor: pointer;
            transition: color 0.2s;
        }
        .nav-item:hover {
            color: #ec4899 !important;
        }
        
        /* Hero Section */
        .hero-blob {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100vh;
            /* Abstract Wave Graphic using pseudo-element or background */
            background-image: url('https://images.unsplash.com/photo-1550684848-fac1c5b4e853?ixlib=rb-4.0.3&auto=format&fit=crop&w=2000&q=80');
            background-size: cover;
            background-position: center;
            opacity: 0.2;
            mix-blend-mode: screen;
            z-index: -1;
            pointer-events: none;
        }

        .hero-title {
            font-size: 4.5rem;
            font-weight: 800;
            line-height: 1.1;
            letter-spacing: -1.5px;
            margin-bottom: 1.5rem;
            color: #ffffff !important;
            position: relative;
            text-shadow: 0 0 40px rgba(236, 72, 153, 0.3);
        }
        .hero-subtitle {
            font-size: 1.35rem;
            color: rgba(255, 255, 255, 0.8) !important;
            line-height: 1.6;
            margin-bottom: 2.5rem;
            max-width: 540px;
            font-weight: 400;
        }
        
        /* Buttons - Neon Gradient */
        .stButton > button {
            background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%) !important;
            color: white !important;
            border: none !important;
            padding: 0.75rem 2.5rem !important;
            font-weight: 600 !important;
            border-radius: 50px !important; /* Pill shape */
            transition: all 0.3s ease !important;
            box-shadow: 0 4px 15px rgba(236, 72, 153, 0.4) !important;
        }
        .stButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(236, 72, 153, 0.6) !important;
            filter: brightness(1.1);
        }
        
        /* Cards - Glassmorphism Dark */
        .feature-card {
            background: rgba(255, 255, 255, 0.03);
            padding: 2.5rem;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            height: 100%;
            backdrop-filter: blur(10px);
            position: relative;
            overflow: hidden;
        }
        .feature-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, #ec4899, #8b5cf6);
            opacity: 0;
            transition: opacity 0.3s ease;
        }
        .feature-card:hover {
            transform: translateY(-5px);
            background: rgba(255, 255, 255, 0.07);
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 20px 40px -10px rgba(0,0,0,0.3);
        }
        .feature-card:hover::before {
            opacity: 1;
        }
        .card-icon {
            font-size: 2.5rem;
            margin-bottom: 1.5rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 64px;
            height: 64px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .card-title {
            font-weight: 700;
            font-size: 1.1rem;
            margin-bottom: 0.5rem;
            color: #ffffff !important;
        }
        .card-text {
            color: rgba(255, 255, 255, 0.7) !important;
            font-size: 0.95rem;
            line-height: 1.5;
        }
        
        /* Section Headers */
        .section-header {
            text-align: center;
            margin-bottom: 3rem;
        }
        .section-badge {
            background: rgba(255, 255, 255, 0.1);
            color: #ec4899 !important;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            display: inline-block;
            margin-bottom: 1rem;
            border: 1px solid rgba(236, 72, 153, 0.3);
        }
        </style>
        
        <!-- Hero Background Blob -->
        <div class="hero-blob"></div>

        <!-- Navbar Structure -->
        <div class="navbar">
            <div class="logo-box">
                <div class="logo-icon">L</div>
                <div class="logo-text">LegalEase</div>
            </div>
            <div class="nav-items">
                <span class="nav-item">Product</span>
                <span class="nav-item">Solutions</span>
                <span class="nav-item">Pricing</span>
                <span class="nav-item">Login</span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Hero Section
    col1, col2 = st.columns([1.1, 1])
    
    with col1:
        st.markdown('<div style="height: 40px;"></div>', unsafe_allow_html=True)
        st.markdown('<h1 class="hero-title">Contract Review,<br>Reimagined.</h1>', unsafe_allow_html=True)
        st.markdown('<p class="hero-subtitle">The first AI legal assistant that runs entirely on your device. Analyze risks, redline documents, and negotiate with confidence—without your data ever leaving your computer.</p>', unsafe_allow_html=True)
        
        # Call to Action
        st.markdown('<div style="display: flex; gap: 15px;">', unsafe_allow_html=True)
        if st.button("Start Free Analysis", type="primary"):
            st.session_state["show_landing"] = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown('<p style="font-size: 0.85rem; color: #94a3b8 !important; margin-top: 1rem;">✅ No credit card required &nbsp; • &nbsp; ✅ 100% Local Privacy</p>', unsafe_allow_html=True)

    with col2:
        # Custom Neon Glass Scales of Justice SVG - Minified to ensure no indentation issues
        scales_svg = """<div style="display: flex; justify-content: center; align-items: center; height: 100%;"><svg width="400" height="400" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="poleGradient" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" style="stop-color:#2e1065;stop-opacity:1" /><stop offset="50%" style="stop-color:#8b5cf6;stop-opacity:1" /><stop offset="100%" style="stop-color:#2e1065;stop-opacity:1" /></linearGradient><linearGradient id="goldGradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#fef3c7;stop-opacity:1" /><stop offset="100%" style="stop-color:#d97706;stop-opacity:1" /></linearGradient><linearGradient id="glassGradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:rgba(255,255,255,0.1)" /><stop offset="100%" style="stop-color:rgba(255,255,255,0.05)" /></linearGradient><filter id="neonGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4" result="coloredBlur"/><feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><circle cx="50" cy="50" r="20" fill="#ec4899" opacity="0.2"><animate attributeName="cy" values="50;60;50" dur="4s" repeatCount="indefinite" /></circle><circle cx="350" cy="300" r="30" fill="#8b5cf6" opacity="0.2"><animate attributeName="cy" values="300;290;300" dur="5s" repeatCount="indefinite" /></circle><g transform="translate(200, 200)"><rect x="-5" y="-150" width="10" height="300" rx="5" fill="url(#poleGradient)" filter="url(#neonGlow)" /><circle cx="0" cy="-150" r="10" fill="#ec4899" /><path d="M-40 150 Q0 130 40 150 L40 160 L-40 160 Z" fill="#2e1065" stroke="#8b5cf6" stroke-width="2" /><g><animateTransform attributeName="transform" type="rotate" values="-5;5;-5" dur="6s" repeatCount="indefinite" /><path d="M-120 -120 Q0 -140 120 -120" stroke="#ec4899" stroke-width="4" fill="none" filter="url(#neonGlow)" /><circle cx="0" cy="-130" r="6" fill="#ffffff" /><g transform="translate(-120, -120)"><line x1="0" y1="0" x2="-30" y2="80" stroke="rgba(255,255,255,0.4)" stroke-width="1" /><line x1="0" y1="0" x2="30" y2="80" stroke="rgba(255,255,255,0.4)" stroke-width="1" /><path d="M-40 80 Q0 120 40 80 Z" fill="url(#glassGradient)" stroke="#8b5cf6" stroke-width="2" /><circle cx="0" cy="85" r="10" fill="url(#goldGradient)" opacity="0.8" /></g><g transform="translate(120, -120)"><line x1="0" y1="0" x2="-30" y2="80" stroke="rgba(255,255,255,0.4)" stroke-width="1" /><line x1="0" y1="0" x2="30" y2="80" stroke="rgba(255,255,255,0.4)" stroke-width="1" /><path d="M-40 80 Q0 120 40 80 Z" fill="url(#glassGradient)" stroke="#ec4899" stroke-width="2" /><rect x="-8" y="75" width="16" height="20" fill="rgba(255,255,255,0.8)" rx="2" /></g></g></g></svg></div>"""
        st.markdown(scales_svg, unsafe_allow_html=True)



    # Trusted By section removed

    # Features
    st.markdown("""
        <div class="section-header">
            <span class="section-badge">Features</span>
            <h2 style="font-size: 2.5rem; font-weight: 700; margin-top: 1rem;">Everything you need to sign faster</h2>
        </div>
        
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 30px; margin-bottom: 8rem;">
            <div class="feature-card">
                <div class="card-icon">🛡️</div>
                <div class="card-title">Instant Red-Flag Detection</div>
                <div class="card-text">Automatically identify risky clauses, hidden fees, and auto-renewals. Our AI highlights exactly what you need to worry about.</div>
            </div>
            <div class="feature-card">
                <div class="card-icon">🔒</div>
                <div class="card-title">100% Local Privacy</div>
                <div class="card-text">Your sensitive contracts never leave your machine. We use optimized local LLMs to ensure zero data leakage.</div>
            </div>
            <div class="feature-card">
                <div class="card-icon">⚡</div>
                <div class="card-title">Smart Negotiation</div>
                <div class="card-text">Get instant suggestions for counter-arguments and redlines based on your playbook and industry standards.</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

def main():
    st.set_page_config(
        page_title="LegalEase",
        page_icon="pdf_extractor/logo.svg",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Initialize session state for landing page
    if "show_landing" not in st.session_state:
        st.session_state["show_landing"] = True

    # Initialize client to None to avoid UnboundLocalError
    client = None

    if st.session_state["show_landing"]:
        render_landing_page()
        return

    # --- Sidebar Navigation (DASHBOARD ONLY) ---
    with st.sidebar:
        # 1. Custom Logo
        st.markdown("""
            <div class="logo-box">
                <div class="logo-icon">L</div>
                <div class="logo-text">LegalEase</div>
            </div>
        """, unsafe_allow_html=True)
        
        # Initialize navigation state if not exists
        if "nav_selection" not in st.session_state:
            st.session_state.nav_selection = "📄 Upload Document"
        
        # 2. Navigation Menu
        # Using emojis as icons to match the visual style
        selected_nav = st.radio(
            "Navigation",
            ["📄 Upload Document", "🏠 Dashboard", "✍️ Contract Editor", "⚔️ Compare Contract"],
            key="nav_selection",
            label_visibility="collapsed"
        )
        
        # --- MULTI-DOCUMENT WORKSPACE SIDEBAR ---
        # (Moved to end of script to ensure immediate update after upload)

        
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        
        # 3. API Key & Settings (HIDDEN)
        # st.markdown('<div class="settings-label">SETTINGS</div>', unsafe_allow_html=True)
        
        # Try to get API key from Streamlit secrets (for deployment) or environment variable (for local)
        env_api_key = st.secrets.get("HF_TOKEN") or os.getenv("HF_TOKEN")
        
        # If still not found, check if it's in session state (from manual input)
        user_api_key = st.session_state.get("user_api_key", env_api_key)
        
        if not user_api_key:
            st.markdown('<div class="settings-label" style="margin-top: 20px;">AI SETTINGS</div>', unsafe_allow_html=True)
            input_key = st.text_input(
                "Hugging Face Access Token", 
                type="password",
                placeholder="hf_...",
                help="Enter your Hugging Face token to enable AI Analysis. You can get one at hf.co/settings/tokens"
            )
            
            if input_key:
                if st.button("💾 Apply Token"):
                    st.session_state["user_api_key"] = input_key
                    st.success("Token applied!")
                    st.rerun()
        else:
            # Hide input if key exists, but allow clearing it
            st.session_state["user_api_key"] = user_api_key
            if st.sidebar.button("🗑️ Clear Token"):
                del st.session_state["user_api_key"]
                st.rerun()
            
        # Status Indicator
        if user_api_key or os.getenv("HF_TOKEN"):
             # st.markdown("""
             # <div class="status-box">
             #    <span>AI Connected</span>
             #    <span class="status-indicator status-connected"></span>
             # </div>
             # """, unsafe_allow_html=True)
             st.session_state["user_api_key"] = user_api_key
        # else:
        #      st.markdown("""
        #      <div class="status-box" style="background: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.2); color: #fca5a5;">
        #         <span>AI Disconnected</span>
        #         <span class="status-indicator" style="background: #ef4444; box-shadow: 0 0 10px #ef4444;"></span>
        #      </div>
        #      """, unsafe_allow_html=True)

        # Model Selection
        # st.markdown('<div class="settings-label" style="margin-top: 20px;">AI MODEL</div>', unsafe_allow_html=True)
        model_options = [
            "meta-llama/Meta-Llama-3-8B-Instruct",
            "HuggingFaceH4/zephyr-7b-beta",
            "microsoft/Phi-3-mini-4k-instruct",
            "mistralai/Mistral-7B-Instruct-v0.2" 
        ]
        
        # selected_model = st.selectbox(
        #     "Select Model",
        #     model_options,
        #     index=0,
        #     label_visibility="collapsed"
        # )
        selected_model = model_options[0]
        st.session_state["selected_model"] = selected_model

        # AI Client Setup
        if user_api_key:
            try:
                client = InferenceClient(token=user_api_key)
            except Exception as e:
                # st.error(f"Failed to initialize AI client: {e}")
                print(f"Failed to initialize AI client: {e}")
        
        # Debug: Check Connection
        # with st.expander("Test Connection"):
        #     if st.button("Ping API"):
        #         try:
        #             with st.spinner("Pinging Hugging Face..."):
        #                 if 'client' in locals():
        #                     # Simple test generation
        #                     client.text_generation("Test", model=selected_model, max_new_tokens=1)
        #                     st.success(f"Connected to {selected_model}")
        #                 else:
        #                     st.error("Client not initialized. Please enter a valid Token first.")
        #         except Exception as e:
        #             st.error(f"Connection failed: {e}")
        
    # Custom CSS for Dashboard Theme
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
        
        /* FORCE SIDEBAR VISIBILITY OVERRIDE */
        section[data-testid="stSidebar"] {
            display: block !important;
            visibility: visible !important;
            width: 240px !important;
            min-width: 240px !important;
            max-width: 240px !important;
            transform: translateX(0px) !important;
        }
        [data-testid="stSidebarCollapsedControl"] {
            display: none !important;
            visibility: hidden !important;
        }
        
        /* HIDE SIDEBAR CLOSE BUTTON (Arrow Icon) */
        section[data-testid="stSidebar"] > div > div > button {
            display: none !important;
        }
        section[data-testid="stSidebar"] button {
            display: none !important;
        }

        /* Global Dark Neon Theme */
        .stApp {
            background-color: #050511 !important;
            background-image: 
                radial-gradient(circle at 85% 20%, rgba(236, 72, 153, 0.15) 0%, transparent 40%),
                radial-gradient(circle at 10% 60%, rgba(99, 102, 241, 0.15) 0%, transparent 40%),
                linear-gradient(135deg, #0f0c29 0%, #1a1b4b 50%, #0f0c29 100%) !important;
            background-attachment: fixed !important;
            color: #ffffff !important;
        }
        
        /* Typography */
        h1, h2, h3, h4, p, a, li, .stMarkdown, .stText, label, input, textarea {
            font-family: 'Inter', sans-serif !important;
            color: #ffffff !important;
        }

        /* Hide Streamlit Chrome & Toolbar */
        header[data-testid="stHeader"] { 
            background: transparent !important;
            visibility: visible !important;
            z-index: 100000 !important;
        }
        [data-testid="stToolbar"] { display: none !important; }
        .stAppDeployButton { display: none !important; }
        [data-testid="stDecoration"] { display: none !important; }
        [data-testid="stStatusWidget"] { display: none !important; }
        
        /* Force Sidebar Open & Hide Close Button - TEMPORARILY DISABLED TO RESTORE VISIBILITY */
        /* [data-testid="stSidebarCollapsedControl"] { display: none !important; } */
        /* section[data-testid="stSidebar"] > div > div > button { display: none !important; } */
        /* button[kind="header"] { display: none !important; } */
        
        /* HIDE NATIVE FILE UPLOADER LIST & PAGINATION */
        [data-testid='stFileUploader'] ul {
            display: none !important;
        }
        [data-testid='stFileUploader'] .stPagination {
            display: none !important;
        }
        [data-testid='stFileUploader'] small {
            display: none !important;
        }
        /* Hide everything below the dropzone (catches loose pagination buttons and file list container) */
        /* Corrected testid from stFileUploadDropzone to stFileUploaderDropzone */
        [data-testid='stFileUploader'] [data-testid='stFileUploaderDropzone'] ~ div,
        [data-testid='stFileUploader'] [data-testid='stFileUploaderDropzone'] ~ section,
        [data-testid='stFileUploader'] [data-testid='stFileUploaderDropzone'] ~ ul {
            display: none !important;
        }
        
        /* AGGRESSIVE: Hide all buttons in uploader except the browse button */
        [data-testid='stFileUploader'] button {
            display: none !important;
        }
        [data-testid='stFileUploader'] [data-testid='stFileUploaderDropzone'] button {
            display: inline-flex !important;
        }
        [data-testid="stSidebarNavItems"] { padding-top: 2rem; }

        h1 { font-size: 2.2rem !important; font-weight: 800 !important; letter-spacing: -1px; }
        h2 { font-size: 1.8rem !important; font-weight: 700 !important; }
        h3 { font-size: 1.4rem !important; font-weight: 600 !important; }
        
        /* Adjust base paragraph size without breaking components */
        .stMarkdown p { font-size: 0.95rem !important; line-height: 1.6; }

        /* Buttons - Neon Gradient */
        .stButton > button {
            background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%) !important;
            color: white !important;
            border: none !important;
            padding: 0.6rem 1.5rem !important;
            font-weight: 600 !important;
            border-radius: 12px !important;
            transition: all 0.3s ease !important;
            box-shadow: 0 4px 15px rgba(236, 72, 153, 0.4) !important;
        }
        .stButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(236, 72, 153, 0.6) !important;
            filter: brightness(1.1);
        }

        /* Inputs & Selectboxes - Glassmorphism */
        .stTextInput input, .stSelectbox div[data-baseweb="select"] > div {
            background-color: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            color: white !important;
            border-radius: 12px !important;
        }
        
        /* Ensure text inside Selectbox is visible */
        .stSelectbox div[data-baseweb="select"] span {
            color: white !important;
        }
        
        .stTextInput input:focus, .stSelectbox div[data-baseweb="select"] > div:focus-within {
            border-color: #ec4899 !important;
            box-shadow: 0 0 10px rgba(236, 72, 153, 0.2) !important;
        }
        
        /* Metrics - Glassmorphism */
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.03);
            padding: 20px;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.2s;
        }
        [data-testid="stMetric"]:hover {
            transform: translateY(-2px);
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 255, 255, 0.2);
        }
        [data-testid="stMetricLabel"] {
            color: rgba(255, 255, 255, 0.7) !important;
            font-size: 0.9rem !important;
        }
        [data-testid="stMetricValue"] {
            color: white !important;
            font-size: 2rem !important;
            font-weight: 700 !important;
            text-shadow: 0 0 20px rgba(139, 92, 246, 0.3);
        }

        /* Fix for Streamlit Alerts/Notifications - Text Visibility */
        div[data-baseweb="notification"], 
        div[data-baseweb="alert"],
        div[data-testid="stAlert"] {
            color: #ffffff !important;
            background-color: rgba(255, 255, 255, 0.03) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            backdrop-filter: blur(10px) !important;
            border-radius: 12px !important;
        }
        
        div[data-baseweb="notification"] *, 
        div[data-baseweb="alert"] *,
        div[data-testid="stAlert"] * {
            color: #ffffff !important;
        }

        /* Specific fix for Custom HTML Explanation Box (overriding global div rule) */
        .explanation-box, 
        .explanation-box * {
            color: #ffffff !important;
        }

        /* Styled Table (Glassmorphism) */
        [data-testid="stTable"] {
            background: rgba(255, 255, 255, 0.03) !important;
            backdrop-filter: blur(10px) !important;
            border-radius: 12px !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            overflow: hidden !important;
            margin-bottom: 1rem !important;
        }
        [data-testid="stTable"] table {
            border-collapse: collapse !important;
            width: 100% !important;
        }
        [data-testid="stTable"] th {
            background-color: rgba(255, 255, 255, 0.1) !important;
            color: rgba(255, 255, 255, 0.9) !important;
            font-weight: 600 !important;
            padding: 12px 16px !important;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important;
            text-align: left !important;
        }
        [data-testid="stTable"] td {
            padding: 12px 16px !important;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05) !important;
            color: rgba(255, 255, 255, 0.8) !important;
        }
        [data-testid="stTable"] tr:last-child td {
            border-bottom: none !important;
        }
        [data-testid="stTable"] tr:hover td {
            background-color: rgba(255, 255, 255, 0.05) !important;
        }
        
        /* Hide Index in st.table */
        [data-testid="stTable"] .blank { display: none !important; }
        [data-testid="stTable"] .row_heading { display: none !important; }
        [data-testid="stTable"] tbody th { display: none !important; }
        [data-testid="stTable"] thead th:first-child { display: none !important; }
        
        /* Sidebar Styling */
        section[data-testid="stSidebar"] {
            background-color: rgba(15, 12, 41, 0.95) !important;
            border-right: 1px solid rgba(255, 255, 255, 0.1) !important;
            backdrop-filter: blur(10px);
        }
        
        /* Custom Logo Style */
        .sidebar-logo-container {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 0px 0 20px 0; /* Reduced top padding */
            margin-bottom: 40px; /* Increased gap */
        }
        
        /* Reduce Sidebar Top Padding to move Logo Up */
        section[data-testid="stSidebar"] .block-container {
            padding-top: 0rem !important;
            padding-bottom: 0rem !important;
            margin-top: -60px !important;
        }
        .sidebar-logo-text {
            font-size: 20px;
            font-weight: 700;
            color: #ffffff;
            font-family: 'Inter', sans-serif;
            letter-spacing: -0.5px;
        }
        
        /* Sidebar Navigation (Radio Button Styling) */
        div[role="radiogroup"] {
            gap: 4px;
            display: flex;
            flex-direction: column;
            background-color: transparent;
        }
        
        div[role="radiogroup"] label {
            background-color: transparent;
            padding: 8px 12px;
            border-radius: 10px;
            color: rgba(255, 255, 255, 0.6) !important;
            transition: all 0.2s ease;
            border: 1px solid transparent;
            margin-bottom: 0px;
            cursor: pointer;
            display: flex;
            align-items: center;
            width: 100%;
        }
        
        div[role="radiogroup"] label:hover {
            background-color: rgba(255, 255, 255, 0.05);
            color: #ffffff !important;
        }
        
        /* Active Item Styling - Neon Gradient */
        div[role="radiogroup"] label:has(input:checked),
        div[role="radiogroup"] label:has(input[aria-checked="true"]),
        div[role="radiogroup"] label[data-checked="true"] {
            background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%) !important; /* Neon Pink to Purple */
            color: #ffffff !important;
            font-weight: 600;
            border: none;
            box-shadow: 0 4px 15px rgba(236, 72, 153, 0.4);
        }
        
        /* Hide the actual radio circle */
        div[role="radiogroup"] label > div:first-child {
            display: none !important;
        }
        
        div[role="radiogroup"] label p {
            font-size: 13px !important;
            font-weight: 500 !important;
            margin: 0 !important;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        /* Monochrome Icons: Grey by default, White when active */
        div[role="radiogroup"] label p {
            filter: grayscale(100%) opacity(0.7); 
        }
        
        /* Force white icons/text when active */
        div[role="radiogroup"] label:has(input:checked) p,
        div[role="radiogroup"] label:has(input[aria-checked="true"]) p,
        div[role="radiogroup"] label[data-checked="true"] p {
            filter: brightness(0) invert(1) !important;
            opacity: 1 !important;
        }

        /* Sidebar Input Fields (API Key) */
        [data-testid="stSidebar"] input {
            background-color: rgba(0, 0, 0, 0.3) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            color: #e2e8f0 !important;
            border-radius: 8px;
            padding: 10px 12px;
            font-size: 14px;
        }
        
        [data-testid="stSidebar"] input:focus {
            border-color: #ec4899 !important;
            color: #e2e8f0 !important;
            box-shadow: 0 0 10px rgba(236, 72, 153, 0.2) !important;
        }
        
        /* Sidebar Status Box - Neon Glass */
        .status-box {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: rgba(255, 255, 255, 0.8);
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 0.85rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: 15px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(5px);
        }
        
        .status-indicator {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }
        
        .status-connected {
            background: #22c55e;
            box-shadow: 0 0 10px #22c55e;
        }
        
        .status-disconnected {
            background: #ef4444;
            box-shadow: 0 0 10px #ef4444;
        }

        /* Sidebar Divider */
        .sidebar-divider {
            height: 1px;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
            margin: 20px 0;
            width: 100%;
        }
        
        /* Settings Label */
        .settings-label {
            color: #94a3b8;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 1px;
            margin-bottom: 15px;
            text-transform: uppercase;
            opacity: 0.8;
        }
        
        /* Header Styling */
        .header-style {
            font-size: 2.5rem;
            font-weight: 700;
            color: #f8fafc !important;
            margin-bottom: 0.5rem;
        }
        .sub-header {
            font-size: 1.1rem;
            color: #94a3b8 !important;
            margin-bottom: 2rem;
        }

        /* Metric Cards Grid */
        .metric-container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .metric-card {
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 24px;
            color: white !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            transition: all 0.3s ease;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            height: 160px;
            position: relative;
            overflow: hidden;
        }
        
        .metric-card:hover {
            transform: translateY(-5px);
            background: rgba(255, 255, 255, 0.07);
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 10px 20px rgba(0,0,0,0.2);
        }
        
        /* Gradient Backgrounds - Updated to Neon */
        .card-purple { background: linear-gradient(135deg, rgba(139, 92, 246, 0.2) 0%, rgba(99, 102, 241, 0.2) 100%); border: 1px solid rgba(139, 92, 246, 0.3); }
        .card-cyan { background: linear-gradient(135deg, rgba(6, 182, 212, 0.2) 0%, rgba(59, 130, 246, 0.2) 100%); border: 1px solid rgba(6, 182, 212, 0.3); }
        .card-blue { background: linear-gradient(135deg, rgba(59, 130, 246, 0.2) 0%, rgba(37, 99, 235, 0.2) 100%); border: 1px solid rgba(59, 130, 246, 0.3); }
        .card-orange { background: linear-gradient(135deg, rgba(245, 158, 11, 0.2) 0%, rgba(239, 68, 68, 0.2) 100%); border: 1px solid rgba(245, 158, 11, 0.3); }
        
        .metric-title {
            font-size: 1rem;
            font-weight: 500;
            opacity: 0.9;
            margin-bottom: 8px;
            color: white !important;
        }
        
        .metric-value {
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 0;
            color: white !important;
        }
        
        .metric-icon {
            position: absolute;
            right: 20px;
            bottom: 20px;
            background: rgba(255, 255, 255, 0.1);
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }

        /* Glassmorphism Content Containers */
        .content-box {
            background: rgba(255, 255, 255, 0.03);
            border-radius: 20px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            margin-bottom: 25px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
        }
        
        /* Tabs Styling */
        .stTabs [data-baseweb="tab-list"] {
            gap: 10px;
            background-color: transparent;
        }
        
        .stTabs [data-baseweb="tab"] {
            height: 50px;
            background-color: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            color: rgba(255, 255, 255, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 0 20px;
            transition: all 0.3s ease;
        }
        
        .stTabs [data-baseweb="tab"]:hover {
            background-color: rgba(255, 255, 255, 0.1);
            color: white;
        }
        
        .stTabs [data-baseweb="tab"][aria-selected="true"] {
            background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%);
            color: white;
            border: none;
            box-shadow: 0 4px 15px rgba(236, 72, 153, 0.3);
        }

        /* Hide default tab underline */
        .stTabs [data-baseweb="tab-highlight"] {
            background-color: transparent !important;
            height: 0px !important;
        }

        /* File Uploader - Neon Style */
        .stFileUploader {
            background-color: rgba(255, 255, 255, 0.03) !important;
            border: 2px dashed #8b5cf6 !important; /* Neon Purple */
            border-radius: 16px;
            padding: 1rem !important;
            transition: all 0.3s ease;
        }
        
        .stFileUploader:hover {
            border-color: #ec4899 !important; /* Neon Pink */
            background-color: rgba(255, 255, 255, 0.05) !important;
            box-shadow: 0 0 20px rgba(236, 72, 153, 0.1);
        }
        
        /* Inner Dropzone */
        [data-testid="stFileUploaderDropzone"] {
            background-color: rgba(15, 12, 41, 0.5) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 12px !important;
            padding: 10px !important;
        }

        /* Make text inside the uploader light and smaller */
        .stFileUploader div, .stFileUploader span, .stFileUploader small, .stFileUploader p {
            color: rgba(255, 255, 255, 0.8) !important;
            font-size: 0.75rem !important;
            font-family: 'Inter', sans-serif !important;
        }

        /* Browse Button - Neon Gradient */
        .stFileUploader button {
             background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%) !important;
             color: white !important;
             border: none !important;
             padding: 0.4rem 1rem !important;
             border-radius: 50px !important;
             font-weight: 600 !important;
             font-size: 0.8rem !important;
             transition: all 0.3s ease !important;
        }
        .stFileUploader button:hover {
             transform: translateY(-2px);
             box-shadow: 0 4px 15px rgba(236, 72, 153, 0.4) !important;
             filter: brightness(1.1);
        }
        
        [data-testid="stFileUploaderFileName"] {
            color: #e2e8f0 !important;
        }
        
        /* Red Flag Styling in Dark Mode - Neon Glass */
        .red-flag-card {
            background: rgba(239, 68, 68, 0.1); /* Red tint glass */
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
            border-left: 5px solid #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.2);
        }
        
        .red-flag-title {
            color: #fca5a5 !important;
            font-weight: 700;
            font-size: 1.2rem;
            margin-bottom: 10px;
            border-bottom: 1px solid rgba(239, 68, 68, 0.3);
            padding-bottom: 8px;
        }
        
        .red-flag-content {
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(10px);
            padding: 15px;
            border-radius: 8px;
            color: rgba(255, 255, 255, 0.9) !important;
            border: 1px solid rgba(255, 255, 255, 0.1);
            white-space: pre-wrap;
            font-family: 'Poppins', sans-serif !important;
            overflow-wrap: break-word;
        }
        
        .highlight {
            background-color: #7f1d1d;
            color: #fecaca !important;
            padding: 2px 6px;
            border-radius: 4px;
        }

        /* Negotiation Success Box */
        .negotiation-success {
            background-color: #064e3b; /* Dark Green */
            padding: 15px;
            border-radius: 8px;
            color: #ecfdf5 !important; /* Light Green Text */
            border: 1px solid #059669;
            white-space: pre-wrap; /* Preserve newlines but wrap text */
            font-family: 'Poppins', sans-serif !important; /* Force sans-serif */
            overflow-wrap: break-word; /* Ensure long words don't overflow */
            margin-top: 10px;
        }

        /* Contract Editor Specific */
        .editor-container {
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 30px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
            margin-top: 20px;
        }

        .original-clause-box {
            background: rgba(255, 255, 255, 0.03); /* Glass background */
            backdrop-filter: blur(10px);
            border-left: 4px solid #ef4444;
            padding: 20px;
            border-radius: 12px;
            color: rgba(255, 255, 255, 0.9) !important;
            font-family: 'Inter', sans-serif;
            font-size: 0.95rem;
            line-height: 1.6;
            margin-bottom: 15px;
            height: 100%;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .editor-label {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 700;
            color: #94a3b8 !important;
            margin-bottom: 10px;
            display: block;
        }
        
        .step-number {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            background-color: #3b82f6;
            color: white;
            border-radius: 50%;
            font-size: 12px;
            font-weight: bold;
            margin-right: 8px;
        }

        /* Summary Box - Neon Glass */
        .summary-box {
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(10px);
            border-left: 5px solid #8b5cf6;
            padding: 25px;
            border-radius: 12px;
            color: rgba(255, 255, 255, 0.9) !important;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }

        /* Floating Chat Button Styling */
        div[data-testid="stPopover"] {
            position: fixed !important;
            bottom: 30px !important;
            right: 30px !important;
            z-index: 9999 !important;
            width: auto !important;
            height: auto !important;
        }
        
        div[data-testid="stPopover"] button {
            width: 60px !important;
            height: 60px !important;
            border-radius: 50% !important;
            background: #4834d4 !important; /* Purple to match call icon style */
            color: white !important;
            box-shadow: 0 4px 15px rgba(72, 52, 212, 0.4) !important;
            border: none !important;
            transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) !important;
            font-size: 36px !important; /* Increased size */
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            padding: 0 !important;
            line-height: 1 !important;
        }

        /* Hide the default chevron/arrow if present - targeting all SVGs in the button */
        div[data-testid="stPopover"] button svg,
        div[data-testid="stPopover"] button span[data-testid="stArrowDown"],
        div[data-testid="stPopover"] button > div > div:nth-child(2) {
            display: none !important;
            opacity: 0 !important;
            width: 0 !important;
        }
        
        /* Force the emoji to be larger by targeting inner paragraph/divs */
        div[data-testid="stPopover"] button p {
            font-size: 36px !important;
            margin-bottom: 0 !important;
            line-height: 1 !important;
        }
        
        div[data-testid="stPopover"] button:hover {
            transform: scale(1.1) !important;
            background: #686de0 !important; /* Lighter purple on hover */
            box-shadow: 0 6px 20px rgba(72, 52, 212, 0.6) !important;
        }
        
        div[data-testid="stPopover"] button:active {
            transform: scale(0.95) !important;
        }



        /* Logo Styles for Sidebar */
        .logo-box {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
        }
        .logo-icon {
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, #ec4899 0%, #8b5cf6 100%);
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            color: white;
            font-size: 20px;
        }
        .logo-text {
            font-size: 24px;
            font-weight: 700;
            color: white;
            letter-spacing: -0.5px;
        }

        /* Popover Content Dark Mode */
        div[data-baseweb="popover"],
        div[data-baseweb="popover"] > div {
            background-color: #1e293b !important;
            color: #e2e8f0 !important;
        }
        
        /* Chat Input & Text Input Dark Mode (Scoped to Popover) */
        div[data-baseweb="popover"] .stTextInput input {
            background: rgba(255, 255, 255, 0.05) !important;
            color: #e2e8f0 !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 12px !important;
        }

        /* 
           AGGRESSIVE OVERRIDE FOR TEXT AREAS 
           Targeting all possible Streamlit text area containers to remove black background 
        */
        .stTextArea,
        .stTextArea > div,
        div[data-testid="stTextArea"],
        div[data-testid="stTextArea"] > div,
        div[data-baseweb="textarea"],
        div[data-baseweb="base-input"] {
            background-color: transparent !important;
            border: none !important;
        }

        .stTextArea textarea, 
        div[data-testid="stTextArea"] textarea,
        div[data-baseweb="textarea"] textarea,
        div[data-baseweb="base-input"] textarea,
        textarea {
            background-color: rgba(255, 255, 255, 0.03) !important;
            backdrop-filter: blur(10px) !important;
            color: rgba(255, 255, 255, 0.9) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-left: 5px solid #8b5cf6 !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important;
        }
        
        /* Ensure focus state is also nice */
        .stTextArea textarea:focus,
        div[data-testid="stTextArea"] textarea:focus,
        div[data-baseweb="textarea"] textarea:focus,
        textarea:focus {
             border-color: #ec4899 !important;
             box-shadow: 0 0 10px rgba(236, 72, 153, 0.2) !important;
        }

        /* 
           FIX FOR CODE BLOCKS IF USED 
           Streamlit code blocks (st.code) often have dark backgrounds. 
           This will force them to match the glassmorphism style.
        */
        .stCodeBlock, 
        div[data-testid="stCodeBlock"],
        pre, 
        code,
        .stMarkdown pre,
        .stMarkdown code {
            background-color: rgba(255, 255, 255, 0.03) !important;
            backdrop-filter: blur(10px) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-left: 5px solid #8b5cf6 !important;
            border-radius: 12px !important;
            color: #e2e8f0 !important;
        }
        
        /* Remove double-border effect if pre is inside another styled container */
        .red-flag-content pre,
        .red-flag-content code {
            background: transparent !important;
            border: none !important;
            padding: 0 !important;
            backdrop-filter: none !important;
        }

        .stCodeBlock code {
             background-color: transparent !important;
             color: #e2e8f0 !important;
        }


        /* Chat Message Backgrounds */
        .stChatMessage {
            background-color: transparent !important;
        }
        [data-testid="stChatMessageContent"] {
            background-color: #334155 !important;
            border-radius: 12px !important;
            padding: 12px !important;
            border: 1px solid #475569 !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # --- Navigation Logic ---
    if "Dashboard" in selected_nav:
        st.title("Dashboard")
        
        # Check if analysis data exists in session state (Updated for Multi-Doc Support)
        has_data = False
        if st.session_state.current_doc and st.session_state.current_doc in st.session_state.processed_docs:
            results = st.session_state.processed_docs[st.session_state.current_doc]
            has_data = True
            
        if has_data:
            risk_score = results["risk_score"]
            found_red_flags = results["found_red_flags"]
            full_text = results["full_text"]
            
            # --- DASHBOARD UI ---
            
            # Calculate Stats
            total_clauses = len(re.split(r'\.\s+', full_text)) # Approx sentence count
            safety_score = max(0, 100 - (risk_score * 10))
            if risk_score == 1 and not found_red_flags: safety_score = 100
            
            # 1. Top Metrics Columns
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric(label="Total Clauses", value=total_clauses)
            
            with col2:
                st.metric(label="Critical Flags", value=len(found_red_flags), delta=f"{len(found_red_flags)} Issues Found", delta_color="inverse")
                
            with col3:
                st.metric(label="Safety Score", value=f"{safety_score}%", delta="Based on analysis")

            # 2. Risk Meter
            st.write("Risk Meter")
            progress_color = "#22c55e" if safety_score > 80 else "#f59e0b" if safety_score > 50 else "#ef4444"
            st.markdown(f"""
                <div style="width: 100%; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; height: 24px; margin-bottom: 20px;">
                    <div style="width: {safety_score}%; background: linear-gradient(90deg, {progress_color}, {progress_color}); height: 100%; border-radius: 12px; transition: width 0.5s; box-shadow: 0 0 15px {progress_color}60;"></div>
                </div>
            """, unsafe_allow_html=True)
            
            # 3. Contract Health Overview Table
            st.subheader('Contract Health Overview')
            
            # Categorize found flags
            categories_map = {
                "Financial": ["Fees", "Payment", "Royalty"],
                "Legal": ["Termination", "Indemnity", "Liability", "Jurisdiction", "Personal Data"],
                "Operational": ["Automatic Renewal", "Delivery", "SLA", "Support"]
            }
            
            health_data = []
            
            for cat, cat_keywords in categories_map.items():
                # Count how many keywords from this category were found
                count = sum(1 for k in cat_keywords if k in found_red_flags)
                
                # Determine status
                if count == 0:
                    status = "✅ Low Risk"
                elif count == 1:
                    status = "⚠️ Medium Risk"
                else:
                    status = "🚨 High Risk"
                    
                health_data.append({
                    "Category": cat,
                    "Risk Level": status,
                    "Issues Found": count
                })
            
            st.table(health_data)
            
            # --- DEADLINES & REMINDERS (AI) ---
            st.markdown('<div style="margin-top: 30px;"></div>', unsafe_allow_html=True)
            st.subheader("📅 Deadlines & Reminders")
            
            # Check if deadlines already extracted
            if "deadlines" not in results:
                st.write("Extract key dates and obligations using AI.")
                if st.button("🔍 Scan for Deadlines", type="primary"):
                    if not client:
                        # Attempt to init client if missing (re-check session)
                        user_api_key = st.session_state.get("user_api_key")
                        if user_api_key:
                             try:
                                 client = InferenceClient(token=user_api_key)
                             except:
                                 pass
                    
                    if client:
                        with st.spinner("Analyzing contract for dates..."):
                            deadlines_data = extract_deadlines_with_ai(
                                full_text, 
                                client, 
                                st.session_state.get("selected_model", "mistralai/Mistral-7B-Instruct-v0.3")
                            )
                            # Save to session state
                            st.session_state.processed_docs[st.session_state.current_doc]["deadlines"] = deadlines_data
                            st.rerun()
                    else:
                        st.error("Please configure your AI Settings/API Key in the sidebar first.")
            
            # Display Deadlines Table
            if "deadlines" in results:
                deadlines = results["deadlines"]
                if deadlines:
                    # Convert to DataFrame for cleaner display (optional, but st.table handles list of dicts)
                    st.table(deadlines)
                    
                    # Sync Button
                    if st.button("📅 Sync All Deadlines to Google Calendar"):
                        with st.spinner("Syncing to Google Calendar..."):
                            success, msg = sync_deadlines_to_calendar(deadlines, st.session_state.current_doc)
                            if success:
                                st.success(msg)
                                st.balloons()
                            else:
                                st.error(msg)
                else:
                     st.info("No specific deadlines were found in this document.")
            
        else:
            # Empty State for Dashboard
            st.info("No document has been analyzed yet. Please go to 'Upload Document' to start.")
            
            # Show empty/placeholder UI to demonstrate layout
            col1, col2, col3 = st.columns(3)
            with col1: st.metric(label="Total Clauses", value="--")
            with col2: st.metric(label="Critical Flags", value="--")
            with col3: st.metric(label="Safety Score", value="--")
            
            st.write("Risk Meter")
            st.markdown("""
                <div style="width: 100%; background-color: #334155; border-radius: 10px; height: 24px; margin-bottom: 20px;">
                    <div style="width: 0%; background-color: #22c55e; height: 100%; border-radius: 10px;"></div>
                </div>
            """, unsafe_allow_html=True)
            
        return

    # Agenda section removed

    if "Compare Contract" in selected_nav:
        st.title("⚔️ Compare Contract Mode")
        st.markdown("Compare two contracts side-by-side to see which one is safer.")

        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Contract A")
            file_a = st.file_uploader("Upload Contract A", type="pdf", key="battle_a")
        
        with col2:
            st.subheader("Contract B")
            file_b = st.file_uploader("Upload Contract B", type="pdf", key="battle_b")

        if file_a and file_b:
            # Helper to extract text
            def extract_pdf_text(uploaded_file):
                # Reset pointer just in case
                uploaded_file.seek(0)
                doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
                text = ""
                for page in doc:
                    text += page.get_text(sort=True).strip() + "\n"
                return text

            with st.spinner("Analyzing fighters..."):
                text_a = extract_pdf_text(file_a)
                text_b = extract_pdf_text(file_b)
                
                flags_a, score_a = scan_for_red_flags(text_a)
                flags_b, score_b = scan_for_red_flags(text_b)

            # --- Comparison Table ---
            st.divider()
            st.subheader("🥊 Head-to-Head Stats")
            
            # Metric Columns
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Contract A Risks", len(flags_a), delta=-len(flags_a), delta_color="inverse")
            with m2:
                st.metric("Contract B Risks", len(flags_b), delta=-len(flags_b), delta_color="inverse")
            with m3:
                if len(flags_a) < len(flags_b):
                    winner = "Contract A"
                    color = "normal"
                elif len(flags_b) < len(flags_a):
                    winner = "Contract B"
                    color = "normal"
                else:
                    winner = "Tie"
                    color = "off"
                st.metric("Winner", winner, delta="Best Choice" if winner != "Tie" else None, delta_color=color)

            # Detailed Comparison Table
            st.subheader("🚩 Risk Comparison")
            
            all_keywords = sorted(list(set(list(flags_a.keys()) + list(flags_b.keys()))))
            comparison_data = []
            
            for k in all_keywords:
                in_a = "❌ Found" if k in flags_a else "✅ Clean"
                in_b = "❌ Found" if k in flags_b else "✅ Clean"
                comparison_data.append({
                    "Risk Category": k,
                    "Contract A": in_a,
                    "Contract B": in_b
                })
            
            st.table(comparison_data)
            
            # Side-by-Side Analysis for Common Risks
            common_risks = [k for k in flags_a if k in flags_b]
            if common_risks:
                st.subheader("⚔️ Clash of Clauses")
                st.write("Comparing specific wording for shared risks:")
                
                for risk in common_risks:
                    with st.expander(f"Compare: {risk}", expanded=True):
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**Contract A ({risk})**")
                            # Show first snippet with highlight
                            snippet = flags_a[risk][0]
                            st.info(snippet) 
                        with c2:
                            st.markdown(f"**Contract B ({risk})**")
                            snippet = flags_b[risk][0]
                            st.warning(snippet)

            # Battle Summary
            st.divider()
            st.subheader("🏆 Battle Summary")
            
            if len(flags_a) < len(flags_b):
                st.success(f"**Recommendation: Choose Contract A**\n\nIt has fewer red flags ({len(flags_a)} vs {len(flags_b)}) and appears to be the more user-friendly option based on our automated scan.")
            elif len(flags_b) < len(flags_a):
                st.success(f"**Recommendation: Choose Contract B**\n\nIt has fewer red flags ({len(flags_b)} vs {len(flags_a)}) and appears to be the more user-friendly option based on our automated scan.")
            else:
                st.info("**It's a Tie!**\n\nBoth contracts have similar risk profiles. Please review the specific clauses above to decide based on wording preference.")

        return

    if "Contract Editor" in selected_nav:
        st.markdown('<div class="header-style">✍️ Contract Editor</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-header">Draft counter-proposals for detected red flags</div>', unsafe_allow_html=True)
        
        # Initialize contract edits in session state if not exists
        if "contract_edits" not in st.session_state:
            st.session_state["contract_edits"] = {}
        
        # Check if analysis data exists in session state (Updated for Multi-Doc Support)
        has_data = False
        if st.session_state.current_doc and st.session_state.current_doc in st.session_state.processed_docs:
            results = st.session_state.processed_docs[st.session_state.current_doc]
            has_data = True

        if has_data:
            found_red_flags = results["found_red_flags"]
            full_text = results["full_text"]
            
            if found_red_flags:
                # Header for the editor box
                st.markdown('<div class="editor-label" style="font-size: 1rem; color: #e2e8f0 !important; margin-bottom: 20px;">1. Select a Clause to Edit</div>', unsafe_allow_html=True)

                # Flatten flags for selection
                flag_options = []
                flag_map = {}
                
                for category, snippets in found_red_flags.items():
                    for i, snippet in enumerate(snippets):
                        # Clean up snippet for label
                        clean_snippet = snippet.replace("\n", " ").strip()
                        if clean_snippet.startswith("..."): clean_snippet = clean_snippet[3:]
                        if clean_snippet.endswith("..."): clean_snippet = clean_snippet[:-3]
                        
                        # Truncate for display
                        if len(clean_snippet) > 80:
                            display_snippet = clean_snippet[:80] + "..."
                        else:
                            display_snippet = clean_snippet
                            
                        label = f"{category}: {display_snippet}"
                        flag_options.append(label)
                        flag_map[label] = snippet
                
                # --- Navigation UI instead of Selectbox ---
                if "current_flag_index" not in st.session_state:
                    st.session_state.current_flag_index = 0
                    
                # Ensure index is valid
                if st.session_state.current_flag_index >= len(flag_options):
                    st.session_state.current_flag_index = 0
                
                current_idx = st.session_state.current_flag_index
                selected_option = flag_options[current_idx]
                
                # Show navigation only if multiple flags
                if len(flag_options) > 1:
                    c1, c2, c3 = st.columns([1, 4, 1])
                    with c1:
                        if st.button("⬅️ Prev", key="prev_flag"):
                            st.session_state.current_flag_index = (current_idx - 1) % len(flag_options)
                            st.rerun()
                    with c2:
                         st.markdown(f"<div style='text-align: center; color: rgba(255,255,255,0.7); padding-top: 5px;'>Clause {current_idx + 1} of {len(flag_options)}</div>", unsafe_allow_html=True)
                    with c3:
                        if st.button("Next ➡️", key="next_flag"):
                            st.session_state.current_flag_index = (current_idx + 1) % len(flag_options)
                            st.rerun()
                
                st.markdown('<div style="margin-bottom: 20px;"></div>', unsafe_allow_html=True)
                
                if selected_option:
                    original_clause = flag_map[selected_option]
                    
                    # Check if we have a saved edit for this clause
                    saved_edit = st.session_state["contract_edits"].get(original_clause)
                    
                    # Prepare clean text for editing (remove ellipses)
                    if saved_edit:
                        edit_default = saved_edit
                    else:
                        edit_default = original_clause
                        if edit_default.startswith("..."): edit_default = edit_default[3:]
                        if edit_default.endswith("..."): edit_default = edit_default[:-3]
                        edit_default = edit_default.strip()
                    
                    col1, col2 = st.columns([1, 1], gap="large")
                    
                    with col1:
                        st.markdown('<span class="editor-label">Original Clause (High Risk)</span>', unsafe_allow_html=True)
                        # Ensure no weird indentation or newlines breaks the display
                        clean_original = "\n".join([line.strip() for line in original_clause.splitlines()])
                        st.markdown(f'<div class="original-clause-box">{clean_original}</div>', unsafe_allow_html=True)
                        
                    with col2:
                        st.markdown('<span class="editor-label">Proposed Revision</span>', unsafe_allow_html=True)
                        new_value = st.text_area("Edit Clause:", value=edit_default, height=300, key=f"edit_{selected_option}", label_visibility="collapsed")
                        st.caption("Tip: Press Ctrl + Enter to apply changes")
                    
                    # Save changes when user types
                    if new_value != edit_default:
                         st.session_state["contract_edits"][original_clause] = new_value
                         st.toast("Changes saved locally!", icon="💾")

                    st.markdown('<div style="margin-top: 20px;"></div>', unsafe_allow_html=True)
                    
                    if st.button("👁️ Preview Final Clause", type="primary"):
                        # Use .replace() logic to simulate the update
                        final_clause = new_value
                        # Use custom HTML to ensure no code block rendering
                        clean_final = "\n".join([line.strip() for line in final_clause.splitlines()])
                        st.markdown(f"""
                        <div style="margin-top: 20px;">
                            <span class="editor-label" style="color: #4ade80 !important;">Negotiation-Ready Output</span>
                            <div class="negotiation-success">{clean_final}</div>
                        </div>
                        """, unsafe_allow_html=True)
                
                
                st.markdown("---")
                
                # --- Download Revised Contract ---
                # Generate PDF logic
                def generate_revised_pdf(original_text, edits):
                    # 1. Apply edits
                    final_text = original_text
                    for original, new in edits.items():
                        # Strip potential ellipsis from keys for matching
                        search_text = original
                        if search_text.startswith("..."): search_text = search_text[3:]
                        if search_text.endswith("..."): search_text = search_text[:-3]
                        
                        # Replace (use 1 replacement to be safe, or all?)
                        # Assuming snippets are unique enough
                        final_text = final_text.replace(search_text, new)
                    
                    # 2. Create PDF
                    doc = fitz.open()
                    page = doc.new_page()
                    
                    # Layout constants
                    margin = 50
                    page_width = page.rect.width
                    page_height = page.rect.height
                    printable_width = page_width - (2 * margin)
                    start_y = 50
                    y_pos = start_y
                    line_height = 14
                    fontsize = 11
                    fontname = "helv"
                    
                    # Wrap text
                    # Approx chars per line: width / (fontsize * 0.5) roughly
                    # Better: use fitz.get_text_length? 
                    # Simpler: standard textwrap with conservative width (e.g. 90 chars)
                    wrapper = textwrap.TextWrapper(width=90, replace_whitespace=False)
                    
                    # Process paragraphs to preserve some structure
                    paragraphs = final_text.split('\n')
                    
                    for para in paragraphs:
                        lines = wrapper.wrap(para)
                        if not lines: lines = [""] # Handle empty lines
                        
                        for line in lines:
                            if y_pos + line_height > page_height - margin:
                                page = doc.new_page()
                                y_pos = start_y
                            
                            page.insert_text((margin, y_pos), line, fontsize=fontsize, fontname=fontname)
                            y_pos += line_height
                    
                    return doc.tobytes()

                # Generate on the fly (fast enough for text)
                if st.button("🔄 Generate Revised PDF"):
                    with st.spinner("Generating PDF..."):
                        pdf_data = generate_revised_pdf(full_text, st.session_state["contract_edits"])
                        st.session_state['revised_pdf_data'] = pdf_data
                        st.success("PDF Generated! Click download below.")

                if 'revised_pdf_data' in st.session_state:
                    st.download_button(
                        label="💾 Download Revised Contract PDF",
                        data=st.session_state['revised_pdf_data'],
                        file_name="revised_contract.pdf",
                        mime="application/pdf"
                    )
            else:
                st.warning("No red flags were detected in the uploaded document.")
        else:
            st.info("⚠️ Please go to 'Upload Document' first to analyze a contract.")
            
        return

    # --- Main App (Upload Document) ---
    st.markdown('<div class="header-style">PDF Critical Words Analyzer</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Upload a contract or document to scan for critical words</div>', unsafe_allow_html=True)

    # Initialize processed_docs in session state if not exists
    if "processed_docs" not in st.session_state:
        st.session_state.processed_docs = {}

    # Initialize deleted_files in session state if not exists
    if "deleted_files" not in st.session_state:
        st.session_state.deleted_files = set()

    # Initialize current_doc if not exists
    if "current_doc" not in st.session_state:
        st.session_state.current_doc = None

    uploaded_files = st.file_uploader("Choose PDF files", type="pdf", accept_multiple_files=True)

    # 1. Process New Uploads
    if uploaded_files:
        for uploaded_file in uploaded_files:
            # Skip if file was deleted
            if uploaded_file.name in st.session_state.deleted_files:
                continue

            # Check if file already processed
            if uploaded_file.name not in st.session_state.processed_docs:
                try:
                    with st.spinner(f"Processing {uploaded_file.name}..."):
                        # Open the file from the uploaded stream
                        uploaded_file.seek(0)
                        file_bytes = uploaded_file.read()
                        doc = fitz.open(stream=file_bytes, filetype="pdf")
                        
                        # Extract text from ALL pages
                        full_text = ""
                        page_count = doc.page_count
                        for page in doc:
                            full_text += page.get_text(sort=True).strip() + "\n"
                        
                        doc.close()

                        # OCR Fallback Logic
                        # Check if text is insufficient (e.g. scanned PDF might only have page numbers)
                        # Threshold: < 100 characters likely means it's a scanned image PDF
                        if len(full_text.strip()) < 100:
                            st.info(f"Scanning images in {uploaded_file.name} (High-Res OCR enabled)...")
                            try:
                                # Convert PDF pages to images with higher DPI for better OCR accuracy
                                images = convert_from_bytes(file_bytes, dpi=300)
                                
                                # Initialize EasyOCR reader
                                # using 'en' for English, 'gpu=False' for wider compatibility if needed, but auto is fine
                                reader = easyocr.Reader(['en'])
                                
                                full_text = ""
                                for img in images:
                                    # Preprocess: Convert to grayscale to reduce noise
                                    img_gray = img.convert('L')
                                    
                                    # Convert PIL Image to numpy array
                                    img_np = np.array(img_gray)
                                    
                                    # Extract text
                                    # paragraph=True helps combine text blocks into coherent paragraphs
                                    result = reader.readtext(img_np, detail=0, paragraph=True)
                                    full_text += "\n".join(result) + "\n"
                                    
                            except Exception as ocr_e:
                                st.warning(f"OCR Warning for {uploaded_file.name}: {str(ocr_e)}. Make sure Poppler is installed.")


                        # --- RED FLAG LOGIC ---
                        keywords = ['Termination', 'Fees', 'Personal Data', 'Automatic Renewal']
                        found_red_flags = {} # Dictionary to store keyword -> list of snippets
                        
                        for keyword in keywords:
                            for match in re.finditer(re.escape(keyword), full_text, re.IGNORECASE):
                                start_idx = match.start()
                                end_idx = match.end()
                                context_start = max(0, start_idx - 200)
                                context_end = min(len(full_text), end_idx + 200)
                                snippet = full_text[context_start:context_end].strip()
                                if context_start > 0: snippet = "..." + snippet
                                if context_end < len(full_text): snippet = snippet + "..."
                                
                                if keyword not in found_red_flags: found_red_flags[keyword] = []
                                if snippet not in found_red_flags[keyword]: found_red_flags[keyword].append(snippet)

                        # --- CALCULATE RISK SCORE ---
                        risk_score = 1
                        if found_red_flags:
                            risk_score += len(found_red_flags) * 2
                        risk_score = min(10, risk_score)

                        # SAVE RESULTS TO PROCESSED_DOCS
                        st.session_state.processed_docs[uploaded_file.name] = {
                            "risk_score": risk_score,
                            "found_red_flags": found_red_flags,
                            "full_text": full_text,
                            "page_count": page_count
                        }
                        
                        # Set as current doc (Always update to latest upload)
                        st.session_state.current_doc = uploaded_file.name

                except Exception as e:
                    st.error(f"Error processing {uploaded_file.name}: {str(e)}")
                    st.error("Please ensure the file is a valid PDF.")

    # --- CUSTOM FILE LIST CSS ---
    st.markdown("""
        <style>
        /* Target the Primary Button (Active Document) */
        div.stButton > button[kind="primary"] {
            background: linear-gradient(90deg, #ec4899 0%, #8b5cf6 100%) !important;
            border: none !important;
            box-shadow: 0 0 15px rgba(236, 72, 153, 0.5) !important; /* GLOW EFFECT */
            color: white !important;
            transition: all 0.3s ease !important;
        }
        
        /* Target the Secondary Button (Inactive Documents) */
        div.stButton > button[kind="secondary"] {
            background: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            color: #e2e8f0 !important;
            box-shadow: none !important; /* NO GLOW */
            text-align: left !important;
            display: flex !important;
            justify-content: flex-start !important;
        }
        div.stButton > button[kind="secondary"]:hover {
            border-color: #8b5cf6 !important;
            color: white !important;
        }

        /* Styling for the DELETE button specifically */
        /* We use the specific column structure to target it: the 2nd column in the row */
        div[data-testid="column"]:nth-of-type(2) div.stButton > button {
            background: linear-gradient(135deg, #ec4899 0%, #8b5cf6 100%) !important;
            color: white !important;
            border-radius: 50% !important;
            width: 32px !important;
            height: 32px !important;
            padding: 0 !important;
            border: none !important;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            font-size: 14px !important;
            line-height: 1 !important;
        }
        div[data-testid="column"]:nth-of-type(2) div.stButton > button:hover {
            transform: scale(1.1) !important;
            box-shadow: 0 0 10px rgba(236, 72, 153, 0.6) !important;
        }
        
        /* Adjust alignment of the delete column */
        div[data-testid="column"]:nth-of-type(2) {
            display: flex;
            align-items: center;
            justify-content: center;
        }
        </style>
    """, unsafe_allow_html=True)

    # --- CUSTOM FILE LIST (Replaces Native List) ---
    if "processed_docs" in st.session_state and st.session_state.processed_docs:
        # Container to mimic being "inside" or attached to the uploader
        st.markdown('<div style="margin-top: -10px; margin-bottom: 20px;">', unsafe_allow_html=True)
        
        # Ensure current_doc is valid
        doc_keys = list(st.session_state.processed_docs.keys())
        if st.session_state.current_doc not in doc_keys and doc_keys:
             st.session_state.current_doc = doc_keys[0]

        # Render custom file buttons
        docs_to_remove = []
        for doc_name in doc_keys:
            # Layout: Button for doc (takes most space), Delete button (fixed small width)
            # We use a tighter ratio to keep the X close to the right edge but not too far
            col1, col2 = st.columns([0.94, 0.06])
            
            # Determine button style based on active state
            is_active = (st.session_state.current_doc == doc_name)
            btn_type = "primary" if is_active else "secondary"
            
            with col1:
                # Add file icon emoji to name
                display_name = f"📄 {doc_name}"
                if st.button(display_name, key=f"btn_{doc_name}", type=btn_type, use_container_width=True):
                    st.session_state.current_doc = doc_name
                    st.session_state.nav_selection = "🏠 Dashboard"
                    st.rerun()
            
            with col2:
                # Circular 'X' button for delete
                # We use a simple "✕" character which looks cleaner in a circle
                if st.button("✕", key=f"del_{doc_name}", help="Remove document"):
                    docs_to_remove.append(doc_name)

        # Process removals
        if docs_to_remove:
            for doc in docs_to_remove:
                if doc in st.session_state.processed_docs:
                    del st.session_state.processed_docs[doc]
                # Add to deleted_files to prevent re-processing
                st.session_state.deleted_files.add(doc)
                
                # If we deleted the current doc, reset current_doc
                if st.session_state.current_doc == doc:
                    st.session_state.current_doc = None
            st.rerun()
        
        st.markdown('</div>', unsafe_allow_html=True)

    # 2. Display Analysis (from current_doc)
    if st.session_state.current_doc and st.session_state.current_doc in st.session_state.processed_docs:
        current_filename = st.session_state.current_doc
        results = st.session_state.processed_docs[current_filename]
        
        st.markdown(f"### 📄 Analyzing: {current_filename}")
        
        full_text = results["full_text"]
        found_red_flags = results["found_red_flags"]


        # Create Tabs
        tab1, tab2, tab3 = st.tabs(["📄 Original Text", "🔍 Red Flags", "🤖 AI Analysis"])

        # --- TAB 1: ORIGINAL TEXT ---
        with tab1:
            st.subheader("Document Content")
            # Using custom HTML for a read-only, scrollable text box
            # This replaces st.text_area which is editable by default
            
            # Escape HTML characters in full_text to prevent rendering issues
            import html
            safe_text = html.escape(full_text).replace("\n", "<br>")
            
            st.markdown(f"""
            <div style="
                height: 600px;
                overflow-y: auto;
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 15px;
                color: #e2e8f0;
                font-family: monospace;
                font-size: 0.9rem;
                white-space: pre-wrap;
            ">
                {safe_text}
            </div>
            """, unsafe_allow_html=True)

        # --- TAB 3: AI ANALYSIS (Summary) ---
        with tab3:
            st.subheader("🤖 Document Summary")
            
            # Toggle for simplified language
            simplify_mode = st.toggle("Simplify for Non-Lawyers", help="Switch to plain, easy-to-understand English.")
            
            if client:
                with st.spinner("Generating summary with AI..."):
                    try:
                        # Construct prompt based on toggle
                        if simplify_mode:
                            prompt = f"Explain the following contract in simple, friendly English for a non-lawyer. Focus on what they actually need to know. Use exactly 3 bullet points (start each point with '* '):\n\n{full_text}"
                        else:
                            prompt = f"Summarize the following contract in exactly 3 concise bullet points (start each point with '* '). Focus on the main purpose and key obligations:\n\n{full_text}"
                            
                        # Use Hugging Face Inference Client
                        messages = [
                            {"role": "user", "content": prompt}
                        ]
                        
                        response = client.chat_completion(
                            model=st.session_state.get("selected_model", "mistralai/Mistral-7B-Instruct-v0.3"),
                            messages=messages,
                            max_tokens=500
                        )
                        
                        response_text = response.choices[0].message.content
                        
                        if response_text:
                            # Robust cleanup of markdown code blocks
                            summary_text = response_text.strip()
                            # Remove opening code fences like ```markdown, ```txt, ```
                            summary_text = re.sub(r'^```\w*\s*', '', summary_text)
                            # Remove closing code fences
                            summary_text = re.sub(r'\s*```$', '', summary_text)
                            # Remove any remaining backticks just in case
                            summary_text = summary_text.replace('```', '')
                            
                            # --- IMPROVED FORMATTING FOR POINTERS ---
                            # 1. Bold text: **text** -> <strong>text</strong>
                            summary_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', summary_text)
                            
                            # 2. Convert bullet points (* or - or numbered) to HTML list items
                            lines = summary_text.split('\n')
                            formatted_lines = []
                            in_list = False
                            
                            for line in lines:
                                line = line.strip()
                                if not line: continue
                                
                                # Check for various bullet markers
                                is_bullet = False
                                content = line
                                
                                if line.startswith('* ') or line.startswith('- ') or line.startswith('• '):
                                    is_bullet = True
                                    content = line[2:].strip()
                                elif re.match(r'^\d+\.\s', line): # Match numbered list like "1. "
                                    is_bullet = True
                                    # Remove the number and dot
                                    content = re.sub(r'^\d+\.\s', '', line).strip()
                                
                                if is_bullet:
                                    if not in_list:
                                        formatted_lines.append('<ul style="margin-left: 20px; list-style-type: disc; color: #e2e8f0;">')
                                        in_list = True
                                    
                                    formatted_lines.append(f'<li style="margin-bottom: 10px;">{content}</li>')
                                else:
                                    if in_list:
                                        formatted_lines.append('</ul>')
                                        in_list = False
                                    formatted_lines.append(f'<p style="margin-bottom: 10px;">{line}</p>')
                            
                            if in_list:
                                formatted_lines.append('</ul>')
                                
                            summary_html = "".join(formatted_lines)
                            
                            st.markdown(f'<div class="summary-box">{summary_html}</div>', unsafe_allow_html=True)
                        else:
                            st.warning("AI Status: Local Mode. The system has successfully scanned the document using the offline Keyword Engine. Review the specific red flags below for details.")
                            print("Debug: AI returned empty summary text.")
                            
                    except Exception as ai_error:
                        st.warning("AI Status: Local Mode. The system has successfully scanned the document using the offline Keyword Engine. Review the specific red flags below for details.")
                        print(f"Debug: AI Summary Error: {str(ai_error)}")
            else:
                st.warning("⚠️ Enter a Hugging Face Token in the sidebar to enable AI Summaries.")

        # --- TAB 2: RED FLAGS ---
        with tab2:
            # Use found_red_flags from above
            # Display Found Red Flags
            if found_red_flags:
                # --- PRE-FETCH AI EXPLANATIONS (BATCH) ---
                explanation_map = {}
                
                if client:
                    # Construct a single prompt for all found keywords
                    # This saves API calls and time compared to looping
                    prompt_intro = "For each of the following legal clauses found in a contract, briefly explain (in 1 sentence each) why it might be a risk or what to watch out for. Return the output as a JSON object where the key is the category name and the value is the explanation."
                    
                    prompt_body = ""
                    for category, snippets in found_red_flags.items():
                        # Take the first snippet as context
                        snippet_context = snippets[0][:300] 
                        prompt_body += f"\n\nCategory: {category}\nContext: {snippet_context}"
                        
                    full_prompt = prompt_intro + prompt_body
                    pass

                for category, snippets in found_red_flags.items():
                    st.markdown(f"""
                    <div class="red-flag-card">
                        <div class="red-flag-title">🚩 {category}</div>
                    """, unsafe_allow_html=True)
                    
                    for snippet in snippets:
                        # Clean and escape snippet to prevent Markdown code blocks and broken HTML
                        import html
                        # Remove newlines to prevent Markdown interpreting indentation as code blocks
                        clean_snippet = snippet.replace("\n", " ").replace("\r", "").strip()
                        escaped_snippet = html.escape(clean_snippet)
                        
                        # Highlight the keyword
                        # Use simple replace for now (matches existing logic)
                        highlighted_snippet = escaped_snippet.replace(html.escape(category), f'<span class="highlight">{html.escape(category)}</span>')
                        
                        st.markdown(f"""<div class="red-flag-content">"...{highlighted_snippet}..."</div><div style="margin-bottom: 10px;"></div>""", unsafe_allow_html=True)
                        
                    # Optional: AI Explanation Button per card
                    if client:
                        # Create a unique key for each button
                        btn_key = f"explain_{category}"
                        if st.button(f"🤖 Explain Risks of {category}", key=btn_key):
                            with st.spinner("Consulting AI..."):
                                try:
                                    prompt = f"Explain why a '{category}' clause in a contract is a potential red flag. Keep it to 2 sentences. Context: {snippets[0]}"
                                    messages = [{"role": "user", "content": prompt}]
                                    
                                    response = client.chat_completion(
                                        model=st.session_state.get("selected_model", "mistralai/Mistral-7B-Instruct-v0.3"),
                                        messages=messages,
                                        max_tokens=150
                                    )
                                    explanation_text = response.choices[0].message.content
                                    st.markdown(f'<div class="explanation-box"><b>AI Insight:</b> {explanation_text}</div>', unsafe_allow_html=True)
                                except Exception as e:
                                    error_msg = str(e)
                                    if "403" in error_msg and "Forbidden" in error_msg:
                                        st.error("🚨 Permission Denied: Your token lacks 'Inference' permissions. Please create a new token with 'Make calls to the serverless inference API' enabled.")
                                    elif "is not a chat model" in error_msg:
                                        st.error(f"⚠️ Model Error: The selected model ({st.session_state.get('selected_model')}) does not support Chat. Please select a different model from the sidebar.")
                                    else:
                                        st.error(f"AI unavailable. Error: {error_msg}")

                    st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.success("No common red flags found in the scanned text.")

    # --- CHATBOT ---
    
    # Custom CSS to style the Popover button as a Floating Action Button (Robot Icon)
    st.markdown("""
    <style>
    /* Position the popover container fixed at bottom right */
    [data-testid="stPopover"] {
        position: fixed;
        bottom: 30px;
        right: 30px;
        z-index: 9999;
    }
    
    /* Style the button inside the popover to look like a circle circle */
    [data-testid="stPopover"] > div > button {
        width: 50px;
        height: 50px;
        border-radius: 50%;
        background-color: #4f46e5; /* Indigo-600 */
        color: white;
        border: none;
        box-shadow: 0 4px 14px 0 rgba(0,0,0,0.39);
        font-size: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        transition: transform 0.2s;
    }
    
    [data-testid="stPopover"] > div > button:hover {
        transform: scale(1.1);
        background-color: #4338ca;
    }
    
    /* Hide the default caret/arrow if visible */
    [data-testid="stPopover"] > div > button > span {
        display: none;
    }
    
    /* Add the emoji manually via pseudo-element if needed, or just rely on button text */
    </style>
    """, unsafe_allow_html=True)
    
    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "Hi! I've analyzed the contract. Ask me anything about clauses, risks, or summaries."}
        ]

    # Floating Chat Interface using Popover
    # The label is just the emoji, which becomes the icon inside our circular button
    with st.popover("🤖", use_container_width=False):
        st.markdown("### Contract Assistant")
        
        # Display chat messages
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Chat Input (Custom implementation using text_input to ensure bottom positioning)
        def handle_chat_submit():
            user_input = st.session_state.chat_input_val
            if user_input.strip():
                st.session_state.messages.append({"role": "user", "content": user_input})
                st.session_state.trigger_ai_response = True
                st.session_state.chat_input_val = "" # Clear input

        st.text_input(
            "Ask a question...", 
            key="chat_input_val", 
            on_change=handle_chat_submit, 
            placeholder="Ask a question about the document...",
            label_visibility="collapsed"
        )

        # Handle AI Response Generation (triggered after user input)
        if st.session_state.get("trigger_ai_response", False):
            st.session_state.trigger_ai_response = False # Reset flag
            
            # Get the last user message (which was just added)
            last_user_msg = st.session_state.messages[-1]["content"]
            
            # Generate response
            with st.spinner("Thinking..."):
                if not user_api_key:
                    response_text = "Please enter a valid Hugging Face Token in the sidebar to use the chat."
                    # We can't use st.warning here effectively as it might disappear on rerun, but adding to history is better
                else:
                    try:
                        # Ensure client is available
                        if client is None:
                            client = InferenceClient(token=user_api_key)
                        
                        # Use full_text from the CURRENTLY SELECTED document as context
                        current_context = ""
                        if st.session_state.current_doc and st.session_state.current_doc in st.session_state.processed_docs:
                            current_context = st.session_state.processed_docs[st.session_state.current_doc]["full_text"]
                        else:
                            current_context = "No document selected."

                        # Construct prompt with context
                        prompt = f"Context from the contract: {current_context[:2000]}...\n\nUser Question: {last_user_msg}\n\nAnswer (be concise):"
                        
                        messages = [
                            {"role": "user", "content": prompt}
                        ]
                        
                        response = client.chat_completion(
                            model=st.session_state.get("selected_model", "mistralai/Mistral-7B-Instruct-v0.3"),
                            messages=messages,
                            max_tokens=300
                        )
                        
                        response_text = response.choices[0].message.content
                        
                    except Exception as e:
                        response_text = f"Error: {str(e)}"
                
                # Add AI response to history
                st.session_state.messages.append({"role": "assistant", "content": response_text})
                # Rerun to display new message
                st.rerun()

    # --- NO DOCUMENT SELECTED STATE ---
    if not st.session_state.current_doc and not uploaded_files:
        st.info("👋 Welcome! Please upload a PDF to the sidebar or select a processed document to begin.")

if __name__ == "__main__":
    main()
