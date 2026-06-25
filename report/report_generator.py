"""
Comprehensive audit report generator.

The report is intentionally written as a management narrative first, with tables
kept as supporting evidence. This keeps the web "综合报告" page readable for
leaders instead of becoming a raw model-output dump.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd

from utils.model_registry import get_model_display_name, legacy_to_display_id


MODEL_NAMES = {
    "1.1": "区域授权与跨区经营",
    "1.2": "业务结构与战略匹配",
    "1.3": "战略客户健康度",
    "1.4": "数据质量校验",
    "2.1": "风险分级严禁投标底线",
    "2.2": "盈利底线与效益偏差",
    "2.3": "资金安全与回收风险",
    "2.4": "合同条款风险",
    "2.5": "施工真实性验证",
    "3.1": "客户全生命周期监控",
    "3.2": "新客户质量评估",
}

# Import shared MODEL_ADVICE from insights module to avoid duplication
from utils.insights import MODEL_ADVICE


def generate_report(
    model_outputs: dict,
    chain_results: dict,
    output_dir: str = "output/reports",
    compliance_df=None,
    compliance_summary=None,
    discrete_results=None,
    business_results=None,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    def w(text: str = "") -> None:
        lines.append(text)

    stats = _collect_stats(model_outputs)
    top_models = sorted(stats["model_rows"], key=lambda row: row["issues"], reverse=True)
    active_models = [row for row in top_models if row["issues"] > 0]
    total_issues = stats["total_issues"]
    red_count = stats["red_count"]
    yellow_count = stats["yellow_count"]
    affected_projects = stats["affected_projects"]
    overall_level = _overall_level(total_issues, red_count)

    w("# 市场营销综合审计报告")
    w()
    w(f"> 自动生成时间：{now}")
    w("> 报告定位：本报告用于管理层快速判断本批次营销审计的总体态势、关键风险、传导链条和整改优先级。")
    w()

    _write_executive_summary(w, overall_level, total_issues, red_count, yellow_count, affected_projects, active_models)
    _write_overall_posture(w, stats, discrete_results, business_results, chain_results)
    _write_key_findings(w, model_outputs, active_models)
    _write_dimension_sections(w, model_outputs)
    _write_chain_section(w, chain_results)
    _write_decision_sections(w, discrete_results, business_results)
    _write_compliance_section(w, compliance_df, compliance_summary)
    _write_rectification_section(w, active_models, total_issues, red_count)
    _write_appendix_tables(w, model_outputs)

    w()
    w(f"*本报告由全面数字化营销审计系统自动生成。生成时间：{now}*")

    report_path = os.path.join(output_dir, "市场营销综合审计报告.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path


def _write_executive_summary(w, level, total_issues, red_count, yellow_count, affected_projects, active_models) -> None:
    w("## 一、审计结论摘要")
    w()
    w(
        f"本批次营销审计总体判断为 **{level}**。系统共识别问题 {total_issues} 项，"
        f"其中重大/红色风险 {red_count} 项、黄色预警 {yellow_count} 项，"
        f"涉及项目 {affected_projects} 个。"
    )
    if total_issues == 0:
        w("本批次未发现明显异常事项，当前数据下营销经营风险总体可控。建议继续保持常态化监测，重点关注后续新增项目的合同底线、客户质量和资金回收。")
    else:
        top_text = "、".join(f"{row.get('display_name', row['model_id'] + ' ' + row['name'])}（{row['issues']}项）" for row in active_models[:3])
        w(f"问题主要集中在 {top_text or '少数模型'}。这说明本批次风险不是单一字段异常，而是需要从项目准入、合同质量、客户履约和资金安全几个环节联动穿透。")
    if red_count > 0:
        w("管理层应优先处理红色风险事项：先控增量、再查存量、同步追溯审批依据，避免风险项目继续扩大敞口。")
    else:
        w("本批次暂未形成明显红线级风险，但黄色预警仍需纳入整改台账，防止从关注事项演化为制度底线问题。")
    w()


def _write_overall_posture(w, stats, discrete_results, business_results, chain_results) -> None:
    w("## 二、总体态势判断")
    w()
    w("从模型结果看，本次审计不宜只理解为“发现了多少问题”，更应关注风险在经营链条中的分布位置。")
    w()
    w(f"- 模型覆盖：已执行 {stats['model_count']} 个模型，其中 {stats['active_model_count']} 个模型发现问题。")
    w(f"- 风险结构：红色/重大风险 {stats['red_count']} 项，黄色预警 {stats['yellow_count']} 项，其他关注事项 {stats['other_count']} 项。")
    w(f"- 项目影响：去重后涉及项目约 {stats['affected_projects']} 个，需优先识别多模型重复命中的项目。")

    chain_hit_count = _chain_hit_count(chain_results)
    if chain_hit_count > 0:
        w(f"- 传导关系：关联分析发现 {chain_hit_count} 条有效风险链，提示部分问题存在从数据质量、客户状态向合同和决策可靠性传导的可能。")
    else:
        w("- 传导关系：暂未识别出显著跨模型风险链，后续可结合重点项目访谈继续复核。")

    disc_summary = _safe_dict(discrete_results).get("summary", {}) if discrete_results else {}
    if disc_summary.get("total_projects"):
        w(
            f"- 九宫格决策：已纳入 {disc_summary.get('total_projects', 0)} 个项目，"
            f"高风险项目 {disc_summary.get('high_risk_count', 0)} 个，"
            f"淘汰/整顿区 {disc_summary.get('elimination_count', 0)} 个。"
        )

    biz_summary = _safe_dict(business_results).get("summary", {}) if business_results else {}
    if biz_summary.get("total_projects"):
        w(
            f"- 经营健康度：覆盖 {biz_summary.get('covered_units', 0)} 个二级单位、"
            f"{biz_summary.get('covered_cities', 0)} 个城市；最高得分单位为"
            f"{biz_summary.get('top_unit', '--')}（{_fmt(biz_summary.get('top_unit_score', 0), 1)}分）。"
        )
    w()


def _write_key_findings(w, model_outputs, active_models) -> None:
    w("## 三、核心问题归纳")
    w()
    if not active_models:
        w("本批次未形成显著问题清单，综合风险处于低位。建议后续重点提升源数据完整性和模型常态化运行频率。")
        w()
        return

    for row in active_models[:5]:
        model_id = row["model_id"]
        df = _model_df(model_outputs, model_id)
        category_text = _top_category_text(df)
        severity_text = f"红色/重大 {row['red']} 项、黄色 {row['yellow']} 项"
        w(f"### {row.get('display_name', f'{model_id} {row['name']}')}")
        w(f"该模型发现 {row['issues']} 项问题，其中{severity_text}。{category_text}")
        w(f"管理含义：{MODEL_ADVICE.get(model_id, '建议纳入专项整改台账并持续跟踪。')}")
        sample_desc = _first_description(df)
        if sample_desc:
            w(f"典型表现：{sample_desc}")
        w()


def _write_dimension_sections(w, model_outputs) -> None:
    groups = [
        ("四、分维度分析：战略布局", ["1.1", "1.2", "1.3", "1.4"], "该维度重点回答“项目从哪里来、是否符合战略方向、数据是否可信”。"),
        ("五、分维度分析：合同与履约风险", ["2.1", "2.2", "2.3", "2.4", "2.5"], "该维度重点回答“项目能不能接、赚不赚钱、条款是否安全、履约是否真实”。"),
        ("六、分维度分析：客户健康", ["3.1", "3.2"], "该维度重点回答“客户是否稳定、是否值得持续投入、是否存在质量下滑”。"),
    ]
    for title, model_ids, intro in groups:
        rows = [_model_stat(model_outputs, model_id) for model_id in model_ids]
        issue_sum = sum(row["issues"] for row in rows)
        w(f"## {title}")
        w()
        w(f"{intro}本维度共发现 {issue_sum} 项问题。")
        if issue_sum == 0:
            w("当前模型结果未提示明显异常，建议保持常态监测。")
        else:
            dominant = max(rows, key=lambda row: row["issues"])
            if dominant["issues"] > 0:
                w(f"其中问题最集中的模块为 **{dominant.get('display_name', dominant['model_id'] + ' ' + dominant['name'])}**，共 {dominant['issues']} 项，需作为本维度整改切入点。")
            for row in rows:
                if row["issues"] > 0:
                    w(f"- {row.get('display_name', row['model_id'] + ' ' + row['name'])}：{row['issues']} 项。建议：{MODEL_ADVICE.get(row['model_id'], '专项复核。')}")
        w()


def _write_chain_section(w, chain_results) -> None:
    w("## 七、关联穿透分析")
    w()
    if not chain_results:
        w("当前未生成关联链结果。建议在模型运行完成后同步执行关联分析，以识别跨模型传导风险。")
        w()
        return

    hit_chains = []
    for chain_name, result in chain_results.items():
        if not isinstance(result, dict):
            continue
        count = _to_int(result.get("total_correlations", result.get("hit_count", 0)))
        if count > 0:
            hit_chains.append((chain_name, result, count))

    if not hit_chains:
        w("本批次暂未识别出显著关联链命中，说明问题之间尚未形成明显系统性传导。但单点问题仍需按模型建议整改。")
        w()
        return

    w(f"关联分析共发现 {len(hit_chains)} 条有效链路。其价值在于把单个模型的异常串联成“原因-表现-后果”的管理线索。")
    for chain_name, result, count in hit_chains:
        title = result.get("chain", chain_name)
        desc = result.get("description", "跨模型风险传导链")
        w(f"- {title}：命中 {count} 项。{desc}")
    w("建议优先抽取同时出现在多条链路中的项目或单位，作为穿透核查样本。")
    w()


def _write_decision_sections(w, discrete_results, business_results) -> None:
    w("## 八、经营决策看板结论")
    w()
    wrote_any = False

    disc = _safe_dict(discrete_results)
    disc_summary = disc.get("summary", {})
    if disc_summary.get("total_projects"):
        wrote_any = True
        total = disc_summary.get("total_projects", 0)
        high = disc_summary.get("high_risk_count", 0)
        elim = disc_summary.get("elimination_count", 0)
        expand = disc_summary.get("expansion_count", 0)
        w(f"风险-收益九宫格已覆盖 {total} 个项目。高风险项目 {high} 个，淘汰/整顿区 {elim} 个，扩张/培育区 {expand} 个。")
        if elim:
            w("淘汰/整顿区项目属于资源效率和合规风险双重承压对象，应优先评估退出、压缩或限期整改。")
        if expand:
            w("扩张/培育区项目风险相对可控且收益表现较好，可作为资源倾斜和经验复制的候选对象。")

    biz = _safe_dict(business_results)
    biz_summary = biz.get("summary", {})
    if biz_summary.get("total_projects"):
        wrote_any = True
        w(
            f"区域-客户-履约健康监测覆盖 {biz_summary.get('covered_units', 0)} 个二级单位、"
            f"{biz_summary.get('covered_cities', 0)} 个城市。"
        )
        top_unit = biz_summary.get("top_unit")
        if top_unit:
            w(f"当前最高得分单位为 {top_unit}，综合得分 {_fmt(biz_summary.get('top_unit_score', 0), 1)} 分，可作为同类单位对标样本。")
        w("建议将经营健康度作为季度经营分析的固定议题，对连续下滑单位开展专项诊断。")

    if not wrote_any:
        w("当前尚未形成九宫格或经营健康度分析结果。建议完成模型运行后重新生成报告。")
    w()


def _write_compliance_section(w, compliance_df, compliance_summary) -> None:
    if compliance_df is None and not compliance_summary:
        return
    w("## 九、制度违规映射")
    w()
    summary = _safe_dict(compliance_summary)
    major = _to_int(summary.get("重大_violations", summary.get("major_violations", 0)))
    categories = summary.get("by_category", {}) if isinstance(summary.get("by_category", {}), dict) else {}
    total = len(compliance_df) if hasattr(compliance_df, "__len__") else sum(_to_int(v) for v in categories.values())
    w(f"系统已将模型发现映射至制度违规口径，共形成 {total} 条制度映射记录，其中重大违规 {major} 条。")
    if categories:
        top = sorted(categories.items(), key=lambda item: _to_int(item[1]), reverse=True)[:5]
        w("高频制度类别包括：" + "；".join(f"{name} {count} 条" for name, count in top) + "。")
    if major > 0:
        w("重大违规事项建议进入责任追溯预审环节，先核事实、再定责任、后做整改闭环。")
    w()


def _write_rectification_section(w, active_models, total_issues, red_count) -> None:
    w("## 十、整改建议与管理动作")
    w()
    if total_issues == 0:
        w("本批次建议以“保持监测、完善数据、定期复盘”为主，不新增专项整改负担。")
        w()
        return

    if red_count > 0:
        w("第一优先级是红色/重大风险事项。建议 5 个工作日内完成事实核查，10 个工作日内形成整改路径，重大事项同步纳入审批追溯。")
    else:
        w("本批次以黄色预警和管理关注事项为主。建议按月形成整改台账，重点防止预警事项升级。")
    w("第二优先级是多模型重复命中的项目。该类项目通常不是单一字段问题，而是存在区域、客户、合同、资金之间的复合风险。")
    w("第三优先级是数据质量问题。对关键字段缺失、口径不一致、时间链条不完整的项目，应先完成数据治理再复核模型结论。")
    w()
    w("| 整改对象 | 主要问题 | 建议动作 |")
    w("|:---|:---:|:---|")
    for row in active_models:
        w(f"| {row['model_id']} {row['name']} | {row['issues']}项 | {MODEL_ADVICE.get(row['model_id'], '纳入专项整改台账。')} |")
    w()


def _write_appendix_tables(w, model_outputs) -> None:
    w("## 十一、问题明细样例")
    w()
    w("以下表格用于支撑上述结论，完整明细请以模型结果页或导出的 Excel 为准。")
    for model_id in MODEL_NAMES:
        df = _model_df(model_outputs, model_id)
        if df.empty:
            continue
        w()
        w(f"### {model_id} {MODEL_NAMES[model_id]}")
        w(format_table(df.head(10), _preferred_columns(df)))
    w()


def _collect_stats(model_outputs: dict) -> dict[str, Any]:
    rows = []
    project_values = set()
    total_issues = red_count = yellow_count = other_count = 0

    for model_id in MODEL_NAMES:
        row = _model_stat(model_outputs, model_id)
        rows.append(row)
        total_issues += row["issues"]
        red_count += row["red"]
        yellow_count += row["yellow"]
        other_count += row["other"]
        df = _model_df(model_outputs, model_id)
        project_col = _find_col(df, ["项目编码", "项目名称", "椤圭洰缂栫爜", "椤圭洰鍚嶇О"])
        if project_col:
            project_values.update(str(v) for v in df[project_col].dropna().tolist() if str(v).strip())

    return {
        "model_rows": rows,
        "model_count": len([row for row in rows if row["checked"]]),
        "active_model_count": len([row for row in rows if row["issues"] > 0]),
        "total_issues": total_issues,
        "red_count": red_count,
        "yellow_count": yellow_count,
        "other_count": other_count,
        "affected_projects": len(project_values),
    }


def _model_stat(model_outputs: dict, model_id: str) -> dict[str, Any]:
    df = _model_df(model_outputs, model_id)
    summary = _model_summary(model_outputs, model_id)
    issues = len(df)
    if issues == 0:
        issues = _to_int(summary.get("total_issues", summary.get("total_violations", 0)))
    red = _severity_count(df, ["red", "红", "重大", "严禁"])
    yellow = _severity_count(df, ["yellow", "黄", "预警", "限制"])
    return {
        "model_id": model_id,
        "display_id": legacy_to_display_id(model_id),
        "display_name": get_model_display_name(model_id, include_legacy=True),
        "name": MODEL_NAMES.get(model_id, model_id),
        "issues": issues,
        "red": red,
        "yellow": yellow,
        "other": max(issues - red - yellow, 0),
        "checked": bool(model_id in model_outputs or issues > 0),
    }


def _model_df(model_outputs: dict, model_id: str) -> pd.DataFrame:
    value = model_outputs.get(model_id, (pd.DataFrame(), {}))
    if isinstance(value, tuple) and len(value) >= 1 and isinstance(value[0], pd.DataFrame):
        return value[0].copy()
    return pd.DataFrame()


def _model_summary(model_outputs: dict, model_id: str) -> dict:
    value = model_outputs.get(model_id, (pd.DataFrame(), {}))
    if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[1], dict):
        return value[1]
    return {}


def _severity_count(df: pd.DataFrame, keywords: list[str]) -> int:
    if df.empty:
        return 0
    col = _find_col(df, ["严重等级", "风险等级", "涓ラ噸绛夌骇", "severity"])
    if not col:
        return 0
    text = df[col].astype(str)
    return int(text.str.contains("|".join(keywords), case=False, na=False, regex=True).sum())


def _top_category_text(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    col = _find_col(df, ["问题分类", "风险类别", "闂鍒嗙被", "categories"])
    if not col:
        return "问题类别需结合明细进一步归因。"
    counts = df[col].dropna().astype(str).value_counts().head(3)
    if counts.empty:
        return "问题类别需结合明细进一步归因。"
    return "高频类别为：" + "、".join(f"{name}（{count}项）" for name, count in counts.items()) + "。"


def _first_description(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    col = _find_col(df, ["问题描述", "风险描述", "闂鎻忚堪", "description"])
    if not col:
        return ""
    for value in df[col].dropna().astype(str).tolist():
        text = value.strip()
        if text:
            return text[:140]
    return ""


def _preferred_columns(df: pd.DataFrame) -> list[str]:
    candidates = [
        "模型编号", "项目编码", "项目名称", "客户名称", "申报单位", "城市",
        "问题分类", "严重等级", "问题描述", "涉及金额", "签约额（元）",
        "妯″瀷缂栧彿", "椤圭洰缂栫爜", "椤圭洰鍚嶇О", "瀹㈡埛鍚嶇О",
        "鐢虫姤鍗曚綅", "闂鍒嗙被", "涓ラ噸绛夌骇", "闂鎻忚堪",
    ]
    cols = [col for col in candidates if col in df.columns]
    return cols or [str(col) for col in df.columns[:8]]


def _find_col(df: pd.DataFrame, names: list[str]) -> str | None:
    if df.empty:
        return None
    for name in names:
        if name in df.columns:
            return name
    return None


def _chain_hit_count(chain_results: dict) -> int:
    total = 0
    for result in (chain_results or {}).values():
        if isinstance(result, dict):
            total += _to_int(result.get("total_correlations", result.get("hit_count", 0)))
    return total


def _overall_level(total_issues: int, red_count: int) -> str:
    if red_count >= 10:
        return "高风险，需立即组织专项整改"
    if red_count > 0:
        return "中高风险，需优先处理红线事项"
    if total_issues >= 30:
        return "中风险，预警事项较多"
    if total_issues > 0:
        return "低至中风险，整体可控但需跟踪"
    return "低风险，当前未见明显异常"


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _to_int(value: Any) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:.{digits}f}"
    except Exception:
        return str(value)


def format_table(df, columns=None):
    if df is None or len(df) == 0:
        return "_无数据_"
    table = df.copy()
    if columns:
        selected = [c for c in columns if c in table.columns]
        if selected:
            table = table[selected]
    table = table.head(20).fillna("")
    header = "| " + " | ".join(str(c) for c in table.columns) + " |"
    sep = "|" + "|".join([":---"] * len(table.columns)) + "|"
    rows = []
    for _, row in table.iterrows():
        cells = [_clean_cell(row[col]) for col in table.columns]
        rows.append("| " + " | ".join(cells) + " |")
    return header + "\n" + sep + "\n" + "\n".join(rows)


def _clean_cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("|", "/").strip()
    return text[:120] + ("..." if len(text) > 120 else "")
