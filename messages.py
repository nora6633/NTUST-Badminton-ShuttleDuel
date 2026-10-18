"""ShuttleDuel — 玩家看到的訊息（English + 繁體中文）。

所有給球員看的文字都集中在這裡，Bot 只要呼叫 render() 就能取得訊息。
要改語氣、翻成其他語言，只需要改這個檔案。

    render("your_turn", payload)            # 英文 + 繁中（預設，外籍生與本地生都看得懂）
    render("your_turn", payload, lang="en") # 只要英文
    render("your_turn", payload, lang="zh") # 只要繁中

payload 就是 service.pending_notifications() 回傳的 payload。
訊息使用 Discord 的 Markdown，<t:時間戳:R> 會在 Discord 顯示成「3 分鐘後」這樣的倒數。
"""


def _names(names) -> str:
    return " & ".join(f"**{n}**" for n in names) if names else "—"


def _ts(payload) -> str:
    deadline = payload.get("deadline")
    return f"<t:{int(deadline)}:R>" if deadline else ""


def _court_en(p) -> str:
    return f"Court **{p['court']}**" if p.get("court") else "a court (to be announced)"


def _court_zh(p) -> str:
    return f"**{p['court']} 號場**" if p.get("court") else "場地待公布"


# ---------------------------------------------------------------- 各種通知

def _next_up(p):
    en = (f"🔔 You're up next!\n"
          f"Partner: {_names(p.get('teammates'))}\n"
          f"Opponents: {_names(p.get('opponents'))}\n"
          f"Stay close to the courts. We'll message you the moment a court opens.")
    zh = (f"🔔 你們是下一場！\n"
          f"你的隊友：{_names(p.get('teammates'))}\n"
          f"對手：{_names(p.get('opponents'))}\n"
          f"請留在球場附近，場地一空出來就會馬上通知你。")
    return en, zh


def _your_turn(p):
    en = (f"⚔️ {_names(p.get('opponents'))} challenge you to a duel!\n"
          f"Your partner: {_names(p.get('teammates'))}\n"
          f"Location: {_court_en(p)}\n"
          f"Press **Ready** before time runs out ({_ts(p)}), or your spot goes to the next team.")
    zh = (f"⚔️ {_names(p.get('opponents'))} 對你提出對決邀請！\n"
          f"你的隊友：{_names(p.get('teammates'))}\n"
          f"場地：{_court_zh(p)}\n"
          f"請在時限內按下**準備**（{_ts(p)} 截止），否則位置會讓給下一組。")
    return en, zh


def _match_start(p):
    en = (f"🏸 All four players are ready. The match is on at {_court_en(p)}!\n"
          f"When you finish, report the score. You must report before you can queue again.")
    zh = (f"🏸 四位球員都準備好了，{_court_zh(p)} 開打！\n"
          f"打完請回報比分，回報完才能再次排隊。")
    return en, zh


def _no_show(p):
    en = ("⏰ You didn't press Ready in time, so your spot went to someone else.\n"
          "You've been moved to the back of the queue.")
    zh = ("⏰ 你沒有及時按下準備，位置已讓給下一位。\n"
          "你已經被排到隊尾。")
    return en, zh


def _match_cancelled(p):
    reason = p.get("reason")
    kept = bool(p.get("requeued"))
    if reason == "timeout":
        en = ("⏰ Someone didn't press Ready in time, so this match was cancelled.\n"
              "You kept your place in the queue and will be rematched shortly.")
        zh = ("⏰ 有人沒有及時按下準備，這場比賽已取消。\n"
              "你保留了原本的排隊順位，很快會重新配對。")
    elif reason == "admin":
        en = "🛠️ An admin cancelled this match. No score was counted.\n"
        en += ("You kept your place in the queue." if kept
               else "Join the queue again whenever you're ready.")
        zh = "🛠️ 社長或管理員取消了這場比賽，不計分。\n"
        zh += "你保留了原本的排隊順位。" if kept else "準備好了再重新排隊就可以。"
    elif reason == "session_closed":
        en = "👋 Today's session has ended. This match was cancelled and no score was counted."
        zh = "👋 今天的社課結束了，這場比賽已取消，不計分。"
    else:  # player_left
        en = ("🚪 A player left, so the upcoming match was cancelled.\n"
              "You kept your place in the queue.")
        zh = ("🚪 有人退出，所以下一場預告已取消。\n"
              "你保留了原本的排隊順位。")
    return en, zh


