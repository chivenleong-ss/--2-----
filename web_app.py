"""
Flask web application for the Marketing Audit System.
"""
from __future__ import annotations

import functools
import gc
import json
import os
import pickle
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import warnings
import pandas as pd
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file

# 屏蔽 openpyxl 读取无默认样式 Excel 的噪音警告
warnings.filterwarnings("ignore", message="Workbook contains no default style")

from correlation.chain_report import CHAIN_DEFINITIONS, build_chain_payload
from data_loader.admin_structure_loader import (
    ALL_CITY,
    ALL_DIRECT,
    ALL_SUB,
    clear_admin_structure_cache,
    detail_filter_names,
    get_admin_scope_options,
    is_all_city,
    is_all_direct,
    is_all_global,
    is_all_sub,
    resolve_unit_scope,
)
from models.business_analysis import BusinessHealthAnalyzer
from models.discrete_analysis import DiscreteAnalyzer
from utils.model_registry import (
    LEGACY_MODEL_REGISTRY,
    build_display_registry,
    display_to_legacy_id,
    legacy_to_display_id,
)
from utils.strategic_scope import detect_strategic_scope, summarize_strategic_scope
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

pipeline_status = {"running": False, "progress": "", "error": None, "recent_logs": [], "output": ""}
pipeline_lock = threading.Lock()
export_lock = threading.Lock()  # v2.10: 导出与数据刷新互斥锁
RUN_ACTION_TOKEN = secrets.token_urlsafe(32)

_BUSINESS_SOURCE_CACHE = {"signature": None, "df": None}
_LABELED_BUSINESS_CACHE = {"signature": None, "df": None}
_SCOPE_OPTIONS_CACHE = {"signature": None, "data": None}


def _latest_existing_path(primary: Path, pattern: str) -> Path:
    if primary.exists():
        return primary
    candidates = []
    try:
        candidates.extend(PROJECT_ROOT.glob(pattern))
    except OSError:
        pass
    files = [path for path in candidates if path.is_file()]
    if not files:
        return primary
    return max(files, key=lambda path: path.stat().st_mtime)


def _resolve_python_command() -> list[str]:
    """Return a stable Python command for child pipeline runs.

    In some Windows desktop sessions, ``sys.executable`` can become invalid
    after the terminal/session is restarted, which makes the web app able to
    serve requests but unable to launch ``main.py``. Prefer the current
    interpreter when it exists, then fall back to the known local install, and
    finally to the ``py`` launcher.
    """
    candidates = []

    if sys.executable:
        candidates.append(Path(sys.executable))

    candidates.append(Path(r"C:\Users\sasa\AppData\Local\Python\pythoncore-3.14-64\python.exe"))

    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.exists():
                return [str(candidate)]
        except OSError:
            continue

    return ["py", "-3"]


def _safe_console_text(text: str) -> str:
    """Make log lines safe for legacy Windows consoles such as GBK."""
    message = str(text)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        message.encode(encoding)
        return message
    except UnicodeEncodeError:
        return message.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _find_listening_pids(port: int) -> list[int]:
    """Return PIDs listening on the given TCP port."""
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return []

    pids = set()
    target_suffix = f":{port}"
    for raw_line in (completed.stdout or "").splitlines():
        line = raw_line.strip()
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state = parts[3]
        pid_text = parts[4]
        if state != "LISTENING" or not local_addr.endswith(target_suffix):
            continue
        try:
            pids.add(int(pid_text))
        except ValueError:
            continue
    return sorted(pids)


def _query_process_info(pid: int) -> dict:
    """Best-effort process metadata lookup for a PID."""
    try:
        command = (
            f"Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" | "
            "Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        text = (completed.stdout or "").strip()
        if not text:
            return {"pid": pid}
        payload = json.loads(text)
        return {
            "pid": int(payload.get("ProcessId", pid)),
            "name": str(payload.get("Name", "") or ""),
            "path": str(payload.get("ExecutablePath", "") or ""),
            "command_line": str(payload.get("CommandLine", "") or ""),
        }
    except Exception:
        return {"pid": pid}


def _is_same_project_web_process(proc_info: dict) -> bool:
    cmd = str(proc_info.get("command_line", "") or "").lower()
    root = str(PROJECT_ROOT).lower()
    return (
        "python" in str(proc_info.get("name", "")).lower()
        and "web_app.py" in cmd
        and root in cmd
    )


def _stop_processes(pids: list[int]) -> tuple[list[int], list[int]]:
    stopped, failed = [], []
    for pid in pids:
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if completed.returncode == 0:
                stopped.append(pid)
            else:
                failed.append(pid)
        except Exception:
            failed.append(pid)
    return stopped, failed


def _port_is_bindable(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _ensure_startup_port_ready(port: int = 5001) -> bool:
    """Check port occupancy before app.run and safely auto-clean stale same-app listeners."""
    if _port_is_bindable(port):
        return True

    pids = _find_listening_pids(port)
    infos = [_query_process_info(pid) for pid in pids]
    stale_same_app = [info["pid"] for info in infos if _is_same_project_web_process(info)]

    if stale_same_app:
        print(_safe_console_text(f"[startup] 检测到 {port} 端口被同项目残留 web_app 进程占用，尝试自动清理: {stale_same_app}"), flush=True)
        stopped, failed = _stop_processes(stale_same_app)
        if stopped:
            print(_safe_console_text(f"[startup] 已停止残留进程: {stopped}"), flush=True)
            time.sleep(1.0)
        if failed:
            print(_safe_console_text(f"[startup] 自动停止失败: {failed}"), flush=True)
        if _port_is_bindable(port):
            return True

    print("=" * 60, flush=True)
    print(_safe_console_text(f"[startup] 端口 {port} 已被占用，无法启动网页端。"), flush=True)
    if infos:
        print("[startup] 当前监听进程：", flush=True)
        for info in infos:
            desc = f"PID={info.get('pid')} name={info.get('name') or '?'}"
            cmd = str(info.get("command_line", "") or "").strip()
            if cmd:
                desc += f" cmd={cmd[:180]}"
            print(_safe_console_text(f"  - {desc}"), flush=True)
    print(_safe_console_text(f"[startup] 处理方式：先执行 `netstat -ano | findstr :{port}`"), flush=True)
    print(_safe_console_text(f"[startup] 然后执行 `Stop-Process -Id <PID1>,<PID2> -Force`"), flush=True)
    print(_safe_console_text(f"[startup] 清理完成后重新运行 `python web_app.py`"), flush=True)
    print("=" * 60, flush=True)
    return False


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
        "label": "企业信息",
        "desc": "企查查导出的风险排查表",
        "required": False,
        "target": None,
    },
    "admin_structure": {
        "label": "行政架构",
        "desc": "四局行政架构树文件",
        "required": False,
        "target": "行政架构-四局.xlsx",
    },
}

MODELS = {
    "1.1": {"name": "区域布局动态偏差检测与窜区预警（v4.0 +授权城市覆盖）", "dim": "维度一：战略与布局"},
    "1.2": {"name": "业务结构战略性偏离检测（v4.0 城市更新+新兴业务占比<5%）", "dim": "维度一：战略与布局"},
    "1.3": {"name": "战略客户组合监控", "dim": "维度一：战略与布局"},
    "1.4": {"name": "营销统计数据多维交叉验真", "dim": "维度一：战略与布局"},
    "2.1": {"name": "风险分级严禁投标底线检测", "dim": "维度二：合同质量与风险"},
    "2.2": {"name": "盈利底线与效益偏差检测", "dim": "维度二：合同质量与风险"},
    "2.3": {"name": "保证金与预收款资金安全监控", "dim": "维度二：合同质量与风险"},
    "2.4": {"name": "合同条款风险穿透", "dim": "维度二：合同质量与风险"},
    "2.5": {"name": "施工真实性验证（v4.0 签约>12月+产值<10%→red）", "dim": "维度二：合同质量与风险"},
    "3.1": {"name": "客户全生命周期监控（中标转化+流失分级+评级核查）", "dim": "维度三：客户健康度"},
    "3.2": {"name": "新客户质量评估与客户结构优化", "dim": "维度三：客户健康度"},
}
LEGACY_MODELS = LEGACY_MODEL_REGISTRY
MODELS = build_display_registry(include_legacy=True)

