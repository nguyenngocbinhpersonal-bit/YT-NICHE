"""Kiểm trang Upload trên dữ liệu thật. Mặc định chỉ đọc: không ghi folder, không ghi project.json, không đụng catalog,
không chạy mark-published; danh sách video YouTube được đọc vào DB tạm trong bộ nhớ, không vào DB app.

  .venv\\Scripts\\python.exe scripts\\check_upload_live.py --no-write-check
  ... --no-api                                  chỉ dùng ảnh chụp đã lưu trong DB app, không gọi YouTube
  ... --compare-handle amateur-filmmaker @jeremybearimytheory   đối chứng âm (không lưu vào DB)
  ... --bind stillhouse-stories @stillhouse-stories             GHI DB app: gán kênh (như bấm "Gán kênh này")
"""

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

from yt_niche import config
from yt_niche.core import upload_match as um
from yt_niche.core import upload_state as us
from yt_niche.db import uploads as repo
from yt_niche.db.connection import connect
from yt_niche.services import pipectl, upload_scan
from yt_niche.services import uploads as upl
from yt_niche.sources.youtube import YouTubeClient

EXPECTED = {"pipeline": 31, "old": 39, "junk": 6, "brand": 5}
STILLHOUSE = {7: "2026-11-02T08:40", 8: "2026-11-05T15:43", 9: "2026-11-06T23:06"}
MIN_SAMPLE = 3


def hr(title: str) -> None:
    print(f"\n=== {title} ===")


def check_counts(scan) -> None:
    hr("1. Số đếm folder")
    c = scan.counts()
    print(f"tổng {c['total']} = pipeline {c['pipeline']} + cũ {c['old']} + rác {c['junk']} + Brand {c['brand']} + khác {c['other']}")
    print(f"kỳ vọng 07/11: {sum(EXPECTED.values())} = " + " + ".join(f"{k} {v}" for k, v in EXPECTED.items()))
    diff = {k: c[k] - v for k, v in EXPECTED.items() if c[k] != v}
    print("khớp kỳ vọng" if not diff and not c["other"] else f"chênh so với kỳ vọng: {diff} (khác: {c['other']}); do owner thêm/đổi video thì bình thường")
    for ch in scan.channels.values():
        for j in ch.junk:
            print(f"  rác: {ch.info.slug}/{j.name} ({j.file_count} file)")


def check_bytes(scan) -> int:
    hr("2. Thẻ so với publish.json (từng byte)")
    bad = total = 0
    edge = []
    for v in scan.videos():
        if v.kind != "pipeline":
            continue
        pj = v.folder / "artifacts" / "publish.json"
        if not pj.is_file():
            print(f"  {v.channel_slug} {v.number:03d}: chưa có publish.json (state={v.state})")
            continue
        total += 1
        d = json.loads(pj.read_text(encoding="utf-8"))
        want_t, want_d = d["title"]["primary"], d["description"]["primary"]
        if v.title != want_t or v.description != want_d:
            bad += 1
            print(f"  LỆCH {v.channel_slug} {v.number:03d}")
        if want_d != want_d.strip() or want_t != want_t.strip():
            edge.append(f"{v.channel_slug} {v.number:03d}")
    print(f"{total - bad}/{total} video trùng từng byte" + ("" if not bad else f", {bad} LỆCH"))
    if edge:
        print("  có khoảng trắng/xuống dòng đầu cuối (khối copy bọc thêm một cặp xuống dòng để giữ đúng): " + ", ".join(edge))
    return bad


def check_missing(scan) -> None:
    hr("3. Video thiếu đồ")
    n = 0
    for v in scan.videos():
        if v.kind != "pipeline" or v.state not in ("complete", "published"):
            continue
        miss = us.missing_items(has_mp4=bool(v.mp4s), has_title=bool(v.title), has_description=bool(v.description), has_thumb_candidates=bool(v.thumbnails))
        if miss:
            n += 1
            print(f"  {v.channel_slug} {v.number:03d}: thiếu {', '.join(miss)}")
        if any("thumbnails." in t.rel for t in v.thumbnails):
            print(f"  LỖI {v.channel_slug} {v.number:03d}: ảnh ghép thumbnails.* lọt vào ứng viên")
    print("không có video thiếu đồ" if not n else f"{n} video thiếu đồ")


def mem_copy_of_bound(real) -> sqlite3.Connection:
    mem = connect(":memory:")
    for ch in repo.list_channels(real):
        repo.upsert_channel(mem, ch["channel_slug"], ch)
    mem.execute("UPDATE upload_channel SET last_refresh_at = NULL")
    for slug, n, vid in real.execute("SELECT channel_slug, number, video_id FROM upload_match_rejected").fetchall():
        repo.reject_match(mem, slug, n, vid)
    return mem


def date_floor(ch_scan) -> str | None:
    created = [v.created for v in ch_scan.videos if v.kind == "pipeline" and v.created]
    return min(created) if created else None