def _confirm_request(p):
    your, their = p.get("your_score"), p.get("their_score")
    en = (f"📝 **{p.get('reported_by')}** reported the score: "
          f"**your team {your} – {their} opponents**.\n"
          f"Please confirm it, or dispute it if it's wrong.")
    zh = (f"📝 **{p.get('reported_by')}** 回報了比分："
          f"**你們 {your} – {their} 對手**。\n"
          f"正確請按確認，不對請按有爭議。")
    return en, zh


def _result(p):
    your, their = p.get("your_score"), p.get("their_score")
    before, after, delta = p.get("before"), p.get("after"), p.get("delta", 0)
    if p.get("won"):
        en = f"🏆 Victory! {your} – {their}\nRating {before} → **{after}** ({delta:+d})"
        zh = f"🏆 勝利！{your} – {their}\n積分 {before} → **{after}**（{delta:+d}）"
    else:
        en = f"😤 Defeat. {your} – {their}\nRating {before} → **{after}** ({delta:+d})\nRematch soon!"
        zh = f"😤 敗北。{your} – {their}\n積分 {before} → **{after}**（{delta:+d}）\n下一場再戰！"
    return en, zh


def _result_disputed(p):
    en = ("⚠️ The opponents didn't confirm the score, so the match is back to in-progress.\n"
          "Please report again, or ask an admin to decide.")
    zh = ("⚠️ 對方沒有確認這個比分，比賽回到進行中的狀態。\n"
          "請重新回報，或請社長裁決。")
    return en, zh


def _admin_dispute(p):
    court = p.get("court")
    en = (f"🛡️ Score dispute on match #{p.get('match_id')}"
          f"{f' (Court {court})' if court else ''}. Please review and make a ruling.")
    zh = (f"🛡️ 第 {p.get('match_id')} 場比賽的比分有爭議"
          f"{f'（{court} 號場）' if court else ''}，請查看並裁決。")
    return en, zh


def _queue_left(p):
    reason, by = p.get("reason"), p.get("by")
    if reason == "admin" and by:
        en = f"🛠️ An admin removed **{by}** from the queue, so your duo entry was cancelled."
        zh = f"🛠️ 社長或管理員把 **{by}** 移出了排隊，所以你們的雙排已取消。"
    elif reason == "admin":
        en = "🛠️ An admin removed you from the queue. Join again whenever you're ready."
        zh = "🛠️ 社長或管理員把你移出了排隊。準備好了可以重新排隊。"
    else:
        en = f"🚪 **{by}** left the queue, so your duo entry was cancelled. Join again to keep playing."
        zh = f"🚪 **{by}** 離開了排隊，所以你們的雙排已取消。要繼續打請重新排隊。"
    return en, zh


TEMPLATES = {
    "next_up": _next_up,
    "your_turn": _your_turn,
    "match_start": _match_start,
    "no_show": _no_show,
    "match_cancelled": _match_cancelled,
    "confirm_request": _confirm_request,
    "result": _result,
    "result_disputed": _result_disputed,
    "admin_dispute": _admin_dispute,
    "queue_left": _queue_left,
}


def render(kind: str, payload: dict, lang: str = "both") -> str:
    """把一則通知轉成要私訊的文字。lang: 'both'（預設）、'en'、'zh'。"""
    en, zh = TEMPLATES[kind](payload)
    if lang == "en":
        return en
    if lang == "zh":
        return zh
    return f"{en}\n\n{zh}"


# ---------------------------------------------------------------- 錯誤訊息（回覆給操作失敗的使用者）

def _who(p, default="That player"):
    """名字加粗；沒有名字時用通用稱呼，避免顯示 None。"""
    return f"**{p['nick']}**" if p.get("nick") else default