# ── 六模块聚合常量（从 MODELS 自动构建） ──
_MODULE_IDS = sorted(set(
    (m.get("module_id"), m.get("module_name", ""))
    for m in MODELS.values()
    if m.get("module_id")
), key=lambda x: x[0])
MODULE_MODEL_MAP = {}
for mod_id, mod_name_full in _MODULE_IDS:
    mod_key = mod_name_full.split("：")[0] if "：" in mod_name_full else mod_name_full
    mod_short = mod_name_full.split("：")[1] if "：" in mod_name_full else mod_name_full
    models_in = [lid for lid, m in MODELS.items() if m.get("module_id") == mod_id]
    MODULE_MODEL_MAP[mod_key] = {
        "id": mod_id, "name": mod_short,
        "models": models_in,
        "dim": MODELS[models_in[0]]["dim"] if models_in else "",
    }
MODEL_TO_MODULE = {m: k for k, v in MODULE_MODEL_MAP.items() for m in v["models"]}


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def load_cached_results():
    cache_path = _latest_existing_path(CACHE_PATH, "*/output/model_outputs/_all_results.pkl")
    if cache_path.exists():
        cached = _load_pickle(cache_path, {})
        if cached:
            return cached
    fallback = _load_results_from_csv_outputs()
    if fallback:
        return fallback
    return {}


def load_cached_results_for_export():
    cache_path = _latest_existing_path(CACHE_PATH, "*/output/model_outputs/_all_results.pkl")
    if cache_path.exists():
        cached = _load_pickle(cache_path, {})
        if cached:
            return cached
    fallback = _load_results_from_csv_outputs()
    if fallback:
        return fallback
    return {}


def _load_pickle(path, default):
    try:
        pattern_map = {
            CACHE_PATH.name: "*/output/model_outputs/_all_results.pkl",
            BUSINESS_PATH.name: "*/output/model_outputs/_business_results.pkl",
            DISCRETE_PATH.name: "*/output/model_outputs/_discrete_results.pkl",
            PREFILTER_PATH.name: "*/output/model_outputs/_prefilter_summary.json",
        }
        real_path = _latest_existing_path(path, pattern_map.get(path.name, ""))
        if real_path.exists():
            with open(real_path, "rb") as file:
                payload = pickle.load(file)
            if isinstance(payload, dict) and "__cache_version__" in payload:
                return payload.get("data", default)
            return payload
    except Exception:
        pass
    return default


def _load_results_from_csv_outputs():
    outputs_dir = OUTPUT_DIR / "model_outputs"
    if not outputs_dir.exists():
        return {}
    results = {}
    for model_id in MODELS:
        csv_path = outputs_dir / f"model_{model_id.replace('.', '_')}_output.csv"
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            continue
        results[model_id] = (df, {})
    return results


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


def _preserved_upload_names() -> set[str]:
    preserved = {"admin_structure.xlsx"}
    admin_target = DATA_SLOTS.get("admin_structure", {}).get("target")
    if admin_target:
        preserved.add(str(admin_target))
    return preserved


def clear_uploaded_files_safe():
    preserved_names = _preserved_upload_names()
    for pattern in ("*.xlsx", "*.xls", "*.csv", "*.txt"):
        for path in UPLOAD_DIR.glob(pattern):
            if path.name in preserved_names:
                continue
            try:
                path.unlink()
            except OSError:
                try:
                    if path.exists():
                        os.remove(path)
                except OSError:
                    pass
    if UPLOAD_MANIFEST_PATH.exists():
        try:
            UPLOAD_MANIFEST_PATH.unlink()
        except OSError:
            pass
    clear_business_runtime_caches()


def clear_uploaded_files():
    preserved_names = {"行政架构-四局.xlsx", "admin_structure.xlsx"}
    preserved_names.add("行政架构-四局.xlsx")
    for pattern in ("*.xlsx", "*.xls", "*.csv", "*.txt"):
        for path in UPLOAD_DIR.glob(pattern):
            if path.name in preserved_names:
                continue
            try:
                path.unlink()
            except OSError:
                pass
    if UPLOAD_MANIFEST_PATH.exists():
        try:
            UPLOAD_MANIFEST_PATH.unlink()
        except OSError:
            pass
    clear_business_runtime_caches()


def clear_runtime_outputs():
    for path in (CACHE_PATH, PREFILTER_PATH, BUSINESS_PATH, DISCRETE_PATH, REPORT_PATH):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    outputs_dir = OUTPUT_DIR / "model_outputs"
    if outputs_dir.exists():
        for csv_path in outputs_dir.glob("model_*_output.csv"):
            try:
                csv_path.unlink()
            except OSError:
                pass


def infer_slot_from_filename(filename: str) -> str | None:
    name = (filename or "").strip().lower()
    if not name:
        return None
    if any(keyword in name for keyword in ("行政架构", "组织架构", "架构树", "admin_structure")):
        return "admin_structure"
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
    clear_business_runtime_caches()

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
        candidates = [UPLOAD_DIR / f"{key}{ext}" for ext in (".xlsx", ".xls", ".csv", ".txt")]
        target = info.get("target")
        if target:
            candidates.append(UPLOAD_DIR / target)
        if not any(path.exists() for path in candidates):
            return False
    return True


def _empty_stats():
    return {"total_issues": 0, "total_red": 0, "total_yellow": 0, "models": [], "modules": [], "top_categories": {}}


def get_model_stats(results_override=None):
    if results_override is None and not required_uploads_ready():
        return _empty_stats()
    results = results_override if results_override is not None else load_cached_results()
    if not results:
        return _empty_stats()

    from utils.insights import MODEL_ADVICE as _MODEL_ADVICE

    models_data = []
    total_issues = 0
    total_red = 0
    total_yellow = 0
    categories_count = {}

    for model_id in MODELS:
        df, summary = results.get(model_id, (None, {}))
        display_id = legacy_to_display_id(model_id)
        import re as _re
        display_name = MODELS[model_id].get("label") or f"{display_id} {MODELS[model_id]['name']}"
        display_name = _re.sub(r"（原[\d.]+）\s*", "", display_name)
        mod_info = MODELS[model_id]
        advice = _MODEL_ADVICE.get(model_id, "")

        if df is None or len(df) == 0:
            models_data.append(
                {
                    "id": model_id,
                    "display_id": display_id,
                    "name": mod_info["name"],
                    "display_name": display_name,
                    "module_name": mod_info.get("module_name", ""),
                    "module_id": mod_info.get("module_id"),
                    "total": 0,
                    "red": 0,
                    "yellow": 0,
                    "categories": {},
                    "summary": summary or {},
                    "advice": advice,
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
                "display_id": display_id,
                "name": mod_info["name"],
                "display_name": display_name,
                "module_name": mod_info.get("module_name", ""),
                "module_id": mod_info.get("module_id"),
                "total": len(df),
                "red": reds,
                "yellow": yellows,
                "categories": {str(k): int(v) for k, v in cats.items()},
                "summary": summary or {},
                "advice": advice,
            }
        )

    # ── 模块聚合 ──
    modules_data = []
    for mod_key, mod_info in MODULE_MODEL_MAP.items():
        mod_total, mod_red, mod_yellow = 0, 0, 0
        mod_models = []
        for m in models_data:
            if m["id"] in mod_info["models"]:
                mod_total += m["total"]
                mod_red += m["red"]
                mod_yellow += m["yellow"]
                mod_models.append({
                    "id": m["id"], "display_id": m.get("display_id", m["id"]),
                    "name": m["name"], "total": m["total"], "red": m["red"], "yellow": m["yellow"],
                })
        modules_data.append({
            "key": mod_key, "id": mod_info["id"], "name": mod_info["name"],
            "dim": mod_info["dim"], "models": mod_models,
            "total": mod_total, "red": mod_red, "yellow": mod_yellow,
        })

    return {
        "total_issues": total_issues,
        "total_red": total_red,
        "total_yellow": total_yellow,
        "models": models_data,
        "modules": modules_data,
        "top_categories": dict(sorted(categories_count.items(), key=lambda item: -item[1])[:10]),
    }


def get_chain_stats():
    if not required_uploads_ready():
        return {"summary": {"total_hits": 0, "chains_with_hits": 0, "max_risk_score": 0, "entity_count": 0}, "chains": []}
    results = load_cached_results()
    if not results:
        return {"summary": {"total_hits": 0, "chains_with_hits": 0, "max_risk_score": 0, "entity_count": 0}, "chains": []}
    return build_chain_payload(results, MODELS, MODEL_TO_MODULE, MODULE_MODEL_MAP)


def _apply_prefilter_to_dmp(dmp: "pd.DataFrame") -> "pd.DataFrame":
    """Remove unstarted projects from DMP based on prefilter summary."""
    prefilter = load_prefilter_summary()
    if not prefilter or not prefilter.get("prefilter_completed"):
        return dmp
    unstarted = prefilter.get("unstarted_projects", [])
    if not unstarted:
        return dmp
    excluded_codes = set(str(p.get("project_code", "")) for p in unstarted if p.get("project_code"))
    if not excluded_codes:
        return dmp
    return dmp[~dmp["项目编码"].astype(str).isin(excluded_codes)].copy()