def report_channel(slug: str, ch_scan, remotes: list[um.RemoteVideo], rejected) -> dict:
    items = [us.to_match_item(v) for v in ch_scan.videos]
    res = um.propose(items, remotes, rejected)
    by_key = {v.key: v for v in ch_scan.videos}
    floor = date_floor(ch_scan)
    prop_by_video = {p.video_id: p for p in res.proposals}
    rv_by_id = {r.video_id: r for r in remotes}
    in_range = [r for r in remotes if floor and (r.published_at or "") >= floor]
    # video cũ không tính: remote nào đề xuất cho video cũ thì bỏ khỏi mẫu
    counted = [r for r in in_range if not (r.video_id in prop_by_video and by_key[(prop_by_video[r.video_id].channel_slug, prop_by_video[r.video_id].number)].kind == "old")]
    strong = [r for r in counted if r.video_id in prop_by_video and prop_by_video[r.video_id].strength == um.STRONG]
    weak = [r for r in counted if r.video_id in prop_by_video and prop_by_video[r.video_id].strength == um.WEAK]
    none = [r for r in counted if r.video_id not in prop_by_video]
    print(f"\n[{slug}] kênh có {len(remotes)} video; dải pipeline từ {floor}: {len(in_range)} video, tính {len(counted)} (bỏ video cũ)")
    n = len(counted)
    print(f"  đề xuất mạnh {len(strong)} · yếu {len(weak)} (chờ owner soát) · không có {len(none)}")
    if n >= MIN_SAMPLE:
        print(f"  tỉ lệ mạnh {len(strong) / n:.0%}; mạnh + yếu chưa soát {(len(strong) + len(weak)) / n:.0%} (yếu chỉ tính khi owner xác nhận đúng)")
    else:
        print(f"  mẫu {n} < {MIN_SAMPLE}: chỉ đọc số thô, không in phần trăm")
    for r in weak:
        p = prop_by_video[r.video_id]
        v = by_key[(p.channel_slug, p.number)]
        print(f"    YẾU {p.number:03d} ({p.score:.2f}, {p.reason}): local «{(v.titles or [v.slug_title])[0]}» ~ YouTube «{r.title}»")
    for r in none:
        print(f"    không đề xuất: «{r.title}» ({r.published_at})")
    pub_total = sum(1 for v in ch_scan.videos if v.kind == "pipeline" and v.state == "published")
    pub_strong = sum(1 for v in ch_scan.videos if v.kind == "pipeline" and v.state == "published"
                     and any(p.strength == um.STRONG and (p.channel_slug, p.number) == v.key for p in res.proposals))
    print(f"  video pipeline đã published có đề xuất mạnh: {pub_strong}/{pub_total}")
    for p in res.proposals:
        if p.strength == um.STRONG:
            print(f"    mạnh {p.number:03d} → {rv_by_id[p.video_id].published_at} {rv_by_id[p.video_id].title[:60]}")
    return {"res": res, "strong": strong, "weak": weak}


def check_stillhouse(slug: str, out: dict, remotes) -> bool:
    ok = True
    rv = {r.video_id: r for r in remotes}
    strong = {p.number: rv[p.video_id].published_at for p in out["res"].proposals if p.strength == um.STRONG}
    anyp = {p.number for p in out["res"].proposals}
    print(f"\n[{slug}] kỳ vọng Stillhouse:")
    for n, day in STILLHOUSE.items():
        got = strong.get(n)
        good = bool(got and got.startswith(day))
        ok &= good
        print(f"  {n:03d} mạnh đúng ngày {day}: {'OK' if good else 'SAI'} ({got})")
    for n in (5, 6):
        good = n in anyp
        ok &= good
        print(f"  {n:03d} có đề xuất bổ sung link: {'OK' if good else 'THIẾU'}")
    return ok


def report_facebook(scan, real) -> None:
    """Kênh chỉ đăng Facebook: số video đã đánh dấu. Không tính vào tỉ lệ khớp (app không thấy video Facebook)."""
    hr("Kênh Facebook (không tính vào tỉ lệ khớp)")
    fb_slugs = [s for s in scan.channels if upl.platform_of(real, s) == us.FACEBOOK]
    if not fb_slugs:
        print("không có kênh Facebook.")
    for slug in fb_slugs:
        ch = scan.channels[slug]
        states = {x["number"]: x for x in repo.list_states(real, slug)}
        pipe = [v for v in ch.videos if v.kind == "pipeline"]
        marked = [v for v in pipe if v.state == "published"]
        waiting = [v for v in pipe if (states.get(v.number) or {}).get("manual_state") == us.MANUAL_WAITING_UPLOAD]
        old_done = [v for v in ch.videos if v.kind == "old" and (states.get(v.number) or {}).get("manual_state") == us.MANUAL_FB_DONE]
        print(f"  {slug}: {len(pipe)} video pipeline, đã đánh dấu (published) {len(marked)}, đang chờ đổi tên {len(waiting)}, "
              f"video cũ đã đánh dấu {len(old_done)}; trang: {upl.page_url_of(real, slug) or '(chưa có URL)'}")


