"""訊息測試：python test_messages.py"""
import re
from pathlib import Path

import messages

SAMPLES = {
    "next_up": {"match_id": 1, "court": None, "teammates": ["Amy"], "opponents": ["Bob", "Cara"]},
    "your_turn": {"match_id": 1, "court": 3, "deadline": 1_000_180.0,
                  "teammates": ["Amy"], "opponents": ["Bob", "Cara"]},
    "match_start": {"match_id": 1, "court": 3, "teammates": ["Amy"], "opponents": ["Bob", "Cara"]},
    "no_show": {"match_id": 1},
    "match_cancelled": {"match_id": 1, "reason": "timeout", "requeued": True},
    "confirm_request": {"match_id": 1, "reported_by": "Bob", "their_score": 21, "your_score": 17},
    "result": {"match_id": 1, "won": True, "before": 1000, "after": 1027, "delta": 27,
               "your_score": 21, "their_score": 17},
    "result_disputed": {"match_id": 1},
    "admin_dispute": {"match_id": 7, "court": 2},
    "queue_left": {"reason": "left", "by": "Amy"},
}


def test_every_service_notification_has_a_template():
    """service.py 發出的每一種通知，這裡都必須有對應的訊息，否則 Bot 會當掉。"""
    source = Path(__file__).with_name("service.py").read_text(encoding="utf-8")
    used = set(re.findall(r'_notify\(\s*\w+,\s*[^,]+,\s*"(\w+)"', source))
    used |= set(re.findall(r'_notify_match\(\s*\w+,\s*[^,]+,\s*"(\w+)"', source))
    assert used, "沒有找到任何通知類型，這個測試的搜尋規則可能失效了"
    missing = used - set(messages.TEMPLATES)
    assert not missing, f"缺少訊息範本：{missing}"
    assert set(SAMPLES) == set(messages.TEMPLATES), "測試樣本要涵蓋所有範本"
    print(f"template coverage OK ({len(used)} kinds)")


def test_render_all_kinds_and_languages():
    for kind, payload in SAMPLES.items():
        en = messages.render(kind, payload, lang="en")
        zh = messages.render(kind, payload, lang="zh")
        both = messages.render(kind, payload)
        assert en and zh and en != zh
        assert both == f"{en}\n\n{zh}"
        assert not re.search(r"[一-鿿]", en), f"{kind} 的英文版混入了中文"
    print("render OK")


def test_content():
    en = messages.render("your_turn", SAMPLES["your_turn"], lang="en")
    assert "**Bob** & **Cara**" in en and "Court **3**" in en and "Amy" in en
    assert "<t:1000180:R>" in en
    zh = messages.render("your_turn", SAMPLES["your_turn"], lang="zh")
    assert "對你提出對決邀請" in zh and "3 號場" in zh
    # 勝負與積分變動符號
    r = messages.render("result", SAMPLES["result"], lang="en")
    assert "Victory" in r and "+27" in r and "1027" in r
    lost = dict(SAMPLES["result"], won=False, after=973, delta=-27)
    r = messages.render("result", lost, lang="zh")
    assert "敗北" in r and "-27" in r
    # 缺欄位不會當掉
    messages.render("your_turn", {}, lang="both")
    messages.render("match_cancelled", {"reason": "admin"}, lang="both")
    # 不認識的類型要明確報錯
    try:
        messages.render("nope", {})
    except KeyError:
        pass
    else:
        raise AssertionError("未知類型應該報錯")
    print("content OK")


def test_errors():
    """service.py 會丟出的每一種錯誤都要有中英訊息，而且英文版不能混入中文。"""
    source = Path(__file__).with_name("service.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'ServiceError\(\s*"(\w+)"', source))
    assert codes, "沒有找到任何錯誤代碼，這個測試的搜尋規則可能失效了"
    missing = codes - set(messages.ERRORS)
    assert not missing, f"缺少錯誤訊息：{missing}"
    params = {"nick": "Amy", "count": 2, "a": 21, "b": 20, "win": 21, "cap": 30}
    for code in codes:
        for p in (params, {}):
            en = messages.error_text(code, p, lang="en")
            zh = messages.error_text(code, p, lang="zh")
            assert en and zh
            assert not re.search(r"[一-鿿]", en), f"{code} 的英文版混入了中文"
            assert "None" not in en and "None" not in zh, f"{code} 缺少參數時顯示了 None"
    assert "Amy" in messages.error_text("report_first", {"nick": "Amy"}, lang="en")
    assert "21:20" in messages.error_text("bad_score", params, lang="zh")
    assert "Something went wrong" in messages.error_text("unknown_code", lang="en")
    print(f"errors OK ({len(codes)} codes)")


def test_service_error_is_bilingual():
    import service
    try:
        service.validate_score(21, 20)
    except service.ServiceError as e:
        assert e.code == "bad_score"
        assert "isn't a possible score" in e.text("en") and "不是合理的比分" in e.text("zh")
        assert "isn't a possible score" in e.message and "不是合理的比分" in e.message
    else:
        raise AssertionError("應該要出現錯誤")
    print("service error OK")


if __name__ == "__main__":
    test_every_service_notification_has_a_template()
    test_render_all_kinds_and_languages()
    test_content()
    test_errors()
    test_service_error_is_bilingual()
    print("\n全部通過")
