import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
import datetime
import re
from pathlib import Path
import uuid
import traceback
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# --- CONFIGURATION ---
st.set_page_config(
    layout="wide",
    page_title="CampaignIQ",
    page_icon="💡",
    initial_sidebar_state="expanded"
)

# --- CSS STYLING ---
st.markdown("""
<style>
    .main { background-color: #F0F2F6; }
    .main-title, .main-subtitle, .input-box-header, .input-box-caption, .results-header { text-align: center; }
    /* ... rest of CSS from before ... */
</style>
""", unsafe_allow_html=True)


# --- INITIALIZATION & DATA LOADING ---
@st.cache_resource
def initialize_and_index():
    try:
        compliance_manual = Path("compliance_manual.txt").read_text()
    except FileNotFoundError:
        st.error("Error: `compliance_manual.txt` not found. Please create it.")
        compliance_manual = ""

    with st.spinner("One-time setup: Reading campaign library & creating 'Smart Index' with Gemini..."):
        try:
            creds = st.secrets["gcp_service_account"]
            gc = gspread.service_account_from_dict(creds)
            spreadsheet = gc.open("Marketing Copilot Data").sheet1
            
            # --- THIS IS THE FIX ---
            # Changed 'worksheet' to 'spreadsheet'
            data = spreadsheet.get_all_records()
            
            df = pd.DataFrame(data)
            df.dropna(subset=['content'], inplace=True)
            df = df[df['content'] != '']

            result = genai.embed_content(
                model="models/embedding-001",
                content=df["content"].tolist(),
                task_type="retrieval_document"
            )
            df['embeddings'] = result['embedding']
            st.session_state.indexed_df = df
            st.success("✅ Campaign library is loaded and the 'Smart Index' is ready!")
        except Exception as e:
            st.error(f"💥 Error during initial data load & indexing:")
            st.code(traceback.format_exc()) # More detailed error
            st.session_state.indexed_df = pd.DataFrame()

    return compliance_manual

try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except Exception as e:
    st.error("💥 Error configuring the Gemini API.")

compliance_manual = initialize_and_index()

# --- AI & HELPER FUNCTIONS ---
def find_similar_campaigns(query, df, n=3):
    if df.empty or 'embeddings' not in df.columns:
        return []
    with st.spinner("Finding similar campaigns..."):
        query_embedding = genai.embed_content(
            model="models/embedding-001",
            content=query,
            task_type="retrieval_query"
        )['embedding']
        similarities = cosine_similarity([query_embedding], list(df['embeddings']))[0]
        top_n_indices = np.argsort(similarities)[-n:][::-1]
    return df.iloc[top_n_indices]["content"].tolist()

# ... (The rest of the file is identical to the last working version)
def determine_overall_status(audit_result_text):
    if not audit_result_text: return "pending"
    if "🛑 FAIL" in audit_result_text: return "non-compliant"
    elif "⚠️ NEEDS INFO" in audit_result_text: return "needs-review"
    elif "✅ PASS" in audit_result_text: return "compliant"
    else: return "pending"
