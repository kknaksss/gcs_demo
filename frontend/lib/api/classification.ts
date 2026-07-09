// SPEC-007 classification job typed client — WORK-004 Phase 5.
// backend: app/schemas/classification.py 계약 기준. FE는 Redis가 아니라
// DB job 상태 API를 폴링한다 (ARCH-002 §6).

import { api, ApiError } from "./client";

// ── types ────────────────────────────────────────────────────────────────────

export type ClassificationJobType = "classification" | "stale_reanalysis";

export type ClassificationJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "candidate_saved"
  | "validation_failed"
  | "failed"
  | "timeout"
  | "stale";

export interface ClassificationJob {
  id: number;
  job_type: ClassificationJobType;
  status: ClassificationJobStatus;
  document_id: number;
  candidate_id: number | null;
  drive_file_id: string;
  fingerprint: string;
  attempt_count: number;
  max_attempts: number;
  external_task_id: string | null;
  last_error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClassificationJobListResponse {
  jobs: ClassificationJob[];
  total: number;
  limit: number;
  offset: number;
}

// /admin/documents 응답 최소 표면 (app/schemas/documents.py)
export interface AdminDocumentSummary {
  id: number;
  mirror: {
    drive_file_id: string;
    drive_name: string;
    drive_mime_type: string;
    drive_state: "active" | "trashed" | "removed" | "out_of_scope";
    drive_modified_time: string | null;
  };
}

export interface AdminDocumentListResponse {
  documents: AdminDocumentSummary[];
  total: number;
  limit: number;
  offset: number;
}

// ── SPEC-007 U-1 상태 문구 ───────────────────────────────────────────────────

export const CLASSIFICATION_STATUS_LABEL: Record<
  ClassificationJobStatus,
  string
> = {
  queued: "AI 분석 대기 중",
  running: "AI 분석 중",
  succeeded: "AI 분석 중", // 결과 검증 진행 — 사용자에겐 분석 중으로 표시
  candidate_saved: "새 후보 준비됨",
  validation_failed: "AI 분석 실패",
  failed: "AI 분석 실패",
  timeout: "AI 분석 실패",
  stale: "Drive 변경으로 다시 분석 중",
};

// ── SPEC-007 Case Matrix 프론트 출력 ─────────────────────────────────────────

const SPEC007_MESSAGES: Record<string, string> = {
  CLASSIFICATION_TASK_FAILED: "AI 분석에 실패했습니다.",
  CLASSIFICATION_TIMEOUT: "AI 분석 시간이 초과되었습니다.",
  CLASSIFICATION_RESULT_INVALID: "AI 결과 형식이 올바르지 않습니다.",
  CLASSIFICATION_FINGERPRINT_STALE: "Drive 파일이 변경되어 다시 분석합니다.",
  OPEN_KKNAKS_NOT_CONFIGURED: "AI 실행 설정이 필요합니다.",
  OPEN_KKNAKS_PROVIDER_INVALID: "지원하지 않는 AI provider입니다.",
  CLASSIFICATION_JOB_NOT_FOUND: "분석 작업을 찾을 수 없습니다.",
  DOCUMENT_UNAVAILABLE: "분석할 수 없는 문서 상태입니다.",
  CLASSIFICATION_RETRY_EXHAUSTED: "재시도 한도를 초과했습니다.",
  DOCUMENT_NOT_FOUND: "문서를 찾을 수 없습니다.",
  FORBIDDEN_ADMIN_ONLY: "관리자만 사용할 수 있습니다.",
};

/** ApiError → 한국어 카피 (SPEC-007 Case Matrix '프론트 출력'). */
export function spec007Message(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.errorCode && SPEC007_MESSAGES[err.errorCode]) {
    return SPEC007_MESSAGES[err.errorCode];
  }
  return fallback;
}

/** job의 last_error_code 표시용 카피 (없으면 null). */
export function jobErrorMessage(job: ClassificationJob): string | null {
  if (!job.last_error_code) return null;
  return SPEC007_MESSAGES[job.last_error_code] ?? job.last_error_code;
}

// ── calls ────────────────────────────────────────────────────────────────────

export function classifyDocument(documentId: number): Promise<ClassificationJob> {
  return api<ClassificationJob>(`/admin/documents/${documentId}/classify`, {
    method: "POST",
  });
}

export function fetchClassificationJob(
  jobId: number,
): Promise<ClassificationJob> {
  return api<ClassificationJob>(`/admin/classification-jobs/${jobId}`);
}

export function fetchDocumentClassificationJobs(
  documentId: number,
  limit = 20,
  offset = 0,
): Promise<ClassificationJobListResponse> {
  return api<ClassificationJobListResponse>(
    `/admin/documents/${documentId}/classification-jobs?limit=${limit}&offset=${offset}`,
  );
}

export function fetchAdminDocuments(
  limit = 10,
  offset = 0,
): Promise<AdminDocumentListResponse> {
  return api<AdminDocumentListResponse>(
    `/admin/documents?limit=${limit}&offset=${offset}`,
  );
}
