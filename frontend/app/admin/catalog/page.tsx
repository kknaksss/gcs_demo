// 관리 설정 — 조직도 / 문서 트리 설정 / 문서종류 (SPEC-002 U-4, WORK-002 Phase 3).
// 시안: 21-html/page-admin-settings.html ④⑤⑥ 섹션. Drive 연동/RBAC 보정 섹션은
// 각각 WORK-003 / WORK-001 소관. 문서종류 "추가"는 승인 게이트 소관(WORK-005) —
// 여기서는 전사 카탈로그 조회와 트리 연결만 제공한다.
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, Plus, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import {
  createDocumentTreeNode,
  fetchDocumentTreeConfig,
  fetchDocumentTypes,
  fetchMe,
  fetchOrganizationTree,
  spec002Message,
  updateDocumentTreeNode,
  updateOrganizationNode,
  type DocumentTypeItem,
  type Me,
  type OrgNode,
  type TreeNode,
} from "@/lib/api/organization";

const NAV = [
  { label: "문서", href: "/documents" },
  { label: "승인", href: "/admin/approvals" },
  { label: "관리", href: "/admin/catalog", active: true },
  { label: "Drive 연동", href: "/admin/connector" },
  { label: "로그인/RBAC", href: "/login" },
];

type Guard = "loading" | "admin" | "forbidden";

// ── 조직도 계층 표시용 (parent_id → depth DFS) ───────────────────────────────

interface OrgRow {
  node: OrgNode;
  depth: number;
}