def generate_content(query, examples, channel):
    prompt = f"""You are a creative marketing genius at Uber India.
**Brand Voice & Style Guide:**
- Tone: Clear, helpful, and optimistic. Empower the user.
- Style: Use short, scannable sentences. Use emojis to add personality.
- Language: Simple and direct. Address the user ("Your ride...", "Get...").
Your task is to generate 3 new, creative variations for a '{channel}' campaign. Include 'T&Cs apply.' in promotional suggestions.
**User's Goal:** {query}
**Use these 3 successful past campaigns as inspiration:**
1. "{examples[0]}"
2. "{examples[1]}"
3. "{examples[2]}"
Generate 3 distinct options, starting each with 'Option 1:', 'Option 2:', and 'Option 3:'.
"""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e: return f"💥 Error: {e}"
def generate_terms_and_conditions(query, channel):
    prompt = f"""You are a Legal Operations Specialist at Uber. Based on the user's campaign goal for the '{channel}' channel, generate a concise and clear Terms & Conditions section.
The goal is: '{query}'.
Structure your response with two sections using markdown:
1.  **Standard Terms:** Include 2-3 general clauses (e.g., non-transferable, Uber's right to end).
2.  **Offer-Specific Terms:** Infer the key conditions from the campaign goal (e.g., location, discount type, validity period). Be specific and use placeholders like [City], [Start Date], [End Date] where necessary.
"""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e: return f"💥 Error generating T&Cs: {e}"
def audit_with_ai(campaign_text, t_and_c_text, manual, end_date):
    prompt = f"""You are a meticulous Marketing Compliance Inspector at Uber. Your goal is to ensure no campaign goes out with errors or placeholders.
    **Your Task:**
    Audit the 'Draft Campaign' against the 'Official Compliance Manual'. Base your audit ONLY on the exact text provided. Do not invent or assume information.
    **CRITICAL INSTRUCTION ON PLACEHOLDERS:**
    A draft is considered a 🛑 FAIL if the 'Terms & Conditions' text contains any placeholder text surrounded by square brackets, such as `[State/Jurisdiction]`, `[Link]`, `[Start Date]`, `[City/Region]`, etc. These are required fields that MUST be filled in.
    **Instructions:**
    - Ignore any rules in the manual related to internal legal processes (Section 5). Focus only on content rules.
    - For each key content rule, provide a one-line bullet point stating the status and a brief reason.
    - Start each bullet with one of these three statuses: ✅ PASS, 🛑 FAIL, or ⚠️ NEEDS INFO.
    **Official Compliance Manual:**
    ---
    {manual}
    ---
    **Context:**
    - Campaign End Date: {end_date.strftime('%A, %B %d, %Y')}
    **Draft Campaign:**
    "{campaign_text}"
    **Terms & Conditions:**
    "{t_and_c_text}"
    Begin your audit now.
    """
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e: return f"💥 Error during audit: {e}"
def finalize_campaign(option, channel, status):
    try:
        creds = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(creds)
        tracker_sheet = gc.open("Campaign Tracker DB").sheet1
        new_row_tracker = [ str(uuid.uuid4()), option['campaign_text'], channel, status, option['t_and_c_text'], option['audit_result'], datetime.datetime.now().isoformat() ]
        tracker_sheet.append_row(new_row_tracker)
        if status == "Approved & Used":
            main_data_sheet = gc.open("Marketing Copilot Data").sheet1
            new_row_main = [ f"PUSH_GEN_{datetime.date.today().strftime('%Y%m%d')}", "Push Notification", "Pan-India", "en-IN", "Promotional", "Drive_Rides", "All_Riders", option['campaign_text'], "CTR", 5.0, datetime.date.today().isoformat(), "generated, approved" ]
            main_data_sheet.append_row(new_row_main)
        st.toast(f"Campaign saved with status: {status}!", icon="🎉")
    except Exception as e:
        st.error(f"💥 Error saving to Google Sheets:")
        st.code(traceback.format_exc())

# --- MAIN APP UI & LOGIC ---
st.markdown("<div class='main-title'><h1>CampaignIQ</h1></div>", unsafe_allow_html=True)
st.markdown("<p class='main-subtitle'>Smart assistant for campaign creation - Generate AI-powered, compliant marketing campaigns instantly</p>", unsafe_allow_html=True)
st.markdown("<div class='subtle-divider'></div>", unsafe_allow_html=True)

_, center_col, _ = st.columns([0.5, 3.0, 0.5]) 
with center_col:
    with st.container(border=True):
        st.markdown("<div class='input-box-header'><h2>Generate Marketing Campaign</h2></div>", unsafe_allow_html=True)
        st.markdown("<div class='input-box-caption'><p>Provide your campaign details and let AI create compliant marketing ideas</p></div>", unsafe_allow_html=True)
        search_query = st.text_input("💡 Promotion Idea", "2-for-1 deal on rides to the cinema on weekends")
        input_cols = st.columns(2)
        with input_cols[0]:
            selected_channel = st.selectbox("⚡ Marketing Channel", ["Push Notification", "SMS", "Email_Subject"])
        with input_cols[1]:
            campaign_end_date = st.date_input("📅 Offer End Date", datetime.date.today())
        st.button("Generate Ideas", key="generate_button", type="primary", use_container_width=True)

if "campaign_options" not in st.session_state:
    st.session_state.campaign_options = []

