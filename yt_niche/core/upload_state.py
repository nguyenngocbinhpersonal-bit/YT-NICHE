"""Trạng thái của một video trên trang Upload. Hàm thuần, không IO.

Thứ tự ưu tiên (cái đứng trước thắng):
dang_san_xuat > ghi_thieu > xong > can_xac_nhan > da_dang_chua_link > cho_public > thieu_do > san_sang
Video cũ không có trường mô tả nên không xét thieu_do; không có đề xuất thì là `chua_doi_chieu`.
"""

from __future__ import annotations

DANG_SAN_XUAT = "dang_san_xuat"
GHI_THIEU = "ghi_thieu"
XONG = "xong"
CAN_XAC_NHAN = "can_xac_nhan"
DA_DANG_CHUA_LINK = "da_dang_chua_link"  # run đã published nhưng chưa có link và chưa có đề xuất
CHO_PUBLIC = "cho_public"
THIEU_DO = "thieu_do"
SAN_SANG = "san_sang"
CHUA_DOI_CHIEU = "chua_doi_chieu"  # chỉ video cũ

LABELS = {
    DANG_SAN_XUAT: "Đang sản xuất",
    GHI_THIEU: "Ghi thiếu catalog",
    XONG: "Xong",
    CAN_XAC_NHAN: "Cần xác nhận",
    DA_DANG_CHUA_LINK: "Đã đăng, chưa có link",
    CHO_PUBLIC: "Đã upload, chờ public",
    THIEU_DO: "Thiếu đồ",
    SAN_SANG: "Sẵn sàng upload",
    CHUA_DOI_CHIEU: "Chưa đối chiếu",
}
ICONS = {
    DANG_SAN_XUAT: "🏗️", GHI_THIEU: "⚠️", XONG: "✅", CAN_XAC_NHAN: "🔔", DA_DANG_CHUA_LINK: "🔗",
    CHO_PUBLIC: "⏳", THIEU_DO: "📦", SAN_SANG: "📤", CHUA_DOI_CHIEU: "▫️",
}
# Việc owner cần làm, theo thứ tự hiện.
TODO_ORDER = (CAN_XAC_NHAN, GHI_THIEU, SAN_SANG, THIEU_DO, DA_DANG_CHUA_LINK, CHO_PUBLIC)

_DONE_RUN_STATES = {"complete", "published"}
_LIVE_LIFECYCLES = {"active", "published"}


def missing_items(*, has_mp4: bool, has_title: bool, has_description: bool, has_thumb_candidates: bool) -> list[str]:
    out = []
    if not has_mp4:
        out.append("mp4")
    if not has_title:
        out.append("tiêu đề")
    if not has_description:
        out.append("mô tả")
    if not has_thumb_candidates:
        out.append("ảnh thumbnail")
    return out


def video_status(
    *,
    kind: str,  # pipeline | old
    run_state: str | None = None,
    lifecycle: str | None = None,
    has_link: bool = False,  # pipeline: youtube_url trong project.json hoặc video_url trong DB; cũ: video_url trong DB
    catalog_pending: bool = False,
    has_proposal: bool = False,
    manual_state: str | None = None,
    has_mp4: bool = True,
    has_title: bool = True,
    has_description: bool = True,
    has_thumb_candidates: bool = True,
) -> str:
    if kind == "old":
        if has_link:
            return XONG
        if has_proposal:
            return CAN_XAC_NHAN
        if manual_state:
            return CHO_PUBLIC
        return CHUA_DOI_CHIEU

    if run_state not in _DONE_RUN_STATES or (lifecycle is not None and lifecycle not in _LIVE_LIFECYCLES):
        return DANG_SAN_XUAT
    if catalog_pending:
        return GHI_THIEU
    published = run_state == "published"
    if published and has_link:
        return XONG
    if has_proposal:
        return CAN_XAC_NHAN
    if published:
        return DA_DANG_CHUA_LINK
    if manual_state:
        return CHO_PUBLIC
    if missing_items(
        has_mp4=has_mp4, has_title=has_title, has_description=has_description, has_thumb_candidates=has_thumb_candidates
    ):
        return THIEU_DO
    return SAN_SANG


def status_for(item, db_state: dict | None, has_proposal: bool) -> str:
    """Ghép từ `upload_scan.VideoItem` + dòng `upload_video_state` (hoặc None) + có đề xuất hay không."""
    st = db_state or {}
    if item.kind == "old":
        link = bool(st.get("video_url"))
    else:
        link = bool(item.youtube_url) or bool(st.get("video_url"))
    return video_status(
        kind=item.kind,
        run_state=item.state,
        lifecycle=item.lifecycle,
        has_link=link,
        catalog_pending=bool(st.get("catalog_pending")),
        has_proposal=has_proposal,
        manual_state=st.get("manual_state") or None,
        has_mp4=bool(item.mp4s),
        has_title=bool(item.title),
        has_description=bool(item.description),
        has_thumb_candidates=bool(item.thumbnails),
    )


# ---------------------------------------------------------------------------
# Ghép với đề xuất (dùng chung cho trang và script kiểm)
# ---------------------------------------------------------------------------
MAX_WEAK_SHOWN = 3


def to_match_item(item):
    """`upload_scan.VideoItem` → `upload_match.MatchItem`."""
    from yt_niche.core.upload_match import MatchItem

    return MatchItem(item.channel_slug, item.number, item.kind, tuple(item.titles), item.slug_title)


def proposals_for(proposals, key: tuple[str, int]) -> list:
    """Đề xuất hiện cho một video local: đã có mạnh thì chỉ mạnh; chỉ có yếu thì tối đa 3, điểm cao trước."""
    mine = [p for p in proposals if (p.channel_slug, p.number) == key]
    strong = [p for p in mine if p.strength == "strong"]
    if strong:
        return strong
    return sorted(mine, key=lambda p: -p.score)[:MAX_WEAK_SHOWN]
