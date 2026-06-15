"""
Mobile HTML export — builds a self-contained HTML file for phone viewing.

Transfer: 微信文件传输 (WeChat file transfer) → phone browser opens .html file.
QR code:  generates a minimal summary data-URL → scanned by phone camera.
"""
import base64
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import qrcode
from jinja2 import Environment, FileSystemLoader

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "templates"

_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


# ── Public API ────────────────────────────────────────────────────────

def build_mobile_html(issues_data: dict,
                      discrete_data: dict,
                      business_data: dict,
                      chains_data: dict | None = None) -> str:
    """Render the standalone mobile HTML page with all data embedded."""
    template = _jinja_env.get_template("mobile_export.html")
    return template.render(
        issues_data=json.dumps(issues_data, ensure_ascii=False, default=str),
        discrete_data=json.dumps(discrete_data, ensure_ascii=False, default=str),
        business_data=json.dumps(business_data, ensure_ascii=False, default=str),
        chains_data=json.dumps(chains_data or {}, ensure_ascii=False, default=str),
        export_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        export_time_iso=datetime.now().isoformat(),
    )


def build_qr_summary_html(issues_data: dict,
                          discrete_data: dict,
                          business_data: dict) -> str:
    """Build a minimal, compact HTML that fits in a QR code (~1 KB target)."""
    total_issues = issues_data.get("total_issues", 0)
    total_red = issues_data.get("total_red", 0)
    total_yellow = issues_data.get("total_yellow", 0)

    disc_summary = discrete_data.get("summary", {})
    total_projects = disc_summary.get("total_projects", 0)
    high_risk = disc_summary.get("high_risk_count", 0)

    biz_summary = business_data.get("summary", {})
    best_unit = biz_summary.get("best_unit", "")
    best_score = biz_summary.get("best_score", "")

    recs = business_data.get("recommendations", [])
    top_rec = ""
    for r in recs[:2]:
        txt = r.get("recommendation", "") if isinstance(r, dict) else str(r)
        if txt:
            top_rec += f"<li>{txt[:80]}</li>"

    return (
        "<!DOCTYPE html><html lang=zh><head><meta charset=UTF-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1.0'>"
        "<title>营销审计摘要</title>"
        "<style>"
        "*{margin:0;padding:0;box-sizing:border-box}"
        "body{font-family:PingFang SC,Microsoft YaHei,sans-serif;background:#f4f8fc;padding:12px;color:#1f2937;font-size:13px}"
        "h1{font-size:16px;color:#0e7ac0;text-align:center;margin-bottom:10px}"
        ".c{background:#fff;border-radius:10px;padding:10px 12px;margin-bottom:8px;box-shadow:0 1px 4px rgba(0,0,0,.04)}"
        ".r{display:flex;gap:6px;flex-wrap:wrap}"
        ".k{flex:1;text-align:center;padding:8px 4px;border-radius:8px;background:#f8fafc}"
        ".kv{font-size:20px;font-weight:800;color:#1f89df}"
        ".kl{font-size:10px;color:#70859a}"
        ".rr .kv{color:#dc2626}.yy .kv{color:#d97706}.gg .kv{color:#25915f}"
        "h2{font-size:14px;color:#24507b;margin:4px 0}"
        "li{margin:2px 0;font-size:11px}"
        ".ft{text-align:center;color:#8da0b5;font-size:10px;margin-top:10px}"
        "</style></head><body>"
        f"<h1>营销审计摘要</h1>"
        f"<div class=c><h2>问题看板</h2><div class=r>"
        f"<div class=k><div class=kv>{total_issues}</div><div class=kl>总问题数</div></div>"
        f"<div class='k rr'><div class=kv>{total_red}</div><div class=kl>红色风险</div></div>"
        f"<div class='k yy'><div class=kv>{total_yellow}</div><div class=kl>黄色风险</div></div>"
        f"</div></div>"
        f"<div class=c><h2>九宫格决策</h2><div class=r>"
        f"<div class=k><div class=kv>{total_projects}</div><div class=kl>总项目数</div></div>"
        f"<div class='k rr'><div class=kv>{high_risk}</div><div class=kl>高风险</div></div>"
        f"</div></div>"
        f"<div class=c><h2>经营健康度</h2><div class=r>"
        f"<div class='k gg'><div class=kv>{best_score}</div><div class=kl>最优单位</div></div>"
        f"<div class=k><div class=kv>{best_unit[:12] if best_unit else '-'}</div><div class=kl>{best_unit[:8] if best_unit else ''}</div></div>"
        f"</div></div>"
        + (f"<div class=c><h2>关键建议</h2><ul style=padding-left:16px>{top_rec}</ul></div>" if top_rec else "") +
        f"<div class=ft>导出 {datetime.now().strftime('%m-%d %H:%M')} · 扫码查看</div>"
        f"</body></html>"
    )


def build_qr_data_url(html: str) -> str:
    """Base64-encode HTML into a data: URL suitable for a QR code."""
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;charset=utf-8;base64,{b64}"


def generate_qr_image(data_url: str) -> BytesIO:
    """Generate a QR code PNG image for the given data URL.

    Returns a BytesIO buffer on success, or None if data is too large.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    try:
        qr.add_data(data_url)
        qr.make(fit=True)
    except (ValueError, TypeError):
        # Data too large for QR code — try with higher version explicitly
        try:
            qr.version = 40  # Max version
            qr.make(fit=False)
        except (ValueError, TypeError):
            return None  # Still too large

    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── Helpers ───────────────────────────────────────────────────────────
# (none currently needed)