function flattenOrgTree(nodes: OrgNode[]): OrgRow[] {
  const byParent = new Map<number | null, OrgNode[]>();
  for (const n of nodes) {
    const list = byParent.get(n.parent_id) ?? [];
    list.push(n);
    byParent.set(n.parent_id, list);
  }
  const rows: OrgRow[] = [];
  const walk = (parentId: number | null, depth: number) => {
    for (const n of byParent.get(parentId) ?? []) {
      rows.push({ node: n, depth });
      walk(n.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

function StatusBadge({ status }: { status: "active" | "inactive" }) {
  return status === "active" ? (
    <span className="inline-flex items-center rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
      active
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
      비활성
    </span>
  );
}

function Panel({
  title,
  badge,
  children,
}: {
  title: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {badge}
      </div>
      {children}
    </section>
  );
}

export default function CatalogPage() {
  const router = useRouter();
  const [guard, setGuard] = useState<Guard>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const [orgNodes, setOrgNodes] = useState<OrgNode[]>([]);
  const [treeNodes, setTreeNodes] = useState<TreeNode[]>([]);
  const [docTypes, setDocTypes] = useState<DocumentTypeItem[]>([]);
  const [loadingData, setLoadingData] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);

  // 조직도 inline rename
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // 업무 추가 form
  const [workOrgId, setWorkOrgId] = useState<number | "">("");
  const [workName, setWorkName] = useState("");
  // 문서종류 연결 form
  const [linkWorkId, setLinkWorkId] = useState<number | "">("");
  const [linkTypeId, setLinkTypeId] = useState<number | "">("");
  const [formBusy, setFormBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoadingData(true);
    setPageError(null);
    try {
      const [orgs, tree, types] = await Promise.all([
        fetchOrganizationTree(),
        fetchDocumentTreeConfig(),
        fetchDocumentTypes(),
      ]);
      setOrgNodes(orgs);
      setTreeNodes(tree);
      setDocTypes(types);
    } catch (err) {
      setPageError(spec002Message(err, "설정 데이터를 불러오지 못했습니다."));
    } finally {
      setLoadingData(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((user) => {
        if (cancelled) return;
        setMe(user);
        if (user.is_admin) {
          setGuard("admin");
          void reload();
        } else {
          // member에게는 관리 화면을 렌더하지 않는다 (FORBIDDEN_ADMIN_ONLY).
          setGuard("forbidden");
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) {
          setGuard("forbidden");
          return;
        }
        router.push("/login");
      });
    return () => {
      cancelled = true;
    };
  }, [router, reload]);

  const orgRows = useMemo(() => flattenOrgTree(orgNodes), [orgNodes]);
  const orgById = useMemo(
    () => new Map(orgNodes.map((n) => [n.id, n])),
    [orgNodes],
  );
  const treeById = useMemo(
    () => new Map(treeNodes.map((n) => [n.id, n])),
    [treeNodes],
  );
  const activeOrgAttachTargets = useMemo(
    () =>
      orgNodes.filter(
        (n) => n.status === "active" && (n.type === "department" || n.type === "team"),
      ),
    [orgNodes],
  );
  const activeWorkNodes = useMemo(
    () => treeNodes.filter((n) => n.type === "work" && n.status === "active"),
    [treeNodes],
  );

  async function handleRenameOrg(node: OrgNode) {
    if (!renameValue.trim() || renameValue.trim() === node.name) {
      setRenamingId(null);
      return;
    }
    try {
      await updateOrganizationNode(node.id, { name: renameValue.trim() });
      setRenamingId(null);
      await reload();
    } catch (err) {
      setPageError(spec002Message(err, "이름 변경에 실패했습니다."));
    }
  }

  async function handleToggleOrgStatus(node: OrgNode) {
    try {
      await updateOrganizationNode(node.id, {
        status: node.status === "active" ? "inactive" : "active",
      });
      await reload();
    } catch (err) {
      setPageError(spec002Message(err, "상태 변경에 실패했습니다."));
    }
  }

  async function handleAddWork() {
    if (workOrgId === "" || !workName.trim()) return;
    setFormBusy(true);
    setPageError(null);
    try {
      await createDocumentTreeNode({
        organization_node_id: workOrgId as number,
        type: "work",
        name: workName.trim(),
      });
      setWorkName("");
      await reload();
    } catch (err) {
      setPageError(spec002Message(err, "업무 추가에 실패했습니다."));
    } finally {
      setFormBusy(false);
    }
  }

  async function handleLinkDocumentType() {
    if (linkWorkId === "" || linkTypeId === "") return;
    const work = treeById.get(linkWorkId as number);
    const catalog = docTypes.find((t) => t.id === linkTypeId);
    if (!work || !catalog) return;
    setFormBusy(true);
    setPageError(null);
    try {
      await createDocumentTreeNode({
        organization_node_id: work.organization_node_id,
        parent_id: work.id,
        type: "document_type",
        document_type_id: catalog.id,
        name: catalog.name,
      });
      setLinkTypeId("");
      await reload();
    } catch (err) {
      setPageError(spec002Message(err, "문서종류 연결에 실패했습니다."));
    } finally {
      setFormBusy(false);
    }
  }

  async function handleToggleTreeStatus(node: TreeNode) {
    try {
      await updateDocumentTreeNode(node.id, {
        status: node.status === "active" ? "inactive" : "active",
      });
      await reload();
    } catch (err) {
      setPageError(spec002Message(err, "상태 변경에 실패했습니다."));
    }
  }

  function treeParentLabel(node: TreeNode): string {
    if (node.parent_id !== null) {
      return treeById.get(node.parent_id)?.name ?? `#${node.parent_id}`;
    }
    return orgById.get(node.organization_node_id)?.name ?? `#${node.organization_node_id}`;
  }

  // ── guard 화면 ────────────────────────────────────────────────────────────

  if (guard === "loading") {
    return (
      <div className="grid min-h-full place-items-center text-sm text-muted-foreground">
        <div className="flex items-center gap-2">
          <LoaderCircle className="size-4 animate-spin" /> 확인 중…
        </div>
      </div>
    );
  }

  if (guard === "forbidden") {
    return (
      <div className="grid min-h-full place-items-center p-6">
        <div className="flex max-w-sm items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3">
          <ShieldAlert className="mt-0.5 size-5 text-destructive" />
          <div>
            <div className="text-sm font-semibold text-destructive">
              관리자만 사용할 수 있습니다.
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              관리 설정은 admin 계정에서만 접근할 수 있습니다.
            </p>
          </div>
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
            {me.name} · admin
          </span>
        )}
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs text-muted-foreground">
              관리 / 조직도 · 문서 트리 설정 · 문서종류
            </div>
            <h1 className="text-xl font-semibold">관리 설정</h1>
          </div>
          <Button variant="outline" onClick={() => void reload()} disabled={loadingData}>
            {loadingData && <LoaderCircle className="animate-spin" />}
            상태 새로고침
          </Button>
        </div>

        {pageError && (
          <div
            role="alert"
            className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {pageError}
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          {/* ── 조직도 ── */}
          <Panel
            title="조직도"
            badge={
              <span className="inline-flex items-center rounded-full bg-foreground px-2.5 py-0.5 text-xs font-medium text-background">
                회사/부서/팀
              </span>
            }
          >
            {orgRows.length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                조직도가 아직 설정되지 않았습니다.
              </p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">노드</th>
                    <th className="px-3 py-2 font-medium">type</th>
                    <th className="px-3 py-2 font-medium">상태</th>
                    <th className="px-3 py-2 font-medium">action</th>
                  </tr>
                </thead>
                <tbody>
                  {orgRows.map(({ node, depth }) => (
                    <tr key={node.id} className="border-t border-border">
                      <td className="px-3 py-2">
                        <span style={{ paddingLeft: `${depth * 16}px` }}>
                          {renamingId === node.id ? (
                            <input
                              className="h-7 rounded-md border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring"
                              value={renameValue}
                              autoFocus
                              onChange={(e) => setRenameValue(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") void handleRenameOrg(node);
                                if (e.key === "Escape") setRenamingId(null);
                              }}
                            />
                          ) : (
                            node.name
                          )}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">{node.type}</td>
                      <td className="px-3 py-2">
                        <StatusBadge status={node.status} />
                      </td>
                      <td className="px-3 py-2">
                        {node.status === "inactive" ? (
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">
                              새 귀속 대상 선택 불가
                            </span>
                            <Button
                              size="xs"
                              variant="outline"
                              onClick={() => void handleToggleOrgStatus(node)}
                            >
                              활성화
                            </Button>
                          </div>
                        ) : renamingId === node.id ? (
                          <div className="flex gap-1">
                            <Button size="xs" onClick={() => void handleRenameOrg(node)}>
                              저장
                            </Button>
                            <Button
                              size="xs"
                              variant="ghost"
                              onClick={() => setRenamingId(null)}
                            >
                              취소
                            </Button>
                          </div>
                        ) : (
                          <div className="flex gap-1">
                            <Button
                              size="xs"
                              variant="outline"
                              onClick={() => {
                                setRenamingId(node.id);
                                setRenameValue(node.name);
                              }}
                            >
                              이름 변경
                            </Button>
                            {node.type !== "company" && (
                              <Button
                                size="xs"
                                variant="outline"
                                onClick={() => void handleToggleOrgStatus(node)}
                              >
                                비활성화
                              </Button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          {/* ── 문서 트리 설정 ── */}
          <Panel
            title="문서 트리 설정"
            badge={
              <span className="inline-flex items-center rounded-full bg-foreground px-2.5 py-0.5 text-xs font-medium text-background">
                업무/문서종류
              </span>
            }
          >
            {treeNodes.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted-foreground">
                아직 설정된 업무/문서종류 노드가 없습니다.
              </p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">노드</th>
                    <th className="px-3 py-2 font-medium">type</th>
                    <th className="px-3 py-2 font-medium">부모</th>
                    <th className="px-3 py-2 font-medium">상태</th>
                    <th className="px-3 py-2 font-medium">action</th>
                  </tr>
                </thead>
                <tbody>
                  {treeNodes.map((node) => (
                    <tr key={node.id} className="border-t border-border">
                      <td className="px-3 py-2">{node.name}</td>
                      <td className="px-3 py-2 text-muted-foreground">{node.type}</td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {treeParentLabel(node)}
                      </td>
                      <td className="px-3 py-2">
                        <StatusBadge status={node.status} />
                      </td>
                      <td className="px-3 py-2">
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => void handleToggleTreeStatus(node)}
                        >
                          {node.status === "active" ? "비활성화" : "활성화"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <div className="flex flex-col gap-3 border-t border-border p-4">
              {/* 업무 추가 */}
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  조직 노드
                  <select
                    className="h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring"
                    value={workOrgId}
                    onChange={(e) =>
                      setWorkOrgId(e.target.value ? Number(e.target.value) : "")
                    }
                  >
                    <option value="">선택</option>
                    {activeOrgAttachTargets.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.name} ({n.type})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  업무 이름
                  <input
                    className="h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring"
                    value={workName}
                    onChange={(e) => setWorkName(e.target.value)}
                    placeholder="예: 제품 운영"
                  />
                </label>
                <Button
                  size="sm"
                  onClick={() => void handleAddWork()}
                  disabled={formBusy || workOrgId === "" || !workName.trim()}
                >
                  <Plus /> 업무 추가
                </Button>
              </div>

              {/* 문서종류 연결 */}
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  업무 노드
                  <select
                    className="h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring"
                    value={linkWorkId}
                    onChange={(e) =>
                      setLinkWorkId(e.target.value ? Number(e.target.value) : "")
                    }
                  >
                    <option value="">선택</option>
                    {activeWorkNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  문서종류 (전사 카탈로그)
                  <select
                    className="h-8 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring"
                    value={linkTypeId}
                    onChange={(e) =>
                      setLinkTypeId(e.target.value ? Number(e.target.value) : "")
                    }
                  >
                    <option value="">선택</option>
                    {docTypes.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  size="sm"
                  onClick={() => void handleLinkDocumentType()}
                  disabled={formBusy || linkWorkId === "" || linkTypeId === ""}
                >
                  <Plus /> 문서종류 추가
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                조직도 변경은 소속/관리 주체 기준을 바꾸고, 업무/문서종류 변경은 문서
                탐색 분류만 바꾼다.
              </p>
            </div>
          </Panel>

          {/* ── 문서종류 카탈로그 ── */}
          <Panel
            title="문서종류"
            badge={
              <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-200">
                전사 공통 catalog
              </span>
            }
          >
            {docTypes.length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                등록된 문서종류가 없습니다. 문서종류 추가는 승인 게이트에서 admin이
                수행한다.
              </p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">표시 이름</th>
                    <th className="px-3 py-2 font-medium">stable key</th>
                  </tr>
                </thead>
                <tbody>
                  {docTypes.map((t) => (
                    <tr key={t.id} className="border-t border-border">
                      <td className="px-3 py-2">{t.name}</td>
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                        {t.normalized_name}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          {/* ── 문서 이관 ── */}
          <Panel
            title="문서 이관"
            badge={
              <span className="inline-flex items-center rounded-full bg-sky-100 px-2.5 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-200">
                admin 전용
              </span>
            }
          >
            <div className="flex flex-col gap-3 p-4">
              <p className="text-xs text-muted-foreground">
                physical path 변경은 명시적 이관으로만 발생하고, 이전/새 path·변경자·
                사유·시각이 append-only 이력으로 남는다. Drive folder 이동은 이관으로
                처리되지 않는다.
              </p>
              <p className="text-xs text-muted-foreground">
                문서 이관은 문서 탐색 화면의 목록/상세 <strong>문서 이관</strong>{" "}
                버튼에서 수행한다 (WORK-006에서 진입점 이동).
              </p>
              <div>
                <a href="/documents">
                  <Button size="sm" variant="outline">
                    문서 탐색으로 이동
                  </Button>
                </a>
              </div>
            </div>
          </Panel>
        </div>
      </main>
    </div>
  );
}
