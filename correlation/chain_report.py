from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from utils.model_registry import get_model_display_name, legacy_to_display_id


CHAIN_DEFINITIONS = {
    "strategic_risk": {
        "name": "关联链：战略 -> 风险关联提示链",
        "description": "识别非常规区域项目是否同步叠加高风险承接、盈利异常或施工真实性异常。",
        "primary_models": {"1.1"},
        "secondary_models": {"2.1", "2.2", "2.5"},
        "focus": "非常规区域项目叠加多类合同风险",
        "suggestion": "优先核查区域授权依据、投标底线触碰情况，以及利润与履约真实性是否同步异常。",
    },
    "customer_contract": {
        "name": "关联链：客户 -> 合同质量关联提示链",
        "description": "识别客户流失、战略客户波动是否同步伴随盈利和资金回收异常。",
        "primary_models": {"1.3", "3.1"},
        "secondary_models": {"2.2", "2.3"},
        "focus": "客户质量变化与合同经营质量联动",
        "suggestion": "复核客户结构变化对利润率、回款周期和保证金回收的影响，避免直接下因果结论。",
    },
    "data_reliability": {
        "name": "关联链：数据质量 -> 决策可靠性提示链",
        "description": "识别数据验真异常是否影响其他模型结果可信度。",
        "primary_models": {"1.4"},
        "secondary_models": {"1.1", "1.2", "1.3", "2.1", "2.2", "2.3", "2.4", "2.5", "3.1", "3.2"},
        "focus": "数据质量异常影响模型解释可靠性",
        "suggestion": "优先回函主数据映射、时间线、金额逻辑和状态字段，再决定是否进入实质性业务核查。",
    },
}

IDENTIFIER_COLUMNS = [
    "统一项目主键",
    "项目主键",
    "项目编码",
    "项目编号",
    "项目ID",
    "合同编号",
    "中标通知书编号",
    "项目名称",
    "客户名称",
]

NAME_COLUMNS = [
    "项目名称",
    "合同名称",
    "客户名称",
    "客户简称",
]

TYPE_HINTS = {
    "项目": ["项目", "合同", "中标", "履约"],
    "客户": ["客户"],
}

TAG_COLUMNS = [
    "问题分类",
    "风险标签",
    "异常类型",
    "预警标签",
    "合同风险类型",
]

DETAIL_COLUMNS = [
    "问题描述",
    "风险说明",
    "异常说明",
    "审计发现",
    "原因说明",
    "核查建议",
    "审计建议",
]

SEVERITY_COLUMNS = [
    "严重等级",
    "风险等级",
    "预警等级",
]


@dataclass
class Observation:
    model_id: str
    display_id: str
    model_name: str
    module_id: str
    module_name: str
    entity_key: str
    entity_name: str
    entity_type: str
    severity: str
    score: int
    tags: list[str]
    evidence: str


def build_chain_payload(
    results: dict[str, Any],
    model_registry: dict[str, dict[str, str]],
    model_to_module: dict[str, str] | None = None,
    module_model_map: dict[str, dict] | None = None,
) -> dict[str, Any]:
    observations = _collect_observations(results, model_registry, model_to_module)
    grouped = _group_by_entity(observations)
    chains = []

    for chain_id, definition in CHAIN_DEFINITIONS.items():
        hits = _build_chain_hits(chain_id, definition, grouped)
        chains.append(
            {
                "id": chain_id,
                "chain_name": definition["name"],
                "name": definition["name"],
                "description": definition["description"],
                "focus": definition["focus"],
                "count": len(hits),
                "hit_count": len(hits),
                "hits": hits,
                "risk_score": max((hit["risk_score"] for hit in hits), default=0),
                "entity_count": len({hit["entity_key"] for hit in hits}),
                "entities": [hit["entity_name"] for hit in hits],
            }
        )

    total_hits = sum(chain["count"] for chain in chains)
    summary = {
        "total_hits": total_hits,
        "chains_with_hits": sum(1 for chain in chains if chain["count"] > 0),
        "max_risk_score": max((hit["risk_score"] for chain in chains for hit in chain["hits"]), default=0),
        "entity_count": len(grouped),
    }
    return {"summary": summary, "chains": chains}


def _collect_observations(
    results: dict[str, Any],
    model_registry: dict[str, dict[str, str]],
    model_to_module: dict[str, str] | None = None,
) -> list[Observation]:
    observations: list[Observation] = []
    _mtm = model_to_module or {}
    for model_id, payload in results.items():
        if model_id not in model_registry:
            continue

        try:
            df, _summary = payload
        except Exception:
            continue

        if df is None or getattr(df, "empty", True):
            continue

        mod_key = _mtm.get(model_id, "")
        mod_name = ""
        if mod_key and "：" in mod_key:
            mod_name = mod_key.split("：")[1]
        elif mod_key:
            mod_name = mod_key

        for _idx, row in df.iterrows():
            row_dict = {}
            for col in df.columns:
                value = row[col]
                try:
                    if hasattr(value, "item"):
                        value = value.item()
                except Exception:
                    pass
                row_dict[str(col)] = value

            entity_key = _pick_first_value(row_dict, IDENTIFIER_COLUMNS)
            if not entity_key:
                entity_key = f"{model_id}::{len(observations) + 1}"

            entity_name = _pick_first_value(row_dict, NAME_COLUMNS) or entity_key
            entity_type = _infer_entity_type(row_dict, entity_name)
            severity = _infer_severity(row_dict)
            score = _severity_score(severity)
            tags = _extract_tags(row_dict)
            evidence = _extract_evidence(row_dict)

            observations.append(
                Observation(
                    model_id=model_id,
                    display_id=legacy_to_display_id(model_id),
                    model_name=model_registry[model_id]["name"],
                    module_id=mod_key,
                    module_name=mod_name,
                    entity_key=str(entity_key),
                    entity_name=str(entity_name),
                    entity_type=entity_type,
                    severity=severity,
                    score=score,
                    tags=tags,
                    evidence=evidence,
                )
            )
    return observations


