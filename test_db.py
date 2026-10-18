"""資料庫與學期測試：python test_db.py"""
import sqlite3

import db


def fresh():
    con = db.connect()
    db.migrate(con)
    return con


def expect_fail(con, sql, params=()):
    try:
        with con:
            con.execute(sql, params)
    except sqlite3.IntegrityError:
        return
    raise AssertionError(f"應該要被擋下但成功了：{sql}")


def test_migrate_idempotent():
    con = db.connect()
    latest = 2
    assert db.migrate(con) == latest
    assert db.migrate(con) == latest  # 重複執行不出錯
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"members", "seasons", "season_members", "queue_entries",
            "matches", "match_players", "rating_history"} <= tables
    print("migrate OK")


def test_seasons():
    con = fresh()
    a = db.get_or_create_member(con, "111", "小明")
    b = db.get_or_create_member(con, "222", "阿華")
    assert db.get_or_create_member(con, "111", "小明") == a  # 不重複建立

    s1 = db.start_season(con, "1142")
    db.join_season(con, s1, a, rating=1300)
    db.join_season(con, s1, b)
    with con:
        con.execute("UPDATE season_members SET rating = 1350, games_played = 30 "
                    "WHERE season_id = ? AND member_id = ?", (s1, a))

    # 新學期：舊學期封存，同一人積分重新開始，舊紀錄還在
    s2 = db.start_season(con, "1151")
    assert db.get_active_season(con)["code"] == "1151"
    assert con.execute("SELECT ended_at FROM seasons WHERE id=?", (s1,)).fetchone()[0]
    db.join_season(con, s2, a)
    new = con.execute("SELECT rating, games_played FROM season_members "
                      "WHERE season_id=? AND member_id=?", (s2, a)).fetchone()
    old = con.execute("SELECT rating, games_played FROM season_members "
                      "WHERE season_id=? AND member_id=?", (s1, a)).fetchone()
    assert (new[0], new[1]) == (1000, 0) and (old[0], old[1]) == (1350, 30)
    # 沒加進新學期的人，新學期名單裡沒有他
    assert con.execute("SELECT COUNT(*) FROM season_members WHERE season_id=?", (s2,)).fetchone()[0] == 1
    # 學期代號不能重複
    expect_fail(con, "INSERT INTO seasons (code) VALUES ('1151')")
    # 只能有一個進行中的學期
    expect_fail(con, "INSERT INTO seasons (code, is_active) VALUES ('9999', 1)")
    print("seasons OK")


def _setup_session(con):
    ids = [db.get_or_create_member(con, str(i), f"p{i}") for i in range(1, 9)]
    season = db.start_season(con, "1151")
    for m in ids:
        db.join_season(con, season, m)
    with con:
        sid = con.execute("INSERT INTO sessions (season_id) VALUES (?)", (season,)).lastrowid
    return ids, season, sid


def test_queue_constraints():
    con = fresh()
    ids, _, sid = _setup_session(con)
    q = "INSERT INTO queue_entries (session_id, member1_id, member2_id, joined_at) VALUES (?,?,?,0)"
    with con:
        con.execute(q, (sid, ids[0], None))
        con.execute(q, (sid, ids[1], ids[2]))          # 雙排
    expect_fail(con, q, (sid, ids[0], None))           # 單排重複
    expect_fail(con, q, (sid, ids[3], ids[0]))         # 被雙排拉進來但已在佇列
    expect_fail(con, q, (sid, ids[2], None))           # 已在別人的雙排裡
    expect_fail(con, q, (sid, ids[4], ids[4]))         # 自己跟自己雙排
    # 離開佇列後可以再進
    with con:
        con.execute("UPDATE queue_entries SET status='cancelled' WHERE member1_id=?", (ids[0],))
        con.execute(q, (sid, ids[0], None))
    print("queue constraints OK")


def test_match_constraints():
    con = fresh()
    ids, _, sid = _setup_session(con)
    m = "INSERT INTO matches (session_id, court, status, created_at) VALUES (?,?,?,0)"
    mp = "INSERT INTO match_players (match_id, member_id, team) VALUES (?,?,?)"
    with con:
        m1 = con.execute(m, (sid, 1, "playing")).lastrowid
        for i, mid in enumerate(ids[:4]):
            con.execute(mp, (m1, mid, "A" if i < 2 else "B"))
    # 同一場地不能同時有兩場進行中
    expect_fail(con, m, (sid, 1, "ready_check"))
    # 另一個場地可以
    with con:
        m2 = con.execute(m, (sid, 2, "playing")).lastrowid
    # 打完（finished）後同場地可再開
    with con:
        con.execute("UPDATE matches SET status='finished' WHERE id=?", (m1,))
        con.execute(m, (sid, 1, "playing"))
    # 同一個人不能同時在兩場未結束的比賽
    with con:
        con.execute(mp, (m2, ids[4], "A"))
    with con:
        m3 = con.execute(m, (sid, None, "upcoming")).lastrowid
    expect_fail(con, mp, (m3, ids[4], "B"))
    # 上一場已 finished 的人可以進新比賽
    with con:
        con.execute(mp, (m3, ids[0], "A"))
    print("match constraints OK")


if __name__ == "__main__":
    test_migrate_idempotent()
    test_seasons()
    test_queue_constraints()
    test_match_constraints()
    print("\n全部通過")
