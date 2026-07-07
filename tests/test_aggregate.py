from sentiment_radar.pipeline.aggregate import aggregate_records, reach_factor

# 테스트용 config (settings.yaml 비의존, 결정적)
CFG = {
    "source_weights": {"report": 3.0, "news_kr": 2.0, "blog": 1.0, "youtube": 1.5},
    "source_groups": {
        "institutional": ["report", "news_kr", "news_global"],
        "retail": ["blog", "youtube", "reddit", "telegram"],
    },
    "aggregation": {
        "extreme_threshold": 0.75,
        "min_confidence": 0.0,
        "exclude_non_opinion": True,
    },
}


def rec(source_type, sentiment, confidence=1.0, reach=0.0, is_opinion=True):
    return {
        "source_type": source_type,
        "sentiment": sentiment,
        "confidence": confidence,
        "reach_score": reach,
        "is_opinion": is_opinion,
    }


def test_raw_percentages_and_nsi():
    recs = [
        rec("news_kr", "positive"),
        rec("news_kr", "positive"),
        rec("news_kr", "negative"),
        rec("news_kr", "neutral"),
    ]
    m = aggregate_records(recs, CFG)["all"]
    assert m.n_items == 4
    assert m.pct_pos_raw == 50.0
    assert m.pct_neg_raw == 25.0
    assert m.pct_neu_raw == 25.0
    assert m.nsi_raw == 25.0  # 50 - 25


def test_fact_report_excluded_from_aggregation():
    """사실보도(is_opinion=False)는 집계에서 제외 — '폭락했다' vs '더 떨어질 것'."""
    recs = [
        # "삼성전자 폭락했다" — 사실보도 → neutral, is_opinion=False → 제외
        rec("news_kr", "neutral", is_opinion=False),
        rec("news_kr", "neutral", is_opinion=False),
        # "반도체 더 떨어질 것" — 전망 → negative, is_opinion=True → 집계 포함
        rec("news_kr", "negative", is_opinion=True),
    ]
    m = aggregate_records(recs, CFG)["all"]
    assert m.n_items == 1                 # 사실보도 2건 제외, 전망 1건만
    assert m.pct_neg_raw == 100.0
    assert m.nsi_raw == -100.0


def test_weighted_nsi_reflects_source_weight():
    # 리포트(가중 3.0) 긍정 1건 vs 블로그(가중 1.0) 부정 1건
    recs = [rec("report", "positive"), rec("blog", "negative")]
    m = aggregate_records(recs, CFG)["all"]
    # raw 는 50/50 → NSI_raw 0, 가중은 리포트가 무거워 양(+)
    assert m.nsi_raw == 0.0
    assert m.nsi_wt > 0.0


def test_reach_factor_monotonic_and_floor():
    assert reach_factor(0) == 1.0          # reach 0 이어도 하한 1 (뉴스 배제 방지)
    assert reach_factor(1000) > reach_factor(10) > 1.0


def test_institutional_retail_divergence():
    recs = [
        rec("report", "positive"),    # 기관 긍정
        rec("news_kr", "positive"),   # 기관 긍정
        rec("blog", "negative"),      # 리테일 부정
        rec("youtube", "negative"),   # 리테일 부정
    ]
    out = aggregate_records(recs, CFG)
    assert out["institutional"].nsi_wt == 100.0
    assert out["retail"].nsi_wt == -100.0
    assert out["all"].divergence == 200.0  # 100 - (-100)


def test_extreme_flag_set_on_skew():
    recs = [rec("news_kr", "positive") for _ in range(9)] + [rec("news_kr", "neutral")]
    m = aggregate_records(recs, CFG)["all"]
    assert m.pct_pos_raw == 90.0
    assert m.extreme_flag == 1


def test_no_divergence_when_one_side_empty():
    recs = [rec("report", "positive"), rec("news_kr", "negative")]  # 기관만
    out = aggregate_records(recs, CFG)
    assert out["all"].divergence is None


def test_min_confidence_filter():
    cfg = {**CFG, "aggregation": {**CFG["aggregation"], "min_confidence": 0.6}}
    recs = [
        rec("news_kr", "positive", confidence=0.9),
        rec("news_kr", "negative", confidence=0.3),  # 필터됨
    ]
    m = aggregate_records(recs, cfg)["all"]
    assert m.n_items == 1
    assert m.pct_pos_raw == 100.0
