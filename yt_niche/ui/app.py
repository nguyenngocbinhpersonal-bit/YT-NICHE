"""Điểm vào của app: streamlit run yt_niche/ui/app.py"""

import streamlit as st

from yt_niche import settings
from yt_niche.ui.common import quota_sidebar, start_radar_thread

st.set_page_config(page_title="YT Niche", page_icon="⛏️", layout="wide")
settings.init()

pages = st.navigation(
    [
        st.Page("pages/dig.py", title="Đào ngách", icon="⛏️", default=True),
        st.Page("pages/saved.py", title="Ngách đã lưu", icon="🗂️"),
        st.Page("pages/subniche_page.py", title="Ngách con & từ khoá", icon="🌿"),
        st.Page("pages/similar_page.py", title="Video & kênh tương tự", icon="🔗"),
        st.Page("pages/compare_page.py", title="So sánh", icon="🌍"),
        st.Page("pages/scorecard_page.py", title="Bảng chấm", icon="⚖️"),
        st.Page("pages/video_plan_page.py", title="10 video thử", icon="🎬"),
        st.Page("pages/progress_page.py", title="Tiến độ chặng", icon="🧭"),
        st.Page("pages/radar_page.py", title="Radar từ khoá", icon="📡"),
        st.Page("pages/settings_page.py", title="Cài đặt", icon="⚙️"),
    ]
)
start_radar_thread()
quota_sidebar()
pages.run()
