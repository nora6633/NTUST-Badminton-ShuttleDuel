-- 001_init：羽球社配對系統初始資料表
-- 設計重點：
--   * 社員身分（members）與「某學期的積分」（season_members）分開，
--     每學期換血時積分重新開始，舊學期資料完整保留。
--   * 用內部 id 當主鍵，discord_id 只是可替換的欄位（以後換平台不用改關聯）。
--   * 重要的「不可能狀態」直接用資料庫擋住，不靠程式碼小心。

-- 社員（跨學期的身分）
CREATE TABLE members (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id TEXT UNIQUE,                 -- 可為空；換平台時只改這欄
    nickname   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 學期。code 是社團自訂的代號，例如 1151（民國115年第1學期）
CREATE TABLE seasons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    is_active  INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at   TEXT
);
-- 同一時間只能有一個進行中的學期
CREATE UNIQUE INDEX one_active_season ON seasons(is_active) WHERE is_active = 1;

-- 某學期的成員：積分、場數、身分都在這裡，換學期就是一批新的列
CREATE TABLE season_members (
    season_id    INTEGER NOT NULL REFERENCES seasons(id),
    member_id    INTEGER NOT NULL REFERENCES members(id),
    rating       INTEGER NOT NULL DEFAULT 1000,
    games_played INTEGER NOT NULL DEFAULT 0,
    role         TEXT NOT NULL DEFAULT 'member'
                 CHECK (role IN ('member', 'admin', 'president')),
    joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (season_id, member_id)
);

-- 全域設定（只有一列）：報名是否開放、可用場地數
CREATE TABLE settings (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    registration_open INTEGER NOT NULL DEFAULT 0 CHECK (registration_open IN (0, 1)),
    court_count       INTEGER NOT NULL DEFAULT 5 CHECK (court_count >= 0)
);
INSERT INTO settings (id) VALUES (1);

-- 每次社課一個 session（社長按「開始報名」時建立）
CREATE TABLE sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    is_open    INTEGER NOT NULL DEFAULT 1 CHECK (is_open IN (0, 1)),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at   TEXT
);
CREATE UNIQUE INDEX one_open_session ON sessions(is_open) WHERE is_open = 1;

-- 佇列：單排 1 人、雙排 2 人，一個 entry 一列
CREATE TABLE queue_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    member1_id INTEGER NOT NULL REFERENCES members(id),
    member2_id INTEGER REFERENCES members(id),   -- NULL = 單排
    joined_at  REAL NOT NULL,                    -- unix 秒（UTC）
    status     TEXT NOT NULL DEFAULT 'waiting'
               CHECK (status IN ('waiting', 'matched', 'cancelled')),
    CHECK (member2_id IS NULL OR member2_id <> member1_id)
);
CREATE INDEX idx_queue_status ON queue_entries(session_id, status);

-- 同一個人不能同時在佇列裡出現兩次（不論單排、雙排，不論是第一或第二位）
CREATE TRIGGER queue_no_double_join
BEFORE INSERT ON queue_entries
WHEN NEW.status = 'waiting' AND EXISTS (
    SELECT 1 FROM queue_entries q
    WHERE q.status = 'waiting'
      AND (q.member1_id IN (NEW.member1_id, IFNULL(NEW.member2_id, -2))
        OR IFNULL(q.member2_id, -1) IN (NEW.member1_id, IFNULL(NEW.member2_id, -2)))
)
BEGIN
    SELECT RAISE(ABORT, 'member already in queue');
END;

-- 比賽狀態：
--   upcoming 下一場預告（場地還在打，先通知準備）
--   ready_check 等待 4 人按準備（有 ready_deadline）
--   playing 比賽中
--   awaiting_confirm 已有人回報，等對方隊伍確認
--   finished 已確認，分數已更新
--   cancelled 管理員取消（不計分）
CREATE TABLE matches (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         INTEGER NOT NULL REFERENCES sessions(id),
    court              INTEGER,               -- 場地編號，預告時可為 NULL
    status             TEXT NOT NULL DEFAULT 'upcoming'
                       CHECK (status IN ('upcoming', 'ready_check', 'playing',
                                         'awaiting_confirm', 'finished', 'cancelled')),
    ready_deadline     REAL,                  -- 準備截止時間（unix 秒）
    score_a            INTEGER,
    score_b            INTEGER,
    reported_by_member INTEGER REFERENCES members(id),
    created_at         REAL NOT NULL,
    finished_at        REAL
);
CREATE INDEX idx_match_status ON matches(session_id, status);

-- 一個場地同一時間只能有一場進行中的比賽
CREATE UNIQUE INDEX one_active_match_per_court
    ON matches(session_id, court)
    WHERE court IS NOT NULL
      AND status IN ('ready_check', 'playing', 'awaiting_confirm');

CREATE TABLE match_players (
    match_id  INTEGER NOT NULL REFERENCES matches(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    team      TEXT NOT NULL CHECK (team IN ('A', 'B')),
    is_ready  INTEGER NOT NULL DEFAULT 0 CHECK (is_ready IN (0, 1)),
    PRIMARY KEY (match_id, member_id)
);

-- 同一個人不能同時在兩場未結束的比賽裡
CREATE TRIGGER player_one_active_match
BEFORE INSERT ON match_players
WHEN EXISTS (
    SELECT 1
    FROM match_players mp JOIN matches m ON m.id = mp.match_id
    WHERE mp.member_id = NEW.member_id
      AND mp.match_id <> NEW.match_id
      AND m.status IN ('upcoming', 'ready_check', 'playing', 'awaiting_confirm')
)
BEGIN
    SELECT RAISE(ABORT, 'member already in an active match');
END;

-- 分數紀錄，方便查歷史、出錯時回溯或整學期重算
CREATE TABLE rating_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    match_id   INTEGER NOT NULL REFERENCES matches(id),
    member_id  INTEGER NOT NULL REFERENCES members(id),
    before     INTEGER NOT NULL,
    after      INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
