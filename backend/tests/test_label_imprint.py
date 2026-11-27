"""标签留印凭据与发运确认核对一致性测试。

覆盖用户提出的完整场景：
- 留印凭据固定真实批次、时间、确认进口，同文案不同包装不可互用；
- 同一包装批两次印签必有差异；
- 原标签已交仓库扫码、发运暂停后包装台更正文案但未重印，发运确认被拦截；
- 重印作废旧印件，未被实际发运消费的新印件不算已办；
- 同一批实物只有一个有效发运号，重印不能让两份文案分别发运；
- 已确认回执保留真实标签；两侧核对视图同步可查。
"""
from __future__ import annotations

import unittest

from app.services.label_imprint import (
    IMPRINT_CONSUMED,
    IMPRINT_ISSUED,
    IMPRINT_SCANNED,
    IMPRINT_VOID,
    label_service,
)
from app.services import service_for
from app.store import store


class LabelImprintTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # 发运门禁：检测报告、认证资质、溯源码段就绪
        for r in store.rows("testing"):
            r["status"] = "已确认"
        for r in store.rows("certification"):
            r["status"] = "已确认"
            r["有效期至"] = "2027-12-31"
        for r in store.rows("trace_code"):
            r["status"] = "已确认"
        store._frozen.clear()

    def _make_packing(self, batch_no: str, label_text: str) -> dict:
        svc = service_for("packing")
        entry, errors = svc.create_entry({
            "包装形式": "袋装",
            "净含量": "0.25kg",
            "标签文案": label_text,
            "封口方式": "热封",
            "批次号": batch_no,
            "登记编号": f"PACK-{batch_no}",
            "负责人": "包装员甲",
        })
        self.assertIsNotNone(entry, f"包装登记失败: {errors}")
        entry["status"] = "已确认"
        entry["pending"] = False
        store.register_lineage(batch_no, "packing", int(entry["id"]))
        return entry

    def _make_shipment(self, batch_no: str, tracking_no: str) -> dict:
        svc = service_for("shipment")
        entry, errors = svc.create_entry({
            "收货方": "测试茶庄",
            "物流单号": tracking_no,
            "发运日期": "2026-11-27",
            "承运单位": "顺丰冷链",
            "批次号": batch_no,
            "登记编号": tracking_no,
            "负责人": "发运员乙",
        })
        self.assertIsNotNone(entry, f"发运登记失败: {errors}")
        store.register_lineage(batch_no, "shipment", int(entry["id"]))
        return entry


class TestImprintIdentity(LabelImprintTestBase):
    def test_imprint_pins_real_batch_time_and_entry(self):
        packing = self._make_packing("BATCH-LABEL-1", "高山乌龙 净含量250g")
        imprint, msg = label_service.issue_imprint(int(packing["id"]))
        self.assertIsNotNone(imprint, msg)
        self.assertEqual(imprint["批次号"], "BATCH-LABEL-1")
        self.assertEqual(imprint["确认进口"], "包装台")
        self.assertTrue(imprint["留印时间"])
        self.assertEqual(imprint["标签文案快照"], "高山乌龙 净含量250g")
        self.assertTrue(imprint["印件编号"])
        self.assertTrue(imprint["内容指纹"])

    def test_same_text_different_packing_not_interchangeable(self):
        p1 = self._make_packing("BATCH-LABEL-A", "完全相同的标签文案")
        p2 = self._make_packing("BATCH-LABEL-B", "完全相同的标签文案")
        i1, _ = label_service.issue_imprint(int(p1["id"]))
        i2, _ = label_service.issue_imprint(int(p2["id"]))
        self.assertNotEqual(i1["印件编号"], i2["印件编号"])
        self.assertNotEqual(i1["内容指纹"], i2["内容指纹"])

        shipment = self._make_shipment("BATCH-LABEL-B", "TRK-CROSS-1")
        # A 批印件挂到 B 批发运单必须被拒绝
        result, msg = label_service.scan_imprint(int(i1["id"]), int(shipment["id"]))
        self.assertIsNone(result)
        self.assertIn("不一致", msg)

    def test_two_imprints_same_packing_always_differ(self):
        packing = self._make_packing("BATCH-LABEL-2", "同一包装批文案")
        i1, _ = label_service.issue_imprint(int(packing["id"]))
        i2, _ = label_service.issue_imprint(int(packing["id"]))
        self.assertEqual(i1["sequence"], 1)
        self.assertEqual(i2["sequence"], 2)
        self.assertNotEqual(i1["印件编号"], i2["印件编号"])
        self.assertNotEqual(i1["内容指纹"], i2["内容指纹"])
        self.assertNotEqual(i1["留印时间"], i2["留印时间"])


