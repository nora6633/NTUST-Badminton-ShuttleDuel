"""資料庫連線、遷移、學期管理。

遷移規則：migrations/ 資料夾裡的 NNN_名稱.sql 依編號依序執行，
已執行過的版本記在 SQLite 的 PRAGMA user_version，所以重複執行是安全的。
要改資料表時，新增下一個編號的檔案，不要修改舊檔案。
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")  # 另一個程序在寫入時，最多等 5 秒
    if path != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")  # Bot 與網頁可同時讀寫
    return con


@contextmanager
def transaction(con: sqlite3.Connection):
    """一個完整的交易：成功就 commit，出任何錯誤就整個還原。

    用 BEGIN IMMEDIATE 先搶到寫入權，避免 Bot 與網頁同時操作時互相覆蓋。
    如果已經在交易裡（巢狀呼叫），就直接併入外層交易。
    """
    if con.in_transaction:
        yield
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.rollback()
        raise
    else:
        con.commit()


def migrate(con: sqlite3.Connection) -> int:
    """套用尚未執行的遷移，回傳目前版本。每個遷移是一個交易，失敗會整個還原。"""
    current = con.execute("PRAGMA user_version").fetchone()[0]
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:3])
        if version <= current:
            continue
        script = path.read_text(encoding="utf-8")
        con.executescript(
            f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
        )
        current = version
    return current


# ---- 社員與學期 ----

def get_or_create_member(con: sqlite3.Connection, discord_id: str, nickname: str) -> int:
    row = con.execute("SELECT id FROM members WHERE discord_id = ?", (discord_id,)).fetchone()
    if row:
        return row["id"]
    with con:
        cur = con.execute(
            "INSERT INTO members (discord_id, nickname) VALUES (?, ?)", (discord_id, nickname)
        )
    return cur.lastrowid


def start_season(con: sqlite3.Connection, code: str) -> int:
    """開新學期（例如 '1151'）。舊學期自動封存、未結束的社課一併結束。
    舊學期的成員、積分、比賽紀錄全部保留，新學期從空白名單開始。"""
    with con:
        con.execute(
            "UPDATE sessions SET is_open = 0, ended_at = datetime('now') WHERE is_open = 1"
        )
        con.execute(
            "UPDATE seasons SET is_active = 0, ended_at = datetime('now') WHERE is_active = 1"
        )
        cur = con.execute("INSERT INTO seasons (code, is_active) VALUES (?, 1)", (code,))
    return cur.lastrowid


def get_active_season(con: sqlite3.Connection):
    return con.execute("SELECT * FROM seasons WHERE is_active = 1").fetchone()


def join_season(
    con: sqlite3.Connection,
    season_id: int,
    member_id: int,
    rating: int = 1000,
    role: str = "member",
) -> None:
    """把社員加進某學期。已經在裡面就忽略。
    社長可以用 rating 參數一開始就把校隊設 1300、菜鳥設 800。"""
    with con:
        con.execute(
            "INSERT OR IGNORE INTO season_members (season_id, member_id, rating, role) "
            "VALUES (?, ?, ?, ?)",
            (season_id, member_id, rating, role),
        )
