from app.core.review_analytics import build_score_pnl_correlation


def test_score_pnl_correlation_spearman_in_range():
    records = [
        {"snapshot": {"score": 90}, "roe": 0.2},
        {"snapshot": {"score": 80}, "roe": 0.1},
        {"snapshot": {"score": 70}, "roe": 0.05},
        {"snapshot": {"score": 60}, "roe": -0.01},
    ]
    out = build_score_pnl_correlation(records, [60, 80])
    assert out["pair_count"] == 4
    sp = out["spearman"]
    assert sp is not None
    assert -1.0 <= sp <= 1.0
    assert len(out["score_buckets"]) >= 1
