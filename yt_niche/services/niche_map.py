"""Niche map: a niche tree Claude builds through MCP, with numbers taken from the existing tables (keyword, radar_keyword,
dig_run, channel).

Division of labour (docs/NICHE_MAP_MCP_PLAN.md): the app fetches real numbers and stores them; Claude reads the numbers,
then names, assigns motivations, merges and ranks potential. Every decision is written to niche_event with a rationale.
Merging is soft (status = merged, merged_into) and can be undone.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from yt_niche import config as cfg

MOTIVATIONS = {
    "learn": "Learn — what X is, how it works",
    "earn": "Earn — how to make money from X",
    "fix": "Fix a problem — X is broken, how to fix it",
    "improve": "Improve — do X better",
    "believe": "Believe & meaning — what this means to me",
    "connect": "Connect — how people like me live",
    "escape": "Escape — let me forget for a while",
    "experience": "Experience — what X feels like",
    "updated": "Stay updated — what's new about X",
    "inspired": "Feel inspired — give me motivation",
}
KINDS = ("main", "related", "longtail", "trend")
STATUSES = ("draft", "active", "merged", "dropped")
ROLES = ("leader", "rising", "fallen")
ACTORS = ("claude", "user", "app")

# A rank counts as stale when the numbers drift more than this from the snapshot taken at ranking time.
STALE_SCORE_DELTA = 10.0
STALE_DEMAND_DELTA = 10.0


class NicheError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _kw(s: str) -> str:
    return " ".join((s or "").lower().split())


def _event(conn: sqlite3.Connection, niche_id: int | None, kind: str, payload: dict, rationale: str, actor: str) -> int:
    if actor not in ACTORS:
        raise NicheError(f"actor must be one of {ACTORS}")
    return conn.execute(
        "INSERT INTO niche_event (niche_id, kind, payload_json, rationale, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (niche_id, kind, json.dumps(payload, ensure_ascii=False), _norm(rationale), actor, _now()),
    ).lastrowid


def _require_rationale(rationale: str) -> str:
    r = _norm(rationale)
    if len(r) < 10:
        raise NicheError("Every decision needs a rationale of at least 10 characters")
    return r


def _check_motivation(m: str | None) -> str | None:
    if m is None or m == "":
        return None
    if m not in MOTIVATIONS:
        raise NicheError(f"Motivation '{m}' is not valid; pick one of {list(MOTIVATIONS)}")
    return m


def language_of_region(region: str) -> str:
    """Language code of a market (vi, en, ja…); an unknown market raises."""
    region = (region or "").upper()
    if region not in cfg.MARKETS:
        raise NicheError(f"Market '{region}' is not in the list {list(cfg.MARKETS)}")
    return cfg.MARKETS[region][0]


def regions_of_language(language: str) -> list[str]:
    """Enabled markets that use this language (en → US, GB, CA, AU, PH…)."""
    return [r for r in cfg.ENABLED_MARKETS if cfg.MARKETS[r][0] == language]


def _check_language(language: str) -> str:
    language = (language or "").strip()
    known = {lang for lang, _ in cfg.MARKETS.values()}
    if language not in known:
        raise NicheError(f"Language '{language}' has no market; pick one of {sorted(known)}")
    return language


def _same_language(niche: dict, language: str, what: str) -> None:
    if niche["language"] != language:
        raise NicheError(
            f"{what} is in language '{language}' but niche '{niche['name']}' is '{niche['language']}'. "
            "A niche belongs to one language; create another niche for this language."
        )


def _row(conn: sqlite3.Connection, niche_id: int) -> dict:
    row = conn.execute("SELECT * FROM niche WHERE id = ?", (niche_id,)).fetchone()
    if row is None:
        raise NicheError(f"No niche with id={niche_id}")
    d = dict(row)
    d["basis"] = json.loads(d.pop("basis_json") or "null")
    return d


def _touch(conn: sqlite3.Connection, niche_id: int) -> None:
    conn.execute("UPDATE niche SET updated_at = ? WHERE id = ?", (_now(), niche_id))


# ---------------------------------------------------------------------------
# Create / edit / tree
# ---------------------------------------------------------------------------


def create(
    conn: sqlite3.Connection,
    name: str,
    *,
    language: str,
    parent_id: int | None = None,
    motivation: str | None = None,
    motivation_secondary: str | None = None,
    summary: str = "",
    rationale: str = "",
    actor: str = "claude",
) -> int:
    name = _norm(name)
    if not name:
        raise NicheError("Niche name is empty")
    language = _check_language(language)
    if parent_id is not None:
        _same_language(_row(conn, parent_id), language, "The child niche")
    m1, m2 = _check_motivation(motivation), _check_motivation(motivation_secondary)
    if m1 and m2 and m1 == m2:
        raise NicheError("The secondary motivation must differ from the primary one")
    now = _now()
    niche_id = conn.execute(
        """INSERT INTO niche (name, language, parent_id, motivation, motivation_secondary, summary, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, language, parent_id, m1, m2, _norm(summary), now, now),
    ).lastrowid
    _event(conn, niche_id, "create", {"name": name, "language": language, "parent_id": parent_id, "motivation": m1},
           rationale, actor)
    return niche_id


