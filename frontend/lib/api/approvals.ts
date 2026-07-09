// SPEC-005 승인 게이트 typed client — WORK-005 Phase 3.
// backend: app/schemas/approval.py 계약 기준. 후보 원장 state는 5개 enum,
// reanalysis_status는 표시용 파생값(원장 미저장 — DEC-022).

import { api, ApiError } from "./client";

// ── types ────────────────────────────────────────────────────────────────────

export type CandidateState =
  | "pending"
  | "stale"
  | "approved"
  | "rejected"
  | "blocked";

export type ReanalysisStatus =
  | "reanalyzing"
  | "new_candidate_ready"
  | "reanalysis_failed";

export type ReadCapability = "content_read" | "metadata_only";

export type RelationCandidateState =
  | "pending"
  | "unresolved"
  | "approved"
  | "removed";

export interface ApprovalCandidateSummary {
  id: number;
  document_id: number;
  drive_name: string;
  drive_state: string;
  state: CandidateState;
  reanalysis_status: ReanalysisStatus | null;
  read_capability: ReadCapability;
  stale_reason: string | null;
  blocked_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalCandidateListResponse {
  candidates: ApprovalCandidateSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface RelationCandidateItem {
  id: number;
  source_document_id: number;
  raw_label: string;
  suggested_relation_type:
    | "related"
    | "references"
    | "supersedes"
    | "duplicate_candidate";
  target_document_id: number | null;
  target_drive_name: string | null;
  state: RelationCandidateState;
  created_at: string;
  updated_at: string;
}

// AI candidate_metadata.resolution (backend services/classification._resolve_output)
export interface CandidateResolution {
  document_type_id: number | null;
  document_type_is_new: boolean;
  owning_department_node_id: number | null;
  created_department_node_id: number | null;
  organization_path_node_ids: (number | null)[];
  tree_path_node_ids: (number | null)[];
  read_department_node_ids: (number | null)[];
  related_department_node_ids: (number | null)[];
  unresolved_fields: string[];
  needs_admin_fix: boolean;
}

export interface CandidateMetadata {
  document_type?: string;
  created_department?: string | null;
  owning_department?: string;
  physical_tree_path?: { organization_path: string[]; tree_path: string[] };
  related_departments?: string[];
  related_products?: string[];
  summary?: string | null;
  sensitivity?: "normal" | "sensitive";
  policy_preset?: string | null;
  read_policy?: {
    read_roles: string[];
    read_departments: string[];
    read_positions: string[];
    access_logic: "ANY" | "ALL" | "PRESET";
  };
  reasons?: string[];
  resolution?: CandidateResolution;
  [key: string]: unknown;
}

export interface ApprovalCandidateDetail extends ApprovalCandidateSummary {
  drive_web_url: string | null;
  candidate_metadata: CandidateMetadata;
  candidate_fingerprint: Record<string, unknown>;
  current_fingerprint: Record<string, unknown>;
  fingerprint_match: boolean;
  approved_by: number | null;
  approved_at: string | null;
  relation_candidates: RelationCandidateItem[];
}

export interface ApprovalPayload {
  document_type_id: number;
  created_department_node_id: number | null;
  owning_department_node_id: number;
  physical_tree_path: { organization_path: number[]; tree_path: number[] };
  related_department_node_ids: number[];
  related_products: string[];
  summary: string | null;
  read_roles: string[];
  read_departments: number[];
  read_positions: string[];
  access_logic: "ANY" | "ALL" | "PRESET";
  sensitivity: "normal" | "sensitive";
  policy_preset: string | null;
}

export interface ApprovedMetadata {
  document_id: number;
  document_type_id: number | null;
  owning_department_node_id: number | null;
  organization_path: number[] | null;
  tree_path: number[] | null;
  access_logic: string;
  sensitivity: string;
  policy_preset: string | null;
  summary: string | null;
}

export interface ApprovalResultResponse {
  candidate: ApprovalCandidateSummary;
  document: ApprovedMetadata;
  idempotent: boolean;
}

export interface AdminDocumentTypeItem {
  id: number;
  name: string;
  normalized_name: string;
}

export interface RelationRematchResponse {
  candidate: RelationCandidateItem;
  suggested_target_document_id: number | null;
  suggested_target_drive_name: string | null;
}

// 민감 preset 후보 목록 (backend core/policy_presets — policy.md SoT, DEC-017)
export const POLICY_PRESETS = [
  "HR_RESTRICTED",
  "CONTRACT_RESTRICTED",
  "FINANCE_RESTRICTED",
  "SECURITY_RESTRICTED",
  "LEGAL_RESTRICTED",
] as const;

// ── SPEC-005 UX 문구 ─────────────────────────────────────────────────────────

export const CANDIDATE_STATE_LABEL: Record<CandidateState, string> = {
  pending: "pending",
  stale: "stale",
  approved: "approved",
  rejected: "rejected",
  blocked: "blocked",
};

export const REANALYSIS_STATUS_LABEL: Record<ReanalysisStatus, string> = {
  reanalyzing: "재분석 중",
  new_candidate_ready: "새 후보 준비됨",
  reanalysis_failed: "재분석 실패",
};

export const STALE_MESSAGE =
  "Drive 파일이 변경되어 이 후보는 승인할 수 없습니다.";
export const METADATA_ONLY_MESSAGE =
  "본문 분석 없이 Drive 정보만으로 생성된 후보입니다.";
export const BLOCKED_MESSAGE = "현재 문서 상태에서는 승인할 수 없습니다.";
export const EMPTY_QUEUE_MESSAGE = "승인할 후보가 없습니다.";

// ── SPEC-005 Case Matrix 프론트 출력 ─────────────────────────────────────────

const SPEC005_MESSAGES: Record<string, string> = {
  FORBIDDEN_ADMIN_ONLY: "관리자만 사용할 수 있습니다.",
  CANDIDATE_NOT_FOUND: "후보를 찾을 수 없습니다.",
  CANDIDATE_NOT_PENDING: "승인할 수 없는 후보 상태입니다.",
  CANDIDATE_STALE: "Drive 파일이 변경되어 다시 분석해야 합니다.",
  DOCUMENT_UNAVAILABLE: "현재 문서 상태에서는 승인할 수 없습니다.",
  DOCUMENT_TYPE_DUPLICATE: "이미 존재하는 문서종류입니다.",
  DOCUMENT_TYPE_NOT_FOUND: "문서종류를 카탈로그에서 찾을 수 없습니다.",
  INVALID_TREE_PATH: "문서 위치를 확인하세요.",
  INVALID_ACCESS_POLICY: "접근 권한 설정을 확인하세요.",
  RELATION_TARGET_REQUIRED: "대상 문서를 선택하거나 보류하세요.",
  REANALYSIS_FAILED: "재분석 요청에 실패했습니다.",
  ORG_NODE_NOT_FOUND: "조직을 찾을 수 없습니다.",
  ORG_NODE_INACTIVE: "비활성 조직은 새 귀속 대상으로 선택할 수 없습니다.",
  DOCUMENT_NOT_FOUND: "문서를 찾을 수 없습니다.",
};

/** ApiError → 한국어 카피 (SPEC-005 Case Matrix '프론트 출력'). */
export function spec005Message(err: unknown, fallback: string): string {
  if (
    err instanceof ApiError &&
    err.errorCode &&
    SPEC005_MESSAGES[err.errorCode]
  ) {
    return SPEC005_MESSAGES[err.errorCode];
  }
  return fallback;
}

// ── calls ────────────────────────────────────────────────────────────────────

export function fetchApprovalCandidates(params?: {
  state?: CandidateState;
  read_capability?: ReadCapability;
  limit?: number;
  offset?: number;
}): Promise<ApprovalCandidateListResponse> {
  const query = new URLSearchParams();
  if (params?.state) query.set("state", params.state);
  if (params?.read_capability)
    query.set("read_capability", params.read_capability);
  query.set("limit", String(params?.limit ?? 100));
  query.set("offset", String(params?.offset ?? 0));
  return api<ApprovalCandidateListResponse>(
    `/admin/approval-candidates?${query.toString()}`,
  );
}

export function fetchApprovalCandidate(
  candidateId: number,
): Promise<ApprovalCandidateDetail> {
  return api<ApprovalCandidateDetail>(
    `/admin/approval-candidates/${candidateId}`,
  );
}

export function approveCandidate(
  candidateId: number,
  payload: ApprovalPayload,
): Promise<ApprovalResultResponse> {
  return api<ApprovalResultResponse>(
    `/admin/approval-candidates/${candidateId}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function rejectCandidate(
  candidateId: number,
  reason?: string,
): Promise<ApprovalCandidateSummary> {
  return api<ApprovalCandidateSummary>(
    `/admin/approval-candidates/${candidateId}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason ?? null }),
    },
  );
}

