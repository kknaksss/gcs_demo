// SPEC-003/006 문서 탐색/관계/검색 typed client — WORK-006.
// backend: app/schemas/explorer.py · app/schemas/documents.py 계약 기준.
// 권한 없는 문서는 BE에서 제거되어 도착한다 — FE는 숨김 필터를 재구현하지 않는다.

import { api, ApiError } from "./client";
import type { PhysicalPath } from "./organization";

// ── types ────────────────────────────────────────────────────────────────────

export type DriveState = "active" | "trashed" | "removed" | "out_of_scope";

export type RelationType =
  | "related"
  | "references"
  | "supersedes"
  | "duplicate_candidate";

export type RelatedSource =
  | "physical"
  | "related_department"
  | "related_product"
  | "document_relation";

export type SearchBadge = "physical" | "related";
export type SearchSourceFilter = "all" | "physical" | "related";

export interface TreeDocumentItem {
  document_id: number;
  drive_name: string;
  drive_state: DriveState;
  drive_modified_time: string | null;
  document_type_name: string | null;
  physical_tree_path: PhysicalPath;
  approved: boolean;
}

export interface TreeNodeContext {
  id: number;
  name: string;
  type: string;
  status: "active" | "inactive";
}

export interface TreeDocumentsResponse {
  organization_node: TreeNodeContext;
  documents: TreeDocumentItem[];
  total: number;
}

export interface RelatedDocumentItem {
  document_id: number;
  drive_name: string;
  physical_tree_path: PhysicalPath;
  source: Exclude<RelatedSource, "physical">;
  relation_type: RelationType | null;
  match_reason: string;
}

export interface RelatedDocumentsResponse {
  documents: RelatedDocumentItem[];
  total: number;
}

export interface DocumentRelationItem {
  id: number;
  source_document_id: number;
  target_document_id: number;
  relation_type: RelationType;
  source_label: string | null;
  approved_by: number;
  approved_at: string;
  target_state: DriveState;
  source_drive_name: string | null;
  target_drive_name: string | null;
}

export interface DocumentRelationsResponse {
  document_id: number;
  relations: DocumentRelationItem[];
  total: number;
}

export interface RelationTypeItem {
  value: RelationType;
  label: string;
}

export interface SearchResultItem {
  document_id: number;
  drive_name: string;
  physical_tree_path: PhysicalPath;
  source_badge: SearchBadge;
  relation_type: RelationType | null;
}

export interface SearchDocumentsResponse {
  query: string;
  results: SearchResultItem[];
  total: number;
}

export interface DriveMirror {
  source_provider: string;
  drive_file_id: string;
  drive_name: string;
  drive_web_url: string | null;
  drive_mime_type: string;
  drive_state: DriveState;
  drive_modified_time: string | null;
  drive_fingerprint: Record<string, unknown>;
}

export interface DocumentDetail {
  id: number;
  mirror: DriveMirror;
  document_type_id: number | null;
  owning_department_node_id: number | null;
  organization_path: number[] | null;
  tree_path: number[] | null;
  access_logic: string;
  sensitivity: string;
  policy_preset: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
  document_type_name: string | null;
  physical_tree_path: PhysicalPath | null;
  related_departments: { node_id: number; name: string }[];
  related_products: string[];
  approved: boolean;
  // admin 전용 `승인 대기` badge — member 응답에는 null (SPEC-003 U-2)
  pending_candidate: { id: number; state: string; created_at: string } | null;
}

// ── UX 카피 (SPEC-003 U-1 / SPEC-006 U-1~U-4) ───────────────────────────────

export const RELATION_TYPE_LABELS: Record<RelationType, string> = {
  related: "관련",
  references: "참조",
  supersedes: "대체",
  duplicate_candidate: "중복 후보",
};

export const RELATED_SOURCE_LABELS: Record<
  Exclude<RelatedSource, "physical">,
  string
> = {
  related_department: "관련 부서",
  related_product: "관련 제품",
  document_relation: "문서 관계",
};