def _scoped_project_codes(df: "pd.DataFrame") -> set[str]:
    """Build a normalized scoped project-code whitelist from filtered business data."""
    if df is None or df.empty or "项目编码" not in df.columns:
        return set()
    return {
        str(code).strip()
        for code in df["项目编码"].tolist()
        if str(code).strip()
    }


def _is_cache_fresh(target: Path, dependencies: list[Path]) -> bool:
    """Return True when target exists and is not older than its dependencies."""
    try:
        if not target.exists():
            return False
        target_mtime = target.stat().st_mtime_ns
    except OSError:
        return False

    for dep in dependencies:
        try:
            if dep.exists() and dep.stat().st_mtime_ns > target_mtime:
                return False
        except OSError:
            continue
    return True


def _business_cache_dependencies() -> list[Path]:
    return [
        CACHE_PATH,
        PREFILTER_PATH,
        PROJECT_ROOT / "config" / "rules.json",
    ]


def _discrete_cache_dependencies() -> list[Path]:
    return [
        CACHE_PATH,
        PREFILTER_PATH,
        BUSINESS_PATH,
        DISCRETE_PATH.parent / "_business_results.pkl",
        PROJECT_ROOT / "config" / "rules.json",
        PROJECT_ROOT / "config" / "discrete_rules.json",
    ]


def get_business_stats():
    cached = _load_pickle(BUSINESS_PATH, None)
    if (
        cached
        and isinstance(cached, dict)
        and cached.get("summary", {}).get("total_projects")
        and _is_cache_fresh(BUSINESS_PATH, _business_cache_dependencies())
    ):
        return cached

    results = load_cached_results()
    if not results:
        return {"summary": {"total_projects": 0, "total_contract_yi": 0.0}, "overview": {}, "subsidiaries": [], "cities": []}

    try:
        from data_loader import load_all_appendices, load_dmp
        from data_loader.data_adapter import build_unified_projects, load_bid_report, merge_qcc_risk

        dmp_raw = load_dmp()
        appendices = load_all_appendices()
        bid_report = load_bid_report()
        dmp = build_unified_projects(dmp_raw, appendices, bid_report)
        dmp = merge_qcc_risk(dmp)
        dmp = _apply_prefilter_to_dmp(dmp)
        analyzer = BusinessHealthAnalyzer(_load_config())
        analyzer._set_model_cache(results)
        business_results = analyzer.run(results, dmp)
        try:
            with open(BUSINESS_PATH, "wb") as file:
                pickle.dump(business_results, file)
        except OSError:
            pass
        return business_results
    except Exception:
        return {"summary": {"total_projects": 0, "total_contract_yi": 0.0}, "overview": {}, "subsidiaries": [], "cities": []}


def get_discrete_stats():
    cached = _load_pickle(DISCRETE_PATH, None)
    if cached and isinstance(cached, dict) and _is_cache_fresh(DISCRETE_PATH, _discrete_cache_dependencies()):
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
        from data_loader.data_adapter import build_unified_projects, load_bid_report, merge_qcc_risk

        config = _load_config()
        dmp_raw = load_dmp()
        appendices = load_all_appendices()
        bid_report = load_bid_report()
        dmp = build_unified_projects(dmp_raw, appendices, bid_report)
        dmp = merge_qcc_risk(dmp)
        dmp = _apply_prefilter_to_dmp(dmp)
        appendix_df = appendices.get("appendix_1", None) if appendices else None
        analyzer = DiscreteAnalyzer(config)

        # v2.10: 优先使用六模块指标进行离散分析
        business_results = get_business_stats()
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


def _json_safe_value(value):
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if pd.isna(value):
        return None
    return value


def clear_business_runtime_caches():
    _BUSINESS_SOURCE_CACHE["signature"] = None
    _BUSINESS_SOURCE_CACHE["df"] = None
    _LABELED_BUSINESS_CACHE["signature"] = None
    _LABELED_BUSINESS_CACHE["df"] = None
    _SCOPE_OPTIONS_CACHE["signature"] = None
    _SCOPE_OPTIONS_CACHE["data"] = None
    clear_admin_structure_cache()


def _business_cache_signature() -> tuple:
    watched_paths = [
        UPLOAD_DIR / "dmp_sales.xlsx",
        UPLOAD_DIR / "appendix.xlsx",
        UPLOAD_DIR / "bid_report.xlsx",
        UPLOAD_DIR / "qcc_risk.xlsx",
        UPLOAD_DIR / "行政架构-四局.xlsx",
        PROJECT_ROOT / "config" / "rules.json",
        BUSINESS_PATH,
        CACHE_PATH,
    ]
    for path in sorted(UPLOAD_DIR.glob("*.xlsx")):
        if path.name not in {item.name for item in watched_paths}:
            watched_paths.append(path)
    signature = []
    for path in watched_paths:
        try:
            signature.append((str(path), path.stat().st_mtime_ns if path.exists() else 0))
        except OSError:
            signature.append((str(path), 0))
    return tuple(signature)


def _scope_filters_active(
    scope_global: str = "",
    scope_secondary: str = "",
    scope_detail: str = "",
    scope_city: str = "",
    quarter: str = "",
) -> bool:
    if not is_all_global(scope_global):
        return True
    if not is_all_direct(scope_secondary):
        return True
    if not is_all_sub(scope_detail):
        return True
    if not is_all_city(scope_city):
        return True
    return bool(quarter and quarter in ("Q1", "Q2", "Q3", "Q4"))


def _tabular_to_json(results: dict) -> dict:
    out = {}
    for key in ["subsidiaries", "cities"]:
        df = results.get(key)
        if df is not None and hasattr(df, "to_dict"):
            out[key] = df.fillna("").to_dict(orient="records")
        else:
            out[key] = []
    out["summary"] = _json_safe_value(results.get("summary", {}))
    out["overview"] = _json_safe_value(results.get("overview", {}))
    out["trends"] = results.get("trends", [])
    out["quarter_trends"] = results.get("quarter_trends", [])
    out["focus_projects"] = results.get("focus_projects", [])
    out["recommendations"] = results.get("recommendations", [])
    return out


def _load_business_source_df() -> pd.DataFrame:
    signature = _business_cache_signature()
    cached_df = _BUSINESS_SOURCE_CACHE.get("df")
    if _BUSINESS_SOURCE_CACHE.get("signature") == signature and cached_df is not None:
        return cached_df.copy()

    from data_loader import load_all_appendices, load_dmp
    from data_loader.data_adapter import build_unified_projects, load_bid_report, merge_qcc_risk

    dmp_raw = load_dmp()
    appendices = load_all_appendices()
    bid_report = load_bid_report()
    dmp = build_unified_projects(dmp_raw, appendices, bid_report)
    dmp = merge_qcc_risk(dmp)
    dmp = _apply_prefilter_to_dmp(dmp)
    if dmp is None or not hasattr(dmp, "copy"):
        empty_df = pd.DataFrame()
        _BUSINESS_SOURCE_CACHE["signature"] = signature
        _BUSINESS_SOURCE_CACHE["df"] = empty_df
        return empty_df.copy()

    _BUSINESS_SOURCE_CACHE["signature"] = signature
    _BUSINESS_SOURCE_CACHE["df"] = dmp.copy()
    return dmp.copy()


def _get_labeled_business_df(config: dict) -> pd.DataFrame:
    signature = _business_cache_signature()
    cached_df = _LABELED_BUSINESS_CACHE.get("df")
    if _LABELED_BUSINESS_CACHE.get("signature") == signature and cached_df is not None:
        return cached_df.copy()

    labeled_df = _attach_scope_labels(_load_business_source_df(), config)
    _LABELED_BUSINESS_CACHE["signature"] = signature
    _LABELED_BUSINESS_CACHE["df"] = labeled_df.copy()
    return labeled_df.copy()


def _unit_to_secondary_map(config: dict) -> dict:
    strat_cfg = (config or {}).get("十五五战略规划", {})
    diff_bases = strat_cfg.get("二级单位差异化基准", {})
    mapping = {}
    for company_name, company_cfg in diff_bases.items():
        if str(company_name).startswith("_") or not isinstance(company_cfg, dict):
            continue
        mapping[str(company_name).strip()] = str(company_name).strip()
        for alias in company_cfg.get("_alias", []):
            alias_name = str(alias).strip()
            if alias_name:
                mapping[alias_name] = str(company_name).strip()
    return mapping