def update(
    conn: sqlite3.Connection,
    niche_id: int,
    *,
    name: str | None = None,
    summary: str | None = None,
    rationale: str = "",
    actor: str = "claude",
) -> dict:
    cur = _row(conn, niche_id)
    changes = {}
    if name is not None and _norm(name) and _norm(name) != cur["name"]:
        changes["name"] = [cur["name"], _norm(name)]
        conn.execute("UPDATE niche SET name = ? WHERE id = ?", (_norm(name), niche_id))
    if summary is not None and _norm(summary) != cur["summary"]:
        changes["summary"] = [cur["summary"], _norm(summary)]
        conn.execute("UPDATE niche SET summary = ? WHERE id = ?", (_norm(summary), niche_id))
    if changes:
        _touch(conn, niche_id)
        _event(conn, niche_id, "rename", changes, rationale, actor)
    return get(conn, niche_id)


def _ancestors(conn: sqlite3.Connection, niche_id: int) -> list[int]:
    out, cur, seen = [], niche_id, set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        row = conn.execute("SELECT parent_id FROM niche WHERE id = ?", (cur,)).fetchone()
        if row is None:
            break
        cur = row["parent_id"]
        if cur is not None:
            out.append(cur)
    return out


def move(conn: sqlite3.Connection, niche_id: int, new_parent_id: int | None, rationale: str, actor: str = "claude") -> dict:
    cur = _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    if new_parent_id is not None:
        _same_language(_row(conn, new_parent_id), cur["language"], "The niche being moved")
        if new_parent_id == niche_id or niche_id in _ancestors(conn, new_parent_id):
            raise NicheError("Cannot move a niche under itself or under one of its descendants")
    conn.execute("UPDATE niche SET parent_id = ? WHERE id = ?", (new_parent_id, niche_id))
    _touch(conn, niche_id)
    _event(conn, niche_id, "move", {"from": cur["parent_id"], "to": new_parent_id}, rationale, actor)
    return get(conn, niche_id)


def set_motivation(
    conn: sqlite3.Connection,
    niche_id: int,
    primary: str,
    secondary: str | None,
    rationale: str,
    actor: str = "claude",
) -> dict:
    cur = _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    m1, m2 = _check_motivation(primary), _check_motivation(secondary)
    if m1 is None:
        raise NicheError("A primary motivation is required")
    if m2 == m1:
        raise NicheError("The secondary motivation must differ from the primary one")
    conn.execute("UPDATE niche SET motivation = ?, motivation_secondary = ? WHERE id = ?", (m1, m2, niche_id))
    _touch(conn, niche_id)
    _event(
        conn, niche_id, "motivation",
        {"from": [cur["motivation"], cur["motivation_secondary"]], "to": [m1, m2]}, rationale, actor,
    )
    return get(conn, niche_id)