export const SEARCH_BADGE_LABELS: Record<SearchBadge, string> = {
  physical: "물리 귀속",
  related: "관련 문서",
};

export const DRIVE_STATE_LABELS: Record<DriveState, string> = {
  active: "활성",
  trashed: "휴지통",
  removed: "삭제됨",
  out_of_scope: "범위 밖",
};

export const TREE_EMPTY_MESSAGE = "이 위치에 귀속된 문서가 없습니다.";
export const RELATED_EMPTY_MESSAGE = "관련 문서가 없습니다.";
export const SEARCH_EMPTY_MESSAGE = "검색 결과가 없습니다.";
export const RELATIONS_EMPTY_MESSAGE = "연결된 문서가 없습니다.";
export const ORG_EMPTY_MESSAGE = "조직도가 아직 설정되지 않았습니다.";

// ── SPEC-006 Case Matrix 프론트 출력 ─────────────────────────────────────────

const SPEC006_MESSAGES: Record<string, string> = {
  DOCUMENT_NOT_FOUND: "문서를 찾을 수 없습니다.",
  DOCUMENT_NOT_READABLE: "문서를 찾을 수 없습니다.",
  RELATION_NOT_FOUND: "연결 정보를 찾을 수 없습니다.",
  INVALID_RELATION_TYPE: "지원하지 않는 연결 타입입니다.",
  ORG_NODE_NOT_FOUND: "조직을 찾을 수 없습니다.",
  TREE_NODE_NOT_FOUND: "문서 트리 항목을 찾을 수 없습니다.",
};

/** ApiError → 한국어 카피 (SPEC-006 Case Matrix '프론트 출력'). */
export function spec006Message(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.errorCode && SPEC006_MESSAGES[err.errorCode]) {
    return SPEC006_MESSAGES[err.errorCode];
  }
  return fallback;
}

// ── calls ────────────────────────────────────────────────────────────────────

export function fetchTreeDocuments(
  orgNodeId: number,
  treeNodeId?: number | null,
): Promise<TreeDocumentsResponse> {
  const query = new URLSearchParams({ org_node_id: String(orgNodeId) });
  if (treeNodeId != null) query.set("tree_node_id", String(treeNodeId));
  return api<TreeDocumentsResponse>(`/tree-documents?${query.toString()}`);
}

export function fetchDepartmentRelatedDocuments(
  departmentNodeId: number,
): Promise<RelatedDocumentsResponse> {
  return api<RelatedDocumentsResponse>(
    `/departments/${departmentNodeId}/related-documents`,
  );
}

export function fetchDocumentRelated(
  documentId: number,
): Promise<RelatedDocumentsResponse> {
  return api<RelatedDocumentsResponse>(`/documents/${documentId}/related`);
}

export function fetchDocumentRelations(
  documentId: number,
): Promise<DocumentRelationsResponse> {
  return api<DocumentRelationsResponse>(`/documents/${documentId}/relations`);
}

export function fetchRelationTypes(): Promise<RelationTypeItem[]> {
  return api<{ relation_types: RelationTypeItem[] }>("/relation-types").then(
    (res) => res.relation_types,
  );
}

export function searchDocuments(
  q: string,
  options?: {
    source?: SearchSourceFilter;
    orgNodeId?: number | null;
    limit?: number;
  },
): Promise<SearchDocumentsResponse> {
  const query = new URLSearchParams({ q });
  if (options?.source && options.source !== "all") {
    query.set("source", options.source);
  }
  if (options?.orgNodeId != null) {
    query.set("org_node_id", String(options.orgNodeId));
  }
  if (options?.limit) query.set("limit", String(options.limit));
  return api<SearchDocumentsResponse>(`/search/documents?${query.toString()}`);
}

export function fetchDocumentDetail(documentId: number): Promise<DocumentDetail> {
  return api<DocumentDetail>(`/documents/${documentId}`);
}
