"""Elo 積分計算（雙打）。

隊伍分數 = 兩人平均。每位球員用自己的 K 值更新：
新手（前 NEW_GAMES 場）K 較大，分數收斂比較快。
"""

DEFAULT_RATING = 1000
K_NEW = 48
K_NORMAL = 32
NEW_GAMES = 10
MAX_MARGIN_DIFF = 15  # 分差超過 15 分不再加成


def expected_score(team_rating: float, opp_rating: float) -> float:
    """隊伍預期勝率 (0~1)。"""
    return 1 / (1 + 10 ** ((opp_rating - team_rating) / 400))


def k_factor(games_played: int) -> int:
    return K_NEW if games_played < NEW_GAMES else K_NORMAL


def margin_multiplier(score_diff: int | None) -> float:
    """比分差加成：1.0（險勝）~ 1.5（大勝）。沒有比分就不加成。"""
    if not score_diff:
        return 1.0
    return 1 + 0.5 * min(abs(score_diff), MAX_MARGIN_DIFF) / MAX_MARGIN_DIFF


def calc_changes(
    team_a: list[tuple[float, int]],
    team_b: list[tuple[float, int]],
    a_won: bool,
    score_diff: int | None = None,
) -> tuple[list[int], list[int]]:
    """計算每位球員的分數變動。

    team_a / team_b: [(rating, games_played), ...]
    回傳 (A 隊各人變動, B 隊各人變動)，贏的為正、輸的為負。
    """
    avg_a = sum(r for r, _ in team_a) / len(team_a)
    avg_b = sum(r for r, _ in team_b) / len(team_b)
    exp_a = expected_score(avg_a, avg_b)
    exp_b = 1 - exp_a
    mult = margin_multiplier(score_diff)

    actual_a = 1.0 if a_won else 0.0
    actual_b = 1.0 - actual_a

    delta_a = [round(k_factor(g) * mult * (actual_a - exp_a)) for _, g in team_a]
    delta_b = [round(k_factor(g) * mult * (actual_b - exp_b)) for _, g in team_b]
    return delta_a, delta_b
