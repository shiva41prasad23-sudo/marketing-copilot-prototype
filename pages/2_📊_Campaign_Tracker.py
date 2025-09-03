import streamlit as st
import pandas as pd
import gspread

st.set_page_config(
    layout="wide", 
    page_title="Campaign Tracker", 
    page_icon="📊",
    initial_sidebar_state="expanded"
)

st.title("📊 Campaign Tracker")
st.write("This page shows the status of all campaigns generated and finalized by the Co-pilot.")

# --- GOOGLE SHEETS CONNECTION ---
@st.cache_data(ttl=60) # Cache data for 60 seconds
def load_tracker_data():
    try:
        creds = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(creds)
        spreadsheet = gc.open("Campaign Tracker DB").sheet1
        data = spreadsheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"💥 Error connecting to the Tracking DB Google Sheet: {e}")
        return pd.DataFrame()

tracker_df = load_tracker_data()

if not tracker_df.empty:
    # Function to apply color styling
    def style_status(status):
        if status == "Approved & Used":
            return 'background-color: #28a745; color: white'
        elif status == "Under Review":
            return 'background-color: #ffc107; color: black'
        return ''

    st.dataframe(
        tracker_df.style.applymap(style_status, subset=['status']),
        use_container_width=True,
        hide_index=True
    )
else:
    st.warning("No campaigns have been finalized yet. Go to the main page to generate and finalize a campaign.")
