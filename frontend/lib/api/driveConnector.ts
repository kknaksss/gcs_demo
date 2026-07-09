// SPEC-004 Drive connector/sync typed client — WORK-003 Phase 4.
// backend: app/schemas/drive_sync.py · app/schemas/documents.py 계약 기준.

import { api, ApiError } from "./client";

// ── types ────────────────────────────────────────────────────────────────────

export type ConnectorState =
  | "connected"
  | "disconnected"
  | "watch_expiring"
  | "error";

export interface ConnectorStatus {
  status: ConnectorState;
  scope: string;
  selected_folder_id: string | null;
  selected_folder_name: string | null;
  watch_channel_id: string | null;
  watch_expires_at: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  page_token: string | null;
}

export interface SyncRetryResult {
  processed: number;
  new_documents: number;
  updated_documents: number;
  unavailable_documents: number;
  skipped: number;
  failed: number;
  page_token: string | null;
}

export type SyncEventType =
  | "webhook_received"
  | "changes_listed"
  | "document_upserted"
  | "document_unavailable"
  | "candidate_staled"
  | "reanalysis_enqueued"
  | "sync_failed";

export interface SyncEvent {
  id: number;
  event_type: SyncEventType;
  drive_file_id: string | null;
  document_id: number | null;
  occurred_at: string;
  result: "success" | "skipped" | "failed";
  message: string | null;
}

export interface SyncEventListResponse {
  events: SyncEvent[];
  total: number;
  limit: number;
  offset: number;
}

// ── SPEC-004 Case Matrix 프론트 출력 ─────────────────────────────────────────

const SPEC004_MESSAGES: Record<string, string> = {
  DRIVE_CONNECTOR_NOT_CONFIGURED: "Drive 연동 설정이 필요합니다.",
  DRIVE_FOLDER_NOT_CONFIGURED: "감시 폴더를 설정하세요.",
  DRIVE_WATCH_EXPIRED: "Drive watch 갱신이 필요합니다.",
  DRIVE_WEBHOOK_INVALID: "유효하지 않은 Drive 알림입니다.",
  DRIVE_CHANGES_FAILED: "Drive 변경 조회에 실패했습니다.",
  DRIVE_FILE_OUT_OF_SCOPE: "감시 범위 밖 문서입니다.",
  FORBIDDEN_ADMIN_ONLY: "관리자만 사용할 수 있습니다.",
};

/** ApiError → 사용자에게 보여줄 한국어 카피 (SPEC-004 Case Matrix '프론트 출력'). */
export function spec004Message(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.errorCode && SPEC004_MESSAGES[err.errorCode]) {
    return SPEC004_MESSAGES[err.errorCode];
  }
  return fallback;
}

// ── calls ────────────────────────────────────────────────────────────────────

export function fetchConnectorStatus(): Promise<ConnectorStatus> {
  return api<ConnectorStatus>("/admin/drive-connector");
}

export function registerWatch(): Promise<ConnectorStatus> {
  return api<ConnectorStatus>("/admin/drive-connector/watch", { method: "POST" });
}

export function retrySync(): Promise<SyncRetryResult> {
  return api<SyncRetryResult>("/admin/drive-connector/sync/retry", {
    method: "POST",
  });
}

export function fetchSyncEvents(
  limit = 30,
  offset = 0,
): Promise<SyncEventListResponse> {
  return api<SyncEventListResponse>(
    `/admin/drive-sync-events?limit=${limit}&offset=${offset}`,
  );
}
