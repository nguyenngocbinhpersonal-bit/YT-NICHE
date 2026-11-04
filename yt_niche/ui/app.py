"""App entry point: streamlit run yt_niche/ui/app.py"""

import streamlit as st

from yt_niche import settings
from yt_niche.ui.common import quota_sidebar, start_radar_thread

st.set_page_config(page_title="YT Niche", page_icon="⛏️", layout="wide")
settings.init()

pages = st.navigation(
    [
        st.Page("pages/dig.py", title="Dig", icon="⛏️", default=True),
        st.Page("pages/saved.py", title="Saved niches", icon="🗂️"),
        st.Page("pages/subniche_page.py", title="Sub-niches & keywords", icon="🌿"),
        st.Page("pages/niche_map_page.py", title="Niche map", icon="🗺️"),
        st.Page("pages/similar_page.py", title="Similar videos & channels", icon="🔗"),
        st.Page("pages/compare_page.py", title="Compare", icon="🌍"),
        st.Page("pages/scorecard_page.py", title="Scorecard", icon="⚖️"),
        st.Page("pages/video_plan_page.py", title="10 test videos", icon="🎬"),
        st.Page("pages/progress_page.py", title="Stage progress", icon="🧭"),
        st.Page("pages/radar_page.py", title="Keyword radar", icon="📡"),
        st.Page("pages/settings_page.py", title="Settings", icon="⚙️"),
    ]
)
start_radar_thread()
quota_sidebar()
pages.run()
