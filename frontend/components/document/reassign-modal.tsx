// 문서 이관 modal — SPEC-002 U-5 (WORK-002 Phase 3).
// 현재 path 표시 + active path 선택 + 변경 사유 필수. `이관 저장`은 유효한
// active path와 변경 사유가 있을 때만 활성화한다. inactive 조직/트리 노드는
// 선택지에서 제외한다 (Case Matrix ORG_NODE_INACTIVE 예방).
"use client";

import { useEffect, useMemo, useState } from "react";
import { LoaderCircle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  fetchPathHistory,
  reassignDocument,
  spec002Message,
  type OrgNode,
  type PathHistoryEntry,
  type PhysicalPath,
  type TreeNode,
} from "@/lib/api/organization";

interface ReassignModalProps {
  documentId: number;
  orgNodes: OrgNode[];
  treeNodes: TreeNode[];
  onClose: () => void;
  /** 이관 저장 성공 후 호출 (목록 갱신용). */
  onReassigned?: (path: PhysicalPath) => void;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR");
}

export function ReassignModal({
  documentId,
  orgNodes,
  treeNodes,
  onClose,
  onReassigned,
}: ReassignModalProps) {
  const [currentPath, setCurrentPath] = useState<PhysicalPath | null>(null);
  const [entries, setEntries] = useState<PathHistoryEntry[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(true);

  const [departmentId, setDepartmentId] = useState<number | "">("");
  const [teamId, setTeamId] = useState<number | "">("");
  const [workId, setWorkId] = useState<number | "">("");
  const [docTypeNodeId, setDocTypeNodeId] = useState<number | "">("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedPath, setSavedPath] = useState<PhysicalPath | null>(null);

  // ── active 노드만 선택지로 (inactive는 새 귀속 대상 선택 불가) ──
  const company = useMemo(
    () => orgNodes.find((n) => n.type === "company") ?? null,
    [orgNodes],
  );
  const departments = useMemo(
    () =>
      orgNodes.filter((n) => n.type === "department" && n.status === "active"),
    [orgNodes],
  );
  const teams = useMemo(
    () =>
      orgNodes.filter(
        (n) =>
          n.type === "team" &&
          n.status === "active" &&
          departmentId !== "" &&
          n.parent_id === departmentId,
      ),
    [orgNodes, departmentId],
  );
  const attachOrgId = teamId !== "" ? teamId : departmentId;
  const workNodes = useMemo(
    () =>
      treeNodes.filter(
        (n) =>
          n.type === "work" &&
          n.status === "active" &&
          attachOrgId !== "" &&
          n.organization_node_id === attachOrgId,
      ),
    [treeNodes, attachOrgId],
  );
  const docTypeNodes = useMemo(
    () =>
      treeNodes.filter(
        (n) =>
          n.type === "document_type" &&
          n.status === "active" &&
          attachOrgId !== "" &&
          n.organization_node_id === attachOrgId &&
          (workId === "" ? n.parent_id === null : n.parent_id === workId),
      ),
    [treeNodes, attachOrgId, workId],
  );

  useEffect(() => {
    // documentId별 초기 상태는 부모의 key 리마운트로 보장된다.
    let cancelled = false;
    fetchPathHistory(documentId)
      .then((res) => {
        if (cancelled) return;
        setCurrentPath(res.current_path);
        setEntries(res.entries);
        setHistoryError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setHistoryError(
          spec002Message(err, "path 이력을 불러오지 못했습니다."),
        );
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  const canSave =
    company !== null && departmentId !== "" && reason.trim().length > 0 && !saving;

  async function handleSave() {
    if (!company || departmentId === "") return;
    setError(null);
    setSaving(true);
    try {
      const organization_path = [company.id, departmentId as number];
      if (teamId !== "") organization_path.push(teamId as number);
      const tree_path: number[] = [];
      if (workId !== "") tree_path.push(workId as number);
      if (docTypeNodeId !== "") tree_path.push(docTypeNodeId as number);

      const res = await reassignDocument(documentId, {
        organization_path,
        tree_path,
        reason: reason.trim(),
      });
      setSavedPath(res.path);
      setCurrentPath(res.path);
      onReassigned?.(res.path);
      const refreshed = await fetchPathHistory(documentId);
      setEntries(refreshed.entries);
    } catch (err) {
      setError(spec002Message(err, "이관 저장에 실패했습니다."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="문서 이관"
    >
      <div className="w-full max-w-lg rounded-xl border border-border bg-card shadow-lg">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">문서 이관</h2>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="닫기">
            <X />
          </Button>
        </div>

        <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto p-4">
          {/* 현재 path */}
          <div className="rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
            <div className="text-xs text-muted-foreground">
              현재 path · 문서 #{documentId}
            </div>
            {loadingHistory ? (
              <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                <LoaderCircle className="size-3.5 animate-spin" /> 불러오는 중
              </div>
            ) : historyError ? (
              <div className="mt-1 text-destructive">{historyError}</div>
            ) : currentPath ? (
              <div className="mt-1 font-medium">{currentPath.display_path}</div>
            ) : (
              <div className="mt-1 text-muted-foreground">
                아직 귀속된 path가 없습니다.
              </div>
            )}
          </div>

          {/* 새 path 선택 — active 노드만 */}
          <div className="grid grid-cols-2 gap-3">
            <label className="col-span-2 flex flex-col gap-1 text-sm">
              회사
              <input
                className="h-9 rounded-lg border border-input bg-muted/40 px-3 text-sm"
                value={company?.name ?? ""}
                disabled
                readOnly
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              부서
              <select
                className="h-9 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                value={departmentId}
                onChange={(e) => {
                  setDepartmentId(e.target.value ? Number(e.target.value) : "");
                  setTeamId("");
                  setWorkId("");
                  setDocTypeNodeId("");
                }}
              >
                <option value="">선택</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              팀 (선택)
              <select
                className="h-9 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                value={teamId}
                onChange={(e) => {
                  setTeamId(e.target.value ? Number(e.target.value) : "");
                  setWorkId("");
                  setDocTypeNodeId("");
                }}
                disabled={departmentId === ""}
              >
                <option value="">선택 안 함</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              업무 (선택)
              <select
                className="h-9 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                value={workId}
                onChange={(e) => {
                  setWorkId(e.target.value ? Number(e.target.value) : "");
                  setDocTypeNodeId("");
                }}
                disabled={attachOrgId === ""}
              >
                <option value="">선택 안 함</option>
                {workNodes.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              문서종류 (선택)
              <select
                className="h-9 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                value={docTypeNodeId}
                onChange={(e) =>
                  setDocTypeNodeId(e.target.value ? Number(e.target.value) : "")
                }
                disabled={attachOrgId === ""}
              >
                <option value="">선택 안 함</option>
                {docTypeNodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="flex flex-col gap-1 text-sm">
            변경 사유
            <textarea
              className="min-h-20 rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="변경 사유를 입력하세요."
            />
          </label>

          {error && (
            <div
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {error}
            </div>
          )}
          {savedPath && !error && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
              이관 완료: {savedPath.display_path}
            </div>
          )}

          {/* path history (append-only) */}
          {entries.length > 0 && (
            <div className="rounded-lg border border-border">
              <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
                변경 이력
              </div>
              <ul className="divide-y divide-border text-xs">
                {entries.map((e) => (
                  <li key={e.id} className="px-3 py-2">
                    <div className="text-muted-foreground">
                      {formatDate(e.changed_at)} · 변경자 #{e.changed_by}
                    </div>
                    <div className="mt-0.5">{e.reason}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="outline" onClick={onClose} disabled={saving}>
            닫기
          </Button>
          <Button onClick={() => void handleSave()} disabled={!canSave}>
            {saving && <LoaderCircle className="animate-spin" />}
            이관 저장
          </Button>
        </div>
      </div>
    </div>
  );
}
