import pandas as pd

from app import config
from app.data import edinet, transcripts


def test_extract_major_customers_from_synthetic_csv():
    # EDINET CSV の実形式: タブ区切り、表のセルは区切り無しで連結される
    block = ("3．主要な顧客ごとの情報（単位：百万円）顧客の名称又は氏名売上高関連するセグメント名"
             "トヨタ自動車㈱1,234,567日本本田技研工業㈱234,000日本Intel Corporation59,210検査・測定装置事業")
    lines = [
        '"jpcrp_cor:NetSalesSummaryOfBusinessResults"\t"売上高、経営指標等"\t"CurrentYearDuration"\t"当期"\t"その他"\t"期間"\t"JPY"\t"円"\t"2469134000000"',
        '"jpcrp_cor:NetSalesSummaryOfBusinessResults"\t"売上高、経営指標等"\t"CurrentYearDuration_NonConsolidatedMember"\t"当期"\t"その他"\t"期間"\t"JPY"\t"円"\t"1000000000000"',
        '"jpcrp_cor:InformationForEachOfMainCustomersTextBlock"\t"主要な顧客ごとの情報 [テキストブロック]"\t"Prior1YearDuration"\t"前期"\t"その他"\t"期間"\t"－"\t"－"\t"古い年度のデータ トヨタ自動車㈱999日本"',
        '"jpcrp_cor:InformationForEachOfMainCustomersTextBlock"\t"主要な顧客ごとの情報 [テキストブロック]"\t"CurrentYearDuration"\t"当期"\t"その他"\t"期間"\t"－"\t"－"\t"' + block + '"',
    ]
    out = edinet.extract_major_customers("\n".join(lines))
    syms = {c["customer_symbol"]: c for c in out}
    assert set(syms) == {"7203.T", "7267.T", "INTC"}
    assert abs(syms["7203.T"]["share"] - 1234567e6 / 2469134e6) < 1e-9   # 当期・連結売上高で割る
    assert abs(syms["7267.T"]["amount"] - 234000e6) < 1


def test_sec_code_and_alias():
    assert edinet.sec_code_to_symbol("69020") == "6902.T"
    assert edinet.resolve_customer("トヨタ自動車株式会社") == "7203.T"
    assert edinet.resolve_customer("Apple Inc.") == "AAPL"
    assert edinet.resolve_customer("存在しない商事") is None


def test_transcript_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRANSCRIPT_DIR", tmp_path)
    p = transcripts.save_transcript("6902", "2026-07-31", "増収増益で過去最高益を更新しました。通期計画は確実に達成できる見込みです。" * 2, kind="manual")
    assert p.exists()
    loaded = transcripts.load_transcripts()
    assert "6902.T" in loaded and loaded["6902.T"][0]["date"] == "2026-07-31"
    assert loaded["6902.T"][0]["kind"] == "manual"
