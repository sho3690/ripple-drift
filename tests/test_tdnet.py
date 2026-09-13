import io
import zipfile

from app.data import tdnet


ROW = ('<tr><td class="oddnew-L kjTime" noWrap>15:30</td><td class="oddnew-M kjCode" noWrap>31610</td>'
       '<td class="oddnew-M kjName" noWrap>アゼアス </td>'
       '<td class="oddnew-M kjTitle" align="left"><a href="140120260909533773.pdf" target="_blank">2027年4月期第1四半期決算短信〔日本基準〕（連結）</a></td>'
       '<td class="oddnew-M kjXbrl" noWrap align="center"><div class="xbrl-mask"><div class="xbrl-button"><a class="style002" href="081220260909533773.zip">XBRL</a></div></div></td>'
       '<td class="oddnew-M kjPlace" noWrap align="left">東 </td><td class="oddnew-R kjHistroy" align="left"> </td></tr>'
       '<tr><td class="kjTime">10:00</td><td class="kjCode">285A0</td><td class="kjName">キオクシア</td>'
       '<td class="kjTitle"><a href="x.pdf">業績予想の修正に関するお知らせ</a></td><td class="kjXbrl"> </td></tr>')


def test_parse_rows_and_filters():
    rows = tdnet.parse_rows("<table>" + ROW + "</table>")
    assert len(rows) == 2
    assert rows[0]["symbol"] == "3161.T" and rows[0]["xbrl"] == "081220260909533773.zip" and rows[0]["time"] == "15:30"
    assert tdnet.is_tanshin(rows[0]["title"]) and not tdnet.is_tanshin(rows[1]["title"])
    assert rows[1]["symbol"] == "285A.T"
    assert tdnet.is_tanshin("2026年3月期 決算短信〔日本基準〕（連結）") and not tdnet.is_tanshin("（訂正）2026年3月期 決算短信")


def test_extract_qualitative_from_zip():
    body = ("<html><head><style>p{font-size:9pt}</style></head><body><p>１．当四半期決算に関する定性的情報</p>"
            "<p>（１）経営成績に関する説明 当第1四半期は増収増益となり、過去最高益を更新しました。通期計画は確実に達成できる見込みです。</p>"
            + "<p>需要は堅調で受注も好調に推移しています。</p>" * 6 +
            "<p>（３）将来予測情報に関する説明 通期業績予想に変更はありません。</p>"
            "<p>２．四半期連結財務諸表及び主な注記</p><p>（１）四半期連結貸借対照表 1,234 5,678</p></body></html>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("XBRLData/Attachment/qualitative.htm", body)
        z.writestr("XBRLData/Summary/x-ixbrl.htm", "<p>サマリー</p>")
    text = tdnet.extract_qualitative(buf.getvalue())
    assert text.startswith("定性的情報") or "定性的情報" in text[:40]
    assert "過去最高益" in text and "font-size" not in text
    assert "貸借対照表" not in text