export function reanalyzeCandidate(candidateId: number): Promise<unknown> {
  return api<unknown>(`/admin/approval-candidates/${candidateId}/reanalyze`, {
    method: "POST",
  });
}

export function fetchAdminDocumentTypes(): Promise<AdminDocumentTypeItem[]> {
  return api<{ document_types: AdminDocumentTypeItem[] }>(
    "/admin/document-types",
  ).then((res) => res.document_types);
}

export function createAdminDocumentType(
  name: string,
): Promise<AdminDocumentTypeItem> {
  return api<AdminDocumentTypeItem>("/admin/document-types", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function resolveRelationCandidate(
  candidateId: number,
  targetDocumentId: number,
): Promise<RelationCandidateItem> {
  return api<RelationCandidateItem>(
    `/admin/relation-candidates/${candidateId}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_document_id: targetDocumentId }),
    },
  );
}

export function holdRelationCandidate(
  candidateId: number,
): Promise<RelationCandidateItem> {
  return api<RelationCandidateItem>(
    `/admin/relation-candidates/${candidateId}/hold`,
    { method: "POST" },
  );
}

export function removeRelationCandidate(
  candidateId: number,
): Promise<RelationCandidateItem> {
  return api<RelationCandidateItem>(
    `/admin/relation-candidates/${candidateId}/remove`,
    { method: "POST" },
  );
}

export function rematchRelationCandidate(
  candidateId: number,
): Promise<RelationRematchResponse> {
  return api<RelationRematchResponse>(
    `/admin/relation-candidates/${candidateId}/rematch`,
    { method: "POST" },
  );
}
