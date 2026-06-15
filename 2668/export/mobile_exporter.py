"""Mobile HTML and QR export helpers."""
import base64
from datetime import datetime
from html import escape
from io import BytesIO

import qrcode


def build_mobile_html(issues_data: dict, discrete_data: dict, business_data: dict, chains_data: dict | None = None) -> str:
    """Build a lightweight standalone mobile report."""
    chains_data = chains_data or {}
    issue_models = issues_data.get("models", [])[:8]
    top_categories = list((issues_data.get("top_categories") or {}).items())[:8]
    disc_summary = discrete_data.get("summary", {}) if isinstance(discrete_data, dict) else {}
    biz_summary = business_data.get("summary", {}) if isinstance(business_data, dict) else {}
    biz_overview = business_data.get("overview", {}) if isinstance(business_data, dict) else {}
    chain_summary = chains_data.get("summary", {}) if isinstance(chains_data, dict) else {}
    chain_rows = chains_data.get("chains", [])[:5] if isinstance(chains_data, dict) else []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    model_cards = "".join(
        f"<div class='row'><b>{escape(str(m.get('id', '')))} {escape(str(m.get('name', '')))}</b>"
        f"<span>总{int(m.get('total', 0) or 0)} / 红{int(m.get('red', 0) or 0)} / 黄{int(m.get('yellow', 0) or 0)}</span></div>"
        for m in issue_models if int(m.get("total", 0) or 0) > 0
    )
    category_items = "".join(f"<li>{escape(str(k))}：{int(v or 0)}项</li>" for k, v in top_categories)
    overview_items = "".join(f"<li>{escape(str(k))}：{escape(str(v))}%</li>" for k, v in list(biz_overview.items())[:6])
    chain_items = "".join(
        f"<li>{escape(str(c.get('title') or c.get('chain') or c.get('name') or '关联链'))}："
        f"{escape(str(c.get('hit_count') or c.get('total_correlations') or 0))}项</li>"
        for c in chain_rows
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>营销审计手机版结果</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef4f9;color:#1f3146;font-family:PingFang SC,Microsoft YaHei UI,Arial,sans-serif;font-size:14px;line-height:1.55}}
.page{{padding:14px;max-width:720px;margin:0 auto}}.hero{{padding:18px 16px;border-radius:18px;background:linear-gradient(135deg,#fff,#eaf5ff);box-shadow:0 12px 28px rgba(31,87,132,.12)}}
.kicker{{color:#2382d3;font-size:11px;font-weight:800;letter-spacing:.16em}}h1{{margin:8px 0 4px;font-size:22px;color:#12385f}}.sub{{margin:0;color:#6c8299;font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}}.kpi{{padding:12px 8px;border-radius:14px;background:#fff;text-align:center;box-shadow:0 8px 20px rgba(31,87,132,.07)}}
.kpi strong{{display:block;font-size:22px;color:#1f86d8}}.kpi span{{font-size:11px;color:#71859a}}.red strong{{color:#df435b}}.yellow strong{{color:#c88a13}}.green strong{{color:#24906f}}
.card{{margin-top:12px;padding:14px;border-radius:16px;background:#fff;box-shadow:0 8px 22px rgba(31,87,132,.08)}}h2{{margin:0 0 10px;font-size:16px;color:#173c62}}
.row{{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid #edf2f7}}.row:first-of-type{{border-top:0}}.row b{{font-size:13px}}.row span{{white-space:nowrap;color:#557089;font-size:12px}}
ul{{margin:0;padding-left:18px}}li{{margin:6px 0;color:#415870}}.tip{{padding:10px 12px;border-radius:12px;background:#eef7ff;color:#2a648f;font-size:12px}}.foot{{padding:16px 0 8px;text-align:center;color:#8aa0b5;font-size:11px}}
@media(max-width:420px){{.grid{{grid-template-columns:1fr 1fr}}.row{{display:block}}.row span{{display:block;margin-top:4px}}}}
</style>
</head>
<body>
<main class="page">
<section class="hero"><div class="kicker">MOBILE AUDIT REPORT</div><h1>营销审计手机版结果</h1><p class="sub">导出时间：{now}</p></section>
<section class="grid">
<div class="kpi"><strong>{int(issues_data.get('total_issues', 0) or 0)}</strong><span>发现问题总数</span></div>
<div class="kpi red"><strong>{int(issues_data.get('total_red', 0) or 0)}</strong><span>重大风险</span></div>
<div class="kpi yellow"><strong>{int(issues_data.get('total_yellow', 0) or 0)}</strong><span>预警关注</span></div>
<div class="kpi"><strong>{int(disc_summary.get('total_projects', 0) or 0)}</strong><span>九宫格项目</span></div>
<div class="kpi red"><strong>{int(disc_summary.get('high_risk_count', 0) or 0)}</strong><span>高风险项目</span></div>
<div class="kpi green"><strong>{escape(str(biz_summary.get('top_unit_score', biz_summary.get('best_score', '-'))))}</strong><span>最高健康分</span></div>
</section>
<section class="card"><h2>模型预警摘要</h2>{model_cards or '<div class="tip">暂无模型预警明细。</div>'}</section>
<section class="card"><h2>高频问题类别</h2><ul>{category_items or '<li>暂无高频问题。</li>'}</ul></section>
<section class="card"><h2>九宫格决策摘要</h2><div class="tip">淘汰/整顿区 {int(disc_summary.get('elimination_count', 0) or 0)} 项，扩张/培育区 {int(disc_summary.get('expansion_count', 0) or 0)} 项，合同额 {escape(str(disc_summary.get('total_contract_yi', 0)))} 亿元。</div></section>
<section class="card"><h2>经营健康度摘要</h2><div class="tip">覆盖 {int(biz_summary.get('covered_units', 0) or 0)} 个二级单位、{int(biz_summary.get('covered_cities', 0) or 0)} 个城市。最高得分单位：{escape(str(biz_summary.get('top_unit') or biz_summary.get('best_unit') or '-'))}</div><ul>{overview_items}</ul></section>
<section class="card"><h2>关联链摘要</h2><div class="tip">总命中 {int(chain_summary.get('total_hits', 0) or 0)} 项，命中链路 {int(chain_summary.get('chains_with_hits', 0) or 0)} 条。</div><ul>{chain_items or '<li>暂无关联链明细。</li>'}</ul></section>
<section class="card"><h2>处置建议</h2><ul><li>红色重大风险先控增量，再追溯审批依据。</li><li>黄色预警纳入整改台账，按月跟踪闭环。</li><li>多模型重复命中的项目优先开展穿透核查。</li></ul></section>
<div class="foot">全面数字化营销审计系统</div>
</main>
</body>
</html>"""


def build_qr_summary_html(issues_data: dict, discrete_data: dict, business_data: dict) -> str:
    total_issues = int(issues_data.get("total_issues", 0) or 0)
    total_red = int(issues_data.get("total_red", 0) or 0)
    total_yellow = int(issues_data.get("total_yellow", 0) or 0)
    disc_summary = discrete_data.get("summary", {}) if isinstance(discrete_data, dict) else {}
    biz_summary = business_data.get("summary", {}) if isinstance(business_data, dict) else {}
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>营销审计摘要</title><style>body{font-family:PingFang SC,Microsoft YaHei,sans-serif;background:#f4f8fc;padding:12px;color:#1f2937}"
        ".c{background:#fff;border-radius:12px;padding:12px;margin-bottom:10px}.n{font-size:22px;font-weight:800;color:#1f89df}</style></head><body>"
        f"<div class=c><b>问题摘要</b><p>总问题 <span class=n>{total_issues}</span>，红色 {total_red}，黄色 {total_yellow}</p></div>"
        f"<div class=c><b>九宫格</b><p>项目 {int(disc_summary.get('total_projects', 0) or 0)}，高风险 {int(disc_summary.get('high_risk_count', 0) or 0)}</p></div>"
        f"<div class=c><b>经营健康度</b><p>最高单位：{escape(str(biz_summary.get('top_unit') or '-'))}</p></div>"
        "</body></html>"
    )


def build_qr_data_url(html: str) -> str:
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;charset=utf-8;base64,{b64}"


def generate_qr_image(data_url: str) -> BytesIO | None:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=2)
    try:
        qr.add_data(data_url)
        qr.make(fit=True)
    except (ValueError, TypeError):
        return None
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
