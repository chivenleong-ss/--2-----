from __future__ import annotations

from copy import deepcopy
import re


LEGACY_MODEL_REGISTRY = {
    "1.1": {"name": "区域布局动态偏差检测与窜区预警", "dim": "维度一：战略与布局"},
    "1.2": {"name": "业务结构战略性偏差检测（v4.0 城市更新+新兴业务占比<5%）", "dim": "维度一：战略与布局"},
    "1.3": {"name": "战略客户组合监控", "dim": "维度三：客户健康度"},
    "1.4": {"name": "营销统计数据多维交叉验真", "dim": "维度一：战略与布局"},
    "2.1": {"name": "风险分级严禁投标底线检测", "dim": "维度二：合同质量与风险"},
    "2.2": {"name": "盈利底线与效益偏差检测", "dim": "维度二：合同质量与风险"},
    "2.3": {"name": "保证金与预收款资金安全监控", "dim": "维度二：合同质量与风险"},
    "2.4": {"name": "合同条款风险穿透", "dim": "维度二：合同质量与风险"},
    "2.5": {"name": "施工真实性验证（v4.0 签约>12月产值<10%即red）", "dim": "维度二：合同质量与风险"},
    "3.1": {"name": "客户全生命周期监控（中标转化+流失分级+评级核查）", "dim": "维度三：客户健康度"},
    "3.2": {"name": "新客户质量评估与客户结构优化", "dim": "维度三：客户健康度"},
    "3.3": {"name": "僵尸客户清理与客户评级内控核查", "dim": "维度三：客户健康度"},
}


MODEL_ID_MAPPING = {
    "1.1": {"display_id": "1.1", "module_id": 1, "module_name": "模块一：区域布局"},
    "1.2": {"display_id": "1.2", "module_id": 1, "module_name": "模块一：区域布局"},
    "1.3": {"display_id": "2.1", "module_id": 2, "module_name": "模块二：客户稳定"},
    "3.1": {"display_id": "2.2", "module_id": 2, "module_name": "模块二：客户稳定"},
    "3.2": {"display_id": "2.3", "module_id": 2, "module_name": "模块二：客户稳定"},
    "3.3": {"display_id": "2.4", "module_id": 2, "module_name": "模块二：客户稳定"},
    "2.1": {"display_id": "3.1", "module_id": 3, "module_name": "模块三：合同质量"},
    "2.4": {"display_id": "3.2", "module_id": 3, "module_name": "模块三：合同质量"},
    "2.2": {"display_id": "4.1", "module_id": 4, "module_name": "模块四：履约盈利"},
    "2.5": {"display_id": "4.2", "module_id": 4, "module_name": "模块四：履约盈利"},
    "2.3": {"display_id": "5.1", "module_id": 5, "module_name": "模块五：资金效率"},
    "1.4": {"display_id": "6.1", "module_id": 6, "module_name": "模块六：数据质量"},
}


DISPLAY_TO_LEGACY = {
    meta["display_id"]: legacy_id for legacy_id, meta in MODEL_ID_MAPPING.items()
}


def legacy_to_display_id(model_id: str) -> str:
    return MODEL_ID_MAPPING.get(str(model_id), {}).get("display_id", str(model_id))


def display_to_legacy_id(model_id: str) -> str:
    raw = str(model_id)
    return DISPLAY_TO_LEGACY.get(raw, raw)


def get_model_display_name(model_id: str, include_legacy: bool = True) -> str:
    # model_id 已经是 legacy ID（来自 build_display_registry 的 LEGACY_MODEL_REGISTRY 迭代）
    # 严禁调用 display_to_legacy_id() —— 某些 legacy ID（如 "2.3"）恰好也是另一个模型的 display_id，
    # 逆向映射会错误地跳到另一个模型（"2.3"→"3.2"），导致显示名称张冠李戴。
    base = LEGACY_MODEL_REGISTRY.get(model_id, {}).get("name", model_id)
    display_id = legacy_to_display_id(model_id)
    label = f"{display_id} {base}"
    # 删除所有括号内的内容（中文括号、英文括号、以及"原X.X"等遗留标记）
    label = re.sub(r"\s*[（(][^）)]*[）)]", "", label)
    return label.strip()


def build_display_registry(include_legacy: bool = True) -> dict[str, dict[str, str | int]]:
    registry = {}
    for legacy_id, meta in LEGACY_MODEL_REGISTRY.items():
        mapping = MODEL_ID_MAPPING.get(legacy_id, {})
        item = deepcopy(meta)
        item["legacy_id"] = legacy_id
        item["display_id"] = mapping.get("display_id", legacy_id)
        item["module_id"] = mapping.get("module_id")
        item["module_name"] = mapping.get("module_name", "")
        item["label"] = get_model_display_name(legacy_id, include_legacy=include_legacy)
        registry[legacy_id] = item
    return registry
