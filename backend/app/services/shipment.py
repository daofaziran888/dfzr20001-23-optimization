"""发运交接领域服务：确认发运信息，校验收货方与物流信息完整性，关联溯源码段。

发运确认与包装标签留印凭据强绑定：
- 确认前必须已扫码绑定一张有效印件，且印件文案与包装台现行文案一致；
- 同一批实物只允许一个有效发运号，重印标签不能让两份文案分别发运；
- 确认成功即消费印件并在发运单固化真实标签回执，历史回执不受事后更正影响。
"""
from __future__ import annotations
from typing import Any
from app.services.base import BaseModuleService
from app.services.label_imprint import label_service
from app.store import store

class ShipmentService(BaseModuleService):
    MODULE = "shipment"
    ENTITY = "发运单"
    OPERATION_TERM = "确认发运"
    LIST_FIELDS = ["收货方", "物流单号", "发运日期", "承运单位", "采纳印件编号", "登记编号", "负责人", "生效版本", "备注说明"]
    REQUIRED_FIELDS = ["收货方", "物流单号", "发运日期", "承运单位"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已撤回"]
    ACTION_RULES = {"确认发运": "已确认", "提交确认": "待确认", "撤回记录": "已撤回"}
    NEGATIVE_ACTIONS = []

    def validate_entry(self, values: dict[str, Any]) -> list[str]:
        errors = []
        tracking_no = str(values.get("物流单号", "")).strip()
        if tracking_no:
            existing = [r for r in store.rows(self.MODULE)
                        if r.get("物流单号") == tracking_no and r.get("status") == "已确认"]
            if existing:
                errors.append(f"物流单号 {tracking_no} 已有已确认发运记录")
        return errors

    def on_confirm(self, entry: dict[str, Any], action: str) -> tuple[bool, str | None]:
        """发运确认前必须经过门禁。"""
        if action == "确认发运":
            # 标签留印凭据核对（扫码绑定、文案一致、未作废、未消费）
            ok, reason, _ = label_service.verify_for_shipment(entry)
            if not ok:
                return False, reason

            batch_no = entry.get("批次号", "")
            if batch_no:
                # 检测报告门禁
                all_testing = [r for r in store.rows("testing") if r.get("status") == "已确认"]
                if not all_testing:
                    return False, "缺少已签收的检测报告，不允许确认发运"

                # 认证资质门禁（有效期检查）
                from datetime import datetime
                all_cert = [r for r in store.rows("certification") if r.get("status") == "已确认"]
                valid_cert = False
                for c in all_cert:
                    try:
                        expiry = str(c.get("有效期至", ""))[:10]
                        if datetime.strptime(expiry, "%Y-%m-%d") >= datetime.now():
                            valid_cert = True
                            break
                    except ValueError:
                        continue
                if not valid_cert:
                    return False, "缺少有效期内的认证资质证书，不允许确认发运"

                # 溯源码段已激活
                trace_codes = [r for r in store.rows("trace_code") if r.get("status") == "已确认"]
                if not trace_codes:
                    return False, "缺少已激活的溯源码段，不允许确认发运"

                # 包装已确认
                lineage = store.get_batch_lineage_nodes(batch_no)
                for n in lineage:
                    if n["module"] == "packing":
                        pack_entry = store.find("packing", n["entry_id"])
                        if pack_entry and pack_entry.get("status") != "已确认":
                            return False, "该批次的包装规格尚未确认，请先确认包装后再发运"

                # 冻结检查
                for n in lineage:
                    if store.is_frozen(n["module"], n["entry_id"]):
                        reason = store.get_frozen_reason(n["module"], n["entry_id"])
                        return False, f"批次中存在冻结记录（{reason}），不允许发运"
        return True, None

    def get_shipped_batches(self) -> list[dict[str, Any]]:
        """获取已发运的批次清单。"""
        return [r for r in store.rows(self.MODULE) if r.get("status") == "已确认"]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """发运动作：确认成功后消费留印凭据并固化真实标签回执。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        before_status = entry.get("status")
        result, message = super().run_action(entry_id, action)
        # 仅在「确认发运」真正把状态推进到已确认时消费印件，保证幂等重复确认不会重复消费
        if (action == "确认发运" and result is not None
                and before_status != "已确认" and result.get("status") == "已确认"):
            consumed, err = label_service.consume_by_shipment(result)
            if not consumed:
                # 理论上 on_confirm 已拦截；兜底回滚状态，避免出现无凭据的已确认发运
                result["status"] = before_status or "待确认"
                result["pending"] = True
                return None, err or "留印凭据消费失败，发运确认已回滚"
            message = f"{message}，并已采纳标签 {result.get('adopted_imprint_no', '')}"
        return result, message

    def get_trace_codes_for_shipment(self, shipment_entry_id: int) -> list[dict[str, Any]]:
        """获取关联到发运单的溯源码段。"""
        return [r for r in store.rows("trace_code") if r.get("shipment_id") == shipment_entry_id]

    def get_shipment_lineage(self, shipment_entry_id: int) -> list[dict[str, Any]]:
        """获取发运单关联的完整批次追溯链。"""
        entry = store.find(self.MODULE, shipment_entry_id)
        if entry is None:
            return []
        batch_no = entry.get("批次号", "")
        if not batch_no:
            return []
        lineage = store.get_lineage(batch_no)
        result = []
        for node in lineage:
            row = store.find(node["module"], node["entry_id"])
            if row:
                result.append({
                    "module": node["module"],
                    "entry_id": node["entry_id"],
                    "status": row.get("status", ""),
                    "data": dict(row),
                })
        return result