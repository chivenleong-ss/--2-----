"""
Flask web application for the Marketing Audit System.
"""
from __future__ import annotations

import functools
import gc
import json
import os
import pickle
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from correlation.chain_report import build_chain_payload
from models.business_analysis import BusinessHealthAnalyzer
from models.discrete_analysis import DiscreteAnalyzer
from utils.qcc_risk_screening import analyze_qcc_risk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

PROJECT_ROOT = Path(__file__).parent
OUTPUT_DIR = PROJECT_ROOT / "output"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
CACHE_PATH = OUTPUT_DIR / "model_outputs" / "_all_results.pkl"
PREFILTER_PATH = OUTPUT_DIR / "model_outputs" / "_prefilter_summary.json"
BUSINESS_PATH = OUTPUT_DIR / "model_outputs" / "_business_results.pkl"
DISCRETE_PATH = OUTPUT_DIR / "model_outputs" / "_discrete_results.pkl"
UPLOAD_MANIFEST_PATH = UPLOAD_DIR / "_upload_manifest.json"
REPORT_PATH = OUTPUT_DIR / "reports" / "市场营销综合审计报告.md"
MAP_PROVINCE_DIR = PROJECT_ROOT / "热点省地图(带底图)-预览"
MAP_NATIONAL_DIR = PROJECT_ROOT / "科技感中国地图" / "预览图"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "model_outputs").mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "reports").mkdir(parents=True, exist_ok=True)

pipeline_status = {"running": False, "progress": "", "error": None}
pipeline_lock = threading.Lock()
export_lock = threading.Lock()  # v2.10: 导出与数据刷新互斥锁


def _safe_console_text(text: str) -> str:
    """Make log lines safe for legacy Windows consoles such as GBK."""
    message = str(text)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        message.encode(encoding)
        return message
    except UnicodeEncodeError:
        return message.encode(encoding, errors="replace").decode(encoding, errors="replace")


