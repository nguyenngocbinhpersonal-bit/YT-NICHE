"""Quét folder cho trang Upload: cây giả trong tmp_path, không gọi pipectl, không đụng dữ liệu thật."""

import json
import os
import sys
from pathlib import Path

import pytest

from yt_niche import config
from yt_niche.services import pipectl, upload_scan

SLUG = "chan-a"


def w(path: Path, data: str | bytes = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="")
    else:
        path.write_bytes(data)
    return path


def make_pipeline(cdir: Path, name: str, vid: str, *, title="Tiêu đề chính", desc="  mô tả  \n\nxin chào  ") -> Path:
    d = cdir / name
    w(d / "project.json", json.dumps({"video_id": vid, "channel_slug": cdir.name, "published_at": "2026-11-02T08:40:00Z"}))
    pub = {
        "title": {"primary": title, "en": "EN " + title},
        "description": {"primary": desc, "en": "x"},
        "disclaimer": {"primary": "disc", "en": "disc-en"},
        "tags": ["a", "b"],
        "chapters": [{"time": "00:00", "label": "x"}],
    }
    w(d / "artifacts" / "publish.json", json.dumps(pub, ensure_ascii=False))
    return d


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "social"
    c = root / SLUG
    return root, c


def run_entry(folder: Path, vid: str, state="complete", lifecycle="active"):
    return {
        "run_dir": str(folder),
        "channel_slug": folder.parent.name,
        "video_id": vid,
        "state": state,
        "lifecycle": lifecycle,
        "created": "2026-11-01T05:00:00Z",
    }


# ---------------------------------------------------------------------------
def test_classification_all_kinds(tree):
    root, c = tree
    # pipeline
    p = make_pipeline(c, "11-01-13-21_chan-a-video-007-foo-bar__PUBLISHED", "007")
    w(p / "vid.mp4")
    # junk: visual/ rỗng + sibling __PUBLISHED (cùng tên không hậu tố)
    (c / "11-01-13-21_chan-a-video-007-foo-bar" / "visual").mkdir(parents=True)
    # junk chỉ có run_log.jsonl
    make_pipeline(c, "10-30-08-18_chan-a-video-044-x__PUBLISHED", "044")
    w(c / "10-30-08-18_chan-a-video-044-x" / "run_log.jsonl", "{}")
    # junk có file lạ (không phải mp4)
    make_pipeline(c, "10-30-08-19_chan-a-video-045-y__PUBLISHED", "045")
    w(c / "10-30-08-19_chan-a-video-045-y" / "visual" / "weird.bin", "zz")
    w(c / "10-30-08-19_chan-a-video-045-y" / "notes.txt", "n")
    # brand (không phân biệt hoa thường) + logo
    w(c / "BRAND" / "logo.png", "img")
    # cũ
    w(c / "10-11-14-28-video-1-buy-vs-rent" / "a.mp4")
    # không có số video
    w(c / "random-stuff" / "a.txt")
    runs = {"runs": [run_entry(p, "007", "published", "published")]}
    res = upload_scan.scan(root, runs, yt_wf_root=root / "nowhere")
    cs = res.channels[SLUG]
    kinds = {v.name: v.kind for v in cs.videos}
    assert kinds["11-01-13-21_chan-a-video-007-foo-bar__PUBLISHED"] == "pipeline"
    assert kinds["10-11-14-28-video-1-buy-vs-rent"] == "old"
    assert {j.name: j.file_count for j in cs.junk} == {
        "11-01-13-21_chan-a-video-007-foo-bar": 0,
        "10-30-08-18_chan-a-video-044-x": 1,
        "10-30-08-19_chan-a-video-045-y": 2,
    }
    assert [b.logo for b in cs.brand] == ["logo.png"]
    assert [o.name for o in cs.other] == ["random-stuff"]
    assert res.counts() == {"pipeline": 3, "old": 1, "junk": 3, "brand": 1, "other": 1, "total": 9}
    item = res.get((SLUG, 7))
    assert item.state == "published" and item.lifecycle == "published" and item.created
    assert item.published_at == "2026-11-02T08:40:00Z"


def test_folder_with_mp4_and_sibling_published_is_not_junk(tree):
    root, c = tree
    w(c / "10-01-00-00-video-3-a" / "x.mp4")
    (c / "10-01-00-00-video-3-a__PUBLISHED").mkdir()
    cs = upload_scan.scan(root, {}).channels[SLUG]
    names = {v.name for v in cs.videos}
    assert "10-01-00-00-video-3-a" in names and not cs.junk


