// 문서 탐색 — 조직 트리 / 물리 귀속 목록 / 관련 문서 / 검색 / 상세 / 문서 연결.
// WORK-006 (SPEC-002 U-1~U-3, SPEC-003 U-1/U-2, SPEC-006 U-1~U-4).
// 시안: 21-html/page-documents.html — 섹션 구분(①~⑥)과 카피를 그대로 따른다.
// 권한 없는 문서는 BE에서 제거되어 도착한다 — 잠금 카드/마스킹 행을 만들지 않는다.
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ExternalLink, LoaderCircle, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ReassignModal } from "@/components/document/reassign-modal";
import { ApiError } from "@/lib/api/client";
import {
  DRIVE_STATE_LABELS,
  ORG_EMPTY_MESSAGE,
  RELATED_EMPTY_MESSAGE,
  RELATED_SOURCE_LABELS,
  RELATION_TYPE_LABELS,
  RELATIONS_EMPTY_MESSAGE,
  SEARCH_BADGE_LABELS,
  SEARCH_EMPTY_MESSAGE,
  spec006Message,
  TREE_EMPTY_MESSAGE,
  fetchDepartmentRelatedDocuments,
  fetchDocumentDetail,
  fetchDocumentRelated,
  fetchTreeDocuments,
  searchDocuments,
  type DocumentDetail,
  type DriveState,
  type RelatedDocumentItem,
  type RelationType,
  type SearchResultItem,
  type SearchSourceFilter,
  type TreeDocumentsResponse,
} from "@/lib/api/documents";
import {
  fetchDocumentTreeConfig,
  fetchMe,
  fetchOrganizationTree,
  spec002Message,
  type Me,
  type OrgNode,
  type TreeNode,
} from "@/lib/api/organization";

// ── 사이드바 트리 (조직 노드 + 부착된 업무/문서종류 노드) ────────────────────

interface SidebarRow {
  key: string;
  label: string;
  depth: number;
  orgNodeId: number;
  treeNodeId: number | null;
  inactive: boolean;
  kind: "org" | "tree";
}

function buildSidebarRows(orgNodes: OrgNode[], treeNodes: TreeNode[]): SidebarRow[] {
  const orgByParent = new Map<number | null, OrgNode[]>();
  for (const n of orgNodes) {
    const list = orgByParent.get(n.parent_id) ?? [];
    list.push(n);
    orgByParent.set(n.parent_id, list);
  }
  const treeByOrg = new Map<number, TreeNode[]>();
  for (const n of treeNodes) {
    const list = treeByOrg.get(n.organization_node_id) ?? [];
    list.push(n);
    treeByOrg.set(n.organization_node_id, list);
  }

  const rows: SidebarRow[] = [];
  const pushTreeRows = (orgId: number, orgInactive: boolean, depth: number) => {
    const attached = treeByOrg.get(orgId) ?? [];
    const children = (parentId: number | null) =>
      attached.filter((t) => t.parent_id === parentId);
    const walkTree = (parentId: number | null, d: number) => {
      for (const t of children(parentId)) {
        rows.push({
          key: `tree-${t.id}`,
          label: t.name,
          depth: d,
          orgNodeId: orgId,
          treeNodeId: t.id,
          inactive: orgInactive || t.status === "inactive",
          kind: "tree",
        });
        walkTree(t.id, d + 1);
      }
    };
    walkTree(null, depth);
  };
  const walkOrg = (parentId: number | null, depth: number) => {
    for (const n of orgByParent.get(parentId) ?? []) {
      rows.push({
        key: `org-${n.id}`,
        label: n.name,
        depth,
        orgNodeId: n.id,
        treeNodeId: null,
        inactive: n.status === "inactive",
        kind: "org",
      });
      pushTreeRows(n.id, n.status === "inactive", depth + 1);
      walkOrg(n.id, depth + 1);
    }
  };
  walkOrg(null, 0);
  return rows;
}

// ── 공용 badge ───────────────────────────────────────────────────────────────

