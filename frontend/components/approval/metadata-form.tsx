// 승인 metadata form — WORK-005 Phase 3 (SPEC-005 U-3/U-4/U-5).
// AI 후보값(candidate_metadata.resolution의 노드 id)이 초기값. 위치 선택은
// SPEC-002 active path만, 문서종류 추가는 admin 전용 modal(중복 시
// "이미 존재하는 문서종류입니다."). 민감 preset 섹션은 preset 승인/권한 수정/
// 민감 아님 3택 (DEC-018 — 승인 시 BE가 preset을 read policy로 풀어 저장).
"use client";

import {
  forwardRef,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { LoaderCircle, Plus, TriangleAlert, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  createAdminDocumentType,
  spec005Message,
  POLICY_PRESETS,
  type AdminDocumentTypeItem,
  type ApprovalCandidateDetail,
  type ApprovalPayload,
} from "@/lib/api/approvals";
import type { OrgNode, TreeNode } from "@/lib/api/organization";

// resolution.unresolved_fields → 관리자 보정 안내 라벨 (WORK-004 resolve 실패 값)
const UNRESOLVED_FIELD_LABEL: Record<string, string> = {
  owning_department: "귀속부서",
  created_department: "생성부서",
  "physical_tree_path.organization_path": "문서 위치(조직)",
  "physical_tree_path.tree_path": "문서 위치(업무/문서종류)",
  "read_policy.read_departments": "읽기 권한 부서",
  related_departments: "관련 부서",
};

export interface MetadataFormHandle {
  /** 검증 통과 시 승인 payload, 실패 시 한국어 오류 메시지를 던진다. */
  buildPayload: () => ApprovalPayload;
}

interface MetadataFormProps {
  detail: ApprovalCandidateDetail;
  orgNodes: OrgNode[];
  treeNodes: TreeNode[];
  docTypes: AdminDocumentTypeItem[];
  onDocTypeCreated: (created: AdminDocumentTypeItem) => void;
  disabled: boolean;
}

const inputClass =
  "h-9 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring disabled:opacity-60";

function initNumber(value: number | null | undefined): number | "" {
  return typeof value === "number" ? value : "";
}

export const MetadataForm = forwardRef<MetadataFormHandle, MetadataFormProps>(
  function MetadataForm(
    { detail, orgNodes, treeNodes, docTypes, onDocTypeCreated, disabled },
    ref,
  ) {
    const meta = detail.candidate_metadata;
    const resolution = meta.resolution;

    const orgById = useMemo(
      () => new Map(orgNodes.map((n) => [n.id, n])),
      [orgNodes],
    );
    const treeById = useMemo(
      () => new Map(treeNodes.map((n) => [n.id, n])),
      [treeNodes],
    );
    const departments = useMemo(
      () =>
        orgNodes.filter(
          (n) => n.type === "department" && n.status === "active",
        ),
      [orgNodes],
    );
    const company = useMemo(
      () => orgNodes.find((n) => n.type === "company") ?? null,
      [orgNodes],
    );

    // ── AI 초기값 (노드 id resolve 결과) ─────────────────────────────────────
    const initial = useMemo(() => {
      const orgIds = (resolution?.organization_path_node_ids ?? []).filter(
        (v): v is number => typeof v === "number",
      );
      const treeIds = (resolution?.tree_path_node_ids ?? []).filter(
        (v): v is number => typeof v === "number",
      );
      let departmentId: number | "" = "";
      let teamId: number | "" = "";
      for (const id of orgIds) {
        const node = orgById.get(id);
        if (node?.type === "department") departmentId = id;
        if (node?.type === "team") teamId = id;
      }
      let workId: number | "" = "";
      let docTypeNodeId: number | "" = "";
      for (const id of treeIds) {
        const node = treeById.get(id);
        if (node?.type === "work") workId = id;
        if (node?.type === "document_type") docTypeNodeId = id;
      }
      return {
        docTypeId: initNumber(resolution?.document_type_id),
        createdDeptId: initNumber(resolution?.created_department_node_id),
        departmentId,
        teamId,
        workId,
        docTypeNodeId,
        readDeptIds: (resolution?.read_department_node_ids ?? []).filter(
          (v): v is number => typeof v === "number",
        ),
        relatedDeptIds: (resolution?.related_department_node_ids ?? []).filter(
          (v): v is number => typeof v === "number",
        ),
      };
    }, [resolution, orgById, treeById]);

    // ── form state ───────────────────────────────────────────────────────────
    const [docTypeId, setDocTypeId] = useState<number | "">(initial.docTypeId);
    const [createdDeptId, setCreatedDeptId] = useState<number | "">(
      initial.createdDeptId,
    );
    const [departmentId, setDepartmentId] = useState<number | "">(
      initial.departmentId,
    );
    const [teamId, setTeamId] = useState<number | "">(initial.teamId);
    const [workId, setWorkId] = useState<number | "">(initial.workId);
    const [docTypeNodeId, setDocTypeNodeId] = useState<number | "">(
      initial.docTypeNodeId,
    );
    const [relatedDeptIds, setRelatedDeptIds] = useState<number[]>(
      initial.relatedDeptIds,
    );
    const [relatedProducts, setRelatedProducts] = useState(
      (meta.related_products ?? []).join(", "),
    );
    const [summary, setSummary] = useState(meta.summary ?? "");
    const [sensitivity, setSensitivity] = useState<"normal" | "sensitive">(
      meta.sensitivity ?? "normal",
    );
    const [accessMode, setAccessMode] = useState<"preset" | "direct">(
      meta.policy_preset ? "preset" : "direct",
    );
    const [presetName, setPresetName] = useState<string>(
      meta.policy_preset ?? POLICY_PRESETS[0],
    );
    const [accessLogic, setAccessLogic] = useState<"ANY" | "ALL">(
      meta.read_policy?.access_logic === "ALL" ? "ALL" : "ANY",
    );
    const [readRoles, setReadRoles] = useState<string[]>(
      meta.read_policy?.read_roles ?? [],
    );
    const [readDeptIds, setReadDeptIds] = useState<number[]>(
      initial.readDeptIds,
    );
    const [readPositions, setReadPositions] = useState(
      (meta.read_policy?.read_positions ?? []).join(", "),
    );

    const permissionRef = useRef<HTMLDivElement | null>(null);

    // ── 위치 선택지 (active만 — SPEC-002) ────────────────────────────────────
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

    const owningDepartment =
      departmentId !== "" ? (orgById.get(departmentId)?.name ?? "") : "";

    // ── 문서종류 추가 modal (U-4) ────────────────────────────────────────────
    const [modalOpen, setModalOpen] = useState(false);
    const [newTypeName, setNewTypeName] = useState("");
    const [modalError, setModalError] = useState<string | null>(null);
    const [modalBusy, setModalBusy] = useState(false);

    async function handleCreateDocType() {
      if (!newTypeName.trim()) return;
      setModalBusy(true);
      setModalError(null);
      try {
        const created = await createAdminDocumentType(newTypeName.trim());
        onDocTypeCreated(created);
        // 추가된 문서종류가 현재 후보 form에 선택된다 (S-3)
        setDocTypeId(created.id);
        setModalOpen(false);
        setNewTypeName("");
      } catch (err) {
        setModalError(spec005Message(err, "문서종류 추가에 실패했습니다."));
      } finally {
        setModalBusy(false);
      }
    }

    // ── payload 조립 (헤더 승인 CTA에서 호출) ────────────────────────────────
    useImperativeHandle(ref, () => ({
      buildPayload: () => {
        if (docTypeId === "") throw new Error("문서종류를 선택하세요.");
        if (!company || departmentId === "")
          throw new Error("문서 위치를 확인하세요.");
        const organization_path = [company.id, departmentId as number];
        if (teamId !== "") organization_path.push(teamId as number);
        const tree_path: number[] = [];
        if (workId !== "") tree_path.push(workId as number);
        if (docTypeNodeId !== "") tree_path.push(docTypeNodeId as number);
        const preset = accessMode === "preset";
        return {
          document_type_id: docTypeId as number,
          created_department_node_id:
            createdDeptId === "" ? null : (createdDeptId as number),
          owning_department_node_id: departmentId as number,
          physical_tree_path: { organization_path, tree_path },
          related_department_node_ids: relatedDeptIds,
          related_products: relatedProducts
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          summary: summary.trim() ? summary.trim() : null,
          read_roles: preset ? [] : readRoles,
          read_departments: preset ? [] : readDeptIds,
          read_positions: preset
            ? []
            : readPositions
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
          access_logic: preset ? "PRESET" : accessLogic,
          sensitivity,
          policy_preset: preset ? presetName : null,
        } satisfies ApprovalPayload;
      },
    }));

    function toggleId(list: number[], id: number): number[] {
      return list.includes(id) ? list.filter((v) => v !== id) : [...list, id];
    }

    function toggleRole(role: string) {
      setReadRoles((prev) =>
        prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role],
      );
    }

    const aiPreset = meta.policy_preset ?? null;
    const aiReasons = meta.reasons ?? [];

    return (
      <div className="flex flex-col gap-6">
        {/* resolve 실패 값 보정 안내 (WORK-004 unresolved_fields) */}
        {resolution?.needs_admin_fix && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <div>
              <div className="font-medium">
                AI가 조직/카탈로그에 매칭하지 못한 값이 있습니다. 확인 후
                직접 선택하세요.
              </div>
              <div className="mt-0.5 text-xs">
                보정 필요:{" "}
                {(resolution.unresolved_fields ?? [])
                  .map((f) => UNRESOLVED_FIELD_LABEL[f] ?? f)
                  .join(", ")}
              </div>
            </div>
          </div>
        )}

        {/* ── 후보 metadata form (U-3) ── */}
        <section className="rounded-xl border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">후보 metadata form</h2>
            <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-200">
              candidate
            </span>
          </div>
          <fieldset disabled={disabled} className="grid gap-4 p-4 sm:grid-cols-2">
            <div className="sm:col-span-2 text-xs font-semibold text-muted-foreground">
              문서 정보
            </div>
            <label className="flex flex-col gap-1 text-sm">
              문서종류
              <select
                className={inputClass}
                value={docTypeId}
                onChange={(e) =>
                  setDocTypeId(e.target.value ? Number(e.target.value) : "")
                }
              >
                <option value="">선택</option>
                {docTypes.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              {resolution?.document_type_is_new && meta.document_type && (
                <span className="text-xs text-amber-700 dark:text-amber-300">
                  AI 제안 “{meta.document_type}”은(는) 카탈로그에 없습니다 —
                  추가 후 선택하세요.
                </span>
              )}
            </label>
            <div className="flex flex-col gap-1 text-sm">
              문서종류 추가
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => {
                  setModalOpen(true);
                  setNewTypeName(
                    resolution?.document_type_is_new
                      ? (meta.document_type ?? "")
                      : "",
                  );
                  setModalError(null);
                }}
              >
                <Plus /> 문서종류 추가
              </Button>
            </div>

            <div className="sm:col-span-2 text-xs font-semibold text-muted-foreground">
              귀속
            </div>
            <label className="flex flex-col gap-1 text-sm">
              생성부서
              <select
                className={inputClass}
                value={createdDeptId}
                onChange={(e) =>
                  setCreatedDeptId(e.target.value ? Number(e.target.value) : "")
                }
              >
                <option value="">선택 안 함</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              귀속부서 (위치 선택에 따름)
              <input className={inputClass} value={owningDepartment} disabled readOnly />
            </label>
            <div className="sm:col-span-2 grid gap-3 sm:grid-cols-3">
              <label className="flex flex-col gap-1 text-sm">
                문서 위치 — 부서 (active만)
                <select
                  className={inputClass}
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
                  className={inputClass}
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
                업무 / 문서종류 노드 (선택)
                <span className="flex gap-2">
                  <select
                    className={`${inputClass} min-w-0 flex-1`}
                    value={workId}
                    onChange={(e) => {
                      setWorkId(e.target.value ? Number(e.target.value) : "");
                      setDocTypeNodeId("");
                    }}
                    disabled={attachOrgId === ""}
                  >
                    <option value="">업무 없음</option>
                    {workNodes.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                  <select
                    className={`${inputClass} min-w-0 flex-1`}
                    value={docTypeNodeId}
                    onChange={(e) =>
                      setDocTypeNodeId(
                        e.target.value ? Number(e.target.value) : "",
                      )
                    }
                    disabled={attachOrgId === ""}
                  >
                    <option value="">종류 없음</option>
                    {docTypeNodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.name}
                      </option>
                    ))}
                  </select>
                </span>
              </label>
            </div>
            <label className="flex flex-col gap-1 text-sm sm:col-span-2">
              관련 부서 (탐색용 — 읽기 권한 아님)
              <span className="flex flex-wrap gap-2">
                {departments.map((d) => (
                  <label
                    key={d.id}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs"
                  >
                    <input
                      type="checkbox"
                      checked={relatedDeptIds.includes(d.id)}
                      onChange={() =>
                        setRelatedDeptIds((prev) => toggleId(prev, d.id))
                      }
                    />
                    {d.name}
                  </label>
                ))}
              </span>
            </label>
            <label className="flex flex-col gap-1 text-sm sm:col-span-2">
              관련 제품/팀 (쉼표 구분)
              <input
                className={inputClass}
                value={relatedProducts}
                onChange={(e) => setRelatedProducts(e.target.value)}
                placeholder="예: cloud-file-organizer"
              />
            </label>

            <div
              ref={permissionRef}
              className="sm:col-span-2 text-xs font-semibold text-muted-foreground"
            >
              권한
            </div>
            <label className="flex flex-col gap-1 text-sm">
              읽기 권한
              <select
                className={inputClass}
                value={accessMode === "preset" ? `PRESET:${presetName}` : "direct"}
                onChange={(e) => {
                  if (e.target.value === "direct") {
                    setAccessMode("direct");
                  } else {
                    setAccessMode("preset");
                    setPresetName(e.target.value.replace("PRESET:", ""));
                  }
                }}
              >
                {POLICY_PRESETS.map((p) => (
                  <option key={p} value={`PRESET:${p}`}>
                    PRESET: {p}
                  </option>
                ))}
                <option value="direct">role/department/position 직접 설정</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              민감도
              <select
                className={inputClass}
                value={sensitivity}
                onChange={(e) =>
                  setSensitivity(e.target.value as "normal" | "sensitive")
                }
              >
                <option value="normal">normal</option>
                <option value="sensitive">sensitive</option>
              </select>
            </label>
            {accessMode === "direct" && (
              <div className="sm:col-span-2 grid gap-3 rounded-lg border border-border bg-muted/20 p-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  access logic
                  <select
                    className={inputClass}
                    value={accessLogic}
                    onChange={(e) =>
                      setAccessLogic(e.target.value as "ANY" | "ALL")
                    }
                  >
                    <option value="ANY">ANY — 하나라도 만족</option>
                    <option value="ALL">ALL — 제약 축 모두 만족</option>
                  </select>
                </label>
                <div className="flex flex-col gap-1 text-sm">
                  읽기 role
                  <span className="flex gap-3 pt-1.5 text-xs">
                    {["admin", "member"].map((role) => (
                      <label key={role} className="inline-flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={readRoles.includes(role)}
                          onChange={() => toggleRole(role)}
                        />
                        {role}
                      </label>
                    ))}
                  </span>
                </div>
                <label className="flex flex-col gap-1 text-sm">
                  읽기 부서
                  <span className="flex flex-wrap gap-2">
                    {departments.map((d) => (
                      <label
                        key={d.id}
                        className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs"
                      >
                        <input
                          type="checkbox"
                          checked={readDeptIds.includes(d.id)}
                          onChange={() =>
                            setReadDeptIds((prev) => toggleId(prev, d.id))
                          }
                        />
                        {d.name}
                      </label>
                    ))}
                  </span>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  읽기 직급 (쉼표 구분)
                  <input
                    className={inputClass}
                    value={readPositions}
                    onChange={(e) => setReadPositions(e.target.value)}
                    placeholder="예: 리드, 시니어"
                  />
                </label>
              </div>
            )}

            <div className="sm:col-span-2 text-xs font-semibold text-muted-foreground">
              요약
            </div>
            <label className="flex flex-col gap-1 text-sm sm:col-span-2">
              요약
              <textarea
                className="min-h-20 rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring disabled:opacity-60"
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
              />
            </label>
          </fieldset>
        </section>

        {/* ── 민감 문서 권한 (U-5) ── */}
        <section className="rounded-xl border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">민감 문서 권한</h2>
            {aiPreset ? (
              <span className="inline-flex items-center rounded-full bg-destructive/10 px-2.5 py-0.5 text-xs font-medium text-destructive">
                제한 필요
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                추천 없음
              </span>
            )}
          </div>
          <div className="flex flex-col gap-3 p-4 text-sm">
            <div className="grid gap-1.5 text-xs text-muted-foreground">
              <div className="flex justify-between gap-3">
                <span>AI 추천 preset</span>
                <strong className="text-foreground">{aiPreset ?? "—"}</strong>
              </div>
              <div className="flex justify-between gap-3">
                <span>preset 후보</span>
                <strong className="text-foreground">
                  HR / CONTRACT / FINANCE / SECURITY / LEGAL_RESTRICTED
                </strong>
              </div>
              <div className="flex justify-between gap-3">
                <span>사유</span>
                <strong className="text-right text-foreground">
                  {aiReasons.length > 0 ? aiReasons.join(" · ") : "—"}
                </strong>
              </div>
              <div className="flex justify-between gap-3">
                <span>현재 적용</span>
                <strong className="text-foreground">
                  {accessMode === "preset"
                    ? `PRESET: ${presetName}`
                    : `직접 설정 (${accessLogic})`}{" "}
                  · {sensitivity}
                </strong>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={disabled || !aiPreset}
                onClick={() => {
                  if (!aiPreset) return;
                  setAccessMode("preset");
                  setPresetName(aiPreset);
                  setSensitivity("sensitive");
                }}
              >
                preset 승인
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={disabled}
                onClick={() => {
                  setAccessMode("direct");
                  permissionRef.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "center",
                  });
                }}
              >
                권한 수정
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={disabled}
                onClick={() => {
                  // 민감 아님 — preset 후보 제거 (S-4)
                  setAccessMode("direct");
                  setSensitivity("normal");
                }}
              >
                민감 아님
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              승인 시 preset은 read policy 필드로 풀어 저장된다 (DEC-018).
            </p>
          </div>
        </section>

        {/* ── 문서종류 추가 modal (U-4) ── */}
        {modalOpen && (
          <div
            className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
            role="dialog"
            aria-modal="true"
            aria-label="문서종류 추가"
          >
            <div className="w-full max-w-sm rounded-xl border border-border bg-card shadow-lg">
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <h2 className="text-sm font-semibold">문서종류 추가</h2>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setModalOpen(false)}
                  aria-label="닫기"
                >
                  <X />
                </Button>
              </div>
              <div className="flex flex-col gap-3 p-4">
                <label className="flex flex-col gap-1 text-sm">
                  이름
                  <input
                    className={inputClass}
                    value={newTypeName}
                    autoFocus
                    onChange={(e) => setNewTypeName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleCreateDocType();
                    }}
                    placeholder="예: 계약서"
                  />
                </label>
                <p className="text-xs text-muted-foreground">
                  전사 공통 카탈로그에 추가된다. 기존 문서의 문서종류는 자동
                  변경되지 않는다.
                </p>
                {modalError && (
                  <div
                    role="alert"
                    className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                  >
                    {modalError}
                  </div>
                )}
              </div>
              <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
                <Button variant="outline" onClick={() => setModalOpen(false)}>
                  취소
                </Button>
                <Button
                  onClick={() => void handleCreateDocType()}
                  disabled={modalBusy || !newTypeName.trim()}
                >
                  {modalBusy && <LoaderCircle className="animate-spin" />}
                  추가
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  },
);