def _attach_scope_labels(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    scoped = df.copy()
    scoped["申报单位"] = scoped.get("申报单位", "").astype(str).str.strip()
    scoped["城市"] = scoped.get("项目城市", scoped.get("项目地址", "")).astype(str).str.strip()

    secondary_map = _unit_to_secondary_map(config)

    def fallback_direct(unit: str) -> str:
        unit = str(unit or "").strip()
        if not unit:
            return "未识别直属机构"
        for key, parent in secondary_map.items():
            if key and key in unit:
                return parent
        return unit

    resolved = scoped["申报单位"].apply(
        lambda unit: resolve_unit_scope(unit, fallback_direct=fallback_direct(unit))
    )
    scoped["局"] = resolved.apply(lambda item: item["局"])
    scoped["二级"] = resolved.apply(lambda item: item["二级"])
    scoped["细分"] = resolved.apply(lambda item: item["细分"])
    return scoped


def _filter_business_scope(
    df: pd.DataFrame,
    scope_global: str = "",
    scope_secondary: str = "",
    scope_detail: str = "",
    scope_city: str = "",
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    scoped = df.copy()
    if not is_all_global(scope_global):
        scoped = scoped[scoped["局"].astype(str).str.strip() == str(scope_global).strip()]
    if not is_all_direct(scope_secondary):
        secondary_parts = [d.strip() for d in str(scope_secondary).split(",") if d.strip()]
        scoped = scoped[scoped["二级"].astype(str).str.strip().isin(secondary_parts)]
    if not is_all_sub(scope_detail):
        detail_parts = [d.strip() for d in str(scope_detail).split(",") if d.strip()]
        allowed_names = set()
        for part in detail_parts:
            allowed_names |= detail_filter_names(part)
        scoped = scoped[
            scoped["细分"].astype(str).str.strip().isin(allowed_names)
            | scoped["申报单位"].astype(str).str.strip().isin(allowed_names)
        ]
    if not is_all_city(scope_city):
        scoped = scoped[scoped["城市"].astype(str).str.strip() == str(scope_city).strip()]
    return scoped.reset_index(drop=True)


def get_business_stats_for_scope(
    scope_global: str = "",
    scope_secondary: str = "",
    scope_detail: str = "",
    scope_city: str = "",
    quarter: str = "",
):
    if not _scope_filters_active(scope_global, scope_secondary, scope_detail, scope_city, quarter):
        return get_business_stats()

    results = load_cached_results()
    if not results:
        return {"summary": {"total_projects": 0, "total_contract_yi": 0.0}, "overview": {}, "subsidiaries": [], "cities": []}

    try:
        config = _load_config()
        analyzer = BusinessHealthAnalyzer(config)
        analyzer._set_model_cache(results)
        df = _get_labeled_business_df(config)
        scoped_df = _filter_business_scope(df, scope_global, scope_secondary, scope_detail, scope_city)
        # quarter filtering: pre-compute _sign_quarter for filtering before analyzer.run()
        if quarter and quarter in ("Q1", "Q2", "Q3", "Q4"):
            quarter_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
            target_q = quarter_map[quarter]
            # pre-extract quarter column (mirrors BusinessHealthAnalyzer._extract_quarter)
            def _extract_quarter(row):
                for col in ("签约时间", "中标时间", "签约报量时间"):
                    val = row.get(col)
                    if pd.notna(val):
                        try:
                            return int(pd.Timestamp(val).quarter)
                        except Exception:
                            continue
                return 0
            scoped_df["_sign_quarter"] = scoped_df.apply(_extract_quarter, axis=1)
            scoped_df = scoped_df[scoped_df["_sign_quarter"] == target_q].reset_index(drop=True)
        if scoped_df.empty:
            return analyzer._empty_result()
        return analyzer.run(results, scoped_df)
    except Exception:
        return {"summary": {"total_projects": 0, "total_contract_yi": 0.0}, "overview": {}, "subsidiaries": [], "cities": []}


def get_discrete_stats_for_scope(
    scope_global: str = "",
    scope_secondary: str = "",
    scope_detail: str = "",
    scope_city: str = "",
):
    """Return discrete stats filtered by admin structure scope.

    Uses the same admin-structure labeling + filtering pipeline as the
    business-health page so the floating scope bar behaves identically.
    """
    if not _scope_filters_active(scope_global, scope_secondary, scope_detail, scope_city):
        return _discrete_to_json(get_discrete_stats())

    try:
        config = _load_config()
        df = _get_labeled_business_df(config)
        scoped_df = _filter_business_scope(df, scope_global, scope_secondary, scope_detail, scope_city)
        scoped_codes = _scoped_project_codes(scoped_df)

        discrete_json = _discrete_to_json(get_discrete_stats())

        all_projects = discrete_json.get("projects", [])

        filtered_projects = [
            p for p in all_projects
            if str(p.get("项目编码", "")).strip() in scoped_codes
        ]

        # recompute grid distribution from filtered projects
        grid = {}
        for proj in filtered_projects:
            grid_key = str(proj.get("九宫格", proj.get("网格键", ""))).strip()
            if not grid_key:
                r_bucket = _bucket_score(proj.get("R_得分", proj.get("平均R", 0)))
                e_bucket = _bucket_score(proj.get("E_得分", proj.get("平均E", 0)))
                grid_key = f"({r_bucket},{e_bucket})"
            grid[grid_key] = grid.get(grid_key, 0) + 1

        city_map = {}
        sub_map = {}
        for proj in filtered_projects:
            city_name = str(proj.get("项目城市", "")).strip() or "未识别城市"
            city_item = city_map.setdefault(city_name, {"城市": city_name, "项目数": 0})
            city_item["项目数"] += 1
            for key in ("合同额（亿元）", "合同总额（亿元）"):
                city_item[key] = round(_to_float(city_item.get(key, 0.0)) + _to_float(proj.get(key, 0.0)), 2)
            city_item["平均R"] = round(
                (_to_float(city_item.get("平均R", 0.0)) * (city_item["项目数"] - 1) + _to_float(proj.get("R_得分", 0.0)))
                / max(city_item["项目数"], 1),
                2,
            )
            city_item["平均E"] = round(
                (_to_float(city_item.get("平均E", 0.0)) * (city_item["项目数"] - 1) + _to_float(proj.get("E_得分", 0.0)))
                / max(city_item["项目数"], 1),
                2,
            )
            if "城市标签" not in city_item and proj.get("城市标签"):
                city_item["城市标签"] = proj.get("城市标签")

            unit_name = str(proj.get("申报单位", "")).strip() or "未识别单位"
            sub_item = sub_map.setdefault(unit_name, {"申报单位": unit_name, "项目数": 0})
            sub_item["项目数"] += 1
            for key in ("合同额（亿元）", "合同总额（亿元）"):
                sub_item[key] = round(_to_float(sub_item.get(key, 0.0)) + _to_float(proj.get(key, 0.0)), 2)
            sub_item["平均R"] = round(
                (_to_float(sub_item.get("平均R", 0.0)) * (sub_item["项目数"] - 1) + _to_float(proj.get("R_得分", 0.0)))
                / max(sub_item["项目数"], 1),
                2,
            )
            sub_item["平均E"] = round(
                (_to_float(sub_item.get("平均E", 0.0)) * (sub_item["项目数"] - 1) + _to_float(proj.get("E_得分", 0.0)))
                / max(sub_item["项目数"], 1),
                2,
            )

        return {
            "projects": filtered_projects,
            "cities": list(city_map.values()),
            "subsidiaries": list(sub_map.values()),
            "summary": {"total_projects": len(filtered_projects)},
            "grid_distribution": grid,
        }
    except Exception:
        return _discrete_to_json(get_discrete_stats())


def _bucket_score(value) -> int:
    """Map a numeric R/E score into 1/2/3 bucket matching frontend thresholds."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 2
    if n >= 2.34:
        return 3
    if n >= 1.67:
        return 2
    return 1


def _rewrite_business_metric_cards(result: dict, module_id: int) -> dict:
    if not isinstance(result, dict):
        return result

    cards = result.get("metric_cards")
    if not isinstance(cards, list):
        return result

    fixes_by_module = {
        3: {
            1: ("限制投标风险", "模型2.1：限制投标规则合并", "正向得分 = (1 - 限制投标异常率) × 100"),
            2: ("付款条件校验", "模型2.1：付款条件规则校验", "正向得分 = (1 - 付款条件异常率) × 100"),
            3: ("无限责任条款", "模型2.4：无限责任条款穿透", "正向得分 = (1 - 无限责任条款异常率) × 100"),
            4: ("放弃优先受偿权", "模型2.1 + 2.4：同概念条款合并", "正向得分 = (1 - 放弃优先受偿权异常率) × 100"),
            5: ("停缓建不利", "模型2.4：停缓建不利条款", "正向得分 = (1 - 停缓建不利异常率) × 100"),
            6: ("三证不全", "模型2.4：三证不全即开工", "正向得分 = (1 - 三证不全异常率) × 100"),
            7: ("质保金偏高", "模型2.4：质保金比例 > 5%", "正向得分 = (1 - 质保金偏高异常率) × 100"),
        },
        4: {
            0: ("A值底部亏损", "模型2.2：A值底线检测", "正向得分 = (1 - A值底部亏损异常率) × 100"),
            1: ("效益偏差", "模型2.2：备案A值 vs 实际利润率偏差", "正向得分 = (1 - 效益偏差异常率) × 100"),
            3: ("签约履约偏差", "模型2.5：签约 > 12个月产值转化率 < 10%", "正向得分 = (1 - 签约履约偏差异常率) × 100"),
        },
        5: {
            0: ("现金保证金占用", "模型2.3：投标保证金 + 履约保证金合并", "正向得分 = (1 - 现金保证金占用异常率) × 100"),
            2: ("联合体超额担保", "模型2.3：联合体项目履约担保 > 合同额10%", "正向得分 = (1 - 联合体超额担保异常率) × 100"),
        },
        6: {
            2: ("中标签约金额偏离", "模型1.4：中标签约金额偏离", "正向得分 = (1 - 中标签约金额偏离异常率) × 100"),
            3: ("签约逾期", "模型1.4：签约逾期规则", "正向得分 = (1 - 签约逾期异常率) × 100"),
            4: ("凑量嫌疑", "模型1.4：短期集中签约", "正向得分 = (1 - 凑量嫌疑异常率) × 100"),
            5: ("邀请招标比例过高", "模型1.4：邀请招标项目数 / 总数 > 70%", "正向得分 = (1 - 邀请招标比例过高异常率) × 100"),
        },
    }

    fixes = fixes_by_module.get(module_id, {})
    if not fixes:
        return result

    new_cards = []
    for idx, card in enumerate(cards):
        updated = dict(card)
        fix = fixes.get(idx)
        if fix:
            name, formula, score_formula = fix
            raw_score = float(updated.get("raw_display_value", updated.get("display_score", 0)) or 0.0)
            anomaly_rate = max(0.0, min(raw_score, 100.0))
            updated.update({
                "name": name,
                "metric_type": "risk_rate",
                "score_mode": "issue_rate",
                "formula": formula,
                "score_formula": score_formula,
                "anomaly_rate": round(anomaly_rate, 1),
                "raw_display_value": round(raw_score, 1),
                "display_score": round(100.0 - anomaly_rate, 1),
            })
        new_cards.append(updated)

    result = dict(result)
    result["metric_cards"] = new_cards

    result["metric_catalog"] = [
        {
            "name": card.get("name", ""),
            "type": card.get("metric_type", "risk_rate"),
            "metric_type": card.get("metric_type", "risk_rate"),
            "score_mode": card.get("score_mode", "issue_rate"),
            "formula": card.get("formula", ""),
            "score_formula": card.get("score_formula", ""),
        }
        for card in new_cards
    ]

    return result


def _build_scope_options_from_stats(stats: dict) -> dict:
    options = get_admin_scope_options()
    cities = sorted(
        {
            str(row.get("名称") or row.get("城市") or "").strip()
            for row in (stats.get("cities") or [])
            if str(row.get("名称") or row.get("城市") or "").strip()
        }
    )
    options["city"] = [ALL_CITY, *cities]
    options.setdefault("resolved_secondaries", [])
    return options


def get_business_scope_options(lightweight: bool = False):
    signature = _business_cache_signature()
    if not lightweight and _SCOPE_OPTIONS_CACHE.get("signature") == signature:
        cached = _SCOPE_OPTIONS_CACHE.get("data")
        if cached:
            return cached

    try:
        options = get_admin_scope_options()
        if lightweight:
            return _build_scope_options_from_stats(_tabular_to_json(get_business_stats()))

        config = _load_config()
        df = _get_labeled_business_df(config)
        if df.empty:
            return options

        df = df.fillna("")
        city = sorted({str(v).strip() for v in df["城市"].tolist() if str(v).strip()})
        detail_by_secondary = dict(options.get("detail_by_secondary") or {})
        city_by_secondary = {}
        city_by_detail = {}
        observed_secondaries = sorted(
            {
                str(v).strip()
                for v in df["二级"].tolist()
                if str(v).strip() and not is_all_direct(str(v).strip())
            }
        )
        observed_details = sorted(
            {
                str(v).strip()
                for v in df["细分"].tolist()
                if str(v).strip() and not is_all_sub(str(v).strip())
            }
        )
        all_secondaries = observed_secondaries

        for secondary_name in all_secondaries:
            scoped = df[df["二级"].astype(str).str.strip() == secondary_name]
            scoped_details = sorted(
                {str(v).strip() for v in scoped["细分"].tolist() if str(v).strip()}
            )
            if scoped_details:
                merged = sorted(set(detail_by_secondary.get(secondary_name, [])) | set(scoped_details))
                detail_by_secondary[secondary_name] = merged
            city_by_secondary[secondary_name] = sorted(
                {str(v).strip() for v in scoped["城市"].tolist() if str(v).strip()}
            )

        # 若组织架构中已有直属机构→下属机构映射，则以组织架构为主；
        # 业务数据中出现但未纳入架构映射的机构，仅作为兜底补充。
        if detail_by_secondary:
            all_details = []
            for secondary_name in all_secondaries:
                merged_details = sorted(set(detail_by_secondary.get(secondary_name, [])))
                if merged_details:
                    detail_by_secondary[secondary_name] = merged_details
                    all_details.extend(merged_details)
            options["detail"] = [ALL_SUB, *sorted(set(all_details))]
        else:
            options["detail"] = [ALL_SUB, *observed_details]

        for detail_name in observed_details:
            allowed_names = detail_filter_names(detail_name)
            scoped = df[
                df["细分"].astype(str).str.strip().isin(allowed_names)
                | df["申报单位"].astype(str).str.strip().isin(allowed_names)
            ]
            city_by_detail[detail_name] = sorted(
                {str(v).strip() for v in scoped["城市"].tolist() if str(v).strip()}
            )

        options["secondary"] = [ALL_DIRECT, *all_secondaries]
        options["city"] = [ALL_CITY, *city]
        options["detail_by_secondary"] = detail_by_secondary
        options["city_by_secondary"] = city_by_secondary
        options["city_by_detail"] = city_by_detail
        # v3.2: 探针路由结果 —— 下属机构仅列出实际命中的二级单位
        from models.business_analysis import BusinessHealthAnalyzer
        try:
            probe = BusinessHealthAnalyzer(config)._detect_strategic_scope(df)
            options["resolved_secondaries"] = probe.get("resolved_secondaries", [])
        except Exception:
            options["resolved_secondaries"] = []
        _SCOPE_OPTIONS_CACHE["signature"] = signature
        _SCOPE_OPTIONS_CACHE["data"] = options
        return options
    except Exception:
        return get_admin_scope_options()


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
        print(_safe_console_text(f"[pipeline] starting subprocess: {subprocess.list2cmdline(cmd)}"), flush=True)
        with pipeline_lock:
            pipeline_status["progress"] = f"已就位 {staged_count} 个文件，正在运行..." if staged_count else "未检测到上传文件，使用默认文件运行..."
            pipeline_status["started_at"] = datetime.now().isoformat()
            pipeline_status["started_at_timestamp"] = time.time()
            pipeline_status["recent_logs"] = []
            pipeline_status["output"] = ""
            pipeline_status["error"] = None

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
                    with pipeline_lock:
                        pipeline_status["recent_logs"] = output_lines[-80:]
                        pipeline_status["output"] = "\n".join(output_lines[-50:])
                        if now - last_update >= 0.2:
                            pipeline_status["progress"] = f"[{line_count} 行] {line_clean[:120]}"
                            last_update = now

        returncode = process.wait(timeout=1800)
        print(_safe_console_text(f"[pipeline] subprocess finished with code {returncode}"), flush=True)

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
                        pipeline_status["recent_logs"] = output_lines[-80:]
                        pipeline_status["output"] = "\n".join(output_lines[-50:])
                    else:
                        pipeline_status["recent_logs"] = output_lines[-80:]
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
    return render_template(
        "dashboard.html",
        slots=DATA_SLOTS,
        models=MODELS,
        legacy_models=LEGACY_MODELS,
        run_action_token=RUN_ACTION_TOKEN,
    )


@app.route("/issues")
def issues_page():
    return render_template("issues.html", stats=get_model_stats())


@app.route("/models")
def models_page():
    return redirect("/issues", code=301)


@app.route("/chains")
def chains_page():
    return render_template("chains.html", chain_stats=get_chain_stats(), models=MODELS, module_model_map=MODULE_MODEL_MAP)


@app.route("/discrete")
def discrete_page():
    try:
        return render_template(
            "discrete.html",
            discrete_stats=_discrete_to_json(get_discrete_stats()),
            scope_options=get_business_scope_options(lightweight=False),
        )
    except Exception:
        return render_template(
            "discrete.html",
            discrete_stats=_discrete_to_json(
                {"summary": {"total_projects": 0}, "projects": [], "cities": [], "subsidiaries": [], "grid_distribution": {}}
            ),
            scope_options=get_business_scope_options(lightweight=False),
        )


def get_scoring_rules() -> dict:
    """Extract the full six-module scoring-rule set from config/rules.json.

    Returns module weights, score bands, per-module sub-indicator weights,
    and the four-defence-line scoring-engine metadata — everything the
    frontend scoring-rules reference panel needs.
    """
    config = _load_config()
    bh = config.get("business_health", {})
    if not bh:
        return {}
    scoring_meta = config.get("_scoring_engine_meta", {})

    def _clean_weights(raw: dict) -> dict:
        """Strip leading-underscore description keys and _逆向 suffixes for display."""
        out = {}
        for k, v in raw.items():
            if str(k).startswith("_"):
                continue
            display = str(k).replace("_逆向", "")
            out[display] = v
        return out

    from utils.insights import MODULE_DIAGNOSIS, module_advice
    module_insights = {}
    for mid in range(1, 7):
        diag = MODULE_DIAGNOSIS.get(mid, {})
        module_insights[str(mid)] = {
            "name": diag.get("name", f"模块{mid}"),
            "strong": diag.get("strong", ""),
            "steady": diag.get("steady", ""),
            "pressure": diag.get("pressure", ""),
            "advice": module_advice(mid),
        }
    return {
        "module_weights": _clean_weights(bh.get("module_weights", {})),
        "score_bands": {k: v for k, v in bh.get("score_bands", {}).items() if not str(k).startswith("_")},
        "global_score_bands": {k: v for k, v in bh.get("global_score_bands", {}).items() if not str(k).startswith("_")},
        "confidence_levels": {k: v for k, v in bh.get("confidence_levels", {}).items() if not str(k).startswith("_")},
        "module_1": _clean_weights(bh.get("module_1_region", {})),
        "module_2": _clean_weights(bh.get("module_2_customer", {})),
        "module_3": _clean_weights(bh.get("module_3_contract", {})),
        "module_4": _clean_weights(bh.get("module_4_performance", {})),
        "module_5": _clean_weights(bh.get("module_5_capital", {})),
        "module_6": _clean_weights(bh.get("module_6_data_quality", {})),
        "scoring_engine": {
            "version": scoring_meta.get("version", ""),
            "defences": scoring_meta.get("四大防线机制", {}),
            "types": scoring_meta.get("评分类型说明", {}),
        },
        "module_insights": module_insights,
    }


@app.route("/business")
def business_page():
    try:
        return render_template(
            "business.html",
            business_stats=_tabular_to_json(get_business_stats()),
            scope_options=get_business_scope_options(lightweight=False),
            scoring_rules=get_scoring_rules(),
        )
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
            scope_options=get_business_scope_options(lightweight=False),
            scoring_rules=get_scoring_rules(),
        )


@app.route("/report")
def report_page():
    report_html = ""
    if REPORT_PATH.exists():
        with open(REPORT_PATH, "r", encoding="utf-8") as file:
            report_html = _md_to_html(file.read())
    return render_template("report.html", report_html=report_html)


def _extract_threshold_summary(level_config: dict) -> list:
    """从 Project_Level / Company_Level 配置中提取指标阈值摘要"""
    result = []
    for section_key, section_val in (level_config or {}).items():
        if str(section_key).startswith("_"):
            continue
        if not isinstance(section_val, dict):
            continue
        for ind_key, ind_val in section_val.items():
            if str(ind_key).startswith("_"):
                continue
            if not isinstance(ind_val, dict):
                continue
            entry = {
                "module_section": str(section_key),
                "indicator": str(ind_key),
                "type": ind_val.get("type", ""),
                "is_veto": bool(ind_val.get("is_veto", False)),
                "description": str(ind_val.get("description", "")),
                "thresholds": [],
            }
            for t in ind_val.get("thresholds", []) if isinstance(ind_val.get("thresholds"), list) else []:
                if not isinstance(t, dict):
                    continue
                tr = {}
                vr = t.get("value_range", [])
                tr["value_min"] = vr[0] if isinstance(vr, list) and len(vr) > 0 else None
                tr["value_max"] = vr[1] if isinstance(vr, list) and len(vr) > 1 else None
                tr["label"] = str(t.get("label", ""))
                sr = t.get("score_range", [])
                if isinstance(sr, list) and len(sr) > 0:
                    tr["score_min"] = sr[0]
                    tr["score_max"] = sr[1] if len(sr) > 1 else sr[0]
                else:
                    s_val = t.get("score")
                    tr["score_min"] = s_val
                    tr["score_max"] = s_val
                entry["thresholds"].append(tr)
            result.append(entry)
    return result


def _get_full_config_for_docs():
    """加载 rules.json 中用于模型说明页的全量配置（评分规则+阈值+差异化基准）"""
    config = _load_config()
    bh = config.get("business_health", {})
    engine = config.get("_scoring_engine_meta", {})
    return {
        # --- 原有评分规则 ---
        "module_weights": bh.get("module_weights", {}),
        "score_bands": bh.get("score_bands", {}),
        "global_score_bands": bh.get("global_score_bands", {}),
        "confidence_levels": bh.get("confidence_levels", {}),
        "high_risk_threshold": bh.get("high_risk_threshold", {}),
        "module_1": bh.get("module_1_region", {}),
        "module_2": bh.get("module_2_customer", {}),
        "module_3": bh.get("module_3_contract", {}),
        "module_4": bh.get("module_4_performance", {}),
        "module_5": bh.get("module_5_capital", {}),
        "module_6": bh.get("module_6_data_quality", {}),
        "scoring_engine": engine,
        "scoring": config.get("scoring", {}),
        "experience_warnings": config.get("experience_warnings", {}),
        # --- 新增：阈值相关 ---
        "score_bands_dynamic": bh.get("score_bands_dynamic", {}),
        "strategic_plan": config.get("十五五战略规划", {}),
        "total_score_constraints": {
            "红线模块否决封顶": 60,
            "合同底线穿透封顶": 50,
            "最低模块阈值": 40,
            "最低模块封顶": 65,
            "双弱模块阈值": 55,
            "双弱模块折扣": 0.85,
        },
        "project_level_summary": _extract_threshold_summary(config.get("Project_Level", {})),
        "company_level_summary": _extract_threshold_summary(config.get("Company_Level", {})),
    }


def _get_discrete_rules():
    """加载 discrete_rules.json"""
    disc_path = PROJECT_ROOT / "config" / "discrete_rules.json"
    if disc_path.exists():
        with open(disc_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


@app.route("/scoring-rules")
def scoring_rules_redirect():
    return redirect(url_for('model_docs_page'), code=301)


@app.route("/thresholds")
def thresholds_redirect():
    return redirect(url_for('model_docs_page'), code=301)


@app.route("/api/thresholds/update", methods=["POST"])
def api_thresholds_update():
    """保存可调节的经验阈值。

    接收 JSON: {"path": "experience_warnings.中标率.黄色预警", "value": 0.30}
    或批量: {"updates": [{"path": "...", "value": ...}, ...]}

    路径前缀决定写入哪个配置文件：
    - "离散化分析.*" → config/discrete_rules.json
    - 其他 → config/rules.json
    """
    ALLOWED_PREFIXES = [
        "experience_warnings",
        "离散化分析.风险维度.分箱阈值",
        "离散化分析.收益维度.分箱阈值",
        "business_health.score_bands_dynamic.历史",
        "business_health.score_bands_dynamic.地产",
        "business_health.score_bands.强势区下限",
        "business_health.score_bands.稳健区下限",
    ]

    data = request.get_json(silent=True) or {}
    updates = data.get("updates", [])
    if not updates and "path" in data:
        updates = [{"path": data["path"], "value": data["value"]}]

    if not updates:
        return jsonify({"ok": False, "error": "缺少 updates 或 path+value"}), 400

    # 安全校验 + 按文件分组
    rules_updates = []   # → rules.json
    disc_updates = []    # → discrete_rules.json
    for u in updates:
        path = str(u.get("path", ""))
        if not any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            return jsonify({"ok": False, "error": f"不允许修改路径: {path}"}), 403
        if path.startswith("离散化分析."):
            disc_updates.append(u)
        else:
            rules_updates.append(u)

    # 写入 rules.json
    if rules_updates:
        config = _load_config()
        for u in rules_updates:
            _deep_set(config, str(u["path"]).split("."), u["value"])
        with open(PROJECT_ROOT / "config" / "rules.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    # 写入 discrete_rules.json
    if disc_updates:
        disc_path = PROJECT_ROOT / "config" / "discrete_rules.json"
        if disc_path.exists():
            disc = json.load(open(disc_path, "r", encoding="utf-8"))
        else:
            disc = {}
        for u in disc_updates:
            _deep_set(disc, str(u["path"]).split("."), u["value"])
        with open(disc_path, "w", encoding="utf-8") as f:
            json.dump(disc, f, ensure_ascii=False, indent=2)

    # 清除缓存结果（阈值变了，旧分数失效）
    _clear_model_cache()

    return jsonify({"ok": True, "saved": len(updates), "message": "阈值已保存，缓存已清除，请重新运行模型。"})


def _deep_set(obj: dict, keys: list, value):
    """按点号路径深写入字典。"""
    for key in keys[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    obj[keys[-1]] = value


def _clear_model_cache():
    """清除模型输出缓存，强制下次重新计算。"""
    import glob as _glob
    patterns = [
        str(PROJECT_ROOT / "output" / "model_outputs" / "_results.pkl"),
        str(PROJECT_ROOT / "output" / "model_outputs" / "_business_health.pkl"),
        str(PROJECT_ROOT / "output" / "model_outputs" / "_discrete_results.pkl"),
    ]
    for pat in patterns:
        for f in _glob.glob(pat):
            try:
                os.remove(f)
            except OSError:
                pass


@app.route("/model-docs")
def model_docs_page():
    rules = _get_full_config_for_docs()
    weights = rules.get("module_weights", {})
    module_table = []
    for mod_key, mod_info in MODULE_MODEL_MAP.items():
        weight_key = mod_key + "_" + mod_info["name"]
        w = weights.get(weight_key, 0)
        mod_idx = mod_info["id"]
        sub_key = f"module_{mod_idx}"
        sub_dict = rules.get(sub_key, {})
        indicator_count = sum(1 for k in sub_dict if not str(k).startswith("_"))
        module_table.append({
            "key": mod_key, "name": mod_info["name"], "dim": mod_info["dim"],
            "models": mod_info["models"], "weight": w,
            "indicator_count": indicator_count,
        })
    chain_docs = {}
    for cid, cdef in CHAIN_DEFINITIONS.items():
        primary_modules = sorted(set(
            MODEL_TO_MODULE.get(m, m) for m in cdef.get("primary_models", set())
        ))
        secondary_modules = sorted(set(
            MODEL_TO_MODULE.get(m, m) for m in cdef.get("secondary_models", set())
        ))
        chain_docs[cid] = dict(cdef)
        chain_docs[cid]["primary_modules"] = primary_modules
        chain_docs[cid]["secondary_modules"] = secondary_modules
    sp = rules.get("strategic_plan", {})
    diff_units = [k for k in sp.get("二级单位差异化基准", {}).keys()
                  if not str(k).startswith("_")]
    # 预处理经验阈值
    exp_raw = rules.get("experience_warnings", {})
    exp_desc = {
        "中标率": "中标项目数/投标项目数",
        "中标转化率": "中标后实际签约转化率",
        "已签约未开工项目_签约后未开工天数": "超过此天数视为异常",
        "客户流失_连续未合作月数": "连续无合作超过此月数视为流失",
        "战略客户拜访_年均次数下限": "年均拜访次数低于此值预警",
        "高端拜访_局级出席率下限": "局领导出席率低于此值预警",
        "保证金逾期_重大风险天数": "逾期超此天数视为重大风险",
        "战略客户断崖下跌_同比降幅": "同比降幅超此值触发预警",
        "客户集中度_前5大占比上限": "前5大客户占比超此值风险集中",
    }
    exp_table = []
    for key, val in exp_raw.items():
        yellow = ""; red = ""
        if isinstance(val, dict):
            yellow = val.get("黄色预警", "")
            red = val.get("红色预警", "")
        else:
            red = val
        exp_table.append({"key": key, "yellow": yellow, "red": red, "desc": exp_desc.get(key, "")})
    return render_template(
        "model_docs.html",
        scoring_rules=rules,
        discrete_rules=_get_discrete_rules(),
        chain_documentation=chain_docs,
        module_table=module_table,
        strategic_plan=sp,
        differentiated_units=diff_units,
        experience_table=exp_table,
    )


@app.route("/api/stats")
def api_stats():
    return jsonify(get_model_stats())


@app.route("/api/prefilter")
def api_prefilter():
    return jsonify(load_prefilter_summary())


@app.route("/api/discrete")
def api_discrete():
    return jsonify(_discrete_to_json(get_discrete_stats()))


@app.route("/api/discrete/scope-options")
def api_discrete_scope_options():
    return jsonify(get_business_scope_options())


@app.route("/api/discrete/scoped")
def api_discrete_scoped():
    scope_global = request.args.get("global", "").strip()
    scope_secondary = request.args.get("secondary", "").strip()
    scope_detail = request.args.get("detail", "").strip()
    scope_city = request.args.get("city", "").strip()
    scoped = get_discrete_stats_for_scope(
        scope_global=scope_global,
        scope_secondary=scope_secondary,
        scope_detail=scope_detail,
        scope_city=scope_city,
    )
    return jsonify(scoped)


@app.route("/api/business")
def api_business():
    return jsonify(_tabular_to_json(get_business_stats()))


@app.route("/api/business/scope-options")
def api_business_scope_options():
    return jsonify(get_business_scope_options())


@app.route("/api/business/scoped")
def api_business_scoped():
    scope_global = request.args.get("global", "").strip()
    scope_secondary = request.args.get("secondary", "").strip()
    scope_detail = request.args.get("detail", "").strip()
    scope_city = request.args.get("city", "").strip()
    quarter = request.args.get("quarter", "").strip()
    scoped = get_business_stats_for_scope(
        scope_global=scope_global,
        scope_secondary=scope_secondary,
        scope_detail=scope_detail,
        scope_city=scope_city,
        quarter=quarter,
    )
    return jsonify(_tabular_to_json(scoped))


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
    scope_global = request.args.get("global", "").strip()
    scope_secondary = request.args.get("secondary", "").strip()
    scope_detail = request.args.get("detail", "").strip()
    scope_city = request.args.get("city", "").strip()
    if any([scope_global, scope_secondary, scope_detail, scope_city]):
        business = get_business_stats_for_scope(scope_global, scope_secondary, scope_detail, scope_city)
    else:
        business = get_business_stats()
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

    scope_global = request.args.get("global", "").strip()
    scope_secondary = request.args.get("secondary", "").strip()
    scope_detail = request.args.get("detail", "").strip()
    scope_city = request.args.get("city", "").strip()
    quarter = request.args.get("quarter", "").strip()
    if any([scope_global, scope_secondary, scope_detail, scope_city, quarter]):
        business = get_business_stats_for_scope(scope_global, scope_secondary, scope_detail, scope_city, quarter)
    else:
        business = get_business_stats()
    if not business or not isinstance(business, dict):
        # v2.10: 缓存无数据时也下发 scope_name，供前端 Badge 展示
        return jsonify({"module_id": module_id, "module_name": f"模块{module_id}",
                        "score": 0, "scope_name": "暂无数据，运行分析后自动识别",
                        "metrics": {}, "top_subsidiaries": [], "top_cities": []})

    try:
        from models.business_analysis import _business_module_detail_stable
        result = _business_module_detail_stable(business, module_id)
        result = _rewrite_business_metric_cards(result, module_id)
        gc.collect(0)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "module_id": module_id}), 500


@app.route("/api/business/benchmark/<string:scope>")
def api_business_benchmark(scope):
    """按需返回对标数据（subsidiaries / cities / projects）"""
    if scope not in ("subsidiaries", "cities", "projects"):
        return jsonify({"error": "scope must be subsidiaries, cities, or projects"}), 400

    scope_global = request.args.get("global", "").strip()
    scope_secondary = request.args.get("secondary", "").strip()
    scope_detail = request.args.get("detail", "").strip()
    scope_city = request.args.get("city", "").strip()
    quarter = request.args.get("quarter", "").strip()
    if any([scope_global, scope_secondary, scope_detail, scope_city, quarter]):
        business = get_business_stats_for_scope(scope_global, scope_secondary, scope_detail, scope_city, quarter)
    else:
        business = get_business_stats()
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

    from utils.insights import MODULE_DIAGNOSIS, module_advice
    module_insights = {}
    for mid in range(1, 7):
        diag = MODULE_DIAGNOSIS.get(mid, {})
        module_insights[str(mid)] = {
            "name": diag.get("name", f"模块{mid}"),
            "strong": diag.get("strong", ""),
            "steady": diag.get("steady", ""),
            "pressure": diag.get("pressure", ""),
            "advice": module_advice(mid),
        }
    return jsonify({
        "module_weights": bh.get("module_weights", {}),
        "score_bands": bh.get("score_bands", {}),
        "global_score_bands": bh.get("global_score_bands", {}),
        "confidence_levels": bh.get("confidence_levels", {}),
        "module_insights": module_insights,
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
    # model_id 来自前端 m.id（legacy ID），直接使用，严禁 display_to_legacy_id() 逆向转换。
    # 若传入的是 display ID（如 URL 直链），先尝试正向匹配再回退。
    if model_id not in results:
        from utils.model_registry import display_to_legacy_id as _dtl
        resolved = _dtl(model_id)
        if resolved != model_id and resolved in results:
            model_id = resolved
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

    return jsonify(
        {
            "model_id": model_id,
            "display_id": legacy_to_display_id(model_id),
            "columns": [str(c) for c in df.columns],
            "rows": rows,
            "summary": {str(k): v for k, v in summary.items()},
        }
    )


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
    for other_ext in (".xlsx", ".xls", ".csv", ".txt"):
        old_path = UPLOAD_DIR / f"qcc_risk{other_ext}"
        if old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass

    storage.save(str(QCC_UPLOAD_PATH))
    manifest = load_upload_manifest()
    manifest["qcc_risk"] = {
        "original_filename": storage.filename,
        "saved_as": QCC_UPLOAD_PATH.name,
        "size": QCC_UPLOAD_PATH.stat().st_size,
        "mtime": datetime.fromtimestamp(QCC_UPLOAD_PATH.stat().st_mtime).isoformat(),
    }
    save_upload_manifest(manifest)
    clear_runtime_outputs()
    clear_business_runtime_caches()

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
        manifest_entry = manifest.get(key)
        candidate_paths = [UPLOAD_DIR / f"{key}{ext}" for ext in (".xlsx", ".xls", ".csv", ".txt")]
        target = info.get("target")
        if target:
            candidate_paths.append(UPLOAD_DIR / target)

        if isinstance(manifest_entry, dict):
            for path in candidate_paths:
                if path.exists() and manifest_entry.get("saved_as") == path.name:
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
    candidate_paths = [UPLOAD_DIR / f"{slot}{ext}" for ext in (".xlsx", ".xls", ".csv", ".txt")]
    target = DATA_SLOTS.get(slot, {}).get("target")
    if target:
        candidate_paths.append(UPLOAD_DIR / target)
    for path in candidate_paths:
        if path.exists():
            path.unlink()
    manifest = load_upload_manifest()
    if slot in manifest:
        manifest.pop(slot, None)
        save_upload_manifest(manifest)
    clear_runtime_outputs()
    clear_business_runtime_caches()
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
    data = request.get_json(silent=True) or {}
    if data.get("clear_uploads") is True:
        clear_uploaded_files_safe()
    clear_runtime_outputs()
    return jsonify({"status": "ok"})


@app.route("/api/run-prefilter", methods=["POST"])
def api_run_prefilter():
    if request.headers.get("X-Run-Intent") != RUN_ACTION_TOKEN:
        return jsonify({"error": "刷新页面不会自动运行；请点击页面按钮重新启动前置过滤。"}), 403

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
            cmd = _resolve_python_command() + [
                str(PROJECT_ROOT / "main.py"),
                "--prefilter-only",
                "--output-dir",
                str(OUTPUT_DIR),
            ]
            _run_pipeline_subprocess(cmd, len(staged))
            with pipeline_lock:
                ok = not pipeline_status.get("error")
            if ok and not PREFILTER_PATH.exists():
                with pipeline_lock:
                    pipeline_status["error"] = "前置过滤任务结束，但未生成过滤摘要文件。请查看终端日志中的异常信息。"
                    pipeline_status["progress"] = "Error"
            elif ok:
                with pipeline_lock:
                    pipeline_status["progress"] = "前置过滤层完成"
        except Exception as exc:
            _mark_pipeline_error(exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "mode": "prefilter"})


@app.route("/api/run", methods=["POST"])
def api_run():
    if request.headers.get("X-Run-Intent") != RUN_ACTION_TOKEN:
        return jsonify({"error": "刷新页面不会自动运行；请点击页面按钮重新启动模型。"}), 403

    with pipeline_lock:
        if pipeline_status["running"]:
            return jsonify({"error": "Pipeline already running"}), 409

    if not required_uploads_ready():
        return jsonify({"error": "请先上传全部必传文件"}), 400
    if not PREFILTER_PATH.exists():
        return jsonify({"error": "请先运行前置过滤层，确认过滤结果后再执行模型"}), 400

    data = request.get_json(silent=True) or {}
    selected_models = data.get("models", [])
    if not selected_models:
        return jsonify({"error": "No models selected"}), 400

    invalid = [model for model in selected_models if model not in MODELS]
    if invalid:
        return jsonify({"error": f"Invalid models: {invalid}"}), 400

    try:
        strategic_scope = summarize_strategic_scope(detect_strategic_scope(_load_business_source_df(), _load_config()))
        strategic_scope_name = strategic_scope.get("scope_name") or "四局全局155基准"
    except Exception:
        strategic_scope = {"scope_name": "战略规划口径自动识别失败"}
        strategic_scope_name = "战略规划口径自动识别失败"

    with pipeline_lock:
        pipeline_status.clear()
        pipeline_status.update({
            "running": True,
            "progress": f"正在运行 {len(selected_models)} 个模型；战略规划口径：{strategic_scope_name}",
            "error": None,
            "started_at": datetime.now().isoformat(),
            "started_at_timestamp": time.time(),
            "strategic_scope": strategic_scope,
        })

    def _run():
        try:
            copied = _stage_uploaded_files()
            cmd = _resolve_python_command() + [
                str(PROJECT_ROOT / "main.py"),
                "--model",
                ",".join(selected_models),
                "--output-dir",
                str(OUTPUT_DIR),
            ]
            _run_pipeline_subprocess(cmd, len(copied))
        except Exception as exc:
            _mark_pipeline_error(exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "models": selected_models, "strategic_scope": strategic_scope})


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
    cache_path = _latest_existing_path(CACHE_PATH, "*/output/model_outputs/_all_results.pkl")
    discrete_path = _latest_existing_path(DISCRETE_PATH, "*/output/model_outputs/_discrete_results.pkl")
    business_path = _latest_existing_path(BUSINESS_PATH, "*/output/model_outputs/_business_results.pkl")
    return jsonify(
        {
            "project_root": str(PROJECT_ROOT),
            "uploads_ready": required_uploads_ready(),
            "cache_exists": cache_path.exists(),
            "discrete_exists": discrete_path.exists(),
            "business_exists": business_path.exists(),
        }
    )


@app.route("/api/strategic-scope")
def api_strategic_scope():
    try:
        if not required_uploads_ready():
            return jsonify({
                "scope_type": "pending",
                "scope_name": "上传必传文件后自动识别战略规划口径",
                "matched_unit": None,
                "fallback_reason": "必传文件尚未齐备",
                "sectors": [],
                "not_applicable": [],
            })
        config = _load_config()
        df = _load_business_source_df()
        scope = summarize_strategic_scope(detect_strategic_scope(df, config))
        return jsonify(scope)
    except Exception as exc:
        return jsonify({
            "scope_type": "error",
            "scope_name": "战略规划口径识别失败",
            "matched_unit": None,
            "fallback_reason": str(exc),
            "sectors": [],
            "not_applicable": [],
        }), 500


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
    if not _ensure_startup_port_ready(5001):
        sys.exit(1)
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