def test_excluded_dir_skipped(tmp_path):
    root = tmp_path / "social"
    w(root / "__PROJECT-OLD" / "x" / "video-1-a" / "a.mp4")
    w(root / SLUG / "10-01-00-00-video-1-a" / "a.mp4")
    res = upload_scan.scan(root, {})
    assert list(res.channels) == [SLUG]


def test_key_numbers_023_and_23(tree):
    root, c = tree
    w(c / "11-02-22-07_chan-a-video-023-foo" / "a.mp4")
    d = make_pipeline(c, "11-02-22-08_chan-a-video-24-bar", "024")
    res = upload_scan.scan(root, {})
    assert [v.number for v in res.channels[SLUG].videos] == [23, 24]
    assert res.get((SLUG, 23)) is res.get((SLUG, 23))
    d2 = make_pipeline(c, "11-02-22-09_chan-a-video-xx", "23")  # pipeline "23" cùng khoá với "023"
    res = upload_scan.scan(root, {})
    assert {v.number for v in res.channels[SLUG].videos} == {23, 24}
    assert d2.exists() and d.exists()


def test_unreadable_number_goes_to_other(tree):
    root, c = tree
    w(c / "no-number-here" / "a.mp4")
    cs = upload_scan.scan(root, {}).channels[SLUG]
    assert not cs.videos and [o.kind for o in cs.other] == ["other"]


def test_slug_title(tree):
    root, c = tree
    w(c / "11-02-22-07_chan-a-video-023-if-had-one-month_DONE" / "a.mp4")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.slug_title == "if had one month"


# --- mp4 -------------------------------------------------------------------
def test_mp4_hard_links_merged(tree):
    root, c = tree
    d = c / "10-01-00-00-video-1-a"
    w(d / "a.mp4", b"1234567")
    try:
        os.link(d / "a.mp4", d / "b.mp4")
    except OSError:
        pytest.skip("hard link không khả dụng")
    w(d / "c.mp4", b"other")
    w(d / "sub" / "deep.mp4", b"deep")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert [(m.rel, m.size) for m in v.mp4s] == [("a.mp4", 7), ("c.mp4", 5)]


# --- thumbnail ---------------------------------------------------------------
def test_thumbnails_collage_excluded_and_duplicates_merged(tree):
    root, c = tree
    d = c / "10-01-00-00-video-1-a"
    w(d / "a.mp4")
    w(d / "thumbnails.jpg", b"collage")
    w(d / "thumbnail.jpg", b"same-bytes")
    w(d / "thumbnail_A.jpg", b"same-bytes")
    w(d / "thumbnail_B.png", b"different")
    w(d / "thumb" / "ignored.png", b"zzz")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert [t.rel for t in v.thumbnails] == ["thumbnail_A.jpg", "thumbnail_B.png"]
    assert all(t.source == "root" for t in v.thumbnails)


def test_thumbnails_same_size_different_content_kept(tree):
    root, c = tree
    d = c / "10-01-00-00-video-1-a"
    w(d / "a.jpg", b"aaaa")
    w(d / "b.jpg", b"bbbb")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert [t.rel for t in v.thumbnails] == ["a.jpg", "b.jpg"]


def test_thumbnails_fallback_dirs(tree):
    root, c = tree
    d1 = c / "10-01-00-00-video-1-a"
    w(d1 / "thumbnail" / "t1.png", b"1")
    w(d1 / "thumbnail" / "thumbnails.png", b"collage")
    w(d1 / "thumbnail_frames" / "f1.png", b"2")
    d2 = c / "10-01-00-00-video-2-b"
    w(d2 / "thumbnail_frames" / "f1.png", b"2")
    w(d2 / "thumbnail_frames" / "f2.png", b"3")
    d3 = c / "10-01-00-00-video-3-c"
    w(d3 / "thumbnails.jpg", b"only-collage")
    vs = {v.number: v for v in upload_scan.scan(root, {}).channels[SLUG].videos}
    assert [(t.rel, t.source) for t in vs[1].thumbnails] == [("thumbnail/t1.png", "thumbnail")]
    assert [(t.rel, t.source) for t in vs[2].thumbnails] == [
        ("thumbnail_frames/f1.png", "thumbnail_frames"),
        ("thumbnail_frames/f2.png", "thumbnail_frames"),
    ]
    assert vs[3].thumbnails == []


# --- tiêu đề video cũ --------------------------------------------------------
def test_youtube_md_all_options(tree):
    root, c = tree
    d = c / "10-01-00-00-video-6-a"
    w(
        d / "YOUTUBE.md",
        "# YouTube kit — E se il tuo superpotere?\n\n## Titolo (3 opzioni)\n\n"
        "1. **E se il tuo superpotere lo avessero 8 miliardi?**\n2. Opzione due\n3) Opzione tre ← consigliata\n\n"
        "## Descrizione\n\n1. Non è un titolo\n",
    )
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.titles == [
        "E se il tuo superpotere lo avessero 8 miliardi?",
        "Opzione due",
        "Opzione tre",
    ]
    assert v.meta_file == "YOUTUBE.md"