def drop(conn: sqlite3.Connection, niche_id: int, rationale: str, actor: str = "claude") -> dict:
    _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    conn.execute("UPDATE niche SET status = 'dropped' WHERE id = ?", (niche_id,))
    _touch(conn, niche_id)
    _event(conn, niche_id, "drop", {}, rationale, actor)
    return get(conn, niche_id)


def _activate_if_ready(conn: sqlite3.Connection, niche_id: int) -> None:
    """draft → active once there is at least one real number (a scored main keyword, or a dig)."""
    row = conn.execute("SELECT status FROM niche WHERE id = ?", (niche_id,)).fetchone()
    if row is None or row["status"] != "draft":
        return
    has_main = conn.execute(
        "SELECT 1 FROM niche_keyword WHERE niche_id = ? AND kind = 'main' AND score IS NOT NULL LIMIT 1", (niche_id,)
    ).fetchone()
    has_run = conn.execute("SELECT 1 FROM niche_run WHERE niche_id = ? LIMIT 1", (niche_id,)).fetchone()
    if has_main or has_run:
        conn.execute("UPDATE niche SET status = 'active' WHERE id = ?", (niche_id,))


# ---------------------------------------------------------------------------
# Keywords, digs, channels
# ---------------------------------------------------------------------------


def lookup_keyword(conn: sqlite3.Connection, region: str, keyword: str) -> dict:
    """Latest real numbers for a keyword: the keyword table (scored with real search) first, then radar_keyword."""
    kw = _kw(keyword)
    row = conn.execute(
        """SELECT score, demand, competition, n, scored_at FROM keyword
           WHERE region = ? AND keyword = ? AND score IS NOT NULL ORDER BY scored_at DESC LIMIT 1""",
        (region, kw),
    ).fetchone()
    out = {"score": None, "demand": None, "competition": None, "n": None, "scored_at": None, "trend_class": None}
    if row:
        out.update(dict(row))
    radar = conn.execute(
        "SELECT yt_score, potential, trend_class, yt_scored_at FROM radar_keyword WHERE region = ? AND keyword = ?",
        (region, kw),
    ).fetchone()
    if radar:
        out["trend_class"] = radar["trend_class"]
        if out["score"] is None and radar["yt_score"] is not None:
            out["score"], out["scored_at"] = radar["yt_score"], radar["yt_scored_at"]
    return out


def attach_keywords(
    conn: sqlite3.Connection,
    niche_id: int,
    items: list[dict],
    rationale: str,
    actor: str = "claude",
) -> dict:
    """items: [{keyword, region, kind, source?}]. Numbers are copied from keyword / radar_keyword at attach time (no API call).

    The keyword's market must share the niche's language (one niche = one language).
    """
    niche = _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    added = []
    for it in items:
        kind = it.get("kind", "related")
        if kind not in KINDS:
            raise NicheError(f"kind '{kind}' is not valid; pick one of {KINDS}")
        kw, region = _kw(it.get("keyword", "")), (it.get("region") or "").upper()
        if not kw or not region:
            raise NicheError("Each keyword needs keyword and region")
        _same_language(niche, language_of_region(region), f"Keyword '{kw}' ({region})")
        nums = lookup_keyword(conn, region, kw)
        conn.execute(
            """INSERT INTO niche_keyword (niche_id, region, keyword, kind, source, demand, competition, score, n,
                   scored_at, trend_class, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(niche_id, region, keyword) DO UPDATE SET kind = excluded.kind, source = excluded.source,
                   demand = excluded.demand, competition = excluded.competition, score = excluded.score,
                   n = excluded.n, scored_at = excluded.scored_at, trend_class = excluded.trend_class""",
            (niche_id, region, kw, kind, it.get("source") or "claude", nums["demand"], nums["competition"],
             nums["score"], nums["n"], nums["scored_at"], nums["trend_class"], _now()),
        )
        added.append({"keyword": kw, "region": region, "kind": kind, "scored": nums["score"] is not None})
    _activate_if_ready(conn, niche_id)
    _touch(conn, niche_id)
    _event(conn, niche_id, "keywords", {"added": added}, rationale, actor)
    return get(conn, niche_id)


