"""Trang Upload (chỉ đọc): hàng đợi upload theo kênh, thẻ copy theo thứ tự form Studio, đối chiếu video đã lên.

Owner tự upload trên Studio. Trang này không ghi gì vào folder, project.json hay sổ ghi nhận của pipeline; chỉ ghi DB của app
(kênh đã gán, lựa chọn mp4/thumbnail, "chờ public", đề xuất bị từ chối). Mở trang chỉ gọi `pipectl runs`.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import streamlit as st

from yt_niche import config as cfg
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
st.title("📤 Upload")
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


def _proposal_block(slug: str, v, props: list, remotes: dict) -> None:
    if not props:
        return
    st.markdown("**Đối chiếu với kênh YouTube**")
    for p in props:
        rv = remotes.get(p.video_id)
        title = rv.title if rv else ""
        kind = "🟢 Đề xuất mạnh" if p.strength == um.STRONG else f"🟡 Đề xuất yếu ({p.score:.0%}, {p.reason})"
        c1, c2 = st.columns([5, 2])
        c1.markdown(f"{kind}: [{title}]({_yt_url(p.video_id)})  \nNgày đăng: {_fmt_date(rv.published_at if rv else None)}")
        c2.button("Không phải video này", key=f"rej_{slug}_{v.number}_{p.video_id}", on_click=_cb_reject, args=(slug, v.number, p.video_id))
    st.caption("Xác nhận và ghi vào máy chưa bật ở bản này.")


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


def _card_pipeline(slug: str, v, status: str, st_row: dict, props: list, remotes: dict, ch_scan, bound) -> None:
    info = ch_scan.info
    number = v.number
    real = bound["title"] if bound else ""
    head = st.columns([1, 6, 2])
    logo = ch_scan.brand[0].logo if ch_scan.brand else None
    if logo:
        with head[0]:
            _image(ch_scan.brand[0].path / logo, width=56)
    head[1].markdown(f"### {real or info.name or slug}  \n{info.language or '?'} · video {number:03d} · {us.ICONS[status]} {us.LABELS[status]}")
    head[2].link_button("Mở Studio", _studio_url(bound))
    if not bound:
        st.warning("Kênh chưa khai URL: chưa chắc đang đứng trên đúng kênh trong Studio.")
    if v.title_en:
        st.caption(f"EN: {v.title_en}")
    if status == us.DANG_SAN_XUAT:
        st.info(f"Run đang ở trạng thái `{v.state}` ({v.lifecycle}). Chưa upload được.")
        return
    if v.youtube_url or st_row.get("video_url"):
        st.success(f"Link: {v.youtube_url or st_row.get('video_url')}")

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
                _image(p, use_container_width=True)
                st.checkbox(f"Chọn · {t.rel}", value=t.rel in saved, key=keys[t.rel], on_change=_cb_thumb, args=(slug, number, keys, t.rel))
                _code(str(p))
        if S.get("_up_thumb_warn") == (slug, number):
            st.warning("Tối đa 3 thumbnail. Bỏ chọn một ảnh trước.")
        if not saved:
            st.caption("Chưa chọn thumbnail nào.")
    else:
        st.caption("Không có ảnh ứng viên.")

    _proposal_block(slug, v, props, remotes)
    c1, c2 = st.columns(2)
    if st_row.get("manual_state") == WAIT_PUBLIC:
        c1.button("Bỏ đánh dấu chờ public", key=f"unwait_{slug}_{number}", on_click=_cb_wait, args=(slug, number, False))
    elif status in (us.SAN_SANG, us.THIEU_DO):
        c1.button("Đã upload, chờ public", key=f"wait_{slug}_{number}", on_click=_cb_wait, args=(slug, number, True))
    with c2:
        _open_folder_button(v)
    _extras(v)


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
    _proposal_block(slug, v, props, remotes)
    if st_row.get("manual_state") == WAIT_PUBLIC:
        st.button("Bỏ đánh dấu chờ public", key=f"unwait_{slug}_{v.number}", on_click=_cb_wait, args=(slug, v.number, False))
    else:
        st.button("Đã upload, chờ public", key=f"wait_{slug}_{v.number}", on_click=_cb_wait, args=(slug, v.number, True))
    _open_folder_button(v)


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
    res = _propose(slug, ch_scan, bound, token)
    remotes = {r.video_id: r for r in _remotes(bound)}
    props_by_key = {}
    for v in ch_scan.videos:
        props_by_key[v.key] = [p for p in us.proposals_for(res.proposals, v.key)] if bound else []

    status = {}
    for v in ch_scan.videos:
        props = props_by_key[v.key]
        status[v.number] = us.status_for(v, states.get(v.number), bool(props))

    st.subheader(f"{(bound or {}).get('title') or info.name or slug}")
    st.caption(f"{info.language or '?'} · folder `{slug}`" + (f" · cập nhật YouTube lúc {_fmt_date(bound.get('last_refresh_at'))}" if bound else ""))
    if bound and bound.get("last_error"):
        st.warning(f"Lỗi lần cập nhật YouTube gần nhất: {bound['last_error']}")
    for w in cross:
        if w.channel_slug == slug:
            st.error(f"Khớp chéo: tiêu đề video {w.number:03d} của kênh này đang xuất hiện trên kênh «{w.other_slug}» ({_yt_url(w.video_id)}).")
    _binding_ui(slug, ch_scan, bound)

    open_props = [p for ps in props_by_key.values() for p in ps]
    todo_videos = [v for v in ch_scan.videos if status[v.number] in us.TODO_ORDER]
    if bound and remotes and not open_props and any(status[v.number] != us.XONG for v in ch_scan.videos if v.kind == "pipeline"):
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
        if us.status_for(v, states.get(v.number), props_exist) in us.TODO_ORDER:
            n += 1
    status_by_slug[slug] = n


def _chan_label(slug: str) -> str:
    flag = "" if slug in bound_all else " ⚠️ chưa khai URL"
    return f"{scan.channels[slug].info.name or slug} · {status_by_slug[slug]}{flag}"


if S.get("up_chan") not in slugs and slugs:
    S["up_chan"] = slugs[0]
chosen_slug = st.radio("Kênh", slugs, key="up_chan", horizontal=True, format_func=_chan_label)

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