def test_youtube_md_in_subfolder_and_title_heading(tree):
    root, c = tree
    d = c / "10-01-00-00-video-5-a"
    w(d / "sub" / "YOUTUBE.md", "# YouTube kit (Italia)\n\n## Title\n\n1. **One**\n2. Two\n")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.titles == ["One", "Two"] and v.meta_file == "sub/YOUTUBE.md"


def test_mo_ta_md(tree):
    root, c = tree
    d = c / "10-01-00-00-video-5-a"
    w(d / "MO-TA.md", "# Hiền lành · mô tả để đăng\n\n**Tiêu đề:** Bạn hiền lành sao? · Kinh Cưa (MN 21)\n\nbody\n")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert "Bạn hiền lành sao? · Kinh Cưa (MN 21)" in v.titles
    assert "Bạn hiền lành sao?" in v.titles and "Hiền lành" in v.titles
    assert v.meta_file == "MO-TA.md"


def test_titles_md_table(tree):
    root, c = tree
    d = c / "10-01-00-00-video-2-a"
    w(
        d / "titles.md",
        "# Titles\n\n| # | Title (≤ 60 chars) | Thumbnail |\n| --- | --- | --- |\n"
        "| **A** ★ | Why Babies Disappear | `t.png` |\n| B | What If? | x |\n",
    )
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.titles == ["Why Babies Disappear", "What If?"] and v.meta_file == "titles.md"


def test_broken_or_missing_meta_gives_empty_titles(tree):
    root, c = tree
    w(c / "10-01-00-00-video-1-a" / "YOUTUBE.md", b"\xff\xfe\x00garbage")
    w(c / "10-01-00-00-video-2-b" / "a.mp4")
    vs = upload_scan.scan(root, {}).channels[SLUG].videos
    assert vs[0].titles == [] and vs[1].titles == [] and vs[1].meta_file is None


