"""核心邏輯測試：python test_core.py"""
import random

from matching import Entry, find_match
from rating import calc_changes


def test_elo():
    # 實力相同：贏 +16 / 輸 -16（K=32）
    a, b = calc_changes([(1000, 20)] * 2, [(1000, 20)] * 2, a_won=True)
    assert a == [16, 16] and b == [-16, -16], (a, b)
    # 爆冷：弱隊贏強隊，加分比 16 多
    weak_win, strong_loss = calc_changes([(900, 20)] * 2, [(1300, 20)] * 2, a_won=True)
    assert weak_win[0] > 16 and strong_loss[0] < -16
    # 強隊贏弱隊：加得很少
    strong_win, _ = calc_changes([(1300, 20)] * 2, [(900, 20)] * 2, a_won=True)
    assert 0 < strong_win[0] < 8
    # 新手 K 較大
    newbie, _ = calc_changes([(1000, 0)] * 2, [(1000, 20)] * 2, a_won=True)
    assert newbie[0] == 24
    print("elo OK")


def test_basic_match():
    now = 1000.0
    q = [Entry(i, (i,), (1000,), now + i) for i in range(4)]
    m = find_match(q, now + 10)
    assert m and sorted(m.team_a + m.team_b) == [0, 1, 2, 3]
    # 不足 4 人
    assert find_match(q[:3], now + 10) is None
    # 雙排 + 雙排
    q = [Entry(1, (1, 2), (1000, 1000), now), Entry(2, (3, 4), (1020, 980), now + 1)]
    m = find_match(q, now + 5)
    assert m and set(m.team_a) in ({1, 2}, {3, 4})
    # 雙排 + 兩個單排：雙排不被拆開
    q = [Entry(1, (1, 2), (1000, 1000), now), Entry(2, (3,), (990,), now + 1),
         Entry(3, (4,), (1010,), now + 2)]
    m = find_match(q, now + 5)
    assert m and (set(m.team_a) == {1, 2} or set(m.team_b) == {1, 2})
    print("basic match OK")


def test_strong_vs_weak_blocked_then_relaxed():
    now = 0.0
    # 1 個校隊(1400) + 3 個菜鳥(800)：一開始差距太大不配，等久了才配
    q = [Entry(0, (0,), (1400,), now)] + [Entry(i, (i,), (800,), now) for i in (1, 2, 3)]
    assert find_match(q, now + 30) is None
    assert find_match(q, now + 11 * 60) is not None
    print("window relax OK")


def test_simulation():
    """60 人：6 校隊(1300) / 6 菜鳥(800) / 48 一般(約1000)，5 場地連續打。"""
    random.seed(42)
    ratings = [1300] * 6 + [800] * 6 + [int(random.gauss(1000, 60)) for _ in range(48)]
    queue, t = [], 0.0
    for pid, r in enumerate(ratings):  # 先全部報到
        queue.append(Entry(pid, (pid,), (r,), t))
        t += random.uniform(0, 5)

    diffs, played = [], {pid: 0 for pid in range(60)}
    now, courts_free = t, 5
    rounds = 0
    while rounds < 12:  # 模擬 12 輪（每輪 5 場）
        rounds += 1
        for _ in range(courts_free):
            m = find_match(queue, now)
            if not m:
                break
            diffs.append(m.diff)
            for pid in m.team_a + m.team_b:
                played[pid] += 1
            used = set(m.entries)
            queue = [e for e in queue if e.id not in used]
        now += 12 * 60  # 一場約 12 分鐘
        # 打完的人重新排隊
        done_ids = [pid for pid, n in played.items()
                    if all(e.id != pid for e in queue)]
        for pid in done_ids:
            queue.append(Entry(pid, (pid,), (ratings[pid],), now + random.uniform(0, 30)))
        queue = [e for e in {e.id: e for e in queue}.values()]

    avg_diff = sum(diffs) / len(diffs)
    print(f"場次 {len(diffs)}，兩隊平均分差：平均 {avg_diff:.0f}，最大 {max(diffs):.0f}")
    print(f"每人場次：最少 {min(played.values())}，最多 {max(played.values())}")
    assert min(played.values()) >= 1, "有人完全沒被配到"
    assert avg_diff < 100
    print("simulation OK")


if __name__ == "__main__":
    test_elo()
    test_basic_match()
    test_strong_vs_weak_blocked_then_relaxed()
    test_simulation()
    print("\n全部通過")