def compare_negative(slug: str, handle: str, scan) -> bool:
    hr(f"Đối chứng âm: {slug} so với {handle}")
    mem = connect(":memory:")

    async def go():
        async with YouTubeClient(mem, api_key=config.api_key(), fresh=True) as yt:
            info = await yt.channel_lookup(handle=handle)
            if not info:
                return None, []
            return info, await yt.upload_items(info["uploads_playlist"])

    info, items = asyncio.run(go())
    if not info:
        print("không tìm thấy kênh đối chứng")
        return False
    remotes = [um.RemoteVideo(i["video_id"], info["channel_id"], i["title"], i["published_at"]) for i in items]
    ch = scan.channels[slug]
    res = um.propose([us.to_match_item(v) for v in ch.videos], remotes, frozenset())
    strong = [p for p in res.proposals if p.strength == um.STRONG]
    weak = [p for p in res.proposals if p.strength == um.WEAK]
    print(f"kênh đối chứng «{info['title']}» có {len(remotes)} video; kênh local «{ch.info.name}» {len(ch.videos)} video")
    print(f"đề xuất mạnh {len(strong)} (kỳ vọng 0) · yếu {len(weak)}")
    return not strong


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-api", action="store_true", help="không gọi YouTube; dùng ảnh chụp đã lưu")
    ap.add_argument("--no-write-check", action="store_true", help="(mặc định) không ghi gì ra folder/catalog; cờ này chỉ để lệnh ghi rõ ý đó")
    ap.add_argument("--compare-handle", nargs=2, metavar=("SLUG", "HANDLE"), help="đối chứng âm, không lưu vào DB")
    ap.add_argument("--bind", nargs=2, metavar=("SLUG", "HANDLE"), help="GHI DB app: gán kênh (xem trước tên rồi gán)")
    args = ap.parse_args()

    t0 = time.perf_counter()
    runs = pipectl.runs(config.SOCIAL_MEDIA_ROOT)
    t1 = time.perf_counter()
    scan = upload_scan.scan(config.SOCIAL_MEDIA_ROOT, runs)
    t2 = time.perf_counter()
    print(f"pipectl runs {t1 - t0:.2f}s · quét folder {t2 - t1:.2f}s")

    real = connect()
    if args.bind:
        slug, handle = args.bind
        prev = upl.preview_binding(real, slug, handle, scan.channels[slug].info.name, [t for v in scan.channels[slug].videos for t in v.titles], api_key=config.api_key())
        print(f"gán {slug}: kênh thật «{prev['channel']['title']}», tên khớp: {prev['name_matches']}, đã gán cho: {prev['bound_to']}")
        if not prev["name_matches"] or prev["bound_to"]:
            print("KHÔNG gán (tên lệch hoặc đã gán chỗ khác).")
            return 1
        upl.bind(real, slug, prev["channel"])
        print("đã ghi DB app.")

    check_counts(scan)
    bad = check_bytes(scan)
    check_missing(scan)

    hr("4. Đề xuất đối chiếu (kênh đã gán)")
    bound = {c["channel_slug"]: c for c in repo.list_channels(real)}
    if not bound:
        print("chưa kênh nào được gán URL.")
    use = real
    if bound and not args.no_api:
        use = mem_copy_of_bound(real)
        res = upl.refresh(use, list(bound), api_key=config.api_key())
        for s, e in res.errors.items():
            print(f"  lỗi API {s}: {e}")
    ok = bad == 0
    rem_by: dict[str, list[um.RemoteVideo]] = {}
    for slug in bound:
        if slug not in scan.channels or upl.platform_of(real, slug) == us.FACEBOOK:
            continue
        ch = repo.get_channel(use, slug)
        remotes = [um.RemoteVideo(r["video_id"], r["channel_id"], r["title"], r["published_at"]) for r in repo.list_remote(use, ch["channel_id"])]
        rem_by[slug] = remotes
        t = time.perf_counter()
        out = report_channel(slug, scan.channels[slug], remotes, repo.rejected_pairs(real, slug))
        print(f"  (đối chiếu {time.perf_counter() - t:.2f}s)")
        if slug == "stillhouse-stories":
            ok &= check_stillhouse(slug, out, remotes)
    if rem_by:
        warns = um.cross_channel({s: [us.to_match_item(v) for v in scan.channels[s].videos] for s in rem_by}, rem_by)
        print(f"\nkhớp chéo giữa các kênh: {len(warns)} cảnh báo (kỳ vọng 0)")
        for w in warns:
            print(f"  {w.channel_slug} {w.number:03d} đang nằm trên kênh {w.other_slug}: {w.title}")
        ok &= not warns

    report_facebook(scan, real)

    if args.compare_handle:
        if args.no_api:
            print("--no-api: bỏ qua đối chứng âm")
        else:
            ok &= compare_negative(args.compare_handle[0], args.compare_handle[1], scan)

    hr("Kết luận")
    print("PASS" if ok else "CÓ MỤC CẦN XEM (xem phía trên)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
