"""Trang Upload: hàng đợi upload theo kênh, thẻ copy theo thứ tự form Studio, đối chiếu video đã lên, xác nhận ghi local.

Owner tự upload trên Studio. Mở trang chỉ gọi `pipectl runs` (không chạm sổ ghi nhận). Ghi vào máy chỉ xảy ra khi owner bấm
xác nhận: pipeline đi qua `services.uploads.confirm_pipeline` (lệnh ghi của pipectl); video cũ chỉ ghi DB của app.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import upload_facebook as fbm
from yt_niche.core import upload_match as um
from yt_niche.core import upload_state as us
from yt_niche.db import uploads as repo
from yt_niche.services import pipectl, upload_scan
from yt_niche.services import uploads as upl
from yt_niche.sources.youtube import YouTubeError
from yt_niche.ui.common import get_conn

RELOAD_S = 120  # dữ liệu pipectl + quét folder dùng lại trong phiên bấy nhiêu giây
STALE_MIN = 30  # ảnh chụp video YouTube cũ hơn thì tự làm mới (một lần mỗi phiên)
META_MAX_CHARS = 20000
WAIT_PUBLIC = "waiting_public"
NL = chr(10)

_T0 = time.perf_counter()
st.title("Upload")
conn = get_conn()
S = st.session_state


# ---------------------------------------------------------------------------
# Dữ liệu (cache trong phiên)
# ---------------------------------------------------------------------------
@st.cache_resource(ttl=RELOAD_S, show_spinner=False)
def _scan_cached(root: str) -> dict:
    """pipectl runs + quét folder, dùng chung giữa các phiên trong RELOAD_S giây (lỗi không được cache)."""
    runs = pipectl.runs(root)
    return {"at": time.time(), "scan": upload_scan.scan(root, runs)}


def _load(force: bool = False) -> dict | None:
    if force:
        _scan_cached.clear()
    try:
        d = _scan_cached(str(cfg.SOCIAL_MEDIA_ROOT))
    except pipectl.PipectlError as e:
        st.error(f"Không đọc được trạng thái run từ pipectl: {e}")
        d = S.get("_up_data")
        if d is None:
            if st.button("Thử lại"):
                st.rerun()
            return None
        st.caption("Đang dùng dữ liệu lần trước.")
        return d
    if S.get("_up_data") is not d:
        S["_up_data"] = d
        S.pop("_up_prop", None)
    return d


def _parse_iso(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stale(ch: dict) -> bool:
    t = _parse_iso(ch.get("last_refresh_at"))
    return t is None or (datetime.now(timezone.utc) - t).total_seconds() > STALE_MIN * 60


def _remotes(bound: dict | None) -> list[um.RemoteVideo]:
    if not bound:
        return []
    return [
        um.RemoteVideo(r["video_id"], r["channel_id"], r["title"], r["published_at"])
        for r in repo.list_remote(conn, bound["channel_id"])
    ]


def _linked_ids(ch_scan, states: dict) -> set[str]:
    ids = set()
    for v in ch_scan.videos:
        if v.youtube_video_id:
            ids.add(v.youtube_video_id)
        vid = (states.get(v.number) or {}).get("video_id")
        if vid:
            ids.add(vid)
    return ids


def _propose(slug: str, ch_scan, bound: dict | None, token: float) -> um.ProposeResult:
    """Đề xuất của một kênh, cache theo (lần làm mới, đề xuất đã từ chối, lần quét)."""
    rejected = repo.rejected_pairs(conn, slug)
    key = (token, (bound or {}).get("last_refresh_at"), (bound or {}).get("channel_id"), frozenset(rejected))
    cache = S.setdefault("_up_prop", {})
    hit = cache.get(slug)
    if hit and hit[0] == key:
        return hit[1]
    res = um.propose([us.to_match_item(v) for v in ch_scan.videos], _remotes(bound), rejected)
    cache[slug] = (key, res)
    return res


def _code(text: str) -> None:
    """Khối copy đúng từng byte. st.code bỏ đúng một xuống-dòng đầu và một cuối, nên bọc thêm một cặp."""
    st.code(NL + text + NL, language=None, wrap_lines=True)


def _image(path, **kw) -> None:
    try:
        st.image(str(path), **kw)
    except Exception:  # ảnh hỏng không được làm hỏng cả thẻ
        st.caption("(không dựng được ảnh)")


def _fmt_date(s: str | None) -> str:
    return (s or "").replace("T", " ").replace("Z", " UTC")[:23] if s else "?"


def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# Callback ghi DB app
# ---------------------------------------------------------------------------
def _cb_reject(slug, number, video_id):
    repo.reject_match(conn, slug, number, video_id)


def _cb_wait(slug, number, on: bool):
    repo.update_state(conn, slug, number, manual_state=WAIT_PUBLIC if on else "")


def _cb_mp4(slug, number, key):
    repo.update_state(conn, slug, number, mp4_choice=S[key])


def _cb_thumb(slug, number, keys: dict, changed: str):
    chosen = [rel for rel, k in keys.items() if S.get(k)]
    if len(chosen) > repo.MAX_THUMBS:
        S[keys[changed]] = False  # chặn ảnh thứ 4
        S["_up_thumb_warn"] = (slug, number)
        chosen = [rel for rel in chosen if rel != changed]
    else:
        S.pop("_up_thumb_warn", None)
    repo.update_state(conn, slug, number, thumb_choice=chosen)


def _cb_open(slug, number):
    S["up_sel"] = (slug, number)


def _row(slug: str, v, video_id: str, published_at: str | None) -> dict:
    return {"slug": slug, "number": v.number, "group": us.confirm_group(v.kind, v.state), "name": v.name,
            "video_id": video_id, "published_at": published_at or ""}


def _cb_stage(slug: str, rows: list):
    S["up_batch"] = {"slug": slug, "rows": rows}


def _cb_cancel_batch():
    S.pop("up_batch", None)


def _cb_unconfirm(slug, number):
    upl.unconfirm_old(conn, slug, number)


def _cb_select_all_rename(keys: list):
    for k in keys:
        S[k] = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _vn_time(iso: str | None) -> str:
    t = fbm.parse_iso(iso)
    if t is None:
        return "?"
    t = t.astimezone(fbm.VN_TZ)
    day = "" if t.date() == _now().astimezone(fbm.VN_TZ).date() else t.strftime("%d/%m ")
    return f"{day}{t.strftime('%H:%M')}"


def _fb_inputs(slug: str, number: int) -> tuple[str, str, str]:
    """Link + ngày tùy chọn đang nhập trên thẻ Facebook → (link, iso ngày hoặc '', lỗi ngày)."""
    link = (S.get(f"fbl_{slug}_{number}") or "").strip()
    if not S.get(f"fbd_on_{slug}_{number}"):
        return link, "", ""
    d = S.get(f"fbd_{slug}_{number}")
    try:
        return link, fbm.date_to_iso(d, _now()), ""
    except (ValueError, TypeError) as e:
        return link, "", str(e) or "Ngày không hợp lệ."


def _cb_mark_stage(slug: str, number: int, name: str, run_state: str, plat: str):
    """Mở hộp xác nhận «Đã đăng, đổi tên sau 1 giờ». Chưa ghi gì."""
    link, iso, err = _fb_inputs(slug, number) if plat == us.FACEBOOK else ("", "", "")
    S["up_mark"] = {"slug": slug, "number": number, "name": name, "run_state": run_state, "plat": plat,
                    "fb_url": link, "published_at": iso, "error": err}


def _cb_cancel_mark_dialog():
    S.pop("up_mark", None)


def _cb_cancel_waiting(slug: str, number: int):
    upl.cancel_mark(conn, slug, number)


# ---------------------------------------------------------------------------
# Khai kênh
# ---------------------------------------------------------------------------
def _binding_ui(slug: str, ch_scan, bound: dict | None) -> None:
    info = ch_scan.info
    pk = f"bind_prev_{slug}"
    label = "Đổi gán kênh YouTube" if bound else "Khai kênh YouTube"
    with st.expander(label, expanded=bound is None):
        if bound:
            st.caption(f"Đang gán: **{bound['title']}** ({bound['handle'] or bound['channel_id']})")
        if not cfg.api_key():
            st.warning("Chưa có YouTube API key (trang Cài đặt).")
        url = st.text_input("Dán @handle, URL kênh, hoặc URL một video của kênh", key=f"bind_url_{slug}")
        if st.button("Xem trước", key=f"bind_go_{slug}", disabled=not (url.strip() and cfg.api_key())):
            titles = [t for v in ch_scan.videos for t in v.titles]
            try:
                S[pk] = upl.preview_binding(conn, slug, url, info.name, titles, api_key=cfg.api_key())
            except (upl.UploadError, YouTubeError, httpx.HTTPError) as e:
                S.pop(pk, None)
                st.error(str(e) or type(e).__name__)
        prev = S.get(pk)
        if not prev:
            return
        ch = prev["channel"]
        st.markdown(f"**Kênh YouTube thật:** {ch['title']} · {prev['video_count']} video  \n**Tên kênh trong dự án:** {info.name or '(trống)'}")
        if not prev["name_matches"]:
            st.error("Tên kênh trên YouTube KHÁC tên kênh trong dự án. Chỉ gán nếu chắc đây đúng kênh.")
        else:
            st.success("Tên kênh khớp.")
        if prev["video_title"] is not None:
            (st.success if prev["video_title_matches"] else st.warning)(
                f"Video «{prev['video_title']}» " + ("khớp một tiêu đề local." if prev["video_title_matches"] else "không khớp tiêu đề local nào.")
            )
        if prev["bound_to"]:
            st.error(f"Kênh YouTube này đã gán cho kênh «{prev['bound_to']}».")
        if st.button("Gán kênh này", key=f"bind_ok_{slug}", type="primary", disabled=bool(prev["bound_to"])):
            try:
                (upl.rebind if bound else upl.bind)(conn, slug, ch)
            except upl.UploadError as e:
                st.error(str(e))
            else:
                S.pop(pk, None)
                S["_up_auto_done"] = False  # kênh mới gán thì làm mới ngay một lần
                st.rerun()


# ---------------------------------------------------------------------------
# Thẻ video
# ---------------------------------------------------------------------------
def _studio_url(bound: dict | None) -> str:
    if bound:
        return f"https://studio.youtube.com/channel/{bound['channel_id']}/videos/upload?d=ud"
    return "https://studio.youtube.com/"


def _proposal_block(slug: str, v, props: list, remotes: dict, status: str) -> None:
    if not props:
        return
    st.markdown("**Đối chiếu với kênh YouTube**")
    for p in props:
        rv = remotes.get(p.video_id)
        title = rv.title if rv else ""
        kind = "Đề xuất mạnh" if p.strength == um.STRONG else f"Đề xuất yếu ({p.score:.0%}, {p.reason})"
        c1, c2 = st.columns([5, 2])
        c1.markdown(f"{kind}: [{title}]({_yt_url(p.video_id)})  \nNgày đăng: {_fmt_date(rv.published_at if rv else None)}")
        c2.button("Không phải video này", key=f"rej_{slug}_{v.number}_{p.video_id}", on_click=_cb_reject, args=(slug, v.number, p.video_id))
        if status == us.CAN_XAC_NHAN:
            label = "Xác nhận video này" if p.strength == um.STRONG else "Xác nhận video này (đề xuất yếu, tự soát)"
            c2.button(label, key=f"stage_{slug}_{v.number}_{p.video_id}", on_click=_cb_stage,
                      args=(slug, [_row(slug, v, p.video_id, rv.published_at if rv else None)]))


def _manual_block(slug: str, v, bound: dict | None) -> None:
    """Dán URL video tay: kiểm kênh bằng API; sai kênh thì cảnh báo đỏ và không có đường ghi."""
    key, rk = f"man_url_{slug}_{v.number}", f"man_res_{slug}_{v.number}"
    with st.expander("Dán link video đã upload (khi app chưa đề xuất)"):
        url = st.text_input("Link video YouTube hoặc Studio", key=key)
        if st.button("Kiểm link", key=f"man_go_{slug}_{v.number}", disabled=not (url.strip() and cfg.api_key())):
            try:
                S[rk] = upl.check_video_url(conn, slug, url, api_key=cfg.api_key())
            except (upl.UploadError, YouTubeError, httpx.HTTPError) as e:
                S.pop(rk, None)
                st.error(str(e) or type(e).__name__)
        r = S.get(rk)
        if not r:
            return
        if r["status"] == upl.CHANNEL_UNBOUND:
            st.warning("Kênh chưa gán URL YouTube. Gán kênh trước (dán URL một video ở mục Khai kênh là đủ).")
        elif r["status"] == upl.CHANNEL_MISMATCH:
            other = f" (kênh «{r['other_slug']}»)" if r.get("other_slug") else ""
            st.error(f"Video này thuộc kênh YouTube khác{other}, không phải kênh «{(bound or {}).get('title', slug)}». Không ghi được.")
        elif r["status"] == upl.NOT_READABLE:
            st.info("Không đọc được video (riêng tư hoặc chưa public). Dùng nút «Đã upload, chờ public».")
        else:
            st.success(f"Đúng kênh · «{r['title']}» · {r['privacy']} · {_fmt_date(r['published_at'])}")
            if r["privacy"] != upl.PRIVACY_PUBLIC:
                st.info("Video chưa public. Dùng «Đã upload, chờ public»; khi public sẽ xác nhận được.")
            else:
                st.button("Xác nhận video này", key=f"man_stage_{slug}_{v.number}", on_click=_cb_stage,
                          args=(slug, [_row(slug, v, r["video_id"], r["published_at"])]))


def _pending_block(slug: str, v, st_row: dict) -> None:
    st.error("Ghi thiếu catalog: folder và project.json đã ghi, catalog chưa. Link và ngày đã được lưu để thử lại.")
    if st.button("Thử lại ghi catalog", key=f"retry_{slug}_{v.number}"):
        with st.spinner("Đang ghi lại…"):
            r = upl.retry_pending(conn, slug, v.number, cfg.SOCIAL_MEDIA_ROOT)
        _scan_cached.clear()
        if r.ok:
            S["up_result"] = ([f"{slug} {v.number:03d}: đã ghi đủ."], [])
        else:
            S["up_result"] = ([], [f"{slug} {v.number:03d}: {r.message}"])
        st.rerun()


def _extras(v) -> None:
    with st.expander("Trường phụ (đọc hiểu, tag, chapter, disclaimer, SRT)"):
        if v.title_en:
            st.markdown("**Tiêu đề EN**")
            _code(v.title_en)
        if v.description_en:
            st.markdown("**Mô tả EN** (để owner đọc hiểu)")
            _code(v.description_en)
        if v.tags:
            st.markdown("**Tag**")
            _code(", ".join(map(str, v.tags)))
        if v.chapters:
            st.markdown("**Chapter**")
            _code("\n".join(f"{c.get('time', '')} {c.get('label', '')}" if isinstance(c, dict) else str(c) for c in v.chapters))
        if v.disclaimer:
            d = v.disclaimer
            st.markdown("**Disclaimer**")
            _code(d.get("primary", "") if isinstance(d, dict) else str(d))
        srts = sorted([*v.folder.glob("*.srt"), *v.folder.glob("*/*.srt")])
        if srts:
            st.markdown("**SRT**")
            _code("\n".join(str(p) for p in srts))


def _open_folder_button(v) -> None:
    if st.button("Mở thư mục (dự phòng)", key=f"openf_{v.channel_slug}_{v.number}"):
        try:
            os.startfile(str(v.folder))  # noqa: S606 - mở File Explorer, không ghi gì
        except (OSError, AttributeError) as e:
            st.error(f"Không mở được thư mục: {e}")


def _fb_optional_inputs(slug: str, number: int, page_url: str) -> None:
    """Link Facebook và ngày đăng: tùy chọn, bấm đánh dấu là đủ."""
    with st.expander("Thêm link, đổi ngày (tùy chọn)"):
        link = st.text_input("Link video Facebook (không bắt buộc)", key=f"fbl_{slug}_{number}")
        if link.strip():
            chk = upl.fb_link_check(conn, slug, link)
            if not chk.ok:
                st.error(chk.error)
            for m in chk.red:
                st.error(m)
            for m in chk.warnings:
                st.warning(m)
        today = _now().astimezone(fbm.VN_TZ).date()
        st.checkbox("Nhập ngày đăng (không chọn thì lấy lúc bấm + 1 giờ)", key=f"fbd_on_{slug}_{number}")
        if S.get(f"fbd_on_{slug}_{number}"):
            st.date_input("Ngày đăng (lưu lúc 12:00 giờ VN)", value=today, max_value=today, key=f"fbd_{slug}_{number}")


def _mark_block(slug: str, v, status: str, plat: str) -> None:
    """Nút chính «Đã đăng, đổi tên sau 1 giờ». Từng video, không vào hàng loạt."""
    if plat == us.FACEBOOK:
        _fb_optional_inputs(slug, v.number, upl.page_url_of(conn, slug))
    st.button("Đã đăng, đổi tên sau 1 giờ", key=f"mark_{slug}_{v.number}", type="primary", on_click=_cb_mark_stage,
              args=(slug, v.number, v.name, v.state or "", plat))
    if plat == us.YOUTUBE:
        st.caption("«Đã đăng, đổi tên sau 1 giờ»: đổi folder ngay cả khi app chưa kiểm được video. "
                   "«Đã upload, chờ public»: chỉ nhớ trong app, không đổi folder.")


def _waiting_block(slug: str, v, st_row: dict) -> None:
    st.info(f"Sẽ đổi tên lúc {_vn_time(st_row.get('due_at'))} (giờ VN). Trong lúc chờ vẫn huỷ được; đã đổi tên thì không có lệnh huỷ.")
    if st_row.get("try_error"):
        retry = f" Sẽ thử lại lúc {_vn_time(st_row['next_try_at'])}." if st_row.get("next_try_at") else ""
        st.warning(f"Chưa đổi tên được: {st_row['try_error']}.{retry}")
    c1, c2 = st.columns(2)
    if c1.button("Ghi ngay", key=f"now_{slug}_{v.number}"):
        with st.spinner("Đang ghi…"):
            r = upl.run_now(conn, cfg.SOCIAL_MEDIA_ROOT, slug, v.number)
        _scan_cached.clear()
        label = f"{slug} {v.number:03d}"
        S["up_result"] = ([f"{label}: đã đổi tên."], []) if r.resolved else ([], [f"{label}: {r.message}"])
        st.rerun()
    c2.button("Huỷ", key=f"cancelmark_{slug}_{v.number}", on_click=_cb_cancel_waiting, args=(slug, v.number))


def _fb_done_block(slug: str, v, st_row: dict) -> None:
    """Video Facebook đã «Xong» (app không thấy video): thêm/sửa link (chỉ DB app) và sửa ngày (ghi lại qua dịch vụ upload)."""
    with st.expander("Link, ngày đăng"):
        cur = st_row.get("fb_url") or ""
        st.caption(f"Ngày đã lưu: {_fmt_date(v.published_at or st_row.get('published_at'))}")
        link = st.text_input("Link video Facebook", value=cur, key=f"fbe_link_{slug}_{v.number}")
        if st.button("Lưu link", key=f"fbe_save_{slug}_{v.number}"):
            try:
                upl.set_fb_link(conn, slug, v.number, link)
            except upl.UploadError as e:
                st.error(str(e))
            else:
                st.success("Đã lưu link (chỉ trong app).")
        if link.strip():
            chk = upl.fb_link_check(conn, slug, link)
            for m in [*chk.red, *chk.warnings]:
                st.warning(m)
        today = _now().astimezone(fbm.VN_TZ).date()
        d = st.date_input("Sửa ngày (lưu lúc 12:00 giờ VN)", value=today, max_value=today, key=f"fbe_date_{slug}_{v.number}")
        if st.button("Sửa ngày", key=f"fbe_date_go_{slug}_{v.number}"):
            try:
                iso = fbm.date_to_iso(d, _now())
            except ValueError as e:
                st.error(str(e))
            else:
                r = upl.set_fb_date(conn, slug, v.number, iso, cfg.SOCIAL_MEDIA_ROOT)
                _scan_cached.clear()
                S["up_result"] = ([f"{slug} {v.number:03d}: đã sửa ngày."], []) if r.ok else ([], [f"{slug} {v.number:03d}: {r.message}"])
                st.rerun()


def _fb_page_ui(slug: str, info) -> None:
    """Kênh Facebook: khai URL trang (Thấu cần). Không có URL thì chỉ ẩn nút mở trang."""
    url = upl.page_url_of(conn, slug)
    with st.expander("URL trang Facebook", expanded=False):
        new = st.text_input("URL trang", value=url, key=f"fbpage_{slug}")
        if st.button("Lưu URL trang", key=f"fbpage_save_{slug}"):
            try:
                upl.set_page_url(conn, slug, new)
            except upl.UploadError as e:
                st.error(str(e))
            else:
                st.rerun()
        if not url:
            st.caption("Chưa có URL trang: vẫn dùng bình thường, chỉ không có nút mở trang.")


def _card_pipeline(slug: str, v, status: str, st_row: dict, props: list, remotes: dict, ch_scan, bound) -> None:
    info = ch_scan.info
    number = v.number
    plat = upl.platform_of(conn, slug)
    is_fb = plat == us.FACEBOOK
    real = bound["title"] if bound else ""
    head = st.columns([1, 6, 2])
    logo = ch_scan.brand[0].logo if ch_scan.brand else None
    if logo:
        with head[0]:
            _image(ch_scan.brand[0].path / logo, width=56)
    head[1].markdown(f"### {real or info.name or slug}  \n{info.language or '?'} · video {number:03d} · {us.ICONS[status]} {us.LABELS[status]}")
    if is_fb:
        page = upl.page_url_of(conn, slug)
        if page:
            head[2].link_button("Mở trang Facebook", page)
    else:
        head[2].link_button("Mở Studio", _studio_url(bound))
    if not bound and not is_fb:
        st.warning("Kênh chưa khai URL: chưa chắc đang đứng trên đúng kênh trong Studio.")
    if is_fb and v.description and "#shorts" in v.description.lower():
        st.warning("Mô tả có #Shorts (Facebook không dùng). Kiểm lại trước khi dán.")
    if status == us.DA_DANG_CHUA_LINK and not is_fb and us.unlinked_overdue(st_row.get("due_at"), _now()):
        st.warning("YouTube chưa thấy video trên kênh đã gán, kiểm xem có đăng nhầm kênh hoặc còn private.")
    waiting = st_row.get("manual_state") == us.MANUAL_WAITING_UPLOAD
    if waiting:  # theo trạng thái chờ thật, không theo trạng thái hiện (có đề xuất thì status là can_xac_nhan)
        _waiting_block(slug, v, st_row)
    if is_fb and status == us.XONG:
        _fb_done_block(slug, v, st_row)
    if v.title_en:
        st.caption(f"EN: {v.title_en}")
    if status == us.DANG_SAN_XUAT:
        st.info(f"Run đang ở trạng thái `{v.state}` ({v.lifecycle}). Chưa upload được.")
        return
    if v.youtube_url or st_row.get("video_url"):
        st.success(f"Link: {v.youtube_url or st_row.get('video_url')}")
    if status == us.GHI_THIEU:
        _pending_block(slug, v, st_row)

    missing = us.missing_items(has_mp4=bool(v.mp4s), has_title=bool(v.title), has_description=bool(v.description), has_thumb_candidates=bool(v.thumbnails))
    if missing:
        st.error("Thiếu đồ: " + ", ".join(missing))
    if v.description_has_youtube_link:
        st.warning("Mô tả có link video YouTube. Kiểm lại trước khi dán (app không sửa nội dung).")

    # 1. mp4
    st.markdown("**1. File video**")
    if len(v.mp4s) > 1:
        rels = [m.rel for m in v.mp4s]
        key = f"mp4_{slug}_{number}"
        cur = st_row.get("mp4_choice")
        S.setdefault(key, cur if cur in rels else rels[0])
        st.selectbox("Có nhiều mp4, chọn file", rels, key=key, on_change=_cb_mp4, args=(slug, number, key),
                     format_func=lambda r: f"{r} ({next(m.size for m in v.mp4s if m.rel == r) / 1e6:,.0f} MB)")
        chosen = S[key]
    else:
        chosen = v.mp4s[0].rel if v.mp4s else None
    if chosen:
        _code(str(v.folder / chosen))
    # 2. tiêu đề, 3. mô tả
    if v.title is not None:
        st.markdown("**2. Tiêu đề**")
        _code(v.title)
    if v.description is not None:
        st.markdown("**3. Mô tả**")
        _code(v.description)
    # 4. thumbnail
    st.markdown("**4. Thumbnail** (chọn tối đa 3)")
    if v.thumbnails:
        saved = [r for r in st_row.get("thumb_choice", []) if any(t.rel == r for t in v.thumbnails)]
        keys = {t.rel: f"th_{slug}_{number}_{i}" for i, t in enumerate(v.thumbnails)}
        cols = st.columns(3)
        for i, t in enumerate(v.thumbnails):
            with cols[i % 3]:
                p = v.folder / t.rel
                _image(p, width="stretch")
                st.checkbox(f"Chọn · {t.rel}", value=t.rel in saved, key=keys[t.rel], on_change=_cb_thumb, args=(slug, number, keys, t.rel))
                _code(str(p))
        if S.get("_up_thumb_warn") == (slug, number):
            st.warning("Tối đa 3 thumbnail. Bỏ chọn một ảnh trước.")
        if not saved:
            st.caption("Chưa chọn thumbnail nào.")
    else:
        st.caption("Không có ảnh ứng viên.")

    _proposal_block(slug, v, props, remotes, status)
    if bound and status in (us.CAN_XAC_NHAN, us.SAN_SANG, us.THIEU_DO, us.CHO_PUBLIC, us.DA_DANG_CHUA_LINK):
        _manual_block(slug, v, bound)
    c1, c2 = st.columns(2)
    if is_fb:
        pass  # Facebook không có «chờ public»: đánh dấu đã đăng là đủ
    elif st_row.get("manual_state") == WAIT_PUBLIC:
        c1.button("Bỏ đánh dấu chờ public", key=f"unwait_{slug}_{number}", on_click=_cb_wait, args=(slug, number, False))
    elif status in (us.SAN_SANG, us.THIEU_DO):
        c1.button("Đã upload, chờ public", key=f"wait_{slug}_{number}", on_click=_cb_wait, args=(slug, number, True))
    with c2:
        _open_folder_button(v)
    if us.can_mark_later(status, v.kind) and not waiting:
        _mark_block(slug, v, status, plat)
    _extras(v)


def _fb_old_block(slug: str, v, status: str, st_row: dict) -> None:
    """Video cũ của kênh Facebook: bấm là chỉ ghi DB app (không pipectl, không đổi folder), sửa được."""
    if status == us.XONG:
        if st_row.get("fb_url"):
            st.success(f"Link Facebook: {st_row['fb_url']}")
        st.caption(f"Đã đánh dấu đã đăng · ngày {_fmt_date(st_row.get('published_at'))} (chỉ trong app)")
        st.button("Bỏ đánh dấu", key=f"fbold_un_{slug}_{v.number}",
                  on_click=lambda: upl.unconfirm_facebook_old(conn, slug, v.number))
        return
    _fb_optional_inputs(slug, v.number, upl.page_url_of(conn, slug))
    if st.button("Đã đăng (chỉ ghi vào app)", key=f"fbold_{slug}_{v.number}", type="primary"):
        link, iso, err = _fb_inputs(slug, v.number)
        try:
            if err:
                raise upl.UploadError(err)
            upl.confirm_facebook_old(conn, slug, v.number, fb_url=link, published_at=iso or None)
        except upl.UploadError as e:
            st.error(str(e))
        else:
            st.rerun()


def _card_old(slug: str, v, status: str, st_row: dict, props: list, remotes: dict, ch_scan, bound) -> None:
    st.markdown(f"### {ch_scan.info.name or slug} · video {v.number:03d} (cũ)  \n{us.ICONS[status]} {us.LABELS[status]}")
    if st_row.get("video_url"):
        st.success(f"Link: {st_row['video_url']}")
    if v.meta_file:
        p = v.folder / v.meta_file
        st.caption(f"Nguyên văn `{v.meta_file}`")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:META_MAX_CHARS]
        except OSError as e:
            text = f"(không đọc được: {e})"
        _code(text)
    else:
        st.caption("Folder này không có file mô tả.")
    if upl.platform_of(conn, slug) == us.FACEBOOK:
        _fb_old_block(slug, v, status, st_row)
        _open_folder_button(v)
        return
    _proposal_block(slug, v, props, remotes, status)
    if st_row.get("video_url"):
        st.button("Bỏ xác nhận", key=f"unconf_{slug}_{v.number}", on_click=_cb_unconfirm, args=(slug, v.number))
    elif bound:
        _manual_block(slug, v, bound)
    if st_row.get("manual_state") == WAIT_PUBLIC:
        st.button("Bỏ đánh dấu chờ public", key=f"unwait_{slug}_{v.number}", on_click=_cb_wait, args=(slug, v.number, False))
    else:
        st.button("Đã upload, chờ public", key=f"wait_{slug}_{v.number}", on_click=_cb_wait, args=(slug, v.number, True))
    _open_folder_button(v)


# ---------------------------------------------------------------------------
# Xác nhận: hàng loạt + hộp xác nhận + chạy
# ---------------------------------------------------------------------------
def _write_enabled(bound: dict | None) -> bool:
    return bool(bound and cfg.api_key() and not bound.get("last_error"))


def _batch_ui(slug: str, ch_scan, status: dict, props_by_key: dict, remotes: dict, bound: dict | None) -> None:
    """Hàng loạt chỉ cho đề xuất mạnh. Nhóm bổ sung link và video cũ tick sẵn; nhóm đổi tên thì không."""
    groups: dict[str, list] = {us.GROUP_SUPPLEMENT: [], us.GROUP_RENAME: [], us.GROUP_OLD: []}
    for v in ch_scan.videos:
        if status[v.number] != us.CAN_XAC_NHAN:
            continue
        p = us.selectable_strong(props_by_key[v.key], v.key)
        if p:
            groups[us.confirm_group(v.kind, v.state)].append((v, p))
    if not any(groups.values()):
        return
    with st.expander("Xác nhận hàng loạt (chỉ đề xuất mạnh)", expanded=True):
        picked: list[dict] = []
        for g, items in groups.items():
            if not items:
                continue
            st.markdown(f"**{us.GROUP_LABELS[g]}** ({len(items)})")
            keys = []
            for v, p in items:
                k = f"bat_{slug}_{v.number}"
                keys.append(k)
                S.setdefault(k, g != us.GROUP_RENAME)
                rv = remotes.get(p.video_id)
                st.checkbox(f"{v.name} → {_yt_url(p.video_id)} · {_fmt_date(rv.published_at if rv else None)}", key=k)
                if S[k]:
                    picked.append(_row(slug, v, p.video_id, rv.published_at if rv else None))
            if g == us.GROUP_RENAME:
                st.button("Chọn tất cả đề xuất mạnh", key=f"bat_all_{slug}", on_click=_cb_select_all_rename, args=(keys,))
        st.button(f"Xem lại và xác nhận ({len(picked)})", key=f"bat_go_{slug}", type="primary", disabled=not picked,
                  on_click=_cb_stage, args=(slug, picked))
        st.caption("Đề xuất yếu không vào hàng loạt: mở từng thẻ để xác nhận.")


@st.dialog("Xác nhận ghi", width="large", on_dismiss=_cb_cancel_batch)
def _confirm_dialog(batch: dict, ch_scan, bound: dict | None) -> None:
    """Hộp thoại nổi giữa màn hình: luôn trong tầm nhìn dù thẻ video nằm sâu hay đang cuộn."""
    slug, rows = batch["slug"], batch["rows"]
    has_local = any(r["group"] != us.GROUP_OLD for r in rows)
    has_rename = any(r["group"] == us.GROUP_RENAME for r in rows)
    st.markdown(f"**{len(rows)} video**")
    for r in rows:
        st.markdown(f"- **{us.GROUP_LABELS[r['group']]}** · `{r['name']}` → {_yt_url(r['video_id'])} · {_fmt_date(r['published_at'])}")
    if has_rename:
        st.warning("Folder sẽ được đổi tên sang `__PUBLISHED`. Draft CapCut sẽ mất liên kết với folder cũ và KHÔNG có lệnh huỷ. "
                   "Đóng File Explorer, CapCut và Studio đang upload trước khi ghi.")
    elif has_local:
        st.info("Chỉ bổ sung link và ngày vào project.json và sổ ghi nhận. Folder không đổi tên.")
    blocked = has_local and not _write_enabled(bound)
    if blocked:
        st.error("API YouTube đang lỗi hoặc chưa có key nên chưa ghi được video pipeline. Thẻ và «Đã upload, chờ public» vẫn dùng được.")
    c1, c2 = st.columns(2)
    go = c1.button("Ghi vào máy", key=f"batch_run_{slug}", type="primary", disabled=blocked)
    if c2.button("Huỷ", key=f"batch_cancel_{slug}"):
        S.pop("up_batch", None)
        st.rerun()
    if not go:
        return
    done, errs = [], []
    prog = st.progress(0.0)
    for i, r in enumerate(rows):
        label = f"{slug} {r['number']:03d}"
        if r["group"] == us.GROUP_OLD:
            upl.confirm_old(conn, slug, r["number"], r["video_id"], r["published_at"] or None)
            done.append(f"{label}: đã ghi vào app.")
        else:
            res = upl.confirm_pipeline(conn, slug, r["number"], r["video_id"], cfg.SOCIAL_MEDIA_ROOT, api_key=cfg.api_key())
            _scan_cached.clear()
            if res.ok:
                done.append(f"{label}: đã ghi (folder, project.json, sổ ghi nhận).")
            else:
                head = "Ghi thiếu catalog (có nút thử lại trên thẻ). " if res.status == pipectl.CATALOG_PENDING else ""
                errs.append(f"{label}: {head}{res.message}")
                errs.append("Dừng ở lỗi đầu tiên, các video sau chưa ghi.")
                break
        prog.progress((i + 1) / len(rows))
    S.pop("up_batch", None)
    S["up_result"] = (done, errs)
    st.rerun()


@st.dialog("Đã đăng, đổi tên sau 1 giờ", width="large", on_dismiss=_cb_cancel_mark_dialog)
def _mark_dialog(m: dict) -> None:
    """Xác nhận đánh dấu tay. Bấm «Đánh dấu» chỉ ghi DB app; folder đổi tên khi tới giờ."""
    slug, number, plat = m["slug"], m["number"], m["plat"]
    now = _now()
    due = fbm.to_iso(now + upl.MARK_DELAY)
    st.markdown(f"**Folder:** `{m['name']}`")
    st.markdown(f"**Sẽ đổi tên lúc:** {_vn_time(due)} (giờ VN), sang `__PUBLISHED`.")
    st.markdown(f"**Ngày đăng ghi vào:** {_fmt_date(m['published_at'] or due)}" + ("" if m["published_at"] else " (lúc bấm + 1 giờ)"))
    if plat == us.FACEBOOK and m["fb_url"]:
        st.markdown(f"**Link Facebook (chỉ lưu trong app):** {m['fb_url']}")
    st.warning("Trong lúc chờ còn nút «Huỷ». Khi đã đổi tên thì KHÔNG có lệnh huỷ, và draft CapCut mất liên kết với folder cũ.")
    if plat == us.YOUTUBE:
        st.error("App chưa kiểm được video này thuộc đúng kênh. Khi YouTube đọc được, app sẽ đề xuất bổ sung link và ngày thật.")
    if m["error"]:
        st.error(m["error"])
    c1, c2 = st.columns(2)
    go = c1.button("Đánh dấu", key=f"mark_run_{slug}", type="primary", disabled=bool(m["error"]))
    if c2.button("Huỷ", key=f"mark_cancel_{slug}"):
        S.pop("up_mark", None)
        st.rerun()
    if not go:
        return
    try:
        due = upl.mark_later(conn, slug, number, fb_url=m["fb_url"] or None, published_at=m["published_at"] or None,
                             now=now, run_state=m["run_state"])
    except upl.UploadError as e:
        st.error(str(e))
        return
    S.pop("up_mark", None)
    S["up_result"] = ([f"{slug} {number:03d}: đã đánh dấu, sẽ đổi tên lúc {_vn_time(due)} (giờ VN)."], [])
    st.rerun()


@st.dialog("Kết quả ghi", width="large")
def _result_dialog(done: list, errs: list) -> None:
    """Kết quả (nhất là lỗi) nằm trong hộp thoại cho tới khi owner bấm Đóng; không tự tắt."""
    for m in done:
        st.success(m)
    for m in errs:
        st.error(m)
    if not done and not errs:
        st.info("Không có gì để báo.")
    if st.button("Đóng", key="result_close", type="primary"):
        st.rerun()


# ---------------------------------------------------------------------------
# Một kênh
# ---------------------------------------------------------------------------
def _queue_row(slug: str, v, status: str, selected: bool) -> None:
    c1, c2 = st.columns([5, 1])
    c1.markdown(f"{us.ICONS[status]} **{v.number:03d}** · {(v.title or v.slug_title or v.name)[:70]}")
    c2.button("Mở", key=f"open_{slug}_{v.number}", on_click=_cb_open, args=(slug, v.number), disabled=selected)


def _render_channel(slug: str, ch_scan, bound: dict | None, token: float, cross: list) -> None:
    info = ch_scan.info
    states = {s["number"]: s for s in repo.list_states(conn, slug)}
    plat = upl.platform_of(conn, slug)
    res = _propose(slug, ch_scan, bound, token)
    remotes = {r.video_id: r for r in _remotes(bound)}
    props_by_key = {}
    for v in ch_scan.videos:
        props_by_key[v.key] = [p for p in us.proposals_for(res.proposals, v.key)] if bound else []

    status = {}
    for v in ch_scan.videos:
        props = props_by_key[v.key]
        status[v.number] = us.status_for(v, states.get(v.number), bool(props), plat)

    st.subheader(f"{(bound or {}).get('title') or info.name or slug}")
    st.caption(f"{info.language or '?'} · folder `{slug}`" + (f" · cập nhật YouTube lúc {_fmt_date(bound.get('last_refresh_at'))}" if bound else ""))
    if bound and bound.get("last_error"):
        st.warning(f"Lỗi lần cập nhật YouTube gần nhất: {bound['last_error']}")
    for w in cross:
        if w.channel_slug == slug:
            st.error(f"Khớp chéo: tiêu đề video {w.number:03d} của kênh này đang xuất hiện trên kênh «{w.other_slug}» ({_yt_url(w.video_id)}).")
    if plat == us.FACEBOOK:
        st.caption("Kênh chỉ đăng Facebook: app không thấy video, «Xong» nghĩa là bạn đã bấm đánh dấu.")
        _fb_page_ui(slug, info)
    else:
        _binding_ui(slug, ch_scan, bound)
    _batch_ui(slug, ch_scan, status, props_by_key, remotes, bound)

    open_props = [p for ps in props_by_key.values() for p in ps]
    todo_videos = [v for v in ch_scan.videos if status[v.number] in us.TODO_ORDER]
    if plat == us.YOUTUBE and bound and remotes and not open_props and any(status[v.number] != us.XONG for v in ch_scan.videos if v.kind == "pipeline"):
        st.warning(f"Kênh đã gán là «{bound['title']}» và có {len(remotes)} video nhưng không có đề xuất nào. **Có phải kênh của bạn không?** Nếu sai, đổi gán ở trên.")

    left, right = st.columns([2, 3])
    sel = S.get("up_sel")
    with left:
        st.markdown("#### Việc cần làm")
        if not todo_videos:
            st.caption("Không có việc cần làm.")
        for stt in us.TODO_ORDER:
            group = [v for v in todo_videos if status[v.number] == stt]
            if not group:
                continue
            st.markdown(f"{us.ICONS[stt]} **{us.LABELS[stt]}** ({len(group)})")
            for v in group:
                _queue_row(slug, v, stt, sel == (slug, v.number))
        for stt, title in ((us.DANG_SAN_XUAT, "Đang sản xuất"), (us.XONG, "Đã xong"), (us.CHUA_DOI_CHIEU, "Video cũ, chưa đối chiếu")):
            group = [v for v in ch_scan.videos if status[v.number] == stt]
            if group:
                with st.expander(f"{title} ({len(group)})"):
                    for v in group:
                        _queue_row(slug, v, stt, sel == (slug, v.number))
        if ch_scan.junk:
            with st.expander(f"Folder rác ({len(ch_scan.junk)})"):
                for j in ch_scan.junk:
                    st.markdown(f"`{j.name}` · {j.file_count} file")
                st.caption("App không xoá gì. Dọn tay nếu muốn.")
        if ch_scan.other or ch_scan.brand:
            with st.expander(f"Khác ({len(ch_scan.other) + len(ch_scan.brand)})"):
                for o in [*ch_scan.brand, *ch_scan.other]:
                    st.markdown(f"`{o.name}` ({o.kind})")
        if bound:
            unmatched = [r for r in res.unmatched_remotes if r.video_id not in _linked_ids(ch_scan, states)]
            with st.expander(f"Video trên kênh chưa thuộc folder nào ({len(unmatched)})"):
                for r in unmatched:
                    st.markdown(f"[{r.title}]({_yt_url(r.video_id)}) · {_fmt_date(r.published_at)}")
    with right:
        v = next((x for x in ch_scan.videos if sel == (slug, x.number)), None)
        if v is None:
            st.info("Chọn một video bên trái để mở thẻ.")
        else:
            fn = _card_pipeline if v.kind == "pipeline" else _card_old
            fn(slug, v, status[v.number], states.get(v.number) or {}, props_by_key[v.key], remotes, ch_scan, bound)


# ---------------------------------------------------------------------------
# Trang
# ---------------------------------------------------------------------------
data = _load()
if data is None:
    st.stop()
scan = data["scan"]
bound_all = {c["channel_slug"]: c for c in repo.list_channels(conn)}

for m in S.pop("_up_msg", []):
    st.info(m)

due_box = st.container()  # lỗi của việc đổi tên đến hạn hiện ở đây (điền sau khi trang dựng xong)
top = st.columns([3, 1, 1])
top[0].caption(f"Gốc dữ liệu: `{scan.root}` · {len(scan.videos())} video")
if top[1].button("Làm mới danh sách folder"):
    _load(force=True)
    st.rerun()
if top[2].button("Cập nhật từ YouTube", disabled=not (bound_all and cfg.api_key())):
    with st.spinner("Đang đọc danh sách video từ YouTube…"):
        r = upl.refresh(conn, list(bound_all), api_key=cfg.api_key())
    S["_up_msg"] = [f"{k}: {n} video" for k, n in r.counts.items()] + [f"{k}: lỗi {e}" for k, e in r.errors.items()]
    st.rerun()

# Hộp thoại (một cái mỗi lần chạy): kết quả ghi trước, rồi hộp xác nhận. Hộp xác nhận giữ trạng thái tới khi Huỷ, ghi xong hoặc đóng bằng dấu X (on_dismiss xoá).
_res = S.pop("up_result", None)
_batch = S.get("up_batch")
if _res:
    _result_dialog(*_res)
elif S.get("up_mark"):
    _mark_dialog(S["up_mark"])
elif _batch and _batch["slug"] in scan.channels:
    _confirm_dialog(_batch, scan.channels[_batch["slug"]], bound_all.get(_batch["slug"]))

slugs = list(scan.channels)
status_by_slug = {}
for slug in slugs:
    ch_scan = scan.channels[slug]
    states = {s["number"]: s for s in repo.list_states(conn, slug)}
    n = 0
    for v in ch_scan.videos:
        props_exist = False
        if slug in bound_all:
            props_exist = bool(us.proposals_for(_propose(slug, ch_scan, bound_all[slug], data["at"]).proposals, v.key))
        if us.status_for(v, states.get(v.number), props_exist, upl.platform_of(conn, slug)) in us.TODO_ORDER:
            n += 1
    status_by_slug[slug] = n


def _chan_label(slug: str) -> str:
    if upl.platform_of(conn, slug) == us.FACEBOOK:
        flag = " · Facebook"
    else:
        flag = "" if slug in bound_all else " chưa khai URL"
    return f"{scan.channels[slug].info.name or slug} · {status_by_slug[slug]}{flag}"


# Kênh đang chọn nhớ ở khoá riêng: st.rerun() trong hộp thoại chạy trước khi radio được dựng nên Streamlit xoá khoá của widget.
if S.get("up_chan") not in slugs and slugs:
    S["up_chan"] = S["_up_chan_keep"] if S.get("_up_chan_keep") in slugs else slugs[0]
chosen_slug = st.radio("Kênh", slugs, key="up_chan", horizontal=True, format_func=_chan_label)
S["_up_chan_keep"] = chosen_slug

# khớp chéo giữa các kênh đã gán
cross_key = (data["at"], tuple(sorted((s, c.get("last_refresh_at")) for s, c in bound_all.items())))
if S.get("_up_cross_key") != cross_key:
    items_by = {s: [us.to_match_item(v) for v in scan.channels[s].videos] for s in bound_all if s in scan.channels}
    rem_by = {s: _remotes(c) for s, c in bound_all.items() if s in scan.channels}
    S["_up_cross"] = um.cross_channel(items_by, rem_by)
    S["_up_cross_key"] = cross_key

if chosen_slug:
    _render_channel(chosen_slug, scan.channels[chosen_slug], bound_all.get(chosen_slug), data["at"], S["_up_cross"])

st.caption(f"Hàng đợi dựng trong {time.perf_counter() - _T0:.2f} giây (dữ liệu pipectl/folder dùng lại tối đa {RELOAD_S} giây).")

# Đổi tên đến hạn của «Đã đăng, đổi tên sau 1 giờ»: sau khi trang đã hiện, ở mỗi lần chạy lại (kiểm DB rất rẻ; chỉ gọi pipectl
# khi có mục đến hạn). Không có tiến trình nền: app đóng thì đợi lần mở sau, ngày đã lưu không đổi.
_due = upl.run_due(conn, cfg.SOCIAL_MEDIA_ROOT)
if _due:
    _done = [f"{r.slug} {r.number:03d}: {'đã đổi tên.' if r.status == pipectl.OK else r.message}" for r in _due
             if r.status in (pipectl.OK, upl.SKIPPED)]
    _pend = [f"{r.slug} {r.number:03d}: Ghi thiếu catalog (có nút thử lại trên thẻ). {r.message}" for r in _due
             if r.status == pipectl.CATALOG_PENDING]
    _errs = [f"{r.slug} {r.number:03d}: {r.message}" for r in _due if not r.resolved]
    with due_box:
        for m in _errs:  # LOCKED / catalog chưa sẵn sàng: giữ lại, lần sau thử tiếp (không phải lỗi nặng)
            st.warning(f"Chưa đổi tên được, sẽ thử lại: {m}")
    if _done or _pend:
        _scan_cached.clear()
        S["up_result"] = (_done, _pend)
        st.rerun()


@st.fragment(run_every=60)
def _due_watch() -> None:
    """Mỗi phút kiểm DB; chỉ chạy lại cả trang khi có mục đến hạn."""
    if not S.get("_up_due_armed"):  # lần chạy đầu (lúc dựng trang) chỉ lên cò; chỉ lần theo chu kỳ mới được chạy lại trang
        S["_up_due_armed"] = True
        return
    if upl.due_items(conn):  # đã loại mục đang đợi giờ thử lại nên không lặp vô tận
        st.rerun()


S["_up_due_armed"] = False
_due_watch()

# Thử lại ghi thiếu: chỉ khi có mục catalog_pending, sau khi trang đã hiện, một lần mỗi phiên (không theo chu kỳ).
if not S.get("_up_pend_done"):
    S["_up_pend_done"] = True
    pend = repo.list_pending(conn)
    if pend:
        errs, ok_n = [], 0
        with st.spinner("Đang thử lại các mục ghi thiếu…"):
            for p in pend:
                r = upl.retry_pending(conn, p["channel_slug"], p["number"], cfg.SOCIAL_MEDIA_ROOT)
                ok_n += r.ok
                if not r.ok:
                    errs.append(f"{p['channel_slug']} {p['number']:03d}: {r.message}")
        _scan_cached.clear()
        if ok_n or errs:
            S["up_result"] = ([f"Thử lại tự động: đã ghi đủ cho {ok_n} video."] if ok_n else [], errs)
        st.rerun()

# Làm mới nền: sau khi trang đã hiện, một lần mỗi phiên, chỉ khi ảnh chụp cũ.
if not S.get("_up_auto_done"):
    S["_up_auto_done"] = True
    stale = [s for s, c in bound_all.items() if _stale(c)]
    if stale and cfg.api_key():
        with st.spinner("Đang cập nhật danh sách video từ YouTube…"):
            upl.refresh(conn, stale, api_key=cfg.api_key())
        st.rerun()
    elif stale:
        st.caption("Chưa có YouTube API key nên chưa cập nhật danh sách video.")
