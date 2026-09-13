from app.models import screen


def _ring(score, **kw):
    return {"score": score, "available": True, **kw}


def test_plain_reasons_handles_missing_surprise_pct():
    a = _ring(90.0, event_date="2026-08-01", sue_rank=0.98, surprise_pct=None, days_since=10, freshness=0.8)
    b = {"score": 50.0, "available": False}
    c = {"score": 50.0, "available": False}
    comp = screen.composite(a, b, c)
    why, risks = screen.plain_reasons("X.T", a, b, c, comp)
    assert why and "アナリスト予想なし" in why[0]
    a_bad = _ring(10.0, event_date="2026-08-01", sue_rank=0.02, surprise_pct=None, days_since=10, freshness=0.8)
    _, risks = screen.plain_reasons("X.T", a_bad, b, c, screen.composite(a_bad, b, c))
    assert any("下回りました" in r for r in risks)


def test_composite_shrinks_with_fewer_signals():
    one = screen.composite(_ring(90.0), {"available": False}, {"available": False})
    three = screen.composite(_ring(90.0), _ring(90.0), _ring(90.0))
    assert one["score"] < three["score"] and three["all_closed"]
