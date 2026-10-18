"""比賽流程（service 層）。

Discord Bot 和網頁後台都只呼叫這裡的函式，不直接改資料表。
所有函式的 `now` 是 unix 秒；不傳就用現在時間（測試時傳固定值）。

一場比賽的狀態：

    upcoming ──(場地空出)──> ready_check ──(4人都按準備)──> playing
                                 │                              │
                          (逾時，有人沒按)                 (任一人回報)
                                 ▼                              ▼
                        cancelled(timeout)              awaiting_confirm
                                                     │ 對方確認       │ 對方不同意
                                                     ▼               ▼
                                                 finished      回到 playing（社長可裁決）

    社長可以隨時 cancel（不計分）。

Bot 的使用方式：
    - 玩家按按鈕 → 呼叫 join_queue / ready / report_result / confirm_result ...
    - 每隔幾秒呼叫一次 tick()，處理準備逾時、補位
    - 每隔幾秒呼叫 pending_notifications()，把通知私訊出去，成功後 mark_sent()
    - 出現 ServiceError 就把 e.message 回覆給使用者
"""

import json
import sqlite3
import time

import db
import messages
import rating as elo
from matching import Entry, find_match

READY_TIMEOUT = 180       # 按「準備」的時限（秒）
UPCOMING_LIMIT = 2        # 最多同時預告幾場「下一場」
WIN_POINTS = 21           # 每局幾分
MAX_POINTS = 30           # 比分上限（29:29 時下一分決勝）
MAX_NOTIFY_ATTEMPTS = 5   # 單則通知最多重試幾次

ACTIVE = ("upcoming", "ready_check", "playing", "awaiting_confirm")
HOLDS_COURT = ("ready_check", "playing", "awaiting_confirm")
ADMIN_ROLES = ("admin", "president")


class ServiceError(Exception):
    """可以直接顯示給使用者看的錯誤。

    code 給程式判斷；params 是訊息裡要填入的名字、數字；
    文字統一放在 messages.py（中英對照），用 .text(lang) 取得要回覆的內容。
    """

    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        super().__init__(messages.error_text(code, params))

    @property
    def message(self) -> str:
        return messages.error_text(self.code, self.params)

    def text(self, lang: str = "both") -> str:
        return messages.error_text(self.code, self.params, lang)


def _t(now):
    return time.time() if now is None else now


def _marks(values) -> str:
    return ",".join("?" * len(values))


# ---------------------------------------------------------------- 基本查詢

def _active_season(con):
    season = db.get_active_season(con)
    if season is None:
        raise ServiceError("no_season")
    return season


def _open_session(con):
    return con.execute("SELECT * FROM sessions WHERE is_open = 1").fetchone()


def _require_session(con):
    session = _open_session(con)
    if session is None:
        raise ServiceError("no_session")
    return session


def _require_admin(con, actor_id):
    season = _active_season(con)
    row = con.execute(
        "SELECT role FROM season_members WHERE season_id = ? AND member_id = ?",
        (season["id"], actor_id),
    ).fetchone()
    if row is None or row["role"] not in ADMIN_ROLES:
        raise ServiceError("forbidden")
    return season


def _fetch_match(con, match_id):
    match = con.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if match is None:
        raise ServiceError("no_match")
    return match


def _nick(con, member_id) -> str:
    row = con.execute("SELECT nickname FROM members WHERE id = ?", (member_id,)).fetchone()
    return row["nickname"] if row else "?"


def _rating(con, season_id, member_id) -> int:
    row = con.execute(
        "SELECT rating FROM season_members WHERE season_id = ? AND member_id = ?",
        (season_id, member_id),
    ).fetchone()
    return row["rating"] if row else elo.DEFAULT_RATING


def _team_of(con, match_id, member_id):
    row = con.execute(
        "SELECT team FROM match_players WHERE match_id = ? AND member_id = ?",
        (match_id, member_id),
    ).fetchone()
    return row["team"] if row else None


