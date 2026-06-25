"""
统一阈值读取工具 —— 支持 _unit_overrides 单位差异化覆盖。

用法:
    from utils.threshold_resolver import ThresholdResolver

    resolver = ThresholdResolver(config)
    value = resolver.get(["experience_warnings", "客户流失_连续未合作月数"])
    value = resolver.get(["institutional", "盈利底线", "手册0520", "承接效益率_严禁投标_上限"], unit_name="安装公司")

支持两种阈值格式:
    1. 简单值: "xxx": 0.05
    2. 注解值: "xxx": {"value": 0.05, "_source": "...", "_unit_overrides": {...}}

当 unit_name 不为空时，自动检查 _unit_overrides 并返回覆盖值。
"""

from typing import Any, Optional, List, Union


class ThresholdResolver:
    """统一阈值解析器，封装 config 读取 + 单位覆盖逻辑。"""

    def __init__(self, config: dict):
        self._config = config

    @property
    def config(self) -> dict:
        return self._config

    def get(self, path: List[str], unit_name: Optional[str] = None, default: Any = None) -> Any:
        """
        按路径读取阈值。

        Args:
            path: 配置键路径，如 ["experience_warnings", "客户流失_连续未合作月数"]
            unit_name: 可选的二级单位简称，用于查询 _unit_overrides
            default: 未找到时的默认值

        Returns:
            阈值数值（已解析 _unit_overrides）
        """
        node = self._resolve_path(path)
        if node is None:
            return default

        # 解析注解格式 {"value": x, "_source": "...", ...}
        if isinstance(node, dict):
            # 有 _unit_overrides 且传入了 unit_name → 优先返回覆盖值
            if unit_name:
                overrides = node.get("_unit_overrides", {})
                if unit_name in overrides:
                    return overrides[unit_name]

            # 取 value 字段
            if "value" in node:
                return node["value"]

            # 没有 value 字段 → 整个 dict 就是值（如嵌套配置段）
            return node

        # 简单值直接返回
        return node

    def get_meta(self, path: List[str]) -> dict:
        """
        获取阈值的元信息（_source, _category 等），不含 value 本身。

        Returns:
            元信息 dict，如果阈值是简单值则返回空 dict
        """
        node = self._resolve_path(path)
        if isinstance(node, dict):
            return {k: v for k, v in node.items() if k.startswith("_")}
        return {}

    def get_with_meta(self, path: List[str], unit_name: Optional[str] = None, default: Any = None) -> dict:
        """
        返回 {value: ..., meta: {...}} 结构，用于前端展示。
        """
        node = self._resolve_path(path)
        if node is None:
            return {"value": default, "meta": {}}

        value = self.get(path, unit_name=unit_name, default=default)

        if isinstance(node, dict):
            meta = {k: v for k, v in node.items() if k.startswith("_")}
            # 扁平化 unit_overrides 中的 meta
            clean_meta = {}
            for k, v in meta.items():
                if k == "_unit_overrides":
                    clean_meta[k] = v
                else:
                    clean_meta[k.lstrip("_")] = v
            return {"value": value, "meta": clean_meta}

        return {"value": value, "meta": {}}

    def get_section(self, path: List[str]) -> dict:
        """
        获取整个配置段（不经阈值解析，原样返回 dict）。
        用于读取盈利底线、付款条件底线等嵌套结构。
        """
        node = self._resolve_path(path)
        if isinstance(node, dict):
            return node
        return {}

    def _resolve_path(self, path: List[str]) -> Any:
        """沿路径遍历 config 树。"""
        node = self._config
        for part in path:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
            if node is None:
                return None
        return node


# 便捷函数，兼容旧代码
def get_threshold(config: dict, path: List[str], unit_name: Optional[str] = None, default: Any = None) -> Any:
    """便捷函数，等价于 ThresholdResolver(config).get(path, unit_name, default)。"""
    return ThresholdResolver(config).get(path, unit_name=unit_name, default=default)


def resolve_threshold(config: dict, path: List[str], unit_name: Optional[str] = None, default: Any = None) -> Any:
    """同 get_threshold，别名。"""
    return get_threshold(config, path, unit_name=unit_name, default=default)