function Badge({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "dark" | "info" | "success" | "warn";
}) {
  const tones: Record<string, string> = {
    muted: "bg-muted text-muted-foreground",
    dark: "bg-foreground text-background",
    info: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
    success:
      "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
    warn: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

function StateBadge({ state }: { state: DriveState }) {
  return (
    <Badge tone={state === "active" ? "success" : "warn"}>
      {DRIVE_STATE_LABELS[state]}
    </Badge>
  );
}

function Panel({
  title,
  badge,
  children,
}: {
  title: React.ReactNode;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {badge}
      </div>
      {children}
    </section>
  );
}

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ko-KR") : "—";
}

// 관계 표시 label — relation 기반이 아니면 `관련`으로 표기 (시안 ② 기준)
function relationLabel(relationType: RelationType | null): string {
  return relationType ? RELATION_TYPE_LABELS[relationType] : "관련";
}

const RELATION_FILTERS = ["전체", "관련", "참조", "대체", "중복 후보"] as const;
const SOURCE_FILTERS: { value: SearchSourceFilter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "physical", label: "물리 귀속" },
  { value: "related", label: "관련 문서" },
];

export default function DocumentsPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loadingMe, setLoadingMe] = useState(true);

  const [orgNodes, setOrgNodes] = useState<OrgNode[]>([]);
  const [treeNodes, setTreeNodes] = useState<TreeNode[]>([]);
  const [loadingTree, setLoadingTree] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);

  const [selection, setSelection] = useState<{
    orgNodeId: number;
    treeNodeId: number | null;
  } | null>(null);

  const [treeDocs, setTreeDocs] = useState<TreeDocumentsResponse | null>(null);
  const [loadingDocs, setLoadingDocs] = useState(false);

  const [related, setRelated] = useState<RelatedDocumentItem[]>([]);
  const [relationFilter, setRelationFilter] =
    useState<(typeof RELATION_FILTERS)[number]>("전체");

  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState<SearchSourceFilter>("all");
  const [searchResults, setSearchResults] = useState<SearchResultItem[] | null>(
    null,
  );
  const [searching, setSearching] = useState(false);

  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [docRelated, setDocRelated] = useState<RelatedDocumentItem[]>([]);

  const [reassignDocId, setReassignDocId] = useState<number | null>(null);

  const isAdmin = me?.is_admin ?? false;

  // ── 초기 로드: 인증 확인 + 조직/문서 트리 ─────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then(async (user) => {
        if (cancelled) return;
        setMe(user);
        setLoadingMe(false);
        try {
          const [orgs, tree] = await Promise.all([
            fetchOrganizationTree(),
            fetchDocumentTreeConfig(),
          ]);
          if (cancelled) return;
          setOrgNodes(orgs);
          setTreeNodes(tree);
          const firstDept = orgs.find((n) => n.type === "department");
          const fallback = firstDept ?? orgs[0] ?? null;
          if (fallback) {
            setSelection({ orgNodeId: fallback.id, treeNodeId: null });
          }
        } catch (err) {
          if (!cancelled) {
            setPageError(spec002Message(err, "조직 트리를 불러오지 못했습니다."));
          }
        } finally {
          if (!cancelled) setLoadingTree(false);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) {
          setPageError("비활성 계정입니다.");
          setLoadingMe(false);
          setLoadingTree(false);
          return;
        }
        router.push("/login");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  const orgById = useMemo(() => new Map(orgNodes.map((n) => [n.id, n])), [orgNodes]);
  const sidebarRows = useMemo(
    () => buildSidebarRows(orgNodes, treeNodes),
    [orgNodes, treeNodes],
  );

  const selectedOrg = selection ? (orgById.get(selection.orgNodeId) ?? null) : null;

  // 선택 노드의 부서 컨텍스트 (team이면 상위 부서) — 관련 문서 영역 기준
  const departmentContext = useMemo(() => {
    let node = selectedOrg;
    while (node) {
      if (node.type === "department") return node;
      node = node.parent_id != null ? (orgById.get(node.parent_id) ?? null) : null;
    }
    return null;
  }, [selectedOrg, orgById]);

  const breadcrumb = useMemo(() => {
    if (!selection || !selectedOrg) return "문서";
    const names: string[] = [];
    let node: OrgNode | null = selectedOrg;
    while (node) {
      names.unshift(node.name);
      node = node.parent_id != null ? (orgById.get(node.parent_id) ?? null) : null;
    }
    if (selection.treeNodeId != null) {
      const treeById = new Map(treeNodes.map((n) => [n.id, n]));
      const chain: string[] = [];
      let t = treeById.get(selection.treeNodeId) ?? null;
      while (t) {
        chain.unshift(t.name);
        t = t.parent_id != null ? (treeById.get(t.parent_id) ?? null) : null;
      }
      names.push(...chain);
    }
    return ["문서", ...names].join(" / ");
  }, [selection, selectedOrg, orgById, treeNodes]);

  // ── 물리 귀속 목록 + 관련 문서 로드 ───────────────────────────────────────
  const reloadDocs = useCallback(async () => {
    if (!selection) return;
    setLoadingDocs(true);
    setPageError(null);
    try {
      const docs = await fetchTreeDocuments(
        selection.orgNodeId,
        selection.treeNodeId,
      );
      setTreeDocs(docs);
    } catch (err) {
      setTreeDocs(null);
      setPageError(spec006Message(err, "문서 목록을 불러오지 못했습니다."));
    } finally {
      setLoadingDocs(false);
    }
  }, [selection]);

  useEffect(() => {
    void reloadDocs();
  }, [reloadDocs]);

  useEffect(() => {
    if (!departmentContext) {
      setRelated([]);
      return;
    }
    let cancelled = false;
    fetchDepartmentRelatedDocuments(departmentContext.id)
      .then((res) => {
        if (!cancelled) setRelated(res.documents);
      })
      .catch(() => {
        if (!cancelled) setRelated([]);
      });
    return () => {
      cancelled = true;
    };
  }, [departmentContext]);

  // ── 검색 ──────────────────────────────────────────────────────────────────
  const runSearch = useCallback(
    async (source: SearchSourceFilter) => {
      const q = query.trim();
      if (!q) {
        setSearchResults(null);
        return;
      }
      setSearching(true);
      try {
        const res = await searchDocuments(q, {
          source,
          orgNodeId: selection?.orgNodeId ?? null,
        });
        setSearchResults(res.results);
      } catch (err) {
        setPageError(spec006Message(err, "검색에 실패했습니다."));
      } finally {
        setSearching(false);
      }
    },
    [query, selection],
  );

  // ── 문서 상세 + 문서 연결 ─────────────────────────────────────────────────
  useEffect(() => {
    if (selectedDocId === null) {
      setDetail(null);
      setDocRelated([]);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailError(null);
    fetchDocumentDetail(selectedDocId)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetail(null);
          setDetailError(spec006Message(err, "문서를 찾을 수 없습니다."));
        }
      });
    fetchDocumentRelated(selectedDocId)
      .then((res) => {
        if (!cancelled) setDocRelated(res.documents);
      })
      .catch(() => {
        if (!cancelled) setDocRelated([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedDocId]);

  const filteredRelated = useMemo(() => {
    if (relationFilter === "전체") return related;
    return related.filter((r) => relationLabel(r.relation_type) === relationFilter);
  }, [related, relationFilter]);

  const inactiveOrgSelected = treeDocs?.organization_node.status === "inactive";

  const NAV = [
    { label: "문서", href: "/documents", active: true, adminOnly: false },
    { label: "승인", href: "/admin/approvals", active: false, adminOnly: true },
    { label: "관리", href: "/admin/catalog", active: false, adminOnly: true },
    { label: "Drive 연동", href: "/admin/connector", active: false, adminOnly: true },
    { label: "로그인/RBAC", href: "/login", active: false, adminOnly: false },
  ].filter((item) => isAdmin || !item.adminOnly);

  if (loadingMe) {
    return (
      <div className="grid min-h-full place-items-center text-sm text-muted-foreground">
        <div className="flex items-center gap-2">
          <LoaderCircle className="size-4 animate-spin" /> 확인 중…
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col">
      {/* topbar */}
      <header className="flex items-center gap-6 border-b border-border px-6 py-3">
        <a href="/documents" className="flex items-center gap-2 font-semibold">
          <span className="grid size-7 place-items-center rounded-md bg-foreground text-xs font-bold text-background">
            CF
          </span>
          <span>Cloud File Organizer</span>
        </a>
        <nav className="flex items-center gap-1 text-sm">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className={`rounded-md px-2.5 py-1 ${
                item.active
                  ? "bg-muted font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="flex-1" />
        {me && (
          <span className="text-xs text-muted-foreground">
            {me.name} · {isAdmin ? "admin" : "member"}
          </span>
        )}
      </header>

      <div className="flex flex-1">
        {/* ── 조직 / 문서 트리 sidebar ── */}
        <aside className="w-64 shrink-0 border-r border-border p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              조직 / 문서 트리
            </span>
            <Badge tone="info">DB path</Badge>
          </div>
          {loadingTree ? (
            <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
              <LoaderCircle className="size-3.5 animate-spin" /> 불러오는 중
            </div>
          ) : sidebarRows.length === 0 ? (
            <div className="flex flex-col gap-2 py-2 text-sm text-muted-foreground">
              <p>{ORG_EMPTY_MESSAGE}</p>
              {isAdmin && (
                <a href="/admin/catalog">
                  <Button size="sm" variant="outline">
                    조직 설정
                  </Button>
                </a>
              )}
            </div>
          ) : (
            <ul className="flex flex-col gap-0.5 text-sm">
              {sidebarRows.map((row) => {
                const selected =
                  selection?.orgNodeId === row.orgNodeId &&
                  selection?.treeNodeId === row.treeNodeId;
                return (
                  <li key={row.key}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelection({
                          orgNodeId: row.orgNodeId,
                          treeNodeId: row.treeNodeId,
                        });
                        setSelectedDocId(null);
                      }}
                      className={`w-full rounded-md px-2 py-1 text-left ${
                        selected
                          ? "bg-muted font-medium text-foreground"
                          : row.inactive
                            ? "text-muted-foreground/60 hover:bg-muted/50"
                            : "text-foreground hover:bg-muted/50"
                      }`}
                      style={{ paddingLeft: `${8 + row.depth * 14}px` }}
                    >
                      {row.kind === "tree" ? "• " : ""}
                      {row.label}
                      {row.inactive && (
                        <span className="ml-1 text-xs text-muted-foreground/70">
                          비활성
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-4 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
            <strong>권한 없는 문서는 보이지 않음</strong>
            <p className="mt-0.5">
              잠금 카드나 마스킹 행 없이 목록, 검색, 관계 결과에서 제거된다.
            </p>
          </div>
        </aside>

        {/* ── main ── */}
        <main className="flex w-full min-w-0 flex-1 flex-col gap-5 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs text-muted-foreground">{breadcrumb}</div>
              <h1 className="text-xl font-semibold">문서 탐색</h1>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone="dark">물리 귀속 {treeDocs?.total ?? 0}건</Badge>
              <Badge>관련 문서 {related.length}건</Badge>
            </div>
          </div>

          {/* 검색 toolbar */}
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <input
                className="h-9 w-full min-w-0 rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                placeholder="문서 검색 (drive_name / 요약)"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void runSearch(sourceFilter);
                }}
                aria-label="문서 검색"
              />
              <Button
                variant="outline"
                onClick={() => void runSearch(sourceFilter)}
                disabled={searching || !query.trim()}
              >
                {searching ? <LoaderCircle className="animate-spin" /> : <Search />}
                검색
              </Button>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-muted-foreground">출처 필터</span>
              {SOURCE_FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => {
                    setSourceFilter(f.value);
                    if (searchResults !== null) void runSearch(f.value);
                  }}
                  className="rounded-full"
                >
                  <Badge tone={sourceFilter === f.value ? "dark" : "muted"}>
                    {f.label}
                  </Badge>
                </button>
              ))}
            </div>
          </div>

          {pageError && (
            <div
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {pageError}
            </div>
          )}

          {/* ── ① 물리 귀속 목록 ── */}
          <Panel
            title={
              <span>
                ① 물리 귀속 목록
                {treeDocs ? ` — ${treeDocs.organization_node.name}` : ""}
              </span>
            }
            badge={
              <div className="flex items-center gap-2">
                {inactiveOrgSelected && <Badge tone="warn">비활성 조직</Badge>}
                <Badge tone="dark">물리 귀속</Badge>
                {isAdmin && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={selectedDocId === null}
                    onClick={() =>
                      selectedDocId !== null && setReassignDocId(selectedDocId)
                    }
                  >
                    문서 이관
                  </Button>
                )}
              </div>
            }
          >
            {loadingDocs ? (
              <div className="flex items-center gap-2 px-4 py-5 text-sm text-muted-foreground">
                <LoaderCircle className="size-3.5 animate-spin" /> 불러오는 중
              </div>
            ) : !treeDocs || treeDocs.documents.length === 0 ? (
              <p className="px-4 py-5 text-sm text-muted-foreground">
                {TREE_EMPTY_MESSAGE}
              </p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">문서명</th>
                    <th className="px-3 py-2 font-medium">실제 귀속</th>
                    <th className="px-3 py-2 font-medium">문서종류</th>
                    <th className="px-3 py-2 font-medium">Drive 수정</th>
                    <th className="px-3 py-2 font-medium">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {treeDocs.documents.map((doc) => (
                    <tr
                      key={doc.document_id}
                      onClick={() => setSelectedDocId(doc.document_id)}
                      className={`cursor-pointer border-t border-border ${
                        selectedDocId === doc.document_id
                          ? "bg-muted/60"
                          : "hover:bg-muted/30"
                      }`}
                    >
                      <td className="px-3 py-2 font-medium">{doc.drive_name}</td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {doc.physical_tree_path.display_path}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {doc.document_type_name ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {formatDate(doc.drive_modified_time)}
                      </td>
                      <td className="px-3 py-2">
                        {doc.approved ? (
                          <Badge tone="success">approved</Badge>
                        ) : (
                          <Badge>미승인</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
              논리 연결 문서는 이 목록에 섞이지 않는다.
            </p>
          </Panel>

          {/* ── ② 관련 문서 ── */}
          <Panel
            title="② 관련 문서"
            badge={
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">관계 필터</span>
                {RELATION_FILTERS.map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setRelationFilter(f)}
                    className="rounded-full"
                  >
                    <Badge tone={relationFilter === f ? "dark" : "muted"}>{f}</Badge>
                  </button>
                ))}
              </div>
            }
          >
            {filteredRelated.length === 0 ? (
              <p className="px-4 py-5 text-sm text-muted-foreground">
                {RELATED_EMPTY_MESSAGE}
              </p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">문서명</th>
                    <th className="px-3 py-2 font-medium">실제 귀속 path</th>
                    <th className="px-3 py-2 font-medium">출처</th>
                    <th className="px-3 py-2 font-medium">관계</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRelated.map((doc) => (
                    <tr
                      key={`${doc.document_id}-${doc.source}`}
                      onClick={() => setSelectedDocId(doc.document_id)}
                      className="cursor-pointer border-t border-border hover:bg-muted/30"
                    >
                      <td className="px-3 py-2 font-medium">{doc.drive_name}</td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {doc.physical_tree_path.display_path}
                      </td>
                      <td className="px-3 py-2">
                        <Badge>{RELATED_SOURCE_LABELS[doc.source]}</Badge>
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone="info">{relationLabel(doc.relation_type)}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
              문서의 실제 관리 주체는 기본 목록/path에서 확인한다. 권한 없는 관련
              문서는 숨긴다.
            </p>
          </Panel>

          {/* ── ③ 검색 결과 ── */}
          {searchResults !== null && (
            <Panel title="③ 검색 결과" badge={<Badge tone="info">출처 badge 필수</Badge>}>
              {searchResults.length === 0 ? (
                <p className="px-4 py-5 text-sm text-muted-foreground">
                  {SEARCH_EMPTY_MESSAGE}
                </p>
              ) : (
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted/50 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">문서명</th>
                      <th className="px-3 py-2 font-medium">실제 physical_tree_path</th>
                      <th className="px-3 py-2 font-medium">출처</th>
                    </tr>
                  </thead>
                  <tbody>
                    {searchResults.map((hit) => (
                      <tr
                        key={hit.document_id}
                        onClick={() => setSelectedDocId(hit.document_id)}
                        className="cursor-pointer border-t border-border hover:bg-muted/30"
                      >
                        <td className="px-3 py-2 font-medium">{hit.drive_name}</td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {hit.physical_tree_path.display_path}
                        </td>
                        <td className="px-3 py-2">
                          <Badge tone={hit.source_badge === "physical" ? "dark" : "muted"}>
                            {SEARCH_BADGE_LABELS[hit.source_badge]}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
                권한 없는 문서는 결과에서 제거된다.
              </p>
            </Panel>
          )}

          {/* ── ④ 선택 문서 상세 ── */}
          {selectedDocId !== null && (
            <Panel
              title="④ 선택 문서 상세"
              badge={
                detail && (
                  <div className="flex items-center gap-2">
                    {isAdmin && detail.pending_candidate && (
                      <Badge tone="warn">승인 대기</Badge>
                    )}
                    <StateBadge state={detail.mirror.drive_state} />
                    {detail.mirror.drive_web_url ? (
                      <a
                        href={detail.mirror.drive_web_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <Button size="sm">
                          <ExternalLink /> Drive에서 열기
                        </Button>
                      </a>
                    ) : (
                      <Button size="sm" disabled>
                        <ExternalLink /> Drive에서 열기
                      </Button>
                    )}
                    {isAdmin && (
                      <a href="/admin/approvals">
                        <Button size="sm" variant="outline">
                          승인 게이트로 이동
                        </Button>
                      </a>
                    )}
                    {isAdmin && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setReassignDocId(detail.id)}
                      >
                        문서 이관
                      </Button>
                    )}
                  </div>
                )
              }
            >
              {detailError ? (
                <p className="px-4 py-5 text-sm text-muted-foreground">
                  {detailError}
                </p>
              ) : !detail ? (
                <div className="flex items-center gap-2 px-4 py-5 text-sm text-muted-foreground">
                  <LoaderCircle className="size-3.5 animate-spin" /> 불러오는 중
                </div>
              ) : (
                <div className="flex flex-col gap-4 p-4">
                  <h3 className="text-base font-semibold">
                    {detail.mirror.drive_name}
                  </h3>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <div className="flex flex-col gap-3">
                      <div className="rounded-lg border border-border">
                        <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                          문서 정보 <Badge tone="success">DB SoT</Badge>
                        </div>
                        <dl className="flex flex-col gap-1.5 px-3 py-2.5 text-sm">
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">문서종류</dt>
                            <dd className="font-medium">
                              {detail.document_type_name ?? "미승인"}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">관련 부서</dt>
                            <dd className="font-medium">
                              {detail.related_departments.length > 0
                                ? detail.related_departments
                                    .map((d) => d.name)
                                    .join(", ")
                                : "—"}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">관련 제품</dt>
                            <dd className="font-medium">
                              {detail.related_products.length > 0
                                ? detail.related_products.join(", ")
                                : "—"}
                            </dd>
                          </div>
                        </dl>
                      </div>
                      <div className="rounded-lg border border-border">
                        <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                          귀속
                        </div>
                        <dl className="flex flex-col gap-1.5 px-3 py-2.5 text-sm">
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">실제 귀속</dt>
                            <dd className="text-right font-medium">
                              {detail.physical_tree_path?.display_path ??
                                "아직 귀속되지 않음"}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">관리 주체</dt>
                            <dd className="font-medium">
                              {detail.physical_tree_path?.owning_department ?? "—"}
                            </dd>
                          </div>
                        </dl>
                      </div>
                      <div className="rounded-lg border border-border">
                        <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                          권한
                        </div>
                        <dl className="flex flex-col gap-1.5 px-3 py-2.5 text-sm">
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">access_logic</dt>
                            <dd className="font-medium">{detail.access_logic}</dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">민감도</dt>
                            <dd className="font-medium">{detail.sensitivity}</dd>
                          </div>
                          {detail.policy_preset && (
                            <div className="flex justify-between gap-2">
                              <dt className="text-muted-foreground">preset</dt>
                              <dd className="font-mono text-xs font-medium">
                                {detail.policy_preset}
                              </dd>
                            </div>
                          )}
                        </dl>
                      </div>
                    </div>
                    <div className="flex flex-col gap-3">
                      <div className="rounded-lg border border-border">
                        <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                          요약
                        </div>
                        <p className="px-3 py-2.5 text-sm">
                          {detail.summary ?? (
                            <span className="text-muted-foreground">
                              승인된 요약이 없습니다.
                            </span>
                          )}
                        </p>
                      </div>
                      <div className="rounded-lg border border-border">
                        <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                          Drive 정보 <Badge tone="dark">Drive SoT</Badge>
                        </div>
                        <dl className="flex flex-col gap-1.5 px-3 py-2.5 text-sm">
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">drive_file_id</dt>
                            <dd className="truncate font-mono text-xs font-medium">
                              {detail.mirror.drive_file_id}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">mime_type</dt>
                            <dd className="truncate font-mono text-xs font-medium">
                              {detail.mirror.drive_mime_type}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">modified_time</dt>
                            <dd className="font-medium">
                              {formatDate(detail.mirror.drive_modified_time)}
                            </dd>
                          </div>
                          <div className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">상태</dt>
                            <dd>
                              <StateBadge state={detail.mirror.drive_state} />
                            </dd>
                          </div>
                        </dl>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </Panel>
          )}

          {/* ── ⑤ 문서 연결 ── */}
          {selectedDocId !== null && !detailError && (
            <Panel
              title="⑤ 문서 연결"
              badge={<Badge tone="info">승인 relation만 표시</Badge>}
            >
              {docRelated.length === 0 ? (
                <p className="px-4 py-5 text-sm text-muted-foreground">
                  {RELATIONS_EMPTY_MESSAGE}
                </p>
              ) : (
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted/50 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">관계</th>
                      <th className="px-3 py-2 font-medium">대상</th>
                      <th className="px-3 py-2 font-medium">출처</th>
                    </tr>
                  </thead>
                  <tbody>
                    {docRelated.map((doc) => (
                      <tr
                        key={`${doc.document_id}-${doc.source}`}
                        onClick={() => setSelectedDocId(doc.document_id)}
                        className="cursor-pointer border-t border-border hover:bg-muted/30"
                      >
                        <td className="px-3 py-2">
                          <Badge tone="info">
                            {relationLabel(doc.relation_type)}
                          </Badge>
                        </td>
                        <td className="px-3 py-2 font-medium">{doc.drive_name}</td>
                        <td className="px-3 py-2">
                          <Badge>{RELATED_SOURCE_LABELS[doc.source]}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
                unresolved 후보는 승인 전 graph에서 제외된다.
              </p>
            </Panel>
          )}
        </main>
      </div>

      {/* ── ⑥ 문서 이관 modal (admin) ── */}
      {isAdmin && reassignDocId !== null && (
        <ReassignModal
          key={reassignDocId}
          documentId={reassignDocId}
          orgNodes={orgNodes}
          treeNodes={treeNodes}
          onClose={() => setReassignDocId(null)}
          onReassigned={() => {
            void reloadDocs();
            if (selectedDocId !== null) {
              fetchDocumentDetail(selectedDocId)
                .then(setDetail)
                .catch(() => undefined);
            }
          }}
        />
      )}
    </div>
  );
}
