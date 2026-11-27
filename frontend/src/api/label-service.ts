import { request } from './client'

// ── 包装台：标签留印凭据 ──

export type CorrectPayload = {
  new_text: string
  operator?: string
  reason?: string
}

export async function listPackingImprints(packingId: number): Promise<any> {
  const response = await request(`/api/packing/${packingId}/imprints`)
  return await response.json()
}

export async function issueImprint(packingId: number, payload: { operator?: string; reason?: string } = {}): Promise<any> {
  const response = await request(`/api/packing/${packingId}/imprints/issue`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function correctImprint(packingId: number, payload: CorrectPayload): Promise<any> {
  const response = await request(`/api/packing/${packingId}/imprints/correct`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function reprintImprint(packingId: number, payload: { operator?: string; reason?: string } = {}): Promise<any> {
  const response = await request(`/api/packing/${packingId}/imprints/reprint`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function fetchPackingReconciliation(packingId: number): Promise<any> {
  const response = await request(`/api/packing/${packingId}/reconciliation`)
  return await response.json()
}

// ── 发运台：扫码与核对 ──

export async function scanImprint(
  shipmentId: number,
  payload: { imprint_id?: number; imprint_no?: string; operator?: string },
): Promise<any> {
  const response = await request(`/api/shipment/${shipmentId}/scan-imprint`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function fetchShipmentReconciliation(shipmentId: number): Promise<any> {
  const response = await request(`/api/shipment/${shipmentId}/reconciliation`)
  return await response.json()
}
