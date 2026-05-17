from pathlib import Path


def test_review_page_has_condition_slices_and_filters():
    html = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
    text = html.read_text(encoding="utf-8")
    assert "review-filters" in text
    assert "review-condition-slices" in text
    assert "review-performance" in text
    assert "review-correlation" in text
    assert "条件切片" in text
    assert "因子切片" not in text


def test_review_js_has_condition_renderers():
    js = Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js"
    text = js.read_text(encoding="utf-8")
    assert "renderReviewConditionSlices" in text
    assert "reviewFilterQueryString" in text
    assert "renderReviewPerformanceReview" in text
    assert "因子切片" not in text
