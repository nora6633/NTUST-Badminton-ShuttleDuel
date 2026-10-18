-- 002_service：比賽流程（service 層）需要的補充資料表

-- 取消原因：'timeout' 準備逾時 / 'admin' 社長取消 / 'session_closed' 社課結束 / 'player_left' 有人退出
ALTER TABLE matches ADD COLUMN cancel_reason TEXT;

-- 一場比賽是由哪些佇列項目（單排/雙排）組成的。
-- 逾時或取消時，要靠這張表把「沒問題的人」放回原本的排隊位置。
CREATE TABLE match_entries (
    match_id INTEGER NOT NULL REFERENCES matches(id),
    entry_id INTEGER NOT NULL REFERENCES queue_entries(id),
    PRIMARY KEY (match_id, entry_id)
);

-- 通知佇列：service 只負責寫入，Discord Bot 定時讀取並私訊，發完標記。
-- 這樣網頁後台觸發的動作（例如強制結束比賽）也能通知到人，Bot 重啟也不會漏。
CREATE TABLE notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER NOT NULL REFERENCES members(id),
    kind       TEXT NOT NULL,                  -- next_up / your_turn / match_start ...
    payload    TEXT NOT NULL DEFAULT '{}',     -- JSON
    created_at REAL NOT NULL,                  -- unix 秒
    sent_at    REAL,                           -- NULL = 還沒發送
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX idx_notifications_pending ON notifications(id) WHERE sent_at IS NULL;

-- 社長手動調整積分的紀錄（誰、何時、從多少改成多少）
CREATE TABLE rating_adjustments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    member_id  INTEGER NOT NULL REFERENCES members(id),
    before     INTEGER NOT NULL,
    after      INTEGER NOT NULL,
    by_member  INTEGER NOT NULL REFERENCES members(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