def cleanup_after_request(func):
    """v2.10: 请求处理完毕后主动触发年轻代GC"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        gc.collect(0)
        return result
    return wrapper

DATA_SLOTS = {
    "bid_report": {
        "label": "中标报量",
        "desc": "中标报量导出数据文件",
        "required": True,
        "target": "中标报量导出数据文件-1780290929280.xlsx",
    },
    "dmp_sales": {
        "label": "签约报量",
        "desc": "签约报量导出数据文件",
        "required": True,
        "target": "签约报量（四局）_2026-06-01 13_14_55.xlsx",
    },
    "appendix": {
        "label": "审计附表",
        "desc": "补充DMP中缺少字段",
        "required": True,
        "target": "【附件5】附表：营销质量管理专项审计附表（定稿）(1).xlsx",
    },
    "qcc_risk": {
        "label": "企业信息（企查查）",
        "desc": "企查查导出的风险排查表",
        "required": False,
        "target": None,
    },
}

MODELS = {
    "1.1": {"name": "区域布局与区域合规性检测", "dim": "维度一：战略与布局"},
    "1.2": {"name": "业务结构战略性偏离检测", "dim": "维度一：战略与布局"},
    "1.3": {"name": "战略客户市场布局", "dim": "维度一：战略与布局"},
    "1.4": {"name": "营销统计数据多维交叉验真", "dim": "维度一：战略与布局"},
    "2.1": {"name": "风险分级严禁投标底线检测", "dim": "维度二：合同质量与风险"},
    "2.2": {"name": "盈利分析与履约异常检测", "dim": "维度二：合同质量与风险"},
    "2.3": {"name": "保证金与预收款资金安全监控", "dim": "维度二：合同质量与风险"},
    "2.4": {"name": "合同条款风险穿透", "dim": "维度二：合同质量与风险"},
    "2.5": {"name": "施工真实性验证", "dim": "维度二：合同质量与风险"},
    "3.1": {"name": "客户全生命周期监控", "dim": "维度三：客户健康度"},
    "3.2": {"name": "新客户质量评估与客户结构优化", "dim": "维度三：客户健康度"},
}


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def load_cached_results():
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "rb") as file:
            return pickle.load(file)
    return {}


def load_cached_results_for_export():
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "rb") as file:
            return pickle.load(file)
    return {}


def _load_pickle(path, default):
    try:
        if path.exists():
            with open(path, "rb") as file:
                return pickle.load(file)
    except Exception:
        pass
    return default


def load_prefilter_summary():
    if not PREFILTER_PATH.exists():
        return {}
    try:
        with open(PREFILTER_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_upload_manifest():
    if not UPLOAD_MANIFEST_PATH.exists():
        return {}
    try:
        with open(UPLOAD_MANIFEST_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_upload_manifest(manifest):
    with open(UPLOAD_MANIFEST_PATH, "w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)


def clear_uploaded_files():
    for pattern in ("*.xlsx", "*.xls", "*.csv", "*.txt"):
        for path in UPLOAD_DIR.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass
    if UPLOAD_MANIFEST_PATH.exists():
        try:
            UPLOAD_MANIFEST_PATH.unlink()
        except OSError:
            pass


def clear_runtime_outputs():
    for path in (CACHE_PATH, PREFILTER_PATH, BUSINESS_PATH, DISCRETE_PATH, REPORT_PATH):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def infer_slot_from_filename(filename: str) -> str | None:
    name = (filename or "").strip().lower()
    if not name:
        return None
    if "中标报量" in name:
        return "bid_report"
    if "签约报量" in name:
        return "dmp_sales"
    if any(keyword in name for keyword in ("附件5", "附表", "审计附表", "appendix")):
        return "appendix"
    if any(keyword in name for keyword in ("风险排查", "企查查", "qcc", "qichacha")):
        return "qcc_risk"
    if any(keyword in name for keyword in ("bid", "award", "中标", "投标")):
        return "bid_report"
    if any(keyword in name for keyword in ("sign", "sales", "签约")):
        return "dmp_sales"
    if "audit" in name and "table" in name:
        return "appendix"
    return None


def _save_uploaded_file(slot: str, storage):
    if slot not in DATA_SLOTS:
        return {"error": f"未知上传槽位: {slot}"}, 400
    if storage is None:
        return {"error": "未接收到上传文件"}, 400
    if storage.filename == "":
        return {"error": "文件名为空"}, 400
    if Path(storage.filename).name.startswith("~$"):
        return {"error": "请不要上传 Excel 临时锁文件（以 ~$ 开头）"}, 400

    ext = Path(storage.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv", ".txt"):
        return {"error": f"不支持的文件类型: {ext}"}, 400

    for other_ext in (".xlsx", ".xls", ".csv", ".txt"):
        old_path = UPLOAD_DIR / f"{slot}{other_ext}"
        if old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass

    save_path = UPLOAD_DIR / f"{slot}{ext}"
    storage.save(str(save_path))

    manifest = load_upload_manifest()
    manifest[slot] = {
        "original_filename": storage.filename,
        "saved_as": save_path.name,
        "size": save_path.stat().st_size,
        "mtime": datetime.fromtimestamp(save_path.stat().st_mtime).isoformat(),
    }
    save_upload_manifest(manifest)
    clear_runtime_outputs()

    return {
        "status": "ok",
        "slot": slot,
        "filename": storage.filename,
        "saved_as": save_path.name,
        "size": save_path.stat().st_size,
    }, 200


def required_uploads_ready():
    for key, info in DATA_SLOTS.items():
        if not info.get("required"):
            continue
        if not any((UPLOAD_DIR / f"{key}{ext}").exists() for ext in (".xlsx", ".xls", ".csv", ".txt")):
            return False
    return True


def _empty_stats():
    return {"total_issues": 0, "total_red": 0, "total_yellow": 0, "models": [], "top_categories": {}}


def get_model_stats(results_override=None):
    results = results_override if results_override is not None else load_cached_results()
    if not results:
        return _empty_stats()

    models_data = []
    total_issues = 0
    total_red = 0
    total_yellow = 0
    categories_count = {}

    for model_id in MODELS:
        df, summary = results.get(model_id, (None, {}))
        if df is None or len(df) == 0:
            models_data.append(
                {
                    "id": model_id,
                    "name": MODELS[model_id]["name"],
                    "total": 0,
                    "red": 0,
                    "yellow": 0,
                    "categories": {},
                    "summary": summary or {},
                }
            )
            continue

        level_col = "严重等级" if "严重等级" in df.columns else None
        cat_col = "问题分类" if "问题分类" in df.columns else None

        reds = int(len(df[df[level_col].astype(str).str.contains("red|严禁|重大", na=False)])) if level_col else 0
        yellows = int(len(df[df[level_col].astype(str).str.contains("yellow|限制|预警", na=False)])) if level_col else 0
        cats = df[cat_col].value_counts().to_dict() if cat_col else {}

        total_issues += len(df)
        total_red += reds
        total_yellow += yellows
        for cat, cnt in cats.items():
            categories_count[str(cat)] = categories_count.get(str(cat), 0) + int(cnt)

        models_data.append(
            {
                "id": model_id,
                "name": MODELS[model_id]["name"],
                "total": len(df),
                "red": reds,
                "yellow": yellows,
                "categories": {str(k): int(v) for k, v in cats.items()},
                "summary": summary or {},
            }
        )

    return {
        "total_issues": total_issues,
        "total_red": total_red,
        "total_yellow": total_yellow,
        "models": models_data,
        "top_categories": dict(sorted(categories_count.items(), key=lambda item: -item[1])[:10]),
    }


def get_chain_stats():
    results = load_cached_results()
    if not results:
        return {"summary": {"total_hits": 0, "chains_with_hits": 0, "max_risk_score": 0, "entity_count": 0}, "chains": []}
    return build_chain_payload(results, MODELS)


def get_business_stats():
    if BUSINESS_PATH.exists():
        with open(BUSINESS_PATH, "rb") as file:
            return pickle.load(file)

    results = load_cached_results()
    if not results:
        return {"summary": {"total_projects": 0, "total_contract_yi": 0.0}, "overview": {}, "subsidiaries": [], "cities": []}

    try:
        from data_loader import load_all_appendices, load_dmp
        from data_loader.data_adapter import build_unified_projects, load_bid_report

        dmp_raw = load_dmp()
        appendices = load_all_appendices()
        bid_report = load_bid_report()
        dmp = build_unified_projects(dmp_raw, appendices, bid_report)
        analyzer = BusinessHealthAnalyzer(_load_config())
        return analyzer.run(results, dmp)
    except Exception:
        return {"summary": {"total_projects": 0, "total_contract_yi": 0.0}, "overview": {}, "subsidiaries": [], "cities": []}


def get_discrete_stats():
    if DISCRETE_PATH.exists():
        with open(DISCRETE_PATH, "rb") as file:
            cached = pickle.load(file)
        grid = cached.get("grid_distribution", {}) if isinstance(cached, dict) else {}
        projects_cached = cached.get("projects") if isinstance(cached, dict) else None
        cities_cached = cached.get("cities") if isinstance(cached, dict) else None
        has_cached_data = bool(
            (hasattr(projects_cached, "__len__") and len(projects_cached) > 0)
            or (hasattr(cities_cached, "__len__") and len(cities_cached) > 0)
            or any(_to_float(value) > 0 for value in (grid or {}).values())
        )
        if has_cached_data:
            return cached

    results = load_cached_results()
    if not results:
        return {"summary": {"total_projects": 0}, "projects": [], "cities": [], "subsidiaries": [], "grid_distribution": {}}

    try:
        from data_loader import load_all_appendices, load_dmp
        from data_loader.data_adapter import build_unified_projects, load_bid_report

        config = _load_config()
        dmp_raw = load_dmp()
        appendices = load_all_appendices()
        bid_report = load_bid_report()
        dmp = build_unified_projects(dmp_raw, appendices, bid_report)
        appendix_df = appendices.get("appendix_1", None) if appendices else None
        analyzer = DiscreteAnalyzer(config)

        # v2.10: 优先使用六模块指标进行离散分析
        business_results = _load_pickle(BUSINESS_PATH, None)
        if business_results and isinstance(business_results, dict):
            try:
                discrete_results = analyzer.run_with_module_scores(
                    results, dmp, business_results, appendix_df
                )
            except Exception:
                discrete_results = analyzer.run(results, dmp, appendix_df)
        else:
            discrete_results = analyzer.run(results, dmp, appendix_df)
        grid = discrete_results.get("grid_distribution", {}) if isinstance(discrete_results, dict) else {}
        projects_value = discrete_results.get("projects") if isinstance(discrete_results, dict) else None
        cities_value = discrete_results.get("cities") if isinstance(discrete_results, dict) else None
        has_discrete_data = bool(
            (hasattr(projects_value, "__len__") and len(projects_value) > 0)
            or (hasattr(cities_value, "__len__") and len(cities_value) > 0)
            or any(_to_float(value) > 0 for value in (grid or {}).values())
        )
        if not has_discrete_data:
            discrete_results = build_discrete_from_model_results(results)
        with open(DISCRETE_PATH, "wb") as file:
            pickle.dump(discrete_results, file)
        return discrete_results
    except Exception as exc:
        print(f"[discrete] failed to build discrete stats: {exc}", flush=True)
        return build_discrete_from_model_results(results)


def _first_value(record, keys, default=""):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _infer_grid_key(r_score, e_score):
    r_bucket = 3 if r_score >= 2.34 else 2 if r_score >= 1.67 else 1
    e_bucket = 3 if e_score >= 2.34 else 2 if e_score >= 1.67 else 1
    return f"({r_bucket},{e_bucket})"


def _grid_strategy(grid_key):
    mapping = {
        "(1,1)": ("退出区", "主动退出或合并，释放资源"),
        "(1,2)": ("培育区", "资源倾斜，扩大市场份额"),
        "(1,3)": ("扩张区", "复制推广，设立区域中心"),
        "(2,1)": ("观察区", "收缩投入，限期改善"),
        "(2,2)": ("维持区", "常规管理，动态监控"),
        "(2,3)": ("优化区", "压缩风险敞口，推动合规化"),
        "(3,1)": ("淘汰区", "立即止损，启动问责"),
        "(3,2)": ("整顿区", "限期整改，回溯审批"),
        "(3,3)": ("警惕区", "专人周跟踪，一案一策"),
    }
    return mapping.get(grid_key, ("维持区", "常规管理，动态监控"))


def _project_key(record, fallback):
    return str(_first_value(record, ["项目编码", "项目编号", "项目代码", "project_code"], fallback))


def build_discrete_from_model_results(results: dict) -> dict:
    """Fallback discrete view from model hits, used when the analyzer cache is empty."""
    projects_by_key = {}

    for model_id, payload in (results or {}).items():
        df = payload[0] if isinstance(payload, (tuple, list)) and payload else None
        if df is None or not hasattr(df, "to_dict") or len(df) == 0:
            continue
        for idx, record in enumerate(df.fillna("").to_dict(orient="records")):
            key = _project_key(record, f"{model_id}-{idx}")
            item = projects_by_key.setdefault(
                key,
                {
                    "项目编码": key,
                    "项目名称": _first_value(record, ["项目名称", "工程名称", "project_name"], key),
                    "申报单位": _first_value(record, ["申报单位", "所属单位", "二级单位", "unit"], ""),
                    "项目城市": _first_value(record, ["项目城市", "城市", "所在地", "city"], ""),
                    "合同总额（亿元）": _to_float(_first_value(record, ["合同总额（亿元）", "合同额（亿元）", "签约额（亿元）", "合同额"], 0)),
                    "red_count": 0,
                    "yellow_count": 0,
                    "models": set(),
                },
            )
            item["models"].add(str(model_id))
            level = str(_first_value(record, ["严重等级", "风险等级", "level", "severity"], "")).lower()
            if "red" in level or "红" in level or "重大" in level:
                item["red_count"] += 1
            elif "yellow" in level or "黄" in level or "预警" in level:
                item["yellow_count"] += 1

    project_rows = []
    grid_distribution = {}
    city_map = {}
    sub_map = {}

    for item in projects_by_key.values():
        risk_score = min(3.0, 1.0 + item["red_count"] * 0.45 + item["yellow_count"] * 0.18)
        return_score = 2.0
        amount = _to_float(item.get("合同总额（亿元）"), 0.0)
        if amount >= 5:
            return_score = 3.0
        elif amount > 0 and amount < 1:
            return_score = 1.0
        grid_key = _infer_grid_key(risk_score, return_score)
        grid_name, action = _grid_strategy(grid_key)
        grid_distribution[grid_key] = grid_distribution.get(grid_key, 0) + 1

        row = {
            "项目编码": item["项目编码"],
            "项目名称": item["项目名称"],
            "申报单位": item["申报单位"],
            "项目城市": item["项目城市"],
            "合同总额（亿元）": round(amount, 4),
            "R_得分": round(risk_score, 2),
            "E_得分": round(return_score, 2),
            "九宫格": f"{grid_key} {grid_name}",
            "处置策略": action,
        }
        project_rows.append(row)

        city = row["项目城市"] or "未识别城市"
        city_item = city_map.setdefault(city, {"城市": city, "项目数": 0, "合同总额（亿元）": 0.0, "R总": 0.0, "E总": 0.0, "高风险": 0, "扩张": 0})
        city_item["项目数"] += 1
        city_item["合同总额（亿元）"] += amount
        city_item["R总"] += risk_score
        city_item["E总"] += return_score
        if grid_key.startswith("(3"):
            city_item["高风险"] += 1
        if grid_key in ("(1,2)", "(1,3)"):
            city_item["扩张"] += 1

        unit = row["申报单位"] or "未识别单位"
        sub_item = sub_map.setdefault(unit, {"申报单位": unit, "项目数": 0, "合同总额（亿元）": 0.0, "R总": 0.0, "E总": 0.0})
        sub_item["项目数"] += 1
        sub_item["合同总额（亿元）"] += amount
        sub_item["R总"] += risk_score
        sub_item["E总"] += return_score

    cities = []
    for item in city_map.values():
        count = max(1, item["项目数"])
        high_ratio = item["高风险"] / count
        expand_ratio = item["扩张"] / count
        tag = "高风险" if high_ratio > 0 else "预警关注" if expand_ratio == 0 else "稳健"
        cities.append(
            {
                "城市": item["城市"],
                "项目数": item["项目数"],
                "合同总额（亿元）": round(item["合同总额（亿元）"], 4),
                "平均R": round(item["R总"] / count, 2),
                "平均E": round(item["E总"] / count, 2),
                "淘汰整顿占比": f"{high_ratio * 100:.2f}%",
                "扩张培育占比": f"{expand_ratio * 100:.2f}%",
                "城市标签": tag,
            }
        )

    subsidiaries = []
    for item in sub_map.values():
        count = max(1, item["项目数"])
        subsidiaries.append(
            {
                "申报单位": item["申报单位"],
                "项目数": item["项目数"],
                "合同总额（亿元）": round(item["合同总额（亿元）"], 4),
                "平均R": round(item["R总"] / count, 2),
                "平均E": round(item["E总"] / count, 2),
                "单位类型": "高风险单位" if item["R总"] / count >= 2.34 else "常规单位",
            }
        )

    return {
        "summary": {
            "total_projects": len(project_rows),
            "total_contract_yi": round(sum(row["合同总额（亿元）"] for row in project_rows), 4),
            "high_risk_count": sum(1 for row in project_rows if _to_float(row["R_得分"]) >= 2.34),
        },
        "projects": project_rows,
        "cities": cities,
        "subsidiaries": subsidiaries,
        "grid_distribution": grid_distribution,
    }


def _load_config():
    config_path = PROJECT_ROOT / "config" / "rules.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as file:
            return json.load(file)
    return {}


def _discrete_to_json(discrete_results: dict) -> dict:
    out = {}
    for key in ["projects", "cities", "subsidiaries"]:
        value = discrete_results.get(key)
        if value is not None and hasattr(value, "to_dict"):
            out[key] = value.fillna("").to_dict(orient="records")
        elif isinstance(value, list):
            out[key] = value
        elif isinstance(value, tuple):
            out[key] = list(value)
        else:
            out[key] = []
    out["summary"] = discrete_results.get("summary", {})
    out["grid_distribution"] = discrete_results.get("grid_distribution", {})
    return out


def _tabular_to_json(results: dict) -> dict:
    out = {}
    for key in ["subsidiaries", "cities"]:
        df = results.get(key)
        if df is not None and hasattr(df, "to_dict"):
            out[key] = df.fillna("").to_dict(orient="records")
        else:
            out[key] = []
    out["summary"] = results.get("summary", {})
    out["overview"] = results.get("overview", {})
    out["trends"] = results.get("trends", [])
    out["focus_projects"] = results.get("focus_projects", [])
    out["recommendations"] = results.get("recommendations", [])
    return out


def _md_to_html(md_text: str) -> str:
    lines = md_text.split("\n")
    html = []
    in_table = False
    in_code = False

    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            html.append("</pre>" if not in_code else "<pre>")
            continue
        if in_code:
            html.append(line)
            continue

        if line.startswith("# "):
            html.append(f'<h1 class="text-2xl font-bold mt-6 mb-3">{line[2:]}</h1>')
        elif line.startswith("## "):
            html.append(f'<h2 class="text-xl font-bold mt-5 mb-2">{line[3:]}</h2>')
        elif line.startswith("### "):
            html.append(f'<h3 class="text-lg font-semibold mt-4 mb-2">{line[4:]}</h3>')
        elif "|" in line and line.strip().startswith("|"):
            if not in_table:
                html.append('<div class="overflow-x-auto"><table class="min-w-full border-collapse border border-gray-300 text-sm">')
                in_table = True
            cleaned = line.replace("|", "").replace("-", "").replace(" ", "").strip()
            if cleaned == "":
                continue
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            tag = "th" if in_table and html[-1].startswith("<div") else "td"
            html.append("<tr>" + "".join(f'<{tag} class="border border-gray-300 px-3 py-1">{cell}</{tag}>' for cell in cells) + "</tr>")
        else:
            if in_table:
                html.append("</table></div>")
                in_table = False
            html.append(f'<p class="my-1">{line}</p>' if line.strip() else "<br>")

    if in_table:
        html.append("</table></div>")
    return "\n".join(html)


def _stage_uploaded_files():
    copied = []
    for slot_key, info in DATA_SLOTS.items():
        target_name = info.get("target")
        if not target_name:
            continue
        src = None
        for ext in (".xlsx", ".xls", ".csv", ".txt"):
            path = UPLOAD_DIR / f"{slot_key}{ext}"
            if path.exists():
                src = path
                break
        if src:
            dst = PROJECT_ROOT / target_name
            try:
                shutil.copy2(str(src), str(dst))
            except PermissionError:
                if not dst.exists():
                    raise
                print(f"[UPLOAD] Target file is locked, keep existing staged file: {dst}", flush=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 32 or not dst.exists():
                    raise
                print(f"[UPLOAD] Target file is in use, keep existing staged file: {dst}", flush=True)
            copied.append(target_name)
    return copied


def _run_pipeline_subprocess(cmd, staged_count):
    global pipeline_status
    try:
        with pipeline_lock:
            pipeline_status["progress"] = f"已就位 {staged_count} 个文件，正在运行..." if staged_count else "未检测到上传文件，使用默认文件运行..."
            pipeline_status["started_at"] = datetime.now().isoformat()
            pipeline_status["started_at_timestamp"] = time.time()

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        output_lines = []
        last_update = time.time()
        line_count = 0

        if process.stdout is not None:
            for line in process.stdout:
                line_clean = line.rstrip()
                output_lines.append(line_clean)
                line_count += 1
                if line_clean.strip():
                    print(_safe_console_text(f"  [pipeline] {line_clean[:160]}"), flush=True)
                    now = time.time()
                    if now - last_update >= 0.2:
                        with pipeline_lock:
                            pipeline_status["progress"] = f"[{line_count} 行] {line_clean[:120]}"
                        last_update = now

        returncode = process.wait(timeout=1800)

        with pipeline_lock:
            if returncode == 0:
                pipeline_status["progress"] = "Complete"
                pipeline_status["output"] = "\n".join(output_lines[-50:]) if output_lines else ""
            else:
                pipeline_status["error"] = "\n".join(output_lines[-30:]) if output_lines else "Unknown error"
                pipeline_status["progress"] = "Error"
    except Exception as exc:
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 22:
            try:
                with pipeline_lock:
                    pipeline_status["progress"] = "实时日志通道异常，正在使用兼容模式运行..."
                command_line = subprocess.list2cmdline(cmd)
                completed = subprocess.run(
                    command_line,
                    cwd=str(PROJECT_ROOT),
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800,
                )
                output = (completed.stdout or "") + (completed.stderr or "")
                output_lines = output.splitlines()
                with pipeline_lock:
                    if completed.returncode == 0:
                        pipeline_status["error"] = None
                        pipeline_status["progress"] = "Complete"
                        pipeline_status["output"] = "\n".join(output_lines[-50:])
                    else:
                        pipeline_status["error"] = "\n".join(output_lines[-30:]) or str(exc)
                        pipeline_status["progress"] = "Error"
                return
            except Exception as fallback_exc:
                exc = fallback_exc
        with pipeline_lock:
            pipeline_status["error"] = str(exc)
            pipeline_status["progress"] = "Error"
    finally:
        with pipeline_lock:
            pipeline_status["running"] = False


def _mark_pipeline_error(exc):
    with pipeline_lock:
        pipeline_status["running"] = False
        pipeline_status["error"] = str(exc)
        pipeline_status["progress"] = "Error"


@app.route("/")
def dashboard():
    return render_template("dashboard.html", slots=DATA_SLOTS, models=MODELS)


@app.route("/issues")
def issues_page():
    return render_template("issues.html", stats=get_model_stats())


@app.route("/models")
def models_page():
    model_id = request.args.get("id", "")
    return render_template("models.html", stats=get_model_stats(), models=MODELS, selected_model=model_id)


@app.route("/chains")
def chains_page():
    return render_template("chains.html", chain_stats=get_chain_stats())


@app.route("/discrete")
def discrete_page():
    return render_template("discrete.html", discrete_stats=_discrete_to_json(get_discrete_stats()))


@app.route("/business")
def business_page():
    try:
        return render_template("business.html", business_stats=_tabular_to_json(get_business_stats()))
    except Exception:
        return render_template(
            "business.html",
            business_stats=_tabular_to_json(
                {
                    "summary": {"total_projects": 0, "total_contract_yi": 0.0},
                    "overview": {},
                    "subsidiaries": [],
                    "cities": [],
                    "trends": [],
                    "focus_projects": [],
                    "recommendations": [],
                }
            ),
        )


@app.route("/report")
def report_page():
    report_html = ""
    if REPORT_PATH.exists():
        with open(REPORT_PATH, "r", encoding="utf-8") as file:
            report_html = _md_to_html(file.read())
    return render_template("report.html", report_html=report_html)


@app.route("/api/stats")
def api_stats():
    return jsonify(get_model_stats())


@app.route("/api/prefilter")
def api_prefilter():
    return jsonify(load_prefilter_summary())


@app.route("/api/discrete")
def api_discrete():
    return jsonify(_discrete_to_json(get_discrete_stats()))


@app.route("/api/business")
def api_business():
    return jsonify(_tabular_to_json(get_business_stats()))


# ═══════════════════════════════════════════════════════════
# v2.10 新增API端点
# ═══════════════════════════════════════════════════════════

@app.route("/api/discrete/config")
def api_discrete_config():
    """返回完整的离散化规则配置，供前端What-If沙盘动态计算使用.

    前端所有What-If计算必须以此响应中的权重和阈值为唯一数据源，
    禁止在JavaScript中硬编码任何数值.
    """
    disc_path = PROJECT_ROOT / "config" / "discrete_rules.json"
    if not disc_path.exists():
        return jsonify({"error": "discrete_rules.json not found"}), 404

    try:
        with open(disc_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"Failed to load discrete_rules.json: {exc}"}), 500

    rules = raw.get("离散化分析", {})
    risk_cfg = rules.get("风险维度", {})
    return_cfg = rules.get("收益维度", {})
    module_mapping = rules.get("六模块指标映射", {})

    # 构建英文dim→权重的映射，供前端JS使用
    _risk_w = risk_cfg.get("权重", {})
    _return_w = return_cfg.get("权重", {})
    _dim_weight_map = {
        "region": _risk_w.get("区域合规", 1.0),
        "contract": _risk_w.get("合同底线", 1.0),
        "customer": _risk_w.get("客户健康", 0.8),
        "capital": _risk_w.get("资金安全", 0.8),
        "perf": _risk_w.get("履约真实", 0.4),
    }
    _edim_weight_map = {
        "profit": _return_w.get("盈利水平", 1.05),
        "conversion": _return_w.get("产值转化", 0.75),
        "collection": _return_w.get("资金回收", 0.75),
        "scale": _return_w.get("合同规模", 0.45),
    }

    return jsonify({
        "riskWeights": _risk_w,
        "returnWeights": _return_w,
        "riskWeightByDim": _dim_weight_map,
        "returnWeightByDim": _edim_weight_map,
        "riskMapping": module_mapping.get("风险维度", {}),
        "returnMapping": module_mapping.get("收益维度", {}),
        "gridStrategies": rules.get("九宫格处置策略", {}),
        "cutThresholds": {
            "riskLow": risk_cfg.get("分箱阈值", {}).get("低风险上限", 1.6),
            "riskHigh": risk_cfg.get("分箱阈值", {}).get("中风险上限", 2.4),
            "returnLow": return_cfg.get("分箱阈值", {}).get("低收益上限", 1.6),
            "returnHigh": return_cfg.get("分箱阈值", {}).get("中收益上限", 2.4),
        },
        "_meta": {
            "source": "config/discrete_rules.json",
            "message": "前端所有What-If计算必须以此响应中的权重和阈值为唯一数据源，禁止硬编码",
        },
    })


@app.route("/api/business/modules-summary")
def api_business_modules_summary():
    """首次快速返回：6个模块得分 + 综合评分（数据量<1KB，秒返）"""
    business = _load_pickle(BUSINESS_PATH, {})
    if not business or not isinstance(business, dict):
        return jsonify({"total_score": 0, "modules": {}, "score_band": {}})

    overview = business.get("overview", {})
    return jsonify({
        "total_score": overview.get("total_score", 0),
        "modules": overview.get("module_scores", {}),
        "score_band": overview.get("score_band", {}),
    })


@app.route("/api/business/module/<int:module_id>")
def api_business_module_detail(module_id):
    """按需返回单个模块的完整指标数据（id=1~6）"""
    if module_id < 1 or module_id > 6:
        return jsonify({"error": "module_id must be 1-6"}), 400

    business = _load_pickle(BUSINESS_PATH, {})
    if not business or not isinstance(business, dict):
        # v2.10: 缓存无数据时也下发 scope_name，供前端 Badge 展示
        return jsonify({"module_id": module_id, "module_name": f"模块{module_id}",
                        "score": 0, "scope_name": "暂无数据，运行分析后自动识别",
                        "metrics": {}, "top_subsidiaries": [], "top_cities": []})

    try:
        from models.business_analysis import _business_module_detail_stable
        result = _business_module_detail_stable(business, module_id)
        gc.collect(0)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "module_id": module_id}), 500


@app.route("/api/business/benchmark/<string:scope>")
def api_business_benchmark(scope):
    """按需返回对标数据（subsidiaries / cities / projects）"""
    if scope not in ("subsidiaries", "cities", "projects"):
        return jsonify({"error": "scope must be subsidiaries, cities, or projects"}), 400

    business = _load_pickle(BUSINESS_PATH, {})
    if not business or not isinstance(business, dict):
        return jsonify([])

    if scope == "projects":
        data = business.get("focus_projects", [])
    else:
        data = business.get(scope, [])
        if hasattr(data, "to_dict"):
            data = data.fillna("").to_dict(orient="records")
        elif not isinstance(data, list):
            data = []

    gc.collect(0)
    return jsonify(data)


@app.route("/api/memory-status")
def api_memory_status():
    """v2.10 运维端点：返回当前内存使用情况"""
    try:
        import psutil
        process = psutil.Process()
        mem_info = process.memory_info()
        return jsonify({
            "rss_mb": round(mem_info.rss / 1024 / 1024, 1),
            "vms_mb": round(mem_info.vms / 1024 / 1024, 1),
            "gc_counts": [list(gc.get_count())],
            "pipeline_running": pipeline_status.get("running", False),
        })
    except ImportError:
        return jsonify({
            "gc_counts": [list(gc.get_count())],
            "pipeline_running": pipeline_status.get("running", False),
            "note": "psutil not installed, memory details unavailable",
        })


@app.route("/api/business/config")
def api_business_config():
    """v2.10: 返回六模块经营健康度的全量阈值配置。

    前端 business.html / discrete.html 必须以此响应为唯一数据源，
    禁止在JS中硬编码任何阈值数字。
    """
    config = _load_config()
    bh = config.get("business_health", {})
    if not bh:
        return jsonify({"error": "business_health section not found in config/rules.json"}), 404

    return jsonify({
        "module_weights": bh.get("module_weights", {}),
        "score_bands": bh.get("score_bands", {}),
        "global_score_bands": bh.get("global_score_bands", {}),
        "confidence_levels": bh.get("confidence_levels", {}),
        "_meta": {
            "source": "config/rules.json → business_health",
            "message": "前端所有阈值判断必须以此响应为唯一数据源，禁止硬编码",
        },
    })


@app.route("/api/business/metrics-distribution")
def api_business_metrics_distribution():
    """v2.10: 返回六模块所有指标的分布统计，用于阈值校准.

    每项指标返回 P10/P25/P50/P75/P90，
    业务人员可据此校准 discrete_rules.json 中各维度的分箱阈值。
    """
    business = _load_pickle(BUSINESS_PATH, {})
    if not business or not isinstance(business, dict):
        return jsonify({"error": "No business results available"}), 404

    subsidiaries = business.get("subsidiaries", [])
    if hasattr(subsidiaries, "to_dict"):
        subsidiaries = subsidiaries.to_dict(orient="records")
    if not subsidiaries:
        return jsonify({"error": "No subsidiary data"}), 404

    import numpy as np

    # 收集六模块所有指标值
    metric_keys = [
        "区域渗透率","跨区域经营指数","深耕区域集中度","区域合同额强度","业务结构偏离度","EPC转型进度",
        "客户稳定性指数","客户产出波动率","客户集中度风险","中标转化率","新客户质量指数","战略客户产出比",
        "风险项目占比","风险合同额集中度","付款条件优良率","合同条款不利度","三证合规率",
        "产值转化率","签约履约偏差率","盈利健康度","停工退场率","效益偏差率","在施项目活跃度",
        "资金占用率","保证金周转天数","逾期回收率","预收款缺口率","负流项目占比","资金回收率",
        "数据完整率","流程合规率","中标签约偏差率","测算规律性指数","签约延迟率",
    ]

    distribution = {}
    for key in metric_keys:
        values = []
        for row in subsidiaries:
            v = row.get(key)
            if v is not None and v != "":
                try:
                    fv = float(v)
                    if fv > 1.5 and key not in ("保证金周转天数","区域合同额强度","综合得分"):
                        fv = fv / 100.0  # 百分比转0-1
                    if 0 <= fv <= 200:  # 合理范围
                        values.append(fv)
                except (ValueError, TypeError):
                    pass

        if len(values) >= 3:
            arr = np.array(values)
            distribution[key] = {
                "count": int(len(arr)),
                "min": round(float(arr.min()), 4),
                "p10": round(float(np.percentile(arr, 10)), 4),
                "p25": round(float(np.percentile(arr, 25)), 4),
                "p50": round(float(np.percentile(arr, 50)), 4),
                "p75": round(float(np.percentile(arr, 75)), 4),
                "p90": round(float(np.percentile(arr, 90)), 4),
                "max": round(float(arr.max()), 4),
                "mean": round(float(arr.mean()), 4),
                "std": round(float(arr.std()), 4),
                "_suggested_thresholds": {
                    "宽松(P25/P75)": [round(float(np.percentile(arr, 25)), 4),
                                    round(float(np.percentile(arr, 75)), 4)],
                    "均衡(P33/P67)": [round(float(np.percentile(arr, 33)), 4),
                                    round(float(np.percentile(arr, 67)), 4)],
                    "严格(P40/P60)": [round(float(np.percentile(arr, 40)), 4),
                                    round(float(np.percentile(arr, 60)), 4)],
                },
            }
        else:
            distribution[key] = {"count": len(values), "note": "样本不足，无法计算分布"}

    gc.collect(0)
    return jsonify({
        "source": "六模块指标体系",
        "sample_size": len(subsidiaries),
        "metrics": distribution,
        "_usage": "请根据实际业务分布校准 config/discrete_rules.json 中 六模块指标映射.[风险/收益]维度.[dim].分箱阈值",
    })


@app.route("/api/chains")
def api_chains():
    return jsonify(get_chain_stats())


@app.route("/api/model/<model_id>")
def api_model_detail(model_id):
    results = load_cached_results()
    if model_id not in results:
        return jsonify({"error": "Model not found"}), 404

    df, summary = results[model_id]
    if df is None or len(df) == 0:
        return jsonify({"rows": [], "columns": [], "summary": summary})

    rows = []
    for _, row in df.head(500).iterrows():
        record = {}
        for col in df.columns:
            value = row[col]
            if isinstance(value, float) and value != value:
                record[str(col)] = None
            elif hasattr(value, "item"):
                record[str(col)] = value.item()
            elif isinstance(value, datetime):
                record[str(col)] = value.isoformat()
            else:
                record[str(col)] = str(value) if value is not None else None
        rows.append(record)

    return jsonify({"columns": [str(c) for c in df.columns], "rows": rows, "summary": {str(k): v for k, v in summary.items()}})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    slot = request.form.get("slot", "")
    payload, status = _save_uploaded_file(slot, request.files.get("file"))
    return jsonify(payload), status


@app.route("/api/upload/batch-auto", methods=["POST"])
def api_upload_batch_auto():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "未接收到上传文件"}), 400

    assigned_slots = set()
    pending_unknown = []
    uploaded = []
    errors = []

    for storage in files:
        filename = storage.filename or ""
        inferred_slot = infer_slot_from_filename(filename)

        if not inferred_slot:
            pending_unknown.append(storage)
            continue
        if inferred_slot in assigned_slots:
            errors.append({"filename": filename, "error": f"与其他文件重复匹配到 {inferred_slot}"})
            continue

        payload, status = _save_uploaded_file(inferred_slot, storage)
        if status != 200:
            errors.append({"filename": filename, "error": payload.get("error", "上传失败")})
            continue

        assigned_slots.add(inferred_slot)
        uploaded.append(payload)

    remaining_slots = [slot for slot in DATA_SLOTS if slot not in assigned_slots]
    if pending_unknown and len(pending_unknown) == len(remaining_slots):
        for storage, inferred_slot in zip(pending_unknown, remaining_slots):
            filename = storage.filename or ""
            payload, status = _save_uploaded_file(inferred_slot, storage)
            if status != 200:
                errors.append({"filename": filename, "error": payload.get("error", "上传失败")})
                continue
            assigned_slots.add(inferred_slot)
            uploaded.append(payload)
    else:
        for storage in pending_unknown:
            errors.append({"filename": storage.filename or "", "error": "无法识别该文件应上传到哪个槽位"})

    response = {"uploaded": uploaded, "errors": errors}
    if uploaded and errors:
        return jsonify(response), 207
    if errors:
        return jsonify(response), 400
    return jsonify(response)


@app.route("/api/upload-batch", methods=["POST"])
def api_upload_batch_alias():
    return api_upload_batch_auto()


# ── 企查查风险排查 API ──────────────────────────────────────

QCC_UPLOAD_PATH = UPLOAD_DIR / "qcc_risk.xlsx"
QCC_OUTPUT_DIR = OUTPUT_DIR / "qcc_screening"


@app.route("/api/qcc/upload", methods=["POST"])
def api_qcc_upload():
    """上传企查查风险排查Excel文件"""
    storage = request.files.get("file")
    if not storage:
        return jsonify({"error": "未接收到上传文件"}), 400
    if storage.filename == "":
        return jsonify({"error": "文件名为空"}), 400
    ext = Path(storage.filename).suffix.lower()
    if ext not in (".xlsx", ".xls"):
        return jsonify({"error": f"不支持的文件类型: {ext}，请上传 .xlsx 文件"}), 400

    QCC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    storage.save(str(QCC_UPLOAD_PATH))

    return jsonify({
        "status": "ok",
        "filename": storage.filename,
        "size": QCC_UPLOAD_PATH.stat().st_size,
    })


@app.route("/api/qcc/analyze", methods=["POST"])
def api_qcc_analyze():
    """分析已上传的企查查风险排查表"""
    if not QCC_UPLOAD_PATH.exists():
        return jsonify({"error": "请先上传企查查风险排查表"}), 400

    try:
        QCC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        result = analyze_qcc_risk(str(QCC_UPLOAD_PATH), output_dir=str(QCC_OUTPUT_DIR))

        # 清理旧输出文件
        for old in QCC_OUTPUT_DIR.glob("风险排查_补充分析_*.xlsx"):
            if old.name != Path(result["output_path"]).name:
                try:
                    old.unlink()
                except OSError:
                    pass

        return jsonify({
            "status": "ok",
            "summary": result["summary"],
            "companies": result["companies"],
            "zhongjian_cases": result["zhongjian_cases"],
            "output_filename": Path(result["output_path"]).name,
        })
    except ValueError as exc:
        return jsonify({"error": f"文件格式异常：{exc}。请确认上传的是企查查标准风险排查表（含14个Sheet）。"}), 400
    except Exception as exc:
        return jsonify({"error": f"分析失败：{exc}"}), 500


@app.route("/api/qcc/download")
def api_qcc_download():
    """下载补充后的风险排查Excel"""
    filename = request.args.get("file", "")
    if filename:
        file_path = QCC_OUTPUT_DIR / filename
    else:
        files = sorted(QCC_OUTPUT_DIR.glob("风险排查_补充分析_*.xlsx"), key=os.path.getmtime, reverse=True)
        file_path = files[0] if files else None

    if not file_path or not file_path.exists():
        return jsonify({"error": "文件不存在，请先执行分析"}), 404

    return send_file(
        str(file_path),
        as_attachment=True,
        download_name=file_path.name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/uploaded-files")
def api_uploaded_files():
    manifest = load_upload_manifest()
    slots_status = {}
    for key, info in DATA_SLOTS.items():
        found = None
        for ext in (".xlsx", ".xls", ".csv", ".txt"):
            path = UPLOAD_DIR / f"{key}{ext}"
            if path.exists():
                manifest_entry = manifest.get(key, {})
                found = {
                    "filename": manifest_entry.get("original_filename") or path.name,
                    "saved_as": path.name,
                    "size": path.stat().st_size,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                }
                break
        slots_status[key] = {
            "label": info["label"],
            "desc": info["desc"],
            "required": info["required"],
            "uploaded": found is not None,
            "file": found,
        }
    return jsonify(slots_status)


@app.route("/api/upload/clear/<slot>", methods=["DELETE"])
def api_clear_slot(slot):
    if slot not in DATA_SLOTS:
        return jsonify({"error": f"未知上传槽位: {slot}"}), 400
    for ext in (".xlsx", ".xls", ".csv", ".txt"):
        path = UPLOAD_DIR / f"{slot}{ext}"
        if path.exists():
            path.unlink()
    manifest = load_upload_manifest()
    if slot in manifest:
        manifest.pop(slot, None)
        save_upload_manifest(manifest)
    clear_runtime_outputs()
    return jsonify({"status": "ok"})


@app.route("/api/reset-session", methods=["POST"])
def api_reset_session():
    with pipeline_lock:
        if pipeline_status["running"]:
            return jsonify({"error": "当前任务运行中，无法重置页面状态"}), 409
        pipeline_status.update(
            {
                "running": False,
                "progress": "idle",
                "error": None,
                "pid": None,
                "started_at": None,
                "started_at_timestamp": None,
                "last_mode": None,
            }
        )
    clear_uploaded_files()
    clear_runtime_outputs()
    return jsonify({"status": "ok"})


@app.route("/api/run-prefilter", methods=["POST"])
def api_run_prefilter():
    with pipeline_lock:
        if pipeline_status["running"]:
            return jsonify({"error": "Pipeline already running"}), 409

    if not required_uploads_ready():
        return jsonify({"error": "请先上传全部必传文件，再运行前置过滤层"}), 400

    with pipeline_lock:
        pipeline_status.clear()
        pipeline_status.update({
            "running": True,
            "progress": "Running pre-filter layer...",
            "error": None,
            "started_at": datetime.now().isoformat(),
            "started_at_timestamp": time.time(),
        })

    def _run():
        try:
            staged = _stage_uploaded_files()
            cmd = [sys.executable, str(PROJECT_ROOT / "main.py"), "--prefilter-only", "--output-dir", str(OUTPUT_DIR)]
            _run_pipeline_subprocess(cmd, len(staged))
        except Exception as exc:
            _mark_pipeline_error(exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "mode": "prefilter"})


@app.route("/api/run", methods=["POST"])
def api_run():
    with pipeline_lock:
        if pipeline_status["running"]:
            return jsonify({"error": "Pipeline already running"}), 409

    if not required_uploads_ready():
        return jsonify({"error": "请先上传全部必传文件"}), 400
    if not PREFILTER_PATH.exists():
        return jsonify({"error": "请先运行前置过滤层，确认过滤结果后再执行模型"}), 400

    data = request.get_json() or {}
    selected_models = data.get("models", [])
    if not selected_models:
        return jsonify({"error": "No models selected"}), 400

    invalid = [model for model in selected_models if model not in MODELS]
    if invalid:
        return jsonify({"error": f"Invalid models: {invalid}"}), 400

    with pipeline_lock:
        pipeline_status.clear()
        pipeline_status.update({
            "running": True,
            "progress": f"Running {len(selected_models)} models...",
            "error": None,
            "started_at": datetime.now().isoformat(),
            "started_at_timestamp": time.time(),
        })

    def _run():
        try:
            copied = _stage_uploaded_files()
            cmd = [sys.executable, str(PROJECT_ROOT / "main.py"), "--model", ",".join(selected_models), "--output-dir", str(OUTPUT_DIR)]
            _run_pipeline_subprocess(cmd, len(copied))
        except Exception as exc:
            _mark_pipeline_error(exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "models": selected_models})


@app.route("/api/status")
def api_status():
    with pipeline_lock:
        if pipeline_status.get("running") and not pipeline_status.get("started_at_timestamp"):
            pipeline_status.update({
                "running": False,
                "progress": "Error",
                "error": "运行状态异常：任务未能正常启动，已自动释放运行锁",
            })
        return jsonify(pipeline_status.copy())


@app.route("/api/runtime-check")
def api_runtime_check():
    return jsonify(
        {
            "project_root": str(PROJECT_ROOT),
            "uploads_ready": required_uploads_ready(),
            "cache_exists": CACHE_PATH.exists(),
            "discrete_exists": DISCRETE_PATH.exists(),
            "business_exists": BUSINESS_PATH.exists(),
        }
    )


@app.route("/api/log", methods=["POST"])
def api_log():
    data = request.get_json() or {}
    tag = data.get("tag", "FRONT")
    message = data.get("message", "")
    if message:
        print(f"[{tag}] {message}", flush=True)
    return jsonify({"status": "logged"})


@app.route("/api/test-progress")
def api_test_progress():
    with pipeline_lock:
        pipeline_status.clear()
        pipeline_status.update({"running": True, "progress": "[TEST] 开始测试进度显示...", "error": None})

    def _test_run():
        test_messages = [
            "[TEST] 正在加载 DMP 数据...",
            "[TEST] 已加载 150 个项目",
            "[TEST] 正在构建统一数据集...",
            "[TEST] 数据集包含 2500 条记录",
            "[TEST] 正在运行模型 2.1 ...",
            "[TEST] 模型 2.1 完成，发现 25 个问题",
            "[TEST] 正在运行模型 2.2 ...",
            "[TEST] 模型 2.2 完成，发现 18 个高风险",
            "[TEST] 正在运行模型 2.5 ...",
            "[TEST] 模型 2.5 完成，发现 42 个问题",
            "[TEST] 正在生成报告...",
            "[TEST] 测试完成",
        ]
        for message in test_messages:
            time.sleep(2)
            with pipeline_lock:
                pipeline_status["progress"] = message
        with pipeline_lock:
            pipeline_status["running"] = False
            pipeline_status["progress"] = "测试完成"

    threading.Thread(target=_test_run, daemon=True).start()
    return jsonify({"status": "test started"})


@app.route("/map-assets/<scope>/<path:filename>")
def map_asset(scope, filename):
    if scope == "province":
        base_dir = MAP_PROVINCE_DIR
    elif scope == "national":
        base_dir = MAP_NATIONAL_DIR
    else:
        abort(404)

    target = (base_dir / filename).resolve()
    try:
        target.relative_to(base_dir.resolve())
    except ValueError:
        abort(404)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_file(target)


@app.route("/export/report")
def export_report():
    if not REPORT_PATH.exists():
        return jsonify({"error": "报告文件不存在，请先运行模型生成报告"}), 404
    return send_file(
        REPORT_PATH,
        as_attachment=True,
        download_name="市场营销综合审计报告.md",
        mimetype="text/markdown; charset=utf-8",
    )


@app.route("/export/mobile")
def export_mobile():
    from export.mobile_exporter import build_mobile_html

    cached_results = load_cached_results_for_export()
    issues = get_model_stats(cached_results)
    discrete_raw = _load_pickle(DISCRETE_PATH, {"summary": {"total_projects": 0}})
    business_raw = _load_pickle(BUSINESS_PATH, {"summary": {"total_projects": 0}, "overview": {}})
    discrete = {"summary": discrete_raw.get("summary", {}) if isinstance(discrete_raw, dict) else {}}
    business = {
        "summary": business_raw.get("summary", {}) if isinstance(business_raw, dict) else {},
        "overview": business_raw.get("overview", {}) if isinstance(business_raw, dict) else {},
    }
    chains = build_chain_payload(cached_results, MODELS) if cached_results else {"summary": {}, "chains": []}

    if not issues.get("total_issues") and not discrete.get("summary", {}).get("total_projects"):
        return "<h3>暂无数据，请先上传文件并运行模型</h3><p><a href='/'>返回首页</a></p>", 404

    html = build_mobile_html(issues, discrete, business, chains)
    return Response(
        html,
        mimetype="text/html; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=营销审计结果_手机版.html",
            "Cache-Control": "no-store",
        },
    )


@app.route("/mobile-export")
def mobile_export_page():
    return render_template("mobile_export.html")


@app.route("/export/qrcode")
def export_qrcode():
    from export.mobile_exporter import build_qr_summary_html, generate_qr_image
    import base64

    cached_results = load_cached_results_for_export()
    issues = get_model_stats(cached_results)
    discrete_raw = _load_pickle(DISCRETE_PATH, {"summary": {"total_projects": 0}})
    business_raw = _load_pickle(BUSINESS_PATH, {"summary": {"total_projects": 0}})
    discrete = {"summary": discrete_raw.get("summary", {}) if isinstance(discrete_raw, dict) else {}}
    business = {"summary": business_raw.get("summary", {}) if isinstance(business_raw, dict) else {}}

    summary_html = build_qr_summary_html(issues, discrete, business)
    data_url = request.url_root.rstrip("/") + "/export/mobile"
    size_warning = False

    buf = generate_qr_image(data_url)
    if buf is None:
        qr_b64 = ""
        size_warning = True
    else:
        qr_b64 = base64.b64encode(buf.read()).decode("ascii")

    return render_template(
        "qrcode_export.html",
        qr_b64=qr_b64,
        data_url=data_url,
        size_warning=size_warning,
        data_size=len(data_url),
    )


if __name__ == "__main__":
    import sys as _sys
    print("=" * 60, flush=True)
    print("  全面数字化营销审计系统", flush=True)
    print("=" * 60, flush=True)
    print(f"  Project root : {PROJECT_ROOT}", flush=True)
    print(f"  Upload dir   : {UPLOAD_DIR}", flush=True)
    print(f"  Output dir   : {OUTPUT_DIR}", flush=True)
    print("-" * 60, flush=True)

    def open_browser():
        time.sleep(2)
        try:
            if _sys.platform == "win32":
                subprocess.Popen(["start", "http://localhost:5001"], shell=True)
            else:
                webbrowser.open("http://localhost:5001")
        except Exception as exc:
            print(f"Could not open browser: {exc}", flush=True)
            print("请手动打开 http://localhost:5001", flush=True)

    threading.Thread(target=open_browser, daemon=True).start()

    print("  服务启动中...", flush=True)
    print("  访问地址: http://localhost:5001", flush=True)
    print("=" * 60, flush=True)
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