def _not_in_season(p):
    if p.get("nick"):
        n = _who(p)
        return (f"{n} isn't a member of this season yet. Ask the president to add them.",
                f"{n} 還不是本學期的成員，請社長先加入。")
    return ("That person isn't on this season's roster.", "這個人不在本學期名單裡。")


def _already_queued(p):
    if p.get("nick"):
        n = _who(p)
        return f"{n} is already in the queue.", f"{n} 已經在排隊了。"
    return "Already in the queue.", "已經在排隊了。"


def _bad_score(p):
    if p.get("a") is None:
        return "Scores must be whole numbers, 0 or higher.", "比分必須是不小於 0 的整數。"
    a, b, win, cap = p["a"], p["b"], p.get("win"), p.get("cap")
    return (f"{a}:{b} isn't a possible score ({win}-point games, win by 2, capped at {cap}).",
            f"{a}:{b} 不是合理的比分（{win} 分制，需領先 2 分，最多 {cap} 分）。")


ERRORS = {
    "no_season": lambda p: ("There's no active season yet. Ask the president to start a new season.",
                            "目前沒有進行中的學期，請社長先開新學期。"),
    "no_session": lambda p: ("Today's session hasn't started. Please wait for the president to open registration.",
                             "社課還沒開始，請等社長開放報名。"),
    "forbidden": lambda p: ("Only the president or an admin can do that.",
                            "只有社長或管理員可以這樣做。"),
    "no_match": lambda p: ("Match not found.", "找不到這場比賽。"),
    "session_open": lambda p: ("A session is already in progress.", "已經有進行中的社課了。"),
    "matches_unfinished": lambda p: (
        f"{p.get('count', '?')} match(es) still have no reported result. Finish them first, or force-end the session.",
        f"還有 {p.get('count', '?')} 場比賽沒回報結果，請先結束，或強制結束社課。"),
    "registration_closed": lambda p: ("Registration is closed right now.", "現在沒有開放報名。"),
    "bad_partner": lambda p: ("You can't team up with yourself.", "不能跟自己雙排。"),
    "not_in_season": _not_in_season,
    "report_first": lambda p: (
        f"{_who(p)} hasn't reported the last match's result yet. Report it before queueing again.",
        f"{_who(p, '這位球員')} 上一場還沒回報結果，要先回報才能再排隊。"),
    "already_matched": lambda p: (f"{_who(p)} is already lined up for a match.",
                                  f"{_who(p, '這位球員')} 已經排進比賽了。"),
    "already_queued": _already_queued,
    "in_match": lambda p: ("You're already in a match. Ask an admin if you need to drop out.",
                           "你已經在比賽流程中，要退出請聯絡社長。"),
    "not_player": lambda p: ("You're not in this match.", "你不在這場比賽裡。"),
    "expired": lambda p: ("Time's up. You missed the ready check and have been moved to a later match.",
                          "已經超過準備時間，你被排到下一場了。"),
    "bad_state": lambda p: ("This match isn't in a state where you can do that.",
                            "這場比賽目前的狀態不能執行這個動作。"),
    "not_playing": lambda p: ("This match can't be reported right now (it may already be reported, or hasn't started).",
                              "這場比賽現在不能回報（可能已回報或還沒開始）。"),
    "not_awaiting": lambda p: ("This match doesn't need confirmation right now.",
                               "這場比賽現在不需要確認。"),
    "not_opponent": lambda p: ("The score has to be confirmed by the other team.",
                               "要由對方隊伍確認比分。"),
    "bad_score": _bad_score,
    "bad_count": lambda p: ("The number of courts must be a whole number from 0 to 20.",
                            "場地數必須是 0 到 20 的整數。"),
    "not_queued": lambda p: ("That person isn't in the queue right now.",
                             "這個人目前不在排隊中。"),
}


def error_text(code: str, params: dict | None = None, lang: str = "both") -> str:
    """把 ServiceError 轉成要回覆給使用者的文字。lang: 'both'（預設）、'en'、'zh'。"""
    params = params or {}
    if code in ERRORS:
        en, zh = ERRORS[code](params)
    else:
        en, zh = f"Something went wrong ({code}).", f"發生錯誤（{code}）。"
    if lang == "en":
        return en
    if lang == "zh":
        return zh
    return f"{en}\n{zh}"
