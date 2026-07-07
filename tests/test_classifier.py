from sentiment_radar.llm.classifier import Classifier, parse_classification


# ---------- 방어적 파싱 ----------

def test_parse_plain_json():
    r = parse_classification(
        '{"sentiment":"positive","confidence":0.8,"one_line_summary":"좋음",'
        '"key_argument":"HBM 수요","time_horizon":"mid","is_opinion":true}'
    )
    assert r["sentiment"] == "positive"
    assert r["confidence"] == 0.8
    assert r["is_opinion"] is True


def test_parse_code_fence_and_prose():
    text = "여기 결과입니다:\n```json\n{\"sentiment\":\"negative\",\"confidence\":0.9}\n```\n감사합니다"
    r = parse_classification(text)
    assert r["sentiment"] == "negative"
    assert r["confidence"] == 0.9


def test_parse_percentage_confidence_clamped():
    r = parse_classification('{"sentiment":"neutral","confidence":80}')
    assert r["confidence"] == 0.8  # 80 -> 0.8


def test_parse_invalid_enum_defaults():
    r = parse_classification('{"sentiment":"매우긍정","time_horizon":"영원"}')
    assert r["sentiment"] == "neutral"       # 알 수 없는 값 -> neutral
    assert r["time_horizon"] == "unclear"


def test_parse_garbage_returns_none():
    assert parse_classification("응답 없음") is None
    assert parse_classification("") is None
    assert parse_classification(None) is None


def test_parse_is_opinion_string():
    r = parse_classification('{"sentiment":"negative","is_opinion":"false"}')
    assert r["is_opinion"] is False


# ---------- 재시도 로직 ----------

def test_classifier_retries_on_parse_failure():
    calls = {"n": 0}

    def flaky_complete(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            return "쓰레기 응답", 10, 5           # 1차 실패
        return '{"sentiment":"positive","confidence":0.7}', 10, 5  # 2차 성공

    clf = Classifier(model="gpt-5-nano", complete_fn=flaky_complete)
    parsed, pt, ct = clf.classify(
        theme_name="반도체", title="t", snippet="s", source_type="news_kr"
    )
    assert parsed["sentiment"] == "positive"
    assert calls["n"] == 2          # 재시도 1회
    assert pt == 20 and ct == 10    # 두 번의 토큰 합산


def test_classifier_gives_up_after_retries():
    def always_bad(system, user):
        return "nope", 3, 1

    clf = Classifier(model="gpt-5-nano", complete_fn=always_bad)
    parsed, pt, ct = clf.classify(
        theme_name="반도체", title="t", snippet="s", source_type="news_kr"
    )
    assert parsed is None
    assert pt > 0  # 실패해도 비용(토큰)은 집계됨