class TestCorrectReprintAndShipment(LabelImprintTestBase):
    def test_full_correct_reprint_reconcile_flow(self):
        batch_no = "BATCH-LABEL-FULL"
        packing = self._make_packing(batch_no, "初版文案：特级铁观音")
        shipment = self._make_shipment(batch_no, "TRK-FULL-1")
        ship_svc = service_for("shipment")

        # 1. 包装台签发原印件，仓库扫码
        old_imprint, _ = label_service.issue_imprint(int(packing["id"]))
        scanned, msg = label_service.scan_imprint(int(old_imprint["id"]), int(shipment["id"]))
        self.assertIsNotNone(scanned, msg)
        self.assertEqual(scanned["状态"], IMPRINT_SCANNED)

        # 2. 发运班暂停确认（不执行确认发运），包装台提交更正但未重印
        corrected, msg = label_service.correct_label_text(
            int(packing["id"]), "更正文案：一级铁观音（产地溯源）", reason="等级标注错误")
        self.assertIsNotNone(corrected, msg)
        self.assertTrue(packing["待重印"])
        # 旧印件快照保持原文案，与现行文案产生差异
        self.assertEqual(old_imprint["标签文案快照"], "初版文案：特级铁观音")
        self.assertEqual(packing["标签文案"], "更正文案：一级铁观音（产地溯源）")

        # 3. 此时从发运台确认原已扫码的确认 -> 必须拦截
        entry, msg = ship_svc.run_action(int(shipment["id"]), "确认发运")
        self.assertIsNone(entry)
        self.assertIn("不一致", msg)
        self.assertEqual(shipment["status"], "草稿")

        # 4. 包装台重印：旧印作废、新印签发；新印未被消费，不能算已办
        new_imprint, msg = label_service.reprint_imprint(int(packing["id"]), reason="更正重印")
        self.assertIsNotNone(new_imprint, msg)
        self.assertEqual(old_imprint["状态"], IMPRINT_VOID)
        self.assertEqual(new_imprint["状态"], IMPRINT_ISSUED)
        self.assertEqual(new_imprint["supersedes"], old_imprint["id"])
        view = label_service.packing_reconciliation(int(packing["id"]))
        self.assertFalse(view["done"], "新印件未实际发运消费，不能当已办")
        self.assertEqual(view["pending_count"], 1)

        # 5. 未重新扫码直接确认 -> 仍拦截（挂着的还是已作废旧印）
        entry, msg = ship_svc.run_action(int(shipment["id"]), "确认发运")
        self.assertIsNone(entry)
        self.assertIn("作废", msg)

        # 6. 仓库扫新印件后再确认 -> 成功，实际采纳新印件
        scanned_new, msg = label_service.scan_imprint(int(new_imprint["id"]), int(shipment["id"]))
        self.assertIsNotNone(scanned_new, msg)
        entry, msg = ship_svc.run_action(int(shipment["id"]), "确认发运")
        self.assertIsNotNone(entry, msg)
        self.assertEqual(shipment["status"], "已确认")
        self.assertEqual(new_imprint["状态"], IMPRINT_CONSUMED)
        self.assertEqual(shipment["adopted_imprint_id"], new_imprint["id"])
        # 回执保留真正被采纳印件的真实标签
        self.assertEqual(shipment["adopted_label_text"], "更正文案：一级铁观音（产地溯源）")
        self.assertTrue(shipment["label_receipt_locked"])

        # 7. 已发运后不能再签发/重印/更正
        self.assertIsNone(label_service.issue_imprint(int(packing["id"]))[0])
        self.assertIsNone(label_service.reprint_imprint(int(packing["id"]))[0])
        self.assertIsNone(
            label_service.correct_label_text(int(packing["id"]), "再改一版")[0])

        # 8. 同批第二张发运单不能再确认（同一批实物只有一个有效发运号）
        shipment2 = self._make_shipment(batch_no, "TRK-FULL-2")
        # 新印件已被消费，无法再扫码；即便手工绑定已消费印件也必须拒绝
        result, msg = label_service.scan_imprint(int(new_imprint["id"]), int(shipment2["id"]))
        self.assertIsNone(result)
        self.assertIn("已被发运消费", msg)

    def test_reprint_cannot_produce_two_shippable_texts(self):
        """重印后旧印作废，只有新印件可能被消费，两份文案不能分别发运。"""
        batch_no = "BATCH-LABEL-TWO"
        packing = self._make_packing(batch_no, "文案甲")
        i1, _ = label_service.issue_imprint(int(packing["id"]))
        label_service.correct_label_text(int(packing["id"]), "文案乙")
        i2, _ = label_service.reprint_imprint(int(packing["id"]))

        self.assertEqual(i1["状态"], IMPRINT_VOID)
        shipment = self._make_shipment(batch_no, "TRK-TWO-1")
        # 作废旧印不能扫码
        self.assertIsNone(label_service.scan_imprint(int(i1["id"]), int(shipment["id"]))[0])
        # 新印扫码并确认
        self.assertIsNotNone(label_service.scan_imprint(int(i2["id"]), int(shipment["id"]))[0])
        entry, msg = service_for("shipment").run_action(int(shipment["id"]), "确认发运")
        self.assertIsNotNone(entry, msg)
        self.assertEqual(shipment["adopted_label_text"], "文案乙")

        # 同批再建发运单，消费校验直接拒绝第二有效发运号
        shipment2 = self._make_shipment(batch_no, "TRK-TWO-2")
        ok, reason, _ = label_service.verify_for_shipment(shipment2)
        self.assertFalse(ok)

    def test_confirmation_without_scan_blocked(self):
        batch_no = "BATCH-LABEL-NOSCAN"
        packing = self._make_packing(batch_no, "无扫码文案")
        label_service.issue_imprint(int(packing["id"]))
        shipment = self._make_shipment(batch_no, "TRK-NOSCAN-1")
        entry, msg = service_for("shipment").run_action(int(shipment["id"]), "确认发运")
        self.assertIsNone(entry)
        self.assertIn("尚未扫码", msg)

    def test_repeat_confirm_is_idempotent_and_consumes_once(self):
        """重复确认发运不会重复消费印件，已确认回执保持不变。"""
        batch_no = "BATCH-LABEL-IDEM"
        packing = self._make_packing(batch_no, "幂等文案")
        shipment = self._make_shipment(batch_no, "TRK-IDEM-1")
        imprint, _ = label_service.issue_imprint(int(packing["id"]))
        label_service.scan_imprint(int(imprint["id"]), int(shipment["id"]))

        first, msg1 = service_for("shipment").run_action(int(shipment["id"]), "确认发运")
        self.assertIsNotNone(first, msg1)
        self.assertEqual(imprint["状态"], IMPRINT_CONSUMED)
        adopted_at = imprint["采纳时间"]

        # 基类对已确认记录的二次确认直接拦截，印件状态与采纳时间保持不变（不重复消费）
        second, msg2 = service_for("shipment").run_action(int(shipment["id"]), "确认发运")
        self.assertIsNone(second)
        self.assertIn("已确认", msg2)
        self.assertEqual(imprint["状态"], IMPRINT_CONSUMED)
        self.assertEqual(imprint["采纳时间"], adopted_at)
        # 原发运单回执不受影响
        self.assertEqual(shipment["status"], "已确认")
        self.assertEqual(shipment["adopted_label_text"], "幂等文案")

    def test_both_reconciliation_views_sync(self):
        """已办/未办在包装台与发运台两个页面同步可查，重新进入可见旧印、更正与采纳印件。"""
        batch_no = "BATCH-LABEL-VIEW"
        packing = self._make_packing(batch_no, "视图文案V1")
        shipment = self._make_shipment(batch_no, "TRK-VIEW-1")

        i1, _ = label_service.issue_imprint(int(packing["id"]))
        label_service.scan_imprint(int(i1["id"]), int(shipment["id"]))
        label_service.correct_label_text(int(packing["id"]), "视图文案V2")
        i2, _ = label_service.reprint_imprint(int(packing["id"]))
        label_service.scan_imprint(int(i2["id"]), int(shipment["id"]))
        service_for("shipment").run_action(int(shipment["id"]), "确认发运")

        p_view = label_service.packing_reconciliation(int(packing["id"]))
        s_view = label_service.shipment_reconciliation(int(shipment["id"]))

        # 两侧都能看到全部印件：旧印（作废）、新印（采纳）
        self.assertEqual(len(p_view["imprints"]), 2)
        self.assertEqual(len(s_view["all_imprints"]), 2)
        self.assertTrue(p_view["done"] and s_view["done"])
        # 重新进入后可以看到旧印、更正记录和真正被采纳的印件
        self.assertEqual(p_view["adopted_imprint"]["印件编号"], i2["印件编号"])
        self.assertEqual(s_view["adopted_imprint"]["印件编号"], i2["印件编号"])
        self.assertEqual(p_view["correction_history"][0]["原文案"], "视图文案V1")
        self.assertEqual(p_view["correction_history"][0]["更正文案"], "视图文案V2")
        self.assertTrue(p_view["correction_history"][0]["已重印"])
        # 发运台明确显示还能不能确认（已确认 -> 不可再确认并说明原因）
        self.assertFalse(s_view["can_confirm"])
        self.assertTrue(s_view["block_reason"])
        # 实际出货状态
        self.assertEqual(p_view["actual_shipment_no"], "TRK-VIEW-1")


if __name__ == "__main__":
    unittest.main()
