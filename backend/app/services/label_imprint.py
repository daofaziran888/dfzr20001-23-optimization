"""标签留印凭据领域服务。

设计目标（对应用户核对一致性需求）：

1. 每次在包装台签发标签都生成一条不可变「留印凭据」，凭据固定真实批次号、
   留印时间、确认进口与当时的标签文案快照；
2. 凭据与具体包装记录绑定，不同包装即使文案完全相同也不能互用；
3. 同一包装批的每一次印签带有递增印次与唯一凭据编号/内容指纹，天然不可互抵；
4. 仓库扫码后凭据进入「已扫码」，发运确认只能消费已扫码且与当前文案一致的印件；
5. 同一批实物只允许一个有效发运号，重印只会作废旧印件，不会产生第二张可发运凭证；
6. 未被实际发运消费的新印件始终是「未办」，不会因为印了就变成已办；
7. 已确认发运的回执保留被采纳印件的真实标签快照，事后更正文案不影响历史回执。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.store import store

# 凭据状态机
IMPRINT_ISSUED = "已签发"      # 包装台已印，仓库尚未扫码
IMPRINT_SCANNED = "已扫码"     # 仓库已扫码，等待发运确认消费
IMPRINT_CONSUMED = "已采纳"    # 已被实际发运确认消费（已办）
IMPRINT_VOID = "已作废"        # 被同包装批的更正重印替代

IMPRINT_STATUSES = (IMPRINT_ISSUED, IMPRINT_SCANNED, IMPRINT_CONSUMED, IMPRINT_VOID)

# 确认进口
ENTRY_PACKING = "包装台"
ENTRY_SHIPMENT = "发运台"


class LabelImprintService:
    MODULE = "label_imprint"

    # ── 基础查询 ──

    def _all(self) -> list[dict[str, Any]]:
        return store.rows("label_imprint")

    def find_imprint(self, imprint_id: int) -> dict[str, Any] | None:
        return store.find("label_imprint", imprint_id)

    def find_by_no(self, imprint_no: str) -> dict[str, Any] | None:
        for row in self._all():
            if row.get("印件编号") == imprint_no:
                return row
        return None

    def list_for_packing(self, packing_id: int) -> list[dict[str, Any]]:
        """某条包装记录的全部留印凭据，按印次升序（旧印在前，新印在后）。"""
        rows = [r for r in self._all() if int(r.get("packing_id", 0)) == packing_id]
        return sorted(rows, key=lambda r: int(r.get("sequence", 0)))

    def list_for_batch(self, batch_no: str) -> list[dict[str, Any]]:
        rows = [r for r in self._all() if r.get("批次号") == batch_no]
        return sorted(rows, key=lambda r: int(r.get("sequence", 0)))

    def next_sequence(self, packing_id: int) -> int:
        existing = self.list_for_packing(packing_id)
        return max((int(r.get("sequence", 0)) for r in existing), default=0) + 1

    @staticmethod
    def _now() -> str:
        # 微秒精度：同一包装批连续两次印签的留印时间也不会相同
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

    def _fingerprint(self, packing_id: int, batch_no: str, sequence: int,
                     label_text: str, issued_at: str) -> str:
        # 内容指纹：绑定包装记录、批次、印次、文案与留印时间，
        # 同一包装批的两次印签即使文案雷同，印次/时间不同指纹也不同。
        raw = f"{packing_id}|{batch_no}|{sequence}|{label_text}|{issued_at}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()

    # ── 包装台：签发 / 更正 / 重印 ──

    def issue_imprint(self, packing_id: int, operator: str = "",
                      reason: str = "") -> tuple[dict[str, Any] | None, str]:
        """在包装台为指定包装记录签发一张标签留印凭据。"""
        packing = store.find("packing", packing_id)
        if packing is None:
            return None, f"包装记录 {packing_id} 不存在，无法签发标签"

        batch_no = str(packing.get("批次号") or "").strip()
        if not batch_no:
            return None, "该包装批缺少真实批次号，不允许签发留印凭据"

        label_text = str(packing.get("标签文案") or "").strip()
        if not label_text:
            return None, "标签文案为空，不允许签发留印凭据"

        # 同一批实物只允许一个有效发运号：已被发运消费的批次不能再签发新印件
        consumed = self._consumed_for_batch(batch_no)
        if consumed is not None:
            return None, (
                f"批次 {batch_no} 已有有效发运号 {consumed.get('物流单号', '')}，"
                "实物已发运，不能再签发新标签"
            )

        sequence = self.next_sequence(packing_id)
        issued_at = self._now()
        imprint_no = f"LB-{batch_no}-{sequence:02d}"
        fingerprint = self._fingerprint(packing_id, batch_no, sequence, label_text, issued_at)

        rows = self._all()
        imprint = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "印件编号": imprint_no,
            "批次号": batch_no,
            "packing_id": packing_id,
            "packing_no": packing.get("登记编号", ""),
            "sequence": sequence,
            "标签文案快照": label_text,
            "留印时间": issued_at,
            "确认进口": ENTRY_PACKING,
            "状态": IMPRINT_ISSUED,
            "内容指纹": fingerprint,
            "operator": operator or packing.get("负责人", "") or "包装台操作员",
            "shipment_id": None,
            "物流单号": "",
            "扫码时间": "",
            "采纳时间": "",
            "更正原因": reason,
            "supersedes": None,
        }
        rows.append(imprint)

        # 包装记录回写当前有效印件与待重印标记清除
        packing["current_imprint_id"] = imprint["id"]
        packing["待重印"] = False
        packing["标签状态"] = "已留印待扫码"

        store.log_event("label_imprint", imprint["id"], "issue",
                        {"packing_id": packing_id, "批次号": batch_no, "sequence": sequence})
        return imprint, f"留印凭据 {imprint_no} 已签发（第{sequence}印，确认进口：包装台）"

    def correct_label_text(self, packing_id: int, new_text: str,
                           operator: str = "", reason: str = "") -> tuple[dict[str, Any] | None, str]:
        """包装台提交更正标签文案；在重新印签之前只挂起，不产生新凭据。"""
        packing = store.find("packing", packing_id)
        if packing is None:
            return None, f"包装记录 {packing_id} 不存在"

        new_text = (new_text or "").strip()
        if not new_text:
            return None, "更正后的标签文案不能为空"

        batch_no = str(packing.get("批次号") or "").strip()
        if not batch_no:
            return None, "该包装批缺少真实批次号，不允许更正标签"

        consumed = self._consumed_for_batch(batch_no)
        if consumed is not None:
            return None, (
                f"批次 {batch_no} 已由发运号 {consumed.get('物流单号', '')} 实际发运，"
                "历史回执保留真实标签，不允许再更正"
            )

        old_text = str(packing.get("标签文案") or "")
        if new_text == old_text:
            return None, "更正文案与现行标签文案完全相同，无需更正"

        history = list(packing.get("correction_history") or [])
        history.append({
            "原文案": old_text,
            "更正文案": new_text,
            "更正时间": self._now(),
            "操作人": operator or "包装台操作员",
            "原因": reason,
            "已重印": False,
        })
        packing["correction_history"] = history
        # 现行文案立即更新，留印凭据上的旧快照不动 -> 重新进入即可见差异
        packing["标签文案"] = new_text
        packing["待重印"] = True
        packing["标签状态"] = "文案已更正待重印"

        store.log_event("packing", packing_id, "correct_label",
                        {"批次号": batch_no, "原文案": old_text, "更正文案": new_text})
        return packing, "标签文案已更正，新印件尚未签发；旧印件在重印前继续留档"

    def reprint_imprint(self, packing_id: int, operator: str = "",
                        reason: str = "") -> tuple[dict[str, Any] | None, str]:
        """包装台重印：作废旧的未消费印件，再按当前文案签发新印件。"""
        packing = store.find("packing", packing_id)
        if packing is None:
            return None, f"包装记录 {packing_id} 不存在"

        batch_no = str(packing.get("批次号") or "").strip()
        consumed = self._consumed_for_batch(batch_no) if batch_no else None
        if consumed is not None:
            return None, (
                f"批次 {batch_no} 已由发运号 {consumed.get('物流单号', '')} 实际发运，不能重印"
            )

        prior = [r for r in self.list_for_packing(packing_id)
                 if r.get("状态") in (IMPRINT_ISSUED, IMPRINT_SCANNED)]
        if not prior:
            return None, "该包装批没有可替代的有效旧印件，无需重印，可直接签发"

        old = prior[-1]
        old["状态"] = IMPRINT_VOID
        old["作废时间"] = self._now()
        old["作废原因"] = reason or "包装台更正文案后重印"
        store.log_event("label_imprint", old["id"], "void",
                        {"packing_id": packing_id, "原因": old["作废原因"]})

        new_imprint, msg = self.issue_imprint(packing_id, operator=operator, reason=reason)
        if new_imprint is not None:
            new_imprint["supersedes"] = old["id"]
            # 标记最近一次更正已重印
            history = packing.get("correction_history") or []
            if history:
                history[-1]["已重印"] = True
                history[-1]["重印印件编号"] = new_imprint["印件编号"]
            packing["标签状态"] = "重印已签发待扫码"
        return new_imprint, msg

    # ── 发运台：仓库扫码 ──

    def scan_imprint(self, imprint_id: int, shipment_id: int,
                     operator: str = "") -> tuple[dict[str, Any] | None, str]:
        """仓库把标签扫码并挂到发运单上（发运班暂停确认时也允许先扫码）。"""
        imprint = self.find_imprint(imprint_id)
        if imprint is None:
            return None, f"留印凭据 {imprint_id} 不存在"
        shipment = store.find("shipment", shipment_id)
        if shipment is None:
            return None, f"发运单 {shipment_id} 不存在"

        if imprint.get("状态") in (IMPRINT_VOID,):
            return None, f"印件 {imprint.get('印件编号')} 已作废（已被更正重印替代），不能扫码"
        if imprint.get("状态") == IMPRINT_CONSUMED:
            return None, f"印件 {imprint.get('印件编号')} 已被发运消费，不能重复扫码"

        # 绑定校验：印件只能挂到同批次的发运单，不同包装/批次不能互用
        ship_batch = str(shipment.get("批次号") or "").strip()
        if ship_batch and ship_batch != imprint.get("批次号"):
            return None, (
                f"印件属于批次 {imprint.get('批次号')}，"
                f"与发运单批次 {ship_batch} 不一致，禁止跨批互用"
            )

        imprint["状态"] = IMPRINT_SCANNED
        imprint["扫码时间"] = self._now()
        imprint["确认进口"] = ENTRY_SHIPMENT
        imprint["scan_operator"] = operator or "仓库扫码员"
        imprint["shipment_id"] = shipment_id
        shipment["scanned_imprint_id"] = imprint_id
        shipment["scanned_imprint_no"] = imprint.get("印件编号", "")
        if not shipment.get("批次号"):
            shipment["批次号"] = imprint.get("批次号", "")

        packing = store.find("packing", int(imprint.get("packing_id", 0)))
        if packing is not None:
            packing["标签状态"] = "标签已扫码待发运确认"

        store.log_event("label_imprint", imprint_id, "scan",
                        {"shipment_id": shipment_id, "批次号": imprint.get("批次号")})
        return imprint, f"印件 {imprint.get('印件编号')} 已扫码并挂到发运单 {shipment_id}"

    # ── 发运台：确认前核对与消费 ──

    def verify_for_shipment(self, shipment: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any] | None]:
        """发运确认前的印件核对，返回 (是否通过, 拒绝原因, 被核对的印件)。"""
        shipment_id = int(shipment.get("id", 0))
        imprint_id = shipment.get("scanned_imprint_id")
        if not imprint_id:
            return False, "该发运单尚未扫码绑定包装标签留印凭据，不允许确认发运", None

        imprint = self.find_imprint(int(imprint_id))
        if imprint is None:
            return False, "绑定的留印凭据不存在，不允许确认发运", None

        if imprint.get("状态") == IMPRINT_VOID:
            return False, (
                f"扫码的旧印件 {imprint.get('印件编号')} 已因标签更正被作废，"
                "请用包装台重印的新标签重新扫码后再确认"
            ), imprint
        if imprint.get("状态") == IMPRINT_CONSUMED:
            return False, f"印件 {imprint.get('印件编号')} 已被其他发运单消费", imprint
        if imprint.get("状态") != IMPRINT_SCANNED:
            return False, f"印件 {imprint.get('印件编号')} 尚未完成仓库扫码，不允许确认发运", imprint

        # 印件绑定的包装记录与发运批次必须一致
        packing = store.find("packing", int(imprint.get("packing_id", 0)))
        if packing is None:
            return False, "印件对应的包装记录不存在，不允许确认发运", imprint
        ship_batch = str(shipment.get("批次号") or "").strip()
        if ship_batch and ship_batch != imprint.get("批次号"):
            return False, (
                f"发运单批次 {ship_batch} 与印件批次 {imprint.get('批次号')} 不一致，禁止跨批发运"
            ), imprint

        # 文案一致性：印件快照必须等于包装台当前文案
        current_text = str(packing.get("标签文案") or "")
        if imprint.get("标签文案快照") != current_text:
            return False, (
                "扫码印件的标签文案与包装台现行文案不一致：包装台已提交更正但新标签尚未重印扫码，"
                "请先在包装台重印并由仓库重新扫码，再恢复发运确认"
            ), imprint

        return True, None, imprint

    def consume_by_shipment(self, shipment: dict[str, Any]) -> tuple[bool, str | None]:
        """发运确认成功后消费印件，并在发运单上固化真实标签回执。"""
        ok, reason, imprint = self.verify_for_shipment(shipment)
        if not ok:
            return False, reason

        batch_no = str(shipment.get("批次号") or imprint.get("批次号") or "")
        # 同一批实物只可能有一个有效发运号
        existing = self._consumed_for_batch(batch_no, exclude_shipment_id=int(shipment.get("id", 0)))
        if existing is not None:
            return False, (
                f"批次 {batch_no} 已存在有效发运号 {existing.get('物流单号', '')}，"
                "重印标签不能让两份文案分别发运"
            )

        imprint["状态"] = IMPRINT_CONSUMED
        imprint["采纳时间"] = self._now()
        imprint["确认进口"] = ENTRY_SHIPMENT
        imprint["shipment_id"] = int(shipment["id"])
        imprint["物流单号"] = shipment.get("物流单号", "")

        # 发运回执固化：保留真正被采纳印件的真实标签，后续文案更正不改写这里
        shipment["adopted_imprint_id"] = imprint["id"]
        shipment["adopted_imprint_no"] = imprint.get("印件编号", "")
        shipment["adopted_label_text"] = imprint.get("标签文案快照", "")
        shipment["adopted_at"] = imprint["采纳时间"]
        shipment["label_receipt_locked"] = True
        shipment["采纳印件编号"] = imprint.get("印件编号", "")

        packing = store.find("packing", int(imprint.get("packing_id", 0)))
        if packing is not None:
            packing["标签状态"] = "标签已随批发运采纳"
            packing["adopted_imprint_id"] = imprint["id"]
            packing["采纳印件编号"] = imprint.get("印件编号", "")

        store.log_event("label_imprint", imprint["id"], "consume",
                        {"shipment_id": shipment["id"], "物流单号": shipment.get("物流单号", ""),
                         "批次号": batch_no})
        return True, None

    # ── 共享核对视图 ──

    def _consumed_for_batch(self, batch_no: str,
                            exclude_shipment_id: int | None = None) -> dict[str, Any] | None:
        for r in self._all():
            if r.get("批次号") != batch_no or r.get("状态") != IMPRINT_CONSUMED:
                continue
            if exclude_shipment_id is not None and r.get("shipment_id") == exclude_shipment_id:
                continue
            return r
        return None

    def packing_reconciliation(self, packing_id: int) -> dict[str, Any]:
        """包装台核对视图：旧印、更正、当前印件与实际出货状态。"""
        packing = store.find("packing", packing_id)
        if packing is None:
            return {"ok": False, "message": f"包装记录 {packing_id} 不存在"}

        imprints = self.list_for_packing(packing_id)
        batch_no = packing.get("批次号", "")
        adopted = next((r for r in imprints if r.get("状态") == IMPRINT_CONSUMED), None)
        active = next((r for r in reversed(imprints)
                       if r.get("状态") in (IMPRINT_ISSUED, IMPRINT_SCANNED)), None)
        shipment_rows = [s for s in store.rows("shipment") if s.get("批次号") == batch_no]
        shipped = next((s for s in shipment_rows if s.get("status") == "已确认"), None)

        return {
            "ok": True,
            "packing_id": packing_id,
            "批次号": batch_no,
            "现行标签文案": packing.get("标签文案", ""),
            "pending_reprint": bool(packing.get("待重印")),
            "label_status": packing.get("标签状态", "未留印"),
            "correction_history": packing.get("correction_history") or [],
            "imprints": [dict(r) for r in imprints],
            "active_imprint": dict(active) if active else None,
            "adopted_imprint": dict(adopted) if adopted else None,
            "shipments": [
                {
                    "shipment_id": s.get("id"),
                    "物流单号": s.get("物流单号", ""),
                    "status": s.get("status", ""),
                    "scanned_imprint_no": s.get("scanned_imprint_no", ""),
                    "adopted_imprint_no": s.get("adopted_imprint_no", ""),
                    "adopted_label_text": s.get("adopted_label_text", ""),
                }
                for s in shipment_rows
            ],
            "actual_shipment_no": shipped.get("物流单号", "") if shipped else "",
            # 已办/未办：被实际发运消费才算已办，只印未消费始终未办
            "done": adopted is not None,
            "pending_count": sum(1 for r in imprints
                                 if r.get("状态") in (IMPRINT_ISSUED, IMPRINT_SCANNED)),
        }

    def shipment_reconciliation(self, shipment_id: int) -> dict[str, Any]:
        """发运台核对视图：实际用了哪份印件、当前还能不能确认。"""
        shipment = store.find("shipment", shipment_id)
        if shipment is None:
            return {"ok": False, "message": f"发运单 {shipment_id} 不存在"}

        batch_no = shipment.get("批次号", "")
        imprints = self.list_for_batch(batch_no) if batch_no else []
        scanned_id = shipment.get("scanned_imprint_id")
        scanned = self.find_imprint(int(scanned_id)) if scanned_id else None
        adopted_id = shipment.get("adopted_imprint_id")
        adopted = self.find_imprint(int(adopted_id)) if adopted_id else None

        can_confirm = False
        block_reason = ""
        if shipment.get("status") == "已确认":
            block_reason = "发运单已确认，回执已锁定真实标签"
        else:
            ok, reason, _ = self.verify_for_shipment(shipment)
            can_confirm = ok
            block_reason = "" if ok else (reason or "印件核对未通过")

        return {
            "ok": True,
            "shipment_id": shipment_id,
            "批次号": batch_no,
            "物流单号": shipment.get("物流单号", ""),
            "shipment_status": shipment.get("status", ""),
            "scanned_imprint": dict(scanned) if scanned else None,
            "adopted_imprint": dict(adopted) if adopted else None,
            "adopted_label_text": shipment.get("adopted_label_text", ""),
            "receipt_locked": bool(shipment.get("label_receipt_locked")),
            "all_imprints": [dict(r) for r in imprints],
            "can_confirm": can_confirm,
            "block_reason": block_reason,
            "done": shipment.get("status") == "已确认",
        }


label_service = LabelImprintService()