def _group_by_entity(observations: list[Observation]) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = {}
    for item in observations:
        grouped.setdefault(item.entity_key, []).append(item)
    return grouped


def _build_chain_hits(chain_id: str, definition: dict[str, Any], grouped: dict[str, list[Observation]]) -> list[dict[str, Any]]:
    hits = []
    primary = definition["primary_models"]
    secondary = definition["secondary_models"]

    for entity_key, items in grouped.items():
        models = {item.model_id for item in items}
        primary_hits = sorted(models & primary)
        secondary_hits = sorted(models & secondary)
        if not primary_hits or not secondary_hits:
            continue

        sorted_items = sorted(items, key=lambda item: (-item.score, item.model_id))
        top_items = sorted_items[:4]
        risk_score = sum(item.score for item in top_items) + len(primary_hits) * 2 + len(secondary_hits)
        tags = []
        for item in top_items:
            tags.extend(item.tags)
        unique_tags = list(dict.fromkeys(tag for tag in tags if tag))
        severity = _score_to_level(risk_score)

        # 提取涉及的模块（去重保持顺序）
        involved_module_ids = list(dict.fromkeys(
            item.module_id for item in top_items if item.module_id
        ))
        # 主/关联模块从各自主模型、关联模型的 Observation 中提取
        primary_module_ids = list(dict.fromkeys(
            item.module_id for item in items
            if item.model_id in primary and item.module_id
        ))
        secondary_module_ids = list(dict.fromkeys(
            item.module_id for item in items
            if item.model_id in secondary and item.module_id
        ))

        hits.append(
            {
                "chain_id": chain_id,
                "entity_key": entity_key,
                "entity_name": top_items[0].entity_name,
                "entity_type": top_items[0].entity_type,
                "severity": severity,
                "risk_score": risk_score,
                "involved_models": [item.model_id for item in top_items],
                "involved_display_models": [item.display_id for item in top_items],
                "involved_modules": involved_module_ids,
                "model_names": [item.model_name for item in top_items],
                "primary_models": primary_hits,
                "primary_display_models": [legacy_to_display_id(model_id) for model_id in primary_hits],
                "primary_modules": primary_module_ids,
                "secondary_models": secondary_hits,
                "secondary_display_models": [legacy_to_display_id(model_id) for model_id in secondary_hits],
                "secondary_modules": secondary_module_ids,
                "risk_tags": unique_tags[:8],
                "evidence_summary": "；".join(f"{item.display_id}：{item.evidence}" for item in top_items if item.evidence)[:240],
                "recommended_action": definition["suggestion"],
                "source_count": len(items),
            }
        )

    hits.sort(key=lambda item: (-item["risk_score"], item["entity_name"]))
    return hits[:200]


def _pick_first_value(row_dict: dict[str, Any], columns: list[str]) -> str:
    for col in columns:
        value = row_dict.get(col)
        text = _stringify(value)
        if text:
            return text
    return ""


def _infer_entity_type(row_dict: dict[str, Any], entity_name: str) -> str:
    text = " ".join(str(v) for v in row_dict.values() if v is not None)
    for entity_type, hints in TYPE_HINTS.items():
        if any(hint in text or hint in entity_name for hint in hints):
            return entity_type
    return "项目"


def _infer_severity(row_dict: dict[str, Any]) -> str:
    joined = " ".join(_stringify(row_dict.get(col)) for col in SEVERITY_COLUMNS if row_dict.get(col) is not None)
    joined_lower = joined.lower()
    if any(token in joined_lower for token in ("red", "严重", "高", "严禁", "重大")):
        return "高"
    if any(token in joined_lower for token in ("yellow", "中", "限制", "预警")):
        return "中"
    return "低"


def _extract_tags(row_dict: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for col in TAG_COLUMNS:
        text = _stringify(row_dict.get(col))
        if not text:
            continue
        parts = [part.strip() for part in text.replace("；", "、").replace(";", "、").split("、") if part.strip()]
        tags.extend(parts[:3])
    return list(dict.fromkeys(tags))


def _extract_evidence(row_dict: dict[str, Any]) -> str:
    snippets = []
    for col in DETAIL_COLUMNS:
        text = _stringify(row_dict.get(col))
        if text:
            snippets.append(text)
        if len(snippets) >= 2:
            break
    if snippets:
        return "；".join(snippets)[:80]

    tags = _extract_tags(row_dict)
    if tags:
        return "、".join(tags[:3])
    return "发现异常特征，建议结合明细结果复核。"


def _severity_score(severity: str) -> int:
    if severity == "高":
        return 5
    if severity == "中":
        return 3
    return 1


def _score_to_level(score: int) -> str:
    if score >= 14:
        return "高"
    if score >= 8:
        return "中"
    return "低"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text
