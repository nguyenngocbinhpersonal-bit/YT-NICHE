"""App entry point: streamlit run yt_niche/ui/app.py"""

import streamlit as st

from yt_niche import settings
from yt_niche.ui.common import quota_sidebar, start_radar_thread

st.set_page_config(page_title="YT Niche", layout="wide")
settings.init()

pages = st.navigation(
    [
        st.Page("pages/dig.py", title="Dig", default=True),
        st.Page("pages/saved.py", title="Saved niches"),
        st.Page("pages/subniche_page.py", title="Sub-niches & keywords"),
        st.Page("pages/niche_map_page.py", title="Niche map"),
        st.Page("pages/similar_page.py", title="Similar videos & channels"),
        st.Page("pages/compare_page.py", title="Compare"),
        st.Page("pages/scorecard_page.py", title="Scorecard"),
        st.Page("pages/video_plan_page.py", title="10 test videos"),
        st.Page("pages/progress_page.py", title="Stage progress"),
        st.Page("pages/radar_page.py", title="Keyword radar"),
        st.Page("pages/settings_page.py", title="Settings"),
    ]
)
start_radar_thread()
quota_sidebar()
pages.run()
