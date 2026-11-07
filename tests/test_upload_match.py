"""Khớp tiêu đề video local với video trên kênh (hàm thuần)."""

import random

from yt_niche.core.upload_match import (
    STRONG,
    WEAK,
    MatchItem,
    RemoteVideo,
    cross_channel,
    norm,
    propose,
)


def item(slug, n, titles=(), slug_title="", kind="pipeline"):
    return MatchItem(slug, n, kind, tuple(titles), slug_title)


def remote(vid, title, cid="UCx"):
    return RemoteVideo(vid, cid, title, "2026-11-01T00:00:00Z")


def test_norm_dash_case_punct_and_width():
    assert norm("What If — The Moon  Vanished?") == norm("what if - the moon vanished")
    assert norm("ＡＢＣ　１２３") == "abc 123"
    assert norm("") == ""


def test_norm_keeps_cjk_tokens():
    assert norm("もしも月が消えたら！") == "もしも月が消えたら"


def test_strong_on_exact_normalised_title():
    r = propose([item("a", 7, ["The Moon — Gone"])], [remote("v1", "the moon - gone!")])
    assert [(p.number, p.video_id, p.strength) for p in r.proposals] == [(7, "v1", STRONG)]
    assert r.unmatched_remotes == []


def test_strong_on_any_of_several_title_options():
    it = item("a", 7, ["Option one title", "Option two title", "Option three title"], kind="old")
    r = propose([it], [remote("v1", "OPTION TWO TITLE")])
    assert r.proposals[0].strength == STRONG


def test_weak_on_similar_title():
    r = propose([item("a", 1, ["The Last Lighthouse Keeper"])], [remote("v1", "The Last Lighthouse Keeper (Full Story)")])
    assert r.proposals[0].strength == WEAK and r.proposals[0].reason == "similar"


def test_slug_is_always_weak_and_accent_insensitive():
    it = item("a", 3, [], slug_title="nhu vay toi nghe")
    r = propose([it], [remote("v1", "Như vậy tôi nghe")])
    p = r.proposals[0]
    assert p.strength == WEAK and p.reason == "slug"
    # slug trùng khít vẫn không bao giờ mạnh
    r2 = propose([item("a", 3, [], slug_title="the moon gone")], [remote("v1", "The Moon Gone")])
    assert r2.proposals[0].strength == WEAK


def test_unrelated_gives_nothing_and_remote_is_unmatched():
    r = propose([item("a", 1, ["Alpha story"])], [remote("v1", "Completely different zebra")])
    assert r.proposals == []
    assert [x.video_id for x in r.unmatched_remotes] == ["v1"]


def test_remote_assigned_to_only_one_item_and_ambiguous_strong_demoted():
    items = [item("a", 1, ["Same Title"]), item("a", 2, ["same title"])]
    r = propose(items, [remote("v1", "Same Title")])
    assert len(r.proposals) == 1
    assert r.proposals[0].strength == WEAK and r.proposals[0].reason == "ambiguous"


def test_item_matching_two_remotes_strongly_is_demoted():
    r = propose([item("a", 1, ["Dup Title"])], [remote("v1", "Dup Title"), remote("v2", "Dup Title")])
    assert all(p.strength == WEAK for p in r.proposals)


def test_rejected_pair_not_proposed_again():
    items = [item("a", 1, ["Hello World"])]
    rem = [remote("v1", "Hello World")]
    assert propose(items, rem, {("a", 1, "v1")}).proposals == []
    assert propose(items, rem, {("a", 2, "v1")}).proposals[0].video_id == "v1"
    assert [x.video_id for x in propose(items, rem, {("a", 1, "v1")}).unmatched_remotes] == ["v1"]


def test_thirteen_local_titles_vs_208_competitor_titles_give_zero_strong():
    rng = random.Random(7)
    words = "moon ocean desert empire fire ice storm tower garden river cave signal library island bridge forest".split()

    def title():
        return "What if the " + " ".join(rng.sample(words, 3)) + f" part {rng.randint(1, 999)}"

    local = {title() for _ in range(40)}
    local = sorted(local)[:13]
    competitors = []
    while len(competitors) < 208:
        t = title()
        if t not in local:
            competitors.append(t)
    items = [item("mine", i + 1, [t]) for i, t in enumerate(local)]
    r = propose(items, [remote(f"c{i:010d}", t, "UCrival") for i, t in enumerate(competitors)])
    assert not [p for p in r.proposals if p.strength == STRONG]


def test_cross_channel_warns_when_title_of_a_is_on_b():
    items = {"a": [item("a", 5, ["Secret Title"])], "b": [item("b", 1, ["Other"])]}
    remotes = {"a": [remote("va", "Nothing here")], "b": [remote("vb", "secret — title")]}
    w = cross_channel(items, remotes)
    assert [(x.channel_slug, x.number, x.other_slug, x.video_id) for x in w] == [("a", 5, "b", "vb")]


def test_cross_channel_silent_when_own_channel_only():
    items = {"a": [item("a", 5, ["Secret Title"])]}
    remotes = {"a": [remote("va", "Secret Title")], "b": [remote("vb", "Unrelated")]}
    assert cross_channel(items, remotes) == []
