"""标签留印凭据路由：包装台签发/更正/重印，仓库扫码，两侧核对视图。

路径均为多段结构，不会被通用资源路由 /api/{module}/{entry_id} 抢占。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.label_imprint import label_service
from app.store import store

router = APIRouter(prefix="/api", tags=["标签留印凭据"])


class CorrectPayload(BaseModel):
    new_text: str
    operator: str = ""
    reason: str = ""


class IssuePayload(BaseModel):
    operator: str = ""
    reason: str = ""


class ScanPayload(BaseModel):
    imprint_id: int | None = None
    imprint_no: str = ""
    operator: str = ""


def _require_packing(packing_id: int) -> dict[str, Any]:
    row = store.find("packing", packing_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"包装记录 {packing_id} 不存在")
    return row


def _require_shipment(shipment_id: int) -> dict[str, Any]:
    row = store.find("shipment", shipment_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"发运单 {shipment_id} 不存在")
    return row


# ── 包装台 ──

@router.get("/packing/{packing_id}/imprints")
def list_packing_imprints(packing_id: int) -> dict[str, Any]:
    """某条包装批的全部留印凭据（旧印在前，新印在后）。"""
    _require_packing(packing_id)
    return {"ok": True, "items": label_service.list_for_packing(packing_id)}


@router.post("/packing/{packing_id}/imprints/issue")
def issue_imprint(packing_id: int, payload: IssuePayload | None = None) -> dict[str, Any]:
    """包装台签发标签留印凭据。"""
    _require_packing(packing_id)
    payload = payload or IssuePayload()
    imprint, message = label_service.issue_imprint(
        packing_id, operator=payload.operator, reason=payload.reason)
    if imprint is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "entry": imprint}


@router.post("/packing/{packing_id}/imprints/correct")
def correct_imprint(packing_id: int, payload: CorrectPayload) -> dict[str, Any]:
    """包装台提交更正标签文案（未重印前只挂起，旧印件继续留档）。"""
    _require_packing(packing_id)
    entry, message = label_service.correct_label_text(
        packing_id, payload.new_text, operator=payload.operator, reason=payload.reason)
    if entry is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "entry": entry}


@router.post("/packing/{packing_id}/imprints/reprint")
def reprint_imprint(packing_id: int, payload: IssuePayload | None = None) -> dict[str, Any]:
    """包装台重印：旧印件作废，按现行文案签发新印件。"""
    _require_packing(packing_id)
    payload = payload or IssuePayload()
    imprint, message = label_service.reprint_imprint(
        packing_id, operator=payload.operator, reason=payload.reason)
    if imprint is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "entry": imprint}


@router.get("/packing/{packing_id}/reconciliation")
def packing_reconciliation(packing_id: int) -> dict[str, Any]:
    """包装台核对视图：旧印、更正、当前印件与实际出货状态，已办/未办同步可查。"""
    result = label_service.packing_reconciliation(packing_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "核对数据不存在"))
    return result


# ── 发运台 ──

@router.post("/shipment/{shipment_id}/scan-imprint")
def scan_imprint(shipment_id: int, payload: ScanPayload) -> dict[str, Any]:
    """仓库把标签扫码挂到发运单（支持印件 id 或印件编号）。"""
    _require_shipment(shipment_id)
    imprint = None
    if payload.imprint_id:
        imprint = label_service.find_imprint(payload.imprint_id)
    elif payload.imprint_no.strip():
        imprint = label_service.find_by_no(payload.imprint_no.strip())
    if imprint is None:
        raise HTTPException(status_code=404, detail="找不到对应的留印凭据，请确认印件编号")
    result, message = label_service.scan_imprint(
        int(imprint["id"]), shipment_id, operator=payload.operator)
    if result is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "entry": result}


@router.get("/shipment/{shipment_id}/reconciliation")
def shipment_reconciliation(shipment_id: int) -> dict[str, Any]:
    """发运台核对视图：实际用了哪份印件、当前是否还能确认。"""
    result = label_service.shipment_reconciliation(shipment_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "核对数据不存在"))
    return result
