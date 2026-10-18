"""比賽流程測試：python test_service.py"""
import db
import service
from service import ServiceError

T0 = 1_000_000.0


# ---------------------------------------------------------------- 測試工具

def setup(n_players=8, courts=5, start=True):
    """建立資料庫、一位社長（不參賽）、n 位球員，並開始社課。"""
    con = db.connect()
    db.migrate(con)
    season = db.start_season(con, "1151")
    admin = db.get_or_create_member(con, "admin", "社長")
    db.join_season(con, season, admin, role="president")
    players = []
    for i in range(n_players):
        m = db.get_or_create_member(con, f"d{i}", f"P{i}")
        db.join_season(con, season, m)
        players.append(m)
    with con:
        con.execute("UPDATE settings SET court_count = ? WHERE id = 1", (courts,))
    if start:
        service.start_session(con, admin, now=T0)
    return con, admin, players


def expect_error(code, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ServiceError as e:
        assert e.code == code, f"預期 {code}，實際 {e.code}：{e.message}"
        return
    raise AssertionError(f"應該要出現錯誤 {code}")


def join_all(con, players, start=T0):
    for i, p in enumerate(players):
        service.join_queue(con, p, now=start + i)


def match_of(con, member):
    return con.execute(
        "SELECT m.* FROM matches m JOIN match_players mp ON mp.match_id = m.id "
        "WHERE mp.member_id = ? AND m.status IN ('upcoming','ready_check','playing','awaiting_confirm')",
        (member,),
    ).fetchone()


def teams(con, match_id):
    out = {"A": [], "B": []}
    for r in con.execute("SELECT member_id, team FROM match_players WHERE match_id = ?", (match_id,)):
        out[r["team"]].append(r["member_id"])
    return out


def kinds(con, member=None):
    sql = "SELECT kind FROM notifications"
    args = ()
    if member is not None:
        sql += " WHERE member_id = ?"
        args = (member,)
    return [r["kind"] for r in con.execute(sql, args)]


def all_ready(con, match_id, now):
    for pid in service._player_ids(con, match_id):
        service.ready(con, match_id, pid, now=now)


def rating_of(con, member):
    return con.execute("SELECT rating, games_played FROM season_members WHERE member_id = ?",
                       (member,)).fetchone()


def play_and_finish(con, match_id, now, score=(21, 17)):
    """4 人準備 → A 隊某人回報（A 贏）→ B 隊某人確認。"""
    all_ready(con, match_id, now)
    t = teams(con, match_id)
    service.report_result(con, match_id, t["A"][0], score[0], score[1], now=now + 1)
    service.confirm_result(con, match_id, t["B"][0], True, now=now + 2)
    return t


# ---------------------------------------------------------------- 測試

def test_full_flow():
    con, admin, p = setup(8)
    join_all(con, p[:4])
    m = match_of(con, p[0])
    assert m["status"] == "ready_check" and m["court"] == 1
    assert m["ready_deadline"] == T0 + 3 + service.READY_TIMEOUT
    assert kinds(con).count("your_turn") == 4

    # 還沒全員準備，不會開打
    t = teams(con, m["id"])
    assert service.ready(con, m["id"], p[0], now=T0 + 10) == "waiting_others"
    for pid in p[1:3]:
        service.ready(con, m["id"], pid, now=T0 + 11)
    assert service.ready(con, m["id"], p[3], now=T0 + 12) == "playing"
    assert match_of(con, p[0])["status"] == "playing"
    assert kinds(con).count("match_start") == 4

    # 還沒回報前不能再排隊
    expect_error("report_first", service.join_queue, con, p[0], now=T0 + 20)

    a, a2, b = t["A"][0], t["A"][1], t["B"][0]
    service.report_result(con, m["id"], a, 21, 17, now=T0 + 30)
    assert match_of(con, p[0])["status"] == "awaiting_confirm"
    assert kinds(con).count("confirm_request") == 2
    # 同隊不能自己確認
    expect_error("not_opponent", service.confirm_result, con, m["id"], a2, True, now=T0 + 31)
    service.confirm_result(con, m["id"], b, True, now=T0 + 32)

    # 結算：A 隊贏，積分上升、B 隊下降，場數 +1，紀錄 4 筆
    for pid in t["A"]:
        r = rating_of(con, pid)
        assert r["rating"] > 1000 and r["games_played"] == 1
    for pid in t["B"]:
        assert rating_of(con, pid)["rating"] < 1000
    assert con.execute("SELECT COUNT(*) FROM rating_history").fetchone()[0] == 4
    assert kinds(con).count("result") == 4
    assert con.execute("SELECT status FROM matches WHERE id = ?", (m["id"],)).fetchone()[0] == "finished"

    # 打完可以重新排隊
    service.join_queue(con, p[0], now=T0 + 40)
    print("full flow OK")


def test_ready_timeout():
    con, admin, p = setup(5)
    join_all(con, p[:5])
    m = match_of(con, p[0])
    assert set(service._player_ids(con, m["id"])) == set(p[:4])  # 先來的四人
    for pid in p[:3]:
        service.ready(con, m["id"], pid, now=T0 + 10)             # p3 沒按

    service.tick(con, now=T0 + 200)                               # 超過 180 秒

    old = con.execute("SELECT status, cancel_reason FROM matches WHERE id = ?", (m["id"],)).fetchone()
    assert (old["status"], old["cancel_reason"]) == ("cancelled", "timeout")
    # 按了準備的 p0~p2 保留順位，p4 遞補進來；p3 被排到隊尾
    new = match_of(con, p[0])
    assert new["id"] != m["id"] and new["status"] == "ready_check"
    assert set(service._player_ids(con, new["id"])) == {p[0], p[1], p[2], p[4]}
    assert service.get_member_status(con, p[3])["state"] == "queued"
    assert "no_show" in kinds(con, p[3])
    assert "match_cancelled" in kinds(con, p[0])
    # 逾時後才按準備 → 告知已過號
    expect_error("expired", service.ready, con, m["id"], p[3], now=T0 + 201)
    print("ready timeout OK")


def test_upcoming_promoted_when_court_frees():
    con, admin, p = setup(8, courts=1)
    join_all(con, p)
    m1 = match_of(con, p[0])
    assert m1["status"] == "ready_check" and m1["court"] == 1
    m2 = match_of(con, p[4])
    assert m2["status"] == "upcoming" and m2["court"] is None
    assert kinds(con, p[4]) == ["next_up"]

    play_and_finish(con, m1["id"], T0 + 100)
    m2 = match_of(con, p[4])
    assert m2["status"] == "ready_check" and m2["court"] == 1
    assert "your_turn" in kinds(con, p[4])
    print("upcoming promoted OK")


def test_admin_cancel_and_permissions():
    con, admin, p = setup(8, courts=1)
    join_all(con, p)
    m1 = match_of(con, p[0])
    all_ready(con, m1["id"], T0 + 10)          # 開打
    expect_error("forbidden", service.admin_cancel_match, con, p[0], m1["id"], now=T0 + 20)

    service.admin_cancel_match(con, admin, m1["id"], now=T0 + 20)
    row = con.execute("SELECT status, cancel_reason FROM matches WHERE id = ?", (m1["id"],)).fetchone()
    assert (row["status"], row["cancel_reason"]) == ("cancelled", "admin")
    assert rating_of(con, p[0])["games_played"] == 0          # 不計分
    assert match_of(con, p[4])["status"] == "ready_check"     # 下一場立刻上場
    assert service.get_member_status(con, p[0])["state"] == "idle"
    service.join_queue(con, p[0], now=T0 + 30)                # 被取消的人可重新排隊

    # 取消尚未開打的比賽：球員回到原本的排隊位置
    m2 = match_of(con, p[4])
    service.admin_cancel_match(con, admin, m2["id"], now=T0 + 40)
    assert service.get_member_status(con, p[4])["state"] in ("ready_check", "upcoming")
    print("admin cancel OK")


def test_score_validation():
    ok = [(21, 0), (21, 19), (22, 20), (25, 23), (29, 27), (30, 28), (30, 29), (19, 21)]
    bad = [(21, 20), (25, 22), (20, 18), (30, 27), (31, 29), (21, 21), (-1, 21)]
    for a, b in ok:
        service.validate_score(a, b)
    for a, b in bad:
        expect_error("bad_score", service.validate_score, a, b)
    print("score validation OK")


def test_dispute_and_admin_ruling():
    con, admin, p = setup(4)
    join_all(con, p)
    m = match_of(con, p[0])
    all_ready(con, m["id"], T0 + 10)
    t = teams(con, m["id"])
    service.report_result(con, m["id"], t["A"][0], 21, 10, now=T0 + 20)
    service.confirm_result(con, m["id"], t["B"][0], False, now=T0 + 21)   # 不同意

    assert match_of(con, p[0])["status"] == "playing"                       # 退回重報
    assert "result_disputed" in kinds(con, p[0])
    assert "admin_dispute" in kinds(con, admin)

    service.admin_set_result(con, admin, m["id"], 21, 19, now=T0 + 30)      # 社長裁決 21:19
    row = con.execute("SELECT status, score_a, score_b FROM matches WHERE id = ?", (m["id"],)).fetchone()
    assert (row["status"], row["score_a"], row["score_b"]) == ("finished", 21, 19)
    assert rating_of(con, t["A"][0])["rating"] > 1000
    print("dispute OK")


def test_court_count_change():
    con, admin, p = setup(8, courts=1)
    join_all(con, p)
    assert match_of(con, p[4])["status"] == "upcoming"
    service.admin_set_court_count(con, admin, 2, now=T0 + 50)
    m2 = match_of(con, p[4])
    assert m2["status"] == "ready_check" and m2["court"] == 2
    expect_error("bad_count", service.admin_set_court_count, con, admin, 99)
    expect_error("forbidden", service.admin_set_court_count, con, p[0], 3)
    print("court count OK")


def test_queue_rules():
    con, admin, p = setup(6, start=False)
    expect_error("no_session", service.join_queue, con, p[0])
    expect_error("forbidden", service.start_session, con, p[0])
    service.start_session(con, admin, now=T0)
    expect_error("session_open", service.start_session, con, admin)

    service.set_registration(con, admin, False)
    expect_error("registration_closed", service.join_queue, con, p[0], now=T0)
    service.set_registration(con, admin, True)

    service.join_queue(con, p[0], now=T0)
    expect_error("already_queued", service.join_queue, con, p[0], now=T0 + 1)
    expect_error("already_queued", service.join_queue, con, p[1], p[0], now=T0 + 1)  # 隊友已在排隊
    expect_error("bad_partner", service.join_queue, con, p[1], p[1], now=T0 + 1)

    # 不在本學期名單的人
    outsider = db.get_or_create_member(con, "x", "外人")
    expect_error("not_in_season", service.join_queue, con, outsider, now=T0 + 1)

    # 離開排隊
    assert service.leave_queue(con, p[0], now=T0 + 2) is True
    assert service.leave_queue(con, p[0], now=T0 + 3) is False

    # 雙排 + 雙排 → 成一場，雙排的人同隊
    service.join_queue(con, p[0], p[1], now=T0 + 10)
    service.join_queue(con, p[2], p[3], now=T0 + 11)
    m = match_of(con, p[0])
    t = teams(con, m["id"])
    assert {p[0], p[1]} in (set(t["A"]), set(t["B"]))
    assert {p[2], p[3]} in (set(t["A"]), set(t["B"]))
    print("queue rules OK")


def test_leave_upcoming_match():
    con, admin, p = setup(8, courts=1)
    join_all(con, p)
    up = match_of(con, p[4])
    assert up["status"] == "upcoming"
    assert service.leave_queue(con, p[5], now=T0 + 30) is True
    assert con.execute("SELECT status FROM matches WHERE id = ?", (up["id"],)).fetchone()[0] == "cancelled"
    assert service.get_member_status(con, p[5])["state"] == "idle"
    for pid in (p[4], p[6], p[7]):                        # 其他人回到排隊
        assert service.get_member_status(con, pid)["state"] == "queued"
    # 打到一半不能自己退出
    expect_error("in_match", service.leave_queue, con, p[0], now=T0 + 31)
    print("leave upcoming OK")


def test_admin_remove_and_rating():
    con, admin, p = setup(4, courts=0)    # 0 個場地：只排隊不開打
    service.join_queue(con, p[0], now=T0)
    service.admin_remove_from_queue(con, admin, p[0], now=T0 + 1)
    assert service.get_member_status(con, p[0])["state"] == "idle"
    assert "queue_left" in kinds(con, p[0])
    expect_error("not_queued", service.admin_remove_from_queue, con, admin, p[0])

    service.admin_set_rating(con, admin, p[1], 1300)
    assert rating_of(con, p[1])["rating"] == 1300
    adj = con.execute("SELECT before, after, by_member FROM rating_adjustments").fetchone()
    assert (adj["before"], adj["after"], adj["by_member"]) == (1000, 1300, admin)
    expect_error("forbidden", service.admin_set_rating, con, p[0], p[1], 900)
    print("admin remove/rating OK")


def test_notification_outbox():
    con, admin, p = setup(4)
    join_all(con, p)
    pending = service.pending_notifications(con)
    assert len(pending) == 4 and all(n["kind"] == "your_turn" for n in pending)
    n = pending[0]
    assert n["discord_id"].startswith("d") and n["payload"]["court"] == 1
    assert len(n["payload"]["teammates"]) == 1 and len(n["payload"]["opponents"]) == 2

    service.mark_sent(con, n["id"], now=T0 + 5)
    assert n["id"] not in [x["id"] for x in service.pending_notifications(con)]
    bad = pending[1]
    for _ in range(service.MAX_NOTIFY_ATTEMPTS):
        service.mark_failed(con, bad["id"], "Cannot send messages to this user")
    assert bad["id"] not in [x["id"] for x in service.pending_notifications(con)]  # 放棄重試
    print("notification outbox OK")


def test_end_session():
    con, admin, p = setup(8, courts=1)
    join_all(con, p)
    m = match_of(con, p[0])
    all_ready(con, m["id"], T0 + 10)
    expect_error("matches_unfinished", service.end_session, con, admin, now=T0 + 20)
    service.end_session(con, admin, force=True, now=T0 + 20)
    assert con.execute("SELECT COUNT(*) FROM sessions WHERE is_open = 1").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM queue_entries WHERE status = 'waiting'").fetchone()[0] == 0
    assert not service.get_board(con)["session_open"]
    print("end session OK")


def test_board():
    con, admin, p = setup(10, courts=2)
    join_all(con, p)                       # 8 人上場、2 人排隊
    board = service.get_board(con, now=T0 + 60)
    assert board["court_count"] == 2 and len(board["courts"]) == 2
    assert all(c["match"]["status"] == "ready_check" for c in board["courts"])
    assert board["upcoming"] == [] and len(board["queue"]) == 2
    assert board["queue"][0]["waited"] >= 50
    names = {x["nickname"] for c in board["courts"] for x in c["match"]["team_a"] + c["match"]["team_b"]}
    assert len(names) == 8
    print("board OK")


def test_duo_partial_noshow():
    """雙排其中一人沒按準備：按了的那位保留原本順位，沒按的排到隊尾。"""
    con, admin, p = setup(6)
    service.join_queue(con, p[0], p[1], now=T0)        # 雙排 A
    service.join_queue(con, p[2], p[3], now=T0 + 1)    # 雙排 B → 成一場
    service.join_queue(con, p[4], now=T0 + 2)
    service.join_queue(con, p[5], now=T0 + 3)
    m = match_of(con, p[0])
    for pid in (p[0], p[2], p[3]):                     # p1 沒按
        service.ready(con, m["id"], pid, now=T0 + 10)
    service.tick(con, now=T0 + 200)

    waiting = {r["member1_id"]: r["joined_at"] for r in con.execute(
        "SELECT member1_id, joined_at FROM queue_entries WHERE status = 'waiting'")}
    # 新的一場已經由 p0、p2、p3、p4 組成（p0 保留了原本的順位），p1 排在 p5 之後
    new = match_of(con, p[0])
    assert new["id"] != m["id"]
    assert set(service._player_ids(con, new["id"])) == {p[0], p[2], p[3], p[4]}
    assert waiting[p[5]] < waiting[p[1]] and waiting[p[1]] == T0 + 200
    print("duo partial no-show OK")


def test_two_connections_share_file_db():
    """Bot 與網頁是兩個程序、兩條連線，共用同一個資料庫檔。"""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "club.db")
        con1 = db.connect(path)
        db.migrate(con1)
        assert con1.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        season = db.start_season(con1, "1151")
        admin = db.get_or_create_member(con1, "admin", "社長")
        db.join_season(con1, season, admin, role="president")
        ids = []
        for i in range(4):
            m = db.get_or_create_member(con1, f"d{i}", f"P{i}")
            db.join_season(con1, season, m)
            ids.append(m)

        con2 = db.connect(path)                         # 另一個程序的連線
        service.start_session(con2, admin, now=T0)      # 網頁開始社課
        for i, m in enumerate(ids):                     # Bot 那邊排隊
            service.join_queue(con1, m, now=T0 + i)
        # 網頁讀得到 Bot 寫入的結果
        board = service.get_board(con2, now=T0 + 10)
        assert board["courts"][0]["match"]["status"] == "ready_check"
        # 網頁強制結束比賽 → Bot 那邊能撈到通知
        mid = board["courts"][0]["match"]["id"]
        service.admin_cancel_match(con2, admin, mid, now=T0 + 20)
        assert "match_cancelled" in [n["kind"] for n in service.pending_notifications(con1)]
        con1.close()
        con2.close()
    print("two connections OK")


if __name__ == "__main__":
    test_full_flow()
    test_ready_timeout()
    test_upcoming_promoted_when_court_frees()
    test_admin_cancel_and_permissions()
    test_score_validation()
    test_dispute_and_admin_ruling()
    test_court_count_change()
    test_queue_rules()
    test_leave_upcoming_match()
    test_admin_remove_and_rating()
    test_notification_outbox()
    test_end_session()
    test_board()
    test_duo_partial_noshow()
    test_two_connections_share_file_db()
    print("\n全部通過")
