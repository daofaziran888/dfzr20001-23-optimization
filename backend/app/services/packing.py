"""包装规格领域服务：管理包装形式、净含量校验、标签文案归档。"""
from __future__ import annotations
from typing import Any
from app.services.base import BaseModuleService
from app.store import store

class PackingService(BaseModuleService):
    MODULE = "packing"
    ENTITY = "包装批"
    OPERATION_TERM = "确认包装"
    LIST_FIELDS = ["包装形式", "净含量", "标签文案", "封口方式", "标签状态", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["包装形式", "净含量"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认包装": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    VALID_FORMATS = ["散装", "袋装", "罐装", "礼盒装", "小泡装", "饼茶", "砖茶"]

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        fmt = values.get("包装形式", "")
        if fmt not in self.VALID_FORMATS:
            pass  # 允许自定义包装形式
        net_weight = store.parse_weight(values.get("净含量", "0"))
        if net_weight <= 0:
            errors.append("净含量必须大于0")
        return errors

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        """确认包装时，需要关联已确认的拼配批次。"""
        if action == "确认包装":
            batch_no = entry.get("批次号", "")
            if batch_no:
                lineage = store.get_batch_lineage_nodes(batch_no)
                has_blending = any(n["module"] == "blending" for n in lineage)
                if has_blending:
                    for n in lineage:
                        if n["module"] == "blending":
                            blend = store.find("blending", n["entry_id"])
                            if blend and blend.get("status") != "已确认":
                                return False, "该批次的拼配配方尚未确认，请先确认配方后再确认包装"
        return True, None

    def get_packing_by_batch(self, batch_no: str) -> dict[str, Any] | None:
        """获取指定批次的包装规格。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("批次号") == batch_no and r.get("status") == "已确认":
                return r
        return None
