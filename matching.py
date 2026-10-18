"""配對演算法。

佇列裡每個項目（Entry）是「單排的 1 人」或「雙排的 2 人」。
一場比賽 = 4 人 = 兩隊，雙排的兩人一定同隊。

規則：
1. 照報到順序，從等最久的人（錨點）開始找。
2. 只考慮「實力接近錨點」的人，接近的範圍會隨錨點等待時間放寬。
3. 在可行組合中，優先選排隊順序最前面的人（先來先上），再選兩隊最平衡的。
4. 錨點湊不到人時，換下一位當錨點，避免場地閒置。
"""

from dataclasses import dataclass, field
from itertools import combinations
import math

BASE_WINDOW = 150        # 一開始允許的分數差
WINDOW_PER_MIN = 50      # 每多等一分鐘放寬多少
UNLIMITED_AFTER_MIN = 10  # 等超過幾分鐘就不限制（保證不會永遠卡住）


@dataclass
class Entry:
    id: int
    players: tuple          # 球員 id，長度 1（單排）或 2（雙排）
    ratings: tuple          # 對應的分數
    joined_at: float        # 報到/進入佇列的時間（秒）

    @property
    def size(self) -> int:
        return len(self.players)

    @property
    def avg(self) -> float:
        return sum(self.ratings) / len(self.ratings)


@dataclass
class Match:
    team_a: list            # 球員 id
    team_b: list
    entries: list           # 用掉的 Entry id
    diff: float             # 兩隊平均分差


def window_for(wait_seconds: float) -> float:
    minutes = wait_seconds / 60
    if minutes >= UNLIMITED_AFTER_MIN:
        return math.inf
    return BASE_WINDOW + WINDOW_PER_MIN * minutes


def _best_split(entries: list[Entry]):
    """把 4 人的 entries 分成兩隊（整個 entry 不拆開），回傳 (diff, teamA, teamB)。"""
    best = None
    first = entries[0]  # 固定第一個 entry 在 A 隊，避免重複
    rest = entries[1:]
    for r in range(len(rest) + 1):
        for mates in combinations(rest, r):
            team_a = [first, *mates]
            if sum(e.size for e in team_a) != 2:
                continue
            team_b = [e for e in rest if e not in mates]
            ra = [x for e in team_a for x in e.ratings]
            rb = [x for e in team_b for x in e.ratings]
            diff = abs(sum(ra) / 2 - sum(rb) / 2)
            if best is None or diff < best[0]:
                best = (diff, team_a, team_b)
    return best


def find_match(queue: list[Entry], now: float) -> Match | None:
    """從佇列找出下一場比賽，找不到回傳 None。不會修改 queue。"""
    ordered = sorted(queue, key=lambda e: e.joined_at)
    position = {e.id: i for i, e in enumerate(ordered)}

    for anchor in ordered:
        win = window_for(now - anchor.joined_at)
        others = [
            e for e in ordered
            if e.id != anchor.id and abs(e.avg - anchor.avg) <= win
        ]
        best = None  # (排隊順序總和, 分差, 組合, 分隊)
        for k in (1, 2, 3):
            for combo in combinations(others, k):
                group = [anchor, *combo]
                if sum(e.size for e in group) != 4:
                    continue
                split = _best_split(group)
                if split is None:
                    continue
                diff, team_a, team_b = split
                if diff > win:
                    continue
                score = (sum(position[e.id] for e in group), diff)
                if best is None or score < best[0]:
                    best = (score, diff, team_a, team_b, group)
        if best:
            _, diff, team_a, team_b, group = best
            return Match(
                team_a=[p for e in team_a for p in e.players],
                team_b=[p for e in team_b for p in e.players],
                entries=[e.id for e in group],
                diff=diff,
            )
    return None
