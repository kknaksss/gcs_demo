// SPEC-002 Organization & Tree typed client — WORK-002 Phase 3.
// backend: app/schemas/organization.py · app/schemas/document_tree.py 계약 기준.
// path array는 노드 name이 아니라 노드 id (SPEC-002 Implementation Rules).

import { api, ApiError } from "./client";

// ── types ────────────────────────────────────────────────────────────────────

export type NodeStatus = "active" | "inactive";

export interface OrgNode {
  id: number;
  parent_id: number | null;
  type: "company" | "department" | "team";
  name: string;
  status: NodeStatus;
}

export interface TreeNode {
  id: number;
  organization_node_id: number;
  parent_id: number | null;
  type: "work" | "document_type";
  document_type_id: number | null;
  name: string;
  status: NodeStatus;
}

export interface DocumentTypeItem {
  id: number;
  name: string;
  normalized_name: string;
}

export interface PhysicalPath {
  organization_path: number[];
  tree_path: number[];
  display_path: string;
  owning_department: string | null;
}

export interface PathHistoryEntry {
  id: number;
  previous_path: { organization_path: number[]; tree_path: number[] };
  new_path: { organization_path: number[]; tree_path: number[] };
  changed_by: number;
  reason: string;
  changed_at: string;
}

export interface PathHistoryResponse {
  document_id: number;
  current_path: PhysicalPath | null;
  entries: PathHistoryEntry[];
}

export interface ReassignResponse {
  document_id: number;
  path: PhysicalPath;
}

// GET /auth/me (backend UserProfile) — admin guard 판정용 부분 타입.
export interface Me {
  id: number;
  email: string;
  name: string;
  role: string;
  is_admin: boolean;
}

// ── SPEC-002 Case Matrix 프론트 출력 ─────────────────────────────────────────

const SPEC002_MESSAGES: Record<string, string> = {
  ORG_NODE_NOT_FOUND: "조직을 찾을 수 없습니다.",
  ORG_NODE_INACTIVE: "비활성 조직은 새 귀속 대상으로 선택할 수 없습니다.",
  TREE_NODE_NOT_FOUND: "문서 트리 항목을 찾을 수 없습니다.",
  INVALID_TREE_DEPTH: "허용되지 않는 계층입니다.",
  REASSIGN_REASON_REQUIRED: "변경 사유를 입력하세요.",
  FORBIDDEN_ADMIN_ONLY: "관리자만 사용할 수 있습니다.",
  DOCUMENT_NOT_READABLE: "문서를 찾을 수 없습니다.",
};

/** ApiError → 사용자에게 보여줄 한국어 카피 (Case Matrix '프론트 출력'). */
export function spec002Message(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.errorCode && SPEC002_MESSAGES[err.errorCode]) {
    return SPEC002_MESSAGES[err.errorCode];
  }
  return fallback;
}

// ── calls ────────────────────────────────────────────────────────────────────

export function fetchMe(): Promise<Me> {
  return api<Me>("/auth/me");
}

export async function fetchOrganizationTree(): Promise<OrgNode[]> {
  const res = await api<{ nodes: OrgNode[] }>("/organization-tree");
  return res.nodes;
}

export async function fetchDocumentTreeConfig(): Promise<TreeNode[]> {
  const res = await api<{ nodes: TreeNode[] }>("/document-tree-config");
  return res.nodes;
}

export async function fetchDocumentTypes(): Promise<DocumentTypeItem[]> {
  const res = await api<{ document_types: DocumentTypeItem[] }>("/document-types");
  return res.document_types;
}

export function createOrganizationNode(input: {
  parent_id: number | null;
  type: OrgNode["type"];
  name: string;
}): Promise<OrgNode> {
  return api<OrgNode>("/organization-nodes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function updateOrganizationNode(
  id: number,
  input: { name?: string; status?: NodeStatus },
): Promise<OrgNode> {
  return api<OrgNode>(`/organization-nodes/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function createDocumentTreeNode(input: {
  organization_node_id: number;
  parent_id?: number | null;
  type: TreeNode["type"];
  document_type_id?: number | null;
  name: string;
}): Promise<TreeNode> {
  return api<TreeNode>("/document-tree-nodes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function updateDocumentTreeNode(
  id: number,
  input: { name?: string; status?: NodeStatus },
): Promise<TreeNode> {
  return api<TreeNode>(`/document-tree-nodes/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function reassignDocument(
  documentId: number,
  input: { organization_path: number[]; tree_path: number[]; reason: string },
): Promise<ReassignResponse> {
  return api<ReassignResponse>(`/documents/${documentId}/reassign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function fetchPathHistory(documentId: number): Promise<PathHistoryResponse> {
  return api<PathHistoryResponse>(`/documents/${documentId}/path-history`);
}