# --- nội dung thẻ pipeline ----------------------------------------------------
def test_description_bytes_preserved_and_youtube_link_flag(tree):
    root, c = tree
    desc = "  dòng đầu  \n\n  ‹ 日本語 ›  \nxem https://www.youtube.com/watch?v=abc123  \n  "
    d = make_pipeline(c, "11-01-00-00_chan-a-video-001-x", "001", desc=desc)
    # ghi lại bằng bytes để chắc chắn không bị đổi \r\n
    (d / "artifacts" / "publish.json").write_text(
        json.dumps({"title": {"primary": "T", "en": "TE"}, "description": {"primary": desc}, "tags": [], "chapters": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.description == desc
    assert v.description_has_youtube_link is True
    assert v.title == "T" and v.title_en == "TE" and v.titles == ["T"]


def test_description_without_link(tree):
    root, c = tree
    make_pipeline(c, "11-01-00-00_chan-a-video-001-x", "001", desc="không có link https://example.com")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.description_has_youtube_link is False


def test_pipeline_fields_from_project_json(tree):
    root, c = tree
    d = make_pipeline(c, "11-01-00-00_chan-a-video-009-x", "009")
    pj = json.loads((d / "project.json").read_text(encoding="utf-8"))
    pj.update(youtube_url="https://youtu.be/abc", youtube_video_id="abc")
    (d / "project.json").write_text(json.dumps(pj), encoding="utf-8")
    v = upload_scan.scan(root, {"runs": [run_entry(d, "009", "complete", "active")]}).channels[SLUG].videos[0]
    assert (v.youtube_url, v.youtube_video_id, v.state, v.lifecycle) == ("https://youtu.be/abc", "abc", "complete", "active")
    assert v.tags == ["a", "b"] and v.disclaimer == {"primary": "disc", "en": "disc-en"}


def test_pipeline_without_publish_json(tree):
    root, c = tree
    w(c / "11-01-00-00_chan-a-video-002-x" / "project.json", json.dumps({"video_id": "002"}))
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.title is None and v.description is None and v.titles == []


# --- kênh -----------------------------------------------------------------------
def test_channel_info(tmp_path):
    wf = tmp_path / "wf"
    w(wf / ".claude" / "skills" / f"producer-{SLUG}" / "config.json", json.dumps({"channel_name": "Chan A", "language": "ja"}))
    assert upload_scan.channel_info(SLUG, wf) == upload_scan.ChannelInfo(SLUG, "Chan A", "ja")
    assert upload_scan.channel_info("nope", wf) == upload_scan.ChannelInfo("nope", "", "")


def test_scan_missing_root(tmp_path):
    assert upload_scan.scan(tmp_path / "missing", {}).counts()["total"] == 0


# --- pipectl._run -----------------------------------------------------------------
def test_run_decodes_utf8_and_keeps_returncode(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write('日本語のテスト\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write(b'noise\\nERROR: catalog \\xe2\\x80\\x94 bad\\xff\\n')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    res = pipectl._run([sys.executable, script], timeout=30)
    assert res.returncode == 2
    assert res.stdout.strip() == "日本語のテスト"
    assert pipectl.error_line(res.stderr) == "ERROR: catalog — bad�"


def test_error_line_picks_last_error_and_falls_back():
    assert pipectl.error_line("log\nERROR: first\nmore log\nERROR: last\ntrailing") == "ERROR: last"
    assert pipectl.error_line("only\nlines") == "lines"
    assert pipectl.error_line("") == ""


def test_run_timeout_raises(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    with pytest.raises(pipectl.PipectlError):
        pipectl._run([sys.executable, script], timeout=0.5)


def test_runs_parses_json(tmp_path, monkeypatch):
    script = tmp_path / "pipectl.py"
    script.write_text(
        "import sys, json\nassert sys.argv[1:] == ['runs', '--root', sys.argv[3], '--json']\n"
        "print(json.dumps({'root': sys.argv[3], 'runs': []}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "YT_WF_PYTHON", Path(sys.executable))
    monkeypatch.setattr(config, "PIPECTL_PATH", script)
    assert pipectl.runs(tmp_path) == {"root": str(tmp_path), "runs": []}


def test_runs_failure_uses_error_line(tmp_path, monkeypatch):
    script = tmp_path / "pipectl.py"
    script.write_text("import sys\nsys.stderr.write('huge log\\nERROR: catalog timeout\\n')\nsys.exit(2)\n", encoding="utf-8")
    monkeypatch.setattr(config, "YT_WF_PYTHON", Path(sys.executable))
    monkeypatch.setattr(config, "PIPECTL_PATH", script)
    with pytest.raises(pipectl.PipectlError, match="ERROR: catalog timeout"):
        pipectl.runs(tmp_path)


# --- nghiệm thu lần 1 -----------------------------------------------------------
def test_description_en_kept(tree):
    root, c = tree
    make_pipeline(c, "11-01-00-00_chan-a-video-001-x", "001")
    pj = c / "11-01-00-00_chan-a-video-001-x" / "artifacts" / "publish.json"
    d = json.loads(pj.read_text(encoding="utf-8"))
    d["description"]["en"] = "  English desc  \n"
    pj.write_text(json.dumps(d), encoding="utf-8")
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.description_en == "  English desc  \n"


def test_no_tree_walk_without_published_sibling(tree, monkeypatch):
    root, c = tree
    w(c / "10-01-00-00-video-1-a" / "a.mp4")
    (c / "10-02-00-00-video-2-b__PUBLISHED").mkdir()
    walked = []
    real = upload_scan._walk_files
    monkeypatch.setattr(upload_scan, "_walk_files", lambda f: (walked.append(f.name), real(f))[1])
    upload_scan.scan(root, {})
    assert walked == []  # video-2-b__PUBLISHED có tên __PUBLISHED nhưng không có anh em "video-2-b"
    (c / "10-02-00-00-video-2-b").mkdir()
    upload_scan.scan(root, {})
    assert walked == ["10-02-00-00-video-2-b"]


def test_youtube_md_strips_length_suffix_keeps_full_story(tree):
    root, c = tree
    w(
        c / "10-01-00-00-video-7-a" / "YOUTUBE.md",
        "# YouTube — E se potessi vedere 10 secondi nel futuro? (video 7)\n\n## Titolo\n\n"
        "1. **E se potessi vedere 10 secondi nel futuro? (42)**\n2. Altro (video 15)\n3. What If (Full Story)\n",
    )
    v = upload_scan.scan(root, {}).channels[SLUG].videos[0]
    assert v.titles == ["E se potessi vedere 10 secondi nel futuro?", "Altro", "What If (Full Story)"]


def test_youtube_md_heading_only_when_no_list(tree):
    root, c = tree
    w(c / "10-01-00-00-video-5-a" / "YOUTUBE.md", "# YouTube — full story (Parts 1–3 in one video, 6:18)\n\n## Title\n\n1. One\n")
    w(c / "10-01-00-00-video-6-b" / "YOUTUBE.md", "# YouTube kit — Solo heading (video 15)\n\nno list\n")
    vs = {v.number: v for v in upload_scan.scan(root, {}).channels[SLUG].videos}
    assert vs[5].titles == ["One"]
    assert vs[6].titles == ["Solo heading"]