def detach_keywords(conn: sqlite3.Connection, niche_id: int, items: list[dict], rationale: str, actor: str = "claude") -> dict:
    _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    removed = []
    for it in items:
        n = conn.execute(
            "DELETE FROM niche_keyword WHERE niche_id = ? AND region = ? AND keyword = ?",
            (niche_id, (it.get("region") or "").upper(), _kw(it.get("keyword", ""))),
        ).rowcount
        if n:
            removed.append({"keyword": _kw(it["keyword"]), "region": it["region"].upper()})
    _event(conn, niche_id, "keywords", {"removed": removed}, rationale, actor)
    return get(conn, niche_id)


def refresh_keyword_numbers(conn: sqlite3.Connection, niche_id: int) -> int:
    """Copy the latest numbers from keyword / radar_keyword for every attached keyword (after re-scoring). Returns rows changed."""
    rows = conn.execute("SELECT region, keyword FROM niche_keyword WHERE niche_id = ?", (niche_id,)).fetchall()
    changed = 0
    for r in rows:
        nums = lookup_keyword(conn, r["region"], r["keyword"])
        changed += conn.execute(
            """UPDATE niche_keyword SET demand = ?, competition = ?, score = ?, n = ?, scored_at = ?, trend_class = ?
               WHERE niche_id = ? AND region = ? AND keyword = ?
                 AND (IFNULL(scored_at, '') != IFNULL(?, '') OR IFNULL(trend_class, '') != IFNULL(?, ''))""",
            (nums["demand"], nums["competition"], nums["score"], nums["n"], nums["scored_at"], nums["trend_class"],
             niche_id, r["region"], r["keyword"], nums["scored_at"], nums["trend_class"]),
        ).rowcount
    if changed:
        _touch(conn, niche_id)
    return changed