def _player_ids(con, match_id) -> list:
    return [
        r["member_id"]
        for r in con.execute(
            "SELECT member_id FROM match_players WHERE match_id = ? ORDER BY team, member_id",
            (match_id,),
        )
    ]


def _entries_of_match(con, match_id) -> list:
    return con.execute(
        "SELECT e.* FROM match_entries me JOIN queue_entries e ON e.id = me.entry_id "
        "WHERE me.match_id = ? ORDER BY e.joined_at, e.id",
        (match_id,),
    ).fetchall()


def _entry_members(entry) -> list:
    ids = [entry["member1_id"]]
    if entry["member2_id"] is not None:
        ids.append(entry["member2_id"])
    return ids


def _active_match_of(con, member_id):
    return con.execute(
        f"SELECT m.* FROM matches m JOIN match_players mp ON mp.match_id = m.id "
        f"WHERE mp.member_id = ? AND m.status IN ({_marks(ACTIVE)})",
        (member_id, *ACTIVE),
    ).fetchone()


def _waiting_entry_of(con, member_id):
    return con.execute(
        "SELECT * FROM queue_entries WHERE status = 'waiting' "
        "AND (member1_id = ? OR member2_id = ?)",
        (member_id, member_id),
    ).fetchone()


def _admin_ids(con, season_id) -> list:
    return [
        r["member_id"]
        for r in con.execute(
            f"SELECT member_id FROM season_members WHERE season_id = ? "
            f"AND role IN ({_marks(ADMIN_ROLES)})",
            (season_id, *ADMIN_ROLES),
        )
    ]


# ---------------------------------------------------------------- 通知佇列

def _notify(con, member_id, kind, payload, now):
    con.execute(
        "INSERT INTO notifications (member_id, kind, payload, created_at) VALUES (?,?,?,?)",
        (member_id, kind, json.dumps(payload, ensure_ascii=False), now),
    )


def _notify_match(con, match_id, kind, now, extra=None, skip=()):
    """通知一場比賽的 4 位球員，內容包含隊友、對手、場地、準備截止時間。"""
    match = _fetch_match(con, match_id)
    rows = con.execute(
        "SELECT mp.member_id, mp.team, mem.nickname FROM match_players mp "
        "JOIN members mem ON mem.id = mp.member_id WHERE mp.match_id = ? "
        "ORDER BY mp.team, mem.id",
        (match_id,),
    ).fetchall()
    for me in rows:
        if me["member_id"] in skip:
            continue
        payload = {
            "match_id": match_id,
            "court": match["court"],
            "deadline": match["ready_deadline"],
            "teammates": [r["nickname"] for r in rows
                          if r["team"] == me["team"] and r["member_id"] != me["member_id"]],
            "opponents": [r["nickname"] for r in rows if r["team"] != me["team"]],
        }
        if extra:
            payload.update(extra)
        _notify(con, me["member_id"], kind, payload, now)


