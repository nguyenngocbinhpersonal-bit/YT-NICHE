"""Trạng thái video: thứ tự ưu tiên và các ca biên."""

from types import SimpleNamespace

import pytest

from yt_niche.core import upload_state as us


def pl(**kw):
    base = dict(kind="pipeline", run_state="complete", lifecycle="active")
    base.update(kw)
    return us.video_status(**base)


@pytest.mark.parametrize("state", [None, "in_flight", "awaiting_human", "uninitialized", "canceled"])
def test_unfinished_run_is_in_production(state):
    assert pl(run_state=state) == us.DANG_SAN_XUAT


def test_canceled_lifecycle_is_in_production():
    assert pl(lifecycle="canceled") == us.DANG_SAN_XUAT


def test_in_production_beats_everything():
    assert pl(run_state="in_flight", catalog_pending=True, has_proposal=True, has_link=True) == us.DANG_SAN_XUAT


def test_catalog_pending_beats_done():
    assert pl(run_state="published", lifecycle="published", has_link=True, catalog_pending=True) == us.GHI_THIEU


def test_done_needs_published_and_link():
    assert pl(run_state="published", lifecycle="published", has_link=True) == us.XONG
    assert pl(run_state="published", lifecycle="published", has_link=False) == us.DA_DANG_CHUA_LINK


def test_published_without_link_with_proposal_needs_confirm():
    assert pl(run_state="published", lifecycle="published", has_proposal=True) == us.CAN_XAC_NHAN


def test_proposal_beats_manual_wait():
    assert pl(has_proposal=True, manual_state="waiting_public") == us.CAN_XAC_NHAN
    assert pl(manual_state="waiting_public") == us.CHO_PUBLIC


def test_missing_stuff():
    assert pl(has_mp4=False) == us.THIEU_DO
    assert pl(has_title=False) == us.THIEU_DO
    assert pl(has_description=False) == us.THIEU_DO
    assert pl(has_thumb_candidates=False) == us.THIEU_DO
    assert pl() == us.SAN_SANG


def test_missing_stuff_does_not_block_confirmation():
    assert pl(has_mp4=False, has_proposal=True) == us.CAN_XAC_NHAN
    assert us.missing_items(has_mp4=False, has_title=True, has_description=True, has_thumb_candidates=False) == ["mp4", "ảnh thumbnail"]


def test_old_video_never_missing_stuff():
    assert us.video_status(kind="old", has_mp4=False, has_title=False) == us.CHUA_DOI_CHIEU
    assert us.video_status(kind="old", has_proposal=True) == us.CAN_XAC_NHAN
    assert us.video_status(kind="old", has_link=True, has_proposal=True) == us.XONG
    assert us.video_status(kind="old", manual_state="waiting_public") == us.CHO_PUBLIC


def item(**kw):
    base = dict(kind="pipeline", state="complete", lifecycle="active", youtube_url=None, mp4s=["a.mp4"],
                title="t", description="d", thumbnails=["x.jpg"])
    base.update(kw)
    return SimpleNamespace(**base)


def test_status_for_uses_db_link_and_pending():
    assert us.status_for(item(), None, False) == us.SAN_SANG
    assert us.status_for(item(state="published", lifecycle="published"), {"video_url": "u"}, False) == us.XONG
    assert us.status_for(item(state="published", lifecycle="published", youtube_url="u"), None, False) == us.XONG
    assert us.status_for(item(), {"catalog_pending": 1}, False) == us.GHI_THIEU
    assert us.status_for(item(kind="old", state=None, lifecycle=None), {"video_url": "u"}, False) == us.XONG
    assert us.status_for(item(mp4s=[]), None, False) == us.THIEU_DO


def test_proposals_for_hides_weak_when_strong_exists_and_caps_weak():
    from yt_niche.core.upload_match import Proposal

    def p(n, vid, st, sc):
        return Proposal("c", n, vid, st, sc, "title")

    props = [p(1, "a", "weak", 0.8), p(1, "b", "strong", 1.0), p(2, "c", "weak", 0.76), p(2, "d", "weak", 0.9),
             p(2, "e", "weak", 0.8), p(2, "f", "weak", 0.78)]
    assert [x.video_id for x in us.proposals_for(props, ("c", 1))] == ["b"]
    assert [x.video_id for x in us.proposals_for(props, ("c", 2))] == ["d", "e", "f"]
    assert us.proposals_for(props, ("c", 3)) == []