def attach_run(conn: sqlite3.Connection, niche_id: int, run_id: int, rationale: str = "", actor: str = "claude") -> dict:
    niche = _row(conn, niche_id)
    run = conn.execute("SELECT topic, language FROM dig_run WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise NicheError(f"No dig with id={run_id}")
    _same_language(niche, run["language"], f"Dig '{run['topic']}'")
    conn.execute(
        "INSERT OR IGNORE INTO niche_run (niche_id, run_id, added_at) VALUES (?, ?, ?)", (niche_id, run_id, _now())
    )
    _activate_if_ready(conn, niche_id)
    _touch(conn, niche_id)
    _event(conn, niche_id, "run", {"run_id": run_id}, rationale, actor)
    return get(conn, niche_id)


def set_channels(conn: sqlite3.Connection, niche_id: int, items: list[dict], rationale: str, actor: str = "claude") -> dict:
    """items: [{channel_id, role, note?}]. Channels missing from the channel table can still be recorded (the app fills them in on scan)."""
    _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    for it in items:
        role = it.get("role", "leader")
        if role not in ROLES:
            raise NicheError(f"role '{role}' is not valid; pick one of {ROLES}")
        if not it.get("channel_id"):
            raise NicheError("Each channel needs a channel_id")
        conn.execute(
            """INSERT INTO niche_channel (niche_id, channel_id, role, note, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(niche_id, channel_id) DO UPDATE SET role = excluded.role, note = excluded.note,
                   updated_at = excluded.updated_at""",
            (niche_id, it["channel_id"], role, _norm(it.get("note", "")), _now()),
        )
    _touch(conn, niche_id)
    _event(conn, niche_id, "channels", {"set": [{"channel_id": i["channel_id"], "role": i.get("role", "leader")} for i in items]},
           rationale, actor)
    return get(conn, niche_id)


# ---------------------------------------------------------------------------
# Soft merge / unmerge
# ---------------------------------------------------------------------------


def merge(conn: sqlite3.Connection, source_id: int, target_id: int, rationale: str, actor: str = "claude") -> dict:
    """Move the source's keywords, digs, channels and children to the target; the source becomes merged (not deleted)."""
    src, dst = _row(conn, source_id), _row(conn, target_id)
    rationale = _require_rationale(rationale)
    if source_id == target_id:
        raise NicheError("Cannot merge a niche into itself")
    if src["status"] == "merged":
        raise NicheError("This niche is already merged")
    if dst["status"] in ("merged", "dropped"):
        raise NicheError("The target niche must be active")
    if source_id in _ancestors(conn, target_id):
        raise NicheError("Cannot merge a parent niche into one of its descendants")
    _same_language(dst, src["language"], f"Niche '{src['name']}'")

    moved = {"keywords": [], "runs": [], "channels": [], "children": [], "parent_id": src["parent_id"]}
    for r in conn.execute("SELECT region, keyword, kind FROM niche_keyword WHERE niche_id = ?", (source_id,)):
        exists = conn.execute(
            "SELECT 1 FROM niche_keyword WHERE niche_id = ? AND region = ? AND keyword = ?",
            (target_id, r["region"], r["keyword"]),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM niche_keyword WHERE niche_id = ? AND region = ? AND keyword = ?",
                (source_id, r["region"], r["keyword"]),
            )
            moved["keywords"].append({"region": r["region"], "keyword": r["keyword"], "kind": r["kind"], "dup": True})
        else:
            conn.execute(
                "UPDATE niche_keyword SET niche_id = ? WHERE niche_id = ? AND region = ? AND keyword = ?",
                (target_id, source_id, r["region"], r["keyword"]),
            )
            moved["keywords"].append({"region": r["region"], "keyword": r["keyword"], "kind": r["kind"]})
    for r in conn.execute("SELECT run_id FROM niche_run WHERE niche_id = ?", (source_id,)):
        conn.execute("INSERT OR IGNORE INTO niche_run (niche_id, run_id, added_at) VALUES (?, ?, ?)",
                     (target_id, r["run_id"], _now()))
        moved["runs"].append(r["run_id"])
    conn.execute("DELETE FROM niche_run WHERE niche_id = ?", (source_id,))
    for r in conn.execute("SELECT channel_id, role, note FROM niche_channel WHERE niche_id = ?", (source_id,)):
        conn.execute(
            """INSERT INTO niche_channel (niche_id, channel_id, role, note, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(niche_id, channel_id) DO NOTHING""",
            (target_id, r["channel_id"], r["role"], r["note"], _now()),
        )
        moved["channels"].append(r["channel_id"])
    conn.execute("DELETE FROM niche_channel WHERE niche_id = ?", (source_id,))
    for r in conn.execute("SELECT id FROM niche WHERE parent_id = ?", (source_id,)):
        conn.execute("UPDATE niche SET parent_id = ? WHERE id = ?", (target_id, r["id"]))
        moved["children"].append(r["id"])

    conn.execute("UPDATE niche SET status = 'merged', merged_into = ? WHERE id = ?", (target_id, source_id))
    _activate_if_ready(conn, target_id)
    _touch(conn, source_id)
    _touch(conn, target_id)
    _event(conn, source_id, "merge", {"into": target_id, "moved": moved}, rationale, actor)
    _event(conn, target_id, "merge", {"from": source_id, "n_keywords": len(moved["keywords"]), "n_runs": len(moved["runs"])},
           rationale, actor)
    return get(conn, target_id)


def unmerge(conn: sqlite3.Connection, niche_id: int, rationale: str, actor: str = "user") -> dict:
    """Undo a merge: read what was moved from the latest merge event and move it back."""
    src = _row(conn, niche_id)
    rationale = _require_rationale(rationale)
    if src["status"] != "merged" or src["merged_into"] is None:
        raise NicheError("This niche is not merged into anything")
    target_id = src["merged_into"]
    ev = conn.execute(
        "SELECT payload_json FROM niche_event WHERE niche_id = ? AND kind = 'merge' ORDER BY id DESC LIMIT 1", (niche_id,)
    ).fetchone()
    moved = json.loads(ev["payload_json"])["moved"] if ev else {}
    for k in moved.get("keywords", []):
        if k.get("dup"):
            continue
        conn.execute(
            "UPDATE niche_keyword SET niche_id = ? WHERE niche_id = ? AND region = ? AND keyword = ?",
            (niche_id, target_id, k["region"], k["keyword"]),
        )
    for run_id in moved.get("runs", []):
        conn.execute("INSERT OR IGNORE INTO niche_run (niche_id, run_id, added_at) VALUES (?, ?, ?)",
                     (niche_id, run_id, _now()))
        conn.execute("DELETE FROM niche_run WHERE niche_id = ? AND run_id = ?", (target_id, run_id))
    for ch in moved.get("channels", []):
        conn.execute("UPDATE niche_channel SET niche_id = ? WHERE niche_id = ? AND channel_id = ?", (niche_id, target_id, ch))
    for child in moved.get("children", []):
        conn.execute("UPDATE niche SET parent_id = ? WHERE id = ? AND parent_id = ?", (niche_id, child, target_id))
    conn.execute("UPDATE niche SET status = 'draft', merged_into = NULL WHERE id = ?", (niche_id,))
    _activate_if_ready(conn, niche_id)
    _touch(conn, niche_id)
    _touch(conn, target_id)
    _event(conn, niche_id, "unmerge", {"from": target_id}, rationale, actor)
    return get(conn, niche_id)


# ---------------------------------------------------------------------------
# Aggregated numbers and ranking
# ---------------------------------------------------------------------------


def _avg(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def signals(conn: sqlite3.Connection, niche_id: int) -> dict:
    """The real numbers of one niche, with no judgement. This is what Claude reads before ranking."""
    kws = [dict(r) for r in conn.execute("SELECT * FROM niche_keyword WHERE niche_id = ?", (niche_id,))]
    main = [k for k in kws if k["kind"] == "main"]
    runs = [
        dict(r)
        for r in conn.execute(
            """SELECT d.id, d.topic, d.region, d.score, d.confidence, d.verdict, d.created_at, d.result_json
               FROM niche_run nr JOIN dig_run d ON d.id = nr.run_id WHERE nr.niche_id = ? ORDER BY d.created_at DESC""",
            (niche_id,),
        )
    ]
    run_views = []
    latest_trend = None
    booms = 0
    outliers = 0
    for r in runs:
        res = json.loads(r.pop("result_json") or "{}")
        run_views.append(res.get("median_views"))
        if latest_trend is None:
            latest_trend = (res.get("trends") or {}).get("class")
        booms += len(res.get("small_boom_channels") or [])
        outliers += (res.get("outlier_counts") or {}).get("total", 0)
    chans = conn.execute("SELECT role, COUNT(*) AS n FROM niche_channel WHERE niche_id = ? GROUP BY role", (niche_id,))
    roles = {r["role"]: r["n"] for r in chans}
    dates = [k["scored_at"] for k in kws if k["scored_at"]] + [r["created_at"] for r in runs]
    return {
        "n_keywords": {k: sum(1 for x in kws if x["kind"] == k) for k in KINDS},
        "n_main_scored": sum(1 for k in main if k["score"] is not None),
        "main_demand": _avg([k["demand"] for k in main]),
        "main_competition": _avg([k["competition"] for k in main]),
        "main_score": _avg([k["score"] for k in main]),
        "n_runs": len(runs),
        "best_run_score": max((r["score"] for r in runs if r["score"] is not None), default=None),
        "latest_run_score": next((r["score"] for r in runs if r["score"] is not None), None),
        "latest_verdict": runs[0]["verdict"] if runs else None,
        "median_views": _avg(run_views),
        "outliers": outliers,
        "small_boom_channels": booms,
        "trend_class": latest_trend or next((k["trend_class"] for k in kws if k["trend_class"]), None),
        "channels": roles,
        "regions": sorted({k["region"] for k in kws} | {r["region"] for r in runs}),
        "latest_data_at": max(dates) if dates else None,
    }


def is_stale(basis: dict | None, now_signals: dict) -> bool:
    """A rank is stale compared with the snapshot taken at ranking time: a dig was added, or score / demand drifted past the threshold."""
    if not basis:
        return True
    if (now_signals.get("n_runs") or 0) != (basis.get("n_runs") or 0):
        return True
    for key, delta in (("best_run_score", STALE_SCORE_DELTA), ("main_demand", STALE_DEMAND_DELTA)):
        a, b = basis.get(key), now_signals.get(key)
        if (a is None) != (b is None) or (a is not None and abs(a - b) > delta):
            return True
    return False


def rank(conn: sqlite3.Connection, items: list[dict], rationale: str = "", actor: str = "claude") -> list[dict]:
    """items: [{niche_id, potential (0–100), rationale}]. Stored with a snapshot of the numbers so staleness can be detected later."""
    out = []
    for it in items:
        niche_id = int(it["niche_id"])
        _row(conn, niche_id)
        p = float(it["potential"])
        if not 0 <= p <= 100:
            raise NicheError("potential must be between 0 and 100")
        why = _require_rationale(it.get("rationale") or rationale)
        basis = signals(conn, niche_id)
        conn.execute(
            "UPDATE niche SET potential = ?, potential_rationale = ?, basis_json = ?, ranked_at = ? WHERE id = ?",
            (p, why, json.dumps(basis, ensure_ascii=False), _now(), niche_id),
        )
        _touch(conn, niche_id)
        _event(conn, niche_id, "rank", {"potential": p, "basis": basis}, why, actor)
        out.append({"niche_id": niche_id, "potential": p})
    return out


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def events(conn: sqlite3.Connection, niche_id: int | None = None, limit: int = 50) -> list[dict]:
    sql, args = "SELECT * FROM niche_event", []
    if niche_id is not None:
        sql += " WHERE niche_id = ?"
        args.append(niche_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
        out.append(d)
    return out


def get(conn: sqlite3.Connection, niche_id: int) -> dict:
    d = _row(conn, niche_id)
    kws = [dict(r) for r in conn.execute(
        "SELECT * FROM niche_keyword WHERE niche_id = ? ORDER BY kind, score IS NULL, score DESC, keyword", (niche_id,)
    )]
    d["keywords"] = {k: [x for x in kws if x["kind"] == k] for k in KINDS}
    d["runs"] = [dict(r) for r in conn.execute(
        """SELECT d.id, d.topic, d.region, d.window_days, d.fmt, d.score, d.label, d.confidence, d.verdict, d.created_at
           FROM niche_run nr JOIN dig_run d ON d.id = nr.run_id WHERE nr.niche_id = ? ORDER BY d.created_at DESC""",
        (niche_id,),
    )]
    d["channels"] = [dict(r) for r in conn.execute(
        """SELECT nc.channel_id, nc.role, nc.note, nc.updated_at, c.title, c.subs, c.median_long, c.median_short
           FROM niche_channel nc LEFT JOIN channel c ON c.channel_id = nc.channel_id
           WHERE nc.niche_id = ? ORDER BY nc.role, c.subs DESC""",
        (niche_id,),
    )]
    d["children"] = [r["id"] for r in conn.execute("SELECT id FROM niche WHERE parent_id = ? ORDER BY name", (niche_id,))]
    d["regions_allowed"] = regions_of_language(d["language"])
    d["signals"] = signals(conn, niche_id)
    d["stale"] = d["potential"] is not None and is_stale(d["basis"], d["signals"])
    d["events"] = events(conn, niche_id, 10)
    return d


def list_tree(conn: sqlite3.Connection, *, include_merged: bool = False, include_dropped: bool = False) -> list[dict]:
    """Flat list in tree order: each row has depth, path and summary counts. Orphans (parent merged/dropped) move to the root."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM niche ORDER BY name")]
    hidden = {"merged"} - ({"merged"} if include_merged else set())
    hidden |= {"dropped"} - ({"dropped"} if include_dropped else set())
    visible = {r["id"]: r for r in rows if r["status"] not in hidden}
    by_parent: dict[int | None, list[dict]] = {}
    for r in visible.values():
        parent = r["parent_id"] if r["parent_id"] in visible else None
        by_parent.setdefault(parent, []).append(r)
    counts = {
        r["niche_id"]: dict(r)
        for r in conn.execute(
            """SELECT n.id AS niche_id,
                      (SELECT COUNT(*) FROM niche_keyword k WHERE k.niche_id = n.id) AS n_keywords,
                      (SELECT COUNT(*) FROM niche_run x WHERE x.niche_id = n.id) AS n_runs,
                      (SELECT COUNT(*) FROM niche_channel c WHERE c.niche_id = n.id) AS n_channels
               FROM niche n"""
        )
    }
    out: list[dict] = []

    def walk(parent: int | None, depth: int, path: list[str]) -> None:
        for r in sorted(by_parent.get(parent, []), key=lambda x: (-(x["potential"] or 0), x["name"])):
            c = counts.get(r["id"], {})
            out.append({
                "id": r["id"], "name": r["name"], "language": r["language"], "parent_id": r["parent_id"], "depth": depth,
                "path": " / ".join(path + [r["name"]]),
                "motivation": r["motivation"], "motivation_secondary": r["motivation_secondary"],
                "status": r["status"], "merged_into": r["merged_into"],
                "potential": r["potential"], "ranked_at": r["ranked_at"],
                "n_keywords": c.get("n_keywords", 0), "n_runs": c.get("n_runs", 0), "n_channels": c.get("n_channels", 0),
                "updated_at": r["updated_at"],
            })
            walk(r["id"], depth + 1, path + [r["name"]])

    walk(None, 0, [])
    return out


def compare_table(conn: sqlite3.Connection, niche_ids: list[int] | None = None) -> list[dict]:
    """One row per live niche: name, motivation, real numbers, current rank and the stale flag. For Claude to compare and rank."""
    if niche_ids:
        ids = list(niche_ids)
    else:
        ids = [r["id"] for r in conn.execute("SELECT id FROM niche WHERE status IN ('draft', 'active') ORDER BY id")]
    out = []
    for nid in ids:
        d = _row(conn, nid)
        sig = signals(conn, nid)
        out.append({
            "id": nid, "name": d["name"], "language": d["language"], "parent_id": d["parent_id"], "status": d["status"],
            "motivation": d["motivation"], "potential": d["potential"], "ranked_at": d["ranked_at"],
            "stale": d["potential"] is not None and is_stale(d["basis"], sig),
            **sig,
        })
    out.sort(key=lambda x: (x["potential"] is None, -(x["potential"] or 0), x["name"]))
    return out


def find_similar_niches(conn: sqlite3.Connection, keywords: list[dict], *, min_overlap: int = 1) -> list[dict]:
    """Which live niches already hold any of these keywords (so Claude can decide to attach, create or merge)."""
    pairs = {((k.get("region") or "").upper(), _kw(k.get("keyword", ""))) for k in keywords}
    pairs.discard(("", ""))
    if not pairs:
        return []
    hits: dict[int, list[str]] = {}
    for region, kw in pairs:
        for r in conn.execute(
            """SELECT nk.niche_id FROM niche_keyword nk JOIN niche n ON n.id = nk.niche_id
               WHERE nk.region = ? AND nk.keyword = ? AND n.status IN ('draft', 'active')""",
            (region, kw),
        ):
            hits.setdefault(r["niche_id"], []).append(kw)
    out = []
    for nid, kws in hits.items():
        if len(kws) >= min_overlap:
            d = _row(conn, nid)
            out.append({"id": nid, "name": d["name"], "language": d["language"], "motivation": d["motivation"], "overlap": sorted(kws)})
    out.sort(key=lambda x: -len(x["overlap"]))
    return out