def pending_notifications(con, limit: int = 50) -> list:
    """給 Bot 用：取出還沒發送的通知（含收件人的 discord_id）。"""
    rows = con.execute(
        "SELECT n.id, n.member_id, n.kind, n.payload, n.created_at, n.attempts, "
        "m.discord_id, m.nickname FROM notifications n JOIN members m ON m.id = n.member_id "
        "WHERE n.sent_at IS NULL AND n.attempts < ? ORDER BY n.id LIMIT ?",
        (MAX_NOTIFY_ATTEMPTS, limit),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["payload"] = json.loads(item["payload"])
        out.append(item)
    return out


def mark_sent(con, notification_id, now=None):
    with db.transaction(con):
        con.execute("UPDATE notifications SET sent_at = ? WHERE id = ?",
                    (_t(now), notification_id))


def mark_failed(con, notification_id, error: str):
    with db.transaction(con):
        con.execute(
            "UPDATE notifications SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:200], notification_id),
        )


# ---------------------------------------------------------------- 社課開關

def start_session(con, actor_id, now=None) -> int:
    """社長開始社課並開放報名。"""
    now = _t(now)
    with db.transaction(con):
        season = _require_admin(con, actor_id)
        if _open_session(con):
            raise ServiceError("session_open")
        cur = con.execute("INSERT INTO sessions (season_id) VALUES (?)", (season["id"],))
        con.execute("UPDATE settings SET registration_open = 1 WHERE id = 1")
        return cur.lastrowid


def set_registration(con, actor_id, is_open: bool, now=None):
    """暫停或恢復報名（不影響已經在排隊的人）。"""
    with db.transaction(con):
        _require_admin(con, actor_id)
        con.execute("UPDATE settings SET registration_open = ? WHERE id = 1", (int(is_open),))


def end_session(con, actor_id, force: bool = False, now=None):
    """結束社課。還有比賽沒結束時預設會拒絕，避免漏掉比分；force=True 才強制取消。"""
    now = _t(now)
    with db.transaction(con):
        _require_admin(con, actor_id)
        session = _require_session(con)
        unfinished = con.execute(
            f"SELECT id, status FROM matches WHERE session_id = ? AND status IN ({_marks(ACTIVE)})",
            (session["id"], *ACTIVE),
        ).fetchall()
        busy = [m for m in unfinished if m["status"] in ("playing", "awaiting_confirm")]
        if busy and not force:
            raise ServiceError("matches_unfinished", count=len(busy))
        for m in unfinished:
            _cancel_match(con, m["id"], "session_closed", now, requeue=False)
        con.execute("UPDATE queue_entries SET status = 'cancelled' "
                    "WHERE session_id = ? AND status = 'waiting'", (session["id"],))
        con.execute("UPDATE sessions SET is_open = 0, ended_at = datetime('now') WHERE id = ?",
                    (session["id"],))
        con.execute("UPDATE settings SET registration_open = 0 WHERE id = 1")


# ---------------------------------------------------------------- 排隊

def join_queue(con, member_id, partner_id=None, now=None) -> int:
    """進入匹配。partner_id 有值就是雙排（Bot 應先取得隊友同意再呼叫）。"""
    now = _t(now)
    with db.transaction(con):
        session = _require_session(con)
        settings = con.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        if not settings["registration_open"]:
            raise ServiceError("registration_closed")
        ids = [member_id] + ([partner_id] if partner_id is not None else [])
        if len(set(ids)) != len(ids):
            raise ServiceError("bad_partner")
        for m in ids:
            nick = _nick(con, m)
            in_season = con.execute(
                "SELECT 1 FROM season_members WHERE season_id = ? AND member_id = ?",
                (session["season_id"], m),
            ).fetchone()
            if not in_season:
                raise ServiceError("not_in_season", nick=nick)
            active = _active_match_of(con, m)
            if active:
                if active["status"] in ("playing", "awaiting_confirm"):
                    raise ServiceError("report_first", nick=nick)
                raise ServiceError("already_matched", nick=nick)
            if _waiting_entry_of(con, m):
                raise ServiceError("already_queued", nick=nick)
        try:
            cur = con.execute(
                "INSERT INTO queue_entries (session_id, member1_id, member2_id, joined_at) "
                "VALUES (?,?,?,?)",
                (session["id"], member_id, partner_id, now),
            )
        except sqlite3.IntegrityError:
            raise ServiceError("already_queued")
        _tick(con, now)
        return cur.lastrowid


def leave_queue(con, member_id, now=None) -> bool:
    """離開排隊（雙排會連隊友一起離開）。
    已被排進「下一場預告」時，該預告會取消，其他人回到原本的排隊位置。"""
    now = _t(now)
    with db.transaction(con):
        entry = _waiting_entry_of(con, member_id)
        if entry:
            _cancel_entry(con, entry, member_id, "left", now)
            _tick(con, now)
            return True
        match = _active_match_of(con, member_id)
        if match is None:
            return False
        if match["status"] != "upcoming":
            raise ServiceError("in_match")
        entries = _entries_of_match(con, match["id"])
        _cancel_match(con, match["id"], "player_left", now, requeue=False, skip=(member_id,))
        for e in entries:
            if member_id in _entry_members(e):
                _cancel_entry(con, e, member_id, "left", now)
            else:
                con.execute("UPDATE queue_entries SET status = 'waiting' WHERE id = ?", (e["id"],))
        _tick(con, now)
        return True


def _cancel_entry(con, entry, leaver_id, reason, now):
    con.execute("UPDATE queue_entries SET status = 'cancelled' WHERE id = ?", (entry["id"],))
    for other in _entry_members(entry):
        if other != leaver_id:
            _notify(con, other, "queue_left", {"reason": reason, "by": _nick(con, leaver_id)}, now)


# ---------------------------------------------------------------- 配對與場地

def tick(con, now=None):
    """定期呼叫（建議每 5 秒）：處理準備逾時，並把空出的場地補上比賽。"""
    now = _t(now)
    with db.transaction(con):
        _tick(con, now)


def _tick(con, now):
    session = _open_session(con)
    if session is None:
        return
    _process_timeouts(con, session, now)
    _fill_courts(con, session, now)
    _fill_upcoming(con, session, now)


def _waiting_entries(con, session) -> list:
    rows = con.execute(
        "SELECT * FROM queue_entries WHERE session_id = ? AND status = 'waiting' "
        "ORDER BY joined_at, id",
        (session["id"],),
    ).fetchall()
    out = []
    for r in rows:
        players = tuple(_entry_members(r))
        ratings = tuple(_rating(con, session["season_id"], p) for p in players)
        out.append(Entry(r["id"], players, ratings, r["joined_at"]))
    return out


def _court_count(con) -> int:
    return con.execute("SELECT court_count FROM settings WHERE id = 1").fetchone()["court_count"]


def _free_courts(con, session) -> list:
    busy = {
        r["court"]
        for r in con.execute(
            f"SELECT court FROM matches WHERE session_id = ? AND court IS NOT NULL "
            f"AND status IN ({_marks(HOLDS_COURT)})",
            (session["id"], *HOLDS_COURT),
        )
    }
    return [c for c in range(1, _court_count(con) + 1) if c not in busy]


def _create_match(con, session, found, status, court, now) -> int:
    deadline = now + READY_TIMEOUT if status == "ready_check" else None
    cur = con.execute(
        "INSERT INTO matches (session_id, court, status, ready_deadline, created_at) "
        "VALUES (?,?,?,?,?)",
        (session["id"], court, status, deadline, now),
    )
    match_id = cur.lastrowid
    for team, ids in (("A", found.team_a), ("B", found.team_b)):
        for pid in ids:
            con.execute("INSERT INTO match_players (match_id, member_id, team) VALUES (?,?,?)",
                        (match_id, pid, team))
    for entry_id in found.entries:
        con.execute("INSERT INTO match_entries (match_id, entry_id) VALUES (?,?)",
                    (match_id, entry_id))
        con.execute("UPDATE queue_entries SET status = 'matched' WHERE id = ?", (entry_id,))
    return match_id


def _start_ready_check(con, match_id, court, now):
    con.execute(
        "UPDATE matches SET court = ?, status = 'ready_check', ready_deadline = ? WHERE id = ?",
        (court, now + READY_TIMEOUT, match_id),
    )
    con.execute("UPDATE match_players SET is_ready = 0 WHERE match_id = ?", (match_id,))
    _notify_match(con, match_id, "your_turn", now)


def _fill_courts(con, session, now):
    for court in _free_courts(con, session):
        upcoming = con.execute(
            "SELECT id FROM matches WHERE session_id = ? AND status = 'upcoming' "
            "ORDER BY created_at, id LIMIT 1",
            (session["id"],),
        ).fetchone()
        if upcoming:
            _start_ready_check(con, upcoming["id"], court, now)
            continue
        found = find_match(_waiting_entries(con, session), now)
        if found is None:
            break
        match_id = _create_match(con, session, found, "ready_check", court, now)
        _notify_match(con, match_id, "your_turn", now)


def _fill_upcoming(con, session, now):
    if _court_count(con) < 1:
        return
    count = con.execute(
        "SELECT COUNT(*) AS n FROM matches WHERE session_id = ? AND status = 'upcoming'",
        (session["id"],),
    ).fetchone()["n"]
    while count < UPCOMING_LIMIT:
        found = find_match(_waiting_entries(con, session), now)
        if found is None:
            break
        match_id = _create_match(con, session, found, "upcoming", None, now)
        _notify_match(con, match_id, "next_up", now)
        count += 1


def _process_timeouts(con, session, now):
    expired = con.execute(
        "SELECT id FROM matches WHERE session_id = ? AND status = 'ready_check' "
        "AND ready_deadline < ?",
        (session["id"], now),
    ).fetchall()
    for m in expired:
        _timeout_match(con, m["id"], now)


def _timeout_match(con, match_id, now):
    """準備逾時：按了準備的人保留原本的排隊順序；沒按的人排到隊尾。"""
    ready_ids = {
        r["member_id"]
        for r in con.execute(
            "SELECT member_id FROM match_players WHERE match_id = ? AND is_ready = 1",
            (match_id,),
        )
    }
    entries = _entries_of_match(con, match_id)
    con.execute(
        "UPDATE matches SET status = 'cancelled', cancel_reason = 'timeout', finished_at = ? "
        "WHERE id = ?",
        (now, match_id),
    )
    for e in entries:
        members = _entry_members(e)
        absent = [m for m in members if m not in ready_ids]
        if not absent:
            con.execute("UPDATE queue_entries SET status = 'waiting' WHERE id = ?", (e["id"],))
            continue
        con.execute("UPDATE queue_entries SET status = 'cancelled' WHERE id = ?", (e["id"],))
        for m in members:
            kept_time = e["joined_at"] if m in ready_ids else now
            con.execute(
                "INSERT INTO queue_entries (session_id, member1_id, joined_at) VALUES (?,?,?)",
                (e["session_id"], m, kept_time),
            )
    for pid in _player_ids(con, match_id):
        if pid in ready_ids:
            _notify(con, pid, "match_cancelled",
                    {"match_id": match_id, "reason": "timeout", "requeued": True}, now)
        else:
            _notify(con, pid, "no_show", {"match_id": match_id}, now)


def _restore_entries(con, match_id):
    for e in _entries_of_match(con, match_id):
        con.execute("UPDATE queue_entries SET status = 'waiting' WHERE id = ?", (e["id"],))


def _cancel_match(con, match_id, reason, now, requeue, skip=()):
    con.execute(
        "UPDATE matches SET status = 'cancelled', cancel_reason = ?, finished_at = ? WHERE id = ?",
        (reason, now, match_id),
    )
    if requeue:
        _restore_entries(con, match_id)
    _notify_match(con, match_id, "match_cancelled", now,
                  extra={"reason": reason, "requeued": requeue}, skip=skip)


# ---------------------------------------------------------------- 準備、回報、確認

def ready(con, match_id, member_id, now=None) -> str:
    """按下「準備」。回傳 'waiting_others'（還在等其他人）或 'playing'（開打）。"""
    now = _t(now)
    tick(con, now)  # 先處理逾時，才知道這場是不是已經作廢
    with db.transaction(con):
        match = _fetch_match(con, match_id)
        if _team_of(con, match_id, member_id) is None:
            raise ServiceError("not_player")
        if match["status"] == "cancelled" and match["cancel_reason"] == "timeout":
            raise ServiceError("expired")
        if match["status"] == "playing":
            return "playing"
        if match["status"] != "ready_check":
            raise ServiceError("bad_state")
        con.execute("UPDATE match_players SET is_ready = 1 WHERE match_id = ? AND member_id = ?",
                    (match_id, member_id))
        waiting = con.execute(
            "SELECT COUNT(*) AS n FROM match_players WHERE match_id = ? AND is_ready = 0",
            (match_id,),
        ).fetchone()["n"]
        if waiting:
            return "waiting_others"
        con.execute("UPDATE matches SET status = 'playing' WHERE id = ?", (match_id,))
        _notify_match(con, match_id, "match_start", now)
        return "playing"


def validate_score(a: int, b: int):
    """檢查比分是否符合羽球規則：21 分制、領先 2 分才贏、30 分封頂。"""
    if not (isinstance(a, int) and isinstance(b, int)) or a < 0 or b < 0:
        raise ServiceError("bad_score")
    hi, lo = max(a, b), min(a, b)
    ok = (
        (hi == WIN_POINTS and lo <= WIN_POINTS - 2)
        or (WIN_POINTS < hi < MAX_POINTS and lo == hi - 2)
        or (hi == MAX_POINTS and lo in (MAX_POINTS - 2, MAX_POINTS - 1))
    )
    if not ok:
        raise ServiceError("bad_score", a=a, b=b, win=WIN_POINTS, cap=MAX_POINTS)


def report_result(con, match_id, reporter_id, my_score: int, opp_score: int, now=None):
    """任一位球員回報比分（以回報者的角度：我們 my_score，對方 opp_score）。
    回報後要由對方隊伍確認，分數才會計算。"""
    now = _t(now)
    validate_score(my_score, opp_score)
    with db.transaction(con):
        match = _fetch_match(con, match_id)
        team = _team_of(con, match_id, reporter_id)
        if team is None:
            raise ServiceError("not_player")
        if match["status"] != "playing":
            raise ServiceError("not_playing")
        score_a, score_b = (my_score, opp_score) if team == "A" else (opp_score, my_score)
        con.execute(
            "UPDATE matches SET status = 'awaiting_confirm', score_a = ?, score_b = ?, "
            "reported_by_member = ? WHERE id = ?",
            (score_a, score_b, reporter_id, match_id),
        )
        for row in con.execute(
            "SELECT member_id FROM match_players WHERE match_id = ? AND team != ?",
            (match_id, team),
        ).fetchall():
            _notify(con, row["member_id"], "confirm_request", {
                "match_id": match_id,
                "reported_by": _nick(con, reporter_id),
                "their_score": my_score,   # 回報者那一隊的分數
                "your_score": opp_score,   # 收到通知的這一隊的分數
            }, now)


def confirm_result(con, match_id, confirmer_id, agree: bool, now=None):
    """對方隊伍確認比分。同意就結算積分；不同意就退回重新回報並通知社長。"""
    now = _t(now)
    with db.transaction(con):
        match = _fetch_match(con, match_id)
        if match["status"] != "awaiting_confirm":
            raise ServiceError("not_awaiting")
        team = _team_of(con, match_id, confirmer_id)
        if team is None:
            raise ServiceError("not_player")
        if team == _team_of(con, match_id, match["reported_by_member"]):
            raise ServiceError("not_opponent")
        if agree:
            _finalize(con, match, match["score_a"], match["score_b"], now)
            _tick(con, now)
            return
        con.execute(
            "UPDATE matches SET status = 'playing', score_a = NULL, score_b = NULL, "
            "reported_by_member = NULL WHERE id = ?", (match_id,))
        _notify_match(con, match_id, "result_disputed", now)
        session = con.execute("SELECT season_id FROM sessions WHERE id = ?",
                              (match["session_id"],)).fetchone()
        for admin in _admin_ids(con, session["season_id"]):
            _notify(con, admin, "admin_dispute",
                    {"match_id": match_id, "court": match["court"]}, now)


def _finalize(con, match, score_a, score_b, now):
    """計算並寫入積分，結束這場比賽。"""
    season_id = con.execute("SELECT season_id FROM sessions WHERE id = ?",
                            (match["session_id"],)).fetchone()["season_id"]
    rows = con.execute(
        "SELECT mp.member_id, mp.team, sm.rating, sm.games_played FROM match_players mp "
        "JOIN season_members sm ON sm.member_id = mp.member_id AND sm.season_id = ? "
        "WHERE mp.match_id = ? ORDER BY mp.team, mp.member_id",
        (season_id, match["id"]),
    ).fetchall()
    team_a = [r for r in rows if r["team"] == "A"]
    team_b = [r for r in rows if r["team"] == "B"]
    delta_a, delta_b = elo.calc_changes(
        [(r["rating"], r["games_played"]) for r in team_a],
        [(r["rating"], r["games_played"]) for r in team_b],
        a_won=score_a > score_b,
        score_diff=abs(score_a - score_b),
    )
    for r, delta in list(zip(team_a, delta_a)) + list(zip(team_b, delta_b)):
        new = r["rating"] + delta
        con.execute(
            "UPDATE season_members SET rating = ?, games_played = games_played + 1 "
            "WHERE season_id = ? AND member_id = ?",
            (new, season_id, r["member_id"]),
        )
        con.execute(
            "INSERT INTO rating_history (season_id, match_id, member_id, before, after) "
            "VALUES (?,?,?,?,?)",
            (season_id, match["id"], r["member_id"], r["rating"], new),
        )
        won = (r["team"] == "A") == (score_a > score_b)
        _notify(con, r["member_id"], "result", {
            "match_id": match["id"], "won": won, "before": r["rating"], "after": new,
            "delta": delta,
            "your_score": score_a if r["team"] == "A" else score_b,
            "their_score": score_b if r["team"] == "A" else score_a,
        }, now)
    con.execute(
        "UPDATE matches SET status = 'finished', score_a = ?, score_b = ?, finished_at = ? "
        "WHERE id = ?",
        (score_a, score_b, now, match["id"]),
    )


# ---------------------------------------------------------------- 社長 / 管理員

def admin_cancel_match(con, actor_id, match_id, now=None):
    """下架一場比賽（不計分），場地立刻換下一場。
    尚未開打的比賽，球員會回到原本的排隊位置；已開打的，球員需自行重新排隊。"""
    now = _t(now)
    with db.transaction(con):
        _require_admin(con, actor_id)
        match = _fetch_match(con, match_id)
        if match["status"] not in ACTIVE:
            raise ServiceError("bad_state")
        _cancel_match(con, match_id, "admin", now,
                      requeue=match["status"] in ("upcoming", "ready_check"))
        _tick(con, now)


def admin_set_result(con, actor_id, match_id, score_a: int, score_b: int, now=None):
    """社長直接裁決比分（score_a 是 A 隊、score_b 是 B 隊），用於爭議或大家都忘記回報。"""
    now = _t(now)
    validate_score(score_a, score_b)
    with db.transaction(con):
        _require_admin(con, actor_id)
        match = _fetch_match(con, match_id)
        if match["status"] not in ("playing", "awaiting_confirm"):
            raise ServiceError("bad_state")
        _finalize(con, match, score_a, score_b, now)
        _tick(con, now)


def admin_set_court_count(con, actor_id, count: int, now=None):
    """動態調整可用場地數（0~20），增加場地時會立刻補上比賽。"""
    now = _t(now)
    if not isinstance(count, int) or not 0 <= count <= 20:
        raise ServiceError("bad_count")
    with db.transaction(con):
        _require_admin(con, actor_id)
        con.execute("UPDATE settings SET court_count = ? WHERE id = 1", (count,))
        _tick(con, now)


def admin_set_rating(con, actor_id, member_id, new_rating: int, now=None):
    """手動調整某人本學期的積分，並留下紀錄。"""
    with db.transaction(con):
        season = _require_admin(con, actor_id)
        row = con.execute(
            "SELECT rating FROM season_members WHERE season_id = ? AND member_id = ?",
            (season["id"], member_id),
        ).fetchone()
        if row is None:
            raise ServiceError("not_in_season")
        con.execute("UPDATE season_members SET rating = ? WHERE season_id = ? AND member_id = ?",
                    (new_rating, season["id"], member_id))
        con.execute(
            "INSERT INTO rating_adjustments (season_id, member_id, before, after, by_member) "
            "VALUES (?,?,?,?,?)",
            (season["id"], member_id, row["rating"], new_rating, actor_id),
        )


def admin_remove_from_queue(con, actor_id, member_id, now=None):
    """社長把某人移出排隊（例如他先去休息了）。"""
    now = _t(now)
    with db.transaction(con):
        _require_admin(con, actor_id)
        entry = _waiting_entry_of(con, member_id)
        if entry is None:
            raise ServiceError("not_queued")
        _cancel_entry(con, entry, member_id, "admin", now)
        _notify(con, member_id, "queue_left", {"reason": "admin"}, now)
        _tick(con, now)


# ---------------------------------------------------------------- 狀態查詢（Bot 與網頁共用）

def _match_view(con, match) -> dict:
    players = con.execute(
        "SELECT mp.member_id, mp.team, mp.is_ready, mem.nickname FROM match_players mp "
        "JOIN members mem ON mem.id = mp.member_id WHERE mp.match_id = ? "
        "ORDER BY mp.team, mem.id",
        (match["id"],),
    ).fetchall()

    def team(t):
        return [{"id": p["member_id"], "nickname": p["nickname"], "ready": bool(p["is_ready"])}
                for p in players if p["team"] == t]

    return {
        "id": match["id"], "status": match["status"], "court": match["court"],
        "deadline": match["ready_deadline"],
        "team_a": team("A"), "team_b": team("B"),
        "score_a": match["score_a"], "score_b": match["score_b"],
    }


def get_board(con, now=None) -> dict:
    """場地看板：各場地狀況、下一場預告、排隊名單。"""
    now = _t(now)
    session = _open_session(con)
    settings = con.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    board = {
        "session_open": session is not None,
        "registration_open": bool(settings["registration_open"]),
        "court_count": settings["court_count"],
        "courts": [], "upcoming": [], "queue": [],
    }
    if session is None:
        return board
    active = con.execute(
        f"SELECT * FROM matches WHERE session_id = ? AND status IN ({_marks(ACTIVE)}) "
        f"ORDER BY created_at, id",
        (session["id"], *ACTIVE),
    ).fetchall()
    by_court = {m["court"]: m for m in active if m["court"] is not None}
    for c in range(1, settings["court_count"] + 1):
        board["courts"].append({"court": c, "match": _match_view(con, by_court[c]) if c in by_court else None})
    board["upcoming"] = [_match_view(con, m) for m in active if m["status"] == "upcoming"]
    for e in _waiting_entries(con, session):
        board["queue"].append({
            "entry_id": e.id,
            "players": [{"id": p, "nickname": _nick(con, p), "rating": r}
                        for p, r in zip(e.players, e.ratings)],
            "waited": max(0, int(now - e.joined_at)),
        })
    return board


def get_member_status(con, member_id) -> dict:
    """某位社員現在的狀態，給「我的狀態」指令使用。
    state: idle / queued / upcoming / ready_check / playing / awaiting_confirm"""
    match = _active_match_of(con, member_id)
    if match:
        return {"state": match["status"], "match_id": match["id"], "court": match["court"],
                "deadline": match["ready_deadline"]}
    if _waiting_entry_of(con, member_id):
        return {"state": "queued"}
    return {"state": "idle"}