if st.session_state.get('generate_button'):
    if search_query and compliance_manual and 'indexed_df' in st.session_state:
        with st.spinner("Generating new ideas..."):
            similar_campaigns = find_similar_campaigns(search_query, st.session_state.indexed_df)
            generated_copy = generate_content(search_query, similar_campaigns, selected_channel)
            generated_t_and_c = generate_terms_and_conditions(search_query, selected_channel)
            st.session_state.campaign_options = []
            options = [opt.strip() for opt in re.split(r'(?i)\boption \d+:', generated_copy) if opt.strip()]
            for i, option_text in enumerate(options):
                st.session_state.campaign_options.append({ "id": i + 1, "campaign_text": option_text, "t_and_c_text": generated_t_and_c, "audit_result": "", "is_editing": False })
    else:
        st.warning("Please enter a campaign goal or wait for the index to load.")

st.markdown("<div class='subtle-divider'></div>", unsafe_allow_html=True)

if st.session_state.campaign_options:
    st.markdown("<div class='results-header'><h2>✨ Review & Refine Your AI Drafts</h2></div>", unsafe_allow_html=True)
    for option in st.session_state.campaign_options:
        overall_status = determine_overall_status(option['audit_result'])
        status_colors = {"compliant": "#28a745", "needs-review": "#ffc107", "non-compliant": "#dc3545", "pending": "#6c757d"}
        
        with st.container(border=True):
            header_cols = st.columns([0.85, 0.15])
            with header_cols[0]:
                st.markdown(f"#### Option {option['id']}")
                st.caption(f"Channel: {selected_channel}")
            with header_cols[1]:
                st.button("✏️ Edit", key=f"edit_{option['id']}", type="secondary", on_click=lambda opt=option: opt.update(is_editing=not opt['is_editing']))
            st.markdown(f"**Status:** <span style='color:{status_colors[overall_status]}; font-weight:bold;'>{overall_status.replace('-', ' ').title()}</span>", unsafe_allow_html=True)
            st.markdown("<div class='subtle-divider'></div>", unsafe_allow_html=True)
            if option['is_editing']:
                edited_campaign_text = st.text_area("Campaign Copy", value=option['campaign_text'], key=f"campaign_{option['id']}", height=120)
                edited_t_and_c_text = st.text_area("Terms and Conditions", value=option['t_and_c_text'], key=f"tandc_{option['id']}", height=250)
                st.markdown("<div class='button-row'>", unsafe_allow_html=True)
                b_col1, b_col2, _ = st.columns([0.2, 0.2, 0.6])
                with b_col1:
                    if st.button("Save", key=f"save_{option['id']}", type="primary"):
                        option['campaign_text'] = edited_campaign_text
                        option['t_and_c_text'] = edited_t_and_c_text
                        option['is_editing'] = False; st.rerun()
                with b_col2:
                     if st.button("Cancel", key=f"cancel_{option['id']}", type="secondary"):
                        option['is_editing'] = False; st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.markdown("**Campaign Copy:**")
                st.markdown(option['campaign_text'])
                st.markdown("**Terms and Conditions:**")
                st.markdown(f"<div style='background-color:#ffffff; padding:10px; border-radius:8px; border: 1px solid #e0e0e0; color:black;'>{option['t_and_c_text']}</div>", unsafe_allow_html=True)
            st.markdown("<div class='subtle-divider'></div>", unsafe_allow_html=True)
            audit_cols = st.columns(2)
            with audit_cols[0]:
                st.button("🕵️ Audit Campaign", key=f"validate_{option['id']}", use_container_width=True, type="secondary")
            with audit_cols[1]:
                if overall_status == 'compliant':
                    st.button("🚀 Use for Campaign", key=f"finalize_{option['id']}", type="primary", use_container_width=True, on_click=finalize_campaign, args=(option, selected_channel, "Approved & Used"))
                elif overall_status in ['needs-review', 'non-compliant']:
                    st.button("📤 Send for Legal Review", key=f"finalize_{option['id']}", use_container_width=True, type="secondary", on_click=finalize_campaign, args=(option, selected_channel, "Under Review"))
            if st.session_state.get(f"validate_{option['id']}"):
                 with st.spinner(f"Auditing Option {option['id']}..."):
                    option['audit_result'] = audit_with_ai(option['campaign_text'], option['t_and_c_text'], compliance_manual, campaign_end_date)
                    st.rerun()
            if option['audit_result']:
                with st.expander("Show AI Compliance Audit", expanded=True):
                    for line in option['audit_result'].split('\n'):
                        if line.strip().startswith("✅"): st.success(line.strip(), icon="✅")
                        elif line.strip().startswith("🛑"): st.error(line.strip(), icon="🛑")
                        elif line.strip().startswith("⚠️"): st.warning(line.strip(), icon="⚠️")
                        else: st.write(line)
